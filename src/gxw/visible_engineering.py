"""Core plans and source readback for a visible native engineering session.

No UI calls or offline adapter call logs are accepted as source evidence here.
The caller supplies bytes saved from the active native document after each step.
"""
from __future__ import annotations

import base64
import math
import re
from copy import deepcopy

from .fb_authoring import (_context, _global_snapshot, _rows, authoring_plan,
                           definition_snapshot, st_program_authoring_plan,
                           verify_st_program_readback)
from .declarations import BASIC_TYPES, CLASS_CODES, _type_parts
from .models import GXWFormatError, NodeKind
from .native_write import workspace_body
from .object_model import read_project_context
from .source_header import source_payload_offset
from .text_pou import parse_st_pou


AUTHORING_OPERATIONS = frozenset({
    'create_global_labels', 'edit_global_labels', 'edit_pou_labels',
    'create_structure', 'create_st_fb', 'edit_st_fb', 'configure_project',
    'create_st_program', 'edit_st_program',
})


def normalized_st(text):
    """RichEdit normalizes line endings, without changing ST tokens or spacing."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def source_projection(raw):
    """Compare declarations, bodies, structures and configuration, without caches."""
    from .project_metadata import read_project_system_variables
    config, streams, groups, sources, _ = _context(raw, require_fbd=False)
    bodies = {}
    for logical, group in groups.items():
        if logical not in streams:
            raise GXWFormatError('current source group has no body')
        source = streams[logical]
        kind = source[source_payload_offset(source)]
        if kind == 193:
            text = parse_st_pou(source).text
            if text is None:
                raise GXWFormatError('visible ST source is opaque')
            body = normalized_st(text)
        elif kind == 208:
            body = workspace_body(source)
        else:
            raise GXWFormatError('visible engineering covers current ST and FBD sources')
        document = sources.declarations.get(group['labels'])
        owner = document.owner_name if document is not None else logical
        if owner in bodies:
            raise GXWFormatError('visible source owner is ambiguous')
        bodies[owner] = {'kind': kind, 'body': body,
                         **({'trailer': source[-24:]} if kind == 208 else {})}
    declarations = {}
    for logical, document in sources.declarations.items():
        if document.scope == 'global':
            declarations[logical] = _global_snapshot(sources, logical)
        else:
            declarations['local:' + document.owner_name] = {'owner': document.owner_name,
                'kind': document.owner_pou_type, 'rows': _rows(document, sources)}
    project_names = [name for name in streams if name.endswith('.prj')]
    if len(project_names) != 1:
        raise GXWFormatError('visible project configuration is ambiguous')
    configuration = {'name': project_names[0], 'ranges':
        read_project_system_variables(streams[project_names[0]])['ranges']}
    parameter = streams.get('Param.wpa', b'')
    if (config['cpu'] == 'FX3U/FX3UC' and len(parameter) == 1100
            and parameter[:4] == bytes.fromhex('40002201')):
        configuration['program_capacity'] = parameter[704] * 1000
    structures = {row['name']: row for row in definition_snapshot(raw, require_fbd=False)['definitions']
                  if row['kind'] == 'structure'}
    return {'cpu': config['cpu'], 'sources': bodies, 'declarations': declarations,
            'structures': structures, 'configuration': configuration}


def validate_visible_initial_values(rows):
    """Reject fields that native label/structure CSV cannot preserve.

    Accept either explicit declaration inputs or normalized Core plan rows.
    Missing input values remain source-dependent and are checked after planning.
    """
    for row in rows:
        if 'initial_value' not in row:
            continue
        constant = (row.get('class_code') in (CLASS_CODES['VAR_CONSTANT'], CLASS_CODES['VAR_GLOBAL_CONSTANT'])
                    or row.get('class_name') in ('VAR_CONSTANT', 'VAR_GLOBAL_CONSTANT'))
        if not constant:
            base, bounds = _type_parts(row['data_type'])
            defaults = {BASIC_TYPES[base][1] if base in BASIC_TYPES and not bounds else ''}
            if base in BASIC_TYPES and bounds:
                # Native arrays retain either an empty cache or the ordinary
                # repeated type default. Neither supplies a custom CSV value.
                defaults.add(f"[{math.prod(high - low + 1 for low, high in bounds)}({BASIC_TYPES[base][1]})]")
            if row['initial_value'] not in defaults:
                raise GXWFormatError('visible label CSV does not cover nondefault ordinary-variable initial values')


def prepare_authoring(raw, command, *, device_profile=None):
    supplied = command
    if command.get('operation') in ('create_global_labels', 'edit_global_labels') and device_profile is not None:
        if 'device_profile' in command and command['device_profile'] != device_profile:
            raise GXWFormatError('visible global address profile differs from the selected engineering CPU')
        supplied = {**command, 'device_profile': device_profile}
    if command.get('operation') not in AUTHORING_OPERATIONS:
        raise GXWFormatError('unsupported visible definition operation')
    planner = (st_program_authoring_plan if command['operation'] in
               ('create_st_program', 'edit_st_program') else
               lambda data, change: authoring_plan(data, change, require_fbd=False))
    plan, before, expected, caller = planner(raw, supplied)
    if command['operation'] != 'configure_project':
        if plan.get('write_declarations', True):
            validate_visible_initial_values(plan['rows'])
        for row in plan['rows']:
            constant = row['class_code'] in (CLASS_CODES['VAR_CONSTANT'], CLASS_CODES['VAR_GLOBAL_CONSTANT'])
            row['csv_constant'] = row['initial_value'] if constant else ''
    return {'kind': 'authoring', 'plan': plan, 'before': before,
            'expected': expected, 'caller': caller}


def _preserved(before, after, *, sources=(), declarations=(), structures=(), configuration=False):
    if before['cpu'] != after['cpu']:
        raise GXWFormatError('visible edit changed the current CPU')
    for key, omitted in (('sources', sources), ('declarations', declarations),
                         ('structures', structures)):
        left = {name: row for name, row in before[key].items() if name not in omitted}
        right = {name: row for name, row in after[key].items() if name not in omitted}
        if left != right:
            raise GXWFormatError('visible edit changed unrelated current ' + key)
    if not configuration and before['configuration'] != after['configuration']:
        raise GXWFormatError('visible edit changed unrelated project configuration')


def verify_authoring_readback(raw, command, saved, prepared=None):
    """Verify saved native state independently of UI success or adapter logs."""
    prepared = prepared or prepare_authoring(raw, command)
    plan, expected = prepared['plan'], prepared['expected']
    operation = command['operation']
    before, after = source_projection(raw), source_projection(saved)
    config, streams, groups, sources, _ = _context(saved, require_fbd=False)
    if config['cpu'] != plan['cpu']:
        raise GXWFormatError('visible definition changed the target CPU')
    allowed_sources, allowed_labels, allowed_types = (), (), ()
    if operation in ('create_global_labels', 'edit_global_labels'):
        logical = plan['source_name'] + '.gh'
        definition = _global_snapshot(sources, logical)
        if definition['declarations'] != expected:
            raise GXWFormatError('visible global declarations differ from the Core plan')
        allowed_labels = (logical,)
    elif operation == 'edit_pou_labels':
        matched = [(logical, group, sources.declarations[group['labels']])
            for logical, group in groups.items() if group['labels'] in sources.declarations
            and sources.declarations[group['labels']].owner_name == plan['source_name']]
        if len(matched) != 1:
            raise GXWFormatError('visible POU declaration owner is ambiguous')
        logical, group, document = matched[0]
        if (_rows(document, sources) != expected
                or workspace_body(streams[logical]) != base64.b64decode(plan['before_body'])):
            raise GXWFormatError('visible POU declarations or retained body differ from the Core plan')
        definition = {'name': plan['source_name'], 'kind': 'pou_labels',
                      'program': logical, 'declarations': _rows(document, sources)}
        allowed_labels = ('local:' + document.owner_name,)
    elif operation in ('create_st_program', 'edit_st_program'):
        checked = verify_st_program_readback(raw, command, saved)
        definition = checked['program']
        allowed_sources = (definition['name'],)
        allowed_labels = ('local:' + definition['name'],)
    elif operation == 'configure_project':
        from .project_metadata import read_project_system_variables
        logical = command['source_name']
        if logical not in streams:
            raise GXWFormatError('visible configuration identity changed')
        definition = {'name': logical, 'kind': 'project_configuration',
            'system_variable_ranges': {k: list(v) for k, v in
                read_project_system_variables(streams[logical])['ranges'].items()}}
        if definition['system_variable_ranges'] != expected['system_variable_ranges']:
            raise GXWFormatError('visible allocation ranges differ from the Core plan')
        if 'parameter_after' in plan:
            if streams.get('Param.wpa') != base64.b64decode(plan['parameter_after']):
                raise GXWFormatError('visible capacity changed unrelated parameter bytes')
            definition['program_capacity'] = streams['Param.wpa'][704] * 1000
    else:
        snapshot = definition_snapshot(saved, plan['source_name'], require_fbd=False)
        definition, = snapshot['definitions']
        if operation == 'create_structure':
            rows = [{key: row[key] for key in ('name', 'data_type', 'initial_value', 'comment')}
                    for row in expected]
            if definition['kind'] != 'structure' or definition['members'] != rows:
                raise GXWFormatError('visible structure members differ from the Core plan')
            allowed_types = (definition['name'],)
        else:
            if (definition.get('language') != 'st' or definition.get('declarations') != expected
                    or normalized_st(definition.get('st_text', '')) != normalized_st(command['st_text'])):
                raise GXWFormatError('visible ST FB interface or body differs from the Core plan')
            allowed_sources = (definition['name'],)
            allowed_labels = ('local:' + definition['name'],)
    _preserved(before, after, sources=allowed_sources, declarations=allowed_labels,
               structures=allowed_types, configuration=operation == 'configure_project')
    return {'status': 'matched', 'definition': definition, 'caller': prepared['caller'],
            **({'task_header_changes': checked['task_header_changes']} if operation in
               ('create_st_program', 'edit_st_program') else {})}


def _fbd_call_command(command):
    if (not isinstance(command, dict) or set(command) != {'operation', 'caller', 'instance', 'definition', 'arguments'}
            or command['operation'] != 'create_fbd_call'
            or any(not isinstance(command[k], str) or not command[k] for k in ('caller', 'instance', 'definition'))
            or not isinstance(command['arguments'], dict)
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                   for k, v in command['arguments'].items())):
        raise GXWFormatError('visible FBD call requires caller, instance, definition and named symbols')


def prepare_fbd_call(raw, command):
    _fbd_call_command(command)
    context = read_project_context(raw, command['caller'])
    if context.program.raw[source_payload_offset(context.program.raw)] != 208:
        raise GXWFormatError('visible FBD insertion requires a current FBD source')
    from .structured_pou_writer import serialize_structured_node
    from .visible_graph import _snapshot
    if any(serialize_structured_node(node) != node.raw for node in context.program.nodes):
        raise GXWFormatError('visible FBD insertion requires canonical complete native node records')
    before_graph = _snapshot(context.program, {node.offset: 'n' + str(node.offset) for node in context.program.nodes})
    if any(n.kind == NodeKind.FUNCTION_BLOCK and n.symbol.casefold() == command['instance'].casefold()
           for n in context.program.nodes):
        raise GXWFormatError('visible FBD instance already has a call in the selected caller')
    binding = context.sources.label(command['instance'])
    if binding is None or binding[1].data_type != command['definition']:
        raise GXWFormatError('visible FBD instance must be declared with the current definition type')
    interface = context.sources.fixed_interface(command['definition'])
    formals = interface['inputs'] + interface['outputs']
    if set(command['arguments']) != {row['name'] for row in formals}:
        raise GXWFormatError('visible FBD arguments must bind every current formal')
    for formal in formals:
        symbol = command['arguments'][formal['name']]
        value = context.sources.structure_value(symbol)
        label = context.sources.label(symbol) if value is None else None
        data_type = value['data_type'] if value else label[1].data_type if label else None
        if data_type is None or data_type.casefold() != formal['declared_type'].casefold():
            raise GXWFormatError('visible FBD argument differs from the current declared formal type: ' + formal['name'])
    from .native_write import native_task_scope_context
    from .project_metadata import project_gui_configuration
    owner = context.declarations[context.sources.local_table].owner_name
    return {'kind': 'fbd_call', 'plan': {**deepcopy(command), 'cpu': context.sources.cpu,
            'interface': interface, 'owner': owner}, 'caller': context.program.logical_name,
            'owner': owner, 'before_graph': before_graph, 'before_project': source_projection(raw),
            'task_scopes': native_task_scope_context(raw), 'gui_configuration': project_gui_configuration(raw)}


def verify_fbd_call(raw, command, saved, prepared=None):
    from .connectivity import build_connectivity_graph
    from .native_write import native_task_scope_context
    from .object_model import _ports
    from .project_metadata import project_gui_configuration
    from .source_header import normalize_visible_save_header
    from .structured_pou_writer import serialize_structured_node
    from .visible_graph import _geometry, _membership, _project_guard, _snapshot
    _fbd_call_command(command)
    prepared = prepared or prepare_fbd_call(raw, command)
    if command.get('caller') != prepared['caller']:
        raise GXWFormatError('visible FBD insertion caller differs from the frozen plan')
    if 'plan' in prepared and any(prepared['plan'].get(key) != value for key, value in command.items()):
        raise GXWFormatError('visible FBD insertion command differs from the frozen plan')
    old = read_project_context(raw, prepared['caller'])
    context = read_project_context(saved, prepared['caller'])
    if any(serialize_structured_node(node) != node.raw for program in (old.program, context.program) for node in program.nodes):
        raise GXWFormatError('visible FBD insertion requires canonical complete native node records')
    old_ids = {node.offset: 'n' + str(node.offset) for node in old.program.nodes}
    before = _snapshot(old.program, old_ids)
    if 'before_graph' in prepared and prepared['before_graph'] != before:
        raise GXWFormatError('visible FBD insertion baseline differs from the frozen graph')
    old_blocks, new_blocks = _membership(old.program), _membership(context.program)
    matched, retained = {}, set()
    for node in old.program.nodes:
        candidates = [row for row in context.program.nodes
                      if new_blocks[row.offset] == old_blocks[node.offset] and row.raw == node.raw]
        if len(candidates) != 1:
            raise GXWFormatError('visible FBD insertion changed or duplicated an existing complete node record')
        matched[candidates[0].offset] = old_ids[node.offset]
        retained.add(candidates[0].offset)
    old_wires = set()
    for wire in old.program.wires:
        candidates = [row for row in context.program.wires
                      if new_blocks[row.offset] == old_blocks[wire.offset] and row.raw == wire.raw]
        if len(candidates) != 1:
            raise GXWFormatError('visible FBD insertion changed or duplicated an existing complete wire record')
        old_wires.add(candidates[0].offset)
    identities = {node.offset: matched.get(node.offset, 'created' + str(node.offset)) for node in context.program.nodes}
    after = _snapshot(context.program, identities)
    if before['trailer'] != after['trailer']:
        raise GXWFormatError('visible FBD insertion changed the retained native source trailer')
    a, b = bytes.fromhex(before['header']), bytes.fromhex(after['header'])
    normalized, header_change = normalize_visible_save_header(a[:54], b[:54], cpu=old.sources.cpu)
    if normalized + b[54:] != a:
        raise GXWFormatError('visible FBD insertion changed an unverified source header')
    if len(before['blocks']) != len(after['blocks']):
        raise GXWFormatError('visible FBD insertion changed the existing graph block inventory')
    for previous, current in zip(before['blocks'], after['blocks']):
        if any(previous[key] != current[key] for key in ('opaque_header', 'unknown')):
            raise GXWFormatError('visible FBD insertion changed retained block or unknown records')
    matches = [n for n in context.program.nodes if n.kind == NodeKind.FUNCTION_BLOCK
               and n.symbol == command['instance'] and n.type_name == command['definition']]
    if len(matches) != 1:
        raise GXWFormatError('visible FBD call is missing or ambiguous in saved geometry')
    node, = matches
    if node.offset in retained:
        raise GXWFormatError('visible FBD insertion did not create a new call')
    target_block = new_blocks[node.offset]
    for index, (previous, current) in enumerate(zip(before['blocks'], after['blocks'])):
        # Placement has no fixed coordinates. The target block may grow blank
        # canvas rows, but existing blank rows and all other blocks stay intact.
        minimum = max([previous['height']] + [n.bbox.bottom for n in context.program.nodes if new_blocks[n.offset] == index]
            + [value for wire in context.program.wires if new_blocks[wire.offset] == index for value in (wire.start.y, wire.end.y)])
        if ((index != target_block and current['height'] != previous['height'])
                or index == target_block and not minimum <= current['height'] <= 4096):
            raise GXWFormatError('visible FBD insertion changed an unrelated canvas or removed retained blank rows')
    ports = context.sources.callable(node)['ports']
    if set(command['arguments']) != {port['formal_name'] for port in ports}:
        raise GXWFormatError('visible FBD arguments must bind every current formal')
    if any(port.get('negated') for port in ports):
        raise GXWFormatError('visible FBD insertion added an unrequested negated call port')
    graph = build_connectivity_graph(context.program)
    wires = {wire.offset: wire for wire in context.program.wires}
    old_nets = []
    for net in graph.nets:
        if not any(port.node_offset in retained for port in net.ports) and not old_wires.intersection(net.wire_offsets):
            continue
        if any(port.node_offset not in retained for port in net.ports) or set(net.wire_offsets) - old_wires:
            raise GXWFormatError('visible FBD insertion connected a new object to an existing net')
        old_nets.append({'block': net.block_index,
            'ports': sorted([[matched[port.node_offset], port.port_index] for port in net.ports]),
            'geometry': _geometry([wires[offset] for offset in net.wire_offsets])})
    old_nets.sort(key=lambda row: (row['block'], row['ports'], row['geometry']))
    if old_nets != before['nets']:
        raise GXWFormatError('visible FBD insertion changed an existing complete port-net partition')
    connections = []
    terminals, created_wires = set(), set()
    for index, port in enumerate(ports):
        symbol = command['arguments'].get(port['formal_name'])
        expected_kind = NodeKind.INPUT if port['side'] == 'in' else NodeKind.OUTPUT
        net = graph.net_for_port(node.offset, index)
        candidates = [p for p in net.ports if p.node_kind == expected_kind and p.node_symbol == symbol
                      and p.node_offset not in retained]
        if symbol is None or len(candidates) != 1:
            raise GXWFormatError('visible FBD formal is not connected to its requested symbol: ' + port['name'])
        terminal, = candidates
        term_node = next(row for row in context.program.nodes if row.offset == terminal.node_offset)
        if (len(term_node.ports) != 1 or terminal.port_index != 0 or len(net.ports) != 2
                or terminal.node_offset in terminals or new_blocks[term_node.offset] != target_block):
            raise GXWFormatError('visible FBD formal has extra or shared terminal connections: ' + port['name'])
        if any(port.get('negated') for port in _ports(term_node, context.sources)):
            raise GXWFormatError('visible FBD insertion added an unrequested negated terminal')
        terminals.add(terminal.node_offset)
        created_wires.update(net.wire_offsets)
        connections.append({'formal': port['name'], 'symbol': symbol, 'side': port['side'],
                            'net': net.index})
    if (set(identities) - retained != terminals | {node.offset}
            or set(wires) - old_wires != created_wires
            or any(new_blocks[offset] != target_block for offset in created_wires)):
        raise GXWFormatError('visible FBD insertion added unrequested graph objects')
    owner = context.declarations[context.sources.local_table].owner_name
    preservation = {'owner': owner, 'before_project': source_projection(raw),
        'task_scopes': native_task_scope_context(raw), 'gui_configuration': project_gui_configuration(raw)}
    _project_guard(saved, preservation)
    return {'status': 'matched', 'caller': prepared['caller'], 'instance': command['instance'],
            'definition': command['definition'], 'connections': connections,
            'graph_preservation': 'complete_old_records_and_port_nets',
            'source_header_changes': [{'source': prepared['caller'], **header_change}] if header_change else []}


def saved_assignments(raw):
    """Read stored assignments; source freshness belongs to the session's compile."""
    from .compiler_symbols import compiler_symbols
    from .compiler_tables import parse_compiler_tables
    config, streams, _, _, _ = _context(raw, require_fbd=False)
    if 'CGTable.dat' not in streams:
        return {'status': 'unavailable', 'rows': []}
    tables = parse_compiler_tables(streams['CGTable.dat'])
    encoding = config['text_encoding']
    if encoding is None:
        raise GXWFormatError('saved assignments require the project codepage; host encoding is not a fallback')
    rows = []
    for symbol in compiler_symbols(tables):
        assignment = symbol.assignment
        try:
            source = tables.pou_at(symbol.pou_offset).name_bytes.decode(encoding)
            name = symbol.component.name_bytes.decode(encoding)
        except UnicodeError as error:
            raise GXWFormatError('saved compiler names are not valid in the project codepage') from error
        rows.append({'source': source,
            'name': name, 'status': assignment.status,
            'address': assignment.operand if assignment.device_family is not None else None,
            'iec_address': assignment.iec_address, 'constant_value': assignment.constant_value,
            'role': assignment.role, 'count': assignment.reserved_count})
    return {'status': 'stored', 'rows': rows}


def prepare_assignment_cross_references(raw, queries, *, assignments=None):
    """Bind explicit global queries to one current saved compiler allocation."""
    from .native_diagnostics import native_inspection_queries
    if not isinstance(queries, list) or not 1 <= len(queries) <= 32:
        raise GXWFormatError('visible cross-reference display requires 1 to 32 explicit queries')
    if any(not isinstance(query, dict) or query.get('scope') != 'global' for query in queries):
        raise GXWFormatError('visible cross-reference display currently requires explicit global scope')
    native_inspection_queries(raw, {'assignments': queries, 'locations': [], 'saved_code': False})
    assignments = saved_assignments(raw) if assignments is None else assignments
    if assignments.get('status') != 'stored' or not isinstance(assignments.get('rows'), list):
        raise GXWFormatError('visible cross-reference display requires saved compiler assignments')
    selected = []
    for query in queries:
        owner = query['source'].removesuffix('.gh')
        # The native FX3U compiler stores project globals under @GLOBALS.
        # native_inspection_queries has already required this exact name to
        # belong uniquely to the requested current global declaration table.
        matches = [row for row in assignments['rows']
                   if row.get('source') in (owner, '@GLOBALS') and row.get('name') == query['name']]
        if len(matches) != 1:
            raise GXWFormatError('visible cross-reference query has no unique source-bound compiler assignment')
        assignment, = matches
        if (assignment.get('status') != 'decoded' or not isinstance(assignment.get('address'), str)
                or not assignment['address'].strip() or assignment.get('constant_value') is not None):
            raise GXWFormatError('visible cross-reference query has no decoded device allocation')
        selected.append({'query': deepcopy(query), 'assignment': deepcopy(assignment)})
    return selected


def verify_native_cross_references(raw, queries, observation, *, selected=None):
    """Match native grid rows to queried allocations and current source POUs."""
    expected = prepare_assignment_cross_references(raw, queries)
    if selected is not None and expected != selected:
        raise GXWFormatError('saved compiler assignments changed during visible cross-reference display')
    records = observation.get('cross_references')
    if not isinstance(records, list) or len(records) != len(expected):
        raise GXWFormatError('native cross-reference results are missing or duplicated')
    _, _, groups, sources, _ = _context(raw, require_fbd=False)
    from .native_write import native_task_scope_context
    task_scopes = native_task_scope_context(raw)
    if task_scopes.get('status') != 'available':
        raise GXWFormatError('native cross-reference resource/task ownership is unavailable')
    owners = {}
    for logical, group in groups.items():
        document = sources.declarations.get(group['labels'])
        if document is not None and document.owner_name:
            owners.setdefault(document.owner_name, []).append(logical)
    matched, seen_queries = [], set()
    for record in records:
        if not isinstance(record, dict):
            raise GXWFormatError('invalid native cross-reference result')
        matches = [row for row in expected if row['query'] == record.get('query')]
        if len(matches) != 1:
            raise GXWFormatError('native cross-reference result belongs to another query')
        chosen, = matches
        query, assignment = chosen['query'], chosen['assignment']
        key = query['source'], query['name']
        if key in seen_queries:
            raise GXWFormatError('duplicate native cross-reference query result')
        seen_queries.add(key)
        visible = record.get('visible_completion')
        if (not isinstance(visible, dict) or visible.get('completed') is not True
                or not isinstance(visible.get('page'), str) or not visible['page'].strip()):
            raise GXWFormatError('native cross-reference result has no visible completion')
        rows, summary = record.get('rows'), record.get('summary')
        count = re.match(r'^\s*([0-9]+)件[：:]', summary) if isinstance(summary, str) else None
        if (record.get('search_text') != query['name'] or count is None
                or '"' + query['name'] + '"' not in summary
                or not isinstance(rows, list) or not 1 <= len(rows) <= 4096
                or int(count[1]) != len(rows)):
            raise GXWFormatError('native cross-reference search or result count differs from its query')
        checked, seen_rows = [], set()
        fields = ('label', 'address', 'program', 'pou', 'location', 'read_write')
        indexed = any(isinstance(row, dict) and 'native_row' in row for row in rows)
        if indexed and [row.get('native_row') if isinstance(row, dict) else None for row in rows] != list(range(2, len(rows) + 2)):
            raise GXWFormatError('native cross-reference result row identities are incomplete or repeated')
        for row in rows:
            if (not isinstance(row, dict) or set(row) != set(fields) | ({'native_row'} if indexed else set())
                    or any(not isinstance(row[field], str) for field in fields)
                    or indexed and type(row['native_row']) is not int
                    or any(not row[field].strip() for field in fields if field != 'read_write')):
                raise GXWFormatError('native cross-reference row is incomplete')
            # GX emits distinct rows when one ST line both reads and writes
            # the same label. The MSAA grid row identities distinguish those
            # occurrences even though its read/write icon has no text value.
            identity = (row['native_row'],) if indexed else tuple(row[field] for field in fields)
            if identity in seen_rows:
                raise GXWFormatError('duplicate native cross-reference row')
            seen_rows.add(identity)
            if row['label'] != query['name'] or row['address'].strip().upper() != assignment['address'].strip().upper():
                raise GXWFormatError('native cross-reference label or address differs from the current compiler assignment')
            current = owners.get(row['pou'], [])
            if len(current) != 1:
                raise GXWFormatError('native cross-reference POU is absent or ambiguous in the current source')
            bindings = [task['resource'] for task in task_scopes['tasks']
                        for program in task['programs'] if program['name'] == row['pou']]
            if len(bindings) != 1 or row['program'] != bindings[0]:
                raise GXWFormatError('native cross-reference Program has no unique matching current resource/task binding')
            checked.append({'native': deepcopy(row), 'source': current[0]})
        matched.append({'query': deepcopy(query), 'assignment': deepcopy(assignment),
            'source_usage': 'queried_native_cross_reference', 'rows': checked,
            'summary': summary, 'visible_completion': deepcopy(visible)})
    return {'status': 'matched', 'queries': matched, 'execution_correspondence': 'unverified'}

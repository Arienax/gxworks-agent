"""Current-build allocations joined to source-owned native cross-references.

The caller supplies bytes saved for this exact native build, after the existing
live source/save verifier accepted them. A phase Save after the build must keep
that same confirmed revision and run. This module never builds, saves, or
adopts a pre-existing compiler cache. Declared array elements and reserved
device units are separate observations, including when the latter are unknown.
"""
from __future__ import annotations

from copy import deepcopy
import re
from types import SimpleNamespace

from .compiler_assignment import parse_compiler_assignment
from .compiler_symbols import compiler_symbol_paths
from .compiler_tables import parse_compiler_tables
from .declarations import BASIC_TYPES, _type_parts
from .fb_authoring import _context
from .live_context import _identity, _same_native_cpu
from .models import GXWFormatError
from .native_diagnostics import _native_assignment_sources, native_inspection_queries
from .native_write import native_task_scope_context


def live_assignment_queries(queries):
    """Validate the shared transport shape; source ownership is checked below."""
    if not isinstance(queries, list) or not 1 <= len(queries) <= 32:
        raise GXWFormatError('Current assignments require 1 to 32 explicit queries')
    result, seen = [], set()
    for query in queries:
        if (not isinstance(query, dict) or set(query) not in ({'source', 'name'}, {'source', 'name', 'scope'})
                or query.get('scope', 'local') not in ('global', 'local', 'instance')
                or any(not isinstance(query[field], str) or not 1 <= len(query[field]) <= 512
                       or any(ord(char) < 32 or ord(char) == 127 for char in query[field])
                       for field in ('source', 'name'))):
            raise GXWFormatError('Invalid current assignment source, name or scope')
        key = query['source'], query['name'], query.get('scope', 'local')
        if key in seen:
            raise GXWFormatError('Duplicate current assignment query')
        seen.add(key)
        result.append(deepcopy(query))
    return result


def _native_search_text(query):
    # The measured 1.635 cross-reference profile resolves Program locals and
    # instance members with owner/name. The source-location combo is disabled;
    # an unqualified label does not establish that ownership.
    return (query['source'] + '/' + query['name']
            if query.get('scope', 'local') in ('local', 'instance') else query['name'])


def prepare_live_assignments(raw, queries, *, revision, compile_run_id, identity):
    """Read source/CGTable identities from an acknowledged current compile save.

    Global source names are exact declaration streams, local sources are exact
    POU owners. Instance sources name the calling Program and names contain its
    declared instance root, optionally followed by exact member names.
    A compiler-triggered save or an explicit phase Save of the same unchanged
    successful build can supply these bytes. Freshness belongs to the owning
    live session, never inferred here from
    the mere presence of CGTable.dat or from its stored addresses.
    """
    queries = live_assignment_queries(queries)
    if (type(revision) is not int or revision < 0 or not isinstance(compile_run_id, str)
            or not compile_run_id.strip() or len(compile_run_id) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in compile_run_id)):
        raise GXWFormatError('Current allocations require the exact compile revision and run')
    identity = deepcopy(_identity(identity))
    config, streams, _, sources, _ = _context(raw, require_fbd=False)
    if identity['software_version'] != '1.635.0.1' or not _same_native_cpu(identity['cpu_menu'], config['cpu']):
        raise GXWFormatError('Current allocations belong to another native version or CPU')
    requested, binding_queries = [], []
    for query in queries:
        binding = deepcopy(query)
        if query.get('scope') == 'instance':
            parts = query['name'].split('.')
            if any(not part or not re.fullmatch(r'\w+', part, re.UNICODE) for part in parts):
                raise GXWFormatError('Current instance queries require an exact declared member path')
            binding.update(scope='local', name=parts[0])
        if binding not in binding_queries:
            binding_queries.append(binding)
        requested.append(dict(query=deepcopy(query), status='unavailable', assignments=[]))
    native_inspection_queries(raw, dict(assignments=binding_queries, locations=[], saved_code=False))
    result = dict(kind='current_compile_allocations', status='unavailable', revision=revision,
        compile_run_id=compile_run_id, identity=identity, queries=requested,
        origin='acknowledged_current_native_compile_save', execution='not_run')
    if 'CGTable.dat' not in streams:
        for row in requested: row['reason'] = 'current_compile_has_no_saved_allocation_table'
        return result
    tables = parse_compiler_tables(streams['CGTable.dat'])
    local, globals_ = _native_assignment_sources(raw)
    encoding = 'cp' + str(config['codepage'])
    pous = [tables.pou_at(row.table_offset) for row in tables.tables[1].records]
    tasks = native_task_scope_context(raw)
    if tasks.get('status') != 'available':
        for row in requested: row['reason'] = 'current_resource_task_ownership_unavailable'
        return result
    roots = {}
    for task in tasks['tasks']:
        for program in task['programs']:
            roots.setdefault(program['name'], []).append(task['resource'])

    def same(left, right):
        # Existing compiler_declaration_bindings uses ASCII folding only.
        return (left.upper() if left.isascii() else left) == (right.upper() if right.isascii() else right)

    def named_pous(name):
        encoded = name.encode(encoding)
        return [pou for pou in pous if same(pou.name_bytes, encoded)]

    root_paths = {}
    for name in roots:
        owners = named_pous(name)
        if len(owners) == 1:
            root_paths[name] = compiler_symbol_paths(tables, owners[0].record.table_offset)

    def declared(document, name):
        matches = [row for row in document.rows if row.name == name]
        if len(matches) != 1:
            raise GXWFormatError('Current instance member has no unique declaration')
        return matches[0]

    def members(type_name):
        from .library_sources import parse_library_declarations
        _, definition = sources.definition(type_name, 'FUNCTION_BLOCK')
        if definition.get('source_kind') == 'project_labels':
            return definition['declarations']
        library = parse_library_declarations(definition)
        if library['gaps']:
            raise GXWFormatError('Current library instance declarations are incomplete')
        return SimpleNamespace(rows=tuple(SimpleNamespace(name=row['name'], data_type=row['data_type_raw'],
            type_reference='', device='', iec_address='') for row in library['rows']))

    def source_path(document, names, offsets):
        row = declared(document, names[0])
        for index, name in enumerate(names[1:]):
            component = tables.component_at(offsets[index])
            target = tables.component_instance_pou_offset(component)
            base, bounds = _type_parts(row.data_type)
            if target is None or bounds or base in BASIC_TYPES:
                raise GXWFormatError('Current member path does not follow a scalar FB reference')
            if not same(tables.pou_at(target).name_bytes, base.encode(encoding)):
                raise GXWFormatError('Compiled instance type differs from its source declaration')
            row = declared(members(base), name)
        return row

    def locations(pou_offset, component_offset, *, root_owner=None):
        result = []
        for owner, paths in root_paths.items():
            if root_owner is not None and owner != root_owner:
                continue
            direct = [pou for pou in named_pous(owner) if pou.record.table_offset == pou_offset]
            paths = [path for path in paths if path.symbol.pou_offset == pou_offset
                     and path.symbol.component.record.table_offset == component_offset]
            if direct or paths:
                names = {owner, tables.pou_at(pou_offset).name_bytes.decode(encoding)}
                result.extend(dict(resource=resource, root_owner=owner, pou=name)
                              for resource in roots[owner] for name in names)
        return result

    def allocation(query, symbol, row, *, name, path=(), root_owner=None):
        component, assigned = symbol.component, symbol.assignment
        base, bounds = _type_parts(row.data_type)
        if base not in BASIC_TYPES:
            raise GXWFormatError('Current aggregate allocation type is outside the observed leaf profile')
        if bounds:
            raise GXWFormatError('Current array allocation requires an independently bound element query')
        address, origin = assigned.operand, 'current_compiler_assignment'
        if assigned.device_family is None:
            # Explicit global declarations and compiler allocations are distinct.
            if query.get('scope') == 'global' and assigned.status == 'unassigned' and row.device:
                address, origin = row.device, 'explicit_global_declaration'
            else:
                raise GXWFormatError('Current variable has no decoded device allocation')
        if assigned.constant_value is not None or not isinstance(address, str) or not address.strip():
            raise GXWFormatError('A compiler constant is not a device allocation')
        source = tables.pou_at(symbol.pou_offset).name_bytes.decode(encoding)
        return dict(source=query['source'], name=name, scope=query.get('scope', 'local'), address=address,
            iec_address=assigned.iec_address or row.iec_address or None,
            type=row.data_type, type_origin='current_source_declaration', compiler_type_code=tables.component_type(component),
            declared_count=1, count=assigned.reserved_count, reserved_count=assigned.reserved_count,
            count_origin='compiler_reserved_units' if assigned.reserved_count is not None else 'unavailable',
            status='decoded', role=assigned.role, allocation_origin=origin, compiled_owner=source,
            compiled_owner_offset=symbol.pou_offset, component_offset=component.record.table_offset,
            component_path=list(path), allowed_locations=locations(symbol.pou_offset, component.record.table_offset,
                                                                   root_owner=root_owner))

    from .compiler_symbols import CompilerSymbol
    for item in requested:
        query, selected, unavailable_members = item['query'], [], []
        scope, owner = query.get('scope', 'local'), query['source']
        root_name = query['name'].split('.')[0] if scope == 'instance' else query['name']
        source_candidates = [dict(scope=kind, source=name, name=row.name)
            for kind, documents in (('global', globals_), ('local', local))
            for name, document in documents.items() for row in document.rows
            if row.name.casefold() == root_name.casefold()]
        item['source_candidates'] = source_candidates
        item['scope_resolution'] = ('owner_qualified_native_search' if scope in ('local', 'instance') else
                                    'requires_native_source_selection' if len(source_candidates) > 1 else
                                    'unambiguous_source_declaration')
        try:
            if scope == 'instance':
                document = local[owner]
                if document.owner_pou_type != 0x01000000 or owner not in root_paths or len(roots[owner]) != 1:
                    raise GXWFormatError('Current instance requires a uniquely tasked calling Program')
                parts = query['name'].split('.')
                root = declared(document, parts[0])
                if sources.declaration_kind(root) != 'function_block':
                    raise GXWFormatError('Current instance root is not a declared function block')
                paths = [path for path in root_paths[owner]
                         if [name.decode(encoding) for name in path.names[1:len(parts) + 1]] == parts
                         and len(path.names) > 2 and tables.component_instance_pou_offset(path.symbol.component) is None]
                if len(parts) > 1:
                    paths = [path for path in paths if len(path.names) == len(parts) + 1]
                if not paths:
                    raise GXWFormatError('Current instance member is absent from its exact compiled reference path')
                for path in paths:
                    names = [name.decode(encoding) for name in path.names[1:]]
                    try:
                        row = source_path(document, names, path.component_offsets)
                        selected.append(allocation(query, path.symbol, row, name='.'.join(names),
                                                   path=path.component_offsets, root_owner=owner))
                    except GXWFormatError as error:
                        unavailable_members.append(dict(name='.'.join(names), reason=str(error)))
                if not selected:
                    raise GXWFormatError(unavailable_members[0]['reason'] if unavailable_members else
                                         'Current instance has no decoded device allocations')
            else:
                document = globals_[owner] if scope == 'global' else local[owner]
                owners = named_pous('@GLOBALS' if scope == 'global' else owner)
                if len(owners) != 1:
                    raise GXWFormatError('Current compiled owner is absent or ambiguous; use an exact instance query')
                pou, = owners
                components = [component for component in tables.components(pou)
                              if same(component.name_bytes, query['name'].encode(encoding))]
                if len(components) != 1:
                    raise GXWFormatError('Current compiled variable is absent or ambiguous')
                component, = components
                symbol = CompilerSymbol(pou.record.table_offset, component,
                    parse_compiler_assignment(component.user_info), tables.component_address_offset(component))
                selected.append(allocation(query, symbol, declared(document, query['name']), name=query['name']))
                if scope == 'global':
                    selected[0]['allowed_locations'] = [dict(resource=resource, root_owner=root, pou=name)
                        for root, resources in roots.items() for resource in resources
                        for name in {root} | {tables.pou_at(path.symbol.pou_offset).name_bytes.decode(encoding)
                                             for path in root_paths.get(root, ())}]
            if len({(row['compiled_owner_offset'], row['component_offset']) for row in selected}) != len(selected):
                raise GXWFormatError('Current allocation path is repeated or ambiguous')
            item.update(status='prepared', assignments=selected, search_text=_native_search_text(query))
            if unavailable_members:
                item['unavailable_members'] = unavailable_members
        except (GXWFormatError, UnicodeError, KeyError) as error:
            item.update(status='unavailable', reason=str(error), assignments=[])
    statuses = [row['status'] for row in requested]
    result['status'] = 'prepared' if all(status == 'prepared' for status in statuses) else (
        'partial' if 'prepared' in statuses else 'unavailable')
    return result


def verify_live_assignments(prepared, observation):
    """Join this build's allocations to newly queried native rows by owner."""
    if (not isinstance(prepared, dict) or prepared.get('kind') != 'current_compile_allocations'
            or not isinstance(observation, dict)):
        raise GXWFormatError('Current assignment verification requires prepared compile allocations')
    result = dict(status='unavailable', origin='current_native_compile_and_cross_reference',
        revision=prepared['revision'], compile_run_id=prepared['compile_run_id'],
        identity=deepcopy(prepared['identity']), rows=[], results=[], driver_saved=False,
        owner_binding='source_and_compiled_component', allocation_status='unverified', execution='not_run')
    if (type(observation.get('revision')) is not int or observation.get('revision') != prepared['revision']
            or observation.get('compile_run_id') != prepared['compile_run_id']
            or observation.get('identity') != prepared['identity']):
        for item in prepared['queries']:
            result['results'].append(dict(query=deepcopy(item['query']), status='unavailable',
                                          reason='current_compile_binding_changed'))
        return result
    records = observation.get('cross_references')
    if not isinstance(records, list): records = []
    ready = [item['query'] for item in prepared['queries'] if item['status'] == 'prepared']
    queries_match = (len(records) == len(ready) and all(isinstance(record, dict) for record in records)
                     and all(sum(record.get('query') == query for record in records) == 1 for query in ready))
    for item in prepared['queries']:
        entry = dict(query=deepcopy(item['query']), status='unavailable', assignments=[])
        result['results'].append(entry)
        if item['status'] != 'prepared':
            entry['reason'] = item.get('reason', 'current_allocation_unavailable')
            continue
        matches = [record for record in records if isinstance(record, dict) and record.get('query') == item['query']]
        try:
            if not queries_match:
                raise GXWFormatError('Current native results differ from this exact query set')
            if item.get('scope_resolution') == 'requires_native_source_selection':
                # A global use is displayed under the caller's POU too. The
                # same label/address/POU tuple cannot distinguish shadowing or
                # intentional aliases; do not invent a verified chooser API.
                raise GXWFormatError('Same-named declarations require independently verified native source selection')
            if len(matches) != 1:
                raise GXWFormatError('Current native assignment query is absent or repeated')
            record, = matches
            rows, summary = record.get('rows'), record.get('summary')
            from .native_locale import cross_reference_summary_count, native_language
            count = cross_reference_summary_count(summary, item['search_text'],
                language=native_language(prepared['identity']))
            visible = record.get('visible_completion')
            if (item['search_text'] != _native_search_text(item['query'])
                    or record.get('search_text') != item['search_text'] or count is None
                    or not isinstance(rows, list) or not 1 <= len(rows) <= 4096 or count != len(rows)
                    or not isinstance(visible, dict) or visible.get('completed') is not True
                    or not isinstance(visible.get('page'), str) or not visible['page'].strip()):
                raise GXWFormatError('Current native search, count or visible completion is unconfirmed')
            indexed = any(isinstance(row, dict) and 'native_row' in row for row in rows)
            if indexed and (any(not isinstance(row, dict) or type(row.get('native_row')) is not int for row in rows)
                    or [row.get('native_row') for row in rows] != list(range(2, len(rows) + 2))):
                raise GXWFormatError('Current native cross-reference row identities are incomplete')
            seen, covered, checked = set(), set(), []
            fields = {'label', 'address', 'program', 'pou', 'location', 'read_write'}
            for row in rows:
                if (not isinstance(row, dict) or set(row) != fields | ({'native_row'} if indexed else set())
                        or any(not isinstance(row[field], str) or (field != 'read_write' and not row[field].strip())
                               for field in fields)):
                    raise GXWFormatError('Current native cross-reference row is incomplete')
                key = (row['native_row'],) if indexed else tuple(row[field] for field in sorted(fields))
                if key in seen: raise GXWFormatError('Current native cross-reference row is repeated')
                seen.add(key)
                candidates = []
                for index, assignment in enumerate(item['assignments']):
                    labels = {assignment['name']}
                    # A qualified exact member search displays a bare leaf in
                    # its called FB, and the declared instance path at its
                    # caller. This does not authorize suffix matching for an
                    # aggregate query, another owner, or another scope.
                    if (assignment['scope'] == 'instance'
                            and item.get('scope_resolution') == 'owner_qualified_native_search'
                            and item['query']['name'] == assignment['name']
                            and '.' in assignment['name'] and len(assignment['component_path']) > 1
                            and row['pou'] == assignment['compiled_owner']):
                        labels.add(assignment['name'].rsplit('.', 1)[1])
                    allowed = assignment['allowed_locations']
                    if (row['label'] in labels and row['address'].strip().upper() == assignment['address'].strip().upper()
                            and any(row['program'] == place['resource'] and row['pou'] == place['pou'] for place in allowed)):
                        candidates.append(index)
                if len(candidates) != 1:
                    raise GXWFormatError('Current native address or source/instance ownership differs')
                covered.add(candidates[0])
                checked.append(deepcopy(row))
            if covered != set(range(len(item['assignments']))):
                raise GXWFormatError('One or more current allocations have no matching native use')
            entry.update(status='partial' if item.get('unavailable_members') else 'current',
                         assignments=deepcopy(item['assignments']), native_rows=checked,
                         source_usage='queried_current_native_cross_reference')
            if item.get('unavailable_members'):
                entry['unavailable_members'] = deepcopy(item['unavailable_members'])
            result['rows'].extend(deepcopy(item['assignments']))
        except GXWFormatError as error:
            entry['reason'] = str(error)
    statuses = [entry['status'] for entry in result['results']]
    result['status'] = 'current' if all(status == 'current' for status in statuses) else (
        'partial' if any(status in ('current', 'partial') for status in statuses) else 'unavailable')
    result['allocation_status'] = 'current' if result['status'] == 'current' else 'unverified'
    return result

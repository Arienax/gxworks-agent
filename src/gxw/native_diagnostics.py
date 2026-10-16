"""Interpret original source, code, completion and diagnostic observations.

Compilation, publication, actual checker inputs, source locations and public
projection remain independent facts. None establishes PLC execution.
"""
from __future__ import annotations


# Existing adapter plan/inventory allocation capacity, not a CPU program limit.
NATIVE_SOURCE_CAPACITY = 8192
NATIVE_COMPILATION_OPERATIONS = ('Compiler.Build', 'Compiler.ChangeSFCProgram')

# Original public 100-byte FBD Build reports, native version 1.635.0.1.
# Each actual menu choice has a cold rejected-Build control with identical
# node bounds in networks 1 and 3 and an empty middle source network.
# See tests/fixtures/gxw_compile_positions_native.json. This scope says nothing
# about ST, legacy raw reports, expanded FB instances or PLC execution.
PUBLIC_FBD_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'Q04UDV', 'FX1N/FX1NC', 'L02', 'FX0N', 'FX1S',
    'FX2/FX2C', 'FX2N/FX2NC', 'FX3S', 'FX3G', 'Q00J', 'Q00UJ', 'Q00', 'Q00U',
    'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U', 'Q03UD', 'Q03UDE', 'Q03UDV',
    'Q04UDH', 'Q04UDEH', 'Q04UDPV', 'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH',
    'Q06UDV', 'Q06UDPV', 'Q10UDH', 'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH',
    'Q13UDH', 'Q13UDEH', 'Q13UDV', 'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H',
    'Q25PH', 'Q25PRH', 'Q26UDH', 'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH',
    'Q100UDEH', 'L02S', 'L06', 'L26', 'L26-BT',
))

# Controlled line changes, original 1.635.0.1 public reports and declaration
# getters. These scopes describe positions, not instruction availability.
PUBLIC_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'Q00J', 'Q00', 'Q01', 'Q02/Q02H', 'Q02PH', 'Q06H', 'Q06PH',
    'Q12H', 'Q12PH', 'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH',
    'FX3U/FX3UC',
))
PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS = frozenset((
    'Q00UJ', 'Q00U', 'Q01U', 'Q02U', 'Q03UD', 'Q04UDH', 'Q06UDH',
    'Q10UDH', 'Q13UDH', 'Q20UDH', 'Q26UDH', 'Q50UDEH', 'Q100UDEH',
))
PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'FX3G', 'FX3S', 'FX2N/FX2NC',
))
PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS = frozenset((
    'FX1N/FX1NC', 'FX1S', 'FX0N', 'FX1', 'FX0S/FX0', 'FXU/FX2C',
))
# Original SFC public reports, narrow/wide prefixes in actions and conditions.
# Each concrete menu is frozen in gxw-sfc-position-menus-20261014; this scope
# does not come from graph-reader or ordinary-ladder instruction support.
SFC_CHILD_INSTRUCTION_POSITION_CPUS = frozenset((
    'FX0S/FX0', 'FX0N', 'FX1', 'FX1S', 'FX1N/FX1NC', 'FXU/FX2C',
    'FX2N/FX2NC', 'FX3S', 'FX3G', 'FX3U/FX3UC',
    'Q00J', 'Q00UJ', 'Q00', 'Q00U', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDH', 'Q04UDEH', 'Q04UDV', 'Q04UDPV',
    'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH', 'Q06UDV', 'Q06UDPV', 'Q10UDH',
    'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDH', 'Q13UDEH', 'Q13UDV',
    'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H', 'Q25PH', 'Q25PRH', 'Q26UDH',
    'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH', 'Q100UDEH',
    'L02S', 'L02', 'L06', 'L26', 'L26-BT',
))


def _native_validation_sources(raw):
    from .container_writer import validate_cfb_streams
    from .project_metadata import logical_mapping, project_source_groups, read_project_text_context
    from .declarations import local_table_for, parse_declarations
    from .source_header import source_payload_offset
    from .models import GXWFormatError
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    metadata = [nested[key] for name, key in mapping.items() if name.endswith('.prj')]
    if len(metadata) != 1:
        raise GXWFormatError('native validation requires one project configuration')
    context = read_project_text_context(metadata[0])
    groups, _ = project_source_groups(outer['projectdatalist.xml'])
    sfc_groups, _ = project_source_groups(outer['projectdatalist.xml'], folder_type=8)
    sfc_labels = {group['labels'] for logical, group in sfc_groups.items()
                  if logical in mapping and mapping[logical] in nested
                  and nested[mapping[logical]][source_payload_offset(nested[mapping[logical]])] == 240}
    documents = {name: parse_declarations(nested[key], logical_name=name,
                    source_kind=240 if name in sfc_labels else None)
                 for name, key in mapping.items() if name.endswith('.lh')}
    programs, names = [], set()
    for logical, key in mapping.items():
        if not logical.endswith('.pou'):
            continue
        kind = nested[key][source_payload_offset(nested[key])]
        labels = local_table_for(documents, logical, sfc_groups if kind == 240 else groups)
        if labels not in mapping:
            raise GXWFormatError('native validation source has no paired declarations: ' + logical)
        declarations = documents[labels]
        name = declarations.owner_name
        if declarations.scope != 'local' or not name or name in names or kind not in (1, 193, 208, 240):
            raise GXWFormatError('native validation source is outside the current structured body binding: ' + logical)
        names.add(name)
        programs.append({'name': name, 'program_kind': kind, 'logical_name': logical, 'raw': nested[key],
                         'declaration_name': labels, 'declarations': declarations})
    if not 1 <= len(programs) <= NATIVE_SOURCE_CAPACITY:
        raise GXWFormatError('native validation source selection is empty or exceeds the bound')
    return context, programs


def _native_validation_source_body(raw):
    """Use the actual native body: SFC graph or structured source body."""
    from .source_header import source_payload_offset
    if raw[source_payload_offset(raw)] == 240:
        from .sfc_pou import frame_sfc_pou
        graph = frame_sfc_pou(raw)['graph']
        return raw[graph['offset']:graph['offset'] + graph['size']]
    if raw[source_payload_offset(raw)] == 1:
        from .token_pou import frame_token_pou
        frame_token_pou(raw)
    from .native_write import workspace_body
    return workspace_body(raw)


def _native_compilation_method(programs):
    from .source_header import source_payload_offset
    kinds = {raw[source_payload_offset(raw)] for raw in programs}
    return 'Compiler.ChangeSFCProgram' if kinds == {240} else None if 240 in kinds else 'Compiler.Build'


def _native_validation_sfc_children(raw):
    from .sfc_pou import frame_sfc_pou
    return [{'type': 68 if row['kind'] == 'zoom' else 66, 'name': row['name'],
             'body': raw[row['payload_offset']:row['tail_offset']]}
            for row in frame_sfc_pou(raw)['children']]


def sfc_child_source_scope(raw, *, cpu=None):
    """Describe current graph references without changing the native selection.

    Retained source remains selected by the existing collector. This inventory
    cannot replace original getters or establish a successful code check.
    """
    from .sfc_pou import parse_sfc_pou, sfc_step_action_reference
    source = parse_sfc_pou(raw)
    children = _native_validation_sfc_children(raw)
    if source.graph_tokens is None or source.diagnostics:
        return {'status': 'unresolved', 'children': [], 'reason': 'complete graph references are unavailable'}
    if any(row['kind'] in ('opaque', 'block-call-reference') for row in source.graph_records):
        return {'status': 'unresolved', 'children': [], 'reason': 'graph includes an unbound source reference'}
    action_references = [sfc_step_action_reference(row, cpu=cpu) for row in source.graph_records
                         if row['kind'] == 'step-action-reference']
    if any(row is None for row in action_references):
        return {'status': 'unresolved', 'children': [], 'reason': 'graph includes an implicit action reference'}
    actions = {row[1] for row in action_references}
    transitions = {row['references'][0] for row in source.graph_records if row['kind'] == 'transition-reference'}
    zooms = {entry['zoom'] for action in source.layout['actions'] if action['number'] in actions
             for entry in action['registrations']}
    transition_names = {row['name'] for row in source.layout['children']
                        if row['kind'] == 'transition' and row['number'] in transitions}
    return {'status': 'source-projected', 'native_check_promoted': False,
            'children': [{'type': row['type'], 'name': row['name'],
                          'role': 'referenced' if row['name'] in (zooms if row['type'] == 68 else transition_names) else 'retained'}
                         for row in children]}


def _native_validation_library_sources(raw):
    """Reuse current library ownership; never resolve a POU by bare name."""
    from .callable_sources import _user_library_owners
    from .container_writer import validate_cfb_streams
    from .declarations import parse_declarations
    from .models import GXWFormatError
    from .project_metadata import logical_mapping
    from .source_header import source_payload_offset
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    owners, issues, _ = _user_library_owners(outer['projectdatalist.xml'], streams)
    if issues:
        raise GXWFormatError('native validation library ownership is missing or ambiguous')
    programs, names = [], set()
    for labels, owner in owners.items():
        document = parse_declarations(streams[labels], logical_name=labels)
        logical = owner['source_program']
        kind = streams[logical][source_payload_offset(streams[logical])]
        identity = (owner['library'], owner['name'])
        if (document.scope != 'local' or document.owner_name != owner['name']
                or identity in names or kind not in (193, 208)):
            raise GXWFormatError('native validation library source is outside the structured body binding: ' + logical)
        names.add(identity)
        programs.append({'name': owner['name'], 'library': owner['library'], 'program_kind': kind,
                         'logical_name': logical, 'raw': streams[logical],
                         'declaration_name': labels, 'declarations': document})
    return programs


def native_validation_source_plan(raw, *, include_logical_names=False):
    """Bind local and namespaced sources; optional names are caller metadata.

    Logical filenames are not part of the strict native adapter wire format.
    """
    import base64
    context, programs = _native_validation_sources(raw)
    libraries = _native_validation_library_sources(raw)
    if len(programs) + len(libraries) > NATIVE_SOURCE_CAPACITY:
        from .models import GXWFormatError
        raise GXWFormatError('native validation source selection exceeds the bound')
    sfc = any(row['program_kind'] == 240 for row in programs)
    if any(row['program_kind'] == 1 for row in programs):
        from gxworks2.project_profiles import native_project_profile
        profile = native_project_profile(context['cpu'])
        if profile is None or not profile.program_supported:
            from .models import GXWFormatError
            raise GXWFormatError('simple Ladder source has no known programmable native CPU identity')
    if any(row['program_kind'] == 1 for row in programs) and (libraries or any(row['program_kind'] != 1 for row in programs)):
        from .models import GXWFormatError
        raise GXWFormatError('mixed simple Ladder and structured source validation is not established')
    if sfc and (libraries or any(row['program_kind'] != 240 for row in programs)):
        from .models import GXWFormatError
        raise GXWFormatError('mixed SFC and structured source conversion is not established')
    return {'cpu': context['cpu'], 'codepage': context['codepage'],
        **({'compilation_method': 'change_sfc_program'} if sfc else {}),
        'lexical_reader': None if sfc else native_code_reader_scope(context['cpu']), 'programs': [
        {'name': row['name'], 'program_kind': row['program_kind'],
         'body': base64.b64encode(_native_validation_source_body(row['raw'])).decode('ascii'),
         **({'children': [{**child, 'body': base64.b64encode(child['body']).decode('ascii')}
                         for child in _native_validation_sfc_children(row['raw'])]}
            if row['program_kind'] == 240 else {}),
         **({'logical_name': row['logical_name']} if include_logical_names else {})} for row in programs],
        'libraries': [{'library': row['library'], 'name': row['name'], 'program_kind': row['program_kind'],
                       'body': base64.b64encode(_native_validation_source_body(row['raw'])).decode('ascii')} for row in libraries]}


def _native_assignment_sources(raw, *, include_globals=True):
    from .container_writer import validate_cfb_streams
    from .declarations import parse_declarations
    from .project_metadata import logical_mapping
    _, programs = _native_validation_sources(raw)
    local = {p['name']: p['declarations'] for p in programs}
    if not include_globals:
        return local, {}
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    return (local,
            {name: parse_declarations(nested[key], logical_name=name) for name, key in mapping.items()
             if name.endswith(('.gh', '.lng')) and key in nested})


def native_inspection_queries(raw, supplied):
    """Bound business queries to this saved source before native I/O."""
    from .models import GXWFormatError
    if not isinstance(supplied, dict) or set(supplied) != {'assignments', 'locations', 'saved_code'}:
        raise GXWFormatError('invalid native inspection fields')
    if type(supplied['saved_code']) is not bool:
        raise GXWFormatError('saved_code must be a boolean')
    result = {'assignments': [], 'locations': [], 'saved_code': supplied['saved_code']}
    for key, fields, bound in (('assignments', {'source', 'name'}, 128), ('locations', {'resource', 'step'}, 64)):
        rows = supplied[key]
        if not isinstance(rows, list) or len(rows) > bound:
            raise GXWFormatError('native inspection query count exceeds bound')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) not in (
                    (fields, fields | {'scope'}) if key == 'assignments' else (fields,)):
                raise GXWFormatError('invalid native inspection query')
            if key == 'assignments' and row.get('scope', 'local') not in ('local', 'global'):
                raise GXWFormatError('assignment scope must be local or global')
            for field in fields - {'step'}:
                value = row[field]
                if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise GXWFormatError('invalid native inspection name')
            if key == 'locations' and (type(row['step']) is not int or not 0 <= row['step'] <= 0x7fffffff):
                raise GXWFormatError('native code step must be a nonnegative integer')
            identity = tuple(row[field] for field in sorted(fields))
            if key == 'assignments':
                identity += (row.get('scope', 'local'),)
            if identity in seen:
                raise GXWFormatError('duplicate native inspection query')
            seen.add(identity)
            result[key].append(dict(row))
    if result['assignments']:
        local, global_ = _native_assignment_sources(raw,
            include_globals=any(row.get('scope') == 'global' for row in result['assignments']))
        for row in result['assignments']:
            scope = row.get('scope', 'local')
            documents = global_ if scope == 'global' else local
            if row['source'] not in documents:
                raise GXWFormatError('assignment source must name a current ' + scope + ' declaration owner')
            matches = [(name, label) for name, doc in documents.items()
                       if scope == 'global' or name == row['source']
                       for label in doc.rows if label.name.casefold() == row['name'].casefold()]
            if len(matches) != 1 or matches[0][0] != row['source'] or matches[0][1].name != row['name']:
                raise GXWFormatError('assignment name is absent or ambiguous in the current ' + scope + ' declarations')
    return result


def project_native_inspection(raw, observation, snapshots, validation, requested):
    """Project original getters using the established compile/source binding.

    Saved code mismatch is an observation, never a save gate. Code locations
    describe this offline compilation; execution correspondence needs separate
    simulator/PLC evidence.
    """
    requested = native_inspection_queries(raw, requested)
    declarations = {}
    if requested['assignments']:
        local, global_ = _native_assignment_sources(raw,
            include_globals=any(row.get('scope') == 'global' for row in requested['assignments']))
        declarations = {(scope, name, row.name): row
                        for scope, documents in (('local', local), ('global', global_))
                        for name, doc in documents.items() for row in doc.rows}

    def assignment_name_bound(name, query):
        if name == query['name'] or name.startswith(query['name'] + '.'):
            return True
        # Original getters expand local arrays into indexed names. Bind those
        # names to the saved declaration rather than accepting any prefix.
        import re
        from .declarations import _type_parts
        from .models import GXWFormatError
        try:
            _, bounds = _type_parts(declarations[(query.get('scope', 'local'), query['source'], query['name'])].data_type)
        except GXWFormatError:
            return False
        match = re.fullmatch(re.escape(query['name']) + r'\[(-?[0-9]+(?:,-?[0-9]+)*)\]', name)
        if not bounds or match is None:
            return False
        try:
            indices = [int(part) for part in match[1].split(',')]
        except ValueError:
            return False
        return len(indices) == len(bounds) and all(low <= index <= high for index, (low, high) in zip(indices, bounds))

    events = (observation.get('validation') or {}).get('events', [])
    current = validation.get('compilation', {}).get('source_context', {}).get('status') == 'current'
    references = validation.get('source_references') or {}
    rows = references.get('rows') if references.get('status') == 'current' else None
    result = {'source_context': 'current' if current else 'unresolved',
              'execution_correspondence': 'unverified', 'assignments': [], 'locations': [],
              'saved_code': {'status': 'not_requested', 'resources': []}}
    for query in requested['assignments']:
        matches = [row for row in events if row.get('operation') == 'NativeDeviceAssignment' and row.get('query') == query]
        item = {'query': query, 'status': 'unresolved', 'assignments': []}
        if len(matches) == 1:
            row = matches[0]
            values = row.get('assignments')
            valid = (type(row.get('hresult')) is int and row['hresult'] >= 0 and type(row.get('code')) is int and row['code'] == 0
                     and type(row.get('count')) is int and isinstance(values, list) and row['count'] == len(values)
                     and len(values) <= 4096 and all(isinstance(v, dict) and set(v) == {'name', 'address', 'source', 'type', 'count'}
                         and all(isinstance(v[k], str) for k in ('name', 'address', 'source', 'type'))
                         and type(v['count']) is int and v['count'] > 0
                         and assignment_name_bound(v['name'], query) for v in values))
            if valid:
                if query.get('scope', 'local') == 'global':
                    # The measured name-only getter also returns same-named
                    # locals. Its empty source identifies project globals;
                    # retain that original field rather than inventing a POU.
                    values = [v for v in values if v['source'] == '']
                else:
                    valid = all(v['source'] == query['source'] for v in values)
            item.update(hresult=row.get('hresult'), code=row.get('code'))
            if current and valid:
                item.update(status='resolved' if values else 'not_assigned', assignments=values)
        result['assignments'].append(item)
    for query in requested['locations']:
        matches = [row.get('source_location') for row in events if row.get('operation') == 'NativeRequestedCodeLocation'
                   and (row.get('source_location') or {}).get('resource') == query['resource']
                   and (row.get('source_location') or {}).get('code_step') == query['step']]
        projection = {'status': 'unresolved', 'reason': 'current compile source and one original getter result are required'}
        native_result = None
        if len(matches) == 1:
            native_result = {key: matches[0].get(key) for key in ('hresult', 'code')}
            if matches[0].get('reason'):
                projection['reason'] = matches[0]['reason']
        if current and len(matches) == 1:
            getter = matches[0]
            location = getter.get('location') or {}
            if all(type(getter.get(key)) is int and getter[key] == 0 for key in ('hresult', 'code')):
                projection = project_native_source_identity(getter.get('source_object'), pou=location.get('pou'),
                    program_kind=location.get('program_kind'), project_id=(observation.get('validation') or {}).get('project'),
                    native_version=observation.get('native_version'), library=location.get('library'))
                if projection['status'] == 'source-resolved':
                    projection['native_position'] = {key: location.get(key) for key in
                        ('network', 'start_step', 'step_count', 'element_id', 'action_transition_present')}
                    if location.get('program_kind') == 193 and type(location.get('start_step')) is int and location['start_step'] >= 0:
                        projection['source_line'] = location['start_step'] + 1
                    scoped = [r for r in rows or [] if r.get('program_kind') == location.get('program_kind')
                        and r.get('source') == location.get('pou') and r.get('library') == location.get('library')
                        and r.get('resource') == query['resource'] and r.get('task') and r.get('instance')
                        and (r.get('top') == location.get('start_step') and r.get('attribute') in (1, 2)
                             if location.get('program_kind') == 193 else r.get('network') == location.get('network'))]
                    binding = bind_native_instance_interval({**getter, 'query': {'resource': query['resource'], 'start_step': query['step']}},
                        scoped, getter.get('instance_ranges'))
                    interval = binding.get('interval')
                    if interval is not None:
                        ref = interval['native_range_evidence']['original_reference']
                        projection['instance'] = {'status': 'instance-resolved', 'task': ref['task'], 'name': ref['instance']}
                    else:
                        projection['instance'] = {'status': 'unresolved', 'reason': binding.get('reason'),
                            'candidates': [{'task': r['task'], 'name': r['instance']} for r in scoped]}
        result['locations'].append({'query': query, 'native_result': native_result, **projection})
    if requested['saved_code']:
        def buffers(event):
            channels = event.get('channels')
            if not isinstance(channels, list) or [c.get('channel') for c in channels] != [0, 1, 2]:
                return None
            data = [snapshots.get(c.get('file')) for c in channels]
            return data if all(isinstance(b, bytes) and type(c.get('bytes')) is int and len(b) == c['bytes']
                               for b, c in zip(data, channels)) else None
        saved = [row for row in events if row.get('operation') == 'SavedResourceCode']
        generated = [row for row in events if row.get('operation') == 'Resource']
        for entry in saved:
            name = entry.get('resource')
            matches = [row for row in generated if row.get('name') == name]
            before = buffers(entry)
            after = buffers(matches[0]) if len(matches) == 1 else None
            item = {'resource': name, 'status': 'unresolved', 'channels': []}
            if current and before is not None and after is not None and sum(r.get('resource') == name for r in saved) == 1:
                item['channels'] = [{'channel': i, 'saved_bytes': len(a), 'generated_bytes': len(b), 'matches': a == b}
                                    for i, (a, b) in enumerate(zip(before, after))]
                item['status'] = 'matches' if before == after else 'differs'
            result['saved_code']['resources'].append(item)
        complete = (current and saved and len(saved) == len(generated) and
                    all(r['status'] != 'unresolved' for r in result['saved_code']['resources']))
        result['saved_code']['status'] = ('matches' if all(r['status'] == 'matches' for r in result['saved_code']['resources'])
                                         else 'differs') if complete else 'unresolved'
    return result


def bind_native_ladder_candidate(raw, program_ir, logical_name):
    """Compare saved simple Ladder with the candidate's complete instruction list.

    Source/CPU/task and current compilation are checked separately. Unsupported
    lexical forms cannot become a successful candidate binding.
    """
    from .models import GXWFormatError
    from .token_listing import decode_token_program
    from .token_pou import frame_token_pou
    from plc.device_identity import canonical_operand, canonical_reference_device
    from plc.ir import ir_to_ladder, lower_rung_instructions
    from gxworks2.project_profiles import native_project_profile
    context, sources = _native_validation_sources(raw)
    source_cpu = native_project_profile(context['cpu'])
    if source_cpu is None or not source_cpu.program_supported:
        raise GXWFormatError('simple Ladder candidate binding is not established for this CPU')
    selected = [row for row in sources if row['logical_name'] == logical_name and row['program_kind'] == 1]
    if len(selected) != 1 or not isinstance(program_ir, dict):
        raise GXWFormatError('simple Ladder candidate or saved source is missing or ambiguous')
    candidate_cpu = native_project_profile((program_ir.get('plc') or {}).get('cpu'))
    if candidate_cpu is None or candidate_cpu.cpu != native_project_profile(context['cpu']).cpu:
        raise GXWFormatError('simple Ladder candidate CPU differs from the saved source')
    # These are binary lexical grammars, not instruction/device permission
    # profiles. Exact CPU selection and the native source getters remain bound
    # independently, and any opaque token prevents a candidate match.
    lexical_profile = 'fx3u' if source_cpu.series[0] == 'FXCPU' else 'q03udv'
    listing = decode_token_program(frame_token_pou(selected[0]['raw']), profile=lexical_profile,
                                   text_encoding=context['text_encoding'])
    actual = listing.instruction_ir()
    if len(listing.records) != len(listing.instructions):
        raise GXWFormatError('unsupported source records prevent a complete simple Ladder candidate binding')
    expected = [{key: row[key] for key in ('op', 'args')}
                for rung in ir_to_ladder(program_ir)['rungs'] for row in lower_rung_instructions(rung)]
    expected.append({'op': 'END', 'args': []})
    def canonical(rows):
        return [{'op': row['op'], 'args': [canonical_reference_device(canonical_operand(arg)) for arg in row['args']]} for row in rows]
    if canonical(actual) != canonical(expected):
        raise GXWFormatError('saved simple Ladder instruction list differs from the current candidate')
    return {'status': 'current', 'cpu': context['cpu'], 'program': logical_name,
            'instructions': actual, 'lexical_profile': lexical_profile,
            'provenance': 'complete saved source token listing versus shared Core candidate lowering; CPU/source/task and native acceptance checked separately'}


def native_code_reader_scope(cpu):
    """Observed converter contexts, bound to exact CPU menu selections."""
    if cpu == 'Q03UDV':
        return {'scope_cpu': cpu, 'profile': 'q03udv', 'module': 'ECCodeGenerator2.dll',
                'version': '15.41', 'native_cpu': 209, 'native_versions': [25]}
    if cpu == 'FX3U/FX3UC':
        return {'scope_cpu': cpu, 'profile': 'fx3u', 'module': 'ECCodeGeneratorFX2.dll',
                'version': '15.31', 'native_cpu': 520, 'native_versions': []}
    if cpu == 'FXU/FX2C':
        # Original UI project Param.wpa, then original 15.31 Open/decode/prefix
        # reads of this CPU's generated bytes; the grammar is not a CPU alias.
        return {'scope_cpu': cpu, 'profile': 'fx', 'module': 'ECCodeGeneratorFX2.dll',
                'version': '15.31', 'native_cpu': 516, 'native_versions': []}
    if cpu == 'FX3G':
        # Original FX3G/FX3GC menu project stores FX3G/521. Its own generated
        # bytes agree with original 15.31 lexical and prefix-step reads.
        return {'scope_cpu': cpu, 'profile': 'fx3g', 'module': 'ECCodeGeneratorFX2.dll',
                'version': '15.31', 'native_cpu': 521, 'native_versions': []}
    return None


def bind_native_code_lexical_read(events, generated, snapshots, *, cpu, codepage):
    """Cross-check original converter text and prefix steps against actual code.

    The converter receives an exact copy of the retained generated primary
    buffer after checking. A missing, conflicting or partial observation never
    enables node/port correlation; source/check freshness is checked separately.
    """
    from .models import GXWFormatError
    from .token_pou import parse_token_fragment
    from .token_listing import (decode_token_program, native_il_projection,
                                TokenInstruction, TokenText, TokenLabel)
    from plc.device_identity import canonical_operand
    scope = native_code_reader_scope(cpu)
    reads = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeCodeLexicalRead']
    modules = [row for row in events if row.get('operation') == 'OwnedBackendModule'
               and scope is not None and row.get('name') == scope['module']]
    ends = [i for i, row in enumerate(events) if row.get('operation') in ('ProgramCheckRawProgress', 'ProgramCheckOriginalProgress')
            and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    results = []
    for resource, buffers in generated.items():
        body = buffers[0]
        base = {'resource': resource, 'status': 'unresolved', 'records': [], 'public_check_promoted': False}
        if not body:
            continue
        matching = [(i, row) for i, row in reads if row.get('resource') == resource]
        if scope is None or not matching:
            results.append({**base, 'status': 'not_observed', 'reason': 'no observed original lexical reader for this CPU/resource'})
            continue
        if len(matching) != 1 or not modules or any(row.get('version') != scope['version'] for row in modules):
            results.append({**base, 'reason': 'original lexical observation or converter version is missing or conflicting'})
            continue
        event_index, row = matching[0]
        try:
            calls = row.get('calls')
            if (any(row.get(key) != value for key, value in scope.items())
                    or row.get('completed') is not True or not ends or event_index <= max(ends)
                    or row.get('provenance') != 'original ChangePToILcode and GetStepSize; retained generated primary bytes after check; not execution'
                    or not isinstance(calls, dict) or calls.get('object_new') is not True
                    or any(type(calls.get(key)) is not int or calls[key] != 0 for key in ('open', 'decode', 'close'))
                    or (type(calls.get('set_version')) is not int or calls['set_version'] != 0
                        if scope['native_versions'] else calls.get('set_version') is not None)
                    or not 0 < len(body) <= 32768
                    or type(row.get('body_bytes')) is not int or row['body_bytes'] != len(body)
                    or type(row.get('provided_bytes')) is not int or row['provided_bytes'] != len(body) + 1
                    or type(row.get('consumed_bytes')) is not int or row['consumed_bytes'] != len(body)
                    or not isinstance(row.get('input_file'), str) or snapshots.get(row['input_file']) != body + b'\0'):
                raise GXWFormatError('original lexical input, scope, lifecycle or ordering is unbound')
            output = snapshots.get(row['output_file']) if isinstance(row.get('output_file'), str) else None
            if (not isinstance(output, bytes) or type(row.get('output_bytes')) is not int
                    or not 0 < row['output_bytes'] <= 4 * 1024 * 1024 or len(output) != row['output_bytes']):
                raise GXWFormatError('original lexical output bytes are missing or truncated')
            program = parse_token_fragment(body, 0, len(body))
            listing = decode_token_program(program, profile=scope['profile'], text_encoding='cp' + str(codepage))
            vendor = native_il_projection(output, encoding='cp' + str(codepage))
            if listing.gaps or vendor['text_gaps']:
                raise GXWFormatError('an opaque lexical region prevents complete correlation')
            ours = []
            for item in listing.records:
                if isinstance(item, TokenInstruction):
                    ours.append({'kind': 'instruction', 'op': item.mnemonic, 'args': list(item.args)})
                elif isinstance(item, TokenText):
                    ours.append({'kind': item.role, 'text': item.text})
                elif isinstance(item, TokenLabel):
                    ours.append({'kind': 'label', 'text': item.text})
            def canonical(item):
                return {**item, 'args': [canonical_operand(arg) for arg in item['args']]} if item['kind'] == 'instruction' else item
            if [canonical(item) for item in ours] != [canonical(item) for item in vendor['records']]:
                raise GXWFormatError('original converter text differs from the Core instruction listing')
            prefixes = row.get('prefixes')
            boundaries = [token.offset + len(token.raw) for token in program.tokens]
            if (not isinstance(prefixes, list) or not 1 <= len(prefixes) <= 4096 or len(prefixes) != len(boundaries)
                    or any(not isinstance(prefix, dict) or type(prefix.get('input_bytes')) is not int
                           or prefix['input_bytes'] != boundary
                           or type(prefix.get('return_code')) is not int or prefix['return_code'] != 0
                           or type(prefix.get('steps')) is not int or prefix['steps'] < 0
                           for prefix, boundary in zip(prefixes, boundaries))):
                raise GXWFormatError('original prefix step reads are missing, rejected or duplicated')
            expected_steps = {token.offset + len(token.raw): item.step + getattr(item, 'step_width', 0)
                              for item in listing.records for token in item.tokens if type(item.step) is int}
            if any(prefix['steps'] != expected_steps.get(prefix['input_bytes']) for prefix in prefixes):
                raise GXWFormatError('original prefix steps differ from the Core stored widths')
            steps = {0: 0, **{prefix['input_bytes']: prefix['steps'] for prefix in prefixes}}
            records = []
            for index, item in enumerate(listing.instructions):
                offset = item.tokens[0].offset
                if type(item.step) is not int or steps.get(offset) != item.step:
                    raise GXWFormatError('original prefix steps differ from the Core code offsets')
                records.append({'record_index': index, 'native_step': item.step, 'source_offset': offset,
                    'source_end': item.tokens[-1].offset + len(item.tokens[-1].raw),
                    'op': item.mnemonic, 'args': list(item.args)})
            results.append({**base, 'status': 'current', 'records': records, 'body_bytes': len(body),
                            'provenance': row['provenance'], 'scope': scope})
        except (GXWFormatError, UnicodeError, LookupError) as error:
            results.append({**base, 'reason': str(error)})
    return results


def native_import_selection(raw, imported_hdb):
    """Compare mapped HDB streams, including global declarations/libraries.

    Imported caches are only input-preservation facts; their bytes never prove
    that compilation or checking used current code.
    """
    from .container_writer import validate_cfb_streams
    from .project_metadata import logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    before, actual = validate_cfb_streams(outer['_hdb']), validate_cfb_streams(imported_hdb)
    sources, outer_only = [], []
    for name, key in mapping.items():
        if key not in before and name in outer:
            # Legacy templates can register the outer Project.gd2 alongside
            # HDB IDs. It has no original HDB payload to compare.
            outer_only.append({'logical_name': name, 'stream': key, 'location': 'outer GXW, outside imported HDB'})
            continue
        sources.append({'logical_name': name, 'stream': key,
                        'status': 'current' if key in before and key in actual and actual[key] == before[key] else 'stale_or_missing'})
    return {'status': 'current' if sources and all(row['status'] == 'current' for row in sources) else 'unresolved',
            'streams': sources, 'outer_only_streams': outer_only,
            'provenance': 'input GXW versus original imported workspace HDB; exact bytes'}


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


def bind_native_check_input(submitted, completed, *, generated, snapshots,
                            native_version, checker_version):
    """Compare the original task's retained primary code to this compilation.

    ECCompiler 15.50 uses its original 0x39f30 helper to find the code body.
    Compare both owned snapshots and their native framing; publication, task
    completion, diagnostics and source positions remain separate evidence.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if not isinstance(submitted, dict) or not isinstance(completed, dict):
        return {**unresolved, 'reason': 'original submission and completion copies are required'}
    backend = submitted.get('input_backend')
    sfc = isinstance(backend, dict) and backend.get('module') == 'DZDataABS_Compiler.dll'
    if native_version != '1.635.0.1' or checker_version != ('1.635.0.1' if sfc else '15.50'):
        return {**unresolved, 'reason': 'unobserved native task owner or checker version'}
    target = _id(submitted.get('target'))
    if (target is None or not any(target)
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in target)
            or target != _id(completed.get('target'))
            or submitted.get('stage') != 'submitted' or completed.get('stage') != 'completed'
            or submitted.get('manager') != completed.get('manager') or not submitted.get('manager')
            or type(submitted.get('mask')) is not int
            or not (submitted['mask'] == -1 or 0 < submitted['mask'] <= 0x7fffffff)
            or submitted.get('mask') != completed.get('mask')):
        return {**unresolved, 'reason': 'native task identities or stages differ'}
    stages = []
    for event in (submitted, completed):
        rows = event.get('rows')
        if (event.get('operation') != 'OwnedCheckInput'
                or event.get('provenance') != 'original ProcessManager.ProgramCheck owned code copy; read-only; no interception'
                or event.get('compiler_member_offset') != (8 if sfc else 12) or event.get('manager_records_offset') != 32
                or not isinstance(rows, list) or not 1 <= len(rows) <= 1024):
            return {**unresolved, 'reason': 'original owned input table is not bound'}
        if sfc and event.get('input_backend') != {
                'module': 'DZDataABS_Compiler.dll', 'version': '1.635.0.1',
                'progress_rva': 0x73e5, 'program_check_rva': 0x4810}:
            return {**unresolved, 'reason': 'original SFC checker interface is not bound'}
        stage = {}
        for row in rows:
            name, framing = row.get('resource'), row.get('framing') or {}
            if 'resource_name' in row:
                observed_name = row['resource_name']
                if (not isinstance(observed_name, dict) or type(observed_name.get('codepage')) is not int
                        or not isinstance(observed_name.get('raw_hex'), str)
                        or not 2 <= len(observed_name['raw_hex']) <= 2046):
                    return {**unresolved, 'reason': 'original task resource name bytes are not bound'}
                try:
                    name_bytes = bytes.fromhex(observed_name['raw_hex'])
                    decoded_name = name_bytes.decode('cp' + str(observed_name['codepage']), errors='strict')
                except (LookupError, ValueError, UnicodeError):
                    return {**unresolved, 'reason': 'original task resource name cannot be decoded'}
                if b'\0' in name_bytes or decoded_name != name:
                    return {**unresolved, 'reason': 'original task resource name decoding differs'}
            full = snapshots.get(row.get('file'))
            reader_bound = (framing.get('reader_module') == 'DZDataABS_SICConverter.dll'
                and framing.get('reader_version') == '1.635.0.1'
                and framing.get('provenance') == 'observed SFC length prefix; remaining header bytes uninterpreted') if sfc else (
                framing.get('reader_module') == 'ECCompiler_IEC.dll'
                and framing.get('reader_version') == '15.50' and framing.get('reader_rva') == 0x39f30
                and framing.get('code') == 0
                and framing.get('provenance') == 'original checker envelope helper; optional tables omitted; read-only')
            if (not isinstance(name, str) or not name or name in stage or row.get('tag') != -1
                    or not isinstance(full, bytes) or len(full) != row.get('bytes') or len(full) < 4
                    or not reader_bound):
                return {**unresolved, 'reason': 'saved original input or envelope reader is not bound'}
            first = int.from_bytes(full[:2], 'little')
            if sfc:
                if first < 2 or first > len(full) - 2:
                    return {**unresolved, 'reason': 'SFC input length prefix is truncated'}
                second = int.from_bytes(full[first:first + 2], 'little')
                offset = first + second
                if (second < 2 or offset > len(full) or framing.get('first_length') != first
                        or framing.get('second_length') != second or framing.get('body_offset') != offset
                        or framing.get('body_bytes') != len(full) - offset):
                    return {**unresolved, 'reason': 'SFC input length prefix differs from its buffer'}
                stage[name] = (full, full[offset:])
                continue
            mode_length_offset = full[2] + 2
            if first > len(full) - 2 or mode_length_offset >= len(full):
                return {**unresolved, 'reason': 'native code envelope is truncated'}
            second = int.from_bytes(full[first:first + 2], 'little')
            offset, mode_offset = first + second, full[2] + full[mode_length_offset] + 4
            if (offset > len(full) or mode_offset >= len(full)
                    or framing.get('first_length') != first or framing.get('second_length') != second
                    or framing.get('body_offset') != offset or framing.get('body_bytes') != len(full) - offset
                    or framing.get('mode') != full[mode_offset]):
                return {**unresolved, 'reason': 'original envelope result differs from its buffer'}
            stage[name] = (full, full[offset:])
        stages.append(stage)
    if set(stages[0]) != set(stages[1]) or set(stages[0]) != set(generated):
        return {**unresolved, 'reason': 'task resources do not match the selected generated resources'}
    if any(not isinstance(value, bytes) or not value for value in generated.values()):
        return {**unresolved, 'reason': 'current generated primary code is missing or empty'}
    stable = all(stages[0][name][0] == stages[1][name][0] for name in generated)
    rows = [{'resource': name, 'body_bytes': len(stages[1][name][1]),
             'status': 'current' if all(stage[name][1] == generated[name] for stage in stages) else 'stale'}
            for name in generated]
    return {**unresolved, 'status': 'unresolved' if not stable else
            'current' if all(row['status'] == 'current' for row in rows) else 'stale',
            'target': list(target), 'mask': submitted.get('mask'),
            'task_copy_stable': stable, 'resources': rows,
            'coverage': 'primary code consumed by original SFC checker; remaining header fields uninterpreted' if sfc
                else 'primary code consumed by original ECCompiler; other channels are separate',
            **({'reason': 'task code copy changed during the check'} if not stable else {})}


def bind_native_source_snapshot(program_raw, *, body_id, reads, snapshots):
    """Bind source offsets to the last original getter read of the same body.

    Equal compiled code or matching graph coordinates cannot establish source
    offsets. A semantically equivalent text edit can move later records. The
    caller supplies independently saved getter buffers, not stored match flags.
    Source framing remains owned by the existing Core workspace-body reader.
    """
    missing = {'status': 'unresolved'}
    identity = _id(body_id)
    if (identity is None or not any(identity)
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)):
        return {**missing, 'reason': 'verified native source body identity is missing'}
    matching = [row for row in reads if row.get('operation') == 'NativeBodyRead'
                and _id(row.get('body')) == identity]
    if not matching:
        return {**missing, 'reason': 'original native source body read is missing'}
    latest = matching[-1]
    filename, size = latest.get('file'), latest.get('bytes')
    if (latest.get('provenance') != 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'
            or not isinstance(filename, str) or type(size) is not int or size < 0):
        return {**missing, 'reason': 'original native source body read is not bound'}
    current = snapshots.get(filename)
    if not isinstance(current, bytes) or len(current) != size:
        return {**missing, 'reason': 'saved original native body buffer is missing or differs in size'}
    from .models import GXWFormatError
    try:
        expected = _native_validation_source_body(program_raw)
    except GXWFormatError:
        return {**missing, 'reason': 'source snapshot framing is outside the observed workspace body'}
    return {'status': 'current' if expected == current else 'stale',
            'body_id': list(identity), 'native_snapshot': filename,
            'source_body_bytes': len(expected), 'native_body_bytes': size,
            'provenance': 'source bytes versus last original getter read of this body'}


def project_native_source_identity(source, *, pou, program_kind, project_id, native_version, library=''):
    """Verify the original namespace, body, POU parent and language reads."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}

    def native_id(value):
        result = _id(value)
        return result if result is not None and any(result) and all(
            type(word) is int and 0 <= word <= 0xffffffff for word in result) else None

    def accepted(value):
        return isinstance(value, dict) and value.get('hresult') == 0 and value.get('code') == 0

    project = native_id(project_id)
    if native_version != '1.635.0.1' or project is None:
        return {**unresolved, 'reason': 'unobserved native version or missing project identity'}
    source = source if isinstance(source, dict) else {}
    kind = program_kind
    if library:
        if (not isinstance(library, str) or not isinstance(pou, str) or not pou
                or type(kind) is not int or kind not in (193, 208)
                or source.get('status') != 'verified-source-object'
                or native_id(source.get('parent')) != project
                or source.get('library') != library or source.get('pou') != pou):
            return {**unresolved, 'reason': 'no verified user-library source identity'}
        lookup = source.get('lookup') or {}
        namespace, owner, body = (source.get(key) or {} for key in ('namespace_object', 'owner', 'body'))
        namespace_id, owner_id, body_id = (native_id(lookup.get(key)) for key in ('namespace_id', 'owner_id', 'body_id'))
        namespace_parent, owner_parent, body_parent, language = (source.get(key) or {} for key in
            ('namespace_parent', 'owner_parent', 'body_parent', 'language'))
        library_collection = native_id(namespace_parent.get('collection'))
        pou_collection = native_id(owner_parent.get('collection'))
        identities = (project, namespace_id, owner_id, body_id, library_collection, pou_collection)
        if (None in identities or len(set(identities)) != len(identities)
                or any(lookup.get(key) != value for key, value in
                       (('namespace_type', 0x300), ('owner_type', 0x30b), ('body_type', 0x30d)))
                or any(type(lookup.get(prefix + key)) is not int or lookup[prefix + key] != 0
                       for prefix in ('namespace_', 'owner_', 'body_') for key in ('hresult', 'code'))
                or native_id(lookup.get('namespace_parent')) != project
                or native_id(lookup.get('owner_parent')) != namespace_id
                or native_id(lookup.get('body_parent')) != namespace_id):
            return {**unresolved, 'reason': 'library source lookup identities or namespace differ'}
        for facts, identity, data_type, name in ((namespace, namespace_id, 0x301, library),
                                               (owner, owner_id, 0x30b, pou), (body, body_id, 0x30d, None)):
            if (native_id(facts.get('id')) != identity or not accepted(facts.get('read_type'))
                    or facts['read_type'].get('data_type') != data_type
                    or not accepted(facts.get('read_name'))
                    or not isinstance(facts['read_name'].get('name'), str) or not facts['read_name']['name']
                    or (name is not None and facts['read_name']['name'] != name)):
                return {**unresolved, 'reason': 'library source type or name differs'}
        if (any(not accepted(row) for row in (namespace_parent, owner_parent, body_parent, language))
                or any(type(namespace_parent.get(key)) is not int or namespace_parent[key] != 0
                       for key in ('collection_hresult', 'collection_code'))
                or any(type(owner_parent.get(key)) is not int or owner_parent[key] != 0
                       for key in ('collection_hresult', 'collection_code', 'collection_parent_hresult', 'collection_parent_code'))
                or native_id(namespace_parent.get('id')) != library_collection
                or native_id(owner_parent.get('id')) != pou_collection
                or native_id(owner_parent.get('collection_parent')) != namespace_id
                or native_id(body_parent.get('id')) != owner_id
                or native_id(language.get('object_id')) != owner_id
                or language.get('value') != kind or language.get('expected') != kind):
            return {**unresolved, 'reason': 'library source parent or language differs'}
        return {'status': 'source-resolved', 'public_check_promoted': False,
                'source': {'project_id': list(project), 'library': library, 'library_id': list(namespace_id),
                           'pou': pou, 'body_id': list(body_id), 'pou_id': list(owner_id), 'program_kind': kind}}
    if library != '':
        return {**unresolved, 'reason': 'source namespace is unavailable'}
    if (not isinstance(pou, str) or not pou
            or kind not in (1, 193, 208, 240) or source.get('status') != 'verified-source-object'
            or native_id(source.get('parent')) != project
            or source.get('lookup_type') != 32 or source.get('name') != pou):
        return {**unresolved, 'reason': 'no verified project-local source identity'}
    lookup, owner_lookup = source.get('lookup') or {}, source.get('owner_lookup') or {}
    body, owner = source.get('body') or {}, source.get('owner') or {}
    parent, language = source.get('body_parent') or {}, source.get('language') or {}
    body_id, owner_id = native_id(lookup.get('id')), native_id(owner_lookup.get('id'))
    if (not accepted(lookup) or not accepted(owner_lookup) or owner_lookup.get('lookup_type') != 26
            or body_id is None or owner_id is None or body_id == owner_id
            or native_id(body.get('id')) != body_id or native_id(owner.get('id')) != owner_id
            or not accepted(parent) or native_id(parent.get('id')) != owner_id):
        return {**unresolved, 'reason': 'source body and POU parent identities disagree'}
    if (not accepted(body.get('read_type')) or body['read_type'].get('data_type') != 32
            or not accepted(body.get('read_name')) or not body['read_name'].get('name')
            or not accepted(owner.get('read_type')) or owner['read_type'].get('data_type') != (60 if kind == 240 else 26)
            or not accepted(owner.get('read_name')) or owner['read_name'].get('name') != pou
            or not accepted(language) or native_id(language.get('object_id')) != owner_id
            or language.get('value') != kind or language.get('expected') != kind):
        return {**unresolved, 'reason': 'native source type, name or POU language does not match'}
    return {'status': 'source-resolved', 'public_check_promoted': False,
            'source': {'project_id': list(project), 'pou': pou,
                       'body_id': list(body_id), 'pou_id': list(owner_id), 'program_kind': kind}}


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version, references=None):
    """Bind a raw or typed public resource diagnostic to its queried POU.

    The observed public resource form carries generated IL positions and no
    library field. Its projected body ID is compared separately; it cannot
    replace the original code-location and workspace identity queries.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    def text(value):
        return value.get('text') if isinstance(value, dict) else None
    public = diagnostic.get('report_interface') == 'public-compiler' or diagnostic.get('record_size') == 100
    resource_report = (
        diagnostic.get('report_interface') == 'public-compiler'
        and type(diagnostic.get('record_size')) is int and diagnostic['record_size'] == 100
        and diagnostic.get('instance_kind') == 6 and diagnostic.get('program_kind') == 1
        and text(diagnostic.get('instance')) == text(diagnostic.get('name'))
    ) if public else text(diagnostic.get('library')) == ''
    if (diagnostic.get('kind') not in (2, 3) or type(diagnostic.get('code')) is not int
            or query.get('hresult') != 0 or query.get('code') != 0
            or not resource_report
            or text(diagnostic.get('name')) != query.get('resource')
            or type(diagnostic.get('step')) is not int or diagnostic['step'] < 0
            or diagnostic['step'] != query.get('code_step')):
        return {**unresolved, 'reason': 'original diagnostic and native resource query do not match'}
    location = query.get('location') or {}
    identity = project_native_source_identity(query.get('source_object'), pou=location.get('pou'),
        program_kind=location.get('program_kind'), project_id=project_id, native_version=native_version,
        library=location.get('library'))
    if identity['status'] != 'source-resolved':
        return identity
    result = {**identity, 'original_diagnostic': diagnostic,
            'resource': query['resource'], 'diagnostic_step': query['code_step'],
            'native_position': {key: location.get(key) for key in
                                ('network', 'start_step', 'step_count', 'element_id',
                                 'action_transition_present')},
            'node_port_resolution': 'not-established',
            'provenance': 'original native location and workspace identity calls'}
    kind, pou = location.get('program_kind'), location.get('pou')
    if references is not None and kind in (193, 208):
        scoped = [row for row in references if row.get('program_kind') == kind
                  and row.get('source') == pou and row.get('library') == location.get('library')
                  and row.get('resource') == query['resource'] and row.get('task') and row.get('instance')
                  and (row.get('top') == location.get('start_step') and row.get('attribute') in (1, 2)
                       if kind == 193 else row.get('network') == location.get('network'))]
        adapted = {**query, 'query': {'resource': query['resource'], 'start_step': query['code_step']}}
        binding = bind_native_instance_interval(adapted, scoped, query.get('instance_ranges'))
        interval = binding.pop('interval')
        if interval is None:
            result['instance_projection'] = {'status': 'unresolved', **binding}
        else:
            reference = interval['native_range_evidence']['original_reference']
            result['instance_projection'] = {'status': 'instance-resolved',
                'instance': reference['instance'], 'task': reference['task'],
                'compiled_interval': interval, 'node_port_resolution': 'not-established'}
    return result


def _debug_st_interval(query, debug, references, encoding):
    """Require one saved ST element matching the independent native location.

    Macro FBs can be inside the parent FBD interval. A native ST location
    distinguishes that child from the parent; stored order alone is not used.
    """
    if debug is None or not encoding:
        return None
    native, location = query['query'], query['location']
    step, line = native['start_step'], location['start_step']
    candidates = []
    for index, element in enumerate(debug.elements):
        try:
            resource = element.resource_bytes.decode(encoding)
            names = [name.decode(encoding) for name in element.names]
        except (UnicodeError, LookupError):
            return None
        if (element.kind_code != 193 or resource != native['resource']
                or names[0] != location['library']
                or names[2] != location['pou']
                or not element.linked_step_start <= step <= element.linked_step_end):
            continue
        table = debug.offset_tables[element.offset_table_index]
        relative = step - element.linked_step_start
        rows = [row for row in table.rows if table.kind_code == 193 and row[0] >= 0
                and row[3] == -1 and row[4] & 7 == 7
                and row[1] <= relative <= row[2] and element.fields[0] + row[0] == line]
        if len(rows) != 1:
            continue
        instance, member = names[3:5]
        if member:
            instance += '.' + member
        matches = [row for row in references if row['instance'] == instance]
        if len(matches) != 1:
            continue
        reference = matches[0]
        candidates.append({'name': reference['task'] + '.' + instance,
            'step_start': element.linked_step_start,
            'step_count': element.linked_step_end - element.linked_step_start + 1,
            'token_offset': None, 'token_length': None,
            'debug_evidence': {'element_index': index, 'element_offset': element.offset,
                'raw_element_hex': element.raw.hex(), 'offset_table_offset': table.offset,
                'raw_offset_row': list(rows[0]), 'native_location_matched': True,
                'encoding': encoding,
                'origin': 'saved same-compile debug element; not primary link map'}})
    return candidates[0] if len(candidates) == 1 else None


def bind_native_instance_interval(query, references, evidence):
    """Require complete original GetPCodeRange responses for native instances.

    The observed 1.635.0.1 ordinary interface accepts a full instance path
    without a task prefix.
    ST uses the observed single-line query. FBD retains every source position
    field returned by GetPOULocation, including its -1 span; it is not a bbox.
    An accepted call can still return a null-resource/-1 sentinel. Inactive
    declarations and cached variable references are not executable intervals.
    """
    native, location = query['query'], query['location']
    missing = {'interval': None}
    kind = location.get('program_kind')
    if kind not in (193, 208):
        return {**missing, 'reason': 'source language outside observed native instance queries'}
    if (not isinstance(evidence, dict) or evidence.get('status') != 'completed'
            or evidence.get('resource') != native.get('resource')
            or evidence.get('diagnostic_step') != native.get('start_step')):
        return {**missing, 'reason': 'native instance range queries incomplete or mismatched'}
    candidates = evidence.get('candidates')
    if not isinstance(candidates, list):
        return {**missing, 'reason': 'native instance range responses unavailable'}
    expected = {(row['task'], row['instance']) for row in references}
    seen, matches = set(), []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return {**missing, 'reason': 'malformed native instance range response'}
        row, source = candidate.get('original_reference'), candidate.get('query') or {}
        if not isinstance(row, dict) or row not in references:
            return {**missing, 'reason': 'native interval reference differs from current source references'}
        identity = (row['task'], row['instance'])
        if identity in seen:
            return {**missing, 'reason': 'duplicate native instance range response'}
        seen.add(identity)
        expected_source = {'library': location.get('library', ''), 'pou': row['instance'], 'program_kind': kind,
            'network': location.get('network'), 'start_step': location.get('start_step'),
            'step_count': 1 if kind == 193 else location.get('step_count'), 'element_id': location.get('element_id')}
        if (source != expected_source
                or any(type(source.get(key)) is not int for key in
                       ('program_kind','network','start_step','step_count','element_id'))
                or any(type(candidate.get(key)) is not int or candidate[key] != 0 for key in ('hresult','code'))):
            return {**missing, 'reason': 'original native source range query failed or differs'}
        value = candidate.get('range')
        if (isinstance(value, dict) and value.get('resource') is None
                and all(value.get(key) == -1 for key in ('start_step', 'step_count', 'timestamp'))):
            continue
        if (not isinstance(value, dict) or value.get('resource') != native.get('resource')
                or any(type(value.get(key)) is not int for key in ('start_step','step_count','timestamp'))
                or value['start_step'] < 0 or value['step_count'] <= 0 or value['timestamp'] < 0):
            return {**missing, 'reason': 'native source interval is absent or belongs to another resource'}
        if value['start_step'] <= native['start_step'] < value['start_step'] + value['step_count']:
            matches.append(dict(name=row['task']+'.'+row['instance'], step_start=value['start_step'],
                step_count=value['step_count'], token_offset=None, token_length=None,
                native_range_evidence=candidate))
    if seen != expected or not expected:
        return {**missing, 'reason': 'native instance range selection is incomplete'}
    if len(matches) != 1:
        return {**missing, 'reason': 'native instance source interval missing or ambiguous',
                'range_candidates': len(matches)}
    return {'interval': matches[0]}


def _public_source_audit_binding(audits, diagnostics):
    return ('conflicted' if audits and any(a['status'] == 'source-conflict' for a in audits)
        else 'consistent' if audits and len(audits) == len(diagnostics)
            and all(a['status'] == 'source-consistent' for a in audits)
        else 'partial_or_unresolved' if audits else 'not_observed')


def compare_public_source_diagnostic(public, query, diagnostic, *, project_id, native_version):
    """Compare public body identity with the independently queried source.

    Resource and POU names live in different namespaces. A successful public
    name lookup can bind another body's diagnostics to an existing POU. Even
    an agreeing body ID does not turn generated-code positions into source
    positions. Keep that comparison separate from check completion.
    """
    native = project_native_source_diagnostic(query, diagnostic,
        project_id=project_id, native_version=native_version)
    result = {'status': 'unresolved', 'public_check_promoted': False,
              'original_public_diagnostic': public, 'native_source_projection': native}
    if native['status'] != 'source-resolved':
        return {**result, 'reason': 'independent native source identity is unresolved'}
    if (type(public.get('kind')) is not int or type(public.get('code')) is not int
            or type(public.get('step')) is not int
            or any(public.get(key) != diagnostic.get(key) for key in ('kind', 'code', 'step'))
            or public.get('name') != query.get('resource')):
        return {**result, 'reason': 'public and original diagnostics do not match'}
    public_id = _id(public.get('object_id'))
    if public_id is None or not any(public_id) or not all(
            type(word) is int and 0 <= word <= 0xffffffff for word in public_id):
        return {**result, 'reason': 'public source body identity is missing'}
    matches = list(public_id) == native['source']['body_id']
    public_kind, source_kind = public.get('program_kind'), native['source']['program_kind']
    return {**result, 'status': 'source-consistent' if matches else 'source-conflict',
            'public_body_matches_native_source': matches,
            'public_position': {key: public.get(key) for key in
                                ('program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')},
            'public_position_binding': {
                'status': 'different-representation' if type(public_kind) is int and public_kind != source_kind
                          else 'not_established',
                'public_program_kind': public_kind, 'source_program_kind': source_kind,
                'direct_source_mapping_allowed': False},
            'source_position_basis': 'independent native source query',
            'limits': ['body identity agreement does not establish public source coordinates',
                       'current resource reads and check completion are separate facts']}


def _native_network_members(records, network, block_count):
    """Select existing source records by the original one-based network ordinal.

    Original reorder and concrete-menu public FBD controls establish that empty
    source blocks retain their ordinal. Never infer membership from coordinates
    or omit an unassigned record from a multi-block source.
    """
    if (type(block_count) is not int or block_count < 1 or type(network) is not int
            or not 1 <= network <= block_count or not isinstance(records, list)):
        return None
    selected = []
    for record in records:
        if not isinstance(record, dict):
            return None
        block = record.get('block', 0 if block_count == 1 else None)
        if type(block) is not int or not 0 <= block < block_count:
            return None
        if block == network - 1:
            selected.append(record)
    return selected


def correlate_native_compile_position(report, *, cpu, pou, program_kind, text=None, model=None, connectivity=None,
                                      source_body_id=None, logical_name=None, library=None):
    """Project observed structured compilation positions, without code.

    The caller first binds all original source getter reads around this Build.
    A compile report's POU scope does not identify an expanded FB instance.
    Formal-name and graph associations retain their distinct provenance; an
    IN_OUT formal name alone cannot choose its input or output endpoint.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    public = report.get('report_interface') == 'public-compiler'
    if public:
        if (report.get('record_size') != 100 or _id(source_body_id) is None
                or _id(report.get('source_object_id')) != _id(source_body_id)):
            return {**unresolved, 'reason': 'public compile report object identity differs from the current original source body'}
    public_fbd = (public and program_kind == 208 and isinstance(cpu, str)
                  and cpu in PUBLIC_FBD_COMPILE_POSITION_CPUS)
    library_st = (public and program_kind == 193 and isinstance(library, str) and bool(library)
                  and cpu in PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS)
    if (not (cpu == 'Q03UDV' or public_fbd or library_st) or (library and not library_st)
            or not isinstance(pou, str) or not pou or report.get('kind') not in (2, 3)
            or report.get('instance_kind') != 6 or (not public and report.get('library', {}).get('text') != '')
            or report.get('name', {}).get('text') != pou or report.get('instance', {}).get('text') != pou
            or program_kind not in (193, 208) or report.get('program_kind') != program_kind):
        return {**unresolved, 'reason': 'outside observed current structured compile source scope'}
    source = {'pou': pou, 'program_kind': program_kind}
    if library_st:
        source['library'] = library
    result = {**unresolved, 'status': 'source-resolved', 'source': source,
              'instance_resolution': 'not_available_in_compile_report',
              'limits': ['original structured compile source position; no checked or executable code required',
                         'compile POU scope does not identify one expanded FB instance',
                         'public diagnostic conversion and overall check remain separate']}
    if public:
        result['source_identity_binding'] = 'original public report matches current native source body'
    if program_kind == 193:
        line = report.get('top')
        lines = text.splitlines() if isinstance(text, str) else []
        if (type(line) is not int or not 0 <= line < len(lines)
                or report.get('step') != (0 if library_st else line)
                or report.get('network') != -1 or any(report.get(key) != -1 for key in ('left', 'right', 'bottom'))):
            return {**unresolved, 'reason': 'native compile ST line is outside the current observed source span'}
        source.update(zero_based_line=line, text=lines[line])
        return result
    expected_program = logical_name if logical_name is not None else pou + '.Program.pou'
    if (not isinstance(expected_program, str) or not expected_program.startswith(pou + '.')
            or not expected_program.endswith('.pou') or not isinstance(model, dict)
            or model.get('schema_version') != 2 or model.get('cpu') != cpu
            or model.get('program') != expected_program):
        return {**unresolved, 'reason': 'current source-bound FBD model is unavailable'}
    blocks = model.get('blocks', [{}])
    network = report.get('network')
    if not isinstance(blocks, list) or not blocks:
        return {**unresolved, 'reason': 'current FBD source blocks are unavailable'}
    nodes = _native_network_members(model.get('nodes', []), network, len(blocks))
    wires = _native_network_members(model.get('wires', []), network, len(blocks))
    if nodes is None or wires is None:
        return {**unresolved, 'reason': 'native compile network differs from current source membership'}
    source.update(network=network, block_index=network-1)
    coordinates = [report.get(key) for key in ('left', 'top', 'right', 'bottom')]
    graph = {'status': 'unresolved', 'public_check_promoted': False}
    result['graph_projection'] = graph
    if coordinates == [-1, -1, -1, -1]:
        graph.update(status='network-only', reason='native compile report has no node span')
        return result
    if (any(type(value) is not int for value in coordinates)
            or not 0 <= coordinates[0] < coordinates[2] or not 0 <= coordinates[1] < coordinates[3]):
        graph['reason'] = 'native compile node span is incomplete or invalid'
        return result
    if model.get('unknown_record_count') != 0:
        graph['reason'] = 'opaque graph records prevent complete node correlation'
        return result
    def bbox(node):
        return [node['x'], node['y'], node['x']+node['width'], node['y']+node['height']]
    objects = [node for node in nodes if bbox(node) == coordinates]
    if len(objects) != 1:
        graph.update(reason='current compile source object is missing or ambiguous',
                     object_candidates=sorted(node['id'] for node in objects))
        return result
    node = objects[0]
    graph.update(status='node-correlated', object={'object_id': node['id'], 'source_offset': node['source_offset'],
                 'symbol': node['symbol'], 'template': node['template'], 'bbox': coordinates, 'block_index': network-1})
    def port_record(owner, port):
        return {'object_id': owner['id'], 'instance': owner['symbol'], 'formal': port['formal_name'],
                'port_name': port['name'], 'side': port['side'], 'class_code': port['class_code'],
                'data_type': port['data_type']}
    arguments = report.get('arguments', [])
    if (report.get('kind') == 2 and report.get('code') == 0x500c2025
            and node['template'].startswith('function_block:') and len(arguments) == 1):
        formal = arguments[0].get('text')
        ports = [port_record(node, port) for port in node.get('ports', []) if port.get('formal_name') == formal]
        graph['port_association'] = {'status': 'uniquely_correlated' if len(ports) == 1 else 'ambiguous' if ports else 'missing',
            'basis': 'native compile formal name; direction is absent', 'candidates': ports}
    elif (report.get('kind') == 2 and report.get('code') == 0x500c2017 and node['template'] == 'output'
            and not arguments and connectivity is not None and connectivity.logical_name == model['program']):
        association = {'status': 'unresolved', 'basis': 'current source connectivity; not a native port-direction field',
                       'candidates': []}
        graph['port_association'] = association
        try:
            net = connectivity.net_for_port(node['source_offset'], 0)
        except KeyError:
            return result
        if net.block_index != network-1 or len(net.ports) != 2:
            return result
        if net.ports[0].point != net.ports[1].point and not net.wire_offsets:
            return result
        keys = [(port.node_offset, port.port_index) for port in net.ports]
        if len(set(keys)) != 2 or (node['source_offset'], 0) not in keys:
            return result
        if any(sum(w.get('source_offset') == offset for w in wires) != 1 for offset in net.wire_offsets):
            return result
        for endpoint in net.ports:
            if endpoint.node_offset == node['source_offset']:
                owner, index = node, 0
            else:
                owners = [value for value in nodes if value['source_offset'] == endpoint.node_offset]
                if len(owners) != 1:
                    return result
                owner, index = owners[0], endpoint.port_index
            ports = owner.get('ports', [])
            if type(index) is not int or not 0 <= index < len(ports):
                return result
            port = ports[index]
            if [owner['x']+port['x'], owner['y']+port['y']] != [endpoint.point.x, endpoint.point.y]:
                return result
            if owner is not node:
                if (not owner['template'].startswith('function_block:') or port.get('side') != 'out'
                        or port.get('class_code') not in (4, 5)):
                    return result
                association['candidates'].append(port_record(owner, port))
        if len(association['candidates']) == 1:
            association['status'] = 'uniquely_correlated'
    return result


def correlate_st_diagnostic(query, link_rows, references, source_texts, caller_nodes, *, debug=None,
                            debug_encoding=None, caller_pou=None, native_ranges=None,
                            caller_block_count=1, caller_context=None):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Follow
    explicit native FB call references through ST callers to a current FBD
    node; do not infer parents by splitting instance names. Optional same-
    compile debug data can resolve an inlined FB without a link-map row.
    When original instance range responses are supplied, require their complete
    agreement and do not replace a failed or conflicting API result with caches.
    Direct library ST calls additionally require the current FBD context to
    bind the graph's interface to the diagnostic's actual library namespace.
    """
    location = query.get('location') or {}
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    library = location.get('library')
    if (query.get('hresult') != 0 or query.get('code') != 0 or location.get('program_kind') != 193
            or not isinstance(library, str)):
        return {**unresolved, 'reason': 'no successful native ST location'}
    if library and (caller_context is None or native_ranges is None):
        return {**unresolved, 'reason': 'library caller requires current source context and original instance ranges'}
    pou, line = location.get('pou'), location.get('start_step')
    lines = source_texts.get((library, pou) if library else pou, '').splitlines()
    if type(line) is not int or not 0 <= line < len(lines):
        return {**unresolved, 'reason': 'native source line outside selected source'}
    native = query.get('query', {})
    step = native.get('start_step')
    line_references = [row for row in references if row.get('program_kind') == 193 and row.get('source') == pou
               and row.get('library') == library
               and row.get('top') == line and row.get('attribute') in ((1, 2) if native_ranges is not None else (2,))
               and row.get('resource') == native.get('resource')
               and row.get('task') and row.get('instance')]
    ranges = [row for row in link_rows if type(step) is int and row['step_count'] > 0
              and row['step_start'] <= step < row['step_start'] + row['step_count']]
    interval = ranges[0] if len(ranges) == 1 else None
    if native_ranges is not None:
        if type(step) is not int or step < 0:
            return {**unresolved, 'reason': 'original diagnostic code step is unavailable'}
        resolved = bind_native_instance_interval(query, line_references, native_ranges)
        if resolved['interval'] is None:
            return {**unresolved, **{key:value for key,value in resolved.items() if key != 'interval'}}
        interval = resolved['interval']
    matches = [row for row in line_references if interval is not None
               and row['task'] + '.' + row['instance'] == interval['name']]
    if native_ranges is not None and interval is not None:
        selected = interval['native_range_evidence']['original_reference']
        # GetPCodeRange is queried once per task/instance/source position, not
        # once per operand on the physical line. Keep its original representative.
        matches = [selected] if selected in matches else []
    if native_ranges is None and type(step) is int and len(matches) != 1:
        recovered = _debug_st_interval(query, debug, line_references, debug_encoding)
        if recovered is not None:
            interval = recovered
            matches = [row for row in line_references if row['task'] + '.' + row['instance'] == interval['name']]
    if interval is None:
        return {**unresolved, 'reason': 'code range missing or ambiguous', 'range_candidates': len(ranges)}
    if len(matches) != 1:
        return {**unresolved, 'reason': 'source reference missing or ambiguous', 'reference_candidates': len(matches),
                'source_instance_candidates': sorted({row['instance'] for row in line_references}),
                'compiled_interval_name': interval['name']}
    reference = matches[0]
    called_pou, called_instance = pou, reference['instance']
    call_chain, visited = [], set()
    while True:
        identity = (called_pou, called_instance)
        if identity in visited or len(call_chain) >= 64:
            return {**unresolved, 'reason': 'native caller chain cycles or exceeds the bound'}
        visited.add(identity)
        calls = [row for row in references if row.get('program_kind') in (193, 208)
                 and row.get('class_code') == 1 and row.get('type') == called_pou
                 and row.get('library') == '' and row.get('attribute') == 1
                 and (not library or row.get('program_kind') == 208 and row.get('data_type') == 15)
                 and row.get('resource') == native.get('resource') and row.get('task') == reference['task']
                 and row.get('instance') and row.get('name') and row.get('source')
                 and row['instance'] + '.' + row['name'] == called_instance]
        if len(calls) != 1:
            return {**unresolved, 'reason': 'native caller missing or ambiguous',
                    'caller_candidates': len(calls), 'called_pou': called_pou,
                    'called_instance': called_instance}
        call = calls[0]
        entry = {'called_pou': called_pou, 'called_instance': called_instance,
                 'native_reference': call}
        call_chain.append(entry)
        if call['program_kind'] == 208:
            if len(call_chain) > 1 and (not isinstance(caller_pou, str) or not caller_pou):
                return {**unresolved, 'reason': 'selected FBD source identity is missing'}
            if caller_pou is not None and call['source'] != caller_pou:
                return {**unresolved, 'reason': 'native FBD caller differs from the selected source'}
            break
        parent_lines = source_texts.get(call['source'], '').splitlines()
        parent_line = call.get('top')
        if (type(parent_line) is not int or not 0 <= parent_line < len(parent_lines)
                or call.get('network') != -1):
            return {**unresolved, 'reason': 'native ST caller line outside the selected source'}
        entry['source'] = {'pou': call['source'], 'program_kind': 193,
                           'zero_based_line': parent_line, 'text': parent_lines[parent_line]}
        called_pou, called_instance = call['source'], call['instance']
    network_nodes = _native_network_members(caller_nodes, call.get('network'), caller_block_count)
    if network_nodes is None:
        return {**unresolved, 'reason': 'native caller network or current block membership is unavailable'}
    objects = [node for node in network_nodes if node.get('symbol') == call['name']
               and node.get('template') == 'function_block:' + called_pou
               and [node['x'], node['y'], node['x'] + node['width'], node['y'] + node['height']]
               == [call[key] for key in ('left', 'top', 'right', 'bottom')]]
    if len(objects) != 1:
        return {**unresolved, 'reason': 'current caller object missing or ambiguous', 'object_candidates': len(objects)}
    interface_source = None
    if library:
        from .models import GXWFormatError, NodeKind
        current_nodes = [node for node in caller_context.program.nodes
                         if node.offset == objects[0]['source_offset']
                         and node.kind == NodeKind.FUNCTION_BLOCK
                         and node.symbol == call['name'] and node.type_name == pou
                         and [node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom]
                         == [call[key] for key in ('left', 'top', 'right', 'bottom')]]
        sources = caller_context.sources
        try:
            local = sources.declarations.get(sources.local_table)
        except GXWFormatError:
            return {**unresolved, 'reason': 'current FBD caller declaration owner is missing or ambiguous'}
        if (len(current_nodes) != 1 or local is None or local.owner_name != call['source']
                or caller_context.program.logical_name != sources.program_name):
            return {**unresolved, 'reason': 'original FBD call does not match the current caller source context'}
        try:
            bound = sources.callable(current_nodes[0])
        except GXWFormatError:
            return {**unresolved, 'reason': 'current caller interface is missing or ambiguous'}
        owner = sources.user_library_owners.get(bound['source_stream'], {})
        if owner.get('library') != library or owner.get('name') != pou:
            return {**unresolved, 'reason': 'current caller interface belongs to another source namespace'}
        interface_source = bound['source_stream']
    result = {'status': 'uniquely_correlated', 'public_check_promoted': False,
            'resource': native['resource'], 'diagnostic_step': step,
            'original_kind': native.get('original_kind'), 'original_code': native.get('original_code'),
            'instance': reference['instance'], 'task': reference['task'],
            'source': {'pou': pou, 'program_kind': 193, 'zero_based_line': line, 'text': lines[line]},
            'compiled_interval': {key: interval[key] for key in
                ('name', 'step_start', 'step_count', 'token_offset', 'token_length')},
            'native_reference': reference,
            'caller': {'pou': call['source'], 'program_kind': 208, 'network': call['network'],
                       'bbox': [call[key] for key in ('left', 'top', 'right', 'bottom')],
                       'object_id': objects[0]['id'], 'source_offset': objects[0]['source_offset']},
            'limits': ['controlled source line, not an expression span',
                       'public diagnostic conversion and overall check remain separate']}
    if len(call_chain) > 1:
        result['call_chain'] = call_chain
        result['caller']['instance'] = called_instance
        result['limits'][0] = 'controlled ST-to-FBD call chain, not an expression span'
    if library:
        result['source']['library'] = library
        result['caller']['interface_source'] = interface_source
        result['limits'].append('direct library ST call; graph port and nested library callers remain unresolved')
    if 'debug_evidence' in interval:
        result['debug_evidence'] = interval['debug_evidence']
    if 'native_range_evidence' in interval:
        result['native_range_evidence'] = interval['native_range_evidence']
    return result



def bind_native_sfc_child_snapshots(program_raw, *, owner_id, stage, reads, snapshots):
    """Bind all stored SFC child identities to this phase's original getters."""
    from .models import GXWFormatError
    try:
        expected = _native_validation_sfc_children(program_raw)
        scope = sfc_child_source_scope(program_raw)
    except GXWFormatError:
        return {'status': 'unresolved', 'stage': stage, 'children': [], 'reason': 'SFC child framing is not established'}
    owner = _id(owner_id)
    selected = [row for row in reads if row.get('operation') == 'NativeSfcChildRead'
                and row.get('stage') == stage and _id(row.get('parent')) == owner]
    missing = {'status': 'unresolved', 'stage': stage, 'children': [], 'source_scope': scope}
    keys = [(row['type'], row['name']) for row in expected]
    if owner is None or not any(owner) or len(set(keys)) != len(keys) or len(selected) > len(expected):
        return {**missing, 'reason': 'current SFC child membership is missing or ambiguous'}
    if any(not isinstance(row.get('identity'), dict) or any(not isinstance(row['identity'].get(key), dict)
            for key in ('read_type', 'read_name')) for row in selected):
        return {**missing, 'reason': 'original SFC child facts are missing or malformed'}
    children, identities, absent = [], set(), []
    roles = {(row['type'], row['name']): row['role'] for row in scope['children']}
    for wanted in expected:
        candidates = [row for row in selected
                      if (row.get('identity') or {}).get('read_type', {}).get('data_type') == wanted['type']
                      and (row.get('identity') or {}).get('read_name', {}).get('name') == wanted['name']]
        role = roles.get((wanted['type'], wanted['name']), 'unresolved')
        if not candidates:
            absent.append({'type': wanted['type'], 'name': wanted['name'], 'role': role})
            continue
        if len(candidates) != 1:
            return {**missing, 'reason': 'stored SFC child type/name is missing or duplicated'}
        row, = candidates
        facts = row['identity']
        identity = _id(facts.get('id'))
        if (identity is None or not any(identity) or identity == owner or identity in identities
                or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)
                or any((facts.get(key) or {}).get('hresult') != 0 or (facts.get(key) or {}).get('code') != 0
                       for key in ('read_type', 'read_name'))
                or row.get('provenance') != 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'):
            return {**missing, 'reason': 'original SFC child identity/getter is not bound'}
        identities.add(identity)
        current = snapshots.get(row.get('file'))
        if not isinstance(current, bytes) or type(row.get('bytes')) is not int or len(current) != row['bytes']:
            return {**missing, 'reason': 'original SFC child snapshot is missing or truncated'}
        children.append({'type': wanted['type'], 'name': wanted['name'], 'role': role, 'id': list(identity),
                         'status': 'current' if current == wanted['body'] else 'stale',
                         'native_snapshot': row['file'], 'native_body_bytes': len(current)})
    if len(children) != len(selected):
        return {**missing, 'reason': 'original SFC child selection includes an unexpected source'}
    return {'status': 'unresolved' if absent else 'current' if all(row['status'] == 'current' for row in children) else 'stale',
            'stage': stage, 'children': children, 'missing': absent, 'source_scope': scope,
            **({'reason': 'stored SFC children are absent from the native workspace'} if absent else {})}


def bind_native_source_chain(programs, required_sources, *, project_id, native_version,
                             selections, reads, snapshots):
    """Require current original bodies for every callee and caller in a chain.

    Equal compiled code, names or coordinates cannot bind source offsets. Use
    the last selection and body read for each POU; never fill a missing caller
    with another source that happens to have the same text or graph.
    """
    def source_key(value):
        if isinstance(value, str) and value:
            return '', value
        if (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(part, str) and part for part in value)):
            return value
        return None
    if (not required_sources or any(source_key(name) is None for name in required_sources)
            or len(required_sources) > NATIVE_SOURCE_CAPACITY):
        return {'status': 'unresolved', 'reason': 'native source chain is missing or outside the bound',
                'sources': []}
    sources = []
    for key in dict.fromkeys(required_sources):
        library, pou = source_key(key)
        namespace = {'library': library} if library else {}
        matching = [row for row in selections if row.get('operation') == 'NativeBodySelection'
                    and row.get('name') == pou and row.get('library', '') == library]
        selected = matching[-1] if matching else {}
        identity = project_native_source_identity(selected.get('source_object'), pou=pou,
            program_kind=selected.get('program_kind'), project_id=project_id, native_version=native_version, library=library)
        if identity['status'] != 'source-resolved':
            sources.append({'pou': pou, **namespace, 'status': 'unresolved', 'reason': identity['reason']})
            continue
        raw = programs.get(key)
        if not isinstance(raw, bytes):
            sources.append({'pou': pou, **namespace, 'status': 'unresolved', 'reason': 'selected source buffer is missing'})
            continue
        from .models import GXWFormatError
        from .source_header import source_payload_offset
        try:
            source_kind = raw[source_payload_offset(raw)]
        except (GXWFormatError, IndexError):
            source_kind = None
        if source_kind != selected.get('program_kind'):
            sources.append({'pou': pou, **namespace, 'status': 'unresolved',
                            'reason': 'native source language differs from the current source envelope'})
            continue
        binding = bind_native_source_snapshot(raw, body_id=identity['source']['body_id'],
            reads=reads, snapshots=snapshots)
        if source_kind == 240:
            child_binding = bind_native_sfc_child_snapshots(raw, owner_id=identity['source']['pou_id'],
                stage=selected.get('stage'), reads=reads, snapshots=snapshots)
            binding = {**binding, 'pou_id': identity['source']['pou_id'], 'sfc_children': child_binding,
                       **({'graph_status': binding['status']} if child_binding.get('missing') else {}),
                       'status': binding['status'] if child_binding['status'] == 'current' else child_binding['status']}
        sources.append({'pou': pou, **namespace, **binding})
    statuses = {row['status'] for row in sources}
    return {'status': 'current' if statuses == {'current'} else
            'unresolved' if 'unresolved' in statuses else 'stale', 'sources': sources}


def bind_native_sfc_referenced_check(programs, *, project_id, native_version, events, snapshots, imported, result,
                                     configuration_current, task_binding, selected, cpu):
    """Bind the current referenced SFC sources without promoting stored sources.

    Missing retained children stay missing. Every referenced child and graph
    needs independent, ordered original getter bytes in all three phases.
    """
    unresolved = {'status': 'unresolved', 'scope': 'referenced_sfc_source',
                  'public_check_promoted': False, 'sources': []}
    from .source_header import source_payload_offset
    if (cpu != 'FX3U/FX3UC' or not programs or any(not isinstance(key, str) or raw[source_payload_offset(raw)] != 240
                            for key, raw in programs.items())
            or native_version != '1.635.0.1' or imported.get('status') != 'current'
            or not configuration_current or not task_binding or not selected or any(name not in programs for name in selected)
            or result['compilation']['acceptance'] != 'accepted' or not result['compilation']['diagnostics_complete']
            or not result['public_check']['completed']
            or result['published_resources']['correspondence'] != 'current'
            or result['owned_check_inputs']['correspondence'] != 'current'
            or result['checker_resources']['correspondence'] not in ('current', 'not_observed')
            or result['checker_resources']['observation_errors']):
        return {**unresolved, 'reason': 'current SFC configuration, compilation and completed check are required'}
    builds = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ChangeSFCProgram'
              and row.get('hresult') == row.get('code') == 0]
    ends = [i for i, row in enumerate(events) if row.get('operation') == 'CompilePublicReports' and row.get('percent') == 100]
    checks = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck'
              and row.get('hresult') == row.get('code') == 0]
    completions = [i for i, row in enumerate(events) if row.get('operation') == 'ProgramCheckOriginalProgress'
                   and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    if len(builds) != 1 or len(ends) != 1 or not checks or not completions or not builds[0] < ends[0] < min(checks) <= max(completions):
        return {**unresolved, 'reason': 'original SFC build/check boundaries are missing or ambiguous'}
    phases, identities = [], []
    for phase in ('before-build', 'after-build', 'after-check'):
        scoped = [row for row in events if row.get('stage') == phase]
        selections = [row for row in scoped if row.get('operation') == 'NativeBodySelection']
        if len(selections) != len(programs) or any(sum(row.get('name') == name and not row.get('library')
                                                   for row in selections) != 1 for name in programs):
            return {**unresolved, 'reason': 'original SFC source selection is missing or duplicated'}
        binding = bind_native_source_chain(programs, list(programs), project_id=project_id, native_version=native_version,
            selections=scoped, reads=scoped, snapshots=snapshots)
        phase_sources, phase_ids = [], []
        for source in binding['sources']:
            children = source.get('sfc_children') or {}
            scope = children.get('source_scope') or {}
            expected = {(row['type'], row['name']) for row in scope.get('children', []) if row['role'] == 'referenced'}
            actual = [row for row in children.get('children', []) if row.get('role') == 'referenced']
            if (source.get('graph_status', source.get('status')) != 'current' or scope.get('status') != 'source-projected' or not expected
                    or len(actual) != len(expected) or {(row['type'], row['name']) for row in actual} != expected
                    or any(row['status'] != 'current' for row in actual)
                    or any(row['role'] != 'retained' for row in children.get('missing', []))):
                return {**unresolved, 'reason': 'a referenced SFC graph or child is missing, stale or ambiguous'}
            required_ids = {_id(source['body_id']), *[_id(row['id']) for row in actual]}
            readings = [(i, row) for i, row in enumerate(events) if row.get('stage') == phase and
                        (row.get('operation') == 'NativeBodyRead' and _id(row.get('body')) in required_ids
                         or row.get('operation') == 'NativeSfcChildRead' and _id((row.get('identity') or {}).get('id')) in required_ids)]
            if len(readings) != len(required_ids) or any(not (
                    i < builds[0] if phase == 'before-build' else ends[0] < i < min(checks)
                    if phase == 'after-build' else i > max(completions)) for i, _ in readings):
                return {**unresolved, 'reason': 'referenced SFC getters are duplicated or out of phase'}
            phase_ids.append((source['pou'], tuple(source['pou_id']), tuple(source['body_id']),
                              tuple(sorted((row['type'], row['name'], tuple(row['id'])) for row in actual))))
            phase_sources.append({'pou': source['pou'], 'pou_id': source['pou_id'], 'body_id': source['body_id'],
                                  'children': actual, 'unloaded_retained_children': children.get('missing', [])})
        if len(phase_sources) != len(programs):
            return {**unresolved, 'reason': 'current SFC source selection is incomplete'}
        phases.append({'stage': phase, 'sources': phase_sources}); identities.append(sorted(phase_ids))
    if not identities[0] == identities[1] == identities[2]:
        return {**unresolved, 'reason': 'referenced SFC native identities changed between phases'}
    return {'status': 'current', 'scope': 'referenced_sfc_source', 'public_check_promoted': False,
            'sources': phases[-1]['sources'], 'phases': phases,
            'provenance': 'current imported SFC source/configuration, three-phase original getters and current owned checker input'}


def project_native_sfc_child_diagnostic(report, sources, source_bodies, *, project_id, native_version, referenced_check=None):
    """Bind a typed SFC child report to its independently read current body.

    The public name is qualified by the SFC POU, while its object ID names a
    zoom/transition body. GetPCodeLocation may fail for this namespace. Keep
    that failure and the uninterpreted report coordinates separate from this
    source-object association.
    """
    from .models import GXWFormatError
    from .sfc_pou import parse_sfc_pou
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    identity = _id(report.get('source_object_id'))
    name = report.get('name', {}).get('text')
    if (native_version != '1.635.0.1' or _id(project_id) is None
            or report.get('report_interface') != 'public-compiler'
            or type(report.get('record_size')) is not int or report['record_size'] != 100
            or report.get('kind') not in (2, 3) or type(report.get('code')) is not int
            or report.get('instance_kind') != 6 or report.get('program_kind') != 1
            or not isinstance(name, str) or report.get('instance', {}).get('text') != name
            or identity is None or not any(identity)):
        return {**unresolved, 'reason': 'outside the observed typed SFC child report form'}
    candidates = []
    referenced_projection = False
    for row in sources:
        if row['program_kind'] != 240:
            continue
        bodies = [body for body in source_bodies.get('sources', []) if body.get('pou') == row['name']]
        if len(bodies) != 1:
            continue
        body = bodies[0]
        children = body.get('sfc_children') or {}
        scoped = [item for item in (referenced_check or {}).get('sources', []) if item.get('pou') == row['name']]
        scoped_current = ((referenced_check or {}).get('status') == 'current' and len(scoped) == 1
                          and _id(scoped[0].get('body_id')) == _id(body.get('body_id'))
                          and _id(scoped[0].get('pou_id')) == _id(body.get('pou_id')))
        if ((body.get('status') != 'current' or children.get('status') != 'current') and not scoped_current
                or children.get('stage') != 'after-check'):
            continue
        selected_children = scoped[0]['children'] if scoped_current else children['children']
        matching = [child for child in selected_children if _id(child.get('id')) == identity
                    and child.get('status') == 'current' and name == row['name'] + '.' + child['name']]
        if len(matching) != 1:
            continue
        child = matching[0]
        try:
            source = parse_sfc_pou(row['raw'])
        except GXWFormatError:
            continue
        kind = 'zoom' if child['type'] == 68 else 'transition'
        stored = [item for item in source.layout['children'] if (item['kind'], item['name']) == (kind, child['name'])]
        if len(stored) != 1:
            continue
        item = stored[0]
        registrations = [dict(action_name=action['name'], action_number=action['number'],
                              registration_index=index, qualifier=entry['qualifier'])
                         for action in source.layout['actions']
                         for index, entry in enumerate(action['registrations']) if entry['zoom'] == child['name']]
        referenced_projection = scoped_current
        candidates.append({'project_id': list(_id(project_id)), 'pou': row['name'], 'program_kind': 240,
            'logical_name': row['logical_name'], 'pou_id': body['pou_id'], 'body_id': list(identity),
            'parent_body_id': body['body_id'], 'child': {'kind': kind, 'name': child['name'],
                'number': item['number'], 'native_type': child['type'], 'native_snapshot': child['native_snapshot'],
                'token_region': {'offset': item['token_offset'], 'bytes': item['token_size']},
                'registrations': registrations}})
    if len(candidates) != 1:
        return {**unresolved, 'reason': 'typed report does not select one current original SFC child'}
    return {'status': 'source-resolved', 'public_check_promoted': False, 'source': candidates[0],
        'current_source_body': 'current', 'node_port_resolution': 'not-established',
        **({'check_scope': 'referenced_sfc_source', 'stored_source_check_promoted': False} if referenced_projection else {}),
        'native_position': {key: report.get(key) for key in ('step', 'network', 'left', 'top', 'right', 'bottom')},
        'position_interpretation': 'not-established',
        'provenance': 'typed public SFC child identity and qualified name matched to current original child getters'}


def project_native_sfc_instruction_position(report, projection, sources, *, cpu, native_version, current_check):
    """Match a checked SFC child's native code step to a known source record.

    This is a child-local instruction position, not a graph cell or resource
    code address. Unknown widths do not permit resynchronizing later steps.
    """
    from .models import GXWFormatError
    from .sfc_pou import parse_sfc_pou
    from .token_pou import parse_token_fragment
    from .token_listing import decode_token_program, TokenInstruction
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if (not current_check or cpu not in SFC_CHILD_INSTRUCTION_POSITION_CPUS or native_version != '1.635.0.1'
            or projection.get('status') != 'source-resolved' or projection.get('current_source_body') != 'current'
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('instance_kind') != 6 or report.get('program_kind') != 1 or report.get('kind') != 2
            or report.get('code') not in (0x050c9319, 0x050c9512)
            or any(report.get(key) != -1 for key in ('left', 'top'))
            or type(report.get('step')) is not int or report['step'] < 0):
        return {**unresolved, 'reason': 'outside the observed current SFC child instruction report scope'}
    source = projection.get('source', {}); child = source.get('child', {})
    selected = [row for row in sources if row.get('name') == source.get('pou') and row.get('program_kind') == 240
                and row.get('logical_name') == source.get('logical_name')]
    if (len(selected) != 1 or _id(source.get('body_id')) != _id(report.get('source_object_id'))
            or report.get('name', {}).get('text') != source.get('pou', '') + '.' + child.get('name', '')
            or report.get('instance', {}).get('text') != report.get('name', {}).get('text')):
        return {**unresolved, 'reason': 'checked child report identity differs from the current source'}
    block_number = getattr(selected[0].get('declarations'), 'owner_sfc_block_number', None)
    if (type(block_number) is not int or type(report.get('network')) is not int
            or report['network'] != block_number):
        return {**unresolved, 'reason': 'checked child report differs from the current SFC block number'}
    if child.get('kind') == 'zoom':
        registrations = child.get('registrations', [])
        matching = [row for row in registrations if row.get('action_number') == report.get('right') and row.get('qualifier') == 0]
        if len(matching) != 1 or report.get('bottom') != -1:
            return {**unresolved, 'reason': 'action registration does not match the observed report form'}
    elif child.get('kind') == 'transition':
        if report.get('bottom') != child.get('number') or report.get('right') != -1:
            return {**unresolved, 'reason': 'transition number does not match the observed report form'}
    else:
        return {**unresolved, 'reason': 'unsupported SFC child role'}
    try:
        document = parse_sfc_pou(selected[0]['raw'])
        stored, = [row for row in document.layout['children'] if (row['kind'], row['name']) == (child['kind'], child['name'])]
        region = child.get('token_region', {})
        if region != dict(offset=stored['token_offset'], bytes=stored['token_size']):
            return {**unresolved, 'reason': 'current child source region differs'}
        listing = decode_token_program(parse_token_fragment(document.raw, stored['token_offset'], stored['token_size']), profile='fx3u')
    except (GXWFormatError, ValueError):
        return {**unresolved, 'reason': 'current child token position is unavailable'}
    records = [row for row in listing.records if isinstance(row, TokenInstruction) and row.step == report['step']]
    if len(records) != 1:
        return {**unresolved, 'reason': 'native step does not uniquely select a known current instruction boundary'}
    record = records[0]; raw = b''.join(token.raw for token in record.tokens)
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'position': {'native_step': record.step, 'step_width': record.step_width,
                     'source_offset': record.tokens[0].offset, 'child_byte_offset': record.tokens[0].offset - stored['token_offset'],
                     'source_bytes': len(raw), 'raw_hex': raw.hex(), 'op': record.mnemonic,
                     'args': [operand.text for operand in record.operands]},
        'provenance': f'current original child getters and controlled instruction-prefix/width moves; {cpu}, native 1.635.0.1',
        'limits': ['child-local code step; report network matched to the paired SFC block metadata; graph coordinates are separate',
                   'known instruction boundary only; no position after an unknown width or within an instruction']}


def _native_declaration_facts(value, kind, name=None):
    if not isinstance(value, dict):
        return False
    identity = _id(value.get('id'))
    return (identity is not None and any(identity)
        and all(type(word) is int and 0 <= word <= 0xffffffff for word in identity)
        and value.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': kind}
        and value.get('read_name', {}).get('hresult') == value.get('read_name', {}).get('code') == 0
        and isinstance(value.get('read_name', {}).get('name'), str)
        and (name is None or value['read_name']['name'] == name))


def _native_declaration_parent(value, identity):
    return (isinstance(value, dict) and value.get('hresult') == value.get('code') == 0
            and _id(value.get('id')) == _id(identity))


def _native_validation_declaration_owners(raw):
    """Reuse current label/type documents and their native directory owners."""
    from .callable_sources import _user_library_owners, _user_library_structures
    from .container_writer import validate_cfb_streams
    from .declarations import parse_declarations
    from .models import GXWFormatError
    from .project_metadata import current_rows, logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    sources = []
    for row in current_rows(outer['projectdatalist.xml'], 'DSPROJECTDATA', 'D_Projectdata')[0]:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        logical = fields.get('szName', '')
        if not logical.endswith('.gh') or fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        document = None
        try:
            if (any(fields.get(key) != value for key, value in
                    {'ucProductType': '1', 'ucFolderType': '5', 'ucFileType': '1', 'ucReserve': '0',
                     'uiFolderNo': '0', 'bStructuredStorage': 'true'}.items()) or logical not in streams):
                raise GXWFormatError('global table has no current native declaration owner')
            document = parse_declarations(streams[logical], logical_name=logical)
        except GXWFormatError:
            pass
        sources.append(dict(role='global', library='', name=logical.removesuffix('.gh'),
                            logical_name=logical, declarations=document))
    _, _, descriptors = _user_library_owners(outer['projectdatalist.xml'], streams)
    structures, _, _ = _user_library_structures(outer['projectdatalist.xml'], streams, descriptors)
    for (namespace, name), documents in structures.items():
        for document in documents:
            logical = document.logical_name if document is not None else name + '\\' + namespace + '.lns'
            owner, _, library = logical.rpartition('\\')
            sources.append(dict(role='library-structure', library=library.removesuffix('.lns'), name=owner,
                                logical_name=logical, declarations=document))
    return sources


def bind_native_compile_declaration_owners(sources, *, project_id, events, source_context):
    """Enrich the same compile context with optional global/type getter reads."""
    import struct
    from .declarations import _string
    facts, parent = _native_declaration_facts, _native_declaration_parent
    key = lambda row: (row['role'], row['library'], row['name'])
    builds = [i for i, row in enumerate(events) if row.get('operation') in NATIVE_COMPILATION_OPERATIONS]
    ends = [i for i, row in enumerate(events) if row.get('operation') in ('CompileRawReports', 'CompilePublicReports') and row.get('percent') == 100]
    checks = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    current = source_context.get('status') == 'current' and len(builds) == len(ends) == 1
    result = {}
    for source in sources:
        identity = key(source); global_ = source['role'] == 'global'
        binding = dict(role=source['role'], library=source['library'], name=source['name'],
                       logical_name=source['logical_name'], status='not_observed')
        result[identity] = binding
        selected = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeDeclarationOwnerRead'
                    and row.get('role') == source['role'] and row.get('library') == source['library']
                    and row.get('owner', {}).get('read_name', {}).get('name') == source['name']
                    and row.get('stage') in ('before-build', 'after-build')]
        if not selected:
            continue
        binding.update(status='unresolved', reason='current declaration owner, fields or ordered reads differ')
        document = source['declarations']
        if (not current or len(selected) != 2 or document is None
                or sum(key(item) == identity for item in sources) != 1):
            continue
        declared = document.rows if global_ else document.members
        expected = {row.name: row for row in declared}
        if len(expected) != len(declared):
            continue
        stages = []
        for stage in ('before-build', 'after-build'):
            observed = [(i, row) for i, row in selected if row.get('stage') == stage]
            if len(observed) != 1:
                break
            index, row = observed[0]
            owner, collection = row.get('owner', {}), row.get('collection', {})
            if (not (index < builds[0] if stage == 'before-build' else ends[0] < index < (min(checks) if checks else len(events)))
                    or row.get('status') != 'observed' or _id(row.get('project_id')) != _id(project_id)
                    or not facts(owner, 14 if global_ else 0x303, source['name'])
                    or not facts(collection, 13 if global_ else 0x302)
                    or not parent(row.get('owner_parent'), collection.get('id'))):
                break
            if global_:
                if not parent(row.get('collection_parent'), project_id):
                    break
            else:
                namespace, namespaces = row.get('namespace_object', {}), row.get('namespace_collection', {})
                if (not facts(namespace, 0x301, source['library']) or not facts(namespaces, 0x300)
                        or not parent(row.get('collection_parent'), namespace.get('id'))
                        or not parent(row.get('namespace_parent'), namespaces.get('id'))
                        or not parent(row.get('namespace_collection_parent'), project_id)):
                    break
            variables = row.get('rows')
            if not isinstance(variables, list) or len(variables) != len(expected):
                break
            rows = {}
            for item in variables:
                if not isinstance(item, dict) or item.get('name') not in expected or item['name'] in rows:
                    break
                wanted = expected[item['name']]
                if (not facts(item.get('identity'), 16 if global_ else 0x305, wanted.name)
                        or not parent(item.get('parent'), owner['id'])
                        or type(item.get('line')) is not int or item['line'] != wanted.record_id
                        or any(item.get(field) != getattr(wanted, field) for field in ('data_type', 'initial_value', 'comment'))):
                    break
                value = dict(name=wanted.name, record_id=wanted.record_id, data_type=wanted.data_type,
                             variable_id=item['identity']['id'])
                if global_:
                    pairs = item.get('devices')
                    if (type(item.get('class_code')) is not int or item['class_code'] != wanted.class_code
                            or type(item.get('struct_mode')) is not int or item['struct_mode'] not in (0, 1)
                            or item['struct_mode'] != wanted.unknown_u32
                            or any(item.get(field) != getattr(wanted, field) for field in ('device', 'iec_address'))
                            or not isinstance(pairs, list) or any(not isinstance(pair, list) or len(pair) != 2
                                or any(not isinstance(text, str) or '\0' in text for text in pair) for pair in pairs)):
                        break
                    extension = struct.pack('<I', len(pairs)) + b''.join(_string(text) for pair in pairs for text in pair) if item['struct_mode'] == 1 else b''
                    if wanted.value_extension != extension or (item['struct_mode'] == 0 and pairs):
                        break
                    value.update(class_code=wanted.class_code, device=wanted.device, iec_address=wanted.iec_address,
                                 struct_mode=item['struct_mode'], devices=pairs)
                rows[wanted.name] = value
            if len(rows) != len(expected) or len({_id(item['variable_id']) for item in rows.values()}) != len(rows):
                break
            stages.append(dict(owner_id=owner['id'], collection_id=collection['id'], rows=rows,
                               namespace_id=None if global_ else namespace['id']))
        if len(stages) == 2 and stages[0] == stages[1]:
            binding.pop('reason', None); binding.update(status='current', **stages[0])
    return result


def correlate_native_compile_declaration_owner(report, *, cpu, binding):
    """Resolve global rows and library members only in controlled CPU scopes."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False,
                  'reason': 'current native declaration owner binding is unavailable'}
    global_ = binding.get('role') == 'global'
    scope = PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS if global_ else PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS
    if (cpu not in scope or binding.get('status') != 'current' or binding.get('role') not in ('global', 'library-structure')
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('kind') not in (2, 3) or report.get('instance_kind') != (1 if global_ else 2)
            or report.get('program_kind') != 0 or _id(report.get('source_object_id')) != _id(binding.get('owner_id'))
            or report.get('name', {}).get('text') != binding.get('name') or report.get('instance', {}).get('text') != binding.get('name')
            or any(report.get(field) != -1 for field in ('step', 'network', 'right', 'bottom'))
            or report.get('left') not in ((4, 5) if global_ else (2,)) or type(report.get('top')) is not int):
        return unresolved
    matches = [row for row in binding['rows'].values() if row['record_id'] == report['top']]
    if len(matches) != 1:
        return unresolved
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'source': dict(role=binding['role'], declaration_owner=binding['name'], library=binding['library'],
                       logical_name=binding['logical_name'], owner_id=binding['owner_id'], declaration=matches[0], native_column=report['left']),
        'source_identity_binding': 'original public report matches current native declaration owner',
        'instance_resolution': 'not_available_in_compile_report',
        'limits': ['declaration record number, not physical row index; original getters before and after Build',
                   'native column semantics and expanded caller instance are not established']}


def bind_native_compile_declarations(sources, *, events, source_context):
    """Bind original table/variable reads to current declarations around Build.

    Only the observed declaration fields are compared. This enriches source
    positions; missing observations never prevent source saving or compilation.
    """
    def key(row):
        return row.get('library', ''), row.get('pou', row.get('name'))

    facts, parent = _native_declaration_facts, _native_declaration_parent

    builds = [i for i, row in enumerate(events) if row.get('operation') in NATIVE_COMPILATION_OPERATIONS]
    ends = [i for i, row in enumerate(events) if row.get('operation') in ('CompileRawReports', 'CompilePublicReports')
            and row.get('percent') == 100]
    checks = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    current = source_context.get('status') == 'current' and len(builds) == len(ends) == 1
    result = {}
    for source in sources:
        identity = key(source)
        binding = dict(pou=source['name'], library=source.get('library', ''), status='not_observed',
                       logical_name=source['declaration_name'])
        selected = [(i, row) for i, row in enumerate(events)
                    if row.get('operation') == 'NativeDeclarationRead' and key(row) == identity
                    and row.get('stage') in ('before-build', 'after-build')]
        result[identity] = binding
        if not selected:
            continue
        binding.update(status='unresolved', reason='current declaration identities, fields or ordered reads differ')
        if not current or len(selected) != 2:
            continue
        stages = []
        library = bool(identity[0])
        expected = {row.name: row for row in source['declarations'].rows}
        for stage in ('before-build', 'after-build'):
            observed = [(i, row) for i, row in selected if row.get('stage') == stage]
            bodies = [row for group in source_context['stages'] if group['stage'] == stage
                      for row in group['sources'] if key(row) == identity]
            selections = [row.get('source_object', {}) for row in events
                          if row.get('operation') == 'NativeBodySelection' and row.get('stage') == stage
                          and key(row) == identity]
            if len(observed) != 1 or len(bodies) != 1 or len(selections) != 1:
                break
            index, row = observed[0]
            if not (index < builds[0] if stage == 'before-build' else
                    ends[0] < index < (min(checks) if checks else len(events))):
                break
            owner, table = row.get('owner', {}), row.get('table', {})
            children, variables = row.get('owner_children'), row.get('rows')
            if (row.get('status') != 'observed' or _id(row.get('body_id')) != _id(bodies[0]['body_id'])
                    or not facts(owner, 0x30b if library else 26, identity[1])
                    or _id(owner.get('id')) != _id(selections[0].get('owner', {}).get('id'))
                    or not parent(row.get('body_parent'), owner.get('id'))
                    or not facts(table, 0x30f if library else 28)
                    or not parent(row.get('table_parent'), owner.get('id'))
                    or not isinstance(children, list) or not all(isinstance(child, dict) for child in children)
                    or sum(child == table for child in children) != 1
                    or sum(facts(child, 0x30d if library else 32)
                           and _id(child.get('id')) == _id(bodies[0]['body_id']) for child in children) != 1
                    or not isinstance(variables, list) or len(variables) != len(expected)
                    or not all(isinstance(item, dict) for item in variables)):
                break
            rows = {}
            for item in variables:
                name = item.get('name')
                if not isinstance(name, str) or name not in expected or name in rows:
                    break
                wanted = expected[name]
                if (not facts(item.get('identity'), 0x311 if library else 30, name)
                        or not parent(item.get('parent'), table['id'])
                        or type(item.get('line')) is not int or item['line'] != wanted.record_id
                        or type(item.get('class_code')) is not int
                        or any(item.get(field) != getattr(wanted, field)
                               for field in ('data_type', 'class_code', 'initial_value', 'comment'))):
                    break
                rows[name] = dict(name=name, record_id=wanted.record_id, data_type=wanted.data_type,
                                  class_code=wanted.class_code, variable_id=item['identity']['id'])
            if len(rows) != len(expected) or len({_id(item['variable_id']) for item in rows.values()}) != len(rows):
                break
            stages.append(dict(table_id=table['id'], body_id=bodies[0]['body_id'], rows=rows))
        if len(stages) == 2 and stages[0] == stages[1]:
            binding.pop('reason', None)
            binding.update(status='current', **stages[0])
    return result


def correlate_native_compile_declaration(report, *, cpu, binding):
    """Resolve an observed declaration row; keep its column and instance opaque."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False,
                  'reason': 'current native declaration row binding is unavailable'}
    if (cpu not in PUBLIC_DECLARATION_COMPILE_POSITION_CPUS or binding.get('status') != 'current'
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('kind') not in (2, 3) or report.get('instance_kind') != 5 or report.get('program_kind') != 0
            or _id(report.get('source_object_id')) != _id(binding.get('table_id'))
            or report.get('name', {}).get('text') != binding.get('pou')
            or report.get('instance', {}).get('text') != binding.get('pou')
            or any(report.get(field) != -1 for field in ('step', 'network', 'right', 'bottom'))
            or report.get('left') != 2 or type(report.get('top')) is not int):
        return unresolved
    matches = [row for row in binding['rows'].values() if row['record_id'] == report['top']]
    if len(matches) != 1:
        return unresolved
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'source': {'pou': binding['pou'], 'library': binding['library'],
                   'logical_name': binding['logical_name'], 'table_id': binding['table_id'],
                   'declaration': matches[0], 'native_column': report['left']},
        'source_identity_binding': 'original public report matches current native declaration table',
        'instance_resolution': 'not_available_in_compile_report',
        'limits': ['declaration record number, not physical row index; original getters before and after Build',
                   'native column semantics and expanded caller instance are not established']}


def bind_native_compile_source_context(programs, *, project_id, native_version, events, snapshots,
                                       selection, compilation):
    """Bind completed Build reports to original source reads around that Build.

    Rejected compilation can have current source positions even though it has
    no executable code or check task. All selected declarations/configuration
    must also match the independently imported workspace HDB.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False, 'stages': []}
    builds = [i for i, row in enumerate(events) if row.get('operation') in NATIVE_COMPILATION_OPERATIONS
              and row.get('hresult') == row.get('code') == 0]
    ends = [i for i, row in enumerate(events) if row.get('operation') in ('CompileRawReports', 'CompilePublicReports')
            and row.get('percent') == 100]
    checks = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    if (selection.get('status') != 'verified' or selection.get('configuration_bytes_current') is not True
            or compilation.get('completed') is not True or compilation.get('diagnostics_complete') is not True
            or compilation.get('acceptance') not in ('accepted', 'rejected')
            or len(builds) != 1 or len(ends) != 1 or not builds[0] < ends[0]
            or events[builds[0]].get('operation') != _native_compilation_method(programs.values())
            or (checks and ends[0] >= min(checks))):
        return {**unresolved, 'reason': 'current source selection or complete original compilation is unavailable'}
    stages = []
    for stage in ('before-build', 'after-build'):
        selections = [row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == stage]
        reads = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeBodyRead' and row.get('stage') == stage]
        binding = bind_native_source_chain(programs, list(programs), project_id=project_id, native_version=native_version,
            selections=selections, reads=[row for row in events if row.get('stage') == stage
                and row.get('operation') in ('NativeBodyRead', 'NativeSfcChildRead')], snapshots=snapshots)
        stages.append({'stage': stage, **binding})
        if (binding['status'] != 'current' or len(reads) != len(programs) or len(selections) != len(programs)
                or any(sum((row.get('library', ''), row.get('name')) ==
                           (key if isinstance(key, tuple) else ('', key)) for row in selections) != 1 for key in programs)
                or any(sum(_id(row.get('body')) == _id(source.get('body_id')) for _, row in reads) != 1
                       for source in binding['sources'])
                or any(not (index < builds[0] if stage == 'before-build' else
                            ends[0] < index < (min(checks) if checks else len(events))) for index, _ in reads)):
            return {**unresolved, 'stages': stages, 'reason': 'original compile-phase source reads are missing, stale or out of order'}
    before = {(row.get('library', ''), row['pou']): row['body_id'] for row in stages[0]['sources']}
    after = {(row.get('library', ''), row['pou']): row['body_id'] for row in stages[1]['sources']}
    if before != after:
        return {**unresolved, 'stages': stages, 'reason': 'native source body identities changed during compilation'}
    return {'status': 'current', 'public_check_promoted': False, 'stages': stages,
            'provenance': 'exact imported HDB and original source getter reads before and after completed Build'}


def bind_native_source_references(events, *, compilation):
    """Require the original complete analysis performed after this build."""
    unresolved = {'status': 'unresolved', 'rows': []}
    creates = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'Compiler.CreateProgramAnalysis3']
    if not creates:
        return {**unresolved, 'status': 'not_observed', 'reason': 'original reference analysis was not observed'}
    queries = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'Compiler.GetProgramAnalysis3']
    records = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeSourceReferences']
    polls = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeReferenceProgress']
    build_ends = [i for i, row in enumerate(events) if row.get('operation') == 'Progress' and row.get('percent') == 100]
    check_starts = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    modules = {name: {row.get('version') for row in events if row.get('operation') == 'OwnedBackendModule'
                      and row.get('name') == name}
               for name in ('DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll')}
    if (compilation.get('acceptance') != 'accepted' or len(creates) != 1 or len(queries) != 1 or len(records) != 1
            or not polls or not build_ends or not check_starts
            or any(versions != {'1.635.0.1'} for versions in modules.values())):
        return {**unresolved, 'reason': 'original reference analysis or current compilation is incomplete'}
    create_index, create = creates[0]; query_index, query = queries[0]; record_index, record = records[0]
    if (any(type(row.get(key)) is not int or row[key] != 0
            for row in (create, query, record) for key in ('hresult', 'code'))
            or record.get('query') != 'all-references' or record.get('declared') != 0 or record.get('plural') != 0
            or record.get('symbol') != '' or record.get('scope') != ''
            or record.get('provenance') != 'original CreateProgramAnalysis3 and GetProgramAnalysis3; current compilation; 88-byte public records'
            or not isinstance(record.get('rows'), list) or type(record.get('count')) is not int
            or not 0 <= record['count'] <= 100000 or query.get('count') != record['count']
            or record['count'] != len(record['rows']) or any(not isinstance(row, dict) for row in record['rows'])
            or not max(build_ends) < create_index < min(i for i, _ in polls)
            or not max(i for i, _ in polls) < query_index <= record_index < min(check_starts)):
        return {**unresolved, 'reason': 'original reference query is unbound, partial or out of order'}
    seen = set()
    for poll_index, poll in polls:
        matching = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeReferenceRawReports'
                    and row.get('poll') == poll.get('poll')]
        if (any(type(poll.get(key)) is not int or poll[key] != 0 for key in ('hresult', 'code'))
                or type(poll.get('poll')) is not int or poll['poll'] <= 0 or poll['poll'] in seen
                or type(poll.get('percent')) is not int or not 0 <= poll['percent'] <= 100
                or type(poll.get('count')) is not int or not 0 <= poll['count'] <= 10000
                or len(matching) != 1 or not poll_index < matching[0][0] < query_index
                or matching[0][1].get('percent') != poll['percent']
                or not isinstance(matching[0][1].get('reports'), list)
                or len(matching[0][1]['reports']) != poll['count']
                or any(not isinstance(report, dict) or report.get('kind') == 2
                       for report in matching[0][1]['reports'])):
            return {**unresolved, 'reason': 'original reference progress or reports are incomplete or rejected'}
        seen.add(poll['poll'])
    terminal = polls[-1][1]['percent'] == 100
    return {'status': 'current' if terminal else 'unresolved', 'rows': record['rows'] if terminal else [],
            'count': record['count'], 'provenance': record['provenance']}


def _native_task_source_bindings(tasks, local_programs, source_bodies, events, references, *,
                                 project_id, native_version):
    """Bind library task entries to original compiled root references and objects.

    A task entry carries a bare program name. The FX3G community-library
    control establishes the namespace through current root references, not
    through a same-named library candidate or an expanded FB instance.
    """
    bindings = []
    for task in tasks:
        for name in task['programs']:
            row = {'resource': task['resource'], 'task': task['task'], 'pou': name,
                   'status': 'unresolved', 'public_check_promoted': False}
            roots = [reference for reference in references or []
                     if reference.get('resource') == task['resource']
                     and reference.get('task') == task['task']
                     and reference.get('source') == name and reference.get('instance') == name]
            scopes = {(reference.get('library'), reference.get('program_kind')) for reference in roots
                      if isinstance(reference.get('library'), str)
                      and type(reference.get('program_kind')) is int
                      and reference.get('program_kind') in (193, 208)}
            if (len(scopes) > 1 or any(not isinstance(reference.get('library'), str)
                    or type(reference.get('program_kind')) is not int
                    or reference.get('program_kind') not in (193, 208) for reference in roots)):
                row['reason'] = 'compiled task root has conflicting source namespaces or languages'
                bindings.append(row)
                continue
            library, kind = next(iter(scopes)) if scopes else ('', None)
            bodies = [body for body in source_bodies.get('sources', [])
                      if body.get('library', '') == library and body.get('pou') == name]
            if len(bodies) != 1 or bodies[0].get('status') != 'current':
                row['reason'] = 'task root source body is missing, ambiguous or not current'
                bindings.append(row)
                continue
            body = bodies[0]
            if not library:
                library_shadows = any(candidate.get('pou') == name and candidate.get('library')
                                      for candidate in source_bodies.get('sources', []))
                if not roots and library_shadows:
                    row['reason'] = 'same-named task sources require an original compiled root namespace'
                    bindings.append(row)
                    continue
                if name not in local_programs:
                    row['reason'] = 'task entry has no original compiled library root'
                    bindings.append(row)
                    continue
                row.update(status='source-resolved', library='', body_id=body['body_id'],
                           provenance='original local task entry and current local source body')
            else:
                selections = [event for event in events if event.get('operation') == 'NativeBodySelection'
                              and event.get('stage') == 'before-build' and event.get('name') == name
                              and event.get('library') == library and event.get('program_kind') == kind]
                identity = project_native_source_identity(
                    selections[0].get('source_object') if len(selections) == 1 else None,
                    pou=name, library=library, program_kind=kind, project_id=project_id,
                    native_version=native_version)
                source = identity.get('source', {})
                owner, selected_body = _id(source.get('pou_id')), _id(source.get('body_id'))
                if (identity['status'] != 'source-resolved' or owner is None or selected_body is None
                        or owner[6] != 0x1000000 or selected_body[6] != 0x1000000
                        or selected_body != _id(body.get('body_id'))):
                    row['reason'] = 'compiled library task root is not the current original Program object'
                else:
                    row.update(status='source-resolved', library=library, program_kind=kind,
                               body_id=list(selected_body), pou_id=list(owner),
                               original_root_reference=roots[0],
                               provenance='original compiled root reference and current namespaced Program object')
            bindings.append(row)
    return bindings


def project_native_sfc_graph_diagnostics(raw, result, native_graph):
    """Correlate current checked child source with original native cell facts.

    Geometry never repairs/promotes the public location query or check status.
    Multiple occurrences of one action remain explicit candidates.
    """
    from copy import deepcopy
    from .models import GXWFormatError
    from .sfc_pou import read_sfc_project_context

    projected = deepcopy(result)
    if (result.get('current_source_check') != 'established' and result.get('referenced_sfc_check', {}).get('status') != 'current'
            or not isinstance(native_graph, dict)
            or result.get('native_version') != native_graph.get('native_version')):
        return projected
    try:
        context = read_sfc_project_context(raw, native_graph.get('logical_name'))
    except GXWFormatError:
        return projected
    grid = context.native_graph(native_graph)
    if grid is None or context.project['cpu'] != result.get('cpu'):
        return projected
    for diagnostic in projected.get('diagnostic_projection', {}).get('source_diagnostics', []):
        source = diagnostic.get('source_projection') or {}
        owner = source.get('source') or {}; child = owner.get('child') or {}
        if (source.get('status') != 'source-resolved' or source.get('current_source_body') != 'current'
                or owner.get('program_kind') != 240 or owner.get('logical_name') != context.program_name):
            continue
        matches = [row for row in context.source.layout['children']
                   if (row['kind'], row['name']) == (child.get('kind'), child.get('name'))]
        if len(matches) != 1:
            continue
        current = matches[0]
        if child.get('token_region') != {'offset': current['token_offset'], 'bytes': current['token_size']}:
            continue
        cells = []
        for cell in grid['cells']:
            binding = cell['binding']
            if binding.get('status') != 'source-resolved':
                continue
            if child.get('kind') == 'zoom' and cell['role'] == 'step':
                registrations = [row for row in binding['registrations'] if row['zoom'] == child['name']]
                if not registrations:
                    continue
                if not any(row.get('action_number') == binding['action_number'] and row.get('qualifier') == 0
                           for row in child.get('registrations', [])):
                    continue
            elif (child.get('kind') == 'transition' and cell['role'] == 'transition'
                    and binding.get('child') == child['name'] and binding.get('transition_number') == child.get('number')):
                pass
            else:
                continue
            cells.append({'id': cell['id'], 'x': cell['x'], 'y': cell['y'], 'role': cell['role'],
                          'binding': deepcopy(binding)})
        if len(cells) == 1:
            diagnostic['graph_projection'] = {'status': 'uniquely_correlated', 'public_check_promoted': False,
                'source': {'pou': owner.get('pou'), 'logical_name': context.program_name, 'child': deepcopy(child)},
                'cell': cells[0], 'provenance': 'current checked child source and source-bound original native cells',
                'limits': ['child-to-cell correspondence only; no repaired native location query, graph ports or PLC execution']}
        elif len(cells) > 1:
            diagnostic['graph_projection'] = {'status': 'unresolved', 'public_check_promoted': False,
                'reason': 'multiple_current_native_cells', 'candidates': cells,
                'provenance': 'current checked child source and source-bound original native cells'}
    return projected


PUBLIC_CHECK_REPORT_CONVERSION_CPUS = frozenset((
    'Q00', 'Q00J', 'Q00U', 'Q00UJ', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDEH', 'Q04UDH', 'Q04UDPV', 'Q04UDV',
    'Q06H', 'Q06PH', 'Q06UDEH', 'Q06UDH', 'Q06UDPV', 'Q06UDV', 'Q100UDEH',
    'Q10UDEH', 'Q10UDH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDEH', 'Q13UDH',
    'Q13UDPV', 'Q13UDV', 'Q20UDEH', 'Q20UDH', 'Q25H', 'Q25PH', 'Q25PRH',
    'Q26UDEH', 'Q26UDH', 'Q26UDPV', 'Q26UDV', 'Q50UDEH',
))


def _public_report_text(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        raise ValueError('public report text is missing')
    data = bytes.fromhex(value['raw_hex_utf16le'])
    if len(data) % 2 or data.decode('utf-16le') != value['text']:
        raise ValueError('public report text bytes differ')
    if type(value.get('is_null')) is not bool or value['is_null'] and data:
        raise ValueError('public report BSTR extent differs')
    return value['text']


def _validate_public_report_layout(report, interface):
    """Validate the observed public ABI without assigning source semantics."""
    if (not isinstance(report, dict) or report.get('report_interface') != interface
            or type(report.get('record_size')) is not int or report['record_size'] != 100):
        raise ValueError('public report ABI differs')
    numeric = ('kind', 'code', 'instance_kind', 'program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')
    if any(type(report.get(field)) is not int or not -(1 << 31) <= report[field] < (1 << 31) for field in numeric):
        raise ValueError('public report integer extent differs')
    identity = report.get('source_object_id')
    if (not isinstance(identity, list) or len(identity) != 12
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)):
        raise ValueError('public report object ID extent differs')
    _public_report_text(report.get('name'))
    _public_report_text(report.get('instance'))
    arguments = report.get('arguments')
    if not isinstance(arguments, list) or len(arguments) > 128:
        raise ValueError('public report argument extent differs')
    for value in arguments:
        _public_report_text(value)


def _public_diagnostic_identity(observation):
    """Check resource-named public reports against independently read resource IDs."""
    if (observation.get('cpu') not in PUBLIC_CHECK_REPORT_CONVERSION_CPUS
            or observation.get('native_version') != '1.635.0.1'):
        return None
    events = observation.get('validation', {}).get('events', [])
    resources, iec_targets = {}, set()
    for row in events:
        if (row.get('operation') == 'OwnedCheckInput' and row.get('check_pass') in (None, 'public')
                and row.get('stage') == 'submitted'):
            target = _id(row.get('target'))
            inputs = row.get('rows')
            if (target is not None and all(type(word) is int and 0 <= word <= 0xffffffff for word in target)
                    and isinstance(inputs, list) and inputs
                    and all(isinstance(item, dict) and item.get('framing', {}).get('reader_module') == 'ECCompiler_IEC.dll'
                            and item['framing'].get('reader_version') == '15.50' for item in inputs)):
                iec_targets.add(target)
        if row.get('operation') != 'NativeTaskSelection':
            continue
        facts = row.get('resource') or {}
        name = facts.get('read_name') or {}
        target = _id(facts.get('id'))
        if (target is not None and all(type(word) is int and 0 <= word <= 0xffffffff for word in target) and any(target)
                and facts.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': 8}
                and name.get('hresult') == name.get('code') == 0 and isinstance(name.get('name'), str)):
            resources[target] = name['name']
    mismatches, checked = [], 0
    for index, row in enumerate(events):
        if row.get('operation') != 'ProgramCheckOriginalReports' or row.get('check_pass') not in (None, 'public'):
            continue
        target = _id(row.get('target'))
        if target is None or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in target) or target not in iec_targets:
            continue
        name = resources.get(target)
        for report in row.get('reports', []):
            if (report.get('kind') not in (2, 3) or report.get('instance_kind') not in (3, 6)
                    or report.get('program_kind') != 1 or name is None
                    or report.get('name', {}).get('text') != name or report.get('instance', {}).get('text') != name):
                continue
            checked += 1
            actual = _id(report.get('source_object_id'))
            if report['instance_kind'] == 6 or actual != target:
                mismatches.append(dict(target=list(target), original=report, event_index=index,
                    expected_object_id=list(target), reported_object_id=list(actual) if actual else None,
                    reason='resource diagnostic uses POU-body scope' if report['instance_kind'] == 6
                        else 'resource diagnostic names a different object'))
    return dict(status='mismatched' if mismatches else 'verified' if checked else 'not_observed',
                checked_report_count=checked, mismatches=mismatches)


def _project_public_diagnostic_conversion(result, observation):
    """Bind every original report to its original 68-to-100-byte conversion.

    Completion of this conversion does not change the vendor GetProgress
    result. The original check task, code and source bindings remain required.
    """
    events = observation.get('validation', {}).get('events', [])
    converted = [row for row in events if row.get('operation') == 'NativePublicDiagnosticConversion']
    unavailable = dict(status='not_observed', completed=False, current_source='not_established',
                       reports=[], public_check_promoted=False)
    if not converted:
        if result['public_check'].get('completed') is True:
            identity = _public_diagnostic_identity(observation)
            if identity and identity['mismatches']:
                return {**unavailable, 'status': 'incomplete', 'origin': 'original-public-get-progress',
                        'reason': 'original public diagnostic resource identity differs',
                        'identity_mismatches': identity['mismatches']}
            reports = [report for row in events if row.get('operation') == 'ProgramCheckOriginalReports'
                       and row.get('check_pass') in (None, 'public') for report in row.get('reports', [])]
            try:
                for report in reports:
                    _validate_public_report_layout(report, 'public-compiler')
            except (KeyError, ValueError, TypeError, UnicodeError) as error:
                return {**unavailable, 'status': 'incomplete', 'origin': 'original-public-get-progress',
                        'reason': str(error), 'observed_report_count': len(reports)}
            return dict(status='completed', completed=True,
                        current_source=result['current_source_check'], reports=reports,
                        origin='original-public-get-progress', public_check_promoted=False)
        return unavailable

    def reject(reason):
        return {**unavailable, 'status': 'incomplete', 'reason': reason,
                'observed_conversions': len(converted), 'origin': 'original-native-converter'}

    if (result.get('cpu') not in PUBLIC_CHECK_REPORT_CONVERSION_CPUS
            or result.get('native_version') != '1.635.0.1'):
        return reject('converter scope is unverified for this CPU or version')
    if (result.get('current_raw_source_check') != 'established'
            or result.get('diagnostic_recheck', {}).get('status') != 'current'
            or result['raw_backend_check'].get('completed') is not True):
        return reject('complete original task and current source/code bindings are not established')
    project = _id(observation['validation'].get('project'))
    planned = {_id(row.get('target')) for row in result['raw_backend_check'].get('targets', [])}
    if project is None or None in planned or not planned:
        return reject('selected resource identities are unavailable')
    raw_reports, stream, names = {}, None, {}
    for row in events:
        if row.get('check_pass') != 'raw-recheck':
            continue
        if row.get('operation') == 'ProgramCheckReportContext':
            stream = _id(row.get('target'))
        elif row.get('operation') == 'ProgramCheckRawReports':
            for report in row.get('reports', []):
                key = (stream, row.get('poll'), report.get('report_index'))
                if stream not in planned or key in raw_reports or report.get('poll') != row.get('poll'):
                    return reject('raw report identity is missing or duplicated')
                raw_reports[key] = report
        elif row.get('operation') == 'ProgramCheckTargetOutcome':
            target = _id(row.get('target'))
            # Resource names also have independent original Workspace reads.
            for native in events:
                if native.get('operation') == 'NativeObject' and _id(native.get('id')) == target:
                    if native.get('name_hresult') == native.get('name_code') == 0:
                        names[target] = native.get('name')

    def original_text(value):
        if not isinstance(value, dict) or value.get('codepage') != result.get('codepage'):
            raise ValueError('original text codepage differs')
        data = bytes.fromhex(value['raw_hex'])
        if data and data[-1] != 0:
            raise ValueError('original text has no terminator')
        text = (data[:-1] if data else data).decode('cp' + str(result['codepage']))
        if text != value.get('text'):
            raise ValueError('original text bytes differ')
        return text

    public_text = _public_report_text

    output, seen = [], set()
    numeric = ('kind', 'code', 'program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')
    try:
        for row in converted:
            original, report = row.get('original'), row.get('report')
            target = _id(row.get('target'))
            if not isinstance(original, dict) or not isinstance(report, dict):
                return reject('original or converted report is missing')
            _validate_public_report_layout(report, 'native-converted-public')
            key = (target, original.get('poll'), original.get('report_index'))
            if key not in raw_reports or key in seen or original != raw_reports[key]:
                return reject('conversion does not match one unique original report')
            if (row.get('check_pass') != 'raw-recheck' or row.get('cpu') != result['cpu']
                    or row.get('module') != 'DZDataABS_CompilerAdapter.dll' or row.get('version') != '1.635.0.1'
                    or row.get('converter_rva') != 0x8e48 or row.get('original_record_size') != 68
                    or row.get('record_size') != 100 or row.get('hresult') != 0 or row.get('code') != 0
                    or row.get('original_report_unchanged') is not True
                    or report.get('report_interface') != 'native-converted-public' or report.get('record_size') != 100
                    or (report.get('poll'), report.get('report_index')) != key[1:]):
                return reject('native conversion failed or its ABI/record origin differs')
            if any(type(report.get(field)) is not int or report[field] != original.get(field) for field in numeric):
                return reject('conversion changed a diagnostic code or position')
            if (public_text(report.get('name')) != original_text(original.get('name'))
                    or public_text(report.get('instance')) != original_text(original.get('instance'))
                    or not isinstance(report.get('arguments'), list) or not isinstance(original.get('arguments'), list)
                    or [public_text(value) for value in report['arguments']] != [original_text(value) for value in original['arguments']]):
                return reject('conversion changed a diagnostic name or argument')
            resource_named = (original.get('kind') in (2, 3) and original.get('instance_kind') == 6
                              and original.get('program_kind') == 1
                              and original_text(original.get('library')) == ''
                              and names.get(target) == original_text(original['name'])
                              and names.get(target) == original_text(original['instance']))
            if resource_named and row.get('scope_adapted') is not True:
                return reject('resource-named diagnostic still uses the original incompatible body scope')
            if original.get('kind') in (2, 3) and _id(report.get('source_object_id')) in (None, (0,) * 12):
                return reject('converted diagnostic has no native source object identity')
            if row.get('scope_adapted') is True:
                lookup = row.get('resource_lookup') or {}
                if (original.get('kind') not in (2, 3) or original.get('instance_kind') != 6
                        or original.get('program_kind') != 1 or report.get('instance_kind') != 3
                        or original_text(original.get('library')) != ''
                        or names.get(target) != original_text(original['name'])
                        or names.get(target) != original_text(original['instance'])
                        or lookup.get('hresult') != 0 or lookup.get('code') != 0
                        or lookup.get('requested_type') != 8 or _id(lookup.get('parent')) != project
                        or lookup.get('name') != names[target] or _id(lookup.get('id')) != target
                        or _id(report.get('source_object_id')) != target):
                    return reject('resource-scope adaptation has no exact native resource identity')
            elif row.get('scope_adapted') is not False or report.get('instance_kind') != original.get('instance_kind'):
                return reject('unexpected scope change')
            seen.add(key)
            output.append({'target': list(target), 'original': original, 'report': report,
                           'resource_scope_adapted': row['scope_adapted']})
    except (KeyError, ValueError, TypeError, UnicodeError):
        return reject('native report text or layout cannot be verified')
    if seen != set(raw_reports):
        return reject('conversion is missing original reports')
    return dict(status='completed', completed=True, current_source='established', reports=output,
                origin='original-native-converter', original_report_count=len(raw_reports),
                converted_report_count=len(output), resource_count=len(planned), public_check_promoted=False)


def project_native_validation(raw, observation, snapshots):
    """Interpret original native checks, retaining any distinct diagnostic recheck."""
    validation = observation.get('validation') if isinstance(observation, dict) else None
    if isinstance(validation, dict) and validation.get('diagnostic_recheck') is True:
        result = _project_native_diagnostic_recheck(raw, observation, snapshots)
    else:
        result = _project_native_validation(raw, observation, snapshots)
    identity = _public_diagnostic_identity(observation)
    if identity and identity['mismatches']:
        result['public_diagnostic_identity'] = identity
        result['current_source_check'] = 'not_established'
        result['diagnostic_projection']['status'] = 'identity_mismatch'
        result['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    if (validation.get('public_diagnostic_conversion_requested') is True
            or any(row.get('operation') == 'NativePublicDiagnosticConversion' for row in validation.get('events', []))):
        result['public_diagnostic_conversion'] = _project_public_diagnostic_conversion(result, observation)
        if result['public_check'].get('completed') is True and not result['public_diagnostic_conversion']['completed']:
            result['diagnostic_projection']['status'] = 'conversion_incomplete'
            result['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    return result


def _project_native_diagnostic_recheck(raw, observation, snapshots):
    from copy import deepcopy
    from .models import GXWFormatError
    events = observation['validation'].get('events')
    phases = {'common', 'public', 'public-task-tail', 'raw-recheck'}
    if (not isinstance(events, list) or any(not isinstance(row, dict) or row.get('check_pass') not in phases for row in events)):
        raise GXWFormatError('native diagnostic recheck has an unbound event pass')
    failed = [(index, row) for index, row in enumerate(events)
        if row.get('check_pass') == 'public' and row.get('operation') == 'ProgramCheckOriginalProgress'
        and row.get('hresult') == -2147467259 and row.get('code') == 0x2d010025]
    identity = _public_diagnostic_identity(observation)
    mismatched = identity['mismatches'] if identity else []
    triggers = failed + [(row['event_index'], row) for row in mismatched]
    if not triggers:
        raise GXWFormatError('native diagnostic recheck has no original public conversion failure or resource identity mismatch')

    def interpret(phase):
        selected = deepcopy(observation)
        selected['validation']['events'] = [row for row in events if row['check_pass'] in ('common', phase)]
        selected['validation'].pop('diagnostic_recheck', None)
        return _project_native_validation(raw, selected, snapshots)

    public, recheck = interpret('public'), interpret('raw-recheck')
    raw_starts = [index for index, row in enumerate(events)
        if row['check_pass'] == 'raw-recheck' and row.get('operation') == 'Compiler.ProgramCheck'
        and row.get('hresult') == row.get('code') == 0]
    raw_start = min(raw_starts, default=len(events))
    completed_targets = set()
    active_tail = None
    for index, row in enumerate(events[:raw_start]):
        if row['check_pass'] == 'public-task-tail' and row.get('operation') == 'ProgramCheckReportContext':
            active_tail = _id(row.get('target'))
        if (row['check_pass'] == 'public-task-tail' and row.get('operation') == 'ProgramCheckRawProgress'
                and row.get('hresult') == row.get('code') == 0 and row.get('percent') == 100):
            completed_targets.add(active_tail)
        if (row['check_pass'] == 'public' and row.get('operation') == 'ProgramCheckOriginalProgress'
                and row.get('percent') == 100):
            completed_targets.add(_id(row.get('target')))
    prior_tasks_terminal = (bool(raw_starts) and all(index < raw_start and _id(row.get('target')) in completed_targets
        for index, row in triggers))
    public_starts = [index for index, row in enumerate(events)
        if row['check_pass'] == 'public' and row.get('operation') == 'Compiler.ProgramCheck']
    mutations = {'Compiler.Build', 'Compiler.ChangeSFCProgram', 'Compiler.PrepareBuildData', 'Compiler.Reinitialize', 'Compiler.ToBackward',
        'Workspace.UpdatePCodeBeforeProgramCheck', 'Workspace.SetPOUBodyData', 'Workspace.SetLine', 'Workspace.CreateData'}
    same_compilation = (bool(public_starts and raw_starts) and max(public_starts) < raw_start
        and not any(row.get('operation') in mutations for row in events[min(public_starts, default=0):]))

    def inputs(phase):
        copies = {}
        for row in events:
            if row['check_pass'] != phase or row.get('operation') != 'OwnedCheckInput' or row.get('stage') != 'submitted':
                continue
            target, items = _id(row.get('target')), row.get('rows')
            if target is None or target in copies or not isinstance(items, list) or not items:
                return None
            values = [(item.get('resource'), snapshots.get(item.get('file'))) for item in items if isinstance(item, dict)]
            if len(values) != len(items) or any(not isinstance(name, str) or not isinstance(data, bytes) for name, data in values):
                return None
            copies[target] = (row.get('mask'), values)
        return copies or None

    prior_inputs, retry_inputs = inputs('public'), inputs('raw-recheck')
    same_inputs = prior_inputs is not None and prior_inputs == retry_inputs
    bound = prior_tasks_terminal and same_compilation and same_inputs
    diagnostics = deepcopy(recheck['raw_diagnostics'])
    for row in diagnostics:
        row['report_origin']['check_pass'] = 'raw-recheck'
        if not bound:
            row['graph_projection'] = {'status': 'unresolved', 'public_check_promoted': False,
                'reason': 'recheck is not bound to the original public pass'}
    public.update(raw_backend_check={**recheck['raw_backend_check'], 'check_pass': 'raw-recheck'},
        raw_diagnostics=diagnostics,
        current_raw_source_check=recheck['current_raw_source_check'] if bound else 'not_established',
        native_source_locations=recheck['native_source_locations'], native_instance_locations=recheck['native_instance_locations'],
        native_code_lexical_reads=recheck['native_code_lexical_reads'],
        diagnostic_recheck={'status': 'current' if bound and recheck['current_raw_source_check'] == 'established' else 'incomplete',
            **({'trigger_code': 0x2d010025} if failed else {}),
            'trigger_reasons': (['public_conversion_failure'] if failed else [])
                + (['public_resource_identity_mismatch'] if mismatched else []),
            'previous_tasks_terminal': prior_tasks_terminal,
            'same_compilation': same_compilation, 'same_checker_input_bytes': same_inputs,
            'public_check': public['public_check'], 'raw_check': recheck['raw_backend_check'],
            'owned_check_inputs': recheck['owned_check_inputs'], 'published_resources': recheck['published_resources']})
    projection = public['diagnostic_projection']
    if (bound and recheck['raw_backend_check'].get('completed') is True
            and recheck['current_raw_source_check'] == 'established' and projection['source_diagnostics']):
        # Preserve the first pass as evidence; the complete recheck supplies
        # the current diagnostic list, without collapsing distinct instances.
        projection['original_public_source_diagnostics'] = projection['source_diagnostics']
        projection['source_diagnostics'] = diagnostics
    else:
        projection['source_diagnostics'].extend(diagnostics)
    public['diagnostic_projection']['backend_diagnostics'] = [row['original'] for row in diagnostics]
    public['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    return public


def _project_native_validation(raw, observation, snapshots):
    """Interpret a standalone collector without trusting its match/status flags.

    Snapshots are independent bytes read by the transport before its isolated
    directory is removed. Missing copies retain incomplete or unresolved facts.
    """
    from .models import GXWFormatError
    context, sources = _native_validation_sources(raw)
    library_sources = _native_validation_library_sources(raw)
    if (not isinstance(observation, dict) or observation.get('protocol_version') != 1
            or type(observation.get('protocol_version')) is not int
            or observation.get('native_version') != '1.635.0.1'
            or observation.get('cpu') != context['cpu']
            or observation.get('status') not in ('observed', 'failed')):
        raise GXWFormatError('native validation observation has an unbound protocol, version or CPU')
    validation = observation.get('validation') or {}
    events = validation.get('events', [])
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        raise GXWFormatError('native validation event list is invalid')
    project = validation.get('project')
    programs = {row['name']: row['raw'] for row in sources}
    all_programs = {**programs, **{(row['library'], row['name']): row['raw'] for row in library_sources}}
    source_bodies = bind_native_source_chain(all_programs, list(all_programs), project_id=project,
        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
    imported = {'status': 'unresolved', 'streams': [], 'reason': 'original imported HDB is missing'}
    if isinstance(snapshots.get('imported-hdb.bin'), bytes):
        try:
            imported = native_import_selection(raw, snapshots['imported-hdb.bin'])
        except GXWFormatError as error:
            imported['reason'] = str(error)

    def facts(row, kind):
        identity = _id(row.get('id'))
        return (identity is not None and any(identity)
            and all(type(word) is int and 0 <= word <= 0xffffffff for word in identity)
            and row.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': kind}
            and row.get('read_name', {}).get('hresult') == row.get('read_name', {}).get('code') == 0
            and isinstance(row.get('read_name', {}).get('name'), str) and bool(row['read_name']['name']))

    tasks, selected, resources = [], [], []
    task_binding = True
    for row in events:
        if row.get('operation') != 'NativeTaskSelection':
            continue
        resource = row.get('resource') or {}
        task_binding &= facts(resource, 8) and row.get('provenance') == (
            'original Workspace collection, child, type, name and parent reads before compilation')
        resources.append(_id(resource.get('id')))
        for task in row.get('tasks', []):
            task_facts = task.get('task') or {}
            task_binding &= facts(task_facts, 10)
            entries = task.get('programs', [])
            names = [entry.get('read_name', {}).get('name') for entry in entries]
            task_binding &= all(facts(entry, 12) for entry in entries)
            tasks.append({'resource': resource.get('read_name', {}).get('name'),
                          'task': task_facts.get('read_name', {}).get('name'), 'programs': names})
            selected.extend(name for name in names if isinstance(name, str))
    task_binding &= bool(resources) and len(resources) == len(set(resources))
    selected = list(dict.fromkeys(selected))
    configuration_current = bool(imported['streams']) and all(row['status'] == 'current'
        for row in imported['streams'] if row['logical_name'].endswith(('.prj', '.tsk', '.lnl')))
    source_current = imported['status'] == 'current' and source_bodies['status'] == 'current'
    local_tasks = all(name in programs for name in selected)
    selection = {'status': 'verified' if task_binding and selected and source_current and local_tasks else 'unresolved',
        'source_bytes_current': source_current, 'configuration_bytes_current': configuration_current,
        'selected_sources': selected, 'empty_task': bool(resources) and not selected, 'tasks': tasks,
        'imported_workspace': imported}

    def channels(rows, size_key, file_key):
        if (not isinstance(rows, list) or len(rows) != 3
                or [row.get('channel') for row in rows] != [0, 1, 2]):
            return None
        data = tuple(snapshots.get(row.get(file_key)) for row in rows)
        return data if all(isinstance(value, bytes) and len(value) == row.get(size_key)
                           for row, value in zip(rows, data)) else None

    generated_names, generated, published = {}, {}, {}
    resource_rows = [row for row in events if row.get('operation') == 'Resource']
    for row in resource_rows:
        name = row.get('name')
        if (isinstance(name, str) and name and sum(other.get('name') == name for other in resource_rows) == 1):
            buffers = channels(row.get('channels'), 'bytes', 'file')
            if buffers is not None:
                generated_names[name] = buffers
    publication_rows = [row for row in events if row.get('operation') == 'PublishedResourceCodeCorrespondence']
    for target in {_id(row.get('target')) for row in publication_rows}:
        rows = [row for row in publication_rows if _id(row.get('target')) == target]
        resources = [row.get('resource') or {} for row in rows]
        if (target is None or any(not facts(resource, 8) or _id(resource.get('id')) != target for resource in resources)
                or len({resource['read_name']['name'] for resource in resources}) != 1):
            continue
        name = resources[0]['read_name']['name']
        buffers = [channels(row.get('channels'), 'published_size', 'published_snapshot') for row in rows]
        if any(value is None for value in buffers) or any(value != buffers[0] for value in buffers):
            continue
        published[target] = buffers[0]
        if name in generated_names:
            generated[target] = generated_names[name]
    result = analyze_native_check(events, [], generated, selection=selection,
        published=published, owned_snapshots=snapshots)
    reference_binding = bind_native_source_references(events, compilation=result['compilation'])
    references = reference_binding['rows'] if reference_binding['status'] == 'current' else None
    task_sources = _native_task_source_bindings(tasks, programs, source_bodies, events, references,
        project_id=project, native_version=observation['native_version'])
    selected_status = ('verified' if task_binding and selected and source_current and task_sources
                       and all(row['status'] == 'source-resolved' for row in task_sources) else 'unresolved')
    selection['task_program_sources'] = task_sources
    if selected_status != selection['status']:
        selection['status'] = selected_status
        result = analyze_native_check(events, [], generated, selection=selection,
            published=published, owned_snapshots=snapshots)
    compile_context = bind_native_compile_source_context(all_programs, project_id=project,
        native_version=observation['native_version'], events=events, snapshots=snapshots,
        selection=selection, compilation=result['compilation'])
    result['compilation']['source_context'] = compile_context
    declaration_bindings = bind_native_compile_declarations([*sources, *library_sources],
        events=events, source_context=compile_context)
    declaration_statuses = {row['status'] for row in declaration_bindings.values()}
    result['compilation']['declaration_context'] = {
        'status': ('current' if declaration_statuses == {'current'} else 'not_observed'
                   if declaration_statuses == {'not_observed'} else 'partial_or_unresolved'),
        'tables': [{key: value for key, value in row.items() if key != 'rows'}
                   for row in declaration_bindings.values()],
        'public_check_promoted': False}
    declaration_owners = bind_native_compile_declaration_owners(_native_validation_declaration_owners(raw),
        project_id=project, events=events, source_context=compile_context)
    owner_statuses = {row['status'] for row in declaration_owners.values()}
    result['compilation']['declaration_context']['global_and_structure'] = {
        'status': ('current' if owner_statuses == {'current'} else 'not_observed'
                   if owner_statuses <= {'not_observed'} else 'partial_or_unresolved'),
        'tables': [{key: value for key, value in row.items() if key != 'rows'} for row in declaration_owners.values()],
        'public_check_promoted': False}
    referenced_check = {}
    if any((body.get('sfc_children') or {}).get('missing') for body in source_bodies['sources']):
        referenced_check = bind_native_sfc_referenced_check(all_programs, project_id=project,
            native_version=observation['native_version'], events=events, snapshots=snapshots,
            imported=imported, result=result, configuration_current=configuration_current,
            task_binding=task_binding, selected=selected, cpu=context['cpu'])
        result['referenced_sfc_check'] = referenced_check
    check_reports, target, report_path, check_context = [], None, None, None
    for event_index, row in enumerate(events):
        if row.get('operation') == 'ProgramCheckReportContext':
            if check_context is not None:
                check_context['end'] = event_index
            target = row.get('target')
            report_path = row.get('path')
            check_context = {'end': len(events)}
        elif row.get('operation') == 'ProgramCheckRawReports':
            check_reports.extend((target, row.get('poll'), index, report, False, event_index, check_context)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
        elif row.get('operation') == 'ProgramCheckOriginalReports' and report_path == 'original-public-get-progress':
            check_reports.extend((target, row.get('poll'), index, report, True, event_index, check_context)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
        elif row.get('operation') in ('ProgramCheckTarget', 'ProgramCheckTargetOutcome') and check_context is not None:
            check_context['end'] = event_index
            check_context = None
            target, report_path = None, None
    diagnostics, source_audits, diagnostic_locations = [], [], []
    indexed_locations = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeDiagnosticLocation']
    for target, poll, index, report, public, report_index, check_context in check_reports:
        matches = [row for i, row in indexed_locations if check_context is not None
            and report_index < i < check_context['end'] and row.get('original') == {
            'poll': poll, 'report_index': index, 'kind': report.get('kind'), 'code': report.get('code'),
            'resource': report.get('name', {}).get('text'), 'step': report.get('step')}
            and row.get('target_resource') == report.get('name', {}).get('text')]
        projection = {'status': 'unresolved', 'reason': 'original diagnostic location is missing or ambiguous',
                      'public_check_promoted': False}
        if len(matches) == 1:
            projection = project_native_source_diagnostic(matches[0].get('source_location') or {}, report,
                project_id=project, native_version=observation['native_version'], references=references)
            if projection['status'] == 'source-resolved':
                current = next((body for body in source_bodies['sources']
                    if body['pou'] == projection['source']['pou']
                    and body.get('library', '') == projection['source'].get('library', '')), {})
                projection['current_source_body'] = current.get('status', 'unresolved')
                if current.get('status') != 'current':
                    projection.update(status='unresolved', reason='original source body is not current')
        origin = {'target': target, 'poll': poll, 'report_index': index}
        if public:
            origin.update(report_interface='public-compiler', record_size=100)
            public_row = {**report, 'name': report.get('name', {}).get('text'),
                'instance': report.get('instance', {}).get('text'), 'object_id': report.get('source_object_id')}
            audit = compare_public_source_diagnostic(public_row,
                matches[0].get('source_location') or {} if len(matches) == 1 else {}, report,
                project_id=project, native_version=observation['native_version'])
            if projection['status'] != 'source-resolved':
                child = project_native_sfc_child_diagnostic(report, sources, source_bodies,
                    project_id=project, native_version=observation['native_version'], referenced_check=referenced_check)
                if child['status'] == 'source-resolved':
                    projection = {**child, 'native_location_query':
                        matches[0].get('source_location') if len(matches) == 1 else None}
                    audit = {'status': 'source-consistent', 'public_check_promoted': False,
                        'original_public_diagnostic': public_row, 'native_source_projection': projection,
                        'position_interpretation': 'not-established'}
            if projection['status'] != 'source-resolved':
                audit.update(status='unresolved', reason=projection.get('reason', 'current source identity is unresolved'))
            source_audits.append(audit)
        diagnostics.append({'target': target, 'report_origin': origin,
                            'original': report, 'source_projection': projection})
        diagnostic_locations.append(matches)
    result['diagnostic_projection'].update(source_audits=source_audits,
        source_body_binding=_public_source_audit_binding(source_audits, result['diagnostic_projection']['public_diagnostics']))
    builds = [index for index, row in enumerate(events) if row.get('operation') in NATIVE_COMPILATION_OPERATIONS
              and row.get('hresult') == row.get('code') == 0]
    compile_ends = [index for index, row in enumerate(events) if row.get('operation') == 'Progress'
                    and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    check_starts = [index for index, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    check_ends = [index for index, row in enumerate(events)
                  if row.get('operation') in ('ProgramCheckRawProgress', 'ProgramCheckOriginalProgress')
                  and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    boundaries = bool(len(builds) == 1 and compile_ends and check_starts and check_ends
                      and events[builds[0]].get('operation') == _native_compilation_method(all_programs.values())
                      and builds[0] < max(compile_ends) < min(check_starts) <= max(check_ends))
    ordered_reads = boundaries and all(
        any(_id(read.get('body')) == _id(body.get('body_id')) and read.get('operation') == 'NativeBodyRead'
            and read.get('stage') == stage and (
                index < builds[0] if stage == 'before-build' else
                max(compile_ends) < index < min(check_starts) if stage == 'after-build' else
                index > max(check_ends)) for index, read in enumerate(events))
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    phase_reads_complete = source_bodies['status'] == 'current' and ordered_reads and all(
        bind_native_source_snapshot(all_programs[(body['library'], body['pou']) if body.get('library') else body['pou']], body_id=body['body_id'],
            reads=[read for read in events if read.get('stage') == stage], snapshots=snapshots)['status'] == 'current'
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    phase_reads_complete = phase_reads_complete and all(
        bind_native_sfc_child_snapshots(all_programs[body['pou']], owner_id=body['pou_id'], stage=stage,
            reads=events, snapshots=snapshots)['status'] == 'current'
        for body in source_bodies['sources'] if 'sfc_children' in body
        for stage in ('before-build', 'after-build', 'after-check'))
    code_current = (selection['status'] == 'verified' and configuration_current and phase_reads_complete
        and result['compilation']['acceptance'] == 'accepted'
        and result['published_resources']['correspondence'] == 'current'
        and result['owned_check_inputs']['correspondence'] == 'current'
        and result['checker_resources']['correspondence'] in ('current', 'not_observed')
        and not result['checker_resources']['observation_errors'])
    current_check = code_current and (result['raw_backend_check']['completed'] or result['public_check']['completed'])
    result['current_source_check'] = 'established' if code_current and result['public_check']['completed'] else 'not_established'
    lexical_reads = bind_native_code_lexical_read(events, generated_names, snapshots,
                                                cpu=context['cpu'], codepage=context['codepage'])
    lexical_by_resource = {row['resource']: row for row in lexical_reads if row['status'] == 'current'}
    texts, graphs, graph_models, graph_connectivity, graph_contexts = {}, {}, {}, {}, {}
    if (current_check and references is not None) or compile_context['status'] == 'current':
        from .text_pou import parse_st_pou
        from .object_model import read_project_context
        from .connectivity import build_connectivity_graph
        for source in [*sources, *library_sources]:
            try:
                if source['program_kind'] == 193:
                    key = (source['library'], source['name']) if source.get('library') else source['name']
                    texts[key] = parse_st_pou(source['raw']).text
                elif source['program_kind'] == 208 and not source.get('library'):
                    graph_context = read_project_context(raw, source['logical_name'])
                    graph_contexts[source['name']] = graph_context
                    graph_models[source['name']] = graph_context.object_model()
                    graphs[source['name']] = graph_models[source['name']]['nodes']
                    graph_connectivity[source['name']] = build_connectivity_graph(graph_context.program)
            except GXWFormatError:
                continue
    source_map = {(source.get('library', ''), source['name']): source for source in [*sources, *library_sources]}
    compile_diagnostics = []
    for row in events:
        if row.get('operation') not in ('CompileRawReports', 'CompilePublicReports'):
            continue
        for index, report in enumerate(row.get('reports', [])):
            if report.get('kind') not in (2, 3):
                continue
            pou = report.get('name', {}).get('text')
            projection = {'status': 'unresolved', 'public_check_promoted': False,
                          'reason': 'current compile-phase source context is unavailable'}
            if compile_context['status'] == 'current':
                tables = [value for value in declaration_bindings.values() if value['status'] == 'current'
                          and value['pou'] == pou and _id(value.get('table_id')) == _id(report.get('source_object_id'))]
                if report.get('instance_kind') in (1, 2):
                    owners = [value for value in declaration_owners.values() if value['status'] == 'current'
                              and value['name'] == pou and _id(value.get('owner_id')) == _id(report.get('source_object_id'))]
                    projection = correlate_native_compile_declaration_owner(report, cpu=context['cpu'],
                        binding=owners[0] if len(owners) == 1 else {})
                elif report.get('instance_kind') == 5:
                    projection = correlate_native_compile_declaration(report, cpu=context['cpu'],
                        binding=tables[0] if len(tables) == 1 else {})
                else:
                    public = report.get('report_interface') == 'public-compiler'
                    bodies = [value for value in compile_context['stages'][-1]['sources'] if value['pou'] == pou
                              and (_id(value['body_id']) == _id(report.get('source_object_id')) if public else not value.get('library'))]
                    body = bodies[0] if len(bodies) == 1 else {}
                    library = body.get('library', '')
                    source = source_map.get((library, pou), {}) if body else {}
                    if library and declaration_bindings.get((library, pou), {}).get('status') != 'current':
                        projection['reason'] = 'current library declaration context is unavailable'
                    else:
                        projection = correlate_native_compile_position(report, cpu=context['cpu'], pou=pou,
                            program_kind=source.get('program_kind'), text=texts.get((library, pou) if library else pou),
                            model=graph_models.get(pou), connectivity=graph_connectivity.get(pou),
                            source_body_id=body.get('body_id'), logical_name=source.get('logical_name'), library=library)
                        if projection['status'] == 'source-resolved':
                            projection['source']['body_id'] = body['body_id']
            graph = projection.pop('graph_projection', {'status': 'not_applicable', 'public_check_promoted': False})
            compile_diagnostics.append({'report_origin': {'phase': 'compile', 'poll': row.get('poll'), 'report_index': index},
                'original': report, 'source_projection': projection, 'graph_projection': graph})
    result['compilation']['source_diagnostics'] = compile_diagnostics
    for diagnostic, matching in zip(diagnostics, diagnostic_locations):
        projection = diagnostic['source_projection']
        if 'child' in projection.get('source', {}) and projection.get('current_source_body') == 'current':
            projection['instruction_position'] = project_native_sfc_instruction_position(diagnostic['original'], projection,
                sources, cpu=context['cpu'], native_version=observation['native_version'],
                current_check=current_check or referenced_check.get('status') == 'current')
        graph = {'status': 'unresolved', 'public_check_promoted': False,
                 'reason': 'current original code, references and source chain are required'}
        if not current_check and 'instance_projection' in projection:
            projection['instance_projection'].update(status='unresolved', reason='actual check code is not bound to this compilation')
        if current_check and references is not None and projection.get('status') == 'source-resolved':
            location = projection['source']
            if location['program_kind'] == 193:
                if len(matching) == 1:
                    query = matching[0]['source_location']
                    query = {**query, 'query': {'resource': projection['resource'], 'start_step': projection['diagnostic_step'],
                             'original_kind': diagnostic['original']['kind'], 'original_code': diagnostic['original']['code']}}
                    candidates = [correlate_st_diagnostic(query, [], references, texts, nodes, caller_pou=pou,
                        native_ranges=query.get('instance_ranges'),
                        caller_context=graph_contexts[pou],
                        caller_block_count=len(graph_models[pou].get('blocks', [None])))
                        for pou, nodes in graphs.items()]
                    resolved = [row for row in candidates if row['status'] == 'uniquely_correlated']
                    if len(resolved) == 1:
                        graph = resolved[0]
                        callee = (location['library'], location['pou']) if location.get('library') else location['pou']
                        needed = [callee, graph['caller']['pou']]
                        needed.extend(row['native_reference']['source'] for row in graph.get('call_chain', []))
                        chain = bind_native_source_chain(all_programs, needed, project_id=project,
                            native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                        graph['source_chain'] = chain
                        if chain['status'] != 'current':
                            graph.update(status='unresolved', reason='an original caller or callee body is not current')
                    else:
                        graph['reason'] = 'original ST instance does not select one current FBD caller'
            elif location.get('library'):
                graph['reason'] = 'library source and instance are bound; graph and port correspondence is not established'
            elif location['program_kind'] == 208:
                instance = projection.get('instance_projection') or {}
                lexical = lexical_by_resource.get(projection['resource'])
                pou = location['pou']
                if (lexical is not None and len(matching) == 1 and pou in graph_models
                        and instance.get('status') == 'instance-resolved'):
                    interval = instance['compiled_interval']
                    records = [row for row in lexical['records']
                               if interval['step_start'] <= row['native_step'] < interval['step_start'] + interval['step_count']]
                    if context['cpu'] in ('FXU/FX2C', 'FX3G'):
                        graph = correlate_fbd_instance_output(matching[0]['source_location'], diagnostic['original'],
                            records, references, graph_contexts[pou], graph_contexts,
                            instance=instance, connectivity=graph_connectivity[pou])
                    else:
                        graph = correlate_fbd_bool_output(matching[0]['source_location'], diagnostic['original'],
                            records, references, graph_models[pou], selected_pou=pou,
                            connectivity=graph_connectivity[pou], instance=(instance['task'], instance['instance']))
                    needed = list(dict.fromkeys([pou, *([graph['caller']['pou']] if 'caller' in graph else [])]))
                    graph['source_chain'] = bind_native_source_chain(programs, needed, project_id=project,
                        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                    if graph['source_chain']['status'] != 'current':
                        graph.update(status='unresolved', reason='original FBD source body is not current')
                else:
                    graph['reason'] = 'current native lexical code, instance interval and FBD context are required'
        diagnostic['graph_projection'] = graph
    result['diagnostic_projection']['source_diagnostics'] = diagnostics
    result.update(native_version=observation['native_version'], cpu=context['cpu'], codepage=context['codepage'],
        adapter={'status': observation['status'], 'message': observation.get('message') or ''},
        source_bodies={**source_bodies, 'phase_reads_complete': phase_reads_complete},
        raw_diagnostics=[row for row in diagnostics if row['report_origin'].get('report_interface') != 'public-compiler'],
        source_references=reference_binding,
        native_code_lexical_reads=lexical_reads,
        native_instance_locations={'status': 'completed' if current_check and references is not None and all(
            row['source_projection'].get('instance_projection', {}).get('status') == 'instance-resolved' for row in diagnostics)
            else 'not_observed' if reference_binding['status'] == 'not_observed' else 'partial_or_unresolved',
            'public_check_promoted': False},
        native_source_locations={'status': 'completed' if (result['raw_backend_check']['completed'] or result['public_check']['completed'])
            and all(row['source_projection']['status'] == 'source-resolved' for row in diagnostics)
            else 'not_requested' if result['raw_backend_check']['status'] == 'not_observed' and not check_reports else 'partial_or_unresolved',
            'node_port_resolution': 'correlated' if any(row['source_projection'].get('source', {}).get('program_kind') == 208
                for row in diagnostics) and all(row['graph_projection']['status'] == 'uniquely_correlated'
                    for row in diagnostics if row['source_projection'].get('source', {}).get('program_kind') == 208)
                else 'partial_or_unresolved', 'public_check_promoted': False},
        current_raw_source_check='established' if code_current and result['raw_backend_check']['completed'] else 'not_established',
        generated_resources=[{'name': name, 'channel_sizes': [len(value) for value in data]}
                             for name, data in generated_names.items()])
    return result


def analyze_native_check(events, observations, generated, *, selection=None, reopened=None, source_audits=None,
                  published=None, owned_snapshots=None):
    """generated maps native resource IDs to three exact GetPCode buffers."""
    trace = [row.get('payload', row) for row in observations if row.get('type') != 'error']
    observation_errors = [row for row in observations if row.get('type') == 'error']
    observation_errors += [row for row in trace if row.get('event') == 'observation-error']
    planned, collection = [], None
    collecting, explicit_targets = False, None
    for row in events:
        if row.get('operation') == 'Workspace.GetProgramCheckCollection':
            collecting = True
        elif collecting and row.get('operation') == 'InventoryCount':
            collection = _id(row.get('id'))
        elif collecting and row.get('operation') == 'NativeObject' and _id(row.get('parent')) == collection:
            planned.append(_id(row['id']))
        elif row.get('operation') == 'ProgramCheckTarget':
            collecting = False
        elif row.get('operation') == 'ProgramCheckTargetOrder':
            explicit_targets = [_id(target) for target in row.get('targets', [])]
    started = [_id(row['id']) for row in events if row.get('operation') == 'ProgramCheckTarget']
    planned = list(dict.fromkeys(explicit_targets if explicit_targets is not None else planned or started))
    # The observer's phase and last check target survive a completed call.
    # Later managed publication guards can therefore still be tagged 'check'.
    # Only the measured native caller path can establish checker input bytes.
    modules = {}
    for row in events:
        if row.get('operation') == 'OwnedBackendModule':
            modules.setdefault(str(row.get('name', '')).lower(), set()).add(row.get('version'))
    read_scope_verified = all(modules.get(name) == {'1.635.0.1'} for name in (
        'dzdataabs_workspace.dll', 'dzdataabs_compileradapter.dll', 'dzdataabs_compiler_iec.dll'))
    publication_targets = {_id(row.get('target')) for row in events
        if row.get('operation') == 'PublishedResourceCodeCorrespondence' and _id(row.get('target')) is not None}
    reads, queries, query_reads, check_starts = [], [], [], {}
    for row in trace:
        if row.get('event') == 'check-start':
            index = row.get('check_index')
            if type(index) is int and index >= 0:
                check_starts.setdefault(index, []).append(_id(row.get('target')))
            continue
        if row.get('event') != 'workspace-pcode-read' or row.get('phase') != 'check':
            continue
        caller, stack = row.get('caller') or {}, row.get('stack') or []
        stack_modules = {str(frame.get('module', '')).lower() for frame in stack if frame.get('module')}
        native_path = (read_scope_verified and str(caller.get('module', '')).lower() == 'dzdataabs_converter.dll'
            and caller.get('rva') in ('0x829a2', '0x82a34')
            and any(str(frame.get('module', '')).lower() == 'dzdataabs_compileradapter.dll'
                and frame.get('rva') in ('0x6c0e', '0x6c82') for frame in stack))
        identity = _id(row.get('target'))
        managed_publication = (read_scope_verified and not caller.get('module') and bool(caller.get('address'))
            and stack_modules == {'dzdataabs_workspace.dll', 'clr.dll'} and identity in publication_targets)
        origin = 'native-checker' if native_path else 'auxiliary-publication' if managed_publication else 'unknown'
        index = row.get('check_index')
        bound = (origin == 'native-checker' and identity is not None
            and identity == _id(row.get('check_target')) and identity in planned and identity in started
            and type(index) is int and check_starts.get(index) == [identity])
        buffers = row.get('buffers', [])
        record = {'serial': row.get('serial'), 'check_index': index, 'check_target': row.get('check_target'),
                  'resource': row.get('target'), 'channel_sizes': [b.get('size') for b in buffers],
                  'origin': origin, 'native_check_target_bound': bound, 'caller': caller, 'stack': stack}
        query = (row.get('hresult') == row.get('code') == 0 and len(buffers) == 3
                 and row.get('requested_sizes') == [0, 0, 0]
                 and any(b.get('size') and b.get('raw_hex') is None for b in buffers))
        if query:
            queries.append(row.get('serial'))
            query_reads.append(record)
            continue
        actual = []
        for buffer in buffers:
            raw = buffer.get('raw_hex')
            # A successful zero-length channel has no bytes even with NULL.
            if raw is None and buffer.get('size') == 0 and row.get('hresult') == row.get('code') == 0:
                raw = ''
            try:
                actual.append(bytes.fromhex(raw) if raw is not None else None)
            except (TypeError, ValueError):
                actual.append(None)
        expected = generated.get(identity)
        if (row.get('hresult') != 0 or row.get('code') != 0 or len(actual) != 3 or any(b is None for b in actual)
                or any(len(data) != buffer.get('size') for data, buffer in zip(actual, buffers) if data is not None)):
            status = 'unreadable'
        elif expected is None:
            status = 'unbound_resource'
        else:
            status = 'current' if tuple(actual) == tuple(expected) else 'stale'
        reads.append({**record, 'status': status})
    completed_backend = list(dict.fromkeys(_id(row.get('target')) for row in trace
        if row.get('event') == 'backend-check-progress' and row.get('percent') == 100
        and row.get('hresult') == row.get('code') == 0))
    public_completion = [row for row in events if row.get('operation') == 'ProgramCheckCompleted']
    drained_completion = [row for row in events if row.get('operation') == 'ProgramCheckPollingFinished']
    target, drained_targets = None, []
    for row in events:
        if row.get('operation') == 'ProgramCheckReportContext':
            target = _id(row.get('target'))
        elif row.get('operation') == 'ProgramCheckTargetOutcome' and 'public_check' in row:
            drained_targets.append((target, row['public_check']))
    projection_failures = [row for row in trace if row.get('event') == 'diagnostic-projection-failure']
    failed_public_poll = any(row.get('operation') in ('ProgramCheckProgress.GetProgress', 'ProgramCheckOriginalProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0) for row in events)
    public_diagnostics = [report for row in events if (row.get('operation', '').startswith('ProgramCheckProgress')
        or row.get('operation') == 'ProgramCheckOriginalReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    backend_diagnostics = [report for row in trace if row.get('event') == 'backend-check-progress'
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    legacy_completed = bool(public_completion) and public_completion[-1].get('targets') == len(planned)
    drained_completed = (bool(drained_completion) and drained_completion[-1].get('targets') == len(planned)
        and len(drained_targets) == len(planned) and {t for t, _ in drained_targets} == set(planned)
        and all(status in ('completed-accepted', 'completed-rejected') for _, status in drained_targets))
    whole_completed = (bool(planned) and set(started) == set(planned)
        and (legacy_completed or drained_completed) and not projection_failures and not failed_public_poll)
    public_status = ('rejected' if (public_completion and public_completion[-1].get('rejected'))
                     or any(status == 'completed-rejected' for _, status in drained_targets)
                     or any(d['kind'] == 2 for d in public_diagnostics)
                     else 'passed') if whole_completed else 'incomplete'
    source_binding = _public_source_audit_binding(source_audits, public_diagnostics)
    requested = any(row.get('operation') == 'Workspace.GetProgramCheckCollection' for row in events) or bool(started)
    if not requested:
        public_status = 'not_requested'
    observed_reads = reads + query_reads
    checker_reads = [row for row in reads if row['origin'] == 'native-checker']
    if not observed_reads or all(row['origin'] == 'auxiliary-publication' for row in observed_reads):
        correspondence = 'not_observed'
    elif (observation_errors or any(row['origin'] == 'unknown' or
            (row['origin'] == 'native-checker' and not row['native_check_target_bound']) for row in observed_reads)
            or not checker_reads or any(row['status'] not in ('current', 'stale') for row in checker_reads)):
        correspondence = 'not_established'
    elif any(row['status'] == 'stale' for row in checker_reads):
        correspondence = 'stale'
    elif (not planned or set(started) != set(planned)
            or any(row.get('operation') == 'ProgramCheckTargetSkipped' for row in events)
            or any(not any(_id(row['check_target']) == target for row in checker_reads) for target in planned)):
        correspondence = 'partial_targets'
    else:
        correspondence = 'current'
    compile_completed = any(row.get('operation') == 'Progress' and row.get('percent') == 100
        and row.get('hresult', 0) == row.get('code', 0) == 0 for row in events)
    compile_diagnostics = [report for row in events if row.get('operation') in ('Progress', 'CompileRawReports', 'CompilePublicReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    compile_failures = [row for row in events if row.get('operation') in NATIVE_COMPILATION_OPERATIONS + ('Progress.GetProgress', 'Compiler.BuildProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0)]
    compile_started = any(row.get('operation') in NATIVE_COMPILATION_OPERATIONS
        and row.get('hresult') == row.get('code') == 0 for row in events)
    conversion_failure = any(row.get('operation') in ('CompileRawReports', 'CompilePublicReports')
        and any(report.get('kind') == 1 and report.get('code') == 0x20 for report in row.get('reports', [])) for row in events)
    compile_polls = [row for row in events if row.get('operation') == 'Progress' and 'poll' in row]
    public_compile = any(row.get('report_interface') == 'public-compiler' for row in compile_polls)
    public_compile_failures = [row for row in compile_polls if row.get('report_interface') == 'public-compiler'
        and (row.get('hresult') != 0 or row.get('code') != 0)]
    compile_reports_complete = len({row.get('poll') for row in compile_polls}) == len(compile_polls) and all(
        poll.get('hresult') == poll.get('code') == 0 and type(poll.get('poll')) is int and poll['poll'] > 0
        and type(poll.get('count')) is int and 0 <= poll['count'] <= 10000
        and type(poll.get('percent')) is int and 0 <= poll['percent'] <= 100
        and len(matching := [report for report in events
        if report.get('operation') in ('CompileRawReports', 'CompilePublicReports') and report.get('poll') == poll.get('poll')]) == 1
        and matching[0].get('operation') == ('CompilePublicReports' if public_compile else 'CompileRawReports')
        and matching[0].get('percent') == poll.get('percent')
        and isinstance(matching[0].get('reports'), list) and len(matching[0]['reports']) == poll.get('count')
        and (not public_compile or (poll.get('report_interface') == matching[0].get('report_interface') == 'public-compiler'
            and matching[0].get('record_size') == 100
            and all(isinstance(report, dict) and report.get('report_interface') == 'public-compiler'
                and report.get('record_size') == 100 for report in matching[0]['reports']))) for poll in compile_polls)
    compile_conversion = {'status': 'failed' if public_compile_failures else
        'completed' if public_compile and compile_completed and compile_reports_complete else
        'incomplete' if public_compile else 'not_observed', 'failures': public_compile_failures}
    compile_acceptance = ('rejected' if conversion_failure or any(row['kind'] == 2 for row in compile_diagnostics)
        or (compile_failures and not public_compile)
        else 'accepted' if compile_started and compile_completed and compile_reports_complete and not compile_failures
        else 'not_established')
    published_reads = []
    for index, row in enumerate(events):
        if row.get('operation') != 'PublishedResourceCodeCorrespondence':
            continue
        identity = _id(row.get('target'))
        expected, actual = generated.get(identity), (published or {}).get(identity)
        facts = row.get('resource', {})
        successful_read = any(event.get('operation') == 'PublishedResourceCodeRead'
            and _id(event.get('target')) == identity and event.get('hresult') == event.get('code') == 0
            and event.get('name') == facts.get('read_name', {}).get('name')
            and actual is not None and event.get('sizes') == [len(value) for value in actual]
            for event in events[:index])
        bound = (identity is not None and _id(facts.get('id')) == identity
            and facts.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': 8}
            and facts.get('read_name', {}).get('hresult') == facts.get('read_name', {}).get('code') == 0
            and bool(facts.get('read_name', {}).get('name')) and successful_read)
        if not bound or actual is None or expected is None or len(actual) != 3 or len(expected) != 3:
            status = 'not_established'
        elif tuple(actual) != tuple(expected):
            status = 'stale'
        else:
            status = 'current' if any(actual) else 'empty'
        published_reads.append({'resource': list(identity) if identity else None,
            'name': facts.get('read_name', {}).get('name'), 'status': status,
            'channel_sizes': [len(value) for value in actual] if actual is not None else []})
    published_correspondence = ('not_observed' if not published_reads else
        'stale' if any(row['status'] == 'stale' for row in published_reads) else
        'not_established' if any(row['status'] != 'current' for row in published_reads) else
        'partial_targets' if not planned or {tuple(row['resource']) for row in published_reads} != set(planned) else 'current')
    skipped_targets = [row for row in events if row.get('operation') == 'ProgramCheckTargetSkipped']
    check_sessions, active, pending_target, check_started = [], None, None, False
    native_names = {}
    for index, row in enumerate(events):
        operation = row.get('operation')
        if operation == 'NativeObject' and row.get('name_hresult') == row.get('name_code') == 0:
            native_names[_id(row.get('id'))] = row.get('name')
        elif operation == 'ProgramCheckTarget':
            pending_target, check_started = _id(row.get('id')), False
            active = None
        elif operation == 'Compiler.ProgramCheck':
            check_started = row.get('hresult') == row.get('code') == 0
        elif operation == 'ProgramCheckReportContext':
            active = None
            if row.get('path') in ('owned-native-backend', 'original-public-get-progress'):
                identity = _id(row.get('target'))
                public = row['path'] == 'original-public-get-progress'
                active = {'target': identity, 'name': native_names.get(identity), 'public': public,
                    'started': identity is not None and identity == pending_target and check_started,
                    'polls': {}, 'report_polls': set(), 'reports': [], 'valid': True,
                    'poll_failed': False, 'reported_status': None, 'owned_inputs': [],
                    'context_index': index, 'poll_indices': [], 'terminal_indices': [], 'copy_indices': []}
                check_sessions.append(active)
        elif active is not None:
            progress_operation = 'ProgramCheckOriginalProgress' if active['public'] else 'ProgramCheckRawProgress'
            reports_operation = 'ProgramCheckOriginalReports' if active['public'] else 'ProgramCheckRawReports'
            outcome_key = 'public_check' if active['public'] else 'backend_check'
            if operation == 'OwnedCheckInput':
                active['owned_inputs'].append(row)
                active['copy_indices'].append(index)
            elif operation == progress_operation:
                active['poll_indices'].append(index)
                active['valid'] &= row.get('poll') not in active['polls']
                active['polls'][row.get('poll')] = row
                active['valid'] &= row.get('hresult') == row.get('code') == 0
                active['poll_failed'] |= row.get('hresult') != 0 or row.get('code') != 0
                if row.get('percent') == 100:
                    active['terminal_indices'].append(index)
                if active['public']:
                    active['valid'] &= (row.get('target') == list(active['target'] or ())
                        and row.get('report_interface') == 'public-compiler'
                        and type(row.get('record_size')) is int and row['record_size'] == 100
                        and type(row.get('percent')) is int and 0 <= row['percent'] <= 100
                        and type(row.get('count')) is int and row['count'] >= 0)
            elif operation == reports_operation:
                poll = active['polls'].get(row.get('poll'), {})
                reports = row.get('reports', [])
                active['valid'] &= (bool(poll) and poll.get('count') == len(reports)
                    and poll.get('percent') == row.get('percent') and row.get('poll') not in active['report_polls'])
                if active['public']:
                    active['valid'] &= (row.get('target') == list(active['target'] or ())
                        and row.get('report_interface') == 'public-compiler'
                        and type(row.get('record_size')) is int and row['record_size'] == 100
                        and all(report.get('report_interface') == 'public-compiler'
                            and type(report.get('record_size')) is int and report['record_size'] == 100
                            and report.get('poll') == row.get('poll') and report.get('report_index') == report_index
                            for report_index, report in enumerate(reports)))
                active['report_polls'].add(row.get('poll'))
                active['reports'].extend(reports)
            elif operation == 'ProgramCheckTargetOutcome' and outcome_key in row:
                active['reported_status'] = row[outcome_key]
                if active['public']:
                    active['valid'] &= row.get('target') == list(active['target'] or ())
                active = None
    raw_outcomes, public_outcomes = [], []
    for session in check_sessions:
        terminal = any(row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0
            for row in session['polls'].values())
        markers = [row for row in session['reports'] if row.get('kind') == 1 and row.get('code') == 0x23
            and session['name'] and row.get('name', {}).get('text') == session['name']]
        end_marker = len(markers) == 1
        expected_counts = None
        if end_marker:
            arguments = [argument.get('text') for argument in markers[0].get('arguments', [])]
            if (len(arguments) == 2 and all(isinstance(value, str) and value.isascii() and value.isdigit()
                    and len(value) <= 10 for value in arguments)):
                expected_counts = [int(value) for value in arguments]
        # SFC returns source-child names for errors in the selected resource's
        # report stream. Its resource end marker counts those diagnostics too.
        source_named_diagnostics = session['public'] and any(
            isinstance(row.get('input_backend'), dict)
            and row['input_backend'].get('module') == 'DZDataABS_Compiler.dll'
            and row['input_backend'].get('version') == '1.635.0.1'
            for row in session['owned_inputs'])
        observed_counts = [sum(row.get('kind') == kind and (source_named_diagnostics
            or row.get('name', {}).get('text') == session['name'])
            for row in session['reports']) for kind in (2, 3)]
        diagnostics_complete = expected_counts is not None and expected_counts == observed_counts
        completed = (session['started'] and session['valid'] and terminal and end_marker and diagnostics_complete
            and session['reported_status'] in ('completed-accepted', 'completed-rejected')
            and all(poll in session['report_polls'] or row.get('count') == 0
                for poll, row in session['polls'].items()))
        status = ('completed-rejected' if any(row.get('kind') == 2 for row in session['reports'])
            else 'completed-accepted') if completed else 'failed' if session['poll_failed'] else 'incomplete'
        outcomes = public_outcomes if session['public'] else raw_outcomes
        outcomes.append({'target': list(session['target']) if session['target'] else None,
            'status': status, 'reported_status': session['reported_status'],
            'terminal_progress': terminal, 'resource_end_marker': end_marker,
            'diagnostics_complete': diagnostics_complete, 'reported_diagnostic_counts': expected_counts,
            'observed_diagnostic_counts': observed_counts,
            **({'diagnostic_count_scope': 'selected-resource-report-stream'} if source_named_diagnostics else {}),
            'completed_resource': session['name'] if end_marker else None})
    raw_completed = (bool(planned) and len(raw_outcomes) == len(planned) and not skipped_targets
        and {tuple(row['target']) if row['target'] else None for row in raw_outcomes} == set(planned)
        and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in raw_outcomes)
        and bool(drained_completion) and drained_completion[-1].get('targets') == len(planned))
    raw_status = ('completed-rejected' if any(row['status'] == 'completed-rejected' for row in raw_outcomes)
        else 'completed-accepted') if raw_completed else 'incomplete' if raw_outcomes or skipped_targets else 'not_observed'
    if public_outcomes or any(row.get('operation') in ('ProgramCheckOriginalProgress', 'ProgramCheckOriginalReports') for row in events):
        whole_completed &= (bool(public_outcomes)
            and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in public_outcomes)
            and len(public_outcomes) == len({tuple(row['target']) if row['target'] else None for row in public_outcomes}))
        if not whole_completed:
            public_status = 'incomplete'
    owned_inputs = []
    versions = {(row.get('name'), row.get('version')) for row in events if row.get('operation') == 'OwnedBackendModule'}
    ec_version = '15.50' if {version for name, version in versions if name == 'ECCompiler_IEC.dll'} == {'15.50'} else None
    for session in check_sessions:
        copies = session['owned_inputs']
        if not copies:
            continue
        backend = copies[0].get('input_backend')
        sfc = isinstance(backend, dict) and backend.get('module') == 'DZDataABS_Compiler.dll'
        required = {'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler.dll', 'DZDataABS_SICConverter.dll'} if sfc else {
            'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll', 'DZDataABS_SICConverter_IEC.dll'}
        owner_version = '1.635.0.1' if all((name, '1.635.0.1') in versions
            and all(version == '1.635.0.1' for module, version in versions if module == name) for name in required) else None
        submitted = [row for row in copies if row.get('stage') == 'submitted']
        completed = [row for row in copies if row.get('stage') == 'completed']
        code = generated.get(session['target'])
        proof = bind_native_check_input(submitted[0] if len(submitted) == 1 else None,
            completed[0] if len(completed) == 1 else None,
            generated={session['name']: code[0]} if session['name'] and code is not None else {},
            snapshots=owned_snapshots or {}, native_version=owner_version, checker_version=owner_version if sfc else ec_version)
        if not session['started'] or any(_id(row.get('target')) != session['target'] for row in copies):
            proof = {'status': 'unresolved', 'reason': 'owned input is not bound to this original check start',
                     'public_check_promoted': False}
        if session['public'] and (len(copies) != 2 or [row.get('stage') for row in copies] != ['submitted', 'completed']
                or len(session['terminal_indices']) != 1
                or not session['context_index'] < session['copy_indices'][0]
                    < session['poll_indices'][0]
                or not session['terminal_indices'][0] < session['copy_indices'][-1]):
            proof = {'status': 'unresolved', 'reason': 'owned copies do not bracket the original public check',
                     'public_check_promoted': False}
        owned_inputs.append({'target': list(session['target']) if session['target'] else None, **proof})
    owned_correspondence = ('not_observed' if not owned_inputs else
        'unresolved' if any(row['status'] == 'unresolved' for row in owned_inputs) else
        'stale' if any(row['status'] == 'stale' for row in owned_inputs) else
        'partial_targets' if {tuple(row['target']) for row in owned_inputs} != set(planned) else 'current')
    selected = selection or {'status': 'not_observed'}
    selection_current = (selected.get('status') == 'verified' and selected.get('source_bytes_current') is True
        and selected.get('configuration_bytes_current') is True and bool(selected.get('selected_sources'))
        and not selected.get('empty_task'))
    retained_code_current = (owned_correspondence == 'current' and published_correspondence == 'current'
        and correspondence in ('current', 'not_observed') and not observation_errors)
    current_source_check = ('established' if selection_current and compile_acceptance == 'accepted'
        and (correspondence == 'current' or retained_code_current) and whole_completed else 'not_established')
    return {
        'selection': selected,
        'compilation': {'completed': compile_completed, 'acceptance': compile_acceptance,
            'diagnostics_complete': compile_reports_complete if compile_polls else None,
            'diagnostic_conversion': compile_conversion,
            'diagnostics': compile_diagnostics, 'failures': compile_failures,
            'returned_resources': sum(row.get('operation') == 'Resource' for row in events), 'bound_resources': len(generated)},
        'publication': {'completed': any(row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck'
            and row.get('hresult') == row.get('code') == 0 for row in events)},
        'checker_resources': {'correspondence': correspondence, 'reads': reads, 'size_queries': queries,
                              'size_query_reads': query_reads, 'scope_verified': read_scope_verified,
                              'observation_errors': observation_errors},
        'owned_check_inputs': {'correspondence': owned_correspondence, 'targets': owned_inputs,
            'provenance': 'original retained task code and original envelope reader; no interception'},
        'published_resources': {'correspondence': published_correspondence, 'reads': published_reads,
            'provenance': 'original workspace read-back before checking; not an observed checker input'},
        'raw_backend_check': {'status': raw_status, 'completed': raw_completed,
            'targets': raw_outcomes, 'skipped_targets': skipped_targets,
            'public_adapter_projection': 'not_called' if raw_outcomes else 'not_observed'},
        'targets': {'planned': [list(t) for t in planned], 'started': [list(t) for t in started],
                    'backend_completed': [list(t) for t in completed_backend]},
        'public_check': {'status': public_status, 'completed': whole_completed},
        'diagnostic_projection': {'status': 'failed' if projection_failures or failed_public_poll else
            'completed' if whole_completed else 'not_completed', 'failures': projection_failures,
            'source_body_binding': source_binding, 'source_audits': source_audits or [],
            'target_checks': public_outcomes,
            'public_diagnostics': public_diagnostics, 'backend_diagnostics': backend_diagnostics,
            'empty_public_list_means_no_errors': whole_completed and not public_diagnostics and not backend_diagnostics},
        'save': {'completed': any(row.get('operation') == 'SaveProject' and row.get('hresult') == row.get('code') == 0
            for row in events), 'exported': any(row.get('operation') == 'NativeExport'
                or row.get('operation') == 'ExportOneFileProject' and row.get('hresult') == row.get('code') == 0 for row in events)},
        'reopen': reopened or {'status': 'not_observed'},
        'current_source_check': current_source_check,
    }


def correlate_fbd_instance_output(query, diagnostic, records, references, context, contexts, *, instance, connectivity):
    """Observed FXU/FX2C and FX3G direct BOOL-input/device-output project FBD FB.

    Native lexical reads and instance intervals are bound by the caller. Keep
    the callee terminal and its ordinary Program's call node as separate sites.
    Other CPUs, expressions, fanout and nested callers remain unresolved.
    """
    from plc.device_identity import canonical_operand

    def reject(reason):
        return {'status': 'unresolved', 'public_check_promoted': False, 'reason': reason}
    def bounds(row):
        return [row.get(key) for key in ('left', 'top', 'right', 'bottom')]
    def node_bounds(node):
        return [node['x'], node['y'], node['x'] + node['width'], node['y'] + node['height']]

    model, location = context.object_model(), query.get('location') or {}
    pou, resource, step = location.get('pou'), query.get('resource'), query.get('code_step')
    interval = instance.get('compiled_interval') or {}
    if (model.get('cpu') not in ('FXU/FX2C', 'FX3G') or model.get('schema_version') != 2
            or model.get('unknown_record_count') != 0 or len(context.program.blocks) != 1
            or context.sources.declarations[context.sources.local_table].owner_pou_type != 0x01000002
            or query.get('hresult') != 0 or query.get('code') != 0
            or location.get('program_kind') != 208 or location.get('library') != ''
            or not pou or location.get('network') != 1
            or instance.get('status') != 'instance-resolved'
            or not isinstance(instance.get('instance'), str) or not instance.get('task')
            or diagnostic.get('kind') != 2 or diagnostic.get('code') != 0x050c9300
            or diagnostic.get('name', {}).get('text') != resource or diagnostic.get('step') != step
            or type(interval.get('step_start')) is not int or type(interval.get('step_count')) is not int
            or type(step) is not int or not interval['step_start'] <= step < interval['step_start'] + interval['step_count']):
        return reject('outside observed FXU/FX2C or FX3G direct FBD instance/output scope')
    arguments = diagnostic.get('arguments', [])
    writes = [r for r in records if r.get('native_step') == step]
    if (len(arguments) != 1 or not arguments[0].get('text') or len(writes) != 1
            or writes[0].get('op') != 'OUT' or len(writes[0].get('args', [])) != 1
            or canonical_operand(writes[0]['args'][0]) != canonical_operand(arguments[0]['text'])):
        return reject('diagnostic does not select one original native OUT operand')
    write = writes[0]
    prior = [r for r in records if type(r.get('record_index')) is int
             and type(write.get('record_index')) is int and r['record_index'] == write['record_index'] - 1]
    if (len(prior) != 1 or prior[0].get('op') != 'LD' or len(prior[0].get('args', [])) != 1
            or prior[0].get('source_end') != write.get('source_offset')
            or type(prior[0].get('native_step')) is not int or prior[0]['native_step'] + 1 != step):
        return reject('not the observed adjacent native LD/OUT pair')
    read = prior[0]
    scoped = [r for r in references if r.get('source') == pou and r.get('resource') == resource
              and r.get('library') == '' and r.get('program_kind') == 208 and r.get('network') == 1
              and (r.get('task'), r.get('instance')) == (instance['task'], instance['instance'])]
    inputs = [r for r in scoped if r.get('attribute') == 1 and r.get('class_code') == 3
              and r.get('data_type') == 1 and r.get('array_data_type') == 0 and r.get('address') == read['args'][0]]
    outputs = [r for r in scoped if r.get('attribute') == 2 and r.get('class_code') == r.get('data_type') == 0
               and r.get('array_data_type') == 0 and r.get('address')
               and canonical_operand(r['address']) == canonical_operand(write['args'][0])]
    if len(inputs) != 1 or len(outputs) != 1:
        return reject('original scalar input or output reference is missing or ambiguous')
    nodes = _native_network_members(model['nodes'], 1, 1)
    if nodes is None:
        return reject('current callee block membership is unavailable')
    selected = []
    for template, reference in (('input', inputs[0]), ('output', outputs[0])):
        matches = [n for n in nodes if n.get('template') == template and n.get('symbol') == reference.get('name')
                   and node_bounds(n) == bounds(reference) and len(n.get('ports', [])) == 1
                   and not n['ports'][0].get('negated')]
        if len(matches) != 1:
            return reject('current callee terminal is missing or ambiguous')
        selected.append(matches[0])
    source, terminal = selected
    keys = {(n['source_offset'], 0) for n in selected}
    if connectivity is None or connectivity.logical_name != model['program']:
        return reject('current callee connectivity is unavailable')
    nets = [n for n in connectivity.nets if n.block_index == 0
            and {(p.node_offset, p.port_index) for p in n.ports} == keys and len(n.ports) == 2]
    if len(nets) != 1:
        return reject('current callee has no unique two-terminal connection')
    points = {(p.node_offset, p.port_index): [p.point.x, p.point.y] for p in nets[0].ports}
    if any(points[n['source_offset'], 0] != [n['x'] + n['ports'][0]['x'], n['y'] + n['ports'][0]['y']]
           for n in selected):
        return reject('current callee port coordinates differ from connectivity')
    calls = [r for r in references if r.get('program_kind') == 208 and r.get('class_code') == 1
             and r.get('data_type') == 15 and r.get('type') == pou and r.get('attribute') == 1
             and r.get('library') == '' and r.get('resource') == resource and r.get('task') == instance['task']
             and r.get('instance') and r.get('name') and r.get('source') == r.get('instance')
             and r['instance'] + '.' + r['name'] == instance['instance']]
    if len(calls) != 1 or calls[0]['source'] not in contexts:
        return reject('original ordinary FBD caller is missing or ambiguous')
    call, caller = calls[0], contexts[calls[0]['source']]
    caller_model = caller.object_model()
    if (caller.sources.cpu != model['cpu'] or caller_model.get('unknown_record_count') != 0
            or caller.sources.declarations[caller.sources.local_table].owner_pou_type != 0x01000000):
        return reject('current ordinary caller source is unavailable')
    caller_nodes = _native_network_members(caller_model['nodes'], call.get('network'), len(caller.program.blocks))
    objects = [n for n in caller_nodes or [] if n.get('template') == 'function_block:' + pou
               and n.get('symbol') == call['name'] and node_bounds(n) == bounds(call)]
    if len(objects) != 1:
        return reject('current caller object is missing or ambiguous')
    return {'status': 'uniquely_correlated', 'public_check_promoted': False,
        'resource': resource, 'diagnostic_step': step, 'instance': instance['instance'], 'task': instance['task'],
        'compiled_interval': interval,
        'source': {'pou': pou, 'program_kind': 208, 'network': 1, 'object_id': terminal['id'],
                   'symbol': terminal['symbol'], 'source_offset': terminal['source_offset'], 'bbox': node_bounds(terminal)},
        'caller': {'pou': call['source'], 'program_kind': 208, 'network': call['network'],
                   'symbol': call['name'], 'object_id': objects[0]['id'], 'source_offset': objects[0]['source_offset'], 'bbox': bounds(call)},
        'native_input_reference': inputs[0], 'native_output_reference': outputs[0], 'native_caller_reference': call,
        'connection': {'net_index': nets[0].index, 'block_index': 0, 'wire_offsets': list(nets[0].wire_offsets)},
        'limits': ['observed direct scalar BOOL input/device output in a project FBD FB',
                   'public diagnostic conversion and overall check remain separate']}


def correlate_fbd_bool_output(query, diagnostic, records, references, model, *, selected_pou, connectivity=None, instance=None):
    """Correlate observed FX3U/FX3UC and Q03UDV scalar FB output diagnostics.

    records must already agree with native lexical reads and native prefix
    GetStepSize. The caller binds all inputs to one current owned compilation.
    Optional connectivity is the existing Core graph read from that same POU.
    Only the observed scalar duplicate-output form is projected. Q03UDV native
    network ordinals select source block membership, including empty blocks;
    FX3U/FX3UC retains the observed single-network scope.
    """
    from plc.device_identity import canonical_operand
    missing = {'status': 'unresolved', 'public_check_promoted': False}
    def reject(reason):
        return {**missing, 'reason': reason}
    def device(value):
        return canonical_operand(value)
    location = query.get('location') or {}
    resource, step = query.get('resource'), query.get('code_step')
    blocks = model.get('blocks', [{}])
    if (model.get('schema_version') != 2 or model.get('cpu') not in ('FX3U/FX3UC','Q03UDV')
            or model.get('unknown_record_count') != 0
            or not isinstance(blocks, list) or not blocks
            or any(not isinstance(block, dict) for block in blocks)
            or (model.get('cpu') == 'FX3U/FX3UC' and len(blocks) != 1)):
        return reject('outside observed FX3U/FX3UC or Q03UDV source/graph scope')
    if (query.get('hresult') != 0 or query.get('code') != 0
            or location.get('program_kind') != 208 or location.get('library') != ''
            or not selected_pou or location.get('pou') != selected_pou):
        return reject('no selected native FBD source location')
    network = location.get('network')
    nodes = _native_network_members(model.get('nodes', []), network, len(blocks))
    wires = _native_network_members(model.get('wires', []), network, len(blocks))
    if nodes is None or wires is None:
        return reject('native FBD network or current block membership is unavailable')
    arguments = diagnostic.get('arguments', [])
    if (diagnostic.get('kind') != 2 or diagnostic.get('code') != 0x050c9300
            or diagnostic.get('name', {}).get('text') != resource
            or diagnostic.get('step') != step or len(arguments) != 1):
        return reject('outside observed duplicate-output diagnostic form')
    at = [r for r in records if r.get('native_step') == step]
    if len(at) != 1 or at[0].get('op') != 'OUT' or len(at[0].get('args', [])) != 1:
        return reject('diagnostic does not select one native OUT')
    output = at[0]
    if not arguments[0].get('text') or device(output['args'][0]) != device(arguments[0]['text']):
        return reject('native diagnostic operand differs from OUT')
    def output_chain(write):
        # Q03UDV's controlled one-driver/two-terminal case emits LD/OUT/OUT.
        # Follow only contiguous ordinary OUT records; every other opcode,
        # lexical gap or duplicate record leaves the producer unresolved.
        chain,seen = [write],set()
        for _ in range(64):
            current=chain[-1]
            if (any(type(current.get(key)) is not int for key in
                    ('record_index','native_step','source_offset','source_end'))
                    or current['record_index'] in seen or current['record_index']<0
                    or sum(type(r.get('record_index')) is int and r['record_index']==current['record_index']
                           for r in records)!=1
                    or not 0<=current['source_offset']<current['source_end']):
                return None
            seen.add(current['record_index'])
            previous=[r for r in records if type(r.get('record_index')) is int
                and r['record_index']==current['record_index']-1]
            if len(previous)!=1:
                return None
            prior=previous[0]
            if (len(prior.get('args',[]))!=1
                    or any(type(prior.get(key)) is not int for key in ('native_step','source_offset','source_end'))
                    or prior['source_end']!=current['source_offset']
                    or not 0<=prior['source_offset']<prior['source_end']
                    or not 0<=prior['native_step']<current['native_step']):
                return None
            if prior.get('op')=='LD':
                return [prior,*reversed(chain)]
            if prior.get('op')!='OUT':
                return None
            chain.append(prior)
        return None
    def direct_read(write):
        chain=output_chain(write)
        return chain[0] if chain is not None else None
    chain=output_chain(output)
    if chain is None:
        return reject('not a contiguous native LD/OUT output chain')
    read=chain[0]
    scoped = [r for r in references if r.get('resource') == resource and r.get('source') == selected_pou
        and r.get('program_kind') == 208 and r.get('library') == ''
        and r.get('network') == location.get('network')
        and (instance is None or (r.get('task'), r.get('instance')) == instance)]
    producers = [r for r in scoped if r.get('class_code') == 4 and r.get('data_type') == 1
        and type(r.get('array_data_type')) is int and r['array_data_type'] == 0
        and isinstance(r.get('name'), str) and r['name']
        and r.get('attribute') == 1 and r.get('address') == read['args'][0]]
    if len(producers) != 1:
        return reject('FB BOOL output address is missing or ambiguous')
    producer = producers[0]
    def bbox(row):
        values = [row.get(key) for key in ('left','top','right','bottom')]
        return values if all(type(value) is int for value in values) else None
    def node_bbox(node):
        return [node['x'],node['y'],node['x']+node['width'],node['y']+node['height']]
    sources = []
    for node in nodes:
        if not node['template'].startswith('function_block:') or node_bbox(node) != bbox(producer):
            continue
        for port in node.get('ports', []):
            if (port.get('side') == 'out' and port.get('class_code') == 4 and port.get('data_type') == 'BOOL'
                    and not port.get('negated')
                    and producer['name'] == node['symbol'] + '.' + port.get('formal_name', port['name'])):
                sources.append((node,port))
    if len(sources) != 1:
        return reject('current graph FB output interface is missing or ambiguous')
    source, port = sources[0]
    calls = [r for r in scoped if r.get('name') == source['symbol'] and r.get('class_code') == 1
        and r.get('type') == source['template'].split(':',1)[1] and bbox(r) == node_bbox(source)
        and r.get('task') == producer.get('task') and r.get('instance') == producer.get('instance')]
    if len(calls) != 1:
        return reject('current native FB instance is missing or ambiguous')
    point = [source['x']+port['x'],source['y']+port['y']]
    def scalar_terminal(row):
        # Original direct-device references have no declaration type. The
        # independently observed scalar local/global BOOL references do.
        # Other declaration roles, arrays and conflicting types stay unresolved.
        return (type(row.get('array_data_type')) is int and row['array_data_type']==0
            and ((type(row.get('class_code')) is int and row['class_code']==0
                  and type(row.get('data_type')) is int and row['data_type']==0)
                or (type(row.get('class_code')) is int and row['class_code'] in (1,8)
                    and type(row.get('data_type')) is int and row['data_type']==1)))
    def connection(node, endpoint):
        target_point = [node['x']+endpoint['x'],node['y']+endpoint['y']]
        if connectivity is None:
            return {'kind':'port_overlap','wire_offsets':[]} if target_point == point else None
        if connectivity.logical_name != model.get('program'):
            return None
        source_key = (source['source_offset'], source['ports'].index(port))
        target_key = (node['source_offset'], 0)
        try:
            net = connectivity.net_for_port(*source_key)
            target_net = connectivity.net_for_port(*target_key)
        except KeyError:
            return None
        # Observed overlap, straight, bent and T-junction forms have one scalar
        # FB driver and only output terminals; expression nodes remain excluded.
        keys = {(p.node_offset,p.port_index) for p in net.ports}
        if (net.block_index != network - 1 or target_net.block_index != network - 1
                or net.index != target_net.index or len(keys) != len(net.ports)
                or not {source_key,target_key} <= keys):
            return None
        graph_points = {(p.node_offset,p.port_index):[p.point.x,p.point.y] for p in net.ports}
        if graph_points[source_key] != point or graph_points[target_key] != target_point:
            return None
        for offset,index in keys - {source_key,target_key}:
            terminals = [n for n in nodes if n.get('source_offset') == offset]
            if len(terminals) != 1 or index != 0:
                return None
            terminal = terminals[0]
            ports = terminal.get('ports', [])
            if terminal['template'] != 'output' or len(ports) != 1 or ports[0].get('negated'):
                return None
            if [terminal['x']+ports[0]['x'],terminal['y']+ports[0]['y']] != graph_points[offset,index]:
                return None
            # Direct device terminals report data_type=0; use their original
            # native LD/OUT operands rather than inventing a BOOL type field.
            writes = [r for r in scoped if r.get('attribute') == 2 and r.get('address') and scalar_terminal(r)
                and r.get('name') == terminal['symbol'] and bbox(r) == node_bbox(terminal)
                and r.get('task') == producer.get('task') and r.get('instance') == producer.get('instance')]
            if len(writes) != 1:
                return None
            native_writes = [r for r in records if r.get('op') == 'OUT' and len(r.get('args', [])) == 1
                and device(r['args'][0]) == device(writes[0]['address'])]
            if not any((previous := direct_read(r)) is not None and previous['args'] == read['args']
                       for r in native_writes):
                return None
        if any(sum(w.get('source_offset') == offset for w in wires) != 1
                for offset in net.wire_offsets):
            return None
        if point != target_point and not net.wire_offsets:
            return None
        return {'kind':'port_overlap' if point == target_point else 'wire_network',
                'net_index':net.index,'block_index':network-1,
                'output_terminal_count':len(keys)-1,'wire_offsets':list(net.wire_offsets),
                'wire_scope':'all conductors in the current Core net, not a unique path'}
    destinations = []
    for reference in scoped:
        if (reference.get('attribute') != 2 or not reference.get('address') or not scalar_terminal(reference)
                or device(reference['address']) != device(output['args'][0])
                or reference.get('task') != producer.get('task')
                or reference.get('instance') != producer.get('instance')):
            continue
        for node in nodes:
            if (node['template'] != 'output' or node['symbol'] != reference['name']
                    or node_bbox(node) != bbox(reference)):
                continue
            ports = node.get('ports', [])
            if len(ports) == 1 and not ports[0].get('negated'):
                link = connection(node, ports[0])
                if link is not None:
                    destinations.append((node,reference,link))
    if len(destinations) != 1:
        result = reject('directly connected current output object is missing or ambiguous')
        result.update(original_kind=diagnostic['kind'],original_code=diagnostic['code'],resource=resource,
            source_pou=selected_pou,diagnostic_step=step,source_status='uniquely_correlated',
            network=network,
            source={'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
                'port_name':port['name'],'side':'out','point':point,'block_index':network-1},
            output_status='ambiguous' if destinations else 'missing',
            output_candidates=sorted({node['id'] for node,_,_ in destinations}))
        if len(chain)>2:
            result['native_output_chain']=chain
        return result
    destination, reference, link = destinations[0]
    result = {'status':'uniquely_correlated','public_check_promoted':False,
        'original_kind':diagnostic['kind'],'original_code':diagnostic['code'],
        'resource':resource,'source_pou':selected_pou,'diagnostic_step':step,'network':network,
        'native_pair':{'read':read,'write':output},'native_member':producer,
        'source':{'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
            'port_name':port['name'],'side':'out','point':point,'block_index':network-1},
        'output':{'object_id':destination['id'],'source_offset':destination['source_offset'],
            'symbol':destination['symbol'],'bbox':node_bbox(destination),'native_reference':reference,
            'block_index':network-1},
        'connection':link,
        'limits':['contiguous native LD/OUT chain and direct scalar FB BOOL/output connection only',
            'one selected current native FBD network; arrays and unknown records unresolved',
            'current native facts and source context required; no execution claim']}
    if len(chain)>2:
        result['native_output_chain']=chain
    return result

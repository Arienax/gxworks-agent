"""Source inventory and native check planning."""
from __future__ import annotations
from .project_metadata import project_stream_mapping
from gxw.diagnostic_scope import (
    NATIVE_SOURCE_CAPACITY,
)


def _native_validation_sources(raw):
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    from .project_metadata import logical_mapping, project_source_groups, read_project_text_context
    from .declarations import local_table_for, parse_declarations
    from .source_header import source_payload_offset
    from .models import GXWFormatError
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    nested = read_project_hdb(outer).payloads
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
        from .models import GXWFormatError
        source = frame_token_pou(raw)
        # The native body includes its self-sized 20-byte prefix. The source
        # envelope's independently observed 20/24-byte tail is not body data.
        start = source.body_offset - 20
        if source.body_end - start > 8 * 1024 * 1024:
            raise GXWFormatError('native ladder source body exceeds the bounded workspace extent')
        return source.raw[start:source.body_end]
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
    from .integrity import read_project_hdb
    from .declarations import parse_declarations
    from .models import GXWFormatError
    from .project_metadata import logical_mapping
    from .source_header import source_payload_offset
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    nested = read_project_hdb(outer).payloads
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
        'programs': [
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
    from .integrity import read_project_hdb
    from .declarations import parse_declarations
    from .project_metadata import logical_mapping
    _, programs = _native_validation_sources(raw)
    local = {p['name']: p['declarations'] for p in programs}
    if not include_globals:
        return local, {}
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    nested = read_project_hdb(outer).payloads
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


def native_import_selection(raw, imported_hdb):
    """Compare mapped HDB streams, including global declarations/libraries.

    Imported caches are only input-preservation facts; their bytes never prove
    that compilation or checking used current code.
    """
    from .container_writer import validate_cfb_streams
    from .project_metadata import logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
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

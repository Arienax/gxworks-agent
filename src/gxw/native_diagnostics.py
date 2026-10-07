"""Interpret original source, code, completion and diagnostic observations.

Compilation, publication, actual checker inputs, source locations and public
projection remain independent facts. None establishes PLC execution.
"""
from __future__ import annotations


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
    documents = {name: parse_declarations(nested[key], logical_name=name)
                 for name, key in mapping.items() if name.endswith('.lh')}
    programs, names = [], set()
    for logical, key in mapping.items():
        if not logical.endswith('.pou'):
            continue
        labels = local_table_for(documents, logical, groups)
        if labels not in mapping:
            raise GXWFormatError('native validation source has no paired declarations: ' + logical)
        declarations = documents[labels]
        name = declarations.owner_name
        kind = nested[key][source_payload_offset(nested[key])]
        if declarations.scope != 'local' or not name or name in names or kind not in (193, 208):
            raise GXWFormatError('native validation source is outside the current structured body binding: ' + logical)
        names.add(name)
        programs.append({'name': name, 'program_kind': kind, 'logical_name': logical, 'raw': nested[key]})
    if not 1 <= len(programs) <= 64:
        raise GXWFormatError('native validation source selection is empty or exceeds the bound')
    return context, programs


def native_validation_source_plan(raw):
    """Bind an isolated structured-project validation to all local POU bodies."""
    import base64
    from .native_write import workspace_body
    context, programs = _native_validation_sources(raw)
    return {'cpu': context['cpu'], 'codepage': context['codepage'],
        'lexical_reader': native_code_reader_scope(context['cpu']), 'programs': [
        {'name': row['name'], 'program_kind': row['program_kind'],
         'body': base64.b64encode(workspace_body(row['raw'])).decode('ascii')} for row in programs]}


def native_code_reader_scope(cpu):
    """Observed converter contexts, bound to exact CPU menu selections."""
    if cpu == 'Q03UDV':
        return {'scope_cpu': cpu, 'profile': 'q03udv', 'module': 'ECCodeGenerator2.dll',
                'version': '15.41', 'native_cpu': 209, 'native_versions': [25]}
    if cpu == 'FX3U/FX3UC':
        return {'scope_cpu': cpu, 'profile': 'fx3u', 'module': 'ECCodeGeneratorFX2.dll',
                'version': '15.31', 'native_cpu': 520, 'native_versions': []}
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
    ends = [i for i, row in enumerate(events) if row.get('operation') == 'ProgramCheckRawProgress'
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
    if native_version != '1.635.0.1' or checker_version != '15.50':
        return {**unresolved, 'reason': 'unobserved native task owner or checker version'}
    if not isinstance(submitted, dict) or not isinstance(completed, dict):
        return {**unresolved, 'reason': 'original submission and completion copies are required'}
    target = _id(submitted.get('target'))
    if (target is None or not any(target)
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in target)
            or target != _id(completed.get('target'))
            or submitted.get('stage') != 'submitted' or completed.get('stage') != 'completed'
            or submitted.get('manager') != completed.get('manager') or not submitted.get('manager')
            or type(submitted.get('mask')) is not int or not 0 < submitted['mask'] <= 0x7fffffff
            or submitted.get('mask') != completed.get('mask')):
        return {**unresolved, 'reason': 'native task identities or stages differ'}
    stages = []
    for event in (submitted, completed):
        rows = event.get('rows')
        if (event.get('operation') != 'OwnedCheckInput'
                or event.get('provenance') != 'original ProcessManager.ProgramCheck owned code copy; read-only; no interception'
                or event.get('compiler_member_offset') != 12 or event.get('manager_records_offset') != 32
                or not isinstance(rows, list) or not 1 <= len(rows) <= 1024):
            return {**unresolved, 'reason': 'original owned input table is not bound'}
        stage = {}
        for row in rows:
            name, framing = row.get('resource'), row.get('framing') or {}
            full = snapshots.get(row.get('file'))
            if (not isinstance(name, str) or not name or name in stage or row.get('tag') != -1
                    or not isinstance(full, bytes) or len(full) != row.get('bytes') or len(full) < 4
                    or framing.get('reader_module') != 'ECCompiler_IEC.dll'
                    or framing.get('reader_version') != '15.50' or framing.get('reader_rva') != 0x39f30
                    or framing.get('code') != 0
                    or framing.get('provenance') != 'original checker envelope helper; optional tables omitted; read-only'):
                return {**unresolved, 'reason': 'saved original input or envelope reader is not bound'}
            first = int.from_bytes(full[:2], 'little')
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
            'coverage': 'primary code consumed by original ECCompiler; other channels are separate',
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
    from .native_write import workspace_body
    try:
        expected = workspace_body(program_raw)
    except GXWFormatError:
        return {**missing, 'reason': 'source snapshot framing is outside the observed workspace body'}
    return {'status': 'current' if expected == current else 'stale',
            'body_id': list(identity), 'native_snapshot': filename,
            'source_body_bytes': len(expected), 'native_body_bytes': size,
            'provenance': 'source bytes versus last original getter read of this body'}


def project_native_source_identity(source, *, pou, program_kind, project_id, native_version):
    """Verify original project-local body, POU parent and language reads."""
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
    if (not isinstance(pou, str) or not pou
            or kind not in (1, 193, 208) or source.get('status') != 'verified-source-object'
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
            or not accepted(owner.get('read_type')) or owner['read_type'].get('data_type') != 26
            or not accepted(owner.get('read_name')) or owner['read_name'].get('name') != pou
            or not accepted(language) or native_id(language.get('object_id')) != owner_id
            or language.get('value') != kind or language.get('expected') != kind):
        return {**unresolved, 'reason': 'native source type, name or POU language does not match'}
    return {'status': 'source-resolved', 'public_check_promoted': False,
            'source': {'project_id': list(project), 'pou': pou,
                       'body_id': list(body_id), 'pou_id': list(owner_id), 'program_kind': kind}}


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version, references=None):
    """Bind an original code diagnostic to its independently queried POU."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    def text(value):
        return value.get('text') if isinstance(value, dict) else None
    if (diagnostic.get('kind') not in (2, 3) or type(diagnostic.get('code')) is not int
            or query.get('hresult') != 0 or query.get('code') != 0
            or text(diagnostic.get('library')) != ''
            or text(diagnostic.get('name')) != query.get('resource')
            or type(diagnostic.get('step')) is not int or diagnostic['step'] < 0
            or diagnostic['step'] != query.get('code_step')):
        return {**unresolved, 'reason': 'original diagnostic and native resource query do not match'}
    location = query.get('location') or {}
    if location.get('library') != '':
        return {**unresolved, 'reason': 'no verified project-local source identity'}
    identity = project_native_source_identity(query.get('source_object'), pou=location.get('pou'),
        program_kind=location.get('program_kind'), project_id=project_id, native_version=native_version)
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
                  and row.get('source') == pou and row.get('library') == ''
                  and row.get('resource') == query['resource'] and row.get('task') and row.get('instance')
                  and (row.get('top') == location.get('start_step') and row.get('attribute') == 2
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
        expected_source = {'library': '', 'pou': row['instance'], 'program_kind': kind,
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

    Q03UDV reorder and empty-network controls establish that empty source blocks
    retain their ordinal. Never infer membership from coordinates or omit an
    unassigned record from a multi-block source.
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
                                      source_body_id=None, logical_name=None):
    """Project observed Q03UDV structured compilation positions, without code.

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
    if (cpu != 'Q03UDV' or not isinstance(pou, str) or not pou or report.get('kind') not in (2, 3)
            or report.get('instance_kind') != 6 or (not public and report.get('library', {}).get('text') != '')
            or report.get('name', {}).get('text') != pou or report.get('instance', {}).get('text') != pou
            or program_kind not in (193, 208) or report.get('program_kind') != program_kind):
        return {**unresolved, 'reason': 'outside observed current Q03UDV structured compile source scope'}
    source = {'pou': pou, 'program_kind': program_kind}
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
        if (type(line) is not int or not 0 <= line < len(lines) or report.get('step') != line
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
                            caller_block_count=1):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Follow
    explicit native FB call references through ST callers to a current FBD
    node; do not infer parents by splitting instance names. Optional same-
    compile debug data can resolve an inlined FB without a link-map row.
    When original instance range responses are supplied, require their complete
    agreement and do not replace a failed or conflicting API result with caches.
    """
    location = query.get('location') or {}
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if (query.get('hresult') != 0 or query.get('code') != 0 or location.get('program_kind') != 193
            or location.get('library') != ''):
        return {**unresolved, 'reason': 'no successful native ST location'}
    pou, line = location.get('pou'), location.get('start_step')
    lines = source_texts.get(pou, '').splitlines()
    if type(line) is not int or not 0 <= line < len(lines):
        return {**unresolved, 'reason': 'native source line outside selected source'}
    native = query.get('query', {})
    step = native.get('start_step')
    line_references = [row for row in references if row.get('program_kind') == 193 and row.get('source') == pou
               and row.get('library') == ''
               and row.get('top') == line and row.get('attribute') == 2
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
    if 'debug_evidence' in interval:
        result['debug_evidence'] = interval['debug_evidence']
    if 'native_range_evidence' in interval:
        result['native_range_evidence'] = interval['native_range_evidence']
    return result



def bind_native_source_chain(programs, required_sources, *, project_id, native_version,
                             selections, reads, snapshots):
    """Require current original bodies for every callee and caller in a chain.

    Equal compiled code, names or coordinates cannot bind source offsets. Use
    the last selection and body read for each POU; never fill a missing caller
    with another source that happens to have the same text or graph.
    """
    if (not required_sources or any(not isinstance(name, str) or not name for name in required_sources)
            or len(required_sources) > 64):
        return {'status': 'unresolved', 'reason': 'native source chain is missing or outside the bound',
                'sources': []}
    sources = []
    for pou in dict.fromkeys(required_sources):
        matching = [row for row in selections if row.get('operation') == 'NativeBodySelection'
                    and row.get('name') == pou]
        selected = matching[-1] if matching else {}
        identity = project_native_source_identity(selected.get('source_object'), pou=pou,
            program_kind=selected.get('program_kind'), project_id=project_id, native_version=native_version)
        if identity['status'] != 'source-resolved':
            sources.append({'pou': pou, 'status': 'unresolved', 'reason': identity['reason']})
            continue
        raw = programs.get(pou)
        if not isinstance(raw, bytes):
            sources.append({'pou': pou, 'status': 'unresolved', 'reason': 'selected source buffer is missing'})
            continue
        binding = bind_native_source_snapshot(raw, body_id=identity['source']['body_id'],
            reads=reads, snapshots=snapshots)
        sources.append({'pou': pou, **binding})
    statuses = {row['status'] for row in sources}
    return {'status': 'current' if statuses == {'current'} else
            'unresolved' if 'unresolved' in statuses else 'stale', 'sources': sources}


def bind_native_compile_source_context(programs, *, project_id, native_version, events, snapshots,
                                       selection, compilation):
    """Bind completed Build reports to original source reads around that Build.

    Rejected compilation can have current source positions even though it has
    no executable code or check task. All selected declarations/configuration
    must also match the independently imported workspace HDB.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False, 'stages': []}
    builds = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.Build'
              and row.get('hresult') == row.get('code') == 0]
    ends = [i for i, row in enumerate(events) if row.get('operation') in ('CompileRawReports', 'CompilePublicReports')
            and row.get('percent') == 100]
    checks = [i for i, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    if (selection.get('status') != 'verified' or selection.get('configuration_bytes_current') is not True
            or compilation.get('completed') is not True or compilation.get('diagnostics_complete') is not True
            or compilation.get('acceptance') not in ('accepted', 'rejected')
            or len(builds) != 1 or len(ends) != 1 or not builds[0] < ends[0]
            or (checks and ends[0] >= min(checks))):
        return {**unresolved, 'reason': 'current source selection or complete original compilation is unavailable'}
    stages = []
    for stage in ('before-build', 'after-build'):
        selections = [row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == stage]
        reads = [(i, row) for i, row in enumerate(events) if row.get('operation') == 'NativeBodyRead' and row.get('stage') == stage]
        binding = bind_native_source_chain(programs, list(programs), project_id=project_id, native_version=native_version,
            selections=selections, reads=[row for _, row in reads], snapshots=snapshots)
        stages.append({'stage': stage, **binding})
        if (binding['status'] != 'current' or len(reads) != len(programs) or len(selections) != len(programs)
                or any(sum(row.get('name') == pou for row in selections) != 1 for pou in programs)
                or any(sum(_id(row.get('body')) == _id(source.get('body_id')) for _, row in reads) != 1
                       for source in binding['sources'])
                or any(not (index < builds[0] if stage == 'before-build' else
                            ends[0] < index < (min(checks) if checks else len(events))) for index, _ in reads)):
            return {**unresolved, 'stages': stages, 'reason': 'original compile-phase source reads are missing, stale or out of order'}
    before = {row['pou']: row['body_id'] for row in stages[0]['sources']}
    after = {row['pou']: row['body_id'] for row in stages[1]['sources']}
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


def project_native_validation(raw, observation, snapshots):
    """Interpret a standalone collector without trusting its match/status flags.

    Snapshots are independent bytes read by the transport before its isolated
    directory is removed. Missing copies retain incomplete or unresolved facts.
    """
    from .models import GXWFormatError
    context, sources = _native_validation_sources(raw)
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
    source_bodies = bind_native_source_chain(programs, list(programs), project_id=project,
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
            task_binding &= all(facts(entry, 12) and name in programs for entry, name in zip(entries, names))
            tasks.append({'resource': resource.get('read_name', {}).get('name'),
                          'task': task_facts.get('read_name', {}).get('name'), 'programs': names})
            selected.extend(name for name in names if isinstance(name, str))
    task_binding &= bool(resources) and len(resources) == len(set(resources))
    selected = list(dict.fromkeys(selected))
    configuration_current = bool(imported['streams']) and all(row['status'] == 'current'
        for row in imported['streams'] if row['logical_name'].endswith(('.prj', '.tsk', '.lnl')))
    source_current = imported['status'] == 'current' and source_bodies['status'] == 'current'
    selection = {'status': 'verified' if task_binding and selected and source_current else 'unresolved',
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
    for row in publication_rows:
        target, resource = _id(row.get('target')), row.get('resource') or {}
        if (target is None or not facts(resource, 8) or _id(resource.get('id')) != target
                or sum(_id(other.get('target')) == target for other in publication_rows) != 1):
            continue
        name = resource['read_name']['name']
        buffers = channels(row.get('channels'), 'published_size', 'published_snapshot')
        if buffers is not None:
            published[target] = buffers
        if name in generated_names:
            generated[target] = generated_names[name]
    result = analyze_native_check(events, [], generated, selection=selection,
        published=published, owned_snapshots=snapshots)
    compile_context = bind_native_compile_source_context(programs, project_id=project,
        native_version=observation['native_version'], events=events, snapshots=snapshots,
        selection=selection, compilation=result['compilation'])
    result['compilation']['source_context'] = compile_context
    reference_binding = bind_native_source_references(events, compilation=result['compilation'])
    references = reference_binding['rows'] if reference_binding['status'] == 'current' else None
    raw_reports, target = [], None
    for row in events:
        if row.get('operation') == 'ProgramCheckReportContext':
            target = row.get('target')
        elif row.get('operation') == 'ProgramCheckRawReports':
            raw_reports.extend((target, row.get('poll'), index, report)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
    diagnostics = []
    locations = [row for row in events if row.get('operation') == 'NativeDiagnosticLocation']
    for target, poll, index, report in raw_reports:
        matches = [row for row in locations if row.get('original') == {
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
                    if body['pou'] == projection['source']['pou']), {})
                projection['current_source_body'] = current.get('status', 'unresolved')
                if current.get('status') != 'current':
                    projection.update(status='unresolved', reason='original source body is not current')
        diagnostics.append({'target': target, 'report_origin': {'target': target, 'poll': poll, 'report_index': index},
                            'original': report, 'source_projection': projection})
    builds = [index for index, row in enumerate(events) if row.get('operation') == 'Compiler.Build'
              and row.get('hresult') == row.get('code') == 0]
    compile_ends = [index for index, row in enumerate(events) if row.get('operation') == 'Progress'
                    and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    check_starts = [index for index, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    check_ends = [index for index, row in enumerate(events) if row.get('operation') == 'ProgramCheckRawProgress'
                  and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    boundaries = bool(len(builds) == 1 and compile_ends and check_starts and check_ends
                      and builds[0] < max(compile_ends) < min(check_starts) <= max(check_ends))
    ordered_reads = boundaries and all(
        any(_id(read.get('body')) == _id(body.get('body_id')) and read.get('operation') == 'NativeBodyRead'
            and read.get('stage') == stage and (
                index < builds[0] if stage == 'before-build' else
                max(compile_ends) < index < min(check_starts) if stage == 'after-build' else
                index > max(check_ends)) for index, read in enumerate(events))
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    phase_reads_complete = source_bodies['status'] == 'current' and ordered_reads and all(
        bind_native_source_snapshot(programs[body['pou']], body_id=body['body_id'],
            reads=[read for read in events if read.get('stage') == stage], snapshots=snapshots)['status'] == 'current'
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    current_check = (selection['status'] == 'verified' and configuration_current and phase_reads_complete
        and result['compilation']['acceptance'] == 'accepted'
        and result['published_resources']['correspondence'] == 'current'
        and result['owned_check_inputs']['correspondence'] == 'current'
        and result['raw_backend_check']['completed'])
    lexical_reads = bind_native_code_lexical_read(events, generated_names, snapshots,
                                                cpu=context['cpu'], codepage=context['codepage'])
    lexical_by_resource = {row['resource']: row for row in lexical_reads if row['status'] == 'current'}
    texts, graphs, graph_models, graph_connectivity = {}, {}, {}, {}
    if (current_check and references is not None) or compile_context['status'] == 'current':
        from .text_pou import parse_st_pou
        from .object_model import read_project_context
        from .connectivity import build_connectivity_graph
        for source in sources:
            try:
                if source['program_kind'] == 193:
                    texts[source['name']] = parse_st_pou(source['raw']).text
                elif source['program_kind'] == 208:
                    graph_context = read_project_context(raw, source['logical_name'])
                    graph_models[source['name']] = graph_context.object_model()
                    graphs[source['name']] = graph_models[source['name']]['nodes']
                    graph_connectivity[source['name']] = build_connectivity_graph(graph_context.program)
            except GXWFormatError:
                continue
    source_map = {source['name']: source for source in sources}
    compile_diagnostics = []
    for row in events:
        if row.get('operation') not in ('CompileRawReports', 'CompilePublicReports'):
            continue
        for index, report in enumerate(row.get('reports', [])):
            if report.get('kind') not in (2, 3):
                continue
            pou = report.get('name', {}).get('text')
            source = source_map.get(pou, {})
            projection = {'status': 'unresolved', 'public_check_promoted': False,
                          'reason': 'current compile-phase source context is unavailable'}
            if compile_context['status'] == 'current':
                body = next((value for value in compile_context['stages'][-1]['sources'] if value['pou'] == pou), {})
                projection = correlate_native_compile_position(report, cpu=context['cpu'], pou=pou,
                    program_kind=source.get('program_kind'), text=texts.get(pou), model=graph_models.get(pou),
                    connectivity=graph_connectivity.get(pou), source_body_id=body.get('body_id'),
                    logical_name=source.get('logical_name'))
                if projection['status'] == 'source-resolved':
                    projection['source']['body_id'] = body['body_id']
            graph = projection.pop('graph_projection', {'status': 'not_applicable', 'public_check_promoted': False})
            compile_diagnostics.append({'report_origin': {'phase': 'compile', 'poll': row.get('poll'), 'report_index': index},
                'original': report, 'source_projection': projection, 'graph_projection': graph})
    result['compilation']['source_diagnostics'] = compile_diagnostics
    for diagnostic in diagnostics:
        projection = diagnostic['source_projection']
        graph = {'status': 'unresolved', 'public_check_promoted': False,
                 'reason': 'current original code, references and source chain are required'}
        if not current_check and 'instance_projection' in projection:
            projection['instance_projection'].update(status='unresolved', reason='actual check code is not bound to this compilation')
        if current_check and references is not None and projection.get('status') == 'source-resolved':
            location = projection['source']
            if location['program_kind'] == 193:
                matching = [row for row in locations if row.get('original') == {
                    'poll': diagnostic['report_origin']['poll'], 'report_index': diagnostic['report_origin']['report_index'],
                    'kind': diagnostic['original']['kind'], 'code': diagnostic['original']['code'],
                    'resource': projection['resource'], 'step': projection['diagnostic_step']}]
                if len(matching) == 1:
                    query = matching[0]['source_location']
                    query = {**query, 'query': {'resource': projection['resource'], 'start_step': projection['diagnostic_step'],
                             'original_kind': diagnostic['original']['kind'], 'original_code': diagnostic['original']['code']}}
                    candidates = [correlate_st_diagnostic(query, [], references, texts, nodes, caller_pou=pou,
                        native_ranges=query.get('instance_ranges'),
                        caller_block_count=len(graph_models[pou].get('blocks', [None])))
                        for pou, nodes in graphs.items()
                        if len(graph_models[pou].get('blocks', [None])) == 1 or context['cpu'] == 'Q03UDV']
                    resolved = [row for row in candidates if row['status'] == 'uniquely_correlated']
                    if len(resolved) == 1:
                        graph = resolved[0]
                        needed = [location['pou'], graph['caller']['pou']]
                        needed.extend(row['native_reference']['source'] for row in graph.get('call_chain', []))
                        chain = bind_native_source_chain(programs, needed, project_id=project,
                            native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                        graph['source_chain'] = chain
                        if chain['status'] != 'current':
                            graph.update(status='unresolved', reason='an original caller or callee body is not current')
                    else:
                        graph['reason'] = 'original ST instance does not select one current FBD caller'
            elif location['program_kind'] == 208:
                instance = projection.get('instance_projection') or {}
                lexical = lexical_by_resource.get(projection['resource'])
                matching = [row for row in locations if row.get('original') == {
                    'poll': diagnostic['report_origin']['poll'], 'report_index': diagnostic['report_origin']['report_index'],
                    'kind': diagnostic['original']['kind'], 'code': diagnostic['original']['code'],
                    'resource': projection['resource'], 'step': projection['diagnostic_step']}]
                pou = location['pou']
                if (lexical is not None and len(matching) == 1 and pou in graph_models
                        and instance.get('status') == 'instance-resolved'):
                    interval = instance['compiled_interval']
                    records = [row for row in lexical['records']
                               if interval['step_start'] <= row['native_step'] < interval['step_start'] + interval['step_count']]
                    graph = correlate_fbd_bool_output(matching[0]['source_location'], diagnostic['original'],
                        records, references, graph_models[pou], selected_pou=pou,
                        connectivity=graph_connectivity[pou], instance=(instance['task'], instance['instance']))
                    graph['source_chain'] = bind_native_source_chain(programs, [pou], project_id=project,
                        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                    if graph['source_chain']['status'] != 'current':
                        graph.update(status='unresolved', reason='original FBD source body is not current')
                else:
                    graph['reason'] = 'current native lexical code, instance interval and FBD context are required'
        diagnostic['graph_projection'] = graph
    result.update(native_version=observation['native_version'], cpu=context['cpu'], codepage=context['codepage'],
        adapter={'status': observation['status'], 'message': observation.get('message') or ''},
        source_bodies={**source_bodies, 'phase_reads_complete': phase_reads_complete},
        raw_diagnostics=diagnostics,
        source_references=reference_binding,
        native_code_lexical_reads=lexical_reads,
        native_instance_locations={'status': 'completed' if current_check and references is not None and all(
            row['source_projection'].get('instance_projection', {}).get('status') == 'instance-resolved' for row in diagnostics)
            else 'not_observed' if reference_binding['status'] == 'not_observed' else 'partial_or_unresolved',
            'public_check_promoted': False},
        native_source_locations={'status': 'completed' if result['raw_backend_check']['completed']
            and all(row['source_projection']['status'] == 'source-resolved' for row in diagnostics)
            else 'not_requested' if result['raw_backend_check']['status'] == 'not_observed' else 'partial_or_unresolved',
            'node_port_resolution': 'correlated' if any(row['source_projection'].get('source', {}).get('program_kind') == 208
                for row in diagnostics) and all(row['graph_projection']['status'] == 'uniquely_correlated'
                    for row in diagnostics if row['source_projection'].get('source', {}).get('program_kind') == 208)
                else 'partial_or_unresolved', 'public_check_promoted': False},
        current_raw_source_check='established' if current_check else 'not_established',
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
    reads, queries = [], []
    for row in trace:
        if row.get('event') != 'workspace-pcode-read' or row.get('phase') != 'check':
            continue
        buffers = row.get('buffers', [])
        query = (row.get('hresult') == row.get('code') == 0 and row.get('requested_sizes') == [0, 0, 0]
                 and any(b.get('size') and b.get('raw_hex') is None for b in buffers))
        if query:
            queries.append(row['serial'])
            continue
        actual = []
        for buffer in buffers:
            raw = buffer.get('raw_hex')
            # A successful zero-length channel has no bytes even with NULL.
            if raw is None and buffer.get('size') == 0 and row.get('hresult') == row.get('code') == 0:
                raw = ''
            actual.append(bytes.fromhex(raw) if raw is not None else None)
        expected = generated.get(_id(row.get('target')))
        if (row.get('hresult') != 0 or row.get('code') != 0 or len(actual) != 3 or any(b is None for b in actual)
                or any(len(data) != buffer.get('size') for data, buffer in zip(actual, buffers) if data is not None)):
            status = 'unreadable'
        elif expected is None:
            status = 'unbound_resource'
        else:
            status = 'current' if tuple(actual) == tuple(expected) else 'stale'
        reads.append({'serial': row['serial'], 'check_target': row.get('check_target'),
                      'resource': row.get('target'), 'channel_sizes': [b.get('size') for b in buffers], 'status': status})
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
    source_binding = ('conflicted' if source_audits and any(a['status'] == 'source-conflict' for a in source_audits)
        else 'consistent' if source_audits and len(source_audits) == len(public_diagnostics)
            and all(a['status'] == 'source-consistent' for a in source_audits)
        else 'partial_or_unresolved' if source_audits else 'not_observed')
    requested = any(row.get('operation') == 'Workspace.GetProgramCheckCollection' for row in events) or bool(started)
    if not requested:
        public_status = 'not_requested'
    if not reads:
        correspondence = 'not_observed'
    elif observation_errors or any(row['status'] not in ('current', 'stale') for row in reads):
        correspondence = 'not_established'
    elif any(row['status'] == 'stale' for row in reads):
        correspondence = 'stale'
    elif not planned or any(not any(_id(row['check_target']) == target for row in reads) for target in planned):
        correspondence = 'partial_targets'
    else:
        correspondence = 'current'
    compile_completed = any(row.get('operation') == 'Progress' and row.get('percent') == 100
        and row.get('hresult', 0) == row.get('code', 0) == 0 for row in events)
    compile_diagnostics = [report for row in events if row.get('operation') in ('Progress', 'CompileRawReports', 'CompilePublicReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    compile_failures = [row for row in events if row.get('operation') in ('Compiler.Build', 'Progress.GetProgress', 'Compiler.BuildProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0)]
    compile_started = any(row.get('operation') == 'Compiler.Build'
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
    raw_sessions, raw_session, pending_target, check_started = [], None, None, False
    native_names = {}
    for row in events:
        operation = row.get('operation')
        if operation == 'NativeObject' and row.get('name_hresult') == row.get('name_code') == 0:
            native_names[_id(row.get('id'))] = row.get('name')
        elif operation == 'ProgramCheckTarget':
            pending_target, check_started = _id(row.get('id')), False
        elif operation == 'Compiler.ProgramCheck':
            check_started = row.get('hresult') == row.get('code') == 0
        elif operation == 'ProgramCheckReportContext':
            raw_session = None
            if row.get('path') == 'owned-native-backend':
                identity = _id(row.get('target'))
                raw_session = {'target': identity, 'name': native_names.get(identity),
                    'started': identity is not None and identity == pending_target and check_started,
                    'polls': {}, 'report_polls': set(), 'reports': [], 'valid': True,
                    'poll_failed': False, 'reported_status': None, 'owned_inputs': []}
                raw_sessions.append(raw_session)
        elif raw_session is not None:
            if operation == 'OwnedCheckInput':
                raw_session['owned_inputs'].append(row)
            elif operation == 'ProgramCheckRawProgress':
                raw_session['polls'][row.get('poll')] = row
                raw_session['valid'] &= row.get('hresult') == row.get('code') == 0
                raw_session['poll_failed'] |= row.get('hresult') != 0 or row.get('code') != 0
            elif operation == 'ProgramCheckRawReports':
                poll = raw_session['polls'].get(row.get('poll'), {})
                reports = row.get('reports', [])
                raw_session['valid'] &= (bool(poll) and poll.get('count') == len(reports)
                    and poll.get('percent') == row.get('percent'))
                raw_session['report_polls'].add(row.get('poll'))
                raw_session['reports'].extend(reports)
            elif operation == 'ProgramCheckTargetOutcome' and 'backend_check' in row:
                raw_session['reported_status'] = row['backend_check']
                raw_session = None
    raw_outcomes = []
    for session in raw_sessions:
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
        observed_counts = [sum(row.get('kind') == kind and row.get('name', {}).get('text') == session['name']
            for row in session['reports']) for kind in (2, 3)]
        diagnostics_complete = expected_counts is not None and expected_counts == observed_counts
        completed = (session['started'] and session['valid'] and terminal and end_marker and diagnostics_complete
            and session['reported_status'] in ('completed-accepted', 'completed-rejected')
            and all(poll in session['report_polls'] or row.get('count') == 0
                for poll, row in session['polls'].items()))
        status = ('completed-rejected' if any(row.get('kind') == 2 for row in session['reports'])
            else 'completed-accepted') if completed else 'failed' if session['poll_failed'] else 'incomplete'
        raw_outcomes.append({'target': list(session['target']) if session['target'] else None,
            'status': status, 'reported_status': session['reported_status'],
            'terminal_progress': terminal, 'resource_end_marker': end_marker,
            'diagnostics_complete': diagnostics_complete, 'reported_diagnostic_counts': expected_counts,
            'observed_diagnostic_counts': observed_counts,
            'completed_resource': session['name'] if end_marker else None})
    raw_completed = (bool(planned) and len(raw_outcomes) == len(planned) and not skipped_targets
        and {tuple(row['target']) if row['target'] else None for row in raw_outcomes} == set(planned)
        and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in raw_outcomes)
        and bool(drained_completion) and drained_completion[-1].get('targets') == len(planned))
    raw_status = ('completed-rejected' if any(row['status'] == 'completed-rejected' for row in raw_outcomes)
        else 'completed-accepted') if raw_completed else 'incomplete' if raw_sessions or skipped_targets else 'not_observed'
    owned_inputs = []
    versions = {(row.get('name'), row.get('version')) for row in events if row.get('operation') == 'OwnedBackendModule'}
    required = {'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll', 'DZDataABS_SICConverter_IEC.dll'}
    owner_version = '1.635.0.1' if all((name, '1.635.0.1') in versions
        and all(version == '1.635.0.1' for module, version in versions if module == name) for name in required) else None
    ec_version = '15.50' if {version for name, version in versions if name == 'ECCompiler_IEC.dll'} == {'15.50'} else None
    for session in raw_sessions:
        copies = session['owned_inputs']
        if not copies:
            continue
        submitted = [row for row in copies if row.get('stage') == 'submitted']
        completed = [row for row in copies if row.get('stage') == 'completed']
        code = generated.get(session['target'])
        proof = bind_native_check_input(submitted[0] if len(submitted) == 1 else None,
            completed[0] if len(completed) == 1 else None,
            generated={session['name']: code[0]} if session['name'] and code is not None else {},
            snapshots=owned_snapshots or {}, native_version=owner_version, checker_version=ec_version)
        if not session['started'] or any(_id(row.get('target')) != session['target'] for row in copies):
            proof = {'status': 'unresolved', 'reason': 'owned input is not bound to this original check start',
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
    current_source_check = ('established' if selection_current and compile_acceptance == 'accepted'
        and correspondence == 'current' and whole_completed else 'not_established')
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
            'public_diagnostics': public_diagnostics, 'backend_diagnostics': backend_diagnostics,
            'empty_public_list_means_no_errors': whole_completed and not public_diagnostics and not backend_diagnostics},
        'save': {'completed': any(row.get('operation') == 'SaveProject' and row.get('hresult') == row.get('code') == 0
            for row in events), 'exported': any(row.get('operation') == 'NativeExport' for row in events)},
        'reopen': reopened or {'status': 'not_observed'},
        'current_source_check': current_source_check,
    }


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

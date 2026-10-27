# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bind native observations to exact source, input and instance identity."""
from __future__ import annotations
from .diagnostic_events import EventIndex
from gxworks2.workspace_observations import EvidenceOrigin, evidence_origin_bound
from gxworks2.workspace_observations import (native_code_reader_scope, check_input_kind,
    check_input_layout_bound, check_input_framing_bound, workspace_code_read_origin,
    source_reference_modules_bound, structured_input_reader_bound, public_report_converter_bound,
    source_named_check_diagnostics, check_input_versions, workspace_read_scope_verified)
from gxw.diagnostic_sources import (
    _native_validation_source_body,
)
from gxw.diagnostic_scope import (
    _id,
)


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
    events = EventIndex.of(events)
    reads = events.grouped('NativeCodeLexicalRead', key=lambda row: row.get('resource'))
    modules = [row for row in events.rows('OwnedBackendModule')
               if scope is not None and row.get('name') == scope['module']]
    modules_current = bool(modules) and all(row.get('version') == scope['version'] for row in modules)
    check_end = max((i for i, row in events.numbered('ProgramCheckRawProgress', 'ProgramCheckOriginalProgress')
                    if row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0), default=-1)
    results = []
    for resource, buffers in generated.items():
        body = buffers[0]
        base = {'resource': resource, 'status': 'unresolved', 'records': [], 'public_check_promoted': False}
        if not body:
            continue
        matching = reads.get(resource, ())
        if scope is None or not matching:
            results.append({**base, 'status': 'not_observed', 'reason': 'no observed original lexical reader for this CPU/resource'})
            continue
        if len(matching) != 1 or not modules_current:
            results.append({**base, 'reason': 'original lexical observation or converter version is missing or conflicting'})
            continue
        event_index, row = matching[0]
        try:
            calls = row.get('calls')
            if (any(row.get(key) != value for key, value in scope.items())
                    or row.get('completed') is not True or check_end < 0 or event_index <= check_end
                    or not evidence_origin_bound(row, EvidenceOrigin.CODE_LEXICAL_READ)
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
                            'scope': scope,
                            **({'provenance': row['provenance']} if 'provenance' in row else {})})
        except (GXWFormatError, UnicodeError, LookupError) as error:
            results.append({**base, 'reason': str(error)})
    return results


def bind_native_check_input(submitted, completed, *, generated, snapshots,
                            native_version, checker_version):
    """Compare the original task's retained primary code to this compilation.

    The adapter binds the original checker interface and envelope reader.
    Compare both owned snapshots and their native framing; publication, task
    completion, diagnostics and source positions remain separate evidence.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if not isinstance(submitted, dict) or not isinstance(completed, dict):
        return {**unresolved, 'reason': 'original submission and completion copies are required'}
    kind = check_input_kind(submitted, native_version=native_version, checker_version=checker_version)
    sfc = kind == 'sfc'
    if kind is None:
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
                or not evidence_origin_bound(event, EvidenceOrigin.OWNED_CHECK_INPUT)
                or not check_input_layout_bound(event, sfc=sfc)
                or not isinstance(rows, list) or not 1 <= len(rows) <= 1024):
            return {**unresolved, 'reason': 'original owned input table is not bound'}
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
            reader_bound = check_input_framing_bound(framing, sfc=sfc)
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
    reads = EventIndex.of(reads)
    matching = [row for row in reads.rows('NativeBodyRead') if _id(row.get('body')) == identity]
    if not matching:
        return {**missing, 'reason': 'original native source body read is missing'}
    latest = matching[-1]
    filename, size = latest.get('file'), latest.get('bytes')
    if (not evidence_origin_bound(latest, EvidenceOrigin.SOURCE_BODY)
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

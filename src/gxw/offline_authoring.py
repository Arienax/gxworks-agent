"""Existing ST bodies and declaration tables in one offline GXW transaction.

The existing authoring planners own names, types, classes and instance policy.
This layer retains original declaration storage, binds checks to the complete
draft, and prepares existing logical streams for the shared project writer.
It never creates engineering objects or invokes a native adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import copy, deepcopy
from dataclasses import replace
import re

from .callable_sources import ProjectCallableSources
from .declarations import BASIC_TYPES, CLASS_CODES, _type_parts, edit_declarations, parse_declarations
from .fb_authoring import _name, _rows, _st_implementation, normalize_declaration_address_pair
from .models import COIL_NODE_KINDS, CONTACT_NODE_KINDS, GXWFormatError, NodeKind
from .project_writer import (PreparedProjectWrite, ProjectWriteResult, binary_diff, prepare_project_write,
                             prepare_st_project_write, write_prepared_project)
from .source_header import source_payload_offset
from .text_pou import parse_st_pou


ST_SOURCE_OPERATIONS = frozenset({'edit_st_program', 'edit_st_fb', 'edit_st_function', 'edit_st_source'})
EXISTING_DEFINITION_OPERATIONS = ST_SOURCE_OPERATIONS | {'edit_global_labels', 'edit_pou_labels'}


def _label_context(baseline):
    """Read registered project/library owners with the paired source's kind."""
    from .offline_project import read_offline_project
    from .project_metadata import existing_source_groups
    project = read_offline_project(baseline)
    groups, issues = existing_source_groups(project.outer['projectdatalist.xml'], project.streams)
    groups = {name: {**group, 'source_kind': project.streams[name][source_payload_offset(project.streams[name])]}
              for name, group in groups.items() if name in project.streams}
    from .callable_sources import read_project_declarations
    documents, _ = read_project_declarations(project.streams, source_groups=groups)
    callers = [name for name in groups if name in project.streams]
    if not callers:
        raise GXWFormatError('existing label editing requires a registered source owner')
    caller = next((name for name in callers if project.streams[name][source_payload_offset(project.streams[name])] in (193, 208)), callers[0])
    sources = ProjectCallableSources.from_project(baseline, caller, documents)
    # Keep independently readable tables and registered source/library owners;
    # unreadable non-target tables remain explicit resolver issues.
    sources.source_groups = groups
    sources.issues = [issue for issue in sources.issues if not (
        issue.get('code') == 'project_source_owner_gap' and issue.get('stream') in groups)] + issues
    sources = sources.with_declarations(documents)
    return project.context, project.streams, groups, sources, caller


def _token_instruction_findings(regions, *, cpu, profile, text_encoding):
    """Use the same complete-circuit rules for Q Programs and SFC children."""
    from plc.target_identity import target_family
    if target_family(cpu) != 'Q':
        return []
    from plc.ordered_source import ordered_circuit_rows, validate_ordered_rung
    from .token_listing import decode_token_program, TokenInstruction, TokenLabel, TokenGap
    findings = []
    for child, region in regions:
        listing = decode_token_program(region, profile=profile, text_encoding=text_encoding)
        records = []
        for index, record in enumerate(listing.records):
            origin = dict(record_index=index, source_offset=record.tokens[0].offset)
            if isinstance(record, TokenInstruction):
                records.append(dict(op=record.mnemonic, args=list(record.args), **origin))
            elif isinstance(record, (TokenLabel, TokenGap)):
                records.append(dict(op='', args=[], kind='label' if isinstance(record, TokenLabel) else 'opaque',
                    raw_hex=b''.join(token.raw for token in record.tokens).hex(), **origin))
        for _note, rows, _leading in ordered_circuit_rows([dict(rung_id=0, ordered_instructions=records)]):
            circuit = [row['_record'] for row in rows]
            try:
                validate_ordered_rung(dict(rung_id=0, ordered_instructions=circuit),
                    plc_model=cpu, require_catalogued=False)
            except ValueError as error:
                position = re.search(r'ordered_instructions\[(\d+)\]', str(error))
                record = circuit[int(position[1])] if position else circuit[-1]
                findings.append(dict(code='TOKEN_INSTRUCTION', severity='error', message=str(error),
                    source_offset=record['source_offset'], opcode=record.get('op'), operands=record.get('args'),
                    **({'subprogram': {key: child[key] for key in ('kind', 'name', 'number')}} if child else {})))
    return findings


def _token_error_key(finding):
    child = finding.get('subprogram') or {}
    return (finding['code'], child.get('kind'), child.get('name'),
            re.sub(r'ordered_instructions\[\d+\]', 'ordered_instructions[*]', finding['message']))


def _token_checks(streams, groups, sources, documents, *, text_encoding, bind_sources=()):
    """Retain explicit token-label binding; device-only source stays unverified."""
    from .sfc_pou import parse_sfc_pou
    from .token_pou import frame_token_pou, parse_token_fragment
    from .token_profiles import source_token_profile
    from .token_listing import decode_token_program
    from plc.instruction_constraints import fx_out_preset_error
    from plc.target_identity import target_family
    profile = source_token_profile(sources.cpu)
    q_source = target_family(sources.cpu) == 'Q'
    source_label_tokens = profile in ('q02', 'q03udv', 'q', 'l')
    checks = []
    for logical in groups:
        raw = streams[logical]
        kind = raw[source_payload_offset(raw)]
        if kind not in (1, 240):
            continue
        current = sources.with_declarations(sources.declarations)
        current.program_name = logical
        changed = current.with_declarations(documents)
        findings, references, invalid_labels, previous_errors = [], set(), [], []
        try:
            if kind == 240:
                source = parse_sfc_pou(raw)
                findings.extend(dict(code='SFC_SOURCE_OBJECT_COVERAGE', severity='unverified', message=message)
                                for message in source.diagnostics)
                regions = [(child, parse_token_fragment(raw, child['token_offset'], child['token_size']))
                           for child in source.layout['children']]
            else:
                regions = [(None, frame_token_pou(raw))]
            if q_source:
                findings.extend(_token_instruction_findings(regions, cpu=sources.cpu,
                    profile=profile, text_encoding=text_encoding))
                previous = getattr(sources, 'source_streams', {}).get(logical)
                if previous is not None:
                    old_source = parse_sfc_pou(previous) if kind == 240 else None
                    old_regions = ([(child, parse_token_fragment(previous, child['token_offset'], child['token_size']))
                                    for child in old_source.layout['children']] if old_source else
                                   [(None, frame_token_pou(previous))])
                    previous_errors = _token_instruction_findings(old_regions, cpu=sources.cpu,
                        profile=profile, text_encoding=text_encoding)
            tokens = [token for _, region in regions for token in region.tokens]
            if profile.startswith('fx'):
                for child, region in regions:
                    listing = decode_token_program(region, profile=profile, text_encoding=text_encoding)
                    for record in listing.instructions:
                        error = fx_out_preset_error(record.mnemonic, record.args)
                        if error:
                            findings.append(dict(code='TOKEN_REQUIRED_PRESET', severity='error', message=error,
                                opcode=record.mnemonic, operands=list(record.args),
                                source_offset=record.tokens[0].offset,
                                **({'subprogram': {key: child[key] for key in ('kind', 'name', 'number')}} if child else {})))
            for token in tokens:
                if token.raw[1] != 0xDF or not source_label_tokens:
                    continue
                # The shared Q lexical reader identifies DF as a source label;
                # a quoted EE string and an instruction's text are not labels.
                try:
                    text = token.raw[2:-1].decode(text_encoding)
                except (UnicodeError, LookupError, TypeError):
                    findings.append(dict(code='TOKEN_LABEL_ENCODING', severity='unverified',
                                         message='source label bytes cannot be decoded with the project codepage'))
                    continue
                match = re.match(r'[^\W\d]\w*', text, re.UNICODE)
                if match:
                    references.add(match[0])
                else:
                    invalid_labels.append(text)
        except GXWFormatError as error:
            findings.append(dict(code='TOKEN_SOURCE_COVERAGE', severity='unverified', message=str(error)))
        if invalid_labels and logical in bind_sources:
            raise GXWFormatError('token source label has no supported declaration identity: ' + invalid_labels[0])
        if logical in bind_sources and any(row['code'] == 'TOKEN_LABEL_ENCODING' for row in findings):
            raise GXWFormatError('token source label cannot be bound in the project codepage: ' + logical)
        for name in references:
            before, after = current.label(name), changed.label(name)
            if before is not None and (after is None or before[0] != after[0]):
                raise GXWFormatError('declaration edit leaves a token source reference without its original binding: ' + name)
            if logical in bind_sources and after is None:
                raise GXWFormatError('token source label has no current declaration binding: ' + name)
        findings.append(dict(code='TOKEN_DECLARATION_USE', severity='unverified',
            message='device token records do not establish complete declaration-use or execution semantics'))
        failed = any(finding['severity'] == 'error' for finding in findings)
        from collections import Counter
        retained = Counter(_token_error_key(finding) for finding in previous_errors)
        introduced = []
        for finding in findings:
            if finding['severity'] != 'error':
                continue
            key = _token_error_key(finding)
            if retained[key]:
                retained[key] -= 1
            else:
                introduced.append(finding)
        checks.append(dict(source=logical, language='sfc' if kind == 240 else 'ladder',
            status='failed' if failed else 'unverified',
            syntax='failed' if failed else 'unverified' if any(f['code'] == 'TOKEN_SOURCE_COVERAGE' for f in findings) else 'passed',
            semantic='failed' if failed else 'unverified', known_label_references=sorted(references), findings=findings,
            **({'introduced_errors': introduced} if q_source else {}),
            native_compile='not_run', execution='not_run'))
    return checks


def _pending_local_source(prepared, selected):
    from .project_metadata import mark_observed_source_compile_pending
    if selected is None or selected in prepared.replacements:
        return prepared
    stream = prepared.mapping[selected]
    original = prepared.nested[stream]
    updated = mark_observed_source_compile_pending(original, logical_name=selected)
    replacements = {**prepared.replacements, selected: (stream, original, updated)}
    objects = [*prepared.objects, dict(object=selected, stream=stream, old_length=len(original), new_length=len(updated),
        offset_space='logical source compile-status bytes', compile_state_only=True,
        binary_changes=binary_diff(original, updated))]
    return replace(prepared, replacements=replacements, objects=objects)


def _local_selection(source_name, groups, sources):
    if not isinstance(source_name, str) or not source_name:
        raise GXWFormatError("existing POU editing requires a current source identity")
    matches = [(logical, sources.declarations[group['labels']]) for logical, group in groups.items()
               if group['labels'] in sources.declarations and (logical == source_name or group['labels'] == source_name
                   or (sources.declarations[group['labels']].owner_name or '').casefold() == source_name.casefold())]
    if len(matches) != 1:
        raise GXWFormatError("missing or ambiguous existing POU source: " + source_name)
    logical, document = matches[0]
    current = copy(sources)
    current.program_name = logical
    if current.local_table != document.logical_name:
        raise GXWFormatError('existing source and declaration owner differ')
    if document.owner_pou_type not in (0x1000000, 0x1000002, 0x1000003):
        raise GXWFormatError("existing definition editing requires a Program, function block or FUNCTION")
    return logical, document


def _existing_st_rows(config, sources, document, rows):
    """Apply the complete-list contract using bound rows and explicit order."""
    if not isinstance(rows, list) or len(rows) > 8192:
        raise GXWFormatError('existing ST declarations require a complete bounded list')
    names = []
    for row in rows:
        if (not isinstance(row, dict) or not {'name', 'data_type'} <= row.keys()
                or set(row) - {'name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment',
                               'device', 'iec_address', 'member_devices', 'structure_array_addresses'}):
            raise GXWFormatError('invalid existing ST declaration fields')
        _name(row['name'], config['codepage'])
        names.append(row['name'])
    if len({name.casefold() for name in names}) != len(names):
        raise GXWFormatError('duplicate existing ST declaration')
    patch = dict(upserts=_preserving_upserts(rows, document, sources),
        remove=[row.name for row in document.rows if row.name.casefold() not in {name.casefold() for name in names}], order=names)
    edited = _edited_document(config, sources, document, patch)
    return _declaration_rows(edited, sources.with_declarations({**sources.declarations, document.logical_name: edited}))


def _global_selection(source_name, sources):
    if not isinstance(source_name, str) or not source_name:
        raise GXWFormatError("existing global editing requires a current declaration table")
    matches = [(name, document) for name, document in sources.declarations.items() if document.scope == 'global'
               and source_name.casefold() in (name.casefold(), name.removesuffix('.gh').casefold())]
    if len(matches) != 1:
        raise GXWFormatError("missing or ambiguous existing global declaration table: " + source_name)
    return matches[0]


def _patch_rows(document, *, renames=None, remove=()):
    renamed = {name.casefold(): value for name, value in (renames or {}).items()}
    removed = {name.casefold() for name in remove}
    return {renamed.get(row.name.casefold(), row.name).casefold(): row for row in document.rows
            if row.name.casefold() not in removed}


def _preserving_upserts(rows, document, sources, *, renames=None, remove=()):
    old = _patch_rows(document, renames=renames, remove=remove)
    result = []
    for supplied in rows:
        allowed = {
            'name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment', 'device', 'iec_address',
            'member_devices', 'structure_array_addresses'}
        if not isinstance(supplied, dict) or set(supplied) - allowed or not isinstance(supplied.get('name'), str):
            raise GXWFormatError('invalid existing declaration upsert fields')
        item = dict(supplied)
        previous = old.get(item['name'].casefold())
        # An unchanged declared kind must not select constructor defaults and
        # erase a native zero cache, type reference or saved array marker.
        if (previous is not None and item.get('kind') == sources.declaration_kind(previous)
                and (item.get('data_type', previous.data_type) == previous.data_type or item['kind'] == 'variable')):
            item.pop('kind')
        if (previous is not None and previous.class_code not in CLASS_CODES.values()
                and item.get('class_name') == str(previous.class_code)):
            item.pop('class_name')
        result.append(item)
    return result


def _removals(value, declarations):
    if not isinstance(value, (list, tuple)) or any(not isinstance(name, str) or not name for name in value):
        raise GXWFormatError("remove_declarations must contain current declaration names")
    folded = [name.casefold() for name in value]
    if len(set(folded)) != len(folded) or set(folded) & {row['name'].casefold() for row in declarations}:
        raise GXWFormatError("duplicate or conflicting declaration removal")
    return tuple(value)


def _save_policy(value):
    if value not in ('draft', 'checked'):
        raise GXWFormatError('save_policy must be draft or checked')
    return value


def _structure_layouts(sources, document, patch):
    """Resolve names only when an edited address record needs their order."""
    rows = _patch_rows(document, renames=patch.get('renames'), remove=patch.get('remove', ()))
    result = {}
    for item in patch.get('upserts', ()):
        if not isinstance(item, dict):
            continue  # The shared storage editor owns malformed input errors.
        previous = rows.get(str(item.get('name', '')).casefold())
        if 'member_devices' not in item and not (item.get('kind') == 'structure' and document.scope == 'global'):
            continue
        value = item.get('data_type', previous.data_type if previous else '')
        base, bounds = _type_parts(value)
        if bounds and 'member_devices' not in item:
            continue
        result[base.casefold()] = tuple(member.name for member in sources.structure(base).members)
    return result


def _normalized_address_edits(document, patch, *, cpu, sources):
    """Fill the existing scalar IEC rule; invalid text remains a diagnostic.

    Address validity is checked after the complete draft is available. Storage
    still validates string framing and structure-member identity immediately.
    """
    from .declarations import structure_address_fields
    patch = deepcopy(dict(patch))
    rows = _patch_rows(document, renames=patch.get('renames'), remove=patch.get('remove', ()))
    for item in patch.get('upserts', ()):
        if not isinstance(item, dict):
            continue
        old = rows.get(str(item.get('name', '')).casefold())
        if {'device', 'iec_address'} & item.keys():
            pair = {key: item.get(key, getattr(old, key) if old else '') for key in ('device', 'iec_address')}
            # A changed MELSEC address must not inherit its old IEC cache.
            if 'device' in item and old is not None and item['device'] != old.device and 'iec_address' not in item:
                pair['iec_address'] = ''
            try:
                pair = normalize_declaration_address_pair(pair, cpu=cpu)
            except ValueError:
                pass
            else:
                item.update(pair)
        member_devices = item.get('member_devices')
        if isinstance(member_devices, Mapping):
            old_pairs = {}
            if old is not None and old.unknown_u32 == 1:
                members = sources.structure(_type_parts(old.data_type)[0]).members
                old_pairs = {name.casefold(): pair for name, pair in structure_address_fields(old, members=members).items()}
            for name, value in member_devices.items():
                supplied = dict(device=value) if isinstance(value, str) else deepcopy(value)
                if not isinstance(supplied, Mapping) or not isinstance(name, str):
                    continue
                previous = old_pairs.get(name.casefold(), dict(device='', iec_address=''))
                pair = {**previous, **supplied}
                if 'device' in supplied and supplied['device'] != previous.get('device') and 'iec_address' not in supplied:
                    pair['iec_address'] = ''
                try:
                    pair = normalize_declaration_address_pair(pair, cpu=cpu)
                except ValueError:
                    pass
                member_devices[name] = pair
    return patch


def _edited_document(config, sources, document, patch):
    if not isinstance(patch, Mapping) or set(patch) - {'upserts', 'renames', 'remove', 'order', 'return_type'}:
        raise GXWFormatError('invalid existing declaration table patch')
    if (not isinstance(patch.get('upserts', ()), (list, tuple))
            or any(not isinstance(row, dict) for row in patch.get('upserts', ()))
            or patch.get('renames') is not None and not isinstance(patch['renames'], dict)
            or not isinstance(patch.get('remove', ()), (list, tuple))
            or any(not isinstance(name, str) for name in patch.get('remove', ()))
            or any(not isinstance(name, str) or not isinstance(value, str) for name, value in (patch.get('renames') or {}).items())):
        raise GXWFormatError('declaration upserts and renames require rows and name mappings')
    patch = dict(patch)
    if 'upserts' in patch:
        patch['upserts'] = _preserving_upserts(patch['upserts'], document, sources,
            renames=patch.get('renames'), remove=patch.get('remove', ()))
    patch = _normalized_address_edits(document, patch, cpu=config['cpu'], sources=sources)
    upserts = patch.get('upserts', ())
    if isinstance(upserts, (list, tuple)):
        names = [str(row.get('name', '')).casefold() for row in upserts if isinstance(row, dict)]
        if len(names) != len(set(names)):
            raise GXWFormatError('duplicate declaration upsert')
        _removals(patch.get('remove', ()), upserts)
    for row in upserts:
        if isinstance(row, dict) and isinstance(row.get('name'), str):
            _name(row['name'], config['codepage'])
    for name in (patch.get('renames') or {}).values():
        _name(name, config['codepage'])
    owner = next((name for name, group in sources.source_groups.items() if group['labels'] == document.logical_name), None)
    # Native-created local caches use the same observed Program/FB shape for ST
    # and FBD. Existing rows retain their original caches through the editor.
    native_locals = (document.owner_pou_type in (0x1000000, 0x1000002) and owner is not None
                     and sources.source_groups[owner].get('source_kind') in (193, 208))
    return edit_declarations(document, **patch, native_fbd_locals=native_locals,
                             structure_members=_structure_layouts(sources, document, patch))


def _declaration_rows(document, sources):
    from .declarations import structure_address_fields
    rows = {row.name.casefold(): row for row in document.rows}
    result = _rows(document, sources)
    for item in result:
        row = rows[item['name'].casefold()]
        if row.device or row.iec_address:
            item.update(device=row.device, iec_address=row.iec_address)
        if row.unknown_u32 == 1:
            try:
                members = sources.structure(_type_parts(row.data_type)[0]).members
                item['member_devices'] = structure_address_fields(row, members=members)
            except GXWFormatError:
                pass  # Preserve an independent unresolved type/address record.
        elif row.unknown_u32 == 2:
            item['structure_array_addresses'] = structure_address_fields(row)
    return result


def _definition_snapshot(document, sources, *, selected=None, text=None):
    kind = {0x1000000: 'program', 0x1000002: 'function_block', 0x1000003: 'function'}.get(document.owner_pou_type)
    result = dict(name=document.owner_name or document.logical_name.removesuffix('.gh'),
        kind=kind if selected else 'global_table', declarations=_declaration_rows(document, sources))
    if selected:
        result.update(program=selected, labels=document.logical_name)
    if text is not None:
        result.update(language='st', st_text=text)
    if document.owner_pou_type == 0x1000003:
        result['return_type'] = document.owner_return_type
    if document.owner_pou_type in (0x1000002, 0x1000003):
        try:
            result['interface'] = sources.st_interface(document.owner_name,
                'FUNCTION' if document.owner_pou_type == 0x1000003 else 'FUNCTION_BLOCK')
        except GXWFormatError as error:
            result['interface'] = dict(status='unverified', diagnostic=str(error))
    return result


def _binding_signature(binding):
    if binding is None:
        return None
    name, row = binding
    return name, row.name.casefold(), row.data_type, row.class_code


def _graph_state(program, sources):
    from .fbd_validation import inspect_graph
    result = inspect_graph(program, sources)
    return result['contracts'], result['findings']


def _finding_key(finding):
    return tuple((key, finding.get(key)) for key in ('code', 'message', 'node_offset', 'net', 'source_name'))


def _check_st_reference_bindings(text, before_sources, after_sources):
    """A deleted bound label must not become an unverified free identifier."""
    from plc.st_frontend import Node, Parser
    try:
        pending = list(Parser(text).body())
    except (ValueError, RecursionError):
        # The shared inspection below reports exact syntax/coverage failures.
        return []
    references = set()
    while pending:
        value = pending.pop()
        if isinstance(value, Node) and value[0] in ('atom', 'call'):
            match = re.match(r'[^\W\d]\w*', str(value[1]), re.UNICODE)
            if match:
                references.add(match[0])
        if isinstance(value, (tuple, list)):
            pending.extend(child for child in value if isinstance(child, (tuple, list)))
    findings = []
    for name in references:
        before, after = before_sources.label(name), after_sources.label(name)
        if before is not None and (after is None or before[0] != after[0]):
            raise GXWFormatError('declaration edit leaves an ST reference without its original binding: ' + name)
        if after is not None and after[1].class_code not in CLASS_CODES.values():
            findings.append(dict(code='ST_DECLARATION_CLASS_COVERAGE', severity='unverified',
                message='current declaration retains an unresolved native class: ' + name))
    return findings


def _check_graphs(baseline, streams, groups, sources, documents, programs):
    from .object_model import read_project_context, source_checks
    from .fbd_validation import inspect_graph, introduced_findings
    def current_findings(context, semantic):
        return [dict(row, severity=row.get('severity', 'unverified'),
                     message=row.get('message', row.get('code', 'unresolved source object')))
                for row in source_checks(context, semantic=semantic)['findings']]
    checks = []
    for logical in groups:
        if streams[logical][source_payload_offset(streams[logical])] != 208:
            continue
        context = read_project_context(baseline, logical)
        before_sources = context.sources
        after_sources = before_sources.with_declarations(documents)
        after_sources.source_streams = streams
        candidate = programs.get(logical, context.program)
        original_nodes = {node.offset: node for node in context.program.nodes}
        # Removal/type edits may not silently retarget existing graph references
        # to a same-named declaration in another scope.
        for node in candidate.nodes:
            if node.kind == NodeKind.FUNCTION:
                continue
            previous = original_nodes.get(node.offset)
            if previous is None or previous.symbol != node.symbol:
                continue
            for name in {row.name for table in sources.declarations.values() for row in table.rows}:
                if not re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', node.symbol, re.I):
                    continue
                old, new = before_sources.label(name), after_sources.label(name)
                if old is not None and (new is None or old[0] != new[0]):
                    raise GXWFormatError('declaration edit leaves a graph reference without its original binding: ' + name)
                if (_binding_signature(old) != _binding_signature(new) and logical not in programs):
                    raise GXWFormatError('declaration type/class change requires a joint graph update: ' + name)
        prior = inspect_graph(context.program, before_sources)
        current = inspect_graph(candidate, after_sources)
        prior_contracts, contracts = prior['contracts'], current['contracts']
        prior_findings = current_findings(context, prior)
        findings = current_findings(replace(context, program=candidate,
                                    declarations=documents, sources=after_sources), current)
        if logical not in programs and any(offset in prior_contracts and signature != prior_contracts[offset]
                                           for offset, signature in contracts.items()):
            raise GXWFormatError('function-block interface change requires a joint caller graph update: ' + logical)
        introduced = introduced_findings(prior_findings, findings)
        status = 'failed' if any(f['severity'] == 'error' for f in findings) else 'unverified' if findings else 'passed'
        checks.append(dict(source=logical, language='fbd', status=status, syntax='not_applicable', semantic=status,
                           findings=findings, introduced_errors=introduced,
                           native_compile='not_run', execution='not_run', execution_dependencies=current['execution']))
    return checks


def _st_dependencies(context, sources):
    """Bind AST call nodes to current source owners, never display text."""
    from plc.st_frontend import Node, Parser
    owners = {group['labels']: name for name, group in sources.source_groups.items()}
    result, parsed = {}, {}
    for pou in context['pous']:
        logical = pou['program']
        result[logical] = set()
        if pou['language'] != 'st' or not isinstance(pou.get('st_text'), str):
            continue
        text = pou['st_text']
        if text not in parsed:
            calls = set()
            try:
                pending = list(Parser(text).body())
            except (ValueError, RecursionError):
                pending = []  # The selected inspector retains exact errors.
            while pending:
                value = pending.pop()
                if isinstance(value, Node) and value[0] == 'call':
                    calls.add(str(value[1]))
                if isinstance(value, (tuple, list)):
                    pending.extend(child for child in value if isinstance(child, (tuple, list)))
            parsed[text] = calls
        current = copy(sources)
        current.program_name = logical
        for name in parsed[text]:
            try:
                binding = current.label(name)
                target = binding[1].data_type if binding is not None else name
                table, definition = current.definition(target, 'FUNCTION_BLOCK' if binding is not None else 'FUNCTION')
            except GXWFormatError:
                # Missing/ambiguous calls are diagnosed by the shared semantic
                # inspector; they cannot select another source body here.
                continue
            source = definition.get('source_program', owners.get(table))
            if source is not None:
                result[logical].add(source)
    return result


def _st_source_context(context, logical, dependencies):
    """Keep reachable bodies and all unique signatures in one shared view."""
    reachable = {logical}
    while True:
        expanded = reachable | {target for name in reachable for target in dependencies.get(name, ())}
        if expanded == reachable:
            break
        reachable = expanded
    counts = {}
    for pou in context['pous']:
        name = pou['name'].casefold()
        counts[name] = counts.get(name, 0) + 1
    pous = []
    for pou in context['pous']:
        if counts[pou['name'].casefold()] != 1 and pou['program'] != logical:
            # The callable resolver retains the namespace ambiguity. A flat ST
            # scope must not silently let the last same-named library win.
            continue
        if pou['language'] == 'st' and pou['program'] not in reachable:
            pou = dict(pou, language='interface_only', source_status='interface_only', st_text=None)
        pous.append(pou)
    return dict(context, selected_source=logical, pous=pous)


def _check_st(baseline, streams, sources, documents, selected, text, *, declaration_changed,
              source_replacements=None, save_policy='checked', affected_sources=None):
    from .st_program import _context_declaration_rows, project_context_snapshot
    from plc.st_validation import inspect_st_program
    context = project_context_snapshot(baseline, include_graphs=False)
    draft = deepcopy(context)
    for table in draft['globals']:
        document = documents.get(table['source'])
        if document is not None:
            table['declarations'] = _context_declaration_rows(document, sources.with_declarations(documents))
    for pou in draft['pous']:
        document = documents.get(pou['labels'])
        if document is not None:
            pou['declarations'] = _context_declaration_rows(document, sources.with_declarations(documents))
            if document.owner_pou_type == 0x1000003:
                pou['return_type'] = document.owner_return_type if document.owner_return_type not in (None, "@'VOID'") else ''
        if pou['program'] == selected and text is not None:
            pou['st_text'] = text
        elif pou['language'] == 'st' and pou['program'] in (source_replacements or {}):
            pou['st_text'] = parse_st_pou(source_replacements[pou['program']]).text
    after_sources = sources.with_declarations(documents)
    dependencies = _st_dependencies(draft, after_sources)
    old_dependencies = _st_dependencies(context, sources)
    changed_tables = {name for name, document in documents.items() if name in sources.declarations and (
        document.rows != sources.declarations[name].rows or document.header != sources.declarations[name].header)}
    targets = {pou['program'] for pou in draft['pous'] if pou['language'] == 'st' and (
        pou['labels'] in changed_tables or pou['st_text'] != parse_st_pou(streams[pou['program']]).text)}
    targets.update(name for name in (source_replacements or {}) if name in sources.source_groups)
    if selected:
        targets.add(selected)
    if any(documents[name].scope == 'global' for name in changed_tables):
        targets.update(pou['program'] for pou in draft['pous'] if pou['language'] == 'st')
    else:
        # An interface may also be owned by FBD or a token POU. Its ST callers
        # are found from actual AST call nodes and the current declarations.
        targets.update(name for name, group in sources.source_groups.items() if group['labels'] in changed_tables)
        while True:
            expanded = targets | {name for name in dependencies if
                (dependencies[name] | old_dependencies.get(name, set())) & targets}
            if expanded == targets:
                break
            targets = expanded
    if affected_sources is not None:
        targets.intersection_update(affected_sources)
    checks = []
    for pou in draft['pous']:
        if pou['language'] != 'st' or pou['program'] not in targets:
            continue
        logical = pou['program']
        current = _st_source_context(draft, logical, dependencies)
        before_sources = copy(sources) if declaration_changed else None
        if before_sources is not None:
            before_sources.program_name = logical
        callable_sources = copy(after_sources)
        callable_sources.program_name = logical
        binding_findings = []
        if before_sources is not None:
            try:
                binding_findings.extend(_check_st_reference_bindings(pou['st_text'], before_sources, callable_sources))
            except GXWFormatError as error:
                binding_findings.append(dict(code='ST_DECLARATION_BINDING', severity='error', message=str(error)))
        report = inspect_st_program(pou['st_text'], plc_model=draft['cpu'], project_context=current,
                                    callable_sources=callable_sources)
        try:
            from plc.st_frontend import Parser
            parsed = Parser(pou['st_text'])
            parsed.body()
        except (ValueError, RecursionError):
            pass  # The shared inspector already reports syntax/coverage.
        else:
            if len(parsed.parsed.units) != 1 or parsed.unit.name != '__BODY__' or parsed.unit.kind != 'program':
                binding_findings.append(dict(code='ST_BODY_REQUIRED', severity='error',
                    message='an existing ST source contains an implementation body, not embedded POU/type definitions'))
        if binding_findings:
            report['findings'].extend(binding_findings)
            status = 'failed' if any(row['severity'] == 'error' for row in report['findings']) else 'unverified'
            report.update(status=status, semantic=status, valid=False)
            report['counts'] = {key: sum(row['severity'] == key for row in report['findings'])
                                for key in ('error', 'warning', 'unverified')}
        errors = [finding for finding in report['findings'] if finding['severity'] == 'error']
        text_changed = pou['st_text'] != parse_st_pou(streams[logical]).text
        if errors and not text_changed:
            original = next(value for value in context['pous'] if value['program'] == logical)
            before = inspect_st_program(original['st_text'], plc_model=context['cpu'],
                project_context=_st_source_context(context, logical, old_dependencies),
                callable_sources=before_sources or ProjectCallableSources.from_project(baseline, logical))
            previous_errors = {_finding_key(finding) for finding in before['findings'] if finding['severity'] == 'error'}
            errors = [finding for finding in errors if _finding_key(finding) not in previous_errors]
        if errors and save_policy == 'checked':
            raise GXWFormatError('offline ST source failed syntax/semantic checks for ' + logical + ': '
                                 + '; '.join(finding['message'] for finding in errors))
        checks.append(dict(source=logical, language='st', **report, introduced_errors=errors, execution='not_run'))
    return checks


def _source_check_error(source, language, code, error, *, severity='error'):
    status = 'failed' if severity == 'error' else 'unverified'
    return dict(source=source, language=language, status=status, syntax='unverified', semantic=status,
        findings=[dict(code=code, severity=severity, message=str(error))], native_compile='not_run', execution='not_run')


def _draft_declaration_checks(before, documents, changed, *, cpu):
    from .declarations import structure_address_fields
    from plc.st_frontend import parse_initializer, STUncovered
    from plc.st_semantics import Symbols, Inspector
    from plc.target_support import normalize_target_cpu
    after = before.with_declarations(documents)
    checks = []
    for name in changed:
        document = documents[name]
        previous = {row.name.casefold(): row for row in before.declarations[name].rows}
        findings = []
        initializer_rows = []
        for row in document.rows:
            old = previous.get(row.name.casefold())
            if row.class_code not in CLASS_CODES.values():
                findings.append(dict(code='DECLARATION_CLASS_COVERAGE', severity='unverified', name=row.name,
                    message='current declaration retains an unresolved native class: ' + str(row.class_code)))
            if old is None or (old.data_type, old.type_code) != (row.data_type, row.type_code):
                try:
                    base, bounds = _type_parts(row.data_type)
                    if base.upper() not in BASIC_TYPES:
                        if row.type_code == 15:
                            after.definition(base, 'FUNCTION_BLOCK')
                        else:
                            before.structure(base)
                except GXWFormatError as error:
                    findings.append(dict(code='DECLARATION_TYPE_BINDING', severity='error', name=row.name, message=str(error)))
            if old is None or (old.device, old.iec_address, old.value_extension) != (row.device, row.iec_address, row.value_extension):
                try:
                    if row.unknown_u32 == 1:
                        pairs = structure_address_fields(row)
                    elif row.unknown_u32 == 2:
                        addresses = structure_address_fields(row)
                        from plc.device_policy import device_address
                        for field in ('word_address', 'bit_address'):
                            if addresses[field]:
                                device_address(addresses[field], cpu)
                        pairs = []
                    else:
                        pairs = [dict(device=row.device, iec_address=row.iec_address)]
                    for pair in pairs:
                        normalize_declaration_address_pair(pair, cpu=cpu)
                except ValueError as error:
                    findings.append(dict(code='DECLARATION_ADDRESS', severity='error', name=row.name, message=str(error)))
            if row.initial_value and (old is None or old.initial_value != row.initial_value or old.data_type != row.data_type):
                initializer_rows.append(row)
        if initializer_rows:
            owner = next((logical for logical, group in before.source_groups.items() if group['labels'] == name), None)
            current = copy(after) if document.scope == 'local' else None
            if current is not None:
                current.program_name = owner
            symbols = Symbols(project_context=dict(configuration=dict(compile_options=dict(
                global_variable_hiding=before.global_variable_hiding))), callable_sources=current)
            for table in documents.values():
                if table.scope == 'global' or table.logical_name == name:
                    for row in table.rows:
                        symbols.add(symbols.globals if table.scope == 'global' else symbols.locals,
                                    Symbols.row(dict(name=row.name, data_type=row.data_type,
                                        class_code=row.class_code, initial_value=row.initial_value, device=row.device)))
            for row in initializer_rows:
                inspector = Inspector(normalize_target_cpu(cpu), symbols=symbols)
                try:
                    pending = [_type_parts(row.data_type)[0]]
                    while pending:
                        base = pending.pop()
                        if base.upper() in BASIC_TYPES or base.upper() in symbols.types:
                            continue
                        try:
                            layout = after.structure(base, library=(before.source_groups.get(owner) or {}).get('library'))
                        except GXWFormatError:
                            continue  # Missing type binding already has its own finding.
                        symbols.types[base.upper()] = [Symbols.row(dict(name=member.name, data_type=member.data_type,
                            initial_value=member.initial_value)) for member in layout.members]
                        pending.extend(_type_parts(member.data_type)[0] for member in layout.members)
                    value = parse_initializer(row.initial_value, row.data_type)
                    inspector.assignment(('atom', row.name.upper(), 0), value, initializing=True)
                except STUncovered as error:
                    inspector.finding('ST_INITIALIZER_COVERAGE', str(error), severity='unverified')
                except GXWFormatError as error:
                    inspector.finding('ST_INITIALIZER_BINDING', str(error), severity='unverified')
                except (ValueError, RecursionError) as error:
                    inspector.finding('ST_INITIALIZER', str(error))
                findings.extend(dict(finding, name=row.name) for finding in inspector.findings)
        old_return = before.declarations[name].owner_return_type
        if document.owner_return_type != old_return and document.owner_return_type not in (None, "@'VOID'"):
            try:
                base, bounds = _type_parts(document.owner_return_type)
                if base.upper() not in BASIC_TYPES:
                    before.structure(base)
            except GXWFormatError as error:
                findings.append(dict(code='FUNCTION_RETURN_TYPE', severity='error', message=str(error)))
        status = 'failed' if any(row['severity'] == 'error' for row in findings) else 'unverified' if findings else 'passed'
        checks.append(dict(source=name, language='declarations', status=status,
            syntax='failed' if any(row['code'] == 'ST_INITIALIZER' and row['severity'] == 'error' for row in findings) else 'passed',
            semantic=status, findings=findings,
            native_compile='not_run', execution='not_run'))
    return checks


def check_project_draft(baseline: bytes, *, source_replacements=None, declarations=None, programs=None,
                        save_policy='checked'):
    """Check the complete in-memory draft, after all source/table edits merge.

    A draft may retain source syntax/type/call errors. Malformed or foreign
    containers and source/declaration storage remain errors in both policies.
    The returned checks never claim native compilation or PLC execution.
    """
    save_policy = _save_policy(save_policy)
    config, streams, groups, sources, _ = _label_context(baseline)
    drafts, changed_documents, programs = dict(source_replacements or {}), dict(declarations or {}), dict(programs or {})
    for name, value in drafts.items():
        if name not in groups or not isinstance(value, bytes) or value[source_payload_offset(value)] != streams[name][source_payload_offset(streams[name])]:
            raise GXWFormatError('stale/foreign source draft or changed registered language: ' + str(name))
    for name, document in changed_documents.items():
        if name not in sources.declarations or document.logical_name != name or document.raw != streams.get(name):
            raise GXWFormatError('stale or foreign draft declaration source: ' + str(name))
    documents = {**sources.declarations, **changed_documents}
    draft_streams = {**streams, **drafts}
    changed = {name for name, document in changed_documents.items()
               if document.header != sources.declarations[name].header or document.rows != sources.declarations[name].rows}
    selected = {name for name, value in drafts.items() if value != streams[name]}
    affected = set(drafts)
    if changed:
        if any(documents[name].scope == 'global' or documents[name].owner_pou_type in (0x1000002, 0x1000003) for name in changed):
            affected.update(groups)
        else:
            affected.update(name for name, group in groups.items() if group['labels'] in changed)
    if any(sources.declarations.get(groups[name]['labels']) is not None and
           sources.declarations[groups[name]['labels']].owner_pou_type in (0x1000002, 0x1000003) for name in selected):
        affected.update(groups)
    checks = _draft_declaration_checks(sources, documents, changed, cpu=config['cpu'])
    st_names = [name for name in affected if streams[name][source_payload_offset(streams[name])] == 193]
    if st_names:
        try:
            st_checks = _check_st(baseline, streams, sources, documents, None, None, declaration_changed=True,
                                  source_replacements=drafts, save_policy='draft', affected_sources=affected)
            checks.extend(row for row in st_checks if row['source'] in affected)
        except GXWFormatError as error:
            from plc.st_validation import inspect_st_program
            for name in st_names:
                current = sources.with_declarations(documents)
                current.program_name = name
                report = inspect_st_program(parse_st_pou(draft_streams[name]).text, plc_model=config['cpu'], callable_sources=current)
                report['findings'].append(dict(code='ST_PROJECT_CONTEXT', severity='unverified', message=str(error)))
                if report['status'] == 'passed':
                    report.update(status='unverified', semantic='unverified', valid=False)
                checks.append(dict(source=name, language='st', **report, execution='not_run'))
    from .structured_pou import parse_structured_pou
    for name, raw in drafts.items():
        if raw[source_payload_offset(raw)] == 208 and name not in programs:
            programs[name] = parse_structured_pou(raw, logical_name=name, preserve_unsupported_records=True)
    for name in sorted(affected):
        if groups[name]['labels'] not in documents:
            checks.append(_source_check_error(name, 'declarations', 'DECLARATION_SOURCE_GAP',
                'current source has an independent unreadable table', severity='unverified'))
            continue
        kind = streams[name][source_payload_offset(streams[name])]
        try:
            if kind == 208:
                checks.extend(_check_graphs(baseline, draft_streams, {name: groups[name]}, sources, documents, programs))
            elif kind in (1, 240):
                checks.extend(_token_checks(draft_streams, {name: groups[name]}, sources, documents,
                    text_encoding=config['text_encoding'], bind_sources=selected))
        except GXWFormatError as error:
            checks.append(_source_check_error(name, {208: 'fbd', 1: 'ladder', 240: 'sfc'}[kind],
                'FBD_BINDING' if kind == 208 else 'TOKEN_DECLARATION_BINDING', error))
    for issue in sources.issues:
        if issue.get('code') == 'declaration_source_gap':
            checks.append(_source_check_error(issue['stream'], 'declarations', 'DECLARATION_SOURCE_GAP',
                issue['message'], severity='unverified'))
    errors = [(row['source'], finding) for row in checks
              for finding in row.get('introduced_errors', row.get('findings', ()))
              if finding['severity'] == 'error' and (save_policy == 'checked' or finding['code'] in
                  {'fbd_literal_syntax', 'fbd_literal_range', 'fbd_constant_type'})]
    if errors:
        error = GXWFormatError('offline source failed syntax/semantic or FBD binding checks: ' + '; '.join(
            name + ': ' + finding['message'] for name, finding in errors))
        error.findings = [dict(finding, source_name=name) for name, finding in errors]
        raise error
    return checks


def prepare_declaration_edits(baseline: bytes, declaration_edits, *, source_replacements=None,
                              expected_sources=None, save_policy='checked', defer_checks=False):
    """Encode existing table patches; optionally defer checks until draft merge."""
    save_policy = _save_policy(save_policy)
    if not isinstance(declaration_edits, (Mapping, type(None))):
        raise GXWFormatError('declaration_edits requires existing table patches')
    if not isinstance(source_replacements, (Mapping, type(None))):
        raise GXWFormatError('source_replacements requires current logical source bytes')
    patches, drafts = dict(declaration_edits or {}), dict(source_replacements or {})
    if not patches and not drafts:
        return None, []
    config, streams, groups, sources, _ = _label_context(baseline)
    if expected_sources is not None and (not isinstance(expected_sources, Mapping) or any(
            not isinstance(value, bytes) or streams.get(name) != value for name, value in expected_sources.items())):
        raise GXWFormatError('stale or foreign offline declaration source')
    for name, value in drafts.items():
        if name not in groups or not isinstance(value, bytes) or value[source_payload_offset(value)] != streams[name][source_payload_offset(streams[name])]:
            raise GXWFormatError('declaration source drafts require current registered source identities and language')
    edits = {}
    owners = {group['labels']: name for name, group in groups.items()}
    for name, patch in patches.items():
        if name not in sources.declarations:
            raise GXWFormatError('invalid or unreadable existing declaration table edit: ' + str(name))
        document = sources.declarations[name]
        if document.scope == 'local' and name not in owners:
            raise GXWFormatError('local declaration table has no current registered owner: ' + name)
        edits[name] = _edited_document(config, sources, document, patch)
    checks = [] if defer_checks else check_project_draft(baseline, source_replacements=drafts,
        declarations=edits, save_policy=save_policy)
    if not edits:
        return None, checks
    prepared = prepare_project_write(baseline, declarations=edits)
    changed = {name for name, (_, old, new) in prepared.replacements.items() if old != new}
    if not changed:
        return prepared, checks
    affected = set(groups) if any(edits[name].scope == 'global' or edits[name].owner_pou_type in (0x1000002, 0x1000003)
                                      for name in changed) else {owners[name] for name in changed if name in owners}
    from .project_metadata import mark_observed_source_compile_pending
    replacements, objects = dict(prepared.replacements), list(prepared.objects)
    for name in sorted(affected | set(drafts)):
        original = streams[name]
        new = mark_observed_source_compile_pending(drafts.get(name, original), logical_name=name)
        replacements[name] = (prepared.mapping[name], original, new)
        objects.append(dict(object=name, stream=prepared.mapping[name], old_length=len(original), new_length=len(new),
                            compile_state_only=name not in drafts, offset_space='logical source bytes',
                            binary_changes=binary_diff(original, new)))
    return replace(prepared, replacements=replacements, objects=objects), checks


def prepare_existing_definition_write(baseline: bytes, command: Mapping, *, expected_sources=None,
                                      programs=None, save_policy='checked', defer_checks=False,
                                      source_replacements=None) -> PreparedProjectWrite:
    """Encode one existing ST/label edit; check the complete draft by policy.

    Complete ST declarations follow their requested order. Table operations use
    existing-row upserts/renames/remove/order. Return types belong only to an
    already registered FUNCTION. No source, table, resource or task is created.
    """
    save_policy = _save_policy(save_policy)
    if not isinstance(command, Mapping) or command.get('operation') not in EXISTING_DEFINITION_OPERATIONS:
        raise GXWFormatError('offline authoring only edits existing ST/label objects')
    command = deepcopy(dict(command))
    operation = command['operation']
    allowed = {'operation', 'source_name', 'declarations', 'return_type', 'renames', 'order', 'remove_declarations'}
    if operation in ST_SOURCE_OPERATIONS:
        allowed.add('st_text')
    if set(command) - allowed:
        raise GXWFormatError('unknown existing definition edit fields')
    config, streams, groups, sources, caller = _label_context(baseline)
    if expected_sources is not None and (not isinstance(expected_sources, Mapping) or any(
            not isinstance(value, bytes) or streams.get(name) != value for name, value in expected_sources.items())):
        raise GXWFormatError('stale or foreign offline authoring source')
    if operation == 'edit_global_labels':
        logical, document = _global_selection(command.get('source_name'), sources)
        selected, original_text = None, None
    else:
        selected, document = _local_selection(command.get('source_name'), groups, sources)
        logical = document.logical_name
        kind = streams[selected][source_payload_offset(streams[selected])]
        original_text = parse_st_pou(streams[selected]).text if kind == 193 else None
        required = {'edit_st_program': 0x1000000, 'edit_st_fb': 0x1000002, 'edit_st_function': 0x1000003}
        if operation in ST_SOURCE_OPERATIONS and (kind != 193 or required.get(operation, document.owner_pou_type) != document.owner_pou_type):
            raise GXWFormatError('ST edit operation differs from the current source language or POU kind')
    before = _definition_snapshot(document, sources, selected=selected, text=original_text)
    patch = {key: command[key] for key in ('renames', 'order', 'return_type') if key in command}
    values = command.get('declarations')
    if operation in ST_SOURCE_OPERATIONS and values is not None:
        if command.get('remove_declarations') or 'renames' in command or 'order' in command:
            raise GXWFormatError('complete ST declarations carry their own complete names and order')
        expected = _existing_st_rows(config, sources, document, values)
        names = [row['name'] for row in expected]
        patch.update(upserts=_preserving_upserts(expected, document, sources),
            remove=[row.name for row in document.rows if row.name.casefold() not in {name.casefold() for name in names}], order=names)
    elif values is not None:
        if not isinstance(values, list) or len(values) > 8192:
            raise GXWFormatError('existing declaration upserts require a bounded list')
        patch['upserts'] = _preserving_upserts(values, document, sources)
    if 'remove_declarations' in command:
        patch['remove'] = command['remove_declarations']
    edited = _edited_document(config, sources, document, patch)
    prepared = prepare_project_write(baseline, dict(programs or {}), declarations={logical: edited})
    drafts = dict(source_replacements or {})
    new_text = original_text
    if operation in ST_SOURCE_OPERATIONS and 'st_text' in command:
        new_text = _st_implementation(command['st_text'])
        source_write = prepare_st_project_write(baseline, selected, parse_st_pou(streams[selected]), new_text)
        if set(prepared.replacements) & set(source_write.replacements):
            raise GXWFormatError('conflicting source edits')
        prepared = replace(prepared, programs={**prepared.programs, **source_write.programs},
            replacements={**prepared.replacements, **source_write.replacements}, objects=[*prepared.objects, *source_write.objects])
        own_bytes = source_write.replacements[selected][2]
        if selected in drafts and drafts[selected] != own_bytes:
            raise GXWFormatError('conflicting draft source and ST body edit: ' + selected)
        drafts[selected] = own_bytes
    elif operation in ST_SOURCE_OPERATIONS and not patch:
        raise GXWFormatError('existing ST editing requires body, declaration or return-type edits')
    declaration_changed = prepared.replacements[logical][1] != prepared.replacements[logical][2]
    if declaration_changed and selected is not None:
        if selected in drafts and selected not in prepared.replacements:
            from .project_metadata import mark_observed_source_compile_pending
            new = mark_observed_source_compile_pending(drafts[selected], logical_name=selected)
            prepared = replace(prepared, replacements={**prepared.replacements,
                selected: (prepared.mapping[selected], streams[selected], new)})
        else:
            prepared = _pending_local_source(prepared, selected)
    changed = any(old != new for _, old, new in prepared.replacements.values())
    check_drafts = {**drafts, **{name: value[2] for name, value in prepared.replacements.items() if name in groups}}
    checks = ([] if defer_checks or not changed else check_project_draft(baseline, source_replacements=check_drafts,
        declarations={logical: edited}, programs=programs, save_policy=save_policy))
    current = sources.with_declarations({**sources.declarations, logical: edited})
    after = _definition_snapshot(edited, current, selected=selected, text=new_text)
    if operation in ('edit_pou_labels', 'edit_global_labels'):
        before['kind'] = after['kind'] = 'pou_labels' if selected else 'global_table'
    summary = dict(operation=operation, source_name=document.owner_name if selected else logical,
        definition_before=before, definition_after=after, caller=caller, save_policy=save_policy,
        checks_deferred=bool(defer_checks), source_checks=checks, native_compile='not_run', execution='not_run')
    objects = [dict(row, **({'offline_authoring': summary} if index == 0 else {})) for index, row in enumerate(prepared.objects)]
    return replace(prepared, objects=objects)


def write_existing_definition(baseline: bytes, command: Mapping, *, expected_sources=None, programs=None,
                              device_comments=None, comment_table=None, save_policy='checked',
                              source_replacements=None) -> ProjectWriteResult:
    """Save existing definitions with exact source preservation and diagnostics."""
    prepared = prepare_existing_definition_write(baseline, command, expected_sources=expected_sources,
        programs=programs, save_policy=save_policy, source_replacements=source_replacements)
    result = write_prepared_project(prepared, device_comments=device_comments, comment_table=comment_table)
    summary = next(row['offline_authoring'] for row in prepared.objects if 'offline_authoring' in row)
    return ProjectWriteResult(result.data, {**result.report, **summary, 'operation': 'offline_definition_authoring',
                                          'authoring_operation': summary['operation']})

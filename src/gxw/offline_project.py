"""Source-bound GXW file inspection and existing-object editing, without an IDE."""
from __future__ import annotations
from .project_metadata import project_stream_mapping

import copy
import re
from dataclasses import dataclass
from typing import Mapping

from .container_writer import validate_cfb_streams
from .integrity import read_project_hdb, inspect_project_integrity
from .models import GXWFormatError
from .project_metadata import logical_mapping, read_project_text_context
from .source_header import source_payload_offset


@dataclass(frozen=True)
class OfflineProject:
    raw: bytes
    outer: dict
    mapping: dict
    streams: dict
    context: dict

    def token_storage_context(self):
        """Bind Q/L stored widths to the unique project parameter stream."""
        from .token_profiles import source_token_profile
        profile = source_token_profile(self.context['cpu'])
        if profile is None or profile.startswith('fx'):
            return None
        from .token_q_encoder import q_project_storage_context
        parameters = [raw for name, raw in self.streams.items() if name.casefold() == 'param.wpa']
        return q_project_storage_context(parameters[0] if len(parameters) == 1 else None,
                                         cpu=self.context['cpu'])

    def select(self, program=None):
        candidates = [row for row in self.sources() if row['unit_kind'] == 'program']
        if program is not None:
            candidates = [row for row in self.sources() if
                program.casefold() in {row['source_name'].casefold(), row['name'].casefold()}]
        if len(candidates) != 1:
            raise GXWFormatError('Select one unambiguous program: ' + ', '.join(row['source_name'] for row in candidates))
        return candidates[0]

    def sources(self):
        from .declarations import parse_declarations
        from .project_metadata import existing_source_groups
        groups, _ = existing_source_groups(self.outer['projectdatalist.xml'], self.streams)
        integrity = inspect_project_integrity(self.outer)
        result = []
        for name, payload in self.streams.items():
            if name not in groups:
                continue
            reason, language = None, None
            try:
                kind = payload[source_payload_offset(payload)]
                language = {1: 'ladder', 193: 'st', 208: 'fbd', 240: 'sfc'}.get(kind)
                if language is None:
                    reason = 'uncovered_source_language:' + str(kind)
            except (ValueError, IndexError) as error:
                kind, reason = None, str(error)
            group = groups.get(name)
            owner = (group or {}).get('owner_name') or name.removesuffix('.Program.pou').removesuffix('.pou')
            unit_kind = 'program' if name.endswith('.Program.pou') else 'function_block'
            if group and group['labels'] in self.streams:
                try:
                    document = parse_declarations(self.streams[group['labels']], logical_name=group['labels'],
                                                  source_kind=240 if kind == 240 else None)
                    owner = document.owner_name or owner
                    unit_kind = {0x1000000: 'program', 0x1000003: 'function',
                                 0x1000002: 'function_block'}.get(document.owner_pou_type, unit_kind)
                except GXWFormatError:
                    pass
            result.append({'source_name': name, 'name': owner, 'language': language,
                'program_kind': kind, 'unit_kind': unit_kind, 'readable': language is not None,
                'offline_editable': language is not None and reason is None,
                'reason': reason})
            if reason is None:
                try:
                    if not integrity['supported']:
                        raise GXWFormatError(integrity.get('message', 'unsupported saved-project envelope'))
                    from .project_metadata import mark_observed_source_compile_pending
                    if not name.endswith('.lnb'):
                        mark_observed_source_compile_pending(payload, logical_name=name)
                    if language == 'ladder':
                        from .token_profiles import source_token_profile
                        from .token_listing import decode_token_listing
                        listing = decode_token_listing(payload, profile=source_token_profile(self.context['cpu']),
                                                       text_encoding=self.context['text_encoding'])
                        result[-1]['parse_status'] = 'partial' if listing.gaps else 'complete'
                        result[-1]['preserved_records'] = sum(row.__class__.__name__ in {'TokenGap', 'TokenLabel'}
                                                             for row in listing.records)
                        result[-1]['native_requirements'] = _instruction_requirements(listing)
                    elif language == 'st':
                        from .text_pou import parse_st_pou
                        if parse_st_pou(payload).text is None:
                            raise GXWFormatError('ST body encoding is not covered')
                except GXWFormatError as error:
                    result[-1].update(offline_editable=False, reason=str(error))
        for row in result:
            row['capabilities'] = _source_capabilities(self, row)
        return result

    def declaration_tables(self):
        from .declarations import CLASS_CODES, parse_declarations
        from .project_metadata import existing_source_groups
        groups, _ = existing_source_groups(self.outer['projectdatalist.xml'], self.streams)
        owners = {group['labels']: logical for logical, group in groups.items()}
        classes = {value: name for name, value in CLASS_CODES.items()}
        result = []
        for name, raw in self.streams.items():
            if not name.endswith(('.lh', '.gh', '.lnl')):
                continue
            try:
                source = self.streams.get(owners.get(name))
                kind = source[source_payload_offset(source)] if source else None
                document = parse_declarations(raw, logical_name=name, source_kind=240 if kind == 240 else None)
                result.append({'source_name': name, 'owner': document.owner_name, 'scope': document.scope,
                    'program': owners.get(name), 'rows': [{key: getattr(row, key) for key in
                        ('name', 'data_type', 'device', 'iec_address', 'initial_value', 'comment')}
                        | {'class_name': classes.get(row.class_code, 'uncovered')} for row in document.rows]})
            except GXWFormatError as error:
                result.append({'source_name': name, 'readable': False, 'reason': str(error)})
        return result

    def comments(self):
        from .device_comments import parse_device_comments
        comments, tables, issues = {}, [], []
        for name, payload in self.streams.items():
            if not name.lower().endswith('.qcd'):
                continue
            try:
                document = parse_device_comments(payload, logical_name=name, plc_model=self.context['cpu'])
                tables.append({'source_name': name, 'count': len(document.comments),
                               'total_rows': len(document.rows),
                               'opaque_rows': sum(not row.editable for row in document.rows),
                               'write_blockers': list(document.write_blockers),
                               'address_validation': document.address_validation})
                for address, text in document.comments.items():
                    if address in comments and comments[address] != text:
                        issues.append({'code': 'ambiguous_device_comment', 'address': address, 'source_name': name})
                    else:
                        comments[address] = text
            except GXWFormatError as error:
                issues.append({'code': 'uncovered_comment_table', 'source_name': name, 'message': str(error)})
        return comments, tables, issues


def read_offline_project(raw, *, allow_unverified=False):
    raw = bytes(raw)
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    try:
        nested = read_project_hdb(outer).payloads
    except GXWFormatError:
        if not allow_unverified:
            raise
        nested = validate_cfb_streams(outer['_hdb'])
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    metadata = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
    if len(metadata) != 1:
        raise GXWFormatError('GXW requires one unambiguous stored CPU/code-page configuration')
    return OfflineProject(raw, outer, mapping, streams, metadata[0])


def _source_capabilities(project, selected):
    """Describe source editing without borrowing native acceptance results."""
    language = selected['language']
    operations = {'ladder': ['instruction_replace', 'instruction_insert', 'instruction_delete', 'instruction_move',
                             'label_replace', 'label_insert', 'label_delete', 'label_move',
                             'inline_text_replace', 'inline_text_insert', 'inline_text_delete', 'inline_text_move'],
                  'st': ['body_replace', 'declaration_upsert', 'declaration_delete'],
                  'fbd': ['graph_edit', 'node_insert', 'node_delete', 'connection_edit', 'instance_edit'],
                  'sfc': ['graph_edit', 'symbol_insert', 'symbol_delete', 'step_edit',
                          'transition_edit', 'action_edit', 'child_edit', 'child_create',
                          'child_rename', 'child_delete']}
    status = 'available' if selected['offline_editable'] else 'unimplemented'
    result = {'language': language, 'unit_kind': selected['unit_kind'], 'write_backend': 'offline',
              'source_edit': {'status': status, 'reason': selected.get('reason'),
                              'scope': 'recognised_objects', 'operations': operations.get(language, [])},
              'native_compile': 'not_run', 'execution': 'unverified'}
    if language in {'ladder', 'sfc'}:
        from .token_profiles import source_token_capability
        result['token_codec'] = source_token_capability(project.context['cpu'], language=language)
        storage_context = project.token_storage_context()
        if storage_context is not None:
            result['token_codec']['project_allocation'] = storage_context.as_dict()
    if selected.get('native_requirements'):
        result['native_requirements'] = selected['native_requirements']
    return result


def _instruction_requirements(listing):
    if listing.profile.startswith('fx'):
        return []
    from .token_q import q_instruction_requirements
    from .token_listing import TokenInstruction
    conditions = []
    for index, record in enumerate(listing.records):
        if not isinstance(record, TokenInstruction):
            continue
        requirement = q_instruction_requirements(record.tokens[0].raw, profile=listing.profile,
            operand_tokens=tuple(token.raw for operand in record.operands for token in operand.tokens))
        if (requirement['requires_native_version_context'] or requirement['requires_named_call_binding']
                or requirement['requires_label_binding'] or requirement['requires_virtual_device_binding']
                or requirement['requires_program_file_binding'] or requirement['requires_double_index_configuration']
                or requirement['extended_prefix_forms']):
            conditions.append({'record_index': index, **requirement})
    return conditions


def read_ordered_program(project, program=None, *, revision=1):
    from .token_profiles import source_token_profile
    from .token_listing import decode_token_listing, TokenInstruction, TokenText, TokenGap, TokenLabel
    from gxworks2.csv_importer import RawInstruction, _split_networks
    from plc.ordered_source import build_instruction_ir
    from plc.target_support import normalize_target_cpu
    selected = project.select(program)
    if selected['language'] != 'ladder':
        raise GXWFormatError('selected source is not an ordinary Ladder instruction program')
    listing = decode_token_listing(project.streams[selected['source_name']],
        profile=source_token_profile(project.context['cpu']), text_encoding=project.context['text_encoding'])
    records, source_records, unknown = [], [], []
    for index, record in enumerate(listing.records):
        offset = record.tokens[0].offset
        item = {'record_index': index, 'source_offset': offset,
                'raw_hex': b''.join(token.raw for token in record.tokens).hex(), 'step': getattr(record, 'step', None)}
        if isinstance(record, TokenInstruction):
            item.update(op=record.mnemonic, args=list(record.args))
            item['operand_forms'] = [operand.storage_form() for operand in record.operands]
            item['operand_details'] = [operand.components(profile=listing.profile)
                                       for operand in record.operands]
            records.append(RawInstruction(str(record.step), record.mnemonic, list(record.args), source_row=index + 1))
        elif isinstance(record, TokenText):
            item.update(kind=record.role, text=record.text)
            if record.text is not None and record.role == 'statement':
                records.append(RawInstruction(str(record.step), '', statement=record.text, source_row=index + 1))
        else:
            item.update(kind='label' if isinstance(record, TokenLabel) else 'opaque')
            if isinstance(record, TokenLabel):
                item['text'] = record.text
            else:
                item['reason'] = record.reason
            unknown.append(item)
        source_records.append(item)
    networks = []
    for index, (note, rows, _leading) in enumerate(_split_networks(records), 1):
        instructions = [{**row.as_ir(), 'step': row.step, 'source_row': row.source_row,
            'source_offset': source_records[row.source_row - 1]['source_offset'],
            'record_index': row.source_row - 1} for row in rows]
        networks.append({'id': f'N{index:04d}', 'comment': note, 'raw_instructions': instructions,
                         'parse_status': 'ordered'})
    if networks and listing.instructions and listing.instructions[-1].mnemonic == 'END':
        networks[-1]['raw_instructions'].append(copy.deepcopy(source_records[-1]))
    if unknown:
        # Network boundaries are a display projection. Partition *every* source
        # record in its original order, including labels and opaque barriers.
        # Never rebuild an editable source from the known-instruction subset.
        if not networks:
            networks = [{'id': 'N0001', 'comment': '', 'raw_instructions': [], 'parse_status': 'partial'}]
        starts = [0] + [network['raw_instructions'][0]['record_index'] for network in networks[1:]]
        ends = [*starts[1:], len(source_records)]
        for network, start, end in zip(networks, starts, ends):
            network['raw_instructions'] = copy.deepcopy(source_records[start:end])
            network['parse_status'] = ('partial' if any(row.get('kind') == 'opaque'
                                                       for row in source_records[start:end]) else 'ordered')
    comments, tables, issues = project.comments()
    issues.extend({'code': 'source_instruction_context_unverified', 'source_name': selected['source_name'], **row}
                  for row in _instruction_requirements(listing))
    model = normalize_target_cpu(project.context['cpu'])
    ir = build_instruction_ir(networks, plc_model=model,
        program_name=selected['source_name'], device_comments=comments, revision=revision)
    return {'selected': selected, 'listing': listing, 'source_records': source_records,
        'raw_networks': networks, 'device_comments': comments, 'comment_tables': tables,
        'issues': issues, 'program_ir': ir, 'plc_model': model,
        'coverage': {'parse_status': 'partial' if unknown else 'complete', 'network_count': len(networks),
            'parsed_network_count': len(networks) if not unknown else 0, 'unknown_instructions': unknown,
            'semantic_status': 'unverified' if unknown else 'static_only', 'runtime_verified': False}}


def inspect_offline_project(raw, program=None):
    project = read_offline_project(raw, allow_unverified=True)
    sources = project.sources()
    selected = project.select(program) if program is not None or len([p for p in sources if p['unit_kind'] == 'program']) == 1 else None
    comments, tables, issues = project.comments()
    result = {'cpu_menu': project.context['cpu'], 'codepage': project.context['codepage'],
        'programs': sources, 'selected_source': selected, 'device_comments': comments,
        'comment_tables': tables, 'issues': issues, 'write_backend': 'offline',
        'declaration_tables': project.declaration_tables(),
        'integrity': inspect_project_integrity(project.outer), 'native_required': False,
        'native_compile': 'not_run', 'runtime_verified': False}
    if selected is not None and selected['language'] is not None:
        try:
            if selected['language'] == 'ladder':
                ordered = read_ordered_program(project, selected['source_name'])
                result.update(coverage=ordered['coverage'], networks=ordered['raw_networks'],
                              source_records=ordered['source_records'])
                result['issues'].extend(row for row in ordered['issues']
                                        if row.get('code') == 'source_instruction_context_unverified')
            elif selected['language'] == 'st':
                from .text_pou import parse_st_pou
                source = parse_st_pou(project.streams[selected['source_name']])
                if source.text is None:
                    raise GXWFormatError(source.diagnostic)
            elif selected['language'] == 'fbd':
                from .structured_pou import parse_structured_pou
                parse_structured_pou(project.streams[selected['source_name']], preserve_unsupported_records=True)
            elif selected['language'] == 'sfc':
                from .sfc_pou import parse_sfc_pou
                parse_sfc_pou(project.streams[selected['source_name']])
        except GXWFormatError as error:
            selected.update(readable=False, offline_editable=False, reason=str(error))
            result['issues'].append({'code': 'uncovered_source_format',
                'source_name': selected['source_name'], 'message': str(error)})
            result['coverage'] = {'parse_status': 'unavailable', 'network_count': 0,
                'parsed_network_count': 0, 'unknown_instructions': [],
                'semantic_status': 'unverified', 'runtime_verified': False}
        selected['capabilities'] = _source_capabilities(project, selected)
        result['programs'] = [selected if row['source_name'] == selected['source_name'] else row
                              for row in sources]
    return result


def prepare_comment_replacements(project, updates, comment_table=None):
    """Bind complete comment text to one existing actual QCD table."""
    from .device_comments import parse_device_comments, edit_device_comments, serialize_device_comments
    if updates is None or updates == {}:
        return {}
    if not isinstance(updates, Mapping):
        raise GXWFormatError('device_comments must map device addresses to complete text or null')
    tables = [name for name in project.streams if name.lower().endswith('.qcd')]
    if comment_table is None and len(tables) == 1:
        comment_table = tables[0]
    if comment_table not in tables:
        raise GXWFormatError('select one current comment table for comment edits')
    old = project.streams[comment_table]
    document = parse_device_comments(old, logical_name=comment_table, plc_model=project.context['cpu'])
    edited = serialize_device_comments(edit_device_comments(document, updates))
    return {comment_table: (project.mapping[comment_table], old, edited)}


def project_declaration_changes(before, after):
    """Review changed existing tables without dropping unreadable neighbors."""
    old = read_offline_project(before, allow_unverified=True) if before is not None else None
    new = read_offline_project(after, allow_unverified=True)
    left = {row['source_name']: row for row in old.declaration_tables()} if old is not None else {}
    right = {row['source_name']: row for row in new.declaration_tables()}
    changed = [name for name in sorted(left.keys() | right.keys())
               if (old.streams.get(name) if old is not None else None) != new.streams.get(name)]
    if not changed:
        return None
    return {'before': {'tables': [left.get(name, {'source_name': name, 'absent': True}) for name in changed]},
            'after': {'tables': [right.get(name, {'source_name': name, 'absent': True}) for name in changed]}}


def project_comment_changes(before, after):
    """Compare actual table/address identities, including empty stored rows."""
    from .device_comments import parse_device_comments
    old = read_offline_project(before, allow_unverified=True) if before is not None else None
    new = read_offline_project(after, allow_unverified=True)
    names = {name for project in (old, new) if project is not None
             for name in project.streams if name.lower().endswith('.qcd')}
    changes = []
    for name in sorted(names):
        a = old.streams.get(name) if old is not None else None
        b = new.streams.get(name)
        if a == b:
            continue
        try:
            left = parse_device_comments(a, logical_name=name, plc_model=old.context['cpu']).comments if a is not None else {}
            right = parse_device_comments(b, logical_name=name, plc_model=new.context['cpu']).comments if b is not None else {}
        except GXWFormatError as error:
            changes.append({'table': name, 'address': None, 'before': None, 'after': None, 'reason': str(error)})
            continue
        for address in sorted(set(left) | set(right)):
            if (address in left) != (address in right) or left.get(address) != right.get(address):
                changes.append({'table': name, 'address': address, 'before': left.get(address), 'after': right.get(address)})
        if not any(row['table'] == name for row in changes):
            changes.append({'table': name, 'address': None, 'before': None, 'after': None,
                            'reason': 'comment storage changed with identical readable text'})
    return changes


def edit_ladder_project(raw, program, edits, *, device_comments=None, comment_table=None,
                        declaration_edits=None, expected_source=None, save_policy='checked',
                        defer_checks=False):
    """Apply source-record edits in original coordinates, then save once offline."""
    from .token_patch import edit_token_records
    from .token_listing import decode_token_listing
    from .token_pou import frame_token_pou
    from .project_writer import write_project_replacements
    import struct
    project = read_offline_project(raw)
    current = read_ordered_program(project, program)
    selected, listing = current['selected'], current['listing']
    logical = selected['source_name']
    before = project.streams[logical]
    storage_context = project.token_storage_context()
    if expected_source is not None and expected_source != before.hex():
        raise GXWFormatError('stale or foreign Ladder source')
    if save_policy not in {'checked', 'draft'} or type(defer_checks) is not bool:
        raise GXWFormatError('invalid Ladder source save policy')
    prepared_records = edit_token_records(listing, edits,
        text_encoding=project.context['text_encoding'], plc_model=project.context['cpu'],
        storage_context=storage_context,
        allow_draft=save_policy == 'draft',
        allow_end_boundary=selected['unit_kind'] in {'function_block', 'function'})
    body, chunks = prepared_records.body, prepared_records.records
    frame = frame_token_pou(before)
    new = bytearray(before[:frame.body_offset] + body + before[frame.body_end:])
    # These are the two observed source-body size fields in the bound frame.
    size_delta = len(new) - len(before)
    for offset in (frame.body_offset - 24, frame.body_offset - 20):
        struct.pack_into('<I', new, offset, struct.unpack_from('<I', before, offset)[0] + size_delta)
    new = bytes(new)
    reread = decode_token_listing(new, profile=listing.profile, text_encoding=project.context['text_encoding'])
    if len(reread.records) != len(chunks) or any(
            b''.join(token.raw for token in record.tokens) != chunk
            for record, chunk in zip(reread.records, chunks)):
        raise GXWFormatError('source edit changes a neighboring record binding')
    if reread.reconstruct() != new:
        raise GXWFormatError('modified instruction source failed lossless readback')
    replacements = {logical: (project.mapping[logical], before, new)}
    from .offline_authoring import prepare_declaration_edits
    prepared, checks = prepare_declaration_edits(raw, declaration_edits,
        source_replacements={logical: new}, expected_sources={logical: before},
        save_policy=save_policy, defer_checks=defer_checks)
    if prepared is not None:
        replacements.update(prepared.replacements)
    saved = write_project_replacements(raw, replacements,
        objects=prepared.objects if prepared is not None else None,
        device_comments=device_comments, comment_table=comment_table)
    saved.report['source_checks'] = checks
    after = read_ordered_program(read_offline_project(saved.data), logical)
    if after['program_ir'] is not None and save_policy == 'checked' and not defer_checks:
        from plc.ir import validate_plc_ir
        validate_plc_ir(after['program_ir'], validate_ladder=False)
    return saved

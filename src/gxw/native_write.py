"""Source-bound plans and saved-source verification for the offline workspace.

PLC/declaration interpretation stays here. The native adapter only executes the
prepared calls; successful save/readback does not establish compilation.
"""
from __future__ import annotations

import base64
from dataclasses import fields
import struct

from .declarations import LabelRecord, local_table_for, parse_declarations
from .models import GXWFormatError
from .project_metadata import current_rows, logical_mapping, project_source_groups, read_project_text_context
from .project_writer import PreparedProjectWrite, ProjectWriteResult, binary_diff
from .container_writer import validate_cfb_streams
from .source_header import source_payload_offset


def workspace_body(raw: bytes) -> bytes:
    """The observed self-sized FBD body, excluding the native source envelope."""
    start = source_payload_offset(raw) + 5
    if start + 4 > len(raw):
        raise GXWFormatError('truncated native structured source body')
    size = struct.unpack_from('<I', raw, start)[0]
    if not 4 <= size <= 8 * 1024 * 1024 or start + size + 24 != len(raw):
        raise GXWFormatError('structured source is outside the observed native body envelope')
    return raw[start:start + size]


def _identity(row):
    return {'name': row.name, 'data_type': row.data_type, 'class_code': row.class_code}


def _values(row, *, omit=()):
    return {f.name: getattr(row, f.name) for f in fields(LabelRecord)
            if f.name not in {'offset', 'raw', *omit}}


def native_source_plan(prepared: PreparedProjectWrite) -> dict:
    """Prepare one existing POU and supported root-local declaration changes."""
    if len(prepared.programs) != 1:
        raise GXWFormatError('native source save requires one selected existing Program.pou')
    logical = next(iter(prepared.programs))
    groups, _ = project_source_groups(prepared.outer['projectdatalist.xml'])
    documents = {name: parse_declarations(prepared.nested[stream], logical_name=name)
                 for name, stream in prepared.mapping.items() if name.endswith('.lh') and stream in prepared.nested}
    local = local_table_for(documents, logical, groups)
    if local not in prepared.mapping:
        raise GXWFormatError('native source save requires the selected POU local declaration table')
    original = parse_declarations(prepared.nested[prepared.mapping[local]], logical_name=local)
    if original.scope != 'local' or not original.owner_name:
        raise GXWFormatError('native source save cannot resolve the declaration owner')
    changed = prepared.declarations.get(local, original)
    for name, (_, old, new) in prepared.replacements.items():
        if old != new and name not in (logical, local):
            raise GXWFormatError('native source save does not yet edit other POU or global tables: ' + name)
    metadata = [prepared.nested[stream] for name, stream in prepared.mapping.items() if name.endswith('.prj')]
    if len(metadata) != 1:
        raise GXWFormatError('native source save requires unambiguous project CPU/configuration')
    project = read_project_text_context(metadata[0])
    # Editor rows retain their original byte span until serialization. Saved
    # record_id is an opaque value and can be shared; never use it as identity.
    old_rows = {r.offset: r for r in original.rows}
    retained, added = {}, []
    for row in changed.rows:
        if not row.raw and row.offset == 0:
            added.append(row)
        elif (row.offset not in old_rows or row.raw != old_rows[row.offset].raw or row.offset in retained):
            raise GXWFormatError('native declaration edit has a stale or repeated source row')
        else:
            retained[row.offset] = row
    remove, create, updates = [], [], []
    for old in original.rows:
        new = retained.get(old.offset)
        if new is None:
            if old.class_code != 1 or old.value_extension:
                raise GXWFormatError('native removal currently supports root VAR declarations only')
            remove.append(_identity(old))
        elif _values(old) != _values(new):
            if old.class_code != new.class_code or old.value_extension:
                raise GXWFormatError('native save does not yet change declaration classes or structure storage')
            omitted = {'name'}
            if old.data_type != new.data_type:
                # The completed lifecycle module establishes scalar BOOL -> FB.
                # Other type transitions need their own native controls.
                if old.data_type != 'BOOL' or old.type_code != 0 or new.type_code != 15 or new.array_marker:
                    raise GXWFormatError('native declaration type change is not verified for this transition')
                omitted |= {'data_type', 'initial_value', 'type_code', 'type_reference'}
            if _values(old, omit=omitted) != _values(new, omit=omitted):
                raise GXWFormatError('native save does not yet edit devices, initial values or comments')
            updates.append({'before': _identity(old), 'after': _identity(new),
                            'initial_before': old.initial_value, 'initial_after': new.initial_value})
    for new in added:
        if (new.class_code != 1 or new.array_marker or new.value_extension or new.device or new.iec_address
                or new.comment or new.unknown_text or new.unknown_u32
                or not (new.data_type == 'BOOL' and new.initial_value == 'FALSE'
                        or new.type_code == 15 and new.initial_value == '')):
            raise GXWFormatError('native creation currently supports default root BOOL/FB declarations only')
        create.append(_identity(new) | {'initial_value': new.initial_value})
    _, before, after = prepared.replacements[logical]
    return {'program_name': original.owner_name, 'cpu': project['cpu'], 'codepage': project['codepage'],
            'before_body': base64.b64encode(workspace_body(before)).decode('ascii'),
            'after_body': base64.b64encode(workspace_body(after)).decode('ascii'),
            'local_before': [_identity(r) for r in original.rows],
            'local_after': [_identity(r) for r in changed.rows],
            'remove': remove, 'create': create, 'updates': updates}


def _saved_object_names(prepared, saved_metadata, saved_mapping, groups, documents):
    """Follow the observed localized child-name normalization by native identity."""
    def identities(metadata, mapping):
        rows, _ = current_rows(metadata, 'DSPROJECTDATA', 'D_Projectdata')
        result = {}
        for row in rows:
            values = {key: value.text.strip() for key, value in row.fields().items()}
            name = values.get('szName')
            if name in mapping and mapping[name] == values.get('iID'):
                result[name] = tuple(values.get(key) for key in
                    ('iID', 'ucProductType', 'ucFolderType', 'uiFolderNo', 'ucReserve', 'ucFileType'))
        return result

    before = identities(prepared.outer['projectdatalist.xml'], prepared.mapping)
    after = identities(saved_metadata, saved_mapping)
    by_identity = {key: name for name, key in after.items()}
    if len(by_identity) != len(after) or set(before.values()) != set(after.values()):
        raise GXWFormatError('native save changed the registered project object identities')
    names = {name: by_identity[key] for name, key in before.items()}
    allowed = {}
    for program, group in groups.items():
        local = local_table_for(documents, program, groups)
        if local is not None:
            owner = documents[local].owner_name
            allowed[program], allowed[local] = owner + '.Program.pou', owner + '.Labels.lh'
    for original, saved in names.items():
        if original != saved and allowed.get(original) != saved:
            raise GXWFormatError('native save changed an unverified project object name: ' + original)
    return names


def verify_native_save(prepared: PreparedProjectWrite, raw: bytes, observation: dict) -> ProjectWriteResult:
    """Inspect the saved file, including untouched source and opaque row fields."""
    plan = native_source_plan(prepared)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    logical = next(iter(prepared.programs))
    groups, _ = project_source_groups(prepared.outer['projectdatalist.xml'])
    documents = {name: parse_declarations(prepared.nested[stream], logical_name=name)
                 for name, stream in prepared.mapping.items() if name.endswith('.lh') and stream in prepared.nested}
    local = local_table_for(documents, logical, groups)
    names = _saved_object_names(prepared, outer['projectdatalist.xml'], mapping, groups, documents)
    if workspace_body(nested[mapping[names[logical]]]) != workspace_body(prepared.replacements[logical][2]):
        raise GXWFormatError('saved native source differs from the current prepared graph')
    for name, stream in prepared.mapping.items():
        if name not in (logical, local) and name.endswith(('.pou', '.lh', '.gh', '.lnl', '.lnb', '.lif', '.prj')):
            if nested.get(mapping[names[name]]) != prepared.nested.get(stream):
                raise GXWFormatError('native save changed an unedited source/configuration object: ' + name)
    original = parse_declarations(prepared.nested[prepared.mapping[local]], logical_name=local)
    expected = prepared.declarations.get(local, original)
    actual = parse_declarations(nested[mapping[names[local]]], logical_name=names[local])
    if (actual.owner_name, actual.owner_pou_type, actual.owner_return_type) != (
            expected.owner_name, expected.owner_pou_type, expected.owner_return_type):
        raise GXWFormatError('saved native declaration owner differs from the selected POU')
    if len(actual.rows) != len(expected.rows):
        raise GXWFormatError('saved native declaration membership differs from the prepared edit')
    created = {r['name'] for r in plan['create']}
    typed = {r['after']['name']: r for r in plan['updates']
             if r['before']['data_type'] != r['after']['data_type']}
    for wanted, saved in zip(expected.rows, actual.rows):
        omit = {'record_id', 'type_code', 'type_reference'} if wanted.name in created else set()
        if wanted.name in typed:
            omit |= {'type_code', 'type_reference'}
        if _values(wanted, omit=omit) != _values(saved, omit=omit):
            raise GXWFormatError('saved native declaration fields differ: ' + wanted.name)
        if wanted.name in created and wanted.type_code == 15 and (saved.type_code, saved.type_reference) != (0, ''):
            raise GXWFormatError('created native FB cache fields differ from the observed layout')
        if wanted.name in typed and (saved.type_code, saved.type_reference) != (0, ''):
            raise GXWFormatError('changed native FB cache fields differ from the observed layout')
    objects = [{'object': name, 'saved_object': names[name], 'stream': mapping[names[name]], 'old_length': len(old),
                'new_length': len(nested[mapping[names[name]]]), 'offset_space': 'logical source stream bytes',
                'binary_changes': binary_diff(old, nested[mapping[names[name]]])}
               for name, (_, old, _) in prepared.replacements.items()]
    report = {'schema_version': 1, 'operation': 'native_source_save', 'objects': objects,
              'native_workspace': observation,
              'registered_object_renames': [{'before': name, 'after': saved}
                                           for name, saved in names.items() if name != saved],
              'vendor_changed_streams': [name for name, stream in prepared.mapping.items()
                                         if prepared.nested.get(stream) != nested.get(mapping[names[name]])],
              'validation': {'parser': 'passed', 'source_body': 'exact', 'declarations': 'passed',
                             'unedited_sources': 'preserved', 'workspace_load': 'passed',
                             'native_save': 'passed', 'saved_file_readback': 'passed', 'gxworks_open': 'not_run',
                             'gxworks_compile': 'not_run', 'gxworks_check': 'not_run',
                             'gxworks_save_reload': 'not_run'},
              'limitations': ['Offline source save; compilation/checking and cold reopen are separate operations',
                              'Existing selected POU; root-local declaration lifecycle only']}
    return ProjectWriteResult(bytes(raw), report)

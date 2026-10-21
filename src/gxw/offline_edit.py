"""Source-bound, multi-POU offline editing over one immutable GXW version."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from .models import GXWFormatError
from .offline_project import read_offline_project
from .project_writer import PreparedProjectWrite, write_prepared_project, write_project_replacements


def _policy(value):
    if value not in ('draft', 'checked'):
        raise GXWFormatError('save_policy must be draft or checked')
    return value


def _source_edits(project, edits):
    if not isinstance(edits, list) or len(edits) > 8192:
        raise GXWFormatError('edits requires a bounded list of existing source commands')
    bound, seen = [], set()
    for edit in edits:
        if (not isinstance(edit, Mapping) or set(edit) != {'source_name', 'command'}
                or not isinstance(edit['command'], Mapping)):
            raise GXWFormatError('each source edit requires source_name and command')
        source = project.select(edit['source_name'])
        logical = source['source_name']
        if logical in seen:
            raise GXWFormatError('conflicting repeated source edit: ' + logical)
        seen.add(logical)
        command = deepcopy(dict(edit['command']))
        if any(key in command for key in ('native_save', 'data_base64', 'baseline', 'path')):
            raise GXWFormatError('offline edit commands cannot request native operations or another input')
        if command.get('source_name', logical) not in (logical, source['name']):
            raise GXWFormatError('command source differs from its bound edit target')
        bound.append((source, command))
    return bound


def _artifact_state(checks):
    for check in checks:
        if (check.get('status') in ('failed', 'unverified', 'incomplete')
                or check.get('syntax') in ('failed', 'unverified')
                or check.get('semantic') in ('failed', 'unverified')
                or any(finding.get('severity') in ('error', 'unverified')
                       for finding in check.get('findings', []))):
            return 'draft'
    return 'source_checked' if checks else 'draft'


def prepare_offline_edit(baseline: bytes, edits, *, declaration_edits=None,
                         save_policy='draft', expected_sources=None):
    """Prepare all commands in memory; validate their combined final context.

    Per-language planners keep ownership and record-reference checks active.
    Intermediate source checks are deferred until every caller and declaration
    edit is present. Only the final replacements are committed to the original
    container, so intermediate history/container allocations are not exported.
    """
    from .offline_authoring import (prepare_declaration_edits, prepare_existing_definition_write,
                                    check_project_draft, _label_context)
    from .source_header import source_payload_offset

    save_policy = _policy(save_policy)
    original = read_offline_project(baseline)
    from .project_metadata import existing_source_groups
    groups, _ = existing_source_groups(original.outer['projectdatalist.xml'], original.streams)
    if expected_sources is not None and (not isinstance(expected_sources, Mapping) or any(
            original.streams.get(name) != value for name, value in expected_sources.items())):
        raise GXWFormatError('stale or foreign offline transaction source')
    bound = _source_edits(original, edits)
    declarations = deepcopy(dict(declaration_edits or {})) if isinstance(
        declaration_edits, (Mapping, type(None))) else None
    if declarations is None:
        raise GXWFormatError('declaration_edits requires existing table patches')
    claimed_tables = set(declarations)
    for source, command in bound:
        if source['language'] == 'st' and command.get('declarations') is not None:
            table = groups[source['source_name']]['labels']
            if table in claimed_tables:
                raise GXWFormatError('conflicting declaration edits: ' + table)
            claimed_tables.add(table)
        if source['language'] == 'fbd':
            model = command.get('model')
            if not isinstance(model, Mapping):
                raise GXWFormatError('FBD edit requires its current object model')
            for table, patch in model.get('declaration_edits', {}).items():
                if table in claimed_tables:
                    raise GXWFormatError('conflicting declaration edits: ' + table)
                claimed_tables.add(table)
                declarations[table] = deepcopy(patch)

    working = bytes(baseline)
    if declarations:
        prepared, _ = prepare_declaration_edits(working, declarations,
                                                save_policy=save_policy, defer_checks=True)
        working = write_prepared_project(prepared).data

    # ST interface and declaration updates precede graph construction. Their
    # callers are checked once, below, against the complete transaction.
    for source, command in sorted(bound, key=lambda pair: pair[0]['language'] != 'st'):
        logical, language = source['source_name'], source['language']
        operation = command.pop('operation', 'edit')
        if language == 'st':
            allowed = {'edit', 'edit_st_program', 'edit_st_source', 'edit_st_fb', 'edit_st_function'}
            if operation not in allowed:
                raise GXWFormatError('offline ST transaction only edits an existing body or interface')
            if operation == 'edit':
                operation = 'edit_st_source'
            command.update(operation=operation, source_name=logical)
            prepared = prepare_existing_definition_write(working, command,
                save_policy=save_policy, defer_checks=True)
            working = write_prepared_project(prepared).data
        elif language == 'fbd':
            from .object_model import prepare_object_project, read_project_context
            if operation != 'edit' or set(command) - {'model', 'source_name'}:
                raise GXWFormatError('offline FBD transaction requires an existing object-model edit')
            model = deepcopy(dict(command['model']))
            if model.get('program') != logical:
                raise GXWFormatError('FBD model differs from its bound program')
            # Verify read-only projections against the explicitly bound base,
            # then let the combined draft supply its updated declarations.
            # Source offsets still identify the unchanged original graph body.
            projection = read_project_context(baseline, logical).object_model()
            for key in ('labels', 'unknown_record_count', 'issues', 'cpu'):
                if key in model and model[key] != projection.get(key):
                    raise GXWFormatError('FBD read-only projection differs from the bound base: ' + key)
                model.pop(key, None)
            model.pop('declaration_edits', None)
            prepared = prepare_object_project(model, baseline=working,
                save_policy=save_policy, defer_project_checks=True)
            working = write_prepared_project(prepared).data
        elif language == 'ladder':
            from .offline_project import edit_ladder_project
            if operation != 'edit' or set(command) - {'instruction_edits', 'source_name'}:
                raise GXWFormatError('offline Ladder transaction requires ordered instruction edits')
            working = edit_ladder_project(working, logical, command.get('instruction_edits', []),
                save_policy=save_policy, defer_checks=True).data
        elif language == 'sfc':
            from .sfc_pou import read_sfc_project_context
            from .sfc_offline import edit_sfc_program
            if operation != 'edit' or set(command) - {'graph_edits', 'child_edits', 'source_name'}:
                raise GXWFormatError('offline SFC transaction requires graph or internal child edits')
            context = read_sfc_project_context(working, logical)
            source_bytes, _ = edit_sfc_program(context, graph_edits=command.get('graph_edits'),
                                               child_edits=command.get('child_edits'), save_policy=save_policy)
            project = read_offline_project(working)
            working = write_project_replacements(working, {
                logical: (project.mapping[logical], context.source.raw, source_bytes)}).data
        else:
            raise GXWFormatError('source language has no offline editor: ' + logical)

    after = read_offline_project(working)
    if after.mapping != original.mapping or set(after.streams) != set(original.streams):
        raise GXWFormatError('offline transaction changed registered engineering objects')
    replacements = {name: (original.mapping[name], value, after.streams[name])
                    for name, value in original.streams.items() if after.streams[name] != value}
    drafts = {name: new for name, (_key, _old, new) in replacements.items()
              if name in groups}
    # An explicitly submitted no-op still requests checks of that source. Keep
    # this selection independent of the physical replacement set so unchanged
    # drafts retain diagnostics without refreshing saved compile results.
    drafts.update({source['source_name']: after.streams[source['source_name']]
                   for source, _command in bound})
    _, _, _, sources, _ = _label_context(working)
    changed_declarations = {name: replace(document, raw=original.streams[name])
                            for name, document in sources.declarations.items()
                            if name in replacements}
    from .structured_pou import parse_structured_pou
    programs = {name: parse_structured_pou(value, logical_name=name, preserve_unsupported_records=True)
                for name, value in drafts.items() if value[source_payload_offset(value)] == 208}
    checks = check_project_draft(baseline, source_replacements=drafts,
        declarations=changed_declarations, programs=programs, save_policy=save_policy)
    objects = [{'object': name, 'stream': key, 'old_length': len(old), 'new_length': len(new)}
               for name, (key, old, new) in replacements.items()]
    from .integrity import read_project_hdb
    prepared = PreparedProjectWrite(bytes(baseline), programs, changed_declarations,
        original.outer, read_project_hdb(original.outer).payloads, original.mapping, replacements, objects)
    report = {'operation': 'offline_project_edit', 'save_policy': save_policy,
              'artifact_state': _artifact_state(checks), 'source_checks': checks,
              'edited_sources': [{key: source[key] for key in ('source_name', 'language', 'unit_kind')}
                                 for source, _command in bound]}
    return prepared, report


def edit_offline_project(baseline: bytes, edits, *, declaration_edits=None,
                         device_comments=None, comment_table=None, save_policy='draft',
                         expected_sources=None):
    prepared, report = prepare_offline_edit(baseline, edits, declaration_edits=declaration_edits,
        save_policy=save_policy, expected_sources=expected_sources)
    result = write_prepared_project(prepared, device_comments=device_comments, comment_table=comment_table)
    # All projections below read the final bytes, after comment storage and
    # source-pending metadata have joined the same container transaction.
    read_offline_project(result.data)
    return replace(result, report={**result.report, **report})

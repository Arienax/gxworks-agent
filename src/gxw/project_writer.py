"""Template-backed GXW project writes with metadata and preservation checks.

Inputs are source-bound StructuredProgram and DeclarationDocument models.
Compiler output, timestamps, and unknown source fields remain untouched.
"""
from __future__ import annotations
from .project_metadata import project_stream_mapping

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .container_writer import replace_project_stream, validate_cfb_streams
from .declarations import (DeclarationDocument, edit_declarations, local_table_for,
                           parse_declarations, serialize_declarations, resolve_label)
from .models import GXWFormatError, StructuredProgram
from .project_metadata import (logical_mapping, synchronize_history, read_project_compile_options,
                               project_source_groups)
from .structured_pou import parse_structured_pou
from .structured_pou_writer import serialize_structured_pou
from .text_pou import StructuredTextSource, serialize_st_pou
from .sfc_pou import SfcSource, serialize_sfc_pou


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def binary_diff(before: bytes, after: bytes) -> list[dict]:
    """Exact, replayable byte changes; variable-size spans need not be minimal."""
    if before == after:
        return []
    if len(before) != len(after):
        start = 0
        while start < min(len(before), len(after)) and before[start] == after[start]:
            start += 1
        tail = 0
        while tail < min(len(before), len(after)) - start and before[-tail-1] == after[-tail-1]:
            tail += 1
        spans = [(start, len(before) - tail, len(after) - tail)]
    else:
        spans, start = [], None
        for index, (old, new) in enumerate(zip(before, after)):
            if old != new and start is None:
                start = index
            elif old == new and start is not None:
                spans.append((start, index, index))
                start = None
        if start is not None:
            spans.append((start, len(before), len(after)))
    return [{"old_offset": start, "new_offset": start,
             "old_length": end_old - start, "new_length": end_new - start,
             "before_hex": before[start:end_old].hex(), "after_hex": after[start:end_new].hex()}
            for start, end_old, end_new in spans]


def record_manifest(program: StructuredProgram) -> list[dict]:
    records = []
    for record in program.iter_records():
        item = {"offset": record.offset, "length": record.record_length,
                "sha256": sha256(record.raw)}
        if hasattr(record, "symbol"):
            item.update(kind=record.kind.value, symbol=record.symbol, type_name=record.type_name)
        elif hasattr(record, "start"):
            item.update(kind="wire", start=[record.start.x, record.start.y], end=[record.end.x, record.end.y])
        else:
            item.update(kind="unknown", record_class=record.record_class)
        records.append(item)
    return records


@dataclass(frozen=True)
class ProjectWriteResult:
    data: bytes
    report: dict

    def saved_object_name(self, logical_name):
        """Select the verified saved object after native child normalization."""
        for item in self.report.get('objects', []):
            if item.get('object') == logical_name:
                return item.get('saved_object', logical_name)
        for item in self.report.get('registered_object_renames', []):
            if item.get('before') == logical_name:
                return item['after']
        return logical_name


@dataclass(frozen=True)
class PreparedProjectWrite:
    """Validated, source-bound edits shared by container and native saves."""

    baseline: bytes
    programs: dict[str, StructuredProgram | StructuredTextSource | SfcSource]
    declarations: dict[str, DeclarationDocument]
    outer: dict[str, bytes]
    nested: dict[str, bytes]
    mapping: dict[str, str]
    replacements: dict[str, tuple[str, bytes, bytes]]
    objects: list[dict]

    @property
    def requires_native_save(self):
        if not any(old != new for _, old, new in self.replacements.values()):
            return False
        from .integrity import inspect_project_integrity
        capability = inspect_project_integrity(self.outer)
        if not capability['supported']:
            return True
        if not capability['size_changes_supported'] and any(
                len(old) != len(new) for _, old, new in self.replacements.values()):
            return True
        return False


def _declaration_manifest(document):
    return [{"offset": r.offset, "length": len(r.raw), "sha256": sha256(r.raw),
             "kind": "declaration", "symbol": r.name, "type_name": r.data_type,
             "record_id": r.record_id, "class_code": r.class_code} for r in document.rows]


def _bind_function_blocks(programs, declarations, mapping, payloads, source_groups=None):
    """Resolve existing local/global instances or add a local declaration.

    This is explicit policy for generation, not an inferred compiler repair.
    Unused declarations and non-FB rows are never removed or reclassified.
    """
    documents = dict(declarations)
    for logical in mapping:
        if logical.endswith((".lh", ".gh")) and logical not in documents and mapping[logical] in payloads:
            documents[logical] = parse_declarations(payloads[mapping[logical]], logical_name=logical)
    changed = dict(declarations)
    project_metadata = [payloads[mapping[name]] for name in mapping if name.endswith('.prj') and mapping[name] in payloads]
    hiding = None
    if len(project_metadata) == 1:
        try:
            hiding = read_project_compile_options(project_metadata[0])['global_variable_hiding']
        except GXWFormatError:
            pass  # Preserve unsupported metadata; ambiguous names cannot bind.
    for logical, program in programs.items():
        local_name = local_table_for(documents, logical, source_groups)
        instances = {}
        for node in program.nodes:
            if node.kind.value != "function_block":
                continue
            key = node.symbol.casefold()
            if key in instances and instances[key].type_name != node.type_name:
                raise GXWFormatError(f"FB instance has conflicting types: {node.symbol}")
            instances[key] = node
        for key, node in instances.items():
            if local_name not in documents:
                raise GXWFormatError(f"missing local declaration table: {local_name}")
            binding = resolve_label(documents, logical, node.symbol, global_variable_hiding=hiding,
                                    local_table=local_name)
            target = binding[0] if binding else local_name
            if binding and binding[1].type_code not in (0, 15):
                raise GXWFormatError(f"FB instance conflicts with a variable: {node.symbol}")
            if binding and binding[1].data_type == node.type_name:
                continue
            doc = edit_declarations(documents[target], upserts=[{
                "name": node.symbol, "data_type": node.type_name, "kind": "function_block"}])
            documents[target] = changed[target] = doc
    return changed


def prepare_project_write(baseline: bytes, programs: StructuredProgram | Mapping[str, StructuredProgram] | None = None,
                          *, declarations: Mapping[str, DeclarationDocument] | None = None,
                          sync_fb_declarations: bool = False) -> PreparedProjectWrite:
    """Validate edits without choosing how the native container will be saved.

    A source-bound model prevents edits based on a different/stale project being
    applied silently. Multiple POU replacements form a single in-memory write.
    """
    if isinstance(programs, StructuredProgram):
        programs = {programs.logical_name: programs}
    programs, declarations = dict(programs or {}), dict(declarations or {})
    if not programs and not declarations:
        raise GXWFormatError("no Program.pou or declaration model was supplied")
    outer_payloads = validate_cfb_streams(baseline)
    for name in ("_hdb", "projectdatalist.xml", "history.xml"):
        if name not in outer_payloads:
            raise GXWFormatError(f"required GXW stream is missing: {name}")
    mapping = project_stream_mapping(outer_payloads)
    hdb = outer_payloads["_hdb"]
    from .integrity import read_project_hdb
    nested_payloads = read_project_hdb(outer_payloads).payloads
    if sync_fb_declarations:
        declarations = _bind_function_blocks(programs, declarations, mapping, nested_payloads,
                                             project_source_groups(outer_payloads['projectdatalist.xml'])[0])
    replacements, objects = {}, []
    for logical, model in programs.items():
        if logical != model.logical_name or not logical.endswith((".pou", ".lnb")):
            raise GXWFormatError("model/key must name the same Program.pou")
        if logical not in mapping or mapping[logical] not in nested_payloads:
            raise GXWFormatError(f"unresolved Program.pou: {logical}")
        stream = mapping[logical]
        original = nested_payloads[stream]
        if model.raw != original:
            raise GXWFormatError(f"stale or foreign model source: {logical}")
        parsed = parse_structured_pou(original, logical_name=logical, preserve_unsupported_records=True)
        # Guarantee opaque source bytes survive a no-op serialization. This
        # rejects parser-accepted layouts that the serializer would normalize.
        if serialize_structured_pou(parsed) != original:
            raise GXWFormatError(f"source is not losslessly serializable: {logical}")
        new = serialize_structured_pou(model)
        rebuilt = parse_structured_pou(new, logical_name=logical, preserve_unsupported_records=True)
        replacements[logical] = (stream, original, new)
        objects.append({"object": logical, "stream": stream,
                        "old_length": len(original), "new_length": len(new),
                        "before_sha256": sha256(original), "after_sha256": sha256(new),
                        "offset_space": "logical Program.pou bytes",
                        "binary_changes": binary_diff(original, new),
                        "records_before": record_manifest(parsed), "records_after": record_manifest(rebuilt)})
    for logical, document in declarations.items():
        if logical != document.logical_name or logical not in mapping or mapping[logical] not in nested_payloads:
            raise GXWFormatError(f"unresolved declaration model: {logical}")
        stream = mapping[logical]
        original = nested_payloads[stream]
        if document.raw != original:
            raise GXWFormatError(f"stale or foreign declaration source: {logical}")
        source_kind = 240 if document.owner_sfc_block_number is not None else None
        parsed = parse_declarations(original, logical_name=logical, source_kind=source_kind)
        new = serialize_declarations(document)
        rebuilt = parse_declarations(new, logical_name=logical, source_kind=source_kind)
        replacements[logical] = (stream, original, new)
        objects.append({"object": logical, "stream": stream,
                        "old_length": len(original), "new_length": len(new),
                        "before_sha256": sha256(original), "after_sha256": sha256(new),
                        "offset_space": "logical declaration stream bytes",
                        "binary_changes": binary_diff(original, new),
                        "records_before": _declaration_manifest(parsed), "records_after": _declaration_manifest(rebuilt)})
    return PreparedProjectWrite(bytes(baseline), programs, declarations, outer_payloads,
                                nested_payloads, mapping, replacements, objects)


def prepare_st_project_write(baseline: bytes, logical_name: str, source: StructuredTextSource,
                             text: str) -> PreparedProjectWrite:
    """Bind one existing ST text edit to its original native project and POU.

    Labels and task configuration are preserved. Native save and compilation
    remain separate operations through the existing workspace adapter.
    """
    outer = validate_cfb_streams(baseline)
    if not all(name in outer for name in ('_hdb', 'projectdatalist.xml', 'history.xml')):
        raise GXWFormatError('ST edit requires an existing native GX Works2 project')
    mapping = project_stream_mapping(outer)
    from .integrity import read_project_hdb
    nested = read_project_hdb(outer).payloads
    from .project_metadata import existing_source_groups
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    groups, issues = existing_source_groups(outer['projectdatalist.xml'], streams)
    if (logical_name not in groups or logical_name not in mapping
            or mapping[logical_name] not in nested):
        raise GXWFormatError('ST edit requires a registered unambiguous source and local table')
    local = groups[logical_name]['labels']
    if local not in mapping or mapping[local] not in nested:
        raise GXWFormatError('ST edit requires the current paired local declaration table')
    original = nested[mapping[logical_name]]
    if source.raw != original:
        raise GXWFormatError('stale or foreign ST model source: ' + logical_name)
    replacement = serialize_st_pou(source, text)
    return PreparedProjectWrite(bytes(baseline), {logical_name: source}, {}, outer, nested, mapping,
        {logical_name: (mapping[logical_name], original, replacement)},
        [{'object': logical_name, 'stream': mapping[logical_name], 'old_length': len(original),
          'new_length': len(replacement), 'offset_space': 'logical ST source stream bytes',
          'binary_changes': binary_diff(original, replacement)}])


def prepare_sfc_project_write(baseline: bytes, logical_name: str, source: SfcSource,
                             child_tokens: Mapping[tuple[str, str], bytes]) -> PreparedProjectWrite:
    """Bind existing SFC child edits to the current project source and labels.

    Graphs, registrations, other children and declarations retain their raw
    bytes. Saving follows the existing project's container/native envelope
    policy; preparing source bytes does not establish compilation or checking.
    """
    outer = validate_cfb_streams(baseline)
    if not all(name in outer for name in ('_hdb', 'projectdatalist.xml', 'history.xml', 'Project.gd2')):
        raise GXWFormatError('SFC edit requires an existing native GX Works2 project')
    mapping = project_stream_mapping(outer)
    from .integrity import read_project_hdb
    nested = read_project_hdb(outer).payloads
    groups, issues = project_source_groups(outer['projectdatalist.xml'], folder_type=8)
    if (logical_name not in groups or logical_name not in mapping
            or mapping[logical_name] not in nested):
        raise GXWFormatError('SFC edit requires a registered unambiguous source and local table')
    local = groups[logical_name]['labels']
    if local not in mapping or mapping[local] not in nested:
        raise GXWFormatError('SFC edit requires the current paired local declaration table')
    original = nested[mapping[logical_name]]
    if source.raw != original:
        raise GXWFormatError('stale or foreign SFC model source: ' + logical_name)
    replacement = serialize_sfc_pou(source, child_tokens)
    return PreparedProjectWrite(bytes(baseline), {logical_name: source}, {}, outer, nested, mapping,
        {logical_name: (mapping[logical_name], original, replacement)},
        [{'object': logical_name, 'stream': mapping[logical_name], 'old_length': len(original),
          'new_length': len(replacement), 'offset_space': 'logical SFC source stream bytes',
          'binary_changes': binary_diff(original, replacement),
          'preserved_source_group_gaps': issues}])


def write_prepared_project(prepared: PreparedProjectWrite, *, device_comments=None, comment_table=None) -> ProjectWriteResult:
    """Save the prepared edits through the common offline transaction."""
    if prepared.outer != validate_cfb_streams(prepared.baseline):
        raise GXWFormatError('stale or foreign prepared outer context')
    return write_project_replacements(prepared.baseline, prepared.replacements,
                                      objects=prepared.objects, device_comments=device_comments, comment_table=comment_table)


def write_project_replacements(baseline: bytes, replacements: Mapping[str, tuple[str, bytes, bytes]],
                               *, objects=None, invalidate=True, device_comments=None, comment_table=None) -> ProjectWriteResult:
    """One offline transaction for existing source, declaration and comment streams.

    Existing format integrity fields are rebuilt only after all sources/history
    are final. No native module or compiler is involved in this save operation.
    """
    from .integrity import read_project_hdb, replace_hdb_streams, rebuild_project_integrity
    from .project_metadata import (mark_observed_source_compile_pending, read_project_system_variables,
                                   native_fbd_pointer_initialization)
    import struct
    baseline = bytes(baseline)
    outer_payloads = validate_cfb_streams(baseline)
    envelope = read_project_hdb(outer_payloads)
    mapping = project_stream_mapping(outer_payloads)
    nested_payloads = envelope.payloads
    replacements = dict(replacements)
    if device_comments is not None:
        from .offline_project import read_offline_project, prepare_comment_replacements
        comments = prepare_comment_replacements(read_offline_project(baseline), device_comments, comment_table)
        if set(comments) & set(replacements):
            raise GXWFormatError('conflicting comment stream replacements')
        replacements.update(comments)
    for logical, (stream, old, new) in replacements.items():
        if mapping.get(logical) != stream or nested_payloads.get(stream) != old:
            raise GXWFormatError('stale or foreign stream replacement: ' + logical)
        if invalidate and old != new and logical.endswith(('.pou', '.lh', '.gh', '.lnb', '.lnl')):
            new = mark_observed_source_compile_pending(new, logical_name=logical)
        replacements[logical] = (stream, old, bytes(new))
    # The five independently measured L FBD sources need this missing range.
    # Keep explicit/partial configuration and all other CPUs byte-identical.
    from .source_header import source_payload_offset
    metadata = [(name, stream) for name, stream in mapping.items() if name.endswith('.prj')]
    changed_fbd = invalidate and any(old != new and name.endswith('.pou') and
                      old[source_payload_offset(old)] == 208
                      for name, (_stream, old, new) in replacements.items())
    if changed_fbd and len(metadata) == 1:
        name, stream = metadata[0]
        original = nested_payloads[stream]
        update = native_fbd_pointer_initialization(original, 208)
        if update is not None:
            offset = read_project_system_variables(original)['offset'] + 64
            value = bytearray(original)
            struct.pack_into('<II', value, offset, *update['after'])
            replacements[name] = (stream, original, bytes(value))
    changed = {name: row for name, row in replacements.items() if row[1] != row[2]}
    if not changed:
        return ProjectWriteResult(baseline, {'schema_version': 1, 'operation': 'offline_source_save',
            'write_backend': 'offline', 'compile_state': 'unchanged',
            'objects': list(objects or []), 'metadata_changes': [], 'allocations': [],
            'validation': {'parser': 'passed', 'all_stream_payloads': 'passed', 'offline_write': 'passed',
                           'compile_state': 'unchanged', 'native_save': 'not_run', 'gxworks_compile': 'not_run'}})
    replacements = changed
    history, metadata, preserved = synchronize_history(outer_payloads["history.xml"], replacements, current_mapping=mapping)
    hdb, allocations = replace_hdb_streams(envelope, {stream: new for stream, old, new in replacements.values()})
    expected_nested = dict(nested_payloads)
    expected_nested.update({stream: new for stream, old, new in replacements.values()})
    expected_outer = rebuild_project_integrity(envelope, dict(outer_payloads, **{'history.xml': history}), hdb)
    result = baseline
    for name, new in expected_outer.items():
        if new == outer_payloads[name]:
            continue
        result, mode = replace_project_stream(result, name, new)
        allocations.append({"layer": "outer", "stream": name, "mode": mode})
    if validate_cfb_streams(result) != expected_outer:
        raise GXWFormatError("outer stream preservation/replacement check failed")
    if read_project_hdb(expected_outer).payloads != expected_nested:
        raise GXWFormatError('nested stream preservation/replacement check failed')
    # Verify the metadata updater is stable against the newly serialized source.
    current = {name: (stream, new, new) for name, (stream, old, new) in replacements.items()}
    if synchronize_history(history, current, current_mapping=mapping)[0] != history:
        raise GXWFormatError("post-write history consistency failed")
    return ProjectWriteResult(result, {
        "schema_version": 1, "baseline_sha256": sha256(baseline), "output_sha256": sha256(result),
        "old_length": len(baseline), "new_length": len(result), "objects": list(objects or []),
        'operation': 'offline_source_save', 'write_backend': 'offline', 'compile_state': 'stale',
        'invalidated_results': {'scope': 'project_version', 'compilation': 'stale',
                                'addresses': 'stale', 'diagnostics': 'stale'},
        'modified_objects': list(replacements),
        "metadata_changes": metadata, "preserved_metadata": preserved, "allocations": allocations,
        "validation": {"parser": "passed", "all_stream_payloads": "passed",
                       'offline_write': 'passed', 'native_save': 'not_run', 'compile_state': 'stale',
                       'compiled_resources': 'preserved; not evidence of the changed source',
                       "gxworks_open": "not_run", "gxworks_compile": "not_run", "gxworks_save_reload": "not_run"},
        "limitations": ["Existing registered source and declaration tables only",
                        "GX Works2 compilation is a separate operation"]})


def build_gxw_project(baseline: bytes, programs: StructuredProgram | Mapping[str, StructuredProgram] | None = None,
                      *, declarations: Mapping[str, DeclarationDocument] | None = None,
                      sync_fb_declarations: bool = False) -> ProjectWriteResult:
    return write_prepared_project(prepare_project_write(
        baseline, programs, declarations=declarations, sync_fb_declarations=sync_fb_declarations))


def write_new_file(path: Path, data: bytes) -> None:
    """Publish verified bytes without overwriting an existing artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gxw-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic no-clobber publication on the destination filesystem.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_gxw_project(source: str | Path, output: str | Path,
                      programs: StructuredProgram | Mapping[str, StructuredProgram] | None = None,
                      *, declarations: Mapping[str, DeclarationDocument] | None = None,
                      sync_fb_declarations: bool = False) -> ProjectWriteResult:
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.exists():
        raise GXWFormatError("output must be a new file distinct from the baseline")
    baseline = source.read_bytes()
    result = build_gxw_project(baseline, programs, declarations=declarations,
                              sync_fb_declarations=sync_fb_declarations)
    if source.read_bytes() != baseline:
        raise GXWFormatError("baseline changed during project build")
    write_new_file(output, result.data)
    return result

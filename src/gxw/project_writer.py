"""Template-backed GXW project writes with metadata and preservation checks.

Inputs are source-bound StructuredProgram and DeclarationDocument models.
Compiler output, timestamps, and unknown source fields remain untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .container_writer import replace_project_stream, validate_cfb_streams
from .declarations import DeclarationDocument, edit_declarations, parse_declarations, serialize_declarations
from .models import GXWFormatError, StructuredProgram
from .project_metadata import logical_mapping, synchronize_history
from .structured_pou import parse_structured_pou
from .structured_pou_writer import serialize_structured_pou


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


def _declaration_manifest(document):
    return [{"offset": r.offset, "length": len(r.raw), "sha256": sha256(r.raw),
             "kind": "declaration", "symbol": r.name, "type_name": r.data_type,
             "record_id": r.record_id, "class_code": r.class_code} for r in document.rows]


def _bind_function_blocks(programs, declarations, mapping, payloads):
    """Resolve existing local/global instances or add a local declaration.

    This is explicit policy for generation, not an inferred compiler repair.
    Unused declarations and non-FB rows are never removed or reclassified.
    """
    documents = dict(declarations)
    for logical in mapping:
        if logical.endswith((".Labels.lh", ".gh")) and logical not in documents:
            documents[logical] = parse_declarations(payloads[mapping[logical]], logical_name=logical)
    changed = dict(declarations)
    for logical, program in programs.items():
        local_name = logical.removesuffix(".Program.pou") + ".Labels.lh"
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
            local = documents[local_name]
            hits = [(local_name, r) for r in local.rows if r.name.casefold() == key]
            hits += [(name, row) for name, doc in documents.items() if doc.scope == "global"
                     for row in doc.rows if row.name.casefold() == key]
            if len(hits) > 1:
                raise GXWFormatError(f"ambiguous FB declaration: {node.symbol}")
            target = hits[0][0] if hits else local_name
            if hits and hits[0][1].type_code != 15:
                raise GXWFormatError(f"FB instance conflicts with a variable: {node.symbol}")
            if hits and hits[0][1].type_reference == node.type_name and hits[0][1].data_type == node.type_name:
                continue
            doc = edit_declarations(documents[target], upserts=[{
                "name": node.symbol, "data_type": node.type_name, "kind": "function_block"}])
            documents[target] = changed[target] = doc
    return changed


def build_gxw_project(baseline: bytes, programs: StructuredProgram | Mapping[str, StructuredProgram] | None = None,
                      *, declarations: Mapping[str, DeclarationDocument] | None = None,
                      sync_fb_declarations: bool = False) -> ProjectWriteResult:
    """Build and verify both CFB layers before exposing output bytes.

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
    mapping = logical_mapping(outer_payloads["projectdatalist.xml"])
    hdb = outer_payloads["_hdb"]
    nested_payloads = validate_cfb_streams(hdb)
    if sync_fb_declarations:
        declarations = _bind_function_blocks(programs, declarations, mapping, nested_payloads)
    replacements, objects = {}, []
    for logical, model in programs.items():
        if logical != model.logical_name or not logical.endswith(".Program.pou"):
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
        parsed = parse_declarations(original, logical_name=logical)
        new = serialize_declarations(document)
        rebuilt = parse_declarations(new, logical_name=logical)
        replacements[logical] = (stream, original, new)
        objects.append({"object": logical, "stream": stream,
                        "old_length": len(original), "new_length": len(new),
                        "before_sha256": sha256(original), "after_sha256": sha256(new),
                        "offset_space": "logical declaration stream bytes",
                        "binary_changes": binary_diff(original, new),
                        "records_before": _declaration_manifest(parsed), "records_after": _declaration_manifest(rebuilt)})
    history, metadata, preserved = synchronize_history(outer_payloads["history.xml"], replacements, current_mapping=mapping)
    allocations = []
    for logical, (stream, old, new) in replacements.items():
        if old == new:
            continue
        hdb, mode = replace_project_stream(hdb, stream, new)
        allocations.append({"layer": "nested", "object": logical, "stream": stream, "mode": mode})
    expected_nested = dict(nested_payloads)
    expected_nested.update({stream: new for stream, old, new in replacements.values()})
    if validate_cfb_streams(hdb) != expected_nested:
        raise GXWFormatError("nested stream preservation/replacement check failed")
    result = baseline
    for name, new in (("_hdb", hdb), ("history.xml", history)):
        if new == outer_payloads[name]:
            continue
        result, mode = replace_project_stream(result, name, new)
        allocations.append({"layer": "outer", "stream": name, "mode": mode})
    expected_outer = dict(outer_payloads, _hdb=hdb)
    expected_outer["history.xml"] = history
    if validate_cfb_streams(result) != expected_outer:
        raise GXWFormatError("outer stream preservation/replacement check failed")
    # Verify the metadata updater is stable against the newly serialized source.
    current = {name: (stream, new, new) for name, (stream, old, new) in replacements.items()}
    if synchronize_history(history, current, current_mapping=mapping)[0] != history:
        raise GXWFormatError("post-write history consistency failed")
    return ProjectWriteResult(result, {
        "schema_version": 1, "baseline_sha256": sha256(baseline), "output_sha256": sha256(result),
        "old_length": len(baseline), "new_length": len(result), "objects": objects,
        "metadata_changes": metadata, "preserved_metadata": preserved, "allocations": allocations,
        "validation": {"parser": "passed", "all_stream_payloads": "passed",
                       "gxworks_open": "not_run", "gxworks_compile": "not_run", "gxworks_save_reload": "not_run"},
        "limitations": ["Template-backed supported StructuredProgram layouts only",
                        "Compiler state preserved; GX Works2 compilation is a separate required gate",
                        "Declaration edits use existing local/global tables; adding new POUs or table streams is unsupported"]})


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

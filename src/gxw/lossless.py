"""Raw-preserved GXW inspection and a bounded, experimental source patch.

This is an overlay on original bytes, not a replacement semantic object model.
Unknown bytes, allocation slack, directory metadata and unsupported streams live
in the immutable source. Byte preservation is distinct from native validity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from .container import CompoundFile
from .container_writer import replace_stream_within_allocation, replace_project_stream, validate_cfb_streams
from .models import GXWFormatError, NodeKind, StructuredNode, StructuredWire
from .project_metadata import logical_mapping, synchronize_history
from .project_writer import ProjectWriteResult, binary_diff
from .structured_pou import parse_structured_pou
from .structured_pou_writer import serialize_structured_node
from .token_pou import parse_token_pou


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Extent:
    """Payload provenance: stream offset -> immediate container byte offset."""
    logical_offset: int
    container_offset: int
    length: int


def map_range(extents: tuple[Extent, ...], start: int, length: int) -> tuple[Extent, ...]:
    if start < 0 or length < 0:
        raise GXWFormatError("negative provenance range")
    end, result = start + length, []
    for span in extents:
        lo, hi = max(start, span.logical_offset), min(end, span.logical_offset + span.length)
        if lo < hi:
            result.append(Extent(lo - start, span.container_offset + lo - span.logical_offset, hi - lo))
    if sum(s.length for s in result) != length:
        raise GXWFormatError("unbacked provenance range")
    return tuple(result)


def stream_extents(cfb: CompoundFile, entry) -> tuple[Extent, ...]:
    """Map mini/regular payloads without interpreting their contents or slack."""
    if not entry.stream_size:
        return ()
    mini = entry.stream_size < cfb.mini_stream_cutoff
    chain = cfb._walk_chain(entry.start_sector, cfb._minifat if mini else cfb._fat)
    unit = cfb.mini_sector_size if mini else cfb.sector_size
    if len(chain) * unit < entry.stream_size:
        raise GXWFormatError("truncated stream allocation")
    root = ()
    if mini:
        root_chain = cfb._walk_chain(cfb.root_entry.start_sector, cfb._fat)
        root = tuple(Extent(i * cfb.sector_size, (sid + 1) * cfb.sector_size,
                            min(cfb.sector_size, cfb.root_entry.stream_size - i * cfb.sector_size))
                     for i, sid in enumerate(root_chain) if i * cfb.sector_size < cfb.root_entry.stream_size)
    result = []
    for i, sid in enumerate(chain):
        start = i * unit
        if start >= entry.stream_size:
            break
        length = min(unit, entry.stream_size - start)
        if mini:
            result.extend(Extent(start + s.logical_offset, s.container_offset, s.length)
                          for s in map_range(root, sid * unit, length))
        else:
            result.append(Extent(start, (sid + 1) * unit, length))
    merged = []
    for span in result:
        if merged and (merged[-1].logical_offset + merged[-1].length == span.logical_offset
                       and merged[-1].container_offset + merged[-1].length == span.container_offset):
            last = merged.pop()
            merged.append(Extent(last.logical_offset, last.container_offset, last.length + span.length))
        else:
            merged.append(span)
    return tuple(merged)


@dataclass(frozen=True)
class RawRegion:
    offset: int
    raw: bytes
    kind: str
    handling: str = "opaque-preserved"


@dataclass(frozen=True)
class ProgramImage:
    raw: bytes
    layout: str
    regions: tuple[RawRegion, ...]
    projection: object | None = None
    diagnostics: tuple[str, ...] = ()

    def reconstruct(self) -> bytes:
        cursor, chunks = 0, []
        for region in self.regions:
            if region.offset != cursor or region.raw != self.raw[cursor:cursor + len(region.raw)]:
                raise GXWFormatError("lossless region partition is not source-bound")
            chunks.append(region.raw)
            cursor += len(region.raw)
        if cursor != len(self.raw):
            raise GXWFormatError("lossless region partition does not cover the source")
        return b"".join(chunks)


def inspect_program(raw: bytes, *, logical_name: str = "<Program.pou>") -> ProgramImage:
    raw = bytes(raw)
    diagnostics = []
    try:
        program = parse_structured_pou(raw, logical_name=logical_name, preserve_unsupported_records=True)
        regions = [RawRegion(0, raw[:71], "program-prefix")]
        for block, records in program.block_records():
            regions.append(RawRegion(block.offset, block.raw_header, "block-header", "partially-decoded"))
            for record in records:
                known = isinstance(record, StructuredWire) or (
                    isinstance(record, StructuredNode) and record.kind != NodeKind.UNKNOWN)
                regions.append(RawRegion(record.offset, record.raw,
                    "node" if isinstance(record, StructuredNode) else "wire" if isinstance(record, StructuredWire) else "record",
                    "partially-decoded" if known else "opaque-preserved"))
        regions.append(RawRegion(len(raw) - len(program.trailer), program.trailer, "trailer"))
        return ProgramImage(bytes(raw), "structured", tuple(regions), program)
    except GXWFormatError as exc:
        diagnostics.append("structured: " + str(exc))
    try:
        program = parse_token_pou(raw)
        regions = [RawRegion(0, raw[:79], "program-prefix")]
        regions.extend(RawRegion(t.offset, t.raw, "token", "opaque-preserved" if
                                 t.annotation()["kind"] == "opaque" else "partially-decoded") for t in program.tokens)
        regions.append(RawRegion(len(raw) - 24, raw[-24:], "trailer"))
        return ProgramImage(bytes(raw), "ladder-token", tuple(regions), program)
    except GXWFormatError as exc:
        diagnostics.append("ladder-token: " + str(exc))
    return ProgramImage(bytes(raw), "unsupported", (RawRegion(0, bytes(raw), "stream"),),
                        diagnostics=tuple(diagnostics))


@dataclass(frozen=True)
class StreamImage:
    layer: str
    directory_index: int
    name: str
    logical_name: str | None
    raw: bytes | None
    extents: tuple[Extent, ...]
    error: str | None = None


@dataclass(frozen=True)
class ProjectImage:
    raw: bytes
    streams: tuple[StreamImage, ...]
    diagnostics: tuple[str, ...]
    unbacked_mappings: tuple[tuple[str, str, str], ...]

    @property
    def sha256(self) -> str:
        return sha256(self.raw)

    def reconstruct(self) -> bytes:
        # A source replay preserves directory slots, allocation slack and even
        # broken containers. It is NOT a semantic/container reconstruction proof.
        return self.raw


def inspect_project(raw: bytes) -> ProjectImage:
    raw = bytes(raw)
    streams, diagnostics, unbacked = [], [], []
    try:
        outer = CompoundFile(raw)
    except (ValueError, KeyError, IndexError) as exc:
        return ProjectImage(bytes(raw), (), ("outer: " + str(exc),), ())

    def inventory(cfb, layer, mapping=None):
        names = {v: k for k, v in (mapping or {}).items()}
        for entry in cfb.iter_streams():
            try:
                payload = cfb.read_entry(entry)
                if len(payload) != entry.stream_size:
                    raise GXWFormatError("payload shorter than directory size")
                spans = stream_extents(cfb, entry)
                if b"".join(cfb._data[s.container_offset:s.container_offset + s.length] for s in spans) != payload:
                    raise GXWFormatError("physical provenance disagrees with payload")
                streams.append(StreamImage(layer, entry.index, entry.name, names.get(entry.name), payload, spans))
            except (ValueError, KeyError, IndexError) as exc:
                streams.append(StreamImage(layer, entry.index, entry.name, names.get(entry.name), None, (), str(exc)))

    inventory(outer, "outer")
    mapping = {}
    try:
        mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    except (ValueError, KeyError, IndexError) as exc:
        diagnostics.append("mapping: " + str(exc))
    try:
        nested = CompoundFile(outer.read_stream("_hdb"))
        inventory(nested, "nested", mapping)
        for logical, name in mapping.items():
            if not nested.find_streams(name):
                entries = [e for e in nested.directory_entries if e.name == name]
                status = "storage" if len(entries) == 1 and entries[0].object_type == 1 else "unresolved"
                unbacked.append((logical, name, status))
    except (ValueError, KeyError, IndexError) as exc:
        diagnostics.append("nested: " + str(exc))
    return ProjectImage(bytes(raw), tuple(streams), tuple(diagnostics), tuple(unbacked))


def patch_structured_symbol_equal_size(source: bytes, *, expected_sha256: str,
                                      logical_name: str, node_offset: int,
                                      old_symbol: str, new_symbol: str) -> ProjectWriteResult:
    """Experimental, source-bound edit beside opaque records; no reserialization.

    Only ordinary contacts/coils/value terminals can be targeted. FB declaration
    binding, variable-length edits, new records and token programs stay with their
    existing supported paths (or unsupported). Native compile/reopen is separate.
    """
    if sha256(source) != expected_sha256:
        raise GXWFormatError("stale or foreign lossless source hash")
    if not logical_name.endswith(".Program.pou") or not new_symbol or "\0" in new_symbol:
        raise GXWFormatError("invalid program or symbol")
    old_text = (old_symbol + "\0").encode("utf-16le")
    new_text = (new_symbol + "\0").encode("utf-16le")
    if len(old_text) != len(new_text):
        raise GXWFormatError("lossless patch requires equal UTF-16 byte length")
    outer_payloads = validate_cfb_streams(source)
    mapping = logical_mapping(outer_payloads["projectdatalist.xml"])
    if logical_name not in mapping:
        raise GXWFormatError("unresolved patch program")
    stream = mapping[logical_name]
    hdb = outer_payloads["_hdb"]
    nested_payloads = validate_cfb_streams(hdb)
    raw = nested_payloads[stream]
    program = parse_structured_pou(raw, logical_name=logical_name, preserve_unsupported_records=True)
    targets = [n for n in program.nodes if n.offset == node_offset and n.symbol == old_symbol]
    if len(targets) != 1 or targets[0].kind not in (
            NodeKind.CONTACT, NodeKind.CONTACT_NC, NodeKind.COIL, NodeKind.INPUT, NodeKind.OUTPUT):
        raise GXWFormatError("patch target is not a recognized ordinary node")
    node = targets[0]
    if serialize_structured_node(node) != node.raw:
        raise GXWFormatError("target node is not losslessly understood")
    start = node.offset + 16
    if raw[start:start + len(old_text)] != old_text:
        raise GXWFormatError("symbol provenance mismatch")
    new = raw[:start] + new_text + raw[start + len(old_text):]
    after = parse_structured_pou(new, logical_name=logical_name, preserve_unsupported_records=True)
    if next(n.symbol for n in after.nodes if n.offset == node.offset) != new_symbol:
        raise GXWFormatError("patch target readback disagrees")
    result = _patch_program_range_equal_size(source, logical_name, start, old_text, new_text)
    return ProjectWriteResult(result.data, dict(result.report, node_offset=node_offset,
                                               old_symbol=old_symbol, new_symbol=new_symbol))


def patch_token_constant(source: bytes, *, expected_sha256: str,
                         logical_name: str, token_offset: int,
                         old_value: int, new_value: int, allow_resize: bool = False) -> ProjectWriteResult:
    """Edit one numeric constant without regenerating other tokens or .res.

    Exact source identity, operand binding and constant type are required.
    Stored width is retained unless allow_resize explicitly permits growth.
    Only the source POU and its existing history fields change. GX Works2 must
    compile the result before any derived representation is considered fresh.
    """
    from .token_listing import decode_token_listing

    if sha256(source) != expected_sha256:
        raise GXWFormatError("stale or foreign lossless source hash")
    if type(old_value) is not int or type(new_value) is not int:
        raise GXWFormatError("constant values must be integers")
    outer = CompoundFile(source)
    mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    if logical_name not in mapping or not logical_name.endswith(".Program.pou"):
        raise GXWFormatError("unresolved token program")
    raw = CompoundFile(outer.read_stream("_hdb")).read_stream(mapping[logical_name])
    listing = decode_token_listing(raw)
    targets = [t for instruction in listing.instructions for t in instruction.tokens[1:]
               if t.offset == token_offset]
    if len(targets) != 1:
        raise GXWFormatError("constant target is not bound to a decoded instruction")
    token = targets[0]
    annotation = token.annotation()
    if annotation.get("constant_type") not in ("K", "H") or annotation["numeric_value"] != old_value:
        raise GXWFormatError("constant target identity mismatch")
    width = len(token.raw) - 3
    signed = annotation["constant_type"] == "K" and width * 8 == annotation["width_bits"]
    try:
        value = new_value.to_bytes(width, "little", signed=signed)
    except OverflowError as exc:
        if not allow_resize:
            raise GXWFormatError("new value does not fit the original constant encoding") from exc
        maximum_width = annotation["width_bits"] // 8
        try:
            full = new_value.to_bytes(maximum_width, "little", signed=annotation["constant_type"] == "K")
        except OverflowError as overflow:
            raise GXWFormatError("new value exceeds the original constant type") from overflow
        value = full if new_value < 0 else full.rstrip(b"\0") or b"\0"
    if len(value) == width:
        result = _patch_program_range_equal_size(source, logical_name, token_offset + 2, token.raw[2:-1], value)
    else:
        size = len(value) + 3
        replacement = bytes((size, token.raw[1])) + value + bytes((size,))
        updated = bytearray(raw[:token_offset] + replacement + raw[token_offset + len(token.raw):])
        struct.pack_into("<II", updated, 55, len(updated) - 83, len(updated) - 83)
        updated = bytes(updated)
        before_tokens, after_tokens = parse_token_pou(raw).tokens, parse_token_pou(updated).tokens
        index = next(i for i, t in enumerate(before_tokens) if t.offset == token_offset)
        if ([t.raw for t in before_tokens[:index]] != [t.raw for t in after_tokens[:index]] or
                [t.raw for t in before_tokens[index + 1:]] != [t.raw for t in after_tokens[index + 1:]] or
                after_tokens[index].annotation().get("numeric_value") != new_value):
            raise GXWFormatError("token resize changed another record or failed readback")
        result = _replace_token_program(source, logical_name, raw, updated)
        result.report["validation"]["all_other_token_bytes"] = "byte-identical (offsets may shift)"
    return ProjectWriteResult(result.data, dict(result.report, token_offset=token_offset,
        old_value=old_value, new_value=new_value, constant_type=annotation["constant_type"],
        derived_resource_policy="preserve original .res; not fresh until GX Works2 compilation"))


def _replace_token_program(source: bytes, logical_name: str, raw: bytes, new: bytes) -> ProjectWriteResult:
    """Use the established allocator for resized token source, preserving other payloads."""
    outer = validate_cfb_streams(source)
    mapping = logical_mapping(outer["projectdatalist.xml"])
    stream = mapping[logical_name]
    nested = validate_cfb_streams(outer["_hdb"])
    if nested[stream] != raw:
        raise GXWFormatError("token program source changed")
    history, metadata, preserved = synchronize_history(outer["history.xml"], {logical_name: (stream, raw, new)})
    hdb, inner_mode = replace_project_stream(outer["_hdb"], stream, new)
    result, outer_mode = replace_project_stream(source, "_hdb", hdb)
    result, history_mode = replace_project_stream(result, "history.xml", history)
    if validate_cfb_streams(hdb) != dict(nested, **{stream: new}):
        raise GXWFormatError("resized token program changed an unrelated nested payload")
    if validate_cfb_streams(result) != dict(outer, _hdb=hdb, **{"history.xml": history}):
        raise GXWFormatError("resized token program changed an unrelated outer payload")
    return ProjectWriteResult(result, {
        "schema_version": 1, "baseline_sha256": sha256(source), "output_sha256": sha256(result),
        "object": logical_name, "old_length": len(raw), "new_length": len(new),
        "metadata_changes": metadata, "preserved_metadata": preserved,
        "allocations": {"program": inner_mode, "outer": outer_mode, "history": history_mode},
        "validation": {"all_other_token_bytes": "caller must verify its intended replacement scope",
                       "all_other_stream_payloads": "byte-identical", "native_compile": "not_run"},
    })


def _patch_program_range_equal_size(source: bytes, logical_name: str, start: int,
                                  old_text: bytes, new_text: bytes) -> ProjectWriteResult:
    """Shared physical patch engine; callers establish the semantic target."""
    outer_payloads = validate_cfb_streams(source)
    mapping = logical_mapping(outer_payloads["projectdatalist.xml"])
    stream = mapping[logical_name]
    hdb = outer_payloads["_hdb"]
    nested_payloads = validate_cfb_streams(hdb)
    raw = nested_payloads[stream]
    if start < 0 or len(old_text) != len(new_text) or raw[start:start + len(old_text)] != old_text:
        raise GXWFormatError("program patch range is not source-bound/equal-size")
    new = raw[:start] + new_text + raw[start + len(old_text):]
    history, metadata, preserved = synchronize_history(outer_payloads["history.xml"],
                                                       {logical_name: (stream, raw, new)})
    if len(history) != len(outer_payloads["history.xml"]):
        raise GXWFormatError("metadata resize is outside the bounded patch contract")
    hdb_new = replace_stream_within_allocation(hdb, stream, new)
    result = replace_stream_within_allocation(source, "_hdb", hdb_new)
    result = replace_stream_within_allocation(result, "history.xml", history)
    if validate_cfb_streams(hdb_new) != dict(nested_payloads, **{stream: new}):
        raise GXWFormatError("non-target nested stream changed")
    expected_outer = dict(outer_payloads, _hdb=hdb_new)
    expected_outer["history.xml"] = history
    if validate_cfb_streams(result) != expected_outer:
        raise GXWFormatError("non-target outer stream changed")

    outer, inner = CompoundFile(source), CompoundFile(hdb)
    hdb_spans = stream_extents(outer, outer.get_stream_entry("_hdb"))
    program_spans = stream_extents(inner, inner.get_stream_entry(stream))
    allowed = []
    for s in map_range(program_spans, start, len(new_text)):
        allowed.extend(map_range(hdb_spans, s.container_offset, s.length))
    history_spans = stream_extents(outer, outer.get_stream_entry("history.xml"))
    for change in metadata:
        if change["old_length"] != change["new_length"]:
            raise GXWFormatError("metadata patch changed length")
        allowed.extend(map_range(history_spans, change["old_offset"], change["old_length"]))
    changes = binary_diff(source, result)
    if len(result) != len(source) or any(not any(
            a.container_offset <= i < a.container_offset + a.length for a in allowed)
            for c in changes for i in range(c["old_offset"], c["old_offset"] + c["old_length"])):
        raise GXWFormatError("writer touched bytes outside the declared physical ranges")
    return ProjectWriteResult(result, {
        "schema_version": 1, "evidence_level": "observed", "handling": "opaque-preserved",
        "baseline_sha256": sha256(source), "output_sha256": sha256(result),
        "object": logical_name,
        "writer_touch": {"offset_space": "outer GXW file bytes", "changed_bytes": sum(c["old_length"] for c in changes),
                         "allowed_ranges": [{"offset": a.container_offset, "length": a.length} for a in allowed],
                         "changes": changes, "outside_ranges": "byte-identical"},
        "metadata_changes": metadata, "preserved_metadata": preserved,
        "validation": {"opaque_preserved": True, "all_stream_payloads": "passed",
                       "native_compile": "not_run", "native_reopen": "not_run",
                       "semantic_equivalence": "not_claimed: intentional source edit"},
        "limitations": ["Experimental equal-size source patch; compiler state is preserved, not regenerated",
                        "Byte preservation does not establish meaning or native acceptance of opaque records"]})

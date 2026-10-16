"""Source-bound replacement of ordinary Ladder instructions, preserving context.

The caller supplies encoded replacement bytes. This module checks their grammar
and exact write scope; it does not claim PLC execution safety or compile .res.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .container import CompoundFile
from .lossless import sha256, _replace_token_program
from .models import GXWFormatError
from .project_metadata import logical_mapping
from .project_writer import ProjectWriteResult
from .token_listing import decode_token_listing, decode_token_program, TokenInstruction, TokenGap, TokenText
from .token_pou import parse_token_region


@dataclass(frozen=True)
class TokenInstructionPatch:
    offset: int
    expected_raw: bytes
    replacement_raw: bytes


@dataclass(frozen=True)
class TokenRecordSplice:
    """Replace an exact source record span, or insert at its starting boundary.

    Empty expected_raw means insertion; empty replacement_raw means deletion.
    Coordinates always refer to the original Program.pou, even in a batch.
    """
    offset: int
    expected_raw: bytes
    replacement_raw: bytes


def splice_token_records(source: bytes, *, expected_sha256: str, logical_name: str,
                         edits: tuple[TokenRecordSplice, ...],
                         text_encoding: str | None = None) -> ProjectWriteResult:
    """Insert/delete/replace complete record spans without rewriting context.

    Selected source bytes are explicit, including any opaque records deliberately
    removed by a splice. All remaining records keep their bytes and boundaries.
    Newly inserted records must decode, and the original terminal END survives.
    This checks file-edit integrity, not control-flow validity or device safety.
    """
    source = bytes(source)
    if sha256(source) != expected_sha256:
        raise GXWFormatError("stale or foreign record splice source")
    if not edits or any(type(edit.offset) is not int or edit.offset < 0 for edit in edits):
        raise GXWFormatError("record splices require nonnegative integer offsets")
    if len({edit.offset for edit in edits}) != len(edits):
        raise GXWFormatError("multiple splices at one source boundary are ambiguous")
    outer = CompoundFile(source)
    mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    if not logical_name.endswith(".Program.pou") or logical_name not in mapping:
        raise GXWFormatError("unresolved record splice program")
    raw = CompoundFile(outer.read_stream("_hdb")).read_stream(mapping[logical_name])
    before = decode_token_listing(raw, text_encoding=text_encoding)
    ends = [i for i, r in enumerate(before.records) if isinstance(r, TokenInstruction) and r.mnemonic == "END"]
    if ends != [len(before.records) - 1]:
        raise GXWFormatError("record splice requires a single terminal END")
    boundaries = {r.tokens[0].offset: i for i, r in enumerate(before.records)}
    terminal = before.records[-1].tokens[0].offset
    plans, previous_end = [], -1
    for edit in sorted(edits, key=lambda e: e.offset):
        old, new = bytes(edit.expected_raw), bytes(edit.replacement_raw)
        end = edit.offset + len(old)
        if (edit.offset not in boundaries or end not in boundaries or end > terminal
                or edit.offset < previous_end or raw[edit.offset:end] != old):
            raise GXWFormatError("record splice is overlapping, unbound, or cuts a record/END")
        if not old and not new:
            raise GXWFormatError("empty record splice changes nothing")
        # A sentinel allows the common framing routine to inspect a fragment;
        # no END from this fragment is copied into the destination program.
        framed = new + b"\x03\x34\x03"
        candidate = decode_token_program(parse_token_region(framed, 0, len(framed)), text_encoding=text_encoding)
        if candidate.gaps or any(
                (isinstance(r, TokenInstruction) and r.mnemonic == "END")
                or (isinstance(r, TokenText) and r.text is None) for r in candidate.records[:-1]):
            raise GXWFormatError("replacement records contain opaque bytes or an embedded END")
        plans.append((edit.offset, end, boundaries[edit.offset], boundaries[end], candidate.records[:-1]))
        previous_end = end
    chunks, origins, changes, cursor = [], [], [], 0
    for start, end, first, last, replacement in plans:
        for index in range(cursor, first):
            chunks.append(b"".join(t.raw for t in before.records[index].tokens))
            origins.append(index)
        new_first = len(chunks)
        for record in replacement:
            chunks.append(b"".join(t.raw for t in record.tokens))
            origins.append(None)
        changes.append({"old_offset": start, "old_length": end - start,
                        "old_record_range": [first, last], "new_record_range": [new_first, len(chunks)],
                        "old_raw_hex": raw[start:end].hex(),
                        "new_raw_hex": b"".join(chunks[new_first:]).hex(),
                        "explicitly_removed_opaque_records": sum(isinstance(r, TokenGap) for r in before.records[first:last])})
        cursor = last
    for index in range(cursor, len(before.records)):
        chunks.append(b"".join(t.raw for t in before.records[index].tokens))
        origins.append(index)
    updated = bytearray(raw[:79] + b"".join(chunks) + raw[-24:])
    struct.pack_into("<II", updated, 55, len(updated) - 83, len(updated) - 83)
    updated = bytes(updated)
    after = decode_token_listing(updated, text_encoding=text_encoding)
    if len(after.records) != len(chunks) or any(b"".join(t.raw for t in r.tokens) != chunk for r, chunk in zip(after.records, chunks)):
        raise GXWFormatError("record splice rebound neighboring tokens")
    if (raw[:55] != updated[:55] or raw[63:79] != updated[63:79] or raw[-24:] != updated[-24:]
            or after.reconstruct() != updated):
        raise GXWFormatError("record splice modified the envelope outside the size fields")
    preserved = [{"source_record": origin, "old_offset": before.records[origin].tokens[0].offset,
                  "new_offset": record.tokens[0].offset, "length": len(chunk), "sha256": sha256(chunk)}
                 for record, chunk, origin in zip(after.records, chunks, origins) if origin is not None]
    for change in changes:
        index = change["new_record_range"][0]
        change["new_offset"] = after.records[index].tokens[0].offset
        change["new_length"] = len(bytes.fromhex(change["new_raw_hex"]))
    result = _replace_token_program(source, logical_name, raw, updated)
    return ProjectWriteResult(result.data, dict(result.report,
        edits=changes, preserved_records=preserved,
        source_gaps_preserved=sum(isinstance(before.records[i], TokenGap) for i in origins if i is not None),
        validation=dict(result.report["validation"],
                        all_other_token_bytes="byte-identical with original record boundaries; offsets may shift",
                        terminal_end="original bytes preserved at the end",
                        program_header="only the two observed size fields may change",
                        control_flow="not validated by the splice operation", native_reopen="not_run"),
        derived_resource_policy="preserve original .res; not fresh until GX Works2 conversion"))


def patch_token_instructions(source: bytes, *, expected_sha256: str, logical_name: str,
                             edits: tuple[TokenInstructionPatch, ...],
                             text_encoding: str | None = None) -> ProjectWriteResult:
    """Apply disjoint edits in original source coordinates as one transaction.

    Unrecognized surrounding records are permitted and retained verbatim. Each
    target and replacement must be exactly one understood instruction. END,
    labels, insertion and deletion are outside this operation's contract.
    """
    source = bytes(source)
    if sha256(source) != expected_sha256:
        raise GXWFormatError("stale or foreign instruction patch source")
    if any(type(edit.offset) is not int or edit.offset < 0 for edit in edits):
        raise GXWFormatError("instruction offsets must be nonnegative integers")
    if not edits or len({edit.offset for edit in edits}) != len(edits):
        raise GXWFormatError("instruction edits must be nonempty and have distinct offsets")
    outer = CompoundFile(source)
    mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    if not logical_name.endswith(".Program.pou") or logical_name not in mapping:
        raise GXWFormatError("unresolved instruction patch program")
    raw = CompoundFile(outer.read_stream("_hdb")).read_stream(mapping[logical_name])
    before = decode_token_listing(raw, text_encoding=text_encoding)
    edits_by_offset = {edit.offset: edit for edit in edits}
    replacements, changes = {}, []
    for index, record in enumerate(before.records):
        edit = edits_by_offset.get(record.tokens[0].offset)
        if edit is None:
            continue
        original = b"".join(t.raw for t in record.tokens)
        if not isinstance(record, TokenInstruction) or record.mnemonic == "END" or original != edit.expected_raw:
            raise GXWFormatError("instruction target bytes or binding disagree")
        body = bytes(edit.replacement_raw) + b"\x03\x34\x03"
        candidate = decode_token_program(parse_token_region(body, 0, len(body)), text_encoding=text_encoding)
        if (len(candidate.records) != 2 or not all(isinstance(r, TokenInstruction) for r in candidate.records)
                or candidate.records[0].mnemonic == "END" or candidate.records[1].mnemonic != "END"):
            raise GXWFormatError("replacement is not exactly one understood non-END instruction")
        replacement = candidate.records[0]
        replacements[index] = bytes(edit.replacement_raw)
        changes.append({"record_index": index, "old_offset": edit.offset,
                        "old_length": len(original), "new_length": len(edit.replacement_raw),
                        "old_raw_hex": original.hex(), "new_raw_hex": edit.replacement_raw.hex(),
                        "before": {"op": record.mnemonic, "args": list(record.args), "step_width": record.step_width},
                        "after": {"op": replacement.mnemonic, "args": list(replacement.args), "step_width": replacement.step_width}})
    if len(replacements) != len(edits):
        raise GXWFormatError("an edit does not point at a complete decoded instruction")
    chunks = [replacements.get(i, b"".join(t.raw for t in record.tokens)) for i, record in enumerate(before.records)]
    updated = bytearray(raw[:79] + b"".join(chunks) + raw[-24:])
    struct.pack_into("<II", updated, 55, len(updated) - 83, len(updated) - 83)
    updated = bytes(updated)
    after = decode_token_listing(updated, text_encoding=text_encoding)
    if len(after.records) != len(before.records):
        raise GXWFormatError("instruction replacement changed surrounding record boundaries")
    for index, record in enumerate(after.records):
        if b"".join(t.raw for t in record.tokens) != chunks[index]:
            raise GXWFormatError("instruction replacement rebound surrounding tokens")
        if index in replacements and not isinstance(record, TokenInstruction):
            raise GXWFormatError("replacement is not understood in its final context")
    if (raw[:55] != updated[:55] or raw[63:79] != updated[63:79] or raw[-24:] != updated[-24:]
            or after.reconstruct() != updated):
        raise GXWFormatError("instruction patch modified envelope bytes outside the two size fields")
    result = _replace_token_program(source, logical_name, raw, updated)
    for change in changes:
        change["new_offset"] = after.records[change["record_index"]].tokens[0].offset
    return ProjectWriteResult(result.data, dict(result.report,
        edits=changes, source_gaps_preserved=len(before.gaps),
        validation=dict(result.report["validation"],
                        all_other_token_bytes="byte-identical, including opaque records; offsets may shift",
                        program_header="only the two observed size fields may change",
                        native_compile="not_run", native_reopen="not_run"),
        derived_resource_policy="preserve original .res; not fresh until GX Works2 conversion"))

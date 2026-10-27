# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Source-bound replacement of ordinary Ladder instructions, preserving context.

The caller supplies encoded replacement bytes. This module checks their grammar
and exact write scope; it does not claim PLC execution safety or compile .res.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import re
import struct

from .lossless import sha256, _replace_token_program
from .models import GXWFormatError
from .project_writer import ProjectWriteResult
from .token_listing import decode_token_listing, decode_token_program, TokenInstruction, TokenGap, TokenLabel, TokenText
from .token_pou import parse_token_region, parse_token_fragment


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


@dataclass(frozen=True)
class PreparedTokenRecords:
    """Source-bound record bytes prepared independently of a project writer."""
    body: bytes
    records: tuple[bytes, ...]


def _terminal_end_index(records, message):
    """END terminates executable records; saved annotations can follow it."""
    ends = [index for index, record in enumerate(records)
            if isinstance(record, TokenInstruction) and record.mnemonic == 'END']
    if not ends:
        return None
    if len(ends) != 1 or any(not isinstance(record, TokenText) for record in records[ends[0] + 1:]):
        raise GXWFormatError(message)
    return ends[0]


def edit_token_records(listing, edits, *, text_encoding, plc_model=None,
                       storage_context=None, allow_end_boundary=False,
                       allow_draft=False) -> PreparedTokenRecords:
    """Apply one batch in original coordinates without a semantic/native check.

    Ladder POU bodies and SFC internal subprograms share the same record
    editing rules. Storage binding, terminal END and neighboring boundaries
    remain mandatory even when the caller saves a semantic draft.
    """
    from .token_encoder import encode_instruction, encode_text_record

    if not isinstance(edits, list) or type(allow_end_boundary) is not bool or type(allow_draft) is not bool:
        raise GXWFormatError('instruction edits must be an array with an explicit boundary policy')
    can_append = allow_end_boundary and not any(
        isinstance(record, TokenInstruction) and record.mnemonic == 'END' for record in listing.records)
    plans, moves = {}, {}
    for edit in edits:
        if (not isinstance(edit, Mapping) or set(edit) - {'operation', 'record_index', 'expected', 'instructions', 'target_record_index'}
                or edit.get('operation') not in {'replace', 'insert_before', 'delete', 'move_before'}
                or type(edit.get('record_index')) is not int):
            raise GXWFormatError('instruction edit requires operation and original record_index')
        index = edit['record_index']
        boundary = edit['operation'] == 'insert_before' and can_append and index == len(listing.records)
        if index in plans or not (0 <= index < len(listing.records) or boundary):
            raise GXWFormatError('duplicate or out-of-range instruction edit')
        source = None if boundary else listing.records[index]
        if not boundary and (not isinstance(source, (TokenInstruction, TokenLabel, TokenText)) or (
                isinstance(source, TokenInstruction) and source.mnemonic == 'END' and edit['operation'] != 'insert_before')):
            raise GXWFormatError('source edit cannot replace opaque records or END')
        identity = ({'op': source.text, 'args': []} if isinstance(source, TokenLabel) else
                    {'kind': source.role, 'text': source.text} if isinstance(source, TokenText) else
                    {'kind': 'end_boundary'} if boundary else
                    {'op': source.mnemonic, 'args': list(source.args)})
        expected = edit.get('expected')
        fields = dict(expected) if isinstance(expected, Mapping) else None
        if fields is not None and 'raw_hex' in fields:
            original = '' if boundary else b''.join(token.raw for token in source.tokens).hex()
            if fields.pop('raw_hex') != original:
                raise GXWFormatError('source edit raw_hex does not match the original record')
        if fields != identity:
            raise GXWFormatError('instruction edit must match the complete current opcode and operands')
        rows = edit.get('instructions', [])
        if edit['operation'] == 'move_before':
            target = edit.get('target_record_index')
            if (type(target) is not int or not (0 <= target < len(listing.records) or can_append and target == len(listing.records))
                    or target == index or rows):
                raise GXWFormatError('source move requires a distinct original target_record_index and no instructions')
            moves.setdefault(target, []).append(index)
            plans[index] = ('move_before', ())
            continue
        if 'target_record_index' in edit:
            raise GXWFormatError('target_record_index is only valid for move_before')
        if (not isinstance(rows, list) or edit['operation'] != 'delete' and not rows
                or edit['operation'] == 'delete' and rows):
            raise GXWFormatError('instruction edit has invalid replacement sequence')
        chunks = []
        for row in rows:
            # A replacement sequence can retain the old instruction among
            # newly added records. Bind every compatible row to that source
            # so an unchanged float/string never travels through its display.
            bound = source if edit['operation'] == 'replace' else None
            if isinstance(row, Mapping) and set(row) == {'raw_hex'}:
                # A record the reader cannot decode travels as its own bytes. It is only
                # accepted when the caller carries it from a bound source program, and the
                # readback below still requires it to reparse as exactly one record.
                try:
                    chunk = bytes.fromhex(row['raw_hex'])
                except (TypeError, ValueError) as exc:
                    raise GXWFormatError('carried record requires hexadecimal bytes') from exc
                if not chunk:
                    raise GXWFormatError('carried record cannot be empty')
                chunks.append(chunk)
            elif (isinstance(row, Mapping) and {'kind', 'text'} <= set(row)
                    and set(row) <= {'kind', 'text', 'flag'}):
                chunks.append(encode_text_record(row['kind'], row['text'], text_encoding=text_encoding,
                    profile=listing.profile,
                    source_record=bound if isinstance(bound, TokenText) else None,
                    storage_flag=row.get('flag')))
            else:
                if not isinstance(row, Mapping) or set(row) != {'op', 'args'} or row['op'] == 'END':
                    raise GXWFormatError('replacement instruction requires op/args and cannot contain END')
                chunks.append(encode_instruction(row['op'], row['args'], profile=listing.profile,
                    text_encoding=text_encoding, plc_model=plc_model, storage_context=storage_context,
                    allow_draft=allow_draft,
                    source_instruction=bound if isinstance(bound, TokenInstruction) or (
                        isinstance(bound, TokenLabel) and isinstance(row['op'], str)
                        and re.fullmatch(r'[PI][0-9]+', row['op'])) else None))
        plans[index] = (edit['operation'], tuple(chunks))
    if any(target in plans and plans[target][0] != 'insert_before' for target in moves):
        raise GXWFormatError('source move target is also deleted, replaced or moved')
    chunks = []
    for index, record in enumerate(listing.records):
        for moved in moves.get(index, []):
            chunks.append(b''.join(token.raw for token in listing.records[moved].tokens))
        operation, replacement = plans.get(index, (None, ()))
        chunks.extend(replacement)
        if operation in (None, 'insert_before'):
            chunks.append(b''.join(token.raw for token in record.tokens))
    for moved in moves.get(len(listing.records), []):
        chunks.append(b''.join(token.raw for token in listing.records[moved].tokens))
    if len(listing.records) in plans:
        chunks.extend(plans[len(listing.records)][1])
    body = b''.join(chunks)
    reread = decode_token_program(parse_token_fragment(body, 0, len(body)),
                                  profile=listing.profile, text_encoding=text_encoding)
    if len(reread.records) != len(chunks) or any(
            b''.join(token.raw for token in record.tokens) != chunk
            for record, chunk in zip(reread.records, chunks)):
        raise GXWFormatError('source edit changes a neighboring record binding')
    old_labels = Counter(record.text for record in listing.records if isinstance(record, TokenLabel))
    new_labels = Counter(record.text for record in reread.records if isinstance(record, TokenLabel))
    if any(count > 1 and count > old_labels[name] for name, count in new_labels.items()):
        raise GXWFormatError('instruction edits introduce a duplicate source label')
    old_end = _terminal_end_index(listing.records, 'source does not have one bound terminal END')
    new_end = _terminal_end_index(reread.records, 'modified instruction source failed terminal END binding')
    if (old_end is None) != (new_end is None):
        raise GXWFormatError('modified instruction source failed terminal END binding')
    return PreparedTokenRecords(body, tuple(chunks))


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
    from .offline_project import read_offline_project
    project = read_offline_project(source)
    mapping = project.mapping
    if not logical_name.endswith(".Program.pou") or logical_name not in mapping:
        raise GXWFormatError("unresolved record splice program")
    raw = project.streams[logical_name]
    before = decode_token_listing(raw, text_encoding=text_encoding)
    terminal_index = _terminal_end_index(before.records, 'record splice requires a single terminal END')
    if terminal_index is None:
        raise GXWFormatError("record splice requires a single terminal END")
    boundaries = {r.tokens[0].offset: i for i, r in enumerate(before.records)}
    terminal = before.records[terminal_index].tokens[0].offset
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
    frame = before.source
    size_offset = frame.body_offset - 24
    updated = bytearray(raw[:frame.body_offset] + b"".join(chunks) + raw[frame.body_end:])
    size = struct.unpack_from('<I', raw, size_offset)[0] + len(updated) - len(raw)
    struct.pack_into("<II", updated, size_offset, size, size)
    updated = bytes(updated)
    after = decode_token_listing(updated, text_encoding=text_encoding)
    if len(after.records) != len(chunks) or any(b"".join(t.raw for t in r.tokens) != chunk for r, chunk in zip(after.records, chunks)):
        raise GXWFormatError("record splice rebound neighboring tokens")
    if (raw[:size_offset] != updated[:size_offset]
            or raw[size_offset + 8:frame.body_offset] != updated[size_offset + 8:frame.body_offset]
            or raw[frame.body_end:] != updated[len(updated) - len(raw[frame.body_end:]):]
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
    from .offline_project import read_offline_project
    project = read_offline_project(source)
    mapping = project.mapping
    if not logical_name.endswith(".Program.pou") or logical_name not in mapping:
        raise GXWFormatError("unresolved instruction patch program")
    raw = project.streams[logical_name]
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
    frame = before.source
    size_offset = frame.body_offset - 24
    updated = bytearray(raw[:frame.body_offset] + b"".join(chunks) + raw[frame.body_end:])
    size = struct.unpack_from('<I', raw, size_offset)[0] + len(updated) - len(raw)
    struct.pack_into("<II", updated, size_offset, size, size)
    updated = bytes(updated)
    after = decode_token_listing(updated, text_encoding=text_encoding)
    if len(after.records) != len(before.records):
        raise GXWFormatError("instruction replacement changed surrounding record boundaries")
    for index, record in enumerate(after.records):
        if b"".join(t.raw for t in record.tokens) != chunks[index]:
            raise GXWFormatError("instruction replacement rebound surrounding tokens")
        if index in replacements and not isinstance(record, TokenInstruction):
            raise GXWFormatError("replacement is not understood in its final context")
    if (raw[:size_offset] != updated[:size_offset]
            or raw[size_offset + 8:frame.body_offset] != updated[size_offset + 8:frame.body_offset]
            or raw[frame.body_end:] != updated[len(updated) - len(raw[frame.body_end:]):]
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

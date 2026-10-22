"""Place a lowered Ladder program into a captured blank Ladder project.

The lowering itself lives in ``gxworks2.csv_export.lower_ladder_program``; this module
only decides how that listing reaches a project. It reuses the source-bound ladder edit
transaction, so the body replacement, the two stored size fields, the allocation and the
lossless readback are the ones an existing-project edit already uses.

A blank baseline is required: the captured projects are empty on purpose, so a delivered
program is exactly the requested records and never a template body with additions.

Ordered entries carry three record kinds. Instructions become instruction records. Line
statements and notes become the body's text records, which is what a real project stores
for ``行间声明`` and inline annotations; without them a program that has statements would
lose content that the CSV export shows. Opaque records that the reader cannot decode are
not expressible here and are refused instead of copied as raw bytes.

A statement shares the step of the instruction it precedes, exactly as the lowering and the
CSV rows describe it, so it is stored with the same-step flag. GX Works2's other form gives
the statement a step of its own and prints it with a leading ``*``; that form changes the
program's step numbering and is not what this lowering produces.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import GXWFormatError
from .offline_project import edit_ladder_project, read_offline_project, read_ordered_program
from .project_templates import (blank_ladder_baseline, captured_project_cpu,
                               verify_ladder_baseline)
from .token_pou import frame_token_pou

TEXT_KINDS = ("statement", "note")
# FX stores this flag beside the text code: 0 shares the step of the next instruction,
# 1 gives the text a step of its own (which GX Works2 prints with a leading '*').
SAME_STEP_FLAG = 0


@dataclass(frozen=True)
class LadderDelivery:
    """One delivered program: bytes plus what the delivery actually did."""

    data: bytes
    plc_model: str
    program: str
    instructions: tuple
    statements: tuple
    device_comments: int
    report: dict
    carried_records: int = 0


def _body_of(stream):
    """The program body of one source stream, without its frame header or trailer."""
    return frame_token_pou(stream).body


def _device(text):
    """Stored spelling, not display spelling: X000 and X0 are one device."""
    import re

    match = re.fullmatch(r"([A-Z]+)0*(\d+)", str(text).upper())
    return match.group(1) + match.group(2) if match else str(text).upper()


def _entry_rows(entries):
    """Turn ordered lowering entries into source rows, keeping their order."""
    rows = []
    for entry in entries:
        mnemonic = getattr(entry, "mnemonic", None)
        if mnemonic is not None:
            rows.append({"op": mnemonic, "args": list(getattr(entry, "operands", ()))})
            note = getattr(entry, "note", "")
            if note and getattr(entry, "note_allowed", True):
                rows.append({"kind": "note", "text": str(note), "flag": SAME_STEP_FLAG})
            continue
        kind = getattr(entry, "kind", "statement")
        if kind not in TEXT_KINDS:
            raise GXWFormatError(f"unsupported text record kind {kind!r}")
        text = str(getattr(entry, "text", "") or "")
        if not text:
            raise GXWFormatError("a statement or note entry needs its text")
        rows.append({"kind": kind, "text": text, "flag": SAME_STEP_FLAG})
    return rows


def _body_rows(program):
    """Ordered rows for a lowering result, without the lowering's own trailing END."""
    entries = getattr(program, "entries", None)
    if entries is None:
        entries = getattr(program, "instructions", ())
    rows = _entry_rows(entries)
    while rows and str(rows[-1].get("op", "")).upper() == "END":
        rows.pop()
    return rows


def _row_identity(row):
    if "op" in row:
        return ("op", row["op"].upper(), tuple(_device(arg) for arg in row["args"]))
    return ("text", row["kind"], row["text"])


def _record_identity(record):
    mnemonic = getattr(record, "mnemonic", None)
    if mnemonic is not None:
        return ("op", mnemonic.upper(),
                tuple(_device(operand.text) for operand in record.operands))
    role = getattr(record, "role", None)
    if role:
        return ("text", role, record.text)
    return ("opaque", b"".join(token.raw for token in record.tokens).hex())


def _empty_baseline_program(baseline, program=None):
    """Return the baseline program name and listing, requiring an empty body."""
    project = read_offline_project(baseline)
    current = read_ordered_program(project, program)
    selected, listing = current["selected"], current["listing"]
    carried = [record for record in listing.instructions if record.mnemonic.upper() != "END"]
    if carried or listing.gaps:
        raise GXWFormatError(
            "the captured Ladder baseline is not an empty program; refusing to deliver "
            f"into {selected['name']!r} which already holds {len(carried)} instruction(s)")
    return project, selected, listing


def build_ladder_project(program, *, plc_model, program_name=None, device_comments=None):
    """Write one lowered Ladder program into the blank project captured for this CPU.

    ``program`` is the result of ``lower_ladder_program``: its ordered entries are inserted
    before the baseline's terminal END, instructions and text records alike. Its own
    trailing END is dropped because the baseline already carries one.
    """
    baseline = blank_ladder_baseline(plc_model)
    verify_ladder_baseline(baseline, plc_model)
    cpu = captured_project_cpu(baseline)
    project, selected, listing = _empty_baseline_program(baseline, program_name)
    rows = _body_rows(program)
    delivered = [row for row in rows if "op" in row]
    statements = [(row["kind"], row["text"]) for row in rows if "kind" in row]
    if not delivered:
        raise GXWFormatError("the lowered program has no instruction to deliver")
    end_index = next(index for index, record in enumerate(listing.records)
                     if getattr(record, "mnemonic", None) == "END")
    edits = [{"operation": "insert_before", "record_index": end_index,
              "expected": {"op": "END", "args": []}, "instructions": rows}]
    comments = dict(program.device_comments) if device_comments is None else device_comments
    saved = edit_ladder_project(baseline, selected["name"], edits,
                                device_comments=comments or None)
    after = read_ordered_program(read_offline_project(saved.data), selected["name"])
    written = [_record_identity(record) for record in after["listing"].records
               if getattr(record, "mnemonic", None) != "END"]
    expected = [_row_identity(row) for row in rows]
    if written != expected:
        raise GXWFormatError("the delivered project does not read back as the requested program")
    report = dict(saved.report)
    report.update({"plc_model": cpu, "program": selected["name"],
                   "instructions": len(delivered), "statements": len(statements),
                   "baseline": "captured blank Ladder project",
                   "device_comments": len(comments or {})})
    return LadderDelivery(data=saved.data, plc_model=cpu, program=selected["name"],
                          instructions=tuple((row["op"], tuple(row["args"])) for row in delivered),
                          statements=tuple(statements),
                          device_comments=len(comments or {}), report=report)

def _source_rows(listing):
    """Rows for an existing program: every record travels as the bytes it already has."""
    rows = []
    for record in listing.records:
        mnemonic = getattr(record, "mnemonic", None)
        if mnemonic is not None and mnemonic.upper() == "END":
            continue
        raw = b"".join(token.raw for token in record.tokens)
        if not raw:
            raise GXWFormatError("the source program holds a record without bytes")
        rows.append({"raw_hex": raw.hex()})
    return rows


def build_ladder_project_from_source(source, *, plc_model, program_name=None,
                                     device_comments=None):
    """Reproduce an existing program in the blank project captured for this CPU.

    This is the clone path, not the generation path: records the reader cannot decode are
    carried as their own bytes instead of being interpreted, so the delivered body is
    compared with the source body and the delivery fails if they differ. Only the program
    body and its device comments travel; project-level declarations, labels outside the
    body and compiled results do not, and the report says so.
    """
    baseline = blank_ladder_baseline(plc_model)
    verify_ladder_baseline(baseline, plc_model)
    cpu = captured_project_cpu(baseline)
    project = read_offline_project(source)
    current = read_ordered_program(project, program_name)
    selected, listing = current["selected"], current["listing"]
    if project.context.get("cpu") != cpu:
        raise GXWFormatError(
            f"the source program belongs to {project.context.get('cpu')!r},"
            f" not to the captured {cpu!r} baseline")
    rows = _source_rows(listing)
    instructions = [record for record in listing.instructions if record.mnemonic.upper() != "END"]
    if not instructions:
        raise GXWFormatError("the source program has no instruction to reproduce")
    statements = [(record.role, record.text) for record in listing.records
                  if getattr(record, "role", None)]
    carried = len(rows) - len(instructions) - len(statements)
    base_project, base_selected, base_listing = _empty_baseline_program(baseline)
    end_index = next(index for index, record in enumerate(base_listing.records)
                     if getattr(record, "mnemonic", None) == "END")
    edits = [{"operation": "insert_before", "record_index": end_index,
              "expected": {"op": "END", "args": []}, "instructions": rows}]
    comments = dict(current.get("device_comments") or {}) if device_comments is None \
        else device_comments
    saved = edit_ladder_project(baseline, base_selected["name"], edits,
                                device_comments=comments or None)
    source_body = _body_of(project.streams[selected["source_name"]])
    delivered_body = _body_of(read_offline_project(saved.data).streams[base_selected["source_name"]])
    if source_body != delivered_body:
        raise GXWFormatError(
            "the reproduced program body differs from its source, so it is not delivered")
    report = dict(saved.report)
    report.update({"plc_model": cpu, "program": base_selected["name"],
                   "source_program": selected["name"],
                   "instructions": len(instructions),
                   "statements": len(statements),
                   "carried_records": carried,
                   "device_comments": len(comments or {}),
                   "baseline": "captured blank Ladder project",
                   "not_carried": "declarations, labels outside the body, compiled results"})
    if carried:
        # Undecoded records carry inline FB-block bodies and symbol operands. Their
        # definitions live outside the program body, so this project opens but does not
        # compile until those declarations exist; say it instead of implying otherwise.
        report["delivery_limits"] = (
            f"{carried} record(s) reference symbols or FB blocks defined outside the program "
            "body; the delivered project carries the body only and does not compile until "
            "those declarations are delivered too")
    return LadderDelivery(data=saved.data, plc_model=cpu, program=base_selected["name"],
                          instructions=tuple((record.mnemonic,
                                              tuple(operand.text for operand in record.operands))
                                             for record in instructions),
                          statements=tuple(statements),
                          device_comments=len(comments or {}), carried_records=carried,
                          report=report)

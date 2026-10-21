"""Source-bound, order-preserving re-serialization of an ordered CSV export.

:mod:`gxworks2.csv_export` refuses ordered instruction sources on purpose: the
graphical exporter would have to guess rung structure, and a guessed export is
worse than a clear refusal.  A CSV export whose drawing preview is incomplete is
still an unambiguous statement list, so this module writes the same two native
documents back from the ordered snapshot instead of from the drawing model:

* instruction order, opcode spelling and operand text come from the snapshot;
* step labels follow the source export's own steps by default, and the shared
  width catalogue is recomputed only to compare — a disagreement is reported as
  ``width_mismatch``/``width_unknown`` and never substituted silently;
* an edited snapshot is renumbered from the same shared catalogue, with the
  original step kept as row provenance instead of the current address;
* per-network leading statement rows, per-instruction statements and labels are
  written back verbatim;
* every written instruction row keeps its binding to the original CSV row.

:func:`verify_gxworks2_csv_source` compares the rewritten documents with the
snapshot and, when the original export is supplied, with the original export.
Identical instruction semantic hashes are the weakest useful statement here, so
statements, labels, step labels, network notes and the row binding are checked
separately.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from gxworks2.csv_manager import (
    GXWORKS2_COMMENT_HEADER,
    GXWORKS2_HEADER,
    GXWORKS2_STATEMENT_MAX_BYTES,
    CSVManager,
)
from plc.instruction_steps import StepCursor, instruction_step_width


#: ``source`` keeps the export's own step labels; ``catalogue`` renumbers an
#: edited instruction sequence from the shared Core width rules.
STEP_SOURCES = ("source", "catalogue")


def _bytes(value: str) -> int:
    return len(str(value or "").encode("gb18030"))


def _write_rows(path: Path, rows: Sequence[Sequence[str]], newline: str = "\r\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-16") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator=newline).writerows(rows)


def _instruction_rows(snapshot: Mapping[str, Any]):
    """Yield (network, position, record) in source order."""
    for network in snapshot.get("raw_networks") or []:
        for position, instruction in enumerate(network.get("raw_instructions") or []):
            if str(instruction.get("op") or "").strip():
                yield network, position, instruction


def _step_source(value: Any) -> str:
    chosen = str(value or "source").strip().lower()
    if chosen not in STEP_SOURCES:
        raise ValueError("step_source must be 'source' or 'catalogue', not " + repr(value))
    return chosen


def step_width_plan(snapshot: Mapping[str, Any], *, plc_model: Any = None,
                    step_source: Any = "source") -> Dict[str, Any]:
    """Expected step labels and width findings for one ordered snapshot.

    The plan is the single place that turns instructions into steps, so the
    writer and both verifiers cannot disagree.  A width the catalogue cannot
    resolve stays unknown: it is reported, never replaced by a one-step guess.
    """
    model = str(plc_model or snapshot.get("plc_model") or "").strip().upper() or None
    chosen = _step_source(step_source)
    cursor = StepCursor()
    entries: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    for network in snapshot.get("raw_networks") or []:
        network_id = str(network.get("id") or "")
        instructions = [item for item in network.get("raw_instructions") or []
                        if str(item.get("op") or "").strip()]
        source_steps = [str(item.get("step") or "").strip() for item in instructions]
        for position, instruction in enumerate(instructions):
            opcode = str(instruction.get("op") or "").strip().upper()
            operands = [str(item) for item in instruction.get("args") or []]
            width = instruction_step_width(opcode, operands, plc_model=model)
            source_step = source_steps[position]
            # The export's own per-instruction width is an observation; the
            # catalogue is the expectation being checked against it.
            native_width = None
            if (position + 1 < len(source_steps) and source_step.isdigit()
                    and source_steps[position + 1].isdigit()):
                native_width = int(source_steps[position + 1]) - int(source_step)
            step = (cursor.label if chosen == "catalogue"
                    else (source_step if source_step.isdigit() else cursor.label))
            entries.append({"network_id": network_id, "instruction_index": position,
                            "opcode": opcode, "operands": operands,
                            "statement": str(instruction.get("statement") or ""),
                            "label": str(instruction.get("label") or ""),
                            "source_row": instruction.get("source_row"),
                            "source_step": source_step, "step": step,
                            "catalogue_steps": width.steps,
                            "width_source": width.source,
                            "width_status": width.status,
                            "native_steps": native_width})
            rule = {"plc_model": model, "rules": list(width.evidence)}
            if width.steps is None:
                findings.append({"kind": "width_unknown", "network_id": network_id,
                                 "instruction_index": position, "opcode": opcode,
                                 "operands": operands, "source_row": instruction.get("source_row"),
                                 "source_step": source_step, "catalogue_steps": None,
                                 "native_steps": native_width, "status": width.status,
                                 "requirements": list(width.requirements), **rule,
                                 "reason": width.reason or "No expected width for this form"})
            elif native_width is not None and native_width != width.steps:
                findings.append({"kind": "width_mismatch", "network_id": network_id,
                                 "instruction_index": position, "opcode": opcode,
                                 "operands": operands, "source_row": instruction.get("source_row"),
                                 "source_step": source_step,
                                 "catalogue_steps": width.steps,
                                 "native_steps": native_width, "status": width.status, **rule,
                                 "reason": "Source export and the shared width catalogue disagree"})
            cursor.advance(width)
            if chosen == "source" and source_step.isdigit() and width.steps is not None:
                # Keep following instructions that have no source step (an
                # inserted or edited row) next to the export's own numbering.
                cursor.step = int(source_step) + width.steps
    summary = {
        "instruction_count": len(entries),
        "mismatch": sum(1 for item in findings if item["kind"] == "width_mismatch"),
        "unknown": sum(1 for item in findings if item["kind"] == "width_unknown"),
        "steps_complete": all(entry["step"].isdigit() for entry in entries),
    }
    return {"plc_model": model, "step_source": chosen, "entries": entries,
            "findings": findings, "summary": summary,
            "next_step_label": cursor.label}


def write_gxworks2_csv_from_source(
    snapshot: Mapping[str, Any],
    program_csv_path: Any,
    comment_csv_path: Any,
    *,
    plc_model: str | None = None,
    title: str | None = None,
    plc_info: str | None = None,
    comment_title: str | None = None,
    program_newline: str | None = None,
    comment_newline: str | None = None,
    step_source: str = "source",
) -> Dict[str, Any]:
    """Write one ordered snapshot back as native program and comment CSV files.

    ``snapshot`` is what :func:`gxworks2.csv_importer.inspect_gxworks2_csv`
    returns.  Nothing is drawn, lowered or reordered: the statement list is
    re-serialized, and the report records the row binding plus any step or
    statement finding that a real GX Works2 import would need to know about.

    ``step_source="source"`` (default) reproduces the export's own step labels,
    so an unedited round-trip stays byte-identical even where the catalogue
    disagrees.  ``step_source="catalogue"`` renumbers an edited sequence from the
    shared Core width rules and keeps ``source_row``/``source_step`` as
    provenance only.
    """
    plan = step_width_plan(snapshot, plc_model=plc_model, step_source=step_source)
    model = plan["plc_model"]
    program_path = Path(program_csv_path).expanduser()
    comment_path = Path(comment_csv_path).expanduser()

    document_title = str(title or snapshot.get("export_title") or snapshot.get("program_name") or "MAIN")
    information = str(plc_info if plc_info is not None else (snapshot.get("plc_info") or ""))
    if not information and model:
        # No source header to reproduce: state the project CPU instead of an
        # empty or borrowed FX3U line. Unknown targets stay empty and reported.
        try:
            from gxworks2.project_profiles import native_plc_info
            information = native_plc_info(model)
        except ValueError:
            information = ""
    rows: List[List[str]] = [[document_title], ["PLC信息:", information], list(GXWORKS2_HEADER)]

    planned = {(entry["network_id"], entry["instruction_index"]): entry
               for entry in plan["entries"]}
    binding: List[Dict[str, Any]] = []
    statement_findings: List[Dict[str, Any]] = []
    statement_count = label_count = 0
    terminal_written = False

    for network in snapshot.get("raw_networks") or []:
        network_id = str(network.get("id") or "")
        for statement in network.get("statements") or []:
            text = str(statement)
            if not text:
                continue
            if _bytes(text) > GXWORKS2_STATEMENT_MAX_BYTES:
                statement_findings.append({"network_id": network_id, "statement": text,
                                           "reason": "interline statement exceeds the native byte limit"})
            # GX Works2 leaves the step column empty on an interline statement
            # row; the following instruction keeps its own step label.
            rows.append(["", text, "", "", "", "", ""])
        for position, instruction in enumerate(network.get("raw_instructions") or []):
            if not str(instruction.get("op") or "").strip():
                continue
            entry = planned[(network_id, position)]
            opcode = entry["opcode"]
            operands = entry["operands"]
            step = entry["step"]
            statement = entry["statement"]
            label = entry["label"]
            if _bytes(statement) > GXWORKS2_STATEMENT_MAX_BYTES:
                statement_findings.append({"network_id": network_id, "instruction_index": position,
                                           "statement": statement,
                                           "reason": "instruction statement exceeds the native byte limit"})
            statement_count += 1 if statement else 0
            label_count += 1 if label else 0
            binding.append({"network_id": network_id, "instruction_index": position,
                            "row": len(rows) + 1, "step": step,
                            "step_source": plan["step_source"],
                            "source_row": entry["source_row"],
                            "source_step": entry["source_step"]})
            # GX Works2 writes the first operand beside the opcode and each
            # further operand on its own continuation row.  Reproducing that
            # split keeps a pure re-serialization byte-identical.
            rows.append([step, statement, opcode,
                         operands[0] if operands else "", "", "", label])
            for operand in operands[1:]:
                rows.append(["", "", "", operand, "", "", ""])
            terminal_written = terminal_written or opcode == "END"

    if not terminal_written:
        # A native statement list needs exactly one terminal END.  Say so instead
        # of writing a file that GX Works2 would reject without an explanation.
        rows.append([plan["next_step_label"], "", "END", "", "", "", ""])

    _write_rows(program_path, rows, program_newline
                or (snapshot.get("newlines") or {}).get("program") or "\r\n")

    comment_rows: List[List[str]] = [
        [str(comment_title or snapshot.get("comment_title") or "COMMENT")],
        list(GXWORKS2_COMMENT_HEADER),
    ]
    for address, comment in (snapshot.get("device_comments") or {}).items():
        if address and comment and str(comment) != "null":
            comment_rows.append([str(address), str(comment)])
    chosen_comment_newline = (comment_newline
                              or (snapshot.get("newlines") or {}).get("comments")
                              or "\r\n")
    _write_rows(comment_path, comment_rows, chosen_comment_newline)

    manager = CSVManager()
    program_validation = manager.validate(program_path)
    comment_validation = manager.validate_comments(
        comment_path, require_crlf=chosen_comment_newline == "\r\n")
    return {
        "program_csv": str(program_path),
        "comment_csv": str(comment_path),
        "plc_model": model,
        "step_source": plan["step_source"],
        "instruction_count": len(binding),
        "statement_count": statement_count,
        "label_count": label_count,
        "terminal_end_written": terminal_written,
        "newlines": {"program": program_newline
                     or (snapshot.get("newlines") or {}).get("program") or "\r\n",
                     "comments": chosen_comment_newline},
        "binding": binding,
        # ``width_mismatch`` is a source observation that disagrees with the
        # catalogue; ``width_unknown`` means the catalogue cannot answer yet.
        # Neither one blocks the write: the report states what is known.
        "width_findings": plan["findings"],
        "width_summary": plan["summary"],
        "statement_findings": statement_findings,
        "validation": {
            "program": {"valid": program_validation.valid,
                        "errors": list(program_validation.errors),
                        "instruction_count": program_validation.instruction_count},
            "comments": {"valid": comment_validation.valid,
                         "errors": list(comment_validation.errors),
                         "comment_count": comment_validation.comment_count},
        },
    }


def verify_gxworks2_csv_source(
    snapshot: Mapping[str, Any],
    *,
    program_csv_path: Any,
    comment_csv_path: Any,
    source_program_csv_path: Any = None,
    source_comment_csv_path: Any = None,
    plc_model: Any = None,
    step_source: str = "source",
    expect_source_unchanged: bool = True,
) -> Dict[str, Any]:
    """Check that a rewritten export still carries this snapshot.

    Every check is reported separately: format, program semantics, comment
    semantics, instruction order, operands, step labels, statements, labels,
    network notes, comment rows and the original-row binding.  Supplying the
    original export adds the two semantic-hash checks and row provenance.

    An edited snapshot legitimately changes those hashes, so pass
    ``expect_source_unchanged=False`` (with ``step_source="catalogue"``): the
    hash checks report ``skipped``, the written program is compared with the
    edited snapshot field by field, and provenance still confirms that rows which
    came from the export point at the original instruction.
    """
    from gxworks2.csv_importer import _read_program_rows, _split_networks, read_device_comments

    manager = CSVManager()
    plan = step_width_plan(snapshot, plc_model=plc_model or snapshot.get("plc_model"),
                           step_source=step_source)
    checks: Dict[str, Dict[str, Any]] = {}
    mismatches: List[Dict[str, Any]] = []

    def record(name, passed, **details):
        checks[name] = {"status": "passed" if passed else "failed", **details}
        if not passed:
            mismatches.append({"check": name, **details})

    def skip(name, reason):
        checks[name] = {"status": "skipped", "reason": reason}

    program_path = Path(program_csv_path).expanduser()
    comment_path = Path(comment_csv_path).expanduser()
    source_program = (Path(source_program_csv_path).expanduser()
                      if source_program_csv_path else None)
    source_comment = (Path(source_comment_csv_path).expanduser()
                      if source_comment_csv_path else None)

    program_validation = manager.validate(program_path)
    # The native comment export uses LF in the field; the row/column format is
    # still the stable discriminator, so CRLF is not a source requirement here.
    comment_validation = manager.validate_comments(comment_path, require_crlf=False)
    record("format", program_validation.valid and comment_validation.valid,
           program_errors=list(program_validation.errors),
           comment_errors=list(comment_validation.errors))

    if source_program is not None and expect_source_unchanged:
        source_program_hash = manager.program_semantic_sha256(source_program)
        rendered_program_hash = manager.program_semantic_sha256(program_path)
        record("program_semantics", source_program_hash == rendered_program_hash,
               source=source_program_hash, rendered=rendered_program_hash)
    else:
        skip("program_semantics", "Edited export: the written program is compared per field below"
             if source_program is not None else "No original program CSV was supplied")
    if source_comment is not None and expect_source_unchanged:
        source_comment_hash = manager.comments_semantic_sha256(source_comment)
        rendered_comment_hash = manager.comments_semantic_sha256(comment_path)
        record("comment_semantics", source_comment_hash == rendered_comment_hash,
               source=source_comment_hash, rendered=rendered_comment_hash)
    else:
        skip("comment_semantics", "Edited export: comments are compared row by row below"
             if source_comment is not None else "No original comment CSV was supplied")

    _rows, records = _read_program_rows(program_path)
    written = [record for record in records if record.op]
    expected = plan["entries"]

    def compare(name, extract_expected, extract_written):
        differences = []
        for index in range(max(len(expected), len(written))):
            before = extract_expected(expected[index]) if index < len(expected) else None
            after = extract_written(written[index]) if index < len(written) else None
            if before != after:
                differences.append({"index": index, "expected": before, "actual": after})
        record(name, not differences and len(expected) == len(written),
               checked=min(len(expected), len(written)), mismatches=differences[:20])
        return differences

    compare("instruction_order", lambda row: row["opcode"], lambda row: row.op)
    compare("operands", lambda row: row["operands"], lambda row: list(row.args))
    # Step labels follow the same plan the writer used, so a renumbered export is
    # verified against Core's own numbering rather than against stale source steps.
    compare("step_labels", lambda row: row["step"], lambda row: row.step)
    compare("statements", lambda row: row["statement"], lambda row: row.statement)
    compare("labels", lambda row: row["label"], lambda row: row.label)

    expected_notes = [str(network.get("comment") or "").strip()
                      for network in snapshot.get("raw_networks") or []]
    written_notes = [note for note, _instructions, _leading in _split_networks(records)]
    if expect_source_unchanged:
        record("network_notes", expected_notes == written_notes,
               expected=expected_notes, actual=written_notes)
    else:
        # A statement row inside a network is part of that network's note, so an
        # edited sequence can move statement text between the note and a
        # per-instruction statement. Compare the retained text, not the partition.
        expected_text: List[str] = []
        for network in snapshot.get("raw_networks") or []:
            note_lines = [line for line in str(network.get("comment") or "").split("\n")
                          if line.strip()]
            expected_text.extend(note_lines)
            for instruction in network.get("raw_instructions") or []:
                statement = str(instruction.get("statement") or "").strip()
                # A leading statement is also carried by the first instruction.
                if statement and statement not in note_lines:
                    expected_text.append(statement)
        written_text = [line for note in written_notes for line in note.split("\n") if line.strip()]
        record("network_notes", sorted(expected_text) == sorted(written_text),
               expected=sorted(expected_text)[:20], actual=sorted(written_text)[:20])

    expected_comments = [[str(address), str(comment)]
                         for address, comment in (snapshot.get("device_comments") or {}).items()
                         if address and comment and str(comment) != "null"]
    written_comments = [[str(address), str(comment)]
                        for address, comment in read_device_comments(comment_path).items()]
    record("comment_rows", expected_comments == written_comments,
           expected=expected_comments[:20], actual=written_comments[:20])

    if source_program is None:
        skip("source_rows", "Edited export: original rows were not supplied for provenance")
        unbound = 0
    else:
        # The row binding must point at rows that really carry the instruction.
        # A renumbered export keeps the original step as provenance, so only a
        # source-preserving write compares steps here.
        source_records = {row.source_row: row for row in _read_program_rows(source_program)[1]}
        binding_problems: List[Dict[str, Any]] = []
        unbound = 0
        for position, entry in enumerate(expected):
            source_row = entry["source_row"]
            if source_row is None:
                # An inserted instruction has no original row; that is a fact to
                # report, not a broken binding.
                unbound += 1
                continue
            original = source_records.get(source_row)
            if original is None:
                binding_problems.append({"index": position, "source_row": source_row,
                                         "reason": "original row does not exist"})
                continue
            if original.op != entry["opcode"]:
                binding_problems.append({"index": position, "source_row": source_row,
                                         "reason": "original row carries another opcode",
                                         "expected": entry["opcode"], "actual": original.op})
            elif plan["step_source"] == "source" and original.step != entry["step"]:
                binding_problems.append({"index": position, "source_row": source_row,
                                         "reason": "original row carries another step",
                                         "expected": entry["step"], "actual": original.step})
        record("source_rows", not binding_problems, checked=len(expected),
               unbound=unbound, mismatches=binding_problems[:20])

    result = {
        "status": "passed" if not mismatches else "failed",
        "checks": checks,
        "mismatches": mismatches,
        "instruction_count": len(expected),
        "comment_count": len(expected_comments),
        "step_source": plan["step_source"],
        "unbound_instructions": unbound,
        "width_findings": plan["findings"],
        "width_summary": plan["summary"],
    }
    if source_program is not None and source_comment is not None:
        # Informational: an unedited re-serialization is byte-identical, while an
        # edited one legitimately is not.  The checks above are what the caller
        # may rely on.
        result["byte_identical"] = {
            "program": program_path.read_bytes() == source_program.read_bytes(),
            "comments": comment_path.read_bytes() == source_comment.read_bytes(),
        }
    return result


__all__ = ["step_width_plan", "verify_gxworks2_csv_source", "write_gxworks2_csv_from_source",
           "STEP_SOURCES"]

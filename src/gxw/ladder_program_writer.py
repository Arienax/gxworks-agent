"""Place a lowered Ladder program into a captured blank Ladder project.

The lowering itself lives in ``gxworks2.csv_export.lower_ladder_program``; this module
only decides how those instructions reach a project. It reuses the source-bound ladder
edit transaction, so the body replacement, the two stored size fields, the allocation and
the lossless readback are the ones an existing-project edit already uses.

A blank baseline is required: the captured projects are empty on purpose, so a delivered
program is exactly the requested instructions and never a template body with additions.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import GXWFormatError
from .offline_project import edit_ladder_project, read_offline_project, read_ordered_program
from .project_templates import blank_ladder_baseline, verify_ladder_baseline


@dataclass(frozen=True)
class LadderDelivery:
    """One delivered program: bytes plus what the delivery actually did."""

    data: bytes
    plc_model: str
    program: str
    instructions: tuple
    device_comments: int
    report: dict


def _identities(rows):
    """Compare stored programs, not display spelling: X000 and X0 are one device."""
    import re

    def device(text):
        match = re.fullmatch(r"([A-Z]+)0*(\d+)", str(text).upper())
        return match.group(1) + match.group(2) if match else str(text).upper()

    return tuple((mnemonic.upper(), tuple(device(arg) for arg in args)) for mnemonic, args in rows)


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

    ``program`` is the result of ``lower_ladder_program``: its instructions are inserted
    before the baseline's terminal END, in the order the lowering produced them. Its own
    trailing END is dropped because the baseline already carries one.
    """
    cpu = verify_ladder_baseline(blank_ladder_baseline(plc_model), plc_model)
    baseline = blank_ladder_baseline(plc_model)
    project, selected, listing = _empty_baseline_program(baseline, program_name)
    delivered = [(entry.mnemonic, list(entry.operands)) for entry in program.instructions
                 if entry.mnemonic.upper() != "END"]
    if not delivered:
        raise GXWFormatError("the lowered program has no instruction to deliver")
    end_index = next(index for index, record in enumerate(listing.records)
                     if getattr(record, "mnemonic", None) == "END")
    edits = [{"operation": "insert_before", "record_index": end_index,
              "expected": {"op": "END", "args": []},
              "instructions": [{"op": mnemonic, "args": args} for mnemonic, args in delivered]}]
    comments = dict(program.device_comments) if device_comments is None else device_comments
    saved = edit_ladder_project(baseline, selected["name"], edits,
                                device_comments=comments or None)
    after = read_ordered_program(read_offline_project(saved.data), selected["name"])
    written = tuple((record.mnemonic, [operand.text for operand in record.operands])
                    for record in after["listing"].instructions)
    expected = tuple(delivered) + (("END", []),)
    if _identities(written) != _identities(expected):
        raise GXWFormatError("the delivered project does not read back as the requested program")
    report = dict(saved.report)
    report.update({"plc_model": cpu, "program": selected["name"],
                   "instructions": len(delivered), "baseline": "captured blank Ladder project",
                   "device_comments": len(comments or {})})
    return LadderDelivery(data=saved.data, plc_model=cpu, program=selected["name"],
                          instructions=tuple(delivered),
                          device_comments=len(comments or {}), report=report)

"""Isolated P0 B-to-C source-envelope transplant, never a production writer.

Keep B's 71-byte prefix except observed lengths/count. Transplant C's native
block containers, including the explicitly tracked local-coordinate change.
Only Program.pou and already-established history integrity fields may change.
"""
from pathlib import Path
import argparse
import json
import struct

from probe_block_boundaries import compare, digest, probe
from gxw.container_writer import replace_project_stream, validate_cfb_streams
from gxw.project_metadata import logical_mapping, synchronize_history
from gxw.project_writer import binary_diff, write_new_file


def transplant(baseline, donor, logical="1.Program.pou"):
    outer = validate_cfb_streams(baseline)
    other = validate_cfb_streams(donor)
    mapping = logical_mapping(outer["projectdatalist.xml"])
    donor_mapping = logical_mapping(other["projectdatalist.xml"])
    nested = validate_cfb_streams(outer["_hdb"])
    native = validate_cfb_streams(other["_hdb"])[donor_mapping[logical]]
    original = nested[mapping[logical]]
    left, right = probe(original), probe(native)
    if left["status"] != "hypothesis_only" or right["status"] != "hypothesis_only":
        raise ValueError("probe rejected source framing")
    if (len(left["blocks"]), len(right["blocks"])) != (1, 2):
        raise ValueError("experiment requires native B (one block) and C (two blocks)")
    nodes = lambda p: sorted((r["kind"], r["symbol"], r["type_name"] or "")
        for b in p["blocks"] for r in b["records"] if r["class"] == 1)
    if nodes(left) != nodes(right):
        raise ValueError("node identities differ")
    prefix = bytearray(original[:71])
    for offset in (0x37, 0x3B, 0x43):
        struct.pack_into("<I", prefix, offset, struct.unpack_from("<I", native, offset)[0])
    candidate = bytes(prefix) + native[71:]
    new_hdb, allocation = replace_project_stream(outer["_hdb"], mapping[logical], candidate)
    updated, outer_allocation = replace_project_stream(baseline, "_hdb", new_hdb)
    history, changes, preserved = synchronize_history(outer["history.xml"], {
        logical: (mapping[logical], original, candidate)})
    updated, history_allocation = replace_project_stream(updated, "history.xml", history)
    final_outer = validate_cfb_streams(updated)
    final_nested = validate_cfb_streams(final_outer["_hdb"])
    assert final_nested.keys() == nested.keys()
    assert final_outer.keys() == outer.keys()
    assert all(final_nested[k] == v for k, v in nested.items() if k != mapping[logical])
    assert all(final_outer[k] == v for k, v in outer.items() if k not in ("_hdb", "history.xml"))
    report = compare(baseline, updated)
    report["experiment"] = {
        "id": "M1", "hypothesis": "H-envelope versus H-other",
        "baseline": digest(baseline), "donor": digest(donor),
        "changed_variables": ["block envelopes/count", "second rung local y: 4..6 to 2..4",
            "per-block rail instead of shared rail", "required history size/known digest"],
        "invariants": ["B prefix opaque bytes and timestamps", "node identities/Boolean paths",
            "all non-target nested payloads including declarations and opaque MAIN.res",
            "all outer payloads except _hdb/history.xml"],
        "program_prefix_diff": binary_diff(original[:71], candidate[:71]),
        "history_changes": changes, "unknown_history_preserved": preserved,
        "allocation": [allocation, outer_allocation, history_allocation],
        "native_validation": {"status": "native_validation_pending"},
    }
    return updated, report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline", type=Path)
    ap.add_argument("donor", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("report", type=Path)
    args = ap.parse_args()
    result, report = transplant(args.baseline.read_bytes(), args.donor.read_bytes())
    write_new_file(args.output, result)
    write_new_file(args.report, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps({"output": str(args.output), **digest(result),
                      "changed_logical_streams": list(report["logical_stream_changes"])}, ensure_ascii=False))

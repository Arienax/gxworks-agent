"""Native-seed SFC child-program framing and raw-preserved mutation probes.

This experiment recognizes the observed uncompressed FX3U SFC envelope only.
The graph and all child metadata remain opaque. Child names/references and code
bytes can be compared with independent native workspace snapshots. No device
session is opened; conversion uses ChangeSFCProgram, never generic IEC Build.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container_writer import replace_project_stream, validate_cfb_streams
from gxw.lossless import inspect_project
from gxw.project_metadata import logical_mapping, synchronize_history
from gxw.token_pou import parse_token_fragment
from replay_gxw_workspace import run


def framing(raw):
    """Return bounded source regions; do not interpret or reconstruct the graph."""
    cursor = 54

    def word():
        nonlocal cursor
        if cursor + 4 > len(raw):
            raise ValueError("truncated SFC framing")
        value = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        return value

    def count():
        value = word()
        if value > 8192:
            raise ValueError("SFC child count exceeds experiment bound")
        return value

    def name():
        nonlocal cursor
        n = count()
        end = cursor + 2 * n
        if not n or end > len(raw) or raw[end - 2:end] != b"\0\0":
            raise ValueError("invalid SFC child name extent")
        result = raw[cursor:end - 2].decode("utf-16le")
        cursor = end
        return result

    def program(kind, identity, number=None):
        nonlocal cursor
        flag = word()
        if flag != 1:
            raise ValueError("unsupported child program storage flag")
        size_offset = cursor
        size = word()
        start, end = cursor, cursor + size
        if size < 20 or end > len(raw) or struct.unpack_from("<I", raw, start)[0] != size:
            raise ValueError("inconsistent child program extent")
        tokens = parse_token_fragment(raw, start + 20, size - 20)
        cursor = end
        tail_offset = cursor
        tail = word()
        if tail != 1:
            raise ValueError("unsupported child program trailer")
        return dict(kind=kind, name=identity, number=number, size_offset=size_offset,
                    payload_offset=start, payload_size=size, token_offset=start + 20,
                    token_size=size - 20, token_hex=tokens.body.hex(),
                    tail_offset=tail_offset)

    if word() != 0xF0:
        raise ValueError("not the observed SFC source type")
    graph_size = word()
    graph_start = cursor
    if graph_size < 12 or graph_start + graph_size > len(raw) or word() != graph_size:
        raise ValueError("unsupported SFC graph extent")
    cursor = graph_start + graph_size
    children = []
    for _ in range(count()):
        children.append(program("zoom", name()))
    actions = []
    for _ in range(count()):
        identity = name()
        number_offset = cursor
        number = word()
        registrations = []
        for _ in range(count()):
            qualifier = word()
            registrations.append(dict(qualifier=qualifier, zoom=name()))
        if word() != 1:
            raise ValueError("unsupported SFC action trailer")
        actions.append(dict(name=identity, number=number, number_offset=number_offset, registrations=registrations))
    for _ in range(count()):
        identity, number = name(), word()
        children.append(program("transition", identity, number))
    tail_offset = cursor
    if word() != 0 or cursor != len(raw):
        raise ValueError("unsupported SFC trailing region")
    return dict(graph=dict(offset=graph_start, size=graph_size, handling="opaque-preserved"),
                children=children, actions=actions, tail_offset=tail_offset)


def replace_child(source, logical, kind, old_code, new_code):
    outer = validate_cfb_streams(source)
    nested = validate_cfb_streams(outer["_hdb"])
    physical = logical_mapping(outer["projectdatalist.xml"])[logical]
    old = nested[physical]
    layout = framing(old)
    candidates = [c for c in layout["children"] if c["kind"] == kind and c["token_hex"] == old_code.hex()]
    if len(candidates) != 1:
        raise ValueError("mutation requires one explicitly matched child program")
    child = candidates[0]
    parse_token_fragment(new_code, 0, len(new_code))
    start, end = child["token_offset"], child["token_offset"] + child["token_size"]
    new = bytearray(old[:start] + new_code + old[end:])
    new_size = 20 + len(new_code)
    struct.pack_into("<I", new, child["size_offset"], new_size)
    struct.pack_into("<I", new, child["payload_offset"], new_size)
    new = bytes(new)
    after = framing(new)
    # Only the two observed length words and the chosen code extent change.
    prefix = bytearray(new[:start])
    for pos in (child["size_offset"], child["payload_offset"]):
        prefix[pos:pos + 4] = old[pos:pos + 4]
    if bytes(prefix) != old[:start] or new[start + len(new_code):] != old[end:]:
        raise ValueError("opaque SFC bytes changed")
    history, changes, preserved = synchronize_history(outer["history.xml"], {logical: (physical, old, new)})
    hdb, inner_mode = replace_project_stream(outer["_hdb"], physical, new)
    updated, outer_mode = replace_project_stream(source, "_hdb", hdb)
    updated, history_mode = replace_project_stream(updated, "history.xml", history)
    if validate_cfb_streams(hdb) != dict(nested, **{physical: new}):
        raise ValueError("unrelated nested stream changed")
    if validate_cfb_streams(updated) != dict(outer, _hdb=hdb, **{"history.xml": history}):
        raise ValueError("unrelated outer stream changed")
    return updated, dict(logical=logical, child=child, old_hex=old_code.hex(), new_hex=new_code.hex(),
        source_sha256=hashlib.sha256(source).hexdigest(), output_sha256=hashlib.sha256(updated).hexdigest(),
        unrelated_payloads="byte-identical", opaque_sfc_regions="byte-identical",
        metadata_changes=changes, preserved_metadata=preserved,
        allocation=dict(program=inner_mode, outer=outer_mode, history=history_mode), after=after)


def native_children(snapshot):
    def child(blob, offset):
        return next(r["data"] for r in blob["relocations"] if r["offset"] == offset)
    result = {}
    pool = child(snapshot["build"], 44)
    for start in range(0, len(base64.b64decode(pool["raw"])), 56):
        for offset, stride, body_offset, kind in [(48, 12, 4, "zoom"), (40, 16, 8, "transition")]:
            sub = child(pool, start + offset)
            for i in range(0, len(base64.b64decode(sub["raw"])), stride):
                identity = base64.b64decode(child(sub, i)["raw"]).rstrip(b"\0").decode("ascii")
                body = base64.b64decode(child(sub, i + body_offset)["raw"])
                result[(kind, identity)] = body[84:]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("seed", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.seed.read_bytes()
    candidates = [s for s in inspect_project(source).streams if s.logical_name and s.logical_name.endswith(".Program.pou")]
    if len(candidates) != 1:
        raise ValueError("probe requires one SFC POU")
    seed = candidates[0]
    (args.output / "source-framing.json").write_text(json.dumps(framing(seed.raw), indent=2) + "\n")
    action = bytes.fromhex("032003049d0004")
    transition = bytes.fromhex("030003049c0004")
    cases = [
        ("grow-action", "zoom", action, action + bytes.fromhex("032003049d0204")),
        ("grow-transition", "transition", transition, transition + bytes.fromhex("030c03049c0204")),
        ("empty-action", "zoom", action, b""),
        ("invalid-transition-output", "transition", transition, action),
    ]
    with (args.output / "cases.jsonl").open("w") as log:
        for tag, kind, old_code, new_code in cases:
            directory = args.output / tag
            directory.mkdir()
            patched, mutation = replace_child(source, seed.logical_name, kind, old_code, new_code)
            path = directory / "patched.gxw"
            path.write_bytes(patched)
            (directory / "mutation.json").write_text(json.dumps(mutation, indent=2) + "\n")
            outcome = run(path, directory / "native", compile=True, change_sfc=True,
                          snapshot_frontend=True, export_project=True, program_check=True)
            snapshot = directory / "native/frontend-snapshot.json"
            matches = None
            if snapshot.exists():
                observed = native_children(json.loads(snapshot.read_text()))
                matches = all(observed.get((c["kind"], c["name"])) == bytes.fromhex(c["token_hex"])
                              for c in mutation["after"]["children"])
            row = dict(case=tag, outcome=outcome, native_child_bytes_match=matches)
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(tag, "native matches", matches, "rejected", outcome["compiler_rejected"],
                  "code bytes", outcome["outputs"].get("pcode-0-0.bin", {}).get("size"), flush=True)


if __name__ == "__main__":
    main()

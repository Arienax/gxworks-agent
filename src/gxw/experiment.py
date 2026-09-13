"""Reproducible GXW experiments and source-level round-trip comparisons."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from difflib import SequenceMatcher, unified_diff
import json
from pathlib import Path

from .connectivity import build_connectivity_graph
from .container import CompoundFile
from .decoder import read_structured_program
from .models import NodeKind, StructuredProgram
from .project_metadata import logical_mapping
from .project_writer import binary_diff, build_gxw_project, record_manifest, sha256, write_new_file
from .semantic import build_semantic_model
from .structured_pou import parse_structured_pou
from .structured_pou_writer import insert_series_contact_after, replace_node_symbol
from .structured_writer import add_detached_node, insert_parallel_contact, simple_rung_template


def canonical_program(program: StructuredProgram) -> dict:
    """Separate device identity, port topology, wire geometry and semantic roles.

    Byte offsets and net numbering are not stable identities after editor saves.
    Repeated identities use spatial occurrence order, retained in the snapshot.
    """
    groups = list(program.block_records())
    membership = {r.offset: i for i, (_, records) in enumerate(groups) for r in records}
    nodes = sorted(program.nodes, key=lambda n: (n.kind_code, n.symbol, n.type_name or "",
                                                membership[n.offset], n.bbox.top, n.bbox.left, n.bbox.bottom, n.bbox.right))
    keys, counts = {}, Counter()
    for node in nodes:
        identity = (node.kind_code, node.symbol, node.type_name)
        occurrence = counts[identity]
        counts[identity] += 1
        keys[node.offset] = json.dumps([*identity, occurrence], ensure_ascii=False)
    graph = build_connectivity_graph(program)
    net_keys = {}
    topology = []
    for net in graph.nets:
        endpoints = sorted([keys[p.node_offset], p.port_index] for p in net.ports)
        net_keys[net.index] = endpoints
        if endpoints:
            topology.append(endpoints)
    semantic = build_semantic_model(program, connectivity=graph)
    semantic_nodes = []
    for element in (*semantic.contacts, *semantic.coils, *semantic.functions, *semantic.function_blocks):
        ports = [{"index": p.port_index, "role": p.role.value,
                  "port_kind_code": p.port_kind_code,
                  "formal": getattr(p, "formal_name", None), "net": net_keys[p.net_index]}
                 for p in element.ports]
        semantic_nodes.append({"key": keys[element.node_offset], "ports": ports,
                               "polarity": getattr(getattr(element, "polarity", None), "value", None)})
    for terminal in semantic.terminals:
        semantic_nodes.append({"key": keys[terminal.node_offset], "role": terminal.role.value,
                               "port_kind_code": terminal.port_kind_code,
                               "net": net_keys[terminal.net_index], "unresolved": terminal.unresolved})
    wires = sorted([sorted([[w.start.x, w.start.y], [w.end.x, w.end.y]]) for w in program.wires])
    blocks = [{"height": block.canvas_height,
               "nodes": sorted(keys[r.offset] for r in records if hasattr(r, "symbol")),
               "wires": sorted(sorted([[r.start.x, r.start.y], [r.end.x, r.end.y]])
                               for r in records if hasattr(r, "start")),
               "unknown_records": [r.raw.hex() for r in records if hasattr(r, "record_class")]}
              for block, records in groups]
    return {"blocks": blocks, "devices": sorted([[k[0], k[1], k[2], count] for k, count in counts.items()], key=repr),
            "topology": sorted(topology, key=repr), "wires": wires,
            "semantic_graph": sorted(semantic_nodes, key=lambda n: n["key"]),
            "unknown_records": [r.raw.hex() for r in program.unknown_records]}


def compare_programs(before: StructuredProgram, after: StructuredProgram) -> dict:
    left, right = canonical_program(before), canonical_program(after)
    return {"checks": {key: left[key] == right[key] for key in left}, "before": left, "after": right}


def cfb_snapshot(raw: bytes) -> dict:
    """Full raw directory slots/tables, independent of stream ID assumptions."""
    cfb = CompoundFile(raw)
    directory = cfb._read_regular_stream(cfb.first_directory_sector)
    streams = {}
    for entry in cfb.iter_streams():
        mini = entry.stream_size < cfb.mini_stream_cutoff
        streams[entry.name] = {**asdict(entry), "storage": "mini" if mini else "regular",
                              "chain": cfb._walk_chain(entry.start_sector, cfb._minifat if mini else cfb._fat)}
    return {"header_hex": raw[:cfb.sector_size].hex(), "length": len(raw),
            "directory_slots_hex": [directory[i:i + 128].hex() for i in range(0, len(directory), 128)],
            "fat": cfb._fat, "minifat": cfb._minifat, "streams": streams}


def field_diff(left, right, path="") -> list:
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        return [change for key in sorted(left.keys() | right.keys())
                for change in field_diff(left.get(key), right.get(key), f"{path}/{key}")]
    if isinstance(left, list) and isinstance(right, list):
        return [change for i in range(max(len(left), len(right)))
                for change in field_diff(left[i] if i < len(left) else None,
                                         right[i] if i < len(right) else None, f"{path}/{i}")]
    return [{"field": path, "before": left, "after": right}]


def compare_projects(before: bytes, after: bytes) -> dict:
    a, b = CompoundFile(before), CompoundFile(after)
    ma = logical_mapping(a.read_stream("projectdatalist.xml"))
    mb = logical_mapping(b.read_stream("projectdatalist.xml"))
    ha, hb = CompoundFile(a.read_stream("_hdb")), CompoundFile(b.read_stream("_hdb"))
    logical_diffs, programs = {}, {}
    for name in sorted(ma.keys() | mb.keys()):
        # Mapping may include folders (non-stream entries).
        old = ha.read_stream(ma[name]) if name in ma and ha.find_streams(ma[name]) else None
        new = hb.read_stream(mb[name]) if name in mb and hb.find_streams(mb[name]) else None
        if old != new:
            logical_diffs[name] = {"old_length": len(old) if old is not None else None,
                                   "new_length": len(new) if new is not None else None,
                                   "before_sha256": sha256(old) if old is not None else None,
                                   "after_sha256": sha256(new) if new is not None else None,
                                   "binary_changes": binary_diff(old or b"", new or b"")}
        if name.endswith(".Program.pou") and old is not None and new is not None:
            try:
                p, q = parse_structured_pou(old, logical_name=name), parse_structured_pou(new, logical_name=name)
            except ValueError as exc:
                programs[name] = {"status": "unknown", "parse_error": str(exc)}
                continue
            left_records, right_records = record_manifest(p), record_manifest(q)
            matcher = SequenceMatcher(a=[r["sha256"] for r in left_records],
                                      b=[r["sha256"] for r in right_records], autojunk=False)
            programs[name] = {"binary_changes": binary_diff(old, new),
                              "counts_before": {"nodes": len(p.nodes), "wires": len(p.wires), "records": p.record_count, "blocks": len(p.blocks)},
                              "counts_after": {"nodes": len(q.nodes), "wires": len(q.wires), "records": q.record_count, "blocks": len(q.blocks)},
                              "records_before": left_records, "records_after": right_records,
                              "record_changes": [{"operation": op, "before": left_records[i:j], "after": right_records[k:l]}
                                                 for op, i, j, k, l in matcher.get_opcodes() if op != "equal"],
                              "roundtrip": compare_programs(p, q)}
    outer_diffs = {}
    for name in sorted({e.name for e in a.iter_streams()} | {e.name for e in b.iter_streams()}):
        if name == "_hdb":
            continue
        old = a.read_stream(name) if a.find_streams(name) else b""
        new = b.read_stream(name) if b.find_streams(name) else b""
        if old != new:
            item = {"binary_changes": binary_diff(old, new)}
            if name.endswith(".xml"):
                # Raw byte diff above is authoritative; text is a readable view.
                item["text_diff"] = list(unified_diff(old.decode("utf-8", errors="replace").splitlines(),
                                                        new.decode("utf-8", errors="replace").splitlines(),
                                                        fromfile="before/" + name, tofile="after/" + name))
            outer_diffs[name] = item
    return {"schema_version": 1, "before_sha256": sha256(before), "after_sha256": sha256(after),
            "programs": programs, "logical_stream_changes": logical_diffs, "outer_stream_changes": outer_diffs,
            "cfb_metadata_changes": {"outer": field_diff(cfb_snapshot(before), cfb_snapshot(after)),
                                     "nested": field_diff(cfb_snapshot(a.read_stream("_hdb")), cfb_snapshot(b.read_stream("_hdb")))}}


def run_regression(baseline: Path, output_dir: Path, results_dir: Path, *, logical_name=None,
                   symbol="X100", contact="X2", coil="Y2") -> dict:
    raw = baseline.read_bytes()
    original = read_structured_program(baseline, logical_name=logical_name)
    source, _, _ = simple_rung_template(original)
    variants = {
        "A": ("original", None),
        "B": ("single_symbol", replace_node_symbol(original, source.symbol, symbol, node_offset=source.offset)),
        "C": ("detached_contact", add_detached_node(original, NodeKind.CONTACT, contact)),
        "D": ("detached_coil", add_detached_node(original, NodeKind.COIL, coil)),
        "E": ("series_contact", insert_series_contact_after(original, source.symbol, contact, node_offset=source.offset)),
        "F": ("parallel_branch", insert_parallel_contact(original, contact)),
    }
    report = {"schema_version": 1, "baseline_sha256": sha256(raw), "variants": {}}
    # Build every case first; no partial suite for unsupported allocations.
    built = {}
    for key, (label, model) in variants.items():
        result = build_gxw_project(raw, model) if model is not None else None
        data = result.data if result else raw
        entry = {"operation": label, "path": str((output_dir / f"{key}.gxw").resolve()),
                 "sha256": sha256(data), "write_report": result.report if result else None,
                 "comparison": compare_projects(raw, data), "gxworks_validation": "not_run"}
        if key in {"C", "D"}:
            entry["purpose"] = "Disconnected object-count control; compilation success is not expected"
        report["variants"][key] = entry
        built[key] = data
    paths = [output_dir / f"{key}.gxw" for key in built] + [results_dir / f"{key}.json" for key in built] + [results_dir / "regression.json"]
    if any(p.exists() for p in paths):
        raise FileExistsError("regression outputs already exist; use a new directory")
    for key, data in built.items():
        write_new_file(output_dir / f"{key}.gxw", data)
        write_new_file(results_dir / f"{key}.json", json.dumps(report["variants"][key], ensure_ascii=False, indent=2).encode())
    index = {"schema_version": 1, "baseline_sha256": report["baseline_sha256"],
             "variants": {key: {"operation": entry["operation"], "sha256": entry["sha256"],
                                 "path": entry["path"], "report": f"{key}.json",
                                 "gxworks_validation": entry["gxworks_validation"]}
                          for key, entry in report["variants"].items()}}
    write_new_file(results_dir / "regression.json", json.dumps(index, ensure_ascii=False, indent=2).encode())
    return report

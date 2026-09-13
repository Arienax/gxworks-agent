"""Research-only test of the block-envelope hypothesis; no production writes.

The framing below was proposed from native A/E on 2026-09-13. Rejection is
reported explicitly. This is not native validation and is not a writer ABI.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container import CompoundFile
from gxw.experiment import compare_projects, field_diff
from gxw.project_metadata import logical_mapping
from gxw.project_writer import sha256, write_new_file
from gxw.structured_pou import _parse_node, _parse_wire


def digest(raw):
    return {"size": len(raw), "sha256": sha256(raw)}


def probe(raw):
    def u32(pos):
        if pos < 0 or pos + 4 > len(raw):
            raise ValueError(f"truncated DWORD at {pos}")
        return struct.unpack_from("<I", raw, pos)[0]

    result = {"status": "hypothesis_only", **digest(raw), "blocks": []}
    try:
        if len(raw) < 95:
            raise ValueError("short envelope")
        count = u32(0x43)
        if count > (len(raw) - 71) // 24:
            raise ValueError("block count exceeds physical capacity")
        result["prefix_hex"] = raw[:71].hex()
        result["candidate_block_count"] = count
        cursor = 71
        for index in range(count):
            start = cursor
            length, records = u32(start), u32(start + 20)
            end = start + length
            if length < 24 or end > len(raw) - 24 or records > (length - 24) // 8:
                raise ValueError(f"invalid block framing at {start}")
            block = {"index": index, "offset": start, **digest(raw[start:end]),
                     "header_hex": raw[start:start + 24].hex(),
                     "candidate_height": u32(start + 16), "candidate_record_count": records,
                     "records": []}
            cursor += 24
            for ri in range(records):
                size, cls = u32(cursor), u32(cursor + 4)
                if size < 8 or cursor + size > end:
                    raise ValueError(f"record crosses block end at {cursor}")
                data = raw[cursor:cursor + size]
                r = {"offset": cursor, "class": cls, **digest(data), "raw_hex": data.hex()}
                if cls == 1:
                    node = _parse_node(data, cursor)
                    r.update(kind=node.kind_code, symbol=node.symbol, type_name=node.type_name,
                             bbox=asdict(node.bbox),
                             ports=[{k: v for k, v in asdict(p).items() if k != "raw"} for p in node.ports])
                elif cls == 2:
                    wire = _parse_wire(data, cursor)
                    r.update(start=asdict(wire.start), end=asdict(wire.end),
                             prefix_fields=list(wire.prefix_fields), suffix=wire.suffix)
                else:
                    r["status"] = "opaque_unknown_class_preserved"
                block["records"].append(r)
                cursor += size
            if cursor != end:
                raise ValueError(f"unconsumed bytes in block {index}: {end - cursor}")
            result["blocks"].append(block)
        if raw[cursor:] != bytes(24):
            raise ValueError(f"unexpected trailer at {cursor}")
        result["trailer_hex"] = raw[cursor:].hex()
        result["candidate_global_lengths"] = {str(pos): u32(pos) for pos in (0x37, 0x3b)}
    except (ValueError, struct.error) as exc:
        result.update(status="hypothesis_rejected", error=str(exc), raw_hex=raw.hex())
    return result


def snapshot(raw):
    outer = CompoundFile(raw)
    mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    reverse = {}
    for name, sid in mapping.items():
        reverse.setdefault(sid, []).append(name)
    nested = CompoundFile(outer.read_stream("_hdb"))
    streams = {}
    programs = {}
    for layer, cfb in (("outer", outer), ("nested", nested)):
        for entry in cfb.iter_streams():
            payload = cfb.read_entry(entry)
            names = reverse.get(entry.name, []) if layer == "nested" else [entry.name]
            key = layer + "/" + entry.name
            streams[key] = {"logical_names": names, **digest(payload)}
            if any(name.endswith(".Program.pou") for name in names):
                programs[names[0]] = probe(payload)
    return {**digest(raw), "streams": streams, "programs": programs}


def xml_fields(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD/entity not supported")
    root = ET.fromstring(raw)
    fields = {}
    def walk(node, path):
        fields[path + "/text"] = node.text
        for key, value in node.attrib.items():
            fields[path + "/@" + key] = value
        counts = Counter()
        for child in node:
            index = counts[child.tag]
            counts[child.tag] += 1
            walk(child, f"{path}/{child.tag}[{index}]")
    walk(root, "/" + root.tag)
    return fields


def compare(before, after):
    # Reuse the existing logical resolution, CFB diff, binary replay and parser
    # comparison. Add all-stream inventory and hypothesis framing diagnostics.
    report = compare_projects(before, after)
    left, right = snapshot(before), snapshot(after)
    report["full_inventory"] = {"before": left, "after": right}
    report["inventory_changes"] = field_diff(left["streams"], right["streams"])
    report["xml_field_changes"] = {}
    a, b = CompoundFile(before), CompoundFile(after)
    for name in sorted({e.name for e in a.iter_streams()} | {e.name for e in b.iter_streams()}):
        if name.endswith(".xml"):
            old = a.read_stream(name) if a.find_streams(name) else b""
            new = b.read_stream(name) if b.find_streams(name) else b""
            if old != new:
                try:
                    report["xml_field_changes"][name] = field_diff(xml_fields(old), xml_fields(new))
                except (ValueError, ET.ParseError) as exc:
                    report["xml_field_changes"][name] = {"status": "unparsed", "error": str(exc)}
    report["hypothesis_record_alignment"] = {}
    report["aligned_node_fields"] = {}
    for name in left["programs"].keys() & right["programs"].keys():
        p, q = left["programs"][name], right["programs"][name]
        lp = [r for block in p["blocks"] for r in block["records"]]
        rq = [r for block in q["blocks"] for r in block["records"]]
        matcher = SequenceMatcher(a=[r["sha256"] for r in lp], b=[r["sha256"] for r in rq], autojunk=False)
        report["hypothesis_record_alignment"][name] = [
            {"operation": op, "before_indices": [i, j], "after_indices": [k, l]}
            for op, i, j, k, l in matcher.get_opcodes()]
        def keyed_nodes(program):
            occurrences = Counter()
            result = {}
            for block in program['blocks']:
                for r in block['records']:
                    if r['class'] != 1:
                        continue
                    identity = (r['kind'], r['symbol'], r['type_name'])
                    occurrence = occurrences[identity]
                    occurrences[identity] += 1
                    key = json.dumps([*identity, occurrence], ensure_ascii=False)
                    result[key] = {**r, 'block_index': block['index']}
            return result
        lnodes, rnodes = keyed_nodes(p), keyed_nodes(q)
        report['aligned_node_fields'][name] = []
        for key in sorted(lnodes.keys() & rnodes.keys()):
            lnode, rnode = lnodes[key], rnodes[key]
            fields = lambda r: {k: v for k, v in r.items() if k not in ('offset', 'raw_hex', 'sha256')}
            report['aligned_node_fields'][name].append({
                'identity': json.loads(key), 'before_offset': lnode['offset'], 'after_offset': rnode['offset'],
                'field_changes': field_diff(fields(lnode), fields(rnode)),
                'matching_policy': 'kind/symbol/type/occurrence; repeated identities require review'})
    report["noise_policy"] = "No bytes ignored. Same-file no-edit saves identify noise separately."
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    report = compare(args.before.read_bytes(), args.after.read_bytes())
    write_new_file(args.output, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps({"output": str(args.output), "programs": {
        side: {name: {"status": p["status"], "blocks": len(p["blocks"])} for name, p in snap["programs"].items()}
        for side, snap in report["full_inventory"].items()},
        "changed_logical_streams": list(report["logical_stream_changes"])}, ensure_ascii=False))

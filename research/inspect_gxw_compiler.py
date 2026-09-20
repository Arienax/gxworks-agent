"""Inspect compiler storage fragments and verify their actual link ranges.

Compiled cache membership requires both the native link flag and exact bytes.
Current source freshness remains unknown, even when every artifact agrees.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from gxw_corpus import inputs, independent_cfb
from gxw.container import CompoundFile
from gxw.compiler_storage import parse_compiler_index, parse_compiler_link_map
from gxw.compiler_tables import parse_compiler_tables
from gxw.compiler_debug import parse_compiler_debug
from gxw.compiler_call_tree import parse_compiler_call_tree
from gxw.compiler_assignment import parse_compiler_assignment
from gxw.compiler_symbols import (compiler_symbols, compiler_symbol_paths, compiler_operand_occurrences,
                                  compiler_declaration_bindings)
from gxw.declarations import parse_declarations
from gxw.lossless import inspect_project, sha256
from gxw.token_pou import parse_token_fragment
from gxw.token_listing import decode_token_program
from gxw.token_resource import parse_token_resource
from native_gxw_tokens import native_batch, native_il_records, listing_records, canonical_record


def inspect_storage(raw, *, encoding="cp936"):
    cfb = CompoundFile(raw)
    index = parse_compiler_index(cfb.read_stream("Index"))
    result = {"storage_sha256": sha256(raw), "raw_base64": base64.b64encode(raw).decode(),
              "index_replay": index.reconstruct() == index.raw, "independent_cfb": independent_cfb(raw),
              "entries": [], "maps": [], "source_freshness": "unknown; stored compiler state"}
    resolved, fragments = {}, {}
    for entry in index.entries:
        item = {"index_offset": entry.offset, "stream_id": entry.stream_id, "kind_code": entry.kind_code,
                "name_hex": entry.name_bytes.hex(), "stream": entry.stream_name}
        result["entries"].append(item)
        try:
            item["name"] = entry.display_name(encoding)
            payload = cfb.read_stream(entry.stream_name)
            item.update(size=len(payload), sha256=sha256(payload))
            resolved.setdefault(item["name"], []).append((item, payload))
            if item["name"].endswith(".qpg"):
                fragment = parse_token_fragment(payload, 0, len(payload))
                listing = decode_token_program(fragment, text_encoding=encoding)
                item.update(token_replay=listing.reconstruct() == payload, records=listing_records(listing), gaps=len(listing.gaps))
                if payload:
                    fragments.setdefault(sha256(payload), {"body_base64": base64.b64encode(payload).decode(), "names": []})["names"].append(item["name"])
        except (ValueError, KeyError) as exc:
            item["unsupported"] = str(exc)
    for name, values in resolved.items():
        if not name.endswith(".map") or name[:-4] + ".qpg" not in resolved:
            continue
        map_case = {"name": name, "code_name": name[:-4] + ".qpg"}
        result["maps"].append(map_case)
        code = resolved[map_case["code_name"]]
        if len(values) != 1 or len(code) != 1:
            map_case["unsupported"] = "ambiguous map/code index entries"
            continue
        _, body = code[0]
        try:
            mapping = parse_compiler_link_map(values[0][1])
        except ValueError as exc:
            map_case["unsupported"] = str(exc)
            continue
        map_case.update(code_sha256=sha256(body), code_bytes=len(body), map_replay=mapping.reconstruct() == mapping.raw,
                        opaque_tail_offset=mapping.tail_offset, opaque_tail_bytes=len(mapping.opaque_tail), rows=[])
        accepted, unresolved_active = [], False
        for entry in mapping.entries:
            start, length = entry.token_offset, entry.token_length
            row = {"offset": entry.offset, "name_hex": entry.name_bytes.hex(), "fields": list(entry.fields),
                   "step_offset": entry.step_offset, "step_count": entry.step_count,
                   "token_offset": start, "token_length": length, "unlinked": entry.unlinked}
            map_case["rows"].append(row)
            try:
                row["name"] = entry.name_bytes.decode(encoding)
            except UnicodeError:
                pass
            if not length:
                row["range_status"] = "empty"
                continue
            if start + length > len(body):
                row["range_status"] = "outside-linked-code"
                unresolved_active |= not entry.unlinked
                continue
            segment = body[start:start + length]
            matches = [n for n, vals in resolved.items() if n.endswith(".qpg") and n != map_case["code_name"]
                       and any(p == segment for _, p in vals)]
            row.update(range_status="exact-fragment" if matches else "unmatched-range", matching_fragments=matches,
                       body_sha256=sha256(segment))
            if matches and not entry.unlinked:
                accepted.append((start, length, entry.step_offset, entry.step_count, segment))
            if not matches and not entry.unlinked:
                unresolved_active = True
        # Both native link membership and exact bytes are necessary. Renamed
        # cached entries may match live bytes while remaining explicitly unlinked.
        cursor, step, complete = 0, 0, not unresolved_active
        for start, length, word_start, words, _ in sorted(accepted):
            complete &= start == cursor and word_start == step
            cursor, step = start + length, word_start + words
        map_case.update(complete_fragment_partition=complete and cursor == len(body), partition_steps=step,
                        reconstructed_code_matches=b"".join(p[-1] for p in sorted(accepted)) == body)
    return result, fragments


def inspect_compiler_artifact(name, raw, *, encoding="cp936"):
    result = {"kind": name, "sha256": sha256(raw), "raw_base64": base64.b64encode(raw).decode()}
    if name == "CGTable.dat":
        tables = parse_compiler_tables(raw)
        result.update(replay=tables.reconstruct() == raw,
                      tables=[{"index": t.index, "offset": t.offset, "bytes": len(t.raw), "records": len(t.records)}
                              for t in tables.tables], pous=[])
        result["declared_addresses"] = []
        for record in tables.tables[4].records:
            address = tables.address_at(record.table_offset)
            result["declared_addresses"].append({"table_offset": record.table_offset,
                "location_code": address.location_code, "size_code": address.size_code,
                "name_hex": address.name_bytes.hex(), "reference_count": address.reference_count,
                "iec_address": address.iec_address_bytes.decode(encoding) if address.iec_address_bytes else None,
                "opaque_tail_hex": address.opaque_tail.hex()})
        result["arrays"] = []
        for record in tables.tables[13].records:
            entry = {"table_offset": record.table_offset, "raw_hex": record.raw.hex()}
            result["arrays"].append(entry)
            try:
                array = tables.array_at(record.table_offset)
                entry.update(element_type=array.element_type, element_parameter=array.element_parameter,
                    total_count=array.total_count, count_matches_dimensions=array.count_matches_dimensions,
                    dimensions=[{"lower": d.lower, "extent": d.extent, "upper": d.upper} for d in array.dimensions],
                    type_padding_hex=array.type_padding.hex(), opaque_tail_hex=array.opaque_tail.hex())
            except ValueError as exc:
                entry["unsupported"] = str(exc)
        for record in tables.tables[1].records:
            item = {"table_offset": record.table_offset}
            result["pous"].append(item)
            try:
                pou = tables.pou_at(record.table_offset)
                item.update(name=pou.name_bytes.decode(encoding), fields=list(pou.fields), components=[])
                for component in tables.components(pou):
                    entry = {"table_offset": component.record.table_offset, "name": component.name_bytes.decode(encoding),
                             "type_code": tables.component_type(component), "fields_hex": component.fields_raw.hex(),
                             "global_offset": component.global_offset, "user_info_hex": component.user_info.hex()}
                    assignment = parse_compiler_assignment(component.user_info)
                    entry["assignment"] = {"status": assignment.status, "iec_address": assignment.iec_address,
                        "fx_operand": assignment.fx_operand, "role": assignment.role, "reserved_count": assignment.reserved_count}
                    entry["declared_address_offset"] = tables.component_address_offset(component)
                    entry["array_offset"] = tables.component_array_offset(component)
                    item["components"].append(entry)
                    reference = component.instance_pou_offset
                    if reference is not None:
                        entry["instance_pou_offset"] = reference
                        entry["instance_pou_name"] = tables.pou_at(reference).name_bytes.decode(encoding)
            except ValueError as exc:
                item["unsupported"] = str(exc)
        targets = {c.instance_pou_offset for c in (s.component for s in compiler_symbols(tables))
                   if c.instance_pou_offset is not None}
        result["instance_paths"] = []
        for record in tables.tables[1].records:
            if record.table_offset in targets:
                continue
            # An unreferenced POU is a traversal root, not an inferred live program.
            try:
                result["instance_paths"].extend({"root_pou_offset": path.root_pou_offset,
                    "names": [n.decode(encoding) for n in path.names], "component_offsets": path.component_offsets,
                    "pou_offset": path.symbol.pou_offset} for path in compiler_symbol_paths(tables, record.table_offset))
            except ValueError as exc:
                result.setdefault("path_gaps", []).append({"root_pou_offset": record.table_offset, "reason": str(exc)})
    elif name == "CallTree.dat":
        tree = parse_compiler_call_tree(raw)
        result.update(replay=tree.reconstruct() == raw, maps=[],
                      resource_offset=tree.resource_offset,
                      resource_tree_hex=tree.resource_tree.hex() if tree.resource_tree is not None else None,
                      opaque_tail_hex=tree.opaque_tail.hex())
        for nodes in tree.maps:
            result["maps"].append([{"offset": n.offset, "flags": n.flags, "kind_code": n.kind_code,
                "names": [s.text(encoding) for s in n.names], "ascii_key": n.ascii_key,
                "references": [{"offset": s.offset, "text": s.text(encoding), "ascii_key": s.ascii_key}
                               for s in n.references]} for n in nodes])
        forward, reverse = (tree.references(index) for index in (1, 2))
        known = lambda rows: Counter((e.source_key, e.target_key) for e in rows
                                     if e.source_key is not None and e.target_key is not None)
        a, b = known(forward), known(reverse)
        result["reference_consistency"] = {"forward_count": len(forward), "used_by_count": len(reverse),
            "reciprocal_count": sum((a & b).values()),
            "forward_only": [{"source": s, "target": t, "count": count} for (s, t), count in (a - b).items()],
            "used_by_only": [{"source": s, "target": t, "count": count} for (s, t), count in (b - a).items()],
            "non_ascii_or_nul_references": sum(e.source_key is None or e.target_key is None for e in (*forward, *reverse)),
            "scope": "stored reference indexes; ASCII case normalization only; not live linked calls"}
    elif name == "DebugInformation2.dat":
        debug = parse_compiler_debug(raw)
        result.update(replay=debug.reconstruct() == raw, opaque_tail_offset=debug.tail_offset,
                      opaque_tail_bytes=len(debug.opaque_tail), offset_tables=[], elements=[])
        for table in debug.offset_tables:
            result["offset_tables"].append({"offset": table.offset, "kind_code": table.kind_code,
                "declared_size": table.declared_size, "rows": table.rows, "sentinel": table.sentinel})
        for element in debug.elements:
            result["elements"].append({"offset": element.offset, "resource": element.resource_bytes.decode(encoding),
                "fields": element.fields, "names": [n.decode(encoding) for n in element.names],
                "kind_code": element.kind_code, "extra_name": element.extra_name.decode(encoding),
                "offset_table_index": element.offset_table_index})
    return result


def inspect_projects(paths, directory, *, native=False, encoding="cp936"):
    directory.mkdir(parents=True, exist_ok=False)
    storages, fragments, failures, artifacts, bindings = {}, {}, [], {}, {}
    for location, source in inputs(paths):
        image = inspect_project(source)
        source_artifacts = []
        for stream in image.streams:
            if stream.logical_name not in ("CGTable.dat", "DebugInformation2.dat", "CallTree.dat") or stream.raw is None:
                continue
            key = sha256(stream.raw)
            source_artifacts.append({"kind": stream.logical_name, "sha256": key})
            if key not in artifacts:
                try:
                    artifacts[key] = inspect_compiler_artifact(stream.logical_name, stream.raw, encoding=encoding)
                except ValueError as exc:
                    artifacts[key] = {"kind": stream.logical_name, "sha256": key, "unsupported": str(exc),
                                      "raw_base64": base64.b64encode(stream.raw).decode()}
        local_labels = [s for s in image.streams if (s.logical_name or "").endswith(".Labels.lh") and s.raw is not None]
        for table_stream in (s for s in image.streams if s.logical_name == "CGTable.dat" and s.raw is not None):
            for labels in local_labels:
                table_hash, label_hash = sha256(table_stream.raw), sha256(labels.raw)
                key = f"{table_hash}/{label_hash}/{labels.logical_name}"
                if key not in bindings:
                    item = {"table_sha256": table_hash, "declaration_sha256": label_hash,
                            "logical_name": labels.logical_name, "sources": []}
                    bindings[key] = item
                    try:
                        tables = parse_compiler_tables(table_stream.raw)
                        document = parse_declarations(labels.raw, logical_name=labels.logical_name)
                        item["owner_name"] = document.owner_name
                        item["rows"] = [{"declaration_offset": b.declaration_offset, "name": b.name,
                            "declared_type": b.declared_type, "status": b.status, "owner_offsets": b.owner_offsets,
                            "candidates": [{"pou_offset": c.pou_offset, "component_offset": c.component_offset,
                                "compiled_type_code": c.compiled_type_code,
                                "instance_type_name": c.instance_type_name.decode(encoding) if c.instance_type_name is not None else None,
                                "instance_type_agrees": c.instance_type_agrees} for c in b.candidates]}
                            for b in compiler_declaration_bindings(tables, document, encoding=encoding)]
                    except ValueError as exc:
                        item["unsupported"] = str(exc)
                bindings[key]["sources"].append(location)
        for stream in image.streams:
            if stream.logical_name != "ESCompiler.stg" or stream.raw is None:
                continue
            key = sha256(stream.raw)
            if key not in storages:
                try:
                    case, found = inspect_storage(stream.raw, encoding=encoding)
                    case["sources"] = []
                    storages[key] = case
                    for digest, fragment in found.items():
                        target = fragments.setdefault(digest, dict(fragment, names=[], storages=[]))
                        target["storages"].append(key)
                        target["names"] = sorted(set(target["names"]) | set(fragment["names"]))
                except (ValueError, KeyError) as exc:
                    failures.append({"location": location, "storage_sha256": key, "error": str(exc)})
                    continue
            resource_bodies = []
            for resource in image.streams:
                if (resource.logical_name or "").endswith(".res") and resource.raw is not None:
                    try:
                        resource_bodies.extend({"resource": resource.logical_name, "body_sha256": sha256(p.body)}
                                               for p in parse_token_resource(resource.raw).code_regions)
                    except ValueError:
                        pass
            storages[key]["sources"].append({"location": location, "project_sha256": image.sha256,
                                            "resource_bodies": resource_bodies, "compiler_artifacts": source_artifacts,
                                            "resource_matches": [{"map": m["name"], "matches": [r for r in resource_bodies
                                                if r["body_sha256"] == m.get("code_sha256")]} for m in storages[key]["maps"]]})
    result = {"scope": "stored compiler artifacts; no current source freshness or execution claim",
              "storages": list(storages.values()), "fragments": fragments,
              "compiler_artifacts": artifacts, "declaration_bindings": list(bindings.values()), "failures": failures}
    result["symbol_cross_references"] = []
    for storage in storages.values():
        table_hashes = {a["sha256"] for source in storage["sources"] for a in source["compiler_artifacts"]
                        if a["kind"] == "CGTable.dat"}
        for table_hash in sorted(table_hashes):
            try:
                tables = parse_compiler_tables(base64.b64decode(artifacts[table_hash]["raw_base64"]))
                symbols = compiler_symbols(tables)
                for mapping in storage["maps"]:
                    code_hash = mapping.get("code_sha256")
                    if code_hash not in fragments:
                        continue
                    body = base64.b64decode(fragments[code_hash]["body_base64"])
                    listing = decode_token_program(parse_token_fragment(body, 0, len(body)), text_encoding=encoding)
                    occurrences = compiler_operand_occurrences(symbols, listing)
                    matched = []
                    for occurrence in occurrences:
                        if not occurrence.component_offsets:
                            continue
                        owners = [row["name"] for row in mapping["rows"] if not row["unlinked"]
                                  and row["token_offset"] <= occurrence.instruction_offset < row["token_offset"] + row["token_length"]]
                        matched.append({"instruction_offset": occurrence.instruction_offset, "step": occurrence.step,
                            "mnemonic": occurrence.mnemonic, "operand_index": occurrence.operand_index,
                            "operand_offset": occurrence.operand_offset, "text": occurrence.text,
                            "candidate_component_offsets": occurrence.component_offsets, "linked_fragment_owners": owners})
                    result["symbol_cross_references"].append({"storage_sha256": storage["storage_sha256"],
                        "table_sha256": table_hash, "code_sha256": code_hash, "map": mapping["name"],
                        "link_partition_complete": mapping["complete_fragment_partition"],
                        "operand_occurrences": len(occurrences), "matched_occurrences": matched,
                        "unmatched_occurrences": len(occurrences) - len(matched),
                        "opaque_instruction_records": len(listing.gaps),
                        "match_kind": "exact lexical operand; all aliases retained; no liveness or access-role inference"})
            except (ValueError, KeyError) as exc:
                result["symbol_cross_references"].append({"storage_sha256": storage["storage_sha256"],
                    "table_sha256": table_hash, "unsupported": str(exc)})
    output = directory / "compiler-storage.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if native and fragments:
        ordered = list(fragments.items())
        rows = native_batch([{"mode": "decode", "fragment_sha256": key,
                              "input_base64": base64.b64encode(base64.b64decode(f["body_base64"]) + b"\0").decode()}
                             for key, f in ordered], directory / "native-decode")
        machine = native_batch([{"mode": "machinecode", "fragment_sha256": key, "input_base64": f["body_base64"]}
                                for key, f in ordered], directory / "native-machinecode")
        for (key, fragment), row, code in zip(ordered, rows, machine):
            body = base64.b64decode(fragment["body_base64"])
            fragment.update(native_decode=row, native_machinecode=code)
            if row["return_code"] == "0x00000000":
                actual = native_il_records(base64.b64decode(row["output_base64"]), encoding=encoding)
                ours = listing_records(decode_token_program(parse_token_fragment(body, 0, len(body)), text_encoding=encoding))
                fragment["native_readings_agree"] = (row["consumed_bytes"] == len(body) and
                    [canonical_record(r) for r in actual] == [canonical_record(r) for r in ours])
    result["summary"] = {"storage_versions": len(storages), "references": sum(len(c["sources"]) for c in storages.values()),
                         "unique_nonempty_fragments": len(fragments), "maps": sum(len(c["maps"]) for c in storages.values()),
                         "map_status": dict(Counter("complete" if m.get("complete_fragment_partition") else "incomplete"
                                                    for c in storages.values() for m in c["maps"])),
                         "native_readings_agree": sum(f.get("native_readings_agree", False) for f in fragments.values()),
                         "compiler_artifacts": dict(Counter(a["kind"] for a in artifacts.values())),
                         "unsupported_artifacts": sum("unsupported" in a for a in artifacts.values()),
                         "declaration_binding_pairs": len(bindings),
                         "declaration_binding_status": dict(Counter(row["status"] for b in bindings.values() for row in b.get("rows", ()))),
                         "failures": len(failures)}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_projects(args.paths, args.output, native=args.native)["summary"]))

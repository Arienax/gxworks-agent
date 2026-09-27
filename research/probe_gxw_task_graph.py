"""Read observed resource/task/POU links with source-bound opaque preservation.

Resource ownership comes from current projectdatalist folder groups, not the
cached names in .res. A program's identity comes from its local-label row;
its source is the Program.pou in the same group, not necessarily the same
filename stem. Task references come from bounded .tsk entry records.
This is an experimental compile-root graph, not a call graph, scheduling model,
runtime reachability proof or task writer. Unknown records remain explicit.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container import CompoundFile
from gxw.lossless import inspect_project, sha256
from gxw.project_metadata import current_rows, logical_mapping
from gxw.source_header import source_payload_offset


_PREFIX = bytes.fromhex("01000000000001000000000001000a00000000000200000001000000010000000000")
_LEGACY_PREFIX = bytes.fromhex("01000000000001000000000000000000000000000200000001000000010000000000")


def _text(raw, offset):
    if offset + 4 > len(raw):
        raise ValueError("truncated task string count")
    count = struct.unpack_from("<I", raw, offset)[0]
    end = offset + 4 + count * 2
    if not 1 <= count <= 8192 or end > len(raw):
        raise ValueError("task string exceeds source boundary")
    text = raw[offset + 4:end].decode("utf-16le")
    if not text.endswith("\0") or "\0" in text[:-1]:
        raise ValueError("unsupported task string terminator")
    return text[:-1], end


def task_records(raw: bytes) -> dict:
    """Keep scheduling fields/text opaque; decode entry names/order values."""
    if not raw.startswith((_PREFIX, _LEGACY_PREFIX)) or len(raw) < 54:
        raise ValueError("outside observed task header")
    first, offset = _text(raw, 54)
    second, offset = _text(raw, offset)
    configuration = raw[offset:offset + 8]
    if configuration not in (bytes.fromhex("1f00000000000000"), bytes.fromhex("1a00000000000000")):
        raise ValueError("outside observed task configuration shape")
    offset += 8
    if offset + 4 > len(raw):
        raise ValueError("truncated task entry count")
    count = struct.unpack_from("<I", raw, offset)[0]
    offset += 4
    if count > 4096:
        raise ValueError("unbounded task entry count")
    prefix_end, entries = offset, []
    for index in range(count):
        start = offset
        name, offset = _text(raw, offset)
        if not name or offset + 4 > len(raw):
            raise ValueError("missing task program name/order")
        order_offset = offset
        order = struct.unpack_from("<I", raw, offset)[0]
        secondary, offset = _text(raw, offset + 4)
        entries.append(dict(index=index, offset=start, length=offset-start,
                            program_reference=name, stored_order=order, order_offset=order_offset,
                            secondary_text=secondary, secondary_text_role="unknown",
                            raw_hex=raw[start:offset].hex()))
    if offset != len(raw):
        raise ValueError("unknown task suffix or incorrect entry count")
    ordered = sorted(entries, key=lambda e: e["stored_order"])
    ambiguous = len({e["stored_order"] for e in entries}) != len(entries)
    # Native frontend sorting and ST output agree for distinct 0/1/2/3 values.
    # Do not predict ties or signed/high-bit ordering from source record order.
    order_known = not ambiguous and all(e["stored_order"] <= 3 for e in entries)
    reconstructed = raw[:prefix_end] + b"".join(bytes.fromhex(e["raw_hex"]) for e in entries)
    assert reconstructed == raw
    return dict(handling="partially-decoded", sha256=sha256(raw), bytes=len(raw),
                opaque_prefix_hex=raw[:prefix_end].hex(), uninterpreted_texts=[first, second],
                configuration_hex=configuration.hex(),
                entries=entries, observed_order=[e["index"] for e in ordered] if order_known else None,
                order_diagnostic=None if order_known else "tied or unobserved order values; raw order retained",
                reconstruction="byte-identical")


def inspect_task_graph(source: bytes) -> dict:
    image = inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError("incomplete source inventory")
    xml = CompoundFile(source).read_stream("projectdatalist.xml")
    mapping = logical_mapping(xml)  # Reject duplicate logical names/physical IDs.
    rows, encoding = current_rows(xml, "DSPROJECTDATA", "D_Projectdata")
    streams = {s.logical_name: s.raw for s in image.streams if s.logical_name and s.raw is not None}
    groups = defaultdict(list)
    metadata = []
    for row in rows:
        fields = {k: v.text.strip() for k, v in row.fields().items()}
        if fields.get("bScrapFlag", "false").lower() in ("true", "1"):
            continue
        name = fields["szName"]
        assert mapping[name] == fields["iID"]
        item = dict(logical_name=name, physical_stream=fields["iID"],
                    xml_offset=row.start, xml_length=row.end-row.start,
                    raw_xml_hex=xml[row.start:row.end].hex(), fields=fields,
                    payload_sha256=sha256(streams[name]) if name in streams else None)
        metadata.append(item)
        key = tuple(fields.get(k) for k in ("ucProductType", "ucFolderType", "uiFolderNo", "ucReserve"))
        if key[1] == "92":
            # uiFolderNo starts again in every library; grouping without its
            # namespace combines unrelated label/source pairs across libraries.
            namespace = name.rsplit("\\", 1)[1].rsplit(".", 1)[0] if "\\" in name else None
            key += (namespace,)
        groups[key].append(item)
    resources, task_images, programs, gaps = [], {}, {}, []
    source_programs = {name for name in streams if name.endswith((".pou", ".lnb"))}
    direct = set()
    paired_sources = set()
    for key, members in groups.items():
        sources = [m for m in members if m["logical_name"].endswith((".pou", ".lnb"))
                   and m["fields"].get("ucFileType") == "2"]
        if not sources:
            continue
        labels = [m for m in members if m["logical_name"].endswith((".lh", ".lnl"))
                  and m["fields"].get("ucFileType") == "1"]
        if (key[0] != "1" or key[1] not in ("7", "8", "92") or key[3] != "0"
                or len(sources) != 1 or len(labels) != 1 or len(members) != 2
                or sources[0]["fields"].get("ucFileType") != "2"
                or labels[0]["fields"].get("ucFileType") != "1"):
            gaps.append(dict(group=list(key), reason="outside observed local-label/source group",
                             members=[m["logical_name"] for m in members]))
            continue
        declaration, source_program = labels[0]["logical_name"], sources[0]["logical_name"]
        if declaration not in streams or source_program not in streams:
            gaps.append(dict(group=list(key), reason="program declaration or source payload is missing"))
            continue
        # English, Chinese, and empty middle name segments occur in native-
        # accepted real projects. Metadata roles bind the pair; the middle
        # segment is a display name, not a format identifier.
        library = None
        if key[1] == "92":
            label_parts = declaration.rsplit("\\", 1)
            source_parts = source_program.rsplit("\\", 1)
            if (len(label_parts) != 2 or len(source_parts) != 2 or
                    not declaration.endswith(".lnl") or not source_program.endswith(".lnb") or
                    label_parts[1][:-4] != source_parts[1][:-4]):
                gaps.append(dict(group=list(key), reason="library source/declaration namespace mismatch"))
                continue
            library = label_parts[1][:-4]
            parts = label_parts[0].rsplit(".", 1)
            source_stem = source_parts[0].rsplit(".", 1)[0]
        else:
            parts = declaration.rsplit(".", 2)
            source_stem = source_program.rsplit(".", 2)[0]
        if len(parts) != (2 if library is not None else 3):
            gaps.append(dict(group=list(key), reason="outside observed declaration name shape"))
            continue
        name = parts[0]
        if not name or name in programs:
            raise ValueError("ambiguous program identity in local-label rows")
        # The payload's embedded owner is preserved for comparison, not used as
        # a binding key: swapping it does not change the native frontend.
        try:
            embedded_owner, _ = _text(streams[declaration], source_payload_offset(streams[declaration]))
        except ValueError:
            embedded_owner = None
        programs[name] = dict(program_name=name, declarations=declaration,
            library=library, identity_scope="library-export" if library is not None else "project",
            source_program=source_program, folder_group=list(key),
            source_stem_agrees=source_stem == name,
            embedded_owner=embedded_owner, embedded_owner_agrees=embedded_owner == name,
            identity_metadata_offset=labels[0]["xml_offset"], source_metadata_offset=sources[0]["xml_offset"])
        paired_sources.add(source_program)
    for name in sorted(source_programs - paired_sources):
        gaps.append(dict(source_program=name, reason="source has no observed local-label identity"))
    for key, members in groups.items():
        if key[0] != "1" or key[1] != "3" or key[3] != "0":
            continue
        res = [m for m in members if m["fields"].get("ucFileType") == "1" and m["logical_name"].endswith(".res")]
        tasks = [m for m in members if m["fields"].get("ucFileType") == "2" and m["logical_name"].endswith(".tsk")]
        if len(res) != 1 or not tasks or len(members) != 1 + len(tasks):
            gaps.append(dict(group=list(key), reason="outside observed one-resource/task group", members=[m["logical_name"] for m in members]))
            continue
        for task_row in tasks:
            task = task_row["logical_name"]
            try:
                if task not in streams:
                    raise ValueError("task payload is missing")
                parsed = task_records(streams[task])
            except (ValueError, UnicodeError) as exc:
                parsed = dict(handling="opaque-preserved", reason=str(exc),
                              raw_hex=streams[task].hex() if task in streams else None)
                gaps.append(dict(task=task, reason=str(exc)))
            else:
                for entry in parsed["entries"]:
                    program = programs.get(entry["program_reference"]) if not entry["secondary_text"] else None
                    entry["source_program"] = program["source_program"] if program else None
                    entry["declarations"] = program["declarations"] if program else None
                    entry["library"] = program["library"] if program else None
                    if program:
                        direct.add(program["source_program"])
                    else:
                        gaps.append(dict(task=task, reference=entry["program_reference"], reason="unresolved task program reference"))
            task_images[task] = parsed
            resources.append(dict(resource=res[0]["logical_name"], task=task, folder_group=list(key),
                                  metadata_rows=[res[0]["xml_offset"], task_row["xml_offset"]]))
    all_tasks = {name for name in streams if name.endswith(".tsk")}
    for name in sorted(all_tasks-task_images.keys()):
        gaps.append(dict(task=name, reason="task has no observed resource folder association"))
    return dict(project_sha256=sha256(source), xml_sha256=sha256(xml), xml_encoding=encoding,
                resources=resources, tasks=task_images, programs=programs, gaps=gaps,
                resource_count=len({r["resource"] for r in resources}),
                source_pous_without_direct_task_reference=sorted(source_programs-direct),
                metadata=metadata,
                scope="Compile-root references only. A source POU may also be called elsewhere; no dead-code, runtime or writer claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_task_graph(args.source.read_bytes())
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(resources=result["resource_count"], tasks=len(result["tasks"]), gaps=len(result["gaps"]),
                          without_direct_reference=len(result["source_pous_without_direct_task_reference"]))))

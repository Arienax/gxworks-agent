"""JSON transport for the existing StructuredProgram model, not a new PLC IR.

New records use saved native ABI templates. Imported records retain their source
layout and opaque fields; geometry is the already verified editor grid.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .container_writer import validate_cfb_streams
from .declarations import parse_declarations, edit_declarations, CLASS_CODES
from .models import GXWFormatError, NodeKind, Point, Rect, StructuredBlock
from .project_metadata import logical_mapping
from .project_writer import build_gxw_project
from .semantic import DEFAULT_FUNCTION_BLOCK_REGISTRY, build_semantic_model
from .structured_pou import _parse_node, parse_structured_pou
from .structured_pou_writer import serialize_structured_pou


TEMPLATES = Path(__file__).with_name("templates")


@lru_cache(maxsize=1)
def native_catalog():
    result = {}
    for key, item in json.loads((TEMPLATES / "nodes.json").read_text()).items():
        raw = bytes.fromhex(item["record_hex"])
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise GXWFormatError("native node template hash mismatch")
        result[key] = _parse_node(raw, 0)
    return result


def default_baseline():
    raw = (TEMPLATES / "fx3u.gxw").read_bytes()
    provenance = json.loads((TEMPLATES / "provenance.json").read_text())
    if hashlib.sha256(raw).hexdigest() != provenance["baseline_sha256"]:
        raise GXWFormatError("GXW baseline template hash mismatch")
    return raw


def _key(node):
    return node.kind.value + (":" + str(node.type_name or node.symbol)
                             if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK) else "")


def _port_names(node):
    if node.kind == NodeKind.FUNCTION_BLOCK:
        spec = DEFAULT_FUNCTION_BLOCK_REGISTRY.get(node.type_name)
        if spec and len(spec.ports) == len(node.ports) and all(
            p.local_y == formal.local_y and
            p.local_x == (0 if formal.role.value.endswith("_in") else node.bbox.right-node.bbox.left)
            for p, formal in zip(node.ports, spec.ports)
        ):
            return [p.formal_name for p in spec.ports]
    if node.kind == NodeKind.FUNCTION and node.symbol == "MOV" and len(node.ports) == 4:
        return ["EN", "IN", "ENO", "OUT"]
    return {NodeKind.CONTACT: ["IN", "OUT"], NodeKind.CONTACT_NC: ["IN", "OUT"],
            NodeKind.COIL: ["IN", "OUT"], NodeKind.INPUT: ["OUT"], NodeKind.OUTPUT: ["IN"]}.get(
                node.kind, [str(i) for i in range(len(node.ports))])


def catalog_description():
    return [{"template": key, "kind": n.kind.value, "type_name": n.type_name,
             "symbol": n.symbol if n.kind == NodeKind.FUNCTION else "",
             "width": n.bbox.right - n.bbox.left, "height": n.bbox.bottom - n.bbox.top,
             "ports": [{"name": name, "x": p.local_x, "y": p.local_y}
                       for name, p in zip(_port_names(n), n.ports)]}
            for key, n in native_catalog().items()]


def read_project(raw, logical_name=None):
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer["projectdatalist.xml"])
    payloads = validate_cfb_streams(outer["_hdb"])
    names = sorted(k for k in mapping if k.endswith(".Program.pou") and mapping[k] in payloads)
    if logical_name is None:
        if len(names) != 1:
            raise GXWFormatError("select one Program.pou from this GXW project")
        logical_name = names[0]
    if logical_name not in names:
        raise GXWFormatError("selected Program.pou is missing")
    program = parse_structured_pou(payloads[mapping[logical_name]], logical_name=logical_name)
    declarations = {k: parse_declarations(payloads[v], logical_name=k) for k, v in mapping.items()
                    if k.endswith((".Labels.lh", ".gh")) and v in payloads}
    return program, declarations, names


def export_object_model(program, declarations=None):
    nodes = []
    for n in program.nodes:
        nodes.append({"id": f"n{n.offset}", "source_offset": n.offset, "template": _key(n),
                      "symbol": n.symbol, "x": n.bbox.left, "y": n.bbox.top,
                      "width": n.bbox.right - n.bbox.left, "height": n.bbox.bottom - n.bbox.top,
                      "ports": [{"name": name, "x": p.local_x, "y": p.local_y}
                                for name, p in zip(_port_names(n), n.ports)]})
    class_names = {v: k for k, v in CLASS_CODES.items()}
    labels = {name: [{"name": r.name, "data_type": r.data_type,
                     "kind": "function_block" if r.type_code == 15 else "variable",
                     "class_name": class_names.get(r.class_code, str(r.class_code)),
                     "initial_value": r.initial_value, "device": r.device,
                     "iec_address": r.iec_address, "comment": r.comment}
                    for r in doc.rows] for name, doc in (declarations or {}).items()}
    semantic = build_semantic_model(program)
    model = {"schema_version": 1, "program": program.logical_name, "canvas_height": program.canvas_height,
            "nodes": nodes, "wires": [{"source_offset": w.offset, "start": [w.start.x, w.start.y],
                                       "end": [w.end.x, w.end.y]} for w in program.wires],
            "labels": labels, "declaration_edits": {}, "unknown_record_count": len(program.unknown_records),
            "issues": [{"code": i.code, "message": i.message} for i in semantic.issues]}
    if len(program.blocks) > 1:
        groups = list(program.block_records())
        membership = {r.offset: index for index, (_, records) in enumerate(groups) for r in records}
        model["blocks"] = [{"source_offset": b.offset, "canvas_height": b.canvas_height} for b, _ in groups]
        for item in (*model["nodes"], *model["wires"]):
            item["block"] = membership[item["source_offset"]]
    return model


def _uint(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise GXWFormatError("editor coordinates must be uint32 integers")
    return value


def _point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise GXWFormatError("wire point requires [x, y]")
    return Point(*map(_uint, value))


def build_object_program(source, model):
    if not isinstance(model, dict) or model.get("schema_version") != 1:
        raise GXWFormatError("unsupported GXW object model version")
    allowed = {"schema_version", "program", "canvas_height", "nodes", "wires", "labels",
               "declaration_edits", "unknown_record_count", "issues", "blocks"}
    if set(model) - allowed or model.get("program", source.logical_name) != source.logical_name:
        raise GXWFormatError("object model does not match the selected Program.pou")
    if not isinstance(model.get("nodes"), list) or not isinstance(model.get("wires"), list):
        raise GXWFormatError("nodes and wires must be lists")
    source_groups = list(source.block_records())
    source_blocks = {b.offset: b for b, _ in source_groups}
    specifications = model.get("blocks")
    if specifications is None:
        if len(source_groups) != 1:
            raise GXWFormatError("multi-block source requires explicit object-model blocks")
        specifications = [{"source_offset": source_groups[0][0].offset,
                           "canvas_height": model.get("canvas_height", source.canvas_height)}]
    if not isinstance(specifications, list) or not specifications:
        raise GXWFormatError("blocks must be a nonempty list")
    block_templates, block_heights, bound_blocks = [], [], {}
    for index, spec in enumerate(specifications):
        if not isinstance(spec, dict) or set(spec) - {"source_offset", "canvas_height"}:
            raise GXWFormatError("invalid structured block fields")
        offset = spec.get("source_offset")
        if offset is not None:
            if type(offset) is not int or offset not in source_blocks or offset in bound_blocks:
                raise GXWFormatError("stale or repeated source block offset")
            bound_blocks[offset] = index
        block_templates.append(source_blocks[offset] if offset is not None else source_groups[0][0])
        block_heights.append(_uint(spec.get("canvas_height", source_groups[0][0].canvas_height)))
    if 'blocks' in model and 'canvas_height' in model and _uint(model['canvas_height']) != sum(block_heights):
        raise GXWFormatError('program canvas_height is derived from block heights')
    # Opaque records cannot be assigned to a newly invented block or lost when
    # a source block is omitted. Explicit source binding preserves membership.
    membership = {}
    for block, records in source_groups:
        for record in records:
            if record in source.unknown_records:
                if block.offset not in bound_blocks:
                    raise GXWFormatError("cannot drop or relocate a block containing unknown records")
                membership[record.offset] = bound_blocks[block.offset]

    def owner(item):
        index = item.get("block", 0 if len(specifications) == 1 else None)
        if type(index) is not int or not 0 <= index < len(specifications):
            raise GXWFormatError("record requires a valid block index")
        return index
    old_nodes = {n.offset: n for n in source.nodes}
    old_wires = {w.offset: w for w in source.wires}
    catalog = native_catalog()
    next_key = max((r.offset for r in source.iter_records()), default=0) + 1
    used, ids, nodes, wires = set(), {}, [], []
    for item in model["nodes"]:
        if not isinstance(item, dict) or set(item) - {"id", "source_offset", "template", "symbol", "x", "y", "width", "height", "ports", "block"}:
            raise GXWFormatError("invalid structured node fields")
        identity = item.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or identity in ids:
            raise GXWFormatError("structured node IDs must be unique identifiers")
        offset = item.get("source_offset")
        old = old_nodes.get(offset) if type(offset) is int else None
        if offset is not None and (old is None or offset in used):
            raise GXWFormatError("stale or repeated source node offset")
        key = item.get("template")
        if old and _key(old) == key:
            template = old
        elif key in catalog:
            template = catalog[key]
        else:
            raise GXWFormatError(f"no verified native ABI template: {key}")
        symbol = item.get("symbol", template.symbol)
        if not isinstance(symbol, str) or not symbol or "\0" in symbol:
            raise GXWFormatError("structured node requires a nonempty symbol")
        if template.kind == NodeKind.FUNCTION and symbol != template.symbol:
            raise GXWFormatError("select a native function template to change a function")
        x, y = _uint(item.get("x")), _uint(item.get("y"))
        bbox = Rect(x, y, _uint(x + template.bbox.right - template.bbox.left),
                    _uint(y + template.bbox.bottom - template.bbox.top))
        derived = {"width": bbox.right-bbox.left, "height": bbox.bottom-bbox.top,
                   "ports": [{"name": name, "x": p.local_x, "y": p.local_y}
                             for name, p in zip(_port_names(template), template.ports)]}
        if any(field in item and item[field] != value for field, value in derived.items()):
            raise GXWFormatError("node dimensions/ports are derived from its native ABI template")
        if old:
            used.add(offset)
        else:
            offset, next_key = next_key, next_key + 1
        node = replace(template, offset=offset, symbol=symbol, bbox=bbox)
        ids[identity] = node
        nodes.append(node)
        membership[offset] = owner(item)
    for item in model["wires"]:
        if not isinstance(item, dict) or set(item) - {"source_offset", "start", "end", "from", "to", "via", "block"}:
            raise GXWFormatError("invalid structured wire fields")
        block_index = owner(item)
        offset = item.get("source_offset")
        old = old_wires.get(offset) if type(offset) is int else None
        if offset is not None and (old is None or offset in used):
            raise GXWFormatError("stale or repeated source wire offset")
        template = old or next(iter(source.wires), None)
        if template is None:
            template = read_project(default_baseline())[0].wires[0]
        if "from" in item or "to" in item:
            if "start" in item or "end" in item or offset is not None:
                raise GXWFormatError("choose coordinate or named-port wiring")
            def endpoint(value):
                if not isinstance(value, str) or "." not in value:
                    raise GXWFormatError("wire endpoint must be node_id.port_name")
                identity, formal = value.rsplit(".", 1)
                node = ids.get(identity)
                if node is None or formal not in _port_names(node):
                    raise GXWFormatError(f"unknown wire endpoint: {value}")
                if membership[node.offset] != block_index:
                    raise GXWFormatError("named wire endpoints cannot cross block boundaries")
                return node.port_point(_port_names(node).index(formal))
            points = [endpoint(item.get("from")), *[_point(p) for p in item.get("via", [])], endpoint(item.get("to"))]
        else:
            points = [_point(item.get("start")), _point(item.get("end"))]
        for start, end in zip(points, points[1:]):
            if start == end:
                if "from" in item:
                    continue  # coincident ports already connect without a wire
                raise GXWFormatError("zero-length explicit wire")
            if start.x != end.x and start.y != end.y:
                raise GXWFormatError("wire bends require explicit orthogonal via points")
            key = offset if old else next_key
            if not old:
                next_key += 1
            if old:
                used.add(offset)
            wires.append(replace(template, offset=key, start=start, end=end))
            membership[key] = block_index
    for n in nodes:
        block_heights[membership[n.offset]] = max(block_heights[membership[n.offset]], n.bbox.bottom)
    for w in wires:
        block_heights[membership[w.offset]] = max(block_heights[membership[w.offset]], w.start.y, w.end.y)
    records = sorted([*nodes, *wires, *source.unknown_records], key=lambda r: r.offset)
    blocks = tuple(StructuredBlock(template.offset, template.byte_length, block_heights[index],
                     tuple(r.offset for r in records if membership[r.offset] == index), template.raw_header)
                   for index, template in enumerate(block_templates))
    result = replace(source, nodes=tuple(nodes), wires=tuple(wires), blocks=blocks,
                     canvas_height=sum(block_heights), record_count=len(records))
    # Source unknown records retain their original ordering and bytes.
    serialized = serialize_structured_pou(result)
    reparsed = parse_structured_pou(serialized, logical_name=source.logical_name)
    from .experiment import compare_programs
    if not all(compare_programs(result, reparsed)["checks"].values()):
        raise GXWFormatError("structured object round-trip changed connectivity")
    return result


def generate_object_project(model, *, baseline=None):
    if not isinstance(model, dict):
        raise GXWFormatError("GXW object model must be an object")
    raw = default_baseline() if baseline is None else baseline
    source, documents, _ = read_project(raw, model.get("program"))
    projection = export_object_model(source, documents)
    if any(key in model and model[key] != projection[key] for key in ("labels", "unknown_record_count", "issues")):
        raise GXWFormatError("labels/issues are read-only source data; use declaration_edits to modify declarations")
    program = build_object_program(source, model)
    edits = model.get("declaration_edits", {})
    if not isinstance(edits, dict):
        raise GXWFormatError("declaration edits must map table names to edits")
    changed = {}
    for logical, patch in edits.items():
        if logical not in documents or not isinstance(patch, dict) or set(patch) - {"upserts", "renames", "remove"}:
            raise GXWFormatError("invalid declaration table edit")
        changed[logical] = edit_declarations(documents[logical], **patch)
    return build_gxw_project(raw, program, declarations=changed, sync_fb_declarations=True)

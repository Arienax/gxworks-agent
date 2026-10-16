"""JSON transport for the existing StructuredProgram model, not a new PLC IR.

New records use saved native ABI templates. Imported records retain their source
layout and opaque fields; geometry is the already verified editor grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
import hashlib
import json
import re
import struct

from .container_writer import validate_cfb_streams
from .callable_sources import ProjectCallableSources
from .declarations import DeclarationDocument, parse_declarations, edit_declarations, CLASS_CODES
from .models import (
    COIL_NODE_KINDS, CONTACT_NODE_KINDS, GXWFormatError, NodeKind, Point, PortDescriptor, Rect, StructuredBlock, StructuredProgram,
)
from .project_metadata import logical_mapping
from .source_header import source_payload_offset
from .project_writer import prepare_project_write, write_prepared_project
from .semantic import DEFAULT_FUNCTION_BLOCK_REGISTRY, build_semantic_model
from .structured_pou import _parse_node, parse_structured_pou
from .structured_pou_writer import serialize_structured_pou


from .template_data import template_bytes


@lru_cache(maxsize=1)
def native_catalog():
    result = {}
    for key, item in json.loads(template_bytes("nodes.json")).items():
        raw = bytes.fromhex(item["record_hex"])
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise GXWFormatError("native node template hash mismatch")
        result[key] = _parse_node(raw, 0)
    return result


def default_baseline():
    raw = template_bytes("fx3u.gxw")
    provenance = json.loads(template_bytes("provenance.json"))
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
    if node.kind in CONTACT_NODE_KINDS | COIL_NODE_KINDS:
        return ["IN", "OUT"] if len(node.ports) == 2 else [str(i) for i in range(len(node.ports))]
    return {NodeKind.INPUT: ["OUT"], NodeKind.OUTPUT: ["IN"]}.get(
                node.kind, [str(i) for i in range(len(node.ports))])


def catalog_description(*, sources=None, program=None):
    result = [{"template": key, "kind": n.kind.value, "type_name": n.type_name,
             "symbol": n.symbol if n.kind == NodeKind.FUNCTION else "",
             "width": n.bbox.right - n.bbox.left, "height": n.bbox.bottom - n.bbox.top,
             "ports": _ports(n, sources, bind_instance=False)}
            for key, n in native_catalog().items()]
    comment=source_node_template('comment',sources)
    result.append(dict(template='comment',kind='comment',symbol='',type_name=None,
        width=comment.bbox.right,height=comment.bbox.bottom,ports=[]))
    if sources is not None:
        for key in ('function:EMOV', 'function:MOVE_E'):
            try:
                node = source_node_template(key, sources)
                ports = sources.callable(node, bind_instance=False)['ports']
            except GXWFormatError:
                continue
            result.append(dict(template=key, kind=node.kind.value, type_name=None,
                symbol=node.symbol, width=7, height=4, ports=ports))
        for name, matches in sources.catalog.items():
            if len(matches) != 1 or matches[0][1]['kind'] != 'FUNCTION_BLOCK':
                continue
            key = 'function_block:' + matches[0][1]['name']
            if key in native_catalog():
                continue
            try:
                node = _source_fb_template(key, sources)
                ports = sources.callable(node, bind_instance=False)['ports']
            except GXWFormatError:
                continue
            result.append({'template': key, 'kind': node.kind.value, 'type_name': node.type_name,
                           'symbol': '', 'width': node.bbox.right, 'height': node.bbox.bottom,
                           'ports': ports})
    if sources is not None and program is not None:
        for node in program.nodes:
            if node.kind not in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                continue
            try:
                ports = sources.callable(node)['ports']
            except GXWFormatError:
                continue
            entry = {'template': _key(node), 'prototype_offset': node.offset, 'kind': node.kind.value,
                           'type_name': node.type_name, 'symbol': node.symbol if node.kind == NodeKind.FUNCTION else '',
                           'width': node.bbox.right-node.bbox.left, 'height': node.bbox.bottom-node.bbox.top,
                           'ports': ports}
            binding = sources.label(node.symbol) if node.kind == NodeKind.FUNCTION_BLOCK else None
            if binding is not None and binding[1].comment:
                entry['description'] = binding[1].comment
                entry['description_source'] = {'kind': 'instance_declaration_comment', 'identity': binding[0] + ':' + binding[1].name}
                entry['behavior_status'] = 'unverified'
            result.append(entry)
    return result


def _ports(node, sources=None, *, bind_instance=True):
    if sources is not None and node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
        try:
            return sources.callable(node, bind_instance=bind_instance)['ports']
        except GXWFormatError:
            # A failed source binding must not inherit names from another type
            # or from the legacy fixed-layout registry.
            return [{"name": str(index), "x": port.local_x, "y": port.local_y}
                    for index, port in enumerate(node.ports)]
    value = None
    if sources is not None and bind_instance and node.kind in (NodeKind.INPUT, NodeKind.OUTPUT):
        try:
            value = sources.structure_value(node.symbol)
        except GXWFormatError:
            pass  # Preserve unresolved terminals; the read issue carries the gap.
    return [{"name": name, "x": port.local_x, "y": port.local_y,
             **({'negated': bool(port.port_kind_code & 8)} if sources is not None else {}),
             **({'data_type': value['data_type']} if value is not None else {})}
            for name, port in zip(_port_names(node), node.ports)]


def _source_fb_template(key, sources):
    """Observed kind-2 envelope with source formals and unmodified data ports.

    The owned Keypad control establishes this layout for a project FB with a
    sized-string output and paired IN_OUT ports. Native CPU acceptance remains
    a separate compilation result, never a conclusion from the library name.
    """
    if sources is None or not isinstance(key, str) or not key.startswith('function_block:'):
        raise GXWFormatError(f'no verified native ABI template: {key}')
    name = key.split(':', 1)[1]
    formals = sources.fixed_interface(name)
    regular = {side: [row for row in formals[side] if row['class_code'] != 5]
               for side in ('inputs', 'outputs')}
    inouts = [row for row in formals['inputs'] if row['class_code'] == 5]
    shared_y = 2 + max(map(len, regular.values()))
    positions = [(row, 1, index + 2) for index, row in enumerate(regular['inputs'])]
    positions.extend((row, flag, shared_y + index)
                     for index, row in enumerate(inouts) for flag in (1, 0))
    positions.extend((row, 0, index + 2) for index, row in enumerate(regular['outputs']))
    # SC_EDIT 1.635 assigns IN_OUT pairs after both ordinary sides and keeps
    # ordinary inputs, IN_OUT pairs, then outputs in its connector description.
    # BOX::Paint
    # draws a cross when this description differs from the current interface.
    text_units = lambda value: len(value.encode('utf-16le')) // 2
    row_text = {}
    for row, _, y in positions:
        row_text[y] = row_text.get(y, 0) + text_units(row['name'])
    # Native character-based width floor; existing authored boxes retain their
    # saved widths and the GUI may expand for the configured font.
    width = max(5, (max(text_units(name) + 2, *row_text.values()) + 2) * 7 // 10)
    ports = tuple(PortDescriptor(16, flag, 0 if flag else width, y,
                    struct.pack('<IIII', 16, flag, 0 if flag else width, y))
                  for _, flag, y in positions)
    height = shared_y + len(inouts)
    template = native_catalog()['function_block:TON']
    return replace(template, symbol='FB', type_name=name, bbox=Rect(0,0,width,height), ports=ports)


def source_node_template(key, sources, *, symbol=None):
    """One constructor for parameter lowering and the existing v2 writer."""
    if key=='comment':
        text=symbol or 'Comment'
        lines=text.splitlines() or ['']
        width=max(12,min(100,max(len(line) for line in lines)*7//10+3))
        height=max(3,1+sum(max(1,(len(line)*7//10+width-1)//width) for line in lines))
        return replace(native_catalog()['input'],kind=NodeKind.UNKNOWN,kind_code=0x10,
            symbol=text,object_flag=1,reserved=0,ports=(),bbox=Rect(0,0,width,height))
    if key in native_catalog():
        return native_catalog()[key]
    if key in ('function:EMOV', 'function:MOVE_E') and sources is not None:
        name = key.split(':', 1)[1]
        interface = sources.fixed_interface(name, 'FUNCTION')
        if ([row['name'].upper() for row in interface['inputs'][:1]] != ['EN']
                or [row['name'].upper() for row in interface['outputs'][:1]] != ['ENO']
                or len(interface['inputs']) != 2 or len(interface['outputs']) != 2
                or any(row['class_code'] == 5 for row in interface['inputs'])):
            raise GXWFormatError('transfer function differs from the observed MOV/EMOV envelope')
        template = native_catalog()['function:MOV']
        return replace(template, symbol=name, bbox=Rect(0,0,7,4))
    return _source_fb_template(key, sources)


def read_project(raw, logical_name=None):
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer["projectdatalist.xml"])
    payloads = validate_cfb_streams(outer["_hdb"])
    names = sorted(k for k in mapping if k.endswith(".pou") and mapping[k] in payloads)
    if logical_name is None:
        if len(names) != 1:
            raise GXWFormatError("select one Program.pou from this GXW project")
        logical_name = names[0]
    if logical_name not in names:
        raise GXWFormatError("selected Program.pou is missing")
    program = parse_structured_pou(payloads[mapping[logical_name]], logical_name=logical_name,
                                   preserve_unsupported_records=True)
    declarations = {k: parse_declarations(payloads[v], logical_name=k) for k, v in mapping.items()
                    if k.endswith((".lh", ".gh", ".lnl")) and v in payloads}
    return program, declarations, names


@dataclass(frozen=True)
class ProjectSourceContext:
    """One selected source snapshot; rebuild after changing project bytes."""

    raw: bytes = field(repr=False)
    program: StructuredProgram
    declarations: dict[str, DeclarationDocument]
    programs: list[str]
    sources: ProjectCallableSources = field(repr=False)

    def object_model(self):
        return export_object_model(self.program, self.declarations, sources=self.sources)

    def catalog(self, *, declaration_edits=None):
        sources = self.draft_sources(declaration_edits)
        # A client selects a template by name. Prefer an existing source
        # prototype when present, retaining its offset for a bounded clone.
        return list({item['template']: item for item in
            catalog_description(sources=sources, program=self.program)}.values())

    def draft_sources(self, declaration_edits=None):
        """Resolve pending declaration edits without rebasing source offsets."""
        documents = dict(self.declarations)
        for name, patch in (declaration_edits or {}).items():
            if name not in documents or not isinstance(patch, dict) or set(patch) - {'upserts', 'renames', 'remove'}:
                raise GXWFormatError('invalid declaration table edit')
            documents[name] = edit_declarations(documents[name], **patch,
                native_fbd_locals=name == self.sources.local_table
                    and self.program.raw[source_payload_offset(self.program.raw)] == 208)
        return self.sources.with_declarations(documents) if declaration_edits else self.sources

    def svg(self):
        from .render import render_structured_svg
        return render_structured_svg(self.program, sources=self.sources)

    def task_scopes(self):
        from .native_write import native_task_scope_context
        return native_task_scope_context(self.raw)

    def empty_model(self):
        return {'schema_version': 2, 'program': self.program.logical_name,
                'canvas_height': 12, 'nodes': [], 'wires': []}


def read_project_context(raw, logical_name=None):
    """Bind GXW bytes, selected Program, declarations and its own CPU."""
    raw = bytes(raw)
    program, declarations, names = read_project(raw, logical_name)
    sources = ProjectCallableSources.from_project(raw, program.logical_name, declarations)
    return ProjectSourceContext(raw, program, declarations, names, sources)


def export_object_model(program, declarations=None, *, sources=None):
    nodes = []
    for n in program.nodes:
        nodes.append({"id": f"n{n.offset}", "source_offset": n.offset, "template": _key(n),
                      "symbol": n.symbol, "x": n.bbox.left, "y": n.bbox.top,
                      "width": n.bbox.right - n.bbox.left, "height": n.bbox.bottom - n.bbox.top,
                      "ports": _ports(n, sources)})
    class_names = {v: k for k, v in CLASS_CODES.items()}
    labels = {name: [{"name": r.name, "data_type": r.data_type,
                     "kind": sources.declaration_kind(r) if sources else
                             "function_block" if r.type_code == 15 else "variable",
                     "class_name": class_names.get(r.class_code, str(r.class_code)),
                     "initial_value": r.initial_value, "device": r.device,
                     "iec_address": r.iec_address, "comment": r.comment}
                    for r in doc.rows] for name, doc in (declarations or {}).items()}
    semantic = build_semantic_model(program, function_block_instances=sources.semantic_specs(program) if sources else None)
    model = {"schema_version": 2 if sources else 1, "program": program.logical_name, "canvas_height": program.canvas_height,
            "nodes": nodes, "wires": [{"source_offset": w.offset, "start": [w.start.x, w.start.y],
                                       "end": [w.end.x, w.end.y]} for w in program.wires],
            "labels": labels, "declaration_edits": {}, "unknown_record_count": len(program.unknown_records),
            "issues": [{"code": i.code, "message": i.message} for i in semantic.issues]}
    if sources is not None:
        model['cpu'] = sources.cpu
        model['issues'].extend(sources.issues)
        for node in program.nodes:
            if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                try:
                    sources.callable(node)
                except GXWFormatError as error:
                    model['issues'].append({'code': 'callable_source_gap', 'node_offset': node.offset,
                                            'message': str(error)})
            elif node.kind in (NodeKind.INPUT, NodeKind.OUTPUT):
                try:
                    sources.structure_value(node.symbol)
                except GXWFormatError as error:
                    model['issues'].append({'code': 'structure_source_gap', 'node_offset': node.offset,
                                            'message': str(error)})
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


def build_object_program(source, model, *, sources=None, original_sources=None):
    if not isinstance(model, dict) or model.get("schema_version") not in (1, 2):
        raise GXWFormatError("unsupported GXW object model version")
    if model['schema_version'] == 2 and sources is None:
        raise GXWFormatError("object model version 2 requires project source context")
    if model['schema_version'] == 1:
        sources = None
    allowed = {"schema_version", "program", "canvas_height", "nodes", "wires", "labels",
               "declaration_edits", "unknown_record_count", "issues", "blocks", "cpu"}
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
    used, ids, nodes, wires, node_ports = set(), {}, [], [], {}
    for item in model["nodes"]:
        if not isinstance(item, dict) or set(item) - {"id", "source_offset", "prototype_offset", "template", "symbol", "x", "y", "width", "height", "ports", "port_edits", "block"}:
            raise GXWFormatError("invalid structured node fields")
        identity = item.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or identity in ids:
            raise GXWFormatError("structured node IDs must be unique identifiers")
        offset = item.get("source_offset")
        old = old_nodes.get(offset) if type(offset) is int else None
        if offset is not None and (old is None or offset in used):
            raise GXWFormatError("stale or repeated source node offset")
        key = item.get("template")
        prototype = item.get('prototype_offset')
        if prototype is not None:
            if sources is None or old is not None or type(prototype) is not int or prototype not in old_nodes:
                raise GXWFormatError('prototype_offset requires a source-bound new node in object model version 2')
            template = old_nodes[prototype]
            if _key(template) != key or template.kind not in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                raise GXWFormatError('callable prototype must have the same template key')
            (original_sources or sources).callable(template)
        elif old and _key(old) == key:
            template = old
        elif key in catalog:
            template = catalog[key]
        else:
            template = source_node_template(key, sources, symbol=item.get('symbol'))
        if old and old.kind == template.kind == NodeKind.FUNCTION_BLOCK and _key(old) != key:
            # Compatible type changes retain the existing user-sized box and
            # connector coordinates, as the native saved transition does.
            shape = lambda node: sorted(port.port_kind_code & ~8 for port in node.ports)
            if shape(old) == shape(template):
                template = replace(old, type_name=template.type_name)
        symbol = item.get("symbol", template.symbol)
        if not isinstance(symbol, str) or not symbol or "\0" in symbol:
            raise GXWFormatError("structured node requires a nonempty symbol")
        if template.kind == NodeKind.FUNCTION and symbol != template.symbol:
            raise GXWFormatError("select a native function template to change a function")
        x, y = _uint(item.get("x")), _uint(item.get("y"))
        bbox = Rect(x, y, _uint(x + template.bbox.right - template.bbox.left),
                    _uint(y + template.bbox.bottom - template.bbox.top))
        derived = {"width": bbox.right-bbox.left, "height": bbox.bottom-bbox.top,
                   "ports": _ports(template, sources, bind_instance=False)}
        original_derived = derived
        if old is not None or prototype is not None:
            original_derived = {**derived, 'ports': _ports(template, original_sources or sources,
                bind_instance=old is not None and _key(old) == key)}
            if old is not None and _key(old) != key:
                original_derived = {'width': old.bbox.right-old.bbox.left,
                    'height': old.bbox.bottom-old.bbox.top,
                    'ports': _ports(old, original_sources or sources)}
            if original_sources is not None and template.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                try:
                    original_sources.callable(template, bind_instance=False)
                except GXWFormatError:
                    pass  # An unresolved original interface remains opaque.
                else:
                    sources.callable(template, bind_instance=False)
        if any(field in item and item[field] != value for field, value in original_derived.items()):
            raise GXWFormatError("node dimensions/ports are derived from its native ABI template")
        if old:
            used.add(offset)
        else:
            offset, next_key = next_key, next_key + 1
        node = replace(template, offset=offset, symbol=symbol, bbox=bbox)
        port_edits = item.get('port_edits', {})
        if not isinstance(port_edits, dict):
            raise GXWFormatError('port edits must map source endpoint names to edits')
        if port_edits:
            if sources is None or node.kind not in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                raise GXWFormatError('callable port edits require object model version 2 and source formals')
            ports = list(node.ports)
            names = [port['name'] for port in derived['ports']]
            for name, patch in port_edits.items():
                if names.count(name) != 1 or not isinstance(patch, dict) or set(patch) != {'negated'} or type(patch['negated']) is not bool:
                    raise GXWFormatError('port edit requires one source endpoint and boolean negated')
                index = names.index(name)
                port = ports[index]
                flags = (port.port_kind_code & ~8) | (8 if patch['negated'] else 0)
                ports[index] = replace(port, port_kind_code=flags,
                    raw=struct.pack('<IIII',port.size,flags,port.local_x,port.local_y))
            node = replace(node, ports=tuple(ports))
        ids[identity] = node
        node_ports[identity] = derived['ports']
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
                identity, formal = value.split(".", 1)
                node = ids.get(identity)
                port_names = [port['name'] for port in node_ports.get(identity, [])]
                if node is None or port_names.count(formal) != 1:
                    raise GXWFormatError(f"unknown wire endpoint: {value}")
                if membership[node.offset] != block_index:
                    raise GXWFormatError("named wire endpoints cannot cross block boundaries")
                return node.port_point(port_names.index(formal))
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
    reparsed = parse_structured_pou(serialized, logical_name=source.logical_name, preserve_unsupported_records=True)
    from .experiment import compare_programs
    if not all(compare_programs(result, reparsed)["checks"].values()):
        raise GXWFormatError("structured object round-trip changed connectivity")
    return result


def prepare_object_project(model, *, baseline=None):
    """Apply the existing editor rules before selecting a project save backend."""
    if not isinstance(model, dict):
        raise GXWFormatError("GXW object model must be an object")
    raw = default_baseline() if baseline is None else baseline
    if 'networks' in model:
        from .ladder_lowering import fbd_topology_to_object_model
        model = fbd_topology_to_object_model(model, read_project_context(raw, model.get('program')))
    if model.get('schema_version') == 2:
        context = read_project_context(raw, model.get('program'))
        source, documents, program_names, sources = context.program, context.declarations, context.programs, context.sources
    else:
        source, documents, program_names = read_project(raw, model.get('program'))
        sources = None
    projection = export_object_model(source, documents, sources=sources)
    if any(key in model and model[key] != projection.get(key) for key in ("labels", "unknown_record_count", "issues", "cpu")):
        raise GXWFormatError("labels/issues are read-only source data; use declaration_edits to modify declarations")
    edits = model.get("declaration_edits", {})
    if not isinstance(edits, dict):
        raise GXWFormatError("declaration edits must map table names to edits")
    changed = {}
    for logical, patch in edits.items():
        if logical not in documents or not isinstance(patch, dict) or set(patch) - {"upserts", "renames", "remove"}:
            raise GXWFormatError("invalid declaration table edit")
        changed[logical] = edit_declarations(documents[logical], **patch,
            native_fbd_locals=sources is not None and logical == sources.local_table
                and source.raw[source_payload_offset(source.raw)] == 208)
        if patch.get('renames') and documents[logical].scope == 'global' and len(program_names) > 1:
            raise GXWFormatError('global label rename requires all program references; this edit is bound to one Program.pou')
    context = (sources or ProjectCallableSources.from_project(raw, source.logical_name, documents)).with_declarations(
        {**documents, **changed}) if changed else sources
    program = build_object_program(source, model, sources=context if sources is not None else None,
                                   original_sources=sources if changed else None)
    for logical, patch in edits.items():
        if documents[logical].scope != 'global' and logical != (sources or context).local_table:
            continue
        for old_name in patch.get('renames', {}):
            if patch['renames'][old_name].casefold() == old_name.casefold():
                continue
            reference = re.compile(r'(?<!\w)' + re.escape(old_name) + r'(?!\w)', re.IGNORECASE)
            if any(node.kind != NodeKind.FUNCTION and reference.search(node.symbol) for node in program.nodes):
                original_sources = sources or ProjectCallableSources.from_project(raw, source.logical_name, documents)
                binding = original_sources.label(old_name)
                if binding is None or binding[0] == logical:
                    raise GXWFormatError('renamed label still has a graph reference; update graph and declaration together: ' + old_name)
    # Reject edits that would silently re-create a removed/renamed FB instance,
    # or overwrite an explicitly edited type during automatic FB synchronization.
    if changed:
        for node in program.nodes:
            if node.kind == NodeKind.FUNCTION_BLOCK:
                prior = (sources or context.with_declarations(documents)).label(node.symbol)
                binding = context.label(node.symbol)
                if prior and not binding or binding and (binding[1].type_code not in (0, 15) or binding[1].data_type != node.type_name):
                    raise GXWFormatError('FB graph and edited declaration must have the same instance name and type: ' + node.symbol)
    return prepare_project_write(raw, program, declarations=changed, sync_fb_declarations=True)


def generate_object_project(model, *, baseline=None):
    return write_prepared_project(prepare_object_project(model, baseline=baseline))

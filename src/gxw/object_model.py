# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""JSON transport for the existing StructuredProgram model, not a new PLC IR.

New records use saved native ABI templates. Imported records retain their source
layout and opaque fields; geometry is the already verified editor grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache, cached_property
from copy import deepcopy
import hashlib
import json
import re
import struct

from .container_writer import validate_cfb_streams
from .integrity import read_project_hdb
from .callable_sources import ProjectCallableSources, read_project_declarations
from .declarations import DeclarationDocument, parse_declarations, edit_declarations, CLASS_CODES
from .models import (
    COIL_NODE_KINDS, CONTACT_NODE_KINDS, GRAPH_CONTACT_NODE_KINDS, GXWFormatError, NodeKind, Point, PortDescriptor, Rect, StructuredBlock, StructuredProgram,
)
from .project_metadata import project_stream_mapping
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
    if node.kind==NodeKind.UNKNOWN and node.kind_code==0x10:
        return 'comment'
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
    if node.kind in GRAPH_CONTACT_NODE_KINDS | COIL_NODE_KINDS:
        return ["IN", "OUT"] if len(node.ports) == 2 else [str(i) for i in range(len(node.ports))]
    return {NodeKind.INPUT: ["OUT"], NodeKind.OUTPUT: ["IN"],NodeKind.INOUT:['IN','OUT'],NodeKind.JUMP:['IN'],NodeKind.RETURN:['IN']}.get(
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
    for key in ('jump','return','inout','contact_rising_negated','contact_falling_negated'):
        node=source_node_template(key,sources)
        result.append(dict(template=key,kind=node.kind.value,symbol=node.symbol,type_name=None,
            width=2,height=2,ports=_ports(node,sources,bind_instance=False)))
    if sources is not None:
        for name in ('@SUB','@DIV','@NE',*[f'@{name}-2' for name in
                     ('AND','OR','XOR','ADD','MUL','GT','GE','EQ','LE','LT')]):
            node=source_node_template('function:'+name,sources)
            result.append(dict(template='function:'+name,kind='function',type_name=None,symbol=name,
                width=node.bbox.right,height=node.bbox.bottom,ports=_ports(node,sources,bind_instance=False)))
        for key in ('function:EMOV', 'function:MOVE_E'):
            try:
                node = source_node_template(key, sources)
                ports = sources.callable(node, bind_instance=False)['ports']
            except GXWFormatError:
                continue
            result.append(dict(template=key, kind=node.kind.value, type_name=None,
                symbol=node.symbol, width=7, height=4, ports=ports))
        for name, matches in sources.catalog.items():
            if len(matches) != 1 or matches[0][1]['kind'] not in ('FUNCTION_BLOCK','FUNCTION'):
                continue
            definition = matches[0][1]
            key = ('function_block:' if definition['kind'] == 'FUNCTION_BLOCK' else 'function:') + definition['name']
            if key in native_catalog():
                continue
            try:
                node = source_node_template(key, sources)
                ports = sources.callable(node, bind_instance=False)['ports']
            except GXWFormatError as error:
                if definition['kind'] != 'FUNCTION' or 'extensible' not in str(error):
                    continue
                key += '-2'
                try:
                    node = source_node_template(key,sources)
                    ports = sources.callable(node,bind_instance=False)['ports']
                except GXWFormatError:
                    continue
            result.append({'template': key, 'kind': node.kind.value, 'type_name': node.type_name,
                           'symbol': node.symbol if node.kind == NodeKind.FUNCTION else '',
                           'width': node.bbox.right, 'height': node.bbox.bottom,
                           'ports': ports})
    if sources is not None and program is not None:
        for node in program.nodes:
            if node.kind not in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                continue
            try:
                prototype = sources.source_prototype(node)
                ports = prototype['ports']
            except GXWFormatError:
                continue
            entry = {'template': _key(node), 'prototype_offset': node.offset, 'kind': node.kind.value,
                           'type_name': node.type_name, 'symbol': node.symbol if node.kind == NodeKind.FUNCTION else '',
                           'width': node.bbox.right-node.bbox.left, 'height': node.bbox.bottom-node.bbox.top,
                           'ports': ports}
            if 'capabilities' in prototype:
                entry['capabilities'] = prototype['capabilities']
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
            from .callable_sources import sorted_ports
            try:
                inputs,_ = sorted_ports(node)
            except GXWFormatError:
                return [{"name":str(index),'x':port.local_x,'y':port.local_y}
                        for index,port in enumerate(node.ports)]
            return [{"name":str(index),'x':port.local_x,'y':port.local_y,
                     'side':'in' if index in inputs else 'out','data_type':None,
                     'negated':bool(port.port_kind_code&8)} for index,port in enumerate(node.ports)]
    value = None
    if sources is not None and bind_instance and node.kind in (NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT):
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
    _, definition = sources.definition(name, 'FUNCTION_BLOCK')
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
    width = max(5, (max([text_units(name)+2,*row_text.values()]) + 2) * 7 // 10)
    ports = tuple(PortDescriptor(16, flag, 0 if flag else width, y,
                    struct.pack('<IIII', 16, flag, 0 if flag else width, y))
                  for _, flag, y in positions)
    height = shared_y + len(inouts)
    template = native_catalog()['function_block:TON']
    return replace(template, symbol='FB', type_name=name, bbox=Rect(0,0,width,height), ports=ports)


def source_node_template(key, sources, *, symbol=None):
    """One constructor for parameter lowering and the existing v2 writer."""
    if key in ('jump','return','inout','contact_rising_negated','contact_falling_negated'):
        # SC_EDIT GetGD2BoxType/SetBoxType and independently authored sources
        # bind these codes to the ordinary node envelope. Their geometry is
        # shared with the saved 2x2 primitives, without an object-ID tail.
        kind={'jump':(NodeKind.JUMP,9),'return':(NodeKind.RETURN,10),
              'inout':(NodeKind.INOUT,15),'contact_rising_negated':(NodeKind.CONTACT_RISING_NEGATED,19),
              'contact_falling_negated':(NodeKind.CONTACT_FALLING_NEGATED,20)}[key]
        template=native_catalog()['contact']
        return replace(template,kind=kind[0],kind_code=kind[1],
            symbol=('Label1' if key=='jump' else '' if key=='return' else 'X0'),
            bbox=Rect(0,0,2,2),ports=template.ports[:1] if key in ('jump','return') else template.ports)
    if key=='comment':
        text='Comment' if symbol is None else symbol
        lines=text.splitlines() or ['']
        width=max(12,min(100,max(len(line) for line in lines)*7//10+3))
        height=max(3,1+sum(max(1,(len(line)*7//10+width-1)//width) for line in lines))
        return replace(native_catalog()['input'],kind=NodeKind.UNKNOWN,kind_code=0x10,
            symbol=text,object_flag=1,reserved=0,ports=(),bbox=Rect(0,0,width,height))
    if key in native_catalog():
        return native_catalog()[key]
    if isinstance(key, str) and key.startswith('function:') and sources is not None:
        name = key.split(':', 1)[1]
        operator = re.fullmatch(r'@(AND|OR|XOR|ADD|MUL|GT|GE|EQ|LE|LT)-([1-9][0-9]*)',name,re.I)
        count = {'@SUB':2,'@DIV':2,'@NE':2}.get(name.upper(),int(operator[2]) if operator else None)
        if count is not None:
            if count > 256:
                raise GXWFormatError('intrinsic operator arity exceeds the graphic port extent')
            width = 5
            entries = [(3,0,index+2) for index in range(count)] + [(2,width,2)]
            ports = tuple(PortDescriptor(16,flags,x,y,struct.pack('<IIII',16,flags,x,y))
                          for flags,x,y in entries)
            return replace(native_catalog()['function:MOV'],symbol=name,ports=ports,
                           bbox=Rect(0,0,width,count+2))
        interface = sources.fixed_interface(name, 'FUNCTION')
        template = native_catalog()['function:MOV']
        regular = {side: [row for row in interface[side] if row['class_code'] != 5]
                   for side in ('inputs', 'outputs')}
        inouts = [row for row in interface['inputs'] if row['class_code'] == 5]
        shared_y = 2 + max(map(len, regular.values()))
        entries = [(row, True, index+2) for index,row in enumerate(regular['inputs'])]
        entries += [(row, flag, shared_y+index) for index,row in enumerate(inouts) for flag in (True,False)]
        entries += [(row, False, index+2) for index,row in enumerate(regular['outputs'])]
        text_units = lambda value: len(value.encode('utf-16le')) // 2
        row_widths = {}
        for row, _, y in entries:
            row_widths[y] = row_widths.get(y,0) + text_units(row['name'])
        width = max(5,(max([text_units(name)+2,*row_widths.values()])+2)*7//10)
        ports = []
        for row, incoming, y in entries:
            flags = (7 if row['class_code'] == 11 else 3) if incoming else (
                0 if row['name'].upper() == 'ENO' else 2)
            x = 0 if incoming else width
            ports.append(PortDescriptor(16,flags,x,y,struct.pack('<IIII',16,flags,x,y)))
        return replace(template, symbol=name, ports=tuple(ports), bbox=Rect(0,0,width,shared_y+len(inouts)))
    return _source_fb_template(key, sources)


def read_project(raw, logical_name=None):
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    payloads = read_project_hdb(outer).payloads
    from .project_metadata import existing_source_groups
    streams = {name: payloads[key] for name, key in mapping.items() if key in payloads}
    groups, _ = existing_source_groups(outer['projectdatalist.xml'], streams)
    names = sorted(k for k in mapping if mapping[k] in payloads and
                   (k.endswith('.pou') or k.endswith('.lnb') and k in groups))
    if logical_name is None:
        if len(names) != 1:
            raise GXWFormatError("select one Program.pou from this GXW project")
        logical_name = names[0]
    if logical_name not in names:
        raise GXWFormatError("selected Program.pou is missing")
    program = parse_structured_pou(payloads[mapping[logical_name]], logical_name=logical_name,
                                   preserve_unsupported_records=True)
    declarations, _ = read_project_declarations(
        streams, source_groups=groups)
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

    @cached_property
    def fbd_programs(self):
        """Actual FBD sources for the language-specific editor selector."""
        outer=validate_cfb_streams(self.raw)
        mapping=project_stream_mapping(outer)
        payloads=read_project_hdb(outer).payloads
        names=[]
        for name in self.programs:
            raw=payloads[mapping[name]]
            try:
                offset=source_payload_offset(raw)
            except GXWFormatError:
                continue
            if offset<len(raw) and raw[offset]==208:
                names.append(name)
        return names

    def catalog(self, *, declaration_edits=None):
        if not declaration_edits:
            return deepcopy(self._baseline_catalog)
        sources = self.draft_sources(declaration_edits)
        # A client selects a template by name. Prefer an existing source
        # prototype when present, retaining its offset for a bounded clone.
        return list({item['template']: item for item in
            catalog_description(sources=sources, program=self.program)}.values())

    @cached_property
    def _baseline_catalog(self):
        return list({item['template']:item for item in
            catalog_description(sources=self.sources,program=self.program)}.values())

    def draft_sources(self, declaration_edits=None):
        """Resolve pending declaration edits without rebasing source offsets."""
        documents = dict(self.declarations)
        for name, patch in (declaration_edits or {}).items():
            if name not in documents or not isinstance(patch, dict) or set(patch) - {'upserts', 'renames', 'remove', 'order', 'return_type'}:
                raise GXWFormatError('invalid declaration table edit')
            from .offline_authoring import _edited_document
            from .offline_project import read_offline_project
            documents[name] = _edited_document(read_offline_project(self.raw).context,
                self.sources, documents[name], patch)
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
                                       "end": [w.end.x, w.end.y],
                                       **({'kind':'junction'} if w.record_class==3 else {})} for w in program.wires],
            "labels": labels, "declaration_edits": {}, "unknown_record_count": len(program.unknown_records),
            "issues": [{"code": i.code, "message": i.message} for i in semantic.issues]}
    if sources is not None:
        model['cpu'] = sources.cpu
        model['issues'].extend(sources.issues)
        for node in program.nodes:
            if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                try:
                    binding = sources.callable(node)
                    if binding.get('binding') == 'serialized_operator':
                        model['issues'].append({'code': 'intrinsic_operator_semantics_unverified',
                            'node_offset': node.offset,
                            'message': 'operator ports retain serialized identity and direction; overloaded type and execution checks remain separate'})
                except GXWFormatError as error:
                    issue = {'code': 'callable_source_gap', 'node_offset': node.offset, 'message': str(error)}
                    try:
                        prototype = sources.source_prototype(node)
                        if 'capabilities' in prototype:
                            issue['capabilities'] = prototype['capabilities']
                    except GXWFormatError:
                        pass
                    model['issues'].append(issue)
            elif node.kind in (NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT):
                try:
                    sources.structure_value(node.symbol)
                except GXWFormatError as error:
                    model['issues'].append({'code': 'structure_source_gap', 'node_offset': node.offset,
                                            'message': str(error)})
    model['issues'].extend({'code': 'unsupported_graph_node', 'node_offset': node.offset,
        'message': f'node kind 0x{node.kind_code:X} is readable as a bounded record and preserve-only'}
        for node in program.nodes if node.kind == NodeKind.UNKNOWN and node.kind_code != 0x10)
    model['issues'].extend({'code':'graph_execution_semantics_unverified','node_offset':node.offset,
        'message':node.kind.value+' has a bound editable graph record; execution semantics are unverified'}
        for node in program.nodes if node.kind in (NodeKind.JUMP,NodeKind.RETURN,NodeKind.INOUT,
            NodeKind.CONTACT_RISING_NEGATED,NodeKind.CONTACT_FALLING_NEGATED))
    if len(program.blocks) > 1:
        groups = list(program.block_records())
        membership = {r.offset: index for index, (_, records) in enumerate(groups) for r in records}
        model["blocks"] = [{"source_offset": b.offset, "canvas_height": b.canvas_height} for b, _ in groups]
        for item in (*model["nodes"], *model["wires"]):
            item["block"] = membership[item["source_offset"]]
    return model


def source_checks(context, *, semantic=None):
    """Check saved graph bindings and known types without requiring compilation.

    Serialized intrinsic connectors and unresolved device widths remain explicit
    findings.  They must not erase a known type conflict on another net.
    """
    from .fbd_validation import inspect_graph
    findings = [dict(issue) for issue in context.object_model()['issues']]
    for name,document in context.declarations.items():
        if name==context.sources.local_table or document.scope=='global':
            findings.extend(dict(code='fbd_retain_execution_unverified',severity='unverified',stream=name,
                name=row.name,message='VAR_RETAIN is preserved; reset and power-cycle retention execution is unverified')
                for row in document.rows if row.class_code==10)
    semantic = semantic if semantic is not None else inspect_graph(context.program, context.sources)
    findings.extend(row for row in semantic['findings'] if row not in findings)
    from .structured_pou import structured_block_text
    destinations = {}
    for index,block in enumerate(context.program.blocks):
        title,_ = structured_block_text(block.raw_header)
        value = title.strip()
        if value.endswith(':') and value[:-1].strip():
            destinations.setdefault(value[:-1].strip().casefold(),[]).append(index)
    for node in context.program.nodes:
        if node.kind==NodeKind.JUMP and len(destinations.get(node.symbol.casefold(),()))!=1:
            findings.append(dict(code='fbd_jump_target_unresolved',severity='error',
                node_offset=node.offset,message='jump requires one current network label: '+node.symbol))
    return dict(structure='passed',status='failed' if any(item.get('severity')=='error' for item in findings)
                else 'unverified' if findings else 'passed',findings=findings,
                execution=semantic['execution'], resolved_types=semantic['resolved_types'])


def _uint(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise GXWFormatError("editor coordinates must be uint32 integers")
    return value


def _coordinate(value):
    value=_uint(value)
    # SC_EDIT LoadCommon stores coordinates in signed 16-bit box fields.
    # A larger positive grid value would wrap on native open and cold save.
    if value>0x7fff:
        raise GXWFormatError('editor coordinates exceed the native signed 16-bit grid')
    return value


def _point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise GXWFormatError("wire point requires [x, y]")
    return Point(*map(_coordinate, value))


def build_object_program(source, model, *, sources=None, original_sources=None, save_policy='checked'):
    if save_policy not in ('draft', 'checked'):
        raise GXWFormatError('save_policy must be draft or checked')
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
        if not isinstance(spec, dict) or set(spec) - {"source_offset", "canvas_height", "title", "comment"}:
            raise GXWFormatError("invalid structured block fields")
        offset = spec.get("source_offset")
        if offset is not None:
            if type(offset) is not int or offset not in source_blocks or offset in bound_blocks:
                raise GXWFormatError("stale or repeated source block offset")
            bound_blocks[offset] = index
        template = source_blocks[offset] if offset is not None else source_groups[0][0]
        from .structured_pou import edit_structured_block_text
        header = edit_structured_block_text(template.raw_header,
            title=spec.get('title', '' if offset is None else None),
            comment=spec.get('comment', '' if offset is None else None))
        block_templates.append(replace(template, raw_header=header))
        block_heights.append(_uint(spec.get("canvas_height", source_groups[0][0].canvas_height)))
    if 'blocks' in model and 'canvas_height' in model and _uint(model['canvas_height']) != sum(block_heights):
        raise GXWFormatError('program canvas_height is derived from block heights')
    # Opaque records cannot be assigned to a newly invented block or lost when
    # a source block is omitted. Explicit source binding preserves membership.
    membership = {}
    source_membership = {record.offset:block.offset for block, records in source_groups for record in records}
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
        if not isinstance(item, dict) or set(item) - {"id", "source_offset", "prototype_offset", "template", "symbol", "x", "y", "width", "height", "ports", "port_edits", "block", "resize", "refresh_interface", "port_layout"}:
            raise GXWFormatError("invalid structured node fields")
        identity = item.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or identity in ids:
            raise GXWFormatError("structured node IDs must be unique identifiers")
        offset = item.get("source_offset")
        old = old_nodes.get(offset) if type(offset) is int else None
        if offset is not None and (old is None or offset in used):
            raise GXWFormatError("stale or repeated source node offset")
        key = item.get("template")
        if key=='unknown' and old is not None and old.kind_code==0x10:
            key='comment'  # Previously saved object models used this spelling.
        prototype = item.get('prototype_offset')
        if prototype is not None:
            if sources is None or old is not None or type(prototype) is not int or prototype not in old_nodes:
                raise GXWFormatError('prototype_offset requires a source-bound new node in object model version 2')
            template = old_nodes[prototype]
            if _key(template) != key or template.kind not in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                raise GXWFormatError('callable prototype must have the same template key')
            if save_policy=='checked':
                (original_sources or sources).source_prototype(template)
            else:
                from .callable_sources import sorted_ports
                sorted_ports(template)
        elif old and _key(old) == key:
            template = old
        elif key in catalog:
            template = catalog[key]
        elif item.get('port_layout') is not None and isinstance(key,str) and key.startswith(('function:','function_block:')):
            template = replace(catalog['function_block:TON'],type_name=key.split(':',1)[1]) if key.startswith('function_block:') else replace(catalog['function:MOV'],symbol=key.split(':',1)[1])
        else:
            template = source_node_template(key, sources, symbol=item.get('symbol'))
        if item.get('refresh_interface', False):
            if item['refresh_interface'] is not True or sources is None or template.kind not in (NodeKind.FUNCTION,NodeKind.FUNCTION_BLOCK):
                raise GXWFormatError('refresh_interface requires a source-bound callable')
            template = source_node_template(key,sources)
        layout = item.get('port_layout')
        if layout is not None:
            if (template.kind not in (NodeKind.FUNCTION,NodeKind.FUNCTION_BLOCK) or not isinstance(layout,dict)
                    or set(layout)!={'width','height','ports'} or not isinstance(layout['ports'],list)
                    or len(layout['ports'])>512):
                raise GXWFormatError('port_layout requires explicit callable dimensions and connector records')
            width,height = _coordinate(layout['width']),_coordinate(layout['height'])
            if width<1 or height<1:
                raise GXWFormatError('callable dimensions must contain its connectors')
            ports=[]
            for record in layout['ports']:
                if not isinstance(record,dict) or set(record)!={'flags','x','y'}:
                    raise GXWFormatError('each connector requires explicit flags/x/y')
                flags,px,py = _uint(record['flags']),_coordinate(record['x']),_coordinate(record['y'])
                ports.append(PortDescriptor(16,flags,px,py,struct.pack('<4I',16,flags,px,py)))
            template=replace(template,bbox=Rect(0,0,width,height),ports=tuple(ports))
            from .callable_sources import sorted_ports
            sorted_ports(template)
            if save_policy=='checked':
                if sources is None:
                    raise GXWFormatError('checked connector edits require current source interfaces')
                sources.callable(template,bind_instance=False)
        if old and old.kind == template.kind == NodeKind.FUNCTION_BLOCK and _key(old) != key and layout is None:
            # Compatible type changes retain the existing user-sized box and
            # connector coordinates, as the native saved transition does.
            shape = lambda node: sorted(port.port_kind_code & ~8 for port in node.ports)
            if shape(old) == shape(template):
                template = replace(old, type_name=template.type_name)
        symbol = item.get("symbol", template.symbol)
        if (not isinstance(symbol,str) or '\0' in symbol
                or not symbol and save_policy=='checked' and template.kind!=NodeKind.RETURN
                and not (template.kind==NodeKind.UNKNOWN and template.kind_code==0x10)
                and (old is None or old.symbol!=symbol)):
            raise GXWFormatError("structured node requires a nonempty symbol")
        if template.kind == NodeKind.FUNCTION and symbol != template.symbol:
            raise GXWFormatError("select a native function template to change a function")
        if template.kind == NodeKind.RETURN and symbol:
            raise GXWFormatError('return nodes have no stored target symbol')
        x, y = _coordinate(item.get("x")), _coordinate(item.get("y"))
        bbox = Rect(x,y,_coordinate(x+template.bbox.right-template.bbox.left),
                    _coordinate(y+template.bbox.bottom-template.bbox.top))
        derived = {"width": bbox.right-bbox.left, "height": bbox.bottom-bbox.top,
                   "ports": _ports(template, sources, bind_instance=False)}
        original_derived = derived
        if old is not None or prototype is not None:
            original_derived = {**derived, 'ports': _ports(template, original_sources or sources,
                bind_instance=old is not None and _key(old) == key)}
            if old is not None and (_key(old) != key or layout is not None):
                original_derived = {'width': old.bbox.right-old.bbox.left,
                    'height': old.bbox.bottom-old.bbox.top,
                    'ports': _ports(old, original_sources or sources)}
            if save_policy == 'checked' and original_sources is not None and template.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                try:
                    original_sources.callable(template, bind_instance=False)
                except GXWFormatError:
                    pass  # An unresolved original interface remains opaque.
                else:
                    sources.callable(template, bind_instance=False)
        if any(field in item and item[field] != value for field, value in original_derived.items()):
            raise GXWFormatError("node dimensions/ports are derived from its native ABI template")
        resize = item.get('resize')
        if resize is not None:
            if not isinstance(resize,dict) or set(resize) - {'width','height'}:
                raise GXWFormatError('resize must contain width/height')
            width = _coordinate(resize.get('width',bbox.right-bbox.left))
            height = _coordinate(resize.get('height',bbox.bottom-bbox.top))
            if (width < 1 or height <= max((port.local_y for port in template.ports),default=0)
                    or template.kind not in (NodeKind.FUNCTION,NodeKind.FUNCTION_BLOCK)
                    and not (template.kind==NodeKind.UNKNOWN and template.kind_code==0x10)):
                raise GXWFormatError('resized callable or comment must contain all connectors')
            old_width = template.bbox.right-template.bbox.left
            ports = tuple(replace(port,local_x=width if port.local_x == old_width else port.local_x)
                          for port in template.ports)
            template = replace(template,ports=ports)
            bbox = Rect(x,y,_coordinate(x+width),_coordinate(y+height))
            derived = {**derived,'width':width,'height':height,
                       'ports':_ports(replace(template,bbox=Rect(0,0,width,height)),sources,bind_instance=False)}
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
            names = [port['name'] for port in _ports(node,sources,bind_instance=False)]
            for name, patch in port_edits.items():
                if names.count(name) != 1 or not isinstance(patch, dict) or set(patch) != {'negated'} or type(patch['negated']) is not bool:
                    raise GXWFormatError('port edit requires one source endpoint and boolean negated')
                index = names.index(name)
                port = ports[index]
                flags = (port.port_kind_code & ~8) | (8 if patch['negated'] else 0)
                ports[index] = replace(port, port_kind_code=flags,
                    raw=struct.pack('<IIII',port.size,flags,port.local_x,port.local_y))
            node = replace(node, ports=tuple(ports))
        if old is not None and old.kind == NodeKind.UNKNOWN and old.kind_code != 0x10:
            from .structured_pou_writer import serialize_structured_node
            if (serialize_structured_node(node) != old.raw
                    or owner(item) != bound_blocks.get(source_membership[old.offset])):
                raise GXWFormatError(f'unsupported node kind 0x{old.kind_code:X} is preserve-only at 0x{old.offset:X}')
        ids[identity] = node
        node_ports[identity] = derived['ports']
        nodes.append(node)
        membership[offset] = owner(item)
    if any(node.kind == NodeKind.UNKNOWN and node.kind_code != 0x10 and node.offset not in used
           for node in source.nodes):
        raise GXWFormatError('unsupported source nodes cannot be deleted or replaced')
    for item in model["wires"]:
        if not isinstance(item, dict) or set(item) - {"source_offset", "start", "end", "from", "to", "via", "block", "kind"}:
            raise GXWFormatError("invalid structured wire fields")
        block_index = owner(item)
        offset = item.get("source_offset")
        old = old_wires.get(offset) if type(offset) is int else None
        if offset is not None and (old is None or offset in used):
            raise GXWFormatError("stale or repeated source wire offset")
        kind=item.get('kind','junction' if old is not None and old.record_class==3 else 'wire')
        if kind not in ('wire','junction'):
            raise GXWFormatError('line kind must be wire or junction')
        if kind=='junction' and any(field in item for field in ('from','to','via')):
            raise GXWFormatError('junction requires one explicit coincident start/end point')
        template = old or next(iter(source.wires), None)
        if template is None or old is None and (kind=='junction' or template.record_class!=2):
            template = read_project(default_baseline())[0].wires[0]
        template=replace(template,record_class=3 if kind=='junction' else 2)
        if "from" in item or "to" in item:
            if "start" in item or "end" in item or offset is not None:
                raise GXWFormatError("choose coordinate or named-port wiring")
            endpoint_types = []
            def endpoint(value, side):
                if not isinstance(value, str) or "." not in value:
                    raise GXWFormatError("wire endpoint must be node_id.port_name")
                identity, formal = value.split(".", 1)
                node = ids.get(identity)
                port_names = [port['name'] for port in node_ports.get(identity, [])]
                if node is None or port_names.count(formal) != 1:
                    raise GXWFormatError(f"unknown wire endpoint: {value}")
                if membership[node.offset] != block_index:
                    raise GXWFormatError("named wire endpoints cannot cross block boundaries")
                index = port_names.index(formal)
                port = node_ports[identity][index]
                direction, data_type = port.get('side'), port.get('data_type')
                if node.kind in (NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT):
                    direction = ({'IN':'in','OUT':'out'}.get(formal) if node.kind==NodeKind.INOUT
                                 else 'out' if node.kind==NodeKind.INPUT else 'in')
                    if sources is not None:
                        from .topology import _terminal_type
                        try:
                            data_type = _terminal_type(node.symbol, sources, sources.cpu)
                        except ValueError:
                            pass  # Untyped device widths remain source-check gaps.
                elif node.kind in GRAPH_CONTACT_NODE_KINDS | COIL_NODE_KINDS | {NodeKind.JUMP,NodeKind.RETURN}:
                    direction = {'IN': 'in', 'OUT': 'out'}.get(formal)
                    data_type = 'BOOL'
                elif sources is None:
                    direction = ('in' if node.ports[index].local_x == 0 else 'out'
                                 if node.ports[index].local_x == node.bbox.right-node.bbox.left else None)
                if direction != side:
                    raise GXWFormatError('named wire port direction is unresolved or incompatible')
                endpoint_types.append(data_type)
                return node.port_point(index)
            points = [endpoint(item.get("from"), 'out'), *[_point(p) for p in item.get("via", [])],
                      endpoint(item.get("to"), 'in')]
            # Type admission is shared with coordinate wiring and transactions
            # after the complete graph/declarations have been assembled.
        else:
            points = [_point(item.get("start")), _point(item.get("end"))]
        if kind=='junction' and points[0]!=points[-1]:
            raise GXWFormatError('junction requires one coincident start/end point')
        for start, end in zip(points, points[1:]):
            if start == end:
                if kind=='junction':
                    pass
                elif "from" in item:
                    continue  # coincident ports already connect without a wire
                else:
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


def prepare_object_project(model, *, baseline=None, save_policy='checked', declarations=None,
                           defer_project_checks=False):
    """Apply the existing editor rules before selecting a project save backend."""
    if save_policy not in ('draft', 'checked'):
        raise GXWFormatError('save_policy must be draft or checked')
    if not isinstance(model, dict):
        raise GXWFormatError("GXW object model must be an object")
    raw = default_baseline() if baseline is None else baseline
    if 'networks' in model:
        from .ladder_lowering import fbd_topology_to_object_model
        lowering_context = read_project_context(raw, model.get('program'))
        if declarations:
            final_documents = {**lowering_context.declarations, **declarations}
            lowering_context = replace(lowering_context, declarations=final_documents,
                sources=lowering_context.sources.with_declarations(final_documents))
        model = fbd_topology_to_object_model(model, lowering_context)
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
    changed = dict(declarations or {})
    if any(name not in documents or document.raw != documents[name].raw
           for name, document in changed.items()):
        raise GXWFormatError('stale or foreign declaration source in FBD transaction')
    for logical, patch in edits.items():
        if logical not in documents or not isinstance(patch, dict) or set(patch) - {"upserts", "renames", "remove", "order", "return_type"}:
            raise GXWFormatError("invalid declaration table edit")
        if logical in changed:
            raise GXWFormatError('declaration table is edited twice in the FBD transaction: '+logical)
        from .offline_authoring import _edited_document
        from .offline_project import read_offline_project
        changed[logical] = _edited_document(read_offline_project(raw).context,
            sources or ProjectCallableSources.from_project(raw, source.logical_name, documents),
            documents[logical], patch)
        if not defer_project_checks and save_policy == 'checked' and patch.get('renames') and documents[logical].scope == 'global' and len(program_names) > 1:
            raise GXWFormatError('global label rename requires all program references; this edit is bound to one Program.pou')
    context = (sources or ProjectCallableSources.from_project(raw, source.logical_name, documents)).with_declarations(
        {**documents, **changed}) if changed else sources
    program = build_object_program(source, model, sources=context if sources is not None else None,
                                   original_sources=sources if changed else None, save_policy=save_policy)
    for logical, patch in edits.items():
        if documents[logical].scope != 'global' and logical != (sources or context).local_table:
            continue
        for old_name in patch.get('renames', {}) if save_policy == 'checked' and not defer_project_checks else ():
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
    if changed and save_policy == 'checked' and not defer_project_checks:
        for node in program.nodes:
            if node.kind == NodeKind.FUNCTION_BLOCK:
                prior = (sources or context.with_declarations(documents)).label(node.symbol)
                binding = context.label(node.symbol)
                if prior and not binding or binding and (binding[1].type_code not in (0,15)
                        or binding[1].data_type.casefold()!=node.type_name.casefold()):
                    raise GXWFormatError('FB graph and edited declaration must have the same instance name and type: ' + node.symbol)
    # Synchronize only explicitly new/renamed/retyped instances. Imported
    # unresolved instances are read issues, not permission to repair their
    # declarations during a no-op or an unrelated graph edit.
    current = context or ProjectCallableSources.from_project(raw, source.logical_name, documents)
    originals = {node.offset: node for node in source.nodes}
    instance_types = {}
    for node in program.nodes:
        if node.kind != NodeKind.FUNCTION_BLOCK:
            continue
        identity = node.symbol.casefold()
        if identity in instance_types and instance_types[identity].casefold()!=node.type_name.casefold():
            raise GXWFormatError('FB instance has conflicting types: ' + node.symbol)
        instance_types[identity] = node.type_name
        old = originals.get(node.offset)
        if old is not None and (old.kind, old.symbol, old.type_name) == (node.kind, node.symbol, node.type_name):
            continue
        # A draft can retain unresolved instance/type diagnostics. Explicitly
        # removed or renamed declarations must never be silently re-created.
        if save_policy == 'draft' and changed:
            prior = (sources or current.with_declarations(documents)).label(node.symbol)
            binding = current.label(node.symbol)
            if prior and (binding is None or binding[1].data_type.casefold()!=node.type_name.casefold()):
                continue
        if save_policy=='checked':
            current.source_prototype(node,bind_instance=False)
        else:
            from .callable_sources import sorted_ports
            sorted_ports(node)
        binding = current.label(node.symbol)
        if binding and binding[1].type_code not in (0, 15):
            if save_policy=='draft':
                continue
            raise GXWFormatError('FB instance conflicts with a variable: ' + node.symbol)
        if binding and binding[1].data_type.casefold()==node.type_name.casefold():
            continue
        local = current.local_table
        table = binding[0] if binding else local
        if table not in documents:
            raise GXWFormatError('new FB instance requires the current readable local declaration table')
        if not defer_project_checks and binding and documents[table].scope == 'global' and len(program_names) > 1:
            raise GXWFormatError('global FB type edit requires all program references')
        changed[table] = edit_declarations(changed.get(table, documents[table]), upserts=[{
            'name': node.symbol, 'data_type': node.type_name, 'kind': 'function_block'}],
            native_fbd_locals=table == local)
        current = current.with_declarations({**documents, **changed})
        if save_policy=='checked':
            current.source_prototype(node)
    if not defer_project_checks:
        from .fbd_validation import inspect_graph, require_graph_checks
        before_context = read_project_context(raw, source.logical_name)
        before = source_checks(before_context)
        after_context = replace(before_context, program=program, declarations={**documents, **changed}, sources=current)
        after = source_checks(after_context, semantic=inspect_graph(program, current))
        require_graph_checks(before['findings'], after['findings'], save_policy=save_policy)
    return prepare_project_write(raw, program, declarations=changed, sync_fb_declarations=False)


def generate_object_project(model, *, baseline=None):
    return write_prepared_project(prepare_object_project(model, baseline=baseline))

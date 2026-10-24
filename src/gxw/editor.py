"""Deterministic FBD draft commands and presentation projections.

A draft is not accepted, compiled, or saved by this module. Native templates,
source offsets, opaque records and the existing generation validation remain
owned by gxw. Every client sends edit intent rather than implementing PLC rules.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Mapping
from uuid import uuid4

from .declarations import _type_parts
from .models import GXWFormatError, NodeKind
from .object_model import _key, catalog_description, default_baseline, read_project, read_project_context


def empty_model():
    source, _, _ = read_project(default_baseline())
    return {"schema_version": 1, "program": source.logical_name,
            "canvas_height": 12, "nodes": [], "wires": []}


def _project_sources(raw):
    """Per-source language, POU identity and offline edit capability, when readable."""
    from .offline_project import read_offline_project
    try:
        rows = read_offline_project(raw).sources()
    except Exception as error:
        return None, str(error)
    keys = ('source_name', 'name', 'language', 'unit_kind', 'capabilities')
    return [{key: deepcopy(row.get(key)) for key in keys} for row in rows if row.get('unit_kind')], None


def _structure_members(sources):
    """Complete composite member rows from the current project, without truncation.

    A type definition is one unit: the task closure decides which types a request
    needs, and the chosen type then keeps every member row.
    """
    names = set(sources.project_structure_names) | {name for _, name in sources.library_structures}
    documents = {}
    for name in names:
        try:
            documents[name] = sources.structure(name)
        except GXWFormatError:
            continue  # An ambiguous owner stays absent and becomes a dependency gap.
    result = {}
    for name, document in sorted(documents.items()):
        result[name] = [{'name': member.name, 'data_type': member.data_type,
                         'initial_value': member.initial_value,
                         'comment': member.comment,
                         'owner_source': document.logical_name}
                        for member in getattr(document, 'members', None) or ()]
    return result


def _json_safe(value):
    """Plain JSON values for saved project facts (no tuples or raw bytes)."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def editor_catalog(context=None):
    from .project_templates import fbd_generation_models, unsupported_fbd_models
    from .generation_contract import FBD_PARAMETER_MODEL_SCHEMA
    from .ladder_lowering import fbd_topology_from_context
    context = context or read_project_context(default_baseline())
    from .fb_authoring import _context
    from .project_metadata import (read_project_system_variables, project_gui_configuration,
                                   read_project_compile_options)
    _,streams,_,_,_=_context(context.raw)
    metadata=next(name for name in streams if name.endswith('.prj'))
    program_sources, sources_error = _project_sources(context.raw)
    groups = getattr(context.sources, 'source_groups', None) or {}
    selected_source = {'source_name': context.program.logical_name,
                       'name': context.program.logical_name,
                       'language': 'fbd', 'unit_kind': 'program',
                       'capabilities': None, 'capabilities_status': 'unverified'}
    for row in program_sources or ():
        if row.get('source_name') == context.program.logical_name:
            selected_source = {**row, 'capabilities_status': 'available'}
            break
    try:
        compile_options = read_project_compile_options(streams[metadata])
    except Exception as error:  # an unreadable option array stays an explicit gap
        compile_options = {'status': 'unverified', 'reason': str(error)}
    return {'nodes': context.catalog(), 'generation_plc_models': list(fbd_generation_models()),
            'unsupported_generation_plc_models': unsupported_fbd_models(),
            'empty_model': context.empty_model(), 'program': context.program.logical_name,
            'programs': list(context.fbd_programs), 'schema_version': 2, 'cpu': context.sources.cpu,
            'task_scopes': context.task_scopes(), 'parameter_schema': deepcopy(FBD_PARAMETER_MODEL_SCHEMA),
            'parameter_model': fbd_topology_from_context(context),
            'project_configuration': dict(source_name=metadata,system_variable_ranges={key:list(value)
                for key,value in read_project_system_variables(streams[metadata])['ranges'].items()},
                gui_configuration=project_gui_configuration(context.raw)),
            'empty_parameter_model': dict(schema_version=2, program=context.program.logical_name, networks=[]),
            'declaration_tables': {name: doc.scope for name, doc in context.declarations.items()},
            # Task identity and project conditions. These describe saved source
            # facts only; they grant no execution or compilation result.
            'project_mode': 'structured' if groups else 'simple',
            'graphical_language_mode': 'fbd',
            'graphical_language_mode_status': 'shared_208_source_record',
            'selected_source': selected_source,
            'project_sources': program_sources,
            'project_sources_status': 'unverified' if sources_error else 'available',
            'project_sources_error': sources_error,
            'compile_options': _json_safe(compile_options),
            'structures': _structure_members(context.sources),
            'catalog_scope': 'task'}


def declaration_rows(model, table):
    """Project pending renames/deletes/upserts without rewriting the source."""
    edit = model.get("declaration_edits", {}).get(table, {})
    renames = edit.get("renames", {})
    upserts = edit.get("upserts", [])
    rows = [{**row, "name": renames.get(row["name"], row["name"])}
            for row in model.get("labels", {}).get(table, []) if row["name"] not in edit.get("remove", [])]
    for row in rows:
        row.update(next((item for item in upserts if item["name"] == row["name"]), {}))
    names = {row["name"] for row in rows}
    rows.extend(deepcopy(row) for row in upserts if row["name"] not in names)
    if 'order' in edit:
        by_name = {row['name']:row for row in rows}
        order = edit['order']
        if len(order) != len(rows) or len(set(order)) != len(order) or set(order) != set(by_name):
            raise GXWFormatError('declaration order must contain each current row exactly once')
        rows = [by_name[name] for name in order]
    return rows


def _node_capabilities(node, *, source=None, sources=None):
    """Use the same object boundary for editor buttons and edit commands."""
    key=node['template']
    if source is not None and source.kind==NodeKind.UNKNOWN and source.kind_code!=0x10:
        return dict(resizable=False,callable=False)
    is_call=isinstance(key,str) and key.startswith(('function:','function_block:'))
    is_comment=key=='comment' or key=='unknown' and source is not None and source.kind_code==0x10
    refresh=False
    if is_call and sources is not None:
        from .object_model import source_node_template
        try:
            source_node_template(key,sources)
        except GXWFormatError:
            pass
        else:
            refresh=True
    return dict(resizable=is_call or is_comment,callable=refresh)


def presentation(model, *, templates=None, context=None):
    if templates is None and context:
        templates = context.catalog(declaration_edits=model.get('declaration_edits'))
    catalog = {item["template"]: item for item in (templates if templates is not None else catalog_description())}
    tables = list(dict.fromkeys([*model.get("labels", {"1.Labels.lh": [], "Global1.gh": []}),
                                *model.get("declaration_edits", {})]))
    nodes = []
    endpoints = []
    sources = context.draft_sources(model.get('declaration_edits')) if context else None
    source_nodes = {node.offset: node for node in context.program.nodes} if context else {}
    for node in model["nodes"]:
        spec = catalog.get(node["template"], {})
        ports = node.get("ports", spec.get("ports", []))
        source = source_nodes.get(node.get('source_offset'))
        if node.get('port_layout'):
            layout=node['port_layout']
            # The writer owns validation. This projection displays the exact
            # explicit connector identities while an interface is incomplete.
            ports=[dict(name=str(index),x=port['x'],y=port['y'],
                        side='in' if port['x']==0 else 'out',data_type=None,
                        negated=bool(port['flags']&8)) for index,port in enumerate(layout['ports'])]
            if context:
                from .object_model import build_object_program,_ports
                probe=deepcopy(model);probe['wires']=[]
                built=build_object_program(context.program,probe,sources=sources,save_policy='draft')
                index=next(i for i,item in enumerate(model['nodes']) if item['id']==node['id'])
                ports=_ports(built.nodes[index],sources,bind_instance=False)
        elif sources and node.get('refresh_interface'):
            from .object_model import source_node_template, _ports
            template = source_node_template(node['template'], sources)
            ports = _ports(template, sources, bind_instance=False)
        elif sources and source and node['template'].startswith(('function:', 'function_block:')):
            try:
                if (node['template'].startswith('function_block:') and source.kind == NodeKind.FUNCTION_BLOCK
                        and _key(source) != node['template']):
                    current = replace(source, symbol=node['symbol'], type_name=node['template'].split(':', 1)[1])
                    ports = sources.callable(current, bind_instance=False)['ports']
                elif _key(source) == node['template']:
                    ports = sources.callable(replace(source, symbol=node['symbol']))['ports']
            except GXWFormatError:
                pass  # Keep unbound source ports visible with the source gap.
        if node.get('resize') and ports:
            old_width = (source.bbox.right-source.bbox.left if source is not None and not node.get('refresh_interface')
                         else spec.get('width',max(port['x'] for port in ports)))
            width = node['resize'].get('width',old_width)
            ports = [{**port, 'x':width if port['x']==old_width else port['x']} for port in ports]
        ports = [{**port, **node.get('port_edits', {}).get(port['name'], {})} for port in ports]
        nodes.append({"id": node["id"], "symbol_editable": not node['template'].startswith('function:') and node['template']!='return',
                      "ports": ports, **_node_capabilities(node,source=source,sources=sources)})
        for port in ports:
            endpoints.append({"value": f'{node["id"]}.{port["name"]}',
                              "label": f'{node["symbol"]} · {port["name"]} ({node["id"]})',
                              "point": [node["x"] + port["x"], node["y"] + port["y"]]})
    from .structured_pou import structured_block_text
    original_blocks = {block.offset:block for block in context.program.blocks} if context else {}
    block_rows = []
    for index, spec in enumerate(_draft_blocks(model, context)):
        original = original_blocks.get(spec.get('source_offset'))
        title,comment = structured_block_text(original.raw_header) if original is not None else ('','')
        block_rows.append({**spec, 'index':index,'title':spec.get('title',title),
                           'comment':spec.get('comment',comment)})
    return {"tables": tables, "rows": {table: declaration_rows(model, table) for table in tables},
            "nodes": nodes, "endpoints": endpoints, 'blocks':block_rows}


def _draft_blocks(model, context):
    if 'blocks' in model:
        return model['blocks']
    original = context.program.blocks[0] if context else None
    return [dict(source_offset=original.offset if original else None,
                 canvas_height=model.get('canvas_height',12))]


def _block_index(value, blocks, *, insertion=False):
    if type(value) is not int or not 0 <= value < len(blocks)+int(insertion):
        raise GXWFormatError('select an existing network index')
    return value


def _remap_blocks(model, mapping):
    for collection in ('nodes','wires'):
        retained=[]
        for record in model[collection]:
            old=record.get('block',0)
            if old in mapping:
                record['block']=mapping[old]
                retained.append(record)
        model[collection]=retained
    model['canvas_height']=sum(row['canvas_height'] for row in model['blocks'])


def _patch(model, table):
    return model.setdefault("declaration_edits", {}).setdefault(table, {})


def _rebind_template_references(model, node, old_ports, new_ports):
    """Keep only unambiguous current endpoints on the same native port position."""
    positions = {}
    for port in new_ports:
        positions.setdefault((port.get('side'),port['x'],port['y']),[]).append(port['name'])
    names = {}
    for port in old_ports:
        matches = positions.get((port.get('side'),port['x'],port['y']),[])
        if len(matches)==1:
            names[port['name']] = matches[0]
    prefix = node['id'] + '.'
    referenced = set(node.get('port_edits',{})) | {
        wire[end][len(prefix):] for wire in model['wires'] for end in ('from','to')
        if isinstance(wire.get(end),str) and wire[end].startswith(prefix)}
    if not referenced <= names.keys():
        raise GXWFormatError('template change cannot preserve referenced native port positions')
    if 'port_edits' in node:
        node['port_edits'] = {names[name]:patch for name,patch in node['port_edits'].items()}
    for wire in model['wires']:
        for end in ('from','to'):
            endpoint = wire.get(end)
            if isinstance(endpoint,str) and endpoint.startswith(prefix):
                wire[end] = prefix + names[endpoint[len(prefix):]]


def _rebind_formal_references(model, table, before, context):
    """Keep draft port edits and named wires on the same source formal slot."""
    after = context.draft_sources(model.get('declaration_edits'))
    source_nodes = {node.offset: node for node in context.program.nodes}
    old_catalog = new_catalog = None
    endpoints = {}
    for node in model['nodes']:
        key = node['template']
        if not key.startswith(('function:', 'function_block:')):
            continue
        kind = 'FUNCTION_BLOCK' if key.startswith('function_block:') else 'FUNCTION'
        try:
            stream, _ = before.definition(key.split(':', 1)[1], kind)
        except GXWFormatError:
            continue  # An unbound source object supplies no formal identity.
        if stream != table:
            continue
        source = source_nodes.get(node.get('source_offset', node.get('prototype_offset')))
        if source is not None and (source.type_name if kind == 'FUNCTION_BLOCK' else source.symbol) != key.split(':', 1)[1]:
            source = None
        if source is not None:
            old_ports = before.callable(source, bind_instance=False)['ports']
            new_ports = after.callable(source, bind_instance=False)['ports']
        else:
            if old_catalog is None:
                old_catalog = {item['template']: item for item in catalog_description(sources=before)}
                new_catalog = {item['template']: item for item in catalog_description(sources=after)}
            if key not in old_catalog or key not in new_catalog:
                raise GXWFormatError('formal rename requires a resolved callable interface')
            old_ports, new_ports = old_catalog[key]['ports'], new_catalog[key]['ports']
        if len(old_ports) != len(new_ports) or any(a['side'] != b['side'] for a, b in zip(old_ports, new_ports)):
            raise GXWFormatError('formal rename changed callable port layout')
        names = {a['name']: b['name'] for a, b in zip(old_ports, new_ports)}
        if 'port_edits' in node:
            if any(name not in names for name in node['port_edits']):
                raise GXWFormatError('formal rename requires current source endpoints for port edits')
            node['port_edits'] = {names.get(name, name): patch for name, patch in node['port_edits'].items()}
        endpoints.update({node['id'] + '.' + a: node['id'] + '.' + b for a, b in names.items()})
    for wire in model['wires']:
        for end in ('from', 'to'):
            if end in wire:
                wire[end] = endpoints.get(wire[end], wire[end])


def edit_draft(value, command=None, *, templates=None, context=None):
    """Apply one atomic command to a copy; never mutate the caller's document."""
    model = empty_model() if value is None else deepcopy(value)
    command = command or {}
    action = command.get("action", "inspect")
    if templates is None and context:
        templates = context.catalog(declaration_edits=model.get('declaration_edits'))
    catalog = {item["template"]: item for item in (templates if templates is not None else catalog_description())}
    if action in ('add_block','update_block','delete_block','move_block'):
        blocks = model.setdefault('blocks',deepcopy(_draft_blocks(model,context)))
        if action == 'add_block':
            index = _block_index(command.get('index',len(blocks)),blocks,insertion=True)
            blocks.insert(index,dict(source_offset=None,canvas_height=command.get('canvas_height',12),
                title=command.get('title',''),comment=command.get('comment','')))
            _remap_blocks(model,{old:old+(old>=index) for old in range(len(blocks)-1)})
        else:
            index = _block_index(command['index'],blocks)
            if action == 'update_block':
                field=command['field']
                if field not in ('title','comment','canvas_height'):
                    raise GXWFormatError('unsupported network field')
                blocks[index][field]=command['value']
            elif action == 'delete_block':
                blocks.pop(index)
                _remap_blocks(model,{old:old-(old>index) for old in range(len(blocks)+1) if old!=index})
                if not blocks:
                    blocks.append(dict(source_offset=None,canvas_height=12,title='',comment=''))
            else:
                target=_block_index(command['target'],blocks)
                order=list(range(len(blocks)));order.insert(target,order.pop(index))
                model['blocks']=[blocks[old] for old in order]
                _remap_blocks(model,{old:new for new,old in enumerate(order)})
        model['canvas_height']=sum(row['canvas_height'] for row in model['blocks'])
    elif action in ('resize_node','refresh_callable','set_arity','set_port_layout'):
        node=next((item for item in model['nodes'] if item['id']==command['id']),None)
        if node is None:
            raise GXWFormatError('select a callable node')
        source=next((item for item in context.program.nodes if item.offset==node.get('source_offset')),None) if context else None
        capabilities=_node_capabilities(node,source=source,
            sources=context.draft_sources(model.get('declaration_edits')) if context else None)
        if action=='resize_node' and not capabilities['resizable']:
            raise GXWFormatError('select a resizable callable or comment')
        if action=='refresh_callable' and not capabilities['callable']:
            raise GXWFormatError('current source cannot rebuild this callable interface')
        if action in ('set_arity','set_port_layout') and not node['template'].startswith(('function:','function_block:')):
            raise GXWFormatError('select a callable node')
        if action=='set_port_layout':
            node['port_layout']=deepcopy(command['value'])
            node.pop('resize',None);node.pop('refresh_interface',None)
        elif action=='resize_node':
            node['resize']={key:command[key] for key in ('width','height') if key in command}
        else:
            if action=='set_arity':
                import re
                arity=command.get('arity')
                if type(arity) is not int or not 1<=arity<=256 or not node['template'].startswith('function:'):
                    raise GXWFormatError('function arity must be between 1 and 256')
                node['template']=re.sub(r'-[1-9][0-9]*$','',node['template'])+'-'+str(arity)
                node['symbol']=node['template'].split(':',1)[1]
            node['refresh_interface']=True
            node.pop('port_layout',None)
            for field in ('ports','width','height','prototype_offset'):
                node.pop(field,None)
    elif action == "add_node":
        spec = catalog.get(command["template"])
        if not spec:
            raise GXWFormatError("no verified native ABI template")
        number = len(model["nodes"]) + 1
        symbol = spec["symbol"] or (f"FB_{number}" if spec["kind"] == "function_block"
                                   else "Y0" if spec["kind"] in {"coil", "output"} else "X0")
        if spec['kind']=='return':
            symbol=''
        node = {"id": "node_" + uuid4().hex, "template": spec["template"], "symbol": symbol,
                "x": 3, "y": 2 + len(model["nodes"]) * 5}
        if spec.get('prototype_offset') is not None:
            node['prototype_offset'] = spec['prototype_offset']
        if model.get("blocks"):
            node["block"] = 0
        model["nodes"].append(node)
    elif action in {"update_node", "delete_node"}:
        node = next((item for item in model["nodes"] if item["id"] == command["id"]), None)
        if node is None:
            raise GXWFormatError("draft node no longer exists")
        if action == "delete_node":
            model["nodes"].remove(node)
            prefix = node["id"] + "."
            model["wires"] = [wire for wire in model["wires"]
                              if not any(wire.get(end, "").startswith(prefix) for end in ("from", "to"))]
        else:
            field, new = command["field"], command["value"]
            if field in {"x", "y", "block"}:
                if field=='block':
                    _block_index(new,_draft_blocks(model,context))
                node[field] = new
            elif field == "template":
                if new not in catalog:
                    raise GXWFormatError("no verified native ABI template")
                old_ports = next(row['ports'] for row in presentation(model,templates=templates,context=context)['nodes']
                                 if row['id']==node['id'])
                new_ports = catalog[new]['ports']
                if context and new.startswith('function_block:'):
                    source = next((item for item in context.program.nodes if item.offset == node.get('source_offset')), None)
                    if source is not None and source.kind == NodeKind.FUNCTION_BLOCK:
                        try:
                            new_ports = context.draft_sources(model.get('declaration_edits')).callable(
                                replace(source, type_name=new.split(':', 1)[1]), bind_instance=False)['ports']
                        except GXWFormatError:
                            pass
                _rebind_template_references(model,node,old_ports,new_ports)
                node["template"] = new
                if catalog[new]["symbol"]:
                    node["symbol"] = catalog[new]["symbol"]
                for key in ("ports", "width", "height"):
                    node.pop(key, None)
                if context and new.startswith('function_block:'):
                    binding = context.draft_sources(model.get('declaration_edits')).label(node['symbol'])
                    if binding is not None:
                        table,row = binding
                        upserts = _patch(model,table).setdefault('upserts',[])
                        item = next((item for item in upserts if item['name']==row.name),None)
                        if item is None:
                            item = {'name':row.name};upserts.append(item)
                        item.update(data_type=new.split(':',1)[1],kind='function_block')
            elif field == "symbol":
                if node['template'].startswith('function:') or node['template']=='return':
                    raise GXWFormatError("select a native function template to change a function")
                old_symbol = node['symbol']
                if node['template'].startswith('function_block:') and old_symbol != new:
                    local = model['program'].removesuffix('.Program.pou') + '.Labels.lh'
                    if context:
                        binding = context.draft_sources(model.get('declaration_edits')).label(old_symbol)
                        hits = [(binding[0], binding[1].name)] if binding else []
                    else:
                        hits = [(table, row['name']) for table in model.get('labels', {})
                                if table == local or table.endswith('.gh')
                                for row in declaration_rows(model, table)
                                if row['name'].casefold() == old_symbol.casefold()]
                    if len(hits) > 1:
                        raise GXWFormatError('ambiguous FB instance declaration')
                    if hits:
                        return edit_draft(model, {'action': 'update_label', 'table': hits[0][0],
                            'name': hits[0][1], 'field': 'name', 'value': new}, templates=templates, context=context)
                node[field] = new
            else:
                raise GXWFormatError("unsupported node edit")
    elif action == 'update_port':
        node = next((item for item in model['nodes'] if item['id'] == command['id']), None)
        if node is None or model.get('schema_version') != 2 or not node['template'].startswith(('function:', 'function_block:')):
            raise GXWFormatError('port edit requires a source-bound callable node')
        ports = next(item['ports'] for item in presentation(model, templates=templates, context=context)['nodes']
                     if item['id'] == node['id'])
        if sum(port['name'] == command['port'] for port in ports) != 1 or type(command['negated']) is not bool:
            raise GXWFormatError('port edit requires an existing endpoint and boolean negated')
        node.setdefault('port_edits', {})[command['port']] = {'negated': command['negated']}
    elif action == "add_wire":
        endpoints = {row["value"]: row["point"] for row in presentation(model, templates=templates, context=context)["endpoints"]}
        source, target = command["from"], command["to"]
        if source not in endpoints or target not in endpoints or source == target:
            raise GXWFormatError("select two existing, distinct endpoints")
        a, b = endpoints[source], endpoints[target]
        wire = {"from": source, "to": target}
        owners={node['id']:node.get('block',0) for node in model['nodes']}
        source_block=owners[source.split('.',1)[0]]
        if source_block!=owners[target.split('.',1)[0]]:
            raise GXWFormatError('wire endpoints cannot cross network boundaries')
        if model.get('blocks'):
            wire['block']=source_block
        if a[0] != b[0] and a[1] != b[1]:
            mid = (a[0] + b[0]) // 2
            wire["via"] = [[mid, a[1]], [mid, b[1]]]
        model["wires"].append(wire)
    elif action == "add_bus":
        index=_block_index(command.get('block',0),_draft_blocks(model,context))
        wire={"start": [1, 0], "end": [1, _draft_blocks(model,context)[index]['canvas_height']]}
        if model.get('blocks'):wire['block']=index
        model['wires'].append(wire)
    elif action in ('add_junction','move_junction'):
        from .object_model import _point
        point=_point(command['point'])
        position=[point.x,point.y]
        if action=='add_junction':
            block=_block_index(command.get('block',0),_draft_blocks(model,context))
            wire=dict(kind='junction',start=position,end=list(position))
            if model.get('blocks'):wire['block']=block
            model['wires'].append(wire)
        else:
            index=command['index']
            if type(index) is not int or not 0<=index<len(model['wires']):
                raise GXWFormatError('select an existing junction index')
            wire=model['wires'][index]
            if wire.get('kind')!='junction':
                raise GXWFormatError('move_junction requires a junction record')
            wire.update(start=position,end=list(position))
    elif action in {"update_wire", "delete_wire"}:
        index = command["index"]
        if type(index) is not int or not 0<=index<len(model['wires']):
            raise GXWFormatError('select an existing wire index')
        if action == "delete_wire":
            model["wires"].pop(index)
        else:
            field, new = command["field"], command["value"]
            if field not in {"start", "end", "via", "block"}:
                raise GXWFormatError("unsupported wire edit")
            if model['wires'][index].get('kind')=='junction' and field!='block':
                raise GXWFormatError('use move_junction to change its single point')
            if field=='block':_block_index(new,_draft_blocks(model,context))
            if field == "via" and isinstance(new, str):
                import re
                parts = [part.strip() for part in new.split(";")] if new.strip() else []
                if any(not re.fullmatch(r"[0-9]+\s*,\s*[0-9]+", part) for part in parts):
                    raise GXWFormatError("折点请填写 x,y; x,y 格式的非负整数坐标。")
                new = [[int(part) for part in point.split(",")] for point in parts]
            model["wires"][index][field] = new
    elif action in ('reorder_labels','set_return_type'):
        table=command['table']
        patch=_patch(model,table)
        if action=='reorder_labels':
            order=command['order'];names=[row['name'] for row in declaration_rows(model,table)]
            if not isinstance(order,list) or len(order)!=len(names) or len(set(order))!=len(order) or set(order)!=set(names):
                raise GXWFormatError('declaration order must contain each current row exactly once')
            patch['order']=list(order)
        else:
            patch['return_type']=command['value']
        if context:context.draft_sources(model.get('declaration_edits'))
    elif action in {"add_label", "update_label", "delete_label"}:
        table = command["table"]
        patch = _patch(model, table)
        rows = declaration_rows(model, table)
        if action == "add_label":
            names = {row["name"] for row in rows}
            number = len(rows) + 1
            while f"label_{number}" in names:
                number += 1
            patch.setdefault("upserts", []).append({"name": f"label_{number}", "data_type": "BOOL",
                "kind": "variable", "class_name": "VAR_GLOBAL" if table.endswith(".gh") else "VAR"})
            if 'order' in patch:
                patch['order'].append(f'label_{number}')
        else:
            name = command["name"]
            if not any(row["name"] == name for row in rows):
                raise GXWFormatError("draft declaration no longer exists")
            source = next((row for row in model.get("labels", {}).get(table, [])
                           if patch.get("renames", {}).get(row["name"], row["name"]) == name), None)
            upserts = patch.setdefault("upserts", [])
            item = next((row for row in upserts if row["name"] == name), None)
            if action == "delete_label":
                if source:
                    patch.setdefault("remove", []).append(source["name"])
                    patch.get("renames", {}).pop(source["name"], None)
                patch["upserts"] = [row for row in upserts if row["name"] != name]
                if 'order' in patch:
                    patch['order'].remove(name)
            else:
                field, new = command["field"], command["value"]
                if field not in {"name", "data_type", "kind", "class_name", "initial_value", "device", "iec_address", "comment"} or not isinstance(new, str):
                    raise GXWFormatError("unsupported declaration edit")
                if field == "name":
                    previous_sources = context.draft_sources(model.get('declaration_edits')) if context else None
                    if source:
                        patch.setdefault("renames", {})[source["name"]] = new
                    if item:
                        item["name"] = new
                    if 'order' in patch:
                        patch['order'] = [new if old==name else old for old in patch['order']]
                    # The graph and its source declaration are one edit. Direct
                    # terminals and instance/member references keep their binding.
                    current_local = previous_sources.local_table if previous_sources else model['program'].removesuffix('.Program.pou') + '.Labels.lh'
                    visible = table == current_local or table.endswith('.gh')
                    if visible and previous_sources:
                        binding = previous_sources.label(name)
                        visible = binding is not None and binding[0] == table
                    for node in model['nodes'] if visible else ():
                        if node['template'].startswith('function:'):
                            continue
                        if node['symbol'].casefold() == name.casefold():
                            node['symbol'] = new
                        elif node['symbol'].casefold().startswith((name.casefold() + '.', name.casefold() + '[')):
                            node['symbol'] = new + node['symbol'][len(name):]
                    if context:
                        _rebind_formal_references(model, table, previous_sources, context)
                else:
                    if item is None:
                        item = {"name": name}
                        upserts.append(item)
                    if field == 'data_type' and source and source['kind'] == 'function_block' and 'kind' not in item:
                        item['kind'] = 'function_block'
                    if field == 'data_type' and context:
                        # The current source owns named structure types. A new
                        # row's initial variable kind is only its BOOL default.
                        base, _ = _type_parts(new)
                        sources = context.draft_sources(model.get('declaration_edits'))
                        try:
                            sources.structure(base)
                        except GXWFormatError:
                            if source and source['kind'] == 'structure':
                                item['kind'] = 'variable'
                        else:
                            item['kind'] = 'structure'
                    item[field] = new
    elif action != "inspect":
        raise GXWFormatError("unsupported FBD editor command")
    if context:
        templates = context.catalog(declaration_edits=model.get('declaration_edits'))
    return {"model": model, "presentation": presentation(model, templates=templates, context=context), "gx_compile": "not_run"}

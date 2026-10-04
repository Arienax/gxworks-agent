"""Deterministic FBD draft commands and presentation projections.

A draft is not accepted, compiled, or saved by this module. Native templates,
source offsets, opaque records and the existing generation validation remain
owned by gxw. Every client sends edit intent rather than implementing PLC rules.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from .models import GXWFormatError
from .object_model import catalog_description, default_baseline, read_project, read_project_context


GENERATION_PLC_MODELS = ("FX3U",)


def empty_model():
    source, _, _ = read_project(default_baseline())
    return {"schema_version": 1, "program": source.logical_name,
            "canvas_height": 12, "nodes": [], "wires": []}


def editor_catalog(context=None):
    context = context or read_project_context(default_baseline())
    return {'nodes': context.catalog(), 'generation_plc_models': list(GENERATION_PLC_MODELS),
            'empty_model': context.empty_model(), 'program': context.program.logical_name,
            'programs': context.programs, 'schema_version': 2, 'cpu': context.sources.cpu,
            'declaration_tables': {name: doc.scope for name, doc in context.declarations.items()}}


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
    return rows


def presentation(model, *, templates=None, context=None):
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
        if sources and source and node['template'].startswith(('function:', 'function_block:')):
            try:
                ports = sources.callable(replace(source, symbol=node['symbol']))['ports']
            except GXWFormatError:
                pass  # Keep unbound source ports visible with the source gap.
        ports = [{**port, **node.get('port_edits', {}).get(port['name'], {})} for port in ports]
        nodes.append({"id": node["id"], "symbol_editable": not node['template'].startswith('function:'), "ports": ports})
        for port in ports:
            endpoints.append({"value": f'{node["id"]}.{port["name"]}',
                              "label": f'{node["symbol"]} · {port["name"]} ({node["id"]})',
                              "point": [node["x"] + port["x"], node["y"] + port["y"]]})
    return {"tables": tables, "rows": {table: declaration_rows(model, table) for table in tables},
            "nodes": nodes, "endpoints": endpoints}


def _patch(model, table):
    return model.setdefault("declaration_edits", {}).setdefault(table, {})


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
    catalog = {item["template"]: item for item in (templates if templates is not None else catalog_description())}
    if action == "add_node":
        spec = catalog.get(command["template"])
        if not spec:
            raise GXWFormatError("no verified native ABI template")
        number = len(model["nodes"]) + 1
        symbol = spec["symbol"] or (f"FB_{number}" if spec["kind"] == "function_block"
                                   else "Y0" if spec["kind"] in {"coil", "output"} else "X0")
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
            if field in {"x", "y"}:
                node[field] = new
            elif field == "template":
                if new not in catalog:
                    raise GXWFormatError("no verified native ABI template")
                node["template"] = new
                if catalog[new]["symbol"]:
                    node["symbol"] = catalog[new]["symbol"]
                for key in ("ports", "width", "height"):
                    node.pop(key, None)
            elif field == "symbol":
                if node['template'].startswith('function:'):
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
        if a[0] != b[0] and a[1] != b[1]:
            mid = (a[0] + b[0]) // 2
            wire["via"] = [[mid, a[1]], [mid, b[1]]]
        model["wires"].append(wire)
    elif action == "add_bus":
        model["wires"].append({"start": [1, 0], "end": [1, model.get("canvas_height") or 12]})
    elif action in {"update_wire", "delete_wire"}:
        index = command["index"]
        if action == "delete_wire":
            model["wires"].pop(index)
        else:
            field, new = command["field"], command["value"]
            if field not in {"start", "end", "via"}:
                raise GXWFormatError("unsupported wire edit")
            if field == "via" and isinstance(new, str):
                import re
                parts = [part.strip() for part in new.split(";")] if new.strip() else []
                if any(not re.fullmatch(r"[0-9]+\s*,\s*[0-9]+", part) for part in parts):
                    raise GXWFormatError("折点请填写 x,y; x,y 格式的非负整数坐标。")
                new = [[int(part) for part in point.split(",")] for point in parts]
            model["wires"][index][field] = new
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
                    # The graph and its source declaration are one edit. Direct
                    # terminals and instance/member references keep their binding.
                    current_local = model['program'].removesuffix('.Program.pou') + '.Labels.lh'
                    visible = table == current_local or table.endswith('.gh')
                    if visible and previous_sources:
                        binding = previous_sources.label(name)
                        visible = binding is not None and binding[0] == table
                    for node in model['nodes'] if visible else ():
                        if node['template'].startswith('function:'):
                            continue
                        if node['symbol'].casefold() == name.casefold():
                            node['symbol'] = new
                        elif node['symbol'].casefold().startswith(name.casefold() + '.'):
                            node['symbol'] = new + node['symbol'][len(name):]
                    if context:
                        _rebind_formal_references(model, table, previous_sources, context)
                else:
                    if item is None:
                        item = {"name": name}
                        upserts.append(item)
                    if field == 'data_type' and source and source['kind'] == 'function_block' and 'kind' not in item:
                        item['kind'] = 'function_block'
                    item[field] = new
    elif action != "inspect":
        raise GXWFormatError("unsupported FBD editor command")
    if context:
        templates = context.catalog(declaration_edits=model.get('declaration_edits'))
    return {"model": model, "presentation": presentation(model, templates=templates, context=context), "gx_compile": "not_run"}

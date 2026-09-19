"""Deterministic FBD draft commands and presentation projections.

A draft is not accepted, compiled, or saved by this module. Native templates,
source offsets, opaque records and the existing generation validation remain
owned by gxw. Every client sends edit intent rather than implementing PLC rules.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .models import GXWFormatError
from .object_model import catalog_description, default_baseline, read_project


GENERATION_PLC_MODELS = ("FX3U",)


def empty_model():
    source, _, _ = read_project(default_baseline())
    return {"schema_version": 1, "program": source.logical_name,
            "canvas_height": 12, "nodes": [], "wires": []}


def editor_catalog():
    return {"nodes": catalog_description(), "generation_plc_models": list(GENERATION_PLC_MODELS),
            "empty_model": empty_model()}


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


def presentation(model):
    catalog = {item["template"]: item for item in catalog_description()}
    tables = list(dict.fromkeys([*model.get("labels", {"1.Labels.lh": [], "Global1.gh": []}),
                                *model.get("declaration_edits", {})]))
    nodes = []
    endpoints = []
    for node in model["nodes"]:
        spec = catalog.get(node["template"], {})
        ports = node.get("ports", spec.get("ports", []))
        nodes.append({"id": node["id"], "symbol_editable": spec.get("kind") != "function", "ports": ports})
        for port in ports:
            endpoints.append({"value": f'{node["id"]}.{port["name"]}',
                              "label": f'{node["symbol"]} · {port["name"]} ({node["id"]})',
                              "point": [node["x"] + port["x"], node["y"] + port["y"]]})
    return {"tables": tables, "rows": {table: declaration_rows(model, table) for table in tables},
            "nodes": nodes, "endpoints": endpoints}


def _patch(model, table):
    return model.setdefault("declaration_edits", {}).setdefault(table, {})


def edit_draft(value, command=None):
    """Apply one atomic command to a copy; never mutate the caller's document."""
    model = empty_model() if value is None else deepcopy(value)
    command = command or {}
    action = command.get("action", "inspect")
    catalog = {item["template"]: item for item in catalog_description()}
    if action == "add_node":
        spec = catalog.get(command["template"])
        if not spec:
            raise GXWFormatError("no verified native ABI template")
        number = len(model["nodes"]) + 1
        symbol = spec["symbol"] or (f"FB_{number}" if spec["kind"] == "function_block"
                                   else "Y0" if spec["kind"] in {"coil", "output"} else "X0")
        node = {"id": "node_" + uuid4().hex, "template": spec["template"], "symbol": symbol,
                "x": 3, "y": 2 + len(model["nodes"]) * 5}
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
                if catalog.get(node["template"], {}).get("kind") == "function":
                    raise GXWFormatError("select a native function template to change a function")
                node[field] = new
            else:
                raise GXWFormatError("unsupported node edit")
    elif action == "add_wire":
        endpoints = {row["value"]: row["point"] for row in presentation(model)["endpoints"]}
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
                    if source:
                        patch.setdefault("renames", {})[source["name"]] = new
                    if item:
                        item["name"] = new
                else:
                    if item is None:
                        item = {"name": name}
                        upserts.append(item)
                    item[field] = new
    elif action != "inspect":
        raise GXWFormatError("unsupported FBD editor command")
    return {"model": model, "presentation": presentation(model), "gx_compile": "not_run"}

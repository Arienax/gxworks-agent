"""Named semantic connections to the existing source-bound object editor.

Initial scope is additive, with an explicit call order. Imported geometry stays
untouched. Source interfaces and the existing writer own ABI and declarations.
"""
from __future__ import annotations

from copy import deepcopy
from heapq import heappop, heappush
import re

from plc.device_policy import device_address
from gxworks2.project_profiles import native_project_profile
from .connectivity import build_connectivity_graph
from .declarations import _type_parts
from .models import GXWFormatError
from .object_model import _key, build_object_program


TOPOLOGY_VERSION = "source-topology-v1"


def _terminal_type(symbol, sources, plc_model):
    structure = sources.structure_value(symbol)
    if structure is not None:
        return structure["data_type"]
    label = sources.label(symbol)
    if label is not None:
        return label[1].data_type
    if symbol.upper() in {"TRUE", "FALSE"}:
        return "BOOL"
    typed = re.fullmatch(r"(BOOL|INT|DINT|WORD|DWORD|REAL|TIME|T|STRING)#.+", symbol, re.I)
    if typed:
        return "TIME" if typed[1].upper() == "T" else typed[1].upper()
    if re.fullmatch(r"[XYMS]\d+", symbol, re.I):
        device_address(symbol, plc_model)
        return "BOOL"
    # A raw D/T/C value has no unique source type; no guessed word/time cast.
    raise GXWFormatError("terminal type is not resolved from the current source: " + symbol)


def _cells(start, end):
    a, b = tuple(start), tuple(end)
    if a[0] == b[0]:
        return {(a[0], y) for y in range(min(a[1], b[1]), max(a[1], b[1]) + 1)}
    if a[1] == b[1]:
        return {(x, a[1]) for x in range(min(a[0], b[0]), max(a[0], b[0]) + 1)}
    raise GXWFormatError("existing diagonal wire is outside topology conversion scope")


def _route(start, end, blocked, *, maximum_x, maximum_y, crossings=None, budget=100000):
    """Bounded deterministic grid routing; no accidental inter-net junctions."""
    start, end = tuple(start), tuple(end)
    crossings = crossings or {}
    initial = (*start, "")
    frontier, previous, distances = [], {}, {initial: 0}
    heappush(frontier, (0, 0, initial))
    visits = 0
    while frontier:
        _, distance, state = heappop(frontier)
        point, incoming = state[:2], state[2]
        if distance != distances.get(state):
            continue
        visits += 1
        if visits > budget:
            raise GXWFormatError("topology route budget exhausted")
        if point == end:
            path = [point]
            while state != initial:
                state = previous[state]; path.append(state[:2])
            path.reverse()
            corners = [path[0]]
            for before, current, after in zip(path, path[1:], path[2:]):
                if (current[0] - before[0], current[1] - before[1]) != (after[0] - current[0], after[1] - current[1]):
                    corners.append(current)
            corners.append(end)
            return corners
        for next_point in ((point[0]+1, point[1]), (point[0], point[1]+1), (point[0], point[1]-1), (point[0]-1, point[1])):
            if not 0 <= next_point[0] <= maximum_x or not 0 <= next_point[1] <= maximum_y:
                continue
            if next_point in blocked and next_point != end:
                continue
            direction = "h" if next_point[0] != point[0] else "v"
            # Source witnesses distinguish an interior crossing from a T
            # endpoint. Cross other nets only straight through, perpendicular
            # to every segment there, with no new bend or endpoint at the cross.
            if point in crossings and (incoming != direction or direction in crossings[point]):
                continue
            if next_point in crossings and direction in crossings[next_point]:
                continue
            next_distance = distance + 1
            next_state = (*next_point, direction)
            if next_distance >= distances.get(next_state, budget + 1):
                continue
            distances[next_state] = next_distance; previous[next_state] = state
            heuristic = abs(next_point[0]-end[0]) + abs(next_point[1]-end[1])
            heappush(frontier, (next_distance + heuristic, next_distance, next_state))
    raise GXWFormatError("no isolated orthogonal route within topology scope")


def verify_topology(program, model, connections):
    """Compare independent native net membership with the requested endpoints."""
    graph = build_connectivity_graph(program)
    actual_nodes = {(n.symbol, n.bbox.left, n.bbox.top, _key(n)): n for n in program.nodes}
    port_refs = {}
    for item in model["nodes"]:
        node = actual_nodes.get((item["symbol"], item["x"], item["y"], item["template"]))
        if node is None:
            raise GXWFormatError("cold read lost a topology node")
        for index, port in enumerate(item.get("ports", [])):
            port_refs[item["id"] + "." + port["name"]] = (node.offset, index)
    expected_groups = []
    for row in connections:
        source, target = row["from"], row["to"]
        if source not in port_refs or target not in port_refs:
            raise GXWFormatError("topology endpoint unavailable after readback")
        joining = [group for group in expected_groups if source in group or target in group]
        merged = {source, target}
        for group in joining:
            merged.update(group); expected_groups.remove(group)
        expected_groups.append(merged)
    for group in expected_groups:
        actual = graph.net_for_port(*port_refs[next(iter(group))])
        members = {(p.node_offset, p.port_index) for p in actual.ports}
        expected = {port_refs[endpoint] for endpoint in group}
        if members != expected:
            raise GXWFormatError("native net contains missing or unintended topology endpoints")
    return {"status": "passed", "connections": len(connections), "nets": len(expected_groups)}


def check_binding_expectations(context, expectations):
    """Observe saved nets against externally frozen symbol/port expectations.

    This is an offline measurement; requested topology is not its oracle and the
    result does not change activation policy or establish callable behavior.
    """
    if not isinstance(expectations, list) or not expectations or len(expectations) > 512:
        raise GXWFormatError("binding expectations need a nonempty bounded list")
    model = context.object_model()
    actual = {(n.symbol, n.bbox.left, n.bbox.top, _key(n)): n for n in context.program.nodes}
    graph = build_connectivity_graph(context.program)
    rows = []
    for expected in expectations:
        if not isinstance(expected, dict) or set(expected) != {"from", "to"}:
            raise GXWFormatError("binding expectations require two named endpoints")
        endpoints, reason = [], None
        for side in ("from", "to"):
            endpoint = expected[side]
            if not isinstance(endpoint, dict) or set(endpoint) != {"symbol", "port"} or any(
                    not isinstance(endpoint[k], str) or not endpoint[k] for k in ("symbol", "port")):
                raise GXWFormatError("binding expectations require explicit symbols and port names")
            matches = [n for n in model["nodes"] if n["symbol"].casefold() == endpoint["symbol"].casefold()]
            if len(matches) != 1:
                reason = "expected_node_missing" if not matches else "expected_node_ambiguous"
                break
            node = matches[0]
            ports = [i for i, p in enumerate(node["ports"]) if p["name"] == endpoint["port"]]
            if len(ports) != 1:
                reason = "expected_port_unresolved"
                break
            native = actual[(node["symbol"], node["x"], node["y"], node["template"])]
            endpoints.append((native.offset, ports[0]))
        if reason:
            status = "failed" if reason == "expected_node_missing" else "unverified"
        else:
            net = graph.net_for_port(*endpoints[0])
            members = {(p.node_offset, p.port_index) for p in net.ports}
            status = "passed" if endpoints[1] in members else "failed"
            reason = None if status == "passed" else "expected_binding_absent"
        rows.append({"expectation": deepcopy(expected), "status": status, "reason": reason})
    return {"status": "failed" if any(r["status"] == "failed" for r in rows) else
            "unverified" if any(r["status"] == "unverified" for r in rows) else "passed",
            "checks": rows, "authority": "external_binding_expectations", "behavior": "unverified"}


def assemble_topology(context, topology, *, plc_model, base_model=None, layout_origin=None):
    """Return an existing v2 object model and receipt; never run native operations."""
    if not isinstance(topology, dict) or set(topology) - {"instances", "connections", "execution_order"}:
        raise GXWFormatError("invalid internal semantic topology")
    requested, actual = native_project_profile(plc_model), native_project_profile(context.sources.cpu)
    if requested is None or actual is None or requested.cpu != actual.cpu or not requested.program_supported:
        raise GXWFormatError("topology CPU must match the exact source menu target")
    model = deepcopy(context.object_model() if base_model is None else base_model)
    if model.get("schema_version") != 2 or model.get("program") != context.program.logical_name:
        raise GXWFormatError("topology needs the current source-bound v2 model")
    if len(model.get("blocks", [])) > 1:
        raise GXWFormatError("multi-block topology conversion requires an explicit block scope")
    instances, connections = topology.get("instances", []), topology.get("connections", [])
    if not isinstance(instances, list) or not isinstance(connections, list) or not 1 <= len(instances) <= 128 or len(connections) > 512:
        raise GXWFormatError("topology instance/connection bounds exceeded")
    existing_ids = {n["id"] for n in model["nodes"]}
    if any(not isinstance(i, dict) or set(i) - {"id", "template", "symbol", "prototype_offset"} for i in instances):
        raise GXWFormatError("semantic instances cannot supply native geometry or port definitions")
    new_ids = [i.get("id") for i in instances]
    if any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", i) for i in new_ids) or len(set(new_ids)) != len(new_ids) or existing_ids.intersection(new_ids):
        raise GXWFormatError("topology needs unique new instance identities")
    sources = context.draft_sources(model.get("declaration_edits"))
    catalog = context.catalog(declaration_edits=model.get("declaration_edits"))
    call_ids = {i["id"] for i in instances if str(i.get("template", "")).startswith(("function:", "function_block:"))}
    order = topology.get("execution_order", [])
    if not isinstance(order, list) or len(set(order)) != len(order) or set(order) != call_ids:
        raise GXWFormatError("call order must explicitly reference every new callable")
    maximum_y = max([model.get("canvas_height", 0), *[n["y"] + n["height"] for n in model["nodes"]],
                    *[max(w["start"][1], w["end"][1]) for w in model["wires"] if "start" in w]])
    origin = layout_origin or (8, maximum_y + 8)
    if len(origin) != 2 or any(type(v) is not int or v < 0 for v in origin) or origin[1] <= maximum_y:
        raise GXWFormatError("additive layout must remain below the existing source area")
    new_nodes, call_symbols = [], set()
    calls = iter([next(i for i in instances if i["id"] == identity) for identity in order])
    ordered_instances = [next(calls) if i["id"] in call_ids else i for i in instances]
    next_x = origin[0]
    for index, instance in enumerate(ordered_instances):
        candidates = [c for c in catalog if c["template"] == instance.get("template")
                      and c.get("prototype_offset") == instance.get("prototype_offset")]
        if len(candidates) != 1:
            raise GXWFormatError("topology callable template/prototype is missing or ambiguous")
        template = candidates[0]
        symbol = instance.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise GXWFormatError("topology instance symbol is required")
        if template["kind"] == "function_block":
            sources.definition(template["type_name"], "FUNCTION_BLOCK")
            if symbol.casefold() in call_symbols or any(n["symbol"].casefold() == symbol.casefold() for n in model["nodes"] if n["template"].startswith("function_block:")):
                raise GXWFormatError("stateful callable instances must have independent identity")
            call_symbols.add(symbol.casefold())
        elif template["kind"] == "function":
            sources.definition(template["symbol"], "FUNCTION")
        elif template["kind"] not in {"input", "output"}:
            raise GXWFormatError("initial topology scope includes source callables and typed terminals")
        ports = deepcopy(template["ports"])
        if template["kind"] in {"input", "output"}:
            value_type = _terminal_type(symbol, sources, plc_model)
            for port in ports:
                port.update(side="out" if template["kind"] == "input" else "in", data_type=value_type)
        if any(p.get("side") not in {"in", "out"} or not p.get("data_type") for p in ports):
            raise GXWFormatError("topology requires resolved source port directions and types")
        node = {**deepcopy(instance), "x": next_x, "y": origin[1] + index * 10,
                "width": template["width"], "height": template["height"], "ports": deepcopy(template["ports"])}
        next_x += template["width"] + 12
        new_nodes.append((node, ports))
    model["nodes"].extend(node for node, _ in new_nodes)
    endpoints = {node["id"] + "." + port["name"]: (node, port) for node, ports in new_nodes for port in ports}
    reserved = {identity: {(node["x"] + port["x"] + (step if port["side"] == "out" else -step), node["y"] + port["y"])
                           for step in range(1, 4)} for identity, (node, port) in endpoints.items()}
    writers, seen_connections = {}, set()
    obstacles = set()
    for node in model["nodes"]:
        if node["width"] > 1024 or node["height"] > 1024:
            raise GXWFormatError("topology obstacle exceeds routing scope")
        obstacles.update((x, y) for x in range(node["x"], node["x"]+node["width"]+1)
                         for y in range(node["y"], node["y"]+node["height"]+1))
    for wire in model["wires"]:
        if "start" not in wire:
            raise GXWFormatError("topology base needs a saved coordinate wire projection")
        obstacles.update(_cells(wire["start"], wire["end"]))
    routed_segments, routed_corners = [], []
    max_x = max(n["x"] + n["width"] for n in model["nodes"]) + 16
    max_y = max(n["y"] + n["height"] for n in model["nodes"]) + 16
    if max_x > 4096 or max_y > 4096:
        raise GXWFormatError("topology canvas is outside the initial routing scope")
    for row in connections:
        if not isinstance(row, dict) or set(row) != {"from", "to"}:
            raise GXWFormatError("semantic connections need named endpoints only")
        source, target = row["from"], row["to"]
        if source not in endpoints or target not in endpoints:
            raise GXWFormatError("initial topology endpoints must belong to the explicit additive scope")
        if (source, target) in seen_connections or target in writers:
            raise GXWFormatError("duplicate connection or multiple port writers")
        a, ap = endpoints[source]; b, bp = endpoints[target]
        if ap["side"] != "out" or bp["side"] != "in":
            raise GXWFormatError("topology port direction mismatch")
        if _type_parts(ap["data_type"]) != _type_parts(bp["data_type"]) or ap["data_type"].upper().startswith("ANY"):
            raise GXWFormatError("topology port type is incompatible or unresolved")
        start = (a["x"] + ap["x"], a["y"] + ap["y"])
        end = (b["x"] + bp["x"], b["y"] + bp["y"])
        blocked = (obstacles | {p for p, owner in routed_corners if owner != source}
                   | {point for identity, cells in reserved.items() if identity not in {source, target} for point in cells})
        blocked.discard(start); blocked.discard(end)
        crossings = {}
        for left, right, owner in routed_segments:
            if owner != source:
                direction = "h" if left[1] == right[1] else "v"
                for point in _cells(left, right):
                    crossings.setdefault(point, set()).add(direction)
        points = _route(start, end, blocked, maximum_x=max_x, maximum_y=max_y, crossings=crossings)
        for left, right in zip(points, points[1:]):
            routed_segments.append((left, right, source))
        routed_corners.extend((p, source) for p in points)
        model["wires"].append({"from": source, "to": target, "via": [list(p) for p in points[1:-1]]})
        writers[target] = source; seen_connections.add((source, target))
    model["canvas_height"] = max(model["canvas_height"], max_y)
    if model.get("blocks"):
        model["blocks"][0]["canvas_height"] = model["canvas_height"]
    program = build_object_program(context.program, model, sources=sources)
    # Use ports only as source projections. No caller-provided type or geometry
    # is passed to the existing writer as an ABI edit.
    binding = verify_topology(program, model, connections)
    receipt = {"version": TOPOLOGY_VERSION, "cpu": context.sources.cpu, "requested_cpu": plc_model,
               "program": context.program.logical_name, "binding": binding, "execution_order": deepcopy(order),
               "execution_order_status": "unverified_native_schedule", "native_compile": "not_executed",
               "behavior": "unverified", "connections": deepcopy(connections),
               "scope": "additive_source_bound_objects", "preserved_node_ids": sorted(existing_ids)}
    return model, receipt

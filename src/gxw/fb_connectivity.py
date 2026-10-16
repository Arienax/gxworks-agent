"""FB endpoint relationships derived from the existing geometric net model.

Offsets identify records only in the current POU. They are not serialized
cross-object references. Unknown formals stay unnamed; no ABI is invented here.
"""
from __future__ import annotations

from dataclasses import asdict

from .connectivity import build_connectivity_graph
from .models import StructuredProgram
from .semantic import SemanticPortRole, build_semantic_model


def fb_connectivity_model(program: StructuredProgram) -> dict:
    graph = build_connectivity_graph(program)
    semantic = build_semantic_model(program, connectivity=graph)
    groups = {}
    inputs = {SemanticPortRole.DATA_IN, SemanticPortRole.ENABLE_IN}
    outputs = {SemanticPortRole.DATA_OUT, SemanticPortRole.ENABLE_OUT}
    for block in semantic.function_blocks:
        for port in block.ports:
            endpoint = {"node_offset": block.node_offset, "instance": block.instance_name,
                        "type": block.type_name, "port_index": port.port_index,
                        "formal": port.formal_name, "role": port.role.value,
                        "port_kind_code": port.port_kind_code, "point": asdict(port.point)}
            side = "sources" if port.role in outputs else "sinks" if port.role in inputs else "unknown"
            net = groups.setdefault(port.net_index, {"sources": [], "sinks": [], "unknown": []})
            net[side].append(endpoint)
    nets = []
    for index, endpoints in sorted(groups.items()):
        if len(endpoints["sources"]) + len(endpoints["sinks"]) + len(endpoints["unknown"]) < 2:
            continue
        net = graph.nets[index]
        points = {tuple(e["point"].values()) for side in endpoints.values() for e in side}
        nets.append({"net_index": index, **endpoints,
                     "wire_offsets": list(net.wire_offsets),
                     "connection": "port_overlap" if len(points) == 1 else "wire_network",
                     "multiple_fb_drivers": len(endpoints["sources"]) > 1,
                     "formal_mapping": "known" if all(e["formal"] is not None for side in endpoints.values() for e in side) else "unknown"})
    return {"schema_version": 1, "reference_space": "current Program.pou record offsets",
            "wire_scope": "all wires in the conductive net, not necessarily a unique path",
            "nets": nets}

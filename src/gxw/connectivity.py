from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Tuple

from .models import GXWFormatError, NodeKind, Point, StructuredProgram, StructuredWire


@dataclass(frozen=True)
class PortRef:
    node_offset: int
    port_index: int
    node_kind: NodeKind
    node_symbol: str
    port_kind_code: int
    point: Point


@dataclass(frozen=True)
class ConnectivityNet:
    index: int
    ports: Tuple[PortRef, ...]
    wire_offsets: Tuple[int, ...]
    block_index: int = 0


@dataclass(frozen=True)
class ConnectivityGraph:
    logical_name: str
    nets: Tuple[ConnectivityNet, ...]

    def net_for_port(self, node_offset: int, port_index: int) -> ConnectivityNet:
        for net in self.nets:
            if any(
                port.node_offset == node_offset and port.port_index == port_index
                for port in net.ports
            ):
                return net
        raise KeyError(f"port not present in connectivity graph: 0x{node_offset:X}:{port_index}")

    def net_for_wire(self, wire_offset: int) -> ConnectivityNet:
        for net in self.nets:
            if wire_offset in net.wire_offsets:
                return net
        raise KeyError(f"wire not present in connectivity graph: 0x{wire_offset:X}")

    def ports_connected(
        self,
        left_node_offset: int,
        left_port_index: int,
        right_node_offset: int,
        right_port_index: int,
    ) -> bool:
        return self.net_for_port(left_node_offset, left_port_index).index == self.net_for_port(
            right_node_offset, right_port_index
        ).index


def _point_on_wire(point: Point, wire: StructuredWire) -> bool:
    if wire.start.x == wire.end.x:
        return point.x == wire.start.x and min(wire.start.y, wire.end.y) <= point.y <= max(
            wire.start.y, wire.end.y
        )
    if wire.start.y == wire.end.y:
        return point.y == wire.start.y and min(wire.start.x, wire.end.x) <= point.x <= max(
            wire.start.x, wire.end.x
        )
    raise GXWFormatError(
        "unsupported diagonal Structured Ladder wire at "
        f"0x{wire.offset:X}: {wire.start} -> {wire.end}"
    )


def _wires_connect(left: StructuredWire, right: StructuredWire) -> bool:
    # Controlled samples 56-58 establish the important distinction:
    # endpoint-on-segment is a junction, while interior/interior crossing is not.
    return (
        _point_on_wire(left.start, right)
        or _point_on_wire(left.end, right)
        or _point_on_wire(right.start, left)
        or _point_on_wire(right.end, left)
    )


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[tuple, tuple] = {}

    def add(self, item: tuple) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: tuple) -> tuple:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: tuple, right: tuple) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def build_connectivity_graph(program: StructuredProgram) -> ConnectivityGraph:
    """Reconstruct independent nets in each native block's local canvas."""
    nets = []
    for block_index, view in enumerate(program.block_views()):
        for net in _build_block_connectivity(view).nets:
            nets.append(replace(net, index=len(nets), block_index=block_index))
    return ConnectivityGraph(program.logical_name, tuple(nets))


def _build_block_connectivity(program: StructuredProgram) -> ConnectivityGraph:
    """Reconstruct observed editor-grid electrical nets from nodes and wires.

    Verified geometry rules from controlled samples 53-58:
    - coincident port points connect without a wire record;
    - a wire endpoint lying on another wire connects the two wires;
    - two wire interiors crossing does not connect them;
    - explicit wires connect ports whose points lie on the wire path.

    The function deliberately rejects diagonal wires because they have not been
    observed in the controlled Structured Ladder sample set.
    """

    uf = _UnionFind()
    port_refs: Dict[tuple, PortRef] = {}
    port_keys_by_point: Dict[Point, List[tuple]] = {}
    wire_by_key: Dict[tuple, StructuredWire] = {}

    for node in program.nodes:
        for port_index, port in enumerate(node.ports):
            key = ("port", node.offset, port_index)
            point = port.absolute_point(node.bbox)
            ref = PortRef(
                node_offset=node.offset,
                port_index=port_index,
                node_kind=node.kind,
                node_symbol=node.symbol,
                port_kind_code=port.port_kind_code,
                point=point,
            )
            uf.add(key)
            port_refs[key] = ref
            port_keys_by_point.setdefault(point, []).append(key)

    for wire in program.wires:
        # Validate the currently observed orthogonal geometry even for wire-only nets.
        _point_on_wire(wire.start, wire)
        key = ("wire", wire.offset)
        uf.add(key)
        wire_by_key[key] = wire

    # Direct node-to-node attachment is represented by coincident port points.
    for keys in port_keys_by_point.values():
        for key in keys[1:]:
            uf.union(keys[0], key)

    # A port is a point connection; if it lies on an explicit wire path, it joins
    # that conductor. Controlled samples normally place it at a wire endpoint.
    for port_key, port_ref in port_refs.items():
        for wire_key, wire in wire_by_key.items():
            if _point_on_wire(port_ref.point, wire):
                uf.union(port_key, wire_key)

    wire_items = list(wire_by_key.items())
    for index, (left_key, left_wire) in enumerate(wire_items):
        for right_key, right_wire in wire_items[index + 1 :]:
            if _wires_connect(left_wire, right_wire):
                uf.union(left_key, right_key)

    grouped_ports: Dict[tuple, List[PortRef]] = {}
    grouped_wires: Dict[tuple, List[int]] = {}
    for key, ref in port_refs.items():
        grouped_ports.setdefault(uf.find(key), []).append(ref)
    for key, wire in wire_by_key.items():
        grouped_wires.setdefault(uf.find(key), []).append(wire.offset)

    roots = set(grouped_ports) | set(grouped_wires)

    def component_sort_key(root: tuple) -> tuple:
        ports = grouped_ports.get(root, [])
        wires = grouped_wires.get(root, [])
        candidates = [
            (0, port.node_offset, port.port_index) for port in ports
        ] + [(1, offset, 0) for offset in wires]
        return min(candidates)

    nets: List[ConnectivityNet] = []
    for net_index, root in enumerate(sorted(roots, key=component_sort_key)):
        ports = tuple(
            sorted(
                grouped_ports.get(root, []),
                key=lambda port: (port.node_offset, port.port_index),
            )
        )
        wire_offsets = tuple(sorted(grouped_wires.get(root, [])))
        nets.append(
            ConnectivityNet(
                index=net_index,
                ports=ports,
                wire_offsets=wire_offsets,
            )
        )

    return ConnectivityGraph(logical_name=program.logical_name, nets=tuple(nets))

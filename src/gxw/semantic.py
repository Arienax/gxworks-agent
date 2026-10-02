from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

from .connectivity import ConnectivityGraph, ConnectivityNet, build_connectivity_graph
from .models import (
    COIL_NODE_KINDS, CONTACT_NODE_KINDS, NodeKind, Point, StructuredNode, StructuredProgram,
)


class SemanticPortRole(str, Enum):
    """Observed semantic role of a Structured Ladder node port."""

    ENABLE_IN = "enable_in"
    DATA_IN = "data_in"
    ENABLE_OUT = "enable_out"
    DATA_OUT = "data_out"
    EXECUTION_IN = "execution_in"
    EXECUTION_OUT = "execution_out"
    UNKNOWN = "unknown"


class ContactPolarity(str, Enum):
    NORMALLY_OPEN = "normally_open"
    NORMALLY_CLOSED = "normally_closed"
    UNKNOWN = "unknown"


class LadderEdge(str, Enum):
    NONE = "none"
    RISING = "rising"
    FALLING = "falling"
    UNKNOWN = "unknown"


class CoilRole(str, Enum):
    NORMAL = "normal"
    NEGATED = "negated"
    SET = "set"
    RESET = "reset"
    UNKNOWN = "unknown"


# Native source -> SIC -> independent PCode observations are retained in
# tests/fixtures/gxw_ladder_primitives.json. Node kind and port flags jointly
# determine behavior. Admit only observed combinations, not every bit pattern.
_OBSERVED_LADDER_INPUTS = {
    NodeKind.CONTACT: frozenset({3, 11, 19, 27, 35, 43}),
    NodeKind.CONTACT_NC: frozenset({3, 11, 19, 35}),
    NodeKind.CONTACT_RISING: frozenset({3}),
    NodeKind.CONTACT_FALLING: frozenset({3}),
    NodeKind.COIL: frozenset({3, 11, 19, 35, 67, 131}),
    NodeKind.COIL_NEGATED: frozenset({3, 11}),
    NodeKind.COIL_SET: frozenset({3, 11, 19}),
    NodeKind.COIL_RESET: frozenset({3, 11, 35}),
}


def _ladder_behavior(kind: NodeKind, input_code: int):
    """Return inversion, edge and coil operation for an observed input only.

    Inversion applies *after* edge detection. An inverted rising edge is not a
    falling edge. The native mode selector prioritizes edge, inversion, SET,
    then RST; e.g. SET kind + port 11 is an inverted ordinary assignment.
    """
    if input_code not in _OBSERVED_LADDER_INPUTS.get(kind, ()):
        return None
    flags = input_code
    if kind in {NodeKind.CONTACT, NodeKind.COIL}:
        flags &= ~8
    flags |= {
        NodeKind.CONTACT_NC: 8, NodeKind.COIL_NEGATED: 8,
        NodeKind.COIL_SET: 64, NodeKind.COIL_RESET: 128,
        NodeKind.CONTACT_RISING: 16, NodeKind.CONTACT_FALLING: 32,
    }.get(kind, 0)
    edge = LadderEdge.RISING if flags & 16 else LadderEdge.FALLING if flags & 32 else LadderEdge.NONE
    negated = bool(flags & 8)
    role = CoilRole.NEGATED if negated else CoilRole.NORMAL
    if edge == LadderEdge.NONE and not negated:
        role = CoilRole.SET if flags & 64 else CoilRole.RESET if flags & 128 else CoilRole.NORMAL
    return negated, edge, role


class TerminalRole(str, Enum):
    SOURCE = "source"
    SINK = "sink"


class SemanticIssueSeverity(str, Enum):
    WARNING = "warning"


@dataclass(frozen=True)
class FunctionFamilySpec:
    base_name: str
    extensible_inputs: bool = False
    expected_data_inputs: Optional[int] = None


class FunctionBlockCategory(str, Enum):
    TIMER = "timer"
    COUNTER = "counter"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FunctionBlockPortSpec:
    formal_name: str
    role: SemanticPortRole
    local_y: int
    port_kind_code: Optional[int] = None


@dataclass(frozen=True)
class FunctionBlockSpec:
    type_name: str
    category: FunctionBlockCategory
    ports: Tuple[FunctionBlockPortSpec, ...]


# FB descriptors do not distinguish EN/IN or ENO/data OUT by code. Names and
# roles require the type-specific layouts verified by samples 72-77.
DEFAULT_FUNCTION_BLOCK_REGISTRY: Mapping[str, FunctionBlockSpec] = MappingProxyType({
    "TON": FunctionBlockSpec("TON", FunctionBlockCategory.TIMER, (
        FunctionBlockPortSpec("IN", SemanticPortRole.DATA_IN, 2),
        FunctionBlockPortSpec("PT", SemanticPortRole.DATA_IN, 3),
        FunctionBlockPortSpec("Q", SemanticPortRole.DATA_OUT, 2),
        FunctionBlockPortSpec("ET", SemanticPortRole.DATA_OUT, 3),
    )),
    "TON_E": FunctionBlockSpec("TON_E", FunctionBlockCategory.TIMER, (
        FunctionBlockPortSpec("EN", SemanticPortRole.ENABLE_IN, 2),
        FunctionBlockPortSpec("IN", SemanticPortRole.DATA_IN, 3),
        FunctionBlockPortSpec("PT", SemanticPortRole.DATA_IN, 4),
        FunctionBlockPortSpec("ENO", SemanticPortRole.ENABLE_OUT, 2),
        FunctionBlockPortSpec("Q", SemanticPortRole.DATA_OUT, 3),
        FunctionBlockPortSpec("ET", SemanticPortRole.DATA_OUT, 4),
    )),
    "CTU": FunctionBlockSpec("CTU", FunctionBlockCategory.COUNTER, (
        FunctionBlockPortSpec("CU", SemanticPortRole.DATA_IN, 2),
        FunctionBlockPortSpec("RESET", SemanticPortRole.DATA_IN, 3),
        FunctionBlockPortSpec("PV", SemanticPortRole.DATA_IN, 4),
        FunctionBlockPortSpec("Q", SemanticPortRole.DATA_OUT, 2),
        FunctionBlockPortSpec("CV", SemanticPortRole.DATA_OUT, 3),
    )),
    "CTU_E": FunctionBlockSpec("CTU_E", FunctionBlockCategory.COUNTER, (
        FunctionBlockPortSpec("EN", SemanticPortRole.ENABLE_IN, 2),
        FunctionBlockPortSpec("CU", SemanticPortRole.DATA_IN, 3),
        FunctionBlockPortSpec("RESET", SemanticPortRole.DATA_IN, 4),
        FunctionBlockPortSpec("PV", SemanticPortRole.DATA_IN, 5),
        FunctionBlockPortSpec("ENO", SemanticPortRole.ENABLE_OUT, 2),
        FunctionBlockPortSpec("Q", SemanticPortRole.DATA_OUT, 3),
        FunctionBlockPortSpec("CV", SemanticPortRole.DATA_OUT, 4),
    )),
})


# This registry records only function-family facts established by controlled
# samples. Unknown symbols are still parsed semantically from their geometry,
# but no family-specific naming convention is inferred for them.
DEFAULT_FUNCTION_FAMILY_REGISTRY: Mapping[str, FunctionFamilySpec] = MappingProxyType({
    "ADD_E": FunctionFamilySpec("ADD_E", extensible_inputs=True),
    "AND_E": FunctionFamilySpec("AND_E", extensible_inputs=True),
    "MOV": FunctionFamilySpec("MOV", expected_data_inputs=1),
    "CMP": FunctionFamilySpec("CMP", expected_data_inputs=2),
    "DIV_E": FunctionFamilySpec("DIV_E", expected_data_inputs=2),
    "ABS": FunctionFamilySpec("ABS", expected_data_inputs=1),
    "ABS_E": FunctionFamilySpec("ABS_E", expected_data_inputs=1),
})

_ARITY_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<arity>[1-9]\d*)$")


@dataclass(frozen=True)
class SemanticIssue:
    code: str
    message: str
    severity: SemanticIssueSeverity = SemanticIssueSeverity.WARNING
    node_offset: Optional[int] = None
    port_index: Optional[int] = None


@dataclass(frozen=True)
class SemanticTerminal:
    node_offset: int
    symbol: str
    role: TerminalRole
    port_kind_code: int
    point: Point
    net_index: int
    unresolved: bool


@dataclass(frozen=True)
class SemanticFunctionPort:
    function_offset: int
    port_index: int
    role: SemanticPortRole
    port_kind_code: int
    point: Point
    net_index: int
    terminal_node_offsets: Tuple[int, ...]
    terminal_symbols: Tuple[str, ...]


@dataclass(frozen=True)
class SemanticFunction:
    node_offset: int
    serialized_symbol: str
    base_name: str
    family_known: bool
    extensible_inputs: bool
    declared_arity: Optional[int]
    data_input_count: int
    data_output_count: int
    has_enable_interface: bool
    ports: Tuple[SemanticFunctionPort, ...]

    def ports_with_role(self, role: SemanticPortRole) -> Tuple[SemanticFunctionPort, ...]:
        return tuple(port for port in self.ports if port.role == role)

    @property
    def enable_in(self) -> Optional[SemanticFunctionPort]:
        ports = self.ports_with_role(SemanticPortRole.ENABLE_IN)
        return ports[0] if len(ports) == 1 else None

    @property
    def enable_out(self) -> Optional[SemanticFunctionPort]:
        ports = self.ports_with_role(SemanticPortRole.ENABLE_OUT)
        return ports[0] if len(ports) == 1 else None

    @property
    def data_inputs(self) -> Tuple[SemanticFunctionPort, ...]:
        return self.ports_with_role(SemanticPortRole.DATA_IN)

    @property
    def data_outputs(self) -> Tuple[SemanticFunctionPort, ...]:
        return self.ports_with_role(SemanticPortRole.DATA_OUT)


@dataclass(frozen=True)
class SemanticFunctionBlockPort:
    block_offset: int
    port_index: int
    formal_name: Optional[str]
    role: SemanticPortRole
    port_kind_code: int
    point: Point
    net_index: int
    terminal_node_offsets: Tuple[int, ...]
    terminal_symbols: Tuple[str, ...]


@dataclass(frozen=True)
class SemanticFunctionBlock:
    node_offset: int
    instance_name: str
    type_name: Optional[str]
    type_known: bool
    category: FunctionBlockCategory
    ports: Tuple[SemanticFunctionBlockPort, ...]

    def port_named(self, formal_name: str) -> Optional[SemanticFunctionBlockPort]:
        ports = tuple(port for port in self.ports if port.formal_name == formal_name)
        return ports[0] if len(ports) == 1 else None

    def ports_with_role(self, role: SemanticPortRole) -> Tuple[SemanticFunctionBlockPort, ...]:
        return tuple(port for port in self.ports if port.role == role)

    @property
    def enable_in(self) -> Optional[SemanticFunctionBlockPort]:
        ports = self.ports_with_role(SemanticPortRole.ENABLE_IN)
        return ports[0] if len(ports) == 1 else None

    @property
    def enable_out(self) -> Optional[SemanticFunctionBlockPort]:
        ports = self.ports_with_role(SemanticPortRole.ENABLE_OUT)
        return ports[0] if len(ports) == 1 else None

    @property
    def has_enable_interface(self) -> bool:
        return self.enable_in is not None and self.enable_out is not None

    @property
    def data_inputs(self) -> Tuple[SemanticFunctionBlockPort, ...]:
        return self.ports_with_role(SemanticPortRole.DATA_IN)

    @property
    def data_outputs(self) -> Tuple[SemanticFunctionBlockPort, ...]:
        return self.ports_with_role(SemanticPortRole.DATA_OUT)


@dataclass(frozen=True)
class SemanticLadderPort:
    node_offset: int
    port_index: int
    role: SemanticPortRole
    port_kind_code: int
    point: Point
    net_index: int
    terminal_node_offsets: Tuple[int, ...]
    terminal_symbols: Tuple[str, ...]


def _unique_ladder_port(
    ports: Tuple[SemanticLadderPort, ...], role: SemanticPortRole
) -> Optional[SemanticLadderPort]:
    matches = tuple(port for port in ports if port.role == role)
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class SemanticContact:
    node_offset: int
    symbol: str
    polarity: ContactPolarity
    ports: Tuple[SemanticLadderPort, ...]
    # Polarity inverts the detected edge, when an edge modifier is present.
    edge: LadderEdge = LadderEdge.NONE

    @property
    def execution_in(self) -> Optional[SemanticLadderPort]:
        return _unique_ladder_port(self.ports, SemanticPortRole.EXECUTION_IN)

    @property
    def execution_out(self) -> Optional[SemanticLadderPort]:
        return _unique_ladder_port(self.ports, SemanticPortRole.EXECUTION_OUT)


@dataclass(frozen=True)
class SemanticCoil:
    node_offset: int
    symbol: str
    role: CoilRole
    ports: Tuple[SemanticLadderPort, ...]
    edge: LadderEdge = LadderEdge.NONE

    @property
    def execution_in(self) -> Optional[SemanticLadderPort]:
        return _unique_ladder_port(self.ports, SemanticPortRole.EXECUTION_IN)


@dataclass(frozen=True)
class UnmodeledNodeRef:
    node_offset: int
    kind: NodeKind
    symbol: str


@dataclass(frozen=True)
class StructuredSemanticModel:
    logical_name: str
    functions: Tuple[SemanticFunction, ...]
    terminals: Tuple[SemanticTerminal, ...]
    unmodeled_nodes: Tuple[UnmodeledNodeRef, ...]
    issues: Tuple[SemanticIssue, ...]
    contacts: Tuple[SemanticContact, ...] = ()
    coils: Tuple[SemanticCoil, ...] = ()
    function_blocks: Tuple[SemanticFunctionBlock, ...] = ()

    def functions_named(self, base_name: str) -> Tuple[SemanticFunction, ...]:
        return tuple(function for function in self.functions if function.base_name == base_name)

    @property
    def timers(self) -> Tuple[SemanticFunctionBlock, ...]:
        return tuple(block for block in self.function_blocks if block.category == FunctionBlockCategory.TIMER)

    @property
    def counters(self) -> Tuple[SemanticFunctionBlock, ...]:
        return tuple(block for block in self.function_blocks if block.category == FunctionBlockCategory.COUNTER)


@dataclass(frozen=True)
class _ResolvedFunctionIdentity:
    base_name: str
    family_known: bool
    extensible_inputs: bool
    declared_arity: Optional[int]
    expected_data_inputs: Optional[int]


def _resolve_function_identity(
    serialized_symbol: str,
    registry: Mapping[str, FunctionFamilySpec],
) -> _ResolvedFunctionIdentity:
    direct = registry.get(serialized_symbol)
    if direct is not None:
        return _ResolvedFunctionIdentity(
            base_name=direct.base_name,
            family_known=True,
            extensible_inputs=direct.extensible_inputs,
            declared_arity=None,
            expected_data_inputs=direct.expected_data_inputs,
        )

    match = _ARITY_SUFFIX_RE.fullmatch(serialized_symbol)
    if match:
        base_name = match.group("base")
        spec = registry.get(base_name)
        if spec is not None and spec.extensible_inputs:
            return _ResolvedFunctionIdentity(
                base_name=spec.base_name,
                family_known=True,
                extensible_inputs=True,
                declared_arity=int(match.group("arity")),
                expected_data_inputs=None,
            )

    # Do not strip an arbitrary '-N' suffix. Samples 67-69 show that arity
    # suffixes are family-specific rather than a universal Function rule.
    return _ResolvedFunctionIdentity(
        base_name=serialized_symbol,
        family_known=False,
        extensible_inputs=False,
        declared_arity=None,
        expected_data_inputs=None,
    )


def _terminal_semantics(
    program: StructuredProgram,
    graph: ConnectivityGraph,
) -> Tuple[Tuple[SemanticTerminal, ...], Tuple[SemanticIssue, ...]]:
    terminals = []
    issues = []
    for node in program.nodes:
        if node.kind not in {NodeKind.INPUT, NodeKind.OUTPUT}:
            continue

        role = TerminalRole.SOURCE if node.kind == NodeKind.INPUT else TerminalRole.SINK
        expected_code = 2 if role == TerminalRole.SOURCE else 3
        if len(node.ports) != 1:
            issues.append(
                SemanticIssue(
                    code="terminal_port_count",
                    message=(
                        f"terminal {node.symbol!r} has {len(node.ports)} ports; "
                        "controlled samples currently expect exactly one"
                    ),
                    node_offset=node.offset,
                )
            )
            if not node.ports:
                continue

        port = node.ports[0]
        if port.port_kind_code != expected_code:
            issues.append(
                SemanticIssue(
                    code="terminal_port_kind",
                    message=(
                        f"terminal {node.symbol!r} role {role.value} has port_kind_code "
                        f"{port.port_kind_code}; observed value is {expected_code}"
                    ),
                    node_offset=node.offset,
                    port_index=0,
                )
            )

        net = graph.net_for_port(node.offset, 0)
        unresolved = node.symbol == "?"
        if unresolved:
            issues.append(
                SemanticIssue(
                    code="unresolved_terminal",
                    message="GX Works2 serialized an unresolved function input as '?'",
                    node_offset=node.offset,
                    port_index=0,
                )
            )
        terminals.append(
            SemanticTerminal(
                node_offset=node.offset,
                symbol=node.symbol,
                role=role,
                port_kind_code=port.port_kind_code,
                point=node.port_point(0),
                net_index=net.index,
                unresolved=unresolved,
            )
        )

    return tuple(terminals), tuple(issues)


def _infer_function_port_roles(
    node: StructuredNode,
) -> Tuple[Tuple[SemanticPortRole, ...], Tuple[SemanticIssue, ...]]:
    roles = [SemanticPortRole.UNKNOWN for _ in node.ports]
    issues = []

    incoming_candidates = []
    enable_out_candidates = []
    for index, port in enumerate(node.ports):
        point = node.port_point(index)
        if port.port_kind_code == 3 and point.x == node.bbox.left:
            roles[index] = SemanticPortRole.DATA_IN
            incoming_candidates.append(index)
        elif port.port_kind_code == 2 and point.x == node.bbox.right:
            roles[index] = SemanticPortRole.DATA_OUT
        elif port.port_kind_code == 0 and point.x == node.bbox.right:
            roles[index] = SemanticPortRole.ENABLE_OUT
            enable_out_candidates.append(index)
        else:
            issues.append(
                SemanticIssue(
                    code="unknown_function_port",
                    message=(
                        f"function {node.symbol!r} port {index} has unsupported observed geometry "
                        f"(code={port.port_kind_code}, point={point})"
                    ),
                    node_offset=node.offset,
                    port_index=index,
                )
            )

    if len(enable_out_candidates) == 1:
        enable_out_index = enable_out_candidates[0]
        enable_y = node.port_point(enable_out_index).y
        matching_inputs = [
            index for index in incoming_candidates if node.port_point(index).y == enable_y
        ]
        if len(matching_inputs) == 1:
            roles[matching_inputs[0]] = SemanticPortRole.ENABLE_IN
        else:
            issues.append(
                SemanticIssue(
                    code="enable_pair_ambiguous",
                    message=(
                        f"function {node.symbol!r} has one code-0 output but "
                        f"{len(matching_inputs)} aligned code-3 input ports"
                    ),
                    node_offset=node.offset,
                    port_index=enable_out_index,
                )
            )
    elif len(enable_out_candidates) > 1:
        issues.append(
            SemanticIssue(
                code="multiple_enable_outputs",
                message=(
                    f"function {node.symbol!r} has {len(enable_out_candidates)} code-0 right ports; "
                    "this ABI has not been observed"
                ),
                node_offset=node.offset,
            )
        )

    return tuple(roles), tuple(issues)


def _net_terminals(
    net: ConnectivityNet,
    terminal_by_offset: Mapping[int, SemanticTerminal],
) -> Tuple[SemanticTerminal, ...]:
    return tuple(
        sorted(
            (
                terminal_by_offset[ref.node_offset]
                for ref in net.ports
                if ref.node_offset in terminal_by_offset
            ),
            key=lambda terminal: terminal.node_offset,
        )
    )


def _matches_function_block_port(
    node: StructuredNode, port_index: int, spec: FunctionBlockPortSpec,
) -> bool:
    port = node.ports[port_index]
    point = node.port_point(port_index)
    if not (
        node.bbox.left < node.bbox.right
        and node.bbox.top < point.y < node.bbox.bottom
        and port.local_y == spec.local_y
    ):
        return False
    if spec.role in {SemanticPortRole.ENABLE_IN, SemanticPortRole.DATA_IN}:
        return port.port_kind_code == (1 if spec.port_kind_code is None else spec.port_kind_code) and point.x == node.bbox.left
    if spec.role in {SemanticPortRole.ENABLE_OUT, SemanticPortRole.DATA_OUT}:
        return port.port_kind_code == (0 if spec.port_kind_code is None else spec.port_kind_code) and point.x == node.bbox.right
    return False


def _resolve_function_block_ports(
    node: StructuredNode, spec: Optional[FunctionBlockSpec],
) -> Tuple[Tuple[Optional[FunctionBlockPortSpec], ...], Tuple[SemanticIssue, ...]]:
    unknown = tuple(None for _ in node.ports)
    if spec is None:
        return unknown, (SemanticIssue(
            code="unknown_function_block_type",
            message=(
                f"FB instance {node.symbol!r} has unregistered type {node.type_name!r}; "
                "formal port names and roles are not inferred"
            ),
            node_offset=node.offset,
        ),)
    if len(node.ports) != len(spec.ports):
        return unknown, (SemanticIssue(
            code="function_block_port_count",
            message=(
                f"FB instance {node.symbol!r} ({node.type_name}) has {len(node.ports)} "
                f"ports; the observed type interface has {len(spec.ports)}"
            ),
            node_offset=node.offset,
        ),)

    candidates = [
        [index for index in range(len(node.ports)) if _matches_function_block_port(node, index, port)]
        for port in spec.ports
    ]
    if (
        any(len(indices) != 1 for indices in candidates)
        or len({indices[0] for indices in candidates}) != len(node.ports)
    ):
        return unknown, (SemanticIssue(
            code="function_block_port_layout",
            message=(
                f"FB instance {node.symbol!r} ({node.type_name}) does not match the "
                "observed type-specific port codes and geometry"
            ),
            node_offset=node.offset,
        ),)

    by_index = {indices[0]: port for indices, port in zip(candidates, spec.ports)}
    return tuple(by_index[index] for index in range(len(node.ports))), ()


def _function_block_semantics(
    program: StructuredProgram,
    graph: ConnectivityGraph,
    terminals: Tuple[SemanticTerminal, ...],
    registry: Mapping[str, FunctionBlockSpec],
    instance_specs: Optional[Mapping[int, Optional[FunctionBlockSpec]]] = None,
) -> Tuple[Tuple[SemanticFunctionBlock, ...], Tuple[SemanticIssue, ...]]:
    terminal_by_offset = {terminal.node_offset: terminal for terminal in terminals}
    blocks = []
    issues = []
    for node in program.nodes:
        if node.kind != NodeKind.FUNCTION_BLOCK:
            continue
        spec = registry.get(node.type_name) if node.type_name is not None else None
        if instance_specs is not None:
            spec = instance_specs.get(node.offset)
        port_specs, port_issues = _resolve_function_block_ports(node, spec)
        issues.extend(port_issues)
        ports = []
        for index, (port, port_spec) in enumerate(zip(node.ports, port_specs)):
            net = graph.net_for_port(node.offset, index)
            terminal_refs = _net_terminals(net, terminal_by_offset)
            ports.append(SemanticFunctionBlockPort(
                block_offset=node.offset,
                port_index=index,
                formal_name=port_spec.formal_name if port_spec else None,
                role=port_spec.role if port_spec else SemanticPortRole.UNKNOWN,
                port_kind_code=port.port_kind_code,
                point=node.port_point(index),
                net_index=net.index,
                terminal_node_offsets=tuple(terminal.node_offset for terminal in terminal_refs),
                terminal_symbols=tuple(terminal.symbol for terminal in terminal_refs),
            ))
        blocks.append(SemanticFunctionBlock(
            node_offset=node.offset,
            instance_name=node.symbol,
            type_name=node.type_name,
            type_known=spec is not None,
            category=spec.category if spec else FunctionBlockCategory.UNKNOWN,
            ports=tuple(ports),
        ))
    return tuple(blocks), tuple(issues)


def _infer_ladder_port_roles(
    node: StructuredNode,
) -> Tuple[Tuple[SemanticPortRole, ...], Tuple[SemanticIssue, ...]]:
    roles = [SemanticPortRole.UNKNOWN for _ in node.ports]
    if len(node.ports) != 2:
        return tuple(roles), (
            SemanticIssue(
                code="ladder_port_count",
                message=(
                    f"{node.kind.value} {node.symbol!r} has {len(node.ports)} ports; "
                    "controlled samples currently expect exactly two"
                ),
                node_offset=node.offset,
            ),
        )

    points = tuple(node.port_point(index) for index in range(len(node.ports)))
    left = [index for index, point in enumerate(points) if point.x == node.bbox.left]
    right = [index for index, point in enumerate(points) if point.x == node.bbox.right]
    if (
        node.bbox.left >= node.bbox.right
        or len(left) != 1
        or len(right) != 1
        or points[left[0]].y != points[right[0]].y
        or not node.bbox.top < points[left[0]].y < node.bbox.bottom
    ):
        return tuple(roles), (
            SemanticIssue(
                code="ladder_port_layout",
                message=(
                    f"{node.kind.value} {node.symbol!r} has unsupported port geometry; "
                    "expected aligned left/right ports inside the bbox height"
                ),
                node_offset=node.offset,
            ),
        )

    # Native graph conversion uses serialized port 0 as the input, including
    # kind-derived flag changes. Geometric coincidence does not make a swapped
    # port array semantically equivalent; native controls reject that form.
    if left[0] != 0 or right[0] != 1:
        return tuple(roles), (
            SemanticIssue(
                code="ladder_port_order",
                message=(f"{node.kind.value} {node.symbol!r} has unsupported serialized "
                         "port order; native controls require input before output"),
                node_offset=node.offset,
            ),
        )

    issues = []
    for index, port in enumerate(node.ports):
        if index == left[0] and port.port_kind_code in _OBSERVED_LADDER_INPUTS[node.kind]:
            roles[index] = SemanticPortRole.EXECUTION_IN
        elif index == right[0] and node.kind in CONTACT_NODE_KINDS and port.port_kind_code == 2:
            roles[index] = SemanticPortRole.EXECUTION_OUT
        elif index == right[0] and node.kind in COIL_NODE_KINDS and port.port_kind_code == 0:
            # The coil's code-0 right port is observed, but its execution meaning
            # is not. Do not reuse the Function-specific ENO interpretation.
            issues.append(
                SemanticIssue(
                    code="unmodeled_coil_output",
                    message=(
                        f"coil {node.symbol!r} has the observed code-0 right graphic port; "
                        "its execution role is unverified and its net is preserved"
                    ),
                    node_offset=node.offset,
                    port_index=index,
                )
            )
        else:
            issues.append(
                SemanticIssue(
                    code="unknown_ladder_port",
                    message=(
                        f"{node.kind.value} {node.symbol!r} port {index} has unsupported "
                        f"port_kind_code {port.port_kind_code} at {points[index]}"
                    ),
                    node_offset=node.offset,
                    port_index=index,
                )
            )

    return tuple(roles), tuple(issues)


def _ladder_semantics(
    program: StructuredProgram,
    graph: ConnectivityGraph,
    terminals: Tuple[SemanticTerminal, ...],
) -> Tuple[Tuple[SemanticContact, ...], Tuple[SemanticCoil, ...], Tuple[SemanticIssue, ...]]:
    terminal_by_offset = {terminal.node_offset: terminal for terminal in terminals}
    contacts = []
    coils = []
    issues = []
    for node in program.nodes:
        if node.kind not in CONTACT_NODE_KINDS | COIL_NODE_KINDS:
            continue

        roles, role_issues = _infer_ladder_port_roles(node)
        issues.extend(role_issues)
        semantic_ports = []
        for port_index, (port, role) in enumerate(zip(node.ports, roles)):
            net = graph.net_for_port(node.offset, port_index)
            terminal_refs = _net_terminals(net, terminal_by_offset)
            semantic_ports.append(
                SemanticLadderPort(
                    node_offset=node.offset,
                    port_index=port_index,
                    role=role,
                    port_kind_code=port.port_kind_code,
                    point=node.port_point(port_index),
                    net_index=net.index,
                    terminal_node_offsets=tuple(
                        terminal.node_offset for terminal in terminal_refs
                    ),
                    terminal_symbols=tuple(terminal.symbol for terminal in terminal_refs),
                )
            )

        input_port = _unique_ladder_port(tuple(semantic_ports), SemanticPortRole.EXECUTION_IN)
        behavior = _ladder_behavior(node.kind, input_port.port_kind_code) if input_port else None
        edge = behavior[1] if behavior else LadderEdge.UNKNOWN
        if node.kind in COIL_NODE_KINDS:
            coils.append(
                SemanticCoil(
                    node_offset=node.offset,
                    symbol=node.symbol,
                    role=behavior[2] if behavior else CoilRole.UNKNOWN,
                    ports=tuple(semantic_ports),
                    edge=edge,
                )
            )
        else:
            contacts.append(
                SemanticContact(
                    node_offset=node.offset,
                    symbol=node.symbol,
                    polarity=(
                        ContactPolarity.UNKNOWN if behavior is None
                        else ContactPolarity.NORMALLY_CLOSED if behavior[0]
                        else ContactPolarity.NORMALLY_OPEN
                    ),
                    ports=tuple(semantic_ports),
                    edge=edge,
                )
            )

    return tuple(contacts), tuple(coils), tuple(issues)


def _function_semantics(
    program: StructuredProgram,
    graph: ConnectivityGraph,
    terminals: Tuple[SemanticTerminal, ...],
    registry: Mapping[str, FunctionFamilySpec],
) -> Tuple[Tuple[SemanticFunction, ...], Tuple[SemanticIssue, ...]]:
    terminal_by_offset: Dict[int, SemanticTerminal] = {
        terminal.node_offset: terminal for terminal in terminals
    }
    functions = []
    issues = []

    for node in program.nodes:
        if node.kind != NodeKind.FUNCTION:
            continue

        identity = _resolve_function_identity(node.symbol, registry)
        roles, role_issues = _infer_function_port_roles(node)
        issues.extend(role_issues)

        semantic_ports = []
        for port_index, (port, role) in enumerate(zip(node.ports, roles)):
            net = graph.net_for_port(node.offset, port_index)
            terminal_refs = _net_terminals(net, terminal_by_offset)
            semantic_ports.append(
                SemanticFunctionPort(
                    function_offset=node.offset,
                    port_index=port_index,
                    role=role,
                    port_kind_code=port.port_kind_code,
                    point=node.port_point(port_index),
                    net_index=net.index,
                    terminal_node_offsets=tuple(
                        terminal.node_offset for terminal in terminal_refs
                    ),
                    terminal_symbols=tuple(terminal.symbol for terminal in terminal_refs),
                )
            )

        data_input_count = sum(role == SemanticPortRole.DATA_IN for role in roles)
        data_output_count = sum(role == SemanticPortRole.DATA_OUT for role in roles)
        has_enable_interface = (
            sum(role == SemanticPortRole.ENABLE_IN for role in roles) == 1
            and sum(role == SemanticPortRole.ENABLE_OUT for role in roles) == 1
        )

        if identity.declared_arity is not None and identity.declared_arity != data_input_count:
            issues.append(
                SemanticIssue(
                    code="extensible_arity_mismatch",
                    message=(
                        f"function {node.symbol!r} declares arity {identity.declared_arity} "
                        f"but geometry contains {data_input_count} data input ports"
                    ),
                    node_offset=node.offset,
                )
            )
        if (
            identity.expected_data_inputs is not None
            and identity.expected_data_inputs != data_input_count
        ):
            issues.append(
                SemanticIssue(
                    code="fixed_arity_mismatch",
                    message=(
                        f"function {node.symbol!r} is observed with "
                        f"{identity.expected_data_inputs} data inputs but geometry contains "
                        f"{data_input_count}"
                    ),
                    node_offset=node.offset,
                )
            )

        functions.append(
            SemanticFunction(
                node_offset=node.offset,
                serialized_symbol=node.symbol,
                base_name=identity.base_name,
                family_known=identity.family_known,
                extensible_inputs=identity.extensible_inputs,
                declared_arity=identity.declared_arity,
                data_input_count=data_input_count,
                data_output_count=data_output_count,
                has_enable_interface=has_enable_interface,
                ports=tuple(semantic_ports),
            )
        )

    return tuple(functions), tuple(issues)


def build_semantic_model(
    program: StructuredProgram,
    *,
    connectivity: Optional[ConnectivityGraph] = None,
    function_registry: Mapping[str, FunctionFamilySpec] = DEFAULT_FUNCTION_FAMILY_REGISTRY,
    function_block_registry: Mapping[str, FunctionBlockSpec] = DEFAULT_FUNCTION_BLOCK_REGISTRY,
    function_block_instances: Optional[Mapping[int, Optional[FunctionBlockSpec]]] = None,
) -> StructuredSemanticModel:
    """Build the first read-only semantic layer above GXW geometry.

    The model identifies observed Function and terminal roles, contact polarity,
    edge modifiers, coil operations, FB instances and net bindings. Unobserved
    ladder input combinations retain their raw codes and unknown behavior.
    Known TON/TON_E/CTU/CTU_E interfaces
    use a separate registry; this layer does not simulate timer/counter state.
    The coil's code-0 right port keeps an unknown
    role pending execution evidence. Other node kinds remain unmodeled references.
    It does not yet lower Structured Ladder to the project-level PLC IR.
    """

    graph = connectivity if connectivity is not None else build_connectivity_graph(program)
    terminals, terminal_issues = _terminal_semantics(program, graph)
    functions, function_issues = _function_semantics(
        program,
        graph,
        terminals,
        function_registry,
    )
    contacts, coils, ladder_issues = _ladder_semantics(program, graph, terminals)
    function_blocks, block_issues = _function_block_semantics(
        program, graph, terminals, function_block_registry, function_block_instances,
    )

    unmodeled = tuple(
        UnmodeledNodeRef(node_offset=node.offset, kind=node.kind, symbol=node.symbol)
        for node in program.nodes
        if node.kind not in CONTACT_NODE_KINDS | COIL_NODE_KINDS | {
            NodeKind.FUNCTION, NodeKind.INPUT, NodeKind.OUTPUT,
            NodeKind.FUNCTION_BLOCK,
        }
    )

    return StructuredSemanticModel(
        logical_name=program.logical_name,
        functions=functions,
        terminals=terminals,
        unmodeled_nodes=unmodeled,
        issues=tuple([*terminal_issues, *function_issues, *ladder_issues, *block_issues]),
        contacts=contacts,
        coils=coils,
        function_blocks=function_blocks,
    )

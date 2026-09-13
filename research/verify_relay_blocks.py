"""Bounded truth-table check for these direct-X, ordinary-coil P0 controls.

This source-graph calculation is independent of the ladder layout algorithm.
It is not a PLC simulator and never substitutes for native compile validation.
"""
from itertools import product
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.connectivity import build_connectivity_graph
from gxw.models import GXWFormatError, NodeKind


def relay_truth_table(program):
    if program.unknown_records or any(n.kind not in (NodeKind.CONTACT, NodeKind.CONTACT_NC, NodeKind.COIL)
                                      for n in program.nodes):
        raise GXWFormatError('truth-table experiment supports only known relay nodes')
    inputs = sorted({n.symbol for n in program.nodes if n.kind != NodeKind.COIL})
    if len(inputs) > 8 or any(not s.startswith('X') for s in inputs):
        raise GXWFormatError('truth-table experiment requires at most eight direct X inputs')
    graph = build_connectivity_graph(program)
    rails = []
    for view in program.block_views():
        matches = [w for w in view.wires if (w.start.x, w.start.y, w.end.x, w.end.y)
                   == (1, 0, 1, view.canvas_height)]
        if len(matches) != 1:
            raise GXWFormatError('experiment requires exactly one observed rail per block')
        rails.append(graph.net_for_wire(matches[0].offset).index)
    table = []
    for values in product((False, True), repeat=len(inputs)):
        assignment = dict(zip(inputs, values))
        powered = set(rails)
        for _ in range(len(program.nodes) + 1):
            previous = set(powered)
            for n in program.nodes:
                if n.kind == NodeKind.COIL:
                    continue
                active = assignment[n.symbol] if n.kind == NodeKind.CONTACT else not assignment[n.symbol]
                if active and graph.net_for_port(n.offset, 0).index in powered:
                    powered.add(graph.net_for_port(n.offset, 1).index)
            if powered == previous:
                break
        outputs = {}
        for n in program.nodes:
            if n.kind == NodeKind.COIL:
                if n.symbol in outputs:
                    raise GXWFormatError('duplicate output assignments are outside this experiment')
                outputs[n.symbol] = graph.net_for_port(n.offset, 0).index in powered
        table.append({'inputs': assignment, 'outputs': dict(sorted(outputs.items()))})
    return table

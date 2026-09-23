from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .models import StructuredNode, StructuredProgram, StructuredWire, UnknownRecord
from .project_resolver import GXWProjectResolver
from .structured_pou import parse_structured_pou


def read_structured_program(
    path: Union[str, Path], *, logical_name: Optional[str] = None
) -> StructuredProgram:
    source = Path(path)
    resolver = GXWProjectResolver.from_file(source)
    selected = resolver.choose_program_pou(logical_name)
    return parse_structured_pou(
        resolver.read_logical_file(selected),
        logical_name=selected,
        source_path=source,
    )


def _node_label(node: StructuredNode) -> str:
    labels = {
        "contact": "Contact",
        "contact_nc": "ContactNC",
        "coil": "Coil",
        "function": "Function",
        "function_block": "FunctionBlock",
        "input": "Input",
        "output": "Output",
        "unknown": f"Node[0x{node.kind_code:02X}]",
    }
    return labels[node.kind.value]


def describe_program(program: StructuredProgram) -> List[str]:
    lines = [
        f"Program {program.logical_name}: {len(program.nodes)} nodes, "
        f"{len(program.wires)} wires, {len(program.blocks)} blocks, canvas_height={program.canvas_height}"
    ]
    for record in program.iter_records():
        if isinstance(record, StructuredNode):
            identity = (
                f"{record.symbol}: {record.type_name}"
                if record.type_name is not None
                else record.symbol
            )
            lines.append(f"{_node_label(record)} {identity} @ {record.bbox}")
        elif isinstance(record, StructuredWire):
            lines.append(
                f"Wire ({record.start.x},{record.start.y}) -> "
                f"({record.end.x},{record.end.y})"
            )
        elif isinstance(record, UnknownRecord):
            lines.append(
                f"UnknownRecord class={record.record_class} @ 0x{record.offset:X}"
            )
    return lines


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only GX Works2 Structured Ladder/FBD decoder"
    )
    parser.add_argument("gxw", type=Path, help="GX Works2 .gxw file")
    parser.add_argument(
        "--program",
        dest="program",
        default=None,
        help="logical *.Program.pou name",
    )
    parser.add_argument("--list-programs", action="store_true", help="List program bodies without modifying the project")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.list_programs:
        print("\n".join(GXWProjectResolver.from_file(args.gxw).program_pou_names()))
        return 0
    program = read_structured_program(args.gxw, logical_name=args.program)
    print("\n".join(describe_program(program)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

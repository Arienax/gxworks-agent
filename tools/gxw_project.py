# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Unified, offline GXW project generation and regression CLI."""
from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gxw.decoder import read_structured_program
from gxw.experiment import compare_projects, run_regression
from gxw.project_writer import write_gxw_project, write_new_file
from gxw.structured_pou_writer import insert_series_contact_after, replace_node_symbol
from gxw.structured_writer import structured_from_ladder
from gxw.declarations import edit_declarations
from gxw.object_model import export_object_model, generate_object_project, read_project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "symbol", "series"):
        p = sub.add_parser(command)
        p.add_argument("baseline", type=Path)
        p.add_argument("-o", "--output", required=True, type=Path)
        p.add_argument("--report", required=True, type=Path)
        p.add_argument("--program")
        if command == "generate":
            p.add_argument("model", type=Path, help="Existing PLC IR or ladder JSON")
        else:
            p.add_argument("old_symbol")
            p.add_argument("new_symbol")
            p.add_argument("--node-offset", type=lambda s: int(s, 0))
            if command == "series":
                p.add_argument("--gap", type=int, default=3)
    p = sub.add_parser("regression")
    p.add_argument("baseline", type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--results-dir", type=Path, default=private_path('research/results'))
    p.add_argument("--program")
    p = sub.add_parser("compare")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--report", required=True, type=Path)
    p = sub.add_parser("inspect", help="Export the structured object/declaration model")
    p.add_argument("baseline", type=Path)
    p.add_argument("--program")
    p.add_argument("-o", "--output", required=True, type=Path)
    p = sub.add_parser("fbd", help="Generate from a structured object model")
    p.add_argument("model", type=Path)
    p.add_argument("--baseline", type=Path, help="Omit to use the verified FX3U template")
    p.add_argument("-o", "--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    p = sub.add_parser("declarations", help="Apply named declaration table edits")
    p.add_argument("baseline", type=Path)
    p.add_argument("edits", type=Path, help="JSON mapping table names to upserts/renames/remove")
    p.add_argument("--program")
    p.add_argument("-o", "--output", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    inputs = [getattr(args, name, None) for name in ("baseline", "before", "after", "model", "edits")]
    outputs = [getattr(args, name, None) for name in ("output", "report")]
    inputs = {p.resolve() for p in inputs if p is not None}
    outputs = [p.resolve() for p in outputs if p is not None]
    if len(set(outputs)) != len(outputs) or inputs.intersection(outputs):
        parser.error("input, output and report paths must be distinct")
    if any(p.exists() for p in outputs):
        parser.error("output/report already exists; select new paths")
    if args.command == "inspect":
        program, documents, _ = read_project(args.baseline.read_bytes(), args.program)
        model = export_object_model(program, documents)
        write_new_file(args.output, json.dumps(model, ensure_ascii=False, indent=2).encode("utf-8"))
        print(f"Model: {args.output}")
        return
    if args.command == "regression":
        report = run_regression(args.baseline, args.output_dir, args.results_dir, logical_name=args.program)
        print(json.dumps({k: v["sha256"] for k, v in report["variants"].items()}, indent=2))
        return
    if args.report.exists():
        parser.error("report already exists; select a new path")
    if args.command == "fbd":
        baseline = args.baseline.read_bytes() if args.baseline else None
        result = generate_object_project(json.loads(args.model.read_text(encoding="utf-8")), baseline=baseline)
        if args.baseline and args.baseline.read_bytes() != baseline:
            parser.error("baseline changed during generation")
        write_new_file(args.output, result.data)
        report = result.report
    elif args.command == "declarations":
        _, documents, _ = read_project(args.baseline.read_bytes(), args.program)
        edits = json.loads(args.edits.read_text(encoding="utf-8"))
        if not isinstance(edits, dict) or not edits or any(name not in documents for name in edits):
            parser.error("edits must name existing declaration tables")
        changed = {name: edit_declarations(documents[name], **patch) for name, patch in edits.items()}
        report = write_gxw_project(args.baseline, args.output, declarations=changed).report
    elif args.command == "compare":
        report = compare_projects(args.before.read_bytes(), args.after.read_bytes())
    else:
        if args.report.resolve() in {args.baseline.resolve(), args.output.resolve()}:
            parser.error("baseline, output and report must be distinct paths")
        program = read_structured_program(args.baseline, logical_name=args.program)
        if args.command == "generate":
            program = structured_from_ladder(program, json.loads(args.model.read_text(encoding="utf-8")))
        elif args.command == "symbol":
            program = replace_node_symbol(program, args.old_symbol, args.new_symbol, node_offset=args.node_offset)
        else:
            program = insert_series_contact_after(program, args.old_symbol, args.new_symbol,
                                                  node_offset=args.node_offset, horizontal_gap=args.gap)
        report = write_gxw_project(args.baseline, args.output, program).report
    write_new_file(args.report, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()

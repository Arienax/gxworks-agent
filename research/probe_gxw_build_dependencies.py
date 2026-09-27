"""Separate observed task, declaration and simple-FB body dependencies.

Native controls show that an uncalled FB instance can allocate storage without
validating the FB body. Unassigned user programs can be absent from both paths.
Global declarations form separate type roots even when tasks contain no POUs.
The observed marker traversal is a lower bound, not complete call analysis.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from probe_gxw_task_graph import inspect_task_graph
from probe_gxw_simple_fb import frame_source, observe_calls
from gxw.declarations import parse_declarations
from gxw.lossless import inspect_project, sha256


def inspect_build_dependencies(source: bytes) -> dict:
    graph = inspect_task_graph(source)
    streams = {s.logical_name: s.raw for s in inspect_project(source).streams
               if s.logical_name and s.raw is not None}
    programs = graph["programs"]
    declarations, declaration_edges, call_edges, body_gaps = {}, [], [], []
    declaration_gaps = []
    owners = [("local", name, item["declarations"]) for name, item in programs.items()]
    owners += [("global", name, name) for name in sorted(streams) if name.endswith(".gh")]
    for scope, owner, logical in owners:
        try:
            document = parse_declarations(streams[logical], logical_name=logical)
        except ValueError as exc:
            declaration_gaps.append(dict(scope=scope, owner=owner, logical_name=logical, reason=str(exc)))
            continue
        declarations[(scope, owner)] = document
        for row in document.rows:
            if row.type_code != 15:
                continue
            target = row.type_reference if row.type_reference in programs else None
            declaration_edges.append(dict(scope=scope, owner=owner, declarations=logical,
                row_offset=row.offset, row_sha256=sha256(row.raw), instance=row.name,
                data_type=row.data_type, array_marker=row.array_marker,
                target_reference=row.type_reference, target_program=target,
                resolution="exact-local-identity" if target else "unresolved-or-external-type"))
    for name, item in programs.items():
        raw = streams[item["source_program"]]
        try:
            framed = frame_source(raw)
        except ValueError as exc:
            body_gaps.append(dict(program=name, source_program=item["source_program"], reason=str(exc)))
            continue
        observed = observe_calls(framed)
        body_gaps.extend(dict(program=name, **gap) for gap in observed["gaps"])
        document = declarations.get(("local", name))
        for call in observed["calls"]:
            matched = [("local", item["declarations"], r) for r in document.rows
                       if r.name == call["instance"]] if document else []
            matched += [("global", global_doc.logical_name, r)
                        for (scope, _), global_doc in declarations.items() if scope == "global"
                        for r in global_doc.rows if r.name == call["instance"]]
            target = call["definition"] if call["definition"] in programs else None
            # Global-only calls are native-validated. Shadowing/case folding is
            # not inferred when multiple declarations could supply the name.
            agrees = (len(matched) == 1 and matched[0][2].type_code == 15
                      and matched[0][2].type_reference == call["definition"])
            binding = (dict(scope=matched[0][0], declarations=matched[0][1],
                            row_offset=matched[0][2].offset, row_sha256=sha256(matched[0][2].raw))
                       if agrees else None)
            call_edges.append(dict(caller=name, source_program=item["source_program"],
                offset=call["offset"], length=call["length"], sha256=call["sha256"],
                instance=call["instance"], definition=call["definition"], target_program=target,
                instance_declaration_agrees=agrees, instance_declaration=binding,
                input_fragment=call["input_fragment"], output_fragment=call["output_fragment"],
                marker_gaps=call["gaps"]))
    roots = sorted({e["program_reference"] for task in graph["tasks"].values()
                    for e in task.get("entries", []) if e.get("source_program") is not None})
    local_edges = defaultdict(list)
    for edge in declaration_edges:
        if edge["scope"] == "local":
            local_edges[edge["owner"]].append(edge)
    pending = list(roots)
    active_declarations = [e for e in declaration_edges if e["scope"] == "global"]
    pending.extend(e["target_program"] for e in active_declarations if e["target_program"])
    instantiated = set()
    while pending:
        name = pending.pop()
        if name in instantiated:
            continue
        instantiated.add(name)
        active_declarations.extend(local_edges[name])
        pending.extend(e["target_program"] for e in local_edges[name] if e["target_program"])
    calls_by_owner = defaultdict(list)
    for edge in call_edges:
        calls_by_owner[edge["caller"]].append(edge)
    pending = list(roots)
    body_candidates, active_calls = set(), []
    while pending:
        name = pending.pop()
        if name in body_candidates:
            continue
        body_candidates.add(name)
        active_calls.extend(calls_by_owner[name])
        pending.extend(e["target_program"] for e in calls_by_owner[name]
                       if e["target_program"] and not e["marker_gaps"]
                       and e["instance_declaration_agrees"])
    return dict(project_sha256=sha256(source), task_roots=roots,
        global_declaration_roots=[logical for scope, _, logical in owners if scope == "global"],
        program_identities=programs, declaration_edges=declaration_edges,
        observed_simple_fb_calls=call_edges,
        declaration_reachability=dict(programs=sorted(instantiated), edges=active_declarations,
            unresolved=[e for e in active_declarations if e["target_program"] is None],
            gaps=[g for g in declaration_gaps if g["scope"] == "global" or g["owner"] in instantiated]),
        observed_body_reachability=dict(programs=sorted(body_candidates), edges=active_calls,
            gaps=[g for g in body_gaps if g["program"] in body_candidates],
            unresolved=[e for e in active_calls if not e["target_program"] or not e["instance_declaration_agrees"]]),
        inventory_gaps=dict(project=graph["gaps"], declarations=declaration_gaps, bodies=body_gaps),
        complete_dependency_proof=False,
        scope="Observed user FB type and marker edges only. No exact allocation, complete validation, dead-code, runtime or safe-deletion inference.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_build_dependencies(args.source.read_bytes())
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(task_roots=result["task_roots"],
        declaration_programs=result["declaration_reachability"]["programs"],
        observed_body_programs=result["observed_body_reachability"]["programs"])))

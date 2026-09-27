"""Reproduce isolated native ladder kind/port controls, without device execution.

Uses the archived native source, patches only the selected node kind/input port
and the observed pending marker, then compiles through WorkspaceReplayOracle.
ECCompiler's independent native token decoder supplies the instruction listing.
Existing observations are compared by full PCode bytes. No source semantics are
used to manufacture the expected native instructions.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gxw.lossless import inspect_project, sha256
from gxw.project_metadata import mark_observed_source_compile_pending
from gxw.semantic import build_semantic_model
from gxw.structured_pou import parse_structured_pou
from native_gxw_tokens import DEFAULT_DLL, native_batch, native_il_projection
from probe_gxw_sfc_graph import patch_raw
from replay_gxw_workspace import run


def probe(output: Path, variants: list[tuple[str, int, int]], *, reverse_ports: bool = False) -> dict:
    fixture = json.loads((ROOT / "tests/fixtures/gxw_ladder_primitives.json").read_text())
    provenance = fixture["provenance"]
    archive = ROOT / provenance["source_archive"]
    if sha256(archive.read_bytes()) != provenance["source_archive_sha256"]:
        raise ValueError("archived native seed hash mismatch")
    with zipfile.ZipFile(archive) as z:
        source = z.read(provenance["source_member"])
    streams = {s.logical_name: s.raw for s in inspect_project(source).streams if s.logical_name}
    logical = "1.Program.pou"
    raw = streams[logical]
    program = parse_structured_pou(raw, logical_name=logical)
    observed = {row["case"]: row for row in fixture["cases"]}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "probe.py").write_bytes(Path(__file__).read_bytes())
    result = dict(schema=1, seed_sha256=sha256(source), provenance=provenance, cases=[])
    for role, kind, port_code in variants:
        case = f"{role}-{kind:02d}-port-{port_code:03d}" + ("-reversed" if reverse_ports else "")
        directory = output / case
        directory.mkdir()
        node = next(n for n in program.nodes if n.kind.value == role)
        port_offset = node.offset + node.record_length - sum(p.size for p in node.ports) + 4
        if struct.unpack_from("<I", raw, port_offset)[0] != node.ports[0].port_kind_code:
            raise ValueError("source input-port field mismatch")
        updated = bytearray(raw)
        struct.pack_into("<I", updated, node.offset + 8, kind)
        struct.pack_into("<I", updated, port_offset, port_code)
        if reverse_ports:
            if len(node.ports) != 2 or any(port.size != 16 for port in node.ports):
                raise ValueError("port reversal requires the observed two-port record")
            start = port_offset - 4
            updated[start:start + 32] = updated[start + 16:start + 32] + updated[start:start + 16]
        updated = mark_observed_source_compile_pending(bytes(updated), logical_name=logical)
        patched, receipt = patch_raw(source, logical, raw, updated)
        actual = {s.logical_name: s.raw for s in inspect_project(patched).streams if s.logical_name}
        if actual != dict(streams, **{logical: updated}):
            raise ValueError("unrelated logical payload changed")
        path = directory / "source.gxw"
        path.write_bytes(patched)
        row = dict(case=case, kind=kind, port_code=port_code, role=role, reversed_ports=reverse_ports,
                   source_sha256=sha256(patched), program_sha256=sha256(updated),
                   mutation=receipt, unrelated_logical_payloads="byte-identical")
        model = build_semantic_model(parse_structured_pou(updated, logical_name=logical))
        row["core_elements"] = [asdict(n) for n in (*model.contacts, *model.coils)]
        row["core_issues"] = [asdict(i) for i in model.issues]
        row["core_unmodeled"] = [asdict(n) for n in model.unmodeled_nodes]
        native = directory / "native"
        row["native"] = run(path, native, compile=True, export_project=True,
                            project_alias="GRAPHPRIMITIVE")
        compiled = native / "pcode-0-0.bin"
        accepted = (row["native"]["compile_completed"]
                    and not row["native"]["compiler_rejected"]
                    and row["native"].get("native_export")
                    and compiled.is_file())
        row["accepted"] = bool(accepted)
        if accepted:
            code = compiled.read_bytes()
            answer, = native_batch(
                [dict(mode="decode", input_base64=base64.b64encode(code + b"\0").decode())],
                directory / "native-decode", dll=DEFAULT_DLL, cpu_code=0x208,
            )
            row["decode_result"] = answer
            if answer["return_code"] == "0x00000000" and answer["consumed_bytes"] == len(code):
                row["listing"] = native_il_projection(
                    base64.b64decode(answer["output_base64"]), encoding="gb18030",
                )
            row["pcode_sha256"] = sha256(code)
            previous = observed.get(case)
            row["matches_observed_pcode"] = (
                code == base64.b64decode(previous["pcode_base64"]) if previous else None
            )
            row["matches_observed_source"] = (
                sha256(updated) == previous["program_sha256"] if previous else None
            )
        result["cases"].append(row)
        (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: row.get(k) for k in
                         ("case", "accepted", "matches_observed_pcode", "matches_observed_source")}), flush=True)
    return result


def variant(value: str) -> tuple[str, int, int]:
    try:
        role, kind, port = value.split(":")
        values = int(kind, 0), int(port, 0)
        if role not in {"contact", "coil"} or any(not 0 <= v <= 0xFFFFFFFF for v in values):
            raise ValueError()
        return role, *values
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected contact:KIND:PORT or coil:KIND:PORT with uint32 values") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory; existing results are never overwritten")
    parser.add_argument("--variant", action="append", type=variant,
                        help="repeat to choose controls; defaults to all 26 recorded combinations")
    parser.add_argument("--reverse-ports", action="store_true",
                        help="reverse the two serialized port records, retaining each port's geometry and flags")
    args = parser.parse_args()
    variants = args.variant
    if not variants:
        fixture = json.loads((ROOT / "tests/fixtures/gxw_ladder_primitives.json").read_text())
        variants = [(r["role"], r["kind"], r["port_code"]) for r in fixture["cases"]]
    if len(variants) != len(set(variants)):
        parser.error("duplicate variants")
    probe(args.output, variants, reverse_ports=args.reverse_ports)

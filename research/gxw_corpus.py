"""Replayable GXW corpus inventory, independent checks and failure capture.

No vendor UI is invoked. Native observations remain separate hash-bound records.
Local reports may contain project names: keep private corpora outside version
control. Archives are read in memory; no extraction or arbitrary replay command.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import io
import json
from pathlib import Path
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container import CompoundFile
from gxw.declarations import parse_declarations
from gxw.experiment import compare_programs
from gxw.lossless import (inspect_project, inspect_program, sha256,
                          patch_structured_symbol_equal_size, patch_token_constant)
from gxw.token_listing import decode_token_listing, TokenInstruction, TokenText, TokenLabel
from gxw.models import NodeKind
from gxw.semantic import DEFAULT_FUNCTION_BLOCK_REGISTRY, DEFAULT_FUNCTION_FAMILY_REGISTRY
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import serialize_structured_pou
from gxw.project_writer import write_new_file


def _manifest_path(base: Path, value: str, label: str) -> Path:
    """Resolve only repository-contained POSIX paths stored in corpus manifests."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} path must use repository-relative POSIX syntax")
    relative = Path(value)
    first = relative.parts[0] if relative.parts else ""
    if relative.is_absolute() or ":" in first:
        raise ValueError(f"{label} path must not be absolute or drive-qualified")
    resolved = (base / relative).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as error:
        raise ValueError(f"{label} path escapes the repository") from error
    return resolved


def independent_cfb(raw: bytes) -> dict:
    """olefile has its own FAT/MiniFAT/tree reader; it imports no gxw code.

    This checks the container, not GX format semantics. Compare multisets to
    avoid collapsing repeated names in different CFB storages.
    """
    try:
        import olefile
    except ImportError:
        return {"status": "not-checkable", "reason": "install requirements/gxw-test.txt"}
    try:
        with olefile.OleFileIO(io.BytesIO(raw)) as ole:
            external = [(tuple(path), ole.openstream(path).read()) for path in ole.listdir()]
            external_set = Counter((p[-1], len(b), sha256(b)) for p, b in external)
            ours = CompoundFile(raw)
            internal_set = Counter((e.name, len(b), sha256(b)) for e in ours.iter_streams()
                                   for b in [ours.read_entry(e)])
            return {"status": "agrees" if external_set == internal_set else "differs",
                    "scope": "reachable stream names, sizes and payloads (multiset)",
                    "reader": "olefile " + olefile.__version__, "stream_count": len(external),
                    "directory_paths": [list(p) for p, _ in external],
                    "parsing_issue_count": len(ole.parsing_issues)}
    except Exception as exc:
        return {"status": "not-checkable", "reason": type(exc).__name__ + ": " + str(exc)}


def reverse_token_boundaries(raw: bytes) -> list[tuple[int, bytes]]:
    """Independent traversal from trailing lengths; no production tokenizer.

    Shares the observed envelope/length hypothesis, so agreement is NOT an
    independent discovery of that hypothesis. It catches traversal/offset bugs.
    """
    stop, cursor, tokens = 79, len(raw) - 24, []
    while cursor > stop:
        size = raw[cursor - 1]
        start = cursor - size
        if size < 2 or start < stop or raw[start] != size:
            raise ValueError("reverse token boundary mismatch")
        tokens.append((start, raw[start:cursor]))
        cursor = start
    if cursor != stop:
        raise ValueError("reverse token body length mismatch")
    return list(reversed(tokens))


def record_envelopes(raw: bytes) -> list[tuple[int, int, int]]:
    """Independent framing-only walk; no node/port/semantic decoder imports.

    Deliberately shares the documented envelope hypothesis, not semantic parser
    code. Record identity/size/class agreement cannot prove node semantics.
    """
    cursor, result = 71, []
    for _ in range(int.from_bytes(raw[67:71], "little")):
        length = int.from_bytes(raw[cursor:cursor + 4], "little")
        end = cursor + length
        count = int.from_bytes(raw[cursor + 20:cursor + 24], "little")
        if length < 24 or end > len(raw) - 24 or count > (length - 24) // 8:
            raise ValueError("invalid independent block envelope")
        cursor += 24
        for _ in range(count):
            size = int.from_bytes(raw[cursor:cursor + 4], "little")
            kind = int.from_bytes(raw[cursor + 4:cursor + 8], "little")
            if size < 8 or cursor + size > end:
                raise ValueError("invalid independent record envelope")
            result.append((cursor, size, kind))
            cursor += size
        if cursor != end:
            raise ValueError("independent record count mismatch")
    if cursor != len(raw) - 24:
        raise ValueError("independent trailer boundary mismatch")
    return result


def unknown_interface(node) -> bool:
    return (node.kind == NodeKind.FUNCTION_BLOCK and node.type_name not in DEFAULT_FUNCTION_BLOCK_REGISTRY) or (
        node.kind == NodeKind.FUNCTION and node.symbol.rsplit("-", 1)[0] not in DEFAULT_FUNCTION_FAMILY_REGISTRY)


def program_report(raw: bytes, logical_name: str, resources: list[bytes]) -> dict:
    image = inspect_program(raw, logical_name=logical_name)
    result = {"layout": image.layout, "handling": "opaque-preserved" if image.layout == "unsupported" else "partially-decoded",
              "evidence_level": "observed", "source_replay": "byte-identical" if image.reconstruct() == raw else "differs",
              "semantic_complete": False, "diagnostics": list(image.diagnostics),
              "regions": [{"offset": r.offset, "length": len(r.raw), "sha256": sha256(r.raw),
                           "kind": r.kind, "handling": r.handling} for r in image.regions],
              "semantic_roundtrip": "not-checkable", "native_validation": "not_run"}
    if image.layout == "structured":
        p = image.projection
        records = list(p.iter_records())
        actual = [(r.offset, r.record_length, struct.unpack_from("<I", r.raw, 4)[0]) for r in records]
        result["framing_cross_check"] = "agrees" if record_envelopes(raw) == actual else "differs"
        result["record_count"] = len(records)
        result["block_count"] = len(p.blocks)
        result["opaque_records"] = len(p.unknown_records)
        result["node_count"] = len(p.nodes)
        result["unknown_node_kinds"] = dict(Counter(str(n.kind_code) for n in p.nodes if n.kind == NodeKind.UNKNOWN))
        unknown_interfaces = [n for n in p.nodes if unknown_interface(n)]
        result["unknown_interfaces"] = len(unknown_interfaces)
        result["known_node_projections"] = sum(n.kind != NodeKind.UNKNOWN and not unknown_interface(n) for n in p.nodes)
        result["critical_record_gaps"] = len(p.unknown_records) + sum(result["unknown_node_kinds"].values()) + len(unknown_interfaces)
        result["abi_signatures"] = dict(Counter(json.dumps([n.kind_code,
            n.type_name if n.kind == NodeKind.FUNCTION_BLOCK else n.symbol if n.kind == NodeKind.FUNCTION else None,
            [[port.port_kind_code, port.local_x, port.local_y] for port in n.ports]], separators=(",", ":")) for n in p.nodes))
        try:
            strict = parse_structured_pou(raw, logical_name=logical_name)
        except ValueError as exc:
            result["strict_parser"] = "unsupported"
            result["writer_roundtrip"] = "not-checkable"
            result["parser_reason"] = str(exc)
        else:
            result["strict_parser"] = "accepted"
            try:
                encoded = serialize_structured_pou(strict)
                result["writer_roundtrip"] = "byte-identical" if encoded == raw else "differs"
            except ValueError as exc:
                result["writer_roundtrip"] = "unsupported"
                result["writer_reason"] = str(exc)
    elif image.layout == "ladder-token":
        p = image.projection
        listing = decode_token_listing(raw)
        result["decoded_instructions"] = len(listing.instructions)
        result["decoded_labels"] = sum(isinstance(r, TokenLabel) for r in listing.records)
        result["instruction_gaps"] = len(listing.gaps)
        result["recognized_texts"] = sum(isinstance(r, TokenText) for r in listing.records)
        result["framing_cross_check"] = "agrees" if reverse_token_boundaries(raw) == [(t.offset, t.raw) for t in p.tokens] else "differs"
        result["token_count"] = len(p.tokens)
        result["annotated_tokens"] = sum(t.annotation()["kind"] != "opaque" for t in p.tokens)
        result["unknown_token_signatures"] = dict(Counter(f"type={t.raw[1]:02x},length={len(t.raw)}" for t in p.tokens if t.annotation()["kind"] == "opaque"))
        result["critical_token_gaps"] = len(p.tokens) - result["annotated_tokens"]
        result["res_body_cross_check"] = "byte-identical-subsequence" if any(p.body in r for r in resources) else "not-found"
        result["res_scope"] = "native .res byte duplication; no decoded semantics or freshness claim"
        result["writer_roundtrip"] = "unsupported: read-only token path"
    else:
        result["framing_cross_check"] = "not-checkable"
        result["writer_roundtrip"] = "not-checkable"
    return result


def analyze(raw: bytes) -> dict:
    image = inspect_project(raw)
    result = {"sha256": image.sha256, "size": len(raw), "diagnostics": list(image.diagnostics),
              "source_replay": "byte-identical" if image.reconstruct() == raw else "differs",
              "source_replay_scope": "raw snapshot replay, not a container rebuild or semantic proof",
              "unbacked_mappings": [dict(zip(("logical_name", "stream", "status"), row)) for row in image.unbacked_mappings],
              "streams": [], "independent_cfb": {"outer": independent_cfb(raw)},
              "dimensions": {"gxworks_version": "unknown unless separately attested", "plc_family": "unknown unless separately attested"}}
    resources = [s.raw for s in image.streams if s.raw is not None and s.logical_name and s.logical_name.endswith(".res")]
    for s in image.streams:
        item = {"layer": s.layer, "directory_index": s.directory_index, "name": s.name,
                "logical_name": s.logical_name, "handling": "opaque-preserved" if s.raw is not None else "unsupported",
                "error": s.error, "provenance": [asdict(e) for e in s.extents]}
        if s.raw is not None:
            item.update(size=len(s.raw), sha256=sha256(s.raw), prefix16=s.raw[:16].hex())
            if s.layer == "outer" and s.name == "_hdb":
                result["independent_cfb"]["nested"] = independent_cfb(s.raw)
            if s.logical_name and s.logical_name.endswith(".Program.pou"):
                item["program"] = program_report(s.raw, s.logical_name, resources)
                item["handling"] = item["program"]["handling"]
            elif s.logical_name and s.logical_name.endswith((".Labels.lh", ".gh")):
                try:
                    d = parse_declarations(s.raw, logical_name=s.logical_name)
                    item.update(handling="partially-decoded", declaration_rows=len(d.rows))
                except ValueError as exc:
                    item["parse_gap"] = str(exc)
        result["streams"].append(item)
    nested = [s for s in result["streams"] if s["layer"] == "nested"]
    programs = [s["program"] for s in nested if "program" in s]
    result["coverage"] = {
        "total_objects": len(nested), "object_unit": "nested CFB streams; storage mappings excluded",
        "recognized_objects": sum("program" in s or "declaration_rows" in s for s in nested),
        "structurally_parsed_objects": sum(s["handling"] == "partially-decoded" for s in nested),
        "fully_semantically_decoded_objects": 0,
        "known_node_projections": sum(p.get("known_node_projections", 0) for p in programs),
        "opaque_preserved_objects": sum(s["handling"] == "opaque-preserved" for s in nested),
        "unreadable_objects": sum(s["handling"] == "unsupported" for s in nested),
        "programs": len(programs), "program_layout_gaps": sum(p["layout"] == "unsupported" for p in programs),
        "critical_record_gaps": sum(p.get("critical_record_gaps", 0) for p in programs),
        "critical_token_gaps": sum(p.get("critical_token_gaps", 0) for p in programs),
        "decoded_instructions": sum(p.get("decoded_instructions", 0) for p in programs),
        "instruction_gaps": sum(p.get("instruction_gaps", 0) for p in programs),
        "unresolved_mappings": sum(r[2] == "unresolved" for r in image.unbacked_mappings),
        "writer_touch_bytes": 0, "native_validated_in_this_run": 0,
    }
    result["coverage"]["parse_gap_objects"] = len(nested) - result["coverage"]["structurally_parsed_objects"]
    return result


def compare(before: bytes, after: bytes) -> dict:
    """Orthogonal verdicts; never normalize away opaque data or compiler state."""
    left, right = inspect_project(before), inspect_project(after)
    a = {(s.layer, s.name): s for s in left.streams}
    b = {(s.layer, s.name): s for s in right.streams}
    valid = (not left.diagnostics and not right.diagnostics and len(a) == len(left.streams)
             and len(b) == len(right.streams) and all(s.raw is not None for s in (*left.streams, *right.streams)))
    if not valid:
        return {"bytes": "byte-identical" if before == after else "differs", "structure": "not-checkable",
                "semantics": "not-checkable", "opaque": "not-checkable"}
    changed = [list(k) for k in sorted(a.keys() | b.keys()) if k not in a or k not in b or a[k].raw != b[k].raw]
    projections = {}
    for key in a.keys() & b.keys():
        x, y = a[key], b[key]
        if x.logical_name and x.logical_name.endswith(".Program.pou"):
            try:
                p, q = parse_structured_pou(x.raw), parse_structured_pou(y.raw)
                checks = compare_programs(p, q)["checks"]
                incomplete = bool(p.unknown_records or q.unknown_records or any(
                    n.kind == NodeKind.UNKNOWN or unknown_interface(n) for n in (*p.nodes, *q.nodes)))
                projections[x.logical_name] = {"checks": checks,
                    "verdict": "not-checkable" if incomplete else "semantically-equivalent" if all(checks.values()) else "differs",
                    "scope": "existing known structured graph projection, not whole-program semantics"}
            except ValueError:
                projections[x.logical_name] = "not-checkable"
    return {"bytes": "byte-identical" if before == after else "differs",
            "structure": "structurally-identical" if a.keys() == b.keys() and all(
                a[k].logical_name == b[k].logical_name and len(a[k].raw) == len(b[k].raw) for k in a) else "differs",
            "structure_scope": "stream inventory/mapping/lengths; allocation order ignored",
            "semantics": "not-checkable: whole-project meaning includes opaque streams",
            "known_structured_projections": projections, "opaque": "opaque-preserved" if before == after else "not-checkable",
            "changed_streams": changed,
            "independent_cfb": {"before": independent_cfb(before), "after": independent_cfb(after)}}


def inputs(paths):
    for path in paths:
        if path.is_dir():
            yield from inputs(sorted(path.rglob("*.gxw")))
        elif path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in sorted(archive.namelist()):
                    if member.lower().endswith(".gxw"):
                        yield {"path": str(path), "member": member}, archive.read(member)
        elif path.suffix.lower() == ".json":
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("schema") != "gxw-corpus-v1":
                raise ValueError("unsupported corpus manifest schema")
            base = (path.parent / manifest.get("base", ".")).resolve()
            for case in manifest["cases"]:
                source = case["source"]
                source_path = _manifest_path(base, source["path"], "corpus source")
                if "member" in source:
                    with zipfile.ZipFile(source_path) as archive:
                        raw = archive.read(source["member"])
                else:
                    raw = source_path.read_bytes()
                if sha256(raw) != source["sha256"]:
                    raise ValueError("corpus source hash mismatch: " + case["id"])
                location = {"path": str(source_path), "case_id": case["id"], "corpus_manifest": str(path),
                            "recorded_observations": case.get("recorded_observations", [])}
                if "expected_native" in case:
                    location["expected_native"] = case["expected_native"]
                if "member" in source:
                    location["member"] = source["member"]
                # Verify attestation bytes too; do not trust an unbound narrative.
                for observation in location["recorded_observations"]:
                    evidence = _manifest_path(base, observation["path"], "native observation").read_bytes()
                    if sha256(evidence) != observation["sha256"]:
                        raise ValueError("native attestation hash mismatch")
                yield location, raw
        else:
            yield {"path": str(path)}, path.read_bytes()


def scan(paths) -> dict:
    cases, by_hash, families, abis, unknown_tokens = [], {}, defaultdict(list), Counter(), Counter()
    for location, raw in inputs(paths):
        digest = sha256(raw)
        if digest in by_hash:
            by_hash[digest]["sources"].append(location)
            continue
        report = analyze(raw)
        report["sources"] = [location]
        cases.append(report)
        by_hash[digest] = report
        for s in report["streams"]:
            if s["layer"] == "nested" and "sha256" in s:
                name = s["logical_name"] or "<unmapped>"
                family = ".".join(name.split(".")[1:]) if "." in name else name
                families[family].append((digest, s["sha256"], s["size"], s["prefix16"], s["handling"]))
            p = s.get("program", {})
            abis.update(p.get("abi_signatures", {}))
            unknown_tokens.update(p.get("unknown_token_signatures", {}))
    programs = [s["program"] for c in cases for s in c["streams"] if "program" in s]
    failures = []
    for c in cases:
        for source in c["sources"]:
            expected = source.get("expected_native", {})
            if expected.get("compile_errors", 0):
                failures.append({"sha256": c["sha256"], "sources": [source], "stage": "compile",
                                 "reason": "recorded native compiler failure", "replay": "native-not-run",
                                 "expected_native": expected})
        for reason in c["diagnostics"]:
            failures.append({"sha256": c["sha256"], "sources": c["sources"], "stage": "container", "reason": reason})
        for layer, check in c["independent_cfb"].items():
            if check["status"] == "differs":
                failures.append({"sha256": c["sha256"], "sources": c["sources"], "stage": "container",
                                 "reason": "independent CFB reader differs", "layer": layer})
        for s in c["streams"]:
            p = s.get("program", {})
            reason = s.get("error") or s.get("parse_gap") or (
                "; ".join(p.get("diagnostics", [])) if p.get("layout") == "unsupported" else p.get("parser_reason"))
            if reason or p.get("framing_cross_check") == "differs":
                failures.append({"sha256": c["sha256"], "sources": c["sources"], "stage": "parse",
                                 "stream": s["name"], "logical_name": s["logical_name"], "reason": reason or "cross-check differs"})
            if p.get("writer_roundtrip") in ("differs", "unsupported"):
                failures.append({"sha256": c["sha256"], "sources": c["sources"], "stage": "writer",
                                 "stream": s["name"], "logical_name": s["logical_name"],
                                 "reason": p.get("writer_reason", "writer roundtrip differs")})
    totals = {}
    for c in cases:
        for key, value in c["coverage"].items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return {"schema_version": 1, "scope": "corpus observations, never universal format or production-safety claims",
            "failure_index": failures,
            "cases": cases, "summary": {
                "source_count": sum(len(c["sources"]) for c in cases), "unique_projects": len(cases),
                "program_layouts": dict(Counter(p["layout"] for p in programs)),
                "container_cross_checks": dict(Counter(v["status"] for c in cases for v in c["independent_cfb"].values())),
                "program_source_replay": dict(Counter(p["source_replay"] for p in programs)),
                "writer_roundtrips": dict(Counter(p["writer_roundtrip"] for p in programs)),
                "res_body_matches": sum(p.get("res_body_cross_check") == "byte-identical-subsequence" for p in programs),
                "coverage_totals": totals,
                "unknown_token_signatures": dict(unknown_tokens.most_common()), "abi_signatures": dict(abis.most_common()),
                "stream_families": {name: {"instances": len(rows), "projects": len({r[0] for r in rows}),
                    "unique_payloads": len({r[1] for r in rows}), "size_range": [min(r[2] for r in rows), max(r[2] for r in rows)],
                    "prefix16_clusters": dict(Counter(r[3] for r in rows)), "handling": dict(Counter(r[4] for r in rows))}
                    for name, rows in sorted(families.items())}},
            "limits": ["Raw replay is not semantic verification", "Framing cross-checks share the documented envelope hypothesis",
                       "olefile only independently checks CFB", "Unknown token signatures may include text; no guessed opcodes",
                       "No native run performed by this script; use separate hash-bound native attestations"]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")


def capture(source: Path, target: Path, *, stage: str, reason: str, copy_source=False):
    raw = source.read_bytes()
    target.mkdir(parents=True, exist_ok=False)
    stored = target / "source.gxw" if copy_source else source.resolve()
    if copy_source:
        stored.write_bytes(raw)
    case = {"schema_version": 1, "source": stored.name if copy_source else str(stored),
            "sha256": sha256(raw), "stage": stage, "reason": reason,
            "native_validation": "not_run", "observed": analyze(raw)}
    write_json(target / "case.json", case)
    return case


def replay(case_path: Path):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    source = Path(case["source"])
    if not source.is_absolute():
        source = case_path.parent / source
    raw = source.read_bytes()
    if sha256(raw) != case["sha256"]:
        raise ValueError("failure source hash changed; refusing stale replay")
    observed = analyze(raw)
    return {"sha256": case["sha256"], "stage": case["stage"],
            "baseline_coverage": case["observed"]["coverage"], "current": observed,
            "native_validation": "not_run: offline replay cannot repeat compile/reopen observations"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("scan")
    cmd.add_argument("paths", nargs="*", type=Path)
    cmd.add_argument("-o", "--output", required=True, type=Path)
    cmd = commands.add_parser("compare")
    cmd.add_argument("before", type=Path)
    cmd.add_argument("after", type=Path)
    cmd.add_argument("-o", "--output", required=True, type=Path)
    cmd = commands.add_parser("capture")
    cmd.add_argument("source", type=Path)
    cmd.add_argument("target", type=Path)
    cmd.add_argument("--stage", required=True, choices=["parse", "writer", "compile", "reopen", "semantic", "container"])
    cmd.add_argument("--reason", required=True)
    cmd.add_argument("--copy-source", action="store_true")
    cmd = commands.add_parser("replay")
    cmd.add_argument("case", type=Path)
    cmd.add_argument("-o", "--output", required=True, type=Path)
    cmd = commands.add_parser("patch-symbol", help="experimental equal-size Structured source patch")
    cmd.add_argument("source", type=Path)
    cmd.add_argument("--sha256", required=True)
    cmd.add_argument("--program", required=True)
    cmd.add_argument("--offset", type=lambda s: int(s, 0), required=True)
    cmd.add_argument("--old", required=True)
    cmd.add_argument("--new", required=True)
    cmd.add_argument("-o", "--output", required=True, type=Path)
    cmd.add_argument("--report", required=True, type=Path)
    cmd = commands.add_parser("decode-tokens", help="read the observed FX ordinary Ladder instruction/text stream")
    cmd.add_argument("source", type=Path)
    cmd.add_argument("--program", default="MAIN.Program.pou")
    cmd.add_argument("--text-encoding", default=None)
    cmd.add_argument("-o", "--output", type=Path, required=True)
    cmd.add_argument("--csv", type=Path)
    cmd = commands.add_parser("patch-constant", help="experimental source-only token constant edit")
    cmd.add_argument("source", type=Path)
    cmd.add_argument("--sha256", required=True)
    cmd.add_argument("--program", default="MAIN.Program.pou")
    cmd.add_argument("--offset", type=lambda s: int(s, 0), required=True)
    cmd.add_argument("--old", type=int, required=True)
    cmd.add_argument("--new", type=int, required=True)
    cmd.add_argument("--allow-resize", action="store_true")
    cmd.add_argument("-o", "--output", type=Path, required=True)
    cmd.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "scan":
        result = scan(args.paths or [ROOT / "research/corpus/gxw.json"])
        write_json(args.output, result)
        print(json.dumps(result["summary"], ensure_ascii=False))
    elif args.command == "compare":
        write_json(args.output, compare(args.before.read_bytes(), args.after.read_bytes()))
    elif args.command == "capture":
        capture(args.source, args.target, stage=args.stage, reason=args.reason, copy_source=args.copy_source)
    elif args.command == "decode-tokens":
        raw = args.source.read_bytes()
        image = inspect_project(raw)
        found = [s for s in image.streams if s.logical_name == args.program and s.raw is not None]
        if len(found) != 1:
            raise ValueError("program is missing or ambiguous")
        listing = decode_token_listing(found[0].raw, text_encoding=args.text_encoding)
        rows = []
        for r in listing.records:
            row = {"offset": r.tokens[0].offset, "raw_hex": "".join(t.raw.hex() for t in r.tokens)}
            if isinstance(r, TokenInstruction):
                row.update(kind="instruction", op=r.mnemonic, args=r.args, step=r.step, step_width=r.step_width)
            elif isinstance(r, TokenText):
                row.update(kind=r.role, text=r.text, step=r.step)
            elif isinstance(r, TokenLabel):
                row.update(kind="label", text=r.text, step=r.step, step_width=r.step_width)
            else:
                row.update(kind="opaque", reason=r.reason)
            rows.append(row)
        csv_raw = listing.csv_bytes(title=args.program.removesuffix(".Program.pou")) if args.csv else None
        if args.output.exists() or (args.csv and (args.csv.exists() or args.csv.resolve() == args.output.resolve())):
            raise ValueError("decode outputs must be distinct new files")
        write_json(args.output, {"source_sha256": sha256(raw), "program": args.program,
                                "records": rows, "instruction_gaps": len(listing.gaps)})
        if args.csv:
            write_new_file(args.csv, csv_raw)
    elif args.command in ("patch-symbol", "patch-constant"):
        if args.output.exists() or args.report.exists() or args.output.resolve() == args.report.resolve():
            raise ValueError("patch outputs must be distinct new paths")
        if args.command == "patch-symbol":
            result = patch_structured_symbol_equal_size(args.source.read_bytes(), expected_sha256=args.sha256,
                logical_name=args.program, node_offset=args.offset, old_symbol=args.old, new_symbol=args.new)
        else:
            result = patch_token_constant(args.source.read_bytes(), expected_sha256=args.sha256,
                logical_name=args.program, token_offset=args.offset, old_value=args.old, new_value=args.new,
                allow_resize=args.allow_resize)
        write_new_file(args.output, result.data)
        write_json(args.report, result.report)
    else:
        write_json(args.output, replay(args.case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

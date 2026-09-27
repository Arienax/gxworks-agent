"""Cross-check stored body locations against explicit compiler instance links.

DebugInformation2 and CGTable describe different parts of a compiler snapshot.
Neither file establishes source freshness. An absent body location does not
prove an unused declaration, an unvalidated body, or safe deletion. Repeated
debug elements are retained as code fragments, not counted as distinct calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.compiler_debug import parse_compiler_debug
from gxw.compiler_symbols import compiler_symbol_paths
from gxw.compiler_tables import parse_compiler_tables
from gxw.lossless import sha256


def inspect_compiled_dependencies(table_raw: bytes, debug_raw: bytes, *, encoding: str) -> dict:
    tables = parse_compiler_tables(table_raw)
    debug = parse_compiler_debug(debug_raw)
    pous = [tables.pou_at(r.table_offset) for r in tables.tables[1].records]
    path_cache = {}
    elements = []
    for index, element in enumerate(debug.elements):
        library, _, definition, owner, instance = element.names
        row = dict(index=index, offset=element.offset, sha256=sha256(element.raw),
            resource=element.resource_bytes.decode(encoding), names=[n.decode(encoding) for n in element.names],
            kind=element.kind_code, fields=list(element.fields), raw_hex=element.raw.hex(),
            candidates=[], target_pou_offset=None)
        elements.append(row)
        if library:
            row["handling"] = "external-library-body"
            continue
        roots = [p for p in pous if p.name_bytes == owner]
        if not roots:
            row["handling"] = "unresolved-owner"
            continue
        if not instance:
            candidates = [dict(root_pou_offset=p.record.table_offset,
                component_offsets=[], target_pou_offset=p.record.table_offset,
                definition_agrees=p.name_bytes == definition) for p in roots]
        else:
            parts = tuple(instance.split(b"."))
            if (any(not part for part in parts)
                    or any(c in instance for c in (b"[", b"]", b":", b"\\"))):
                row["handling"] = "unsupported-instance-path"
                continue
            candidates = []
            # Global instances can supply the first name. Keep aliases from a
            # local external component and @GLOBALS; compare resolved targets.
            for root in roots + [p for p in pous if p.name_bytes == b"@GLOBALS"]:
                root_offset = root.record.table_offset
                if root_offset not in path_cache:
                    path_cache[root_offset] = compiler_symbol_paths(tables, root_offset)
                for path in path_cache[root_offset]:
                    if path.names[1:] != parts:
                        continue
                    target = tables.component_instance_pou_offset(path.symbol.component)
                    candidates.append(dict(root_pou_offset=root_offset,
                        component_offsets=list(path.component_offsets), target_pou_offset=target,
                        definition_agrees=(tables.pou_at(target).name_bytes == definition
                                           if target is not None else False)))
        row["candidates"] = candidates
        targets = {c["target_pou_offset"] for c in candidates}
        if not candidates:
            row["handling"] = "unresolved-instance-path"
        elif any(not c["definition_agrees"] for c in candidates):
            row["handling"] = "instance-definition-mismatch"
        elif len(targets) != 1:
            row["handling"] = "ambiguous-instance-target"
        else:
            row["handling"] = "explicit-reference-agrees"
            row["target_pou_offset"] = next(iter(targets))
    return dict(compiler_table_sha256=sha256(table_raw), debug_sha256=sha256(debug_raw),
        reconstruction=dict(compiler_tables="byte-identical" if tables.reconstruct() == table_raw else "differs",
                            debug="byte-identical" if debug.reconstruct() == debug_raw else "differs"),
        encoding=encoding, elements=elements,
        summary=dict(elements=len(elements), handling=dict(Counter(e["handling"] for e in elements)),
            resolved_pou_offsets=sorted({e["target_pou_offset"] for e in elements if e["target_pou_offset"] is not None})),
        source_freshness="not-established", complete_dependency_proof=False,
        scope="Stored body fragments versus scalar FB instance references. No complete call count, source validation, runtime reachability or safe-deletion inference.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler_table", type=Path)
    parser.add_argument("debug", type=Path)
    parser.add_argument("--encoding", required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_compiled_dependencies(args.compiler_table.read_bytes(), args.debug.read_bytes(), encoding=args.encoding)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result["summary"]))

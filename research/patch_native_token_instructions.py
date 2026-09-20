"""Encode only requested instructions or record splices, retaining source context.

The remaining source is never re-encoded through a textual representation.
Native conversion evidence is separate from actual project conversion/reopen.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from native_gxw_tokens import native_batch, native_il_records, listing_records, canonical_record
from native_gxw_roundtrip import require_machinecode_roundtrip
from gxw.lossless import sha256
from gxw.project_writer import write_new_file
from gxw.token_patch import TokenInstructionPatch, patch_token_instructions, TokenRecordSplice, splice_token_records
from gxw.token_pou import parse_token_region
from gxw.token_listing import decode_token_program


def patch(source, plan, directory):
    if sha256(source) != plan["source_sha256"]:
        raise ValueError("source identity changed")
    operation = plan.get("operation", "replace_instructions")
    if operation not in ("replace_instructions", "splice_records"):
        raise ValueError("unknown patch operation")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    requests = []
    for edit in plan["edits"]:
        lines = edit["instructions"] if operation == "splice_records" else [edit["instruction"]]
        if (not isinstance(lines, list) or any(not isinstance(line, str) or not line.strip()
                or any(c in line for c in "\0\r\n") or line.strip().upper() == "END" for line in lines)):
            raise ValueError("provide nonempty single-line instructions; the existing END is preserved")
        requests.append({"mode": "encode", "text": lines,
                         "input_base64": base64.b64encode(("\0".join(lines + ["END"]) + "\0\0").encode("cp936")).decode()})
    encoded = native_batch(requests, directory / "encode")
    if any(r["return_code"] != "0x00000000" for r in encoded):
        raise ValueError("native encoder rejected a replacement; inputs and errors retained")
    require_machinecode_roundtrip([base64.b64decode(r["output_base64"]) for r in encoded], directory / "machinecode-roundtrip")
    decoded = native_batch([{"mode": "decode", "encoder_id": r["id"],
                            "input_base64": base64.b64encode(base64.b64decode(r["output_base64"]) + b"\0").decode()}
                           for r in encoded], directory / "decode")
    edits, expectations = [], []
    for edit, encoded_row, decoded_row in zip(plan["edits"], encoded, decoded):
        body = base64.b64decode(encoded_row["output_base64"])
        listing = decode_token_program(parse_token_region(body, 0, len(body)), text_encoding="cp936")
        if decoded_row["return_code"] != "0x00000000" or decoded_row["consumed_bytes"] != len(body):
            raise ValueError("native replacement decode was incomplete")
        native = native_il_records(base64.b64decode(decoded_row["output_base64"]))
        if [canonical_record(r) for r in listing_records(listing)] != [canonical_record(r) for r in native]:
            raise ValueError("Python and native replacement decoding disagree")
        patch_type = TokenRecordSplice if operation == "splice_records" else TokenInstructionPatch
        edits.append(patch_type(edit["offset"], bytes.fromhex(edit["expected_raw_hex"]), body[:-3]))
        expectations.append({"requested": edit["instructions"] if operation == "splice_records" else edit["instruction"], "native_records": native})
    apply = splice_token_records if operation == "splice_records" else patch_token_instructions
    result = apply(source, expected_sha256=plan["source_sha256"], logical_name=plan["program"],
                   edits=tuple(edits), text_encoding="cp936")
    (directory / "patch.json").write_text(json.dumps(dict(result.report, native_replacement_readings=expectations),
                                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    result = patch(args.source.read_bytes(), json.loads(args.plan.read_text(encoding="utf-8")), args.evidence_dir)
    write_new_file(args.output, result.data)
    print(json.dumps({"output_sha256": sha256(result.data), "edits": len(result.report["edits"]),
                      "allocations": result.report["allocations"], "native_project_conversion": "not_run"}))

"""Infer token spellings by aligning complete native CSV exports with GXW bytes.

CSV is the separate GX Works2 decoder, not output from our parser. The alignment
uses operands, order, comments and every native step offset; ambiguous/mismatched
pairs are retained as conflicts, never silently turned into opcode mappings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container import CompoundFile
from gxw.project_metadata import logical_mapping
from gxw.token_pou import parse_token_pou
from gxw.token_listing import _operand_groups


def read_csv(raw):
    instructions, texts = [], []
    rows = list(csv.reader(io.StringIO(raw.decode("utf-16")), delimiter="\t"))
    for row in rows[3:]:
        if not row:
            continue
        row += [""] * (7 - len(row))
        if row[2]:
            instructions.append({"step": int(row[0]), "opcode": row[2], "operands": shlex.split(row[3], posix=False)})
        elif row[3]:
            if not instructions:
                raise ValueError("CSV operand without an instruction")
            instructions[-1]["operands"].extend(shlex.split(row[3], posix=False))
        if row[1]:
            texts.append({"kind": "statement", "text": row[1], "before_instruction": len(instructions)})
        if row[6]:
            texts.append({"kind": "note", "text": row[6], "after_instruction": len(instructions) - 1})
    return instructions, texts


def canonical_operand(text):
    match = re.fullmatch(r"(K[1-8])?([XY])([0-7]+)([ZV][0-7])?", text)
    if match:
        return (match[1] or "") + match[2] + format(int(match[3], 8), "o") + (match[4] or "")
    if re.fullmatch(r"H[0-9A-F]+", text):
        return "H" + format(int(text[1:], 16), "X")
    return text


def align(project_raw, csv_raw, program_name="MAIN.Program.pou", encoding="cp936"):
    outer = CompoundFile(project_raw)
    nested = CompoundFile(outer.read_stream("_hdb"))
    mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
    raw = nested.read_stream(mapping[program_name])
    program = parse_token_pou(raw)
    native, native_texts = read_csv(csv_raw)
    groups, texts, conflicts = [], [], []
    for token in program.tokens:
        t = token.raw
        if t[1] in (0x80, 0x82) and len(t) >= 4 and t[2] == 0:
            text = t[3:-1].decode(encoding)
            texts.append({"kind": "statement" if t[1] == 0x80 else "note", "text": text,
                          "before_instruction" if t[1] == 0x80 else "after_instruction": len(groups) if t[1] == 0x80 else len(groups) - 1})
        elif token.annotation()["kind"] in ("operand", "operand-modifier"):
            if not groups:
                raise ValueError("binary operand without a header")
            groups[-1]["operand_tokens"].append(token)
        elif t == b"\x02\x02" or (3 <= len(t) <= 6 and t[1] < 0x80):
            groups.append({"offset": token.offset, "token": t.hex(), "operand_tokens": [],
                           "is_label": token.annotation()["kind"] == "label-header",
                           "encoded_steps": 1 if len(t) <= 3 else t[2]})
        else:
            conflicts.append({"offset": token.offset, "reason": "unclassified token", "raw": t.hex()})
    if len(groups) != len(native):
        conflicts.append({"reason": "instruction count mismatch", "binary": len(groups), "native": len(native)})
    step, mappings, labels = 0, defaultdict(list), []
    for index, (group, row) in enumerate(zip(groups, native)):
        # The CSV is the independent oracle. Binary operand projection shares
        # the Core grammar; this is not a second independent binary decoder.
        try:
            group["operands"] = [canonical_operand(o.text) for o in _operand_groups(tuple(group["operand_tokens"]), encoding)]
        except ValueError as exc:
            group["operands"] = []
            conflicts.append({"index": index, "reason": "operand grammar gap", "detail": str(exc)})
        expected = [canonical_operand(x) for x in row["operands"]]
        if group["is_label"]:
            label = group["operands"][0] if len(group["operands"]) == 1 else None
            if label != row["opcode"] or expected:
                conflicts.append({"index": index, "reason": "label mismatch", "binary": label, "native": row})
            labels.append({"offset": group["offset"], "text": label, "step": step, "width": group["encoded_steps"]})
            group["operands"] = []
        if group["operands"] != expected:
            conflicts.append({"index": index, "reason": "operand mismatch", "binary": group["operands"], "native": expected})
        if row["step"] != step:
            conflicts.append({"index": index, "reason": "step mismatch", "binary": step, "native": row["step"]})
        step += group["encoded_steps"]
        if not group["is_label"]:
            mappings[group["token"]].append({"opcode": row["opcode"], "offset": group["offset"],
                                            "native_step": row["step"], "operands": expected})
    if texts != native_texts:
        conflicts.append({"reason": "text content/placement mismatch", "binary_count": len(texts), "native_count": len(native_texts)})
    for token, examples in mappings.items():
        if len({e["opcode"] for e in examples}) != 1:
            conflicts.append({"token": token, "reason": "one token maps to multiple mnemonics"})
    return {"project_sha256": hashlib.sha256(project_raw).hexdigest(), "csv_sha256": hashlib.sha256(csv_raw).hexdigest(),
            "program_sha256": hashlib.sha256(raw).hexdigest(), "program": program_name,
            "instructions": len(groups) - len(labels), "labels": labels, "texts": len(texts), "encoded_steps": step,
            "all_native_steps_agree": not any(c["reason"] == "step mismatch" for c in conflicts),
            "status": "agrees" if not conflicts else "conflicts", "conflicts": conflicts,
            "mappings": dict(mappings), "method": "full sequence/operand/step/text alignment against separately supplied GX Works2 CSV"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--program", default="MAIN.Program.pou")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    result = align(args.project.read_bytes(), args.csv.read_bytes(), args.program)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "mappings"}, ensure_ascii=False))

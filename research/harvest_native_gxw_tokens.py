"""Generate instruction candidates from installed vendor metadata, then query
the offline native encoder/decoder. Results are conversion evidence, not proof
of valid PLC programs. Rejections and undecoded grammar are retained verbatim.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct

from native_gxw_tokens import native_batch, native_il_records, canonical_record, ROOT, DEFAULT_DLL, DLL_SHA256
from gxw.lossless import sha256
from gxw.token_listing import decode_token_listing
from gxw.token_pou import parse_token_pou


def read_table(path, query):
    import win32com.client  # optional, research-only Windows ADO reader
    connection = win32com.client.Dispatch("ADODB.Connection")
    connection.Mode = 1
    connection.Open("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" + str(path) + ";Mode=Read;")
    try:
        rows = connection.Execute(query)[0]
        result = []
        while not rows.EOF:
            result.append({f.Name: f.Value for f in rows.Fields})
            rows.MoveNext()
        return result
    finally:
        connection.Close()


def candidate_requests(metadata, operands, model):
    names = {m["Symbol"] for m in model}
    by_id = defaultdict(list)
    for row in operands:
        by_id[row["CpuInstruction ID"]].append(row)
    requests, skipped = [], []
    for instruction in metadata:
        symbol = instruction["Symbol"]
        if symbol not in names or not instruction["IL"]:
            continue
        definitions = sorted(by_id[instruction["CpuInstruction ID"]], key=lambda r: r["ArgNumber"])
        count = instruction["Valid Operand Count"]
        if len(definitions) != count or [r["ArgNumber"] for r in definitions] != list(range(1, count + 1)):
            skipped.append({"symbol": symbol, "reason": "variable or nonsequential operand metadata"})
            continue
        for strategy in ("device", "constant"):
            arguments = []
            for index, row in enumerate(definitions):
                preferences = (["K", "H"] if strategy == "constant" else []) + ["D", "M", "S", "Y", "X", "TN", "CN", "TS", "CS", "K", "H", "P", "I", "Z", "V"]
                selected = next((p for p in preferences if row.get(p)), None)
                if selected is None:
                    break
                prefix = {"TN": "T", "TS": "T", "CN": "C", "CS": "C"}.get(selected, selected)
                value = 1 if prefix in ("K", "H") else 10 * (index + 1) if prefix in ("D", "M", "S") else 0
                arguments.append(prefix + str(value))
            if len(arguments) != count:
                skipped.append({"symbol": symbol, "reason": "operand metadata requires an unsupported candidate form"})
                continue
            for name in [symbol] + ([symbol + "P"] if instruction["Pulse"] and symbol + "P" in names else []):
                text = " ".join([name] + arguments)
                if any(r["text"] == text for r in requests):
                    continue
                payload = text.encode("ascii") + b"\0" + (b"" if name == "END" else b"END\0") + b"\0"
                requests.append({"mode": "encode", "input_base64": base64.b64encode(payload).decode(),
                                 "text": text, "expected": {"kind": "instruction", "op": name, "args": arguments},
                                 "metadata_id": instruction["CpuInstruction ID"], "strategy": strategy})
    return requests, skipped


def envelope(body):
    prefix = bytearray(79)
    struct.pack_into("<II", prefix, 55, len(body) + 20, len(body) + 20)
    return bytes(prefix) + body + bytes(24)


def observed_catalog(result):
    """Exact, conflict-free native encodings; never extrapolate a bit formula."""
    table = {}
    for case in result["cases"]:
        if not case.get("native_text_agrees") or "header_hex" not in case:
            continue
        record = case["native_records"][0]
        value = [record["op"], len(record["args"])]
        key = case["header_hex"]
        if key in table and table[key] != value:
            raise ValueError("native header has conflicting opcode/arity observations: " + key)
        table[key] = value
    return {"schema_version": 1, "evidence": "observed native memory conversion, not project compile or execution",
            "dll_sha256": result["dll_sha256"], "cpu_code": result["cpu_code"],
            "reproducer": "research/harvest_native_gxw_tokens.py", "opcodes": dict(sorted(table.items()))}


def harvest(database, directory, *, dll=DEFAULT_DLL):
    database = database.resolve()
    instruction_db, model_db = database / "InstructionFX_E.mdb", database / "CpuFX3UC_E.mdb"
    metadata = read_table(instruction_db, "SELECT [CpuInstruction ID], [Symbol], [Pulse], [Valid Operand Count], [IL] FROM TCpuInstruction")
    operands = read_table(instruction_db, "SELECT * FROM TValidDevice")
    model = read_table(model_db, "SELECT [Symbol], [Valid Operand Count] FROM TCpuInstruction")
    requests, skipped = candidate_requests(metadata, operands, model)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "metadata.json").write_text(json.dumps({"instructions": metadata, "operands": operands, "model": model,
        "hashes": {p.name: sha256(p.read_bytes()) for p in (instruction_db, model_db)}}, indent=2) + "\n", encoding="utf-8")
    encoded = native_batch(requests, directory / "encode", dll=dll)
    to_decode = [{"mode": "decode", "input_base64": base64.b64encode(base64.b64decode(r["output_base64"]) + b"\0").decode(),
                  "encoder_id": r["id"]} for r in encoded if r["return_code"] == "0x00000000"]
    decoded = native_batch(to_decode, directory / "decode", dll=dll)
    native = {request["encoder_id"]: response for request, response in zip(to_decode, decoded)}
    cases = []
    for request, row in zip(requests, encoded):
        case = {"id": row["id"], "text": request["text"], "encode_result": row["return_code"]}
        if row["return_code"] == "0x00000000":
            body = base64.b64decode(row["output_base64"])
            case["body_hex"] = body.hex()
            reply = native[row["id"]]
            case["decode_result"] = reply["return_code"]
            if reply["return_code"] == "0x00000000":
                records = native_il_records(base64.b64decode(reply["output_base64"])); case["native_records"] = records
                expected = [request["expected"]] + ([] if request["expected"]["op"] == "END" else [{"kind": "instruction", "op": "END", "args": []}])
                case["native_text_agrees"] = [canonical_record(r) for r in records] == [canonical_record(r) for r in expected]
            try:
                raw = envelope(body); program = parse_token_pou(raw); listing = decode_token_listing(raw)
                case["header_hex"] = program.tokens[0].raw.hex()
                case["our_gaps"] = len(listing.gaps)
                case["our_instruction_ir"] = listing.instruction_ir() if not listing.gaps else None
            except ValueError as exc:
                case["framing_gap"] = str(exc)
        cases.append(case)
    result = {"dll_sha256": DLL_SHA256, "cpu_code": 0x208, "scope": "native memory conversion only; generated snippets are not compiled GXW projects",
              "cases": cases, "skipped": skipped, "summary": {"requests": len(requests),
                "encode_return_codes": dict(Counter(c["encode_result"] for c in cases)),
                "native_text_agrees": sum(c.get("native_text_agrees", False) for c in cases),
                "python_complete": sum(c.get("our_gaps") == 0 for c in cases),
                "framing_gaps": sum("framing_gap" in c for c in cases)}}
    (directory / "harvest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("D:/GXWORKS2/Easysocket/ProductDataBase"))
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(harvest(args.database, args.output, dll=args.dll)["summary"]))

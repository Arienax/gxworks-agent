"""Controlled offline operand probes against the hash-pinned vendor converter.

Each input and rejection is retained. Encoder acceptance is not PLC validation;
native decoder output is kept even when it changes or truncates the input.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from native_gxw_tokens import native_batch, native_il_records, DLL_SHA256
from harvest_native_gxw_tokens import envelope
from gxw.token_pou import parse_token_pou


def candidates():
    texts = []
    for op in ("MOV", "DMOV"):
        for value in (-2147483648, -65536, -32769, -32768, -256, -128, -1,
                      0, 1, 127, 128, 255, 256, 32767, 32768, 65535,
                      65536, 16777215, 16777216, 2147483647, 2147483648):
            texts.append(f"{op} K{value} D100")
        for value in (0, 1, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000,
                      0xFFFF, 0x10000, 0xFFFFFF, 0x1000000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
            texts.append(f"{op} H{value:X} D100")
        for base in ("M0", "M255", "M256", "M8000", "X0", "X10", "Y0", "S0"):
            for count in range(1, 9):
                texts.append(f"{op} K{count}{base} D100")
                texts.append(f"{op} D100 K{count}{base}")
        for prefix in ("D", "T", "C", "V", "Z"):
            for number in (0, 1, 7, 8, 255, 256):
                texts.append(f"{op} {prefix}{number} D100")
        for base in ("D0", "D255", "D256", "T0", "C0", "K4M0", "K4X0", "K8Y0"):
            for index in ("Z0", "Z1", "Z7", "V0", "V1", "V7"):
                texts.append(f"{op} {base}{index} D100")
        for bit in ("0", "1", "A", "F", "15", "16"):
            texts.extend((f"{op} D10.{bit} D100", f"LD D10.{bit}"))
    for op in ("DEMOV", "DEADD"):
        for value in ("0", "1", "1.0", "-1.0", "1.5", "-2.5", "3.1415926",
                      "1.23456789", "1.0E+10", "1.0E-10", "-1.234E-20",
                      "3.402823E+38", "1.1754944E-38"):
            texts.append(f"{op} E{value} D100" + (" D110" if op == "DEADD" else ""))
    for value in ("", "A", "AB", "ABC", "ABCD", "A B", "abc_xyz", "1234567890",
                  "中文", "分拣程序", "A中文B", "\\", 'A"B'):
        texts.append(f'$MOV "{value}" D100')
    for op in ("CJ", "CALL"):
        for value in (0, 1, 127, 128, 255, 256, 32767):
            texts.append(f"{op} P{value}")
    return list(dict.fromkeys(texts))


def probe(texts, directory):
    directory.mkdir(parents=True, exist_ok=False)
    requests = [{"mode": "encode", "text": text,
                 "input_base64": base64.b64encode(text.encode("cp936") + b"\0END\0\0").decode()}
                for text in texts]
    encoded = native_batch(requests, directory / "encode")
    dec_requests = [{"mode": "decode", "encoder_id": row["id"],
                     "input_base64": base64.b64encode(base64.b64decode(row["output_base64"]) + b"\0").decode()}
                    for row in encoded if row["return_code"] == "0x00000000"]
    decoded = native_batch(dec_requests, directory / "decode")
    replies = {req["encoder_id"]: row for req, row in zip(dec_requests, decoded)}
    cases = []
    for request, row in zip(requests, encoded):
        case = {"id": row["id"], "text": request["text"], "encode_result": row["return_code"]}
        if row["return_code"] == "0x00000000":
            body = base64.b64decode(row["output_base64"])
            case["body_hex"] = body.hex()
            reply = replies[row["id"]]
            case["decode_result"] = reply["return_code"]
            if reply["return_code"] == "0x00000000":
                case["native_records"] = native_il_records(base64.b64decode(reply["output_base64"]))
                case["native_consumed_all"] = reply["consumed_bytes"] == len(body)
            try:
                case["tokens"] = [{"raw": t.raw.hex(), "annotation": t.annotation()}
                                  for t in parse_token_pou(envelope(body)).tokens]
            except ValueError as exc:
                case["framing_error"] = str(exc)
        cases.append(case)
    result = {"dll_sha256": DLL_SHA256, "cpu_code": 0x208, "encode_option1": 1,
              "scope": "offline conversion only; no project compilation or device access",
              "cases": cases, "summary": {
                  "requests": len(cases), "encode_results": dict(Counter(c["encode_result"] for c in cases)),
                  "decode_results": dict(Counter(c.get("decode_result", "not-run") for c in cases)),
                  "framing_errors": sum("framing_error" in c for c in cases)}}
    (directory / "probes.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instructions", type=Path, help="one CP936-representable instruction per UTF-8 line")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    texts = args.instructions.read_text(encoding="utf-8").splitlines() if args.instructions else candidates()
    print(json.dumps(probe(texts, args.output)["summary"]))

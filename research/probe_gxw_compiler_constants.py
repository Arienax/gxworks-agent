"""Raw-preserved CGTable constants, checked against the native CstLine reader.

Physical widths are from ECCompiler 15.22 A550 / ECCompiler_IEC A3C0.
Spelling is not a numeric decoder: compiler flags and array initializers also
occupy this table. Uninterpreted value kinds retain their bytes and a gap.
"""
from __future__ import annotations

import base64
import math
import re
import struct


WIDTHS = {0: 4, 1: 1, 2: 1, 3: 4, 4: 2, 5: 4, 6: 4,
          7: 8, 8: 8, 9: 4, 10: 8, 11: 12, 12: 4, 13: 8, 14: 12,
          15: 0, 255: 0}
INTEGER_SIGNED = {1: True, 2: False, 3: True, 4: False, 5: True, 6: False}


def integer_operand(view, *, negate=False):
    """Observed Q scalar rendering; never parse the spelling as the value.

    Native ECCompiler_IEC A63B0 selects H for all three radix prefixes.
    Unary minus is a separate generated-source expression, not a CstLine
    field. Radix-prefixed unary negation is intentionally left unresolved.
    """
    value, spelling = view.get("literal_integer"), view.get("spelling_ascii")
    if value is None or spelling is None:
        return None
    if re.match(r"^(16|8|2)#", spelling):
        if negate or not 0 <= value <= 0xffffffff:
            return None
        digits = format(value, "X")
        return "H" + ("0" if digits[0] in "ABCDEF" else "") + digits
    if not re.fullmatch(r"-?[0-9]+", spelling):
        return None
    return "K" + str(-value if negate else value)


def constant_record(record):
    raw = record.payload
    result = dict(table_offset=record.table_offset, stream_offset=record.offset,
                  raw_base64=base64.b64encode(record.raw).decode(),
                  handling="opaque-preserved", gaps=[])
    if len(raw) < 3:
        result["gaps"].append("constant header truncated")
        return result
    kind, size = raw[0], struct.unpack_from("<H", raw, 1)[0]
    result.update(kind=kind, spelling_byte_length=size)
    end = 3 + size
    if end > len(raw):
        result["gaps"].append("constant spelling extends beyond record")
        return result
    spelling = raw[3:end]
    result.update(spelling_base64=base64.b64encode(spelling).decode(),
                  spelling_ascii=spelling.decode("ascii") if spelling.isascii() else None)
    role = ("compiler-marker" if spelling.startswith(b"@@") else
            "initializer-expression" if spelling.startswith(b"[") else "literal")
    result["spelling_role"] = role
    if kind not in WIDTHS:
        result["gaps"].append("uninspected constant value kind")
        return result
    width = WIDTHS[kind]
    if end + width + 4 > len(raw):
        result["gaps"].append("constant typed value/reference count truncated")
        return result
    value = raw[end:end + width]
    result.update(handling="framed-known-width", value_raw_hex=value.hex(),
                  reference_count=struct.unpack_from("<I", raw, end + width)[0],
                  opaque_tail_hex=raw[end + width + 4:].hex())
    if kind in INTEGER_SIGNED:
        result["stored_integer"] = int.from_bytes(value, "little", signed=INTEGER_SIGNED[kind])
        if role == "literal":
            result["literal_integer"] = result["stored_integer"]
    elif kind == 0:
        result["stored_bit_value"] = int.from_bytes(value, "little")
    elif kind in (9, 10):
        number = struct.unpack("<f" if kind == 9 else "<d", value)[0]
        result.update(real_encoding="binary32" if kind == 9 else "binary64",
                      stored_real_hex=number.hex(), stored_real=number if math.isfinite(number) else None)
        if not math.isfinite(number):
            result["gaps"].append("nonfinite real semantics not validated")
    elif kind == 11:
        days, milliseconds = struct.unpack("<id", value)
        result.update(stored_days=days, stored_day_milliseconds_hex=milliseconds.hex(),
                      stored_day_milliseconds=milliseconds if math.isfinite(milliseconds) else None)
        if math.isfinite(milliseconds):
            result["duration_milliseconds"] = days * 86400000 + milliseconds
            result["duration_handling"] = "mathematical stored duration; target range and lowering precision are separate"
        else:
            result["gaps"].append("nonfinite duration semantics not validated")
    elif kind not in (15, 255):
        result["gaps"].append("typed payload semantics remain opaque")
    if result["opaque_tail_hex"]:
        result["gaps"].append("uninterpreted trailing UserInfo")
    return result


def compare_constant_reads(raw, rows):
    from gxw.compiler_tables import parse_compiler_tables
    table = parse_compiler_tables(raw).tables[5]
    views = {r.table_offset: constant_record(r) for r in table.records}
    mismatches = []
    for row in rows:
        offset = row["requested_offset"]
        view = views[offset]
        record = table.record_at(offset)
        native_value = base64.b64decode(row["value_base64"], validate=True)
        value = bytes.fromhex(view["value_raw_hex"])
        checks = dict(kind=row["kind"] == view["kind"],
                      spelling=row["spelling_base64"] == view["spelling_base64"],
                      typed_value=native_value[:len(value)] == value,
                      reference_count=row["reference_count"] == view["reference_count"],
                      next_offset=row["next_offset"] == offset + len(record.raw))
        mismatches.extend(dict(offset=offset, field=key) for key, equal in checks.items() if not equal)
    return dict(constant_reads_complete=list(views) == [r["requested_offset"] for r in rows]
                and all(r["return_code"] == 1 for r in rows),
                constant_count=len(rows), constant_field_mismatches=mismatches)

"""Lossless framing and lexical decoding of ordinary FX Ladder Program.pou.

Facts come from FX3U controls, full native CSV alignments and hash-bound native
converter observations. Lexical annotations do not prove execution semantics.
Unknown opcodes, text encodings, modifiers and operands remain opaque tokens.
Native CSV alignment establishes the extended headers and text roles below;
source and compiled .res can duplicate this body.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

from .models import GXWFormatError
from plc.instruction_steps import native_lexical_overrides, native_token_encodings


# Exact observations from the native converter. A subset also has full native
# project/CSV checks. This is binary grammar, not PLC execution support.
_NATIVE_ENCODINGS = native_token_encodings()


def _opcode_family_key(raw: bytes) -> tuple[int, ...] | None:
    # Converter 15.31 RVA 0x3D9D5 dispatches on byte 1, byte 3 (if
    # present), and byte 4 (if present). Byte 2 is the stored step width.
    # Keep header shapes separate for operand binding, even when the vendor
    # prints the same name: it also accepts a one-operand three-byte OUT-T/C
    # fragment, whereas the four-byte OUT-T/C form carries the preset operand.
    # Preserve application/pulse/comparison modifiers in the identity.
    # Recognition is lexical: neither a familiar family nor native decoding
    # proves that the stored width agrees with these operands.
    if (len(raw) not in (3, 4, 5, 6) or raw[0] != len(raw)
            or raw[-1] != len(raw) or raw[1] >= 0x80
            # GetStepSize uses signed byte extension (RVA 0x18D08).
            # Zero/negative widths are retained, not projected as valid steps.
            or (len(raw) >= 4 and not 1 <= raw[2] <= 127)):
        return None
    return (len(raw), raw[1], *raw[3:-1])


_NATIVE_FAMILIES: dict[tuple[int, ...], tuple[str, int]] = {}
_AMBIGUOUS_FAMILIES: set[tuple[int, ...]] = set()
for _header, _definition in _NATIVE_ENCODINGS.items():
    _family = _opcode_family_key(bytes.fromhex(_header))
    if _family is not None:
        _value = tuple(_definition)
        if _family in _NATIVE_FAMILIES and _NATIVE_FAMILIES[_family] != _value:
            _AMBIGUOUS_FAMILIES.add(_family)
        _NATIVE_FAMILIES[_family] = _value
for _family in _AMBIGUOUS_FAMILIES:
    _NATIVE_FAMILIES.pop(_family, None)
# Existing native project/CSV evidence for 04 21 03 04, plus the indexed
# 04 21 04 04 control and vendor descriptor RVA 0x82E84 (operand count 2).
# Do not inherit the one-operand observation of the three-byte 03 21 03 form.
_NATIVE_FAMILIES[(4, 0x21)] = ("OUT", 2)


def _native_opcode(raw: bytes):
    return _NATIVE_ENCODINGS.get(raw.hex()) or _NATIVE_FAMILIES.get(_opcode_family_key(raw))


def observed_native_arity(raw: bytes) -> int | None:
    item = _native_opcode(raw)
    return item[1] if item else None


# Exact observed spellings only. In particular do not infer application opcodes
# from FNC arithmetic, or the unobserved pulse/width variants of basic opcodes.
_OPCODES = native_lexical_overrides()
_DEVICES = {0x90: "M", 0x98: "S", 0x9C: "X", 0x9D: "Y", 0xA8: "D", 0xC2: "T", 0xC5: "C",
            0xCC: "Z", 0xCD: "V", 0xD0: "P", 0xD1: "I"}


@dataclass(frozen=True)
class LadderToken:
    offset: int
    raw: bytes

    def annotation(self) -> dict:
        """Lexical observations only; no operand role or arity is inferred."""
        native = _native_opcode(self.raw)
        mnemonic = _OPCODES.get(self.raw.hex()) or (native[0] if native else None)
        if mnemonic:
            return {"kind": "opcode", "mnemonic": mnemonic}
        if self.raw in (b"\x03\x3c\x03", b"\x04\x3c\x02\x04"):
            return {"kind": "label-header"}
        code, value = self.raw[1], self.raw[2:-1]
        if code in (0x80, 0x82) and len(self.raw) >= 4 and self.raw[2] == 0:
            return {"kind": "text", "role": "statement" if code == 0x80 else "note",
                    "payload_hex": self.raw[3:-1].hex()}
        # Only widths observed by the controlled corpus. A longer token with a
        # familiar type byte might be a modifier, not a plain device address.
        if code in _DEVICES and 1 <= len(value) <= (1 if code in (0xCC, 0xCD, 0xD1) else 2):
            return {"kind": "operand", "device_type": _DEVICES[code],
                    "numeric_value": int.from_bytes(value, "little")}
        if code in (0xE8, 0xE9, 0xEA, 0xEB):
            width = 4 if code in (0xE9, 0xEB) else 2
            if 1 <= len(value) <= width:
                return {"kind": "operand", "constant_type": "H" if code in (0xEA, 0xEB) else "K",
                        "width_bits": width * 8,
                        "numeric_value": int.from_bytes(value, "little", signed=(
                            code in (0xE8, 0xE9) and len(value) == width))}
        if code in (0xEC, 0xED) and len(value) == 4:
            number = struct.unpack("<f", value)[0]
            if math.isfinite(number):
                return {"kind": "operand", "constant_type": "E", "numeric_value": number,
                        "notation": "decimal" if code == 0xEC else "scientific", "width_bits": 32}
        if code == 0xEE and 1 <= len(value) <= 32:
            return {"kind": "operand", "constant_type": "string", "payload_hex": value.hex()}
        if len(value) == 1:
            role = {0xF0: "index_z", 0xF1: "digit_group", 0xF2: "bit_select", 0xF4: "index_v"}.get(code)
            bounds = {"index_z": (0, 7), "index_v": (0, 7), "digit_group": (1, 8), "bit_select": (0, 15)}
            if role and bounds[role][0] <= value[0] <= bounds[role][1]:
                return {"kind": "operand-modifier", "role": role, "numeric_value": value[0]}
        return {"kind": "opaque", "type_byte": code}


@dataclass(frozen=True)
class TokenProgram:
    raw: bytes
    tokens: tuple[LadderToken, ...]
    body_offset: int = 79
    body_length: int | None = None

    @property
    def body_end(self) -> int:
        return len(self.raw) - 24 if self.body_length is None else self.body_offset + self.body_length

    @property
    def body(self) -> bytes:
        return self.raw[self.body_offset:self.body_end]

    def reconstruct(self) -> bytes:
        return self.raw[:self.body_offset] + b"".join(token.raw for token in self.tokens) + self.raw[self.body_end:]


def parse_token_pou(raw: bytes) -> TokenProgram:
    """Recognize the observed envelope; never search past a broken boundary.

    Matching lengths alone do not establish a format. Require both size fields,
    the exact trailer, complete consumption, and the observed terminal END.
    Unknown layouts must be preserved by the caller as a whole opaque stream.
    """
    raw = bytes(raw)
    if len(raw) < 106 or any(raw[-24:]):
        raise GXWFormatError("unsupported token Program.pou envelope/trailer")
    if struct.unpack_from("<II", raw, 55) != (len(raw) - 83,) * 2:
        raise GXWFormatError("unsupported token Program.pou size fields")
    return parse_token_region(raw, 79, len(raw) - 103)


def parse_token_region(raw: bytes, offset: int, length: int) -> TokenProgram:
    """Frame a complete code region, including its observed terminal END."""
    program = parse_token_fragment(raw, offset, length)
    if not program.tokens or program.tokens[-1].raw != b"\x03\x34\x03":
        raise GXWFormatError("token Program.pou lacks the observed terminal END")
    return program


def parse_token_fragment(raw: bytes, offset: int, length: int) -> TokenProgram:
    """Frame an explicitly bounded compiler fragment without inventing END.

    Compiled POU/FB fragments can end in SRET or at a link boundary. This
    framing-only entry point does not recognize a complete program envelope;
    source POU and complete .res readers retain their terminal-END requirement.
    All offsets and the reconstruction remain bound to the original stream.
    """
    raw = bytes(raw)
    if offset < 0 or length < 0 or offset + length > len(raw):
        raise GXWFormatError("token region is outside its source stream")
    cursor, end, tokens = offset, offset + length, []
    while cursor < end:
        size = raw[cursor]
        if size < 2 or cursor + size > end or raw[cursor + size - 1] != size:
            raise GXWFormatError(f"broken token boundary at 0x{cursor:X}")
        tokens.append(LadderToken(cursor, raw[cursor:cursor + size]))
        cursor += size
    return TokenProgram(raw, tuple(tokens), offset, length)

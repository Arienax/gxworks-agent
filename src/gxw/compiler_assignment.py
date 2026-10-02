"""Preserved compiler allocations, distinct from declared addresses.

The compact and simple inline forms are independently formatted by ECCompiler
15.22 RVA 0x51650. Inline constants are operands, not device allocations.
An allocation is stored compiler state, not proof that current source uses it.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError


@dataclass(frozen=True)
class CompilerAssignment:
    raw: bytes
    status: str
    device_family: str | None = None
    number: int | None = None
    role: str | None = None
    reserved_count: int | None = None
    iec_address: str | None = None
    constant_value: int | None = None

    @property
    def operand(self) -> str | None:
        """Stored operand spelling, including constants and hexadecimal W."""
        if self.status != "decoded":
            return None
        if self.constant_value is not None:
            return f"K{self.constant_value}"
        if self.device_family is None or self.number is None:
            return None
        digits = f"{self.number:X}" if self.device_family == "W" else str(self.number)
        return f"{self.device_family}{digits}"

    @property
    def fx_operand(self) -> str | None:
        """FX operand spelling; timer access role remains a separate field."""
        if self.device_family not in ("M", "D", "T"):
            return None
        return self.operand


def parse_compiler_assignment(raw: bytes) -> CompilerAssignment:
    raw = bytes(raw)
    if len(raw) != 26:
        raise GXWFormatError("compiler assignment requires 26 UserInfo bytes")
    if not any(raw):
        return CompilerAssignment(raw, "unassigned")
    if raw[0] == 7:
        # UserInfo contains a 25-byte operand. Unexercised modifiers, wide
        # values, indexed forms and other prefixes remain preserved/opaque.
        if any(raw[1:9]) or any(raw[13:21]) or any(raw[22:]):
            return CompilerAssignment(raw, "opaque")
        family = chr(raw[21]).upper()
        if family == "K":
            value = struct.unpack_from("<i", raw, 9)[0]
            return CompilerAssignment(raw, "decoded", role="constant", constant_value=value)
        number = struct.unpack_from("<I", raw, 9)[0]
        # A conservative projection profile, not a PLC capacity rule. Larger
        # operands can format successfully but are outside this evidence scope.
        prefix = {"M": "%MX0", "D": "%MW0", "W": "%MW1"}.get(family)
        # FX3G rejects M7680..M7999 while Q03UDV accepts them. This entry
        # point has no CPU context, so that interval remains opaque as well.
        if prefix is None or number > 8191 or (family == "M" and 7680 <= number < 8000):
            return CompilerAssignment(raw, "opaque")
        return CompilerAssignment(raw, "decoded", family, number,
                                  "device_reference", None, f"{prefix}.{number}")
    # Constant-table references and other variants remain opaque here.
    form = {
        (0x21, 0): ("M", "bit", "%MX0"),
        (0x24, 5): ("D", "word", "%MW0"),
        (0x05, 11): ("T", "timer_status", "%MX3"),
        (0x05, 12): ("T", "timer_coil", "%MX5"),
        (0x05, 13): ("T", "timer_value", "%MW3"),
    }.get((raw[0], raw[1]))
    number = int.from_bytes(raw[2:5], "little", signed=True)
    count = struct.unpack_from("<i", raw, 6)[0]
    if form is None or raw[5] or any(raw[10:]) or number < 0 or count <= 0:
        return CompilerAssignment(raw, "opaque")
    family, role, prefix = form
    return CompilerAssignment(raw, "decoded", family, number, role, count, f"{prefix}.{number}")

"""Preserved compiler allocations, distinct from declared addresses.

The compact form is independently formatted by ECCompiler 15.22 RVA 0x51650.
Only the M/D/T forms exercised by the compiler-artifact corpus are projected.
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

    @property
    def fx_operand(self) -> str | None:
        """FX operand spelling; timer access role remains a separate field."""
        if self.status != "decoded":
            return None
        return f"{self.device_family}{self.number}"


def parse_compiler_assignment(raw: bytes) -> CompilerAssignment:
    raw = bytes(raw)
    if len(raw) != 26:
        raise GXWFormatError("compiler assignment requires 26 UserInfo bytes")
    if not any(raw):
        return CompilerAssignment(raw, "unassigned")
    # Native formatting handles other variants (including an embedded address
    # and a constant-table reference). Their layouts remain opaque here.
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

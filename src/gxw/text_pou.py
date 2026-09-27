"""Source-bound reading of the observed uncompressed ST source envelope.

The source stores UTF-16LE independently of the project's compiler code page.
Text decoding does not parse ST syntax or establish native compilability.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError
from .source_header import source_payload_offset


@dataclass(frozen=True)
class StructuredTextSource:
    raw: bytes
    code_units: int  # Includes the serialized terminating UTF-16 NUL.
    text: str | None
    diagnostic: str | None = None
    text_offset: int = 71

    @property
    def text_end(self) -> int:
        return self.text_offset + 2 * (self.code_units - 1)

    @property
    def text_bytes(self) -> bytes:
        return self.raw[self.text_offset:self.text_end]

    def reconstruct(self) -> bytes:
        # Never normalize line endings, replace undecodable code units or
        # convert through the native compiler's narrower code page.
        return self.raw


def parse_st_pou(raw: bytes) -> StructuredTextSource:
    """Read the bounded C1 envelope; keep invalid text as an explicit raw gap."""
    raw = bytes(raw)
    start = source_payload_offset(raw)
    if len(raw) < start + 47 or raw[start] != 0xc1:
        raise GXWFormatError("outside observed uncompressed ST source envelope")
    size, repeated, source_flag, count = struct.unpack_from("<4I", raw, start + 1)
    text_offset = start + 17
    # Zero/one flag and final trailer word occur in independently captured
    # FX3G library sources. They are retained without assigning a meaning.
    if (size != repeated or size != len(raw) - start - 29 or source_flag not in (0, 1)
            or count < 1 or text_offset + 2 * count + 28 != len(raw)
            or raw[-28:-4] != bytes(24) or struct.unpack_from("<I", raw, len(raw) - 4)[0] not in (0, 1)
            or raw[-30:-28] != b"\0\0"):
        raise GXWFormatError("unsupported ST source lengths, terminator or trailer")
    payload = raw[text_offset:-30]
    try:
        text = payload.decode("utf-16le")
    except UnicodeError:
        return StructuredTextSource(raw, count, None, "ST source contains undecodable UTF-16 code units", text_offset)
    if "\0" in text:
        return StructuredTextSource(raw, count, None, "ST source contains an embedded NUL; text is opaque", text_offset)
    return StructuredTextSource(raw, count, text, text_offset=text_offset)

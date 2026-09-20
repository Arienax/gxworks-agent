"""Read bounded code regions in the observed FX .res wrapper without rewriting it.

Both code copies, timestamps and the unparsed suffix retain original bytes.
Decoded compiler output must never be presented as proof of source freshness.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError
from .token_pou import TokenProgram, parse_token_region


@dataclass(frozen=True)
class TokenResource:
    raw: bytes
    code_regions: tuple[TokenProgram, ...]
    code_spans: tuple[tuple[int, int], ...]
    suffix_offset: int

    @property
    def observed_program_names(self) -> tuple[str, ...] | None:
        """Known single-name suffix only; other suffixes remain opaque.

        The trailing 4/1 fields are matched, not assigned speculative roles.
        Names were cross-checked against projectdatalist in the corpus.
        """
        tail = self.raw[self.suffix_offset:]
        if len(tail) < 18 or struct.unpack_from("<I", tail)[0] != 1:
            return None
        length = struct.unpack_from("<I", tail, 4)[0]
        if not 1 <= length <= 256 or len(tail) != 16 + 2 * length or tail[-8:] != struct.pack("<II", 4, 1):
            return None
        encoded = tail[8:-8]
        if encoded[-2:] != b"\0\0":
            return None
        try:
            name = encoded[:-2].decode("utf-16le")
        except UnicodeError:
            return None
        return (name,) if name and "\0" not in name else None

    def reconstruct(self) -> bytes:
        if any(region.raw != self.raw or region.reconstruct() != self.raw for region in self.code_regions):
            raise GXWFormatError("compiled region is not bound to the resource bytes")
        return self.raw


def parse_token_resource(raw: bytes) -> TokenResource:
    """Observed wrapper only; retain its unresolved suffix, including names.

The two length-prefixed code spans are separated/followed by eight zero bytes.
Either span may be empty. Their roles and freshness are deliberately unnamed.
"""
    raw = bytes(raw)
    signature = bytes.fromhex("01000000000001000000000001000a00000000000200000001000000010000000000")
    if len(raw) < 78 or not raw.startswith(signature):
        raise GXWFormatError("unsupported compiled resource wrapper")
    cursor, spans, regions = 54, [], []
    for _ in range(2):
        if cursor + 4 > len(raw):
            raise GXWFormatError("truncated compiled region length")
        size = struct.unpack_from("<I", raw, cursor)[0]
        offset = cursor + 4
        end = offset + size
        if end + 8 > len(raw) or any(raw[end:end + 8]):
            raise GXWFormatError("compiled region exceeds source or has unknown separator")
        spans.append((offset, size))
        if size:
            regions.append(parse_token_region(raw, offset, size))
        cursor = end + 8
    return TokenResource(raw, tuple(regions), tuple(spans), cursor)

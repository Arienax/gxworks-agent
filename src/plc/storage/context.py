"""Project-bound device allocation context shared by every storage consumer.

The context describes the source storage width of D/W extended ranges. It is not an
allocation check and says nothing about execution safety, and its four states stay
distinct: no context was provided (``None`` reaches the caller), a parsed parameter
record with no extended ranges, one with extended ranges, and parameter data that was
obtained but whose allocation could not be explained. Unknown never degrades into the
default allocation on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

#: ``ranges`` meanings, kept apart on purpose.
ALLOCATION_UNKNOWN = "unavailable"
ALLOCATION_NONE = "none"
ALLOCATION_EXTENDED = "extended"


@dataclass(frozen=True)
class QStorageContext:
    """Project-bound D/W extension ranges; absence is explicitly unknown.

    Each row is (source device code, source start, count, ZR start). These
    ranges describe source storage width, not allocation or execution safety.
    An empty tuple means a parsed parameter record has no extended ranges.
    ``None`` means its allocation information could not be established.
    """
    cpu: str
    parameter_record: bytes | None
    ranges: tuple[tuple[int, int, int, int], ...] | None
    reason: str | None = None
    device_limits: tuple[tuple[int, int], ...] = ()

    @property
    def status(self):
        return 'available' if self.ranges is not None else 'unavailable'

    @property
    def has_extended_ranges(self):
        return bool(self.ranges) if self.ranges is not None else None

    def as_dict(self):
        return dict(status=self.status, cpu=self.cpu, has_extended_ranges=self.has_extended_ranges,
                    ranges=[dict(device=code, start=start, count=count, zr_start=target)
                            for code, start, count, target in self.ranges or ()], reason=self.reason)




def allocation_state(context):
    """Name the allocation state of one context, or ``not_provided`` for ``None``."""
    if context is None:
        return "not_provided"
    if context.ranges is None:
        return ALLOCATION_UNKNOWN
    return ALLOCATION_EXTENDED if context.ranges else ALLOCATION_NONE

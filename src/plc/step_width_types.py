# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Step width result values, independent of any calculation source.

A width answer says how many program steps a form occupies and why it is unresolved
when it is. Defining that value here lets a calculation module return it without
importing the whole public step-width service, and keeps one definition for every
consumer (Core, CSV planning, GXW writers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

#: Resolution states. None of them changes the project's model range, and none of
#: them means "this CPU does not support the instruction".
STATUS_KNOWN = "known"
STATUS_REQUIRES_OPERANDS = "requires_operands"
STATUS_REQUIRES_CONTEXT = "requires_context"
STATUS_NO_PROFILE = "no_profile"
STATUS_CONFLICT = "conflict"
STATUS_METADATA_UNAVAILABLE = "metadata_unavailable"
STATUS_UNKNOWN_CPU = "unknown_cpu"
STATUS_NOT_APPLICABLE = "not_applicable"

#: How unresolved member answers merge, most fundamental first.
UNRESOLVED_PRIORITY = (
    STATUS_NO_PROFILE, STATUS_CONFLICT, STATUS_METADATA_UNAVAILABLE,
    STATUS_UNKNOWN_CPU, STATUS_REQUIRES_CONTEXT, STATUS_REQUIRES_OPERANDS,
    STATUS_NOT_APPLICABLE,
)


@dataclass(frozen=True)
class StepWidth:
    """One expected program width, with the reason it is (un)resolved."""

    steps: Optional[int]
    source: str
    reason: str = ""
    evidence: Tuple[str, ...] = ()
    status: str = ""
    requirements: Tuple[str, ...] = ()
    #: "exact" when the rule was verified for this CPU, "family" when it is a
    #: shared family rule whose evidence names the CPUs it was verified on.
    scope: str = "exact"
    # Per-member answers for a merged target such as FX3U/FX3UC or L02/L02-P.
    members: Tuple[Tuple[str, "StepWidth"], ...] = field(default=(), repr=False)

    def __post_init__(self):
        if not self.status:
            object.__setattr__(self, "status",
                               STATUS_KNOWN if self.steps is not None else STATUS_NO_PROFILE)

    @property
    def known(self) -> bool:
        return self.steps is not None

    def as_dict(self) -> dict:
        return {"steps": self.steps, "status": self.status, "source": self.source,
                "scope": self.scope, "reason": self.reason,
                "requirements": list(self.requirements), "evidence": list(self.evidence),
                "members": [{"cpu": cpu, **value.as_dict()} for cpu, value in self.members]}


def known_width(steps: int, source: str, *, evidence: Sequence[str] = (),
                scope: str = "exact",
                members: Sequence[Tuple[str, "StepWidth"]] = ()) -> StepWidth:
    return StepWidth(int(steps), source, evidence=tuple(evidence), status=STATUS_KNOWN,
                     scope=scope, members=tuple(members))


def unresolved_width(status: str, reason: str, *, source: str = "unknown",
                     requirements: Sequence[str] = (),
                     members: Sequence[Tuple[str, "StepWidth"]] = ()) -> StepWidth:
    return StepWidth(None, source, reason=reason, status=status,
                     requirements=tuple(requirements), members=tuple(members))

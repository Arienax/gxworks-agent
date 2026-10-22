"""Registered CPU target identity: menu identities, their members and their series.

Only identity lives here: which spellings name a saved CPU menu option, which concrete
CPUs a merged option covers, which series a CPU belongs to and which targets carry no
program source. GX Works2 representation - native version, multilingual series labels,
CSV PLC-info headers - stays in :mod:`gxworks2.project_profiles`, which builds its
profiles from this catalogue so the project keeps one table.

This is storage/identity metadata. It never states that a CPU is supported, callable or
generated for; see :mod:`plc.target_support` for that.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional, Tuple

SERIES_Q = "Q"
SERIES_L = "L"
SERIES_FX = "FX"

Q_CPUS = (
    "Q00J", "Q00UJ", "Q00", "Q00U", "Q01", "Q01U", "Q02/Q02H", "Q02PH", "Q02U",
    "Q03UD", "Q03UDE", "Q03UDV", "Q04UDH", "Q04UDEH", "Q04UDV", "Q04UDPV",
    "Q06H", "Q06PH", "Q06UDH", "Q06UDEH", "Q06UDV", "Q06UDPV", "Q10UDH",
    "Q10UDEH", "Q12H", "Q12PH", "Q12PRH", "Q13UDH", "Q13UDEH", "Q13UDV",
    "Q13UDPV", "Q20UDH", "Q20UDEH", "Q25H", "Q25PH", "Q25PRH", "Q26UDH",
    "Q26UDEH", "Q26UDV", "Q26UDPV", "Q50UDEH", "Q100UDEH",
    "QJ72LP25/QJ72BR15(RemoteI/O)",
)
L_CPUS = ("L02S/L02S-P", "L02/L02-P", "L06/L06-P", "L26/L26-P", "L26-BT/L26-PBT",
          "LJ72GF15-T2", "LJ72MS15")
FX_CPUS = ("FX0S/FX0", "FX0N", "FX1", "FX1S", "FX1N/FX1NC", "FXU/FX2C",
           "FX2N/FX2NC", "FX3S", "FX3G/FX3GC", "FX3U/FX3UC")

#: Registered menu identities grouped by series, in catalogue order.
CPU_GROUPS = ((SERIES_Q, Q_CPUS), (SERIES_L, L_CPUS), (SERIES_FX, FX_CPUS))

#: Menu options that expose parameter navigation without a program source.
PARAMETER_ONLY = frozenset({"QJ72LP25/QJ72BR15(RemoteI/O)", "LJ72GF15-T2", "LJ72MS15"})

#: Identities the project knows without a GX Works2 creation option.
CORE_ONLY_TARGETS = {"FX5U": SERIES_FX + "5U"}

REMOTE_IO_SUFFIX = "(REMOTEI/O)"


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


@lru_cache(maxsize=1)
def _catalog() -> dict:
    """menu spelling -> (series, owning menu identity), members included."""
    catalog = {}
    for series, cpus in CPU_GROUPS:
        for cpu in cpus:
            catalog[_key(cpu)] = (series, cpu)
            # The suffix is written "(RemoteI/O)" in the menu, so strip it on the
            # normalised spelling instead of the literal one.
            for member in _key(cpu).removesuffix(REMOTE_IO_SUFFIX).split("/"):
                catalog.setdefault(_key(member), (series, cpu))
    return catalog


@lru_cache(maxsize=1)
def known_menu_identities() -> frozenset:
    """Every saved CPU menu option, with and without a Remote/O suffix."""
    names = set()
    for _series, cpus in CPU_GROUPS:
        for cpu in cpus:
            text = cpu.strip().upper()
            names.add(text)
            names.add(text.removesuffix(REMOTE_IO_SUFFIX))
    # Existing Core target without a GX Works2 creation option.
    names.update(CORE_ONLY_TARGETS)
    return frozenset(name for name in names if name)


def target_members(plc_model: Any) -> Tuple[str, ...]:
    """Concrete members of one target identity, including merged menu options."""
    text = _key(plc_model).removesuffix(REMOTE_IO_SUFFIX)
    return tuple(part for part in (piece.strip() for piece in text.split("/")) if part)


def target_identity_state(plc_model: Any) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    """Members of one saved CPU menu option, or the reason this identity is none.

    A merged identity has to name a saved menu option as a whole: every part being
    a real CPU is not enough (``FX3U/FX1S`` is not a menu option), and a trailing
    slash names nothing. Core width resolution and the knowledge query both ask
    this, so one spelling of a target has one answer.
    """
    merged_text = ("" if isinstance(plc_model, (list, tuple))
                   else str(plc_model or "").strip().upper())
    if isinstance(plc_model, (list, tuple)):
        # An explicit member list is an internal convenience only.
        members = tuple(str(part).strip().upper() for part in plc_model if str(part).strip())
    else:
        members = target_members(plc_model)
    if members:
        merged = "/".join(members)
        spelling = merged_text.removesuffix(REMOTE_IO_SUFFIX).replace(" ", "")
        if spelling != merged:
            return None, (f"Unsupported CPU identity spelling {merged_text!r}: "
                          "it is not a saved CPU menu option")
        if len(members) > 1 and merged not in known_menu_identities():
            return None, (f"Unsupported merged CPU identity {merged_text or merged!r}: "
                          "it is not a saved CPU menu option")
    if not members:
        return None, "A stored step width needs the saved CPU identity"
    return members, None


def target_series(plc_model: Any) -> Optional[Tuple[str, str]]:
    """``(series, owning menu identity)`` for one identity or concrete member."""
    return _catalog().get(_key(plc_model))


def is_parameter_only(plc_model: Any) -> bool:
    resolved = target_series(plc_model)
    return bool(resolved and resolved[1] in PARAMETER_ONLY)


def target_family(plc_model: Any) -> Optional[str]:
    """Existing native CPU family for one identity; never narrows the model range."""
    identity = _key(plc_model)
    resolved = target_series(identity)
    if resolved is not None:
        return resolved[0]
    if identity.upper() in CORE_ONLY_TARGETS:
        # Existing Core target; not a GX Works2 native creation option, and it has
        # no program-width coverage of its own yet.
        return CORE_ONLY_TARGETS[identity.upper()]
    return None

# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""GX Works2 1.635 native New-dialog targets, including merged menu options.

This is native project selection metadata, not instruction/device policy.
GX Developer compatibility entries are deliberately outside this catalogue.
The three remote-I/O modules expose parameter navigation without a program
source; residual MDI documents are not evidence of program support.
"""
from dataclasses import dataclass


GXWORKS2_NATIVE_VERSION = (1, 635, 0, 1)

from plc.target_identity import CPU_GROUPS, PARAMETER_ONLY, SERIES_FX, SERIES_L, SERIES_Q

#: Native display labels are a GX Works2 representation, not CPU identity.
_NATIVE_SERIES_NAMES = {
    SERIES_Q: ('QCPU(Q模式)', 'QCPU (Q mode)', 'QCPU(Qモード)'),
    SERIES_L: ('LCPU',),
    SERIES_FX: ('FXCPU',),
}


@dataclass(frozen=True)
class NativeProjectProfile:
    cpu: str
    series: tuple[str, ...]
    program_supported: bool

    def accepts_plc_info(self, value):
        if value == "三菱 GX Works2 兼容":
            return True
        # Native CSV headers describe the exact menu identity. Do not accept
        # an FX export as evidence that a Q/L target is compatible.
        # L's exporter writes a concrete base name for a merged menu option;
        # the PH converter retains its observed legacy Japanese series label.
        # Neither changes the native menu range or permits another CPU group.
        series_names = (*self.series, "QCPU(Q儌乕僪)") if self.series[0].startswith("QCPU") else self.series
        cpu_names = {self.cpu, *self.cpu.removesuffix("(RemoteI/O)").split("/")}
        return str(value).strip() in {f"{series} {cpu}" for series in series_names for cpu in cpu_names}


NATIVE_PROJECT_PROFILES = tuple(
    NativeProjectProfile(cpu, _NATIVE_SERIES_NAMES[series], cpu not in PARAMETER_ONLY)
    for series, cpus in CPU_GROUPS
    for cpu in cpus
)


def _cpu_key(value):
    return str(value or "").strip().upper()


_PROFILES_BY_NAME = {}
for _profile in NATIVE_PROJECT_PROFILES:
    _PROFILES_BY_NAME[_cpu_key(_profile.cpu)] = _profile
    # Preserve the menu's merged identity when given a concrete member.
    _members = _profile.cpu.removesuffix("(RemoteI/O)").split("/")
    for _member in _members:
        _PROFILES_BY_NAME[_cpu_key(_member)] = _profile


def native_project_profile(cpu):
    """Return an exact menu target; unsupported/Developer/FX5 names stay unknown."""
    return _PROFILES_BY_NAME.get(_cpu_key(cpu))


def can_prepare_program_project(cpu):
    profile = native_project_profile(cpu)
    return bool(profile and profile.program_supported)


def native_plc_info(cpu):
    """Exact native CSV PLC-info value for one menu target.

    Merged menu options keep their observed identity, and a concrete member
    resolves to the menu option that owns it, so an FX3UC project exports the
    same ``FXCPU FX3U/FX3UC`` header GX Works2 writes. Unknown, Developer and
    FX5U names raise instead of borrowing an FX header.
    """
    profile = native_project_profile(cpu)
    if profile is None:
        raise ValueError("no native GX Works2 CSV PLC-info for " + repr(str(cpu or "")))
    return f"{profile.series[0]} {profile.cpu}"


def plc_info_target(value):
    """The menu target a native CSV PLC-info value states, or ``None``.

    The reverse of :func:`native_plc_info`. An export names its own CPU, so a
    reader uses that identity instead of assuming one; the generic
    ``三菱 GX Works2 兼容`` value names no CPU and stays unrecognised.
    """
    text = str(value or "").strip()
    if not text or text == "三菱 GX Works2 兼容":
        return None
    for profile in NATIVE_PROJECT_PROFILES:
        if profile.accepts_plc_info(text):
            return profile.cpu
    return None

"""Operation-specific expansion points for every native GX Works2 CPU menu target.

Selection metadata never grants instruction, ABI, or execution support. Evidence
is exact-target and operation scoped; merged menu options retain their identity.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from gxworks2.project_profiles import GXWORKS2_NATIVE_VERSION, NATIVE_PROJECT_PROFILES, native_project_profile
from plc.runtime_semantics import control_runtime_facts


OPERATIONS = ("composition", "fbd_topology", "fbd_native_compile", "closed_loop_scan")


def target_cpu_names():
    """Known identities, independent of instruction/device validation coverage.

    FX5U remains an existing Core target, not a GX Works2 creation option.
    Native menu identities and their concrete members come from one catalogue.
    """
    names = {"FX5U"}
    for profile in NATIVE_PROJECT_PROFILES:
        names.add(profile.cpu)
        names.update(profile.cpu.removesuffix("(RemoteI/O)").split("/"))
    return tuple(sorted(names))


def normalize_target_cpu(cpu="FX3U"):
    """Preserve a known selected CPU; never replace an unknown one with FX3U."""
    candidate = str(cpu or "FX3U").strip().upper()
    profile = native_project_profile(candidate)
    if profile is not None:
        return profile.cpu if candidate == profile.cpu.upper() else candidate
    if candidate == "FX5U":
        return candidate
    raise ValueError(f"Unknown PLC target {cpu!r}")


@dataclass(frozen=True)
class TargetEvidence:
    cpu: str
    operation: str
    status: str
    source: str
    software_version: str
    scope: str


class TargetSupportRegistry:
    """A provider supplies evidence, never an implicit family fallback."""
    def __init__(self, evidence=()):
        self._evidence = {}
        for row in evidence:
            profile = native_project_profile(row.cpu)
            if profile is None or row.cpu != profile.cpu:
                raise ValueError("target evidence must name the exact native menu option")
            if row.operation not in OPERATIONS or row.status not in {"verified", "unverified", "unsupported"}:
                raise ValueError("invalid target operation or evidence status")
            if not profile.program_supported and row.status == "verified":
                raise ValueError("parameter-only targets cannot acquire program operation support")
            if not all(isinstance(value, str) and value.strip() for value in (row.source, row.software_version, row.scope)):
                raise ValueError("target evidence needs source, version and operation scope")
            key = (row.cpu, row.operation, row.software_version, row.scope)
            if key in self._evidence:
                raise ValueError("duplicate target evidence")
            self._evidence[key] = row

    def target(self, cpu, *, software_version=None, scope=None):
        profile = native_project_profile(cpu)
        if profile is None:
            return {"requested_cpu": cpu, "menu_cpu": None, "status": "unknown", "operations": {}}
        family = "Q" if profile.series[0].startswith("Q") else "L" if profile.series[0].startswith("L") else "FX"
        operations = {}
        for operation in OPERATIONS:
            matching = [row for (model, op, version, evidence_scope), row in self._evidence.items()
                        if model == profile.cpu and op == operation and version == software_version and evidence_scope == scope]
            operations[operation] = {"status": "unsupported" if not profile.program_supported else "unverified",
                                     "reason": "parameter_only_target" if not profile.program_supported else "exact_target_evidence_required",
                                     "evidence": []}
            if matching:
                operations[operation] = {"status": matching[0].status, "evidence": [vars(r).copy() for r in matching]}
        return {"requested_cpu": str(cpu), "menu_cpu": profile.cpu, "family": family,
                "members": profile.cpu.removesuffix("(RemoteI/O)").split("/"),
                "program_supported": profile.program_supported,
                "catalog_version": ".".join(map(str, GXWORKS2_NATIVE_VERSION)),
                "operations": operations,
                "scan_fact_status": "source_scoped_subset" if control_runtime_facts(cpu).get("control") else "unverified"}

    def catalogue(self):
        return [self.target(profile.cpu) for profile in NATIVE_PROJECT_PROFILES]


DEFAULT_TARGET_SUPPORT = TargetSupportRegistry()


def native_target_catalogue():
    return deepcopy(DEFAULT_TARGET_SUPPORT.catalogue())

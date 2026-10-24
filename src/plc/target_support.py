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
# Core identities only: these are not GX Works2 native menu options.
# Operation capabilities remain gated by exact CPU source-scoped facts.
CORE_ONLY_TARGETS = frozenset({"FX5S", "FX5UJ", "FX5U", "FX5UC"})
NATIVE_UI_LANGUAGES = ("zh-CN", "en", "ja")
LIVE_OPERATIONS = (
    "create_global_labels", "edit_global_labels", "edit_pou_labels", "delete_label",
    "create_st_fb", "edit_st_fb", "create_st_program", "edit_st_program",
    "configure_project", "create_fbd_call", "edit_fbd_graph", "compile",
    "show_assignments", "save", "reopen",
)


def require_native_program_target(cpu):
    """Validate a menu identity without granting native operation evidence."""
    profile = native_project_profile(cpu)
    if profile is None:
        raise ValueError("Live native editing requires a known GX Works2 FX, Q or L CPU menu target")
    if not profile.program_supported:
        raise ValueError("The selected native CPU is a parameter-only target without program operations")
    return profile


def target_cpu_names():
    """Known identities, independent of instruction/device validation coverage.

    FX5 targets are Core identities, not GX Works2 creation options.
    Native menu identities and their concrete members come from one catalogue.
    """
    names = set(CORE_ONLY_TARGETS)
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
    if candidate in CORE_ONLY_TARGETS:
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
    native_ui_language: str | None = None


class TargetSupportRegistry:
    """A provider supplies evidence, never an implicit family fallback."""
    def __init__(self, evidence=()):
        self._evidence = {}
        for row in evidence:
            profile = native_project_profile(row.cpu)
            if profile is None or row.cpu != profile.cpu:
                raise ValueError("target evidence must name the exact native menu option")
            if row.operation not in OPERATIONS + LIVE_OPERATIONS or row.status not in {"verified", "unverified", "unsupported"}:
                raise ValueError("invalid target operation or evidence status")
            if (row.native_ui_language is not None and row.native_ui_language not in NATIVE_UI_LANGUAGES
                    or row.operation in LIVE_OPERATIONS and row.native_ui_language is None):
                raise ValueError("live native operation evidence requires the exact native UI language")
            if not profile.program_supported and row.status == "verified":
                raise ValueError("parameter-only targets cannot acquire program operation support")
            if not all(isinstance(value, str) and value.strip() for value in (row.source, row.software_version, row.scope)):
                raise ValueError("target evidence needs source, version and operation scope")
            key = (row.cpu, row.operation, row.software_version, row.scope, row.native_ui_language)
            if key in self._evidence:
                raise ValueError("duplicate target evidence")
            self._evidence[key] = row

    def target(self, cpu, *, software_version=None, scope=None, native_ui_language=None):
        if native_ui_language is not None and native_ui_language not in NATIVE_UI_LANGUAGES:
            raise ValueError("unknown native UI language")
        profile = native_project_profile(cpu)
        if profile is None:
            return {"requested_cpu": cpu, "menu_cpu": None, "status": "unknown", "operations": {}}
        family = "Q" if profile.series[0].startswith("Q") else "L" if profile.series[0].startswith("L") else "FX"
        operations = {}
        for operation in OPERATIONS:
            matching = [row for (model, op, version, evidence_scope, language), row in self._evidence.items()
                        if model == profile.cpu and op == operation and version == software_version
                        and evidence_scope == scope and language == native_ui_language]
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

    def live_capabilities(self, cpu, *, software_version, native_ui_language, scope="live_native_session"):
        """Look up exact menu/version/language/operation receipts, with no family fallback."""
        if native_ui_language not in NATIVE_UI_LANGUAGES:
            raise ValueError("live capabilities require the actual native UI language")
        profile = native_project_profile(cpu)
        result = {}
        for operation in LIVE_OPERATIONS:
            row = self._evidence.get((profile.cpu, operation, software_version, scope, native_ui_language)) if profile else None
            if row is not None:
                result[operation] = {"status": row.status, "evidence": [vars(row).copy()]}
            else:
                parameter_only = profile is not None and not profile.program_supported
                result[operation] = {
                    "status": "unsupported" if parameter_only else "unverified",
                    "reason": "parameter_only_target" if parameter_only else "exact_target_language_evidence_required",
                    "evidence": [],
                }
        return result

    def catalogue(self):
        return [self.target(profile.cpu) for profile in NATIVE_PROJECT_PROFILES]


# Completed author-owned native-session receipts, scoped to the observed menu
# and bounded operations. These are FX3U project observations, not evidence of
# every physical member's PLC execution. A new global table was not witnessed.
_FX3U_ZH_LIVE_STEPS = (
    ('edit_global_labels', 'continuous-mcp-6/103'),
    ('edit_pou_labels', 'continuous-mcp-6/105'),
    ('delete_label', '066-reconcile-observed-cursor-row-delete'),
    ('create_st_fb', 'continuous-mcp-6/104'),
    ('edit_st_fb', 'continuous-mcp-6/109'),
    ('create_st_program', '035-active-st-program-create'),
    ('edit_st_program', 'continuous-mcp-6/110'),
    ('configure_project', '043-active-allocation-ranges'),
    ('create_fbd_call', 'continuous-mcp-6/106'),
    ('edit_fbd_graph', 'continuous-mcp-6/107'),
    ('compile', 'continuous-mcp-6/108'),
    ('show_assignments', 'continuous-mcp-6/120'),
    ('save', 'continuous-mcp-6/120'),
    ('reopen', 'continuous-mcp-6/124'),
)

# Each entry names a completed independent-engineering-cell audit. Keep the
# operation list explicit: adding a new action must not inherit old evidence.
_SAMPLED_LIVE_OPERATIONS = (
    'create_global_labels', 'edit_global_labels', 'edit_pou_labels',
    'create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program',
    'configure_project', 'create_fbd_call', 'edit_fbd_graph', 'compile',
    'show_assignments', 'save', 'reopen',
)
_SAMPLED_LIVE_CELLS = (
    ('FX3S', 'en', 's18-fx3s-en-mcp-02'),
    ('Q03UDV', 'en', 's18-q03udv-en-mcp-01'),
    ('L02/L02-P', 'en', 's18-l02-en-mcp-03'),
    ('Q00U', 'en', 's18-q00u-en-mcp-01'),
    ('Q06UDEH', 'en', 's18-q06udeh-en-mcp-01'),
    ('FX3G/FX3GC', 'en', 's18-fx3g-en-mcp-01'),
    ('FX3U/FX3UC', 'en', 's18-fx3u-en-mcp-01'),
    ('L06/L06-P', 'en', 's18-l06-en-mcp-01'),
)


def _default_live_evidence():
    records = {}

    def retain(row):
        key = (row.cpu, row.operation, row.software_version, row.scope, row.native_ui_language)
        records.setdefault(key, row)

    for operation, step in _FX3U_ZH_LIVE_STEPS:
        retain(TargetEvidence('FX3U/FX3UC', operation, 'verified',
            'gxw-live-native-engineering-20261018/' + step,
            '1.635.0.1', 'live_native_session', 'zh-CN'))
    for cpu, language, source in _SAMPLED_LIVE_CELLS:
        for operation in _SAMPLED_LIVE_OPERATIONS:
            retain(TargetEvidence(cpu, operation, 'verified',
                'gxw-live-sampled18-20261019/' + source + '/cell-audit#' + operation,
                '1.635.0.1', 'live_native_session', language))
    return tuple(records.values())


DEFAULT_TARGET_SUPPORT = TargetSupportRegistry(_default_live_evidence())


def native_target_catalogue():
    return deepcopy(DEFAULT_TARGET_SUPPORT.catalogue())

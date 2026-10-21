"""Source-backed program widths for every target the project already knows.

This is storage metadata, not validation and not a statement about which CPUs the
project supports. All generated step labels use this module. Native token headers
are observations of stored widths: readers preserve them, including positive but
incorrect widths, rather than silently replacing them with a calculated width.

Target identity follows the existing native CPU catalogue, including merged menu
options such as ``FX3U/FX3UC`` or ``L02/L02-P``: a merged target answers only when
every member answers, and consistently. FX rules live in the shared resource. Q/L
widths reuse the stored-width algorithms in :mod:`gxw.step_rules_q` through a
lazy delegation, so each CPU family has exactly one implementation and Core holds
no second copy of a formula.

A width is never guessed. An unresolved form reports why: missing operands,
missing project context, no coverage for that CPU, conflicting member answers, or
unavailable metadata. ``None`` never becomes a one-step instruction and never
blocks generation by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple


NATIVE_CATALOG = "fx3u_step_widths.json"

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
_UNRESOLVED_PRIORITY = (
    STATUS_NO_PROFILE, STATUS_CONFLICT, STATUS_METADATA_UNAVAILABLE,
    STATUS_UNKNOWN_CPU, STATUS_REQUIRES_CONTEXT, STATUS_REQUIRES_OPERANDS,
    STATUS_NOT_APPLICABLE,
)


def _catalog_directories() -> Tuple[Path, ...]:
    roots = []
    configured = os.environ.get("GXW2_INSTRUCTION_CATALOG")
    if configured:
        roots.append(Path(configured).expanduser())
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle) / "resources/instructions/mitsubishi")
    roots.extend((Path(__file__).resolve().parents[2] / "resources/instructions/mitsubishi",
                  Path.cwd() / "resources/instructions/mitsubishi"))
    return tuple(dict.fromkeys(root.resolve() for root in roots))


def _read_resource(name: str) -> dict:
    for directory in _catalog_directories():
        path = directory / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("Instruction resource not found: " + name)


@lru_cache(maxsize=2)
def native_token_encodings(include_supplemental: bool = False) -> Mapping[str, Tuple[str, int]]:
    """One immutable native encoding table shared with the GXW lexical decoder.

    Arity here describes a binary spelling; it does not replace the semantic
    instruction registry. Keep the source catalogue's evidence/scope intact.
    """
    payload = _read_resource(NATIVE_CATALOG)
    entries = dict(payload["opcodes"])
    for key, value in (payload.get("supplemental_encodings", {}) if include_supplemental else {}).items():
        if key in entries and entries[key] != value:
            raise ValueError("Conflicting native encoding: " + key)
        entries[key] = value
    return MappingProxyType({key: (str(value[0]), int(value[1]))
                             for key, value in entries.items()})


@lru_cache(maxsize=1)
def native_lexical_overrides() -> Mapping[str, str]:
    """Existing exact lexical spellings; do not expand native family inference."""
    return MappingProxyType(dict(_read_resource(NATIVE_CATALOG)["lexical_overrides"]))


def stored_header_width(raw: bytes) -> Optional[int]:
    """Read a framed opcode/label header's *stored* width, not its correctness.

    The caller must first establish that this is an opcode/label, not an operand.
    The native GetStepSize uses signed byte extension; 0/negative are unknown.
    """
    raw = bytes(raw)
    if len(raw) not in (2, 3, 4, 5, 6) or raw[0] != len(raw) or raw[-1] != len(raw):
        return None
    if len(raw) <= 3:
        return 1
    return raw[2] if 1 <= raw[2] <= 127 else None


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


def _known(steps: int, source: str, *, evidence: Sequence[str] = (), scope: str = "exact",
           members: Sequence[Tuple[str, "StepWidth"]] = ()) -> StepWidth:
    return StepWidth(int(steps), source, evidence=tuple(evidence), status=STATUS_KNOWN,
                     scope=scope, members=tuple(members))


def _unresolved(status: str, reason: str, *, source: str = "unknown",
                requirements: Sequence[str] = (),
                members: Sequence[Tuple[str, "StepWidth"]] = ()) -> StepWidth:
    return StepWidth(None, source, reason=reason, status=status,
                     requirements=tuple(requirements), members=tuple(members))


def _operand_text(value: Any) -> str:
    text = str(value).strip()
    # Do not normalize or reinterpret quoted data, bit selects or indices.
    match = re.fullmatch(r"([A-Za-z]+)([0-9]+)", text)
    if match:
        return match[1].upper() + (match[2].lstrip("0") or "0")
    return text.upper() if not text.startswith(('"', "'")) else text


class StepWidthCatalog:
    """Derive fixed widths from exact native spellings; apply explicit rules.

    No blanket 'D + name'/'name + P' heuristics. No formula based only on arity.
    A single observed width is usable for the ordinary operand form, not proof of
    every string/index/bit-selection encoding or CPU instruction support.
    """

    def __init__(self, encodings: Mapping[str, Sequence[Any]], rules: Mapping[str, Any]):
        self.models = frozenset(rules["models"])
        self.rules = tuple(rules["rules"])
        self.rule_opcodes = frozenset(op for rule in self.rules for op in rule["opcodes"])
        self.variable = frozenset(rules.get("variable_opcodes", ()))
        self.variable_forms = tuple(rules.get("variable_forms", ()))
        self.aliases = dict(rules.get("aliases", {}))
        self.device_ranges = tuple(rules.get("device_ranges", ()))
        exact = {}
        for row in rules.get("exact_forms", ()):
            opcode = self.aliases.get(str(row["opcode"]).upper(), str(row["opcode"]).upper())
            operands = tuple(_operand_text(value) for value in row.get("operands", ()))
            key = (opcode, operands)
            value = (int(row["steps"]), str(row.get("evidence") or ""))
            if key in exact and exact[key] != value:
                raise ValueError("Conflicting exact step-width form: " + " ".join((opcode, *operands)))
            exact[key] = value
        self.exact_forms = MappingProxyType(exact)
        widths = {}
        for header, (opcode, arity) in encodings.items():
            raw = bytes.fromhex(header)
            width = stored_header_width(raw)
            if width is None:
                continue
            widths.setdefault(str(opcode).upper(), []).append((width, int(arity), header))
        self.observations = MappingProxyType({op: tuple(items) for op, items in widths.items()})

    def resolve(self, opcode: Any, operands: Sequence[Any] = (), *, plc_model: str,
                text_encoding: Optional[str] = None) -> StepWidth:
        """Resolve one width inside this catalogue's own CPU family."""
        model = str(plc_model or "").strip().upper()
        scope = "exact"
        if model not in self.models:
            if not _in_fx_family(model):
                return _unresolved(STATUS_NO_PROFILE,
                                   "No step-width catalogue for CPU " + (model or "<missing target>"))
            # These are shared FX instruction-width rules, not an FX3U
            # substitution: the real identity is threaded through, and the answer
            # names the CPUs the evidence came from. A real export that disagrees
            # is still reported as a mismatch, never hidden.
            scope = "family"

        def known(steps: int, source: str, *, evidence: Sequence[str] = ()) -> StepWidth:
            items = tuple(evidence)
            if scope == "family":
                items += (f"Shared FX instruction-width rule applied to {model}; verified on "
                          f"{'/'.join(sorted(self.models))} manual evidence and native samples",)
            return _known(steps, source, evidence=items, scope=scope)

        op = str(opcode).strip().upper()
        op = self.aliases.get(op, op)
        args = tuple(_operand_text(value) for value in operands)
        joined = " ".join(args)
        exact = self.exact_forms.get((op, args))
        if exact is not None:
            steps, evidence = exact
            return known(steps, "exact_native_form", evidence=(evidence,) if evidence else ())
        ranged = self._device_range_steps(op, args, model)
        if ranged is not None:
            outcome, detail = ranged
            if outcome is None:
                return _unresolved(STATUS_NO_PROFILE, detail)
            return known(outcome, "device_range", evidence=(detail,))
        if op in self.rule_opcodes:
            matches = [rule for rule in self.rules
                       if op in rule["opcodes"]
                       and len(args) == rule["arity"]
                       and re.fullmatch(rule["operands"], joined)]
            if not matches:
                return _unresolved(STATUS_NO_PROFILE,
                                   "Operand-dependent width has no matching rule")
            widths = {rule["steps"] for rule in matches}
            if len(widths) != 1 or None in widths:
                return _unresolved(STATUS_CONFLICT, "Ambiguous or unresolved operand-width rule")
            return known(next(iter(widths)), "operand_rule",
                         evidence=tuple(rule["evidence"] for rule in matches))
        if op in self.variable:
            form = self._variable_form_steps(op, args, text_encoding, known)
            if form is not None:
                return form
            return _unresolved(STATUS_REQUIRES_OPERANDS,
                               "Variable-width instruction needs an operand/encoding-specific rule",
                               requirements=("operands", "operand_encoding"))
        observations = self.observations.get(op, ())
        if not observations:
            return _unresolved(STATUS_NO_PROFILE, "No native step-width observation for " + op)
        # Stored width is not an operand count. A binary-arity mismatch must not be
        # made to look like a known size, but never rejects the user's program.
        compatible = [entry for entry in observations if entry[1] == len(args)]
        sizes = {entry[0] for entry in compatible}
        if len(sizes) != 1:
            return _unresolved(STATUS_CONFLICT,
                               "Native widths are missing or ambiguous for this operand form")
        # Strings and indexed/bit-selected operands may change the encoding.
        # Ordinary constants, direct devices and K-digit groups are covered by the
        # observed fixed applied-instruction headers. Do not guess elsewhere.
        ordinary = r"(?:[K][+-]?[0-9]+|H[0-9A-F]+|E[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee+-][+-]?[0-9]+)?|[A-Z]+[0-9]+|K[1-8][XYMS][0-9]+)"
        if any(re.fullmatch(ordinary, arg) is None for arg in args):
            return _unresolved(STATUS_REQUIRES_CONTEXT,
                               "Operand encoding is outside the fixed-width profile",
                               requirements=("operand_encoding",))
        return known(next(iter(sizes)), "native_observation",
                     evidence=tuple(entry[2] for entry in compatible))

    def _variable_form_steps(self, op: str, args: Sequence[str],
                             text_encoding: Optional[str], known) -> Optional[StepWidth]:
        """Variable-string storage for one FX form, or ``None`` when none matches.

        The stored width is one instruction word, the destination words and two
        words per source character pair, so the payload has to be measured in the
        project code page. A missing code page is a stated requirement, not a
        coverage gap and never a guess at the payload size.
        """
        for entry in self.variable_forms:
            if op not in entry["opcodes"] or len(args) != int(entry["arity"]):
                continue
            if not re.fullmatch(r"[DW][0-9]+", args[1]):
                continue
            source = entry.get("source")
            if source == "word":
                if not re.fullmatch(r"[DW][0-9]+", args[0]):
                    continue
                steps_spec = entry["steps"]
                return known(int(steps_spec["fixed"]), "variable_string_form",
                             evidence=(entry["evidence"],))
            if source != "text":
                continue
            text = args[0]
            if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
                continue
            if not text_encoding:
                return _unresolved(
                    STATUS_REQUIRES_CONTEXT,
                    "A variable-string width needs the project code page to measure its payload",
                    requirements=("text_encoding",))
            try:
                size = len(text[1:-1].encode(text_encoding))
            except (LookupError, UnicodeEncodeError) as exc:
                return _unresolved(
                    STATUS_REQUIRES_CONTEXT,
                    f"The code page {text_encoding!r} cannot measure this payload: {exc}",
                    requirements=("text_encoding",))
            steps_spec = entry["steps"]
            words = (size + 1) // 2
            steps = int(steps_spec["base"]) + int(steps_spec["per_source_word"]) * words
            return known(steps, "variable_string_form",
                         evidence=(entry["evidence"],
                                   f"payload {size} bytes in {text_encoding} ({words} source words)"))
        return None

    def _device_range_steps(self, op: str, args: Sequence[str],
                            model: str) -> Optional[Tuple[Optional[int], str]]:
        """Device-index step table for one direct target, or ``None`` to fall through.

        The stored width of a direct bit device depends on the index range of the
        CPU series, not on the mnemonic alone. Returns ``(steps, evidence)``, or
        ``(None, reason)`` when the table owns the form and the index is outside
        every range it covers.
        """
        if len(args) != 1:
            return None
        match = re.fullmatch(r"([A-Z]+)([0-9]+)", args[0])
        if match is None:
            return None
        device, index = match.group(1), int(match.group(2))
        series = fx_series(model)
        for entry in self.device_ranges:
            if op not in entry["opcodes"] or device not in entry["devices"]:
                continue
            if not (model in entry.get("models", ())
                    or (series is not None and series in entry.get("series", ()))):
                continue
            for span in entry["ranges"]:
                if int(span["from"]) <= index <= int(span["to"]):
                    return int(span["steps"]), str(entry["evidence"])
            if entry.get("outside", "fall_through") == "gap":
                covered = ", ".join(f"{span['from']}-{span['to']}" for span in entry["ranges"])
                return None, (f"{device}{index} is outside the device-range step table for "
                              f"{model} (covered: {covered})")
            return None
        return None

    def fixed_forms(self) -> Mapping[str, Tuple[int, int]]:
        """Inspectable catalogue coverage, excluding operand-dependent forms."""
        result = {}
        for opcode, entries in self.observations.items():
            signatures = {(width, arity) for width, arity, _ in entries}
            if len(signatures) == 1 and opcode not in self.rule_opcodes and opcode not in self.variable and opcode not in self.aliases:
                result[opcode] = next(iter(signatures))
        return MappingProxyType(result)


@lru_cache(maxsize=1)
def default_step_width_catalog() -> StepWidthCatalog:
    return StepWidthCatalog(native_token_encodings(True), _read_resource(NATIVE_CATALOG)["step_width_profile"])


@lru_cache(maxsize=1)
def known_menu_identities() -> frozenset:
    """Every saved CPU menu option, with and without a Remote/O suffix."""
    from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
    names = set()
    for profile in NATIVE_PROJECT_PROFILES:
        text = str(profile.cpu).strip().upper()
        names.add(text)
        names.add(text.removesuffix("(REMOTEI/O)"))
    # Existing Core target without a GX Works2 creation option.
    names.add("FX5U")
    return frozenset(name for name in names if name)


def target_members(plc_model: Any) -> Tuple[str, ...]:
    """Concrete members of one target identity, including merged menu options."""
    text = str(plc_model or "").strip().upper()
    text = text.removesuffix("(REMOTEI/O)")
    return tuple(part for part in (piece.strip() for piece in text.split("/")) if part)


@lru_cache(maxsize=256)
def _native_profile(plc_model: str):
    from gxworks2.project_profiles import native_project_profile
    return native_project_profile(plc_model)


def target_family(plc_model: Any) -> Optional[str]:
    """Existing native CPU family for one identity; never narrows the model range."""
    identity = str(plc_model or "").strip().upper()
    profile = _native_profile(identity)
    if profile is not None:
        series = profile.series[0]
        if series.startswith("FX"):
            return "FX"
        if series.startswith("QCPU"):
            return "Q"
        if series.startswith("L"):
            return "L"
        return None
    if identity == "FX5U":
        # Existing Core target; not a GX Works2 native creation option, and it has
        # no program-width coverage of its own yet.
        return "FX5U"
    return None


def _in_fx_family(plc_model: Any) -> bool:
    """True for an FX menu identity: the FX width rules are shared family rules."""
    profile = _native_profile(str(plc_model or "").strip().upper())
    return bool(profile is not None and profile.series[0].startswith("FX"))


#: FX series groups whose device-index step ranges differ. The group is a
#: property of the CPU, not of the catalogue: an FX3 range split must not reach
#: an FX2N whose M device encoding has a different width.
_FX_SERIES_PREFIXES = (("FX3", "FX3"), ("FX2", "FX2"), ("FX1", "FX1"),
                       ("FX0", "FX0"), ("FXU", "FX2"))


def fx_series(plc_model: Any) -> Optional[str]:
    """FX series group of one identity (``FX3``, ``FX2``, ``FX1``, ``FX0``)."""
    identity = str(plc_model or "").strip().upper()
    if not _in_fx_family(identity):
        return None
    for prefix, series in _FX_SERIES_PREFIXES:
        if identity.startswith(prefix):
            return series
    return None


def _resolve_member(member: str, opcode: Any, operands: Sequence[Any], context: Any,
                    assume_default_allocation: bool = False,
                    text_encoding: Optional[str] = None) -> StepWidth:
    family = target_family(member)
    if family is None:
        return _unresolved(STATUS_UNKNOWN_CPU, "Unknown PLC target " + repr(member))
    if family == "FX5U":
        return _unresolved(STATUS_NO_PROFILE,
                           "No program step-width coverage for FX5U; FX rules are not borrowed")
    if family in ("Q", "L"):
        # The Q/L stored-width algorithms stay in the GXW layer that owns the
        # native encodings; this delegation is the single implementation.
        from gxw.step_rules_q import resolve_stored_steps
        return resolve_stored_steps(opcode, operands, cpu=member, context=context,
                                    assume_default_allocation=assume_default_allocation,
                                    text_encoding=text_encoding)
    return default_step_width_catalog().resolve(opcode, operands, plc_model=member,
                                                text_encoding=text_encoding)


def instruction_step_width(opcode: Any, operands: Sequence[Any] = (), *,
                           plc_model: Any, context: Any = None,
                           assume_default_allocation: bool = False,
                           text_encoding: Optional[str] = None) -> StepWidth:
    """Shared, non-blocking expected-width API for generators and diagnostics.

    ``plc_model`` is the target actually being edited and is required: a missing
    target is reported, never replaced by FX3U. A merged menu option answers only
    when every member answers, and consistently: members that disagree are a
    conflict, and a member without coverage keeps the merged target unresolved.

    ``context`` is the saved project allocation for a Q/L target. Without it a
    D/W form answers ``requires_context`` instead of assuming an unextended
    allocation; ``assume_default_allocation=True`` states that premise
    explicitly and is recorded in the evidence. ``text_encoding`` is the project
    code page a string payload is stored in; without it such a form answers
    ``requires_context`` for ``text_encoding``.
    """
    merged_text = "" if isinstance(plc_model, (list, tuple)) else str(plc_model or "").strip().upper()
    if isinstance(plc_model, (list, tuple)):
        # An explicit member list is an internal convenience only.
        members = tuple(str(part).strip().upper() for part in plc_model if str(part).strip())
    else:
        members = target_members(plc_model)
    if len(members) > 1 or (merged_text.endswith("/") and members):
        # A merged identity is a menu option, not an arbitrary pair: a spelling
        # such as FX3U/FX1S or a trailing slash names no saved CPU.
        merged = "/".join(members)
        if merged_text not in known_menu_identities() and merged not in known_menu_identities():
            return _unresolved(STATUS_UNKNOWN_CPU,
                               f"Unsupported merged CPU identity {merged_text or merged!r}: "
                               "it is not a saved CPU menu option",
                               requirements=("plc_model",))
    if not members:
        return _unresolved(STATUS_UNKNOWN_CPU,
                           "A stored step width needs the saved CPU identity",
                           requirements=("plc_model",))
    try:
        if len(members) == 1:
            return _resolve_member(members[0], opcode, operands, context,
                                   assume_default_allocation, text_encoding)
        results = tuple((member, _resolve_member(member, opcode, operands, context,
                                                 assume_default_allocation, text_encoding))
                        for member in members)
        return _merge_members(results)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A missing/incompatible resource is reported, not silently replaced by an
        # incorrect width and not turned into a hidden model retry.
        return _unresolved(STATUS_METADATA_UNAVAILABLE,
                           "Step-width metadata unavailable: " + str(exc))


def _merge_members(results: Sequence[Tuple[str, StepWidth]]) -> StepWidth:
    unresolved = [(member, width) for member, width in results if not width.known]
    if not unresolved:
        widths = {width.steps for _member, width in results}
        if len(widths) == 1:
            return _known(next(iter(widths)), "merged_members",
                          evidence=tuple(item for _member, width in results
                                         for item in width.evidence)[:8],
                          scope="family" if any(width.scope == "family" for _m, width in results)
                          else "exact",
                          members=results)
        return _unresolved(STATUS_CONFLICT,
                           "Merged target members disagree; name the exact CPU",
                           members=results)
    statuses = {width.status for _member, width in unresolved}
    status = next((candidate for candidate in _UNRESOLVED_PRIORITY if candidate in statuses),
                  STATUS_NO_PROFILE)
    reason = "; ".join(f"{member}: {width.reason}" for member, width in unresolved)
    if len(statuses) == 1 and status in (STATUS_REQUIRES_CONTEXT, STATUS_REQUIRES_OPERANDS):
        requirements = tuple(dict.fromkeys(requirement
                                          for _member, width in unresolved
                                          for requirement in width.requirements))
        return _unresolved(status, reason, requirements=requirements, members=results)
    return _unresolved(status, reason, members=results)


@dataclass
class StepCursor:
    """Accumulate absolute steps only while every preceding width is known."""
    step: Optional[int] = 0

    @property
    def label(self) -> str:
        return "" if self.step is None else str(self.step)

    def advance(self, width: StepWidth) -> None:
        self.step = self.step + width.steps if self.step is not None and width.steps is not None else None


__all__ = ["StepWidth", "StepWidthCatalog", "StepCursor", "instruction_step_width",
           "default_step_width_catalog", "native_token_encodings", "native_lexical_overrides",
           "stored_header_width", "target_members", "target_family",
           "STATUS_KNOWN", "STATUS_REQUIRES_OPERANDS", "STATUS_REQUIRES_CONTEXT",
           "STATUS_NO_PROFILE", "STATUS_CONFLICT", "STATUS_METADATA_UNAVAILABLE",
           "STATUS_UNKNOWN_CPU", "STATUS_NOT_APPLICABLE"]

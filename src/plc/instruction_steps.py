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


#: Scope vocabulary for one width rule: ``scope: "family"`` covers every FX
#: target, ``scope: "FX3"`` (or FX2/FX1/FX0/FXU) only that series group, and an
#: explicit ``models`` list only those identities. A rule that declares none of
#: these is refused at load time: an observation taken on one CPU must not answer
#: for a CPU it was never taken on.
_SCOPE_KEYS = ("scope", "series", "models")


#: FX series groups a width entry may name instead of single CPUs.
_SERIES_GROUPS = frozenset(("FX0", "FX1", "FX2", "FX3", "FX5"))


def _scope_tokens(entry: Mapping[str, Any], name: str, label: str) -> Tuple[str, ...]:
    """Declared scope tokens, refusing an empty declaration."""
    raw = entry.get(name)
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = (raw,)
    tokens = tuple(str(item).strip().upper() for item in raw if str(item).strip())
    if not tokens:
        raise ValueError(f"Step-width entry declares an empty {name} list: " + label)
    return tokens


def _scope_spec(entry: Mapping[str, Any],
                label: str = "") -> Tuple[Tuple[str, ...], Tuple[str, ...], bool]:
    """(explicit models, series groups, family) declared by one width entry.

    One parser answers for operand rules, device-range tables, exact forms,
    variable forms and native observations, so a declared scope means the same
    thing everywhere. A series name is a series whether it is written as
    ``scope``, in ``series`` or in ``models``; a CPU identity is a model the same
    way. An empty, unknown or contradictory declaration is refused instead of
    being read as "the whole FX family".
    """
    models: list = []
    series: list = []
    family = False
    if "scope" in entry:
        declared = str(entry.get("scope") or "").strip().upper()
        if not declared:
            raise ValueError("Step-width entry declares an empty evidence scope: " + label)
        if declared == "FAMILY":
            family = True
        elif declared in _SERIES_GROUPS:
            series.append(declared)
        elif _native_profile(declared) is not None:
            models.append(declared)
        else:
            raise ValueError("Step-width entry declares an unknown evidence scope "
                             + repr(declared) + ": " + label)
    for name, noun, bucket in (("models", "model", models), ("series", "series", series)):
        for token in _scope_tokens(entry, name, label):
            if token in _SERIES_GROUPS:
                series.append(token)
            elif _native_profile(token) is not None:
                bucket.append(token)
            else:
                raise ValueError("Step-width entry declares an unknown evidence "
                                 + noun + " " + repr(token) + ": " + label)
    if family and (models or series):
        raise ValueError("Step-width entry declares both the whole FX family and "
                         "specific targets: " + label)
    if not family and not models and not series and any(
            key in entry for key in ("models", "series")):
        raise ValueError("Step-width entry declares no evidence scope: " + label)
    return tuple(dict.fromkeys(models)), tuple(dict.fromkeys(series)), family


def _scope_applies(entry: Mapping[str, Any], model: str) -> bool:
    """Whether one width rule was recorded for this CPU."""
    models, series, family = _scope_spec(entry)
    if model in models:
        return True
    group = fx_series(model)
    if group is not None and group in series:
        return True
    return bool(family and _in_fx_family(model))


def _scope_text(entry: Mapping[str, Any]) -> str:
    """How a width rule states the CPUs its evidence covers."""
    models, series, family = _scope_spec(entry)
    if family:
        return "the whole FX family"
    if models and series:
        return "/".join(models) + " (" + "/".join(series) + " series)"
    if models:
        return "/".join(models)
    return "the " + "/".join(series) + " series"


def _check_range_overlaps(entry: Mapping[str, Any], label: str) -> None:
    """Refuse one table whose own ranges overlap with a different step count.

    The same width written twice is one statement; different widths over one
    address would make the answer depend on the order the file lists them in, so
    the table is refused instead of being resolved by position.
    """
    spans = sorted((int(item["from"]), int(item.get("to", item["from"])), int(item["steps"]))
                   for item in entry.get("ranges", ()))
    for (start, end, steps), (next_start, next_end, next_steps) in zip(spans, spans[1:]):
        if next_start <= end and steps != next_steps:
            raise ValueError(f"Step-width table ranges overlap with different widths "
                             f"({start}..{end}={steps}, {next_start}..{next_end}={next_steps}): "
                             + label)


def _require_scope(entry: Mapping[str, Any], label: str) -> None:
    """Refuse a width entry that does not say which CPUs its evidence covers.

    The presence of a key is not enough: the declaration itself is parsed, so an
    empty string, an empty list or an unknown series name is refused here rather
    than widening the entry to every FX model at query time.
    """
    if not any(key in entry for key in _SCOPE_KEYS):
        raise ValueError("Step-width rule declares no evidence scope: " + label)
    _scope_spec(entry, label)


class StepWidthCatalog:
    """Derive fixed widths from exact native spellings; apply explicit rules.

    No blanket 'D + name'/'name + P' heuristics. No formula based only on arity.
    A single observed width is usable for the ordinary operand form, not proof of
    every string/index/bit-selection encoding or CPU instruction support.
    """

    def __init__(self, encodings: Mapping[str, Sequence[Any]], rules: Mapping[str, Any]):
        self.models = frozenset(rules["models"])
        for row in [*rules["rules"], *rules.get("variable_forms", ())]:
            _require_scope(row, " ".join(row.get("opcodes", ())))
        self.rules = tuple(rules["rules"])
        self.rule_opcodes = frozenset(op for rule in self.rules for op in rule["opcodes"])
        self.variable = frozenset(rules.get("variable_opcodes", ()))
        self.variable_forms = tuple(rules.get("variable_forms", ()))
        self.aliases = dict(rules.get("aliases", {}))
        self.device_ranges = tuple(rules.get("device_ranges", ()))
        for row in self.device_ranges:
            label = " ".join(row.get("opcodes", ())) or "<unnamed table>"
            _require_scope(row, label)
            _check_range_overlaps(row, label)
        declared = rules.get("observation_scopes") or {}
        default_scope = dict(declared.get("default") or {})
        if default_scope:
            _require_scope(default_scope, "native observations")
        overrides = {str(key).strip().upper(): dict(value)
                     for key, value in (declared.get("overrides") or {}).items()}
        for opcode, spec in overrides.items():
            _require_scope(spec, "native observations for " + opcode)
        # Until a resource states them, the native samples are family evidence:
        # that is the pre-existing reading, not a new claim about one model.
        self.observation_default = default_scope or {"scope": "family"}
        self.observation_scopes = MappingProxyType(overrides)
        exact = {}
        for row in rules.get("exact_forms", ()):
            opcode = self.aliases.get(str(row["opcode"]).upper(), str(row["opcode"]).upper())
            operands = tuple(_operand_text(value) for value in row.get("operands", ()))
            key = (opcode, operands)
            _require_scope(row, " ".join((opcode, *operands)))
            value = (int(row["steps"]), str(row.get("evidence") or ""), dict(row))
            if key in exact and exact[key][:2] != value[:2]:
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
        out_of_scope: list = []
        exact = self.exact_forms.get((op, args))
        if exact is not None:
            steps, evidence, spec = exact
            if _scope_applies(spec, model):
                items = (evidence,) if evidence else ()
                if not _scope_spec(spec)[2]:
                    items = items + (f"evidence scope: {_scope_text(spec)}",)
                return known(steps, "exact_native_form", evidence=items)
            out_of_scope.append(f"{op} {joined} is recorded for {_scope_text(spec)}")
        ranged = self._device_range_steps(op, args, model)
        if ranged is not None:
            status, steps, detail = ranged
            if status == "known":
                return known(steps, "device_range", evidence=(detail,))
            if status == "conflict":
                return _unresolved(STATUS_CONFLICT, detail)
            return _unresolved(STATUS_NO_PROFILE, detail)
        if op in self.rule_opcodes:
            candidates = [rule for rule in self.rules
                          if op in rule["opcodes"]
                          and len(args) == rule["arity"]
                          and re.fullmatch(rule["operands"], joined)]
            matches = [rule for rule in candidates if _scope_applies(rule, model)]
            if not matches:
                if candidates:
                    return _unresolved(
                        STATUS_NO_PROFILE,
                        f"Operand-dependent width for {op} {joined} is recorded for "
                        + " and ".join(sorted({_scope_text(rule) for rule in candidates}))
                        + f", not {model}")
                return _unresolved(STATUS_NO_PROFILE,
                                   "Operand-dependent width has no matching rule")
            widths = {rule["steps"] for rule in matches}
            if len(widths) != 1 or None in widths:
                return _unresolved(STATUS_CONFLICT, "Ambiguous or unresolved operand-width rule")
            evidence = []
            for rule in matches:
                evidence.append(rule["evidence"])
                if not _scope_spec(rule)[2]:
                    evidence.append(f"evidence scope: {_scope_text(rule)}")
            return known(next(iter(widths)), "operand_rule", evidence=tuple(evidence))
        if op in self.variable:
            form = self._variable_form_steps(op, args, text_encoding, known, model)
            if form is not None:
                return form
            scoped = {_scope_text(entry) for entry in self.variable_forms
                      if op in entry["opcodes"] and len(args) == int(entry["arity"])
                      and not _scope_applies(entry, model)}
            if scoped:
                return _unresolved(
                    STATUS_NO_PROFILE,
                    f"Variable-width form for {op} {joined} is recorded for "
                    + " and ".join(sorted(scoped)) + f", not {model}")
            return _unresolved(STATUS_REQUIRES_OPERANDS,
                               "Variable-width instruction needs an operand/encoding-specific rule",
                               requirements=("operands", "operand_encoding"))
        observations, observation_scope_note = self._applicable_observations(op, model)
        if not observations:
            reason = "No native step-width observation for " + op
            notes = [*out_of_scope, *observation_scope_note]
            if notes:
                reason += f" on {model} (" + "; ".join(notes) + ")"
            return _unresolved(STATUS_NO_PROFILE, reason)
        # Stored width is not an operand count. A binary-arity mismatch must not be
        # made to look like a known size, but never rejects the user's program.
        compatible = [entry for entry in observations if entry[1] == len(args)]
        if not compatible:
            arities = " or ".join(str(item) for item in
                                  sorted({entry[1] for entry in observations}))
            return _unresolved(
                STATUS_REQUIRES_OPERANDS,
                f"{op} only stores an observed width with {arities} operand(s) known",
                requirements=("operands",))
        sizes = {entry[0] for entry in compatible}
        if len(sizes) != 1:
            return _unresolved(STATUS_CONFLICT,
                               "Native widths disagree for this operand form: "
                               + ", ".join(str(item) for item in sorted(sizes)) + " words")
        # Strings and indexed/bit-selected operands may change the encoding.
        # Ordinary constants, direct devices and K-digit groups are covered by the
        # observed fixed applied-instruction headers. Do not guess elsewhere.
        # An index (Z/V) or a bit selector changes the operand *encoding* of an
        # applied instruction, not the number of stored words it occupies: the
        # native rows for D0Z0, K4M0Z0, D0.3 and the direct forms all carry the
        # same applied-instruction width. Contact forms are answered by the rules
        # above, where the index does widen the instruction.
        # A device token: a code letter run, an optional network/U prefix, an index
        # (single or double Z/V) and an optional bit selector. K-digit groups and
        # buffer-memory (U0\G0) operands keep the same applied width as well.
        device = r"[A-Z][A-Z0-9]*(?:\\[A-Z][A-Z0-9]*)?(?:[VZ]{1,2}[0-7])?(?:\.[0-9A-F])?"
        ordinary = (r"(?:" + device
                    + r"|K[+-]?[0-9]+(?:[VZ]{1,2}[0-7])?"
                    r"|H[0-9A-F]+(?:[VZ]{1,2}[0-7])?"
                    r"|E[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee+-][+-]?[0-9]+)?"
                    r"|K[1-8]" + device + r")")
        if any(re.fullmatch(ordinary, arg) is None for arg in args):
            return _unresolved(STATUS_REQUIRES_CONTEXT,
                               "Operand encoding is outside the fixed-width profile",
                               requirements=("operand_encoding",))
        return known(next(iter(sizes)), "native_observation",
                     evidence=tuple(entry[2] for entry in compatible))

    def _variable_form_steps(self, op: str, args: Sequence[str],
                             text_encoding: Optional[str], known,
                             model: str = "") -> Optional[StepWidth]:
        """Variable-string storage for one FX form, or ``None`` when none matches.

        The stored width is one instruction word, the destination words and two
        words per source character pair, so the payload has to be measured in the
        project code page. A missing code page is a stated requirement, not a
        coverage gap and never a guess at the payload size.
        """
        for entry in self.variable_forms:
            if op not in entry["opcodes"] or len(args) != int(entry["arity"]):
                continue
            if model and not _scope_applies(entry, model):
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
                            model: str):
        """Device-index step tables for one operand, scanned without an order bias.

        A form may need more than one table: a normal M target and a special M
        target are two tables for the same mnemonic and device, and only one of
        them can cover a given address. Every applicable table is therefore
        scanned, and the first table that does not cover the address must not end
        the search.

        Returns ``("known", steps, evidence)``, ``("conflict", None, reason)``
        for tables that disagree, ``("no_profile", None, reason)`` when every
        applicable table owns the form and none covers the address, or ``None``
        so that the other width rules can answer.
        """
        series = fx_series(model)
        candidates = []
        applicable = 0
        open_ended = False
        operand_text = ""
        for entry in self.device_ranges:
            if op not in entry["opcodes"]:
                continue
            if len(args) != int(entry.get("arity", 1)):
                continue
            if not (model in entry.get("models", ())
                    or (series is not None and series in entry.get("series", ()))):
                continue
            position = int(entry.get("operand_index", 0))
            if position >= len(args):
                continue
            match = re.fullmatch(r"([A-Z]+)([0-9]+)", args[position])
            if match is None or match.group(1) not in entry["devices"]:
                continue
            applicable += 1
            index = int(match.group(2))
            operand_text = args[position]
            for span in entry["ranges"]:
                if int(span["from"]) <= index <= int(span["to"]):
                    candidates.append((int(span["steps"]), str(entry.get("evidence") or "")))
                    break
            else:
                if entry.get("outside", "fall_through") != "gap":
                    open_ended = True
        if not candidates:
            if applicable and not open_ended:
                covered = ", ".join(
                    f"{'/'.join(entry['devices'])} {span['from']}-{span['to']}"
                    for entry in self.device_ranges
                    for span in entry.get("ranges", ())
                    if op in entry["opcodes"]
                    and (model in entry.get("models", ())
                         or (series is not None and series in entry.get("series", ()))))
                return ("no_profile", None,
                        f"{op} {operand_text} is outside the device-range step table for "
                        f"{model} (covered: {covered})")
            return None
        widths = sorted({steps for steps, _ in candidates})
        if len(widths) > 1:
            return ("conflict", None,
                    f"Device-range tables disagree about {op} {operand_text} on {model}: "
                    + "; ".join(evidence for _, evidence in candidates))
        steps, evidence = candidates[0]
        return ("known", steps, evidence)

    def observation_scope(self, opcode: str) -> Mapping[str, Any]:
        """The evidence scope recorded for one mnemonic's native observations."""
        return self.observation_scopes.get(str(opcode or "").strip().upper(),
                                           self.observation_default)

    def _applicable_observations(self, opcode: str, model: str):
        """Native observations that may answer for this target.

        Returns the entries whose evidence scope covers the target and a note
        describing the ones that do not. Both width questions ask this one filter,
        so an observation can never be usable through one query and invisible to
        the other, and a target with no applicable evidence reports a coverage gap
        even when other CPUs have samples.
        """
        entries = self.observations.get(opcode, ())
        if not entries:
            return (), ()
        spec = self.observation_scope(opcode)
        if _scope_applies(spec, model):
            return entries, ()
        return (), (f"its native observations are recorded for {_scope_text(spec)}",)

    def _owns_operandless_form(self, opcode: str, model: str) -> bool:
        """Whether a form of this mnemonic applies to the target without operands."""
        for rule in self.rules:
            if (opcode in rule["opcodes"] and rule["arity"] == 0
                    and _scope_applies(rule, model)
                    and re.fullmatch(rule["operands"], "")):
                return True
        if any(key[0] == opcode and not key[1] and _scope_applies(spec, model)
               for key, (_steps, _evidence, spec) in self.exact_forms.items()):
            return True
        return any(opcode in entry["opcodes"] and entry["arity"] == 0
                   and _scope_applies(entry, model)
                   for entry in self.variable_forms)

    def observed_forms(self) -> Mapping[str, Tuple[int, int]]:
        """Inspectable native observations, one signature per mnemonic.

        This is a corpus view: it lists the mnemonics the native headers record
        with a single width and arity, and excludes the forms another rule kind
        owns. It says nothing about whether that width is fixed; whether a target
        can answer without operands is what :meth:`mnemonic_width` decides.
        """
        governed = set(self.rule_opcodes) | set(self.variable) | set(self.aliases)
        for entry in self.device_ranges:
            governed.update(entry["opcodes"])
        result = {}
        for opcode, entries in self.observations.items():
            signatures = {(width, arity) for width, arity, _ in entries}
            if len(signatures) == 1 and opcode not in governed:
                result[opcode] = next(iter(signatures))
        return MappingProxyType(result)

    def mnemonic_width(self, opcode: Any, *, plc_model: str) -> StepWidth:
        """Width of one mnemonic when its operands are not supplied.

        Only an instruction that takes no operands has a width of its own: every
        other stored width is a function of the operand encodings, so native
        samples that happen to agree on one number do not make the mnemonic
        fixed. Operand rules, device-range tables, exact operand forms and
        variable forms answer "needs the operands" here, on the same target
        scoping the instance query uses.
        """
        model = str(plc_model or "").strip().upper()
        scope = "exact"
        if model not in self.models:
            if not _in_fx_family(model):
                return _unresolved(STATUS_NO_PROFILE,
                                   "No step-width catalogue for CPU "
                                   + (model or "<missing target>"))
            scope = "family"
        op = str(opcode).strip().upper()
        op = self.aliases.get(op, op)
        # An instruction with a zero-operand form answers from the same rules an
        # instance does: the form is evaluated with no operands instead of being
        # called operand-dependent merely because a rule owns its mnemonic.
        if self._owns_operandless_form(op, model):
            return self.resolve(op, (), plc_model=model)
        owners = []
        if any(op in rule["opcodes"] and _scope_applies(rule, model) for rule in self.rules):
            owners.append("an operand-width rule")
        if any(op in entry["opcodes"] and _scope_applies(entry, model)
               for entry in self.device_ranges):
            owners.append("a device-range step table")
        if any(key[0] == op and _scope_applies(spec, model)
               for key, (_steps, _evidence, spec) in self.exact_forms.items()):
            owners.append("an exact operand form")
        if any(op in entry["opcodes"] and _scope_applies(entry, model)
               for entry in self.variable_forms):
            owners.append("a variable-width form")
        if owners:
            return _unresolved(STATUS_REQUIRES_OPERANDS,
                               f"{op} is covered by {owners[0]} on {model}: its stored "
                               "width depends on the operands",
                               requirements=("operands",))
        observations, observation_scope_note = self._applicable_observations(op, model)
        if not observations:
            reason = "No program step-width coverage for " + op + " on " + model
            if observation_scope_note:
                reason += " (" + "; ".join(observation_scope_note) + ")"
            return _unresolved(STATUS_NO_PROFILE, reason)
        signatures = {(width, arity) for width, arity, _ in observations}
        if len(signatures) != 1:
            return _unresolved(STATUS_REQUIRES_OPERANDS,
                               f"{op} stores several widths on {model}: the operands decide "
                               "which one applies",
                               requirements=("operands",))
        steps, arity = next(iter(signatures))
        if arity:
            return _unresolved(STATUS_REQUIRES_OPERANDS,
                               f"{op} only stores a width with its {arity} operand(s) known",
                               requirements=("operands",))
        items = (f"native stored header {observations[0][2]} for {op}: an instruction with "
                 "no operands",)
        if scope == "family":
            items += (f"Shared FX instruction-width rule applied to {model}; verified on "
                      f"{'/'.join(sorted(self.models))} manual evidence and native samples",)
        return _known(int(steps), "native_observation", evidence=items, scope=scope)


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
        # The spelling may differ from the joined members only by spacing: a
        # leading or trailing slash, or a pair of unrelated CPUs, names no saved
        # option even when every part of it is a real CPU.
        spelling = merged_text.removesuffix("(REMOTEI/O)").replace(" ", "")
        if spelling != merged:
            return None, (f"Unsupported CPU identity spelling {merged_text!r}: "
                          "it is not a saved CPU menu option")
        if len(members) > 1 and merged not in known_menu_identities():
            return None, (f"Unsupported merged CPU identity {merged_text or merged!r}: "
                          "it is not a saved CPU menu option")
    if not members:
        return None, "A stored step width needs the saved CPU identity"
    return members, None


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
                    text_encoding: Optional[str] = None,
                    identity: Optional[str] = None) -> StepWidth:
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
                                    text_encoding=text_encoding,
                                    target_identity=identity or member)
    return default_step_width_catalog().resolve(opcode, operands, plc_model=member,
                                                text_encoding=text_encoding)


def mnemonic_step_width(opcode: Any, operands: Sequence[Any] = (),
                        *, plc_model: Optional[str] = None, context: Any = None,
                        assume_default_allocation: bool = False,
                        text_encoding: Optional[str] = None) -> StepWidth:
    """Width of one mnemonic when its operands are not supplied.

    Core answers two width questions from one place: :func:`instruction_step_width`
    takes the operands and computes that instance, and this one says whether the
    mnemonic has a width of its own, needs its operands, or has no coverage. The
    identity validation, the member handling and the rule kinds behind both are
    the same, so a mnemonic cannot look fixed here and operand-dependent there, and
    a merged menu option keeps its members and its saved allocation context.
    """
    members, identity_error = target_identity_state(plc_model)
    if members is None:
        return _unresolved(STATUS_UNKNOWN_CPU, identity_error or "",
                           requirements=("plc_model",))
    families: dict = {}
    for member in members:
        family = target_family(member)
        if family is None:
            return _unresolved(STATUS_UNKNOWN_CPU, "Unknown PLC target " + repr(member))
        families[member] = family
    op = str(opcode or "").strip().upper()
    if not op:
        return _unresolved(STATUS_NOT_APPLICABLE, "Instruction opcode is missing")
    if len(set(families.values())) > 1:
        return _unresolved(STATUS_NO_PROFILE,
                           "No single program step-width family covers "
                           + "/".join(families) + " (opcode-only target)")
    if set(families.values()) == {"FX"}:
        catalogue = default_step_width_catalog()
        answers = [(member, catalogue.mnemonic_width(op, plc_model=member))
                   for member in members]
        return answers[0][1] if len(answers) == 1 else _merge_members(answers)
    # Q/L: the same stored-width algorithm answers a zero-operand form, given the
    # full target identity, so a merged menu option is not split into members here.
    width = instruction_step_width(op, list(operands), plc_model=plc_model,
                                   context=context,
                                   assume_default_allocation=assume_default_allocation,
                                   text_encoding=text_encoding)
    if not width.known and not _registry_knows(op):
        # A mnemonic the registry does not know is missing coverage here, not a
        # missing operand: the stored-width algorithm cannot tell those apart.
        return _unresolved(STATUS_NO_PROFILE,
                           "No program step-width coverage for " + op + " on "
                           + "/".join(families) + " (opcode-only target)")
    return width


def _registry_knows(opcode: str) -> bool:
    """Whether the instruction registry knows this mnemonic at all."""
    from .instructions import DEFAULT_INSTRUCTION_REGISTRY

    return DEFAULT_INSTRUCTION_REGISTRY.is_known(opcode)


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
    members, identity_error = target_identity_state(plc_model)
    if members is None:
        return _unresolved(STATUS_UNKNOWN_CPU, identity_error or "",
                           requirements=("plc_model",))
    try:
        if len(members) == 1:
            return _resolve_member(members[0], opcode, operands, context,
                                   assume_default_allocation, text_encoding)
        results = tuple((member, _resolve_member(member, opcode, operands, context,
                                                 assume_default_allocation, text_encoding,
                                                 identity="/".join(members)))
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

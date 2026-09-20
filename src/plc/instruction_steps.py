"""Source-backed FX program widths. This is storage metadata, not validation.

All generated step labels use this module. Native token headers are observations
of stored widths: readers must preserve them, including positive but incorrect
widths, rather than silently replacing them with a calculated width.

The shared catalogue supplies exact native mnemonic forms (including D/P)
and the operand-dependent basic-instruction rules in the same resource. Unknown
widths are None, never a guessed one-step instruction or a generation gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Tuple


NATIVE_CATALOG = "fx3u_step_widths.json"


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
    steps: Optional[int]
    source: str
    reason: str = ""
    evidence: Tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.steps is not None


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
    A single observed width is usable for the ordinary operand form, not proof
    of every string/index/bit-selection encoding or CPU instruction support.
    """

    def __init__(self, encodings: Mapping[str, Sequence[Any]], rules: Mapping[str, Any]):
        self.models = frozenset(rules["models"])
        self.rules = tuple(rules["rules"])
        self.rule_opcodes = frozenset(op for rule in self.rules for op in rule["opcodes"])
        self.variable = frozenset(rules.get("variable_opcodes", ()))
        self.aliases = dict(rules.get("aliases", {}))
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

    def resolve(self, opcode: Any, operands: Sequence[Any] = (), *, plc_model: str = "FX3U") -> StepWidth:
        model = str(plc_model or "FX3U").strip().upper()
        if model not in self.models:
            return StepWidth(None, "unknown", "No step-width catalogue for CPU " + model)
        op = str(opcode).strip().upper()
        op = self.aliases.get(op, op)
        args = tuple(_operand_text(value) for value in operands)
        joined = " ".join(args)
        exact = self.exact_forms.get((op, args))
        if exact is not None:
            steps, evidence = exact
            return StepWidth(steps, "exact_native_form", evidence=(evidence,) if evidence else ())
        if op in self.rule_opcodes:
            matches = [rule for rule in self.rules
                       if op in rule["opcodes"]
                       and len(args) == rule["arity"]
                       and re.fullmatch(rule["operands"], joined)]
            if not matches:
                return StepWidth(None, "unknown", "Operand-dependent width has no matching rule")
            widths = {rule["steps"] for rule in matches}
            if len(widths) != 1 or None in widths:
                return StepWidth(None, "unknown", "Ambiguous or unresolved operand-width rule")
            return StepWidth(next(iter(widths)), "operand_rule", evidence=tuple(rule["evidence"] for rule in matches))
        if op in self.variable:
            return StepWidth(None, "unknown", "Variable-width instruction needs an operand/encoding-specific rule")
        observations = self.observations.get(op, ())
        if not observations:
            return StepWidth(None, "unknown", "No native step-width observation for " + op)
        # Stored width is not an operand count. A binary-arity mismatch must not
        # be made to look like a known size, but never rejects the user's program.
        compatible = [entry for entry in observations if entry[1] == len(args)]
        sizes = {entry[0] for entry in compatible}
        if len(sizes) != 1:
            return StepWidth(None, "unknown", "Native widths are missing or ambiguous for this operand form")
        # Strings and indexed/bit-selected operands may change the encoding.
        # Ordinary constants, direct devices and K-digit groups are covered by
        # the observed fixed applied-instruction headers. Do not guess elsewhere.
        ordinary = r"(?:[K][+-]?[0-9]+|H[0-9A-F]+|E[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee+-][+-]?[0-9]+)?|[A-Z]+[0-9]+|K[1-8][XYMS][0-9]+)"
        if any(re.fullmatch(ordinary, arg) is None for arg in args):
            return StepWidth(None, "unknown", "Operand encoding is outside the fixed-width profile")
        return StepWidth(next(iter(sizes)), "native_observation", evidence=tuple(entry[2] for entry in compatible))

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


def instruction_step_width(opcode: Any, operands: Sequence[Any] = (), *, plc_model: str = "FX3U") -> StepWidth:
    """Shared, non-blocking expected-width API for generators and diagnostics."""
    try:
        return default_step_width_catalog().resolve(opcode, operands, plc_model=plc_model)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A missing/incompatible resource is reported, not silently replaced by
        # an incorrect width and not turned into a hidden model retry.
        return StepWidth(None, "unknown", "Step-width metadata unavailable: " + str(exc))


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
           "default_step_width_catalog", "native_token_encodings", "native_lexical_overrides", "stored_header_width"]

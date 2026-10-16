"""Typed parameter identity and separate review/generation views.

This adapter does not validate PLC behavior, infer an address or translate an
answer. Unknown identities remain generic parameters. Malformed optional type
metadata cannot acquire a hardware field or block an otherwise valid request.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter, ValidationError


class Identity(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str = ""
    name: str = ""
    semantic_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    unit: str | None = None


class TextValue(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    value_kind: Literal["text"]
    value: StrictStr


class ChoiceValue(TextValue):
    value_kind: Literal["choice"]


class NumberValue(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)
    value_kind: Literal["number"]
    value: StrictInt | StrictFloat


class BooleanValue(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    value_kind: Literal["boolean"]
    value: StrictBool


ParameterValue = Annotated[TextValue | ChoiceValue | NumberValue | BooleanValue, Field(discriminator="value_kind")]
_VALUE = TypeAdapter(ParameterValue)


class TextOrigin(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    kind: Literal["review_choices", "analysis_overview"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GenerationParameter(Identity):
    source: str = ""
    value: StrictStr | StrictInt | StrictFloat | StrictBool
    value_kind: Literal["text", "choice", "number", "boolean"] | None = None
    note: str | None = None


class ReviewParameter(Identity):
    source: str = ""
    typed_value: ParameterValue = Field(exclude=True)
    options: list[str] = Field(default_factory=list, exclude=True)
    suggested_default: str | None = Field(default=None, exclude=True)
    note: str = Field(default="", exclude=True)
    note_provenance: TextOrigin | None = Field(default=None, exclude=True)

    def generation(self, *, explicit_kind=False) -> dict:
        note = self.note
        if matches_origin(note, self.note_provenance, "review_choices"):
            note = ""
        # Read old form notes only when they are EXACTLY reconstructible from
        # the still-present options/default. Never strip words from user prose.
        legacy = list(self.options)
        if self.suggested_default:
            legacy.append(f"AI建议：{self.suggested_default}（尚未确认）")
        if self.note_provenance is None and legacy and note == " / ".join(legacy):
            note = ""
        return GenerationParameter(
            **self.model_dump(), value=self.typed_value.value,
            value_kind=self.typed_value.value_kind if explicit_kind else None,
            note=note or None,
        ).model_dump(exclude_none=True)


def text_origin(text: str, kind: str) -> dict:
    return TextOrigin(kind=kind, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()).model_dump()


def matches_origin(text, origin, kind):
    if not isinstance(text, str):
        return False
    try:
        parsed = origin if isinstance(origin, TextOrigin) else TextOrigin.model_validate(origin)
    except ValidationError:
        return False
    return parsed.kind == kind and parsed.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def _actual_kind(value):
    return "boolean" if type(value) is bool else "number" if type(value) in (int, float) else "text"


def read_parameter(raw):
    """Return a detached typed view and whether optional binding metadata is valid."""
    if not isinstance(raw, Mapping):
        return None, False
    value = raw.get("value", "")
    if value is None:
        value = ""
    supplied_kind = raw.get("value_kind") or _actual_kind(value)
    # HTML form inputs are strings. Parse only an explicitly declared primitive,
    # using JSON's grammar; never bool("false"), float("5 seconds"), or 0 -> False.
    wire_value = value
    if isinstance(value, str) and supplied_kind in {"number", "boolean"} and value.strip():
        try:
            wire_value = json.loads(value)
        except ValueError:
            pass
    valid = True
    try:
        identity = Identity.model_validate(raw)
        typed = _VALUE.validate_python({"value_kind": supplied_kind, "value": wire_value})
    except ValidationError:
        valid = False
        try:
            # Preserve the original scalar; discard only uncertain ownership.
            identity = Identity.model_validate({k: raw[k] for k in ("id", "name", "unit") if k in raw})
            typed = _VALUE.validate_python({"value_kind": _actual_kind(value), "value": value})
        except ValidationError:
            return None, False
    try:
        provenance = TextOrigin.model_validate(raw.get("note_provenance"))
    except ValidationError:
        provenance = None
    return ReviewParameter(
        **identity.model_dump(), typed_value=typed,
        source=raw.get("source") if isinstance(raw.get("source"), str) else "",
        options=[x for x in raw.get("options", []) if isinstance(x, str)]
                if isinstance(raw.get("options"), (list, tuple)) else [],
        suggested_default=str(raw["suggested_default"]) if raw.get("suggested_default") is not None else None,
        note=raw.get("note") if isinstance(raw.get("note"), str) else "",
        note_provenance=provenance,
    ), valid


def parameter_metadata(raw):
    # A pending numeric/boolean question has no answer yet. Its declared type
    # and identity still have to reach the review editor without a fake default.
    if isinstance(raw, Mapping) and raw.get("value") in (None, ""):
        try:
            identity = Identity.model_validate(raw)
        except ValidationError:
            return {}
        metadata = {key: getattr(identity, key) for key in ("semantic_key", "unit")
                    if key in raw and getattr(identity, key) is not None}
        if raw.get("value_kind") in {"text", "choice", "number", "boolean"}:
            metadata["value_kind"] = raw["value_kind"]
        try:
            metadata["note_provenance"] = TextOrigin.model_validate(raw.get("note_provenance")).model_dump()
        except ValidationError:
            pass
        return metadata
    view, valid = read_parameter(raw)
    if view is None:
        return {}
    metadata = {key: getattr(view, key) for key in ("semantic_key", "unit")
                if valid and key in raw and getattr(view, key) is not None}
    if valid and raw.get("value_kind"):
        metadata["value_kind"] = view.typed_value.value_kind
    if view.note_provenance is not None:
        metadata["note_provenance"] = view.note_provenance.model_dump()
    return metadata


def parameter_selections(rows):
    """Index current answers by exact id, semantic key or label; no fuzzy match."""
    candidates = {}
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        value = row.get("value")
        for key in (row.get("id"), row.get("semantic_key"), row.get("name")):
            if isinstance(key, str) and key.strip():
                candidates.setdefault(key.strip(), []).append(value)
    return {key: values[0] for key, values in candidates.items()
            if all(type(value) is type(values[0]) and value == values[0] for value in values)}


def dependency_state(condition, values, *, _depth=0):
    """True/False for understood dependencies, None for missing/unknown evidence.

    Negative-only rules are useful (e.g. not a drive-internal home). They must
    not require a positive matcher, and zero/False are not missing answers.
    Unknown conditions never erase a confirmed value from generation context.
    """
    if not isinstance(condition, Mapping) or _depth > 12:
        return None
    for key in ("all", "any"):
        if key in condition:
            group = condition[key]
            if not isinstance(group, list) or not group:
                return None
            states = [dependency_state(c, values, _depth=_depth+1) for c in group]
            if key == "all":
                return False if False in states else None if None in states else True
            return True if True in states else None if None in states else False
    controller = condition.get("parameter")
    if not isinstance(controller, str) or controller not in values:
        return None
    selected = values[controller]
    if selected is None or isinstance(selected, str) and not selected.strip():
        return None
    text = str(selected).strip().casefold()
    matches = []
    for key in ("equals", "in", "contains_any", "contains", "not_equals", "not_contains"):
        if key not in condition:
            continue
        choices = condition[key]
        choices = choices if isinstance(choices, (list, tuple, set)) else [choices]
        choices = [str(c).strip().casefold() for c in choices if c is not None and str(c).strip()]
        if not choices:
            return None
        matched = any(c in text if key in {"contains_any", "contains", "not_contains"}
                      else c == text for c in choices)
        matches.append(not matched if key in {"not_equals", "not_contains"} else matched)
    return all(matches) if matches else None


def parameter_is_applicable(parameter, rows):
    condition = parameter.get("required_when") if isinstance(parameter, Mapping) else None
    return not isinstance(condition, Mapping) or dependency_state(condition, parameter_selections(rows)) is not False


def generation_parameters(rows):
    """No form choices/defaults in model input; unknown user notes are retained."""
    result = []
    for raw in rows if isinstance(rows, list) else []:
        view, valid = read_parameter(raw)
        if view is None or view.typed_value.value == "" or not parameter_is_applicable(raw, rows):
            continue
        projected = view.generation(explicit_kind=valid and bool(raw.get("value_kind")))
        # Keep legacy absence of optional scalar fields, rather than inventing
        # ids/names/sources on each projection. Zero and False are real values.
        for key in ("id", "name", "source"):
            if key not in raw:
                projected.pop(key, None)
        result.append(projected)
    return result


def hardware_parameter_id(raw, labels):
    """Map only a declared semantic key, registered id, or exact legacy label.

    `hardware.<registered id>` is the shared Core namespace. A different explicit
    namespace wins over a legacy id, so `transport.mode` never binds a drive.
    """
    if not isinstance(raw, Mapping):
        return ""
    if raw.get("value") in (None, ""):
        try:
            Identity.model_validate(raw)
        except ValidationError:
            return ""
        if raw.get("value_kind") not in {None, "text", "choice", "number", "boolean"}:
            return ""
    else:
        _, valid = read_parameter(raw)
        if not valid:
            return ""
    if "semantic_key" in raw:
        key = raw.get("semantic_key")
        # Explicit registered aliases only. In particular transport.mode or an
        # arbitrary positioning.* field cannot acquire a hardware owner.
        aliases = {"hardware.base_unit_output_type": "output_type",
                   "positioning.interface": "positioning_implementation",
                   "positioning.homing_signal_type": "homing_method"}
        field = aliases.get(key) or next((name for name in labels if key == "hardware." + name), "")
        return field if field in labels else ""
    identifier = str(raw.get("id") or "").strip()
    if identifier in labels:
        return identifier
    text = str(raw.get("question") or raw.get("name") or "").strip().rstrip("?？").strip().casefold()
    matches = [key for key, label in labels.items() if text == str(label).casefold()]
    if len(matches) == 1:
        return matches[0]
    # Frozen, exact pre-identity field labels; never substring/fuzzy matching.
    legacy = {
        "变频器频率给定方式": "control_method",
        "请填写所接驱动设备的具体订货号": "drive_model",
        "定位扩展模块完整型号": "positioning_module_model",
        "伺服驱动器控制方式": "motion_control_method",
        "步进驱动器控制方式": "motion_control_method",
        "伺服驱动器完整型号": "motion_drive_model",
        "步进驱动器完整型号": "motion_drive_model",
        "伺服驱动器端子映射": "motion_wiring_mapping",
        "步进驱动器端子映射": "motion_wiring_mapping",
        "高速输出适配器数量": "positioning_module_quantity",
    }
    identifier = legacy.get(text, "")
    return identifier if identifier in labels else ""


def parameter_value(raw):
    """Decode declared form primitives at the Core boundary; preserve legacy text."""
    view, valid = read_parameter(raw)
    if view is not None and valid and raw.get("value_kind") in {"number", "boolean"}:
        return view.typed_value.value
    return str(raw.get("value", "")).strip()


def generation_parameter_view(specification):
    """Prepare typed current values before the dependency-neutral wire allowlist.

    Called by the shared application projector and advisory specialist context.
    The low-level generation contract stays stdlib-only. Stored specs are untouched.
    """
    if not isinstance(specification, Mapping):
        return specification
    result = copy.deepcopy(dict(specification))
    if isinstance(result.get("parameters"), list):
        result["parameters"] = generation_parameters(result["parameters"])
    if matches_origin(result.get("summary"), result.get("summary_provenance"), "analysis_overview"):
        result.pop("summary", None)
    return result

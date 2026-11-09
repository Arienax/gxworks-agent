"""Ground Agent-A reports of user-fixed low-level PLC constraints.

Free-form language understanding belongs to Agent A. Core verifies only claim
shape, exact current-request evidence, PLC lexical identities, and instruction
form. It never classifies natural-language directive wording.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping

from plc.device_identity import DEVICE_TOKEN_RE, canonical_device
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY


OPERATIONS = frozenset({"require", "forbid", "clear"})
SCOPES = frozenset({"global", "scoped", "ambiguous"})
CLEAR_CATEGORIES = frozenset({"opcodes", "devices", "instruction_instances", "all"})
_STRINGS = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
TARGET_CONTRACTS = {
    "opcode": {"operations": ("require", "forbid", "clear"), "fields": {"values": _STRINGS},
               "example": {"kind": "opcode", "values": ["SFTL"]}},
    "device": {"operations": ("require", "forbid", "clear"), "fields": {"values": _STRINGS},
               "example": {"kind": "device", "values": ["M8011"]}},
    "instruction_instance": {"operations": ("require", "clear"),
                             "fields": {"opcode": {"type": "string", "minLength": 1}, "operands": _STRINGS},
                             "example": {"kind": "instruction_instance", "opcode": "SFTL",
                                         "operands": ["M10", "M100", "K128", "K1"]}},
    "category": {"operations": ("clear",), "fields": {"value": {"enum": sorted(CLEAR_CATEGORIES)}},
                 "example": {"kind": "category", "value": "all"}},
}
TARGET_KINDS = frozenset(TARGET_CONTRACTS)
_OPERAND = re.compile(
    r"^(?:K[+-]?\d+|H[0-9A-F]+|(?:SM|SD|TS|TC|CS|CC|ER|X|Y|M|S|T|C|D|R|V|Z|P|I)"
    r"\d+(?:[VZ]\d+)?|[+-]?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_TOKEN_BOUNDARY = r"A-Za-z0-9_.$@+<>!=\-"
_INSTANCE_SEPARATOR = r"[\s,，/、:：()（）\[\]{}]+"


def _fold_ws(value):
    return " ".join(str(value or "").split())


def _claim_id(frame):
    payload = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "LC-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _token_in_evidence(token, evidence):
    pattern = re.compile(rf"(?<![{_TOKEN_BOUNDARY}]){re.escape(str(token))}(?![{_TOKEN_BOUNDARY}])", re.IGNORECASE)
    return any(pattern.search(item) for item in evidence)


def _device_in_evidence(device, evidence):
    # Retain the stricter claim token boundaries while comparing device aliases.
    return any(canonical_device(match.group(0).upper()) == device
               and _token_in_evidence(match.group(0), [text])
               for text in evidence for match in DEVICE_TOKEN_RE.finditer(text))


def explicit_target_contracts():
    """The same target descriptions serve validators and model delivery."""
    return {kind: {"allowed_operations": list(item["operations"]),
                   "required_fields": ["kind", *item["fields"]],
                   "schema": {"type": "object", "properties": {"kind": {"enum": [kind]}, **item["fields"]},
                              "required": ["kind", *item["fields"]]},
                   "example": copy.deepcopy(item["example"])}
            for kind, item in copy.deepcopy(TARGET_CONTRACTS).items()}


def explicit_constraint_protocol():
    """Compact, literal examples, with operation rules from the Core table."""
    return "\n".join(json.dumps(item["example"], ensure_ascii=False, separators=(",", ":"))
                     + " " + "|".join(item["operations"])
                     for item in TARGET_CONTRACTS.values())


def _canonical_opcode(value, plc_model):
    opcode = str(value or "").strip().upper()
    if not opcode:
        return None
    resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
    if resolved is None or not resolved.spec.supports_cpu(str(plc_model or "").upper()):
        return None
    return opcode


def _canonical_device(value):
    device = canonical_device(str(value or "").strip().upper())
    if isinstance(device, str) and DEVICE_TOKEN_RE.fullmatch(device):
        return device
    return None


def _instruction_instance(value, plc_model):
    if not isinstance(value, Mapping):
        return None
    opcode = _canonical_opcode(value.get("opcode"), plc_model)
    operands = value.get("operands")
    if not opcode or not isinstance(operands, (list, tuple)):
        return None
    operands = [str(item or "").strip() for item in operands]
    if not operands or any(not item or not _OPERAND.fullmatch(item) for item in operands):
        return None
    resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
    spec = resolved.spec
    minimum = spec.min_operands if spec.min_operands is not None else (len(spec.operands) if spec.operands else None)
    maximum = spec.max_operands if spec.max_operands is not None else (len(spec.operands) if spec.operands else None)
    if minimum is not None and len(operands) < minimum:
        return None
    if maximum is not None and len(operands) > maximum:
        return None
    return {"opcode": opcode, "operands": operands}


def _instance_in_evidence(instance, evidence):
    pieces = [re.escape(instance["opcode"]), *(re.escape(item) for item in instance["operands"])]
    pattern = re.compile(rf"(?<![{_TOKEN_BOUNDARY}])" + _INSTANCE_SEPARATOR.join(pieces) + rf"(?![{_TOKEN_BOUNDARY}])", re.IGNORECASE)
    return any(pattern.search(item) for item in evidence)


def explicit_constraint_claim_details(value, path="$.explicit_constraint_claims"):
    """Validate Agent-A claim shape only; never interpret sentence meaning."""
    if value is None:
        return []
    details = []
    def add(location, message, actual, code="invalid_claim_shape", **expected):
        details.append({"path": location, "code": code, "message": message,
                        "actual": copy.deepcopy(actual), **copy.deepcopy(expected)})
    if not isinstance(value, list):
        add(path, "expected an array", value, schema={"type": "array"})
        return details
    for index, claim in enumerate(value):
        root = f"{path}[{index}]"
        if not isinstance(claim, Mapping):
            add(root, "claim must be an object", claim, schema={"type": "object"})
            continue
        operation = str(claim.get("operation") or "").strip().casefold()
        if operation not in OPERATIONS:
            add(root + ".operation", "expected require, forbid, or clear", claim.get("operation"), allowed_types=sorted(OPERATIONS))
        scope = str(claim.get("scope") or "").strip().casefold()
        if scope not in SCOPES:
            add(root + ".scope", "expected global, scoped, or ambiguous", claim.get("scope"), allowed_types=sorted(SCOPES))
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
            add(root + ".evidence", "one or more exact user-text spans are required", evidence, schema=_STRINGS)
        target = claim.get("target")
        if not isinstance(target, Mapping):
            add(root + ".target", "object is required", target, allowed_types=sorted(TARGET_KINDS), contracts=explicit_target_contracts())
            continue
        kind = str(target.get("kind") or "").strip().casefold()
        if kind not in TARGET_KINDS:
            add(root + ".target.kind", "unsupported target kind", target,
                allowed_types=sorted(TARGET_KINDS), contracts=explicit_target_contracts())
            continue
        contract = TARGET_CONTRACTS[kind]
        expected = explicit_target_contracts()[kind]
        if operation in OPERATIONS and operation not in contract["operations"]:
            message = ("category targets are only valid for clear" if kind == "category"
                       else "exact instruction instances support require/clear only")
            add(root + ".target", message, target, **expected)
        for field, schema in contract["fields"].items():
            actual = target.get(field)
            if schema.get("type") == "array":
                valid = isinstance(actual, list) and bool(actual) and all(isinstance(v, str) and v.strip() for v in actual)
                message = "expected a non-empty string array"
            elif "enum" in schema:
                valid = str(actual or "").strip().casefold() in schema["enum"]
                message = "expected " + ", ".join(schema["enum"])
            else:
                valid = isinstance(actual, str) and bool(actual.strip())
                message = "non-empty string is required"
            if not valid:
                add(root + ".target." + field, message, actual, **expected)
    return details


def explicit_constraint_claim_violations(value, path="$.explicit_constraint_claims"):
    return [item["path"] + ": " + item["message"] for item in explicit_constraint_claim_details(value, path)]


def normalize_explicit_claim_representations(value):
    """Only lossless known container/type aliases; never supply evidence."""
    result, changes = [], []
    if not isinstance(value, list):
        return copy.deepcopy(value), changes
    aliases = {"opcodes": "opcode", "devices": "device", "instruction_instances": "instruction_instance",
               "instruction_instance": "instruction_instance"}
    for index, raw in enumerate(value):
        claim = copy.deepcopy(raw)
        target = claim.get("target") if isinstance(claim, Mapping) else None
        kind = (target.get("kind") or target.get("category")) if isinstance(target, Mapping) else None
        key = "kind" if isinstance(target, Mapping) and "kind" in target else "category"
        if (isinstance(kind, str) and kind in aliases and set(target) == {key, "values"}
                and isinstance(target.get("values"), list) and target["values"]):
            if aliases[kind] == "instruction_instance" and all(isinstance(v, Mapping) and set(v) == {"opcode", "operands"} for v in target["values"]):
                replacements = [{**claim, "target": {"kind": "instruction_instance", **v}} for v in target["values"]]
            elif kind in {"opcodes", "devices"}:
                replacements = [{**claim, "target": {"kind": aliases[kind], "values": target["values"]}}]
            else:
                replacements = [claim]
            if replacements != [claim]:
                changes.append({"path": f"/explicit_constraint_claims/{index}", "reason": "target_representation", "count": len(replacements)})
            result.extend(replacements)
        else:
            result.append(claim)
    return result, changes


def explicit_claim_anchors(claim, user_text, plc_model="FX3U"):
    """Lexical identities already present in a reported target and current text.

    This does not classify directive language or add targets absent from a claim.
    A repair must retain these identities and the claim's valid operation/scope.
    """
    target = claim.get("target") or {} if isinstance(claim, Mapping) else {}
    source = [_fold_ws(user_text)]
    anchors = []
    if isinstance(target, Mapping):
        if "opcode" in target and "operands" in target:
            instance = _instruction_instance(target, plc_model)
            if instance and _instance_in_evidence(instance, source):
                anchors.append({"kind": "instruction_instance", **instance})
        else:
            values = target.get("values") or []
            values = [values] if isinstance(values, str) else values if isinstance(values, list) else []
            for value in values:
                if isinstance(value, Mapping):
                    instance = _instruction_instance(value, plc_model)
                    if instance and _instance_in_evidence(instance, source):
                        anchors.append({"kind": "instruction_instance", **instance})
                    continue
                opcode, device = _canonical_opcode(value, plc_model), _canonical_device(value)
                if opcode and _token_in_evidence(opcode, source):
                    anchors.append({"kind": "opcode", "values": [opcode]})
                elif device and _device_in_evidence(device, source):
                    anchors.append({"kind": "device", "values": [device]})
    return anchors


def compile_explicit_constraint_claims(value, user_text, plc_model="FX3U"):
    """Ground claims and emit structured edits for global user-fixed constraints."""
    shape = explicit_constraint_claim_details(value)
    if shape:
        return {"operations": [], "accepted": [], "rejected": [
            {"reason": "invalid_claim_shape", "detail": item["path"] + ": " + item["message"], "details": [item]}
            for item in shape]}
    source_text = _fold_ws(user_text)
    accepted, rejected, operations = [], [], []
    for index, raw in enumerate(value or []):
        claim = copy.deepcopy(dict(raw))
        evidence = [_fold_ws(item) for item in claim.get("evidence") or []]
        if any(not item or item not in source_text for item in evidence):
            rejected.append({"index": index, "reason": "evidence_not_in_current_request", "details": [{
                "path": f"$.explicit_constraint_claims[{index}].evidence", "code": "evidence_not_in_current_request",
                "message": "copy exact evidence from the current request; do not paraphrase", "actual": claim.get("evidence"),
                "schema": copy.deepcopy(_STRINGS)}]})
            continue
        operation = str(claim.get("operation") or "").strip().casefold()
        scope = str(claim.get("scope") or "").strip().casefold()
        target = dict(claim.get("target") or {})
        kind = str(target.get("kind") or "").strip().casefold()
        normalized_target = {"kind": kind}
        grounded, op = True, None
        if kind == "opcode":
            values = []
            for item in target.get("values") or []:
                opcode = _canonical_opcode(item, plc_model)
                if not opcode or not _token_in_evidence(opcode, evidence):
                    grounded = False
                    break
                if opcode not in values:
                    values.append(opcode)
            normalized_target["values"] = values
            op = {"operation": operation, "kind": "opcode", "values": values}
        elif kind == "device":
            values = []
            for item in target.get("values") or []:
                device = _canonical_device(item)
                if not device or not _device_in_evidence(device, evidence):
                    grounded = False
                    break
                if device not in values:
                    values.append(device)
            normalized_target["values"] = values
            op = {"operation": operation, "kind": "device", "values": values}
        elif kind == "instruction_instance":
            instance = _instruction_instance(target, plc_model)
            if not instance or not _instance_in_evidence(instance, evidence):
                grounded = False
            else:
                normalized_target.update(instance)
                op = {"operation": operation, "kind": "instruction_instance", "instance": instance}
        elif kind == "category":
            category = str(target.get("value") or "").strip().casefold()
            normalized_target["value"] = category
            op = {"operation": "clear", "kind": "category", "value": category}
        if not grounded:
            rejected.append({"index": index, "reason": "target_not_grounded_in_evidence", "details": [{
                "path": f"$.explicit_constraint_claims[{index}].target", "code": "target_not_grounded_in_evidence",
                "message": "each target must be a PLC lexical identity present in this claim's exact evidence; device values cannot be methods",
                "actual": copy.deepcopy(target), **explicit_target_contracts()[kind]}]})
            continue
        frame = {"operation": operation, "scope": scope, "target": normalized_target, "evidence": evidence}
        item = {"claim_id": _claim_id(frame), **frame}
        if scope == "global":
            item["projection_status"] = "applied"
            operations.append(op)
        else:
            item["projection_status"] = "non_global"
        accepted.append(item)
    return {"operations": operations, "accepted": accepted, "rejected": rejected}


__all__ = ["compile_explicit_constraint_claims", "explicit_constraint_claim_violations",
           "explicit_constraint_claim_details", "explicit_target_contracts", "explicit_constraint_protocol",
           "normalize_explicit_claim_representations", "explicit_claim_anchors"]

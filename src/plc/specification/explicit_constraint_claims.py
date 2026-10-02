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
TARGET_KINDS = frozenset({"opcode", "device", "instruction_instance", "category"})
CLEAR_CATEGORIES = frozenset({"opcodes", "devices", "instruction_instances", "all"})
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


def explicit_constraint_claim_violations(value, path="$.explicit_constraint_claims"):
    """Validate Agent-A claim shape only; never interpret sentence meaning."""
    if value is None:
        return []
    if not isinstance(value, list):
        return [path + ": expected an array"]
    violations = []
    for index, claim in enumerate(value):
        root = f"{path}[{index}]"
        if not isinstance(claim, Mapping):
            violations.append(root + ": claim must be an object")
            continue
        operation = str(claim.get("operation") or "").strip().casefold()
        if operation not in OPERATIONS:
            violations.append(root + ".operation: expected require, forbid, or clear")
        scope = str(claim.get("scope") or "").strip().casefold()
        if scope not in SCOPES:
            violations.append(root + ".scope: expected global, scoped, or ambiguous")
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
            violations.append(root + ".evidence: one or more exact user-text spans are required")
        target = claim.get("target")
        if not isinstance(target, Mapping):
            violations.append(root + ".target: object is required")
            continue
        kind = str(target.get("kind") or "").strip().casefold()
        if kind not in TARGET_KINDS:
            violations.append(root + ".target.kind: unsupported target kind")
            continue
        if kind in {"opcode", "device"}:
            values = target.get("values")
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item.strip() for item in values):
                violations.append(root + ".target.values: expected a non-empty string array")
        elif kind == "instruction_instance":
            if operation == "forbid":
                violations.append(root + ".target: exact instruction instances support require/clear only")
            if not isinstance(target.get("opcode"), str) or not str(target.get("opcode")).strip():
                violations.append(root + ".target.opcode: non-empty string is required")
            operands = target.get("operands")
            if not isinstance(operands, list) or not operands or any(not isinstance(item, str) or not item.strip() for item in operands):
                violations.append(root + ".target.operands: expected a non-empty string array")
        elif kind == "category":
            if operation != "clear":
                violations.append(root + ".target: category targets are only valid for clear")
            if str(target.get("value") or "").strip().casefold() not in CLEAR_CATEGORIES:
                violations.append(root + ".target.value: expected opcodes, devices, instruction_instances, or all")
    return violations


def compile_explicit_constraint_claims(value, user_text, plc_model="FX3U"):
    """Ground claims and emit structured edits for global user-fixed constraints."""
    shape = explicit_constraint_claim_violations(value)
    if shape:
        return {"operations": [], "accepted": [], "rejected": [{"reason": "invalid_claim_shape", "detail": item} for item in shape]}
    source_text = _fold_ws(user_text)
    accepted, rejected, operations = [], [], []
    for index, raw in enumerate(value or []):
        claim = copy.deepcopy(dict(raw))
        evidence = [_fold_ws(item) for item in claim.get("evidence") or []]
        if any(not item or item not in source_text for item in evidence):
            rejected.append({"index": index, "reason": "evidence_not_in_current_request"})
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
                if not device or not _token_in_evidence(device, evidence):
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
            rejected.append({"index": index, "reason": "target_not_grounded_in_evidence"})
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


__all__ = ["compile_explicit_constraint_claims", "explicit_constraint_claim_violations"]

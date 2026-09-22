"""Generic accounting for exact fact evidence that actually reaches a model."""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping

from knowledge.evidence import text_sha256

_VERSION = "fact-coverage-v1"
_DEFAULT_DIMENSION = "definition"


def _instruction_identity(value):
    if not isinstance(value, Mapping):
        return str(value or "").strip().upper(), None
    opcode = str(value.get("opcode") or value.get("base_opcode") or "").strip().upper()
    operands = value.get("operands")
    instance = tuple(str(item) for item in operands) if isinstance(operands, (list, tuple)) else None
    return opcode, instance


def _target_identity(kind, value):
    if kind == "instruction":
        return _instruction_identity(value)
    return str(value or "").strip().upper(), None


def fact_requirements(targets, *, instruction_questions=None):
    """Project structured targets into generic, dimension-addressed requirements."""
    targets = targets if isinstance(targets, Mapping) else {}
    instruction_dimensions = tuple((instruction_questions or {_DEFAULT_DIMENSION: ""}).keys())
    requirements, seen = [], set()
    for kind, key, dimensions in (
        ("instruction", "instructions", instruction_dimensions),
        ("device", "devices", (_DEFAULT_DIMENSION,)),
        ("error", "errors", (_DEFAULT_DIMENSION,)),
    ):
        for raw_target in targets.get(key) or ():
            target, instance = _target_identity(kind, raw_target)
            if not target:
                continue
            for dimension in dimensions:
                marker = (kind, target, instance, str(dimension))
                if marker in seen:
                    continue
                seen.add(marker)
                requirement = {
                    "id": ":".join((kind, target, str(dimension))),
                    "kind": kind, "target": target, "dimension": str(dimension),
                    "candidate_source_ids": [], "source_ids": [], "status": "unresolved",
                }
                if instance is not None:
                    requirement["instruction_instance"] = {"opcode": target, "operands": list(instance)}
                requirements.append(requirement)
    return requirements


def _record_descriptor(record, included):
    kind = str(record.get("fact_kind") or record.get("structured_fact_kind") or "").strip().lower()
    target = str(record.get("fact_target") or record.get("structured_fact_target") or "").strip().upper()
    if not kind or not target or not record.get("id"):
        return None
    dimensions = record.get("fact_dimensions") or record.get("candidate_fact_categories") or ()
    dimensions = [str(value) for value in dimensions if str(value)] or [_DEFAULT_DIMENSION]
    aliases = [target]
    requested = str(record.get("structured_fact_requested_target") or "").strip().upper()
    if requested and requested not in aliases:
        aliases.append(requested)
    descriptor = {
        "id": str(record["id"]), "kind": kind, "target": target,
        "target_aliases": aliases, "dimensions": list(dict.fromkeys(dimensions)),
        "content_sha256": str(record.get("content_sha256") or text_sha256(record.get("text", ""))),
        "included": str(record["id"]) in included,
    }
    instance = record.get("instruction_instance")
    if isinstance(instance, Mapping):
        opcode, operands = _instruction_identity(instance)
        if opcode:
            descriptor["instruction_instance"] = {"opcode": opcode, "operands": list(operands or ())}
    return descriptor


def _requirement_matches_record(requirement, record):
    if requirement.get("kind") != record.get("kind"):
        return False
    if requirement.get("target") not in record.get("target_aliases", (record.get("target"),)):
        return False
    required_instance = requirement.get("instruction_instance")
    if isinstance(required_instance, Mapping) and record.get("instruction_instance") != required_instance:
        return False
    return requirement.get("dimension") in record.get("dimensions", ())


def _recompute(report, included_ids):
    included = {str(value) for value in included_ids}
    for record in report.get("records", []):
        record["included"] = record.get("id") in included
    for requirement in report.get("requirements", []):
        candidates = [str(value) for value in requirement.get("candidate_source_ids") or ()]
        delivered = [value for value in candidates if value in included]
        requirement["source_ids"] = delivered
        requirement["status"] = (
            "candidate_evidence" if delivered else "budget_omitted" if candidates else "unresolved"
        )
    counts = {"candidate_evidence": 0, "budget_omitted": 0, "unresolved": 0}
    for requirement in report.get("requirements", []):
        status = requirement.get("status")
        if status in counts:
            counts[status] += 1
    report["summary"] = counts
    return report


def build_fact_coverage(targets, records, included_ids=(), *, instruction_questions=None):
    """Build one coverage receipt for instruction, device and error facts."""
    included = {str(value) for value in included_ids}
    descriptors = []
    for record in records or ():
        if isinstance(record, Mapping):
            descriptor = _record_descriptor(record, included)
            if descriptor is not None:
                descriptors.append(descriptor)
    requirements = fact_requirements(targets, instruction_questions=instruction_questions)
    for requirement in requirements:
        requirement["candidate_source_ids"] = [
            record["id"] for record in descriptors if _requirement_matches_record(requirement, record)
        ]
    return _recompute({
        "version": _VERSION, "verification": "not_performed",
        "records": descriptors, "requirements": requirements,
    }, included)


def reconcile_fact_coverage(report, included_ids):
    """Recompute delivery after later prompt compilation or privacy filtering."""
    return _recompute(copy.deepcopy(report), included_ids)


def instruction_report_view(report, included_ids):
    """Preserve the legacy instruction receipt shape from generic coverage."""
    result = copy.deepcopy(report)
    records = []
    for row in result.get("records", []):
        value = copy.deepcopy(row)
        value["fact_kind"] = "instruction"
        value["fact_dimensions"] = list(value.get("candidate_fact_categories") or ())
        records.append(value)
    coverage = build_fact_coverage(
        {"instructions": result.get("targets") or (), "devices": [], "errors": []},
        records, included_ids, instruction_questions=result.get("questions") or {},
    )
    included_by_id = {row["id"]: row["included"] for row in coverage["records"]}
    for record in result.get("records", []):
        record["included"] = included_by_id.get(record.get("id"), False)
    result["coverage_version"] = coverage["version"]
    result["verification"] = coverage["verification"]
    result["facts"] = [
        {"opcode": requirement["target"], "question": requirement["dimension"],
         "source_ids": list(requirement["source_ids"]), "status": requirement["status"]}
        for requirement in coverage["requirements"]
    ]
    return result


def included_evidence_ids(text, records=()):
    """Return complete, byte-consistent knowledge blocks from compiled text."""
    expected = {
        str(record["id"]): record.get("content_sha256")
        for record in records if isinstance(record, Mapping) and record.get("id")
    }
    ids = []
    pattern = (
        r"(?ms)^\[KNOWLEDGE (\{[^\n]*\})\]\n"
        r"((?:(?!^\[KNOWLEDGE |^\[/KNOWLEDGE\]).)*?)\n"
        r"\[/KNOWLEDGE\]"
    )
    for match in re.finditer(pattern, str(text)):
        try:
            marker = json.loads(match.group(1)).get("id")
        except (ValueError, AttributeError):
            continue
        if isinstance(marker, str) and (
            not expected.get(marker) or text_sha256(match.group(2)) == expected[marker]
        ):
            ids.append(marker)
    return ids

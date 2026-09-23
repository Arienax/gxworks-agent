"""Confirmed implementation-semantic validation for generated ladder candidates.

New Agent-A analyses emit implementation_semantics. Core projects those semantics
into generation_contract for generation/tool compatibility and validates the same
semantic requirements here. Legacy specs without implementation_semantics keep the
previous narrow compatibility behavior.
"""
from __future__ import annotations

from collections.abc import Mapping

from plc.validation import PLCJsonValidationError

_VERSION = "confirmed-semantics-v3"
class ConfirmedSemanticValidationError(PLCJsonValidationError):
    """A candidate or semantic handoff contradicts confirmed machine facts."""


def semantic_requirements(confirmed_spec):
    """Return canonical semantics plus declarative structure obligations."""
    if not isinstance(confirmed_spec, Mapping):
        return []
    selected = confirmed_spec.get("selected_approach")
    if not isinstance(selected, Mapping):
        return []

    from plc.specification.approach import (
        normalize_implementation_semantics,
        structure_obligations,
    )
    from plc.specification.explicit_constraints import normalize_explicit_user_constraints

    result = []
    for index, item in enumerate(
        normalize_implementation_semantics(selected.get("implementation_semantics"))
    ):
        requirement_id = f"implementation_semantics[{index}]"
        row = dict(item)
        row["requirement_id"] = requirement_id
        result.append(row)

        # A forbidden structure has no positive runtime obligations.  For
        # any_of, the selected implementation is intentionally not fixed to one
        # member, so member-specific obligations must not become a hidden gate.
        if item.get("status") != "required":
            continue
        structure = item.get("value")
        obligations = structure_obligations(structure)
        selector = str(obligations.get("instance_selector") or "").strip()

        for role_index, role in enumerate(obligations.get("required_roles") or ()):
            result.append({
                "requirement_id": (
                    f"{requirement_id}.obligations.binding_roles[{role_index}]"
                ),
                "kind": "binding_role",
                "status": "required",
                "structure": structure,
                "role": str(role),
            })

        for relation_index, roles in enumerate(obligations.get("distinct_roles") or ()):
            result.append({
                "requirement_id": (
                    f"{requirement_id}.obligations.distinct_roles[{relation_index}]"
                ),
                "kind": "binding_relation",
                "status": "required",
                "structure": structure,
                "relation": "distinct_roles",
                "roles": [str(role) for role in roles],
            })

        for predicate_index, predicate in enumerate(obligations.get("predicates") or ()):
            if not isinstance(predicate, Mapping):
                continue
            result.append({
                "requirement_id": (
                    f"{requirement_id}.obligations.predicates[{predicate_index}]"
                ),
                "kind": "binding_predicate",
                "status": "required",
                "structure": structure,
                "role": str(predicate.get("role") or ""),
                "predicate_key": str(predicate.get("predicate_key") or ""),
                "coverage": str(predicate.get("coverage") or "any_path"),
                "independent_of_feedback": predicate.get("independent_of_feedback") is True,
                "instance_selector": selector,
            })

    explicit = normalize_explicit_user_constraints(
        selected.get("explicit_user_constraints")
    )
    for field, kind, status in (
        ("required_opcodes", "opcode", "required"),
        ("forbidden_opcodes", "opcode", "forbidden"),
        ("required_devices", "device", "required"),
        ("forbidden_devices", "device", "forbidden"),
    ):
        for index, value in enumerate(explicit[field]):
            result.append({
                "requirement_id": f"explicit_user_constraints.{field}[{index}]",
                "kind": kind,
                "status": status,
                "value": value,
            })
    for index, item in enumerate(explicit["instruction_instances"]):
        result.append({
            "requirement_id": f"explicit_user_constraints.instruction_instances[{index}]",
            "kind": "instruction_instance",
            "status": "required",
            "opcode": item["opcode"],
            "operands": list(item["operands"]),
        })
    return result

def _ladder_instruction_instances(ladder):
    result = set()
    for rung in (ladder or {}).get("rungs", []) or []:
        for branch in rung.get("branches", []) if isinstance(rung, Mapping) else []:
            for output in branch.get("outputs", []) if isinstance(branch, Mapping) else []:
                if not isinstance(output, Mapping) or output.get("type") != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode") or "").strip().upper()
                operands = tuple(str(value).strip() for value in output.get("operands", []) or [])
                if opcode:
                    result.add((opcode, operands))
    return result


def _feature_coverage(ladder, requirements):
    from plc.specification.approach import inspect_ladder_features
    features = inspect_ladder_features(ladder)
    available = {
        "structure": set(features.get("structures") or []),
        "opcode": set(features.get("opcodes") or []),
        "device": set(features.get("devices") or []),
    }
    rows, violations = [], []
    for requirement in requirements:
        kind = requirement.get("kind")
        status = requirement.get("status")
        if kind not in available:
            continue
        if status == "any_of":
            values = list(requirement.get("values") or [])
            ok = bool(set(values) & available[kind])
            expected = values
        else:
            value = requirement.get("value")
            present = value in available[kind]
            ok = present if status == "required" else not present
            expected = value
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "contract_feature",
            "kind": kind,
            "semantic_status": status,
            "expected": expected,
            "status": "verified" if ok else "violated",
        }
        rows.append(row)
        if not ok:
            violations.append(row)
    return rows, violations


def _instruction_instance_coverage(ladder, requirements):
    actual = _ladder_instruction_instances(ladder)
    rows, violations = [], []
    for requirement in requirements:
        if requirement.get("kind") != "instruction_instance":
            continue
        expected = (requirement["opcode"], tuple(requirement.get("operands") or ()))
        ok = expected in actual
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "exact_instruction_instance",
            "kind": "instruction_instance",
            "semantic_status": "required",
            "expected": " ".join([expected[0], *expected[1]]).strip(),
            "status": "verified" if ok else "violated",
        }
        rows.append(row)
        if not ok:
            violations.append(row)
    return rows, violations




_SUPPORTED_INSTANCE_SELECTORS = frozenset({"feedback_coil"})


def _structure_instances(ladder, selector_name):
    from plc.specification.approach import inspect_ladder_features

    features = inspect_ladder_features(ladder)
    return [
        row for row in features.get("structure_instances", []) or []
        if isinstance(row, Mapping)
        and str(row.get("selector") or "") == selector_name
    ]


def _binding_role_addresses(confirmed_spec):
    from plc.device_identity import canonical_device

    result = {}
    for row in (confirmed_spec or {}).get("io_bindings", []) or []:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "").strip().casefold()
        raw_address = str(row.get("address") or "").strip()
        if not role or not raw_address:
            continue
        address = canonical_device(raw_address)
        values = result.setdefault(role, [])
        if address not in values:
            values.append(address)
    return result


def _binding_predicate_facts(confirmed_spec):
    from plc.specification.conditions import generation_input_conditions

    facts = generation_input_conditions(
        (confirmed_spec or {}).get("io_bindings")
        if isinstance(confirmed_spec, Mapping)
        else None
    )
    indexed = {}
    for row in facts.get("level_predicates", []) or []:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "").strip().casefold()
        address = str(row.get("address") or "").strip().upper()
        if role and address:
            indexed[(role, address)] = row
    return indexed, facts


def _predicate_matches_instance(requirement, expected, instance):
    paths = list(instance.get("paths") or [])
    feedback = str(instance.get("feedback_predicate") or "")
    coverage = str(requirement.get("coverage") or "any_path")

    if coverage == "all_paths":
        matched = bool(paths) and all(expected in path for path in paths)
    elif coverage == "any_non_feedback_path":
        non_feedback = [path for path in paths if feedback not in path]
        matched = any(expected in path for path in non_feedback)
    elif coverage == "any_path":
        matched = any(expected in path for path in paths)
    else:
        return False

    if matched and requirement.get("independent_of_feedback") is True:
        feedback_paths = [path for path in paths if feedback in path]
        matched = bool(feedback_paths) and any(
            expected not in path for path in feedback_paths
        )
    return matched


def _structure_obligation_coverage(ladder, confirmed_spec, requirements):
    obligation_kinds = {"binding_role", "binding_relation", "binding_predicate"}
    scoped = [row for row in requirements if row.get("kind") in obligation_kinds]
    if not scoped:
        return [], []

    roles = _binding_role_addresses(confirmed_spec)
    predicate_facts, _raw_predicate_facts = _binding_predicate_facts(confirmed_spec)
    rows, violations = [], []

    for requirement in scoped:
        if requirement.get("kind") != "binding_role":
            continue
        role = str(requirement.get("role") or "").casefold()
        addresses = roles.get(role, [])
        if len(addresses) == 1:
            status, reason = "verified", None
        elif not addresses:
            status, reason = "violated", "missing_roles"
        else:
            status, reason = "violated", "ambiguous_roles"
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "structure_binding_role",
            "kind": "binding_role",
            "structure": requirement.get("structure"),
            "role": role,
            "expected": role,
            "status": status,
        }
        if reason:
            row["reason"] = reason
        rows.append(row)
        if status == "violated":
            violations.append(row)

    for requirement in scoped:
        if requirement.get("kind") != "binding_relation":
            continue
        relation = requirement.get("relation")
        relation_roles = [str(role).casefold() for role in requirement.get("roles") or []]
        addresses = [roles.get(role, []) for role in relation_roles]
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "structure_binding_relation",
            "kind": "binding_relation",
            "structure": requirement.get("structure"),
            "relation": relation,
            "expected": relation_roles,
        }
        if any(len(values) != 1 for values in addresses):
            row.update(status="unresolved", reason="role_resolution_failed")
        elif relation == "distinct_roles":
            resolved = [values[0] for values in addresses]
            if len(set(resolved)) == len(resolved):
                row["status"] = "verified"
            else:
                row.update(status="violated", reason="roles_not_distinct")
                violations.append(row)
        else:
            row.update(status="unresolved", reason="unknown_binding_relation")
        rows.append(row)

    groups = {}
    for requirement in scoped:
        if requirement.get("kind") != "binding_predicate":
            continue
        key = (
            str(requirement.get("structure") or ""),
            str(requirement.get("instance_selector") or ""),
        )
        groups.setdefault(key, []).append(requirement)

    for (structure, selector_name), group in groups.items():
        prepared = []
        blocked = False
        for requirement in group:
            role = str(requirement.get("role") or "").casefold()
            addresses = roles.get(role, [])
            if len(addresses) != 1:
                rows.append({
                    "requirement_id": requirement["requirement_id"],
                    "check": "structure_binding_predicate",
                    "kind": "binding_predicate",
                    "structure": structure,
                    "role": role,
                    "expected": requirement.get("predicate_key"),
                    "status": "unresolved",
                    "reason": (
                        "missing_roles" if not addresses else "ambiguous_roles"
                    ),
                })
                blocked = True
                continue

            address = str(addresses[0]).upper()
            fact = predicate_facts.get((role, address))
            predicate_key = str(requirement.get("predicate_key") or "")
            expected = fact.get(predicate_key) if isinstance(fact, Mapping) else None
            if not expected:
                row = {
                    "requirement_id": requirement["requirement_id"],
                    "check": "structure_binding_predicate",
                    "kind": "binding_predicate",
                    "structure": structure,
                    "role": role,
                    "expected": f"{predicate_key}:{address}",
                    "status": "violated",
                    "reason": "missing_input_levels",
                }
                rows.append(row)
                violations.append(row)
                blocked = True
                continue
            prepared.append((requirement, str(expected)))

        if blocked and not prepared:
            continue

        if selector_name not in _SUPPORTED_INSTANCE_SELECTORS:
            for requirement, expected in prepared:
                row = {
                    "requirement_id": requirement["requirement_id"],
                    "check": "structure_binding_predicate",
                    "kind": "binding_predicate",
                    "structure": structure,
                    "role": requirement.get("role"),
                    "expected": expected,
                    "status": "violated",
                    "reason": "unknown_instance_selector",
                }
                rows.append(row)
                violations.append(row)
            continue

        instances = _structure_instances(ladder, selector_name)
        matched = next(
            (
                instance
                for instance in instances
                if all(
                    _predicate_matches_instance(requirement, expected, instance)
                    for requirement, expected in prepared
                )
            ),
            None,
        )
        best = matched
        if best is None and instances and prepared:
            best = max(
                instances,
                key=lambda instance: sum(
                    _predicate_matches_instance(requirement, expected, instance)
                    for requirement, expected in prepared
                ),
            )
        reason = (
            "structure_instance_not_found"
            if not instances
            else "binding_predicate_mismatch"
        )
        for requirement, expected in prepared:
            predicate_ok = (
                best is not None
                and _predicate_matches_instance(requirement, expected, best)
            )
            row = {
                "requirement_id": requirement["requirement_id"],
                "check": "structure_binding_predicate",
                "kind": "binding_predicate",
                "structure": structure,
                "role": requirement.get("role"),
                "predicate_key": requirement.get("predicate_key"),
                "expected": expected,
                "status": "verified" if predicate_ok else "violated",
            }
            if best is not None:
                row["rung_id"] = best.get("rung_id")
                row["target"] = best.get("target")
            if not predicate_ok:
                row["reason"] = reason
                violations.append(row)
            rows.append(row)

    return rows, violations


def _feature_checker(ladder, _confirmed_spec, requirements):
    return _feature_coverage(ladder, requirements)


def _instruction_instance_checker(ladder, _confirmed_spec, requirements):
    return _instruction_instance_coverage(ladder, requirements)


def _structure_obligation_checker(ladder, confirmed_spec, requirements):
    return _structure_obligation_coverage(ladder, confirmed_spec, requirements)


_CHECKER_REGISTRY = (
    ("contract_features", _feature_checker),
    ("instruction_instances", _instruction_instance_checker),
    ("structure_obligations", _structure_obligation_checker),
)


def _legacy_compatibility_check(ladder, confirmed_spec, plc_model):
    """Old snapshots remain readable; ladder_v1 validation owns old contracts."""
    selected = confirmed_spec.get("selected_approach") if isinstance(confirmed_spec, Mapping) else None
    contract = selected.get("generation_contract") if isinstance(selected, Mapping) else None
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": "not_applicable",
        "requirements": [],
        "checks": [{
            "check": "selected_approach_contract",
            "status": "deferred_to_ladder_validation"
            if isinstance(contract, Mapping) and contract
            else "not_applicable",
        }],
        "legacy_compatibility": True,
    }


def validate_confirmed_semantics(ladder, confirmed_spec, plc_model="FX3U"):
    """Validate canonical implementation semantics and return coverage."""
    if not isinstance(confirmed_spec, Mapping):
        return {"version": _VERSION, "status": "not_applicable", "requirements": [], "checks": []}

    selected = confirmed_spec.get("selected_approach")
    canonical_sources = (
        isinstance(selected, Mapping)
        and (
            "implementation_semantics" in selected
            or "explicit_user_constraints" in selected
        )
    )
    requirements = semantic_requirements(confirmed_spec)
    if not canonical_sources:
        return _legacy_compatibility_check(ladder, confirmed_spec, plc_model)
    if not requirements:
        return {
            "version": _VERSION,
            "plc_model": str(plc_model or "").strip().upper(),
            "status": "not_applicable",
            "requirements": [],
            "checks": [],
            "legacy_compatibility": False,
        }

    checks, violations = [], []
    for _name, checker in _CHECKER_REGISTRY:
        rows, failed = checker(ladder, confirmed_spec, requirements)
        checks.extend(rows)
        violations.extend(failed)

    if violations:
        summary = ", ".join(
            (
                f"{row.get('kind')}:{row.get('expected')}"
                + (f" ({row.get('reason')})" if row.get("reason") else "")
            )
            for row in violations[:8]
        )
        raise ConfirmedSemanticValidationError(
            "$.confirmed_spec.selected_approach: generated candidate violates "
            "confirmed implementation semantics/user constraints: " + summary
        )

    unresolved = [row for row in checks if row.get("status") == "unresolved"]
    verified_ids = {row.get("requirement_id") for row in checks if row.get("status") == "verified"}
    requirement_ids = {row["requirement_id"] for row in requirements}
    unresolved_ids = {row.get("requirement_id") for row in unresolved}
    for requirement_id in sorted(requirement_ids - verified_ids - unresolved_ids):
        checks.append({
            "requirement_id": requirement_id,
            "check": "coverage",
            "status": "unresolved",
            "reason": "no_registered_checker",
        })
    unresolved = [row for row in checks if row.get("status") == "unresolved"]
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": "unresolved" if unresolved else "verified",
        "requirements": requirements,
        "checks": checks,
        "legacy_compatibility": False,
    }

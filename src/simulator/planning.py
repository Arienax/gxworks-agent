"""Deterministic boundary for AI-proposed PLC simulator test suites."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Mapping, Set

from plc.ir import canonical_sha256, validate_plc_ir

from .models import normalize_test_suite


class SimulatorTestPlanError(ValueError):
    pass


def build_test_generation_context(program: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the behavior-bearing IR subset used to propose tests."""

    validate_plc_ir(program)
    networks = []
    for network in program.get("networks", []) or []:
        networks.append(
            {
                "id": network.get("id"),
                "comment": network.get("comment") or "",
                "instructions": copy.deepcopy(network.get("instructions") or []),
                "reads": list(network.get("reads") or []),
                "writes": list(network.get("writes") or []),
                "execution": copy.deepcopy(network.get("execution") or {}),
            }
        )
    return {
        "schema_version": 1,
        "binding": {
            "plc_model": str(program.get("plc", {}).get("cpu") or "FX3U").upper(),
            "program_name": str(program.get("program_name") or "MAIN"),
            "revision": int(program.get("revision") or 0),
            "ir_sha256": canonical_sha256(program),
        },
        "io_map": copy.deepcopy(program.get("io_map") or {}),
        "devices": copy.deepcopy(program.get("devices") or {}),
        "networks": networks,
        "state_machines": copy.deepcopy(
            (program.get("logic") or {}).get("state_machines") or []
        ),
        "semantic_requirements": copy.deepcopy(
            (program.get("logic") or {}).get("requirements") or []
        ),
        "static_findings": [
            copy.deepcopy(item)
            for item in ((program.get("analysis") or {}).get("findings") or [])
            if str(item.get("severity") or "").lower() in {"warning", "info"}
        ],
    }


def _referenced_devices(test: Mapping[str, Any]) -> Set[str]:
    result = set(test.get("initial") or {})
    result.update(test.get("trace_devices") or [])
    for step in test.get("steps", []) or []:
        result.update(step.get("set") or {})
        result.update(item["address"] for item in step.get("expect", []) or [])
        result.update(item["address"] for item in step.get("wait_for", []) or [])
    for invariant in test.get("invariants", []) or []:
        result.update(invariant.get("devices") or [])
        if invariant.get("device"):
            result.add(invariant["device"])
    result.update(item["device"] for item in test.get("fault_injections", []) or [])
    return result


def _stimulus_devices(test: Mapping[str, Any]) -> Set[str]:
    result = set()
    for step in test.get("steps", []) or []:
        result.update(step.get("set") or {})
    result.update(item["device"] for item in test.get("fault_injections", []) or [])
    return result


def _edge_activation_values(program: Mapping[str, Any]) -> Dict[str, tuple]:
    """Return deterministic active/inactive values for one-shot inputs."""

    semantics_by_device: Dict[str, Set[str]] = {}
    for network in program.get("networks", []) or []:
        if not isinstance(network, Mapping):
            continue
        for trigger in ((network.get("execution") or {}).get("triggers") or []):
            if not isinstance(trigger, Mapping):
                continue
            semantic = str(trigger.get("semantic") or "").strip().upper()
            if semantic not in {"RISING_EDGE", "FALLING_EDGE"}:
                continue
            device = str(trigger.get("device") or "").strip().upper()
            if device:
                semantics_by_device.setdefault(device, set()).add(semantic)

    result = {}
    for device, semantics in semantics_by_device.items():
        if semantics == {"RISING_EDGE"}:
            result[device] = (1, 0)
        elif semantics == {"FALLING_EDGE"}:
            result[device] = (0, 1)
    return result


def _repair_repeated_edge_stimuli(
    test: Dict[str, Any],
    edge_values: Mapping[str, tuple],
) -> None:
    """Re-arm an AI-planned repeated edge instead of executing a no-op write.

    A second ``X0=1`` while X0 is still ON cannot trigger ANDP/LDP.  Older
    cached plans can contain exactly that pattern even though their prose says
    "restart".  Insert the missing opposite level immediately before the
    repeated activation and record the deterministic repair in metadata.
    """

    values = dict(test.get("initial") or {})
    original_steps = list(test.get("steps") or [])
    repaired_steps = []
    repair_records = []
    used_ids = {str(step.get("id") or "") for step in original_steps}
    settle_ms = max(20, int(test.get("sample_ms") or 1))

    for step_index, step in enumerate(original_steps, start=1):
        repeated = {}
        for device, value in (step.get("set") or {}).items():
            activation = edge_values.get(device)
            if activation is None:
                continue
            active_value, inactive_value = activation
            if value == active_value and values.get(device) == active_value:
                repeated[device] = inactive_value

        if repeated:
            previous_at = int(repaired_steps[-1]["at_ms"]) if repaired_steps else 0
            release_at = max(previous_at, int(step["at_ms"]) - settle_ms)
            base_id = f"auto_edge_rearm_before_{step.get('id') or step_index}"
            release_id = base_id
            suffix = 2
            while release_id in used_ids:
                release_id = f"{base_id}_{suffix}"
                suffix += 1
            used_ids.add(release_id)
            repaired_steps.append(
                {
                    "id": release_id,
                    "at_ms": release_at,
                    "set": dict(sorted(repeated.items())),
                    "expect": [],
                    "wait_for": [],
                }
            )
            values.update(repeated)
            for device, inactive_value in sorted(repeated.items()):
                repair_records.append(
                    {
                        "kind": "edge_rearm",
                        "device": device,
                        "inactive_value": inactive_value,
                        "before_step_id": step.get("id"),
                        "inserted_step_id": release_id,
                        "at_ms": release_at,
                    }
                )

        repaired_steps.append(step)
        values.update(step.get("set") or {})

    if not repair_records:
        return
    test["steps"] = repaired_steps
    metadata = copy.deepcopy(test.get("metadata") or {})
    existing = list(metadata.get("normalization_repairs") or [])
    metadata["normalization_repairs"] = existing + repair_records
    test["metadata"] = metadata


def _repair_generated_plan_shape(value: Any) -> Any:
    """Repair unambiguous presentation-shape errors in an AI-generated plan.

    Test DSL invariants are continuous constraints with an explicit ``type``.
    Models occasionally duplicate a final ``{at_ms, expect}`` step inside the
    invariant list or reuse a human-readable step id.  This adapter is
    intentionally limited to generated plans; the reusable Test DSL validator
    remains strict.
    """

    repaired = copy.deepcopy(value)
    if isinstance(repaired, list):
        raw_tests = repaired
    elif isinstance(repaired, Mapping):
        raw_tests = repaired.get("tests")
    else:
        return repaired
    if not isinstance(raw_tests, list):
        return repaired

    allowed_keys = {"id", "at_ms", "expect"}
    for test in raw_tests:
        if not isinstance(test, dict):
            continue
        raw_invariants = test.get("invariants")
        if raw_invariants is None:
            raw_invariants = []
        raw_steps = test.get("steps")
        if not isinstance(raw_invariants, list) or not isinstance(raw_steps, list):
            continue

        retained_invariants = []
        repair_records = []
        for invariant_index, invariant in enumerate(raw_invariants):
            is_step_expectation = (
                isinstance(invariant, Mapping)
                and not str(invariant.get("type") or "").strip()
                and "at_ms" in invariant
                and "expect" in invariant
                and bool(invariant.get("expect"))
                and set(invariant).issubset(allowed_keys)
            )
            if not is_step_expectation:
                retained_invariants.append(invariant)
                continue

            duplicate = any(
                isinstance(step, Mapping)
                and step.get("at_ms") == invariant.get("at_ms")
                and step.get("expect") == invariant.get("expect")
                for step in raw_steps
            )
            action = "dropped_duplicate"
            if not duplicate:
                generated_id = str(
                    invariant.get("id")
                    or f"normalized_expectation_{invariant_index + 1}"
                )
                raw_steps.append(
                    {
                        "id": generated_id,
                        "at_ms": copy.deepcopy(invariant.get("at_ms")),
                        "expect": copy.deepcopy(invariant.get("expect")),
                    }
                )
                action = "moved_to_step"
            repair_records.append(
                {
                    "kind": "misplaced_expectation",
                    "source_invariant_index": invariant_index,
                    "action": action,
                    "at_ms": copy.deepcopy(invariant.get("at_ms")),
                }
            )

        if any(record["action"] == "moved_to_step" for record in repair_records):
            indexed_steps = list(enumerate(raw_steps))

            def step_order(item):
                original_index, step = item
                if not isinstance(step, Mapping):
                    return (1, 0, original_index)
                raw_at = step.get("at_ms", 0)
                if isinstance(raw_at, bool):
                    return (1, 0, original_index)
                try:
                    numeric_at = float(raw_at)
                except (TypeError, ValueError):
                    return (1, 0, original_index)
                return (0, numeric_at, original_index)

            test["steps"] = [step for _, step in sorted(indexed_steps, key=step_order)]

        used_ids = set()
        occurrence_counts = {}
        for step_index, step in enumerate(test.get("steps") or []):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                continue
            occurrence_counts[step_id] = occurrence_counts.get(step_id, 0) + 1
            if step_id not in used_ids:
                used_ids.add(step_id)
                continue
            occurrence = occurrence_counts[step_id]
            replacement = f"{step_id}（{occurrence}）"
            while replacement in used_ids:
                occurrence += 1
                replacement = f"{step_id}（{occurrence}）"
            occurrence_counts[step_id] = occurrence
            step["id"] = replacement
            used_ids.add(replacement)
            repair_records.append(
                {
                    "kind": "duplicate_step_id",
                    "step_index": step_index,
                    "original_id": step_id,
                    "replacement_id": replacement,
                }
            )

        if not repair_records:
            continue
        test["invariants"] = retained_invariants
        raw_metadata = test.get("metadata")
        metadata = (
            copy.deepcopy(raw_metadata)
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        raw_existing = metadata.get("normalization_repairs")
        existing = list(raw_existing) if isinstance(raw_existing, list) else []
        metadata["normalization_repairs"] = existing + repair_records
        test["metadata"] = metadata
    return repaired


def _deterministic_state_invariants(program: Mapping[str, Any]):
    """Return only invariants proved by the structured PLC IR itself.

    State registers and their finite values are derived from compare/write
    instructions by plc_semantics.  No labels, safety policy, timeout, mutex or
    recovery behavior are guessed here.
    """

    invariants = []
    seen = set()
    for machine in ((program.get("logic") or {}).get("state_machines") or []):
        if not isinstance(machine, Mapping):
            continue
        register = str(machine.get("state_register") or "").strip().upper()
        if not re.fullmatch(r"D\d+", register):
            continue
        allowed = []
        for state in machine.get("states") or []:
            if not isinstance(state, Mapping) or isinstance(state.get("value"), bool):
                continue
            try:
                allowed.append(int(state.get("value")))
            except (TypeError, ValueError):
                continue
        allowed = sorted(set(allowed))
        if len(allowed) < 2:
            continue
        marker = (register, tuple(allowed))
        if marker in seen:
            continue
        seen.add(marker)
        invariants.append(
            {
                "type": "state_constraint",
                "device": register,
                "allowed": allowed,
                "name": f"{register} 状态机合法状态",
            }
        )
    return invariants


def normalize_generated_test_suite(
    value: Any,
    program: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate a generated suite against the exact program device boundary."""

    validate_plc_ir(program)
    plc_model = str(program.get("plc", {}).get("cpu") or "FX3U").upper()
    suite = normalize_test_suite(
        _repair_generated_plan_shape(value),
        plc_model=plc_model,
    )
    if suite["plc_model"] != plc_model:
        raise SimulatorTestPlanError("测试套件 PLC 型号与当前程序不一致。")

    declared = {str(item).upper() for item in (program.get("devices") or {})}
    declared.update(str(item).upper() for item in (program.get("io_map") or {}))
    if not declared:
        raise SimulatorTestPlanError("当前程序没有可用于生成测试的软元件。")
    writable = {
        address
        for address in declared
        if address.startswith("X")
        or (
            address.startswith(("M", "D"))
            and not re.fullmatch(r"(?:M8\d{3}|D8\d{3})", address)
        )
    }
    state_invariants = _deterministic_state_invariants(program)
    edge_values = _edge_activation_values(program)

    for index, test in enumerate(suite["tests"]):
        _repair_repeated_edge_stimuli(test, edge_values)
        existing_invariants = list(test.get("invariants") or [])
        existing_state_devices = {
            str(item.get("device") or "").upper()
            for item in existing_invariants
            if isinstance(item, Mapping) and item.get("type") == "state_constraint"
        }
        for invariant in state_invariants:
            if invariant["device"] not in existing_state_devices:
                existing_invariants.append(copy.deepcopy(invariant))
        test["invariants"] = existing_invariants
        test["trace_devices"] = sorted(
            set(test.get("trace_devices") or [])
            | {item["device"] for item in state_invariants}
        )
        references = _referenced_devices(test)
        unknown = sorted(references.difference(declared))
        if unknown:
            raise SimulatorTestPlanError(
                f"tests[{index}] 引用了当前程序之外的软元件：{', '.join(unknown)}"
            )
        invalid_stimuli = sorted(_stimulus_devices(test).difference(writable))
        if invalid_stimuli:
            raise SimulatorTestPlanError(
                f"tests[{index}] 将非外部刺激软元件作为输入：{', '.join(invalid_stimuli)}"
            )
        has_assertion = any(
            step.get("expect") or step.get("wait_for")
            for step in test.get("steps", []) or []
        ) or bool(test.get("invariants"))
        if not has_assertion:
            raise SimulatorTestPlanError(
                f"tests[{index}] 没有期望值或运行不变量，不能判断通过或失败。"
            )
        metadata = copy.deepcopy(test.get("metadata") or {})
        metadata["program_revision"] = int(program.get("revision") or 0)
        metadata["program_ir_sha256"] = canonical_sha256(program)
        test["metadata"] = metadata
    # Re-run the plain DSL boundary after deterministic insertions so step
    # limits, ordering and device values remain enforced for repaired caches.
    return normalize_test_suite(suite, plc_model=plc_model)


__all__ = [
    "SimulatorTestPlanError",
    "build_test_generation_context",
    "normalize_generated_test_suite",
]

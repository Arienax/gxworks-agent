"""Deterministic acceptance of version-bound simulator evidence.

This checks the recorded test contract, not the meaning of a requirement and
not the identity of a physical PLC. A passing test double remains a test double.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Mapping

from plc.ir import canonical_sha256

from .models import normalize_test_suite


EVIDENCE_SCHEMA_VERSION = 2
SUPPORTED_RESULT_SCHEMA_VERSION = 1
BINDING_FIELDS = ("project_id", "version_id", "revision", "ir_sha256", "suite_sha256")


class SimulatorEvidenceError(ValueError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def execution_binding(project_id, version_id, program, suite):
    """Capture before execution; persistence must compare this snapshot again."""
    normalized = normalize_test_suite(suite, plc_model=program["plc"]["cpu"])
    return {
        "project_id": project_id,
        "version_id": version_id,
        "revision": program.get("revision"),
        "ir_sha256": canonical_sha256(program),
        "suite_sha256": canonical_sha256(normalized),
    }


def require_matching_binding(actual, expected):
    if not isinstance(actual, Mapping) or any(
        actual.get(key) != expected[key] for key in BINDING_FIELDS
    ):
        raise SimulatorEvidenceError(
            "version_conflict", "仿真证据的项目、版本或测试套件已变化，请重新执行完整测试。"
        )


def blocked_verification(category, detail=""):
    actions = {
        "environment": "repair_environment",
        "setup": "repair_test_setup",
        "runtime": "inspect_runner",
        "incomplete": "rerun_full_suite",
        "evidence_invalid": "rerun_full_suite",
        "version_conflict": "rerun_current_version",
        "legacy_evidence": "rerun_current_version",
    }
    return {
        "schema_version": 1,
        "status": "blocked",
        "category": category,
        "program_repair_allowed": False,
        "next_action": actions[category],
        "detail": detail,
    }


def evaluate_suite_result(suite: Mapping[str, Any], result: Mapping[str, Any]):
    """Accept only a complete, consistent run of the supplied normalized suite.

    Environment/setup/runner failures never authorize a program repair, even
    when an earlier case recorded an assertion failure. Assertion failures are
    evidence for review of both the program and the test, not proof of a bug.
    """
    if not isinstance(result, Mapping):
        return blocked_verification("evidence_invalid", "result")
    if result.get("schema_version") != SUPPORTED_RESULT_SCHEMA_VERSION:
        return blocked_verification("evidence_invalid", "result.schema_version")
    status = result.get("status")
    if not isinstance(status, str) or status not in {"passed", "failed", "error", "unavailable"}:
        return blocked_verification("evidence_invalid", "result.status")
    if result.get("name") != suite["name"] or result.get("plc_model") != suite["plc_model"]:
        return blocked_verification("evidence_invalid", "result.name/plc_model")
    rows = result.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        return blocked_verification("evidence_invalid", "result.results")
    if any(not isinstance(row.get("name"), str) or not isinstance(row.get("status"), str) for row in rows):
        return blocked_verification("evidence_invalid", "result.results.name/status")
    # Failed setup may have no case rows at all. Preserve it as environment
    # evidence instead of mislabelling it as a program or coverage failure.
    if status == "unavailable" or any(
        row.get("environment_failure") is True or row.get("status") == "unavailable"
        for row in rows
    ):
        return blocked_verification("environment")
    if status == "error" or any(row.get("status") == "error" for row in rows):
        setup_failed = any(
            row.get("status") == "error" and (
                row.get("execution_started") is False or row.get("setup_stage") == "stimulus_write"
            )
            for row in rows
        )
        return blocked_verification("setup" if setup_failed else "runtime")

    tests = suite["tests"]
    if result.get("suite_sha256") != canonical_sha256(suite):
        return blocked_verification("evidence_invalid", "result.suite_sha256")
    names = [test["name"] for test in tests]
    if Counter(row.get("name") for row in rows) != Counter(names):
        return blocked_verification("incomplete", "result.results: test identities")
    counts = Counter(row.get("status") for row in rows)
    if not isinstance(result.get("counts"), Mapping):
        return blocked_verification("evidence_invalid", "result.counts")
    if any(not isinstance(row.get("backend_kind"), str) or not row["backend_kind"] for row in rows):
        return blocked_verification("evidence_invalid", "result.backend_kinds")
    if result.get("backend_kinds") != sorted({row["backend_kind"] for row in rows}):
        return blocked_verification("evidence_invalid", "result.backend_kinds")
    expected_status = "failed" if counts.get("failed") else "passed"
    if (
        set(counts) - {"passed", "failed"}
        or status != expected_status
        or result.get("passed") is not (status == "passed")
        or any((result.get("counts") or {}).get(key, 0) != counts[key]
               for key in ("passed", "failed", "error", "unavailable"))
    ):
        return blocked_verification("evidence_invalid", "result.status/counts")
    if any(result.get(key) != value for key, value in {
        "test_count": len(tests), "attempted_count": len(tests),
        "executed_count": len(tests), "not_executed_count": 0,
    }.items()):
        return blocked_verification("incomplete", "result.execution_counts")

    by_name = {row["name"]: row for row in rows}
    assertion_failure = False
    invariant_failure = False
    for test in tests:
        row = by_name[test["name"]]
        path = "results." + test["name"]
        if (
            row.get("execution_started") is not True
            or row.get("schema_version") != SUPPORTED_RESULT_SCHEMA_VERSION
            or row.get("setup_stage") != "complete"
            or row.get("plc_model") != suite["plc_model"]
            or row.get("error")
        ):
            return blocked_verification("incomplete", path + ".execution")
        if row.get("test_sha256") != canonical_sha256(test):
            return blocked_verification("evidence_invalid", path + ".test_sha256")
        assertions = row.get("assertions")
        violations = row.get("invariant_violations")
        trace = row.get("trace")
        if any(not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items)
               for items in (assertions, violations, trace)):
            return blocked_verification("evidence_invalid", path + ".observations")
        expected = Counter(
            (step["id"], expectation["address"], kind == "wait_for")
            for step in test["steps"] for kind in ("expect", "wait_for")
            for expectation in step[kind]
        )
        if not expected and not test["invariants"]:
            return blocked_verification("incomplete", path + ": no behavioral checks")
        if any(
            not isinstance(item.get("step_id"), str) or not isinstance(item.get("address"), str)
            or type(item.get("wait_for", False)) is not bool for item in assertions
        ):
            return blocked_verification("evidence_invalid", path + ".assertions")
        recorded = Counter(
            (item.get("step_id"), item.get("address"), item.get("wait_for", False))
            for item in assertions
        )
        if expected != recorded:
            return blocked_verification("incomplete", path + ".assertions")
        if any(type(item.get("passed")) is not bool for item in assertions):
            return blocked_verification("evidence_invalid", path + ".assertions.passed")
        # A final sample proves the case reached its end, including invariant
        # sampling. Require the observation trace for every expected step too.
        for event in ("initial_sample", "final_sample"):
            if not any(
                item.get("event") == event and isinstance(item.get("values"), Mapping)
                and all(_has_value(item["values"], address) for address in test["trace_devices"])
                for item in trace
            ):
                return blocked_verification("incomplete", path + "." + event)
        for step in test["steps"]:
            for kind in ("expect", "wait_for"):
                if step[kind] and not any(
                    item.get("event") == kind and item.get("step_id") == step["id"]
                    and isinstance(item.get("values"), Mapping)
                    and all(_has_value(item["values"], expectation["address"]) for expectation in step[kind])
                    for item in trace
                ):
                    return blocked_verification("incomplete", path + ".trace." + step["id"])
        for violation in violations:
            index = violation.get("invariant_index")
            if type(index) is not int or not 0 <= index < len(test["invariants"]):
                return blocked_verification("evidence_invalid", path + ".invariant_violations")
        case_assertion_failure = any(item["passed"] is False for item in assertions)
        case_failed = case_assertion_failure or bool(violations)
        if row.get("status") != ("failed" if case_failed else "passed") or row.get("passed") is not (not case_failed):
            return blocked_verification("evidence_invalid", path + ".status")
        assertion_failure |= case_assertion_failure
        invariant_failure |= bool(violations)

    failed = assertion_failure or invariant_failure
    return {
        "schema_version": 1,
        "status": "failed" if failed else "passed",
        "category": "assertion" if assertion_failure else "invariant" if invariant_failure else None,
        "program_repair_allowed": failed,
        "next_action": "review_program_and_test" if failed else "none",
        "detail": "",
    }


def _has_value(values, address):
    value = values.get(address)
    return isinstance(value, (str, bool, int, float)) and not (
        isinstance(value, float) and not math.isfinite(value)
    )

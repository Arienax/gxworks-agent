"""Compare complete pytest runs without hiding pre-existing failures.

Exit 1 for new failures, new skips, missing tests, duplicate node IDs, an empty
suite or a collection/crash exit code. Persistent failures are reported, not
relabelled as passing. Explicit test renames are recorded, including parameterized cases. Contract
changes keep their negative assertions rather than dropping old test nodes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

RENAMES = {
    "tests.test_spec_review_workbench.test_pyinstaller_specs_keep_legacy_editor_as_runtime_data":
        "tests.test_spec_review_workbench.test_pyinstaller_specs_use_importable_editor_package",
    "tests.test_architecture_boundaries.test_generation_contract_and_tool_messages_depend_only_on_standard_library":
        "tests.test_architecture_boundaries.test_generation_contract_uses_only_stdlib_and_authoritative_instruction_registry",
    "tests.test_provider_error_privacy.test_generation_injected_transport_error_is_safe_and_still_falls_back":
        "tests.test_provider_error_privacy.test_generation_injected_transport_error_is_safe_and_never_replayed",
    "tests.test_response_language.test_fallback_discards_partial_callbacks_and_keeps_original_language":
        "tests.test_response_language.test_partial_stream_failure_is_not_replayed_or_published",

}


def load_report(path: Path) -> dict:
    result = {}
    for case in ET.parse(path).iter("testcase"):
        name = case.get("classname", "") + "." + case.get("name", "")
        if name in result:
            raise ValueError("Duplicate test node: " + name)
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        status = "failed" if failure is not None else "skipped" if case.find("skipped") is not None else "passed"
        result[name] = {"status": status, "message": failure.get("message", "") if failure is not None else ""}
    if not result:
        raise ValueError("No test cases in " + str(path))
    return result


def compare_reports(baseline: dict, candidate: dict) -> dict:
    def renamed(name):
        function, bracket, parameters = name.partition("[")
        return RENAMES.get(function, function) + bracket + parameters
    before = {renamed(name): value for name, value in baseline.items()}
    if len(before) != len(baseline):
        raise ValueError("Renamed test IDs collide")
    missing = sorted(set(before) - set(candidate))
    new_failures = sorted(name for name, item in candidate.items()
                          if item["status"] == "failed" and before.get(name, {}).get("status") != "failed")
    new_skips = sorted(name for name, item in candidate.items()
                       if item["status"] == "skipped" and before.get(name, {}).get("status") != "skipped")
    persistent = sorted(name for name, item in candidate.items()
                        if item["status"] == "failed" and before.get(name, {}).get("status") == "failed")
    def counts(items):
        return {status: sum(x["status"] == status for x in items.values())
                for status in ("passed", "failed", "skipped")}
    return {
        "regression_gate_passed": not (missing or new_failures or new_skips),
        "full_candidate_suite_passed": all(x["status"] == "passed" for x in candidate.values()),
        "baseline": counts(before), "candidate": counts(candidate),
        "missing_tests": missing, "new_failures": new_failures, "new_skips": new_skips,
        "persistent_failures": persistent,
        "resolved_failures": sorted(name for name, item in before.items()
                                    if item["status"] == "failed" and candidate.get(name, {}).get("status") == "passed"),
        "added_tests": sorted(set(candidate) - set(before)),
        "persistent_failure_message_changes": [
            {"test": name, "before": before[name]["message"], "after": candidate[name]["message"]}
            for name in persistent if before[name]["message"] != candidate[name]["message"]
        ],
        "reviewed_test_renames": {name: renamed(name) for name in baseline if renamed(name) != name},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline-exit", type=Path, required=True)
    parser.add_argument("--candidate-exit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.baseline_exit, args.candidate_exit):
        if int(path.read_text().strip()) not in (0, 1):
            parser.error("pytest did not complete its suite: " + str(path))
    report = compare_reports(load_report(args.baseline), load_report(args.candidate))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["regression_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

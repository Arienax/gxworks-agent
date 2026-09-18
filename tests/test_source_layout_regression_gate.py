"""The regression gate never silently hides a missing/failing/skipped test."""
import pytest
from scripts.source_layout_regression import compare_reports, load_report, RENAMES


def row(status):
    return {"status": status, "message": "fixture"}


def test_known_failure_is_reported_but_not_called_a_full_pass():
    report = compare_reports({"a": row("failed")}, {"a": row("failed")})
    assert report["regression_gate_passed"]
    assert not report["full_candidate_suite_passed"]
    assert report["persistent_failures"] == ["a"]


@pytest.mark.parametrize("change", ["failure", "skip", "missing"])
def test_regression_or_missing_test_fails(change):
    candidate = {"a": row("failed" if change == "failure" else "skipped")} if change != "missing" else {}
    assert not compare_reports({"a": row("passed")}, candidate)["regression_gate_passed"]


def test_only_reviewed_test_renames_are_accepted():
    old, new = next(iter(RENAMES.items()))
    assert compare_reports({old: row("passed")}, {new: row("passed")})["regression_gate_passed"]
    assert not compare_reports({"a": row("passed")}, {"renamed_a": row("passed")})["regression_gate_passed"]


def test_empty_or_duplicate_junit_is_rejected(tmp_path):
    path = tmp_path / "suite.xml"
    for text in ('<testsuites/>', '<testsuite><testcase name="a"/><testcase name="a"/></testsuite>'):
        path.write_text(text)
        with pytest.raises(ValueError):
            load_report(path)

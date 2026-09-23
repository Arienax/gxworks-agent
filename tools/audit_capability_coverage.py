#!/usr/bin/env python3
"""Audit cross-refactor capability ownership and migration coverage.

The JSON manifest is executable policy rather than a progress log. An enforced
capability must keep every declared owner/consumer/test check. A tracked_gap
must keep both its surviving checks and an explicit machine-observable gap.
When a gap is fixed, this audit fails until the manifest is updated to enforced
with the new consumer/test coverage.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "architecture" / "capability-coverage.json"
_ALLOWED_STATES = {"enforced", "tracked_gap"}
_ALLOWED_CHECKS = {"file", "python_symbol", "contains", "absent"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")


def _inside_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def _repo_path(value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ".." in Path(text).parts:
        raise ValueError(f"invalid repository path: {value!r}")
    path = ROOT / text
    if not _inside_root(path):
        raise ValueError(f"path escapes repository: {value!r}")
    return path


def _python_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _check(item: dict[str, Any]) -> tuple[bool, str]:
    kind = item.get("kind")
    if kind not in _ALLOWED_CHECKS:
        return False, f"unsupported check kind {kind!r}"
    try:
        path = _repo_path(item.get("path"))
    except ValueError as error:
        return False, str(error)
    rel = path.relative_to(ROOT).as_posix()

    if kind == "file":
        return path.is_file(), f"{rel}: file is missing"
    if not path.is_file():
        return False, f"{rel}: file is missing"

    if kind == "python_symbol":
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            return False, f"{rel}: python_symbol check has no symbol"
        try:
            symbols = _python_symbols(path)
        except (SyntaxError, UnicodeError) as error:
            return False, f"{rel}: cannot parse Python: {type(error).__name__}"
        return symbol in symbols, f"{rel}: missing Python symbol {symbol}"

    text = path.read_text(encoding="utf-8")
    needle = str(item.get("text") or "")
    if not needle:
        return False, f"{rel}: {kind} check has empty text"
    if kind == "contains":
        return needle in text, f"{rel}: expected text not found: {needle!r}"
    return needle not in text, f"{rel}: tracked absence no longer holds: {needle!r}"


def _validate_manifest(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["manifest root must be an object"]
    if document.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if set(document.get("states") or ()) != _ALLOWED_STATES:
        errors.append("states must declare exactly enforced and tracked_gap")
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return [*errors, "capabilities must be a non-empty list"]

    seen: set[str] = set()
    for index, capability in enumerate(capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(capability, dict):
            errors.append(where + " must be an object")
            continue
        identifier = capability.get("id")
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            errors.append(where + ".id is invalid")
            continue
        if identifier in seen:
            errors.append(f"duplicate capability id: {identifier}")
        seen.add(identifier)
        state = capability.get("state")
        if state not in _ALLOWED_STATES:
            errors.append(f"{identifier}: invalid state {state!r}")
        if not str(capability.get("owner") or "").strip():
            errors.append(f"{identifier}: owner is required")
        if not str(capability.get("summary") or "").strip():
            errors.append(f"{identifier}: summary is required")
        checks = capability.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append(f"{identifier}: at least one capability check is required")
        elif any(not isinstance(item, dict) for item in checks):
            errors.append(f"{identifier}: checks must be objects")
        tests = capability.get("tests")
        if not isinstance(tests, list) or not tests:
            errors.append(f"{identifier}: at least one regression test file is required")
        else:
            for test in tests:
                try:
                    path = _repo_path(test)
                except ValueError as error:
                    errors.append(f"{identifier}: {error}")
                    continue
                if not path.is_file():
                    errors.append(f"{identifier}: regression test is missing: {test}")
                elif not path.relative_to(ROOT).as_posix().startswith("tests/"):
                    errors.append(f"{identifier}: regression owner must be under tests/: {test}")

        gap_checks = capability.get("gap_checks")
        gap_reason = str(capability.get("gap_reason") or "").strip()
        if state == "tracked_gap":
            if not gap_reason:
                errors.append(f"{identifier}: tracked_gap requires gap_reason")
            if not isinstance(gap_checks, list) or not gap_checks:
                errors.append(f"{identifier}: tracked_gap requires machine-observable gap_checks")
        else:
            if gap_reason or gap_checks:
                errors.append(f"{identifier}: enforced capability cannot retain gap_reason/gap_checks")
    return errors


def audit_coverage(manifest_path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = Path(manifest_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    schema_errors = _validate_manifest(document)
    results = []
    failures = list(schema_errors)

    capabilities = document.get("capabilities", []) if isinstance(document, dict) else []
    for capability in capabilities:
        if not isinstance(capability, dict) or not isinstance(capability.get("id"), str):
            continue
        identifier = capability["id"]
        state = capability.get("state")
        check_rows = []
        for item in capability.get("checks") or []:
            ok, message = _check(item)
            check_rows.append({
                "ok": ok, "kind": item.get("kind"),
                "path": item.get("path"), "message": message,
            })
            if not ok:
                failures.append(f"{identifier}: {message}")

        gap_rows = []
        for item in capability.get("gap_checks") or []:
            ok, message = _check(item)
            gap_rows.append({
                "ok": ok, "kind": item.get("kind"),
                "path": item.get("path"), "message": message,
            })
            if not ok:
                failures.append(
                    f"{identifier}: tracked gap changed; update capability state/coverage: {message}"
                )

        results.append({
            "id": identifier,
            "category": capability.get("category"),
            "state": state,
            "owner": capability.get("owner"),
            "checks": check_rows,
            "gap_checks": gap_rows,
            "tests": list(capability.get("tests") or []),
        })

    counts = {
        "total": len(results),
        "enforced": sum(row["state"] == "enforced" for row in results),
        "tracked_gap": sum(row["state"] == "tracked_gap" for row in results),
        "failures": len(failures),
    }
    return {
        "schema_version": 1,
        "manifest": path.relative_to(ROOT).as_posix() if _inside_root(path) else str(path),
        "counts": counts,
        "capabilities": results,
        "failures": failures,
        "ok": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--json", action="store_true", help="print the complete JSON report")
    parser.add_argument("--check", action="store_true", help="return nonzero when coverage is invalid")
    args = parser.parse_args()

    try:
        report = audit_coverage(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        report = {
            "schema_version": 1,
            "manifest": str(args.manifest),
            "counts": {"total": 0, "enforced": 0, "tracked_gap": 0, "failures": 1},
            "capabilities": [],
            "failures": [f"{type(error).__name__}: {error}"],
            "ok": False,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        counts = report["counts"]
        print(
            "capability coverage: "
            f"{counts['total']} total, {counts['enforced']} enforced, "
            f"{counts['tracked_gap']} tracked gaps, {counts['failures']} failures"
        )
        for failure in report["failures"]:
            print("- " + failure)
    return 1 if args.check and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Compile a copied Ladder project and compare its cold-opened native CSV.

Run on an interactive Windows desktop with the GX Works2 version supported by
NativeEngineeringDriver. The supplied project is read only; GX Works2 operates
on output-dir/project.gxw. The reference CSV should be the intended listing (or
an earlier native export), not another export made from the resulting project.

Example:
    python scripts/native_ladder_acceptance.py program.gxw --output-dir native-result

Add --expected-program-csv program.csv for comparison with an external intended
listing. Otherwise the complete decoded source is used when it can produce a
CSV; opaque source records leave the comparison unverified, without preventing
native compilation, saving, reopening or export.

Compilation covers the whole project. CSV comparison covers the selected Ladder
Program, including instruction order, operands, steps, statements and notes.
The receipt does not assert simulation or physical PLC behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _file_record(path, raw=None):
    raw = path.read_bytes() if raw is None else raw
    return {"path": str(path), "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _json_value(value):
    if isinstance(value, bytes):
        return {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    return str(value)


def _write_receipt(path, receipt):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2,
                                    default=_json_value) + "\n", encoding="utf-8")
    temporary.replace(path)


def _program_rows(path):
    from gxworks2.csv_importer import _read_program_rows
    from gxworks2.csv_manager import CSVManager

    _, records = _read_program_rows(path)
    rows = []
    for record in records:
        if record.statement:
            rows.append({"kind": "statement", "step": record.step,
                         "text": record.statement.replace("\r\n", "\n")})
        if record.op:
            rows.append({"kind": "instruction", "step": record.step,
                         "opcode": record.op,
                         "operands": CSVManager._program_operand_cell(" ".join(record.args)),
                         "note": record.label.replace("\r\n", "\n")})
    return rows


def _csv_target_matches(path, cpu):
    from gxworks2.csv_importer import _read_program_rows
    from gxworks2.project_profiles import native_project_profile

    rows, _ = _read_program_rows(path)
    return (len(rows) >= 4 and len(rows[1]) >= 2
            and native_project_profile(cpu).accepts_plc_info(rows[1][1].strip()))


def _compare_rows(expected, actual):
    differences = []
    for index in range(max(len(expected), len(actual))):
        left = expected[index] if index < len(expected) else None
        right = actual[index] if index < len(actual) else None
        if left != right:
            differences.append({"index": index, "expected": left, "actual": right})
    return {"status": "passed" if not differences else "failed",
            "expected_count": len(expected), "actual_count": len(actual),
            "differences": differences}


def _comment_rows(path):
    from gxworks2.csv_importer import read_device_comments
    from gxworks2.csv_manager import CSVManager

    return sorted((CSVManager._program_operand_cell(device), text.replace("\r\n", "\n"))
                  for device, text in read_device_comments(path).items())


def _compile_status(report):
    """Judge the rebuilt project by what the native component actually reported."""
    report = report or {}
    workspace = report.get("native_workspace", {})
    failures = [row for row in report.get("steps", []) if row.get("code")]
    if workspace.get("status") != "observed" or failures:
        return "unverified"
    return "passed" if workspace.get("compile") == "completed_accepted" else "failed"


def _close_owned_empty_frame(driver):
    """Close only the empty GX process started for this run, without killing it."""
    import subprocess
    import win32gui
    import win32process

    frame = driver._frame_identity()
    process = getattr(driver.finder, "_started_process", None)
    if (not driver.owned or process is None or process.pid != frame.process_id
            or driver.finder.started_process_id != frame.process_id
            or frame.project_open or driver._dialogs() or process.poll() is not None
            or win32gui.GetClassName(frame.window_handle) != "GXW2FrameWnd"
            or win32process.GetWindowThreadProcessId(frame.window_handle)[1] != process.pid):
        raise RuntimeError("Cannot confirm ownership of the empty GX Works2 frame")
    win32gui.PostMessage(frame.window_handle, 0x0010, 0, 0)  # WM_CLOSE
    # The process handle remains valid after its frame disappears; the driver's
    # identity-bound wait deliberately rejects a frame that no longer exists.
    try:
        exit_code = process.wait(timeout=min(driver.timeout, 60))
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("The empty GX Works2 process did not exit after WM_CLOSE") from error
    return {"process_id": process.pid, "exit_code": exit_code, "closed": True}


def _native_run(options, receipt, receipt_path, expected_rows, expected_comments):
    import pythoncom
    from application.execution import DesktopResourceLock
    from gxworks2.engineering_driver import NativeEngineeringDriver
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

    driver = None
    path = Path(receipt["working_project"]["path"])
    output = receipt_path.parent
    stages = receipt["stages"]

    def stage(name):
        receipt["active_stage"] = name
        stages[name]["status"] = "running"
        _write_receipt(receipt_path, receipt)
        print(name, flush=True)

    # The native component rebuilds the saved copy without a visible IDE, so the
    # program, its compiled results and the file the IDE later opens are one object.
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave

    compiled = NativeWorkspaceSourceSave().compile_project(path.read_bytes())
    body_before = path.read_bytes()
    path.write_bytes(compiled.data)
    workspace = (compiled.report or {}).get("native_workspace", {})
    compile_stage = {
        "status": _compile_status(compiled.report),
        "component": "native_workspace_adapter",
        "command": "compile_project",
        "native_workspace": workspace,
        "nonzero_steps": [row for row in (compiled.report or {}).get("steps", [])
                           if row.get("code")][:5],
        # A native rebuild reserialises the program body, so the delivered body is
        # only comparable before that rebuild; record both sides of that transition.
        "body_rewritten_by_native_compile": compiled.data != body_before,
        "project": _file_record(path),
    }

    with DesktopResourceLock():
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            stage("open")
            driver = NativeEngineeringDriver.start_from_saved(
                path, receipt["cpu"], timeout=options.timeout,
                evidence_directory=output / "open", accept_library_update=True)
            initial_identity = driver.inspect()
            stages["open"] = {"status": "passed", "identity": initial_identity,
                              "library_updates": driver.open_updates}

            stage("compile")
            stages["compile"] = compile_stage

            stage("save")
            saved = driver.snapshot()
            stages["save"] = {"status": "passed", "project": _file_record(path)}
            if path.read_bytes() != saved:
                raise RuntimeError("The saved project changed before cold reopening")

            stage("cold_reopen")
            # close() releases the controller, not the native document. Close
            # the saved document first, without discarding or saving any edit.
            if driver.read_saved_project() != saved:
                raise RuntimeError("The project changed before the native close")
            driver._command(0x401)
            driver._wait(lambda: not driver._frame_identity().project_open
                         and not driver._dialogs(), "The saved Ladder project did not close")
            closed_process = _close_owned_empty_frame(driver)
            driver.close()
            driver = None
            if path.read_bytes() != saved:
                raise RuntimeError("The project changed while closing")
            driver = NativeEngineeringDriver.start_from_saved(
                path, receipt["cpu"], timeout=options.timeout,
                evidence_directory=output / "reopen", accept_library_update=False)
            reopened_identity = driver.inspect()
            old_process = (initial_identity["process_id"], initial_identity["process_started"])
            new_process = (reopened_identity["process_id"], reopened_identity["process_started"])
            if (old_process == new_process or driver.read_saved_project() != saved
                    or driver.open_updates):
                raise RuntimeError("Cold reopen did not read the saved project in a new process")
            stages["cold_reopen"] = {"status": "passed", "identity": reopened_identity,
                                     "new_process": True, "saved_file_unchanged": True,
                                     "previous_process": closed_process}

            stage("native_export")
            automation = PywinautoGXWorks2UIAutomation(timeout=options.timeout,
                                                      program=receipt["program"])
            native_program = output / "native-program.csv"
            driver._idle()
            automation.export_current_program(driver.session, native_program)
            driver._idle()
            actual_rows = _program_rows(native_program)
            comparison = (_compare_rows(expected_rows, actual_rows) if expected_rows is not None else
                          {"status": "unverified", "reason": receipt["comparison_reason"],
                           "actual_count": len(actual_rows)})
            comparison["target_matches"] = _csv_target_matches(native_program, receipt["cpu"])
            if not actual_rows or not comparison["target_matches"]:
                comparison["status"] = "failed"
            exported = {"program": _file_record(native_program), "comparison": comparison}
            if options.expected_comments_csv:
                native_comments = output / "native-comments.csv"
                automation.export_current_comments(driver.session, native_comments)
                driver._idle()
                exported["comments"] = _file_record(native_comments)
                exported["comment_comparison"] = (
                    _compare_rows(expected_comments, _comment_rows(native_comments))
                    if expected_comments is not None else
                    {"status": "unverified", "reason": receipt["comment_comparison_reason"]})
            if driver.inspect() != reopened_identity or driver.read_saved_project() != saved:
                raise RuntimeError("The project changed during native export")
            comparison_status = comparison["status"]
            if options.expected_comments_csv:
                comment_status = exported["comment_comparison"]["status"]
                comparison_status = ("failed" if "failed" in (comparison_status, comment_status) else
                                     "unverified" if "unverified" in (comparison_status, comment_status) else "passed")
            stages["native_export"] = {"status": comparison_status, "export_completed": True, **exported}
        finally:
            if driver is not None:
                driver.close()
            pythoncom.CoUninitialize()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--expected-program-csv", type=Path,
                        help="Optional intended listing; otherwise use a complete decoded source when available")
    parser.add_argument("--expected-comments-csv", type=Path)
    parser.add_argument("--program", help="Program name; may be omitted for a single-program project")
    parser.add_argument("--cpu", help="Optional expected CPU; otherwise read from the saved project")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory for the working project, native CSV and receipt.json")
    parser.add_argument("--timeout", type=float, default=60)
    options = parser.parse_args(argv)
    if not 0 < options.timeout < float("inf"):
        parser.error("--timeout must be a positive finite number")
    output = options.output_dir.expanduser().resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        parser.error(f"Cannot create a new output directory: {error}")
    receipt_path = output / "receipt.json"
    receipt = {"schema_version": 1, "operation": "native_ladder_acceptance",
               "status": "not_run", "started_at": datetime.now(timezone.utc).isoformat(),
               "active_stage": "prepare", "simulation": "not_run",
               "stages": {name: {"status": "not_run"} for name in
                          ("open", "compile", "save", "cold_reopen", "native_export")}}
    exit_code = 2
    try:
        from gxw.offline_project import read_offline_project, read_ordered_program
        from gxw.models import GXWFormatError
        from gxworks2.csv_manager import CSVManager
        from gxworks2.project_profiles import native_project_profile

        source = options.project.expanduser().resolve()
        if source.suffix.casefold() != ".gxw":
            raise ValueError("The input must be a saved .gxw project")
        raw = source.read_bytes()
        project = read_offline_project(raw)
        selected = project.select(options.program)
        if selected["language"] != "ladder" or selected["unit_kind"] != "program":
            raise ValueError("Select a Ladder Program in the supplied project")
        profile = native_project_profile(project.context["cpu"])
        expected_profile = native_project_profile(options.cpu) if options.cpu else profile
        if profile is None or expected_profile is None or profile.cpu != expected_profile.cpu:
            raise ValueError("The requested CPU does not match the saved project")
        receipt.update(cpu=profile.cpu, program=selected["name"], source=_file_record(source, raw),
                       source_program=selected["source_name"])
        expected_rows = None
        try:
            if options.expected_program_csv:
                expected = options.expected_program_csv.expanduser().resolve()
                receipt["comparison_origin"] = "supplied_reference_csv"
            else:
                expected = output / "source-listing.csv"
                listing = read_ordered_program(project, selected["source_name"])["listing"]
                from gxw.token_listing import TokenText
                if any(isinstance(record, TokenText) and record.tokens[0].raw[2] != 0
                       for record in listing.records):
                    raise GXWFormatError("Stored text flags need an external reference CSV for comparison")
                expected.write_bytes(listing.csv_bytes(title=selected["name"], plc_model=profile.cpu))
                receipt["comparison_origin"] = "decoded_input_source"
            if not _csv_target_matches(expected, profile.cpu):
                raise ValueError("The comparison CSV has no matching CPU header")
            expected_rows = _program_rows(expected)
            if not expected_rows:
                raise ValueError("The comparison CSV has no source records")
            receipt["expected_program"] = _file_record(expected)
        except (GXWFormatError, ValueError, OSError) as error:
            expected_rows = None
            receipt["comparison_reason"] = str(error)
        expected_comments = None
        if options.expected_comments_csv:
            try:
                comments = options.expected_comments_csv.expanduser().resolve()
                comment_validation = CSVManager().validate_comments(comments, require_crlf=False)
                if not comment_validation.valid:
                    raise ValueError("The reference comment CSV is invalid: "
                                     + "; ".join(comment_validation.errors))
                expected_comments = _comment_rows(comments)
                receipt["expected_comments"] = _file_record(comments)
            except (GXWFormatError, ValueError, OSError) as error:
                expected_comments = None
                receipt["comment_comparison_reason"] = str(error)
        working = output / "project.gxw"
        working.write_bytes(raw)
        receipt["working_project"] = _file_record(working)
        _write_receipt(receipt_path, receipt)
        if os.name != "nt":
            receipt["reason"] = "Native execution requires an interactive Windows desktop with GX Works2"
        else:
            _native_run(options, receipt, receipt_path, expected_rows, expected_comments)
            statuses = [stage["status"] for stage in receipt["stages"].values()]
            receipt["status"] = ("failed" if "failed" in statuses else
                                 "passed" if all(status == "passed" for status in statuses) else "unverified")
            exit_code = 0 if receipt["status"] == "passed" else 1 if receipt["status"] == "failed" else 2
    except Exception as error:
        receipt["status"] = "interrupted"
        receipt["error"] = {"type": type(error).__name__, "message": str(error),
                            "observation": getattr(error, "observation", {})}
        active = receipt["stages"].get(receipt["active_stage"])
        if active is not None and active["status"] == "running":
            active["status"] = "unverified"
        exit_code = 2
    finally:
        receipt["gx_compile_status"] = receipt["stages"]["compile"]["status"]
        receipt["export_comparison_status"] = receipt["stages"]["native_export"].get(
            "comparison", {}).get("status", "not_run")
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_receipt(receipt_path, receipt)
    print(json.dumps({"status": receipt["status"], "gx_compile_status": receipt["gx_compile_status"],
                      "export_comparison_status": receipt["export_comparison_status"],
                      "receipt": str(receipt_path)}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

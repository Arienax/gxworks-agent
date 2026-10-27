# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Run an explicit engineering plan in one visible GX Works2 project.

The CLI creates an independent GUI process unless --process-id explicitly binds
an existing process. It keeps the native window open and writes a JSON receipt,
including the completed steps of an interrupted run, to a new output directory.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _validate_plan(plan, *, allow_empty=False):
    from gxw.visible_engineering import AUTHORING_OPERATIONS

    if not isinstance(plan, dict) or set(plan) != {"cpu", "commands"}:
        raise ValueError("The plan requires exactly cpu and commands")
    cpu, commands = plan["cpu"], plan["commands"]
    if not isinstance(cpu, str) or cpu.strip().upper() != 'FX3U':
        raise ValueError('Visible native engineering is verified only for concrete FX3U; other CPU targets are unverified')
    operations = AUTHORING_OPERATIONS | {"create_fbd_call", "edit_fbd_graph", "compile", "show_assignments", "save", "reopen"}
    if (not isinstance(commands, list) or not (0 if allow_empty else 1) <= len(commands) <= 256
            or any(not isinstance(item, dict) or item.get("operation") not in operations
                   for item in commands)):
        raise ValueError("The plan requires 1 to 256 supported engineering commands")
    normalized = deepcopy(plan)
    normalized['cpu'] = 'FX3U'
    return normalized


def _write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute_plan(plan, *, project, output_directory, process_id=None, timeout=30,
                 prepare_plan=None, language='fbd'):
    """Run an explicit plan under one desktop lock and COM apartment.

    Optional prepare_plan receives the initial active-document bytes and returns
    the concrete plan, allowing a local caller to bind native configuration names.
    It runs before any engineering command. No offline Workspace is used.
    """
    receipt = {"status": "interrupted", "passed": False, "steps": [],
               "execution": "not_run", "window_policy": "keep_open"}
    directory = None
    driver = None
    session_started = False
    try:
        directory = Path(output_directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=False)
        receipt["receipt_file"] = str(directory / "receipt.json")
        receipt["phase"] = "preparing"
        _write_json(directory / "receipt.json", receipt)
        if isinstance(plan, Path):
            plan = json.loads(plan.read_text(encoding="utf-8-sig"))
        plan = _validate_plan(plan, allow_empty=prepare_plan is not None)
        target = Path(project).expanduser().resolve()
        if target.suffix.casefold() != ".gxw":
            raise ValueError("The explicit project path must end in .gxw")
        if target in {directory / name for name in ("initial.gxw", "final.gxw", "last-verified.gxw")}:
            raise ValueError("The project path collides with a session snapshot; select a different project name")
        if process_id is not None and (type(process_id) is not int or process_id <= 0):
            raise ValueError("An attached native process requires an explicit positive process ID")
        if language not in ('fbd', 'st') or process_id is not None and language != 'fbd':
            raise ValueError('The new-project language must be fbd or st and cannot be specified for an attached project')
        if process_id is None and target.exists():
            raise FileExistsError("The new native project already exists; select an exclusive project path")
        if process_id is not None and not target.is_file():
            raise FileNotFoundError("The explicitly attached project file does not exist")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("The native timeout must be positive and finite")
        if process_id is None:
            target.parent.mkdir(parents=True, exist_ok=True)
        request = {"project": str(target), "process_id": process_id,
                   "timeout": timeout, "plan": plan}
        _write_json(directory / "request.json", request)
        receipt["project"] = str(target)
        receipt["requested_cpu"] = plan["cpu"]

        def progress(*events):
            # Persist step boundaries before returning to the driver/controller.
            event = events[0] if len(events) == 1 else {"stage": events[0], "details": list(events[1:])}
            if isinstance(event, dict) and type(event.get("index")) is int:
                step = deepcopy(event)
                for index, old in enumerate(receipt["steps"]):
                    if old.get("index") == step["index"]:
                        receipt["steps"][index] = step
                        break
                else:
                    receipt["steps"].append(step)
            receipt["phase"] = "executing"
            _write_json(directory / "receipt.json", receipt)
            print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

        # Keep library/native diagnostics out of the CLI's single JSON stdout.
        with redirect_stdout(sys.stderr):
            from application.execution import DesktopResourceLock
            from application.native_engineering import NativeEngineeringSession
            from gxworks2.engineering_driver import NativeEngineeringDriver
            import pythoncom

            with DesktopResourceLock():
                pythoncom.CoInitialize()
                try:
                    options = {"timeout": timeout, "progress": progress,
                               "evidence_directory": directory}
                    if process_id is None:
                        if language != 'fbd':
                            options['language'] = language
                        driver = NativeEngineeringDriver.start_new(target, plan["cpu"], **options)
                    else:
                        driver = NativeEngineeringDriver.attach(process_id, target, plan["cpu"], **options)
                    baseline = driver.snapshot()
                    if not isinstance(baseline, (bytes, bytearray)) or not baseline:
                        raise ValueError("The active native project did not return an initial saved snapshot")
                    with (directory / "initial.gxw").open("xb") as stream:
                        stream.write(bytes(baseline))
                    if prepare_plan is not None:
                        prepared = _validate_plan(prepare_plan(bytes(baseline)))
                        from gxworks2.project_profiles import native_project_profile
                        if native_project_profile(prepared["cpu"]).cpu != native_project_profile(plan["cpu"]).cpu:
                            raise ValueError("Preparing the plan changed the explicit native CPU")
                        plan = prepared
                        request["plan"] = plan
                        _write_json(directory / "request.json", request)
                    session = NativeEngineeringSession(driver, expected_cpu=plan["cpu"], progress=progress)
                    session_started = True
                    result = session.run(plan["commands"], baseline=bytes(baseline))
                    data = result.get("data")
                    receipt.update({key: value for key, value in result.items()
                                    if key not in ("data", "unverified_data")})
                    if "unverified_data" in result:
                        receipt["unverified_data_present"] = True
                    if isinstance(data, (bytes, bytearray)) and data:
                        filename = "final.gxw" if receipt.get("passed") is True else "last-verified.gxw"
                        with (directory / filename).open("xb") as stream:
                            stream.write(bytes(data))
                        receipt["snapshot"] = {"path": str(directory / filename),
                            "status": "completed" if receipt.get("passed") is True else "last_verified_source_snapshot"}
                    elif receipt.get("passed") is True:
                        raise ValueError("The completed engineering session did not return verified project bytes")
                finally:
                    try:
                        if driver is not None and not session_started:
                            driver.close()
                    finally:
                        pythoncom.CoUninitialize()
        receipt["phase"] = "finished"
    except Exception as error:
        receipt.update(status="interrupted", passed=False, phase="finished",
            error={"type": type(error).__name__, "message": str(error)})
    finally:
        if directory is not None and "receipt_file" in receipt:
            try:
                _write_json(directory / "receipt.json", receipt)
            except Exception as error:
                receipt.update(status="interrupted", passed=False,
                    receipt_write_error={"type": type(error).__name__, "message": str(error)})
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="UTF-8 JSON with cpu and commands")
    parser.add_argument("--project", required=True, type=Path, help="Exclusive new .gxw path, or the exact attached project")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for receipts and snapshots")
    parser.add_argument("--process-id", type=int, help="Attach only this explicit existing GX Works2 process")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--language", choices=['fbd', 'st'], default='fbd', help="Language of an exclusive new structured project")
    args = parser.parse_args(argv)
    result = execute_plan(args.plan, project=args.project, output_directory=args.output_dir,
                          process_id=args.process_id, timeout=args.timeout, language=args.language)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

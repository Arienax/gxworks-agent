"""One lazy, apartment-bound owner of the local GX desktop execution resource.

Only the application's proposal approval callback calls ``submit_approved``.
This is deliberately not an HTTP API, and an approval id is an audit binding,
not a substitute for the proposal service's permission and integrity checks.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import queue
import re
import tempfile
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Mapping


class ExecutionUnavailableError(RuntimeError):
    """No operation was started because its desktop environment is unavailable."""


class DesktopResourceLock:
    """OS-released, cross-process lock shared by all local GX coordinators."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(tempfile.gettempdir()) / "gxworks-agent-desktop.lock"
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            handle.close()
            raise ExecutionUnavailableError("GX desktop resource is owned by another process.") from error
        self._file = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        handle, self._file = self._file, None
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _default_com():
    if os.name != "nt":
        raise ExecutionUnavailableError("GX execution requires an interactive Windows desktop.")
    try:
        return importlib.import_module("pythoncom")
    except ImportError as error:
        raise ExecutionUnavailableError("The Windows COM runtime is not installed.") from error


def _default_dependencies(operation):
    """Loading this bundle is permitted only inside an explicit worker task."""
    from gxworks2 import import_current_program

    bundle = {"importer": import_current_program}
    if operation == "gx_import":
        from gxworks2.project_import import import_gxw_project
        bundle["gxw_importer"] = import_gxw_project
    if operation in {"read_gx", "inspect_gx"}:
        from gxworks2 import read_current_snapshot, inspect_current_sync

        bundle.update(reader=read_current_snapshot, inspector=inspect_current_sync)
    if operation in {"simulation", "debug"}:
        from gxworks2 import GXSimulator2PreparationService
        from simulator.runtime import get_simulator_gateway_runtime

        runtime = get_simulator_gateway_runtime()
        bundle.update(preparer=GXSimulator2PreparationService(runtime=runtime), backend=runtime.client(timeout=5.0))
    return bundle


def read_gx_environment():
    """Observe GX Works2 without starting it or touching an engineering project."""
    from gxworks2.finder import GXWorks2Finder

    try:
        session = GXWorks2Finder().find_running()
    except Exception:
        session = None
    running = session is not None
    return {
        "status": "ready" if running else "unavailable",
        "passed": running,
        "desktop_execution_required": True,
        "gx_works2_running": running,
        "project_open": bool(session and session.project_open),
        "message": (
            "GX Works2 已运行。"
            if running
            else "GX Works2 未运行，请先启动 GX Works2 后再发送。"
        ),
    }


def read_environment():
    """Observe installed software/processes without creating or starting a gateway."""
    from simulator.gateway import detect_simulator_environment

    evidence = detect_simulator_environment()
    installed = bool(evidence.get("simulator_installed") and evidence.get("mx_component_installed"))
    return {
        "status": "unverified" if installed else "unavailable",
        "passed": False,
        "desktop_execution_required": True,
        "gateway_started": False,
        "evidence": evidence,
    }


def _mapping(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise ValueError("GX workflow returned an invalid result.")
    result = copy.deepcopy(dict(value))
    code = result.get("error_code")
    if hasattr(code, "value"):
        result["error_code"] = code.value
    return result


def _record_id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Invalid %s id." % label)
    return value


def _contained(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError as error:
        raise ValueError("Execution artifact escapes its managed directory.") from error


class GXExecutionCoordinator:
    """Serialize import, simulation and debug on one fixed COM thread.

    Futures can be cancelled while queued. Once a task has started, closing a
    browser or requesting cancellation cannot undo an external GX operation.
    Shutdown drains approved operations by default; it never retries them.
    """

    OPERATIONS = {"gx_import", "simulation", "debug"}
    READ_OPERATIONS = {"inspect_gx", "read_gx"}
    _ALIASES = {"import_gx": "gx_import", "simulate": "simulation"}

    def __init__(self, store, *, resource_lock_path=None, dependencies_factory=None, com_factory=None):
        self.store = store
        self.resource_lock_path = resource_lock_path
        self._dependencies_factory = dependencies_factory or _default_dependencies
        self._com_factory = com_factory or _default_com
        self._queue = queue.Queue()
        self._guard = threading.Lock()
        self._thread = None
        self._closed = False

    def submit_approved(self, operation, payload, *, approval_id, progress=None, test_progress=None):
        operation = self._ALIASES.get(operation, operation)
        if operation not in self.OPERATIONS:
            raise ValueError("Unsupported approved GX operation.")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("An approved proposal id is required.")
        return self._submit(operation, payload, approval_id, progress, test_progress)

    def submit_read(self, operation, payload, *, progress=None):
        """Explicit operator read/export command; never an Agent tool or page load."""
        if operation not in self.READ_OPERATIONS:
            raise ValueError("Unsupported GX read operation.")
        return self._submit(operation, payload, None, progress, None)

    def _submit(self, operation, payload, approval_id, progress, test_progress):
        if not isinstance(payload, Mapping):
            raise ValueError("Execution payload must be an object.")
        frozen = copy.deepcopy(dict(payload))
        future = Future()
        with self._guard:
            if self._closed:
                raise RuntimeError("GX execution coordinator is closed.")
            self._queue.put((future, operation, frozen, approval_id, progress, test_progress))
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, name="gx-desktop-execution", daemon=True)
                self._thread.start()
        return future

    def _worker(self):
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                future, operation, payload, approval_id, progress, test_progress = item
                if not future.set_running_or_notify_cancel():
                    continue
                com = None
                try:
                    with DesktopResourceLock(self.resource_lock_path):
                        com = self._com_factory()
                        com.CoInitialize()
                        try:
                            result = (
                                self._read(operation, payload, progress)
                                if operation in self.READ_OPERATIONS
                                else self._execute(operation, payload, progress, test_progress)
                            )
                        finally:
                            com.CoUninitialize()
                    result["approval_id"] = approval_id
                    result["operation"] = operation
                    future.set_result(result)
                except Exception as error:
                    future.set_result({
                        "status": "unavailable" if isinstance(error, ExecutionUnavailableError) else "error",
                        "passed": False,
                        "message": str(error),
                        "approval_id": approval_id,
                        "operation": operation,
                    })
            finally:
                self._queue.task_done()

    def _version(self, payload, *, allow_empty=False):
        project_id = _record_id(payload.get("project_id"), "project")
        selected = payload.get("version_id") or payload.get("base_version_id")
        version_id = _record_id(selected, "version") if selected else None
        project = self.store.get_project(project_id)
        if not project or project.get("active_version_id") != version_id:
            raise ValueError("Operation is stale: the active project version changed.")
        if version_id is None:
            if allow_empty:
                return project_id, None, {}, None
            raise ValueError("Operation requires an active project version.")
        version = self.store.get_version(project_id, version_id)
        if not isinstance(version, Mapping):
            raise ValueError("Approved version no longer exists.")
        root = self.store.version_dir(project_id, version_id)
        _contained(root, self.store.project_dir(project_id))
        return project_id, version_id, version, root

    @staticmethod
    def _csv_artifacts(version, root):
        artifacts = version.get("artifacts") or {}
        paths = []
        for name in ("program_csv", "comment_csv"):
            filename = str(artifacts.get(name) or "")
            if not filename or ":" in filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
                raise ValueError("Execution artifact escapes its managed directory or names an alternate stream.")
            path = root / filename
            _contained(path, root)
            if not path.is_file():
                raise ValueError("Selected version is missing managed GX CSV artifacts.")
            paths.append(path)
        return paths

    def _read(self, operation, payload, progress):
        project_id, version_id, version, root = self._version(payload, allow_empty=operation == "read_gx")
        project = copy.deepcopy(self.store.get_project(project_id))
        bundle = self._dependencies_factory(operation)
        context = {"project_id": project_id, "version_id": version_id,
                   "program_name": version.get("program_name") or "MAIN",
                   "revision": version.get("revision"), "ir_sha256": version.get("ir_sha256")}
        if operation == "inspect_gx":
            paths = self._csv_artifacts(version, root)
            sync = _mapping(bundle["inspector"](
                *paths, progress=progress, import_context=context,
                save_project=False, persist_baseline=False,
            ))
        else:
            sync = _mapping(bundle["reader"](progress=progress, import_context=context, save_project=False))
        result = {"status": "inspected" if operation == "inspect_gx" else "read", "passed": False,
                  "project_id": project_id, "version_id": version_id,
                  "message": sync.get("message", ""), "sync": sync}
        if sync.get("success") is not True or sync.get("error_code"):
            unavailable = {"gx_works2_not_running", "gx_project_not_open", "gx_program_not_ready",
                           "gx_automation_unavailable", "gx_uia_access_denied", "automation_unavailable"}
            result["status"] = "unavailable" if sync.get("error_code") in unavailable else "error"
            return result
        if operation == "inspect_gx":
            messages = {
                "needs_push": "本地版本相对同步基线有变更，尚未导入GX Works2。",
                "needs_pull": "GX Works2相对同步基线有变更，可读取为待审批候选。",
                "conflict": "本地与GX Works2均有变更，请审查差异后选择后续操作。",
                "unbound": "尚无匹配的同步基线，请审查两侧内容后决定后续操作。",
            }
            if sync.get("status") in messages:
                sync["message"] = messages[sync["status"]]
                result["message"] = sync["message"]
        if operation == "read_gx":
            from gxworks2.csv_importer import materialize_gxworks2_version, parse_gxworks2_csv
            from plc.validation import find_unverified_app_instructions

            exported_program = Path(str(sync.get("exported_program_path") or ""))
            exported_comments = Path(str(sync.get("exported_comment_path") or ""))
            if not exported_program.is_file() or not exported_comments.is_file():
                raise ValueError("GX snapshot is missing its exported CSV evidence.")
            native = parse_gxworks2_csv(exported_program, exported_comments)
            unverified = find_unverified_app_instructions(native.ladder)
            if unverified:
                result.update(
                    status="unsupported", error_code="unverified_native_instruction",
                    message="当前GX程序包含语义目录尚未覆盖的原生指令。Web暂不能接受此候选；请保留GX原工程，并使用独立原生CSV解析接口进行只读检查。",
                    findings=unverified,
                )
                return result
            # Reuse native CSV decoding AND lossless round-trip validation. The
            # temporary artifacts are consumed before cleanup; no path escapes
            # into an HTTP download and no SessionStore version is prepared.
            with tempfile.TemporaryDirectory(prefix="gx-read-candidate-") as directory:
                metadata = materialize_gxworks2_version(
                    exported_program, exported_comments, directory,
                    plc_model=version.get("plc_model") or project.get("plc_model") or "FX3U",
                    program_name=version.get("program_name") or (sync.get("details") or {}).get("program_name") or "MAIN",
                    revision=int(version.get("revision") or 0) + 1,
                )
                candidate = json.loads((Path(directory) / metadata["artifacts"]["ir"]).read_text(encoding="utf-8"))
                result["_candidate_ir"] = candidate
                result["_confirmed_spec"] = copy.deepcopy(project.get("confirmed_spec"))
                result["candidate_metadata"] = {key: value for key, value in metadata.items() if key != "artifacts"}
            # Read/export did not grant permission to replace the active version.
            result["requires_local_acceptance"] = True
        return result

    def _execute(self, operation, payload, progress, test_progress):
        project_id, version_id, version, root = self._version(payload)
        # Dependencies are loaded only after the queued snapshot has been rechecked.
        bundle = self._dependencies_factory(operation)
        if operation == "gx_import":
            if version.get("target_mode") == "fbd":
                from application.fbd import validate_candidate
                from application.workspace import artifact_relative_path, contained
                artifacts = {name: contained(root / artifact_relative_path(filename), root).read_bytes()
                             for name, filename in version["artifacts"].items()}
                validate_candidate(artifacts)
                # Native Save/Compile must never mutate the immutable version.
                directory = contained(self.store.project_dir(project_id) / "gxw_runs" / uuid.uuid4().hex,
                                      self.store.project_dir(project_id))
                directory.mkdir(parents=True, exist_ok=False)
                # GX Works2 1.635 rejects stems longer than 30 characters.
                # The exclusive UUID directory remains the full run identity.
                path = directory / ("gxw_" + directory.name[:24] + ".gxw")
                path.write_bytes(artifacts["gxw"])
                imported = _mapping(bundle["gxw_importer"](path, progress=progress,
                    expected_sha256=hashlib.sha256(artifacts["gxw"]).hexdigest()))
                success = imported.get("success") is True and not imported.get("error_code")
                return {"status": "imported" if success else "import_failed", "passed": False,
                        "message": imported.get("message", ""), "import": imported,
                        "gx_compile_status": "unverified", "simulation_status": "not_run"}
            paths = self._csv_artifacts(version, root)
            # Only the Web operator's version-bound backup acknowledgement
            # selects the fast path. Old proposals and Agent calls stay protected.
            pre_import_policy = ("manual_backup" if payload.get("manual_backup_acknowledged") is True
                                 else "protected")
            imported = _mapping(bundle["importer"](
                paths[0], comment_csv_path=paths[1], start_if_needed=False,
                synchronize_comments=True, verify_roundtrip=False, save_project=True,
                pre_import_policy=pre_import_policy,
                progress=progress,
                import_context={
                    "project_id": project_id, "version_id": version_id,
                    "revision": version.get("revision"), "ir_sha256": version.get("ir_sha256"),
                    "source": "approved_application_proposal",
                },
            ))
            success = imported.get("success") is True and not imported.get("error_code")
            details = imported.get("details") or {}
            return {
                "status": "imported" if success else "import_failed",
                "passed": False,
                "message": imported.get("message", ""),
                "import": imported,
                "gx_import_summary": {
                    "pre_import_policy": pre_import_policy,
                    "backup_performed": details.get("backup_performed", False),
                    "timings_ms": details.get("timings_ms", {}),
                },
                "gx_compile_status": "unverified", "simulation_status": "not_run",
            }
        plan = payload.get("plan")
        if not isinstance(plan, Mapping):
            raise ValueError("An immutable, version-bound approved plan is required.")
        if operation == "simulation":
            from simulator.workflow import SimulatorVersionWorkflowService

            service = SimulatorVersionWorkflowService(
                self.store, importer=bundle["importer"], preparer=bundle["preparer"], backend=bundle.get("backend"),
            )
            result = _mapping(service.run_approved_plan(project_id, version_id, plan, progress=progress, test_progress=test_progress))
        else:
            from application.debug_loop import DebugPatchLoopService

            if plan.get("project_id") != project_id or plan.get("base_version_id") != version_id:
                raise ValueError("Debug plan does not belong to the approved version.")
            service = DebugPatchLoopService(
                self.store, importer=bundle["importer"], simulator_preparer=bundle["preparer"], simulator_backend=bundle.get("backend"),
            )
            result = _mapping(service.execute_approved_plan(plan))
        # A job finishing is not proof of a verified PLC test result.
        result["passed"] = result.get("status") == "passed"
        return result

    def close(self, *, wait=True, cancel_pending=False):
        with self._guard:
            if not self._closed:
                self._closed = True
                if cancel_pending:
                    while True:
                        try:
                            item = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if item is not None:
                            item[0].cancel()
                        self._queue.task_done()
                if self._thread is not None:
                    self._queue.put(None)
            thread = self._thread
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

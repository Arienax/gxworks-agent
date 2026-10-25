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
import math
import os
import queue
import re
import tempfile
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Mapping

from shared.records import record_id as checked_record_id, contained as checked_contained


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
    if operation == 'native_engineering':
        from gxworks2.engineering_driver import NativeEngineeringDriver
        from application.native_engineering import NativeEngineeringSession
        def live_session_factory(*args, **kwargs):
            from application.live_engineering import LiveEngineeringSession
            return LiveEngineeringSession(*args, **kwargs)
        return {'driver_factory': NativeEngineeringDriver.start_from_saved,
                'session_factory': NativeEngineeringSession, 'live_session_factory': live_session_factory}
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
    installed = GXWorks2Finder().find_executable() is not None
    return {
        "status": "ready" if running else "unavailable",
        "passed": running,
        "desktop_execution_required": True,
        "gx_works2_running": running,
        "gx_works2_installed": installed,
        "project_open": bool(session and session.project_open),
        "message": (
            "GX Works2 已运行。"
            if running
            else "GX Works2已安装，发送时将启动并准备支持的目标工程。" if installed
            else "未找到GX Works2安装程序。"
        ),
    }


def knowledge_runtime_status():
    """Application-owned availability query for protocol launchers."""
    from knowledge.scope import runtime_status
    return runtime_status()


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
    return checked_record_id(value, message="Invalid %s id." % label)


def _contained(path, root):
    checked_contained(path, root, message="Execution artifact escapes its managed directory.")


def _native_engineering_filename(projection):
    """Use the saved logical project identity inside the exclusive run folder."""
    name = (projection.get('configuration') or {}).get('name')
    stem = name[:-4] if isinstance(name, str) and name.endswith('.prj') else ''
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)),
                *(f'LPT{n}' for n in range(1, 10))}
    if (not 1 <= len(stem) <= 30 or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem)
            or stem.endswith(('.', ' ')) or stem.split('.')[0].upper() in reserved):
        raise ValueError('Saved native project identity is not a supported managed GX filename')
    return stem + '.gxw'


def native_saved_cpu(version, project):
    """Require explicit saved selection; generic syntax must never choose a CPU."""
    cpu = version.get('plc_model') or project.get('plc_model')
    if not isinstance(cpu, str) or not cpu.strip():
        raise ValueError('Native engineering requires an explicit saved project CPU model')
    return cpu.strip()


def _native_diagnostic_value(value):
    """Detach private diagnostics without serializing project or binary data."""
    if isinstance(value, Mapping):
        return {key: _native_diagnostic_value(item) for key, item in value.items()
                if isinstance(key, str) and key not in {'data', 'unverified_data'}
                and not isinstance(item, (bytes, bytearray, memoryview))}
    if isinstance(value, (list, tuple)):
        return [_native_diagnostic_value(item) for item in value
                if not isinstance(item, (bytes, bytearray, memoryview))]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


class GXExecutionCoordinator:
    """Serialize import, simulation and debug on one fixed COM thread.

    Futures can be cancelled while queued. Once a task has started, closing a
    browser or requesting cancellation cannot undo an external GX operation.
    Shutdown drains approved operations by default; it never retries them.
    """

    OPERATIONS = {"gx_import", "simulation", "debug", "native_engineering"}
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
        self._live_guard = threading.RLock()
        self._live_sessions = {}
        self._live_com = None
        self._worker_com = None

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
                    # A controller close never saves or closes its IDE. Release
                    # apartment-bound wrappers on their owning worker thread.
                    self._close_live_sessions()
                    return
                future, operation, payload, approval_id, progress, test_progress = item
                if not future.set_running_or_notify_cancel():
                    continue
                com = None
                try:
                    with DesktopResourceLock(self.resource_lock_path):
                        com = self._com_factory()
                        com.CoInitialize()
                        self._worker_com = com
                        try:
                            result = (
                                self._read(operation, payload, progress)
                                if operation in self.READ_OPERATIONS
                                else self._execute(operation, payload, progress, test_progress)
                            )
                        finally:
                            com.CoUninitialize()
                            self._worker_com = None
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
            from gxworks2.csv_importer import (GXCSVImportError, inspect_gxworks2_csv,
                                               materialize_gxworks2_version, parse_gxworks2_csv,
                                               read_plc_info_target)
            from plc.validation import find_unverified_app_instructions

            exported_program = Path(str(sync.get("exported_program_path") or ""))
            exported_comments = Path(str(sync.get("exported_comment_path") or ""))
            if not exported_program.is_file() or not exported_comments.is_file():
                raise ValueError("GX snapshot is missing its exported CSV evidence.")
            # The export names its own CPU; only fall back to it when neither the
            # stored version nor the project record states one.
            snapshot_model = (version.get("plc_model") or project.get("plc_model")
                              or read_plc_info_target(exported_program) or "")
            if not snapshot_model:
                raise ValueError(
                    "无法确认该GX程序的目标CPU：工程记录与导出CSV均未给出型号，"
                    "不能按其他型号推算步号。")
            snapshot_program = (version.get("program_name")
                                or (sync.get("details") or {}).get("program_name") or "MAIN")
            try:
                native = parse_gxworks2_csv(exported_program, exported_comments)
            except GXCSVImportError as graph_error:
                # An export the drawing model cannot represent is still an
                # ordered statement list; read it instead of refusing the pull.
                ordered = inspect_gxworks2_csv(exported_program, exported_comments,
                                               plc_model=snapshot_model,
                                               program_name=snapshot_program)
                if ordered["coverage"].get("ir_status") != "valid":
                    raise graph_error
                unverified = ordered["coverage"].get("unknown_instructions") or []
            else:
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
                    plc_model=snapshot_model,
                    program_name=snapshot_program,
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
        from application.workspace import contained

        project_id, version_id, version, root = self._version(payload)
        if operation == 'native_engineering':
            return self._native_engineering(payload, version, root, project_id, progress)
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
            # Web, MCP and replayed proposals share the same file-backup import.
            pre_import_policy = "project_file"
            directory = contained(self.store.project_dir(project_id) / "gx_runs" / uuid.uuid4().hex,
                                  self.store.project_dir(project_id))
            directory.mkdir(parents=True, exist_ok=False)
            imported = _mapping(bundle["importer"](
                paths[0], comment_csv_path=paths[1], start_if_needed=True,
                create_project_path=directory / "gx_import.gxw",
                # A long project path reaches us as an abbreviated caption, so the
                # operator may name the file explicitly; without it the service
                # refuses instead of guessing which project to write into.
                project_path=payload.get("project_path"),
                synchronize_comments=True, verify_roundtrip=False, save_project=True,
                pre_import_policy=pre_import_policy,
                progress=progress,
                import_context={
                    "project_id": project_id, "version_id": version_id,
                    "revision": version.get("revision"), "ir_sha256": version.get("ir_sha256"),
                    "plc_model": version.get("plc_model") or self.store.get_project(project_id).get("plc_model"),
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
            view, view_error = None, ""
            try:
                from application.simulator_view import get_simulation_views
                from simulator.models import normalize_test_suite
                from simulator.signals import suite_view_config
                suite = normalize_test_suite(plan.get("suite"), plc_model=version.get("plc_model") or "FX3U")
                view = get_simulation_views().create_normalized(suite_view_config(suite),
                    source={"kind": "test_execution", "backend_kind": str(getattr(bundle.get("backend"), "backend_kind", "unknown")),
                            "program_correspondence": "see_version_verification", "physical_plc": False},
                    binding={"project_id": project_id, "version_id": version_id})
            except (ValueError, OSError) as error:
                view_error = str(error)

            def observed_progress(update):
                if view is not None:
                    view.progress(update)
                if test_progress is not None:
                    test_progress(update)
            observed_progress.capture_samples = view is not None
            try:
                result = _mapping(service.run_approved_plan(project_id, version_id, plan,
                    progress=progress, test_progress=observed_progress))
            except Exception as error:
                if view is not None:
                    view.finish("error", str(error))
                raise
            if view is not None:
                view.finish(result.get("status", "error"), result.get("message", "") if result.get("status") == "error" else "")
                result["visualization"] = {"view_ref": view.ref, "url": view.url}
            elif view_error:
                result["visualization"] = {"status": "unavailable", "error": view_error}
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

    def _native_engineering(self, payload, version, root, project_id, progress):
        from application.native_engineering import (normalize_native_plan, validate_native_baseline,
            verified_native_data, native_engineering_summary)
        from application.workspace import artifact_relative_path, contained
        if set(payload) - {'project_id', 'version_id', 'plan', 'type', 'change_scope'}:
            raise ValueError('Native engineering accepts only a version-bound domain plan')
        if version.get('target_mode') not in {'fbd', 'st'}:
            raise ValueError('Native engineering requires a saved structured FBD or ST version')
        cpu = native_saved_cpu(version, self.store.get_project(project_id))
        if isinstance(payload.get('plan'), dict) and payload['plan'].get('mode') == 'live':
            return self._live_native_engineering(payload, version, root, project_id, cpu, progress)
        plan = normalize_native_plan(payload.get('plan'), plc_model=cpu)
        artifacts = {name: contained(root / artifact_relative_path(filename), root).read_bytes()
                     for name, filename in version['artifacts'].items()}
        if version['target_mode'] == 'st':
            from application.native_st import validate_candidate
            validate_candidate(artifacts, plc_model=cpu, program_name=version.get('program_name'), metadata=version)
        else:
            from application.fbd import validate_candidate
            validate_candidate(artifacts)
        projection = validate_native_baseline(artifacts['gxw'], plc_model=cpu)
        filename = _native_engineering_filename(projection)
        directory = contained(self.store.project_dir(project_id) / 'gxw_runs' / uuid.uuid4().hex,
                              self.store.project_dir(project_id))
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / filename
        path.write_bytes(artifacts['gxw'])
        def observed(step):
            # Native observations and errors may contain private evidence paths.
            # Only a sentence formed from whitelisted operation/status values
            # leaves the desktop worker; machine receipt fields stay unchanged.
            if progress is not None:
                actions = {
                    'create_structure': '建立结构类型', 'create_global_labels': '建立全局标签表',
                    'edit_global_labels': '写入全局声明', 'edit_pou_labels': '写入局部声明',
                    'create_st_fb': '建立 ST 功能块', 'edit_st_fb': '写入 ST 实现',
                    'create_st_program': '建立 ST 程序并登录任务', 'edit_st_program': '写入 ST 程序',
                    'edit_fbd_graph': '修改图形连接',
                    'create_fbd_call': '建立功能块调用', 'configure_project': '设置自动分配范围',
                    'compile': '编译工程', 'show_assignments': '核对地址映射',
                    'save': '保存工程', 'reopen': '重新打开工程',
                }
                for change in native_engineering_summary({'steps': [step]})['changes']:
                    action = actions.get(change['operation'])
                    if action is not None:
                        progress({
                            'executing': f'正在{action}。', 'completed': f'已完成：{action}。',
                            'failed': f'{action}未完成。', 'interrupted': f'已中止{action}。',
                            'unverified': f'尚未确认{action}是否完成。',
                        }[change['status']])
        try:
            bundle = self._dependencies_factory('native_engineering')
            driver = bundle['driver_factory'](path, cpu, timeout=30, progress=None,
                                             evidence_directory=directory / 'evidence')
            try:
                session = bundle['session_factory'](driver, expected_cpu=cpu, progress=observed)
            except Exception:
                driver.close()
                raise
            result = session.run(plan['commands'], baseline=artifacts['gxw'])
            from application.workspace import atomic_json
            atomic_json(contained(directory / 'receipt.json', directory), _native_diagnostic_value(result))
        except Exception as error:
            from application.workspace import atomic_json
            failure = {'type': type(error).__name__, 'message': str(error),
                       'observation': getattr(error, 'observation', None)}
            atomic_json(contained(directory / 'failure.json', directory), _native_diagnostic_value(failure))
            raise
        summary = native_engineering_summary(result)
        try:
            verified_native_data(result, plan)
        except ValueError:
            summary.update(status='interrupted', passed=False,
                message='原生工程会话或请求的验证层未完成，未创建活动版本。')
            return summary
        # Internal-only receipt, consumed by WorkbenchService before projection.
        return {'status': 'completed', 'passed': True, '_native_result': result}

    def _release_live_com(self):
        if self._live_com is not None and not any(not row['closed'] for row in self._live_sessions.values()):
            com, self._live_com = self._live_com, None
            com.CoUninitialize()

    def _persist_live_receipt(self, row):
        from application.workspace import atomic_json, contained
        with self._live_guard:
            durable = copy.deepcopy(row['cached'])
        native_error = getattr(row['controller'], '_private_native_error', None)
        if native_error is not None:
            durable['private_native_observation'] = _native_diagnostic_value(native_error)
        atomic_json(contained(row['directory'] / 'receipt.json', row['directory']), durable)

    def _close_live_sessions(self):
        try:
            with self._live_guard:
                rows = list(self._live_sessions.values())
            for row in rows:
                if not row['closed']:
                    controller_closed = False
                    try:
                        result = row['controller'].close()
                        if not isinstance(result, dict):
                            result = row['controller'].status()
                        with self._live_guard:
                            row['cached'] = _native_diagnostic_value(result)
                        controller_closed = True
                    except Exception as error:
                        with self._live_guard:
                            row['cached'].update(status='requires_reconciliation', passed=False,
                                                 error={'type': type(error).__name__, 'message': str(error)})
                    finally:
                        with self._live_guard:
                            # The service worker is ending; this registry row
                            # cannot be resumed even when native close refused.
                            row['closed'] = True
                            row['cached'].update(controller_closed=controller_closed,
                                                 ide_policy='keep_open', com_release='worker_shutdown')
                        try:
                            self._persist_live_receipt(row)
                        except Exception as error:
                            with self._live_guard:
                                row['cached']['receipt_error'] = {'type': type(error).__name__, 'message': str(error)}
        finally:
            self._release_live_com()

    def live_session_status(self, project_id, session_id=None):
        """Read a detached cache, without queueing work or touching native UI."""
        project_id = _record_id(project_id, 'project')
        with self._live_guard:
            if session_id is None:
                active = [(key, value) for key, value in self._live_sessions.items()
                          if value['project_id'] == project_id and not value['closed']]
                if not active:
                    return {'mode': 'background', 'status': 'not_started', 'project_id': project_id}
                if len(active) != 1:
                    raise RuntimeError('Multiple active live sessions in this project')
                session_id, row = active[0]
            else:
                session_id = _record_id(session_id, 'session')
                row = self._live_sessions.get(session_id)
                if row is None or row['project_id'] != project_id:
                    raise KeyError('Live session not found in this project')
            return {**copy.deepcopy(row['cached']), 'mode': 'live', 'session_id': session_id,
                    'project_id': project_id, 'base_version_id': row['base_version_id'],
                    'operation_in_progress': row['busy'], 'pending_save_transaction': bool(row['pending_save']),
                    'pending_stage_save': copy.deepcopy(row['pending_save']) if row['pending_save'] else None}

    def request_live_session_pause(self, project_id, session_id):
        """Signal a controller Event; no GUI call or desktop execution is queued."""
        project_id, session_id = _record_id(project_id, 'project'), _record_id(session_id, 'session')
        with self._live_guard:
            row = self._live_sessions.get(session_id)
            if row is None or row['project_id'] != project_id:
                raise KeyError('Live session not found in this project')
            if row['closed']:
                raise ValueError('The live session is closed')
            request = row['controller'].request_pause()
            row['cached']['pause_requested'] = True
            return {**copy.deepcopy(request), 'mode': 'live', 'project_id': project_id,
                    'session_id': session_id, 'pause_requested': True}

    @staticmethod
    def _live_stage_save_receipt(result):
        """Bind returned bytes to this run's confirmed save, including resumed work."""
        if result.get('data') is None:
            return None
        revision = result.get('data_revision')
        saved_revision = result.get('saved_revision')
        saves, executed = result.get('stage_saves'), result.get('executed_steps')
        if (not isinstance(result['data'], bytes) or result.get('data_status') != 'verified_stage_save'
                or type(revision) is not int or revision < 0 or type(saved_revision) is not int
                or saved_revision < revision
                or not isinstance(saves, list) or not saves or not isinstance(executed, list)):
            raise ValueError('Live project data has no current verified stage save')
        saved = saves[-1]
        if (not isinstance(saved, dict) or saved.get('revision') != revision
                or type(saved.get('sequence')) is not int or saved['sequence'] < 1
                or not isinstance(saved.get('validation'), dict)):
            raise ValueError('Live project data has an invalid stage save binding')
        matches = [step for step in executed if isinstance(step, dict)
                   and step.get('index') == saved['sequence']]
        if len(matches) != 1:
            raise ValueError('Live stage save is absent or ambiguous in this run')
        step, verification = matches[0], matches[0].get('verification')
        if (step.get('operation') != 'save' or step.get('status') != 'completed'
                or step.get('revision') != revision or not isinstance(verification, dict)
                or verification.get('status') != 'passed' or verification.get('revision') != revision
                or verification.get('validation') != saved['validation']):
            raise ValueError('Live stage save does not match this run\'s completed native step')
        return copy.deepcopy(saved)

    def rebase_live_session(self, project_id, session_id, old_base, new_base):
        """A completed Workbench version transaction updates metadata only."""
        project_id, session_id = _record_id(project_id, 'project'), _record_id(session_id, 'session')
        old_base, new_base = _record_id(old_base, 'version'), _record_id(new_base, 'version')
        with self._live_guard:
            row = self._live_sessions.get(session_id)
            if row is None or row['project_id'] != project_id:
                raise KeyError('Live session not found in this project')
            if row['closed'] or row['busy'] or row['base_version_id'] != old_base or not row['pending_save']:
                raise ValueError('Live session save baseline changed or no verified save is pending')
            if self.store.get_project(project_id).get('active_version_id') != new_base:
                raise ValueError('Live session rebase requires the committed active version')
            row['controller'].set_base_version(new_base)
            row.update(base_version_id=new_base, pending_save=False)
            row['cached'] = _native_diagnostic_value(row['controller'].status())
            row['cached']['base_version_id'] = new_base
            return self.live_session_status(project_id, session_id)

    def _live_native_engineering(self, payload, version, root, project_id, cpu, progress):
        from application.live_engineering import normalize_live_plan
        from application.workspace import artifact_relative_path, contained
        from application.native_engineering import validate_native_baseline
        if not isinstance(cpu, str) or not cpu.strip():
            raise ValueError('Native engineering requires an explicit saved project CPU model')
        plan = normalize_live_plan(payload['plan'], plc_model=cpu)
        action = plan['session_action']
        if action == 'start':
            with self._live_guard:
                if any(row['project_id'] == project_id and not row['closed'] for row in self._live_sessions.values()):
                    raise ValueError('This project already has an active live session')
            artifacts = {name: contained(root / artifact_relative_path(filename), root).read_bytes()
                         for name, filename in version['artifacts'].items()}
            if version['target_mode'] == 'st':
                from application.native_st import validate_candidate
                validate_candidate(artifacts, plc_model=cpu, program_name=version.get('program_name'), metadata=version)
            else:
                from application.fbd import validate_candidate
                validate_candidate(artifacts)
            projection = validate_native_baseline(artifacts['gxw'], plc_model=cpu, live=True)
            session_id = uuid.uuid4().hex
            directory = contained(self.store.project_dir(project_id) / 'gxw_runs' / session_id,
                                  self.store.project_dir(project_id))
            directory.mkdir(parents=True, exist_ok=False)
            path = directory / _native_engineering_filename(projection)
            path.write_bytes(artifacts['gxw'])
            if self._live_com is None:
                self._worker_com.CoInitialize()
                self._live_com = self._worker_com
            driver = None
            try:
                bundle = self._dependencies_factory('native_engineering')
                driver = bundle['driver_factory'](path, cpu, timeout=30, progress=None,
                    evidence_directory=directory / 'evidence')
                controller = bundle['live_session_factory'](driver, expected_cpu=cpu, baseline=artifacts['gxw'],
                    session_id=session_id, project_id=project_id, base_version_id=version['id'], progress=None)
                row = dict(project_id=project_id, base_version_id=version['id'], controller=controller,
                    directory=directory, cached=_native_diagnostic_value(controller.status()),
                    busy=False, closed=False, pending_save=False)
                with self._live_guard:
                    self._live_sessions[session_id] = row
            except Exception:
                try:
                    if driver is not None:
                        driver.close()
                finally:
                    self._release_live_com()
                raise
        else:
            session_id = _record_id(plan.get('session_id'), 'session')
            with self._live_guard:
                row = self._live_sessions.get(session_id)
                if row is None or row['project_id'] != project_id:
                    raise KeyError('Live session not found in this project')
                if row['closed'] or row['base_version_id'] != version['id']:
                    raise ValueError('Live session closed or saved project baseline changed')
                if row['pending_save'] and action != 'end':
                    raise ValueError('The verified live save has not completed its version transaction')
        with self._live_guard:
            row['busy'] = True
        try:
            controller = row['controller']
            def observed(step):
                # The native controller survives this job. Its observer must
                # belong to the current approved call, never a finished job.
                with self._live_guard:
                    row['cached'] = _native_diagnostic_value(controller.status())
                if progress is not None and isinstance(step, dict) and step.get('status') in (
                        'executing', 'completed', 'failed', 'interrupted', 'unverified'):
                    progress({'executing': '正在执行原生工程步骤。', 'completed': '原生工程步骤已完成。',
                        'failed': '原生工程步骤未完成。', 'interrupted': '原生工程步骤已中止。',
                        'unverified': '尚未确认原生工程步骤。'}[step['status']])
            controller.progress = observed
            if action == 'end':
                result = controller.close()
                result = result if isinstance(result, dict) else controller.status()
                with self._live_guard:
                    row['closed'] = True
            elif action == 'rebind':
                result = controller.rebind()
                result = result if isinstance(result, dict) else controller.status()
            else:
                result = controller.run(plan['commands'], controls=plan.get('controls'))
            if not isinstance(result, dict) or result.get('session_id') != session_id or result.get('mode') != 'live':
                raise ValueError('Live session returned an unbound receipt')
            stage_save = self._live_stage_save_receipt(result)
            with self._live_guard:
                row['cached'] = _native_diagnostic_value(result)
                row['pending_save'] = row['pending_save'] or stage_save
            self._persist_live_receipt(row)
            return {'status': result.get('status', 'unverified'), 'passed': result.get('passed') is True,
                    '_live_result': result}
        except Exception as error:
            try:
                current = _native_diagnostic_value(row['controller'].status())
            except Exception:
                current = {}
            with self._live_guard:
                row['cached'].update(current)
                row['cached'].update(status='requires_reconciliation', passed=False,
                    error={'type': type(error).__name__, 'message': str(error)})
            try:
                self._persist_live_receipt(row)
            except Exception as receipt_error:
                with self._live_guard:
                    row['cached']['receipt_error'] = {'type': type(receipt_error).__name__, 'message': str(receipt_error)}
            raise
        finally:
            with self._live_guard:
                row['busy'] = False
                self._release_live_com()

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

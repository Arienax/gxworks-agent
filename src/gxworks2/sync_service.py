# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Three-way synchronization between one application version and GX Works2."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Mapping, Optional

from .baseline_store import ImportBaselineStore
from .csv_importer import diff_gxworks2_programs
from .diagnostics import (
    GXAutomationError,
    classify_automation_error,
    describe_exception,
    exception_details,
)
from .models import GXSyncErrorCode, SyncResult, SyncStatus
from .import_service import _unsaved_project_identity


AUTO_RETRY_CODES = {
    GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED,
    GXSyncErrorCode.GX_EXPORT_MENU_FAILED,
    GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
    GXSyncErrorCode.GX_UIA_TIMEOUT,
    GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
    GXSyncErrorCode.GX_PROGRAM_EXPORT_INVALID,
    GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
    GXSyncErrorCode.GX_COMMENT_EXPORT_INVALID,
}


ERROR_SUGGESTIONS = {
    GXSyncErrorCode.GX_LOCAL_CSV_INVALID: "请重新生成或修复当前项目版本的CSV文件后重试。",
    GXSyncErrorCode.GX_WORKS2_NOT_RUNNING: "请启动GX Works2，并打开目标工程和MAIN程序后重试。",
    GXSyncErrorCode.GX_PROJECT_NOT_OPEN: "请在GX Works2中打开目标工程后重试。",
    GXSyncErrorCode.GX_PROGRAM_NOT_READY: "请在GX Works2中打开MAIN程序并保持窗口可见后重试。",
    GXSyncErrorCode.GX_AUTOMATION_UNAVAILABLE: "请确认已安装UI Automation依赖，并在Windows环境中运行。",
    GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED: "请将GX Works2置于前台后重试。",
    GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED: "请将GX Works2的MAIN窗口置于前台并保持可见后重试。",
    GXSyncErrorCode.GX_EXPORT_MENU_FAILED: "请确认MAIN或软元件注释窗口已激活，然后重试。",
    GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT: "请保持GX Works2窗口可见，关闭无关对话框后重试。",
    GXSyncErrorCode.GX_UIA_TIMEOUT: "请将GX Works2置于前台并保持界面空闲后重试。",
    GXSyncErrorCode.GX_UIA_ACCESS_DENIED: "请让本程序与GX Works2使用相同权限级别后重新启动。",
    GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED: "请确认MAIN可读取，并尝试在GX Works2中手动导出一次CSV。",
    GXSyncErrorCode.GX_PROGRAM_EXPORT_INVALID: "请检查MAIN内容，并确认GX Works2能导出有效的程序CSV。",
    GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED: "请确认全局软元件注释可打开，然后重试。",
    GXSyncErrorCode.GX_COMMENT_EXPORT_INVALID: "请检查软元件注释，并确认GX Works2能导出有效的注释CSV。",
    GXSyncErrorCode.GX_EXPORT_MANIFEST_FAILED: "请检查同步备份目录的磁盘空间和写入权限。",
    GXSyncErrorCode.GX_BASELINE_READ_FAILED: "请检查同步状态目录是否可读取，原项目版本不会被修改。",
    GXSyncErrorCode.GX_BASELINE_WRITE_FAILED: "请检查同步状态目录的写入权限后重试。",
    GXSyncErrorCode.GX_UNEXPECTED_ERROR: "请查看技术详情；若问题持续出现，请保留详情用于排查。",
}


class GXWorks2SyncService:
    def __init__(
        self,
        finder,
        automation,
        csv_manager,
        backup_root,
        baseline_store=None,
        export_validation_timeout=2.5,
        export_validation_poll_interval=0.05,
        max_export_attempts=2,
        export_retry_delay=0.3,
    ):
        self.finder = finder
        self.automation = automation
        self.csv_manager = csv_manager
        self.backup_root = Path(backup_root)
        self.baseline_store = baseline_store or ImportBaselineStore(self.backup_root)
        self.export_validation_timeout = max(0.0, float(export_validation_timeout))
        self.export_validation_poll_interval = max(
            0.01, float(export_validation_poll_interval)
        )
        self.max_export_attempts = min(2, max(1, int(max_export_attempts)))
        self.export_retry_delay = max(0.0, float(export_retry_delay))

    def _wait_for_valid_export(self, path, validator):
        deadline = time.monotonic() + self.export_validation_timeout
        result = validator(path)
        while not result.valid and time.monotonic() < deadline:
            time.sleep(self.export_validation_poll_interval)
            result = validator(path)
        return result

    @staticmethod
    def _report(progress, stage, message):
        if progress is not None:
            progress(stage, message)

    @staticmethod
    def _category(stage, code):
        if code in {
            GXSyncErrorCode.GX_AUTOMATION_UNAVAILABLE,
            GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
            GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED,
            GXSyncErrorCode.GX_EXPORT_MENU_FAILED,
            GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
            GXSyncErrorCode.GX_UIA_TIMEOUT,
            GXSyncErrorCode.GX_UIA_ACCESS_DENIED,
        }:
            return "ui_automation"
        if "comment" in stage:
            return "validate_comments" if "validate" in stage else "export_comments"
        if "program" in stage or stage in {"export_program", "write_manifest"}:
            return "validate_program" if "validate" in stage else "export_program"
        if "baseline" in stage or stage == "compare":
            return "baseline"
        return "precheck"

    @staticmethod
    def _state_value(state, key, fallback=None):
        if isinstance(state, Mapping) and key in state:
            return state.get(key)
        return fallback

    @classmethod
    def _error(
        cls,
        message,
        code,
        *,
        stage,
        retryable=False,
        project_name="",
        session=None,
        state=None,
        error=None,
        details=None,
        attempt=1,
        max_attempts=1,
        attempts=None,
    ):
        payload = dict(details or {})
        from agent_runtime.tool_errors import ToolPreconditionError
        cause = error
        for _ in range(3):
            if isinstance(cause, ToolPreconditionError):
                payload["precondition"] = cause.payload()
                break
            cause = getattr(cause, "original_error", None)
        gx_running = (
            True
            if session is not None
            else payload.get(
                "gx_running",
                (
                    False
                    if stage in {"check_gxworks2", "retry_check_gxworks2"}
                    else None
                ),
            )
        )
        session_project_open = (
            bool(getattr(session, "project_open", False))
            if session is not None
            and bool(getattr(session, "project_state_known", False))
            else None
        )
        project_open = cls._state_value(
            state,
            "project_open",
            payload.get("project_open", session_project_open),
        )
        program_ready = cls._state_value(
            state,
            "program_ready",
            payload.get("program_ready"),
        )
        program_name_value = cls._state_value(
            state,
            "program_name",
            payload.get("program_name", "MAIN" if program_ready else ""),
        )
        payload.update(
            {
                "category": cls._category(stage, code),
                "stage": stage,
                "error_code": code.value,
                "retryable": bool(retryable),
                "suggestion": ERROR_SUGGESTIONS.get(code, "请查看技术详情后重试。"),
                "gx_running": gx_running,
                "gx_process_id": (
                    int(getattr(session, "process_id", 0) or 0) or None
                    if session is not None
                    else None
                ),
                "gx_window_handle": (
                    int(getattr(session, "window_handle", 0) or 0) or None
                    if session is not None
                    else None
                ),
                "project_open": project_open,
                "program_ready": program_ready,
                "program_name": program_name_value,
                "attempt": int(attempt),
                "max_attempts": int(max_attempts),
                "attempts": list(attempts or []),
            }
        )
        captured_exception = exception_details(error)
        if error is None:
            for key, value in captured_exception.items():
                payload.setdefault(key, value)
        else:
            payload.update(captured_exception)
        return SyncResult(
            False,
            SyncStatus.ERROR,
            str(message or "GX Works2同步未完成。"),
            error_code=code,
            project_name=project_name,
            details=payload,
            stage=stage,
            retryable=bool(retryable),
        )

    @staticmethod
    def _coerce_error_code(value, default):
        if isinstance(value, GXSyncErrorCode):
            return value
        text = str(value or "")
        try:
            return GXSyncErrorCode(text)
        except (TypeError, ValueError):
            try:
                return GXSyncErrorCode[text]
            except (KeyError, TypeError):
                return default

    @staticmethod
    def _classified_error(error, *, default_code, stage, retryable=True, message=""):
        return classify_automation_error(
            error,
            default_code=default_code,
            stage=stage,
            retryable=retryable,
            message=message or describe_exception(error),
        )

    @staticmethod
    def _attempt_details(attempt, error):
        payload = {
            "attempt": int(attempt),
            "stage": error.stage,
            "error_code": error.code.value,
            "retryable": bool(error.retryable),
        }
        payload.update(exception_details(error))
        payload.update(dict(getattr(error, "details", {}) or {}))
        return payload

    def _save_snapshot(
        self,
        identity,
        *,
        app_program_path,
        app_comment_path,
        gx_program_path,
        gx_comment_path,
        import_context=None,
    ):
        app_program_hash = self.csv_manager.program_semantic_sha256(app_program_path)
        gx_program_hash = self.csv_manager.program_semantic_sha256(gx_program_path)
        app_comment_hash = self.csv_manager.comments_semantic_sha256(app_comment_path)
        gx_comment_hash = self.csv_manager.comments_semantic_sha256(gx_comment_path)
        return self.baseline_store.save(
            identity,
            program_semantic_sha256=gx_program_hash,
            program_file_sha256=self.csv_manager.file_sha256(app_program_path),
            app_program_semantic_sha256=app_program_hash,
            gx_program_semantic_sha256=gx_program_hash,
            comments_semantic_sha256=gx_comment_hash,
            app_comment_semantic_sha256=app_comment_hash,
            gx_comment_semantic_sha256=gx_comment_hash,
            import_context=import_context,
        )

    def record_snapshot(
        self,
        identity: Mapping[str, Any],
        *,
        app_program_path,
        app_comment_path,
        gx_program_path,
        gx_comment_path,
        import_context=None,
    ):
        return self._save_snapshot(
            dict(identity),
            app_program_path=app_program_path,
            app_comment_path=app_comment_path,
            gx_program_path=gx_program_path,
            gx_comment_path=gx_comment_path,
            import_context=import_context,
        )

    def _inspect_project_state(self, session, *, program_name="MAIN"):
        try:
            state = dict(self.automation.inspect_project(session) or {})
        except Exception as error:
            classified = self._classified_error(
                error,
                default_code=GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                stage="inspect_project",
                retryable=True,
                message=(
                    "无法检查GX Works2当前工程：" + describe_exception(error)
                ),
            )
            project_name = str(
                getattr(session, "project_name", "") or "GXWorks2"
            )
            return {}, project_name, {
                "message": str(classified),
                "code": classified.code,
                "stage": classified.stage,
                "retryable": classified.retryable,
                "error": classified,
                "details": dict(classified.details),
            }

        project_name = str(
            state.get("project_name") or session.project_name or "GXWorks2"
        )
        if not state.get("automation_available", True):
            code = self._coerce_error_code(
                state.get("error_code"),
                GXSyncErrorCode.GX_AUTOMATION_UNAVAILABLE,
            )
            return state, project_name, {
                "message": state.get("message", "GX Works2自动化驱动不可用。"),
                "code": code,
                "stage": state.get("stage") or "inspect_project",
                "retryable": bool(state.get("retryable", False)),
                "details": {
                    key: state.get(key, "")
                    for key in (
                        "exception_type",
                        "exception_repr",
                        "exception_message",
                    )
                },
            }
        if not state.get("project_open"):
            return state, project_name, {
                "message": "GX Works2中没有已打开的目标工程。",
                "code": GXSyncErrorCode.GX_PROJECT_NOT_OPEN,
                "stage": "check_project",
                "retryable": True,
            }
        if not state.get("program_ready"):
            program_name = str(state.get("program_name") or program_name)
            return state, project_name, {
                "message": f"GX Works2当前{program_name}程序不可读取，请先打开{program_name}。",
                "code": GXSyncErrorCode.GX_PROGRAM_NOT_READY,
                "stage": "check_program",
                "retryable": True,
            }
        return state, project_name, None

    def _export_snapshot_once(
        self,
        session,
        *,
        folder,
        gx_program_path,
        gx_comment_path,
        progress,
        identity_observer=None,
    ):
        if identity_observer:
            identity_observer(session)
        self._report(progress, "export_program", "正在从GX Works2读取当前MAIN")
        try:
            self.automation.export_current_program(session, gx_program_path)
        except GXAutomationError:
            raise
        except Exception as error:
            raise self._classified_error(
                error,
                default_code=GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                stage="export_program",
                retryable=True,
                message=(
                    "无法导出GX Works2当前MAIN：" + describe_exception(error)
                ),
            ) from error

        self._report(progress, "validate_program_csv", "正在校验GX Works2程序CSV")
        try:
            exported_program = self._wait_for_valid_export(
                gx_program_path, self.csv_manager.validate
            )
        except Exception as error:
            raise GXAutomationError(
                GXSyncErrorCode.GX_PROGRAM_EXPORT_INVALID,
                "validate_program_csv",
                "无法校验GX Works2程序CSV：" + describe_exception(error),
                retryable=True,
                original_error=error,
                details={"export_path": str(gx_program_path)},
            ) from error
        if not exported_program.valid:
            validation_errors = list(exported_program.errors)
            detail = "；".join(validation_errors) or "未知格式错误"
            raise GXAutomationError(
                GXSyncErrorCode.GX_PROGRAM_EXPORT_INVALID,
                "validate_program_csv",
                "GX Works2程序CSV未通过格式校验：" + detail,
                retryable=True,
                details={
                    "export_path": str(gx_program_path),
                    "validation_errors": validation_errors,
                },
            )

        self._report(progress, "export_comments", "正在从GX Works2读取软元件注释")
        if identity_observer:
            identity_observer(session)
        try:
            self.automation.export_current_comments(session, gx_comment_path)
        except GXAutomationError:
            raise
        except Exception as error:
            raise self._classified_error(
                error,
                default_code=GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
                stage="export_comments",
                retryable=True,
                message=(
                    "无法导出GX Works2软元件注释：" + describe_exception(error)
                ),
            ) from error

        self._report(progress, "validate_comment_csv", "正在校验GX Works2注释CSV")
        if identity_observer:
            identity_observer(session)
        try:
            exported_comments = self._wait_for_valid_export(
                gx_comment_path,
                lambda path: self.csv_manager.validate_comments(
                    path, require_crlf=False
                ),
            )
        except Exception as error:
            raise GXAutomationError(
                GXSyncErrorCode.GX_COMMENT_EXPORT_INVALID,
                "validate_comment_csv",
                "无法校验GX Works2注释CSV：" + describe_exception(error),
                retryable=True,
                original_error=error,
                details={"export_path": str(gx_comment_path)},
            ) from error
        if not exported_comments.valid:
            validation_errors = list(exported_comments.errors)
            detail = "；".join(validation_errors) or "未知格式错误"
            raise GXAutomationError(
                GXSyncErrorCode.GX_COMMENT_EXPORT_INVALID,
                "validate_comment_csv",
                "GX Works2注释CSV未通过格式校验：" + detail,
                retryable=True,
                details={
                    "export_path": str(gx_comment_path),
                    "validation_errors": validation_errors,
                },
            )

        self._report(progress, "write_manifest", "正在保存GX Works2导出校验清单")
        try:
            self.csv_manager.write_checksum_manifest(folder)
        except Exception as error:
            raise GXAutomationError(
                GXSyncErrorCode.GX_EXPORT_MANIFEST_FAILED,
                "write_manifest",
                "无法保存GX Works2导出校验清单：" + describe_exception(error),
                retryable=False,
                original_error=error,
                details={"export_folder": str(folder)},
            ) from error

    @staticmethod
    def _clear_retry_exports(gx_program_path, gx_comment_path):
        for path in (gx_program_path, gx_comment_path):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                continue

    def _save_snapshot_error(
        self,
        identity,
        *,
        app_program_path,
        app_comment_path,
        gx_program_path,
        gx_comment_path,
        import_context,
        project_name,
        session,
        state,
        details,
        persist=True,
    ):
        if not persist:
            return None
        try:
            self._save_snapshot(
                identity,
                app_program_path=app_program_path,
                app_comment_path=app_comment_path,
                gx_program_path=gx_program_path,
                gx_comment_path=gx_comment_path,
                import_context=import_context,
            )
        except Exception as error:
            return self._error(
                "GX Works2内容已读取，但无法保存同步基线："
                + describe_exception(error),
                GXSyncErrorCode.GX_BASELINE_WRITE_FAILED,
                stage="save_baseline",
                retryable=False,
                project_name=project_name,
                session=session,
                state=state,
                error=error,
                details=details,
                attempt=details.get("export_attempt", 1),
                max_attempts=details.get("max_export_attempts", 1),
                attempts=details.get("export_attempts", []),
            )
        return None

    def _read_current_snapshot_core(
        self,
        *,
        progress=None,
        import_context=None,
        project_identity: Optional[str] = None,
        save_project=True,
        target_session=None,
        identity_observer=None,
    ):
        """Read one coherent MAIN/comments snapshot without requiring a local version."""
        expected_program_name = str(
            (import_context or {}).get("program_name") or "MAIN"
            if isinstance(import_context, Mapping)
            else "MAIN"
        )
        precheck_state = {
            "project_open": None,
            "program_ready": None,
            "program_name": expected_program_name,
        }
        self._report(progress, "check_gxworks2", "正在检查GX Works2当前工程")
        try:
            session = self._bound_session(target_session)
            if identity_observer and session:
                identity_observer(session)
        except Exception as error:
            classified = self._classified_error(
                error,
                default_code=GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                stage="check_gxworks2",
                retryable=True,
                message="无法检查GX Works2进程：" + describe_exception(error),
            )
            return self._error(
                str(classified),
                classified.code,
                stage=classified.stage,
                retryable=classified.retryable,
                state=precheck_state,
                error=classified,
            )
        if session is None:
            return self._error(
                "GX Works2未运行，请先打开目标工程和MAIN程序。",
                GXSyncErrorCode.GX_WORKS2_NOT_RUNNING,
                stage="check_gxworks2",
                retryable=True,
                state=precheck_state,
            )
        if session.project_state_known and not session.project_open:
            return self._error(
                "GX Works2尚未新建或打开工程。",
                GXSyncErrorCode.GX_PROJECT_NOT_OPEN,
                stage="check_project",
                retryable=True,
                project_name=session.project_name,
                session=session,
                state={
                    "project_open": False,
                    "program_ready": False,
                    "program_name": expected_program_name,
                },
            )
        state, project_name, state_failure = self._inspect_project_state(session, program_name=expected_program_name)
        state.setdefault("program_name", expected_program_name)
        if state_failure is not None:
            return self._error(
                state_failure["message"],
                state_failure["code"],
                stage=state_failure["stage"],
                retryable=state_failure["retryable"],
                project_name=project_name,
                session=session,
                state=state,
                error=state_failure.get("error"),
                details=state_failure.get("details"),
            )
        try:
            effective_project_identity = project_identity or _unsaved_project_identity(
                session,
                project_name,
                import_context=import_context,
            )
            identity = self.baseline_store.project_identity(
                session,
                project_name=project_name,
                project_identity=effective_project_identity,
            )
        except Exception as error:
            return self._error(
                "无法确定GX Works2同步基线：" + describe_exception(error),
                GXSyncErrorCode.GX_BASELINE_READ_FAILED,
                stage="resolve_baseline",
                retryable=False,
                project_name=project_name,
                session=session,
                state=state,
                error=error,
            )

        try:
            folder = self.csv_manager.backup_folder(self.backup_root, project_name)
        except Exception as error:
            return self._error(
                "无法创建GX Works2同步导出目录：" + describe_exception(error),
                GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                stage="prepare_program_export",
                retryable=False,
                project_name=project_name,
                session=session,
                state=state,
                error=error,
                details={"project_identity": identity},
            )

        gx_program_path = folder / "program_from_gxworks2.csv"
        gx_comment_path = folder / "comments_from_gxworks2.csv"
        attempts = []
        recovery_details = []
        completed_attempt = 0
        progress_setter = getattr(self.automation, "set_progress_reporter", None)
        progress_reporter_set = False
        if callable(progress_setter):
            try:
                progress_setter(progress)
                progress_reporter_set = True
            except Exception:
                progress_setter = None
        try:
            for attempt in range(1, self.max_export_attempts + 1):
                completed_attempt = attempt
                if attempt > 1:
                    self._report(
                        progress,
                        "retry_export",
                        f"正在恢复GX Works2界面并进行第{attempt}次读取",
                    )
                    try:
                        session = self._bound_session(target_session)
                        if identity_observer and session:
                            identity_observer(session)
                    except Exception as error:
                        classified = self._classified_error(
                            error,
                            default_code=GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                            stage="retry_inspect_project",
                            retryable=True,
                            message=(
                                "重试前无法检查GX Works2进程："
                                + describe_exception(error)
                            ),
                        )
                        return self._error(
                            str(classified),
                            classified.code,
                            stage=classified.stage,
                            retryable=classified.retryable,
                            project_name=project_name,
                            state=state,
                            error=classified,
                            details={"project_identity": identity},
                            attempt=attempt,
                            max_attempts=self.max_export_attempts,
                            attempts=attempts,
                        )
                    if session is None:
                        return self._error(
                            "重试前检测到GX Works2已退出。",
                            GXSyncErrorCode.GX_WORKS2_NOT_RUNNING,
                            stage="retry_check_gxworks2",
                            retryable=True,
                            project_name=project_name,
                            state=state,
                            details={"project_identity": identity},
                            attempt=attempt,
                            max_attempts=self.max_export_attempts,
                            attempts=attempts,
                        )
                    state, refreshed_project_name, state_failure = (
                        self._inspect_project_state(session, program_name=expected_program_name)
                    )
                    state.setdefault("program_name", expected_program_name)
                    if state_failure is not None:
                        return self._error(
                            state_failure["message"],
                            state_failure["code"],
                            stage=state_failure["stage"],
                            retryable=state_failure["retryable"],
                            project_name=refreshed_project_name,
                            session=session,
                            state=state,
                            error=state_failure.get("error"),
                            details={
                                "project_identity": identity,
                                **dict(state_failure.get("details") or {}),
                            },
                            attempt=attempt,
                            max_attempts=self.max_export_attempts,
                            attempts=attempts,
                        )
                    if (
                        project_name
                        and refreshed_project_name
                        and project_name.casefold() != refreshed_project_name.casefold()
                    ):
                        return self._error(
                            "重试前检测到GX Works2工程已切换，已停止读取。",
                            GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                            stage="retry_inspect_project",
                            retryable=True,
                            project_name=refreshed_project_name,
                            session=session,
                            state=state,
                            details={
                                "project_identity": identity,
                                "original_project_name": project_name,
                            },
                            attempt=attempt,
                            max_attempts=self.max_export_attempts,
                            attempts=attempts,
                        )
                    recovery = getattr(self.automation, "prepare_export_retry", None)
                    if callable(recovery):
                        try:
                            recovery_details.append(dict(recovery(session) or {}))
                        except Exception as error:
                            classified = self._classified_error(
                                error,
                                default_code=GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED,
                                stage="activate_main",
                                retryable=True,
                                message=(
                                    "重试前无法恢复GX Works2界面："
                                    + describe_exception(error)
                                ),
                            )
                            attempts.append(self._attempt_details(attempt, classified))
                            return self._error(
                                str(classified),
                                classified.code,
                                stage=classified.stage,
                                retryable=classified.retryable,
                                project_name=project_name,
                                session=session,
                                state=state,
                                error=classified,
                                details={
                                    "project_identity": identity,
                                    "recovery": recovery_details,
                                },
                                attempt=attempt,
                                max_attempts=self.max_export_attempts,
                                attempts=attempts,
                            )
                    time.sleep(self.export_retry_delay)
                    try:
                        self._clear_retry_exports(
                            gx_program_path,
                            gx_comment_path,
                        )
                    except Exception as error:
                        return self._error(
                            "无法清理本轮失败的临时CSV："
                            + describe_exception(error),
                            GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                            stage="prepare_program_export",
                            retryable=False,
                            project_name=project_name,
                            session=session,
                            state=state,
                            error=error,
                            details={
                                "project_identity": identity,
                                "recovery": recovery_details,
                            },
                            attempt=attempt,
                            max_attempts=self.max_export_attempts,
                            attempts=attempts,
                        )
                try:
                    self._export_snapshot_once(
                        session,
                        folder=folder,
                        gx_program_path=gx_program_path,
                        gx_comment_path=gx_comment_path,
                        progress=progress,
                        **({"identity_observer": identity_observer} if identity_observer else {}),
                    )
                    break
                except GXAutomationError as error:
                    attempts.append(self._attempt_details(attempt, error))
                    if (
                        attempt < self.max_export_attempts
                        and error.code in AUTO_RETRY_CODES
                        and error.retryable
                    ):
                        continue
                    return self._error(
                        str(error),
                        error.code,
                        stage=error.stage,
                        retryable=error.retryable,
                        project_name=project_name,
                        session=session,
                        state=state,
                        error=error,
                        details={
                            "project_identity": identity,
                            "export_folder": str(folder),
                            "export_program_path": str(gx_program_path),
                            "export_comment_path": str(gx_comment_path),
                            "recovery": recovery_details,
                            **dict(error.details),
                        },
                        attempt=attempt,
                        max_attempts=self.max_export_attempts,
                        attempts=attempts,
                    )
        finally:
            if progress_reporter_set and callable(progress_setter):
                try:
                    progress_setter(None)
                except Exception:
                    pass

        if identity_observer:
            try:
                identity_observer(session)
            except Exception as error:
                return self._error(str(error), GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                    stage="verify_snapshot_identity", retryable=False, session=session, error=error)
        save_method = getattr(self.automation, "save_project", None)
        if not save_project:
            gx_save = {
                "success": False,
                "attempted": False,
                "save_required": None,
                "message": "本次只读快照未保存GX Works2工程。",
            }
        elif callable(save_method):
            try:
                gx_save = dict(save_method(session) or {})
            except Exception as error:
                gx_save = {
                    "success": False,
                    "save_required": True,
                    "message": (
                        "无法自动保存GX Works2工程："
                        + describe_exception(error)
                    ),
                }
        else:
            gx_save = {
                "success": False,
                "save_required": True,
                "message": "请在GX Works2中保存当前工程。",
            }

        return {
            "session": session,
            "state": state,
            "project_name": project_name,
            "identity": identity,
            "folder": folder,
            "gx_program_path": gx_program_path,
            "gx_comment_path": gx_comment_path,
            "attempts": attempts,
            "recovery_details": recovery_details,
            "completed_attempt": completed_attempt,
            "gx_save": gx_save,
        }

    def read_current_snapshot(
        self,
        *,
        progress=None,
        import_context=None,
        project_identity: Optional[str] = None,
        save_project=True,
        target_session=None,
        identity_observer=None,
    ) -> SyncResult:
        """Export GX MAIN/comments for bootstrap import into an empty AI project."""
        snapshot = self._read_current_snapshot_core(
            progress=progress,
            import_context=import_context,
            project_identity=project_identity,
            save_project=save_project,
            target_session=target_session,
            identity_observer=identity_observer,
        )
        if isinstance(snapshot, SyncResult):
            return snapshot
        state = snapshot["state"]
        details = {
            "project_identity": snapshot["identity"],
            "export_folder": str(snapshot["folder"]),
            "gx_save": snapshot["gx_save"],
            "export_attempt": snapshot["completed_attempt"],
            "max_export_attempts": self.max_export_attempts,
            "export_attempts": snapshot["attempts"],
            "recovery": snapshot["recovery_details"],
            "program_name": str(state.get("program_name") or "MAIN"),
            "bootstrap": True,
        }
        return SyncResult(
            True,
            SyncStatus.SYNCED,
            "已读取GX Works2当前MAIN和软元件注释。",
            project_name=snapshot["project_name"],
            exported_program_path=str(snapshot["gx_program_path"]),
            exported_comment_path=str(snapshot["gx_comment_path"]),
            details=details,
            stage="read_snapshot",
            retryable=False,
        )

    def _bound_session(self, target):
        if target is None:
            return self.finder.find_running()
        current = self.finder.find_running(process_id=target.process_id)
        if current is None or any(getattr(current, name) != getattr(target, name)
                                  for name in ("process_id", "window_handle", "process_started", "executable")):
            from agent_runtime.tool_errors import ToolPreconditionError
            raise ToolPreconditionError("STALE_NATIVE_SESSION", "所选 GX Works2 会话已退出或被替换。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        return current

    def inspect(
        self,
        app_program_path,
        app_comment_path,
        *,
        progress=None,
        import_context=None,
        project_identity: Optional[str] = None,
        save_project=True,
        persist_baseline=True,
    ) -> SyncResult:
        app_program_path = Path(app_program_path).expanduser().resolve()
        app_comment_path = Path(app_comment_path).expanduser().resolve()
        expected_program_name = str(
            (import_context or {}).get("program_name") or "MAIN"
            if isinstance(import_context, Mapping)
            else "MAIN"
        )
        precheck_state = {
            "project_open": None,
            "program_ready": None,
            "program_name": expected_program_name,
        }
        self._report(progress, "validate_local", "正在校验当前项目版本")
        try:
            program_validation = self.csv_manager.validate(app_program_path)
            comment_validation = self.csv_manager.validate_comments(app_comment_path)
        except Exception as error:
            return self._error(
                "当前项目版本无法校验：" + describe_exception(error),
                GXSyncErrorCode.GX_LOCAL_CSV_INVALID,
                stage="validate_local",
                retryable=False,
                state=precheck_state,
                error=error,
                details={
                    "program_path": str(app_program_path),
                    "comment_path": str(app_comment_path),
                },
            )
        errors = list(program_validation.errors) + list(comment_validation.errors)
        if errors:
            return self._error(
                "当前项目版本无法同步：" + "；".join(errors),
                GXSyncErrorCode.GX_LOCAL_CSV_INVALID,
                stage="validate_local",
                retryable=False,
                state=precheck_state,
                details={
                    "program_path": str(app_program_path),
                    "comment_path": str(app_comment_path),
                    "validation_errors": errors,
                },
            )

        snapshot = self._read_current_snapshot_core(
            progress=progress,
            import_context=import_context,
            project_identity=project_identity,
            save_project=save_project,
        )
        if isinstance(snapshot, SyncResult):
            return snapshot
        session = snapshot["session"]
        state = snapshot["state"]
        project_name = snapshot["project_name"]
        identity = snapshot["identity"]
        folder = snapshot["folder"]
        gx_program_path = snapshot["gx_program_path"]
        gx_comment_path = snapshot["gx_comment_path"]
        attempts = snapshot["attempts"]
        recovery_details = snapshot["recovery_details"]
        completed_attempt = snapshot["completed_attempt"]
        gx_save = snapshot["gx_save"]

        self._report(progress, "compare", "正在比较项目与GX Works2版本")
        try:
            app_program_hash = self.csv_manager.program_semantic_sha256(app_program_path)
            gx_program_hash = self.csv_manager.program_semantic_sha256(gx_program_path)
            app_comment_hash = self.csv_manager.comments_semantic_sha256(app_comment_path)
            gx_comment_hash = self.csv_manager.comments_semantic_sha256(gx_comment_path)
            baseline = self.baseline_store.load(identity)
            difference = diff_gxworks2_programs(app_program_path, gx_program_path)
        except Exception as error:
            return self._error(
                "无法核对同步基线：" + describe_exception(error),
                GXSyncErrorCode.GX_BASELINE_READ_FAILED,
                stage="compare",
                retryable=False,
                project_name=project_name,
                session=session,
                state=state,
                error=error,
                details={
                    "project_identity": identity,
                    "export_folder": str(folder),
                },
                attempt=completed_attempt,
                max_attempts=self.max_export_attempts,
                attempts=attempts,
            )

        hashes = {
            "app_program_semantic_sha256": app_program_hash,
            "gx_program_semantic_sha256": gx_program_hash,
            "app_comment_semantic_sha256": app_comment_hash,
            "gx_comment_semantic_sha256": gx_comment_hash,
        }
        details = {
            "project_identity": identity,
            "baseline_found": baseline is not None,
            "baseline_write_enabled": bool(persist_baseline),
            "baseline": baseline or {},
            "hashes": hashes,
            "diff": difference,
            "export_folder": str(folder),
            "gx_save": gx_save,
            "export_attempt": completed_attempt,
            "max_export_attempts": self.max_export_attempts,
            "export_attempts": attempts,
            "recovery": recovery_details,
        }

        program_equal = app_program_hash == gx_program_hash
        comments_equal = app_comment_hash == gx_comment_hash
        if baseline is None:
            if program_equal and comments_equal:
                baseline_error = self._save_snapshot_error(
                    identity,
                    app_program_path=app_program_path,
                    app_comment_path=app_comment_path,
                    gx_program_path=gx_program_path,
                    gx_comment_path=gx_comment_path,
                    import_context=import_context,
                    project_name=project_name,
                    session=session,
                    state=state,
                    details=details,
                    persist=persist_baseline,
                )
                if baseline_error is not None:
                    return baseline_error
                return SyncResult(
                    True,
                    SyncStatus.SYNCED,
                    "项目与GX Works2内容一致，已建立同步关系。" if persist_baseline else "项目与GX Works2内容一致，本次未写入同步基线。",
                    project_name=project_name,
                    exported_program_path=str(gx_program_path),
                    exported_comment_path=str(gx_comment_path),
                    details=details,
                )
            return SyncResult(
                True,
                SyncStatus.UNBOUND,
                "这是首次同步，项目与GX Works2内容不同，请选择保留哪一方。",
                project_name=project_name,
                exported_program_path=str(gx_program_path),
                exported_comment_path=str(gx_comment_path),
                details=details,
            )

        previous_project_id = str(
            (baseline.get("import_context") or {}).get("project_id") or ""
        )
        current_project_id = str(
            (import_context or {}).get("project_id") or ""
            if isinstance(import_context, Mapping)
            else ""
        )
        binding_mismatch = bool(
            previous_project_id
            and current_project_id
            and previous_project_id != current_project_id
        )
        details["binding_mismatch"] = binding_mismatch
        details["previous_project_id"] = previous_project_id
        if binding_mismatch:
            if program_equal and comments_equal:
                baseline_error = self._save_snapshot_error(
                    identity,
                    app_program_path=app_program_path,
                    app_comment_path=app_comment_path,
                    gx_program_path=gx_program_path,
                    gx_comment_path=gx_comment_path,
                    import_context=import_context,
                    project_name=project_name,
                    session=session,
                    state=state,
                    details=details,
                    persist=persist_baseline,
                )
                if baseline_error is not None:
                    return baseline_error
                return SyncResult(
                    True,
                    SyncStatus.SYNCED,
                    "当前项目与GX Works2内容一致，已更新工程绑定。" if persist_baseline else "当前项目与GX Works2内容一致，本次未更新工程绑定。",
                    project_name=project_name,
                    exported_program_path=str(gx_program_path),
                    exported_comment_path=str(gx_comment_path),
                    details=details,
                )
            return SyncResult(
                True,
                SyncStatus.UNBOUND,
                "当前GX Works2工程上次绑定到另一个项目，请选择本次以哪一方为准。",
                project_name=project_name,
                exported_program_path=str(gx_program_path),
                exported_comment_path=str(gx_comment_path),
                details=details,
            )

        base_app_program = baseline.get("app_program_semantic_sha256")
        base_gx_program = baseline.get("gx_program_semantic_sha256")
        base_app_comment = baseline.get("app_comment_semantic_sha256") or ""
        base_gx_comment = baseline.get("gx_comment_semantic_sha256") or ""
        comment_base_known = bool(base_app_comment and base_gx_comment)
        local_changed = app_program_hash != base_app_program
        gx_changed = gx_program_hash != base_gx_program
        if comment_base_known:
            local_changed = local_changed or app_comment_hash != base_app_comment
            gx_changed = gx_changed or gx_comment_hash != base_gx_comment
        elif comments_equal:
            # A v1 baseline did not track comments. Equal current mappings do
            # not affect change classification.  Persist the upgraded base
            # only after the program sides are also known to be equivalent;
            # otherwise an unresolved conflict would accidentally become the
            # next common base.
            pass
        else:
            local_changed = True
            gx_changed = True

        details["local_changed"] = local_changed
        details["gx_changed"] = gx_changed
        if program_equal and comments_equal:
            baseline_error = self._save_snapshot_error(
                identity,
                app_program_path=app_program_path,
                app_comment_path=app_comment_path,
                gx_program_path=gx_program_path,
                gx_comment_path=gx_comment_path,
                import_context=import_context,
                project_name=project_name,
                session=session,
                state=state,
                details=details,
                persist=persist_baseline,
            )
            if baseline_error is not None:
                return baseline_error
            status = SyncStatus.SYNCED
            message = "项目与GX Works2内容一致。"
        elif local_changed and not gx_changed:
            status = SyncStatus.NEEDS_PUSH
            message = "项目版本有修改，正在准备同步到GX Works2。"
        elif gx_changed and not local_changed:
            status = SyncStatus.NEEDS_PULL
            message = "GX Works2中有人工修改，正在回读为新的项目版本。"
        else:
            status = SyncStatus.CONFLICT
            message = "项目与GX Works2都已修改，必须先选择保留哪一方。"
        return SyncResult(
            True,
            status,
            message,
            project_name=project_name,
            exported_program_path=str(gx_program_path),
            exported_comment_path=str(gx_comment_path),
            details=details,
        )


__all__ = ["GXWorks2SyncService"]

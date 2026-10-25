from dataclasses import asdict, replace
from pathlib import Path
import shutil
import tempfile
import time

from .baseline_store import ImportBaselineStore
from .models import ImportErrorCode, ImportResult
from .project_path import UNSAVED_PROJECT_NAMES as _UNSAVED_PROJECT_NAMES
from .project_path import caption_label, discover_candidates, resolve_project_path
from .project_profiles import native_project_profile


def _unsaved_project_identity(session, project_name, import_context=None):
    """Return a session-bound identity for an unnamed GX Works2 project.

    GX Works2 gives every newly created, unsaved project the same visible name.
    Persisting a baseline under that shared name makes an unrelated blank
    project look like an externally modified old project.  An unsaved project
    cannot be identified across GX Works2 launches, so bind it to the current
    process/window and, when available, the PLC AI project id.
    """

    normalized = str(project_name or "").strip().casefold()
    if normalized not in _UNSAVED_PROJECT_NAMES:
        return None
    context = import_context if isinstance(import_context, dict) else {}
    plc_ai_project = str(context.get("project_id") or "").strip()
    process_id = int(getattr(session, "process_id", 0) or 0)
    window_handle = int(getattr(session, "window_handle", 0) or 0)
    parts = ["unsaved", f"process:{process_id}", f"window:{window_handle}"]
    if plc_ai_project:
        parts.append(f"plc-ai:{plc_ai_project}")
    return "|".join(parts)


class ImportService:
    def __init__(
        self,
        finder,
        automation,
        csv_manager,
        backup_root,
        baseline_store=None,
    ):
        self.finder = finder
        self.automation = automation
        self.csv_manager = csv_manager
        self.backup_root = Path(backup_root)
        self.baseline_store = baseline_store or ImportBaselineStore(self.backup_root)

    def import_current_program(
        self,
        csv_path,
        *,
        comment_csv_path=None,
        start_if_needed=False,
        progress=None,
        import_context=None,
        project_identity=None,
        rollback_expected_current_sha256=None,
        expected_current_comment_sha256=None,
        synchronize_comments=False,
        verify_roundtrip=False,
        save_project=False,
        pre_import_policy="project_file",
        create_project_path=None,
        project_path=None,
        project_search_roots=(),
    ):
        """Back up an existing GXW and import each CSV once, without exporting."""
        # Legacy policy names remain accepted, but never select CSV exports or
        # skip the file backup. Readback and guarded rollback require a separate
        # explicit read request; an import cannot silently perform either.
        if not isinstance(pre_import_policy, str) or pre_import_policy not in {
            "project_file", "protected", "manual_backup"
        } or verify_roundtrip or rollback_expected_current_sha256 or expected_current_comment_sha256:
            return ImportResult(
                False, "validate_request",
                "CSV导入只执行导入；回读或回滚校验请单独请求读取/导出。",
                ImportErrorCode.INVALID_REQUEST,
            )
        started = stage_started = time.monotonic()
        current_stage = None
        timings = {}

        def report(stage, message):
            nonlocal current_stage, stage_started
            now = time.monotonic()
            if current_stage is not None:
                timings[current_stage] = timings.get(current_stage, 0) + (now - stage_started) * 1000
            current_stage, stage_started = stage, now
            if progress is not None:
                progress(stage, message)

        with tempfile.TemporaryDirectory(prefix="gx-import-") as directory:
            result = self._import_current_program(
                csv_path, comment_csv_path=comment_csv_path, start_if_needed=start_if_needed,
                progress=report, import_context=import_context, project_identity=project_identity,
                synchronize_comments=synchronize_comments, save_project=save_project,
                staging_folder=Path(directory), create_project_path=create_project_path,
                project_path=project_path, project_search_roots=project_search_roots,
            )
        finished = time.monotonic()
        if current_stage is not None:
            timings[current_stage] = timings.get(current_stage, 0) + (finished - stage_started) * 1000
        return replace(result, details={**result.details,
            "pre_import_policy": "project_file",
            "backup_performed": bool(result.backup_path),
            "timings_ms": {**{key: round(value, 3) for key, value in timings.items()},
                           "total": round((finished - started) * 1000, 3)},
        })

    def _import_current_program(
        self, csv_path, *, comment_csv_path, start_if_needed, progress, import_context,
        project_identity, synchronize_comments, save_project, staging_folder,
        create_project_path, project_path=None, project_search_roots=(),
    ):
        def report(stage, message):
            if progress is not None:
                progress(stage, message)

        report("validate_csv", "正在校验待导入的程序CSV…")
        validation = self.csv_manager.validate(csv_path)
        if not validation.valid:
            code = (
                ImportErrorCode.CSV_NOT_FOUND
                if "不存在" in " ".join(validation.errors)
                else ImportErrorCode.CSV_INVALID
            )
            return ImportResult(
                False,
                "validate_csv",
                "CSV格式验证失败：" + "；".join(validation.errors),
                code,
                csv_path=validation.path,
                details={"validation": asdict(validation)},
            )

        comment_validation = None
        should_import_comments = False
        if comment_csv_path is not None:
            report("validate_comments", "正在校验软元件注释CSV…")
            comment_validation = self.csv_manager.validate_comments(comment_csv_path)
            if not comment_validation.valid:
                code = (
                    ImportErrorCode.CSV_NOT_FOUND
                    if "不存在" in " ".join(comment_validation.errors)
                    else ImportErrorCode.CSV_INVALID
                )
                return ImportResult(
                    False,
                    "validate_comments",
                    "软元件注释CSV格式验证失败："
                    + "；".join(comment_validation.errors),
                    code,
                    csv_path=validation.path,
                    details={
                        "validation": asdict(validation),
                        "comment_validation": asdict(comment_validation),
                    },
                )
            should_import_comments = bool(
                comment_validation.comment_count > 0 or synchronize_comments
            )

        report("check_project", "正在检查GX Works2与当前目标工程…")
        session = self.finder.find_running()
        context = import_context if isinstance(import_context, dict) else {}
        profile = native_project_profile(context.get("plc_model"))
        if create_project_path is not None and (session is None or not session.project_open):
            # The original 1.635 save dialog rejects a full path over 200
            # UTF-16 characters. Reject before starting or creating a project;
            # that modal cannot be mistaken for a completed first save.
            path_characters = len(str(Path(create_project_path).expanduser().resolve()).encode('utf-16le')) // 2
            if path_characters > 200:
                return ImportResult(False, "prepare_project", "独立 GX 工程目标路径超过 200 字符，未启动工程创建。",
                                    ImportErrorCode.AUTOMATION_FAILED, csv_path=validation.path)
            if profile is None or not profile.program_supported or not profile.accepts_plc_info(validation.plc_info):
                return ImportResult(False, "prepare_project", "型号不支持GX Works2原生程序工程，或CSV的CPU信息与目标不一致。",
                                    ImportErrorCode.TARGET_PROJECT_NOT_OPEN, csv_path=validation.path)
        if session is None and start_if_needed:
            report("start_gxworks2", "正在启动GX Works2并准备目标工程…")
            executable = self.finder.start()
            if executable and create_project_path is not None:
                deadline = time.monotonic() + 30
                while session is None and time.monotonic() < deadline:
                    session = self.finder.find_running()
                    if session is not None and (
                        (getattr(self.finder, "started_process_id", session.process_id) != session.process_id)
                        or session.project_open
                    ):
                        return ImportResult(False, "start_gxworks2", "启动后检测到其他会话或已打开工程，未执行自动导入。",
                                            ImportErrorCode.AUTOMATION_FAILED, csv_path=validation.path)
                    if session is None:
                        time.sleep(.1)
            if session is None:
                return ImportResult(
                    False,
                    "find_gxworks2",
                    ("启动GX Works2后未确认可用窗口。" if create_project_path is not None and executable else
                     f"已启动GX Works2（{executable}），请新建或打开目标工程后重试。" if executable else
                     "未找到GX Works2安装程序。"),
                    ImportErrorCode.TARGET_PROJECT_NOT_OPEN if executable else ImportErrorCode.GXWORKS2_NOT_FOUND,
                    csv_path=validation.path,
                )
        if session is None:
            return ImportResult(
                False,
                "find_gxworks2",
                "GX Works2未运行，请先启动并新建或打开目标工程。",
                ImportErrorCode.GXWORKS2_NOT_RUNNING,
                csv_path=validation.path,
            )
        project_preparation = None
        created_project = False
        if not session.project_open and create_project_path is not None:
            report("prepare_project", f"正在新建并保存独立的{profile.cpu}梯形图工程…")
            try:
                project_preparation = self.automation.create_simple_project(
                    session, create_project_path, cpu=profile.cpu, progress=report)
                prepared_session = self.finder.find_running()
                if (prepared_session is None or prepared_session.process_id != session.process_id
                        or prepared_session.window_handle != session.window_handle or not prepared_session.project_open):
                    raise RuntimeError("新建工程后的GX会话身份无法确认。")
                session = prepared_session
                created_project = True
            except Exception as error:
                return ImportResult(False, "prepare_project", "自动准备GX工程失败：" + str(error),
                                    ImportErrorCode.AUTOMATION_FAILED, csv_path=validation.path)
        if session.project_state_known and not session.project_open:
            return ImportResult(
                False,
                "check_project",
                "GX Works2已运行，但尚未新建或打开工程；编辑菜单此时没有“从CSV文件读取”。",
                ImportErrorCode.TARGET_PROJECT_NOT_OPEN,
                csv_path=validation.path,
            )

        state = self.automation.inspect_project(session)
        if not state.get("automation_available", True):
            return ImportResult(
                False,
                "check_project",
                state.get("message", "GX Works2自动化驱动不可用。"),
                ImportErrorCode.AUTOMATION_UNAVAILABLE,
                csv_path=validation.path,
                project_name=session.project_name,
            )
        if not state.get("project_open"):
            return ImportResult(
                False,
                "check_project",
                "GX Works2中没有已打开的目标工程。",
                ImportErrorCode.TARGET_PROJECT_NOT_OPEN,
                csv_path=validation.path,
            )
        if not state.get("program_ready"):
            return ImportResult(
                False,
                "check_program",
                "目标工程已打开，但当前程序不可编辑或未选中。",
                ImportErrorCode.TARGET_PROGRAM_NOT_READY,
                csv_path=validation.path,
                project_name=session.project_name,
            )

        project_name = state.get("project_name") or session.project_name or "GXWorks2"
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
        backup_path = ""
        try:
            if not created_project:
                caption = state.get("project_path") or project_name
                # A brand new project has no file to protect. With an explicit
                # path there is something to verify, so the resolver decides and
                # refuses when the window cannot corroborate it.
                nothing_saved = (
                    not project_path
                    and str(caption).strip().casefold() in _UNSAVED_PROJECT_NAMES
                )
                if not nothing_saved:
                    candidates = ()
                    if not project_path and project_search_roots:
                        candidates = discover_candidates(
                            caption_label(caption), project_search_roots
                        )
                    resolution = resolve_project_path(
                        caption, explicit=project_path, candidates=candidates
                    )
                    if not resolution:
                        reason = resolution.reason or "无法确定当前工程的保存位置。"
                        return ImportResult(
                            False, "backup", f"导入前GXW备份失败：{reason}",
                            ImportErrorCode.PROJECT_PATH_UNKNOWN,
                            csv_path=validation.path, project_name=project_name,
                            details={
                                "project_path_source": resolution.source,
                                "project_path_candidates": [
                                    str(item) for item in resolution.candidates
                                ],
                            },
                        )
                    source = resolution.path
                    report("backup", "正在备份当前GXW工程文件…")
                    folder = self.csv_manager.backup_folder(self.backup_root, source.stem)
                    destination = folder / source.name
                    # Keep the saved project intact before any native import.
                    shutil.copy2(source, destination)
                    backup_path = str(destination)
        except Exception as error:
            return ImportResult(
                False, "backup", f"导入前GXW备份失败：{error}",
                ImportErrorCode.BACKUP_FAILED, csv_path=validation.path,
                project_name=project_name,
            )

        report("prepare_import", "正在准备待发送的程序与注释…")
        try:
            target_semantic_hash = self.csv_manager.program_semantic_sha256(validation.path)
            target_file_hash = self.csv_manager.file_sha256(validation.path)
            target_comment_hash = (
                self.csv_manager.comments_semantic_sha256(comment_validation.path)
                if should_import_comments and comment_validation is not None else ""
            )
        except Exception as error:
            return ImportResult(
                False, "prepare_import", f"无法准备待发送的CSV：{error}",
                ImportErrorCode.CSV_INVALID, csv_path=validation.path,
                backup_path=backup_path, project_name=project_name,
            )
        protection_details = {
            "project_identity": identity,
            "baseline_found": None,
            "baseline_checked": False,
            "roundtrip_verified": False,
            "target_program_semantic_sha256": target_semantic_hash,
            "target_comment_semantic_sha256": target_comment_hash,
        }

        try:
            staged_import_path = self.csv_manager.prepare_import_program(
                validation.path,
                staging_folder / "program_to_import.csv",
            )
            report(
                "import",
                "正在从CSV文件读取新程序并写入当前MAIN…",
            )
            import_state = self.automation.import_program_csv(
                session, staged_import_path
            )
        except Exception as error:
            return ImportResult(
                False,
                "import",
                f"GX Works2导入失败：{error}",
                ImportErrorCode.AUTOMATION_FAILED,
                csv_path=validation.path,
                backup_path=str(backup_path),
                project_name=project_name,
            )
        if not import_state.get("success"):
            return ImportResult(
                False,
                "verify",
                import_state.get("message", "GX Works2未报告导入成功。"),
                ImportErrorCode.IMPORT_VERIFICATION_FAILED,
                csv_path=validation.path,
                backup_path=str(backup_path),
                project_name=project_name,
                details={"gxworks2": import_state},
            )

        comment_import_state = None
        if should_import_comments:
            try:
                report(
                    "import_comments",
                    "正在从CSV文件读取软元件注释…",
                )
                comment_import_state = self.automation.import_comments_csv(
                    session, comment_validation.path
                )
            except Exception as error:
                return ImportResult(
                    False,
                    "import_comments",
                    f"程序已导入，但GX Works2软元件注释导入失败：{error}",
                    ImportErrorCode.AUTOMATION_FAILED,
                    csv_path=validation.path,
                    backup_path=str(backup_path),
                    project_name=project_name,
                    details={
                        "gxworks2": import_state,
                        "comment_backup_path": "",
                        "version_protection": protection_details,
                    },
                )
            if not comment_import_state.get("success"):
                return ImportResult(
                    False,
                    "verify_comments",
                    "程序已导入，但软元件注释导入未完成：" + str(comment_import_state.get(
                        "message") or "GX Works2未报告软元件注释导入成功。"),
                    ImportErrorCode.IMPORT_VERIFICATION_FAILED,
                    csv_path=validation.path,
                    backup_path=str(backup_path),
                    project_name=project_name,
                    details={
                        "gxworks2": import_state,
                        "comments": comment_import_state,
                        "comment_backup_path": "",
                        "version_protection": protection_details,
                    },
                )

        baseline_error = None
        report("record_baseline", "正在记录本次发送版本…")
        try:
            self.baseline_store.save(
                identity,
                program_semantic_sha256=target_semantic_hash,
                program_file_sha256=target_file_hash,
                app_program_semantic_sha256=target_semantic_hash,
                gx_program_semantic_sha256=target_semantic_hash,
                comments_semantic_sha256=target_comment_hash,
                app_comment_semantic_sha256=target_comment_hash,
                gx_comment_semantic_sha256=target_comment_hash,
                import_context=import_context,
            )
            protection_details["status"] = "baseline_recorded"
        except Exception as error:
            baseline_error = str(error)
            protection_details["status"] = "baseline_write_failed"
            protection_details["error"] = baseline_error

        project_save_state = None
        if save_project:
            report("save_project", "正在保存GX Works2工程…")
            save_method = getattr(self.automation, "save_project", None)
            try:
                project_save_state = (
                    dict(save_method(session) or {})
                    if callable(save_method)
                    else {
                        "success": False,
                        "save_required": True,
                        "message": "请在GX Works2中保存当前工程。",
                    }
                )
            except Exception as error:
                project_save_state = {
                    "success": False,
                    "save_required": True,
                    "message": f"无法自动保存GX Works2工程：{error}",
                }

        report("verify", "正在检查GX Works2导入结果…")
        imported_message = "当前程序已导入GX Works2。"
        if should_import_comments:
            imported_message = "当前程序和软元件注释已导入GX Works2。"
            if comment_import_state and comment_import_state.get("comment_scope") == "program":
                imported_message = "当前程序和MAIN局部软元件注释已导入GX Works2；该型号使用局部注释表。"
        elif comment_validation is not None:
            imported_message = "当前程序已导入GX Works2；注释CSV无内容，已保留工程现有注释。"
        error_code = None
        stage = "complete"
        if baseline_error:
            stage = "complete_with_warning"
            imported_message += " 未能记录同步基线；本次导入已完成。"
        if project_save_state is not None and not project_save_state.get("success"):
            stage = "complete_with_warning"
            error_code = ImportErrorCode.PROJECT_SAVE_REQUIRED
            imported_message += " " + str(
                project_save_state.get("message")
                or "请在GX Works2中保存当前工程。"
            )
        return ImportResult(
            True,
            stage,
            imported_message,
            error_code=error_code,
            csv_path=validation.path,
            backup_path=str(backup_path),
            project_name=project_name,
            details={
                "validation": asdict(validation),
                "comment_validation": (
                    asdict(comment_validation) if comment_validation else None
                ),
                "gxworks2": import_state,
                "comments": comment_import_state,
                "comment_backup_path": "",
                "verified_program_path": "",
                "verified_comment_path": "",
                "project_save": project_save_state,
                "project_preparation": project_preparation,
                "version_protection": protection_details,
            },
        )

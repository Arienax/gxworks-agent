from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import time

from .baseline_store import ImportBaselineStore
from .models import ImportErrorCode, ImportResult
from .project_profiles import native_project_profile


_UNSAVED_PROJECT_NAMES = {
    "(工程未设置)",
    "工程未设置",
    "(project not set)",
    "project not set",
}


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
        export_validation_timeout=2.5,
        export_validation_poll_interval=0.05,
    ):
        self.finder = finder
        self.automation = automation
        self.csv_manager = csv_manager
        self.backup_root = Path(backup_root)
        self.baseline_store = baseline_store or ImportBaselineStore(self.backup_root)
        self.export_validation_timeout = max(
            0.0, float(export_validation_timeout or 0.0)
        )
        self.export_validation_poll_interval = max(
            0.01, float(export_validation_poll_interval or 0.05)
        )

    def _wait_for_valid_export(self, path, validator):
        """Wait until GX Works2 has finished flushing a newly created CSV.

        GX Works2 creates the destination before all rows and the final END
        instruction have been written.  UI automation can therefore observe
        the file a few milliseconds before it is a valid program.  Retrying
        the real format validator avoids both a fixed delay and a false backup
        failure on otherwise successful exports.
        """

        deadline = time.monotonic() + self.export_validation_timeout
        result = validator(path)
        while not result.valid and time.monotonic() < deadline:
            time.sleep(self.export_validation_poll_interval)
            result = validator(path)
        return result

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
        pre_import_policy="protected",
        create_project_path=None,
    ):
        """Import with protection by default; manual backup is an explicit caller choice."""
        if not isinstance(pre_import_policy, str) or pre_import_policy not in {"protected", "manual_backup"} or (
            pre_import_policy == "manual_backup" and (
                verify_roundtrip or rollback_expected_current_sha256
                or expected_current_comment_sha256
            )
        ):
            return ImportResult(False, "validate_request", "导入策略无效或与回读/回滚保护冲突。",
                                ImportErrorCode.INVALID_REQUEST)
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

        with ExitStack() as resources:
            staging_folder = None
            if pre_import_policy == "manual_backup":
                staging_folder = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="gx-import-")))
            result = self._import_current_program(
                csv_path, comment_csv_path=comment_csv_path, start_if_needed=start_if_needed,
                progress=report, import_context=import_context, project_identity=project_identity,
                rollback_expected_current_sha256=rollback_expected_current_sha256,
                expected_current_comment_sha256=expected_current_comment_sha256,
                synchronize_comments=synchronize_comments, verify_roundtrip=verify_roundtrip,
                save_project=save_project, staging_folder=staging_folder,
                create_project_path=create_project_path,
            )
        finished = time.monotonic()
        if current_stage is not None:
            timings[current_stage] = timings.get(current_stage, 0) + (finished - stage_started) * 1000
        return replace(result, details={**result.details,
            "pre_import_policy": pre_import_policy,
            "backup_performed": bool(result.backup_path),
            "timings_ms": {**{key: round(value, 3) for key, value in timings.items()},
                           "total": round((finished - started) * 1000, 3)},
        })

    def _import_current_program(
        self, csv_path, *, comment_csv_path, start_if_needed, progress, import_context,
        project_identity, rollback_expected_current_sha256, expected_current_comment_sha256,
        synchronize_comments, verify_roundtrip, save_project, staging_folder,
        create_project_path,
    ):
        manual_backup = staging_folder is not None
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
        backup_folder = None
        comment_backup_path = None
        backup_path = ""
        if not manual_backup:
            try:
                report(
                    "backup",
                    "正在备份当前MAIN（GX Works2将显示“写入至CSV文件”）…",
                )
                backup_folder = self.csv_manager.backup_folder(
                    self.backup_root, project_name
                )
                exported = backup_folder / "program_before_import.csv"
                self.automation.export_current_program(session, exported)
                exported_validation = self._wait_for_valid_export(
                    exported,
                    self.csv_manager.validate,
                )
                if not exported_validation.valid:
                    detail = "；".join(exported_validation.errors) or "未知格式错误"
                    raise RuntimeError(
                        "GX Works2导出的备份CSV未通过格式验证：" + detail
                    )
                backup_path = exported
                if should_import_comments:
                    report(
                        "backup_comments",
                        "正在备份当前全局软元件注释…",
                    )
                    comment_backup_path = backup_folder / "comments_before_import.csv"
                    self.automation.export_current_comments(
                        session, comment_backup_path
                    )
                    exported_comments = self._wait_for_valid_export(
                        comment_backup_path,
                        lambda path: self.csv_manager.validate_comments(
                            path,
                            require_crlf=False,
                        ),
                    )
                    if not exported_comments.valid:
                        detail = "；".join(exported_comments.errors) or "未知格式错误"
                        raise RuntimeError(
                            "GX Works2导出的软元件注释备份未通过格式验证："
                            + detail
                        )
                self.csv_manager.write_checksum_manifest(backup_folder)
            except Exception as error:
                return ImportResult(
                    False,
                    "backup",
                    f"导入前备份失败：{error}",
                    ImportErrorCode.BACKUP_FAILED,
                    csv_path=validation.path,
                    project_name=project_name,
                    details={
                        "comment_backup_path": (
                            str(comment_backup_path) if comment_backup_path else ""
                        )
                    },
                )

        report("prepare_import" if manual_backup else "compare_baseline",
               "正在准备待发送的程序与注释…" if manual_backup else "正在检查GX Works2工程是否被外部修改…")
        try:
            current_semantic_hash = self.csv_manager.program_semantic_sha256(
                backup_path
            ) if not manual_backup else ""
            target_semantic_hash = self.csv_manager.program_semantic_sha256(
                validation.path
            )
            target_file_hash = self.csv_manager.file_sha256(validation.path)
            current_comment_hash = (
                self.csv_manager.comments_semantic_sha256(comment_backup_path)
                if comment_backup_path is not None
                else ""
            )
            target_comment_hash = (
                self.csv_manager.comments_semantic_sha256(comment_validation.path)
                if should_import_comments and comment_validation is not None
                else ""
            )
            baseline = None if manual_backup else self.baseline_store.load(identity)
        except Exception as error:
            return ImportResult(
                False,
                "prepare_import" if manual_backup else "compare_baseline",
                f"无法准备待发送的CSV：{error}" if manual_backup else f"无法核对GX Works2版本，已停止覆盖：{error}",
                ImportErrorCode.CSV_INVALID if manual_backup else ImportErrorCode.BASELINE_READ_FAILED,
                csv_path=validation.path,
                backup_path=str(backup_path),
                project_name=project_name,
            )

        protection_details = {
            "project_identity": identity,
            "baseline_found": None if manual_backup else baseline is not None,
            "baseline_checked": not manual_backup,
            "current_program_semantic_sha256": current_semantic_hash,
            "target_program_semantic_sha256": target_semantic_hash,
            "baseline_program_semantic_sha256": (
                baseline.get("program_semantic_sha256", "") if baseline else ""
            ),
            "current_comment_semantic_sha256": current_comment_hash,
            "target_comment_semantic_sha256": target_comment_hash,
            "baseline_comment_semantic_sha256": (
                baseline.get("gx_comment_semantic_sha256", "") if baseline else ""
            ),
            "previous_import_context": (
                baseline.get("import_context", {}) if baseline else {}
            ),
        }
        baseline_program_hash = (
            baseline.get("gx_program_semantic_sha256")
            or baseline.get("program_semantic_sha256")
            if baseline
            else ""
        )
        baseline_comment_hash = (
            str(baseline.get("gx_comment_semantic_sha256") or "")
            if baseline
            else ""
        )
        program_modified = bool(
            baseline is not None and baseline_program_hash != current_semantic_hash
        )
        comments_modified = bool(
            baseline is not None
            and baseline_comment_hash
            and current_comment_hash
            and baseline_comment_hash != current_comment_hash
        )
        previous_project_id = str(
            ((baseline or {}).get("import_context") or {}).get("project_id") or ""
        )
        current_project_id = str(
            (import_context or {}).get("project_id") or ""
            if isinstance(import_context, dict)
            else ""
        )
        binding_mismatch = bool(
            previous_project_id
            and current_project_id
            and previous_project_id != current_project_id
        )
        protection_details["binding_mismatch"] = binding_mismatch
        protection_details["previous_project_id"] = previous_project_id
        rollback_guard = str(rollback_expected_current_sha256 or "").strip().lower()
        comment_guard = str(expected_current_comment_sha256 or "").strip().lower()
        guarded_rollback = bool(
            baseline is not None
            and (program_modified or comments_modified or binding_mismatch)
            and len(rollback_guard) == 64
            and rollback_guard == current_semantic_hash
            and (
                not current_comment_hash
                or (len(comment_guard) == 64 and comment_guard == current_comment_hash)
            )
        )
        protection_details["guarded_rollback"] = guarded_rollback
        if (
            baseline is not None
            and (program_modified or comments_modified or binding_mismatch)
            and not guarded_rollback
        ):
            protection_details["status"] = "external_modification_detected"
            return ImportResult(
                False,
                "external_modification",
                (
                    "当前GX Works2工程上次绑定到另一个PLC AI项目。"
                    "为避免串项目覆盖，已停止写入；请使用“高级同步”选择保留哪一方。"
                    if binding_mismatch
                    else
                    "检测到GX Works2中的当前程序或软元件注释与上次同步版本不同，"
                    "可能存在人工修改。为避免覆盖，已停止导入；刚才导出的程序备份已保留。"
                ),
                ImportErrorCode.EXTERNAL_MODIFICATION_DETECTED,
                csv_path=validation.path,
                backup_path=str(backup_path),
                project_name=project_name,
                details={
                    "version_protection": protection_details,
                    "comment_backup_path": (
                        str(comment_backup_path) if comment_backup_path else ""
                    ),
                },
            )

        try:
            staged_import_path = self.csv_manager.prepare_import_program(
                validation.path,
                (staging_folder if manual_backup else backup_folder) / "program_to_import.csv",
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
                        "comment_backup_path": str(comment_backup_path) if comment_backup_path else "",
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
                        "comment_backup_path": str(comment_backup_path) if comment_backup_path else "",
                        "version_protection": protection_details,
                    },
                )

        verified_program_path = None
        verified_comment_path = None
        verified_program_hash = target_semantic_hash
        verified_comment_hash = target_comment_hash
        if verify_roundtrip:
            try:
                report("verify_roundtrip", "正在回读GX Works2并核对同步结果…")
                verified_program_path = backup_folder / "program_after_import.csv"
                self.automation.export_current_program(session, verified_program_path)
                verified_program = self._wait_for_valid_export(
                    verified_program_path,
                    self.csv_manager.validate,
                )
                if not verified_program.valid:
                    raise RuntimeError("；".join(verified_program.errors))
                verified_program_hash = self.csv_manager.program_semantic_sha256(
                    verified_program_path
                )
                if verified_program_hash != target_semantic_hash:
                    raise RuntimeError("GX Works2回读程序与待同步版本不一致")
                if should_import_comments:
                    verified_comment_path = backup_folder / "comments_after_import.csv"
                    self.automation.export_current_comments(
                        session, verified_comment_path
                    )
                    verified_comments = self._wait_for_valid_export(
                        verified_comment_path,
                        lambda path: self.csv_manager.validate_comments(
                            path, require_crlf=False
                        ),
                    )
                    if not verified_comments.valid:
                        raise RuntimeError("；".join(verified_comments.errors))
                    verified_comment_hash = self.csv_manager.comments_semantic_sha256(
                        verified_comment_path
                    )
                    if verified_comment_hash != target_comment_hash:
                        raise RuntimeError("GX Works2回读软元件注释与待同步版本不一致")
                self.csv_manager.write_checksum_manifest(backup_folder)
            except Exception as error:
                protection_details["status"] = "roundtrip_verification_failed"
                return ImportResult(
                    False,
                    "verify_roundtrip",
                    f"同步后复核失败：{error}。导入前后的CSV均已保留，请勿继续覆盖。",
                    ImportErrorCode.IMPORT_VERIFICATION_FAILED,
                    csv_path=validation.path,
                    backup_path=str(backup_path),
                    project_name=project_name,
                    details={
                        "gxworks2": import_state,
                        "comments": comment_import_state,
                        "comment_backup_path": (
                            str(comment_backup_path) if comment_backup_path else ""
                        ),
                        "verified_program_path": (
                            str(verified_program_path) if verified_program_path else ""
                        ),
                        "verified_comment_path": (
                            str(verified_comment_path) if verified_comment_path else ""
                        ),
                        "version_protection": protection_details,
                    },
                )

        baseline_error = None
        report("record_baseline", "正在记录本次发送版本…")
        try:
            self.baseline_store.save(
                identity,
                program_semantic_sha256=verified_program_hash,
                program_file_sha256=target_file_hash,
                app_program_semantic_sha256=target_semantic_hash,
                gx_program_semantic_sha256=verified_program_hash,
                comments_semantic_sha256=verified_comment_hash,
                app_comment_semantic_sha256=target_comment_hash,
                gx_comment_semantic_sha256=verified_comment_hash,
                import_context=import_context,
            )
            protection_details["status"] = (
                "baseline_recorded" if manual_backup else
                "baseline_created" if baseline is None else "baseline_updated"
            )
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
            error_code = None if manual_backup else ImportErrorCode.BASELINE_WRITE_FAILED
            imported_message += (
                " 未能记录同步基线；本次导入已完成。" if manual_backup else
                " 但未能保存下次导入所需的版本基线；再次导入前请先核对工程。"
            )
        if (manual_backup or not baseline_error) and project_save_state is not None and not project_save_state.get("success"):
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
                "comment_backup_path": (
                    str(comment_backup_path) if comment_backup_path else ""
                ),
                "verified_program_path": (
                    str(verified_program_path) if verified_program_path else ""
                ),
                "verified_comment_path": (
                    str(verified_comment_path) if verified_comment_path else ""
                ),
                "project_save": project_save_state,
                "project_preparation": project_preparation,
                "version_protection": protection_details,
            },
        )

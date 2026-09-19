"""Workers."""
from shared.i18n import get_language, language_context, tr
import copy
from pathlib import Path
from ui.desktop.qt import QThread, pyqtSignal
from application.model_api import generate_model_json
from storage.config import get_active_model_name, get_api_key, load_full_config
from plc.ir import is_plc_ir

def merge_partial_update(previous_json: dict, partial: dict) -> dict:
    """将增量修改合并到上一版完整 JSON 中，返回合并后的完整 JSON。"""
    merged = copy.deepcopy(previous_json)

    # 1. 合并 device_comments（仅更新有变化的条目）
    if "device_comments" in partial and partial["device_comments"]:
        for addr, comment in partial["device_comments"].items():
            merged["device_comments"][addr] = comment

    # 2. 合并 rungs：构建 rung_id → rung 的映射
    existing_rungs = {r["rung_id"]: r for r in merged.get("rungs", [])}

    # 3. 替换/新增
    for new_rung in partial.get("rungs", []):
        rid = new_rung.get("rung_id")
        if rid is not None:
            existing_rungs[rid] = new_rung

    # 4. 删除
    for rid in partial.get("delete_rung_ids", []):
        existing_rungs.pop(rid, None)

    # 5. 按 rung_id 排序
    merged["rungs"] = sorted(existing_rungs.values(), key=lambda r: r["rung_id"])

    return merged


class LanguageScopedThread(QThread):
    """Capture the caller's language before crossing the Qt thread boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_language = get_language()

    def run(self):
        with language_context(self.response_language):
            self.run_in_language()


class AnalysisThread(LanguageScopedThread):
    """轻量分析线程 — 流式调用阶段1 API，不生成代码"""
    analysis_done = pyqtSignal(str, dict)
    analysis_failed = pyqtSignal(str, str)
    thinking_updated = pyqtSignal(str, str)
    content_updated = pyqtSignal(str, str)

    def __init__(
        self,
        task_id,
        user_input,
        conversation_history=None,
        confirmed_context=None,
        task_type=None,
        image_attachments=None,
    ):
        super().__init__()
        self.task_id = task_id
        self.user_input = user_input
        self.conversation_history = conversation_history or []
        self.confirmed_context = confirmed_context
        self.task_type = task_type
        self.image_attachments = tuple(image_attachments or ())

    def run_in_language(self):
        try:
            from application.model_api import analyze_requirement_streaming

            def on_reasoning(token):
                self.thinking_updated.emit(self.task_id, token)

            def on_content(token):
                self.content_updated.emit(self.task_id, token)

            result = analyze_requirement_streaming(
                self.user_input,
                on_reasoning_chunk=on_reasoning,
                on_content_chunk=on_content,
                conversation_history=self.conversation_history,
                confirmed_context=self.confirmed_context,
                task_type=self.task_type,
                image_attachments=self.image_attachments,
            )
            if result is None:
                self.analysis_failed.emit(
                    self.task_id, tr('AI 分析返回空结果，请重试。')
                )
                return
            self.analysis_done.emit(self.task_id, result)
        except Exception as e:
            self.analysis_failed.emit(self.task_id, tr('分析失败: {v0}', v0=str(e)))


class ToolAgentThread(LanguageScopedThread):
    """Run the bounded, allow-listed PLC tool loop outside the UI thread."""

    agent_done = pyqtSignal(str, object)
    agent_failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)
    thinking_updated = pyqtSignal(str, str)
    content_updated = pyqtSignal(str, str)

    def __init__(self, task_id, user_text, context, conversation_history=None):
        super().__init__()
        self.task_id = task_id
        self.user_text = user_text
        self.context = context
        self.conversation_history = conversation_history or []

    def run_in_language(self):
        try:
            from agent_runtime.agent import run_tool_agent

            result = run_tool_agent(
                self.user_text,
                context=self.context,
                conversation_history=self.conversation_history,
                on_reasoning_chunk=lambda token: self.thinking_updated.emit(
                    self.task_id, token
                ),
                on_content_chunk=lambda token: self.content_updated.emit(
                    self.task_id, token
                ),
                on_progress=lambda message: self.progress_updated.emit(
                    self.task_id, message
                ),
            )
            self.agent_done.emit(
                self.task_id,
                {
                    "content": result.content,
                    "pending_actions": result.pending_actions,
                    "audit": result.audit,
                    "rounds": result.rounds,
                },
            )
        except Exception as error:
            self.agent_failed.emit(self.task_id, tr('AI 工具任务失败：{v0}', v0=error))


class GXWorks2ImportThread(QThread):
    completed = pyqtSignal(object)
    progress_changed = pyqtSignal(str, str)

    def __init__(
        self,
        csv_path,
        comment_csv_path=None,
        import_context=None,
        *,
        expected_current_program_sha256=None,
        expected_current_comment_sha256=None,
        synchronize_comments=True,
        verify_roundtrip=True,
        save_project=True,
    ):
        super().__init__()
        self.csv_path = str(csv_path)
        self.comment_csv_path = (
            str(comment_csv_path) if comment_csv_path is not None else None
        )
        self.import_context = dict(import_context or {})
        self.expected_current_program_sha256 = str(
            expected_current_program_sha256 or ""
        )
        self.expected_current_comment_sha256 = str(
            expected_current_comment_sha256 or ""
        )
        self.synchronize_comments = bool(synchronize_comments)
        self.verify_roundtrip = bool(verify_roundtrip)
        self.save_project = bool(save_project)

    def run(self):
        from contextlib import ExitStack
        from application.execution import DesktopResourceLock

        desktop_cleanup = ExitStack()
        pythoncom = None
        try:
            desktop_cleanup.enter_context(DesktopResourceLock())
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pythoncom = None
            from gxworks2 import import_current_program

            result = import_current_program(
                self.csv_path,
                comment_csv_path=self.comment_csv_path,
                progress=self.progress_changed.emit,
                import_context=self.import_context,
                rollback_expected_current_sha256=(
                    self.expected_current_program_sha256 or None
                ),
                expected_current_comment_sha256=(
                    self.expected_current_comment_sha256 or None
                ),
                synchronize_comments=self.synchronize_comments,
                verify_roundtrip=self.verify_roundtrip,
                save_project=self.save_project,
            )
        except Exception as error:
            from gxworks2.models import ImportErrorCode, ImportResult

            result = ImportResult(
                False,
                "unexpected",
                tr('GX Works2导入服务异常：{v0}', v0=error),
                ImportErrorCode.AUTOMATION_FAILED,
                csv_path=self.csv_path,
            )
        finally:
            try:
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            finally:
                desktop_cleanup.close()
        self.completed.emit(result)


class GXWorks2SyncInspectThread(QThread):
    completed = pyqtSignal(object)
    progress_changed = pyqtSignal(str, str)

    def __init__(
        self,
        program_csv_path=None,
        comment_csv_path=None,
        import_context=None,
        *,
        snapshot_only=False,
    ):
        super().__init__()
        self.program_csv_path = str(program_csv_path or "")
        self.comment_csv_path = str(comment_csv_path or "")
        self.import_context = dict(import_context or {})
        self.snapshot_only = bool(snapshot_only)

    def run(self):
        from contextlib import ExitStack
        from application.execution import DesktopResourceLock

        desktop_cleanup = ExitStack()
        pythoncom = None
        try:
            desktop_cleanup.enter_context(DesktopResourceLock())
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pythoncom = None
            if self.snapshot_only:
                from gxworks2 import read_current_snapshot

                result = read_current_snapshot(
                    progress=self.progress_changed.emit,
                    import_context=self.import_context,
                )
            else:
                from gxworks2 import inspect_current_sync

                result = inspect_current_sync(
                    self.program_csv_path,
                    self.comment_csv_path,
                    progress=self.progress_changed.emit,
                    import_context=self.import_context,
                )
        except Exception as error:
            from gxworks2.diagnostics import describe_exception, exception_details
            from gxworks2.models import GXSyncErrorCode, SyncResult, SyncStatus

            result = SyncResult(
                False,
                SyncStatus.ERROR,
                tr('GX Works2读取检查异常：') + describe_exception(error),
                GXSyncErrorCode.GX_UNEXPECTED_ERROR,
                details={
                    "category": "precheck",
                    "stage": "unexpected",
                    "error_code": GXSyncErrorCode.GX_UNEXPECTED_ERROR.value,
                    "retryable": False,
                    "suggestion": tr('请查看技术详情；若问题持续出现，请保留详情用于排查。'),
                    "gx_running": None,
                    "gx_process_id": None,
                    "gx_window_handle": None,
                    "project_open": None,
                    "program_ready": None,
                    "program_name": str(
                        self.import_context.get("program_name") or "MAIN"
                    ),
                    "attempt": 1,
                    "max_attempts": 1,
                    "attempts": [],
                    "program_path": self.program_csv_path,
                    "comment_path": self.comment_csv_path,
                    "bootstrap": self.snapshot_only,
                    **exception_details(error),
                },
                stage="unexpected",
                retryable=False,
            )
        finally:
            try:
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            finally:
                desktop_cleanup.close()
        self.completed.emit(result)


class GXWorks2PullThread(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        program_csv_path,
        comment_csv_path,
        output_dir,
        *,
        plc_model="FX3U",
        program_name="MAIN",
        revision=1,
    ):
        super().__init__()
        self.program_csv_path = str(program_csv_path)
        self.comment_csv_path = str(comment_csv_path)
        self.output_dir = str(output_dir)
        self.plc_model = str(plc_model or "FX3U")
        self.program_name = str(program_name or "MAIN")
        self.revision = int(revision)

    def run(self):
        from contextlib import ExitStack
        from application.execution import DesktopResourceLock

        desktop_cleanup = ExitStack()
        failure_message = None
        try:
            desktop_cleanup.enter_context(DesktopResourceLock())
            from gxworks2.csv_importer import materialize_gxworks2_version

            metadata = materialize_gxworks2_version(
                self.program_csv_path,
                self.comment_csv_path,
                self.output_dir,
                plc_model=self.plc_model,
                program_name=self.program_name,
                revision=self.revision,
            )
        except Exception as error:
            failure_message = str(error)
        finally:
            desktop_cleanup.close()
        if failure_message is not None:
            self.failed.emit(failure_message)
        else:
            self.completed.emit(metadata)


class DebugThread(LanguageScopedThread):
    debug_done = pyqtSignal(str, dict)
    debug_failed = pyqtSignal(str, str)

    def __init__(
        self,
        task_id,
        user_question,
        current_version_json,
        base_version_id,
        local_findings=None,
        conversation_history=None,
        confirmed_spec=None,
    ):
        super().__init__()
        self.task_id = task_id
        self.user_question = user_question
        self.current_version_json = current_version_json
        self.base_version_id = base_version_id
        self.local_findings = local_findings or []
        self.conversation_history = conversation_history or []
        self.confirmed_spec = confirmed_spec
        try:
            self.model_name = get_active_model_name(load_full_config())
        except Exception:
            self.model_name = None

    def run_in_language(self):
        try:
            from application.model_api import debug_ladder

            report = debug_ladder(
                self.user_question,
                self.current_version_json,
                confirmed_spec=self.confirmed_spec,
                conversation_history=self.conversation_history,
                local_findings=self.local_findings,
                model_name=self.model_name,
                effort=None,
                raise_errors=False,
            )
            if report is None:
                report = self._fallback_report()
            report["local_findings"] = self.local_findings
            report["base_version_id"] = self.base_version_id
            self.debug_done.emit(self.task_id, report)
        except Exception as error:
            self.debug_done.emit(self.task_id, self._fallback_report(str(error)))

    def _fallback_report(self, error_text=""):
        causes = [
            tr('AI 调试接口暂时不可用，已先返回本地结构评审结果。')
        ]
        if error_text:
            causes.append(tr('接口错误：{v0}', v0=error_text))
        return {
            "summary": tr('调试报告已由本地评审兜底生成'),
            "possible_causes": causes,
            "related_rungs": [],
            "recommended_changes": [
                tr('根据本地评审提示检查输出所有权、状态跳转、复位优先级和定时器复位路径。')
            ],
            "needs_fix": False,
            "fix_instruction": "",
            "local_findings": self.local_findings,
            "base_version_id": self.base_version_id,
        }


class InspectionThread(LanguageScopedThread):
    """Run deterministic inspection first, then optionally enrich it with AI."""

    local_ready = pyqtSignal(str, dict)
    inspection_done = pyqtSignal(str, dict)
    inspection_failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)

    def __init__(
        self,
        task_id,
        report_type,
        request,
        current_version_json,
        base_version_id,
        plc_model,
        *,
        project_id=None,
        program_ir=None,
        confirmed_spec=None,
        conversation_history=None,
        effort=None,
        deep=True,
    ):
        super().__init__()
        self.task_id = task_id
        self.report_type = report_type
        self.request = copy.deepcopy(request)
        self.current_version_json = copy.deepcopy(current_version_json)
        self.base_version_id = base_version_id
        self.plc_model = plc_model
        self.project_id = str(project_id or "")
        self.program_ir = copy.deepcopy(program_ir) if isinstance(program_ir, dict) else None
        self.confirmed_spec = copy.deepcopy(confirmed_spec)
        self.conversation_history = copy.deepcopy(conversation_history or [])
        self.effort = effort
        self.deep = bool(deep)

    @staticmethod
    def _mark_ai(report, status, error=""):
        from application.review import InspectionWorkflow

        return InspectionWorkflow._mark_ai(report, status, error)

    def run_in_language(self):
        from application.review import InspectionWorkflow

        def on_event(event_type, payload):
            if event_type == "progress":
                self.progress_updated.emit(self.task_id, payload.get("message", ""))
            elif event_type == "local":
                self.local_ready.emit(self.task_id, payload)

        try:
            workflow = InspectionWorkflow(
                self.task_id, self.report_type, self.request,
                self.current_version_json, self.base_version_id, self.plc_model,
                project_id=self.project_id, program_ir=self.program_ir,
                confirmed_spec=self.confirmed_spec,
                conversation_history=self.conversation_history,
                effort=self.effort, deep=self.deep,
                on_event=on_event, response_language=self.response_language,
            )
            workflow._api_key_available = self._api_key_available
            self.inspection_done.emit(self.task_id, workflow.run())
        except Exception as error:
            self.inspection_failed.emit(self.task_id, str(error))

    @staticmethod
    def _api_key_available():
        try:
            config = load_full_config()
            return bool(get_api_key(config))
        except Exception:
            return False


class EvidenceDebugPlanThread(LanguageScopedThread):
    """Build an evidence-bound diagnosis and local patch off the GUI thread."""

    plan_ready = pyqtSignal(str, dict)
    plan_failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)

    def __init__(
        self,
        task_id,
        store,
        project_id,
        base_version_id,
        run_id,
        *,
        effort=None,
    ):
        super().__init__()
        self.task_id = task_id
        self.store = store
        self.project_id = project_id
        self.base_version_id = base_version_id
        self.run_id = run_id
        self.effort = effort

    def run_in_language(self):
        from application.planning import EvidenceDebugPlanWorkflow

        def on_event(event_type, payload):
            if event_type == "progress":
                self.progress_updated.emit(self.task_id, payload.get("message", ""))

        try:
            workflow = EvidenceDebugPlanWorkflow(
                self.task_id, self.store, self.project_id,
                self.base_version_id, self.run_id, effort=self.effort,
                on_event=on_event, response_language=self.response_language,
            )

            self.plan_ready.emit(self.task_id, workflow.run())
        except Exception as error:
            self.plan_failed.emit(self.task_id, str(error))


class EvidenceDebugExecuteThread(QThread):
    """Execute one already approved Debug/Patch plan off the GUI thread."""

    completed = pyqtSignal(str, dict)
    failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)

    def __init__(self, task_id, store, plan):
        super().__init__()
        self.task_id = task_id
        self.store = store
        self.plan = copy.deepcopy(plan)

    def run(self):
        from contextlib import ExitStack
        from application.execution import DesktopResourceLock

        desktop_cleanup = ExitStack()
        pythoncom = None
        failure_message = None
        try:
            desktop_cleanup.enter_context(DesktopResourceLock())
            try:
                import pythoncom

                pythoncom.CoInitialize()
            except ImportError:
                pythoncom = None
            from gxworks2 import GXSimulator2PreparationService, import_current_program
            from application.debug_loop import DebugPatchLoopService
            from simulator.runtime import get_simulator_gateway_runtime

            self.progress_updated.emit(self.task_id, tr('正在校验候选补丁'))

            def importing(*args, **kwargs):
                phase = (kwargs.get("import_context") or {}).get("debug_phase")
                self.progress_updated.emit(
                    self.task_id,
                    tr('正在恢复原版本') if phase == "rollback" else tr('正在导入候选版本'),
                )
                return import_current_program(*args, **kwargs)

            runtime = get_simulator_gateway_runtime()
            preparer = GXSimulator2PreparationService(runtime=runtime)
            service = DebugPatchLoopService(
                self.store,
                importer=importing,
                simulator_backend=runtime.client(timeout=5.0),
                simulator_preparer=preparer,
            )
            result = service.execute_approved_plan(self.plan)
        except Exception as error:
            failure_message = str(error)
        finally:
            try:
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            finally:
                desktop_cleanup.close()
        if failure_message is not None:
            self.failed.emit(self.task_id, failure_message)
        else:
            self.completed.emit(self.task_id, result)


class SimulatorTestPlanThread(LanguageScopedThread):
    """Generate and deterministically validate a version-bound Test DSL plan."""

    completed = pyqtSignal(str, dict)
    failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)
    thinking_updated = pyqtSignal(str, str)
    content_updated = pyqtSignal(str, str)

    def __init__(self, task_id, store, project_id, version_id, *, effort=None):
        super().__init__()
        self.task_id = task_id
        self.store = store
        self.project_id = project_id
        self.version_id = version_id
        self.effort = effort

    def run_in_language(self):
        from application.planning import SimulatorTestPlanWorkflow

        def on_event(event_type, payload):
            if event_type == "progress":
                self.progress_updated.emit(self.task_id, payload.get("message", ""))
            elif event_type == "reasoning":
                self.thinking_updated.emit(self.task_id, payload["text"])
            elif event_type == "content":
                self.content_updated.emit(self.task_id, payload["text"])
        try:
            workflow = SimulatorTestPlanWorkflow(
                self.task_id, self.store, self.project_id, self.version_id, effort=self.effort,
                on_event=on_event, response_language=self.response_language,
            )

            self.completed.emit(self.task_id, workflow.run())
        except Exception as error:
            self.failed.emit(self.task_id, str(error))


class SimulatorTestExecuteThread(QThread):
    """Execute an approved high-level import/simulator workflow off the UI thread."""

    completed = pyqtSignal(str, dict)
    failed = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, str)
    test_progress_updated = pyqtSignal(str, dict)

    def __init__(self, task_id, store, project_id, version_id, plan):
        super().__init__()
        self.task_id = task_id
        self.store = store
        self.project_id = project_id
        self.version_id = version_id
        self.plan = copy.deepcopy(plan)

    def run(self):
        from contextlib import ExitStack
        from application.execution import DesktopResourceLock

        desktop_cleanup = ExitStack()
        pythoncom = None
        failure_message = None
        try:
            desktop_cleanup.enter_context(DesktopResourceLock())
            try:
                import pythoncom

                pythoncom.CoInitialize()
            except ImportError:
                pythoncom = None
            from gxworks2 import GXSimulator2PreparationService, import_current_program
            from simulator.runtime import get_simulator_gateway_runtime
            from simulator.workflow import SimulatorVersionWorkflowService

            runtime = get_simulator_gateway_runtime()
            preparer = GXSimulator2PreparationService(runtime=runtime)
            service = SimulatorVersionWorkflowService(
                self.store,
                importer=import_current_program,
                preparer=preparer,
                backend=runtime.client(timeout=5.0),
            )

            stage_percent = {
                "preflight": 3,
                "stop_simulator": 8,
                "import": 18,
                "start_simulator": 42,
                "ready": 48,
                "execute_tests": 50,
                "save_evidence": 96,
                "stop_after_tests": 98,
                "complete": 100,
            }

            def report_stage(stage, message):
                self.progress_updated.emit(self.task_id, message)
                self.test_progress_updated.emit(
                    self.task_id,
                    {
                        "event": "workflow_stage",
                        "stage": str(stage),
                        "message": str(message),
                        "percent": int(stage_percent.get(str(stage), 5)),
                    },
                )

            def report_test(update):
                payload = dict(update or {})
                test_percent = max(
                    0,
                    min(100, int(payload.get("percent") or 0)),
                )
                payload["test_percent"] = test_percent
                payload["percent"] = 50 + round(test_percent * 0.45)
                self.test_progress_updated.emit(self.task_id, payload)

            result = service.run_approved_plan(
                self.project_id,
                self.version_id,
                self.plan,
                progress=report_stage,
                test_progress=report_test,
            )
        except Exception as error:
            failure_message = str(error)
        finally:
            try:
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            finally:
                desktop_cleanup.close()
        if failure_message is not None:
            self.failed.emit(self.task_id, failure_message)
        else:
            self.completed.emit(self.task_id, result)


class CompilerThread(LanguageScopedThread):
    success = pyqtSignal(str, object)
    failure = pyqtSignal(str, str)
    thinking_updated = pyqtSignal(str, str)
    content_updated = pyqtSignal(str, str)
    progress_updated = pyqtSignal(str, object)

    def __init__(
        self,
        task_id,
        user_input,
        effort,
        target_mode,
        output_dir,
        previous_json=None,
        conversation_history=None,
        confirmed_context=None,
        task_type=None,
        current_version_json=None,
        previous_ir=None,
        plc_model="FX3U",
        program_name="MAIN",
        revision=1,
        requirement_text="",
        repair_mode=False,
        format_repair=False,
        allowed_rung_ids=None,
        allowed_addresses=None,
        repair_plan=None,
        image_attachments=None,
        source_handoff=None,
    ):
        super().__init__()
        self.task_id = task_id
        self.user_input = user_input
        self.effort = effort
        self.target_mode = target_mode  # "ladder" 或 "st"
        self.previous_json = copy.deepcopy(previous_json)  # 多轮对话时的上一版完整 JSON（dict）
        self.output_dir = Path(output_dir)
        self.conversation_history = copy.deepcopy(conversation_history or [])
        self.confirmed_context = copy.deepcopy(confirmed_context)
        self.task_type = task_type or ("edit" if previous_json is not None else "generate")
        self.current_version_json = copy.deepcopy(current_version_json)
        self.previous_ir = copy.deepcopy(previous_ir) if is_plc_ir(previous_ir) else None
        self.plc_model = str(plc_model or "FX3U").upper()
        self.program_name = str(program_name or "MAIN").strip() or "MAIN"
        self.requirement_text = str(requirement_text or user_input or "")
        try:
            self.revision = max(0, int(revision))
        except (TypeError, ValueError):
            self.revision = 1
        self.repair_mode = bool(repair_mode)
        self.format_repair = bool(format_repair)
        self.allowed_rung_ids = {
            int(item) for item in (allowed_rung_ids or [])
        }
        self.allowed_addresses = {
            str(item).strip().upper()
            for item in (allowed_addresses or [])
            if str(item).strip()
        }
        self.repair_plan = copy.deepcopy(repair_plan) if isinstance(repair_plan, dict) else None
        self.image_attachments = tuple(image_attachments or ())
        self.source_handoff = copy.deepcopy(source_handoff)
        # 从配置文件读取默认模型
        try:
            self.model_name = get_active_model_name(load_full_config())
        except Exception:
            self.model_name = None
    
    def run_in_language(self):
        from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow

        request = GenerationRequest(**{
            field: getattr(self, field)
            for field in GenerationRequest.__dataclass_fields__
        })

        def on_event(event_type, payload):
            if event_type == "progress":
                self.progress_updated.emit(self.task_id, payload)
            elif event_type == "reasoning":
                self.thinking_updated.emit(self.task_id, payload["text"])
            elif event_type == "content":
                self.content_updated.emit(self.task_id, payload["text"])

        try:
            result = GenerationWorkflow(
                request, self.output_dir, on_event=on_event,
                dependencies=GenerationDependencies(generate_json=generate_model_json),
            ).run()
            self.success.emit(self.task_id, result)
        except Exception as error:
            self.failure.emit(self.task_id, str(error))


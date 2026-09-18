"""Deterministic inspection and optional model review, without GUI dependencies."""
import copy

from application.base import Workflow, WorkflowError, model_call
from storage.config import load_full_config, get_api_key
from shared.i18n import tr
from plc.ir import build_plc_ir


class InspectionWorkflow(Workflow):
    """Run deterministic inspection first, then optionally enrich it with AI."""

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
        on_event=None,
        response_language=None,
        provider=None,
        model_name=None,
    ):
        super().__init__(
            on_event=on_event, response_language=response_language,
            provider=provider, model_name=model_name,
        )
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
        report = copy.deepcopy(report)
        if status == "complete":
            report["status"] = "complete"
        elif status == "skipped_no_key":
            report["status"] = "local_only"
        else:
            report["status"] = "partial"
        report["ai_status"] = status
        report["ai_error"] = str(error or "")
        report["depth"] = "deep"
        if error:
            report["summary"] = (
                str(report.get("summary", "")).rstrip("。")
                + tr('。AI 深查未完成：{v0}', v0=error)
            ).strip()
        return report

    def _run(self):
        try:
            from inspection.engine import (
                merge_inspection_reports,
                run_local_inspection,
            )

            self._emit("progress", tr('正在执行本地规则'))
            local_report = run_local_inspection(
                self.current_version_json,
                report_type=self.report_type,
                request=self.request,
                confirmed_spec=self.confirmed_spec,
                plc_model=self.plc_model,
                base_version_id=self.base_version_id,
                trigger="manual",
                depth="deep" if self.deep else "basic",
            )
            self._emit("local", local_report)
            if not self.deep:
                return local_report

            try:
                if self.provider is None and not self._api_key_available():
                    partial = self._mark_ai(
                        local_report,
                        "skipped_no_key",
                        tr('未配置 API Key；已保留本地检查结果。'),
                    )
                    return partial

                self._emit("progress", tr('正在进行多角色深度评审'))
                from application.model_workflows import run_multi_agent_specialist
                from agent_runtime.multi_agent import DeterministicMultiAgentSupervisor

                program = self.program_ir
                if not isinstance(program, dict):
                    program = build_plc_ir(
                        self.current_version_json,
                        plc_model=self.plc_model,
                        revision=1,
                        confirmed_spec=self.confirmed_spec,
                    )

                def run_specialist(role, payload):
                    self._emit("progress", tr('正在检查程序逻辑') if role == "reviewer" else tr('正在复核扫描与时序'))
                    return model_call(run_multi_agent_specialist,
                        role,
                        payload,
                        effort=self.effort,
                        raise_errors=True,
                    )

                result = DeterministicMultiAgentSupervisor(
                    run_specialist
                ).review_program(
                    program,
                    project_id=self.project_id or self.task_id,
                    version_id=self.base_version_id,
                    request=self.request,
                    local_report=local_report,
                    confirmed_spec=self.confirmed_spec,
                )
                merged = local_report
                for ai_report in result["reports"]:
                    merged = merge_inspection_reports(merged, ai_report)
                merged["multi_agent"] = result["audit"]
                merged = self._mark_ai(merged, "complete")
                return merged
            except Exception as error:
                partial = self._mark_ai(local_report, "failed", str(error))
                return partial
        except Exception as error:
            raise WorkflowError(str(error))

    @staticmethod
    def _api_key_available():
        try:
            config = load_full_config()
            return bool(get_api_key(config))
        except Exception:
            return False

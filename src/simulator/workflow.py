"""User-approved, version-bound GX Works2 import and simulator-test workflow."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Mapping, Optional

from plc.ir import canonical_sha256

from .planning import normalize_generated_test_suite
from .service import SimulatorRegressionService
from .verification import execution_binding


class SimulatorWorkflowError(RuntimeError):
    pass


def _result_payload(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise SimulatorWorkflowError("GX Works2 导入服务返回了无效结果。")
    code = payload.get("error_code")
    if hasattr(code, "value"):
        payload["error_code"] = code.value
    payload["success"] = bool(payload.get("success"))
    return payload


class SimulatorVersionWorkflowService:
    """Import the selected version, prepare Simulator2, run, and persist trace."""

    def __init__(
        self,
        store,
        *,
        importer: Callable[..., Any],
        preparer,
        backend=None,
    ):
        self.store = store
        self.importer = importer
        self.preparer = preparer
        self.backend = backend

    @staticmethod
    def _emit(progress: Optional[Callable[[str, str], None]], stage, message):
        if progress:
            progress(stage, message)

    def _validate_plan(self, project_id, version_id, plan):
        if not isinstance(plan, Mapping):
            raise SimulatorWorkflowError("测试方案无效。")
        binding = plan.get("binding") or {}
        if binding.get("project_id") != project_id or binding.get("version_id") != version_id:
            raise SimulatorWorkflowError("测试方案不属于当前项目版本。")
        project = self.store.get_project(project_id)
        if not project or project.get("active_version_id") != version_id:
            raise SimulatorWorkflowError("只能测试当前启用版本。")
        version = self.store.get_version(project_id, version_id)
        program = self.store.load_program_ir(project_id, version_id)
        if not isinstance(version, Mapping) or not isinstance(program, Mapping):
            raise SimulatorWorkflowError("当前版本没有可测试的 PLC IR。")
        ir_sha256 = canonical_sha256(program)
        if (
            binding.get("revision") != program.get("revision")
            or binding.get("ir_sha256") != ir_sha256
        ):
            raise SimulatorWorkflowError("测试方案已过期，请基于当前版本重新生成。")
        suite = normalize_generated_test_suite(plan.get("suite"), program)
        return version, program, suite

    def _persist_unavailable(self, project_id, version_id, suite, preparation, binding):
        result = {
            "schema_version": 1,
            "name": suite["name"],
            "plc_model": suite["plc_model"],
            "status": "unavailable",
            "passed": False,
            "counts": {
                "passed": 0,
                "failed": 0,
                "error": 0,
                "unavailable": 1,
            },
            "test_count": len(suite["tests"]),
            "attempted_count": 0,
            "executed_count": 0,
            "not_executed_count": len(suite["tests"]),
            "backend_kinds": [],
            "results": [],
            "error": preparation.message,
        }
        record = self.store.save_simulator_run(
            project_id, version_id, suite, result, execution_snapshot=binding
        )
        return {
            "status": "unavailable",
            "message": preparation.message,
            "stop": {},
            "import": {},
            "execution": {
                "record": record,
                "result": result,
                "preparation": preparation.to_dict(),
                "verification": record["verification"],
            },
        }

    def run_approved_plan(
        self,
        project_id,
        version_id,
        plan,
        *,
        progress=None,
        test_progress=None,
    ):
        version, _program, suite = self._validate_plan(project_id, version_id, plan)
        binding = execution_binding(project_id, version_id, _program, suite)
        artifacts = version.get("artifacts") or {}
        version_dir = self.store.version_dir(project_id, version_id)
        program_csv = version_dir / str(artifacts.get("program_csv") or "")
        comment_csv = version_dir / str(artifacts.get("comment_csv") or "")
        if not program_csv.is_file() or not comment_csv.is_file():
            raise SimulatorWorkflowError("当前版本缺少 GX Works2 程序或注释 CSV。")

        preflight = getattr(self.preparer, "preflight", None)
        if callable(preflight):
            checked = preflight(progress=progress)
            if not checked.success:
                return self._persist_unavailable(
                    project_id, version_id, suite, checked, binding
                )

        self._emit(progress, "stop_simulator", "正在确认 Simulator2 已停止…")
        stopped = self.preparer.stop_if_running(progress=progress)
        if not stopped.success:
            return {
                "status": "prepare_failed",
                "message": stopped.message,
                "stop": stopped.to_dict(),
                "import": {},
                "execution": {},
            }

        self._emit(progress, "import", "正在导入当前程序和软元件注释…")
        imported = _result_payload(
            self.importer(
                program_csv,
                comment_csv_path=comment_csv,
                start_if_needed=False,
                progress=progress,
                import_context={
                    "project_id": project_id,
                    "version_id": version_id,
                    "revision": version.get("revision"),
                    "ir_sha256": version.get("ir_sha256"),
                    "simulator_phase": "approved_test",
                },
            )
        )
        if not imported.get("success") or imported.get("error_code"):
            return {
                "status": "import_failed",
                "message": str(imported.get("message") or "程序未能完整导入 GX Works2。"),
                "stop": stopped.to_dict(),
                "import": imported,
                "execution": {},
            }

        self._emit(progress, "start_simulator", "正在准备 GX Simulator2…")
        service = SimulatorRegressionService(
            self.store,
            backend=self.backend,
            preparer=self.preparer,
        )
        execution = service.run_version_suite(
            project_id,
            version_id,
            suite,
            progress=progress,
            test_progress=test_progress,
            expected_binding=binding,
        )
        result = execution.get("result") or {}
        status = str(result.get("status") or "error")
        messages = {
            "passed": "全部仿真测试已通过。",
            "failed": "仿真发现逻辑不符合测试期望，失败证据已保存。",
            "unavailable": "仿真环境尚未就绪，环境证据已保存。",
            "error": "仿真测试执行出错，运行证据已保存。",
        }
        message = messages.get(status, "仿真测试已结束。")
        if status == "unavailable":
            preparation = execution.get("preparation") or {}
            message = str(
                preparation.get("message")
                or result.get("error")
                or message
            )

        final_stop = {}
        preparation = execution.get("preparation") or {}
        if bool(preparation.get("success")):
            self._emit(
                progress,
                "stop_after_tests",
                "测试完成，正在自动停止 GX Simulator2…",
            )
            stopped_after = self.preparer.stop_if_running(progress=progress)
            final_stop = stopped_after.to_dict()
            if stopped_after.success:
                message += " GX Simulator2 已自动停止。"
            else:
                message += f" GX Simulator2 自动停止失败：{stopped_after.message}"
        self._emit(progress, "complete", message)
        return {
            "status": status,
            "message": message,
            "stop": stopped.to_dict(),
            "final_stop": final_stop,
            "import": imported,
            "execution": copy.deepcopy(execution),
        }


__all__ = ["SimulatorVersionWorkflowService", "SimulatorWorkflowError"]

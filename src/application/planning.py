"""Version-bound test and evidence/debug planning; execution requires approval."""
import copy
from contextlib import nullcontext

from application.base import Workflow, WorkflowError, model_call
from shared.i18n import tr


class EvidenceDebugPlanWorkflow(Workflow):
    """Build a version-bound diagnosis and patch without executing it.

    A queued caller supplies a snapshot store for the deterministic plan builder
    and a write lock/before_save check to reject changes before persistence.
    """

    def __init__(
        self,
        task_id,
        store,
        project_id,
        base_version_id,
        run_id,
        *,
        effort=None,
        on_event=None,
        response_language=None,
        provider=None,
        model_name=None,
        program_ir=None,
        saved_run=None,
        before_save=None,
        write_lock=None,
    ):
        super().__init__(
            on_event=on_event, response_language=response_language,
            provider=provider, model_name=model_name,
        )
        self.task_id = task_id
        self.store = store
        self.project_id = project_id
        self.base_version_id = base_version_id
        self.run_id = run_id
        self.effort = effort
        self.program_ir = copy.deepcopy(program_ir)
        self.saved_run = copy.deepcopy(saved_run)
        self.before_save = before_save
        self.write_lock = write_lock

    def _run(self):
        try:
            from application.model_api import (
                debug_evidence_diagnosis,
                debug_evidence_patch,
            )
            from application.debug_loop import (
                DebugPatchLoopService,
                build_failure_evidence,
            )
            from agent_runtime.specialists import (
                DEBUG_AGENT,
                PATCH_AGENT,
                DeterministicMultiAgentSupervisor,
            )

            program = (
                self.program_ir if self.program_ir is not None
                else self.store.load_program_ir(self.project_id, self.base_version_id)
            )
            saved_run = (
                self.saved_run if self.saved_run is not None
                else self.store.load_simulator_run(self.project_id, self.base_version_id, self.run_id)
            )
            if not isinstance(program, dict) or not isinstance(saved_run, dict):
                raise ValueError(tr('找不到当前版本的 PLC IR 或失败仿真记录。'))
            self._emit("progress", tr('正在整理失败轨迹和反向依赖'))
            evidence = build_failure_evidence(
                program,
                saved_run,
                project_id=self.project_id,
                version_id=self.base_version_id,
            )
            service = DebugPatchLoopService(self.store)

            def run_specialist(role, payload):
                if role == DEBUG_AGENT:
                    self._emit("progress", tr('AI 正在分析证据链'))
                    return model_call(debug_evidence_diagnosis,
                        payload["evidence"],
                        effort=self.effort,
                        raise_errors=True,
                    )
                if role == PATCH_AGENT:
                    self._emit("progress", tr('AI 正在生成局部网络补丁'))
                    return model_call(debug_evidence_patch,
                        payload["evidence"],
                        payload["diagnosis"],
                        effort=self.effort,
                        raise_errors=True,
                    )
                raise ValueError(tr('不支持的调试代理角色：{v0}', v0=role))

            plan = DeterministicMultiAgentSupervisor(
                run_specialist
            ).prepare_debug_plan(
                evidence=evidence,
                plan_builder=lambda diagnosis, patch: service.prepare_plan(
                    self.project_id,
                    self.base_version_id,
                    self.run_id,
                    diagnosis,
                    patch,
                ),
            )
            with self.write_lock or nullcontext():
                if self.before_save:
                    self.before_save()
                saved_agents = self.store.save_multi_agent_run(
                    self.project_id,
                    self.base_version_id,
                    plan["multi_agent"],
                )
                plan["multi_agent"]["run_id"] = saved_agents["run_id"]
                persisted = self.store.save_debug_plan(
                    self.project_id, self.base_version_id, plan
                )
            return persisted
        except Exception as error:
            raise WorkflowError(str(error))


class SimulatorTestPlanWorkflow(Workflow):
    """Generate and deterministically validate a version-bound Test DSL plan."""

    def __init__(
        self, task_id, store, project_id, version_id, *, effort=None,
        on_event=None, response_language=None, provider=None, model_name=None,
        program_ir=None, before_save=None, write_lock=None,
    ):
        super().__init__(
            on_event=on_event, response_language=response_language,
            provider=provider, model_name=model_name,
        )
        self.task_id = task_id
        self.store = store
        self.project_id = project_id
        self.version_id = version_id
        self.effort = effort
        self.program_ir = copy.deepcopy(program_ir)
        self.before_save = before_save
        self.write_lock = write_lock

    def _run(self):
        try:
            from application.model_api import generate_simulator_test_suite
            from simulator.planning import (
                build_test_generation_context,
                normalize_generated_test_suite,
            )

            program = (
                self.program_ir if self.program_ir is not None
                else self.store.load_program_ir(self.project_id, self.version_id)
            )
            if not isinstance(program, dict):
                raise ValueError(tr('当前版本没有可用于生成测试的 PLC IR。'))
            self._emit("progress", tr('正在整理程序行为和 I/O'))
            context = build_test_generation_context(program)
            self._emit("progress", tr('AI 正在生成仿真测试方案'))
            candidate = model_call(generate_simulator_test_suite,
                context,
                effort=self.effort,
                raise_errors=True,
                on_reasoning_chunk=lambda token: self._emit("reasoning", token),
                on_content_chunk=lambda token: self._emit("content", token),
                on_progress=lambda message: self._emit("progress", message),
            )
            self._emit("progress", tr('正在解析模型输出：规范化测试步骤与时间约束'))
            suite = normalize_generated_test_suite(candidate, program)
            self._emit("progress", tr('正在解析模型输出：保存版本绑定测试方案'))
            with self.write_lock or nullcontext():
                if self.before_save:
                    self.before_save()
                persisted = self.store.save_simulator_test_plan(
                    self.project_id,
                    self.version_id,
                    suite,
                    source="ai",
                )
            return persisted
        except Exception as error:
            raise WorkflowError(str(error))

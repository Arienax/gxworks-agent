from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from application.execution import read_gx_environment
from application.workbench import WorkbenchService
from application.workspace import ConflictError, atomic_json
from shared.tracing import load_operator_actions


ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "web/src/features/JobProgress.tsx").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "web/src/features/ProjectToolbar.tsx").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "web/src/workbench/useProjectWorkspace.ts").read_text(encoding="utf-8")
APP = (ROOT / "web/src/workbench/useWorkbenchController.tsx").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "src/application/proposal_workflow.py").read_text(encoding="utf-8")


def test_gx_environment_probe_has_deterministic_shape():
    result = read_gx_environment()
    assert result["status"] in {"ready", "unavailable"}
    assert isinstance(result["gx_works2_running"], bool)
    assert isinstance(result["gx_works2_installed"], bool)
    assert isinstance(result["project_open"], bool)
    assert result["desktop_execution_required"] is True


def test_execution_proposal_preflights_gx_without_model_path():
    block = WORKBENCH.split("    def create_execution(self, command):", 1)[1].split(
        "    def decide(self, proposal_id", 1
    )[0]
    assert "read_gx_environment()" in block
    assert 'command["action"] == "gx_import"' in block
    assert "model_factory" not in block


def test_deterministic_execution_jobs_never_render_fake_model_waiting_ui():
    assert 'const modelDriven = !["execution", "gx_read", "gx_inspect"].includes(job.kind);' in PROGRESS
    assert "{showModelPreview && <details" in PROGRESS
    assert "executionFailed" in PROGRESS
    assert "GX 执行未完成，请检查 GX Works2 状态。" in PROGRESS


def test_execution_failure_is_not_presented_as_completed():
    assert 'currentJob?.kind === "execution"' in APP
    assert '["failed", "interrupted", "conflict"].includes(String(currentJob.result?.status || ""))' in APP


def test_toolbar_contains_confirmed_project_delete():
    assert '`/projects/${encodeURIComponent(deletedId)}`' in WORKSPACE
    assert '"DELETE"' in WORKSPACE
    assert "window.confirm" in TOOLBAR
    assert 't(deleting ? "正在删除…" : "删除项目")' in TOOLBAR
    assert "window.location" not in TOOLBAR


@pytest.mark.parametrize("already_deleted", [False, True])
def test_workbench_delete_project_removes_managed_project_without_model(tmp_path, already_deleted):
    model_calls = []

    def forbidden_model():
        model_calls.append(True)
        raise AssertionError("Deleting a project must not initialize a model")

    workspace = tmp_path / "workspace"
    service = WorkbenchService(
        workspace,
        tmp_path / "state",
        model_factory=forbidden_model,
    )
    service.start()
    try:
        project = service.create_project(name="delete-me")
        project_id = project["id"]
        project_dir = service.store.project_dir(project_id)
        assert project_dir.is_dir()

        gate = Event()
        def pending(_ctx):
            gate.wait(5)
        request_id = "delete-project-job"
        job = service.jobs.submit("analysis", {"project_id": project_id, "start_conversation": True},
                                  pending, request_id=request_id)
        job_id = job["id"]
        try:
            with pytest.raises(ConflictError, match="active jobs"):
                service.delete_project(project_id)
            assert project_dir.is_dir()
        finally:
            gate.set()
            service.jobs._futures[job_id].result(timeout=5)

        kept = service.create_project(name="keep-me")["id"]
        other = service.jobs.submit("analysis", {"project_id": kept, "start_conversation": True}, lambda _ctx: None)
        service.jobs._futures[other["id"]].result(timeout=5)
        state = service.state_dir
        output = state / "outputs" / (job_id + ".json")
        command = state / "job_commands" / (sha256(request_id.encode()).hexdigest() + ".json")
        staging = state / "staging" / job_id
        staging.mkdir(parents=True)
        (staging / "candidate.json").write_text("{}", encoding="utf-8")
        atomic_json(output, {"status": "completed"})
        atomic_json(command, {"job_id": job_id})
        diagnostic = state / "diagnostics" / (job_id + ".jsonl")
        assert diagnostic.is_file()
        if already_deleted:
            service.store.delete_project(project_id)

        result = service.delete_project(project_id)

        assert result == {"deleted": True, "project_id": project_id}
        assert not project_dir.exists()
        assert project_id not in {item["id"] for item in service.projects.list_projects()}
        assert service.jobs.list(project_id) == []
        assert [entry["id"] for entry in service.jobs.list(kept)] == [other["id"]]
        assert not any(path.exists() for path in (output, command, staging, diagnostic))
        assert load_operator_actions(state, project_id=project_id) == []
        assert [row["job_id"] for row in load_operator_actions(state, project_id=kept)] == [other["id"]]
        assert model_calls == []
    finally:
        service.close()

from pathlib import Path

from application.execution import read_gx_environment
from application.workbench import WorkbenchService


ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "web/src/features/JobProgress.tsx").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "web/src/features/ProjectToolbar.tsx").read_text(encoding="utf-8")
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
    assert '`/projects/${encodeURIComponent(pid)}`' in TOOLBAR
    assert '"DELETE"' in TOOLBAR
    assert "window.confirm" in TOOLBAR
    assert 't(deleting ? "正在删除…" : "删除项目")' in TOOLBAR


def test_workbench_delete_project_removes_managed_project_without_model(tmp_path):
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

        result = service.delete_project(project_id)

        assert result == {"deleted": True, "project_id": project_id}
        assert not project_dir.exists()
        assert project_id not in {item["id"] for item in service.projects.list_projects()}
        assert model_calls == []
    finally:
        service.close()

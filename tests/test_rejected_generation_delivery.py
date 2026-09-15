import json

from application.generation_repair import GenerationValidationError
from application.jobs import JobManager
from application.projects import ProjectService
from application.proposals import ProposalService
from application.rejected_generation_preview import (
    materialize_rejected_preview,
    recover_compact_for_validation,
)
from application.workspace import WorkspaceWriterLock
from session_store import SessionStore


BROKEN_COMPACT = (
    '{"r":['
    '{"b":[{"i":["NO M100",{"or":['
    '[">= D200 K1","<= D200 K3","> D220 D102"],'
    '[">= D200 K4","<= D200 K6","> D220 D104"],'
    '[">= D200 K7","<= D200 K9","> D220 D106]]}],'
    '"o":["COIL M200"]}]},'
    '{"b":[{"i":["NO M200"],"o":["RST M100","MOV K0 D200","MOV K0 D220"]}]}'
    ']}'
)


def test_missing_compact_quote_is_recovered_without_dropping_rungs():
    ladder, info = recover_compact_for_validation(BROKEN_COMPACT)

    assert info["syntax_quote_repairs"] == 1
    assert len(ladder["rungs"]) == 2
    release = ladder["rungs"][0]["branches"][0]
    parallel = release["inputs"][1]
    assert parallel["type"] == "parallel_block"
    assert parallel["branches"][2][-1]["expression"] == "> D220 D106"
    assert release["outputs"] == [{"type": "COIL", "address": "M200"}]


def test_rejected_candidate_still_materializes_svg_ir_and_both_gx_csvs(tmp_path):
    metadata = materialize_rejected_preview(
        BROKEN_COMPACT,
        tmp_path,
        plc_model="FX3U",
        program_name="MAIN",
        revision=3,
    )

    assert metadata["diagnostic_only"] is True
    assert metadata["validation"]["status"] == "invalid_candidate"
    assert metadata["recovery"]["syntax_quote_repairs"] == 1
    for key in ("json", "ir", "svg", "program_csv", "comment_csv"):
        path = tmp_path / metadata["artifacts"][key]
        assert path.is_file() and path.stat().st_size > 0
    assert "<svg" in (tmp_path / metadata["artifacts"]["svg"]).read_text(encoding="utf-8")


def test_generation_validation_failure_becomes_active_diagnostic_version_with_gx_csv(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("Rejected candidate")
    store.set_confirmed_spec(project["id"], {
        "summary": "Preserve the model candidate even if validation fails",
        "io_table": [],
        "parameters": [],
    })
    frozen_project = store.get_project(project["id"])
    state = tmp_path / "state"

    lock = WorkspaceWriterLock(store.base_dir, tmp_path / "locks").acquire()
    manager = JobManager(state, lock, max_workers=1)
    try:
        snapshot = {
            "project_id": project["id"],
            "project": frozen_project,
            "version": None,
            "version_id": None,
            "program_ir": None,
        }

        def worker(ctx):
            staging = state / "staging" / ctx.job_id
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "repair_candidate.json").write_text(BROKEN_COMPACT, encoding="utf-8")
            error = json.JSONDecodeError("missing compact token quote", BROKEN_COMPACT, 164)
            raise GenerationValidationError(
                [error], attempts=0, max_attempts=0,
                language="zh-CN", stop_reason="final_validation",
            )

        submitted = manager.submit("generation", snapshot, worker, request_id="invalid-delivery")
        manager._futures[submitted["id"]].result(timeout=10)
        completed = manager.get(submitted["id"])

        assert completed["status"] == "completed"
        assert completed["error_code"] is None
        assert completed["result"]["status"] == "saved_invalid"
        version_id = completed["result"]["version_id"]

        refreshed = SessionStore(base_dir=store.base_dir, create=False)
        saved = refreshed.get_version(project["id"], version_id)
        assert refreshed.get_project(project["id"])["active_version_id"] == version_id
        assert saved["lifecycle_status"] == "diagnostic"
        assert saved["validation"]["status"] == "invalid_candidate"
        assert saved["validation_profile"] == "generation_structural"
        assert {"json", "ir", "svg", "program_csv", "comment_csv"}.issubset(saved["artifacts"])
        for filename in saved["artifacts"].values():
            assert (refreshed.version_dir(project["id"], version_id) / filename).is_file()

        # The ordinary project service can still open the internally consistent
        # diagnostic IR, while the user-facing validation state remains invalid.
        service = ProjectService(refreshed.base_dir)
        assert service.program(project["id"], version_id)["kind"] == "plc_program_ir"
        assert service.capabilities(saved)["operations"]["gx_import"] is True

        # Creating a GX-import proposal must not be blocked by the local failure.
        proposals = ProposalService(refreshed, state, lock)
        proposal = proposals.create(
            "gx_import", project["id"],
            {"project_id": project["id"], "version_id": version_id},
            base_version_id=version_id, request_id="send-invalid-csv",
        )
        assert proposal["status"] == "pending"
        assert proposal["action"] == "gx_import"
    finally:
        manager.shutdown(wait=True)
        lock.release()

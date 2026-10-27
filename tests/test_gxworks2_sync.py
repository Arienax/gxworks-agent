import csv
import json
import os
import re
from pathlib import Path

import pytest

from gxworks2.csv_export import generate_gx_works2_csv
from gxworks2.csv_importer import (
    materialize_gxworks2_version,
    parse_gxworks2_csv,
)
from gxworks2.import_service import ImportService
from gxworks2.csv_manager import CSVManager, GXWORKS2_COMMENT_HEADER, GXWORKS2_HEADER
from gxworks2.diagnostics import (
    GXAutomationError,
    describe_exception,
    exception_details,
)
from gxworks2.models import (
    GXSyncErrorCode,
    GXWorks2Session,
    SyncResult,
    SyncStatus,
)
from gxworks2.sync_service import ERROR_SUGGESTIONS, GXWorks2SyncService
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation


def _write_program(path, output="Y000", condition="X000"):
    rows = [
        ["MAIN - 副本"],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        ["0", "起保停", "LD", condition, "", "", ""],
        ["1", "", "OR", output, "", "", ""],
        ["2", "", "ANI", "X001", "", "", ""],
        ["3", "", "OUT", output, "", "", "电机"],
        ["4", "", "END", "", "", "", ""],
    ]
    with Path(path).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)


def _write_comments(path, comments=None):
    rows = [["COMMENT - 副本"], GXWORKS2_COMMENT_HEADER]
    rows.extend(comments or [["X000", "启动"], ["X001", "停止"], ["Y000", "电机"]])
    with Path(path).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)


class _Finder:
    def __init__(self):
        self.session = GXWorks2Session(
            process_id=10,
            window_handle=20,
            title="Fixture - GX Works2",
            executable="GD2.exe",
            project_open=True,
            project_name="Fixture",
            project_state_known=True,
        )

    def find_running(self):
        return self.session


class _Automation:
    def __init__(self, program, comments):
        self.program = Path(program)
        self.comments = Path(comments)

    def inspect_project(self, _session):
        return {
            "automation_available": True,
            "project_open": True,
            "program_ready": True,
            "project_name": "Fixture",
        }

    def export_current_program(self, _session, destination):
        Path(destination).write_bytes(self.program.read_bytes())

    def export_current_comments(self, _session, destination):
        Path(destination).write_bytes(self.comments.read_bytes())


class _RoundtripAutomation(_Automation):
    def __init__(self, program, comments, *, apply_import=True):
        super().__init__(program, comments)
        self.program_bytes = self.program.read_bytes()
        self.comment_bytes = self.comments.read_bytes()
        self.apply_import = apply_import
        self.saved = 0

    def export_current_program(self, _session, destination):
        Path(destination).write_bytes(self.program_bytes)

    def export_current_comments(self, _session, destination):
        Path(destination).write_bytes(self.comment_bytes)

    def import_program_csv(self, _session, source):
        if self.apply_import:
            self.program_bytes = Path(source).read_bytes()
        return {"success": True, "message": "导入完成"}

    def import_comments_csv(self, _session, source):
        if self.apply_import:
            self.comment_bytes = Path(source).read_bytes()
        return {"success": True, "message": "注释导入完成"}

    def save_project(self, _session):
        self.saved += 1
        return {"success": True, "save_required": False, "message": "已保存"}


def _service(tmp_path, automation):
    return GXWorks2SyncService(
        _Finder(),
        automation,
        CSVManager(),
        tmp_path / "backups",
    )


def test_comment_semantic_hash_ignores_document_title_and_row_order(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_comments(first, [["X000", "启动"], ["Y000", "电机"]])
    _write_comments(second, [["Y000", "电机"], ["X000", "启动"]])
    rows = list(csv.reader(second.open(encoding="utf-16"), delimiter="\t"))
    rows[0][0] = "生产线工程"
    with second.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)

    manager = CSVManager()
    assert manager.comments_semantic_sha256(first) == manager.comments_semantic_sha256(second)


def test_native_program_roundtrips_through_ladder_ir(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    output = tmp_path / "version"
    _write_program(source)
    _write_comments(comments)

    parsed = parse_gxworks2_csv(source, comments)
    metadata = materialize_gxworks2_version(source, comments, output, revision=7)

    assert len(parsed.ladder["rungs"]) == 1
    inputs = parsed.ladder["rungs"][0]["branches"][0]["inputs"]
    assert inputs[0]["type"] == "parallel_block"
    assert inputs[1]["type"] == "NC"
    assert metadata["revision"] == 7
    assert metadata["source_kind"] == "gxworks2_sync"
    assert CSVManager().program_semantic_sha256(output / "program.csv") == (
        parsed.source_program_semantic_sha256
    )


def test_state_machine_mps_branches_roundtrip_without_rewriting_logic(tmp_path):
    program = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    output = tmp_path / "version"
    ladder = {
        "device_comments": {"D0": "步骤", "M8000": "运行", "T0": "到时"},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "步骤一",
                "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": ""},
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "M8000", "label": ""}],
                        "outputs": [{"type": "TIMER", "address": "T0", "value": "K10", "label": ""}],
                    },
                    {
                        "branch_id": 2,
                        "y_offset_level": 1,
                        "inputs": [
                            {
                                "type": "parallel_block",
                                "branches": [
                                    [{"type": "NO", "address": "T0", "label": ""}],
                                    [{"type": "NO", "address": "M8000", "label": ""}],
                                ],
                            }
                        ],
                        "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K2", "D0"], "label": ""}],
                    },
                ],
            }
        ],
    }
    assert generate_gx_works2_csv(ladder, program, comments)
    source_hash = CSVManager().program_semantic_sha256(program)

    # The compatibility generator writes no CPU into the PLC信息 line, so the
    # caller names the target instead of the materializer assuming FX3U.
    metadata = materialize_gxworks2_version(program, comments, output, plc_model="FX3U")

    assert metadata["source_program_semantic_sha256"] == source_hash
    assert CSVManager().program_semantic_sha256(output / "program.csv") == source_hash


def test_sync_inspection_detects_each_three_way_state(tmp_path):
    base_program = tmp_path / "base.csv"
    app_program = tmp_path / "app.csv"
    gx_program = tmp_path / "gx.csv"
    comments = tmp_path / "comments.csv"
    _write_program(base_program)
    _write_program(app_program)
    _write_program(gx_program)
    _write_comments(comments)
    automation = _Automation(gx_program, comments)
    service = _service(tmp_path, automation)
    context = {"project_id": "p1", "version_id": "v1"}

    first = service.inspect(app_program, comments, import_context=context)
    assert first.status == SyncStatus.SYNCED

    _write_program(gx_program, output="Y001")
    gx_only = service.inspect(app_program, comments, import_context=context)
    assert gx_only.status == SyncStatus.NEEDS_PULL

    # Restore the common snapshot, then change only the application side.
    _write_program(gx_program)
    assert service.inspect(app_program, comments, import_context=context).status == SyncStatus.SYNCED
    _write_program(app_program, output="Y002")
    app_only = service.inspect(app_program, comments, import_context=context)
    assert app_only.status == SyncStatus.NEEDS_PUSH

    _write_program(gx_program, output="Y003")
    both = service.inspect(app_program, comments, import_context=context)
    assert both.status == SyncStatus.CONFLICT
    assert both.details["diff"]["changed_instruction_count"] > 0


def test_first_sync_with_different_programs_requires_source_choice(tmp_path):
    app = tmp_path / "app.csv"
    gx = tmp_path / "gx.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app, output="Y000")
    _write_program(gx, output="Y001")
    _write_comments(comments)
    result = _service(tmp_path, _Automation(gx, comments)).inspect(app, comments)

    assert result.success
    assert result.status == SyncStatus.UNBOUND
    assert Path(result.exported_program_path).is_file()


def test_csv_push_imports_program_comments_and_saves_without_export(tmp_path, monkeypatch):
    current, target = tmp_path / "current.csv", tmp_path / "target.csv"
    comments = tmp_path / "comments.csv"
    _write_program(current, output="Y000")
    _write_program(target, output="Y001")
    _write_comments(comments)
    automation = _RoundtripAutomation(current, comments)
    saved = tmp_path / "existing.gxw"
    saved.write_bytes(b"saved GXW")
    inspect = automation.inspect_project
    monkeypatch.setattr(automation, "inspect_project", lambda session: {**inspect(session), "project_path": str(saved)})
    def forbidden(*args, **kwargs):
        pytest.fail("A push must not export CSV before or after importing")
    monkeypatch.setattr(automation, "export_current_program", forbidden)
    monkeypatch.setattr(automation, "export_current_comments", forbidden)
    service = ImportService(_Finder(), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(target, comment_csv_path=comments,
        synchronize_comments=True, save_project=True)
    assert result.success and automation.saved == 1
    assert Path(result.backup_path).read_bytes() == b"saved GXW"
    assert automation.program_bytes == target.read_bytes()
    assert automation.comment_bytes == comments.read_bytes()
    assert result.details["verified_program_path"] == result.details["verified_comment_path"] == ""
    assert result.details["version_protection"]["roundtrip_verified"] is False


@pytest.mark.parametrize("guard", [
    {"verify_roundtrip": True},
    {"rollback_expected_current_sha256": "a" * 64},
    {"expected_current_comment_sha256": "b" * 64},
])
def test_import_requires_a_separate_request_for_readback_or_rollback(tmp_path, guard):
    current = tmp_path / "current.csv"
    comments = tmp_path / "comments.csv"
    _write_program(current)
    _write_comments(comments)
    automation = _RoundtripAutomation(current, comments)
    service = ImportService(_Finder(), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(current, comment_csv_path=comments, **guard)
    assert not result.success and result.stage == "validate_request"
    assert result.error_code.value == "invalid_request"
    assert "单独请求" in result.message
    assert not service.backup_root.exists() and automation.saved == 0


def test_legacy_program_only_baseline_does_not_absorb_unresolved_conflict(tmp_path):
    base = tmp_path / "base.csv"
    app = tmp_path / "app.csv"
    gx = tmp_path / "gx.csv"
    comments = tmp_path / "comments.csv"
    _write_program(base, output="Y000")
    _write_program(app, output="Y001")
    _write_program(gx, output="Y002")
    _write_comments(comments)
    automation = _Automation(gx, comments)
    service = _service(tmp_path, automation)
    identity = service.baseline_store.project_identity(
        _Finder().session, project_name="Fixture"
    )
    service.baseline_store.save(
        identity,
        program_semantic_sha256=CSVManager().program_semantic_sha256(base),
        program_file_sha256=CSVManager().file_sha256(base),
    )
    baseline_path = service.baseline_store.path_for(identity)
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    for key in list(payload):
        if key.startswith("app_") or key.startswith("gx_") or key == "comments_semantic_sha256":
            payload.pop(key, None)
    baseline_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert service.inspect(app, comments).status == SyncStatus.CONFLICT
    assert service.inspect(app, comments).status == SyncStatus.CONFLICT


def test_same_gx_project_name_cannot_silently_switch_plc_ai_projects(tmp_path):
    gx = tmp_path / "gx.csv"
    first_app = tmp_path / "first.csv"
    second_app = tmp_path / "second.csv"
    comments = tmp_path / "comments.csv"
    _write_program(gx, output="Y000")
    _write_program(first_app, output="Y000")
    _write_program(second_app, output="Y001")
    _write_comments(comments)
    service = _service(tmp_path, _Automation(gx, comments))

    first = service.inspect(
        first_app,
        comments,
        import_context={"project_id": "project-a", "version_id": "v1"},
    )
    second = service.inspect(
        second_app,
        comments,
        import_context={"project_id": "project-b", "version_id": "v1"},
    )

    assert first.status == SyncStatus.SYNCED
    assert second.status == SyncStatus.UNBOUND
    assert second.details["binding_mismatch"] is True


def _fast_sync_service(tmp_path, automation, finder=None):
    return GXWorks2SyncService(
        finder or _Finder(),
        automation,
        CSVManager(),
        tmp_path / "diagnostic-backups",
        export_validation_timeout=0.02,
        export_validation_poll_interval=0.01,
        export_retry_delay=0,
    )


class _RetryAutomation(_Automation):
    def __init__(
        self,
        program,
        comments,
        *,
        program_failures=0,
        comment_failures=0,
        error_factory=RuntimeError,
    ):
        super().__init__(program, comments)
        self.program_failures = int(program_failures)
        self.comment_failures = int(comment_failures)
        self.error_factory = error_factory
        self.inspect_calls = 0
        self.program_calls = 0
        self.comment_calls = 0
        self.recovery_calls = 0

    def inspect_project(self, session):
        self.inspect_calls += 1
        return super().inspect_project(session)

    def export_current_program(self, session, destination):
        self.program_calls += 1
        if self.program_calls <= self.program_failures:
            raise self.error_factory()
        return super().export_current_program(session, destination)

    def export_current_comments(self, session, destination):
        self.comment_calls += 1
        if self.comment_calls <= self.comment_failures:
            raise self.error_factory()
        return super().export_current_comments(session, destination)

    def prepare_export_retry(self, _session):
        self.recovery_calls += 1
        return {"dismissed_dialogs": ["CSV"], "main_activated": True}


def test_empty_exception_description_never_returns_an_empty_string():
    assert describe_exception(TimeoutError()) == "TimeoutError (TimeoutError())"
    assert describe_exception(RuntimeError()) == "RuntimeError (RuntimeError())"


def test_sync_result_serialization_keeps_success_and_adds_agent_fields():
    result = SyncResult(
        False,
        SyncStatus.ERROR,
        "failed",
        GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
        stage="wait_program_file_dialog",
        retryable=True,
    )

    payload = result.to_dict()

    assert payload["success"] is False
    assert payload["ok"] is False
    assert payload["stage"] == "wait_program_file_dialog"
    assert payload["retryable"] is True
    assert payload["error_code"] == "gx_file_dialog_timeout"


@pytest.mark.parametrize("code", list(GXSyncErrorCode))
def test_every_sync_error_code_has_stable_diagnostics(code):
    result = GXWorks2SyncService._error(
        "diagnostic",
        code,
        stage="diagnostic_stage",
        retryable=False,
    )

    assert result.error_code is code
    assert result.details["error_code"] == code.value
    assert result.details["stage"] == "diagnostic_stage"
    assert result.details["suggestion"] == ERROR_SUGGESTIONS[code]
    assert "exception_type" in result.details
    assert "exception_repr" in result.details
    assert "exception_message" in result.details


def test_transient_program_timeout_retries_the_complete_snapshot(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _RetryAutomation(
        app,
        comments,
        program_failures=1,
        error_factory=TimeoutError,
    )

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert result.success
    assert result.status == SyncStatus.SYNCED
    assert automation.program_calls == 2
    assert automation.comment_calls == 1
    assert automation.inspect_calls == 2
    assert automation.recovery_calls == 1
    assert result.details["export_attempt"] == 2
    assert result.details["export_attempts"][0]["error_code"] == "gx_uia_timeout"
    assert result.details["export_attempts"][0]["exception_type"] == "TimeoutError"


def test_transient_comment_failure_restarts_at_program_export(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _RetryAutomation(app, comments, comment_failures=1)

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert result.success
    assert automation.program_calls == 2
    assert automation.comment_calls == 2
    assert automation.recovery_calls == 1
    assert result.details["export_attempts"][0]["error_code"] == (
        "gx_comment_export_failed"
    )


def test_access_denied_is_diagnostic_and_is_not_automatically_retried(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _RetryAutomation(
        app,
        comments,
        program_failures=2,
        error_factory=lambda: PermissionError(13, "Access denied"),
    )

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_UIA_ACCESS_DENIED
    assert result.stage == "export_program"
    assert result.retryable is False
    assert automation.program_calls == 1
    assert automation.recovery_calls == 0
    assert result.details["exception_type"] == "PermissionError"


def test_empty_runtime_error_reports_type_after_both_attempts(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _RetryAutomation(app, comments, program_failures=2)

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED
    assert result.message == "无法导出GX Works2当前MAIN：RuntimeError (RuntimeError())"
    assert result.details["exception_type"] == "RuntimeError"
    assert result.details["exception_repr"] == "RuntimeError()"
    assert result.details["attempt"] == 2
    assert len(result.details["attempts"]) == 2


class _InvalidThenMissingAutomation(_Automation):
    def __init__(self, program, comments):
        super().__init__(program, comments)
        self.program_calls = 0
        self.recovery_calls = 0

    def export_current_program(self, _session, destination):
        self.program_calls += 1
        if self.program_calls == 1:
            Path(destination).write_bytes(b"invalid partial export")

    def prepare_export_retry(self, _session):
        self.recovery_calls += 1
        return {"dismissed_dialogs": [], "main_activated": True}


def test_retry_cannot_accept_the_previous_attempt_partial_file(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _InvalidThenMissingAutomation(app, comments)

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_PROGRAM_EXPORT_INVALID
    assert automation.program_calls == 2
    assert automation.recovery_calls == 1
    assert not Path(result.details["export_program_path"]).exists()
    assert "程序CSV文件不存在" in result.details["validation_errors"]


def test_invalid_comment_export_retries_the_program_and_comment_together(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)

    class _InvalidCommentsAutomation(_RetryAutomation):
        def export_current_comments(self, _session, destination):
            self.comment_calls += 1
            Path(destination).write_bytes(b"invalid comments")

    automation = _InvalidCommentsAutomation(app, comments)
    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_COMMENT_EXPORT_INVALID
    assert result.stage == "validate_comment_csv"
    assert automation.program_calls == 2
    assert automation.comment_calls == 2
    assert automation.recovery_calls == 1


def test_manifest_failure_is_separate_and_never_retried(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _RetryAutomation(app, comments)

    class _ManifestFailureManager(CSVManager):
        @staticmethod
        def write_checksum_manifest(_folder):
            raise OSError("manifest blocked")

    service = GXWorks2SyncService(
        _Finder(),
        automation,
        _ManifestFailureManager(),
        tmp_path / "manifest-backups",
        export_retry_delay=0,
    )
    result = service.inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_EXPORT_MANIFEST_FAILED
    assert result.stage == "write_manifest"
    assert result.retryable is False
    assert automation.program_calls == 1
    assert automation.comment_calls == 1
    assert automation.recovery_calls == 0


def test_project_precondition_is_manual_retry_only(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)
    automation = _Automation(app, comments)
    automation.inspect_project = lambda _session: {
        "automation_available": True,
        "project_open": False,
        "program_ready": False,
        "project_name": "Fixture",
    }

    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert not result.success
    assert result.error_code == GXSyncErrorCode.GX_PROJECT_NOT_OPEN
    assert result.stage == "check_project"
    assert result.retryable is True
    assert result.details["attempts"] == []


@pytest.mark.parametrize("program", ["MAIN", "FBD_MATRIX"])
def test_unready_program_message_uses_the_requested_source(tmp_path, program):
    automation = _Automation(tmp_path / "unused.csv", tmp_path / "comments.csv")
    automation.inspect_project = lambda _session: dict(
        automation_available=True, project_open=True, program_ready=False, project_name="Fixture")
    result = _fast_sync_service(tmp_path, automation).read_current_snapshot(
        import_context={"program_name": program}, save_project=False)
    assert not result.success and result.error_code == GXSyncErrorCode.GX_PROGRAM_NOT_READY
    assert result.stage == "check_program"
    assert result.message == f"GX Works2当前{program}程序不可读取，请先打开{program}。"


def test_baseline_read_and_write_failures_have_separate_codes(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)

    read_service = _fast_sync_service(tmp_path, _Automation(app, comments))
    read_service.baseline_store.load = lambda _identity: (_ for _ in ()).throw(
        OSError("read blocked")
    )
    read_result = read_service.inspect(app, comments)
    assert read_result.error_code == GXSyncErrorCode.GX_BASELINE_READ_FAILED
    assert read_result.stage == "compare"

    write_service = _fast_sync_service(tmp_path, _Automation(app, comments))
    write_service.baseline_store.save = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(OSError("write blocked"))
    )
    write_result = write_service.inspect(app, comments)
    assert write_result.error_code == GXSyncErrorCode.GX_BASELINE_WRITE_FAILED
    assert write_result.stage == "save_baseline"


def test_structured_automation_error_keeps_its_specific_stage(tmp_path):
    app = tmp_path / "app.csv"
    comments = tmp_path / "comments.csv"
    _write_program(app)
    _write_comments(comments)

    class _DialogTimeoutAutomation(_RetryAutomation):
        def export_current_program(self, _session, _destination):
            self.program_calls += 1
            raise GXAutomationError(
                GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
                "wait_program_file_dialog",
                "12.0秒内未检测到GX Works2文件选择窗口。",
                retryable=True,
                details={"timeout_seconds": 12.0},
            )

    automation = _DialogTimeoutAutomation(app, comments)
    result = _fast_sync_service(tmp_path, automation).inspect(app, comments)

    assert result.error_code == GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT
    assert result.stage == "wait_program_file_dialog"
    assert result.details["timeout_seconds"] == 12.0
    assert automation.program_calls == 2


def test_file_dialog_wait_raises_specific_timeout_with_actual_duration():
    automation = PywinautoGXWorks2UIAutomation(timeout=0.01)
    automation._legacy_dialog = lambda *_args, **_kwargs: None

    with pytest.raises(GXAutomationError) as captured:
        automation._wait_legacy_dialog(
            _Finder().session,
            re.compile("CSV"),
            timeout=0.01,
            failure_stage="wait_program_file_dialog",
        )

    error = captured.value
    assert error.code == GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT
    assert error.stage == "wait_program_file_dialog"
    assert error.retryable is True
    assert error.details["timeout_seconds"] == 0.01
    assert error.details["elapsed_seconds"] >= 0.01
    assert exception_details(error)["exception_type"] == "TimeoutError"


def test_project_inspection_preserves_empty_uia_timeout_details():
    automation = PywinautoGXWorks2UIAutomation(timeout=0.01)

    def fail_window(_session):
        raise TimeoutError()

    automation._main_window = fail_window
    state = automation.inspect_project(_Finder().session)

    assert state["automation_available"] is False
    assert state["error_code"] == GXSyncErrorCode.GX_UIA_TIMEOUT
    assert state["stage"] == "inspect_project"
    assert state["retryable"] is True
    assert state["exception_type"] == "TimeoutError"
    assert "TimeoutError" in state["message"]






def test_read_current_snapshot_needs_no_local_version(tmp_path):
    gx_program = tmp_path / "gx-bootstrap.csv"
    gx_comments = tmp_path / "gx-bootstrap-comments.csv"
    _write_program(gx_program, output="Y007")
    _write_comments(gx_comments, [["X000", "启动"], ["Y007", "已有输出"]])
    service = _service(tmp_path, _Automation(gx_program, gx_comments))

    result = service.read_current_snapshot(
        import_context={"project_id": "empty-project", "program_name": "MAIN"}
    )

    assert result.success
    assert result.details["bootstrap"] is True
    assert result.details["project_identity"]
    assert Path(result.exported_program_path).is_file()
    assert Path(result.exported_comment_path).is_file()
    assert CSVManager().program_semantic_sha256(result.exported_program_path) == (
        CSVManager().program_semantic_sha256(gx_program)
    )


@pytest.mark.parametrize("switch", [False, True])
def test_read_snapshot_pins_target_through_retry_and_does_not_save(tmp_path, switch):
    from dataclasses import replace
    from agent_runtime.tool_errors import ToolPreconditionError
    program, comments = tmp_path / "source.csv", tmp_path / "comments.csv"
    _write_program(program)
    _write_comments(comments)
    original = program.read_bytes(), comments.read_bytes()
    target = _Finder().session
    requests = []
    class Finder:
        def find_running(self, *, process_id):
            requests.append(process_id)
            return replace(target, window_handle=999) if switch and len(requests) > 1 else target
    class Automation(_RoundtripAutomation):
        def export_current_program(self, session, destination):
            assert session.process_id == target.process_id
            if len(requests) == 1:
                raise GXAutomationError(GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED, "export_program", "retry", retryable=True)
            super().export_current_program(session, destination)
    automation = Automation(program, comments)
    service = GXWorks2SyncService(Finder(), automation, CSVManager(), tmp_path / "exports", export_retry_delay=0)
    result = service.read_current_snapshot(target_session=target, save_project=False)
    assert result.success is (not switch)
    assert requests == [target.process_id, target.process_id]
    assert automation.saved == 0
    assert (program.read_bytes(), comments.read_bytes()) == original


def _write_ordered_export(program, comments):
    """A condition restarting after an output, with statements and a label."""
    rows = [
        ["MAIN"],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        ["", "网络说明", "", "", "", "", ""],
        ["1", "初始化", "LD", "M8002", "", "", ""],
        # MOV is five words, so the condition that follows it starts at step 7.
        ["2", "", "MOV", "K0", "", "", ""],
        ["", "", "", "D0", "", "", ""],
        ["7", "", "LD", "X000", "", "", "启动按钮"],
        ["8", "", "OUT", "Y000", "", "", ""],
        ["9", "", "ANI", "X001", "", "", ""],
        ["10", "", "OUT", "Y001", "", "", ""],
        ["11", "", "END", "", "", "", ""],
    ]
    with Path(program).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)
    comment_rows = [["COMMENT"], GXWORKS2_COMMENT_HEADER,
                    ["M8002", "初始脉冲"], ["X000", "启动"], ["Y000", "电机"]]
    with Path(comments).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\n").writerows(comment_rows)


def test_source_bound_csv_writer_reproduces_the_native_documents(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_ordered_export(program, comments)
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    assert snapshot["coverage"]["graph_preview_status"] == "partial"

    written_program, written_comments = tmp_path / "out" / "MAIN.csv", tmp_path / "out" / "COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, written_program, written_comments)
    assert report["validation"]["program"]["valid"] and report["validation"]["comments"]["valid"]
    assert report["instruction_count"] == 7
    # One instruction statement, one leading network statement and one label.
    assert report["statement_count"] == 1 and report["label_count"] == 1
    assert snapshot["raw_networks"][0]["statements"] == ["网络说明"]
    assert report["newlines"]["comments"] == "\n"
    assert [row["source_row"] for row in report["binding"]] == [5, 6, 8, 9, 10, 11, 12]
    assert report["width_findings"] == []

    verified = verify_gxworks2_csv_source(
        snapshot, program_csv_path=written_program, comment_csv_path=written_comments,
        source_program_csv_path=program, source_comment_csv_path=comments)
    assert verified["status"] == "passed", verified["mismatches"]
    assert {name: check["status"] for name, check in verified["checks"].items()} == {
        name: "passed" for name in (
            "format", "target_identity", "source_header", "program_semantics",
            "comment_semantics", "instruction_order", "operands", "step_labels",
            "statements", "labels", "network_notes", "comment_rows", "source_rows")}
    # An unedited re-serialization is byte-identical, including the comment CSV's
    # LF line endings and the operand continuation rows.
    assert verified["byte_identical"] == {"program": True, "comments": True}
    assert written_program.read_bytes() == program.read_bytes()
    assert written_comments.read_bytes() == comments.read_bytes()


def test_source_bound_csv_checks_catch_statements_labels_and_rows_not_just_the_hash(tmp_path):
    import copy
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_ordered_export(program, comments)
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")

    def edit_and_verify(change):
        edited = copy.deepcopy(snapshot)
        change(edited["raw_networks"][0]["raw_instructions"][0])
        written_program = tmp_path / "lbl" / "MAIN.csv"
        written_comments = tmp_path / "lbl" / "COMMENT.csv"
        write_gxworks2_csv_from_source(edited, written_program, written_comments)
        return verify_gxworks2_csv_source(
            snapshot, program_csv_path=written_program, comment_csv_path=written_comments,
            source_program_csv_path=program, source_comment_csv_path=comments)

    def set_label(instruction):
        instruction["label"] = "改名后的按钮"

    labelled = edit_and_verify(set_label)
    # The instruction sequence is unchanged, so an instruction hash alone would
    # have accepted this export.
    assert labelled["status"] == "failed"
    assert labelled["checks"]["program_semantics"]["status"] == "passed"
    assert labelled["checks"]["instruction_order"]["status"] == "passed"
    assert labelled["checks"]["labels"]["status"] == "failed"
    assert {item["check"] for item in labelled["mismatches"]} == {"labels"}
    assert labelled["byte_identical"] == {"program": False, "comments": True}

    def set_statement(instruction):
        instruction["statement"] = "另一条声明"

    stated = edit_and_verify(set_statement)
    assert stated["status"] == "failed"
    assert stated["checks"]["program_semantics"]["status"] == "passed"
    assert stated["checks"]["statements"]["status"] == "failed"
    assert "statements" in {item["check"] for item in stated["mismatches"]}


def test_strict_import_materializes_an_ordered_export_and_keeps_the_exporter_guard(tmp_path):
    import pytest as _pytest
    from gxworks2.native_export import GXNativeLoweringError
    from plc.ir import is_plc_ir

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_ordered_export(program, comments)
    output = tmp_path / "version"
    metadata = materialize_gxworks2_version(program, comments, output, revision=5)

    assert metadata["target_mode"] == "ladder"
    assert metadata["source_format"] == "instruction_sequence_v1"
    assert metadata["revision"] == 5
    assert metadata["st_status"] == "unavailable" and metadata["st_from_ir_sha256"] is None
    assert "st_from_ir" not in metadata["artifacts"]
    assert metadata["preservation"]["status"] == "passed"
    assert metadata["preservation"]["byte_identical"] == {"program": True, "comments": True}
    assert metadata["coverage"]["parse_status"] == "complete"
    assert metadata["coverage"]["graph_preview_status"] == "partial"
    assert (output / "program.csv").read_bytes() == program.read_bytes()

    program_ir = json.loads((output / "program.ir.json").read_text(encoding="utf-8"))
    assert is_plc_ir(program_ir)
    # The source-bound writer is the only CSV export for an ordered source: the
    # graphical exporter keeps refusing instead of guessing rung structure.
    from gxworks2.csv_export import _write_program_csv
    with _pytest.raises(GXNativeLoweringError) as error:
        _write_program_csv(program_ir, str(tmp_path / "guessed.csv"),
                           str(tmp_path / "guessed_comments.csv"))
    assert "source-bound GXW writer" in str(error.value)
    assert generate_gx_works2_csv(program_ir, tmp_path / "refused.csv",
                                  tmp_path / "refused_comments.csv") is False
    assert not (tmp_path / "refused.csv").exists()


def test_warehouse_ordered_export_reimports_all_nine_networks(tmp_path):
    from tools.offline_inputs import private_path

    fixtures = Path(private_path("research/fixtures/gxw-offline/warehouse"))
    output = tmp_path / "version"
    metadata = materialize_gxworks2_version(
        fixtures / "MAIN.csv", fixtures / "COMMENT.csv", output,
        plc_model="FX3U", program_name="MAIN")

    assert metadata["source_format"] == "instruction_sequence_v1"
    assert metadata["coverage"]["network_count"] == 9
    assert metadata["coverage"]["instruction_count"] == 155
    assert metadata["preservation"]["status"] == "passed"
    assert metadata["preservation"]["byte_identical"] == {"program": True, "comments": True}
    assert metadata["artifacts"]["program_csv"] == "program.csv"
    assert (output / "program.csv").read_bytes() == (fixtures / "MAIN.csv").read_bytes()
    assert (output / "comments.csv").read_bytes() == (fixtures / "COMMENT.csv").read_bytes()
    # The shared width catalogue now explains every direct-S target, so this
    # native export agrees with Core step for step and keeps its own numbering.
    assert metadata["preservation"]["step_width"]["summary"] == {
        "instruction_count": 155, "mismatch": 0, "unknown": 0, "target_conflict": 0,
        "steps_complete": True}
    assert metadata["preservation"]["step_width"]["findings"] == []
    assert metadata["preservation"]["step_source"] == "source"
    assert any("原始步号与共享宽度规则逐条一致" in message
               for message in metadata["validation"]["messages"])


def _steps_written(path):
    with Path(path).open(encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    return [row[0] for row in rows[3:] if len(row) > 3 and str(row[2] or "").strip()]


def test_edited_ordered_export_renumbers_from_the_shared_catalogue(tmp_path):
    import copy
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)
    from plc.instruction_steps import instruction_step_width

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_ordered_export(program, comments)
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    edited = copy.deepcopy(snapshot)
    network = next(item for item in edited["raw_networks"] if item["id"] == "N0002")
    instructions = network["raw_instructions"]
    assert [item["op"] for item in instructions] == ["LD", "OUT", "ANI", "OUT", "END"]
    # Delete the ANI and insert a fresh LD: the sequence is no longer the export's.
    del instructions[2]
    del network["source_map"][2]
    instructions.insert(1, {"op": "LD", "args": ["X002"], "statement": "新增触点",
                            "label": "", "step": "", "source_row": None})

    written_program = tmp_path / "out" / "MAIN.csv"
    written_comments = tmp_path / "out" / "COMMENT.csv"
    report = write_gxworks2_csv_from_source(edited, written_program, written_comments,
                                           step_source="catalogue")
    assert report["step_source"] == "catalogue"
    assert report["validation"]["program"]["valid"]
    assert report["width_summary"]["steps_complete"] is True

    # Steps are accumulated from Core across networks, not copied from the export.
    expected_steps, step = [], 0
    for item in edited["raw_networks"]:
        for instruction in item["raw_instructions"]:
            if not str(instruction.get("op") or "").strip():
                continue
            expected_steps.append(str(step))
            step += instruction_step_width(instruction["op"], instruction["args"],
                                           plc_model="FX3U").steps
    assert _steps_written(written_program) == expected_steps
    assert _steps_written(written_program)[-1] != _steps_written(program)[-1]

    # Provenance survives the renumbering: original rows keep their row number and
    # original step, the inserted instruction is reported as unbound.
    binding = report["binding"]
    inserted = binding[3]  # LD M8002, MOV, LD X002
    assert inserted["source_row"] is None and inserted["source_step"] == ""
    assert inserted["step"] == expected_steps[3]
    assert binding[0]["source_step"] == "1" and binding[0]["step"] == "0"
    assert [row["source_row"] for row in binding if row["source_row"]] == [5, 6, 8, 9, 11, 12]

    verified = verify_gxworks2_csv_source(
        edited, program_csv_path=written_program, comment_csv_path=written_comments,
        source_program_csv_path=program, source_comment_csv_path=comments,
        step_source="catalogue", expect_source_unchanged=False)
    assert verified["status"] == "passed", verified["mismatches"]
    assert verified["unbound_instructions"] == 1
    assert verified["checks"]["program_semantics"]["status"] == "skipped"
    assert verified["checks"]["step_labels"]["status"] == "passed"
    assert verified["checks"]["source_rows"]["status"] == "passed"
    assert verified["byte_identical"] == {"program": False, "comments": True}


def test_width_findings_separate_a_source_mismatch_from_an_unknown_width(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    rows = [
        ["MAIN"],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        # The source export says this contact occupies two steps; the catalogue
        # says one. That is an observation to report, not a reason to rewrite it.
        ["0", "", "LD", "X000", "", "", ""],
        ["2", "", "OUT", "Y000", "", "", ""],
        # No width rule and no native observation exist for this instruction.
        ["3", "", "FUTURE_VENDOR_OP", "D0", "", "", ""],
        ["", "", "", "D10", "", "", ""],
        ["6", "", "END", "", "", "", ""],
    ]
    with program.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)
    with comments.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(
            [["COMMENT"], GXWORKS2_COMMENT_HEADER, ["X000", "启动"]])

    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    assert snapshot["step_width"]["status"] == "differences_reported"
    kinds = {finding["kind"] for finding in snapshot["step_width"]["findings"]}
    assert kinds == {"width_mismatch", "width_unknown"}
    mismatch = next(finding for finding in snapshot["step_width"]["findings"]
                    if finding["kind"] == "width_mismatch")
    assert mismatch["opcode"] == "LD" and mismatch["native_steps"] == 2
    assert mismatch["catalogue_steps"] == 1 and mismatch["source_row"] == 4
    unknown = next(finding for finding in snapshot["step_width"]["findings"]
                   if finding["kind"] == "width_unknown")
    assert unknown["opcode"] == "FUTURE_VENDOR_OP" and unknown["catalogue_steps"] is None

    written_program, written_comments = tmp_path / "out" / "MAIN.csv", tmp_path / "out" / "COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, written_program, written_comments)
    assert report["width_summary"] == {"instruction_count": 4, "mismatch": 1,
                                      "unknown": 1, "target_conflict": 0,
                                      "steps_complete": True}
    # The unedited export still keeps the export's own steps, so the reported
    # difference never turns into a silent renumbering.
    assert _steps_written(written_program) == ["0", "2", "3", "6"]
    verified = verify_gxworks2_csv_source(
        snapshot, program_csv_path=written_program, comment_csv_path=written_comments,
        source_program_csv_path=program, source_comment_csv_path=comments)
    assert verified["status"] == "passed", verified["mismatches"]
    assert verified["width_summary"]["mismatch"] == 1


def test_width_findings_name_the_cpu_and_the_rule_that_answered():
    from gxworks2.csv_source_export import step_width_plan

    snapshot = {"plc_model": "FX3U", "raw_networks": [{"id": "N0001", "comment": "", "raw_instructions": [
        {"op": "SET", "args": ["S100"], "step": "0", "source_row": 4},
        {"op": "END", "args": [], "step": "3", "source_row": 5}]}]}
    plan = step_width_plan(snapshot)
    assert plan["summary"] == {"instruction_count": 2, "mismatch": 1, "unknown": 0,
                               "target_conflict": 0, "steps_complete": True}
    finding, = plan["findings"]
    assert finding["kind"] == "width_mismatch"
    assert finding["plc_model"] == "FX3U"
    assert finding["status"] == "known"
    # The rule that answered is carried with the finding, not just the number.
    assert any("7.16" in item for item in finding["rules"])
    assert plan["entries"][0]["width_status"] == "known"
    # The export's own step stays authoritative for a source-preserving write.
    assert [entry["step"] for entry in plan["entries"]] == ["0", "3"]


def test_step_width_plan_reports_an_uncovered_form_without_inventing_a_step():
    from gxworks2.csv_source_export import step_width_plan

    snapshot = {"plc_model": "FX3U", "raw_networks": [{"id": "N0001", "comment": "", "raw_instructions": [
        {"op": "NOT_A_REAL_OPCODE", "args": ["D0"], "step": "0", "source_row": 4},
        {"op": "END", "args": [], "step": "3", "source_row": 5}]}]}
    plan = step_width_plan(snapshot)
    finding, = plan["findings"]
    assert finding["kind"] == "width_unknown" and finding["status"] == "no_profile"
    assert finding["catalogue_steps"] is None
    # The source keeps its own numbering and the incomplete accumulator is reported.
    assert plan["entries"][0]["step"] == "0" and plan["entries"][1]["step"] == "3"
    assert plan["summary"]["steps_complete"] is True
    assert step_width_plan(snapshot, step_source="catalogue")["summary"]["steps_complete"] is False


def test_plc_info_target_reads_the_cpu_the_export_names():
    from gxworks2.csv_importer import read_plc_info_target
    from gxworks2.project_profiles import native_plc_info, plc_info_target

    # The reverse of native_plc_info, and a concrete member keeps its menu option.
    assert plc_info_target("FXCPU FX3U/FX3UC") == "FX3U/FX3UC"
    assert plc_info_target("FXCPU FX3UC") == "FX3U/FX3UC"
    assert plc_info_target("QCPU(Q模式) Q03UDV") == "Q03UDV"
    assert plc_info_target("LCPU L02/L02-P") == "L02/L02-P"
    assert native_plc_info(plc_info_target("QCPU(Q模式) Q03UDV")) == "QCPU(Q模式) Q03UDV"
    # A value that names no CPU stays unrecognised instead of guessing one.
    assert plc_info_target("三菱 GX Works2 兼容") is None
    assert plc_info_target("FXCPU FX5U") is None
    assert plc_info_target("") is None

    from tools.offline_inputs import private_path

    program = private_path("research/fixtures/gxw-offline/warehouse/MAIN.csv")
    # The merged menu option resolves to the member the rest of Core names.
    assert read_plc_info_target(program) == "FX3U"
    assert read_plc_info_target(program.parent / "COMMENT.csv") is None


def _write_targeted_export(program, comments, plc_info):
    """The same tiny sequence, stated for one CPU identity."""
    rows = [
        ["MAIN"],
        ["PLC信息:", plc_info],
        GXWORKS2_HEADER,
        ["0", "", "LD", "M8002", "", "", ""],
        ["2", "", "OUT", "M0", "", "", ""],
        ["3", "", "END", "", "", "", ""],
    ]
    with Path(program).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)
    comment_rows = [["COMMENT"], GXWORKS2_COMMENT_HEADER, ["M8002", "初始脉冲"]]
    with Path(comments).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\n").writerows(comment_rows)


def test_csv_inspection_reports_a_header_that_names_another_cpu(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    matched = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    assert matched["target"]["status"] == "matched"
    assert matched["coverage"]["target_status"] == "matched"
    # The export states its own CPU, so inspecting it as another one is a
    # reported conflict instead of a silent reinterpretation.
    wrong = inspect_gxworks2_csv(program, comments, plc_model="FX2N")
    assert wrong["target"]["status"] == "mismatch"
    assert wrong["target"]["expected"] == "FXCPU FX2N/FX2NC"
    assert wrong["target"]["plc_info_target"] == "FX3U/FX3UC"
    assert wrong["coverage"]["target_status"] == "mismatch"
    assert wrong["step_width"]["status"] == "target_conflict"


def test_source_preserving_write_cannot_change_the_cpu(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    out_program, out_comments = tmp_path / "OUT.csv", tmp_path / "OUT_COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments,
                                            plc_model="FX2N", step_source="source")
    assert report["target"]["status"] == "conflict"
    assert report["target"]["output_plc_info"] == "FXCPU FX3U/FX3UC"
    assert report["width_summary"]["target_conflict"] == 1
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          plc_model="FX2N", step_source="source")
    # The written header still names FX3U while the steps were computed for FX2N:
    # that output must never verify as passing.
    assert verified["status"] == "failed"
    identity = verified["checks"]["target_identity"]
    assert identity["status"] == "failed"
    assert identity["accepts_header"] is False
    assert [item["check"] for item in verified["mismatches"]] == ["target_identity"]


def test_explicit_conversion_rebuilds_the_plc_info_header(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    out_program, out_comments = tmp_path / "OUT.csv", tmp_path / "OUT_COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments,
                                            plc_model="FX2N", step_source="catalogue")
    assert report["target"]["status"] == "converted"
    assert report["target"]["output_plc_info"] == "FXCPU FX2N/FX2NC"
    # The source CPU stays as provenance instead of being overwritten.
    assert report["target"]["source_plc_info"] == "FXCPU FX3U/FX3UC"
    assert report["target"]["source_model"] == "FX3U"
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          plc_model="FX2N", step_source="catalogue",
                                          expect_source_unchanged=False)
    assert verified["checks"]["target_identity"]["status"] == "passed"
    assert verified["checks"]["target_identity"]["actual"] == "FXCPU FX2N/FX2NC"


def test_width_observation_follows_the_program_not_the_network(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import step_width_plan

    # A network's last instruction is followed by the next network's first one,
    # so its observed width has to come from document order. Reading only inside
    # the network silently skipped those instructions.
    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    rows = [
        ["MAIN"],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        ["0", "", "LD", "X000", "", "", ""],
        ["1", "", "OUT", "Y000", "", "", ""],
        ["网络说明", "", "", "", "", "", ""],
        ["2", "", "LD", "X001", "", "", ""],
        ["3", "", "OUT", "Y001", "", "", ""],
        ["4", "", "END", "", "", "", ""],
    ]
    with program.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)
    with comments.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\n").writerows(
            [["COMMENT"], GXWORKS2_COMMENT_HEADER])
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    plan = step_width_plan(snapshot)
    first_network_last = plan["entries"][1]
    assert (first_network_last["opcode"], first_network_last["network_id"]) == ("OUT", "N0001")
    assert first_network_last["native_steps"] == 1
    # Only the terminal END has no successor to measure against.
    assert [entry["opcode"] for entry in plan["entries"] if entry["native_steps"] is None] == ["END"]


def test_materialize_without_a_stated_cpu_is_reported(tmp_path):
    from gxworks2.csv_export import generate_gx_works2_csv
    from gxworks2.csv_importer import GXCSVImportError, materialize_gxworks2_version

    program, comments = tmp_path / "p.csv", tmp_path / "c.csv"
    ladder = {"device_comments": {}, "rungs": [{
        "rung_id": 1, "debug_note": "",
        "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": ""},
        "shared_inputs": [],
        "branches": [{"branch_id": 1, "y_offset_level": 0,
                      "inputs": [{"type": "NO", "address": "M8000", "label": ""}],
                      "outputs": [{"type": "APP_INSTR", "opcode": "MOV",
                                   "operands": ["K2", "D0"], "label": ""}]}]}]}
    generate_gx_works2_csv(ladder, program, comments)
    # The compatibility header names no CPU, so neither the export nor the caller
    # states a target: that is reported instead of materialized as FX3U.
    with pytest.raises(GXCSVImportError, match="目标CPU"):
        materialize_gxworks2_version(program, comments, tmp_path / "out")


def test_native_csv_export_accepts_every_native_fx_target():
    from gxw.models import GXWFormatError
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment

    listing = decode_token_program(parse_token_fragment(bytes.fromhex("03000304900004"), 0, 7),
                                   profile="fx3u", text_encoding="cp936")
    for identity, header in (("FX3U", "FXCPU FX3U/FX3UC"), ("FX1S", "FXCPU FX1S"),
                             ("FX3G/FX3GC", "FXCPU FX3G/FX3GC")):
        exported = listing.csv_bytes(plc_model=identity).decode("utf-16")
        assert header in exported, (identity, exported.splitlines()[:2])
    # An FX1S program decoded with its own codec exports from that codec, not from
    # a hardcoded FX3U profile: the column format is shared by the FX codecs.
    for profile, identity, header in (("fx1s", "FX1S", "FXCPU FX1S"),
                                      ("fx3g", "FX3G", "FXCPU FX3G/FX3GC")):
        own = decode_token_program(parse_token_fragment(bytes.fromhex("03000304900004"), 0, 7),
                                   profile=profile, text_encoding="cp936")
        assert header in own.csv_bytes(plc_model=identity).decode("utf-16")
    # A non-FX target keeps its refusal.
    with pytest.raises(GXWFormatError, match="FX CPU"):
        listing.csv_bytes(plc_model="Q03UDV")


def test_a_stated_target_cpu_is_written_from_the_plan(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    # A program export without a PLC信息 line: the CPU is stated by the caller.
    snapshot = {**inspect_gxworks2_csv(program, comments, plc_model="FX3U"),
                "plc_info": None}
    out_program, out_comments = tmp_path / "OUT.csv", tmp_path / "OUT_COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments)
    target = report["target"]
    # The plan states the header from the CPU; the writer does not fill it in.
    assert target["header"]["status"] == "derived"
    assert target["output_plc_info"] == "FXCPU FX3U/FX3UC"
    assert target["status"] == "derived"
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          expect_source_unchanged=False)
    assert verified["checks"]["target_identity"]["status"] == "passed"
    assert verified["checks"]["target_identity"]["actual"] == "FXCPU FX3U/FX3UC"
    # There was no source header to preserve, and that is reported as its own fact.
    assert verified["checks"]["source_header"]["status"] == "skipped"
    assert verified["status"] == "passed", verified["mismatches"]


def test_a_merged_source_identity_is_not_a_target_conflict(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    # The export names the menu option, the writer computes for one of its
    # members: that is the same saved CPU, not a retarget.
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U/FX3UC")
    out_program, out_comments = tmp_path / "OUT.csv", tmp_path / "OUT_COMMENT.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments,
                                            plc_model="FX3U")
    target = report["target"]
    assert target["status"] == "same"
    assert target["identity"] == {"status": "same", "compatible": True, "reason": None}
    assert target["header"]["status"] == "preserved"
    assert target["output_plc_info"] == "FXCPU FX3U/FX3UC"
    assert report["width_summary"]["target_conflict"] == 0
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          plc_model="FX3U",
                                          source_program_csv_path=program,
                                          source_comment_csv_path=comments)
    assert verified["status"] == "passed", verified["mismatches"]
    assert verified["checks"]["source_header"]["status"] == "passed"
    assert verified["byte_identical"] == {"program": True, "comments": True}


def test_an_explicit_header_is_planned_once_and_a_contradiction_is_refused(tmp_path):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import (verify_gxworks2_csv_source,
                                            write_gxworks2_csv_from_source)

    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_targeted_export(program, comments, "FXCPU FX3U/FX3UC")
    snapshot = inspect_gxworks2_csv(program, comments, plc_model="FX3U")

    # The caller's own spelling of the same CPU is accepted by both sides.
    out_program, out_comments = tmp_path / "A.csv", tmp_path / "AC.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments,
                                            plc_info="FXCPU FX3UC")
    assert report["target"]["output_plc_info"] == "FXCPU FX3UC"
    assert report["target"]["header"]["status"] == "requested"
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          plc_info="FXCPU FX3UC",
                                          expect_source_unchanged=False)
    assert verified["checks"]["target_identity"]["status"] == "passed"
    assert verified["checks"]["target_identity"]["actual"] == "FXCPU FX3UC"

    # A conversion that carries the old header is refused by the plan itself,
    # instead of being reported as converted and rejected afterwards.
    out_program, out_comments = tmp_path / "B.csv", tmp_path / "BC.csv"
    report = write_gxworks2_csv_from_source(snapshot, out_program, out_comments,
                                            plc_model="FX2N", plc_info="FXCPU FX3U/FX3UC")
    assert report["target"]["status"] == "mismatch"
    assert report["target"]["header"]["status"] == "rejected"
    assert report["target"]["expected"] == "FXCPU FX2N/FX2NC"
    assert report["width_summary"]["target_conflict"] == 1
    verified = verify_gxworks2_csv_source(snapshot, program_csv_path=out_program,
                                          comment_csv_path=out_comments,
                                          plc_model="FX2N", plc_info="FXCPU FX3U/FX3UC")
    assert verified["status"] == "failed"
    assert [item["check"] for item in verified["mismatches"]] == ["target_identity"]


def test_the_project_code_page_reaches_the_csv_plan(tmp_path):
    from gxworks2.csv_source_export import step_width_plan

    snapshot = {"plc_model": "Q03UDV", "raw_networks": [{"id": "N0001", "comment": "",
        "raw_instructions": [{"op": "$MOV", "args": ['"abc"', "ZR0"], "step": "0",
                              "source_row": 4},
                             {"op": "END", "args": [], "step": "5", "source_row": 5}]}]}
    # The CSV file is UTF-16; the string operand needs the project code page,
    # which is a different fact and has to be supplied separately.
    unstated = step_width_plan(snapshot)
    finding, = unstated["findings"]
    assert (finding["opcode"], finding["status"]) == ("$MOV", "requires_context")
    assert finding["requirements"] == ["text_encoding"]
    stated = step_width_plan(snapshot, text_encoding="cp936")
    assert stated["findings"] == []
    # A three-character payload in a two-byte code page is five stored words.
    assert stated["entries"][0]["catalogue_steps"] == 5


def _write_context_sensitive_export(program, comments, *, plc_info, statements):
    """One ordered export whose width needs a project fact, not just the CPU."""
    rows = [["MAIN"], ["PLC信息:", plc_info], GXWORKS2_HEADER] + statements
    with Path(program).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)
    with Path(comments).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\n").writerows(
            [["COMMENT"], GXWORKS2_COMMENT_HEADER, ["M8002", "初始脉冲"]])


def test_the_ordered_materialize_carries_the_project_context(tmp_path):
    """The application entry must report the same width state as the CSV API."""
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxw.token_q_encoder import QStorageContext

    # A string operand stores a length the code page decides, so the form has no
    # width until the project says which code page its text is in.
    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_context_sensitive_export(program, comments, plc_info="FXCPU FX3U/FX3UC", statements=[
        ["1", "初始化", "LD", "M8002", "", "", ""],
        ["2", "", "$MOV", '"abc"', "", "", ""],
        ["", "", "", "D10", "", "", ""],
        ["9", "", "LD", "X000", "", "", ""],
        ["10", "", "OUT", "Y000", "", "", ""],
        ["11", "", "ANI", "X001", "", "", ""],
        ["12", "", "END", "", "", "", ""],
    ])
    stated = inspect_gxworks2_csv(program, comments, plc_model="FX3U", text_encoding="cp936")
    assert stated["step_width"]["summary"]["unknown"] == 0
    stated_version = materialize_gxworks2_version(
        program, comments, tmp_path / "stated", plc_model="FX3U", text_encoding="cp936")
    assert stated_version["preservation"]["step_width"]["summary"] == stated["step_width"]["summary"]
    unstated = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
    assert unstated["step_width"]["summary"]["unknown"] == 1
    unstated_version = materialize_gxworks2_version(
        program, comments, tmp_path / "unstated", plc_model="FX3U")
    assert unstated_version["preservation"]["step_width"]["summary"] == unstated["step_width"]["summary"]

    # D12000 is a local word only while the saved allocation is the ordinary one,
    # so the same program reports a context gap, or the extended mapping.
    program, comments = tmp_path / "MAINQ.csv", tmp_path / "COMMENTQ.csv"
    _write_context_sensitive_export(program, comments, plc_info="QCPU (Q mode) Q03UDV", statements=[
        ["0", "初始化", "LD", "X0", "", "", ""],
        ["1", "", "MOV", "D12000", "", "", ""],
        ["", "", "", "D0", "", "", ""],
        ["7", "", "LD", "X1", "", "", ""],
        ["8", "", "OUT", "Y0", "", "", ""],
        ["9", "", "ANI", "X2", "", "", ""],
        ["10", "", "END", "", "", "", ""],
    ])
    extended = QStorageContext(cpu="Q03UDV", parameter_record=None,
                               ranges=((0xa8, 12000, 1024, 65536),))
    mapped = inspect_gxworks2_csv(program, comments, plc_model="Q03UDV",
                                  storage_context=extended)
    assert mapped["step_width"]["summary"]["unknown"] == 0
    mapped_version = materialize_gxworks2_version(
        program, comments, tmp_path / "mapped", plc_model="Q03UDV", storage_context=extended)
    assert mapped_version["preservation"]["step_width"]["summary"] == mapped["step_width"]["summary"]
    gap = inspect_gxworks2_csv(program, comments, plc_model="Q03UDV")
    assert gap["step_width"]["summary"]["unknown"] == 1
    gap_version = materialize_gxworks2_version(
        program, comments, tmp_path / "gap", plc_model="Q03UDV")
    assert gap_version["preservation"]["step_width"]["summary"] == gap["step_width"]["summary"]

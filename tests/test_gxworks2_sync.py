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

    metadata = materialize_gxworks2_version(program, comments, output)

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


def test_push_roundtrip_verifies_program_comments_and_saves_project(tmp_path):
    current = tmp_path / "current.csv"
    target = tmp_path / "target.csv"
    current_comments = tmp_path / "current-comments.csv"
    target_comments = tmp_path / "target-comments.csv"
    _write_program(current, output="Y000")
    _write_program(target, output="Y001")
    _write_comments(current_comments, [["Y000", "旧输出"]])
    _write_comments(target_comments, [["Y001", "新输出"]])
    automation = _RoundtripAutomation(current, current_comments)
    service = ImportService(
        _Finder(), automation, CSVManager(), tmp_path / "backups"
    )

    result = service.import_current_program(
        target,
        comment_csv_path=target_comments,
        synchronize_comments=True,
        verify_roundtrip=True,
        save_project=True,
    )

    assert result.success
    assert automation.saved == 1
    assert Path(result.details["verified_program_path"]).is_file()
    assert Path(result.details["verified_comment_path"]).is_file()
    assert result.details["project_save"]["success"] is True


def test_push_roundtrip_fails_if_gx_did_not_apply_the_program(tmp_path):
    current = tmp_path / "current.csv"
    target = tmp_path / "target.csv"
    comments = tmp_path / "comments.csv"
    _write_program(current, output="Y000")
    _write_program(target, output="Y001")
    _write_comments(comments)
    automation = _RoundtripAutomation(current, comments, apply_import=False)
    service = ImportService(
        _Finder(), automation, CSVManager(), tmp_path / "backups"
    )

    result = service.import_current_program(
        target,
        comment_csv_path=comments,
        synchronize_comments=True,
        verify_roundtrip=True,
    )

    assert not result.success
    assert result.stage == "verify_roundtrip"
    assert "回读程序" in result.message


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


def test_sync_failure_dialog_expands_details_and_hides_unsafe_retry():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ui.desktop.dialogs.workflow import GXWorks2SyncErrorDialog
    from ui.desktop.qt import QApplication

    app = QApplication.instance() or QApplication([])
    retryable = GXWorks2SyncService._error(
        "未检测到文件窗口",
        GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
        stage="wait_program_file_dialog",
        retryable=True,
        details={
            "gx_running": True,
            "project_open": True,
            "program_ready": True,
            "program_name": "MAIN",
        },
    )
    dialog = GXWorks2SyncErrorDialog(retryable)
    assert dialog.retry_button is not None
    assert dialog.details_editor.isHidden()
    dialog._toggle_details()
    assert not dialog.details_editor.isHidden()
    assert '"ok": false' in dialog.details_editor.toPlainText()
    assert dialog.details_button.text() == "收起技术详情"
    dialog.close()

    non_retryable = GXWorks2SyncService._error(
        "权限不一致",
        GXSyncErrorCode.GX_UIA_ACCESS_DENIED,
        stage="activate_main",
        retryable=False,
    )
    dialog = GXWorks2SyncErrorDialog(non_retryable)
    assert dialog.retry_button is None
    dialog.close()
    app.processEvents()


def test_pending_manual_retry_waits_until_the_worker_is_released():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ui.desktop.main_window import _IndustrialWorkbenchUI

    class _Workbench:
        def __init__(self):
            self._gx_sync_retry_pending = True
            self._gxworks2_sync_thread = object()
            self.started = 0

        def _gx_sync_busy(self):
            return self._gxworks2_sync_thread is not None

        def _sync_current_version_with_gxworks2(self):
            self.started += 1

    workbench = _Workbench()
    _IndustrialWorkbenchUI._run_pending_gx_sync_retry(workbench)
    assert workbench.started == 0
    assert workbench._gx_sync_retry_pending is True

    workbench._gxworks2_sync_thread = None
    _IndustrialWorkbenchUI._run_pending_gx_sync_retry(workbench)
    assert workbench.started == 1
    assert workbench._gx_sync_retry_pending is False


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

import csv
import threading
from pathlib import Path

from gxworks2.csv_manager import (
    CSVManager,
    GXWORKS2_COMMENT_HEADER,
    GXWORKS2_HEADER,
)
from gxworks2.baseline_store import ImportBaselineStore
from gxworks2.import_service import ImportService
from gxworks2.finder import GXWorks2Finder
from gxworks2.models import GXWorks2Session, ImportErrorCode, ImportResult
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation


def _write_program(
    path,
    *,
    end=True,
    title="MAIN - 副本",
    first_step="0",
    output="Y000",
    statement="",
    note="",
):
    rows = [
        [title],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        [first_step, statement, "LD", "X000", "", "", ""],
        [str(int(first_step) + 1), "", "OUT", output, "", "", ""],
    ]
    if note:
        rows.append(["", "", "", "", "", "", note])
    if end:
        rows.append([str(int(first_step) + 2), "", "END", "", "", "", ""])
    with Path(path).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)


def _write_comments(path, comments=None, title="COMMENT - 副本"):
    rows = [[title], GXWORKS2_COMMENT_HEADER]
    rows.extend(comments or [])
    with Path(path).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)


def _write_native_multi_operand_export(path, *, destination="D0"):
    rows = [
        ["(工程未设置)"],
        ["PLC信息:", "FXCPU FX3U/FX3UC"],
        GXWORKS2_HEADER,
        ["0", "初始化", "", "", "", "", ""],
        ["0", "", "LD=", "D0", "", "", ""],
        ["", "", "", "K0", "", "", ""],
        ["5", "", "MOV", "K1", "", "", ""],
        ["", "", "", destination, "", "", ""],
        ["10", "", "END", "", "", "", ""],
    ]
    with Path(path).open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)


class FakeFinder:
    def __init__(self, session):
        self.session = session

    def find_running(self):
        return self.session

    def start(self):
        return None


class FakeAutomation:
    def __init__(self, source_backup, *, success=True, project_name="FixtureProject"):
        self.source_backup = Path(source_backup)
        self.success = success
        self.project_name = project_name
        self.imported = None
        self.imported_comments = None
        self.comment_backup = None
        self.events = []

    def inspect_project(self, session):
        return {
            "automation_available": True,
            "project_open": True,
            "program_ready": True,
            "project_name": self.project_name,
        }

    def export_current_program(self, session, destination):
        self.events.append("backup_program")
        Path(destination).write_bytes(self.source_backup.read_bytes())

    def import_program_csv(self, session, csv_path):
        self.events.append("import_program")
        self.imported = Path(csv_path)
        return {"success": self.success, "message": "导入完成" if self.success else "导入被拒绝"}

    def export_current_comments(self, session, destination):
        self.events.append("backup_comments")
        _write_comments(destination, [["X000", "旧注释"]])
        self.comment_backup = Path(destination)

    def import_comments_csv(self, session, csv_path):
        self.events.append("import_comments")
        self.imported_comments = Path(csv_path)
        return {"success": self.success, "message": "注释导入完成"}


class DelayedBackupAutomation(FakeAutomation):
    """Simulate GX Works2 creating a CSV before its final rows are flushed."""

    def __init__(self, source_backup, **kwargs):
        super().__init__(source_backup, **kwargs)
        self.export_timer = None

    def export_current_program(self, session, destination):
        self.events.append("backup_program")
        destination = Path(destination)
        complete = self.source_backup.read_bytes()
        destination.write_bytes(complete[: max(4, len(complete) // 2)])
        self.export_timer = threading.Timer(
            0.05,
            destination.write_bytes,
            args=(complete,),
        )
        self.export_timer.start()


def _session(project_open=True):
    return GXWorks2Session(
        process_id=1,
        window_handle=2,
        title="Fixture - GX Works2",
        executable="GD2.exe",
        project_open=project_open,
        project_name="Fixture",
        project_state_known=True,
    )


def test_project_name_ignores_active_gxworks2_editor_title():
    program_title = "MELSOFT系列 GX Works2 (工程未设置) - [[PRG]写入 MAIN 84步]"
    comment_title = "MELSOFT系列 GX Works2 (工程未设置) - [软元件注释 COMMENT ]"

    assert GXWorks2Finder._project_name_from_title(program_title) == "(工程未设置)"
    assert GXWorks2Finder._project_name_from_title(comment_title) == "(工程未设置)"


def test_project_name_keeps_legacy_name_before_gxworks2_marker():
    assert GXWorks2Finder._project_name_from_title("Fixture - GX Works2") == "Fixture"


def test_bare_gxworks2_title_means_no_project_is_open():
    assert GXWorks2Finder._project_name_from_title("MELSOFT系列 GX Works2") == ""










def test_csv_manager_accepts_generated_statement_list(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    result = CSVManager().validate(source)
    assert result.valid
    assert result.instruction_count == 3
    assert result.encoding == "utf-16"


def test_import_staging_limits_statements_without_changing_program_semantics(tmp_path):
    source = tmp_path / "source.csv"
    staged = tmp_path / "staged.csv"
    long_statement = "停止状态且未满料时启动进入运行；NC X1为停止常闭触点，未按下时导通"
    _write_program(source, statement=long_statement)
    manager = CSVManager()

    manager.prepare_import_program(source, staged)

    with staged.open("r", encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    assert len(rows[3][1].encode("gb18030")) <= 64
    assert rows[3][1] != long_statement
    assert manager.program_semantic_sha256(staged) == manager.program_semantic_sha256(
        source
    )


def test_csv_manager_rejects_missing_end(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source, end=False)
    result = CSVManager().validate(source)
    assert not result.valid
    assert any("END" in error for error in result.errors)


def test_csv_manager_accepts_generated_device_comments(tmp_path):
    source = tmp_path / "comments.csv"
    _write_comments(source, [["X000", "启动"], ["Y000", "电机输出"]])

    result = CSVManager().validate_comments(source)

    assert result.valid
    assert result.comment_count == 2
    assert result.encoding == "utf-16"


def test_comment_csv_without_rows_is_valid_but_not_importable(tmp_path):
    source = tmp_path / "comments.csv"
    _write_comments(source)

    result = CSVManager().validate_comments(source)

    assert result.valid
    assert result.comment_count == 0
    assert any("保留" in warning for warning in result.warnings)


def test_comment_validator_accepts_gxworks2_exported_project_title(tmp_path):
    source = tmp_path / "comments_backup.csv"
    _write_comments(source, title="(工程未设置)")

    result = CSVManager().validate_comments(source)

    assert result.valid
    assert result.comment_count == 0


def test_native_comment_backup_may_use_lf_without_weakening_import_validation(tmp_path):
    source = tmp_path / "comments_backup.csv"
    source.write_bytes(
        '\ufeff"(工程未设置)"\n"软元件名"\t"注释"\n'.encode("utf-16-le")
    )

    strict_result = CSVManager().validate_comments(source)
    backup_result = CSVManager().validate_comments(source, require_crlf=False)

    assert not strict_result.valid
    assert any("CRLF" in error for error in strict_result.errors)
    assert backup_result.valid


def test_import_stops_when_gxworks2_has_no_project(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session(False)), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(source)
    assert not result.success
    assert result.error_code == ImportErrorCode.TARGET_PROJECT_NOT_OPEN
    assert automation.imported is None


def test_import_backs_up_before_importing(tmp_path):
    source = tmp_path / "program.csv"
    previous = tmp_path / "previous.csv"
    _write_program(source)
    _write_program(previous)
    automation = FakeAutomation(previous)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(source)
    assert result.success
    assert Path(result.backup_path).is_file()
    assert automation.imported == source.resolve()


def test_import_waits_for_gxworks2_backup_to_finish_flushing(tmp_path):
    source = tmp_path / "program.csv"
    previous = tmp_path / "previous.csv"
    _write_program(source)
    _write_program(previous)
    automation = DelayedBackupAutomation(previous)
    service = ImportService(
        FakeFinder(_session()),
        automation,
        CSVManager(),
        tmp_path / "backups",
        export_validation_timeout=0.5,
        export_validation_poll_interval=0.01,
    )

    try:
        result = service.import_current_program(source)
    finally:
        if automation.export_timer is not None:
            automation.export_timer.join(timeout=1.0)

    assert result.success
    assert automation.events[:2] == ["backup_program", "import_program"]
    assert CSVManager().validate(result.backup_path).valid


def test_import_reports_backup_format_reason_after_retry_timeout(tmp_path):
    source = tmp_path / "program.csv"
    invalid_backup = tmp_path / "invalid_backup.csv"
    _write_program(source)
    _write_program(invalid_backup, end=False)
    automation = FakeAutomation(invalid_backup)
    service = ImportService(
        FakeFinder(_session()),
        automation,
        CSVManager(),
        tmp_path / "backups",
        export_validation_timeout=0.03,
        export_validation_poll_interval=0.01,
    )

    result = service.import_current_program(source)

    assert not result.success
    assert result.error_code == ImportErrorCode.BACKUP_FAILED
    assert "END" in result.message
    assert automation.imported is None


def test_import_backs_up_and_imports_device_comments(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    _write_program(source)
    _write_comments(comments, [["X000", "启动"], ["Y000", "电机输出"]])
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    result = service.import_current_program(source, comment_csv_path=comments)

    assert result.success
    assert result.message == "当前程序和软元件注释已导入GX Works2。"
    assert automation.events == [
        "backup_program",
        "backup_comments",
        "import_program",
        "import_comments",
    ]
    assert automation.imported_comments == comments.resolve()
    assert Path(result.details["comment_backup_path"]).is_file()


def test_empty_comment_csv_preserves_existing_project_comments(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    _write_program(source)
    _write_comments(comments)
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    result = service.import_current_program(source, comment_csv_path=comments)

    assert result.success
    assert automation.events == ["backup_program", "import_program"]
    assert "保留工程现有注释" in result.message


def test_invalid_comment_csv_stops_before_any_backup_or_import(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    _write_program(source)
    comments.write_text("bad", encoding="utf-8")
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    result = service.import_current_program(source, comment_csv_path=comments)

    assert not result.success
    assert result.stage == "validate_comments"
    assert result.error_code == ImportErrorCode.CSV_INVALID
    assert automation.events == []


def test_failed_verification_keeps_backup(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source, success=False)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(source)
    assert not result.success
    assert result.error_code == ImportErrorCode.IMPORT_VERIFICATION_FAILED
    assert Path(result.backup_path).is_file()


def test_program_semantic_hash_ignores_title_steps_and_comments(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    changed = tmp_path / "changed.csv"
    _write_program(first)
    _write_program(
        second,
        title="工程导出标题",
        first_step="100",
        statement="工程师的行间声明",
        note="输出注释",
    )
    _write_program(changed, output="Y001")

    manager = CSVManager()
    assert manager.program_semantic_sha256(first) == manager.program_semantic_sha256(
        second
    )
    assert manager.program_semantic_sha256(first) != manager.program_semantic_sha256(
        changed
    )


def test_semantic_hash_rejoins_native_gxworks2_operand_continuation_rows(tmp_path):
    generated = tmp_path / "generated.csv"
    native = tmp_path / "native.csv"
    native_changed = tmp_path / "native_changed.csv"
    rows = [
        ["MAIN - 副本"],
        ["PLC信息:", "三菱 GX Works2 兼容"],
        GXWORKS2_HEADER,
        ["0", "初始化", "", "", "", "", ""],
        ["0", "", "LD=", "D0 K0", "", "", ""],
        ["5", "", "MOV", "K1 D0", "", "", ""],
        ["10", "", "END", "", "", "", ""],
    ]
    with generated.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(
            handle,
            delimiter="\t",
            quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        ).writerows(rows)
    _write_native_multi_operand_export(native)
    _write_native_multi_operand_export(native_changed, destination="D1")

    manager = CSVManager()
    assert manager.program_semantic_sha256(generated) == manager.program_semantic_sha256(
        native
    )
    assert manager.program_semantic_sha256(generated) != manager.program_semantic_sha256(
        native_changed
    )


def test_first_successful_import_creates_version_baseline(tmp_path):
    source = tmp_path / "program.csv"
    current = tmp_path / "current.csv"
    _write_program(source)
    _write_program(current, title="GX导出标题", first_step="20")
    automation = FakeAutomation(current)
    service = ImportService(
        FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups"
    )

    result = service.import_current_program(
        source,
        import_context={
            "project_id": "project-1",
            "version_id": "v7",
            "revision": 7,
            "ignored": "not persisted",
        },
    )

    assert result.success
    protection = result.details["version_protection"]
    assert protection["status"] == "baseline_created"
    identity = service.baseline_store.project_identity(
        _session(), project_name="FixtureProject"
    )
    baseline = service.baseline_store.load(identity)
    assert baseline["program_semantic_sha256"] == protection[
        "target_program_semantic_sha256"
    ]
    assert baseline["import_context"] == {
        "project_id": "project-1",
        "version_id": "v7",
        "revision": 7,
    }


def test_external_program_change_blocks_overwrite_and_keeps_backup(tmp_path):
    target = tmp_path / "target.csv"
    external = tmp_path / "external.csv"
    _write_program(target)
    _write_program(external, output="Y001")
    automation = FakeAutomation(target)
    service = ImportService(
        FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups"
    )
    first = service.import_current_program(target)
    assert first.success

    automation.source_backup = external
    automation.events.clear()
    automation.imported = None
    second = service.import_current_program(target)

    assert not second.success
    assert second.stage == "external_modification"
    assert second.error_code == ImportErrorCode.EXTERNAL_MODIFICATION_DETECTED
    assert automation.events == ["backup_program"]
    assert automation.imported is None
    assert Path(second.backup_path).read_bytes() == external.read_bytes()
    assert second.details["version_protection"]["status"] == (
        "external_modification_detected"
    )


def test_failed_import_does_not_advance_version_baseline(tmp_path):
    original = tmp_path / "original.csv"
    new_target = tmp_path / "new_target.csv"
    _write_program(original)
    _write_program(new_target, output="Y002")
    automation = FakeAutomation(original)
    service = ImportService(
        FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups"
    )
    assert service.import_current_program(original).success
    identity = service.baseline_store.project_identity(
        _session(), project_name="FixtureProject"
    )
    before = service.baseline_store.load(identity)

    automation.success = False
    result = service.import_current_program(new_target)
    after = service.baseline_store.load(identity)

    assert not result.success
    assert result.error_code == ImportErrorCode.IMPORT_VERIFICATION_FAILED
    assert after["program_semantic_sha256"] == before["program_semantic_sha256"]


class FailingBaselineStore:
    @staticmethod
    def project_identity(session, project_name="", project_identity=""):
        return {"application": "GX Works2", "project": project_name.casefold()}

    @staticmethod
    def load(identity):
        return None

    @staticmethod
    def save(identity, **values):
        raise OSError("disk unavailable")


def test_import_reports_success_with_clear_warning_if_baseline_cannot_be_saved(
    tmp_path,
):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    service = ImportService(
        FakeFinder(_session()),
        automation,
        CSVManager(),
        tmp_path / "backups",
        baseline_store=FailingBaselineStore(),
    )

    result = service.import_current_program(source)

    assert result.success
    assert result.stage == "complete_with_warning"
    assert result.error_code == ImportErrorCode.BASELINE_WRITE_FAILED
    assert result.details["version_protection"]["status"] == "baseline_write_failed"


def test_corrupt_baseline_fails_closed_before_import(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    service = ImportService(
        FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups"
    )
    identity = service.baseline_store.project_identity(
        _session(), project_name="FixtureProject"
    )
    baseline_path = service.baseline_store.path_for(identity)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text("not-json", encoding="utf-8")

    result = service.import_current_program(source)

    assert not result.success
    assert result.stage == "compare_baseline"
    assert result.error_code == ImportErrorCode.BASELINE_READ_FAILED
    assert automation.events == ["backup_program"]
    assert automation.imported is None
    assert Path(result.backup_path).is_file()


def test_baselines_are_separate_for_different_gx_project_names(tmp_path):
    store = ImportBaselineStore(tmp_path / "backups")
    first = store.project_identity(_session(), project_name="Line-A")
    second = store.project_identity(_session(), project_name="Line-B")

    assert store.path_for(first) != store.path_for(second)


def test_unsaved_project_does_not_reuse_legacy_shared_name_baseline(tmp_path):
    source = tmp_path / "program.csv"
    current = tmp_path / "current.csv"
    _write_program(source)
    _write_program(current, output="Y001")
    session = GXWorks2Session(
        process_id=101,
        window_handle=202,
        title="MELSOFT系列 GX Works2 (工程未设置) - [[PRG]写入 MAIN 1步]",
        executable="GD2.exe",
        project_open=True,
        project_name="(工程未设置)",
        project_state_known=True,
    )
    automation = FakeAutomation(current, project_name="(工程未设置)")
    service = ImportService(
        FakeFinder(session), automation, CSVManager(), tmp_path / "backups"
    )

    legacy_identity = service.baseline_store.project_identity(
        session,
        project_name="(工程未设置)",
    )
    service.baseline_store.save(
        legacy_identity,
        program_semantic_sha256=CSVManager().program_semantic_sha256(source),
        program_file_sha256=CSVManager().file_sha256(source),
    )

    result = service.import_current_program(
        source,
        import_context={"project_id": "plc-ai-project-1"},
    )

    assert result.success
    protection = result.details["version_protection"]
    assert protection["baseline_found"] is False
    assert protection["project_identity"]["project"] == (
        "unsaved|process:101|window:202|plc-ai:plc-ai-project-1"
    )


def test_unsaved_project_keeps_version_protection_within_same_session(tmp_path):
    source = tmp_path / "program.csv"
    externally_changed = tmp_path / "external.csv"
    _write_program(source)
    _write_program(externally_changed, output="Y002")
    session = GXWorks2Session(
        process_id=303,
        window_handle=404,
        title="MELSOFT系列 GX Works2 (工程未设置) - [[PRG]写入 MAIN 1步]",
        executable="GD2.exe",
        project_open=True,
        project_name="(工程未设置)",
        project_state_known=True,
    )
    automation = FakeAutomation(source, project_name="(工程未设置)")
    service = ImportService(
        FakeFinder(session), automation, CSVManager(), tmp_path / "backups"
    )
    context = {"project_id": "plc-ai-project-2"}

    assert service.import_current_program(source, import_context=context).success
    automation.source_backup = externally_changed
    second = service.import_current_program(source, import_context=context)

    assert not second.success
    assert second.error_code == ImportErrorCode.EXTERNAL_MODIFICATION_DETECTED


class _ElementInfo:
    def __init__(self, automation_id="", class_name=""):
        self.automation_id = automation_id
        self.class_name = class_name


class _Control:
    def __init__(self, text, *, automation_id="", enabled=True):
        self._text = text
        self._enabled = enabled
        self.element_info = _ElementInfo(automation_id=automation_id)
        self.invoked = False

    def window_text(self):
        return self._text

    def is_enabled(self):
        return self._enabled

    def invoke(self):
        self.invoked = True


class _Edit(_Control):
    def __init__(self):
        super().__init__("", automation_id="1148")
        self.value = ""
        self.entered = False

    def set_edit_text(self, value):
        self.value = value

    def type_keys(self, keys):
        self.entered = keys == "{ENTER}"


class _FileDialog:
    def __init__(self, edit, buttons):
        self.edit = edit
        self.buttons = buttons

    def descendants(self, control_type=None):
        if control_type == "Edit":
            return [self.edit]
        if control_type == "Button":
            return self.buttons
        return []


def test_file_dialog_uses_default_button_not_combobox_dropdown(tmp_path):
    edit = _Edit()
    dropdown = _Control("打开", automation_id="DropDown")
    save = _Control("保存(S)", automation_id="1")
    dialog = _FileDialog(edit, [dropdown, save])

    PywinautoGXWorks2UIAutomation._set_file_name(
        dialog, tmp_path / "backup.csv"
    )

    assert not dropdown.invoked
    assert save.invoked
    assert edit.value.endswith("backup.csv")


class _KeyboardWindow:
    def __init__(self):
        self.keys = []

    def set_focus(self):
        pass

    def type_keys(self, keys):
        self.keys.append(keys)


def test_csv_accelerator_does_not_scan_the_accessibility_tree(monkeypatch):
    automation = PywinautoGXWorks2UIAutomation()
    window = _KeyboardWindow()
    access_keys = []
    monkeypatch.setattr("gxworks2.ui_automation.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(automation, "_send_popup_access_key", access_keys.append)

    automation._send_edit_accelerator(window, automation.PROGRAM_READ_KEY)

    assert window.keys == ["%e"]
    assert access_keys == ["j"]


def test_program_and_comment_editors_use_different_accelerators():
    automation = PywinautoGXWorks2UIAutomation()
    assert automation.PROGRAM_READ_KEY == "j"
    assert automation.PROGRAM_WRITE_KEY == "k"
    assert automation.COMMENT_READ_KEY == "f"
    assert automation.COMMENT_WRITE_KEY == "o"


def test_program_import_switches_read_only_main_to_write_mode(monkeypatch):
    automation = PywinautoGXWorks2UIAutomation(timeout=0.2)
    window = _KeyboardWindow()
    title_snapshots = iter(
        [
            ["[PRG]读取 MAIN (只读) 4步"],
            ["[PRG]写入 MAIN 4步"],
        ]
    )
    write_shortcuts = []

    monkeypatch.setattr(automation, "_activate_program_editor", lambda _session: window)
    monkeypatch.setattr(automation, "_activate_native_document", lambda *_args: True)
    monkeypatch.setattr(
        automation,
        "_program_editor_titles",
        lambda _session: next(title_snapshots),
    )
    monkeypatch.setattr(
        automation,
        "_send_write_mode_shortcut",
        lambda: write_shortcuts.append("F2"),
    )

    assert automation._ensure_program_writable(object()) is window
    assert write_shortcuts == ["F2"]


def test_program_import_keeps_existing_write_mode_without_f2(monkeypatch):
    automation = PywinautoGXWorks2UIAutomation(timeout=0.2)
    window = _KeyboardWindow()
    write_shortcuts = []

    monkeypatch.setattr(automation, "_activate_program_editor", lambda _session: window)
    monkeypatch.setattr(
        automation,
        "_program_editor_titles",
        lambda _session: ["[PRG]写入 MAIN 4步"],
    )
    monkeypatch.setattr(
        automation,
        "_send_write_mode_shortcut",
        lambda: write_shortcuts.append("F2"),
    )

    assert automation._ensure_program_writable(object()) is window
    assert write_shortcuts == []


def test_failure_pattern_does_not_treat_error_code_header_as_failure():
    automation = PywinautoGXWorks2UIAutomation()
    assert automation.FAILURE_TEXT.search("错误代码")
    # Operation-result handling now filters candidates to real #32770
    # dialogs before applying the text pattern; the output-table header is
    # never a result candidate.
    assert _ElementInfo(class_name="Table").class_name != "#32770"


def test_read_confirmation_warning_is_not_treated_as_failure():
    automation = PywinautoGXWorks2UIAutomation()
    message = "读取指定的文件内容。确定吗？执行读取后，将无法撤消。"

    assert automation.CONFIRMATION_TEXT.search(message)
    assert automation.FAILURE_TEXT.search(message)
    # Confirmation handling is intentionally evaluated before the generic
    # word "无法" failure pattern in the live automation path.


def test_export_completion_uses_created_file_without_slow_uia_probe(
    tmp_path, monkeypatch
):
    destination = tmp_path / "backup.csv"
    destination.write_bytes(b"complete")
    automation = PywinautoGXWorks2UIAutomation(timeout=0.4)

    def forbidden_probe(_session):
        raise AssertionError("completed export must not query the legacy UIA frame")

    monkeypatch.setattr(automation, "_main_window", forbidden_probe)
    result = automation._wait_operation_result(object(), destination=destination)

    assert result["success"]
    assert "备份" in result["message"]


def test_backup_folders_are_unique_when_windows_clock_has_not_advanced(tmp_path, monkeypatch):
    import gxworks2.csv_manager as module
    class FrozenClock:
        @staticmethod
        def now():
            return FrozenClock()
        def strftime(self, _format):
            return "20260911-000000-000000"
    monkeypatch.setattr(module, "datetime", FrozenClock)
    first = CSVManager.backup_folder(tmp_path, "Fixture")
    (first / "original.csv").write_bytes(b"original backup")
    folders = [CSVManager.backup_folder(tmp_path, "Fixture") for _ in range(5)]
    assert len(set([first, *folders])) == 6
    assert all(folder.is_dir() and folder.parent == tmp_path / "Fixture" for folder in folders)
    assert (first / "original.csv").read_bytes() == b"original backup"

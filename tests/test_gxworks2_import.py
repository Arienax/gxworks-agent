
from tools.offline_inputs import private_path
import csv
from dataclasses import replace
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_all_native_cpu_entries_have_independent_composition_expansion_slots():
    from plc.target_support import native_target_catalogue, DEFAULT_TARGET_SUPPORT, TargetEvidence, TargetSupportRegistry
    from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
    entries = native_target_catalogue()
    assert len(entries) == 60 and {e["family"] for e in entries} == {"Q", "L", "FX"}
    assert {e["menu_cpu"] for e in entries} == {p.cpu for p in NATIVE_PROJECT_PROFILES}
    assert sum(e["program_supported"] for e in entries) == 57
    assert all(e["operations"]["fbd_topology"]["status"] == ("unverified" if e["program_supported"] else "unsupported") for e in entries)
    assert DEFAULT_TARGET_SUPPORT.target("L02-P")["menu_cpu"] == "L02/L02-P"
    assert DEFAULT_TARGET_SUPPORT.target("Q02H")["members"] == ["Q02", "Q02H"]
    assert DEFAULT_TARGET_SUPPORT.target("FX5U")["status"] == "unknown"
    evidence = TargetEvidence("Q03UDV", "fbd_topology", "verified", "frozen-source", "1.635.0.1", "owned-test-program")
    registry = TargetSupportRegistry([evidence])
    assert registry.target("Q03UDV")["operations"]["fbd_topology"]["status"] == "unverified"
    assert registry.target("Q03UDV", software_version="1.635.0.1", scope="owned-test-program")["operations"]["fbd_topology"]["status"] == "verified"
    assert registry.target("Q04UDV", software_version="1.635.0.1", scope="owned-test-program")["operations"]["fbd_topology"]["status"] == "unverified"
from hypothesis import given, settings, strategies as st

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
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES, native_project_profile, can_prepare_program_project

_NATIVE_CPU_MENU = json.loads((private_path('tests/fixtures/gxworks2_native_cpu_menu.json')).read_text(encoding="utf-8"))["rows"]


def _write_program(
    path,
    *,
    end=True,
    title="MAIN - 副本",
    first_step="0",
    output="Y000",
    statement="",
    note="",
    plc_info="FXCPU FX3U/FX3UC",
):
    rows = [
        [title],
        ["PLC信息:", plc_info],
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
        self.project_path = None
        if project_name not in {"(工程未设置)", "工程未设置"}:
            self.project_path = self.source_backup.with_name("FixtureProject.gxw")
            self.project_path.write_bytes(b"saved GXW before import")
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
            "project_path": str(self.project_path) if self.project_path else self.project_name,
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


@pytest.mark.parametrize('requested,expected', [(None, None), (200, 200), (999, None)])
def test_finder_explicit_process_does_not_adopt_another_gx_window(monkeypatch, requested, expected):
    import gxworks2.finder as module
    title = 'MELSOFT系列 GX Works2'
    class Windows:
        def IsWindowVisible(self, handle): return True
        def GetClassNameW(self, handle, buffer, size): buffer.value = 'GXW2FrameWnd'
        def GetWindowTextLengthW(self, handle): return len(title)
        def GetWindowTextW(self, handle, buffer, size): buffer.value = title
        def GetWindowThreadProcessId(self, handle, pointer): pointer._obj.value = {10: 100, 20: 200}[handle]
        def EnumWindows(self, visit, parameter):
            for handle in (10, 20): visit(handle, parameter)
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(module.ctypes, 'windll', SimpleNamespace(user32=Windows(), kernel32=None), raising=False)
    monkeypatch.setattr(module.ctypes, 'WINFUNCTYPE', lambda *args: lambda function: function, raising=False)
    monkeypatch.setattr(GXWorks2Finder, '_process_image', staticmethod(lambda kernel, pid: 'GD2.exe'))
    monkeypatch.setattr(GXWorks2Finder, '_process_started', staticmethod(lambda kernel, pid: str(pid)))
    assert len(GXWorks2Finder().list_running()) == 2
    if requested is None:
        from agent_runtime.tool_errors import ToolPreconditionError
        with pytest.raises(ToolPreconditionError) as error:
            GXWorks2Finder().find_running()
        assert error.value.code == 'AMBIGUOUS_NATIVE_TARGET'
        return
    actual = GXWorks2Finder().find_running(process_id=requested)
    assert (actual.process_id if actual is not None else None) == expected


@pytest.mark.parametrize('pid', [0, -1, '200', True])
def test_finder_rejects_invalid_explicit_process_id(pid):
    with pytest.raises(ValueError, match='positive integer'):
        GXWorks2Finder().find_running(process_id=pid)










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


@pytest.mark.parametrize("policy", ["project_file", "protected", "manual_backup"])
def test_import_backs_up_gxw_and_imports_once_without_exporting(tmp_path, policy):
    source = tmp_path / "program.csv"
    _write_program(source)
    original_csv = source.read_bytes()
    automation = FakeAutomation(source)
    saved = automation.project_path.read_bytes()
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    def imported(session, path):
        assert Path(path).is_file()
        assert Path(path) == source
        backups = list(service.backup_root.rglob("*.gxw"))
        assert len(backups) == 1 and backups[0].read_bytes() == saved
        automation.events.append("import_program")
        automation.project_path.write_bytes(b"project changed by GX")
        return {"success": True}

    automation.import_program_csv = imported
    result = service.import_current_program(source, pre_import_policy=policy)
    assert result.success
    assert result.details["pre_import_policy"] == "project_file"
    assert result.details["backup_performed"] is True
    assert Path(result.backup_path).read_bytes() == saved
    assert source.read_bytes() == original_csv
    assert automation.events == ["import_program"]
    assert not list(service.backup_root.rglob("*.csv"))


@pytest.mark.parametrize("project_name", ["(工程未设置)", "工程未设置", "(project not set)", "project not set"])
def test_unsaved_project_import_uses_existing_csv_without_backup(tmp_path, project_name):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    automation.project_path = None
    automation.project_name = project_name
    session = replace(_session(), project_name=project_name)
    service = ImportService(FakeFinder(session), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(source)
    assert result.success and result.backup_path == ""
    assert result.details["backup_performed"] is False
    assert automation.events == ["import_program"]
    assert not list(service.backup_root.rglob("*.gxw"))
    assert not list(service.backup_root.rglob("*.csv"))


@pytest.mark.parametrize("failure", ["missing", "unresolved", "abbreviated", "copy_failed"])
def test_gxw_backup_failure_stops_before_native_import(tmp_path, monkeypatch, failure):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    if failure == "missing":
        automation.project_path.unlink()
    elif failure in {"unresolved", "abbreviated"}:
        automation.project_path = "FixtureProject" if failure == "unresolved" else r"...\FixtureProject.gxw"
    else:
        def fail(*args, **kwargs):
            raise OSError("backup disk unavailable")
        monkeypatch.setattr("gxworks2.import_service.shutil.copy2", fail)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    result = service.import_current_program(source)
    assert not result.success and result.error_code == ImportErrorCode.BACKUP_FAILED
    assert result.stage == "backup" and "GXW备份失败" in result.message
    assert result.details["backup_performed"] is False
    assert automation.events == []


def test_import_backs_up_gxw_and_imports_device_comments_once(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    _write_program(source)
    _write_comments(comments, [["X000", "启动"], ["Y000", "电机输出"]])
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    result = service.import_current_program(source, comment_csv_path=comments)

    assert result.success
    assert result.message == "当前程序和软元件注释已导入GX Works2。"
    assert automation.events == ["import_program", "import_comments"]
    assert automation.imported_comments == comments.resolve()
    assert result.details["comment_backup_path"] == ""
    assert Path(result.backup_path).suffix == ".gxw"


def test_empty_comment_csv_preserves_existing_project_comments(tmp_path):
    source = tmp_path / "program.csv"
    comments = tmp_path / "comments.csv"
    _write_program(source)
    _write_comments(comments)
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")

    result = service.import_current_program(source, comment_csv_path=comments)

    assert result.success
    assert automation.events == ["import_program"]
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


@settings(max_examples=60)
@given(prefix=st.sampled_from(['X', 'Y', 'M', 'D', 'SM', 'SD', 'B', 'W']),
       number=st.integers(min_value=0, max_value=4095), padding=st.integers(min_value=1, max_value=6),
       literal=st.text(alphabet='abAB ０0123xyXYZ', min_size=0, max_size=30))
def test_native_operand_spelling_preserves_addresses_and_literal_data(prefix, number, padding, literal):
    digits = format(number, 'X') if prefix in ('X', 'Y', 'B', 'W') else str(number)
    plain = prefix + digits
    padded = prefix.lower() + '0' * padding + digits.lower()
    expected = f'{plain} "{literal}"'
    cell = CSVManager._program_operand_cell
    assert cell(f'{padded}   "{literal}"') == expected
    assert cell(expected) == expected


def test_native_operand_equivalence_does_not_convert_radix_or_fold_strings():
    cell = CSVManager._program_operand_cell
    assert cell('X010') == 'X10'
    assert cell('X010') != cell('X8')
    assert cell('"a B" D001') == '"a B" D1'
    assert cell('"a B" D001') != cell('"A B" D1')
    assert cell('"a  B" D1') != cell('"a B" D1')
    assert cell('D001Z02') == 'D1Z2'


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
    assert protection["status"] == "baseline_recorded"
    assert protection["roundtrip_verified"] is False
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


def test_each_import_preserves_the_latest_saved_gxw_without_csv_readback(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    first = service.import_current_program(source)
    assert first.success
    automation.project_path.write_bytes(b"engineer saved changes")
    automation.events.clear()
    second = service.import_current_program(source)
    assert second.success
    assert first.backup_path != second.backup_path
    assert Path(first.backup_path).read_bytes() == b"saved GXW before import"
    assert Path(second.backup_path).read_bytes() == b"engineer saved changes"
    assert second.details["version_protection"]["baseline_checked"] is False
    assert automation.events == ["import_program"]


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
    assert result.error_code is None
    assert result.details["version_protection"]["status"] == "baseline_write_failed"


def test_import_does_not_require_reading_an_old_csv_baseline(tmp_path, monkeypatch):
    source = tmp_path / "program.csv"
    _write_program(source)
    automation = FakeAutomation(source)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    def forbidden(*args, **kwargs):
        pytest.fail("Import must not read the old baseline or export CSV")
    monkeypatch.setattr(service.baseline_store, "load", forbidden)
    monkeypatch.setattr(automation, "export_current_program", forbidden)
    monkeypatch.setattr(automation, "export_current_comments", forbidden)
    result = service.import_current_program(source)
    assert result.success and Path(result.backup_path).is_file()
    assert automation.events == ["import_program"]


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
    assert protection["baseline_found"] is None
    assert protection["baseline_checked"] is False
    assert protection["project_identity"]["project"] == (
        "unsaved|process:101|window:202|plc-ai:plc-ai-project-1"
    )


def test_unsaved_project_reimport_does_not_trigger_csv_exports(tmp_path):
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

    assert second.success
    assert second.backup_path == ""
    assert automation.events == ["import_program", "import_program"]


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
    commands = []
    monkeypatch.setattr(automation, "_post_window_command", lambda target, command: commands.append((target, command)))

    automation._send_edit_accelerator(window, automation.PROGRAM_READ_KEY)

    assert window.keys == []
    assert commands == [(window, 0x755)]


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
        "_post_native_command",
        lambda _session, command: write_shortcuts.append(command),
    )

    assert automation._ensure_program_writable(object()) is window
    assert write_shortcuts == [0x200]


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
        "_post_native_command",
        lambda _session, command: write_shortcuts.append(command),
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


def test_native_application_binding_reuses_only_the_same_process_window_and_backend(monkeypatch):
    calls = []

    class App:
        def __init__(self, **kwargs):
            self.backend = kwargs["backend"]

        def connect(self, **kwargs):
            calls.append((self.backend, kwargs["process"]))
            return self

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=App))
    automation = PywinautoGXWorks2UIAutomation()
    session = _session()
    original = automation._application(session)
    assert automation._application(session) is original
    assert calls == [("win32", 1)]
    assert automation._application(replace(session, window_handle=3)) is not original
    assert automation._application(replace(session, process_id=4)) is not original
    assert calls == [("win32", 1), ("win32", 1), ("win32", 4)]


@pytest.mark.parametrize("comment,read,key", [(False, True, "j"), (False, False, "k"),
                                              (True, True, "f"), (True, False, "o")])
def test_csv_retry_uses_native_command_instead_of_accessibility_search(monkeypatch, comment, read, key):
    automation = PywinautoGXWorks2UIAutomation()
    window = SimpleNamespace(window_text=lambda: "软元件注释 COMMENT" if comment else "[PRG]写入 MAIN 3步")
    calls = []
    monkeypatch.setattr(automation, "_send_edit_accelerator", lambda target, value: calls.append((target, value)))
    automation._csv_command(window, automation.READ_CSV_ITEM if read else automation.WRITE_CSV_ITEM).invoke()
    assert calls == [(window, key)]


@pytest.mark.parametrize("focused,allowed", [(31, True), (37, True), (99, False), ([0,0,37],True)])
def test_native_document_focus_accepts_its_internal_control_and_detaches_thread(monkeypatch, focused, allowed):
    import gxworks2.ui_automation as module

    attachments = []
    focus_values=iter(focused) if isinstance(focused,list) else None

    def title(_handle, buffer, _length):
        buffer.value = "[PRG]写入 MAIN 3步"

    user32 = SimpleNamespace(
        GetWindowTextLengthW=lambda _: 25, GetWindowTextW=title,
        EnumChildWindows=lambda _frame, callback, _data: callback(31, 0),
        GetParent=lambda _: 10, SendMessageW=lambda *_: 0,
        SetForegroundWindow=lambda _: True, GetWindowThreadProcessId=lambda *_: 42,
        AttachThreadInput=lambda a, b, attach: attachments.append((a, b, attach)) or True,
        SetFocus=lambda _: 0, GetFocus=lambda: next(focus_values) if focus_values else focused,
        IsChild=lambda parent, child: parent == 31 and child == 37)
    monkeypatch.setattr(module.ctypes, "windll", SimpleNamespace(user32=user32,
        kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 7)), raising=False)
    monkeypatch.setattr(module.os, "name", "nt")
    automation = PywinautoGXWorks2UIAutomation()
    if allowed:
        assert automation._activate_native_document(_session(), automation.PROGRAM_EDITOR)
    else:
        with pytest.raises(RuntimeError, match="键盘焦点"):
            automation._activate_native_document(_session(), automation.PROGRAM_EDITOR)
    assert attachments == [(7, 42, True), (7, 42, False)]


@pytest.mark.parametrize('title', ['[PRG]读取 MAIN 0步','[PRG]写入 MAIN 0步','[PRG]Read MAIN 0Step'])
def test_new_project_accepts_created_main_then_verifies_write_mode(monkeypatch,title):
    automation=PywinautoGXWorks2UIAutomation(timeout=.1)
    session=_session()
    activated=[]
    monkeypatch.setattr(automation,'_native_dialog_handles',lambda _:[])
    monkeypatch.setattr(automation,'_child_window_titles',lambda _:[title])
    monkeypatch.setattr(automation,'_ensure_program_writable',lambda target:activated.append(target))
    automation._wait_created_program(session,SimpleNamespace(program_supported=True),SimpleNamespace())
    assert activated==[session]


@pytest.mark.parametrize('modal',[False,True])
def test_new_project_still_rejects_missing_main_or_unknown_modal(monkeypatch,modal):
    automation=PywinautoGXWorks2UIAutomation(timeout=.001)
    monkeypatch.setattr(automation,'_native_dialog_handles',lambda _:[9] if modal else [])
    monkeypatch.setattr(automation,'_child_window_titles',lambda _:['MAIN','PLC参数'])
    monkeypatch.setattr(automation,'_ensure_program_writable',lambda _:pytest.fail('no current source editor'))
    with pytest.raises(RuntimeError if modal else TimeoutError):
        automation._wait_created_program(_session(),SimpleNamespace(program_supported=True),SimpleNamespace())


@pytest.mark.parametrize('becomes_ready',[False,True])
def test_new_project_waits_for_current_source_tree_after_mdi_title(monkeypatch,becomes_ready):
    from gxworks2.ui_automation import NativeEditorNotFound
    automation=PywinautoGXWorks2UIAutomation(timeout=.15)
    checks=[]
    monkeypatch.setattr(automation,'_native_dialog_handles',lambda _:[])
    monkeypatch.setattr(automation,'_child_window_titles',lambda _:['[PRG]读取 MAIN 0步'])
    def verify(_):
        checks.append(True)
        if len(checks)==1 or not becomes_ready:
            raise NativeEditorNotFound('source tree not initialized')
    monkeypatch.setattr(automation,'_ensure_program_writable',verify)
    if becomes_ready:
        automation._wait_created_program(_session(),SimpleNamespace(program_supported=True),SimpleNamespace())
        assert len(checks)==2
    else:
        with pytest.raises(TimeoutError):
            automation._wait_created_program(_session(),SimpleNamespace(program_supported=True),SimpleNamespace())


@settings(max_examples=48, deadline=None)
@given(order=st.permutations(("source", "task", "disabled", "function_block")),
       comment=st.booleans())
def test_native_navigation_selects_exact_source_despite_tree_order_and_decoys(order, comment):
    # Paths frozen from the native navigation probe. A task also has a MAIN
    # leaf, and another pane can expose a disabled project tree.
    expected = ("全局软元件注释",) if comment else ("程序部件", "程序", "MAIN")
    activated = []
    window = SimpleNamespace(window_text=lambda: "PLC参数")

    class Tree:
        def __init__(self, name):
            self.name = name

        def control_id(self):
            return 12051 if self.name == "source" else 12050

        def is_visible(self):
            return True

        def is_enabled(self):
            return self.name != "disabled"

        def get_item(self, path, exact=False):
            if (self.name in {"source", "disabled"} and tuple(path) == expected) or not exact:
                return SimpleNamespace(elem=self.name)
            raise IndexError("path not present")

        def send_message(self, message, key, data):
            if message == 0x110B:
                assert key == 9 and data == self.name
                activated.append(self.name)
                return 1
            assert message in {0x100, 0x101} and key == 0x0D
            window.window_text = lambda: "软元件注释 COMMENT" if comment else "[PRG]写入 MAIN 1步"

    def descendants(**criteria):
        assert criteria == {"class_name": "SysTreeView32"}, "UIA traversal is forbidden"
        return [Tree(name) for name in order]

    window.descendants = descendants
    automation = PywinautoGXWorks2UIAutomation()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(automation, "_main_window", lambda _: window)
        patch.setattr(automation, "_activate_native_document", lambda *_: False)
        if comment:
            automation._activate_comments_editor(_session())
        else:
            automation._activate_program_editor(_session())
    assert activated == ["source"]


def test_parameter_navigation_blocks_a_residual_main_before_activation(monkeypatch):
    automation = PywinautoGXWorks2UIAutomation()
    from gxworks2.ui_automation import NativeEditorNotFound

    def missing(*args, **kwargs):
        raise IndexError("parameter-only navigation")

    tree = SimpleNamespace(control_id=lambda: 12051, get_item=missing)
    window = SimpleNamespace(window_text=lambda: "[PRG]写入 MAIN 1步",
                             descendants=lambda **_: [tree])
    monkeypatch.setattr(automation, "_main_window", lambda _: window)
    monkeypatch.setattr(automation, "_activate_native_document", lambda *_: pytest.fail("stale MAIN must not be activated"))
    with pytest.raises(NativeEditorNotFound, match="没有MAIN程序源"):
        automation._activate_program_editor(_session())


def test_native_tree_selection_requires_the_original_control_acknowledgement():
    automation = PywinautoGXWorks2UIAutomation()
    tree = SimpleNamespace(send_message=lambda *_: 0)
    with pytest.raises(RuntimeError, match="未确认节点选择"):
        automation._select_native_tree_item(tree, SimpleNamespace(elem=7))


def test_native_combo_changes_selection_before_end_notification_without_closeup():
    messages = []
    control = SimpleNamespace(item_texts=lambda: ["Q100UDEH", "QJ72LP25/QJ72BR15(RemoteI/O)"],
                              is_enabled=lambda: True, selected_text=lambda: "QJ72LP25/QJ72BR15(RemoteI/O)",
                              send_message=lambda *args: messages.append(args) or 1,
                              notify_parent=lambda code: messages.append(code))
    PywinautoGXWorks2UIAutomation._select_native_combo(control, "QJ72LP25/QJ72BR15(RemoteI/O)")
    assert messages == [(0x14E, 1, 0), 1, 9]


@pytest.mark.parametrize('checked', [False, True])
def test_native_label_checkbox_updates_the_dialog_language_options(checked):
    state = {'checked': not checked, 'languages': ['Ladder', 'SFC']}
    def notify(code):
        assert code == 0
        state['languages'] = ['Ladder', 'ST'] if state['checked'] else ['Ladder', 'SFC']
    control = SimpleNamespace(is_enabled=lambda: True,
        get_check_state=lambda: int(state['checked']),
        check=lambda: state.update(checked=True), uncheck=lambda: state.update(checked=False),
        notify_parent=notify)
    PywinautoGXWorks2UIAutomation._set_native_check(control, checked)
    assert ('ST' in state['languages']) is checked


@pytest.mark.parametrize("already_running", [False, True])
@pytest.mark.parametrize("model,menu_cpu", [("FX3U", "FX3U/FX3UC"), ("Q03UDV", "Q03UDV"), ("L02-P", "L02/L02-P")])
def test_explicit_managed_target_can_start_create_and_import(tmp_path, already_running, model, menu_cpu):
    source, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    _write_program(source, plc_info="三菱 GX Works2 兼容")
    _write_comments(comments, [["X0", "启动"]])
    finder = FakeFinder(_session(False) if already_running else None)
    automation = FakeAutomation(source)
    events = []

    def start():
        events.append("start")
        finder.session = _session(False)
        return Path("GD2.exe")

    def create(session, path, *, cpu, progress):
        assert session == _session(False)
        assert path == tmp_path / "owned.gxw" and cpu == menu_cpu
        events.append("create")
        progress("prepare_project", "MAIN已新建，正在首次保存GX工程…")
        finder.session = _session(True)

    finder.start = start
    automation.create_simple_project = create
    result = ImportService(finder, automation, CSVManager(), tmp_path / "backups").import_current_program(
        source, comment_csv_path=comments, start_if_needed=True,
        create_project_path=tmp_path / "owned.gxw", import_context={"plc_model": model},
        pre_import_policy="manual_backup")
    assert result.success
    assert events == (["create"] if already_running else ["start", "create"])
    assert automation.events == ["import_program", "import_comments"]
    assert "prepare_project" in result.details["timings_ms"]
    assert ("start_gxworks2" in result.details["timings_ms"]) is not already_running


@pytest.mark.parametrize('length', [199, 200, 201])
def test_new_native_project_path_limit_is_checked_before_launch(tmp_path, length):
    source = tmp_path / 'program.csv'
    _write_program(source, plc_info='LCPU L02')
    finder, automation = FakeFinder(None), FakeAutomation(source)
    starts = []
    finder.start = lambda: starts.append(True) or None
    prefix = tmp_path.resolve()
    target = prefix / ('d' * (length - len(str(prefix)) - len('ref.gxw') - 2)) / 'ref.gxw'
    assert len(str(target)) == length
    result = ImportService(finder, automation, CSVManager(), tmp_path / 'backups').import_current_program(
        source, start_if_needed=True, create_project_path=target, import_context={'plc_model': 'L02'})
    assert not result.success and automation.events == []
    if length > 200:
        assert result.stage == 'prepare_project' and '200' in result.message and starts == []
    else:
        assert result.stage == 'find_gxworks2' and starts == [True]


@pytest.mark.parametrize("model", [None, "A1S", "Q02(H)-A", "FX5U", "LJ72MS15"])
def test_unverified_cpu_does_not_start_gx_or_create_target(tmp_path, model):
    source = tmp_path / "program.csv"
    _write_program(source)
    finder, automation = FakeFinder(None), FakeAutomation(source)

    def forbidden(*args, **kwargs):
        raise AssertionError("an unverified CPU cannot launch or create GX")

    finder.start = automation.create_simple_project = forbidden
    result = ImportService(finder, automation, CSVManager(), tmp_path / "backups").import_current_program(
        source, start_if_needed=True, create_project_path=tmp_path / "owned.gxw",
        import_context={"plc_model": model})
    assert not result.success and result.stage == "prepare_project"
    assert automation.events == []


@pytest.mark.parametrize("foreign_pid,opened", [(99, False), (1, True)])
def test_automatic_start_does_not_adopt_a_foreign_or_preopened_target(tmp_path, foreign_pid, opened):
    source = tmp_path / "program.csv"
    _write_program(source)
    finder, automation = FakeFinder(None), FakeAutomation(source)

    def start():
        finder.started_process_id = 1
        finder.session = replace(_session(opened), process_id=foreign_pid)
        return Path("GD2.exe")

    finder.start = start
    result = ImportService(finder, automation, CSVManager(), tmp_path / "backups").import_current_program(
        source, start_if_needed=True, create_project_path=tmp_path / "owned.gxw",
        import_context={"plc_model": "FX3U"})
    assert not result.success and result.stage == "start_gxworks2"
    assert automation.events == []


def test_existing_project_is_kept_and_default_caller_does_not_create_one(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    finder, automation = FakeFinder(_session()), FakeAutomation(source)

    def forbidden(*args, **kwargs):
        raise AssertionError("must not replace the open target")

    automation.create_simple_project = forbidden
    service = ImportService(finder, automation, CSVManager(), tmp_path / "backups")
    assert service.import_current_program(source, create_project_path=tmp_path / "owned.gxw").success
    finder.session = _session(False)
    automation.events.clear()
    result = service.import_current_program(source)
    assert not result.success and result.error_code == ImportErrorCode.TARGET_PROJECT_NOT_OPEN
    assert automation.events == []


@pytest.mark.parametrize("changed", ["process_id", "window_handle", "project_open"])
def test_new_target_requires_same_native_session_before_import(tmp_path, changed):
    source = tmp_path / "program.csv"
    _write_program(source)
    finder, automation = FakeFinder(_session(False)), FakeAutomation(source)

    def create(*args, **kwargs):
        finder.session = replace(_session(), **{changed: False if changed == "project_open" else 99})

    automation.create_simple_project = create
    result = ImportService(finder, automation, CSVManager(), tmp_path / "backups").import_current_program(
        source, create_project_path=tmp_path / "owned.gxw", import_context={"plc_model": "FX3U"})
    assert not result.success and result.error_code == ImportErrorCode.AUTOMATION_FAILED
    assert automation.events == []


@pytest.mark.parametrize("row", _NATIVE_CPU_MENU, ids=lambda row: row["cpu"])
@pytest.mark.parametrize('ui_language,new_title,save_title,simple_type,ladder_type', [
    ('zh-CN', '新建', '工程另存为', '简单工程', '梯形图'),
    ('en', 'New Project', 'Save As', 'Simple Project', 'Ladder'),
])
def test_native_project_creation_selects_explicit_cpu_type_and_language(
        tmp_path, monkeypatch, row, ui_language, new_title, save_title, simple_type, ladder_type):
    cpu, family = row["cpu"], row["series"]
    if ui_language == 'en':
        family = next(text for text in native_project_profile(cpu).series if text.isascii())
    path = tmp_path / "owned.gxw"
    selections = []
    controls = {}
    for control_id, choices in ((10002, [family]), (10003, [cpu]),
                                (10001, ["Structured Project", simple_type]), (10070, ["ST", ladder_type])):
        controls[control_id] = SimpleNamespace(
            item_texts=lambda choices=choices: choices,
            is_enabled=lambda control_id=control_id: row["type_enabled"] if control_id == 10001 else row["language_enabled"] if control_id == 10070 else True,
            selected_text=lambda control_id=control_id: {10001: simple_type, 10070: ladder_type, 10003: cpu, 10002: family}[control_id],
            select=lambda value, control_id=control_id: selections.append((control_id, value)))
    label_state = [1]
    def uncheck():
        label_state[0] = 0
        selections.append((10068, False))
    controls[10068] = SimpleNamespace(is_enabled=lambda: row["labels_enabled"], get_check_state=lambda: label_state[0],
                                     uncheck=uncheck, notify_parent=lambda code: selections.append((10068, 'BN_CLICKED', code)))
    controls[1] = SimpleNamespace(click=lambda: selections.append((1, "OK")))
    dialog = SimpleNamespace(child_window=lambda **kw: controls[kw["control_id"]])
    main = SimpleNamespace(type_keys=lambda key: selections.append(("key", key)), window_text=lambda: "(工程未设置) " + path.name)
    automation = PywinautoGXWorks2UIAutomation()
    monkeypatch.setattr(automation, "_select_native_combo", lambda control, value: control.select(value))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(GetFileVersionInfo=lambda *_: {
        "FileVersionMS": (1 << 16) + 635, "FileVersionLS": 1}))
    monkeypatch.setattr(automation, "_main_window", lambda _: main)
    monkeypatch.setattr('gxworks2.native_locale.inspect_native_language', lambda *_: ui_language)
    monkeypatch.setattr(automation, "_post_native_command", lambda _, command: selections.append(("command", command)))
    titles = iter([new_title, save_title])
    def wait_dialog(_session, pattern):
        assert pattern.fullmatch(next(titles))
        assert not pattern.fullmatch('Open Project')
        return dialog
    monkeypatch.setattr(automation, "_wait_legacy_dialog", wait_dialog)
    monkeypatch.setattr(automation, "_native_dialog_handles", lambda _: [])
    monkeypatch.setattr(automation, "_child_window_titles", lambda _: ["[PRG]写入 MAIN 1步"])
    monkeypatch.setattr(automation, "_ensure_program_writable", lambda _: selections.append(("mode", "write")))
    monkeypatch.setattr(automation, "_set_legacy_file_name", lambda _, target: target.write_bytes(b"GX native witness"))
    result = automation.create_simple_project(_session(False), path, cpu=cpu)
    assert result["success"]
    expected = [("command", 0x3FF), (10002, family)]
    if row["type_enabled"]:
        expected.append((10001, simple_type))
    expected.append((10003, cpu))
    if row["type_enabled"]:
        expected.append((10001, simple_type))
    if row["labels_enabled"]:
        expected.append((10068, False))
        expected.append((10068, 'BN_CLICKED', 0))
    if row["language_enabled"]:
        expected.append((10070, ladder_type))
    expected.append((1, "OK"))
    if row["language_enabled"]:
        expected.append(("mode", "write"))
    expected.append(("command", 0x402))
    assert selections == expected
    with pytest.raises(RuntimeError, match="独立保存路径无效"):
        automation.create_simple_project(_session(False), path, cpu="FX3U/FX3UC")


def test_native_cpu_catalogue_matches_frozen_menu_and_excludes_developer_entries():
    assert len(NATIVE_PROJECT_PROFILES) == len(_NATIVE_CPU_MENU) == 60
    assert {p.cpu for p in NATIVE_PROJECT_PROFILES} == {r["cpu"] for r in _NATIVE_CPU_MENU}
    for row in _NATIVE_CPU_MENU:
        profile = native_project_profile(row["cpu"])
        assert row["series"] in profile.series
        assert profile.program_supported == row["language_enabled"]
        for member in row["cpu"].removesuffix("(RemoteI/O)").split("/"):
            assert native_project_profile(member).cpu == row["cpu"]
    for name in ["ACPU", "A1S", "QnACPU", "Q2A", "Q02(H)-A", "QS001", "Q172", "FX5U"]:
        assert native_project_profile(name) is None


@settings(max_examples=60)
@given(row=st.sampled_from(_NATIVE_CPU_MENU), lower=st.booleans(), whitespace=st.sampled_from(["", " ", "\t"]))
def test_native_cpu_aliases_preserve_the_merged_menu_identity(row, lower, whitespace):
    for member in [row["cpu"], *row["cpu"].removesuffix("(RemoteI/O)").split("/")]:
        name = member.lower() if lower else member
        profile = native_project_profile(whitespace + name + whitespace)
        assert profile.cpu == row["cpu"] and row["series"] in profile.series
        assert can_prepare_program_project(name) == row["language_enabled"]


def test_cpu_header_mismatch_stops_before_launch(tmp_path):
    source = tmp_path / "program.csv"
    _write_program(source)
    finder, automation = FakeFinder(None), FakeAutomation(source)
    finder.start = lambda: pytest.fail("mismatched native CPU header must not launch GX")
    result = ImportService(finder, automation, CSVManager(), tmp_path / "backups").import_current_program(
        source, start_if_needed=True, create_project_path=tmp_path / "owned.gxw", import_context={"plc_model": "Q03UDV"})
    assert not result.success and result.stage == "prepare_project" and automation.events == []


def _save_notice_dialog_double(monkeypatch, *, language='zh-CN', fault=None, recognized=True, system_button_language=None):
    import gxworks2.ui_automation as module
    from gxw.native_locale import ui_text

    automation = PywinautoGXWorks2UIAutomation()
    clicked = []
    text = ui_text('auth_save_notice', language) if recognized else "覆盖现有工程文件，确定吗？"
    if fault == 'wrong_language':
        text = ui_text('auth_save_notice', 'en')
    classes = {7: '#32770', 8: 'Static', 9: 'Button', 10: 'Button', 11: 'Static'}
    captions = {8: text, 9: ui_text('ok', system_button_language or language),
                10: 'Other action', 11: 'Other notice'}
    if fault == 'dialog_class':
        classes[7] = 'OtherWindow'
    if fault == 'button_class':
        classes[9] = 'Edit'
    if fault == 'button_caption':
        captions[9] = 'Delete'
    descendants = [8, 9] + ([10] if fault == 'extra_button' else [11] if fault == 'extra_message' else [])
    pid_reads, owner_reads, window_reads = {}, [], []
    def class_name(handle, buffer, _):
        buffer.value = classes[handle]
    def process_id(handle, pointer):
        pid_reads[handle] = pid_reads.get(handle, 0) + 1
        foreign = (fault == 'dialog_pid' and handle == 7 or fault == 'frame_pid' and handle == 2
            or fault == 'button_pid' and handle == 9
            or fault == 'dialog_pid_changed' and handle == 7 and pid_reads[handle] > 1)
        pointer._obj.value = 999 if foreign else 1
    def owner(handle, flag):
        assert (handle, flag) == (7, 3)
        owner_reads.append(handle)
        return 999 if fault == 'dialog_owner' or fault == 'owner_changed' and len(owner_reads) > 1 else 2
    def is_window(handle):
        window_reads.append(handle)
        return not (fault == 'dialog_gone' and handle == 7 or fault == 'button_gone' and handle == 9
            or fault == 'dialog_gone_after_read' and handle == 7 and window_reads.count(handle) > 1)

    user32 = SimpleNamespace(GetClassNameW=class_name,
        IsWindowVisible=lambda handle: not (fault == 'dialog_hidden' and handle == 7
                                           or fault == 'button_hidden' and handle == 9),
        GetDlgCtrlID=lambda handle: 1 if fault == 'button_id' else 2 if handle == 9 else 3,
        IsWindow=is_window, GetWindowThreadProcessId=process_id, GetAncestor=owner,
        IsWindowEnabled=lambda handle: fault != 'button_disabled',
        IsChild=lambda parent, child: (parent, child) == (7, 9) and fault != 'detached_button',
        SendMessageW=lambda *args: clicked.append(args))
    monkeypatch.setattr(module.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(automation, "_native_dialog_handles", lambda _: [7])
    monkeypatch.setattr(automation, "_native_dialog_text", lambda _: text)
    monkeypatch.setattr(automation, "_native_descendants", lambda _: descendants)
    monkeypatch.setattr(automation, "_native_window_title", lambda handle: captions[handle])
    return automation, clicked, text


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize("recognized", [True, False])
def test_save_acknowledges_only_the_frozen_one_button_parameter_notice(monkeypatch, language, recognized):
    automation, clicked, text = _save_notice_dialog_double(monkeypatch, language=language, recognized=recognized)
    # The unqualified Chinese call remains the legacy contract.
    options = {} if language == 'zh-CN' else dict(native_ui_language=language)
    if recognized:
        assert automation._acknowledge_known_save_notices(_session(), **options) == [text]
        assert clicked == [(9, 0x00F5, 0, 0)]
    else:
        with pytest.raises(RuntimeError, match="未适配的提示"):
            automation._acknowledge_known_save_notices(_session(), **options)
        assert clicked == []


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('system_button_language', ['zh-CN', 'en', 'ja'])
def test_known_notice_binds_ide_body_separately_from_windows_button_language(monkeypatch, language, system_button_language):
    automation, clicked, text = _save_notice_dialog_double(monkeypatch, language=language,
        system_button_language=system_button_language)
    assert automation._acknowledge_known_save_notices(_session(), native_ui_language=language) == [text]
    assert clicked == [(9, 0x00F5, 0, 0)]


@pytest.mark.parametrize('fault', ['wrong_language', 'dialog_class', 'dialog_pid', 'dialog_owner', 'frame_pid',
    'dialog_hidden', 'extra_button', 'extra_message', 'button_id', 'button_caption', 'button_class',
    'button_pid', 'button_hidden', 'button_disabled', 'button_gone', 'detached_button',
    'dialog_pid_changed', 'owner_changed'])
def test_save_notice_rejects_changed_or_foreign_dialog_structure_without_clicking(monkeypatch, fault):
    automation, clicked, _ = _save_notice_dialog_double(monkeypatch, fault=fault)
    with pytest.raises(RuntimeError, match='保存提示|未适配'):
        automation._acknowledge_known_save_notices(_session())
    assert clicked == []


@pytest.mark.parametrize('fault', ['dialog_gone', 'dialog_gone_after_read'])
def test_save_notice_disappeared_during_observation_is_not_clicked(monkeypatch, fault):
    automation, clicked, _ = _save_notice_dialog_double(monkeypatch, fault=fault)
    assert automation._acknowledge_known_save_notices(_session()) == []
    assert clicked == []


def test_save_notice_legacy_text_still_matches_the_known_resource_identity():
    from gxw.native_locale import ui_text
    assert PywinautoGXWorks2UIAutomation.USER_AUTH_SAVE_NOTICE == ui_text('auth_save_notice', 'zh-CN')


def test_finder_binds_only_the_native_frame_even_if_modal_has_same_title(monkeypatch):
    import gxworks2.finder as module

    def class_name(handle, buffer, _):
        buffer.value = "#32770" if handle == 7 else "GXW2FrameWnd"

    def title(handle, buffer, _):
        buffer.value = "MELSOFT系列 GX Works2" if handle == 7 else "MELSOFT系列 GX Works2 owned.gxw - [MAIN]"

    def process_id(_, pointer):
        pointer._obj.value = 1

    user32 = SimpleNamespace(IsWindowVisible=lambda _: True, GetClassNameW=class_name,
        GetWindowTextLengthW=lambda _: 80, GetWindowTextW=title, GetWindowThreadProcessId=process_id,
        EnumWindows=lambda callback, _: [callback(h, 0) for h in [7, 9]])
    monkeypatch.setattr(module.ctypes, "windll", SimpleNamespace(user32=user32, kernel32=object()), raising=False)
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.GXWorks2Finder, "_process_image", lambda *_: "GD2.exe")
    monkeypatch.setattr(module.GXWorks2Finder, "_process_started", lambda *_: "start")
    target = GXWorks2Finder().find_running()
    assert target.window_handle == 9 and target.project_open


@pytest.mark.parametrize("owner,version,expected", [(1, 635, True), (99, 635, False), (1, 634, False)])
def test_native_command_binds_original_pid_and_supported_version(monkeypatch, owner, version, expected):
    posted, closed = [], []
    process = SimpleNamespace(Close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(IsWindow=lambda _: True,
        PostMessage=lambda *args: posted.append(args)))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda _: (4, owner),
        GetModuleFileNameEx=lambda *_: "D:/GXWORKS2/GPPW2/GD2.exe"))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda access, inherited, pid: process,
        GetFileVersionInfo=lambda *_: {"FileVersionMS": (1 << 16) + version, "FileVersionLS": 1}))
    automation = PywinautoGXWorks2UIAutomation()
    automation._application_binding = ((1, 2, "win32"), object())
    # A HWND wrapper can report its new owner after handle reuse. The guard
    # must use the originally bound PID, not compare two current PID reads.
    window = SimpleNamespace(handle=2, process_id=lambda: owner)
    if expected:
        automation._post_window_command(window, 0x755)
        assert posted == [(2, 0x111, 0x755, 0)]
    else:
        with pytest.raises(RuntimeError):
            automation._post_window_command(window, 0x755)
        assert posted == []
    assert closed == ([True] if owner == 1 else [])


_PROJECT_CODEPAGE_STATUS_CASES = [
    ('zh-CN', '日文', [932]), ('zh-CN', '英语', [1252]),
    ('zh-CN', '简体中文', [936, 54936]), ('zh-CN', '韩文', [949]), ('zh-CN', '繁体中文', [950]),
    ('en', 'Japanese', [932]), ('en', 'English', [1252]),
    ('en', 'Chinese (Simplified)', [936, 54936]), ('en', 'Korean', [949]),
    ('en', 'Chinese (Traditional)', [950]),
    ('ja', '日本語', [932]), ('ja', '英語', [1252]),
    ('ja', '中国語（簡体字）', [936, 54936]), ('ja', '韓国語', [949]), ('ja', '中国語（繁体字）', [950]),
]


def _project_codepage_statusbar_double(monkeypatch, texts):
    state = SimpleNamespace(texts=list(texts), reads=0, after_read=None,
        windows={2, 3, 4}, pids={2: 1, 3: 1, 4: 1}, children={(2, 3), (2, 4)},
        visible={2, 3, 4}, classes={3: 'msctls_statusbar32', 4: 'msctls_statusbar32'},
        ids={3: 59393, 4: 59393})
    def read_texts():
        state.reads += 1
        result = list(state.texts)
        if state.after_read:
            state.after_read()
        return result
    bar = SimpleNamespace(handle=3, control_id=lambda: 59393, texts=read_texts)
    state.bars = [bar]
    def descendants(**criteria):
        assert criteria == dict(class_name='msctls_statusbar32')
        return list(state.bars)
    state.frame = SimpleNamespace(handle=2, descendants=descendants)
    automation = PywinautoGXWorks2UIAutomation()
    monkeypatch.setattr(automation, '_main_window', lambda session: state.frame)
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        IsWindow=lambda handle: handle in state.windows,
        IsWindowVisible=lambda handle: handle in state.visible,
        IsChild=lambda parent, child: (parent, child) in state.children,
        GetClassName=lambda handle: state.classes.get(handle, 'GXW2FrameWnd'),
        GetDlgCtrlID=lambda handle: state.ids.get(handle, 0)))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(
        GetWindowThreadProcessId=lambda handle: (7, state.pids[handle])))
    return automation, state


@pytest.mark.parametrize('language,text,candidates', _PROJECT_CODEPAGE_STATUS_CASES)
def test_current_native_project_codepage_reads_exact_localized_status_and_preserves_candidates(
        monkeypatch, language, text, candidates):
    from gxw.native_locale import project_codepage_status
    raw_text = ' ' + text + ' '
    # StatusBarWrapper can repeat its first pane as the window text.
    texts = ['', 'FX3U/FX3UC', raw_text, text, 'Ln 1', 'Col 1']
    automation, state = _project_codepage_statusbar_double(monkeypatch, texts)
    expected = dict(status='observed', codepage_candidates=candidates, status_text=raw_text)
    assert project_codepage_status(texts, language=language) == expected
    result = automation._current_native_project_codepage_observation(_session(), language)
    assert result == expected and state.reads == 1
    result['codepage_candidates'].clear()
    assert automation._current_native_project_codepage_observation(_session(), language) == expected


@pytest.mark.parametrize('language,texts', [
    ('zh-CN', ['', 'FX3U/FX3UC']), ('en', ['English (default)']), ('ja', ['GB18030']),
    ('zh-CN', ['English']), ('en', ['日本語']), ('ja', ['简体中文']),
    ('zh-CN', ['英语', '日文']), ('en', ['English', 'Japanese']),
    ('ja', ['日本語', 'English']), ('en', ['English\0']), ('en', ['English', None]),
])
def test_project_language_status_rejects_unknown_mixed_or_malformed_text(language, texts):
    from gxw.models import GXWFormatError
    from gxw.native_locale import project_codepage_status
    with pytest.raises(GXWFormatError, match='project-language'):
        project_codepage_status(texts, language=language)


@pytest.mark.parametrize('fault', ['missing_bar', 'duplicate_bar', 'hidden_bar', 'foreign_bar_pid',
    'foreign_frame_pid', 'not_a_child', 'wrong_class', 'wrong_native_id', 'stale_frame',
    'stale_bar', 'wrong_frame_handle', 'bar_changed_during_read'])
def test_current_project_language_status_is_bound_to_one_current_native_frame(monkeypatch, fault):
    automation, state = _project_codepage_statusbar_double(monkeypatch, ['FX3U/FX3UC', 'English'])
    if fault == 'missing_bar':
        state.bars = []
    elif fault == 'duplicate_bar':
        state.bars.append(SimpleNamespace(handle=4, control_id=lambda: 59393))
    elif fault == 'hidden_bar':
        state.visible.remove(3)
    elif fault == 'foreign_bar_pid':
        state.pids[3] = 99
    elif fault == 'foreign_frame_pid':
        state.pids[2] = 99
    elif fault == 'not_a_child':
        state.children.clear()
    elif fault == 'wrong_class':
        state.classes[3] = 'Edit'
    elif fault == 'wrong_native_id':
        state.ids[3] = 100
    elif fault == 'stale_frame':
        state.windows.remove(2)
    elif fault == 'stale_bar':
        state.windows.remove(3)
    elif fault == 'wrong_frame_handle':
        state.frame.handle = 9
    else:
        state.after_read = lambda: state.pids.update({3: 99})
    with pytest.raises(RuntimeError, match='工程语言|状态栏'):
        automation._current_native_project_codepage_observation(_session(), 'en')
    assert state.reads == (1 if fault == 'bar_changed_during_read' else 0)


def test_current_project_language_status_is_not_cached_or_derived_from_the_host(monkeypatch):
    import locale
    def forbidden_host_encoding(*args, **kwargs):
        pytest.fail('Native project-language status cannot use the host codepage')
    monkeypatch.setattr(locale, 'getpreferredencoding', forbidden_host_encoding)
    automation, state = _project_codepage_statusbar_double(monkeypatch, ['FX3U/FX3UC', 'English'])
    first = automation._current_native_project_codepage_observation(_session(), 'en')
    state.texts = ['FX3U/FX3UC', 'Japanese']
    second = automation._current_native_project_codepage_observation(_session(), 'en')
    state.texts = ['FX3U/FX3UC', 'Chinese (Simplified)']
    third = automation._current_native_project_codepage_observation(_session(), 'en')
    assert [row['codepage_candidates'] for row in (first, second, third)] == [[1252], [932], [936, 54936]]
    assert state.reads == 3


def test_current_project_language_status_requires_the_observed_ui_language(monkeypatch):
    automation, state = _project_codepage_statusbar_double(monkeypatch, ['FX3U/FX3UC', 'English'])
    with pytest.raises(RuntimeError, match='界面语言未确认'):
        automation._current_native_project_codepage_observation(_session(), 'zh')
    assert state.reads == 0


@pytest.mark.parametrize("global_present", [True, False])
def test_native_comment_navigation_uses_global_or_exact_main_local_scope(monkeypatch, global_present):
    from gxworks2.ui_automation import NativeEditorNotFound

    automation = PywinautoGXWorks2UIAutomation()
    selected = []
    window = SimpleNamespace(window_text=lambda: "软元件注释 COMMENT" if global_present else "软元件注释 MAIN")

    def activate(session, pattern, tree):
        if pattern is automation.COMMENT_EDITOR and not global_present:
            raise NativeEditorNotFound()
        selected.append(pattern)
        return window

    monkeypatch.setattr(automation, "_activate_editor", activate)
    monkeypatch.setattr(automation, "_current_native_project_profile", lambda _: native_project_profile("Q00J"))
    monkeypatch.setattr(automation, "_activate_program_editor", lambda _: None)
    monkeypatch.setattr(automation, "_invoke_csv_command", lambda *a, **kw: {"success": True})
    result = automation.import_comments_csv(_session(), "comments.csv")
    assert result["comment_scope"] == ("global" if global_present else "program")
    assert result["comment_name"] == ("COMMENT" if global_present else "MAIN")
    assert selected == [automation.COMMENT_EDITOR if global_present else automation.LOCAL_COMMENT_EDITOR]


def test_missing_global_comment_source_does_not_fall_back_on_a_global_capable_cpu(monkeypatch):
    from gxworks2.ui_automation import NativeEditorNotFound

    automation = PywinautoGXWorks2UIAutomation()
    patterns = []

    def activate(session, pattern, tree):
        patterns.append(pattern)
        raise NativeEditorNotFound()

    monkeypatch.setattr(automation, "_activate_editor", activate)
    monkeypatch.setattr(automation, "_current_native_project_profile", lambda _: native_project_profile("Q03UDV"))
    with pytest.raises(NativeEditorNotFound):
        automation._activate_comments_editor(_session())
    assert patterns == [automation.COMMENT_EDITOR]


@pytest.mark.parametrize("header,cpu,valid", [("LCPU L02", "L02-P", True),
                                            ("QCPU(Q儌乕僪) Q02PH", "Q02PH", True),
                                            ("LCPU L26", "L02-P", False),
                                            ("FXCPU FX3U/FX3UC", "Q03UDV", False)])
def test_native_csv_cpu_header_preserves_the_exact_menu_group(header, cpu, valid):
    assert native_project_profile(cpu).accepts_plc_info(header) is valid


@given(st.lists(st.tuples(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=8),
                          st.integers(min_value=-32768, max_value=32767)), min_size=1, max_size=8))
@settings(max_examples=40, deadline=None)
def test_read_only_projection_retains_unknown_vendor_instructions_and_original_rows(entries):
    import tempfile
    from gxworks2.csv_importer import inspect_gxworks2_csv
    with tempfile.TemporaryDirectory() as directory:
        program, comments = Path(directory) / "source.csv", Path(directory) / "comments.csv"
        rows = [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
                ["0", "来源", "LD", "X000", "", "", ""]]
        rows.extend([str(index), "", "ZZVENDOR" + suffix, f"K{value}", "", "", ""]
                    for index, (suffix, value) in enumerate(entries, 1))
        rows.append([str(len(entries) + 1), "", "END", "", "", "", ""])
        with program.open("w", encoding="utf-16", newline="") as handle:
            csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)
        _write_comments(comments)
        before = program.read_bytes()
        result = inspect_gxworks2_csv(program, comments, plc_model="FX3U")
        assert result["raw_rows"] == rows
        raw = [row for network in result["raw_networks"] for row in network["raw_instructions"]]
        assert [raw[0]["op"], raw[-1]["op"]] == ["LD", "END"]
        assert [(row["op"], row["args"]) for row in raw[1:-1]] == [
            ("ZZVENDOR" + suffix, [f"K{value}"]) for suffix, value in entries]
        assert result["coverage"]["ir_status"] == "valid"
        assert result["coverage"]["semantic_status"] == "unverified"
        assert len(result["coverage"]["unknown_instructions"]) == len(entries)
        assert program.read_bytes() == before


def test_csv_analysis_keeps_the_ordered_ir_when_the_drawing_preview_fails():
    import tempfile
    from gxworks2.csv_importer import inspect_gxworks2_csv, locate_csv_source
    with tempfile.TemporaryDirectory() as directory:
        program, comments = Path(directory) / "program.csv", Path(directory) / "comments.csv"
        # Network 1 restarts a condition after an output: the older graphical
        # model cannot draw it, but the statement list stays executable order.
        rows = [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
                ["0", "", "LD", "X000", "", "", ""],
                ["2", "", "OUT", "Y000", "", "", ""],
                ["3", "", "AND", "X001", "", "", ""],
                ["4", "", "OUT", "Y001", "", "", ""],
                ["6", "", "LD", "X002", "", "", ""],
                ["8", "", "ZZVENDOR", "D0", "", "", ""],
                ["10", "", "END", "", "", "", ""]]
        with program.open("w", encoding="utf-16", newline="") as handle:
            csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                       lineterminator="\r\n").writerows(rows)
        _write_comments(comments)
        result = inspect_gxworks2_csv(program, comments, plc_model="FX3U")

    coverage = result["coverage"]
    assert coverage["instruction_status"] == "complete"
    assert coverage["ir_status"] == "valid"
    assert coverage["parse_status"] == "complete"
    assert coverage["graph_preview_status"] == "partial"
    assert [row["network_id"] for row in coverage["graph_preview_failures"]] == ["N0001"]
    assert result["ladder"] is None
    networks = result["program_ir"]["networks"]
    assert [(row["id"], [item["op"] for item in row["ladder"]["ordered_instructions"]])
            for row in networks] == [("N0001", ["LD", "OUT", "AND", "OUT"]),
                                     ("N0002", ["LD", "ZZVENDOR", "END"])]
    # Read/write reference analysis and the static checks still run on the IR.
    assert [(row["reads"], row["writes"]) for row in networks] == [
        (["X000", "X001"], ["Y000", "Y001"]), (["X002", "D0"], [])]
    located = [(item["code"], [(row["source_row"], row["step"]) for row in item["csv_sources"]])
               for item in result["diagnostics"]]
    assert located == [("SOURCE_RECORD_SEMANTICS_UNVERIFIED", [(8, "6"), (9, "8"), (10, "10")])]
    assert locate_csv_source(result["source_map"], "N0001") == result["raw_networks"][0]["source_map"]
    assert coverage["runtime_verified"] is False


def test_csv_analysis_reports_the_supplied_export_as_ordered_ir_with_csv_coordinates():
    from gxworks2.csv_importer import inspect_gxworks2_csv, locate_csv_source
    warehouse = private_path("research/fixtures/gxw-offline/warehouse")
    program, comments = warehouse / "MAIN.csv", warehouse / "COMMENT.csv"
    if not program.is_file() or not comments.is_file():
        pytest.skip("the warehouse CSV attachments are unavailable")
    result = inspect_gxworks2_csv(program, comments, plc_model="FX3U")

    coverage = result["coverage"]
    assert coverage["instruction_status"] == "complete"
    assert coverage["instruction_count"] == coverage["network_instruction_count"] == 155
    assert coverage["network_count"] == coverage["parsed_network_count"] == 9
    assert coverage["ir_status"] == "valid" and coverage["ir_failures"] == []
    # The three networks the older graphical model rejects limit the drawing only.
    assert coverage["graph_preview_status"] == "partial"
    assert [row["network_id"] for row in coverage["graph_preview_failures"]] == [
        "N0003", "N0004", "N0009"]
    assert coverage["unknown_instructions"] == []
    assert coverage["semantic_status"] == "static_only"
    assert coverage["runtime_verified"] is False
    assert len(result["device_comments"]) == 22
    assert result["ladder"] is None
    assert result["program_ir"]["source"]["format"] == "instruction_sequence_v1"
    assert [row["id"] for row in result["program_ir"]["networks"]] == [
        f"N{index:04d}" for index in range(1, 10)]

    rows = result["raw_rows"]
    for network in result["raw_networks"]:
        ordered = next(row for row in result["program_ir"]["networks"]
                       if row["id"] == network["id"])
        assert [row["op"] for row in ordered["ladder"]["ordered_instructions"]] == [
            row["op"] for row in network["raw_instructions"]]
        positions = locate_csv_source(result["source_map"], network["id"])
        assert [row["instruction_index"] for row in positions] == list(range(len(positions)))
        assert positions == network["source_map"]
    # Every ordered instruction maps back to its own CSV row, step and mnemonic.
    for position in result["source_map"]:
        csv_row = rows[position["source_row"] - 1]
        instruction = result["raw_networks"][int(position["network_id"][1:]) - 1][
            "raw_instructions"][position["instruction_index"]]
        assert csv_row[0].strip() == position["step"] == instruction["step"]
        assert csv_row[2].strip().upper() == instruction["op"]
    assert len(result["source_map"]) == 155
    assert all(item["csv_sources"] is not None for item in result["diagnostics"])


@pytest.mark.parametrize("program", ["MAIN", "程序1", "Main-1", "Program+1"])
def test_explicit_read_program_is_a_literal_native_name(program):
    automation = PywinautoGXWorks2UIAutomation(program=program)
    assert automation.PROGRAM_EDITOR.search(f"[PRG]写入 {program} 20步")
    assert not automation.PROGRAM_EDITOR.search(f"[PRG]写入 {program}Other 20步")
    assert automation.PROGRAM_WRITE_EDITOR.search(f"[PRG]写入 {program} 20步")


@pytest.mark.parametrize("program", ["", "MAIN\nOther", "MAIN\x00Other", "x" * 65])
def test_explicit_read_program_rejects_control_characters_and_unbounded_names(program):
    with pytest.raises(ValueError):
        PywinautoGXWorks2UIAutomation(program=program)

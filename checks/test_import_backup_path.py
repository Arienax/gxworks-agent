"""The import service must not back up a project the open window does not show.

A long project path arrives as an abbreviated caption and an unsaved project
arrives as a placeholder, so the caption cannot name the file to protect.  These
cases pin the structured refusal, the escape hatch that is still corroborated by
the window, and that a project which *is* locatable gets backed up first.
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxworks2.csv_manager import CSVManager  # noqa: E402
from gxworks2.import_service import ImportService  # noqa: E402
from gxworks2.models import ImportErrorCode  # noqa: E402


@dataclass
class FakeValidation:
    valid: bool = True
    path: str = ""
    errors: list = field(default_factory=list)
    plc_info: str = "三菱 GX Works2 兼容"


@dataclass
class FakeSession:
    project_open: bool = True
    project_state_known: bool = True
    project_name: str = ""
    process_id: int = 111
    window_handle: int = 222
    executable: str = ""


class FakeFinder:
    def __init__(self, session):
        self._session = session

    def find_running(self):
        return self._session


class FakeAutomation:
    """Stops at the native import so the test never touches GX Works2."""

    def __init__(self, state):
        self._state = state
        self.imported = False

    def inspect_project(self, session):
        return dict(self._state)

    def import_program_csv(self, session, csv_path):
        self.imported = True
        raise RuntimeError("用例不执行原生导入")


class FakeCSVManager:
    backup_folder = staticmethod(CSVManager.backup_folder)

    def validate(self, csv_path):
        return FakeValidation(path=str(csv_path))

    def program_semantic_sha256(self, path):
        return "0" * 64

    def file_sha256(self, path):
        return "1" * 64

    def comments_semantic_sha256(self, path):
        return "2" * 64


def _abbreviate(path):
    """Build a GX-style abbreviated caption for a real path."""

    return "..." + str(path)[2:]


def service_module_restore(original):
    from gxworks2 import import_service as service_module

    service_module._native_file_owner = original


def _owner_patch(owner):
    """Swap the Restart Manager predicate so cases do not need a live GX window.

    ``owner=None`` models a session that cannot answer (no pid/start time);
    ``True``/``False`` model a process that does/does not hold the file.
    """

    from gxworks2 import import_service as service_module

    original = service_module._native_file_owner
    if owner is None:
        service_module._native_file_owner = lambda session: None
    else:
        service_module._native_file_owner = lambda session: (lambda path: bool(owner))
    return original


def _run(caption, *, backup_root, project_path=None, project_search_roots=(),
         csv_path=None, owner=True, create_project_path=None, project_open=True,
         import_context=None):
    automation = FakeAutomation(
        {
            "project_open": True,
            "program_ready": True,
            "automation_available": True,
            "project_name": caption,
        }
    )
    service = ImportService(
        FakeFinder(FakeSession(project_name=caption, project_open=project_open)),
        automation,
        FakeCSVManager(),
        backup_root,
    )
    original = _owner_patch(owner)
    try:
        result = service.import_current_program(
            csv_path or str(Path(backup_root) / "program.csv"),
            save_project=False,
            project_path=project_path,
            project_search_roots=project_search_roots,
            create_project_path=create_project_path,
            import_context=import_context,
        )
    except RuntimeError:
        # The fake stops at the native import; reaching it means the backup
        # stage already succeeded, which is what these cases observe.
        return None, automation
    finally:
        service_module_restore(original)
    return result, automation


def test_an_abbreviated_caption_is_refused_with_a_structured_reason():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result, automation = _run(r"...\work\repos\plc\project.gxw", backup_root=root)
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert result.stage == "backup"
        assert "另存到较短路径" in result.message
        assert "显式指定" in result.message, "占用证明可用时应给出显式路径这条出路"
        # Nothing was copied and nothing reached the native import.
        assert list(root.rglob("*.gxw")) == []
        assert automation.imported is False


def test_an_explicit_path_corroborated_by_the_window_is_backed_up():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        result, _ = _run(_abbreviate(target), backup_root=root, project_path=str(target))
        backups = sorted(root.rglob("project.gxw"))
        assert len(backups) == 1, "备份必须在写入前生成"
        assert backups[0].read_bytes() == b"project"
        if result is not None:
            assert result.error_code is not ImportErrorCode.PROJECT_PATH_UNKNOWN


def test_an_explicit_path_for_another_project_is_refused_before_backup():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "wrong_project.gxw"
        target.write_bytes(b"THIS IS NOT THE OPEN PROJECT")
        # The window shows the real project; project_path names a different file.
        result, automation = _run(
            r"...\work\repos\plc\test_10_25.gxw",
            backup_root=root,
            project_path=str(target),
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert "不是同一个工程" in result.message
        assert list(root.rglob("*.gxw")) == [], "不能备份一个不会被修改的工程"
        assert automation.imported is False


def test_an_explicit_path_is_refused_while_the_window_is_unsaved():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "named.gxw"
        target.write_bytes(b"named")
        result, automation = _run(
            "(工程未设置)", backup_root=root, project_path=str(target)
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert "还没有已保存的工程" in result.message
        assert list(root.rglob("*.gxw")) == []
        assert automation.imported is False


def test_a_complete_caption_path_is_backed_up():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "short_path.gxw"
        target.write_bytes(b"project")
        result, _ = _run(str(target), backup_root=root)
        backups = sorted(root.rglob("short_path.gxw"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"project"
        if result is not None:
            assert result.error_code is not ImportErrorCode.PROJECT_PATH_UNKNOWN


def test_an_unsaved_project_skips_the_backup_instead_of_failing():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result, _ = _run("(工程未设置)", backup_root=root)
        # An unsaved project has no file to copy, and that is not an error.
        assert list(root.rglob("*.gxw")) == []
        assert result is not None
        assert result.stage != "backup"
        assert result.error_code is not ImportErrorCode.PROJECT_PATH_UNKNOWN


def test_a_unique_project_under_a_search_root_is_backed_up():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as projects:
        root = Path(directory)
        target = Path(projects) / "nested" / "target.gxw"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"project")
        result, _ = _run(
            _abbreviate(target),
            backup_root=root,
            project_search_roots=[projects],
        )
        backups = sorted(root.rglob("target.gxw"))
        assert len(backups) == 1, "唯一候选应被采用并完成备份"
        assert backups[0].read_bytes() == b"project"
        if result is not None:
            assert result.error_code is not ImportErrorCode.PROJECT_PATH_UNKNOWN


def test_two_matching_projects_under_the_search_root_are_refused():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as first, \
            tempfile.TemporaryDirectory() as second:
        root = Path(directory)
        for base in (first, second):
            target = Path(base) / "plc" / "target.gxw"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"project")
        result, _ = _run(
            r"...\plc\target.gxw",
            backup_root=root,
            project_search_roots=[first, second],
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert len(result.details["project_path_candidates"]) == 2
        assert list(root.rglob("*.gxw")) == []


def test_a_backup_the_window_does_not_hold_is_refused():
    """Restart Manager proof: a shared caption suffix alone must not authorise."""

    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        result, automation = _run(
            _abbreviate(target), backup_root=root, project_path=str(target), owner=False
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert "占用" in result.message
        assert list(root.rglob("*.gxw")) == []
        assert automation.imported is False


def test_the_backup_records_which_evidence_tied_it():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        result, _ = _run_succeeding(
            _abbreviate(target), backup_root=root, project_path=str(target)
        )
        assert result is not None
        protection = result.details["version_protection"]
        assert protection["project_path_source"] == "explicit"
        assert protection["project_path_binding"] == "file-owner"
        assert protection["project_path"] == str(target)


def _run_succeeding(caption, *, backup_root, project_path=None):
    """Drive the flow past the native import so the success record is observable."""

    class SucceedingCSVManager(FakeCSVManager):
        def prepare_import_program(self, csv_path, destination):
            Path(destination).write_bytes(b"staged")
            return str(destination)

    class SucceedingAutomation(FakeAutomation):
        def import_program_csv(self, session, csv_path):
            self.imported = True
            return {"success": True, "message": "ok"}

    automation = SucceedingAutomation(
        {
            "project_open": True,
            "program_ready": True,
            "automation_available": True,
            "project_name": caption,
        }
    )
    service = ImportService(
        FakeFinder(FakeSession(project_name=caption)),
        automation,
        SucceedingCSVManager(),
        backup_root,
    )
    original = _owner_patch(True)
    try:
        result = service.import_current_program(
            str(Path(backup_root) / "program.csv"),
            save_project=False,
            project_path=project_path,
        )
    finally:
        service_module_restore(original)
    return result, automation


def test_without_owner_proof_a_truncated_caption_is_refused():
    """The maintainer's repro: caption-only fallback backed up the wrong file."""

    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        result, automation = _run(
            _abbreviate(target), backup_root=root, project_path=str(target), owner=None
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert "占用证明" in result.message
        assert "显式指定" not in result.message, "无法取得证明时，显式路径也是死路，不能建议"
        assert list(root.rglob("*.gxw")) == [], "拿不到占用证明时不得备份"
        assert automation.imported is False


def test_a_named_target_is_not_replaced_by_an_auto_created_project():
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "requested.gxw"
        target.write_bytes(b"requested")
        auto = Path(project_dir) / "gx_import.gxw"
        result, automation = _run(
            "(工程未设置)",
            backup_root=root,
            project_path=str(target),
            create_project_path=str(auto),
            project_open=False,
            import_context={"plc_model": "FX3U"},
        )
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert "另一个文件" in result.message
        assert automation.imported is False


def test_a_home_relative_target_matches_the_auto_created_path():
    """The auto-create comparison must expand `~` the same way the resolver does."""

    import os

    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as directory:
        previous = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = home
        try:
            target = Path(home) / "requested.gxw"
            target.write_bytes(b"requested")
            result, _ = _run(
                "(工程未设置)",
                backup_root=directory,
                project_path="~/requested.gxw",
                create_project_path=str(target),
                project_open=False,
                import_context={"plc_model": "FX3U"},
            )
        finally:
            if previous is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = previous

        assert result is not None
        assert result.error_code is not ImportErrorCode.PROJECT_PATH_UNKNOWN, (
            "expanduser 之后两者是同一个文件，不应被判成另一个工程"
        )
        assert result.stage == "prepare_project"


def test_a_failed_import_still_reports_which_evidence_tied_the_backup():
    """The backup report is about what was protected, not about the import result."""

    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        result, automation = _run(
            _abbreviate(target), backup_root=root, project_path=str(target)
        )
        assert automation.imported is False
        assert result.stage == "import"
        protection = result.details["version_protection"]
        assert protection["project_path_source"] == "explicit"
        assert protection["project_path_binding"] == "file-owner"
        assert protection["project_path"] == str(target)


def test_a_complete_caption_does_not_walk_the_workspace():
    """Short-path projects are the common case; the walk is not free."""

    from gxworks2 import import_service as service_module

    def explode(*args, **kwargs):
        raise AssertionError("完整标题不应触发工作区遍历")

    original = service_module.discover_candidates
    service_module.discover_candidates = explode
    try:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
            root = Path(directory)
            target = Path(project_dir) / "short.gxw"
            target.write_bytes(b"project")
            result, _ = _run(
                str(target),
                backup_root=root,
                project_search_roots=[str(root)],
            )
            assert result is not None
            assert list(root.rglob("*.gxw")), "完整标题仍应正常备份"
    finally:
        service_module.discover_candidates = original


def test_the_csv_preparation_failure_keeps_the_full_audit_record():
    """version_protection must have the same shape wherever the import stops."""

    class BrokenHash(FakeCSVManager):
        def program_semantic_sha256(self, path):
            raise RuntimeError("CSV 无法解析")

    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as project_dir:
        root = Path(directory)
        target = Path(project_dir) / "project.gxw"
        target.write_bytes(b"project")
        original = _owner_patch(True)
        try:
            service = ImportService(
                FakeFinder(FakeSession(project_name=_abbreviate(target))),
                FakeAutomation({"project_open": True, "program_ready": True,
                                "automation_available": True,
                                "project_name": _abbreviate(target)}),
                BrokenHash(),
                root,
            )
            result = service.import_current_program(
                str(root / "program.csv"), save_project=False, project_path=str(target)
            )
        finally:
            service_module_restore(original)

        assert result.stage == "prepare_import"
        protection = result.details["version_protection"]
        assert set(protection) == {
            "project_identity", "project_path", "project_path_source", "project_path_binding",
        }

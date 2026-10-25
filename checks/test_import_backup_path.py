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
    plc_info: dict = field(default_factory=dict)


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


def _run(caption, *, backup_root, project_path=None, project_search_roots=(), csv_path=None):
    automation = FakeAutomation(
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
        FakeCSVManager(),
        backup_root,
    )
    try:
        result = service.import_current_program(
            csv_path or str(Path(backup_root) / "program.csv"),
            save_project=False,
            project_path=project_path,
            project_search_roots=project_search_roots,
        )
    except RuntimeError:
        # The fake stops at the native import; reaching it means the backup
        # stage already succeeded, which is what these cases observe.
        return None, automation
    return result, automation


def test_an_abbreviated_caption_is_refused_with_a_structured_reason():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result, automation = _run(r"...\work\repos\plc\project.gxw", backup_root=root)
        assert result is not None
        assert result.error_code is ImportErrorCode.PROJECT_PATH_UNKNOWN
        assert result.stage == "backup"
        assert "显式指定工程路径" in result.message
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

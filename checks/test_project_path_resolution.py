"""Regressions for resolving a GX Works2 project file from an open window.

GX Works2 abbreviates a long project path with a leading ``...`` and shows
``(工程未设置)`` until the project is saved, so the caption is a display string.
These cases pin when a caption may corroborate a path, and - more importantly -
that an unconfirmed path is refused instead of adopted: the import always
modifies the project open in the window, so the path only decides what gets
backed up first.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxworks2.project_path import (  # noqa: E402
    CAPTION,
    CANDIDATE,
    EXPLICIT,
    caption_describes,
    caption_matches_path,
    discover_candidates,
    resolve_project_path,
)

LONG_DIR = Path(r"D:\work\repos\plc")
LONG_FILE = LONG_DIR / "target.gxw"
ABBREVIATED = r"...\work\repos\plc\target.gxw"


def _touch(directory, name):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def _abbreviate(path):
    """Build a GX-style abbreviated caption for a real path."""

    return "..." + str(path)[2:]


def test_an_abbreviated_caption_only_verifies_a_path():
    assert caption_matches_path(ABBREVIATED, LONG_FILE) is True
    assert caption_matches_path(ABBREVIATED, LONG_DIR / "other.gxw") is False
    # A complete path is not an abbreviation and must not match by accident.
    assert caption_matches_path(str(LONG_FILE), LONG_FILE) is False
    assert caption_matches_path("...", LONG_FILE) is False
    assert caption_matches_path("", LONG_FILE) is False


def test_caption_describes_accepts_full_abbreviated_and_bare_names():
    assert caption_describes(str(LONG_FILE), LONG_FILE) is True
    assert caption_describes(LONG_FILE.name, LONG_FILE) is True
    assert caption_describes("(工程未设置)", LONG_FILE) is True
    assert caption_describes(str(LONG_DIR / "other.gxw"), LONG_FILE) is False


def test_an_explicit_path_must_be_corroborated_by_the_window():
    with tempfile.TemporaryDirectory() as directory:
        target = _touch(directory, "target.gxw")
        accepted = resolve_project_path(_abbreviate(target), explicit=str(target))
        assert accepted.source == EXPLICIT
        assert accepted.path == target

        # The window shows another project: the named file is not the one that
        # would be modified, so backing it up would protect the wrong file.
        contradicted = resolve_project_path(
            _abbreviate(Path(directory) / "other.gxw"), explicit=str(target)
        )
        assert not contradicted
        assert "不是同一个工程" in contradicted.reason


def test_an_explicit_path_is_refused_while_the_window_is_unsaved():
    with tempfile.TemporaryDirectory() as directory:
        target = _touch(directory, "target.gxw")
        for caption in ("(工程未设置)", ""):
            resolution = resolve_project_path(caption, explicit=str(target))
            assert not resolution
            assert "还没有已保存的工程" in resolution.reason


def test_an_explicit_path_must_exist_and_be_gxw():
    with tempfile.TemporaryDirectory() as directory:
        missing = Path(directory) / "absent.gxw"
        refused = resolve_project_path(ABBREVIATED, explicit=str(missing))
        assert not refused
        assert "不存在" in refused.reason

        not_gxw = _touch(directory, "project.txt")
        refused = resolve_project_path(ABBREVIATED, explicit=str(not_gxw))
        assert not refused
        assert ".gxw" in refused.reason


def test_a_complete_caption_path_is_used_directly():
    with tempfile.TemporaryDirectory() as directory:
        target = _touch(directory, "short.gxw")
        resolution = resolve_project_path(str(target))
        assert resolution.source == CAPTION
        assert resolution.path == target


def test_an_abbreviated_caption_without_candidates_is_refused():
    resolution = resolve_project_path(ABBREVIATED)
    assert not resolution
    assert resolution.path is None
    # The message has to tell the operator what to do next.
    assert "显式指定工程路径" in resolution.reason


def test_a_candidate_must_match_the_caption_tail():
    with tempfile.TemporaryDirectory() as directory:
        unrelated = _touch(Path(directory) / "elsewhere", "target.gxw")
        resolution = resolve_project_path(ABBREVIATED, candidates=[str(unrelated)])
        assert not resolution, "同名但目录不符的候选不能被采纳"
        assert resolution.candidates == (unrelated,)
        assert "不一致" in resolution.reason


def test_a_unique_candidate_matching_the_tail_is_used():
    with tempfile.TemporaryDirectory() as directory:
        target = _touch(Path(directory) / "repos" / "plc", "target.gxw")
        resolution = resolve_project_path(_abbreviate(target), candidates=[str(target)])
        assert resolution.source == CANDIDATE
        assert resolution.path == target


def test_ambiguous_candidates_are_not_guessed():
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        left = _touch(Path(first) / "plc", "target.gxw")
        right = _touch(Path(second) / "plc", "target.gxw")
        resolution = resolve_project_path(
            r"...\plc\target.gxw", candidates=[str(left), str(right)]
        )
        assert not resolution
        assert len(resolution.candidates) == 2
        assert "多个同名工程" in resolution.reason


def test_an_unsaved_window_never_claims_a_path():
    for caption in ("(工程未设置)", "工程未设置", ""):
        resolution = resolve_project_path(caption, candidates=[str(LONG_FILE)])
        assert not resolution
        assert "没有已保存的工程" in resolution.reason


def test_candidate_discovery_is_bounded_and_skips_dependency_trees():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shallow = _touch(root, "target.gxw")
        _touch(root / "a" / "b" / "c" / "d" / "e", "target.gxw")   # 超出深度上限
        _touch(root / "node_modules", "target.gxw")                # 依赖目录不参与
        _touch(root, "other.gxw")

        found = discover_candidates("target.gxw", [root], max_depth=3)
        assert found == (shallow,), "只应命中浅层且可用的同名工程"

        assert discover_candidates("target.gxw", [root], max_depth=6)
        assert discover_candidates("", [root]) == ()
        assert discover_candidates("target.gxw", [root / "missing"]) == ()


def test_the_tool_layer_accepts_only_an_absolute_gxw_path():
    from agent_runtime.native_tools import _requested_project_path
    from agent_runtime.tool_errors import ToolPreconditionError

    assert _requested_project_path({}) == {}
    assert _requested_project_path({"project_path": None}) == {}
    accepted = _requested_project_path({"project_path": str(LONG_FILE)})
    assert accepted == {"project_path": str(LONG_FILE)}

    for invalid in ("relative.gxw", r"D:\work\project.txt", "   "):
        try:
            _requested_project_path({"project_path": invalid})
        except ToolPreconditionError as error:
            assert error.code == "INVALID_ARGUMENTS"
        else:
            raise AssertionError(f"{invalid!r} 应被拒绝")


def test_a_full_path_caption_naming_another_file_is_refused():
    with tempfile.TemporaryDirectory() as directory:
        target = _touch(directory, "target.gxw")
        shown = _touch(directory, "shown.gxw")   # 窗口实际显示、也就是将被写入的工程
        refused = resolve_project_path(str(shown), explicit=str(target))
        assert not refused
        assert "不是同一个工程" in refused.reason


def test_a_bare_name_caption_only_corroborates_the_file_name():
    """Known limitation, pinned so a later change cannot widen it silently.

    A caption with neither a directory nor a ``...`` prefix only carries a file
    name, so corroboration degrades to a name comparison.  GX Works2 does not
    produce such a caption for a saved project, so this is accepted rather than
    guessed further.
    """

    with tempfile.TemporaryDirectory() as directory:
        target = _touch(Path(directory) / "elsewhere", "target.gxw")
        resolution = resolve_project_path("target.gxw", explicit=str(target))
        assert resolution.path == target


def test_candidate_discovery_stops_at_the_limit():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index in range(3):
            _touch(root / f"d{index}", "target.gxw")
        assert len(discover_candidates("target.gxw", [root], limit=2)) == 2


def test_the_mcp_schema_exposes_an_optional_project_path():
    from agent_runtime.tool_catalog import _IMPORT_PROJECT_SCHEMA

    assert set(_IMPORT_PROJECT_SCHEMA["properties"]) == {"project_path"}
    assert _IMPORT_PROJECT_SCHEMA["additionalProperties"] is False
    project_path = _IMPORT_PROJECT_SCHEMA["properties"]["project_path"]
    assert project_path["type"] == "string"
    assert project_path["minLength"] == 1


def test_the_web_execution_proposal_carries_an_optional_project_path():
    try:
        from integrations.web.schemas import ExecutionProposal
    except ModuleNotFoundError:
        return  # pydantic 属于 Web 依赖；未安装时跳过

    base = dict(action="gx_import", project_id="p", version_id="v", request_id="r")
    assert ExecutionProposal(**base).project_path is None
    assert ExecutionProposal(**base, project_path=str(LONG_FILE)).project_path
    for invalid in ("", "x" * 5000):
        try:
            ExecutionProposal(**base, project_path=invalid)
        except Exception as error:
            assert type(error).__name__ == "ValidationError"
        else:
            raise AssertionError(f"{invalid[:12]!r} 应被拒绝")

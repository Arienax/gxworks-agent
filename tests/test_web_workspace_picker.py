# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""The in-app workspace picker lists and creates directories, never OS dialogs."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from integrations.web.workspaces import (
    ServiceHolder,
    WorkspaceNotSelected,
    WorkspacePathError,
    create_directory,
    is_fully_qualified,
    list_directory,
    workspace_snapshot,
)

WINDOWS = os.name == "nt"


def test_only_fully_qualified_paths_are_accepted():
    assert is_fully_qualified("C:\\work", windows=True)
    assert is_fully_qualified("\\\\server\\share\\work", windows=True)
    assert is_fully_qualified("/home/me", windows=False)
    # Resolving these against the process drive would silently change the target.
    assert not is_fully_qualified("C:work", windows=True)
    assert not is_fully_qualified("\\foo", windows=True)
    assert not is_fully_qualified("\\\\server", windows=True)
    assert not is_fully_qualified("work", windows=False)
    assert not is_fully_qualified("", windows=True)
    assert not is_fully_qualified(None, windows=True)


def test_listing_returns_child_directories_sorted_with_hidden_flags(tmp_path):
    (tmp_path / "beta").mkdir()
    (tmp_path / "Alpha").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    listing = list_directory(str(tmp_path))
    assert listing["path"] == str(tmp_path)
    # Hidden entries are listed and flagged, not filtered: the dialog decides what to show.
    assert [row["name"] for row in listing["entries"]] == [".cache", "Alpha", "beta"]
    assert all(row["path"] for row in listing["entries"])
    assert [row["hidden"] for row in listing["entries"]] == [True, False, False]
    assert listing["truncated"] is False


def test_listing_carries_the_ancestor_chain_from_the_root(tmp_path):
    child = tmp_path / "line" / "cell"
    child.mkdir(parents=True)
    listing = list_directory(str(child))
    crumbs = listing["crumbs"]
    assert [row["path"] for row in crumbs][-1] == str(child)
    assert all(Path(row["path"]).is_absolute() for row in crumbs)
    # The first crumb is the filesystem root and is labelled by its full path.
    assert crumbs[0]["path"] == Path(crumbs[0]["path"]).anchor
    assert crumbs[0]["name"] == crumbs[0]["path"]


def test_listing_reports_truncation_instead_of_silently_cutting(tmp_path):
    for index in range(5):
        (tmp_path / f"d{index}").mkdir()
    listing = list_directory(str(tmp_path), max_entries=3)
    assert len(listing["entries"]) == 3
    assert listing["truncated"] is True


def test_a_relative_path_is_refused_rather_than_resolved(tmp_path):
    with pytest.raises(WorkspacePathError) as error:
        list_directory("relative/dir")
    assert error.value.code == "directory-unreadable"


def test_an_unreadable_target_reports_the_browse_failure(tmp_path):
    missing = tmp_path / "gone"
    with pytest.raises(WorkspacePathError) as error:
        list_directory(str(missing))
    assert error.value.code == "directory-unreadable"


def test_creating_one_child_directory(tmp_path):
    created = create_directory(str(tmp_path), "分拣线")
    assert Path(created).is_dir()
    assert Path(created).name == "分拣线"


def test_creating_an_existing_directory_is_reported(tmp_path):
    (tmp_path / "line").mkdir()
    with pytest.raises(WorkspacePathError) as error:
        create_directory(str(tmp_path), "line")
    assert error.value.code == "directory-exists"


def test_creating_is_never_recursive():
    # A missing parent is a real failure, not a level to invent.
    missing_parent = Path(os.environ.get("TEMP", ".")) / "gxworks-no-such-parent" / "deep"
    with pytest.raises(WorkspacePathError) as error:
        create_directory(str(missing_parent), "child")
    assert error.value.code == "directory-create-failed"


@pytest.mark.parametrize("name", ["", "  ", "..", ".", "a/b", "a\\b", "bad\x00name"])
def test_only_a_single_plain_path_segment_is_accepted(tmp_path, name):
    with pytest.raises(WorkspacePathError) as error:
        create_directory(str(tmp_path), name)
    assert error.value.code == "directory-create-failed"


def test_workspace_snapshot_distinguishes_none_from_a_path(tmp_path):
    empty = workspace_snapshot(None)
    assert empty["selected"] is False and empty["path"] is None
    bound = workspace_snapshot(str(tmp_path))
    assert bound["selected"] is True
    assert bound["path"] == str(tmp_path)
    assert bound["name"] == tmp_path.name


def test_holder_reports_the_missing_workspace_before_one_is_bound(tmp_path):
    holder = ServiceHolder(read_only=True)
    assert holder.current() is None
    assert holder.read_only is True
    with pytest.raises(WorkspaceNotSelected):
        _ = holder.projects
    holder.bind(type("Bound", (), {"read_only": False})())
    assert holder.read_only is False
    assert holder.current() is not None


def test_the_system_folder_dialog_can_be_built_without_showing_it():
    """Proves the COM wiring (GUIDs, vtable slots, folder options) without a UI."""
    from integrations.web.native_picker import choose_folder, native_folder_chooser_available

    if not native_folder_chooser_available():
        pytest.skip("no Windows folder dialog on this platform")
    assert choose_folder(probe=True) is None


def test_a_platform_without_the_dialog_reports_picker_unavailable(monkeypatch):
    from integrations.web import workspaces

    monkeypatch.setattr("integrations.web.native_picker.native_folder_chooser_available",
                        lambda: False)
    with pytest.raises(workspaces.WorkspacePathError) as error:
        workspaces.pick_native_directory()
    assert error.value.code == "picker_unavailable"

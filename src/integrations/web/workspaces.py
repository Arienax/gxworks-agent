# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Single-level directory listing for the in-app workspace picker.

The workbench chooses its engineering workspace inside the browser, so it needs
the same two primitives the desktop host exposes: list one directory level and
create one child directory. Nothing here renders a native OS dialog, and no path
is resolved against the service's own working directory: a value that is not
fully qualified is rejected instead of being reinterpreted.
"""
from __future__ import annotations

import json
import os
import ntpath
import posixpath
import re
from pathlib import Path

MAX_ENTRIES_DEFAULT = 1000

# Windows FILE_ATTRIBUTE_HIDDEN; os.stat exposes it as st_file_attributes.
_FILE_ATTRIBUTE_HIDDEN = 0x2

_UNIX_SEGMENT = re.compile(r"^[^/\\\x00]+$")
_WINDOWS_SEGMENT = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]+$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_UNC_PREFIX = re.compile(r"^\\\\[^\\]+\\[^\\]+")


class WorkspacePathError(ValueError):
    """A browse or create request the service refuses to reinterpret."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def is_fully_qualified(value: str, *, windows: bool | None = None) -> bool:
    """True only for a path pinned to one fixed filesystem location.

    A drive-relative path such as ``C:work`` and a rooted path without a drive
    such as ``\\foo`` both pass ``os.path.isabs`` on Windows yet resolve against
    the process drive and directory, so they are not acceptable here.
    """
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    text = value.strip()
    if windows is None:
        windows = os.name == "nt"
    if windows:
        if _UNC_PREFIX.match(text):
            share = ntpath.splitdrive(text)[0]
            return len(share) > 3
        if not _WINDOWS_DRIVE.match(text):
            return False
        return ntpath.isabs(text) and bool(ntpath.splitdrive(text)[0])
    return posixpath.isabs(text)


def _normalize(value: str, *, windows: bool) -> str:
    path = ntpath if windows else posixpath
    return path.normpath(value)


def _hidden(entry: os.DirEntry, *, windows: bool) -> bool:
    if entry.name.startswith("."):
        return True
    if not windows:
        return False
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & _FILE_ATTRIBUTE_HIDDEN)


def _crumbs(target: str, *, windows: bool) -> list[dict]:
    """Every ancestor of `target`, root first, each one a jump target."""
    path = ntpath if windows else posixpath
    crumbs: list[dict] = []
    current = path.normpath(target)
    while True:
        parent = path.dirname(current)
        if not parent or parent == current:
            break
        crumbs.append({"name": path.basename(current) or current, "path": current})
        current = parent
    crumbs.append({"name": current, "path": current})
    crumbs.reverse()
    return crumbs


def list_directory(value: str | None = None, *, max_entries: int = MAX_ENTRIES_DEFAULT,
                   windows: bool | None = None) -> dict:
    """List one directory level: its child directories, hidden flags and ancestors."""
    if windows is None:
        windows = os.name == "nt"
    if value in (None, ""):
        target = str(Path.home().resolve())
    else:
        if not is_fully_qualified(value, windows=windows):
            raise WorkspacePathError(
                "directory-unreadable",
                "只能浏览完整路径（例如 C:\\work 或 \\\\server\\share\\work）。")
        target = _normalize(str(value).strip(), windows=windows)
    try:
        entries = list(os.scandir(target))
    except OSError as error:
        raise WorkspacePathError("directory-unreadable", str(error)) from error

    directories = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        directories.append({"name": entry.name, "path": entry.path,
                            "hidden": _hidden(entry, windows=windows)})
    directories.sort(key=lambda row: row["name"].lower())
    truncated = len(directories) > max_entries
    return {
        "path": target,
        "home": str(Path.home().resolve()),
        "crumbs": _crumbs(target, windows=windows),
        "entries": directories[:max_entries],
        "truncated": truncated,
    }


def create_directory(parent: str, name: str, *, windows: bool | None = None) -> str:
    """Create one child directory under an existing parent, never recursively."""
    if windows is None:
        windows = os.name == "nt"
    if not is_fully_qualified(parent, windows=windows):
        raise WorkspacePathError(
            "directory-create-failed", "只能在接受完整路径的目录下创建文件夹。")
    segment = str(name or "").strip()
    pattern = _WINDOWS_SEGMENT if windows else _UNIX_SEGMENT
    if not segment or not pattern.match(segment) or segment in {".", ".."}:
        raise WorkspacePathError("directory-create-failed", "文件夹名称只能是一个路径段。")
    path = ntpath if windows else posixpath
    target = path.join(_normalize(parent.strip(), windows=windows), segment)
    try:
        os.mkdir(target)
    except FileExistsError as error:
        raise WorkspacePathError("directory-exists", "同名文件夹已存在。") from error
    except OSError as error:
        raise WorkspacePathError("directory-create-failed", str(error)) from error
    return target


def workspace_snapshot(workspace: str | None) -> dict:
    """What the workbench reports about the currently bound workspace."""
    if not workspace:
        return {"selected": False, "path": None, "name": None, "home": str(Path.home().resolve())}
    path = Path(workspace)
    return {"selected": True, "path": str(path), "name": path.name,
            "home": str(Path.home().resolve())}


def pick_native_directory(timeout: float = 900.0):
    """Ask the operating system for a folder in a child process.

    The dialog is modal and needs a single-threaded apartment, so it runs in its
    own process: the service keeps serving while the operator browses, and an
    abandoned dialog cannot pin a worker thread.
    """
    import subprocess
    import sys as _sys

    from .native_picker import native_folder_chooser_available
    if not native_folder_chooser_available():
        raise WorkspacePathError("picker_unavailable", "当前平台没有可用的系统文件夹选择器。")
    command = ([str(_sys.executable), "--pick-directory"] if getattr(_sys, "frozen", False)
               else [str(_sys.executable), "-m", "integrations.web.native_picker"])
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspacePathError("picker_unavailable", str(error)) from error
    for line in reversed((completed.stdout or "").splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if payload.get("cancelled"):
            return None
        path = payload.get("path")
        if isinstance(path, str) and path:
            return path
    raise WorkspacePathError(
        "picker_unavailable",
        ((completed.stderr or completed.stdout) or "系统文件夹选择器未能返回结果。").strip()[:200])


class WorkspaceNotSelected(RuntimeError):
    """No engineering workspace is bound yet; the browser must choose one."""

    def payload(self) -> dict:
        return {"error": {"code": "workspace_required",
                          "message": "尚未选择工作区，请先选择或新建一个工作区文件夹。"}}


class ServiceHolder:
    """One indirection so every route can outlive a workspace replacement.

    Routes keep the object they were registered with; binding swaps the service
    behind it. Before the first workspace exists, unrelated work raises
    `WorkspaceNotSelected` instead of failing with an attribute error.
    """

    def __init__(self, *, read_only: bool = False):
        self.__dict__["_service"] = None
        self.__dict__["_read_only"] = read_only

    def bind(self, service) -> None:
        self.__dict__["_service"] = service

    def current(self):
        return self.__dict__["_service"]

    @property
    def read_only(self) -> bool:
        service = self.__dict__["_service"]
        return service.read_only if service is not None else self.__dict__["_read_only"]

    def __getattr__(self, name: str):
        service = self.__dict__["_service"]
        if service is None:
            raise WorkspaceNotSelected()
        return getattr(service, name)


def companion(service, factory):
    """Resolve one companion service lazily and rebuild it after a rebind.

    Route modules that wrap the workbench (simulation, native validation, hardware)
    must not construct their wrapper while no workspace is bound, and must not keep
    one that belongs to a replaced workspace. `factory` receives the bound service.
    """
    cache: dict[str, object] = {"owner": object(), "value": None}

    def resolve():
        owner = service.current() if isinstance(service, ServiceHolder) else service
        if owner is None:
            raise WorkspaceNotSelected()
        if cache["owner"] is not owner:
            cache["owner"] = owner
            cache["value"] = factory(owner)
        return cache["value"]

    return resolve

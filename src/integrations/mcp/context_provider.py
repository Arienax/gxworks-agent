"""Headless context loading through the desktop's existing SessionStore."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from agent_runtime.plc_tools import ToolContext, build_tool_context
from storage.session import SessionStore


class ContextUnavailableError(ValueError):
    """The configured engineering snapshot cannot be loaded safely."""


class ToolContextProvider(Protocol):
    def get_context(self) -> ToolContext:
        """Return a copied snapshot for one call, without committing changes."""
        ...


class StaticToolContextProvider:
    """An isolated snapshot for embedding and deterministic tests."""

    def __init__(self, context: ToolContext):
        self._context = self._copy(context)

    @staticmethod
    def _copy(context: ToolContext) -> ToolContext:
        return build_tool_context(
            context.project,
            version=context.version,
            ladder=context.ladder,
            program_ir=context.program_ir,
        )

    def get_context(self) -> ToolContext:
        return self._copy(self._context)


def _record_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise ContextUnavailableError(f"Invalid {label} ID.")
    return value


def _contained(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ContextUnavailableError("Project artifact escapes its storage directory.")


class SessionToolContextProvider:
    """Read a configured project and pinned or active persisted version.

    There is no GUI selection inference, migration, save, import or approval.
    A future desktop bridge can implement ToolContextProvider independently.
    """

    def __init__(
        self, workspace: Path, project_id: str, version_id: str | None = None
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.project_id = _record_id(project_id, "project")
        self.version_id = (
            _record_id(version_id, "version") if version_id is not None else None
        )
        if not (self.workspace / "projects").is_dir():
            raise ContextUnavailableError("Workspace must contain an existing projects directory.")
        self._store = SessionStore(base_dir=self.workspace, create=False)

    def get_context(self) -> ToolContext:
        store = self._store
        project_path = store.project_path(self.project_id)
        _contained(project_path, self.workspace)
        project = store.get_project(self.project_id)
        if project is None or project.get("id") != self.project_id:
            raise ContextUnavailableError("Configured project is missing or invalid.")
        selected = self.version_id or project.get("active_version_id")
        version = None
        ladder = None
        program_ir = None
        if selected:
            selected = _record_id(selected, "version")
            version = next(
                (item for item in project["versions"] if item.get("id") == selected),
                None,
            )
            if version is None:
                raise ContextUnavailableError("Configured version is missing from the project.")
            version_dir = store.version_dir(self.project_id, selected)
            _contained(version_dir, store.project_dir(self.project_id))
            if version.get("target_mode") == "ladder":
                artifacts = version.get("artifacts") or {}
                if not isinstance(artifacts, dict):
                    raise ContextUnavailableError("Invalid version artifacts.")
                for key in ("ir", "json"):
                    if artifacts.get(key):
                        _contained(version_dir / str(artifacts[key]), version_dir)
                program_ir = store.load_program_ir(
                    self.project_id, selected, persist_legacy=False
                )
                ladder = store.load_ladder(
                    self.project_id, selected, persist_legacy=False
                )
                if program_ir is None or ladder is None:
                    raise ContextUnavailableError("Selected ladder version has no readable program.")
        if store.get_project(self.project_id) != project:
            raise ContextUnavailableError("Project changed while loading; retry the call.")
        return build_tool_context(
            project, version=version, ladder=ladder, program_ir=program_ir
        )

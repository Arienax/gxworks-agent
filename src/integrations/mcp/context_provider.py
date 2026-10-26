"""Headless context loading through the shared application session."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_runtime.plc_tools import ToolContext, build_tool_context
from application.projects import ProjectService
from shared.records import record_id


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
            fbd_baseline=context.fbd_baseline,
            native_snapshot=context.native_snapshot,
            st_text=context.st_text,
            sfc_graph=context.sfc_graph,
        )

    def get_context(self) -> ToolContext:
        return self._copy(self._context)


class SessionToolContextProvider:
    """Read a configured project and pinned or active persisted version.

    There is no GUI selection inference, migration, save, import or approval.
    A future desktop bridge can implement ToolContextProvider independently.
    """

    def __init__(
        self, workspace: Path, project_id: str, version_id: str | None = None
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.project_id = record_id(project_id, message="Invalid project ID.", error_type=ContextUnavailableError)
        self.version_id = (
            record_id(version_id, message="Invalid version ID.", error_type=ContextUnavailableError) if version_id is not None else None
        )
        if not (self.workspace / "projects").is_dir():
            raise ContextUnavailableError("Workspace must contain an existing projects directory.")
        self.projects = ProjectService(self.workspace)
        self._store = self.projects.store

    def get_context(self) -> ToolContext:
        try:
            return self.projects.tool_context(self.project_id, self.version_id)
        except (KeyError, ValueError, OSError) as error:
            raise ContextUnavailableError(str(error)) from error

    def get_model_context(self) -> ToolContext:
        try:
            return self.projects.tool_context(self.project_id, self.version_id, requirement='model')
        except (KeyError, ValueError, OSError) as error:
            raise ContextUnavailableError(str(error)) from error

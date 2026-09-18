"""Workspace ownership and small durable storage primitives (no desktop imports)."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any


class WorkspaceBusyError(RuntimeError):
    """Another writer owns this workspace."""


class ConflictError(ValueError):
    """An idempotency key or frozen engineering input no longer matches."""


_guard = threading.Lock()
_locks = {}
_owners = set()


def record_id(value: str, label: str = "record") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Invalid " + label + " ID")
    return value


def contained(path: Path, root: Path) -> Path:
    path, root = Path(path).resolve(), Path(root).resolve()
    if path != root and root not in path.parents:
        raise ValueError("Artifact escapes its managed directory")
    return path


def artifact_relative_path(value: str) -> Path:
    """Validate manifest paths before resolving or opening any artifact.

    Check both separators on every platform; Windows ADS and drive-relative
    paths must not become an alternative way to address a managed file.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid artifact manifest")
    relative = value.replace("\\", "/")
    if ":" in relative or relative.startswith("/") or ".." in relative.split("/"):
        raise ValueError("Invalid artifact manifest")
    return Path(relative)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    from storage.session import SessionStore

    SessionStore._write_json(path, value)
    if os.name != "nt":
        Path(path).chmod(0o600)


def read_json(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Invalid durable record")
    return value


def private_state_dir(path: Path, workspace: Path) -> Path:
    path, workspace = Path(path).resolve(), Path(workspace).resolve()
    if path == workspace or workspace in path.parents:
        raise ValueError("Application state must be outside the engineering workspace")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def bind_workspace_state(path: Path, workspace: Path) -> Path:
    """Bind application records to exactly one workspace, without storing its path.

    This is intentionally separate from private_state_dir: the OS lock directory
    is shared by many workspaces, whereas job/proposal state must never be shared.
    """
    path = private_state_dir(path, workspace)
    marker = contained(path / ".workspace.json", path)
    key = os.path.normcase(str(Path(workspace).expanduser().resolve()))
    expected = {"schema_version": 1, "workspace_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest()}
    try:
        descriptor = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if read_json(marker) != expected:
            raise ConflictError("Application state belongs to another workspace")
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(expected, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    return path


# Only these fields may cross a transport boundary from jobs/proposals. In
# particular, snapshots, model configuration and arbitrary artifact paths cannot.
_PUBLIC_FIELDS = frozenset({
    "id", "job_id", "proposal_id", "project_id", "version_id", "base_version_id",
    "candidate_id", "status", "summary", "message", "progress", "stage", "kind",
    "action", "target_mode", "plc_model", "valid", "counts", "error", "warning",
    "info", "network_count", "instruction_count", "device_count", "result",
    "proposal", "version", "diagnostics", "error_code", "completed", "total",
    "request_id", "created_at", "updated_at", "sequence", "type", "data",
    "cancel_requested", "last_sequence", "name", "label", "accepted", "reason",
    "event_type", "payload", "diff", "validation", "findings", "rules_checked",
    "added", "deleted", "modified", "changes", "marker", "network", "comment",
    "device_comments_changed", "before_network_count", "after_network_count",
    "rule", "severity", "code", "description", "messages", "network_id",
    "text", "report_id", "plan_id", "run_id", "proposal_ids", "analysis_id",
    "outcome", "passed", "environment", "reasoning",
    "gx_compile_status", "simulation_status", "operation", "approval_id",
    "success", "attempt_id", "candidate_version_id", "source_run_id",
    "change_scope", "impact", "network_ids", "addresses", "device_comments",
    "metadata_fields", "network_order_changed", "normalization", "skipped",
    # Explicit model activity/preview fields; no provider configuration or
    # tool-call arguments are included in these presentation events.
    "phase", "received_characters", "request_number", "provisional", "truncated", "content",
})


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: public_payload(item) for key, item in value.items() if key in _PUBLIC_FIELDS}
        if "error_details" in value:
            # Diagnostic JSON paths are schema locations, not arbitrary file
            # paths. Reproject this one typed resource without widening the
            # general job/proposal field whitelist.
            from .job_errors import public_error_details
            result["error_details"] = public_error_details(value["error_details"])
        if isinstance(value.get("gx_import_summary"), dict):
            # Keep timing evidence without exposing the importer's private paths
            # or expanding the generic payload whitelist to arbitrary details.
            summary = value["gx_import_summary"]
            projected = {}
            if isinstance(summary.get("pre_import_policy"), str) and summary["pre_import_policy"] in {"protected", "manual_backup"}:
                projected["pre_import_policy"] = summary["pre_import_policy"]
            if type(summary.get("backup_performed")) is bool:
                projected["backup_performed"] = summary["backup_performed"]
            stages = {"validate_csv", "validate_comments", "check_project", "backup", "backup_comments",
                      "prepare_import", "compare_baseline", "import", "import_comments", "verify_roundtrip",
                      "record_baseline", "save_project", "verify", "total"}
            timings = summary.get("timings_ms")
            if isinstance(timings, dict):
                projected["timings_ms"] = {key: duration for key, duration in timings.items()
                    if key in stages and type(duration) in (int, float) and math.isfinite(duration) and duration >= 0}
            result["gx_import_summary"] = projected
        return result
    if isinstance(value, (list, tuple)):
        return [public_payload(item) for item in value]
    if isinstance(value, str):
        # Paths belong in managed artifact URLs, never in raw service responses.
        if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|etc)/)", value):
            return "[private path]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return copy.deepcopy(value)
    return None


class WorkspaceWriterLock:
    """Lifetime cross-process writer lock, plus a shared in-process transaction lock.

    The lock file is outside the workspace so opening an old workspace does not
    touch its bytes. The OS releases ownership on process death; files may remain.
    """

    def __init__(self, workspace: Path, lock_dir: Path | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.key = os.path.normcase(str(self.workspace))
        with _guard:
            self.thread_lock = _locks.setdefault(self.key, threading.RLock())
        root = Path(lock_dir) if lock_dir is not None else Path(tempfile.gettempdir()) / "gxworks-agent-locks"
        self.lock_dir = private_state_dir(root, self.workspace)
        self.lock_path = self.lock_dir / (hashlib.sha256(self.key.encode("utf-8")).hexdigest() + ".lock")
        self._stream = None

    @property
    def acquired(self) -> bool:
        return self._stream is not None

    def acquire(self):
        with _guard:
            if self.acquired:
                return self
            if self.key in _owners:
                raise WorkspaceBusyError("This workspace already has an active writer")
            stream = self.lock_path.open("a+b")
            try:
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError) as exc:
                stream.close()
                raise WorkspaceBusyError("This workspace already has an active writer") from exc
            self._stream = stream
            _owners.add(self.key)
        return self

    def require_acquired(self) -> None:
        if not self.acquired:
            raise WorkspaceBusyError("Workspace writer ownership is required")

    def release(self):
        with _guard:
            if not self.acquired:
                return
            stream, self._stream = self._stream, None
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
                _owners.discard(self.key)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()

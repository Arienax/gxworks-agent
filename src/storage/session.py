# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import copy
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from shared.records import record_id as checked_record_id
from weakref import WeakValueDictionary


_logger = logging.getLogger(__name__)
_record_locks = WeakValueDictionary()
_record_locks_guard = threading.Lock()


def _record_lock(path):
    """Serialize local file access without blocking readers on native operations."""
    key = os.path.normcase(str(Path(path).resolve()))
    with _record_locks_guard:
        lock = _record_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _record_locks[key] = lock
        return lock


def _index_transaction(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with _record_lock(self.index_path):
            return method(self, *args, **kwargs)
    return locked


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_PROJECT_DEFAULTS = {
    "plc_model": "FX3U",
    "target_mode": "ladder",
    "effort": None,
    "workflow_mode": "generate",
    "messages": [],
    "confirmed_spec": None,
    "pending_review": None,
    "versions": [],
    "reports": [],
    "active_version_id": None,
}

_VERSION_METADATA_DEFAULTS = {
    "plc_model": None,
    "program_name": "MAIN",
    "revision": None,
    "ir_schema_version": None,
    "ir_sha256": None,
    "ladder_sha256": None,
    "st_from_ir_sha256": None,
    "st_renderer_schema_version": None,
    "semantic_schema_version": None,
    "semantic_summary": None,
    "static_analysis_schema_version": None,
    "static_analysis_summary": None,
    "timing_analysis_schema_version": None,
    "timing_summary": None,
    "simulator_runs": [],
    "simulator_test_plans": [],
    "multi_agent_runs": [],
    "last_simulator_status": None,
    # Whether a version is selected is represented by project.active_version_id.
    # Lifecycle describes the immutable version's Debug/Patch disposition.
    "lifecycle_status": "accepted",
    "debug_attempts": [],
    "confirmed_spec_snapshot": None,
    "confirmed_spec_hash": None,
    "parent_version_id": None,
    "source_report_id": None,
    "selected_finding_ids": [],
    "review_report_id": None,
}

_REPORT_INDEX_FIELDS = (
    "report_id",
    "report_type",
    "trigger",
    "depth",
    "base_version_id",
    "base_json_hash",
    "plc_model",
    "status",
    "summary",
    "created_at",
    "updated_at",
)

_IMAGE_MEDIA_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_IMAGE_ATTACHMENT_COUNT = 12
MAX_IMAGE_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_IMAGE_ATTACHMENTS_TOTAL_BYTES = 30 * 1024 * 1024


def detect_image_media_type(data):
    """Identify supported image bytes instead of trusting a filename suffix."""

    data = bytes(data or b"")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def default_workspace_dir():
    """Resolve the former Qt app-data workspace; never create or move files."""
    override = os.environ.get("PLC_AI_WORKSPACE_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA", "").strip()
        root = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        root = Path(data_home) if data_home and Path(data_home).is_absolute() else Path.home() / ".local" / "share"
    return root / "PLC AI Studio" / "PLC AI Workbench" / "workspace"


class SessionStore:
    """Persistent project, conversation, and generated-version storage."""

    def __init__(self, base_dir=None, legacy_dir=None, *, create=True):
        """Open the existing format without importing a GUI or migrating paths.

        An explicit directory wins over PLC_AI_WORKSPACE_DIR. The default keeps
        the former desktop application's organization/application path so that
        retiring the UI does not silently create a different workspace.
        """
        if base_dir is None:
            base_dir = default_workspace_dir()
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.index_path = self.base_dir / "index.json"
        self.legacy_dir = Path(legacy_dir) if legacy_dir else Path.cwd()
        if create:
            self.projects_dir.mkdir(parents=True, exist_ok=True)
            with _record_lock(self.index_path):
                missing = object()
                if self._read_json(self.index_path, missing) is missing:
                    self._write_json(self.index_path, {"projects": [], "legacy_imported": False})

    @staticmethod
    def _read_json(path, default=None):
        # Only absence means an optional record/default. A failed read must
        # never become an empty index that a subsequent transaction can save.
        with _record_lock(path):
            for attempt in range(5):
                try:
                    with Path(path).open("r", encoding="utf-8") as f:
                        return json.load(f)
                except FileNotFoundError:
                    return default
                except (OSError, ValueError, TypeError) as error:
                    if (isinstance(error, PermissionError)
                            and getattr(error, "winerror", None) in {5, 32, 33} and attempt < 4):
                        time.sleep(0.01 * 2 ** attempt)
                        continue
                    _logger.error("Cannot read durable JSON record %s", path, exc_info=True)
                    raise

    @staticmethod
    def _write_json(path, payload):
        with _record_lock(path):
            SessionStore._write_json_locked(path, payload)

    @staticmethod
    def _write_json_locked(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Windows readers/virus scanners can briefly deny replacing a
            # complete file. Retry only the known Windows access/share errors;
            # retain the old document until the atomic replacement succeeds.
            for attempt in range(5):
                try:
                    os.replace(str(temp_path), str(path))
                    break
                except PermissionError as error:
                    if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.01 * 2 ** attempt)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _load_index(self):
        index = self._read_json(
            self.index_path, {"projects": [], "legacy_imported": False}
        )
        if (not isinstance(index, dict) or not isinstance(index.get("projects"), list)
                or any(not isinstance(value, str) for value in index["projects"])
                or type(index.get("legacy_imported", False)) is not bool):
            raise ValueError("Invalid project index; refusing to replace it")
        return index

    def _save_index(self, index):
        self._write_json(self.index_path, index)

    def project_dir(self, project_id):
        return self.projects_dir / project_id

    def project_path(self, project_id):
        return self.project_dir(project_id) / "project.json"

    def version_dir(self, project_id, version_id):
        return self.project_dir(project_id) / "versions" / version_id

    def attachments_dir(self, project_id):
        return self.project_dir(project_id) / "attachments"

    def import_image_attachments(self, project_id, source_paths):
        """Copy validated local images into one project and return safe records."""

        if self.get_project(project_id) is None:
            raise KeyError(project_id)
        paths = [Path(value) for value in (source_paths or [])]
        if not paths:
            return []
        if len(paths) > MAX_IMAGE_ATTACHMENT_COUNT:
            raise ValueError(
                f"一次最多添加 {MAX_IMAGE_ATTACHMENT_COUNT} 张图片。"
            )

        prepared = []
        total_bytes = 0
        for path in paths:
            if not path.is_file():
                raise ValueError(f"找不到图片：{path.name or path}")
            size = path.stat().st_size
            if size <= 0:
                raise ValueError(f"图片内容为空：{path.name}")
            if size > MAX_IMAGE_ATTACHMENT_BYTES:
                raise ValueError(f"单张图片不能超过 32 MiB：{path.name}")
            total_bytes += size
            if total_bytes > MAX_IMAGE_ATTACHMENTS_TOTAL_BYTES:
                raise ValueError("本次图片总大小不能超过 30 MiB。")
            data = path.read_bytes()
            media_type = detect_image_media_type(data)
            if not media_type:
                raise ValueError(
                    f"不支持的图片格式：{path.name}。仅支持 JPEG、PNG、GIF、WebP。"
                )
            prepared.append((path, data, media_type))

        target_dir = self.attachments_dir(project_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        records = []
        created = []
        try:
            for path, data, media_type in prepared:
                attachment_id = uuid.uuid4().hex
                stored_name = attachment_id + _IMAGE_MEDIA_EXTENSIONS[media_type]
                target = target_dir / stored_name
                temporary = target.with_name(f".{stored_name}.{uuid.uuid4().hex}.tmp")
                temporary.write_bytes(data)
                os.replace(str(temporary), str(target))
                created.append(target)
                records.append(
                    {
                        "attachment_id": attachment_id,
                        "filename": path.name,
                        "stored_name": stored_name,
                        "media_type": media_type,
                        "size_bytes": len(data),
                    }
                )
        except Exception:
            for target in created:
                try:
                    target.unlink()
                except OSError:
                    pass
            raise
        return records

    def load_image_attachment(self, project_id, record):
        """Load one persisted attachment without allowing a path escape."""

        if not isinstance(record, Mapping):
            raise TypeError("图片附件记录格式无效。")
        stored_name = str(record.get("stored_name") or "").strip()
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|gif|webp)", stored_name):
            raise ValueError("图片附件记录包含无效文件名。")
        root = self.attachments_dir(project_id).resolve()
        path = (root / stored_name).resolve()
        if root not in path.parents:
            raise ValueError("图片附件路径超出项目目录。")
        data = path.read_bytes()
        media_type = detect_image_media_type(data)
        if not media_type or media_type != str(record.get("media_type") or ""):
            raise ValueError("图片附件内容或格式已损坏。")
        if len(data) > MAX_IMAGE_ATTACHMENT_BYTES:
            raise ValueError("图片附件超过 32 MiB。")
        return data

    def reports_dir(self, project_id):
        return self.project_dir(project_id) / "reports"

    @staticmethod
    def _validate_record_id(value, label):
        return checked_record_id(str(value or "").strip(), message=f"Invalid {label}")

    def report_path(self, project_id, report_id):
        report_id = self._validate_record_id(report_id, "report id")
        return self.reports_dir(project_id) / f"{report_id}.json"

    @staticmethod
    def _with_version_defaults(version):
        normalized = dict(version or {})
        for key, default in _VERSION_METADATA_DEFAULTS.items():
            if key not in normalized:
                normalized[key] = list(default) if isinstance(default, list) else default
        return normalized

    @classmethod
    def _with_project_defaults(cls, project):
        normalized = dict(project or {})
        for key, default in _PROJECT_DEFAULTS.items():
            if key not in normalized:
                normalized[key] = list(default) if isinstance(default, list) else default
        versions = normalized.get("versions")
        if not isinstance(versions, list):
            versions = []
        normalized["versions"] = [
            cls._with_version_defaults(version)
            for version in versions
            if isinstance(version, dict)
        ]
        if not isinstance(normalized.get("messages"), list):
            normalized["messages"] = []
        if not isinstance(normalized.get("reports"), list):
            normalized["reports"] = []
        return normalized

    def list_projects(self):
        index = self._load_index()
        projects = []
        for project_id in index.get("projects", []):
            project = self.get_project(project_id)
            if project:
                projects.append(project)
        return sorted(
            projects, key=lambda item: item.get("updated_at", ""), reverse=True
        )

    @_index_transaction
    def create_project(
        self,
        name="新项目",
        plc_model="FX3U",
        target_mode="ladder",
        effort=None,
        native_project_type=None,
        use_labels=None,
    ):
        index = self._load_index()
        project_id = uuid.uuid4().hex[:12]
        now = _utc_now()
        project = {
            "id": project_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "plc_model": plc_model,
            "target_mode": target_mode,
            "native_project_type": native_project_type or ("simple" if target_mode == "ladder" else "structured"),
            "use_labels": target_mode != "ladder" if use_labels is None else use_labels,
            "effort": None,
            "workflow_mode": "generate",
            "messages": [],
            "confirmed_spec": None,
            "pending_review": None,
            "versions": [],
            "reports": [],
            "active_version_id": None,
        }
        self.save_project(project)
        index.setdefault("projects", []).append(project_id)
        self._save_index(index)
        return project

    def get_project(self, project_id):
        missing = object()
        project = self._read_project_record(project_id, missing)
        if project is missing:
            return None
        if not isinstance(project, dict):
            raise ValueError("Invalid project record")
        return self._with_project_defaults(project)

    def save_project(self, project):
        project = self._with_project_defaults(project)
        project["updated_at"] = _utc_now()
        self._save_project_record(project)
        return project

    @_index_transaction
    def delete_project(self, project_id):
        index = self._load_index()
        project_dir = self.project_dir(project_id).resolve()
        projects_root = self.projects_dir.resolve()
        if projects_root not in project_dir.parents:
            raise ValueError("Project path escaped the workspace root")
        if project_dir.exists():
            shutil.rmtree(project_dir)
        index["projects"] = [
            item for item in index.get("projects", []) if item != project_id
        ]
        self._save_index(index)

    def _read_project_record(self, project_id, default=None):
        from . import project_records
        manifest = self._read_json(self.project_path(project_id), default)
        if isinstance(manifest, dict) and "storage_format" in manifest:
            if manifest["storage_format"] != project_records.FORMAT:
                raise ValueError("Unsupported project record format")
            project = project_records.read(self.project_dir(project_id) / "records.sqlite3")
            if project.get("id") != project_id or manifest.get("id") != project_id:
                raise ValueError("Project records belong to another project")
            return project
        return manifest

    def _save_project_record(self, project):
        from . import project_records
        path = self.project_path(project["id"])
        with _record_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            manifest = self._read_json(path, {})
            if not isinstance(manifest, dict) or manifest.get("storage_format", project_records.FORMAT) != project_records.FORMAT:
                raise ValueError("Unsupported project record format")
            project_records.save(path.parent / "records.sqlite3", project)
            if manifest.get("storage_format") != project_records.FORMAT:
                # Publish only after the complete database transaction succeeds.
                # A failed manifest write leaves the legacy document authoritative.
                self._write_json(path, {"id": project["id"], "storage_format": project_records.FORMAT})

    def add_message(self, project_id, role, content, kind="message", metadata=None):
        from . import project_records
        message = {
            "id": uuid.uuid4().hex[:12], "role": role, "kind": kind,
            "content": str(content), "created_at": _utc_now(), "metadata": metadata or {},
        }
        path = self.project_path(project_id)
        with _record_lock(path):
            manifest = self._read_json(path)
            if manifest is None:
                raise KeyError(project_id)
            if manifest.get("storage_format") != project_records.FORMAT:
                # First explicit write imports the legacy record once.
                self._save_project_record(self.get_project(project_id))
            project_records.append_message(path.parent / "records.sqlite3", message, updated_at=message["created_at"])
        return message

    def update_project_settings(
        self, project_id, plc_model=None, target_mode=None, effort=None,
        name=None, workflow_mode=None, native_project_type=None, use_labels=None
    ):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        updates = {
            "plc_model": plc_model,
            "target_mode": target_mode,
            "effort": None,
            "name": name,
            "workflow_mode": workflow_mode,
            "native_project_type": native_project_type,
            "use_labels": use_labels,
        }
        for key, value in updates.items():
            if value is not None:
                project[key] = value
        return self.save_project(project)


    def set_pending_review(self, project_id, pending_review):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        project["pending_review"] = pending_review
        return self.save_project(project)

    def prepare_version(self, project_id):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        next_number = len(project.get("versions", [])) + 1
        while True:
            version_id = f"v{next_number:04d}"
            output_dir = self.version_dir(project_id, version_id)
            if not output_dir.exists():
                break
            next_number += 1
        output_dir.mkdir(parents=True, exist_ok=False)
        return version_id, output_dir

    def complete_version(self, project_id, version_id, metadata, *, activate=True):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        metadata = copy.deepcopy(metadata)
        version = self._with_version_defaults(
            {
                "id": version_id,
                "created_at": _utc_now(),
                **dict(metadata),
            }
        )
        # Persist the complete version before the project's atomic visibility
        # and activation commit. Failed writes must leave the prior selection.
        self._write_json(
            self.version_dir(project_id, version_id) / "version.json", version
        )
        project.setdefault("versions", []).append(version)
        if activate:
            project["active_version_id"] = version_id
        self.save_project(project)
        return version

    def update_version_metadata(self, project_id, version_id, updates):
        """Atomically update metadata without changing immutable artifacts or id."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if not isinstance(updates, Mapping):
            raise TypeError("version updates must be an object")
        forbidden = {"id", "created_at", "artifacts", "ir_sha256", "ladder_sha256"}
        overlap = forbidden.intersection(updates)
        if overlap:
            raise ValueError("immutable version fields cannot be changed: " + ", ".join(sorted(overlap)))
        updated = None
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != version_id:
                continue
            merged = self._with_version_defaults(current)
            merged.update(dict(updates))
            merged["id"] = version_id
            project["versions"][index] = merged
            updated = merged
            break
        if updated is None:
            raise KeyError(version_id)
        self.save_project(project)
        self._write_json(
            self.version_dir(project_id, version_id) / "version.json", updated
        )
        return updated

    def activate_version(self, project_id, version_id):
        """Select an existing completed version without altering its artifacts."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if not any(item.get("id") == version_id for item in project.get("versions", [])):
            raise KeyError(version_id)
        version = self.get_version(project_id, version_id)
        project["active_version_id"] = version_id
        self.save_project(project)
        return self.get_version(project_id, version_id)

    def save_debug_attempt(self, project_id, base_version_id, attempt):
        """Persist one append-only Debug/Patch attempt and index it on the base."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if self.get_version(project_id, base_version_id) is None:
            raise KeyError(base_version_id)
        if not isinstance(attempt, Mapping):
            raise TypeError("debug attempt must be an object")
        attempt_id = self._validate_record_id(
            attempt.get("attempt_id") or "dbg_" + uuid.uuid4().hex[:16],
            "debug attempt id",
        )
        payload = dict(attempt)
        payload["attempt_id"] = attempt_id
        payload["project_id"] = project_id
        payload["base_version_id"] = base_version_id
        payload.setdefault("created_at", _utc_now())
        path = self.project_dir(project_id) / "debug" / f"{attempt_id}.json"
        self._write_json(path, payload)

        record = {
            "attempt_id": attempt_id,
            "status": str(payload.get("status") or ""),
            "candidate_version_id": payload.get("candidate_version_id"),
            "created_at": payload["created_at"],
            "artifact": str(path.relative_to(self.project_dir(project_id))).replace("\\", "/"),
        }
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != base_version_id:
                continue
            current = self._with_version_defaults(current)
            current["debug_attempts"] = list(current.get("debug_attempts") or []) + [record]
            project["versions"][index] = current
            self._write_json(
                self.version_dir(project_id, base_version_id) / "version.json",
                current,
            )
            break
        self.save_project(project)
        return payload

    def save_debug_plan(self, project_id, base_version_id, plan):
        """Persist an immutable user-reviewable Debug/Patch plan."""

        if self.get_project(project_id) is None:
            raise KeyError(project_id)
        if self.get_version(project_id, base_version_id) is None:
            raise KeyError(base_version_id)
        if not isinstance(plan, Mapping):
            raise TypeError("debug plan must be an object")
        if str(plan.get("base_version_id") or "") != str(base_version_id):
            raise ValueError("debug plan base version does not match")
        plan_id = self._validate_record_id(
            plan.get("plan_id") or "plan_" + uuid.uuid4().hex[:16],
            "debug plan id",
        )
        payload = dict(plan)
        payload["plan_id"] = plan_id
        payload["project_id"] = project_id
        payload["base_version_id"] = base_version_id
        payload.setdefault("created_at", _utc_now())
        path = self.project_dir(project_id) / "debug" / "plans" / f"{plan_id}.json"
        if path.exists():
            raise ValueError("debug plan already exists")
        self._write_json(path, payload)
        return payload

    def load_debug_plan(self, project_id, plan_id):
        plan_id = self._validate_record_id(plan_id, "debug plan id")
        if self.get_project(project_id) is None:
            return None
        payload = self._read_json(
            self.project_dir(project_id) / "debug" / "plans" / f"{plan_id}.json"
        )
        return payload if isinstance(payload, dict) else None

    def load_debug_attempt(self, project_id, attempt_id):
        attempt_id = self._validate_record_id(attempt_id, "debug attempt id")
        if self.get_project(project_id) is None:
            return None
        payload = self._read_json(
            self.project_dir(project_id) / "debug" / f"{attempt_id}.json"
        )
        return payload if isinstance(payload, dict) else None

    def discard_version(self, project_id, version_id):
        output_dir = self.version_dir(project_id, version_id).resolve()
        project_root = self.project_dir(project_id).resolve()
        if project_root not in output_dir.parents:
            raise ValueError("Version path escaped the project root")
        if output_dir.exists():
            shutil.rmtree(output_dir)

    def get_version(self, project_id, version_id):
        project = self.get_project(project_id)
        if not project:
            return None
        for version in project.get("versions", []):
            if version.get("id") == version_id:
                return version
        return None


    def latest_version(self, project_id):
        project = self.get_project(project_id)
        if not project or not project.get("versions"):
            return None
        return project["versions"][-1]


    def list_simulator_runs(self, project_id, version_id):
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        return list(version.get("simulator_runs") or [])


    @staticmethod
    def _report_payload(report):
        if hasattr(report, "to_dict") and callable(report.to_dict):
            payload = report.to_dict()
        elif is_dataclass(report):
            payload = asdict(report)
        elif isinstance(report, Mapping):
            payload = dict(report)
        else:
            raise TypeError("report must be a mapping, dataclass, or expose to_dict()")
        if not isinstance(payload, dict):
            raise TypeError("report serialization must produce an object")
        return payload

    @staticmethod
    def _report_index_entry(report):
        entry = {key: report.get(key) for key in _REPORT_INDEX_FIELDS}
        base = report.get("base") if isinstance(report.get("base"), dict) else {}
        execution = (
            report.get("execution")
            if isinstance(report.get("execution"), dict)
            else {}
        )
        entry["base_version_id"] = (
            entry.get("base_version_id") or base.get("version_id")
        )
        entry["base_json_hash"] = (
            entry.get("base_json_hash")
            or base.get("json_sha256")
            or base.get("json_hash")
        )
        entry["plc_model"] = entry.get("plc_model") or base.get("plc_model")
        entry["status"] = entry.get("status") or execution.get("status")
        findings = report.get("findings")
        entry["finding_count"] = len(findings) if isinstance(findings, list) else 0
        return entry

    def _upsert_report_index(self, project, report):
        entry = self._report_index_entry(report)
        report_id = entry["report_id"]
        report_index = []
        replaced = False
        for current in project.get("reports", []):
            current_id = (
                current.get("report_id") if isinstance(current, dict) else str(current)
            )
            if current_id == report_id:
                report_index.append(entry)
                replaced = True
            else:
                report_index.append(current)
        if not replaced:
            report_index.append(entry)
        project["reports"] = report_index

    def create_report(self, project_id, report):
        """Atomically persist one report and add its lightweight project index."""
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        payload = self._report_payload(report)
        legacy_id = payload.pop("id", None)
        report_id = payload.get("report_id") or legacy_id
        if not report_id:
            report_id = f"r{uuid.uuid4().hex[:12]}"
        report_id = self._validate_record_id(report_id, "report id")
        path = self.report_path(project_id, report_id)
        if path.exists():
            raise FileExistsError(report_id)

        now = _utc_now()
        payload["schema_version"] = int(payload.get("schema_version") or 1)
        payload["report_id"] = report_id
        payload["created_at"] = payload.get("created_at") or now
        payload["updated_at"] = now
        self._write_json(path, payload)
        self._upsert_report_index(project, payload)
        self.save_project(project)
        return payload

    def get_report(self, project_id, report_id):
        if self.get_project(project_id) is None:
            return None
        payload = self._read_json(self.report_path(project_id, report_id))
        return payload if isinstance(payload, dict) else None

    def update_report(self, project_id, report_id, report_or_updates):
        """Atomically merge report fields and refresh the project report index."""
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        report_id = self._validate_record_id(report_id, "report id")
        current = self.get_report(project_id, report_id)
        if current is None:
            raise KeyError(report_id)
        updates = self._report_payload(report_or_updates)
        update_id = updates.get("report_id") or updates.get("id")
        if update_id is not None and str(update_id) != report_id:
            raise ValueError("Report id cannot be changed")
        updates.pop("id", None)
        merged = dict(current)
        merged.update(updates)
        merged["report_id"] = report_id
        merged["schema_version"] = int(merged.get("schema_version") or 1)
        merged["created_at"] = current.get("created_at") or _utc_now()
        merged["updated_at"] = _utc_now()
        self._write_json(self.report_path(project_id, report_id), merged)
        self._upsert_report_index(project, merged)
        self.save_project(project)
        return merged

    def list_reports(
        self, project_id, base_version_id=None, report_type=None
    ):
        """Return full reports, newest first, with optional stable filters."""
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)

        report_ids = []
        for entry in project.get("reports", []):
            report_id = entry.get("report_id") if isinstance(entry, dict) else entry
            if report_id and report_id not in report_ids:
                report_ids.append(str(report_id))
        reports_dir = self.reports_dir(project_id)
        if reports_dir.exists():
            for path in reports_dir.glob("*.json"):
                if path.stem not in report_ids:
                    report_ids.append(path.stem)

        reports = []
        for report_id in report_ids:
            report = self.get_report(project_id, report_id)
            if not report:
                continue
            report_base = report.get("base_version_id")
            if report_base is None and isinstance(report.get("base"), dict):
                report_base = report["base"].get("version_id")
            if base_version_id is not None and report_base != base_version_id:
                continue
            if report_type is not None and report.get("report_type") != report_type:
                continue
            reports.append(report)
        return sorted(
            reports,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("report_id") or ""),
            ),
            reverse=True,
        )

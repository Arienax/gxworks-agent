import copy
import json
import os
import re
import shutil
import sys
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path


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
            if not self.index_path.exists():
                self._write_json(self.index_path, {"projects": [], "legacy_imported": False})

    @staticmethod
    def _read_json(path, default=None):
        try:
            with Path(path).open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            return default

    @staticmethod
    def _write_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(temp_path), str(path))
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _load_index(self):
        return self._read_json(
            self.index_path, {"projects": [], "legacy_imported": False}
        )

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
        value = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError(f"Invalid {label}")
        return value

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
        spec = normalized.get("confirmed_spec")
        from plc.specification.provenance import needs_spec_migration
        if needs_spec_migration(spec):
            from plc.specification.provenance import migrate_confirmed_spec
            clean, receipt = migrate_confirmed_spec(spec)
            old_history = normalized.get("decision_history")
            history = copy.deepcopy(dict(old_history)) if isinstance(old_history, Mapping) else {}
            history[receipt["receipt_id"]] = receipt
            normalized["decision_history"] = history
            normalized["confirmed_decision_receipt_id"] = receipt["receipt_id"]
            normalized["confirmed_spec"] = clean
        # This is an in-memory read adapter. A subsequent explicit save commits
        # the pair via one existing atomic project write; versions stay intact.
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

    def create_project(
        self,
        name="新项目",
        plc_model="FX3U",
        target_mode="ladder",
        effort=None,
    ):
        project_id = uuid.uuid4().hex[:12]
        now = _utc_now()
        project = {
            "id": project_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "plc_model": plc_model,
            "target_mode": target_mode,
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
        index = self._load_index()
        index.setdefault("projects", []).append(project_id)
        self._save_index(index)
        return project

    def get_project(self, project_id):
        project = self._read_json(self.project_path(project_id))
        if not isinstance(project, dict):
            return None
        return self._with_project_defaults(project)

    def save_project(self, project):
        project = self._with_project_defaults(project)
        project["updated_at"] = _utc_now()
        self._write_json(self.project_path(project["id"]), project)
        return project

    def delete_project(self, project_id):
        project_dir = self.project_dir(project_id).resolve()
        projects_root = self.projects_dir.resolve()
        if projects_root not in project_dir.parents:
            raise ValueError("Project path escaped the workspace root")
        if project_dir.exists():
            shutil.rmtree(project_dir)
        index = self._load_index()
        index["projects"] = [
            item for item in index.get("projects", []) if item != project_id
        ]
        self._save_index(index)

    def add_message(self, project_id, role, content, kind="message", metadata=None):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        message = {
            "id": uuid.uuid4().hex[:12],
            "role": role,
            "kind": kind,
            "content": str(content),
            "created_at": _utc_now(),
            "metadata": metadata or {},
        }
        project.setdefault("messages", []).append(message)
        self.save_project(project)
        return message

    def update_project_settings(
        self, project_id, plc_model=None, target_mode=None, effort=None,
        name=None, workflow_mode=None
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
        }
        for key, value in updates.items():
            if value is not None:
                project[key] = value
        return self.save_project(project)

    def set_confirmed_spec(self, project_id, confirmed_spec):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if confirmed_spec is not None:
            from plc.specification.provenance import seal_confirmation
            old_history = project.get("decision_history")
            history = copy.deepcopy(dict(old_history)) if isinstance(old_history, Mapping) else {}
            previous = history.get(project.get("confirmed_decision_receipt_id"))
            confirmed_spec, receipt = seal_confirmation(confirmed_spec, previous_receipt=previous)
            history[receipt["receipt_id"]] = receipt
            project["decision_history"] = history
            project["confirmed_decision_receipt_id"] = receipt["receipt_id"]
        else:
            project["confirmed_decision_receipt_id"] = None
        project["confirmed_spec"] = confirmed_spec
        return self.save_project(project)

    def get_decision_receipt(self, project_id, receipt_id):
        """Read a specific immutable receipt; never substitute the latest one."""
        project = self.get_project(project_id)
        history = project.get("decision_history") if isinstance(project, Mapping) else None
        receipt = history.get(receipt_id) if isinstance(history, Mapping) and receipt_id else None
        from plc.specification.provenance import receipt_is_intact
        return copy.deepcopy(receipt) if receipt_is_intact(receipt, receipt_id) else None

    def migrate_confirmed_specs(self):
        """Explicit writer-only upgrade; retain timestamps and every version value.

        The application owns the workspace lock. Each project is a single atomic
        transaction: clean facts, archived legacy snapshot, and receipt reference.
        A failed write leaves the previous project intact and is reported, not
        converted into a generation/confirmation gate.
        """
        from plc.specification.provenance import needs_spec_migration
        report = {"migrated": [], "failed": []}
        for project_id in self._load_index().get("projects", []):
            try:
                self._validate_record_id(project_id, "project id")
                path = self.project_path(project_id)
                if not path.resolve().is_relative_to(self.projects_dir.resolve()):
                    raise ValueError("Project path escaped workspace")
                raw = self._read_json(path)
                if not isinstance(raw, Mapping) or not needs_spec_migration(raw.get("confirmed_spec")):
                    continue
                upgraded = self._with_project_defaults(raw)
                persisted = copy.deepcopy(raw)
                for key in ("confirmed_spec", "decision_history", "confirmed_decision_receipt_id"):
                    persisted[key] = upgraded[key]
                self._write_json(path, persisted)
                report["migrated"].append(project_id)
            except (OSError, ValueError, TypeError, KeyError):
                report["failed"].append(str(project_id))
        return report

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
        version = self._with_version_defaults(
            {
                "id": version_id,
                "created_at": _utc_now(),
                **dict(metadata),
            }
        )
        project.setdefault("versions", []).append(version)
        if activate:
            project["active_version_id"] = version_id
        self.save_project(project)
        self._write_json(
            self.version_dir(project_id, version_id) / "version.json", version
        )
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

    def load_ladder(self, project_id, version_id, *, persist_legacy=True):
        """Load a ladder through canonical IR, migrating legacy data on view."""

        version = self.get_version(project_id, version_id)
        if not version or version.get("target_mode") != "ladder":
            return None
        artifacts = version.get("artifacts", {}) or {}
        version_dir = self.version_dir(project_id, version_id)
        try:
            from plc.ir import ir_to_ladder

            program = self.load_program_ir(
                project_id, version_id, persist_legacy=persist_legacy
            )
            if isinstance(program, dict):
                return ir_to_ladder(program)
        except (OSError, TypeError, ValueError):
            # A valid legacy ladder remains readable if migration cannot be
            # completed (for example, a future-schema IR is present).
            pass
        json_name = str(artifacts.get("json") or "").strip()
        ladder = self._read_json(version_dir / json_name) if json_name else None
        return ladder if isinstance(ladder, dict) else None

    @staticmethod
    def _ir_metadata(program):
        """Build the deterministic metadata projection stored beside an IR."""

        import copy

        from plc.ir import canonical_sha256
        from plc.semantics import SEMANTICS_SCHEMA_VERSION
        from plc.static_analysis import STATIC_ANALYSIS_SCHEMA_VERSION
        from plc.timing import TIMING_ANALYSIS_SCHEMA_VERSION

        logic = program.get("logic") or {}
        timing_root = program.get("timing") or {}
        performance = timing_root.get("performance") or {}
        analysis = program.get("analysis") or {}
        return {
            "program_name": str(program.get("program_name") or "MAIN"),
            "revision": int(program.get("revision") or 0),
            "ir_schema_version": program.get("schema_version"),
            "ir_sha256": canonical_sha256(program),
            "ladder_sha256": (program.get("source") or {}).get("ladder_sha256"),
            "semantic_schema_version": SEMANTICS_SCHEMA_VERSION,
            "semantic_summary": {
                "requirements": copy.deepcopy(logic.get("requirements") or []),
                "coverage": copy.deepcopy(timing_root.get("coverage") or []),
                "state_machine_count": len(logic.get("state_machines") or []),
                "regions": [
                    {
                        "code": region.get("code"),
                        "kind": region.get("kind"),
                        "network_count": len(region.get("network_refs") or []),
                    }
                    for region in logic.get("regions") or []
                    if isinstance(region, Mapping)
                ],
            },
            "static_analysis_schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
            "static_analysis_summary": {
                "counts": copy.deepcopy(analysis.get("counts") or {}),
                "rules_checked": list(analysis.get("rules_checked") or []),
                "dependency_nodes": len(
                    (analysis.get("dependency_graph") or {}).get("nodes") or []
                ),
                "dependency_edges": len(
                    (analysis.get("dependency_graph") or {}).get("device_edges") or []
                ),
            },
            "timing_analysis_schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
            "timing_summary": {
                "profile": performance.get("profile"),
                "estimate": copy.deepcopy(performance.get("estimate") or {}),
                "scan_budget": copy.deepcopy(performance.get("scan_budget") or {}),
                "scan_monitor": copy.deepcopy(performance.get("scan_monitor") or {}),
                "pulse_capture_assessments": copy.deepcopy(
                    timing_root.get("pulse_capture_assessments") or []
                ),
            },
        }

    def _persist_legacy_program_ir(self, project_id, version_id, program):
        """Atomically add canonical IR to one legacy version without rewriting it."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        version_path = self.version_dir(project_id, version_id)
        ir_name = "program.ir.json"
        ir_path = version_path / ir_name
        metadata = self._ir_metadata(program)
        updated = None
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != version_id:
                continue
            merged = self._with_version_defaults(current)
            recorded_hash = str(merged.get("ir_sha256") or "")
            if recorded_hash and recorded_hash != metadata["ir_sha256"]:
                raise ValueError("Legacy version IR hash conflicts with stored metadata")
            artifacts = dict(merged.get("artifacts") or {})
            artifacts["ir"] = ir_name
            merged.update(metadata)
            merged["artifacts"] = artifacts
            project["versions"][index] = merged
            updated = merged
            break
        if updated is None:
            raise KeyError(version_id)

        # Each write is atomic.  Write the self-validating artifact first; if a
        # later metadata write is interrupted the next load safely repeats the
        # same deterministic migration.
        self._write_json(ir_path, program)
        self.save_project(project)
        self._write_json(version_path / "version.json", updated)
        return updated

    def load_program_ir(self, project_id, version_id, *, persist_legacy=True):
        """Load canonical IR, optionally persisting a legacy ladder's upgrade.

        The desktop keeps migration-on-view. External readers can request the
        same deterministic compatibility view without writing to the project.
        """

        version = self.get_version(project_id, version_id)
        if not version or version.get("target_mode") != "ladder":
            return None
        artifacts = version.get("artifacts", {}) or {}
        version_dir = self.version_dir(project_id, version_id)
        ir_name = str(artifacts.get("ir") or "").strip()
        can_persist_legacy = not ir_name
        if ir_name:
            program = self._read_json(version_dir / ir_name)
            if isinstance(program, dict):
                try:
                    from plc.ir import validate_plc_ir

                    validate_plc_ir(
                        program,
                        confirmed_spec=version.get("confirmed_spec_snapshot"),
                        validate_ladder=(version.get("validation_profile") != "generation_structural"),
                    )
                    return program
                except (TypeError, ValueError):
                    pass
        json_name = str(artifacts.get("json") or "").strip()
        ladder = self._read_json(version_dir / json_name) if json_name else None
        if not isinstance(ladder, dict):
            return None
        from plc.ir import build_plc_ir, revision_from_value, validate_plc_ir

        program = build_plc_ir(
            ladder,
            plc_model=version.get("plc_model") or "FX3U",
            program_name=version.get("program_name") or "MAIN",
            revision=revision_from_value(
                version.get("revision"),
                default=revision_from_value(version_id, default=1),
            ),
            confirmed_spec=version.get("confirmed_spec_snapshot"),
            semantic_requirements=(
                (version.get("semantic_summary") or {}).get("requirements", [])
                if isinstance(version.get("semantic_summary"), dict)
                else []
            ),
        )
        validate_plc_ir(
            program,
            confirmed_spec=version.get("confirmed_spec_snapshot"),
            validate_ladder=(version.get("validation_profile") != "generation_structural"),
        )
        # If metadata already names an IR that this runtime cannot validate,
        # keep that artifact byte-for-byte for a newer reader.  The rebuilt IR
        # is only an in-memory compatibility view in that case.
        if can_persist_legacy and persist_legacy:
            self._persist_legacy_program_ir(project_id, version_id, program)
        return program

    def latest_version(self, project_id):
        project = self.get_project(project_id)
        if not project or not project.get("versions"):
            return None
        return project["versions"][-1]

    def save_simulator_run(self, project_id, version_id, suite, result, *, execution_snapshot=None):
        """Persist one immutable simulator suite/result pair against an exact IR.

        Test evidence is append-only.  The stored IR hash and revision prevent a
        later Debug/Patch pass from accidentally diagnosing a different program.
        """

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        program = self.load_program_ir(project_id, version_id)
        if not isinstance(program, dict):
            raise ValueError("Simulator tests require a ladder version with PLC IR")

        from plc.ir import canonical_sha256
        from simulator.models import normalize_test_suite
        from simulator.verification import (
            EVIDENCE_SCHEMA_VERSION, SimulatorEvidenceError, blocked_verification,
            evaluate_suite_result, execution_binding, require_matching_binding,
        )

        ir_sha256 = canonical_sha256(program)
        recorded_hash = str(version.get("ir_sha256") or "")
        if recorded_hash and recorded_hash != ir_sha256:
            raise SimulatorEvidenceError("version_conflict", "Version IR hash no longer matches its stored metadata")
        normalized_suite = normalize_test_suite(
            suite,
            plc_model=str(program.get("plc", {}).get("cpu") or "FX3U"),
        )
        if not isinstance(result, Mapping):
            raise TypeError("Simulator result must be an object")
        status = str(result.get("status") or "")
        if status not in {"passed", "failed", "error", "unavailable"}:
            raise ValueError("Simulator result has an invalid status")
        if str(result.get("plc_model") or "").upper() != normalized_suite["plc_model"]:
            raise ValueError("Simulator result PLC model does not match the suite")
        result = copy.deepcopy(dict(result))
        expected = execution_binding(project_id, version_id, program, normalized_suite)
        if execution_snapshot is not None:
            require_matching_binding(execution_snapshot, expected)
        verification = evaluate_suite_result(normalized_suite, result)
        if status == "passed" and verification["status"] != "passed":
            raise SimulatorEvidenceError(
                verification["category"],
                "仿真结果缺少完整且一致的通过证据：" + verification["detail"],
            )
        if execution_snapshot is None:
            verification = blocked_verification(
                "legacy_evidence", "No snapshot captured before execution."
            )

        run_id = "sim_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        run_id = self._validate_record_id(run_id, "simulator run id")
        version_path = self.version_dir(project_id, version_id)
        suite_path = version_path / "tests" / f"{run_id}.suite.json"
        trace_path = version_path / "traces" / f"{run_id}.result.json"
        binding = {
            **expected,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "binding_scope": "execution_snapshot" if execution_snapshot is not None else "persistence_snapshot",
            "run_id": run_id,
            "result_sha256": canonical_sha256(result),
            "created_at": _utc_now(),
        }
        suite_payload = {"binding": binding, "suite": normalized_suite}
        result_payload = {"binding": binding, "result": dict(result)}
        self._write_json(suite_path, suite_payload)
        self._write_json(trace_path, result_payload)

        record = {
            **binding,
            "status": status,
            "passed": status == "passed",
            "suite_name": normalized_suite["name"],
            "test_count": len(normalized_suite["tests"]),
            "counts": dict(result.get("counts") or {}),
            "backend_kinds": list(result.get("backend_kinds") or []),
            "verification": verification,
            "suite_artifact": str(suite_path.relative_to(version_path)).replace("\\", "/"),
            "trace_artifact": str(trace_path.relative_to(version_path)).replace("\\", "/"),
        }
        updated = False
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != version_id:
                continue
            current = self._with_version_defaults(current)
            current["simulator_runs"] = list(current.get("simulator_runs") or []) + [record]
            current["last_simulator_status"] = status
            project["versions"][index] = current
            version = current
            updated = True
            break
        if not updated:
            raise KeyError(version_id)
        self.save_project(project)
        self._write_json(version_path / "version.json", version)
        return record

    def save_multi_agent_run(self, project_id, version_id, run):
        """Persist one append-only P9 supervisor audit for an exact PLC IR."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        if not isinstance(run, Mapping):
            raise TypeError("multi-agent run must be an object")
        program = self.load_program_ir(project_id, version_id)
        if not isinstance(program, dict):
            raise ValueError("Multi-agent runs require a ladder version with PLC IR")

        from plc.ir import canonical_sha256

        binding = run.get("binding")
        if not isinstance(binding, Mapping):
            raise ValueError("multi-agent run has no version binding")
        expected = {
            "project_id": project_id,
            "version_id": version_id,
            "revision": int(program.get("revision") or 0),
            "ir_sha256": canonical_sha256(program),
        }
        if any(binding.get(key) != value for key, value in expected.items()):
            raise ValueError("multi-agent run is stale or cross-version")
        if str(version.get("ir_sha256") or "") not in {"", expected["ir_sha256"]}:
            raise ValueError("Version IR hash no longer matches its stored metadata")
        route = run.get("route")
        stages = run.get("stages")
        if not isinstance(route, list) or not isinstance(stages, list):
            raise ValueError("multi-agent run is missing route or stages")
        if [str(item.get("role") or "") for item in stages] != [
            str(item) for item in route
        ]:
            raise ValueError("multi-agent audit route does not match its stages")

        run_id = self._validate_record_id(
            run.get("run_id") or "agents_" + uuid.uuid4().hex[:16],
            "multi-agent run id",
        )
        version_path = self.version_dir(project_id, version_id)
        artifact_path = version_path / "agents" / f"{run_id}.json"
        if artifact_path.exists():
            raise ValueError("multi-agent run already exists")
        payload = dict(run)
        payload["run_id"] = run_id
        payload["binding"] = dict(expected)
        payload.setdefault("created_at", _utc_now())
        self._write_json(artifact_path, payload)
        record = {
            **expected,
            "run_id": run_id,
            "workflow": str(payload.get("workflow") or ""),
            "status": str(payload.get("status") or ""),
            "route": [str(item) for item in route],
            "created_at": payload["created_at"],
            "artifact": str(artifact_path.relative_to(version_path)).replace("\\", "/"),
        }
        updated = False
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != version_id:
                continue
            current = self._with_version_defaults(current)
            current["multi_agent_runs"] = list(
                current.get("multi_agent_runs") or []
            ) + [record]
            project["versions"][index] = current
            version = current
            updated = True
            break
        if not updated:
            raise KeyError(version_id)
        self.save_project(project)
        self._write_json(version_path / "version.json", version)
        return payload

    def load_multi_agent_run(self, project_id, version_id, run_id):
        """Load and revalidate one exact-version supervisor audit."""

        run_id = self._validate_record_id(run_id, "multi-agent run id")
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        record = next(
            (
                item
                for item in version.get("multi_agent_runs", []) or []
                if isinstance(item, dict) and item.get("run_id") == run_id
            ),
            None,
        )
        if record is None:
            return None
        version_path = self.version_dir(project_id, version_id)
        artifact_path = (version_path / str(record.get("artifact") or "")).resolve()
        if version_path.resolve() not in artifact_path.parents:
            raise ValueError("Multi-agent artifact path escaped the version directory")
        payload = self._read_json(artifact_path)
        program = self.load_program_ir(project_id, version_id)
        if not isinstance(payload, dict) or not isinstance(program, dict):
            return None
        from plc.ir import canonical_sha256

        binding = payload.get("binding") or {}
        if (
            binding.get("project_id") != project_id
            or binding.get("version_id") != version_id
            or binding.get("revision") != int(program.get("revision") or 0)
            or binding.get("ir_sha256") != canonical_sha256(program)
        ):
            raise ValueError("multi-agent run is stale or cross-version")
        return payload

    def save_simulator_test_plan(self, project_id, version_id, suite, *, source="ai"):
        """Persist an immutable, version-bound simulator plan before approval."""

        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        program = self.load_program_ir(project_id, version_id)
        if not isinstance(program, dict):
            raise ValueError("Simulator plans require a ladder version with PLC IR")

        from plc.ir import canonical_sha256
        from simulator.planning import normalize_generated_test_suite

        normalized_suite = normalize_generated_test_suite(suite, program)
        ir_sha256 = canonical_sha256(program)
        recorded_hash = str(version.get("ir_sha256") or "")
        if recorded_hash and recorded_hash != ir_sha256:
            raise ValueError("Version IR hash no longer matches its stored metadata")
        plan_id = (
            "testplan_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )
        plan_id = self._validate_record_id(plan_id, "simulator test plan id")
        version_path = self.version_dir(project_id, version_id)
        plan_path = version_path / "tests" / "plans" / f"{plan_id}.json"
        binding = {
            "plan_id": plan_id,
            "project_id": project_id,
            "version_id": version_id,
            "revision": program.get("revision"),
            "ir_sha256": ir_sha256,
            "created_at": _utc_now(),
        }
        payload = {
            "schema_version": 1,
            "binding": binding,
            "source": str(source or "ai"),
            "suite": normalized_suite,
        }
        self._write_json(plan_path, payload)
        record = {
            **binding,
            "source": payload["source"],
            "suite_name": normalized_suite["name"],
            "test_count": len(normalized_suite["tests"]),
            "plan_artifact": str(plan_path.relative_to(version_path)).replace("\\", "/"),
        }
        updated = False
        for index, current in enumerate(project.get("versions", [])):
            if current.get("id") != version_id:
                continue
            current = self._with_version_defaults(current)
            current["simulator_test_plans"] = list(
                current.get("simulator_test_plans") or []
            ) + [record]
            project["versions"][index] = current
            version = current
            updated = True
            break
        if not updated:
            raise KeyError(version_id)
        self.save_project(project)
        self._write_json(version_path / "version.json", version)
        return payload

    def load_simulator_test_plan(self, project_id, version_id, plan_id, *, persist_legacy=True):
        plan_id = self._validate_record_id(plan_id, "simulator test plan id")
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        record = next(
            (
                item
                for item in version.get("simulator_test_plans", []) or []
                if isinstance(item, dict) and item.get("plan_id") == plan_id
            ),
            None,
        )
        if record is None:
            return None
        version_path = self.version_dir(project_id, version_id)
        plan_path = (version_path / str(record.get("plan_artifact") or "")).resolve()
        root = version_path.resolve()
        if root not in plan_path.parents:
            raise ValueError("Simulator plan artifact path escaped the version directory")
        payload = self._read_json(plan_path)
        if not isinstance(payload, dict):
            return None
        program = self.load_program_ir(project_id, version_id, persist_legacy=persist_legacy)
        if not isinstance(program, dict):
            return None
        from plc.ir import canonical_sha256

        binding = payload.get("binding") or {}
        if (
            binding.get("project_id") != project_id
            or binding.get("version_id") != version_id
            or binding.get("plan_id") != plan_id
            or binding.get("revision") != program.get("revision")
            or binding.get("ir_sha256") != canonical_sha256(program)
        ):
            raise ValueError("Simulator test plan is stale or cross-version")
        return payload

    def load_latest_simulator_test_plan(self, project_id, version_id):
        """Return the newest usable test plan bound to the exact PLC IR.

        Plan artifacts are immutable.  A missing, damaged, or stale newest
        artifact therefore must not make an older valid artifact unusable.
        The suite is normalized again in memory so plans created by an older
        application build still receive the current deterministic checks; the
        saved artifact itself is never rewritten.
        """

        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        program = self.load_program_ir(project_id, version_id)
        if not isinstance(program, dict):
            return None

        from simulator.planning import normalize_generated_test_suite

        records = version.get("simulator_test_plans") or []
        for record in reversed(records):
            if not isinstance(record, dict):
                continue
            plan_id = str(record.get("plan_id") or "").strip()
            if not plan_id:
                continue
            try:
                payload = self.load_simulator_test_plan(
                    project_id, version_id, plan_id
                )
                if not isinstance(payload, dict):
                    continue
                normalized = normalize_generated_test_suite(
                    payload.get("suite"), program
                )
            except (OSError, TypeError, ValueError):
                continue
            reusable = dict(payload)
            reusable["suite"] = normalized
            reusable["cache_reused"] = True
            return reusable
        return None

    def list_simulator_runs(self, project_id, version_id):
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        return list(version.get("simulator_runs") or [])

    def load_simulator_run(self, project_id, version_id, run_id):
        run_id = self._validate_record_id(run_id, "simulator run id")
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        record = next(
            (
                item
                for item in version.get("simulator_runs", []) or []
                if isinstance(item, dict) and item.get("run_id") == run_id
            ),
            None,
        )
        if record is None:
            return None
        version_path = self.version_dir(project_id, version_id)
        suite_path = (version_path / str(record.get("suite_artifact") or "")).resolve()
        trace_path = (version_path / str(record.get("trace_artifact") or "")).resolve()
        root = version_path.resolve()
        if root not in suite_path.parents or root not in trace_path.parents:
            raise ValueError("Simulator artifact path escaped the version directory")
        suite = self._read_json(suite_path)
        trace = self._read_json(trace_path)
        if not isinstance(suite, dict) or not isinstance(trace, dict):
            return None
        from plc.ir import canonical_sha256
        from simulator.verification import (
            EVIDENCE_SCHEMA_VERSION, SimulatorEvidenceError, blocked_verification,
            evaluate_suite_result, execution_binding, require_matching_binding,
        )

        # Never merge the two envelopes: a trace binding must not overwrite
        # conflicting suite provenance, nor may either replace the index record.
        binding = suite.get("binding")
        if not isinstance(binding, dict) or binding != trace.get("binding"):
            raise SimulatorEvidenceError("evidence_invalid", "仿真套件与轨迹的版本绑定不一致。")
        if any(record.get(key) != value for key, value in binding.items()):
            raise SimulatorEvidenceError("evidence_invalid", "仿真证据与版本索引不一致。")
        program = self.load_program_ir(project_id, version_id, persist_legacy=False)
        if not isinstance(program, Mapping):
            raise SimulatorEvidenceError("version_conflict", "仿真证据对应的程序版本已缺失。")
        saved_suite, result = suite.get("suite"), trace.get("result")
        if not isinstance(saved_suite, Mapping) or not isinstance(result, Mapping):
            raise SimulatorEvidenceError("evidence_invalid", "仿真证据缺少套件或结果。")
        expected = execution_binding(project_id, version_id, program, saved_suite)
        if (
            binding.get("run_id") != run_id
            or any(binding.get(key) != expected[key] for key in ("project_id", "version_id", "revision", "ir_sha256"))
            or (version.get("ir_sha256") and version["ir_sha256"] != expected["ir_sha256"])
        ):
            raise SimulatorEvidenceError("version_conflict", "仿真证据不属于当前程序版本。")
        schema = binding.get("evidence_schema_version")
        if schema is None:
            # Old artifacts remain readable, without migration writes or
            # retroactively granting them integrity/execution provenance.
            verification = blocked_verification("legacy_evidence")
        elif schema != EVIDENCE_SCHEMA_VERSION:
            raise SimulatorEvidenceError("evidence_invalid", "不支持的仿真证据版本。")
        else:
            require_matching_binding(binding, expected)
            if (
                binding.get("suite_sha256") != canonical_sha256(saved_suite)
                or binding.get("result_sha256") != canonical_sha256(result)
            ):
                raise SimulatorEvidenceError("evidence_invalid", "仿真套件或轨迹内容已变化，请重新测试。")
            verification = evaluate_suite_result(saved_suite, result)
            if binding.get("binding_scope") != "execution_snapshot":
                verification = blocked_verification(
                    "legacy_evidence", "No snapshot captured before execution."
                )
            expected_index = {
                "verification": verification, "status": result.get("status"),
                "passed": result.get("status") == "passed", "suite_name": saved_suite["name"],
                "test_count": len(saved_suite["tests"]), "counts": dict(result.get("counts") or {}),
                "backend_kinds": list(result.get("backend_kinds") or []),
            }
            if any(record.get(key) != value for key, value in expected_index.items()):
                raise SimulatorEvidenceError("evidence_invalid", "仿真验收结果与索引不一致。")
        return {
            "record": copy.deepcopy(record), "binding": dict(binding),
            "suite": saved_suite, "result": result, "verification": verification,
        }

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

    def import_legacy_once(self):
        index = self._load_index()
        if index.get("legacy_imported"):
            return None

        history = self._read_json(self.legacy_dir / "chat_history.json", [])
        confirmed = self._read_json(
            self.legacy_dir / "confirmed_requirements.json", {}
        )
        ladder = self._read_json(self.legacy_dir / "real_deepseek_output.json")
        has_legacy = bool(history or confirmed or ladder)

        imported_project = None
        if has_legacy:
            imported_project = self.create_project(name="导入的旧会话")
            project_id = imported_project["id"]
            for item in history or []:
                self.add_message(
                    project_id,
                    item.get("role", "user"),
                    item.get("content", ""),
                    metadata={
                        "reasoning": item.get("reasoning", "")
                        or item.get("reasoning_content", "")
                    },
                )
            context = confirmed.get("context") if isinstance(confirmed, dict) else None
            if context:
                self.set_confirmed_spec(
                    project_id, {"legacy_context": str(context)}
                )
            if ladder:
                version_id, output_dir = self.prepare_version(project_id)
                self._write_json(output_dir / "ladder.json", ladder)
                for source_name in (
                    "temp_ladder.svg",
                    "plc_import_program.csv",
                    "plc_import_comment.csv",
                ):
                    source = self.legacy_dir / source_name
                    if source.exists():
                        shutil.copy2(source, output_dir / source.name)
                self.complete_version(
                    project_id,
                    version_id,
                    {
                        "target_mode": "ladder",
                        "summary": "从旧版全局会话导入",
                        "artifacts": {
                            "json": "ladder.json",
                            "svg": "temp_ladder.svg",
                            "program_csv": "plc_import_program.csv",
                            "comment_csv": "plc_import_comment.csv",
                        },
                        "validation": {"status": "imported", "messages": []},
                    },
                )
            imported_project = self.get_project(project_id)

        index = self._load_index()
        index["legacy_imported"] = True
        self._save_index(index)
        return imported_project

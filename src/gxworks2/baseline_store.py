"""Persistent last-import baselines for GX Works2 overwrite protection."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


BASELINE_SCHEMA_VERSION = 2


class BaselineStoreError(RuntimeError):
    """The persisted protection state exists but cannot be trusted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_context(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "project_id",
        "version_id",
        "revision",
        "program_name",
        "ir_schema_version",
        "ir_sha256",
        "ladder_sha256",
    }
    result = {}
    for key in allowed:
        item = value.get(key)
        if item is not None and isinstance(item, (str, int, float, bool)):
            result[key] = item
    return result


class ImportBaselineStore:
    """Atomically persist the program last imported into each GX project."""

    def __init__(self, backup_root):
        self.backup_root = Path(backup_root).expanduser().resolve()
        self.state_root = self.backup_root / ".import_state"

    @staticmethod
    def project_identity(session, project_name="", project_identity="") -> dict:
        executable = str(getattr(session, "executable", "") or "").strip()
        name = str(
            project_identity
            or project_name
            or getattr(session, "project_name", "")
            or getattr(session, "title", "")
            or "GXWorks2"
        ).strip()
        return {
            "application": "GX Works2",
            "executable": executable.casefold(),
            "project": name.casefold(),
        }

    @staticmethod
    def project_key(identity: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            dict(identity),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def path_for(self, identity: Mapping[str, Any]) -> Path:
        return self.state_root / f"{self.project_key(identity)}.json"

    def load(self, identity: Mapping[str, Any]):
        path = self.path_for(identity)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError) as error:
            raise BaselineStoreError(
                f"版本保护基线无法读取：{path}"
            ) from error
        if not isinstance(payload, dict):
            raise BaselineStoreError("版本保护基线不是有效对象")
        schema_version = payload.get("schema_version")
        if schema_version not in {1, BASELINE_SCHEMA_VERSION}:
            raise BaselineStoreError("版本保护基线格式版本不受支持")
        if payload.get("project_identity") != dict(identity):
            raise BaselineStoreError("版本保护基线与当前GX Works2工程不匹配")
        semantic_hash = str(payload.get("program_semantic_sha256", "") or "")
        if len(semantic_hash) != 64:
            raise BaselineStoreError("版本保护基线缺少有效的程序摘要")
        if schema_version == 1:
            # Version 1 represented the common program after a successful
            # push.  It remains a valid two-way base for program comparison;
            # comment hashes are learned on the next inspection.
            payload = dict(payload)
            payload["app_program_semantic_sha256"] = semantic_hash
            payload["gx_program_semantic_sha256"] = semantic_hash
            payload["app_comment_semantic_sha256"] = ""
            payload["gx_comment_semantic_sha256"] = ""
            payload["comments_semantic_sha256"] = ""
            payload["legacy_schema_version"] = 1
        else:
            for key in (
                "app_program_semantic_sha256",
                "gx_program_semantic_sha256",
            ):
                value = str(payload.get(key, "") or "")
                if len(value) != 64:
                    raise BaselineStoreError(f"版本保护基线缺少有效字段：{key}")
            for key in (
                "app_comment_semantic_sha256",
                "gx_comment_semantic_sha256",
                "comments_semantic_sha256",
            ):
                value = str(payload.get(key, "") or "")
                if value and len(value) != 64:
                    raise BaselineStoreError(f"版本保护基线字段无效：{key}")
        return payload

    def save(
        self,
        identity: Mapping[str, Any],
        *,
        program_semantic_sha256: str,
        program_file_sha256: str,
        import_context=None,
        app_program_semantic_sha256: str = "",
        gx_program_semantic_sha256: str = "",
        comments_semantic_sha256: str = "",
        app_comment_semantic_sha256: str = "",
        gx_comment_semantic_sha256: str = "",
    ) -> dict:
        semantic_hash = str(program_semantic_sha256 or "").strip().lower()
        file_hash = str(program_file_sha256 or "").strip().lower()
        if len(semantic_hash) != 64 or len(file_hash) != 64:
            raise ValueError("baseline hashes must be SHA256 values")
        app_program_hash = str(
            app_program_semantic_sha256 or semantic_hash
        ).strip().lower()
        gx_program_hash = str(
            gx_program_semantic_sha256 or semantic_hash
        ).strip().lower()
        if len(app_program_hash) != 64 or len(gx_program_hash) != 64:
            raise ValueError("sync program hashes must be SHA256 values")
        comment_hash = str(comments_semantic_sha256 or "").strip().lower()
        app_comment_hash = str(
            app_comment_semantic_sha256 or comment_hash
        ).strip().lower()
        gx_comment_hash = str(
            gx_comment_semantic_sha256 or comment_hash
        ).strip().lower()
        for value in (comment_hash, app_comment_hash, gx_comment_hash):
            if value and len(value) != 64:
                raise ValueError("sync comment hashes must be empty or SHA256 values")
        payload = {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "project_identity": dict(identity),
            "program_semantic_sha256": semantic_hash,
            "program_file_sha256": file_hash,
            "app_program_semantic_sha256": app_program_hash,
            "gx_program_semantic_sha256": gx_program_hash,
            "comments_semantic_sha256": comment_hash or gx_comment_hash,
            "app_comment_semantic_sha256": app_comment_hash,
            "gx_comment_semantic_sha256": gx_comment_hash,
            "import_context": _clean_context(import_context),
            "updated_at": _utc_now(),
        }
        path = self.path_for(identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return payload


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BaselineStoreError",
    "ImportBaselineStore",
]

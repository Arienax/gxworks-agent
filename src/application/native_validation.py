"""Operator-reported native results, bound to immutable GXW bytes.

This service archives evidence only. It neither runs GX Works2 nor upgrades the
version's automatic compilation status based on an operator assertion.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
from datetime import datetime, timezone

from .fbd import decode_upload, graph_diff
from .projects import public
from .workspace import ConflictError, atomic_json, canonical_hash, contained, read_json, record_id


class NativeValidationService:
    def __init__(self, workbench):
        self.workbench = workbench

    def _source(self, project_id, version_id):
        wb = self.workbench
        version = wb.projects.raw_version(project_id, version_id)
        if version.get("target_mode") != "fbd":
            raise ValueError("Native GXW evidence requires an FBD version")
        data = wb.projects.artifact(project_id, version_id, "gxw").read_bytes()
        version_root = wb.store.version_dir(project_id, version_id)
        root = contained(version_root / "native-validation", version_root)
        return data, root

    def _view(self, item, root, current_hash, project_id, version_id):
        result = {k: item[k] for k in ("id", "project_id", "version_id", "created_at", "source_gxw_sha256",
            "operator", "tool_version", "outcome", "report", "automatic_verification")}
        result["binding_current"] = (current_hash == item["source_gxw_sha256"]
                                     and item["project_id"] == project_id and item["version_id"] == version_id)
        native = item.get("native_gxw")
        if native:
            path = contained(root / (record_id(item["id"]) + ".gxw"), root)
            result["native_gxw"] = {k: native[k] for k in ("filename", "sha256", "size", "matches_source_bytes", "selected_program_matches")}
            result["native_gxw"]["integrity_verified"] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == native["sha256"]
        return public(result)

    def list(self, project_id, version_id):
        with self.workbench.lock.thread_lock if self.workbench.lock else nullcontext():
            raw, root = self._source(project_id, version_id)
            digest = hashlib.sha256(raw).hexdigest()
            records = [self._view(read_json(contained(path, root)), root, digest, project_id, version_id)
                       for path in sorted(root.glob("native_*.json"))] if root.is_dir() else []
            records.sort(key=lambda row: row["created_at"], reverse=True)
            return {"source_gxw_sha256": digest, "automatic_verification": "not_run", "records": records}

    def record(self, project_id, version_id, command):
        wb = self.workbench
        wb.writable()
        record_id(command["request_id"])
        if command.get("outcome") not in ("passed", "failed", "inconclusive"):
            raise ValueError("Unsupported native validation outcome")
        for name in ("operator", "tool_version", "report"):
            if not isinstance(command.get(name), str) or not command[name].strip():
                raise ValueError("Native validation requires operator, tool version and report")
        if command.get("attested") is not True:
            raise ValueError("Operator must attest to the native evidence")
        record_key = "native_" + hashlib.sha256(command["request_id"].encode()).hexdigest()[:32]
        with wb.lock.thread_lock:
            source, root = self._source(project_id, version_id)
            digest = hashlib.sha256(source).hexdigest()
            if command.get("source_gxw_sha256") != digest:
                raise ConflictError("GXW changed since the operator selected the source")
            saved = contained(root / (record_key + ".json"), root)
            command_hash = canonical_hash(command)
            if saved.is_file():
                item = read_json(saved)
                if (item["command_hash"] != command_hash or item["project_id"] != project_id
                        or item["version_id"] != version_id or item["id"] != record_key):
                    raise ConflictError("Native validation request ID is already bound to another report")
                return self._view(item, root, digest, project_id, version_id)
            item = {"id": record_key, "project_id": project_id, "version_id": version_id,
                    "created_at": datetime.now(timezone.utc).isoformat(), "source_gxw_sha256": digest,
                    "command_hash": command_hash, "automatic_verification": "not_run",
                    **{k: command[k].strip() for k in ("operator", "tool_version", "report")},
                    "outcome": command["outcome"]}
            upload = command.get("native_gxw")
            if upload:
                from .fbd import read_snapshot
                filename = upload.get("filename", "")
                if not filename.lower().endswith(".gxw") or "/" in filename or "\\" in filename or ":" in filename:
                    raise ValueError("Upload a GXW filename without a directory")
                raw = decode_upload(upload.get("data_base64", ""))
                program_name = wb.projects.program(project_id, version_id)["program"]
                same_program = not graph_diff(read_snapshot(source, program_name)['model'],
                                              read_snapshot(raw, program_name)['model'])['has_changes']
                native_hash = hashlib.sha256(raw).hexdigest()
                item["native_gxw"] = {"filename": filename, "sha256": native_hash, "size": len(raw),
                    "matches_source_bytes": native_hash == digest, "selected_program_matches": same_program}
                root.mkdir(parents=True, exist_ok=True)
                contained(root / (record_key + ".gxw"), root).write_bytes(raw)
            atomic_json(saved, item)
            return self._view(item, root, digest, project_id, version_id)

    def artifact(self, project_id, version_id, evidence_id):
        record_id(evidence_id)
        with self.workbench.lock.thread_lock if self.workbench.lock else nullcontext():
            _, root = self._source(project_id, version_id)
            saved = contained(root / (evidence_id + ".json"), root)
            if not saved.is_file():
                raise KeyError("Native evidence does not exist")
            item = read_json(saved)
            if item["project_id"] != project_id or item["version_id"] != version_id or item["id"] != evidence_id:
                raise ConflictError("Native evidence belongs to a different project or version")
            if not item.get("native_gxw"):
                raise KeyError("No native GXW attached")
            path = contained(root / (evidence_id + ".gxw"), root)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["native_gxw"]["sha256"]:
                raise ConflictError("Native GXW evidence changed after recording")
            return path

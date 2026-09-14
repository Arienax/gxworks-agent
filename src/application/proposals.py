"""Durable, explicitly approved engineering actions with private frozen inputs."""

from __future__ import annotations

import copy
import hashlib
import uuid
from pathlib import Path

from .events import utc_now
from .workspace import (ConflictError, artifact_relative_path, atomic_json, bind_workspace_state, canonical_hash, contained,
                        private_state_dir, public_payload, read_json, record_id)


_ACTIONS = frozenset({"accept_local", "gx_import", "simulation", "debug"})
_PUBLIC = ("id", "action", "project_id", "base_version_id", "status", "created_at",
           "updated_at", "summary", "result", "error_code")


class _NoMigrationStore:
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    def load_program_ir(self, project_id, version_id, **kwargs):
        return self.store.load_program_ir(project_id, version_id, persist_legacy=False)


class ProposalService:
    def __init__(self, store, state_dir, writer_lock):
        self.store, self.lock = store, writer_lock
        self.lock.require_acquired()
        if Path(store.base_dir).resolve() != self.lock.workspace:
            raise ValueError("Proposal store must match the locked workspace")
        self.state_dir = bind_workspace_state(state_dir, self.lock.workspace)
        self.directory = contained(self.state_dir / "proposals", self.state_dir)
        self.directory.mkdir(exist_ok=True)
        with self.lock.thread_lock:
            for path in self.directory.glob("*.json"):
                record = self._load(path.stem)
                if record["status"] == "executing":
                    record.update(status="interrupted", error_code="execution_requires_reconciliation")
                    self._save(record)

    def _load(self, proposal_id):
        path = contained(self.directory / (record_id(proposal_id, "proposal") + ".json"), self.directory)
        if not path.is_file():
            raise KeyError(proposal_id)
        record = read_json(path)
        if record.get("id") != proposal_id:
            raise ValueError("Invalid proposal record")
        return record

    def _save(self, record):
        self.lock.require_acquired()
        record["updated_at"] = utc_now()
        atomic_json(contained(self.directory / (record_id(record["id"]) + ".json"), self.directory), record)

    @staticmethod
    def _public(record):
        result = public_payload({key: record.get(key) for key in _PUBLIC})
        # Deliberately project only server-authored enum values. Do not widen
        # the generic job payload allowlist to arbitrary source/mode fields.
        audit = (record.get("summary") or {}).get("approval")
        if isinstance(audit, dict) and audit.get("source") in {"user", "local_autosave", "policy"}:
            mode = audit.get("mode")
            if mode is None or mode in {"ask", "auto", "full"}:
                result.setdefault("summary", {})["approval"] = {"source": audit["source"], "mode": mode}
        return result

    def get(self, proposal_id):
        with self.lock.thread_lock:
            return self._public(self._load(proposal_id))

    def read_private(self, proposal_id):
        """Backend-only copied payload. Never expose this method as a route."""
        with self.lock.thread_lock:
            record = self._load(proposal_id)
            self._verify_payload(record)
            return copy.deepcopy(record["private_payload"])

    def list(self, project_id=None):
        with self.lock.thread_lock:
            records = [self._load(path.stem) for path in self.directory.glob("*.json")]
            if project_id is not None:
                records = [r for r in records if r["project_id"] == project_id]
            return [self._public(r) for r in sorted(records, key=lambda r: r["created_at"], reverse=True)]

    def _project(self, project_id):
        record_id(project_id, "project")
        root = contained(self.store.project_dir(project_id), self.lock.workspace / "projects")
        contained(self.store.project_path(project_id), root)
        project = self.store.get_project(project_id)
        if not isinstance(project, dict) or project.get("id") != project_id:
            raise KeyError(project_id)
        # Reject redirected future-version output directories before any writes.
        contained(root / "versions", root)
        return project

    def _base_snapshot(self, project_id, version_id):
        if version_id is None:
            return None
        record_id(version_id, "version")
        version = self.store.get_version(project_id, version_id)
        if not isinstance(version, dict):
            raise ConflictError("The proposal's base version is missing")
        project_root = contained(self.store.project_dir(project_id), self.lock.workspace / "projects")
        root = contained(self.store.version_dir(project_id, version_id), project_root)
        artifacts = version.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            raise ValueError("Invalid version artifacts")
        # Validate the entire manifest before hashing even its first artifact.
        paths = {key: contained(root / artifact_relative_path(filename), root)
                 for key, filename in artifacts.items()}
        hashes = {}
        for key, path in paths.items():
            if not path.is_file():
                raise ConflictError("The proposal's base artifact is missing")
            hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        program = self.store.load_program_ir(project_id, version_id, persist_legacy=False)
        return {"metadata_hash": canonical_hash(version), "artifact_hashes": hashes,
                "ir_sha256": canonical_hash(program) if program is not None else None}

    def _freeze_staging(self, proposal_id, payload):
        manifest = payload.get("artifacts")
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError("Candidate requires a managed artifact manifest")
        source = contained(Path(payload.get("staging_dir") or ""), self.state_dir)
        if not source.is_dir():
            raise ValueError("Candidate staging directory is missing")
        prepared = []
        for name, entry in manifest.items():
            record_id(name, "artifact")
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("Invalid staging manifest")
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
                raise ValueError("Invalid staging artifact path")
            data = contained(source / relative, source).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise ConflictError("Staged artifact hash changed")
            prepared.append((name, relative, data))
        if payload.get("target_mode") == "fbd":
            from .fbd import validate_candidate
            validate_candidate({name: data for name, _relative, data in prepared})
        target = contained(self.directory / proposal_id, self.directory)
        target.mkdir(exist_ok=False)
        for name, relative, data in prepared:
            path = contained(target / relative, target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        payload["staging_dir"] = str(target)
        return payload

    def create(self, action, project_id, private_payload, public_summary=None,
               base_version_id=None, request_id=None):
        if action not in _ACTIONS:
            raise ValueError("Unsupported proposal action")
        if not isinstance(private_payload, dict):
            raise ValueError("Proposal payload must be an object")
        if request_id is not None:
            record_id(request_id, "request")
        payload = copy.deepcopy(private_payload)
        if payload.get("project_id") not in (None, "", project_id):
            raise ConflictError("Proposal project binding does not match")
        payload["project_id"] = project_id
        selected = base_version_id or payload.get("base_version_id") or payload.get("version_id") or None
        digest = canonical_hash({"action": action, "project_id": project_id, "base_version_id": selected, "payload": payload})
        with self.lock.thread_lock:
            self.lock.require_acquired()
            if request_id is not None:
                for path in self.directory.glob("*.json"):
                    prior = self._load(path.stem)
                    if prior.get("request_id") == request_id:
                        if prior["input_hash"] != digest:
                            raise ConflictError("Request ID is already bound to another proposal")
                        return self._public(prior)
            project = self._project(project_id)
            base = self._base_snapshot(project_id, selected)
            spec = project.get("confirmed_spec")
            if "_confirmed_spec" in payload and canonical_hash(payload["_confirmed_spec"]) != canonical_hash(spec):
                raise ConflictError("Candidate confirmation specification is stale")
            if action == "accept_local":
                payload.setdefault("_confirmed_spec", copy.deepcopy(spec))
                if "_candidate_ir" in payload:
                    from plc_ir import canonical_sha256, ir_to_ladder, validate_plc_ir
                    validation_profile = str(payload.get("_validation_profile") or "strict")
                    structural = validation_profile == "generation_structural"
                    validate_plc_ir(
                        payload["_candidate_ir"], confirmed_spec=spec,
                        validate_ladder=not structural,
                    )
                    if structural:
                        from plc_json_validator import validate_ladder_candidate_structure
                        validate_ladder_candidate_structure(
                            ir_to_ladder(payload["_candidate_ir"]),
                            plc_model=str((payload["_candidate_ir"].get("plc") or {}).get("cpu") or "FX3U"),
                            require_catalogued_instructions=False,
                        )
                    candidate_hash = canonical_sha256(payload["_candidate_ir"])
                    if payload.get("candidate_ir_sha256") not in (None, candidate_hash):
                        raise ConflictError("Candidate content hash does not match")
                    payload["candidate_ir_sha256"] = candidate_hash
                    if selected:
                        payload["base_version_id"] = selected
                        if payload.get("base_ir_sha256") not in (None, base["ir_sha256"]):
                            raise ConflictError("Candidate base program is stale")
                        payload["base_ir_sha256"] = base["ir_sha256"]
                    payload["confirmed_spec_hash"] = canonical_sha256(spec) if spec is not None else None
                elif payload.get("target_mode") not in ("st", "fbd"):
                    raise ValueError("Local candidate requires IR or staged ST/FBD artifacts")
                scope_summary = self._candidate_scope(project_id, selected, payload)
                public_summary = {**(public_summary or {}), **scope_summary}
            proposal_id = "proposal_" + uuid.uuid4().hex
            if action == "accept_local" and payload.get("target_mode") in ("st", "fbd") and "_candidate_ir" not in payload:
                payload = self._freeze_staging(proposal_id, payload)
            now = utc_now()
            record = {"id": proposal_id, "action": action, "project_id": project_id,
                      "base_version_id": selected, "status": "pending", "created_at": now,
                      "updated_at": now, "summary": public_payload(public_summary or {}),
                      "result": None, "error_code": None, "request_id": request_id,
                      "input_hash": digest, "active_version_id": project.get("active_version_id"),
                      "confirmed_spec_hash": canonical_hash(spec), "base_snapshot": base,
                      "private_payload": payload, "payload_hash": canonical_hash(payload)}
            self._save(record)
            return self._public(record)

    def _candidate_scope(self, project_id, base_id, payload):
        """Derive constraints and impact before any durable candidate save."""
        from plc_change_scope import enforce_change_scope, validate_scope_baseline
        before = self.store.load_program_ir(project_id, base_id, persist_legacy=False) if base_id else None
        target_mode = payload.get("target_mode") or ("ladder" if "_candidate_ir" in payload else None)
        scope = validate_scope_baseline(payload.get("change_scope"), before, target_mode=target_mode)
        payload["change_scope"] = scope
        summary = {"change_scope": scope}
        if "_candidate_ir" in payload:
            summary["impact"] = enforce_change_scope(before, payload["_candidate_ir"], scope, target_mode=target_mode)
        return summary

    @staticmethod
    def _verify_payload(record):
        if canonical_hash(record["private_payload"]) != record["payload_hash"]:
            raise ConflictError("Proposal content changed after it was created")

    def _check_binding(self, record):
        self._verify_payload(record)
        project = self._project(record["project_id"])
        if project.get("active_version_id") != record["active_version_id"]:
            raise ConflictError("Active version changed; regenerate the proposal")
        if canonical_hash(project.get("confirmed_spec")) != record["confirmed_spec_hash"]:
            raise ConflictError("Confirmed specification changed; regenerate the proposal")
        if self._base_snapshot(record["project_id"], record["base_version_id"]) != record["base_snapshot"]:
            raise ConflictError("Base version changed; regenerate the proposal")
        payload = record["private_payload"]
        if record["action"] == "accept_local":
            self._candidate_scope(record["project_id"], record["base_version_id"], copy.deepcopy(payload))
        if payload.get("target_mode") in ("st", "fbd") and payload.get("artifacts"):
            root = contained(Path(payload["staging_dir"]), self.directory)
            for entry in payload["artifacts"].values():
                path = contained(root / entry["path"], root)
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ConflictError("Staged candidate changed; regenerate the proposal")

    def reject(self, proposal_id):
        with self.lock.thread_lock:
            record = self._load(proposal_id)
            if record["status"] == "rejected":
                return self._public(record)
            if record["status"] != "pending":
                raise ConflictError("Only a pending proposal can be rejected")
            record["status"] = "rejected"
            self._save(record)
            return self._public(record)

    def accept(self, proposal_id, executor=None, *, approved_by="user", approval_mode=None):
        with self.lock.thread_lock:
            self.lock.require_acquired()
            record = self._load(proposal_id)
            if record["status"] in {"accepted", "failed"}:
                return self._public(record)
            if record["status"] != "pending":
                raise ConflictError("This proposal cannot be executed again")
            if record["action"] != "accept_local" and not callable(executor):
                raise ValueError("This action requires an execution coordinator")
            try:
                self._check_binding(record)
            except (ValueError, OSError, KeyError):
                record.update(status="conflict", error_code="proposal_inputs_changed")
                self._save(record)
                raise ConflictError("Proposal inputs changed; regenerate it") from None
            if approved_by not in {"user", "local_autosave", "policy"}:
                raise ValueError("Unknown approval source")
            record["summary"]["approval"] = {"source": approved_by, "mode": approval_mode}
            record["status"] = "executing"
            self._save(record)
            try:
                if record["action"] == "accept_local":
                    result = self._accept_local(record)
                else:
                    result = executor(copy.deepcopy(record["private_payload"]), record["id"])
                if not isinstance(result, dict):
                    raise TypeError("Execution callback must return a completed result object")
                outcome = str(result.get("status") or "")
                if record["action"] == "accept_local" or outcome in {"accepted", "completed", "passed", "imported"}:
                    status, error_code = "accepted", None
                elif outcome in {"error", "interrupted", "rollback_failed"}:
                    status, error_code = "interrupted", "execution_requires_reconciliation"
                else:
                    status, error_code = "failed", "execution_not_successful"
                record.update(status=status, error_code=error_code, result=public_payload(result))
            except Exception:
                # A callback may have performed its side effect before failing.
                # Never retry it automatically or invite another approval click.
                record.update(status="interrupted", error_code="execution_requires_reconciliation")
                self._save(record)
                raise
            self._save(record)
            return self._public(record)

    def _accept_local(self, record):
        from plc_core import PLCCore, accept_candidate_patch

        payload = record["private_payload"]
        project_id, base_id = record["project_id"], record["base_version_id"]
        if "_candidate_ir" in payload and base_id:
            version = accept_candidate_patch(_NoMigrationStore(self.store), payload)
            return {"version_id": version["id"], "status": "accepted", "target_mode": "ladder"}
        version_id = None
        try:
            version_id, output = self.store.prepare_version(project_id)
            if "_candidate_ir" in payload:
                candidate = payload["_candidate_ir"]
                validation_profile = str(payload.get("_validation_profile") or "strict")
                compiled = PLCCore().compile_project(
                    candidate, output, validation_profile=validation_profile
                )
                metadata = self.store._ir_metadata(candidate)
                metadata.update(target_mode="ladder", artifacts=compiled["artifacts"],
                                plc_model=(candidate.get("plc") or {}).get("cpu", "FX3U"),
                                validation_profile=validation_profile)
                metadata["normalization"] = copy.deepcopy(payload.get("normalization"))
                if compiled["artifacts"].get("st_from_ir"):
                    from plc_st_renderer import ST_RENDERER_SCHEMA_VERSION
                    metadata["st_from_ir_sha256"] = hashlib.sha256((output / compiled["artifacts"]["st_from_ir"]).read_bytes()).hexdigest()
                    metadata["st_renderer_schema_version"] = ST_RENDERER_SCHEMA_VERSION
            else:
                root = contained(Path(payload["staging_dir"]), self.directory)
                artifacts = {}
                for name, entry in payload["artifacts"].items():
                    data = contained(root / entry["path"], root).read_bytes()
                    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise ConflictError("Staged candidate changed")
                    destination = contained(output / entry["path"], output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    artifacts[name] = entry["path"]
                allowed = {"summary", "validation", "plc_model", "program_name", "generation_metadata"}
                metadata = {key: copy.deepcopy(value) for key, value in (payload.get("metadata") or {}).items() if key in allowed}
                metadata.update(target_mode=payload["target_mode"], artifacts=artifacts, plc_model=payload.get("plc_model", "FX3U"))
            metadata.update(summary=metadata.get("summary") or record.get("summary", {}).get("summary") or "已校验的程序",
                            parent_version_id=base_id, source_candidate_id=payload.get("candidate_id", record["id"]),
                            lifecycle_status="accepted", confirmed_spec_snapshot=copy.deepcopy(payload.get("_confirmed_spec")))
            from plc_ir import canonical_sha256
            metadata["confirmed_spec_hash"] = canonical_sha256(payload["_confirmed_spec"]) if payload.get("_confirmed_spec") is not None else None
            version = self.store.complete_version(project_id, version_id, metadata, activate=True)
            return {"version_id": version["id"], "status": "accepted", "target_mode": version["target_mode"]}
        except Exception:
            if version_id is not None and self.store.get_version(project_id, version_id) is None:
                self.store.discard_version(project_id, version_id)
            raise
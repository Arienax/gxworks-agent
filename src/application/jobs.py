"""Persistent background jobs with frozen inputs and cooperative cancellation."""

from __future__ import annotations

import copy
import json
import uuid
import threading
import time
import shared.diagnostics as diagnostics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .events import append_event, utc_now
from .job_errors import acceptance_error_details, generation_error_details, workflow_error_code
from .workspace import (ConflictError, atomic_json, bind_workspace_state, canonical_hash, contained,
                        private_state_dir, public_payload, read_json, record_id)


class JobCancelled(BaseException):
    """Control-flow signal for an operator-requested cancellation.

    Cancellation must cross model/provider ``except Exception`` boundaries
    unchanged. Treating it as an ordinary Exception used to make the response
    collector translate an operator cancel into a model-provider failure after
    waiting for the stream to finish.
    """


_ACTIVE = frozenset({"queued", "running", "cancelling"})
_JOB_FIELDS = ("id", "kind", "status", "created_at", "updated_at", "cancel_requested",
               "last_sequence", "result", "error_code", "error_details")


def _check_snapshot(value):
    if isinstance(value, dict):
        forbidden = {"api_key", "apikey", "password", "secret", "access_token", "refresh_token",
                     "credentials", "provider_configuration", "authorization", "credential"}
        if any(str(key).lower().lstrip("_") in forbidden for key in value):
            raise ValueError("Credentials must not be persisted in job snapshots")
        for child in value.values():
            _check_snapshot(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_snapshot(child)


class JobContext:
    def __init__(self, manager, job_id):
        self.manager, self.job_id = manager, job_id

    @property
    def snapshot(self):
        with self.manager._record_lock:
            return copy.deepcopy(self.manager._load(self.job_id)["snapshot"])

    def emit(self, event_type, data=None):
        # Model progress/preview events are emitted while the provider stream is
        # being consumed. Checking here makes those live events the cancellation
        # boundary instead of waiting until generation parsing/validation ends.
        self.checkpoint()
        if event_type == "context_audit" and isinstance(data, dict):
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            included = [item for item in sections if isinstance(item, dict) and item.get("status") == "included"]
            excluded = [item for item in sections if isinstance(item, dict) and item.get("status") == "excluded"]
            retrieval = [item for item in sections if isinstance(item, dict)
                         and str(item.get("section") or "").startswith("manual_chunk:")]
            policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
            messages = data.get("messages") if isinstance(data.get("messages"), list) else []
            diagnostics.emit(
                "context_audit", stage="model_request", policy=policy.get("name"),
                context_request_index=data.get("request_index"),
                message_count=len(messages), message_chars=data.get("message_text_chars"),
                context_section_count=len(sections), included_section_count=len(included),
                excluded_section_count=len(excluded),
                included_section_chars=sum(item.get("chars", 0) for item in included if type(item.get("chars")) is int),
                excluded_section_chars=sum(item.get("chars", 0) for item in excluded if type(item.get("chars")) is int),
                retrieval_section_count=len(retrieval),
                retrieval_context_chars=sum(item.get("chars", 0) for item in retrieval
                                            if item.get("status") == "included" and type(item.get("chars")) is int),
                dropped_sections=data.get("dropped_sections"),
            )
        return self.manager.emit(self.job_id, event_type, data)

    def checkpoint(self):
        with self.manager._record_lock:
            if self.manager._load(self.job_id).get("cancel_requested"):
                raise JobCancelled()


class JobManager:
    def __init__(self, state_dir, writer_lock, max_workers=2):
        self.lock = writer_lock
        self._record_lock = threading.RLock()
        self.lock.require_acquired()
        self.state_dir = bind_workspace_state(state_dir, self.lock.workspace)
        self.directory = contained(self.state_dir / "jobs", self.state_dir)
        self.directory.mkdir(exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="plc-job")
        self._futures = {}
        self._closed = False
        with self._record_lock:
            for path in self.directory.glob("*.json"):
                record = read_json(contained(path, self.directory))
                if record.get("status") in _ACTIVE:
                    record.update(status="interrupted", error_code="process_interrupted", error_details=None)
                    append_event(record, "interrupted", {"error_code": "process_interrupted"})
                    self._save(record)

    def _load(self, job_id):
        path = contained(self.directory / (record_id(job_id, "job") + ".json"), self.directory)
        if not path.is_file():
            raise KeyError(job_id)
        record = read_json(path)
        if record.get("id") != job_id:
            raise ValueError("Invalid job record")
        return record

    def _save(self, record):
        self.lock.require_acquired()
        atomic_json(contained(self.directory / (record_id(record["id"]) + ".json"), self.directory), record)

    @staticmethod
    def _public(record):
        snapshot = record.get("snapshot", {})
        return public_payload({**{key: record.get(key) for key in _JOB_FIELDS},
                               "project_id": snapshot.get("project_id"),
                               "version_id": snapshot.get("version_id") or snapshot.get("base_version_id")})

    def get(self, job_id):
        with self._record_lock:
            return self._public(self._load(job_id))

    def list(self, project_id=None):
        with self._record_lock:
            records = [self._load(path.stem) for path in self.directory.glob("*.json")]
            if project_id is not None:
                records = [r for r in records if r["snapshot"].get("project_id") == project_id]
            return [self._public(r) for r in sorted(records, key=lambda r: r["created_at"], reverse=True)]

    def events(self, job_id, after_sequence=0):
        if not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("Event sequence must be a nonnegative integer")
        with self._record_lock:
            return copy.deepcopy([e for e in self._load(job_id)["events"] if e["sequence"] > after_sequence])

    def submit(self, kind, snapshot, worker, request_id=None):
        if not isinstance(snapshot, dict):
            raise ValueError("Job snapshot must be an object")
        if not callable(worker):
            raise TypeError("Job worker must be callable")
        if request_id is not None:
            record_id(request_id, "request")
        frozen = copy.deepcopy(snapshot)
        _check_snapshot(frozen)
        digest = canonical_hash({"kind": kind, "snapshot": frozen})
        with self._record_lock:
            self.lock.require_acquired()
            if self._closed:
                raise RuntimeError("Job manager is closed")
            if request_id is not None:
                for path in self.directory.glob("*.json"):
                    previous = self._load(path.stem)
                    if previous.get("request_id") == request_id:
                        if previous["input_hash"] != digest:
                            raise ConflictError("Request ID is already bound to different inputs")
                        return self._public(previous)
            now = utc_now()
            record = {"id": "job_" + uuid.uuid4().hex, "kind": str(kind), "status": "queued",
                      "created_at": now, "updated_at": now, "snapshot": frozen,
                      "input_hash": digest, "request_id": request_id, "cancel_requested": False,
                      "last_sequence": 0, "events": [], "result": None, "error_code": None, "error_details": None}
            append_event(record, "queued")
            self._save(record)
            diagnostics.record_operator_action(
                self.state_dir, "job_submit", project_id=frozen.get("project_id"),
                job_id=record["id"], payload={"kind": kind, "request_id": request_id, "snapshot": frozen},
                result={"status": "queued"},
            )
            self._futures[record["id"]] = self._executor.submit(self._run, record["id"], worker)
            return self._public(record)

    def emit(self, job_id, event_type, data=None):
        with self._record_lock:
            record = self._load(job_id)
            if record["status"] not in _ACTIVE:
                raise ConflictError("Cannot append events to a finished job")
            event = append_event(record, event_type, data)
            self._save(record)
            return copy.deepcopy(event)

    def _preserve_rejected_generation(self, job_id, error):
        """Turn a rejected ladder into a visible/exportable diagnostic version.

        Validation still remains failed on the version metadata. The distinction is
        that a bad candidate is no longer discarded: if enough model-authored
        structure can be recovered, we render SVG and GX CSV, store an internally
        consistent IR for navigation, activate the diagnostic version, and let the
        existing GX-import path send those CSVs to GX Works2 for native diagnostics.
        """
        from plc.candidate_repair import GenerationValidationError

        if not isinstance(error, GenerationValidationError):
            return None
        with self._record_lock:
            record = self._load(job_id)
            if record.get("kind") != "generation":
                return None
            snapshot = copy.deepcopy(record.get("snapshot") or {})

        staging = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir)
        candidate_path = contained(staging / "repair_candidate.json", staging)
        if not candidate_path.is_file():
            return None
        candidate_text = candidate_path.read_text(encoding="utf-8")
        if not candidate_text.strip() or len(candidate_text) > 512000:
            return None

        project_snapshot = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
        program_snapshot = snapshot.get("program_ir") if isinstance(snapshot.get("program_ir"), dict) else {}
        project_id = snapshot.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            return None
        plc_model = str(project_snapshot.get("plc_model") or "FX3U")
        program_name = str(program_snapshot.get("program_name") or "MAIN")
        revision = int(program_snapshot.get("revision") or 0) + 1
        confirmed_spec = copy.deepcopy(project_snapshot.get("confirmed_spec"))

        try:
            from application.rejected_generation_preview import materialize_rejected_preview

            metadata = materialize_rejected_preview(
                candidate_text,
                staging,
                confirmed_spec=confirmed_spec,
                plc_model=plc_model,
                program_name=program_name,
                revision=revision,
            )
        except Exception as salvage_error:
            diagnostics.exception_record(salvage_error)
            return None

        handoff_path = contained(staging / "generation_handoff.json", staging)
        generation_handoff = None
        if handoff_path.is_file():
            try:
                saved_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                if isinstance(saved_handoff, dict):
                    generation_handoff = saved_handoff
            except (OSError, ValueError):
                pass  # Missing diagnostic metadata must not discard the candidate.
        if generation_handoff is None:
            from application.confirmed_generation_context import project_confirmed_specification
            from plc.specification.provenance import handoff_snapshot
            generation_handoff = handoff_snapshot(
                project_confirmed_specification(confirmed_spec), stage="generate",
                evidence={"stage": "generate", "status": "not_recorded", "records": []},
            )
        generation_handoff["confirmed_spec_sha256"] = (
            canonical_hash(confirmed_spec) if confirmed_spec is not None else None)
        validation = metadata.setdefault("validation", {})
        validation["violations"] = copy.deepcopy(error.diagnostics.get("violations", []))
        validation["diagnostic_id"] = error.diagnostics.get("diagnostic_id")
        validation["stop_reason"] = error.diagnostics.get("stop_reason")
        validation["original_failure_stage"] = error.diagnostics.get("stage")

        version_id = None
        try:
            from storage.session import SessionStore

            with self.lock.thread_lock:
                self.lock.require_acquired()
                store = SessionStore(base_dir=self.lock.workspace, create=False)
                current = store.get_project(project_id)
                if not isinstance(current, dict):
                    return None
                frozen = project_snapshot
                if any(current.get(key) != frozen.get(key)
                       for key in ("active_version_id", "confirmed_spec", "target_mode", "plc_model")):
                    return None
                base_version_id = snapshot.get("version_id")
                if base_version_id:
                    current_base = store.get_version(project_id, base_version_id)
                    if current_base != snapshot.get("version"):
                        return None

                version_id, output_dir = store.prepare_version(project_id)
                copied_artifacts = {}
                for name, filename in (metadata.get("artifacts") or {}).items():
                    relative = Path(str(filename))
                    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                            or ":" in str(relative)):
                        raise ValueError("Invalid diagnostic artifact path")
                    source = contained(staging / relative, staging)
                    if not source.is_file():
                        raise ValueError("Diagnostic artifact is missing")
                    destination = contained(output_dir / relative, output_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read_bytes())
                    copied_artifacts[str(name)] = str(relative).replace("\\", "/")

                required = {"json", "ir", "svg", "program_csv", "comment_csv"}
                if not required.issubset(copied_artifacts):
                    raise ValueError("Diagnostic generation did not produce the full GX artifact set")
                program = json.loads((output_dir / copied_artifacts["ir"]).read_text(encoding="utf-8"))
                version_metadata = store._ir_metadata(program)
                version_metadata.update(
                    target_mode="ladder",
                    plc_model=plc_model,
                    program_name=program_name,
                    revision=revision,
                    artifacts=copied_artifacts,
                    validation_profile="generation_structural",
                    validation=copy.deepcopy(validation),
                    summary="模型候选存在校验错误；已保留梯形图与 GX CSV",
                    lifecycle_status="diagnostic",
                    parent_version_id=base_version_id,
                    confirmed_spec_snapshot=copy.deepcopy(confirmed_spec),
                    generation_handoff=copy.deepcopy(generation_handoff),
                    confirmed_spec_hash=(canonical_hash(confirmed_spec) if confirmed_spec is not None else None),
                    generation_metadata={
                        "diagnostic_only": True,
                        "recovery": copy.deepcopy(metadata.get("recovery") or {}),
                    },
                )
                version = store.complete_version(project_id, version_id, version_metadata, activate=True)
        except Exception as save_error:
            diagnostics.exception_record(save_error)
            if version_id is not None:
                try:
                    from storage.session import SessionStore
                    with self.lock.thread_lock:
                        store = SessionStore(base_dir=self.lock.workspace, create=False)
                        if store.get_version(project_id, version_id) is None:
                            store.discard_version(project_id, version_id)
                except Exception:
                    pass
            return None

        output = {
            "generation": metadata,
            "version_id": version["id"],
            "status": "saved_invalid",
        }
        atomic_json(self.state_dir / "outputs" / (record_id(job_id) + ".json"), output)
        diagnostics.emit(
            "invalid_candidate_preserved",
            stage="generation_delivery",
            version_id=version["id"],
            validation_status="invalid_candidate",
            gx_csv_available=True,
        )
        return {"version_id": version["id"], "status": "saved_invalid"}

    def _run(self, job_id, worker):
        with self._record_lock:
            initial = self._load(job_id)
            snapshot = initial.get("snapshot", {})
        with diagnostics.diagnostic_scope(self.state_dir, job_id,
                kind=initial.get("kind"), project_id=snapshot.get("project_id"),
                version_id=snapshot.get("version_id"),
                policy=(snapshot.get("context_policy") or {}).get("name")) as capture:
            try:
                return self._run_recorded(job_id, worker)
            except BaseException as error:
                diagnostics.exception_record(error)
                raise
            finally:
                with self._record_lock:
                    outcome = self._load(job_id).get("status")
                diagnostics.emit("job_finished", stage="workflow", status=outcome,
                    elapsed_ms=int((time.monotonic() - capture.started) * 1000))

    def _run_recorded(self, job_id, worker):
        error_details = None
        try:
            with self._record_lock:
                record = self._load(job_id)
                if record.get("cancel_requested"):
                    raise JobCancelled()
                record["status"] = "running"
                append_event(record, "running")
                self._save(record)
            result = worker(JobContext(self, job_id))
            status, error_code = "completed", None
        except JobCancelled:
            status, error_code, result = "cancelled", None, None
        except Exception as exc:
            diagnostics.exception_record(exc)
            preserved = self._preserve_rejected_generation(job_id, exc)
            if preserved is not None:
                status, error_code, error_details, result = "completed", None, None, preserved
            else:
                safe_codes = {"ResponseRejectedError": "response_rejected", "ConflictError": "input_conflict",
                              "ContextUnavailableError": "context_unavailable", "ChangeScopeError": "change_scope_violation"}
                status, error_code, result = "failed", safe_codes.get(type(exc).__name__, "job_failed"), None
                error_details = acceptance_error_details(exc)
                if error_details is not None:
                    error_code = "response_rejected"
                else:
                    error_details = generation_error_details(exc)
                    if error_details is not None:
                        error_code = "generation_validation_failed"
                    else:
                        error_code = workflow_error_code(exc) or error_code
        with self._record_lock:
            record = self._load(job_id)
            record.update(status=status, error_code=error_code, error_details=error_details, result=public_payload(result))
            append_event(record, status, {"result": record["result"], "error_code": error_code, "error_details": error_details})
            self._save(record)

    def cancel(self, job_id):
        with self._record_lock:
            record = self._load(job_id)
            if record["status"] in _ACTIVE and not record.get("cancel_requested"):
                record.update(cancel_requested=True, status="cancelling")
                append_event(record, "cancelling")
                self._save(record)
            snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), dict) else {}
            diagnostics.record_operator_action(
                self.state_dir, "job_cancel", project_id=snapshot.get("project_id"),
                job_id=job_id, result={"status": record.get("status"),
                                       "cancel_requested": bool(record.get("cancel_requested"))},
            )
            return self._public(record)

    def shutdown(self, wait=True):
        with self._record_lock:
            self._closed = True
        self._executor.shutdown(wait=wait)
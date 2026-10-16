"""Isolated native snapshot checks and separate operator-reported evidence.

NativeValidationService archives manual evidence only; operator assertions never
upgrade a version's automatic compilation status.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
from datetime import datetime, timezone

from .fbd import decode_upload, graph_diff
from .projects import public
from .workspace import ConflictError, atomic_json, canonical_hash, contained, read_json, record_id


def validate_native_snapshot(raw, *, project_id, version_id, program=None, expected_cpu=None, target_mode=None, st_text=None, program_ir=None, sfc_graph=None, inspection=None):
    """Check saved bytes without changing workspace versions or the GX UI.

    The requested POU identifies the caller's context. Core's selection and
    target fields describe what the compiler and checker actually used; an
    uncalled FB does not become a checked task merely because it was selected.
    """
    from gxw.native_diagnostics import native_validation_source_plan
    from gxworks2.workspace_adapter import NativeWorkspaceValidation
    from .execution import DesktopResourceLock, ExecutionUnavailableError

    raw = bytes(raw)
    source = native_validation_source_plan(raw, include_logical_names=True)
    names = [row['logical_name'] for row in source['programs']]
    if program is None and len(names) == 1:
        program = names[0]
    if program not in names:
        matches = [row['logical_name'] for row in source['programs'] if row['name']==program]
        if len(matches)==1:
            program = matches[0]
    if program not in names:
        raise ValueError('原生检查需要指定当前 GXW 中的 POU；所选程序缺失或未指定。')
    if expected_cpu is not None:
        from gxworks2.project_profiles import native_project_profile
        actual, expected = native_project_profile(source['cpu']), native_project_profile(expected_cpu)
        if actual is None or expected is None or actual.cpu != expected.cpu:
            raise ValueError('GXW CPU 与当前版本不一致，未启动原生检查。')
    selected = next(row for row in source['programs'] if row['logical_name']==program)
    candidate_binding = None
    if target_mode == 'ladder':
        from gxw.native_diagnostics import bind_native_ladder_candidate
        if selected['program_kind'] != 1:
            raise ValueError('GXW 所选源码不是当前简单 Ladder 版本。')
        try:
            candidate_binding = bind_native_ladder_candidate(raw, program_ir, program)
        except ValueError as error:
            raise ValueError('Ladder 快照未绑定当前候选，未启动原生检查：' + str(error)) from error
    if target_mode == 'st':
        if selected['program_kind'] != 193:
            raise ValueError('GXW 所选源码不是当前 ST 版本。')
        from gxw.native_diagnostics import _native_validation_sources
        from gxw.text_pou import parse_st_pou
        original = next(row for row in _native_validation_sources(raw)[1] if row['logical_name']==program)
        saved_text = parse_st_pou(original['raw']).text
        if st_text is None or saved_text != st_text:
            raise ValueError('GXW ST 源码与当前版本不一致，未启动原生检查。')
    if target_mode == 'sfc' and selected['program_kind'] != 240:
        raise ValueError('GXW 所选源码不是当前 SFC 版本。')
    if inspection is not None:
        from gxw.native_diagnostics import native_inspection_queries
        inspection = native_inspection_queries(raw, inspection)
    try:
        with DesktopResourceLock():
            options = {'inspection': inspection} if inspection is not None else {}
            result = NativeWorkspaceValidation().validate(raw, timeout=None, **options)
    except ExecutionUnavailableError as error:
        raise ConflictError('GX 原生资源正被其他操作占用，本次未启动检查。') from error
    if target_mode == 'sfc' and sfc_graph is not None:
        from gxw.native_diagnostics import project_native_sfc_graph_diagnostics
        result = project_native_sfc_graph_diagnostics(raw, result, sfc_graph)
    return public({**result, 'mode': 'native', 'snapshot': {
        'project_id': project_id, 'version_id': version_id,
        'requested_program': program, 'cpu': source['cpu'],
        **({'candidate_binding': candidate_binding} if candidate_binding is not None else {}),
    }})


class NativeValidationService:
    def __init__(self, workbench):
        self.workbench = workbench

    def _source(self, project_id, version_id):
        wb = self.workbench
        version = wb.projects.raw_version(project_id, version_id)
        if version.get("target_mode") not in {"fbd", "ladder", "st", "sfc"}:
            raise ValueError("Native evidence requires a saved program version with a GXW snapshot")
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
                version = wb.projects.raw_version(project_id, version_id)
                program_name = (wb.projects.program(project_id, version_id)['program'] if version.get('target_mode')=='fbd'
                                else version.get('program_name'))
                if wb.projects.raw_version(project_id, version_id).get('target_mode') == 'fbd':
                    same_program = not graph_diff(read_snapshot(source, program_name)['model'],
                                                  read_snapshot(raw, program_name)['model'])['has_changes']
                else:
                    from gxw.native_diagnostics import native_validation_source_plan
                    original = native_validation_source_plan(source, include_logical_names=True)
                    uploaded_plan = native_validation_source_plan(raw, include_logical_names=True)
                    before = [p for p in original['programs'] if program_name in (p['logical_name'],p['name'])]
                    after = [p for p in uploaded_plan['programs'] if program_name in (p['logical_name'],p['name'])]
                    same_program = original['cpu']==uploaded_plan['cpu'] and len(before)==len(after)==1 and before==after
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

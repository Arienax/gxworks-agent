# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Ephemeral, source-bound program analysis and simulator observation.

No project records, active versions, approvals, native saves or PLC writes are
created here. Sources/targets are opaque references scoped to one host session.
"""
from __future__ import annotations

import copy
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.tool_errors import ToolPreconditionError


def _now():
    return datetime.now(timezone.utc).isoformat()


def _session_identity(session):
    return tuple(getattr(session, name) for name in
                 ("process_id", "window_handle", "process_started", "executable", "software_version"))


_CSV_DOCUMENT_LIMIT = 30 * 1024 * 1024


def _csv_document(data_base64, path, label):
    """Accept exactly one of in-memory content or one local path per CSV."""
    if (data_base64 is None) == (path is None):
        raise ToolPreconditionError("INVALID_ARGUMENTS",
            f"必须且只能提供{label}的内容或本地路径之一。")
    if data_base64 is not None:
        import base64
        import binascii
        if (not isinstance(data_base64, str)
                or len(data_base64) > 4 * ((_CSV_DOCUMENT_LIMIT + 2) // 3)):
            raise ToolPreconditionError("INVALID_ARGUMENTS", f"{label}内容必须为 1 字节到 30 MiB。")
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ToolPreconditionError("INVALID_ARGUMENTS",
                f"{label}内容不是有效的 base64：{error}"[:500]) from error
        name = label + ".csv"
    else:
        source = Path(str(path)).expanduser()
        if source.suffix.casefold() != ".csv" or not source.is_file():
            raise ToolPreconditionError("INVALID_ARGUMENTS", f"{label}必须是已存在的 .csv 文件。")
        data = source.read_bytes()
        name = source.name
    if not 0 < len(data) <= _CSV_DOCUMENT_LIMIT:
        raise ToolPreconditionError("INVALID_ARGUMENTS", f"{label}内容必须为 1 字节到 30 MiB。")
    return data, name


def _csv_source_context(build_tool_context, source, parsed, inspection):
    """The one analysis context used by CSV snapshots (native or supplied)."""
    return build_tool_context({"plc_model": source["plc_model"], "target_mode": "ladder"},
        ladder=parsed["ladder"], program_ir=parsed["program_ir"], inspection=inspection,
        analysis_source={**source, "raw_networks": parsed["raw_networks"],
                         "device_comments": parsed["device_comments"]})


class NativeReadBackend:
    """Reuse the existing bounded native CSV reader with an exact target."""
    def __init__(self):
        from gxworks2.finder import GXWorks2Finder
        self.finder = GXWorks2Finder()

    def sessions(self):
        return self.finder.list_running()

    def identity(self, session, program="MAIN"):
        from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
        return PywinautoGXWorks2UIAutomation(program=program).inspect_read_identity(session)

    def snapshot(self, target, identity, program):
        from application.execution import DesktopResourceLock
        from gxworks2.csv_manager import CSVManager
        from gxworks2.sync_service import GXWorks2SyncService
        from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
        automation = PywinautoGXWorks2UIAutomation(program=program)

        def observe(session):
            current = self.finder.find_running(process_id=target.process_id)
            if (current is None or _session_identity(current) != _session_identity(target)
                    or automation.inspect_read_identity(current) != identity):
                raise ToolPreconditionError("STALE_NATIVE_SESSION", "原生工程或目标会话在读取时改变。",
                    next_actions=({"tool": "list_gxworks2_sessions"},))
        with DesktopResourceLock(), tempfile.TemporaryDirectory(prefix="gxworks-analysis-") as directory:
            sync = GXWorks2SyncService(finder=self.finder, automation=automation,
                csv_manager=CSVManager(), backup_root=Path(directory))
            result = sync.read_current_snapshot(target_session=target, identity_observer=observe,
                save_project=False, import_context={"program_name": program})
            if not result.success:
                precondition = result.details.get("precondition") or {}
                raise ToolPreconditionError(precondition.get("code") or "NATIVE_READ_FAILED", result.message,
                    next_actions=({"tool": "list_gxworks2_sessions"},),
                    details={"native_error_code": result.error_code.value, "stage": result.stage})
            observe(target)
            return {"program_csv": Path(result.exported_program_path).read_bytes(),
                    "comment_csv": Path(result.exported_comment_path).read_bytes()}


class ProgramInspectionService:
    def __init__(self, *, native=None, gateway=None, clock=time.monotonic, ttl=900):
        self._native = native
        self._gateway = gateway
        self._clock, self.ttl = clock, ttl
        self._sessions, self._sources, self._targets = {}, {}, {}

    @property
    def native(self):
        if self._native is None:
            self._native = NativeReadBackend()
        return self._native

    @property
    def gateway(self):
        if self._gateway is None:
            from simulator.gateway import GXSimulatorGatewayClient
            self._gateway = GXSimulatorGatewayClient()
        return self._gateway

    def list_gxworks2_sessions(self):
        candidates = []
        current = self.native.sessions()
        for session in current:
            existing = next((key for key, item in self._sessions.items()
                             if _session_identity(item["session"]) == _session_identity(session)
                             and item["session"].project_name == session.project_name), None)
            ref = existing or uuid.uuid4().hex
            identity, reason = None, None
            try:
                if (not session.process_started or Path(session.executable).name.lower() != "gd2.exe"
                        or not session.software_version):
                    raise RuntimeError("未确认原生进程身份、路径或软件版本。")
                identity = self.native.identity(session)
            except Exception as error:
                reason = str(error)[:500]
            self._sessions[ref] = {"session": session, "identity": identity, "created": self._clock()}
            candidates.append({"session_id": ref, "process_id": session.process_id,
                "window_handle": session.window_handle, "project_hint": session.project_name,
                "software_version": session.software_version or None,
                "native_identity": identity, "readable": identity is not None,
                "readability_reason": reason})
        return {"sessions": candidates, "selection_status": "none" if not candidates else
                "ambiguous" if len(candidates) > 1 else "candidate", "selected_session_id": None,
                "next_actions": [{"tool": "read_gxworks2_snapshot", "required_arguments": ["session_id"]}]
                                 if candidates else []}

    def _session(self, session_id, program):
        entry = self._sessions.get(session_id)
        if entry is None or self._clock() - entry["created"] > self.ttl:
            raise ToolPreconditionError("STALE_NATIVE_SESSION", "原生会话引用不存在或已过期。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        original = entry["session"]
        if (not original.process_started or Path(original.executable).name.lower() != "gd2.exe"
                or not original.software_version):
            raise ToolPreconditionError("NATIVE_IDENTITY_UNAVAILABLE", "未确认原生进程路径、启动身份或软件版本。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        matches = [item for item in self.native.sessions() if _session_identity(item) == _session_identity(original)]
        if len(matches) != 1:
            raise ToolPreconditionError("STALE_NATIVE_SESSION", "原生进程或窗口已被替换。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        identity = self.native.identity(matches[0], program)
        if identity.get("project_name") != original.project_name:
            raise ToolPreconditionError("STALE_NATIVE_SESSION", "所选原生窗口已切换工程。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        if not identity.get("program_source_confirmed") or not identity.get("cpu_menu"):
            raise ToolPreconditionError("NATIVE_IDENTITY_UNAVAILABLE", "未确认原生程序源和 CPU。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        return matches[0], identity

    def _csv_analysis(self, program_bytes, comment_bytes, *, plc_model, program_name):
        """One CSV parse path for native exports and for supplied CSV files.

        Both entries decode the same two documents through
        :func:`gxworks2.csv_importer.inspect_gxworks2_csv`, so a native export
        and a hand-supplied export cannot diverge in what they report.
        """
        from gxworks2.csv_importer import GXCSVImportError, inspect_gxworks2_csv
        with tempfile.TemporaryDirectory(prefix="gxworks-analysis-parse-") as directory:
            main, comments = Path(directory) / "program.csv", Path(directory) / "comments.csv"
            main.write_bytes(program_bytes)
            comments.write_bytes(comment_bytes)
            try:
                return inspect_gxworks2_csv(main, comments, plc_model=plc_model,
                                            program_name=program_name)
            except GXCSVImportError as error:
                raise ToolPreconditionError("CSV_SOURCE_INVALID", str(error)[:500],
                    next_actions=({"tool": "inspect_gxworks2_csv_files"},)) from error

    def read_gxworks2_snapshot(self, session_id, program="MAIN"):
        from gxworks2.project_profiles import native_project_profile
        from plc.target_support import normalize_target_cpu
        target, identity = self._session(session_id, program)
        payload = self.native.snapshot(target, identity, program)
        after, after_identity = self._session(session_id, program)
        if identity != after_identity:
            raise ToolPreconditionError("STALE_NATIVE_SESSION", "工程在导出期间改变；快照未接受。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        model = normalize_target_cpu(identity["cpu_menu"])
        parsed = self._csv_analysis(payload["program_csv"], payload["comment_csv"],
                                    plc_model=model, program_name=program)
        profile = native_project_profile(identity["cpu_menu"])
        if identity["program_name"].casefold() != program.casefold() or not profile.accepts_plc_info(parsed["plc_info"]):
            raise ToolPreconditionError("NATIVE_SOURCE_MISMATCH", "导出程序名或 CPU 与原生目标不一致。",
                next_actions=({"tool": "list_gxworks2_sessions"},), details={
                    "expected_program": program, "observed_program": parsed["program_name"],
                    "expected_cpu_menu": identity["cpu_menu"], "observed_plc_info": parsed["plc_info"]})
        # Re-reading this target supersedes its older source references.
        self._sources = {key: value for key, value in self._sources.items()
                         if value["source"].get("session_id") != session_id
                         and self._clock() - value["created"] <= self.ttl}
        source_ref = uuid.uuid4().hex
        source = {"source_ref": source_ref, "session_id": session_id, "kind": "native_ladder_snapshot",
                  "cpu_menu": identity["cpu_menu"], "plc_model": model, "program_name": program,
                  "software_version": target.software_version or None,
                  "export_title": parsed["export_title"],
                  "read_at": _now(), "expires_in_seconds": self.ttl,
                  "origin": identity, "coverage": parsed["coverage"],
                  "step_width": parsed.get("step_width"),
                  "editable": False, "saved_version_id": None, "freshness": "immutable_snapshot",
                  "runtime_verified": False}
        self._sources[source_ref] = {"source": source, "parsed": parsed, "identity": identity,
                                    "created": self._clock()}
        return {**copy.deepcopy(source), "next_actions": [
            {"tool": "get_current_program_info", "arguments": {"source_ref": source_ref}},
            {"tool": "list_simulator_targets"}]}

    def inspect_gxworks2_csv_files(self, *, plc_model, program_name=None,
                                   program_data_base64=None, program_path=None,
                                   comment_data_base64=None, comment_path=None):
        """Read-only analysis of two exported GX Works2 CSV files.

        The program CSV and the device-comment CSV are the whole source here: no
        GX Works2 process, no selected session and no Agent project are needed.
        The returned ``source_ref`` uses the same ephemeral mechanism as native
        and GXW snapshots, so ``get_current_program_info``, ``read_network``,
        ``get_diagnostics`` and ``validate_current_program`` work unchanged.
        """
        import hashlib
        from plc.target_support import normalize_target_cpu
        if not str(plc_model or "").strip():
            raise ToolPreconditionError("INVALID_ARGUMENTS",
                "必须显式提供 plc_model；CSV 分析不按默认型号猜测。")
        try:
            model = normalize_target_cpu(str(plc_model).strip().upper())
        except ValueError as error:
            raise ToolPreconditionError("INVALID_ARGUMENTS", str(error)) from error
        program_bytes, program_file = _csv_document(
            program_data_base64, program_path, "程序CSV")
        comment_bytes, comment_file = _csv_document(
            comment_data_base64, comment_path, "软元件注释CSV")
        parsed = self._csv_analysis(program_bytes, comment_bytes, plc_model=model,
                                    program_name=program_name)
        ref = uuid.uuid4().hex
        source = {"source_ref": ref, "session_id": None, "kind": "csv_file_snapshot",
                  "cpu_menu": model, "plc_model": model,
                  "program_name": parsed["program_name"], "software_version": None,
                  "export_title": parsed["export_title"], "read_at": _now(),
                  "expires_in_seconds": self.ttl,
                  "origin": {"program_file": program_file, "comment_file": comment_file,
                             "program_sha256": hashlib.sha256(program_bytes).hexdigest(),
                             "comment_sha256": hashlib.sha256(comment_bytes).hexdigest()},
                  "coverage": parsed["coverage"], "editable": False,
                  "step_width": parsed.get("step_width"),
                  "saved_version_id": None, "freshness": "immutable_snapshot",
                  "runtime_verified": False}
        # Re-reading a CSV export supersedes nothing else; expired references go.
        self._sources = {key: value for key, value in self._sources.items()
                         if self._clock() - value["created"] <= self.ttl}
        self._sources[ref] = {"source": source, "parsed": parsed, "created": self._clock()}
        return {**copy.deepcopy(source), "plc_info": parsed["plc_info"],
                "comment_count": len(parsed["device_comments"]),
                "next_actions": [
                    {"tool": "get_current_program_info", "arguments": {"source_ref": ref}},
                    {"tool": "read_network",
                     "arguments": {"source_ref": ref, "network_id": "N0001"}},
                    {"tool": "get_diagnostics", "arguments": {"source_ref": ref}}]}

    def source_context(self, source_ref):
        from agent_runtime.plc_tools import build_tool_context
        entry = self._sources.get(source_ref)
        if entry is None or self._clock() - entry["created"] > self.ttl:
            raise ToolPreconditionError("STALE_SOURCE_REF", "分析快照不存在、已被新快照替换或已过期。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        source, parsed = entry["source"], entry["parsed"]
        if source['kind'] == 'csv_file_snapshot':
            # Two supplied CSV files need no native process to stay readable.
            return _csv_source_context(build_tool_context, source, parsed, self)
        if source['kind'] == 'gxw_file_snapshot':
            return build_tool_context({'plc_model': source['plc_model'], 'target_mode': source['target_mode']},
                program_ir=parsed.get('program_ir'), native_snapshot=entry['raw'],
                fbd_baseline=entry['raw'] if source['target_mode'] == 'fbd' else None,
                st_text=parsed.get('st_text'), sfc_graph=parsed.get('sfc_graph'), inspection=self,
                analysis_source={**source, 'raw_networks': parsed.get('raw_networks', []),
                                 'device_comments': parsed.get('device_comments', {}),
                                 'source_model': parsed.get('source_model')})
        try:
            _, identity = self._session(source["session_id"], source["program_name"])
        except ToolPreconditionError as error:
            raise ToolPreconditionError("STALE_SOURCE_REF", str(error), next_actions=error.next_actions) from error
        if identity != entry["identity"]:
            raise ToolPreconditionError("STALE_SOURCE_REF", "原生工程身份已改变。",
                next_actions=({"tool": "list_gxworks2_sessions"},))
        return _csv_source_context(build_tool_context, source, parsed, self)

    def inspect_gxw_project(self, *, data_base64=None, path=None, program=None):
        from application.gxw_files import read_file_input
        from gxw.models import GXWFormatError
        from gxw.offline_project import read_offline_project, inspect_offline_project, read_ordered_program
        raw = read_file_input(data_base64=data_base64, path=path)
        project = read_offline_project(raw, allow_unverified=True)
        public = inspect_offline_project(raw, program)
        selected = public['selected_source']
        if selected is None or selected['language'] is None or not selected['readable']:
            return {**public, 'source_ref': None, 'next_actions': [
                {'tool': 'inspect_gxw_project', 'required_arguments': ['program']}]}
        parsed = {'device_comments': public['device_comments']}
        try:
            if selected['language'] == 'ladder':
                parsed.update(read_ordered_program(project, selected['source_name']))
            elif selected['language'] == 'st':
                from gxw.text_pou import parse_st_pou
                parsed['st_text'] = parse_st_pou(project.streams[selected['source_name']]).text
                if parsed['st_text'] is None:
                    raise GXWFormatError('ST body encoding is not covered')
            elif selected['language'] == 'sfc':
                from gxw.sfc_pou import read_sfc_project_context
                parsed['source_model'] = read_sfc_project_context(raw, selected['source_name']).object_model()
            elif selected['language'] == 'fbd':
                from gxw.object_model import read_project_context
                parsed['source_model'] = read_project_context(raw, selected['source_name']).object_model()
        except GXWFormatError as error:
            selected.update(readable=False, offline_editable=False, reason=str(error))
            public['programs'] = [selected if row['source_name'] == selected['source_name'] else row
                                  for row in public['programs']]
            public['issues'].append({'code': 'uncovered_source_format',
                'source_name': selected['source_name'], 'message': str(error)})
            return {**public, 'source_ref': None, 'next_actions': []}
        ref = uuid.uuid4().hex
        source = {'source_ref': ref, 'kind': 'gxw_file_snapshot', 'cpu_menu': project.context['cpu'],
            'plc_model': project.context['cpu'], 'program_name': selected['source_name'],
            'target_mode': selected['language'], 'programs': public['programs'],
            'read_at': _now(), 'expires_in_seconds': self.ttl, 'freshness': 'immutable_snapshot',
            'coverage': parsed.get('coverage', {'parse_status': 'complete', 'network_count': 0,
                'parsed_network_count': 0, 'unknown_instructions': [], 'semantic_status': 'static_only',
                'runtime_verified': False}), 'editable': False, 'runtime_verified': False}
        self._sources = {key: value for key, value in self._sources.items()
                         if self._clock() - value['created'] <= self.ttl}
        self._sources[ref] = {'source': source, 'parsed': parsed, 'raw': raw, 'created': self._clock()}
        return {**public, **source, 'next_actions': [
            {'tool': 'get_current_program_info', 'arguments': {'source_ref': ref}},
            {'tool': 'import_gxw_project', 'required_arguments': ['data_base64_or_path', 'program']}]}

    def list_simulator_targets(self):
        from simulator.gateway import GatewayError
        try:
            health = self.gateway.health()
        except GatewayError as error:
            return {"targets": [], "available": False, "reason": str(error),
                    "next_actions": [], "runtime_verified": False}
        if (health.get("simulator_only") is not True or health.get("route") != "GX Simulator2"
                or health.get("unit_type") != 0x30):
            raise ToolPreconditionError("PERMISSION_DENIED", "网关未确认 Simulator2 专用路由。")
        if not health.get("target_binding") or not health.get("connected"):
            return {"targets": [], "available": False,
                    "reason": "模拟器网关尚未连接，或不支持明确的只读目标绑定。",
                    "runtime_verified": False, "next_actions": []}
        ref = uuid.uuid4().hex
        self._targets[ref] = {"binding": health["target_binding"], "created": self._clock()}
        return {"available": True, "targets": [{"target_ref": ref,
                "route": "GX Simulator2", "plc_model": "FX3U", "connected": True,
                "program_correspondence": "unverified", "physical_plc": False}],
                "next_actions": [{"tool": "get_simulator_status", "arguments": {"target_ref": ref}}]}

    def _target(self, target_ref):
        entry = self._targets.get(target_ref)
        if entry is None or self._clock() - entry["created"] > self.ttl:
            raise ToolPreconditionError("STALE_SIMULATOR_TARGET", "模拟器目标引用不存在或已过期。",
                next_actions=({"tool": "list_simulator_targets"},))
        return entry["binding"]

    def read_simulator_devices(self, target_ref, addresses):
        from plc.device_policy import DevicePolicyError, native_read_plan
        from simulator.gateway import GatewayError
        binding = self._target(target_ref)
        try:
            plan = native_read_plan(addresses, "FX3U", maximum=64)
        except DevicePolicyError as error:
            raise ToolPreconditionError("INVALID_ARGUMENTS", str(error)) from error
        try:
            values = self.gateway.read_bound(plan, binding)
        except GatewayError as error:
            raise ToolPreconditionError(getattr(error, "code", "") or "SIMULATOR_UNAVAILABLE", str(error),
                next_actions=({"tool": "list_simulator_targets"},)) from error
        return {"target_ref": target_ref, "route": "GX Simulator2", "plc_model": "FX3U",
                "values": values, "read_at": _now(), "evidence_kind": "runtime_device_observation",
                "program_correspondence": "unverified", "physical_plc": False}

    def get_simulator_status(self, target_ref):
        from plc.device_policy import simulator_run_monitor
        monitor, expected = simulator_run_monitor("FX3U")
        result = self.read_simulator_devices(target_ref, [monitor])
        return {**result, "run_monitor": monitor,
                "run_state": "run" if result["values"][monitor] == expected else "not_running",
                "state_basis": "model_specific_run_monitor"}

    def connect_simulator(self):
        """Open the existing Simulator2-only gateway; never start a PLC program."""
        from simulator.runtime import get_simulator_gateway_runtime
        from simulator.gateway import GatewayError
        try:
            runtime = get_simulator_gateway_runtime()
            runtime.ensure_gateway()
            self._gateway = runtime.client(timeout=2)
            health = self._gateway.health()
            if not health.get("connected"):
                self._gateway.connect()
            return self.list_simulator_targets()
        except (GatewayError, RuntimeError) as error:
            raise ToolPreconditionError(getattr(error, "code", "") or "SIMULATOR_GATEWAY_UNAVAILABLE", str(error),
                next_actions=({"tool": "list_simulator_targets"},)) from error

    def prepare_simulator_scenario(self, config):
        from application.simulator_view import get_simulation_views
        from simulator.signals import normalize_scenario
        try:
            # Validate all names, types and values before allocating a view.
            normalized, suite = normalize_scenario(config)
            views = get_simulation_views()
            view = views.create_normalized(normalized, source={"kind": "prepared_test_plan",
                "program_correspondence": "unverified", "physical_plc": False})
        except ValueError as error:
            raise ToolPreconditionError("INVALID_ARGUMENTS", str(error)) from error
        return {**views.public(view), "suite": suite, "execution_started": False,
                "next_step": "Save this suite through the existing version-bound simulation plan service; execution uses its existing consent and coordinator."}

    def start_simulator_trace(self, target_ref, config):
        from application.simulator_view import get_simulation_views
        from simulator.gateway import GatewayError
        try:
            return get_simulation_views().watch(self, target_ref, config)
        except ValueError as error:
            raise ToolPreconditionError("INVALID_ARGUMENTS", str(error)) from error
        except GatewayError as error:
            raise ToolPreconditionError(getattr(error, "code", "") or "SIMULATOR_UNAVAILABLE", str(error),
                next_actions=({"tool": "list_simulator_targets"},)) from error

    def get_simulator_trace(self, view_ref):
        from application.simulator_view import get_simulation_views
        views = get_simulation_views()
        try:
            return views.public(views.get(view_ref))
        except ValueError as error:
            raise ToolPreconditionError("STALE_SIMULATOR_VIEW", str(error)) from error

    def stop_simulator_trace(self, view_ref):
        from application.simulator_view import get_simulation_views
        try:
            return get_simulation_views().stop(view_ref)
        except ValueError as error:
            raise ToolPreconditionError("STALE_SIMULATOR_VIEW", str(error)) from error

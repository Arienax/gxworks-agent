"""Headless, non-migrating project queries and one bounded file boundary."""
from __future__ import annotations

import copy
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Mapping

from storage.session import SessionStore
from agent_runtime.messages import ToolCall
from agent_runtime.runtime import build_default_tool_runtime, public_tool_result_data
from .workspace import artifact_relative_path


class ProjectError(ValueError):
    pass


def record_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ProjectError("Invalid resource ID")
    return value


def contained(path: Path, root: Path) -> Path:
    path, root = path.resolve(), root.resolve()
    if path == root or root not in path.parents:
        raise ProjectError("Resource escapes its storage directory")
    return path


def public(value: Any) -> Any:
    """Defense in depth for metadata; resource schemas remain allowlisted."""
    if isinstance(value, Mapping):
        return {str(k): public(v) for k, v in value.items()
                if not str(k).startswith("_") and not str(k).lower().endswith(("_path", "_dir", "_file")) and not any(
                    token in str(k).lower() for token in ("api_key", "apikey", "token", "credential", "password", "secret", "staging_dir"))}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|etc)/)[^\s\"<>|]*", "[private path]", value)
    return copy.deepcopy(value)


class ProjectService:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.store = SessionStore(base_dir=self.workspace, create=False)
        self.runtime = build_default_tool_runtime()

    def raw_project(self, project_id: str) -> dict:
        record_id(project_id)
        path = contained(self.store.project_path(project_id), self.workspace)
        project = self.store.get_project(project_id)
        if not path.is_file() or not isinstance(project, dict) or project.get("id") != project_id:
            raise KeyError("Project not found")
        return project

    def list_projects(self) -> list[dict]:
        if not self.store.index_path.exists():
            return []
        contained(self.store.index_path, self.workspace)
        index = self.store._read_json(self.store.index_path, {})
        result = []
        for project_id in index.get("projects", []):
            result.append(self.project(project_id, detail=False))
        return sorted(result, key=lambda p: p.get("updated_at", ""), reverse=True)

    def project(self, project_id: str, *, detail=True) -> dict:
        project = self.raw_project(project_id)
        keys = ("id", "name", "created_at", "updated_at", "plc_model", "target_mode", "effort", "active_version_id")
        result = {k: project.get(k) for k in keys}
        result["version_count"] = len(project["versions"])
        if detail:
            result["confirmed_spec"] = public(project.get("confirmed_spec"))
            result["versions"] = [self.version(project_id, v["id"]) for v in project["versions"]]
            result["messages"] = [{k: public(m.get(k)) for k in ("id", "role", "kind", "content", "created_at")}
                                  for m in project.get("messages", []) if isinstance(m, dict)]
            result["reports"] = self.reports(project_id)
        return result

    def raw_version(self, project_id: str, version_id: str) -> dict:
        project = self.raw_project(project_id)
        record_id(version_id)
        contained(self.store.version_dir(project_id, version_id), self.store.project_dir(project_id))
        version = next((v for v in project["versions"] if v.get("id") == version_id), None)
        if version is None:
            raise KeyError("Version not found")
        return version

    def version(self, project_id: str, version_id: str) -> dict:
        version = self.raw_version(project_id, version_id)
        keys = ("id", "created_at", "target_mode", "plc_model", "summary", "validation", "lifecycle_status",
                "parent_version_id", "ir_sha256", "confirmed_spec_hash", "confirmed_spec_snapshot",
                "gx_sync", "simulator_runs", "simulator_test_plans", "debug_attempts")
        result = {k: public(version.get(k)) for k in keys}
        result["artifacts"] = self.artifacts(project_id, version_id)
        result["capabilities"] = self.capabilities(version)
        return result

    @staticmethod
    def capabilities(version=None) -> dict:
        mode = str((version or {}).get("target_mode") or "ladder").lower()
        ladder = mode == "ladder"
        fbd = mode == "fbd"
        return {"project_type": "fx", "body_form": mode,
                "representations": ["ladder_svg", "st", "ir"] if ladder else ["fbd", "svg", "gxw"] if fbd else ["st"],
                "operations": {"view": True, "generate": mode in ("ladder", "st", "fbd"),
                               "diagnose": ladder, "patch": ladder, "gx_import": ladder or fbd,
                               "simulation": ladder, "gx_compile": False, "fbd_edit": fbd,
                               "fbd_convert": ladder},
                "sfc": "requirement_input", "structured_semantics": "verified_native_templates" if fbd else "experimental_read_only"}

    def artifact(self, project_id: str, version_id: str, artifact_id: str) -> Path:
        version = self.raw_version(project_id, version_id)
        record_id(artifact_id)
        artifacts = version.get("artifacts") or {}
        if artifact_id not in artifacts or not isinstance(artifacts[artifact_id], str):
            raise KeyError("Artifact not found")
        try:
            relative = artifact_relative_path(artifacts[artifact_id])
        except ValueError:
            raise ProjectError("Invalid artifact manifest") from None
        root = self.store.version_dir(project_id, version_id)
        path = contained(root / relative, root)
        if path.suffix.lower() not in (".svg", ".st", ".txt", ".json", ".csv", ".html", ".md", ".png", ".pdf", ".gxw"):
            raise ProjectError("Unsupported artifact type")
        if not path.is_file():
            raise KeyError("Artifact file not found")
        return path

    @staticmethod
    def themed_svg(svg_text: str, theme: str | None = None) -> str:
        """Transform display colors in memory, preserving canonical artifacts."""
        if theme is None:
            return svg_text
        if theme not in ("light", "dark"):
            raise ProjectError("Unsupported SVG theme")
        from rendering.ladder import normalize_svg_for_preview
        return normalize_svg_for_preview(svg_text, theme)

    def svg_preview(self, project_id: str, version_id: str, artifact_id="svg", *, theme=None) -> str:
        path = self.artifact(project_id, version_id, artifact_id)
        if path.suffix.lower() != ".svg":
            raise ProjectError("Artifact is not an SVG")
        text = path.read_text(encoding="utf-8")
        # FBD has its own stylesheet. The legacy ladder recoloring changes its
        # background without changing class-based text, making labels unreadable.
        if self.raw_version(project_id, version_id).get("target_mode") == "fbd":
            return text
        return self.themed_svg(text, theme)

    def artifacts(self, project_id: str, version_id: str) -> list[dict]:
        version = self.raw_version(project_id, version_id)
        result = []
        for artifact_id in (version.get("artifacts") or {}):
            try:
                path = self.artifact(project_id, version_id, artifact_id)
            except KeyError:
                result.append({"id": artifact_id, "available": False})
                continue
            result.append({"id": artifact_id, "filename": path.name, "available": True,
                           "size": path.stat().st_size, "media_type": media_type(path)})
        return result

    def program(self, project_id: str, version_id: str) -> dict | None:
        version = self.raw_version(project_id, version_id)
        if version.get("target_mode") == "fbd":
            return json.loads(self.artifact(project_id, version_id, "fbd").read_text(encoding="utf-8"))
        for key in ("ir", "json"):
            if (version.get("artifacts") or {}).get(key):
                self.artifact(project_id, version_id, key)
        return self.store.load_program_ir(project_id, version_id, persist_legacy=False)

    def tool_context(self, project_id: str, version_id: str | None = None):
        from agent_runtime.plc_tools import build_tool_context
        from plc.ir import ir_to_ladder
        project = self.raw_project(project_id)
        selected = version_id or project.get("active_version_id")
        version = self.raw_version(project_id, selected) if selected else None
        program = self.program(project_id, selected) if version and version.get("target_mode") == "ladder" else None
        if self.raw_project(project_id) != project:
            raise ProjectError("Project changed during read; retry")
        return build_tool_context(project, version=version, program_ir=program,
                                  ladder=ir_to_ladder(program) if program else None)

    def verified_program(self, project_id: str, version_id: str) -> dict | None:
        """Bind navigation and evidence to the saved IR without semantic revalidation."""
        from plc.ir import canonical_sha256
        from .workspace import ConflictError
        version = self.raw_version(project_id, version_id)
        program = self.program(project_id, version_id)
        if version.get("target_mode") == "ladder" and version.get("ir_sha256"):
            if not program or canonical_sha256(program) != version["ir_sha256"]:
                raise ConflictError("Version IR changed after validation")
        return program

    def invoke(self, project_id: str, version_id: str | None, call_id: str, name: str, arguments: dict):
        return self.runtime.invoke(ToolCall(call_id, name, arguments), self.tool_context(project_id, version_id))

    def diagnostics(self, project_id: str, version_id: str) -> dict:
        return public_tool_result_data(self.invoke(project_id, version_id, "diagnostics", "get_diagnostics", {}))

    def reports(self, project_id: str) -> list[dict]:
        project = self.raw_project(project_id)
        return [{k: public(r.get(k)) for k in ("report_id", "report_type", "status", "summary", "created_at", "base_version_id")}
                for r in project.get("reports", []) if isinstance(r, dict)]

    def report(self, project_id: str, report_id: str) -> dict:
        self.raw_project(project_id)
        record_id(report_id)
        contained(self.store.report_path(project_id, report_id), self.store.project_dir(project_id))
        report = self.store.get_report(project_id, report_id)
        if not report:
            raise KeyError("Report not found")
        return public(report)

    def plan(self, project_id: str, version_id: str, plan_id: str, kind="simulation") -> dict:
        """Load a managed executable plan without migration or device access.

        This backend method retains the private candidate needed by approval.
        Transport readers must use their existing public preview projection.
        """
        if kind not in ("simulation", "debug"):
            raise ProjectError("Unsupported plan kind")
        version = self.raw_version(project_id, version_id)
        record_id(plan_id)
        for artifact_id in version.get("artifacts") or {}:
            self.artifact(project_id, version_id, artifact_id)
        root = self.store.version_dir(project_id, version_id)
        if kind == "simulation":
            entry = next((item for item in version.get("simulator_test_plans", [])
                          if isinstance(item, dict) and item.get("plan_id") == plan_id), None)
            if entry is None:
                raise KeyError("Plan not found")
            filename = entry.get("plan_artifact")
            if not isinstance(filename, str):
                raise ProjectError("Invalid plan artifact manifest")
            relative = filename.replace("\\", "/")
            if not relative or ":" in relative or relative.startswith("/") or ".." in relative.split("/"):
                raise ProjectError("Invalid plan artifact manifest")
            path = contained(root / filename, root)
            if path.suffix.lower() != ".json" or not path.is_file():
                raise ProjectError("Plan artifact is missing or invalid")
            plan = self.store.load_simulator_test_plan(project_id, version_id, plan_id, persist_legacy=False)
            if not isinstance(plan, dict) or plan.get("schema_version") != 1:
                raise ProjectError("Invalid saved simulator plan")
            from simulator.planning import normalize_generated_test_suite
            program = self.program(project_id, version_id)
            normalized = normalize_generated_test_suite(plan.get("suite"), program)
            return {"schema_version": 1, "binding": copy.deepcopy(plan["binding"]),
                    "source": str(plan.get("source") or ""), "suite": normalized}

        project_root = self.store.project_dir(project_id)
        path = contained(project_root / "debug" / "plans" / (plan_id + ".json"), project_root)
        if not path.is_file():
            raise KeyError("Plan not found")
        plan = self.store.load_debug_plan(project_id, plan_id)
        from application.debug_loop import (DEBUG_LOOP_SCHEMA_VERSION, build_failure_evidence,
                                    normalize_debug_diagnosis, normalize_and_apply_debug_patch)
        from plc.ir import canonical_sha256
        if (not isinstance(plan, dict) or plan.get("schema_version") != DEBUG_LOOP_SCHEMA_VERSION
                or plan.get("plan_id") != plan_id or plan.get("project_id") != project_id
                or plan.get("base_version_id") != version_id):
            raise ProjectError("Debug plan belongs to a different project or version")
        run_id = record_id(plan.get("source_run_id"))
        # The public query checks every managed path and the evidence integrity.
        # Re-read its private envelope for hashes; redacted text cannot replace
        # the bytes that were bound to the original simulator evidence.
        self.simulator_run(project_id, version_id, run_id)
        run = self.store.load_simulator_run(project_id, version_id, run_id)
        program = self.program(project_id, version_id)
        evidence = build_failure_evidence(program, run, project_id=project_id, version_id=version_id,
            retriever=lambda *_args, **_kwargs: copy.deepcopy((plan.get("evidence") or {}).get("knowledge") or []))
        diagnosis = normalize_debug_diagnosis(plan.get("diagnosis"), evidence)
        candidate, patch = normalize_and_apply_debug_patch(program, plan.get("patch"), evidence, diagnosis)
        if (canonical_sha256(candidate) != canonical_sha256(plan.get("candidate_ir"))
                or canonical_sha256(run["suite"]) != canonical_sha256(plan.get("regression_suite"))):
            raise ProjectError("Debug candidate or regression suite changed after planning")
        return {"schema_version": DEBUG_LOOP_SCHEMA_VERSION, "plan_id": plan_id,
                "project_id": project_id, "base_version_id": version_id,
                "created_at": plan.get("created_at"), "source_run_id": run_id,
                "evidence": evidence, "diagnosis": diagnosis, "patch": patch,
                "candidate_ir": candidate, "regression_suite": copy.deepcopy(run["suite"])}

    def simulator_run(self, project_id: str, version_id: str, run_id: str) -> dict:
        version = self.raw_version(project_id, version_id)
        record_id(run_id)
        # Saved indexes may contain paths; validate each before calling the store.
        root = self.store.version_dir(project_id, version_id)
        entry = next((r for r in version.get("simulator_runs", [])
                      if isinstance(r, dict) and r.get("run_id") == run_id), None)
        if not entry:
            raise KeyError("Run not found")
        for artifact_id in version.get("artifacts") or {}:
            self.artifact(project_id, version_id, artifact_id)
        for key, value in entry.items():
            if key.endswith("artifact"):
                if not isinstance(value, str) or not value or ":" in value:
                    raise ProjectError("Invalid simulator artifact manifest")
                relative = Path(value)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ProjectError("Invalid simulator artifact manifest")
                path = contained(root / relative, root)
                if path.suffix.lower() != ".json" or not path.is_file():
                    raise ProjectError("Simulator evidence file is missing or invalid")
        # SessionStore already explicitly uses persist_legacy=False here.
        result = self.store.load_simulator_run(project_id, version_id, run_id)
        if result is None:
            raise KeyError("Run evidence not found")
        return public(result)


def media_type(path: Path) -> str:
    return {".st": "text/plain", ".md": "text/plain", ".json": "application/json",
            ".svg": "image/svg+xml"}.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

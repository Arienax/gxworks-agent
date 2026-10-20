"""Engineering session use cases over the domain-free persistence store.

All reads are side-effect free. Compatibility upgrades are explicit commands.
"""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

from storage.session import SessionStore, _index_transaction, _utc_now


class ProjectSession(SessionStore):
    """Application commands for PLC checks, version lifecycle and evidence."""

    @classmethod
    def _with_project_defaults(cls, project):
        normalized = super()._with_project_defaults(project)
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

    def complete_version(self, project_id, version_id, metadata, *, activate=True):
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        metadata = copy.deepcopy(metadata)
        spec = metadata.get('confirmed_spec_snapshot') or project.get('confirmed_spec')
        if spec and spec.get('behavior_constraints') and (metadata.get('artifacts') or {}).get('ir'):
            from simulator.bounded import check_confirmed_behavior
            program = json.loads((self.version_dir(project_id, version_id) / metadata['artifacts']['ir']).read_text(encoding='utf-8'))
            report = check_confirmed_behavior(program, spec, version_binding={'project_id': project_id, 'version_id': version_id})
            receipt = (metadata.get('generation_metadata') or {}).get('construction_binding') or {}
            from plc.construction import apply_construction_check
            report = apply_construction_check(report, receipt)
            metadata['behavior_check'] = report
            if report['activation_blocked']:
                activate = False
                metadata.update(activation_blocked=True, lifecycle_status='diagnostic')
                metadata.setdefault('generation_metadata', {})['diagnostic_only'] = True
            elif report['status'] == 'unverified':
                metadata['lifecycle_status'] = 'draft'
        return super().complete_version(project_id, version_id, metadata, activate=activate)

    def activate_version(self, project_id, version_id):
        project = self.get_project(project_id)
        version = self.get_version(project_id, version_id)
        if project is None or version is None:
            raise KeyError(version_id)
        if version.get('activation_blocked'):
            raise ValueError('已确认行为关系存在反例；诊断草稿不能激活。')
        spec = project.get('confirmed_spec')
        if spec and spec.get('behavior_constraints'):
            from simulator.bounded import check_confirmed_behavior
            program = self.load_program_ir(project_id, version_id, persist_legacy=False)
            if program and check_confirmed_behavior(program, spec)['activation_blocked']:
                raise ValueError('当前规格的行为检查发现反例；请修改候选后重新检查。')
        return super().activate_version(project_id, version_id)

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
                raw = self._read_project_record(project_id)
                if not isinstance(raw, Mapping) or not needs_spec_migration(raw.get("confirmed_spec")):
                    continue
                upgraded = self._with_project_defaults(raw)
                persisted = copy.deepcopy(raw)
                for key in ("confirmed_spec", "decision_history", "confirmed_decision_receipt_id"):
                    persisted[key] = upgraded[key]
                self._save_project_record(persisted)
                report["migrated"].append(project_id)
            except (OSError, ValueError, TypeError, KeyError):
                report["failed"].append(str(project_id))
        return report

    def load_ladder(self, project_id, version_id, *, persist_legacy=False):
        """Read a ladder through canonical IR without modifying the workspace."""

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
        # later metadata write is interrupted, an explicit retry can repeat the
        # same deterministic migration. Publish the project index last.
        self._write_json(ir_path, program)
        self._write_json(version_path / "version.json", updated)
        self.save_project(project)
        return updated

    def load_program_ir(self, project_id, version_id, *, persist_legacy=False):
        """Return a validated compatibility view without changing any files.

        The legacy keyword is accepted for old callers but never enables writes.
        Use migrate_program_ir to persist a compatibility upgrade explicitly.
        """

        version = self.get_version(project_id, version_id)
        if not version or version.get("target_mode") != "ladder":
            return None
        artifacts = version.get("artifacts", {}) or {}
        version_dir = self.version_dir(project_id, version_id)
        ir_name = str(artifacts.get("ir") or "").strip()
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
        return program

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

    def load_simulator_test_plan(self, project_id, version_id, plan_id, *, persist_legacy=False):
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

    @_index_transaction
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

    def migrate_program_ir(self, project_id, version_id):
        """Explicitly persist a legacy IR; never replace an existing IR artifact."""
        version = self.get_version(project_id, version_id)
        if version is None:
            raise KeyError(version_id)
        program = self.load_program_ir(project_id, version_id)
        if program is not None and not (version.get("artifacts") or {}).get("ir"):
            self._persist_legacy_program_ir(project_id, version_id, program)
        return program

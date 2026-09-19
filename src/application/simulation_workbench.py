"""Human-authored simulator plans and read-only replay of recorded evidence."""
from __future__ import annotations

import copy
import math
import re

from application.projects import public
from application.workspace import ConflictError
from plc.ir import canonical_sha256
from plc.device_policy import is_stimulus_device
from simulator.editor import editor_metadata, edit_suite, parse_editor_suite


class SimulationWorkbenchError(ValueError):
    pass


def requirements_for(program, version):
    """IDs refer to requirements in this immutable version, never live project text."""
    rows = (program.get("logic") or {}).get("requirements") or []
    if not rows:
        rows = (version.get("confirmed_spec_snapshot") or {}).get("execution_semantics") or []
    if isinstance(rows, dict):
        rows = rows.get("requirements") or []
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        result.append({"id": str(row.get("requirement_id") or row.get("id") or f"SEM{index + 1:03d}"),
                       "text": str(row.get("evidence") or row.get("text") or row.get("description") or row.get("semantic") or ""),
                       "devices": row.get("devices") or [], "source": row.get("source") or "version"})
    return result


def plan_view(plan):
    links, issues = {}, []
    source = None
    for test in plan["suite"]["tests"]:
        meta = (test.get("metadata") or {}).get("workbench") or {}
        if not isinstance(meta, dict):
            raise SimulationWorkbenchError("方案中的工作台关联元数据格式无效。")
        links[test["name"]] = list(meta.get("requirement_ids") or [])
        for issue in meta.get("issue_ids") or []:
            if issue not in issues:
                issues.append(issue)
        source = source or meta.get("source_plan_id")
    return public({**plan, "suite_sha256": canonical_sha256(plan["suite"]),
                   "requirement_links": links, "issue_ids": issues, "source_plan_id": source})


class SimulationWorkbenchService:
    def __init__(self, workbench):
        self.workbench = workbench
        self.projects = workbench.projects

    def _context(self, project_id, version_id):
        version = self.projects.raw_version(project_id, version_id)
        if version.get("target_mode") != "ladder":
            raise SimulationWorkbenchError("仿真步骤当前需要梯形图版本。")
        program = self.projects.verified_program(project_id, version_id)
        if not isinstance(program, dict):
            raise SimulationWorkbenchError("当前版本没有可用于仿真的程序。")
        return version, program

    def read(self, project_id, version_id):
        version, program = self._context(project_id, version_id)
        plans, unavailable = [], []
        for record in reversed(version.get("simulator_test_plans") or []):
            try:
                plans.append(plan_view(self.projects.plan(project_id, version_id, record["plan_id"])))
            except (KeyError, ValueError):
                unavailable.append({"plan_id": record.get("plan_id"), "message": "方案已损坏或与当前版本不符。"})
        declared = {**(program.get("devices") or {}), **(program.get("io_map") or {})}
        devices = []
        for address, row in sorted(declared.items()):
            devices.append({"address": address, "label": str((row.get("comment") or row.get("label") or "") if isinstance(row, dict) else row or ""),
                            "writable": is_stimulus_device(address, program["plc"]["cpu"])})
        requirements = requirements_for(program, version)
        execution_by_test, latest_by_requirement, unavailable_runs = {}, {}, []
        for record in reversed(version.get("simulator_runs") or []):
            try:
                saved = self.projects.simulator_run(project_id, version_id, record["run_id"])
            except (KeyError, ValueError):
                unavailable_runs.append(record.get("run_id"))
                continue
            verification = saved["verification"]
            outcomes = {row.get("name"): row for row in saved["result"].get("results") or []}
            for test in saved["suite"]["tests"]:
                meta = (test.get("metadata") or {}).get("workbench") or {}
                if not isinstance(meta, dict) or meta.get("project_id") != project_id or meta.get("version_id") != version_id:
                    continue
                row = outcomes.get(test["name"], {})
                execution = {"run_id": record["run_id"], "test_name": test["name"],
                             "created_at": record.get("created_at"), "backend_kind": row.get("backend_kind"),
                             "status": "blocked" if verification.get("status") == "blocked" else row.get("status", "blocked"),
                             "recorded_status": row.get("status"), "verification": verification}
                suite_hash = saved["binding"].get("suite_sha256")
                for requirement_id in meta.get("requirement_ids") or []:
                    execution_by_test.setdefault((requirement_id, suite_hash, test["name"]), execution)
                    latest_by_requirement.setdefault(requirement_id, execution)
        for requirement in requirements:
            requirement["tests"] = [{"plan_id": p["binding"]["plan_id"], "test_name": name,
                                     "latest_run": execution_by_test.get((requirement["id"], p["suite_sha256"], name))}
                                    for p in plans for name, ids in p["requirement_links"].items() if requirement["id"] in ids]
            requirement["status"] = "linked" if requirement["tests"] else "unlinked"
            requirement["latest_run"] = latest_by_requirement.get(requirement["id"])
        runs = [{k: public(r.get(k)) for k in ("run_id", "created_at", "status", "suite_name", "verification", "backend_kinds")}
                for r in reversed(version.get("simulator_runs") or [])]
        return public({"project_id": project_id, "version_id": version_id, "ir_sha256": canonical_sha256(program),
                       "plc_model": program["plc"]["cpu"], "devices": devices, "plans": plans,
                       "unavailable_plans": unavailable, "unavailable_runs": unavailable_runs,
                       "requirements": requirements, "runs": runs, "editor": editor_metadata(program["plc"]["cpu"])})

    def edit_draft(self, project_id, version_id, *, suite, command):
        _, program = self._context(project_id, version_id)
        try:
            return {"suite": edit_suite(suite, command, program["plc"]["cpu"])}
        except (ValueError, KeyError, TypeError) as error:
            raise SimulationWorkbenchError(str(error)) from error

    def save(self, project_id, version_id, *, suite, requirement_links, issue_ids,
             expected_ir_sha256, source_plan_id=None):
        from simulator.models import normalize_test_suite
        from simulator.planning import normalize_generated_test_suite

        self.workbench.writable()
        with self.workbench.lock.thread_lock:
            version, program = self._context(project_id, version_id)
            if canonical_sha256(program) != expected_ir_sha256:
                raise ConflictError("程序内容已变化，请重新加载仿真方案。")
            if source_plan_id:
                self.projects.plan(project_id, version_id, source_plan_id)
            available = {r["id"] for r in requirements_for(program, version)}
            if not isinstance(requirement_links, dict) or not isinstance(issue_ids, list):
                raise SimulationWorkbenchError("需求与问题关联格式无效。")
            if len(issue_ids) > 200 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in issue_ids):
                raise SimulationWorkbenchError("问题标识无效。")
            # Strict DSL validation first: hand edits must not be silently repaired
            # into a different input sequence by the model-output shape repairer.
            try:
                candidate = normalize_test_suite(parse_editor_suite(suite), plc_model=program["plc"]["cpu"])
                names = {test["name"] for test in candidate["tests"]}
                if set(requirement_links) - names:
                    raise SimulationWorkbenchError("关联引用了方案中不存在的测试。")
                for test in candidate["tests"]:
                    ids = requirement_links.get(test["name"], [])
                    if not isinstance(ids, list) or any(not isinstance(v, str) or v not in available for v in ids):
                        raise SimulationWorkbenchError("关联引用了当前版本之外的需求。")
                    test["metadata"]["workbench"] = {
                        "schema_version": 1, "project_id": project_id, "version_id": version_id,
                        "requirement_ids": list(dict.fromkeys(ids)), "issue_ids": list(dict.fromkeys(issue_ids)),
                        "source_plan_id": source_plan_id,
                    }
                normalized = normalize_generated_test_suite(candidate, program)
            except (ValueError, TypeError, OverflowError) as error:
                raise SimulationWorkbenchError(str(error)) from error
            # SessionStore's managed writer creates a fresh immutable plan ID.
            saved = self.workbench.store.save_simulator_test_plan(project_id, version_id, normalized, source="manual")
            return plan_view(saved)

    def replay(self, project_id, version_id, run_id):
        self._context(project_id, version_id)
        saved = self.projects.simulator_run(project_id, version_id, run_id)
        tests = {test["name"]: test for test in saved["suite"]["tests"]}
        cases = []
        for row in saved["result"].get("results") or []:
            # Only observed reads form waveforms. Stimulus writes are events,
            # never evidence that the PLC actually held the requested value.
            observations = []
            for event in row.get("trace") or []:
                at = event.get("at_ms")
                if (event.get("event") in {"write", "initial_write", "fault_write"}
                        or not isinstance(at, (int, float)) or not math.isfinite(at)
                        or not isinstance(event.get("values"), dict)):
                    continue
                observations.append({"at_ms": at, "event": event.get("event"), "values": copy.deepcopy(event["values"])})
            observations.sort(key=lambda e: e["at_ms"])
            test = tests.get(row.get("name"), {})
            meta = (test.get("metadata") or {}).get("workbench") or {}
            if not isinstance(meta, dict):
                meta = {}
            assertions, used = [], {}
            for assertion in row.get("assertions") or []:
                kind = "wait_for" if assertion.get("wait_for") else "expect"
                step = next((s for s in test.get("steps") or [] if s.get("id") == assertion.get("step_id")), {})
                matches = [e for e in step.get(kind) or [] if e.get("address") == assertion.get("address")]
                key = (assertion.get("step_id"), kind, assertion.get("address"))
                offset = used.get(key, 0)
                expected = matches[offset] if offset < len(matches) else {}
                used[key] = offset + 1
                observed = next((event.get("values") or {} for event in row.get("trace") or []
                                 if event.get("event") == kind and event.get("step_id") == assertion.get("step_id")
                                 and event.get("at_ms") == assertion.get("at_ms")), {})
                assertions.append({**assertion, "actual": observed.get(assertion.get("address")),
                                   "expected": {k: expected[k] for k in ("operator", "value", "tolerance") if k in expected}})
            cases.append({"name": row.get("name"), "status": row.get("status"), "backend_kind": row.get("backend_kind"),
                          "duration_ms": row.get("duration_ms"), "observations": observations,
                          "assertions": assertions, "invariant_violations": row.get("invariant_violations") or [],
                          "requirement_ids": meta.get("requirement_ids") or [], "issue_ids": meta.get("issue_ids") or [],
                          "steps": test.get("steps") or [], "sample_ms": test.get("sample_ms")})
        return public({"run_id": run_id, "version_id": version_id, "binding": saved["binding"],
                       "verification": saved["verification"], "status": saved["result"].get("status"), "cases": cases})

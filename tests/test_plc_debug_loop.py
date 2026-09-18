import copy

import pytest

from gxworks2.models import ImportResult
from application.debug_loop import (
    DebugLoopError,
    DebugPatchLoopService,
    build_failure_evidence,
    normalize_and_apply_debug_patch,
    normalize_debug_diagnosis,
    render_candidate_artifacts,
)
from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
from simulator import InMemoryTestBackend, SimulatorRegressionService


def _contact(kind, address):
    return {"type": kind, "address": address, "label": ""}


def _coil(address):
    return {"type": "COIL", "address": address, "label": ""}


def _rung(rung_id, inputs, output):
    return {
        "rung_id": rung_id,
        "debug_note": f"network {rung_id}",
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": list(inputs),
                "outputs": [_coil(output)],
            }
        ],
    }


def _program():
    return build_plc_ir(
        {
            "device_comments": {
                "X0": "start",
                "X1": "stop",
                "M0": "run",
                "Y0": "motor",
            },
            "rungs": [
                _rung(1, [_contact("NO", "X0")], "M0"),
                _rung(2, [_contact("NO", "M0")], "Y0"),
            ],
        },
        revision=7,
    )


def _suite():
    return {
        "name": "motor_regression",
        "plc_model": "FX3U",
        "tests": [
            {
                "name": "motor_stop",
                "plc_model": "FX3U",
                "initial": {"X0": 0, "X1": 0},
                "steps": [
                    {"id": "start", "at_ms": 0, "set": {"X0": 1}},
                    {"id": "started", "at_ms": 0, "expect": {"Y0": 1}},
                    {"id": "stop", "at_ms": 10, "set": {"X1": 1}},
                    {"id": "stopped", "at_ms": 10, "expect": {"Y0": 0}},
                ],
                "sample_ms": 5,
                "timeout_ms": 100,
            }
        ],
    }


def _base_logic(backend, _values):
    backend.values["Y0"] = int(bool(backend.values.get("X0", 0)))


def _fixed_logic(backend, _values):
    backend.values["Y0"] = int(
        bool(backend.values.get("X0", 0))
        and not bool(backend.values.get("X1", 0))
    )


def _project_with_failure(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="debug", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    program = _program()
    artifacts = render_candidate_artifacts(program, version_dir)
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "program_name": "MAIN",
            "revision": program["revision"],
            "ir_sha256": canonical_sha256(program),
            "ladder_sha256": program["source"]["ladder_sha256"],
            "artifacts": artifacts,
            "lifecycle_status": "accepted",
        },
    )
    execution = SimulatorRegressionService(
        store, backend=InMemoryTestBackend(on_write=_base_logic)
    ).run_version_suite(project["id"], version_id, _suite())
    failed = execution["result"]
    assert failed["status"] == "failed"
    record = execution["record"]
    return store, project["id"], version_id, program, record["run_id"]


def _knowledge(_query, **_kwargs):
    return [
        {
            "id": "debug-case-stop-contact",
            "source": "debug_cases",
            "page": "",
            "section": "motor stop",
            "text": "A maintained branch must include its proven stop condition.",
        }
    ]


def _diagnosis():
    return {
        "schema_version": 1,
        "root_cause": "The motor output path omits X1 stop contact.",
        "confidence": 0.95,
        "affected_networks": ["N0002"],
        "evidence_refs": [
            "network:N0002",
            "knowledge:debug-case-stop-contact",
        ],
        "recommended_change": "Add X1 as a normally-closed series contact in N0002.",
    }


def _patch(program):
    replacement = copy.deepcopy(program["networks"][1]["ladder"])
    replacement["branches"][0]["inputs"].append(_contact("NC", "X1"))
    return {
        "schema_version": 1,
        "base_revision": program["revision"],
        "base_ir_sha256": canonical_sha256(program),
        "target_revision": program["revision"] + 1,
        "operations": [
            {
                "operation": "modify_network",
                "network": "N0002",
                "ladder": replacement,
            }
        ],
    }


class _FakeImporter:
    def __init__(self, *, partial_failure=False):
        self.calls = []
        self.partial_failure = partial_failure

    def __call__(self, csv_path, **kwargs):
        self.calls.append({"csv_path": str(csv_path), **kwargs})
        is_candidate = (kwargs.get("import_context") or {}).get("debug_phase") == "candidate"
        if is_candidate and self.partial_failure:
            return ImportResult(
                False,
                "verify_comments",
                "comment verification failed",
                csv_path=str(csv_path),
                details={
                    "gxworks2": {"success": True},
                    "version_protection": {
                        "target_program_semantic_sha256": "c" * 64
                    },
                },
            )
        return ImportResult(
            True,
            "complete",
            "imported",
            csv_path=str(csv_path),
            details={
                "version_protection": {
                    "target_program_semantic_sha256": "c" * 64
                }
            },
        )


class _ReadOnlyImporter(_FakeImporter):
    def __call__(self, csv_path, **kwargs):
        self.calls.append({"csv_path": str(csv_path), **kwargs})
        return ImportResult(
            False,
            "import",
            "读取目标的程序为写入禁止，因此无法执行读取。请将程序设置为写入允许。",
            csv_path=str(csv_path),
        )


def test_failure_evidence_is_version_bound_scoped_and_citation_bearing(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    run = store.load_simulator_run(project_id, version_id, run_id)

    evidence = build_failure_evidence(
        program,
        run,
        project_id=project_id,
        version_id=version_id,
        retriever=_knowledge,
    )

    assert evidence["binding"]["ir_sha256"] == canonical_sha256(program)
    assert evidence["affected_devices"] == ["Y0"]
    assert set(evidence["allowed_patch_devices"]) >= {"X0", "X1", "M0", "Y0"}
    assert evidence["related_networks"] == ["N0001", "N0002"]
    assert {item["id"] for item in evidence["network_excerpts"]} == {
        "N0001",
        "N0002",
    }
    assert evidence["knowledge"][0]["id"] == "debug-case-stop-contact"
    assert any(item["ref"].startswith("assertion:motor_stop:stopped:Y0") for item in evidence["failures"])
    assert evidence["device_trace"]


@pytest.mark.parametrize("status", ["passed", "error", "unavailable"])
def test_only_assertion_or_invariant_failures_may_trigger_patch(tmp_path, status):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    run = store.load_simulator_run(project_id, version_id, run_id)
    run["result"]["status"] = status
    with pytest.raises(DebugLoopError, match="只有断言或不变量失败"):
        build_failure_evidence(
            program,
            run,
            project_id=project_id,
            version_id=version_id,
            retriever=_knowledge,
        )


def test_evidence_rejects_stale_or_cross_version_binding(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    run = store.load_simulator_run(project_id, version_id, run_id)
    run["binding"]["ir_sha256"] = "0" * 64
    with pytest.raises(DebugLoopError, match="不属于当前程序版本"):
        build_failure_evidence(
            program,
            run,
            project_id=project_id,
            version_id=version_id,
            retriever=_knowledge,
        )


def test_patch_boundary_rejects_whole_program_and_unrelated_network_changes(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    run = store.load_simulator_run(project_id, version_id, run_id)
    evidence = build_failure_evidence(
        program,
        run,
        project_id=project_id,
        version_id=version_id,
        retriever=_knowledge,
    )
    diagnosis = normalize_debug_diagnosis(_diagnosis(), evidence)

    forbidden = _patch(program)
    forbidden["operations"][0]["operation"] = "replace_program"
    with pytest.raises(DebugLoopError, match="只允许 modify_network"):
        normalize_and_apply_debug_patch(program, forbidden, evidence, diagnosis)

    unrelated = _patch(program)
    unrelated["operations"][0]["network"] = "N0001"
    unrelated["operations"][0]["ladder"] = copy.deepcopy(
        program["networks"][0]["ladder"]
    )
    with pytest.raises(DebugLoopError, match="未获授权"):
        normalize_and_apply_debug_patch(program, unrelated, evidence, diagnosis)


def test_approved_patch_passes_full_regression_before_activation(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter()
    service = DebugPatchLoopService(
        store,
        importer=importer,
        simulator_service=SimulatorRegressionService(
            store, backend=InMemoryTestBackend(on_write=_fixed_logic)
        ),
        retriever=_knowledge,
    )
    plan = service.prepare_plan(
        project_id, version_id, run_id, _diagnosis(), _patch(program)
    )
    assert plan["candidate_ir"]["revision"] == 8
    assert store.get_project(project_id)["active_version_id"] == version_id

    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] == "passed"
    candidate_id = attempt["candidate_version_id"]
    assert candidate_id != version_id
    assert store.get_project(project_id)["active_version_id"] == candidate_id
    candidate = store.get_version(project_id, candidate_id)
    assert candidate["lifecycle_status"] == "accepted"
    assert candidate["parent_version_id"] == version_id
    assert candidate["last_simulator_status"] == "passed"
    assert len(importer.calls) == 1
    assert store.load_debug_attempt(project_id, attempt["attempt_id"])["status"] == "passed"
    assert store.get_version(project_id, version_id)["debug_attempts"][-1]["attempt_id"] == attempt["attempt_id"]


def test_unrecorded_pass_cannot_activate_candidate(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter()

    class UnrecordedSimulator:
        def run_version_suite(self, *_args, **_kwargs):
            return {"result": {"status": "passed"}}

    service = DebugPatchLoopService(
        store, importer=importer, simulator_service=UnrecordedSimulator(),
        retriever=_knowledge,
    )
    plan = service.prepare_plan(project_id, version_id, run_id, _diagnosis(), _patch(program))
    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] != "passed"
    assert store.get_project(project_id)["active_version_id"] == version_id
    assert attempt["rollback"]["restored"]


@pytest.mark.parametrize("source", [
    "different_suite", "different_version", "changed_return", "changed_artifact", "persistence_only",
])
def test_pass_must_match_persisted_candidate_and_full_approved_suite(tmp_path, source):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter()
    real = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=_fixed_logic))

    class UnreliableSimulator:
        def run_version_suite(self, project_id, candidate_id, suite, **_kwargs):
            suite = copy.deepcopy(suite)
            if source == "different_suite":
                # Same suite/case/step names, but the stop check was removed.
                suite["tests"][0]["steps"] = suite["tests"][0]["steps"][:2]
            target_id = version_id if source == "different_version" else candidate_id
            execution = real.run_version_suite(project_id, target_id, suite)
            assert execution["verification"]["status"] == "passed"
            if source == "persistence_only":
                execution["record"] = store.save_simulator_run(
                    project_id, target_id, suite, execution["result"]
                )
            if source == "changed_return":
                execution["result"]["results"][0]["trace"].clear()
            if source == "changed_artifact":
                path = store.version_dir(project_id, target_id) / execution["record"]["trace_artifact"]
                payload = store._read_json(path)
                payload["result"]["results"][0]["trace"].clear()
                store._write_json(path, payload)
            return execution

    service = DebugPatchLoopService(
        store, importer=importer, simulator_service=UnreliableSimulator(), retriever=_knowledge,
    )
    plan = service.prepare_plan(project_id, version_id, run_id, _diagnosis(), _patch(program))
    attempt = service.execute_approved_plan(plan)
    assert attempt["status"] != "passed"
    if source == "persistence_only":
        assert attempt["status"] == "regression_unverified"
    assert attempt["regression"]["verification"]["status"] == "blocked"
    assert attempt["regression"]["verification"]["program_repair_allowed"] is False
    assert store.get_project(project_id)["active_version_id"] == version_id
    assert attempt["rollback"]["restored"]
    assert len(importer.calls) == 2


def test_failed_regression_reimports_base_and_keeps_it_active(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter()
    service = DebugPatchLoopService(
        store,
        importer=importer,
        simulator_service=SimulatorRegressionService(
            store, backend=InMemoryTestBackend(on_write=_base_logic)
        ),
        retriever=_knowledge,
    )
    plan = service.prepare_plan(
        project_id, version_id, run_id, _diagnosis(), _patch(program)
    )

    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] == "regression_failed"
    assert attempt["rollback"]["attempted"]
    assert attempt["rollback"]["restored"]
    assert len(importer.calls) == 2
    assert importer.calls[1]["rollback_expected_current_sha256"] == "c" * 64
    assert store.get_project(project_id)["active_version_id"] == version_id
    assert store.get_version(
        project_id, attempt["candidate_version_id"]
    )["lifecycle_status"] == "rejected"


def test_partial_import_failure_triggers_physical_rollback(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter(partial_failure=True)
    service = DebugPatchLoopService(
        store,
        importer=importer,
        simulator_service=SimulatorRegressionService(
            store, backend=InMemoryTestBackend(on_write=_fixed_logic)
        ),
        retriever=_knowledge,
    )
    plan = service.prepare_plan(
        project_id, version_id, run_id, _diagnosis(), _patch(program)
    )

    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] == "import_failed"
    assert attempt["rollback"]["restored"]
    assert len(importer.calls) == 2
    assert not attempt["regression"]
    assert store.get_project(project_id)["active_version_id"] == version_id


def test_read_only_rejection_does_not_attempt_false_physical_rollback(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _ReadOnlyImporter()
    service = DebugPatchLoopService(
        store,
        importer=importer,
        simulator_service=SimulatorRegressionService(
            store, backend=InMemoryTestBackend(on_write=_fixed_logic)
        ),
        retriever=_knowledge,
    )
    plan = service.prepare_plan(
        project_id, version_id, run_id, _diagnosis(), _patch(program)
    )

    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] == "import_failed"
    assert attempt["rollback"] == {"required": False, "attempted": False}
    assert len(importer.calls) == 1
    assert store.get_project(project_id)["active_version_id"] == version_id
    assert store.get_version(
        project_id, attempt["candidate_version_id"]
    )["lifecycle_status"] == "rejected"


def test_debug_loop_stops_simulator_before_candidate_and_rollback(tmp_path):
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    importer = _FakeImporter()

    class Preparation:
        success = True
        message = "stopped"

        def to_dict(self):
            return {"success": True, "message": self.message}

    class Preparer:
        def __init__(self):
            self.calls = 0

        def stop_if_running(self):
            self.calls += 1
            return Preparation()

        def prepare(self):
            return Preparation()

    preparer = Preparer()
    service = DebugPatchLoopService(
        store,
        importer=importer,
        simulator_service=SimulatorRegressionService(
            store,
            backend=InMemoryTestBackend(on_write=_base_logic),
            preparer=preparer,
        ),
        simulator_preparer=preparer,
        retriever=_knowledge,
    )
    plan = service.prepare_plan(
        project_id, version_id, run_id, _diagnosis(), _patch(program)
    )

    attempt = service.execute_approved_plan(plan)

    assert attempt["status"] == "regression_failed"
    assert preparer.calls == 2
    assert [
        item["import_context"]["debug_phase"] for item in importer.calls
    ] == ["candidate", "rollback"]
    assert attempt["rollback"]["simulator_stop"]["success"] is True

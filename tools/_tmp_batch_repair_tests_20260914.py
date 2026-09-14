from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker count {text.count(old)} != 1")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Existing field-patch tests should preserve all targets in a payload.
replace_once(
    "tests/test_field_patch_repair_protocol.py",
    '''def _payload(repair):\n    return {\n        \"repair_mode\": \"field_patch\",\n        \"base_sha256\": repair[\"base_sha256\"],\n        \"target\": copy.deepcopy(repair[\"target\"]),\n    }\n''',
    '''def _payload(repair):\n    targets = repair.get(\"targets\")\n    if not isinstance(targets, list):\n        target = repair.get(\"target\")\n        targets = [target] if isinstance(target, dict) else []\n    payload = {\n        \"repair_mode\": \"field_patch\",\n        \"base_sha256\": repair[\"base_sha256\"],\n        \"targets\": copy.deepcopy(targets),\n    }\n    if len(targets) == 1:\n        payload[\"target\"] = copy.deepcopy(targets[0])\n    return payload\n''',
)
with open("tests/test_field_patch_repair_protocol.py", "a", encoding="utf-8") as handle:
    handle.write(r'''


def test_multiple_validator_proven_modifier_errors_are_batched_deterministically():
    base = _base()
    base["rungs"][0]["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": "DDADDP", "operands": ["D0", "D2", "D4"], "label": None},
        {"type": "APP_INSTR", "opcode": "DDMOVP", "operands": ["D10", "D12"], "label": None},
    ]
    repair = plan(base, [
        {"path": "content$.rungs.0.branches.0.outputs.0.opcode", "reason": "invalid_ladder_structure", "observed_opcode": "DDADDP"},
        {"path": "content$.rungs.0.branches.0.outputs.1.opcode", "reason": "invalid_ladder_structure", "observed_opcode": "DDMOVP"},
    ], "FX3U")
    assert repair["mode"] == "field_patch"
    assert [target["strategy"] for target in repair["targets"]] == ["deterministic", "deterministic"]
    assert [target["deterministic_value"] for target in repair["targets"]] == ["DADDP", "DMOVP"]
    response = deterministic_response(_payload(repair))
    assert len(response["patches"]) == 2
    result = apply(base, response, repair)
    outputs = result["rungs"][0]["branches"][0]["outputs"]
    assert [output["opcode"] for output in outputs] == ["DADDP", "DMOVP"]


def test_multiple_independent_scalar_repairs_keep_unproven_fields_frozen():
    base = _base()
    base["rungs"][0]["debug_note"] = "x" * 100
    base["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "DDMOVP"
    base["rungs"][0]["branches"][0]["outputs"][0]["operands"] = ["D0", "D2"]
    repair = plan(base, [
        {"path": "content$.rungs.0.debug_note", "reason": "field_too_long"},
        {"path": "content$.rungs.0.branches.0.outputs.0.opcode", "reason": "invalid_ladder_structure", "observed_opcode": "DDMOVP"},
    ], "FX3U")
    result = apply(base, deterministic_response(_payload(repair)), repair)
    assert result["rungs"][0]["debug_note"] == "x" * 64
    output = result["rungs"][0]["branches"][0]["outputs"][0]
    assert output["opcode"] == "DMOVP"
    assert output["operands"] == ["D0", "D2"]
''')

# Collector contract: one candidate pass returns independent sibling failures together.
Path("tests/test_generation_validation_collector.py").write_text(r'''import copy

import pytest

from application.generation_repair import GenerationValidationError
from plc_generation import prepare_ladder_candidate
from plc_json_validator import (
    PLCJsonValidationAggregateError,
    collect_ladder_candidate_structure_errors,
)
from test_generation_repair_assembly import ladder40


def _multi_bad_candidate():
    candidate = ladder40(invalid=False)
    rung = candidate["rungs"][0]
    rung["debug_note"] = "x" * 100
    rung["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": "DDADDP", "operands": ["D0", "D2", "D4"], "label": None},
        {"type": "APP_INSTR", "opcode": "DDMOVP", "operands": ["D10", "D12"], "label": None},
    ]
    return candidate


def test_collector_reports_independent_sibling_failures_in_one_pass():
    errors = collect_ladder_candidate_structure_errors(_multi_bad_candidate(), "FX3U")
    assert len(errors) == 3
    texts = [str(error) for error in errors]
    assert any("debug_note" in text for text in texts)
    assert {getattr(error, "observed_opcode", None) for error in errors} >= {"DDADDP", "DDMOVP"}


def test_generation_raises_one_aggregate_with_all_collected_violations():
    with pytest.raises(PLCJsonValidationAggregateError) as rejected:
        prepare_ladder_candidate(_multi_bad_candidate(), plc_model="FX3U")
    wrapped = GenerationValidationError(
        [rejected.value], attempts=0, max_attempts=0, language="zh-CN", stop_reason="final_validation"
    )
    assert wrapped.diagnostics["violation_count"] == 3
    assert wrapped.diagnostics["truncated"] is False
    observed = {row.get("observed_opcode") for row in wrapped.diagnostics["violations"]}
    assert {"DDADDP", "DDMOVP"} <= observed
''', encoding="utf-8")

# End-to-end: one failed job exposes both errors, one user repair fixes both with no second model call.
with open("tests/test_user_confirmed_generation_repair.py", "a", encoding="utf-8") as handle:
    handle.write(r'''


class BatchModifierTypoProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("batch deterministic repair must not call provider again")
        payload = _ladder()
        payload["rungs"][0]["branches"][0]["outputs"] = [
            {"type": "APP_INSTR", "opcode": "DDADDP", "operands": ["D0", "D2", "D4"], "label": None},
            {"type": "APP_INSTR", "opcode": "DDMOVP", "operands": ["D10", "D12"], "label": None},
        ]
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


def test_user_sees_all_opcode_errors_once_and_one_repair_fixes_all(offline, tmp_path):
    provider = BatchModifierTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, {"model": "offline"}

    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state", model_factory=factory,
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "batch-opcode-repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "two calculations", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "bad-two-modifiers",
            "text": "two calculations", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        failed = client.get(f"/api/jobs/{first}").json()
        assert failed["status"] == "failed"
        observed = {row.get("observed_opcode") for row in failed["error_details"]["violations"]}
        assert {"DDADDP", "DDMOVP"} <= observed
        assert failed["error_details"]["violation_count"] >= 2
        assert len(provider.requests) == 1 and len(factory_calls) == 1

        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "fix-two-modifiers"})
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 1
        assert len(factory_calls) == 1
        snapshot = service.jobs._load(repair_job)["snapshot"]
        targets = snapshot["repair_plan"]["targets"]
        assert [target["deterministic_value"] for target in targets] == ["DADDP", "DMOVP"]
        assert service.projects.project(project)["version_count"] == 1
''')

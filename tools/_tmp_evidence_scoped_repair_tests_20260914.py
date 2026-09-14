from pathlib import Path


# Unit protocol tests.
path = Path("tests/test_field_patch_repair_protocol.py")
text = path.read_text(encoding="utf-8")
insert = r'''

def test_duplicate_modifier_opcode_is_repaired_deterministically():
    base = _base()
    output = base["rungs"][0]["branches"][0]["outputs"][0]
    output["opcode"] = "DDADDP"
    output["operands"] = ["D0", "D2", "D4"]
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
        "observed_opcode": "DDADDP",
    }], "FX3U")
    target = repair["target"]
    assert target["strategy"] == "deterministic"
    assert target["value_schema"]["enum"] == ["DADDP"]
    assert target["deterministic_value"] == "DADDP"
    assert target["context"]["candidate_basis"] == "modifier_normalization"
    result = apply(base, deterministic_response(_payload(repair)), repair)
    fixed = result["rungs"][0]["branches"][0]["outputs"][0]
    assert fixed["opcode"] == "DADDP"
    assert fixed["operands"] == ["D0", "D2", "D4"]


def test_ambiguous_opcode_unlocks_only_the_reported_field_with_small_enum():
    base = _base()
    output = base["rungs"][0]["branches"][0]["outputs"][0]
    output["opcode"] = "MOVQ"
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
        "observed_opcode": "MOVQ",
    }], "FX3U")
    target = repair["target"]
    assert target["strategy"] == "constrained_model"
    assert target["value_schema"]["enum"] == ["MOV", "MOVP"]
    assert target["context"]["immutable_operands"] == ["D0", "D1"]
    response = {
        "schema_version": 1,
        "mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "patches": [{"path": target["path"], "value": "MOVP"}],
    }
    result = apply(base, response, repair)
    fixed = result["rungs"][0]["branches"][0]["outputs"][0]
    assert fixed["opcode"] == "MOVP"
    assert fixed["operands"] == ["D0", "D1"]
'''
anchor = "\ndef test_long_debug_note_is_truncated_deterministically():\n"
if anchor not in text or "test_duplicate_modifier_opcode_is_repaired_deterministically" in text:
    raise SystemExit("field repair test marker mismatch")
path.write_text(text.replace(anchor, insert + anchor, 1), encoding="utf-8")


# End-to-end user-confirmed repair test: no second provider/factory call.
path = Path("tests/test_user_confirmed_generation_repair.py")
text = path.read_text(encoding="utf-8")
insert = r'''

class ModifierTypoProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("unique modifier repair must not call the provider")
        payload = _ladder()
        payload["rungs"][0]["branches"][0]["outputs"] = [{
            "type": "APP_INSTR", "opcode": "DDADDP",
            "operands": ["D0", "D2", "D4"], "label": None,
        }]
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


def test_user_confirmed_unique_opcode_repair_is_model_free(offline, tmp_path):
    provider = ModifierTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, {"model": "offline"}

    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state", model_factory=factory,
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "opcode-repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 triggers one calculation", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "bad-modifier",
            "text": "X0 triggers one calculation", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        failed = client.get(f"/api/jobs/{first}").json()
        assert failed["status"] == "failed"
        assert failed["error_details"]["violations"][0]["observed_opcode"] == "DDADDP"
        assert len(provider.requests) == 1 and len(factory_calls) == 1

        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "fix-modifier"})
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 1, "deterministic modifier repair must not call model"
        assert len(factory_calls) == 1, "deterministic modifier repair must not initialize provider"
        snapshot = service.jobs._load(repair_job)["snapshot"]
        target = snapshot["repair_plan"]["target"]
        assert target["strategy"] == "deterministic"
        assert target["deterministic_value"] == "DADDP"
        assert service.projects.project(project)["version_count"] == 1
'''
anchor = "\n\nclass FormatThenStructuralProvider:\n"
if anchor not in text or "test_user_confirmed_unique_opcode_repair_is_model_free" in text:
    raise SystemExit("user repair test marker mismatch")
path.write_text(text.replace(anchor, insert + anchor, 1), encoding="utf-8")

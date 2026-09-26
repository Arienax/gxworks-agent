"""Navigation anchors and review evidence must agree with the selected version."""
import copy

import pytest

from application.program_projection import explore_program, issue_cards
from plc.ir import build_plc_ir
from test_web_api import offline


def _program():
    return build_plc_ir({"device_comments": {"X0": "启动", "Y0": "电机"}, "rungs": [
        {"rung_id": rid, "header_element": None, "shared_inputs": [], "branches": [
            {"branch_id": 1, "y_offset_level": 0,
             "inputs": [{"type": "NO", "address": read}],
             "outputs": [{"type": "COIL", "address": write}]}]}
        for rid, read, write in [(7, "X0", "M0"), (42, "M0", "Y0")]]})


def test_navigation_uses_real_rung_ids_and_canonical_read_write_references():
    program = _program()
    before = copy.deepcopy(program)
    view = explore_program(program)
    assert [n["id"] for n in view["networks"]] == ["N0007", "N0042"]
    assert [n["bounds"]["display_number"] for n in view["networks"]] == [1, 2]
    assert view["devices"]["M0"]["read_by"] == ["N0042"]
    assert view["devices"]["M0"]["written_by"] == ["N0007"]
    assert {target["address"] for target in view["address_targets"]} == {"X0", "Y0", "M0"}
    assert all(0 <= n["bounds"]["top"] < view["height"] for n in view["networks"])
    assert program == before


def test_issue_cards_preserve_evidence_and_do_not_invent_missing_networks():
    finding = {"finding_id": "f1", "message": "Check stop", "rung_ids": [42],
               "network_refs": ["N9999"], "addresses": ["Y0"], "evidence": ["observed X0=1"]}
    cards = issue_cards(_program(), [finding], report_id="report1")
    assert cards[0]["id"] == "report1:f1"
    assert cards[0]["networks"] == ["N0042"]
    assert cards[0]["unresolved_networks"] == ["N9999"]
    assert cards[0]["evidence"] == finding["evidence"]
    assert issue_cards(_program(), [finding], report_id="report2")[0]["id"] != cards[0]["id"]


def test_combined_instruction_addresses_remain_clickable():
    from plc.ir import ir_to_ladder
    ladder = ir_to_ladder(_program())
    ladder["rungs"][0]["branches"][0]["outputs"][0] = {
        "type": "APP_INSTR", "opcode": "SET", "operands": ["M0"]}
    view = explore_program(build_plc_ir(ladder))
    first = view["networks"][0]["bounds"]
    assert any(target["address"] == "M0" and first["top"] <= target["y"] <= first["top"] + first["height"]
               for target in view["address_targets"])


def test_explorer_http_is_read_only_and_report_cannot_cross_versions(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from test_web_api import ORIGIN, _legacy_workspace, _login
    store, pid, vid, _ = _legacy_workspace(tmp_path / "workspace")
    report = store.create_report(pid, {"report_id": "review-fixture", "base_version_id": "another-version", "findings": []})
    before = {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()}
    app = create_app(store.base_dir, state_dir=tmp_path / "state", read_only=True, origin=ORIGIN, operator_token="operator-test")
    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        result = client.get(f"/api/projects/{pid}/versions/{vid}/explorer")
        assert result.status_code == 200, result.text
        assert result.json()["networks"][0]["id"]
        import xml.etree.ElementTree as ET
        assert ET.fromstring(result.json()["svg"]).tag == "{http://www.w3.org/2000/svg}svg"
        result = client.get(f"/api/projects/{pid}/versions/{vid}/issues?report_id={report['report_id']}")
        assert result.status_code == 400
    assert {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()} == before


def test_job_explorer_reuses_exact_saved_generation_and_refuses_corrupt_source(offline, tmp_path):
    import json
    from test_generation_delivery import prepared, generate
    service, client = prepared(tmp_path)
    with client:
        pid, jid, output, _ = generate(client, service)
        result = client.get(f"/api/jobs/{jid}/explorer?theme=light")
        assert result.status_code == 200, result.text
        assert result.json()["networks"][0]["id"] == "N0001"
        version_route = f"/api/projects/{pid}/versions/{output['version_id']}/explorer?theme=light"
        assert client.get(version_route).json() == result.json()
        assert service.projects.project(pid)["version_count"] == 1
        assert client.get("/api/jobs/missing/explorer").status_code == 404
        path = service.projects.artifact(pid, output["version_id"], "ir")
        program = json.loads(path.read_text(encoding="utf-8"))
        program["program_name"] = "TAMPERED"
        path.write_text(json.dumps(program), encoding="utf-8")
        assert client.get(version_route).status_code in (404, 409)
        for endpoint in ("issues", "simulation-workbench", "delivery"):
            assert client.get(f"/api/projects/{pid}/versions/{output['version_id']}/{endpoint}").status_code == 409


def test_wrapped_contacts_and_wide_instructions_keep_measured_navigation_anchors():
    import json
    import xml.etree.ElementTree as ET
    from plc.ir import canonical_sha256
    ladder = {"device_comments": {"M0": "公共运行条件" * 20}, "rungs": [
        {"rung_id": 42, "header_element": {"type": "NO", "address": "M0"},
         "shared_inputs": [{"type": "NO", "address": f"M{i}"} for i in range(1, 13)],
         "branches": [{"branch_id": 1, "y_offset_level": 0,
                       "inputs": [{"type": "NO", "address": f"M{i}"} for i in range(13, 24)],
                       "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K32767", "D7999"]}]}]}
    ]}
    program = build_plc_ir(ladder)
    before = json.dumps(program, sort_keys=True, ensure_ascii=False)
    view = explore_program(program)
    assert view["ir_sha256"] == canonical_sha256(program)
    assert json.dumps(program, sort_keys=True, ensure_ascii=False) == before
    bounds = view["networks"][0]["bounds"]
    assert bounds["raw_rung_id"] == 42 and bounds["display_number"] == 1
    root = ET.fromstring(view["svg"])
    text = next(t for t in root.iter("{http://www.w3.org/2000/svg}text")
                if t.text == "MOV K32767 D7999")
    anchor = next(a for a in view["address_targets"] if a["address"] == "D7999")
    # The operand is at the right end of a measured, centered instruction.
    text_right = float(text.get("x")) + float(text.get("textLength")) / 2
    assert abs(anchor["x"] + anchor["width"] - text_right - 3) < 0.01
    assert {a["address"] for a in view["address_targets"]} == {f"M{i}" for i in range(24)} | {"D7999"}
    assert all(0 <= a["x"] <= a["x"] + a["width"] <= view["width"]
               and bounds["top"] <= a["y"] < a["y"] + a["height"] <= bounds["top"] + bounds["height"]
               for a in view["address_targets"])

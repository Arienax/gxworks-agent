"""One physical device, one comment, one reference index through delivery."""
import copy
import csv
import itertools
import json
from pathlib import Path

from model_profile_fixtures import offline_runtime_profile
import pytest

from plc.device_identity import canonical_device, canonical_operand, canonical_ladder_devices
from plc.ir import build_plc_ir, validate_plc_ir, ir_to_ladder, canonical_sha256
from plc.generation import prepare_ladder_candidate, render_generation_artifacts
from application.program_projection import explore_program


def ladder_fixture(*, padded_logic=False, output="Y0"):
    pad = lambda s: s[0] + s[1:].zfill(3) if padded_logic else s
    return {"device_comments": {"x000": "启动按钮", "X001": "停止按钮", output[0] + output[1:].zfill(3): "电机接触器输出"},
            "rungs": [{"rung_id": 1, "header_element": None, "shared_inputs": [], "branches": [
                {"branch_id": 1, "y_offset_level": 0, "inputs": [
                    {"type": "NC", "address": pad("X1")},
                    {"type": "parallel_block", "branches": [[{"type": "NO", "address": pad("X0")}],
                                                              [{"type": "NO", "address": pad(output)}]]}],
                 "outputs": [{"type": "COIL", "address": pad(output)}]}]}]}


@pytest.mark.parametrize("value,expected", [("X000", "X0"), ("x001", "X1"), ("y001", "Y1"), ("X010", "X10"),
                                           ("M0005", "M5"), ("D0010", "D10"), ("T000", "T0"), ("C001", "C1"),
                                           ("SM08000", "SM8000"), ("SD0001", "SD1")])
def test_device_identity_is_not_an_octal_to_decimal_conversion(value, expected):
    assert canonical_device(value) == expected
    assert canonical_device(expected) == expected


@pytest.mark.parametrize("value", ["K0010", "H0010", "K4X000", '"X000"', "X00A", "myX000", "NO X000", None, False])
def test_non_device_tokens_are_not_reinterpreted(value):
    assert canonical_operand(value) == value


def test_known_indexed_operands_and_comparisons_keep_constants_and_labels():
    raw = ladder_fixture()
    branch = raw["rungs"][0]["branches"][0]
    branch["inputs"].append({"type": "COMPARE", "expression": ">= D0001Z00 K0010", "label": "Reference X001"})
    branch["outputs"].extend([{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K0010", "D0002Z00"]},
                              {"type": "BLOCK_OUTPUT", "expression": 'ASC "literal X001" D0010'}])
    result = canonical_ladder_devices(raw)
    fixed = result["rungs"][0]["branches"][0]
    assert fixed["inputs"][-1]["expression"] == ">= D1Z0 K0010"
    assert fixed["inputs"][-1]["label"] == "Reference X001"
    assert fixed["outputs"][-2]["operands"] == ["K0010", "D2Z0"]
    assert fixed["outputs"][-1]["expression"] == 'ASC "literal X001" D10'
    assert canonical_ladder_devices(result) == result


@pytest.mark.parametrize("padded_logic", [False, True])
@pytest.mark.parametrize("output", ["Y0", "Y1"])
def test_fresh_program_artifacts_and_explorer_share_device_identity(tmp_path, padded_logic, output):
    raw = ladder_fixture(padded_logic=padded_logic, output=output)
    before = copy.deepcopy(raw)
    spec = {"io_table": [{"kind": k[0].upper(), "address": k, "label": v} for k, v in raw["device_comments"].items()]}
    ready = prepare_ladder_candidate(raw, confirmed_spec=spec)
    program = ready["program_ir"]
    assert raw == before
    assert validate_plc_ir(program, validate_ladder=False) is program
    assert set(program["devices"]) == {"X0", "X1", output}
    assert set(program["io_map"]) == {"X0", "X1", output}
    assert program["devices"]["X0"]["comment"] == "启动按钮"
    assert program["devices"]["X1"]["comment"] == "停止按钮"
    assert program["devices"][output]["comment"] == "电机接触器输出"
    assert program["devices"]["X0"]["read_by"] == ["N0001"]
    assert program["devices"][output]["written_by"] == ["N0001"]
    assert ready["ladder"] == ir_to_ladder(program)
    view = explore_program(program)
    assert set(view["devices"]) == {"X0", "X1", output}
    assert {t["address"] for t in view["address_targets"]} == {"X0", "X1", output}
    rendered = render_generation_artifacts(program, tmp_path)
    for relative in rendered["artifacts"].values():
        assert (tmp_path / relative).stat().st_size > 0
    saved = json.loads((tmp_path / "ladder.json").read_text())
    assert set(saved["device_comments"]) == {"X0", "X1", output}
    with (tmp_path / "comments.csv").open(encoding="utf-16", newline="") as file:
        rows = list(csv.reader(file, delimiter="\t"))[2:]
    assert {r[0] for r in rows} == {"X000", "X001", output[0]+output[1:].zfill(3)}
    assert len(rows) == 3  # GX CSV has its own padding, not duplicate identities.
    svg = (tmp_path / "ladder.svg").read_text()
    assert "启动按钮" in svg and "停止按钮" in svg and "电机接触器输出" in svg


def test_old_saved_ir_is_projected_without_mutating_its_hash_or_original_contents():
    raw = ladder_fixture()
    old = build_plc_ir(raw, io_map={"X000": "启动", "X001": "停止", "Y000": "电机"}, _canonicalize_devices=False)
    assert len(old["devices"]) == 6  # Reproduce the exact legacy defect.
    snapshot, digest = copy.deepcopy(old), canonical_sha256(old)
    assert validate_plc_ir(old, validate_ladder=False) is old
    view = explore_program(old)
    assert old == snapshot and canonical_sha256(old) == digest
    assert view["ir_sha256"] == digest
    assert set(view["devices"]) == {"X0", "X1", "Y0"}
    assert view["devices"]["X1"]["read_by"] == ["N0001"]
    assert view["devices"]["X1"]["comment"] == "停止按钮"


def test_alias_comment_update_or_delete_targets_the_same_physical_device():
    from plc.ir import apply_network_patch
    base = build_plc_ir(ladder_fixture())
    rung = base["networks"][0]["ladder"]
    for comment in ("新的停止按钮注释", None, ""):
        updated = apply_network_patch(base, {"base_ir_sha256": canonical_sha256(base),
            "operations": [{"operation": "modify_network", "network": "N0001", "ladder": rung}],
            "device_comments": {"x001": comment}})
        comments = ir_to_ladder(updated)["device_comments"]
        assert "x001" not in comments and "X001" not in comments
        if comment is None:
            assert "X1" not in comments
        else:
            assert comments["X1"] == comment


def test_cpu_range_validation_is_not_bypassed_by_normalizing_padding():
    from plc.validation import PLCJsonValidationError
    raw = ladder_fixture()
    raw["rungs"][0]["branches"][0]["inputs"][0]["address"] = "X008"
    with pytest.raises(PLCJsonValidationError):
        prepare_ladder_candidate(raw, plc_model="FX3U")
    result = prepare_ladder_candidate(raw, plc_model="FX5U")
    assert "X8" in result["program_ir"]["devices"]


def test_alias_spec_rows_and_binding_values_coalesce_without_losing_roles():
    from plc.specification.confirmed import canonicalize_confirmed_spec
    from test_confirmed_compatibility import operator_spec
    raw = operator_spec()
    for p, address in zip(raw["parameters"], ("x000", "X001", "Y001")):
        p["value"] = address
    raw["io_table"] = [
        {"kind": "X", "address": "X000", "label": "启动按钮"},
        {"kind": "X", "address": "X0", "label": "启动按钮"},
        {"kind": "X", "address": "X001", "label": "停止按钮"},
        {"kind": "Y", "address": "Y001", "label": "电机接触器输出"}]
    spec = canonicalize_confirmed_spec(raw)
    assert {r["address"] for r in spec["io_table"]} == {"X0", "X1", "Y1"}
    assert len(spec["io_table"]) == 3
    assert {b["role"]: b["address"] for b in spec["io_bindings"]} == {"start": "X0", "stop": "X1", "output": "Y1"}
    assert canonicalize_confirmed_spec(spec) == spec


@pytest.mark.parametrize("representation", ["compact", "ladder_v1"])
@pytest.mark.parametrize("padded_logic", [True, False])
def test_http_analysis_confirmation_delivery_keeps_comments_without_bogus_vfd(tmp_path, monkeypatch, representation, padded_logic):
    import application.model_api as api
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from model_runtime.provider import SystemMessage, TextDelta
    from test_web_api import ORIGIN, OPERATOR, _login, _complete
    from test_hardware_intent_boundary import hallucinated_analysis
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "_build_model_context", lambda *a, **k: "")
    requests = []

    class Provider:
        profile = offline_runtime_profile()
        def stream(self, request):
            requests.append(request)
            if request.response_contract.name == "analysis":
                payload = hallucinated_analysis()
                # Labels and roles are declared bindings; guessed hardware
                # addresses in suggested_io are intentionally not authoritative.
                bindings = {
                    "start_input": ("start", "X", "启动按钮"),
                    "stop_input": ("stop", "X", "停止按钮"),
                    "output_address": ("output", "Y", "电机接触器输出"),
                }
                for question in payload["missing_info"]:
                    identity = question["id"]
                    if identity in bindings:
                        role, kind, label = bindings[identity]
                        question["io_binding"] = {"binding_id": identity, "role": role, "kind": kind, "label": label}
                # This is a fresh provider response, not a legacy saved snapshot.
                for approach in payload["approaches"]:
                    contract = approach.pop("generation_contract")
                    approach["implementation_semantics"] = [
                        {"kind": "structure", "status": status, "value": value}
                        for status in ("required", "forbidden")
                        for value in contract.get(status + "_structures", [])
                    ]
            else:
                assert len(requests) == 2  # one analysis, one generation, no probes
                prompt = next(m.content for m in request.messages if isinstance(m, SystemMessage))
                spec, _ = json.JSONDecoder().raw_decode(prompt.split("# Confirmed project specification\n", 1)[1])
                assert not spec["hardware_requirements"]["vfd"]
                assert {r["address"] for r in spec["io_table"]} == {"X0", "X1", "Y0"}
                assert not any(p["id"] == "control_method" for p in spec["parameters"])
                if representation == "ladder_v1":
                    payload = ladder_fixture(padded_logic=padded_logic)
                else:
                    pad = lambda s: s[0]+s[1:].zfill(3) if padded_logic else s
                    payload = {"r": [{"s": None, "b": [{"i": ["NC "+pad("X1"),
                        {"or": [["NO "+pad("X0")], ["NO "+pad("Y0")]]}], "o": ["COIL "+pad("Y0")]}]}]}
            raw = json.dumps(payload, ensure_ascii=False)
            for offset in range(0, len(raw), 11):
                yield TextDelta(raw[offset:offset+11])

    provider = Provider()
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", model_factory=lambda: (provider, provider.profile))
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service, origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", headers=headers, json={"name": "alias regression", "plc_model": "FX3U"}).json()["id"]
        _, analysis = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "analysis", "project_id": project, "request_id": "analyze", "text": "起保停", "response_language": "zh-CN"}))
        draft = analysis["spec_draft"]
        assert [p["id"] for p in draft["parameters"]] == ["start_input", "stop_input", "output_address"]
        for p, address in zip(draft["parameters"], ("X000", "X001", "Y000")):
            p.update(value=address, source="user")
        confirmation = client.put(f"/api/projects/{project}/spec", headers=headers, json={"expected_hash": None, "spec": draft})
        assert confirmation.status_code == 200 and confirmation.json()["valid"], confirmation.text
        assert len(requests) == 1
        _, output = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "generation", "project_id": project, "request_id": "generate", "text": "按确认规格生成", "response_language": "zh-CN"}))
        assert output["status"] == "saved" and len(requests) == 2
        version = output["version_id"]
        view = client.get(f"/api/projects/{project}/versions/{version}/explorer").json()
        assert set(view["devices"]) == {"X0", "X1", "Y0"}
        assert view["devices"]["X0"]["comment"] == "启动按钮"
        assert view["devices"]["X1"]["comment"] == "停止按钮"
        assert view["devices"]["Y0"]["comment"] == "电机接触器输出"
        assert view["devices"]["X1"]["read_by"] == ["N0001"]
        assert view["devices"]["Y0"]["written_by"] == ["N0001"]
        for kind in ("json", "ir", "svg", "program_csv", "comment_csv"):
            assert service.projects.artifact(project, version, kind).stat().st_size > 0


def test_typed_semantic_and_analysis_addresses_use_the_same_identity():
    raw = ladder_fixture(padded_logic=True)
    requirement = {"id": "keep-literal-X001", "semantic": "LEVEL", "devices": ["X001"],
                   "description": "Physical X001 contact", "source": "confirmed_spec"}
    config = {"same_scan_expectations": [{"device": "Y000", "reader_network": "N0001"}],
              "terminal_states": {"D0001": [2, 3]}}
    before = copy.deepcopy((requirement, config))
    result = build_plc_ir(raw, semantic_requirements=[requirement], analysis_config=config)
    assert result["logic"]["requirements"][0]["devices"] == ["X1"]
    assert result["analysis"]["config"]["same_scan_expectations"][0]["device"] == "Y0"
    assert set(result["analysis"]["config"]["terminal_states"]) == {"D1"}
    assert (requirement, config) == before
    validate_plc_ir(result, validate_ladder=False)


def test_scoped_patch_keeps_unmodified_legacy_networks_byte_equivalent():
    from plc.ir import apply_network_patch
    source = ladder_fixture(padded_logic=True)
    second = copy.deepcopy(source["rungs"][0])
    second["rung_id"] = 2
    second["branches"][0]["outputs"][0]["address"] = "Y001"
    source["rungs"].append(second)
    legacy = build_plc_ir(source, _canonicalize_devices=False)
    untouched = copy.deepcopy(legacy["networks"][1])
    first = copy.deepcopy(source["rungs"][0])
    first["branches"][0]["outputs"][0]["label"] = "Edited note only"
    revised = apply_network_patch(legacy, {"base_ir_sha256": canonical_sha256(legacy),
        "operations": [{"operation": "modify_network", "network": "N0001", "ladder": first}]})
    assert revised["networks"][1] == untouched

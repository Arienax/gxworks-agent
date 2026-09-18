"""Confirmation-to-artifact regressions; no live models, GX, or physical PLCs."""
from __future__ import annotations

import copy
import itertools
import json
from types import SimpleNamespace

import pytest

from plc.specification.confirmed import canonicalize_confirmed_spec, validate_spec_draft, build_review_draft
from application.compact_protocol import (
    CompactProtocolError, compact_response_schema, decode_compact,
    normalize_compact, expand_compact_ladder,
)
from model_runtime.contract import CapabilityContract, ParameterDescriptor, CapabilityDescriptor, contract_scope
from model_runtime.response_format import response_plan
from model_runtime.request_policy import resolve_request


def operator_spec(polarity="NO"):
    # Synthetic equivalent of the uploaded failure, without endpoints, tokens,
    # project IDs, paths, or captured engineering transcript.
    parameters = [
        ("start_input", "启动按钮或启动信号接哪个X输入？", "X0（建议）"),
        ("stop_input", "停止按钮或停止信号接哪个X输入？", "X1（建议）"),
        ("output_address", "被控负载输出接哪个Y输出？", "Y0（建议）"),
        ("stop_contact_polarity", "停止按钮或停止信号是常开触点还是常闭触点？",
         "常开：未按下时断开，按下时接通" if polarity == "NO" else "常闭：未按下时接通，按下时断开"),
        ("start_mode", "启动方式", "保持闭合即有效（普通起保停）"),
        ("fault_stop_input", "是否另加故障输入？", "不需要"),
    ]
    return {
        "schema_version": 3, "plc_model": "FX3U", "summary": "基本起保停，停止优先。",
        "io_table": [], "io_allocation_raw": "", "missing_answers": {},
        "parameters": [{"id": identifier, "name": name, "value": value, "source": "user", "required": True}
                       for identifier, name, value in parameters],
        "selected_approach": {"approach_id": "direct_self_hold", "name": "直接自保持逻辑",
            "generation_contract": {"schema_version": 1, "required_structures": ["direct_logic", "self_hold"],
                "forbidden_structures": [], "required_opcodes": [], "forbidden_opcodes": [],
                "required_devices": [], "forbidden_devices": [], "enforce": True, "source": "explicit"}},
    }


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
@pytest.mark.parametrize("output_id", ["output_address", "output_coil"])
def test_empty_table_confirmation_keeps_all_roles_for_every_order(order, output_id):
    spec = operator_spec()
    spec["parameters"][2]["id"] = output_id
    spec["parameters"] = [spec["parameters"][i] for i in order] + spec["parameters"][3:]
    before = copy.deepcopy(spec)
    assert not validate_spec_draft(spec)["errors"]
    result = canonicalize_confirmed_spec(spec)
    assert spec == before
    assert {row["address"] for row in result["io_table"]} == {"X0", "X1", "Y0"}
    bindings = {row["role"]: row["address"] for row in result["io_bindings"]}
    assert bindings == {"start": "X0", "stop": "X1", "output": "Y0"}
    assert "启动" in next(r["label"] for r in result["io_table"] if r["address"] == "X0")
    assert "停止" in next(r["label"] for r in result["io_table"] if r["address"] == "X1")
    assert not validate_spec_draft(result)["errors"]
    assert canonicalize_confirmed_spec(result) == result


def test_address_swapping_changes_addresses_not_binding_meanings():
    spec = operator_spec()
    spec["io_table"] = [{"kind": "X", "address": "X0", "label": "启动按钮"},
                        {"kind": "X", "address": "X1", "label": "停止按钮"},
                        {"kind": "Y", "address": "Y0", "label": "控制输出"}]
    spec["parameters"][0]["value"] = "X1"
    spec["parameters"][1]["value"] = "X0"
    result = canonicalize_confirmed_spec(spec)
    assert [(r["label"], r["address"]) for r in result["io_table"]][:2] == [("启动按钮", "X1"), ("停止按钮", "X0")]
    assert canonicalize_confirmed_spec(result) == result


def test_distinct_machines_with_same_question_label_do_not_merge():
    spec = {"plc_model": "FX3U", "io_table": [], "parameters": [
        {"id": "motor_1_start", "name": "启动输入", "value": "X0", "source": "user",
         "io_binding": {"binding_id": "motor_1.start", "kind": "X", "role": "start"}},
        {"id": "motor_2_start", "name": "启动输入", "value": "X2", "source": "user",
         "io_binding": {"binding_id": "motor_2.start", "kind": "X", "role": "start"}},
    ]}
    result = canonicalize_confirmed_spec(spec)
    assert {r["binding_id"]: r["address"] for r in result["io_table"]} == {"motor_1.start": "X0", "motor_2.start": "X2"}
    assert len(result["io_bindings"]) == 2
    assert canonicalize_confirmed_spec(result) == result


def test_structured_rows_and_direct_user_edits_remain_authoritative():
    from application.generation_agent import _strict_generation_projection
    result = canonicalize_confirmed_spec(operator_spec())
    next(r for r in result["io_table"] if r["binding_id"] == "legacy_start")["address"] = "X2"
    result["io_allocation_raw"] = "X: X0=obsolete display text"
    result = canonicalize_confirmed_spec(result)
    assert {r["address"] for r in result["io_table"]} == {"X2", "X1", "Y0"}
    projected = _strict_generation_projection(result)
    assert next(b["address"] for b in projected["io_bindings"] if b["role"] == "start") == "X2"
    assert all("value" not in b for b in projected["io_bindings"])
    assert canonicalize_confirmed_spec(result) == result


def test_legacy_text_can_be_imported_but_hardware_prose_is_not_an_io_answer():
    spec = {"plc_model": "FX3U", "io_allocation_raw": "X: X0=启动按钮, X1=停止按钮\nY: Y0=控制输出",
            "missing_answers": {"启动按钮接哪个输入点？": "X2"},
            "parameters": [{"id": "cpu", "name": "CPU型号", "value": "FX3U-32MT/ES-A"},
                           {"id": "notes", "name": "说明", "value": "Do not turn D8260 into an I/O assignment"}]}
    result = canonicalize_confirmed_spec(spec)
    assert {r["address"] for r in result["io_table"]} == {"X2", "X1", "Y0"}
    assert {p["id"] for p in result["parameters"]} == {"cpu", "notes"}
    assert result["missing_answers"] == {}


def test_existing_damaged_spec_is_not_silently_rewritten_from_old_audit():
    damaged = {"plc_model": "FX3U", "io_table": [{"kind": "X", "address": "X1", "label": "启动按钮或启动信号接X输入"},
               {"kind": "Y", "address": "Y0", "label": "被控负载输出接Y输出"}], "parameters": [],
               "io_overrides_applied": {"启动按钮或启动信号接哪个X输入？": "X0（建议）", "停止按钮或停止信号接哪个X输入？": "X1（建议）"}}
    unchanged = canonicalize_confirmed_spec(damaged)
    assert {r["address"] for r in unchanged["io_table"]} == {"X1", "Y0"}
    # Explicit reconfirmation restores the complete allocation without creating
    # a new project or discarding the existing source rows.
    damaged["parameters"] = operator_spec()["parameters"]
    restored = canonicalize_confirmed_spec(damaged)
    assert {r["address"] for r in restored["io_table"]} == {"X0", "X1", "Y0"}


def _compact(s="omit", polarity="NO"):
    row = {"h": None, "b": [{"i": ["NC X1" if polarity == "NO" else "NO X1",
                                    {"or": [["NO X0"], ["NO Y0"]]}], "o": ["COIL Y0"]}]}
    if s != "omit":
        row["s"] = s
    return {"r": [row]}


@pytest.mark.parametrize("shared", ["omit", [], None])
def test_wire_variants_expand_to_identical_control_logic(shared):
    source = _compact(shared)
    before = copy.deepcopy(source)
    normalized, changes = normalize_compact(source)
    assert source == before and normalize_compact(normalized) == (normalized, [])
    assert len(changes) == (1 if shared is None else 0)
    assert expand_compact_ladder(source) == expand_compact_ladder(_compact([]))


@pytest.mark.parametrize("path,bad", [("s", False), ("s", 0), ("s", ""), ("s", {}),
                                      ("i", None), ("o", None), ("o", [])])
def test_non_equivalent_values_remain_local_protocol_failures(path, bad):
    source = _compact([])
    (source["r"][0] if path == "s" else source["r"][0]["b"][0])[path] = bad
    with pytest.raises(CompactProtocolError):
        expand_compact_ladder(source)


@pytest.mark.parametrize("raw", ['{"r":[],"r":[]}', '{"r": [}', '[]', '{"r":NaN}', '{"r":[]} {}'])
def test_json_parser_never_discards_ambiguous_content(raw):
    with pytest.raises(CompactProtocolError):
        decode_compact(raw)


def test_strict_schema_has_required_fields_and_disjoint_anyof():
    import jsonschema
    schema = compact_response_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            assert set(node["required"]) == set(node["properties"])
            assert node["additionalProperties"] is False
        assert "oneOf" not in node
        for value in node.values():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
    walk(schema)
    jsonschema.validate(_compact([]), schema)


def profile_for(mode, streaming=True, model="synthetic-model"):
    profile = {"id": "fixture", "adapter": "openai_compatible", "baseUrl": "https://fixture.invalid/v1",
               "model": model, "capabilities": {}, "generationDefaults": {}, "requestOverrides": {}}
    params = {"temperature": ParameterDescriptor.from_dict("temperature", {"type": "number", "status": "supported", "source": "manual"})}
    caps = {"structured_output": CapabilityDescriptor.from_dict({"status": "supported" if mode else "unsupported", "source": "manual", "modes": [mode] if mode else []}),
            "streaming": CapabilityDescriptor.from_dict({"status": "supported" if streaming else "unsupported", "source": "manual"})}
    scope = contract_scope(profile, params, api_key="offline-fixture-key")
    profile["capabilityContract"] = CapabilityContract(scope, caps, params, {}).to_dict()
    profile["userModelSettings"] = {"scope": scope, "parameters": {"temperature": {"mode": "value", "value": .733}}}
    return profile


@pytest.mark.parametrize("mode", ["json_schema", "json_object", None])
@pytest.mark.parametrize("streaming", [True, False])
def test_response_plan_uses_scoped_capabilities_without_mutating_user_values(mode, streaming):
    profile = profile_for(mode, streaming)
    before = copy.deepcopy(profile)
    options, actual_stream = response_plan(profile, compact_response_schema(), api_key="offline-fixture-key")
    assert actual_stream is streaming
    assert (options["response_format"] or {}).get("type") == mode
    resolved = resolve_request(profile, protocol={**options, "stream": actual_stream}, api_key="offline-fixture-key")
    assert resolved.options["temperature"] == .733 and profile == before


def _truth_table(ladder, polarity="NO"):
    def evaluate(item, state):
        if item is None:
            return True
        if item["type"] == "parallel_block":
            return any(all(evaluate(child, state) for child in path) for path in item["branches"])
        assert item["type"] in {"NO", "NC"}
        return state[item["address"]] if item["type"] == "NO" else not state[item["address"]]
    for start, stop, previous in itertools.product((False, True), repeat=3):
        state = {"X0": start, "X1": stop, "Y0": previous}
        for rung in ladder["rungs"]:
            prefix = evaluate(rung.get("header_element"), state) and all(evaluate(i, state) for i in rung["shared_inputs"])
            for branch in rung["branches"]:
                enabled = prefix and all(evaluate(i, state) for i in branch["inputs"])
                for output in branch["outputs"]:
                    assert output["type"] == "COIL"
                    state[output["address"]] = enabled
        assert state["Y0"] == ((start or previous) and (not stop if polarity == "NO" else stop))


class SDKFixture:
    """A minimal SDK-shaped endpoint, not a live service or a replacement pipeline."""
    def __init__(self, shared, polarity, *, malformed=False, representation="compact"):
        self.calls = []
        self.representation = representation
        self.shared, self.polarity, self.malformed = shared, polarity, malformed
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda: pytest.fail("generation must not trigger discovery"))

    def with_options(self, **options):
        assert options.get("max_retries", 0) == 0
        return self

    def create(self, **params):
        self.calls.append(copy.deepcopy(params))
        assert len(self.calls) == 1, "no hidden model regeneration"
        assert params["temperature"] == .733
        system = next(m["content"] for m in params["messages"] if m["role"] == "system")
        encoded = system.split("# Confirmed project specification\n", 1)[1]
        spec, _ = json.JSONDecoder().raw_decode(encoded)
        assert {b["role"]: b["address"] for b in spec["io_bindings"]} == {"start": "X0", "stop": "X1", "output": "Y0"}
        assert {r["address"] for r in spec["io_table"]} == {"X0", "X1", "Y0"}
        content = _compact(self.shared, self.polarity)
        if self.malformed:
            content["r"][0]["b"][0]["i"] = None
        if self.representation == "ladder_v1":
            content = expand_compact_ladder(content)
        elif self.representation == "compact_alias":
            branch = content["r"][0]["b"][0]
            content = {"rungs": [{"branches": [{"inputs": branch["i"], "outputs": branch["o"]}]}]}
        raw = json.dumps(content, ensure_ascii=False)
        if params["stream"]:
            # Deliberately split inside tokens/arrays, not just whole objects.
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[i:i+3]), finish_reason=None)])
                         for i in range(0, len(raw), 3)])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason="stop")])


@pytest.mark.parametrize("mode", ["json_schema", "json_object", None])
@pytest.mark.parametrize("shared", ["omit", [], None])
@pytest.mark.parametrize("polarity", ["NO", "NC"])
@pytest.mark.parametrize("representation", ["compact", "ladder_v1", "compact_alias"])
def test_http_confirmation_to_saved_ladder_svg_and_csv(tmp_path, mode, shared, polarity, representation):
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from application.workbench import WorkbenchService
    from model_runtime.provider import OpenAICompatibleProvider
    from test_web_api import ORIGIN, OPERATOR, _login, _complete
    from plc.ir import ir_to_ladder
    sdk = SDKFixture(shared, polarity, representation=representation)
    # The no-schema case also exercises a declared non-streaming endpoint.
    profile = profile_for(mode, streaming=mode is not None, model="fixture-" + str(mode))
    provider = OpenAICompatibleProvider(profile, "offline-fixture-key", client=sdk)
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": profile["model"]}))
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post("/api/projects", headers=headers, json={"name": "synthetic compatibility", "target_mode": "ladder", "plc_model": "FX3U"})
        assert response.status_code == 201, response.text
        pid = response.json()["id"]
        confirmation = client.put(f"/api/projects/{pid}/spec", headers=headers,
                                  json={"spec": operator_spec(polarity), "expected_hash": None})
        assert confirmation.status_code == 200 and confirmation.json()["valid"], confirmation.text
        assert sdk.calls == []
        # Read persisted data back through HTTP, then exercise the real job,
        # provider serializer, compact expansion, IR, rendering and autosave.
        reread = client.get(f"/api/projects/{pid}").json()["confirmed_spec"]
        assert {r["address"] for r in reread["io_table"]} == {"X0", "X1", "Y0"}
        response = client.post("/api/jobs", headers=headers, json={"kind": "generation", "project_id": pid,
            "version_id": None, "request_id": "one-confirmed-request", "text": "按确认规格生成", "response_language": "zh-CN", "attachment_ids": []})
        job_id, output = _complete(client, service, response)
        assert output["status"] == "saved", output
        vid = output["version_id"]
        program = service.projects.program(pid, vid)
        _truth_table(ir_to_ladder(program), polarity)
        assert service.projects.artifact(pid, vid, "svg").read_text(encoding="utf-8").lstrip().startswith("<")
        for artifact in ("json", "ir", "program_csv", "comment_csv"):
            assert service.projects.artifact(pid, vid, artifact).stat().st_size > 0
        assert len(sdk.calls) == 1
        assert (sdk.calls[0].get("response_format") or {}).get("type") == mode
        # Idempotent HTTP retry is not a second model invocation or version.
        again = client.post("/api/jobs", headers=headers, json={"kind": "generation", "project_id": pid,
            "version_id": None, "request_id": "one-confirmed-request", "text": "按确认规格生成", "response_language": "zh-CN", "attachment_ids": []})
        assert again.json()["id"] == job_id and len(sdk.calls) == 1


def test_null_normalization_never_invents_a_missing_stop_condition():
    from plc.confirmed_checks import check_direct_self_hold
    from plc.validation import PLCJsonValidationError
    # Synthetic negative equivalent: this is NOT a golden correct start/stop
    # candidate, even though its JSON syntax and optional-null form are valid.
    bad = {"r": [{"s": None, "b": [{"i": [{"or": [["NO X1"], ["NO Y0"]]}], "o": ["COIL Y0"]}]}]}
    ladder = expand_compact_ladder(bad)
    with pytest.raises(PLCJsonValidationError):
        check_direct_self_hold(ladder, canonicalize_confirmed_spec(operator_spec()))
    with pytest.raises(AssertionError):
        _truth_table(ladder)


def test_local_compact_error_retains_path_and_is_not_an_api_error():
    from application.base import model_call
    from application.job_errors import generation_error_details, workflow_error_code
    from integrations.web.responses import JobErrorDetails
    failure = CompactProtocolError("r[0].b[0].i: invalid sk-private-fixture")
    def local():
        raise failure
    with pytest.raises(CompactProtocolError) as caught:
        model_call(local)
    assert caught.value is failure
    assert workflow_error_code(failure) == "generation_failed"
    detail = generation_error_details(failure)
    assert detail["contract_name"] == "compact_ladder"
    assert detail["violations"][0]["path"] == "content.r.0.b.0.i"
    JobErrorDetails.model_validate(detail)
    assert "sk-private-fixture" not in str(failure) + json.dumps(detail)


def test_unsupported_boolean_shapes_do_not_gain_a_new_semantic_gate():
    from plc.confirmed_checks import check_direct_self_hold
    spec = canonicalize_confirmed_spec(operator_spec())
    ladder = expand_compact_ladder(_compact([]))
    ladder["rungs"][0]["branches"][0]["inputs"][0] = {"type": "COMPARE", "expression": "> D0 K1"}
    assert check_direct_self_hold(ladder, spec)["status"] == "not_covered"


def test_format_selection_preserves_zero_false_omission_and_inheritance():
    profile = profile_for("json_object")
    profile["capabilityContract"]["parameters"]["custom_flag"] = ParameterDescriptor.from_dict(
        "custom_flag", {"type": "boolean", "status": "supported", "source": "manual"}).to_dict()
    profile["userModelSettings"]["parameters"]["custom_flag"] = {"mode": "value", "value": False}
    profile["userModelSettings"]["parameters"]["temperature"] = {"mode": "value", "value": 0}
    options, streaming = response_plan(profile, compact_response_schema(), api_key="offline-fixture-key")
    resolved = resolve_request(profile, {"temperature": .5}, protocol={**options, "stream": streaming}, api_key="offline-fixture-key")
    assert resolved.options["temperature"] == 0 and resolved.options["custom_flag"] is False
    profile["userModelSettings"]["parameters"]["temperature"] = {"mode": "omit"}
    resolved = resolve_request(profile, {"temperature": .5}, protocol={**options, "stream": streaming}, api_key="offline-fixture-key")
    assert "temperature" not in resolved.options
    profile["userModelSettings"]["parameters"]["temperature"] = {"mode": "inherit"}
    resolved = resolve_request(profile, {"temperature": .37}, protocol={**options, "stream": streaming}, api_key="offline-fixture-key")
    assert resolved.options["temperature"] == .37


def test_scoped_contract_overrides_legacy_format_flags_without_model_name_rules():
    profile = profile_for("json_object")
    profile["capabilities"]["json_schema_response_format"] = True
    options, _ = response_plan(profile, compact_response_schema(), api_key="offline-fixture-key")
    assert options["response_format"] == {"type": "json_object"}


def test_known_ladder_representations_never_bypass_validation():
    from application.generation_agent import _decode_generated_ladder
    from plc.validation import PLCJsonValidationError
    good = expand_compact_ladder(_compact([]))
    bad = copy.deepcopy(good)
    bad["rungs"][0]["branches"][0]["outputs"][0]["address"] = "NOT_A_DEVICE"
    with pytest.raises(PLCJsonValidationError):
        _decode_generated_ladder(bad, {}, "FX3U")
    for value in ({"r": [], "rungs": []}, {"wrapper": good}, {**good, "unknown_field": "X1"}):
        with pytest.raises(CompactProtocolError):
            _decode_generated_ladder(value, {}, "FX3U")

"""Candidate controls retain user choices across analysis, review and storage."""

import copy

from plc.specification.confirmed import (
    build_review_draft,
    canonicalize_confirmed_spec,
    restore_review_choices,
    validate_spec_draft,
)


def _analysis():
    return {
        "summary": "启动保持停止",
        "plc_model": "FX3U",
        "missing_info": [
            {"id": "start_input", "question": "启动按钮接哪个输入？", "required": True},
            {"id": "stop_input", "question": "停止按钮接哪个输入？（常开还是常闭）", "required": True},
            {"id": "output_coil", "question": "控制对象输出用哪个Y？", "required": True},
        ],
        "suggested_io": {"X": {"X0": "启动按钮", "X1": "停止按钮"}, "Y": {"Y0": "控制输出"}},
    }


def test_analysis_choices_and_suggestion_survive_merge_and_canonicalization():
    analysis = {"missing_info": [{"id": "interface", "question": "输出接口", "options": ["0-10V / 4-20mA", "RS-485"],
                                  "default": "RS-485", "required": True}]}
    draft = build_review_draft(analysis)
    parameter = draft["parameters"][0]
    assert parameter["options"] == ["0-10V / 4-20mA", "RS-485"]
    assert parameter["suggested_default"] == "RS-485"
    assert parameter["value"] == ""
    assert "required_parameter_missing" in {issue["code"] for issue in validate_spec_draft(draft)["errors"]}

    parameter.update(value="用户自定义接口", source="user")
    merged = build_review_draft(analysis, draft)
    canonical = canonicalize_confirmed_spec(merged)
    assert canonical["parameters"][0] == parameter
    assert canonicalize_confirmed_spec(canonical)["parameters"] == canonical["parameters"]


def test_historical_choices_match_stable_id_before_wording_and_keep_user_answers():
    analysis = {"missing_info": [
        {"id": "interface", "question": "新的问题措辞", "options": ["FX3U-32MT/ES-A", "其他"], "default": "其他"},
        {"question": "定时方式", "options": ["每次启动", "每日一次"]},
    ]}
    draft = {"parameters": [
        {"id": "interface", "name": "原来的问题措辞", "value": "手动填写", "source": "user"},
        {"name": "定时方式", "value": "每日一次"},
    ]}
    before = copy.deepcopy(draft)
    restored = restore_review_choices(draft, analysis)
    assert draft == before
    assert restored["parameters"][0]["options"] == ["FX3U-32MT/ES-A", "其他"]
    assert restored["parameters"][0]["value"] == "手动填写"
    assert restored["parameters"][0]["source"] == "user"
    assert restored["parameters"][1]["options"] == ["每次启动", "每日一次"]


def test_missing_model_choices_offer_only_allocated_addresses_without_assuming_polarity():
    draft = build_review_draft(_analysis())
    start, stop, output = draft["parameters"]
    assert start["options"] == ["X0", "X1"]
    assert start["suggested_default"] == "X0"
    assert stop["options"] == ["X1，常开", "X1，常闭", "X0，常开", "X0，常闭"]
    assert "suggested_default" not in stop
    assert output["options"] == ["Y0"]
    assert [parameter["value"] for parameter in draft["parameters"]] == ["", "", ""]

    for parameter in draft["parameters"]:
        parameter.pop("options", None)
        parameter.pop("suggested_default", None)
    old_analysis = _analysis()
    old_analysis["io_allocation"] = old_analysis.pop("suggested_io")
    draft.pop("io_table")
    restored = restore_review_choices(draft, old_analysis)
    assert restored["parameters"][1]["options"] == ["X1，常开", "X1，常闭", "X0，常开", "X0，常闭"]


def test_polarity_only_answers_bind_to_predeclared_io_without_reasking_address():
    analysis = {
        "plc_model": "FX3U",
        "summary": "sorting station",
        "suggested_io": {"X": {"X001": "启动按钮", "X003": "停止按钮"}},
        "missing_info": [
            {
                "id": "start_input",
                "question": "启动按钮 X001 使用常开还是常闭触点？",
                "required": True,
                "options": ["常开（按下为 ON）", "常闭（按下为 OFF）"],
            },
            {
                "id": "stop_input",
                "question": "停止按钮 X003 使用常开还是常闭触点？",
                "required": True,
                "options": ["常开（按下为 ON）", "常闭（按下为 OFF）"],
            },
        ],
    }
    draft = build_review_draft(analysis)
    draft["parameters"][0].update(value="常开（按下为 ON）", source="user")
    draft["parameters"][1].update(value="常闭（按下为 OFF）", source="user")
    assert validate_spec_draft(draft)["errors"] == []

    canonical = canonicalize_confirmed_spec(draft)
    assert {row["address"] for row in canonical["io_table"]} == {"X1", "X3"}
    by_role = {item["role"]: item for item in canonical["io_bindings"]}
    assert by_role["start"]["address"] == "X1"
    assert by_role["start"]["active_level"] == 1
    assert by_role["stop"]["address"] == "X3"
    assert by_role["stop"]["active_level"] == 0
    assert [(p["id"], p["value"]) for p in canonical["parameters"]] == [
        ("start_input", "常开（按下为 ON）"),
        ("stop_input", "常闭（按下为 OFF）"),
    ]


def test_address_and_contact_answer_remains_explicit_across_canonicalization():
    draft = build_review_draft(_analysis())
    for parameter, value in zip(draft["parameters"], ["X2", "X1，常闭", "Y0"]):
        parameter.update(value=value, source="user")
    assert validate_spec_draft(draft)["errors"] == []
    canonical = canonicalize_confirmed_spec(draft)
    assert {row["address"] for row in canonical["io_table"]} == {"X2", "X1", "Y0"}
    assert [(parameter["id"], parameter["value"]) for parameter in canonical["parameters"]] == [("stop_input", "X1，常闭")]
    assert canonicalize_confirmed_spec(canonical)["parameters"] == canonical["parameters"]


def test_address_alone_does_not_answer_a_combined_contact_question():
    draft = build_review_draft(_analysis())
    for parameter, value in zip(draft["parameters"], ["X0", "X1", "Y0"]):
        parameter["value"] = value
    errors = validate_spec_draft(draft)["errors"]
    assert any(issue["code"] == "contact_type_missing" and issue["path"] == "$.parameters[1].value" for issue in errors)


def test_unallocated_or_non_address_questions_do_not_gain_invented_options():
    analysis = {"missing_info": [
        {"id": "start_input", "question": "启动按钮接哪个输入？"},
        {"id": "mode", "question": "运行模式？"},
    ]}
    draft = build_review_draft(analysis)
    assert all(parameter["options"] == [] for parameter in draft["parameters"])


def _purpose_analysis():
    """Question prose and a device purpose are deliberately different fields."""
    return {
        "plc_model": "FX3U", "summary": "起保停", "suggested_io": {},
        "missing_info": [
            {"id": "start_device", "question": "启动按钮接在哪个输入点，程序中使用何种触点极性？",
             "required": True, "options": ["X000", "自定义"],
             "io_binding": {"binding_id": "machine.start", "kind": "X", "role": "start", "label": "启动按钮"}},
            {"id": "stop_device", "question": "停止按钮接在哪个输入点，常开还是常闭？",
             "required": True, "options": ["X001，常闭", "自定义"],
             "io_binding": {"binding_id": "machine.stop", "kind": "X", "role": "stop", "label": "停止按钮"}},
            {"id": "output_device", "question": "输出（执行元件）接在哪个输出点？",
             "required": True, "options": ["Y000", "自定义"],
             "io_binding": {"binding_id": "machine.output", "kind": "Y", "role": "output", "label": "控制输出"}},
        ],
    }


def _purpose_draft():
    from application.analysis_results import _normalize_analysis_result
    analysis = _normalize_analysis_result(_purpose_analysis(), user_text="起保停")
    draft = build_review_draft(analysis)
    for parameter, value in zip(draft["parameters"], ["X000", "X001，常闭", "Y000"]):
        parameter.update(value=value, source="user")
    return draft


def test_io_purpose_is_preserved_without_turning_suggestions_into_answers():
    analysis = _purpose_analysis()
    original = copy.deepcopy(analysis)
    draft = build_review_draft(analysis)
    assert draft["io_table"] == []
    assert [p["value"] for p in draft["parameters"]] == ["", "", ""]
    assert [p["io_binding"]["label"] for p in draft["parameters"]] == ["启动按钮", "停止按钮", "控制输出"]
    assert [p["name"] for p in draft["parameters"]] == [p["question"] for p in analysis["missing_info"]]
    assert analysis == original
    assert any(i["code"] == "required_parameter_missing" for i in validate_spec_draft(draft)["errors"])


def test_io_purpose_reaches_bound_rows_and_comments_not_question_prose():
    from application.confirmed_generation_context import project_confirmed_specification
    from application.compact_protocol import expand_compact_ladder
    draft = _purpose_draft()
    original = copy.deepcopy(draft)
    assert validate_spec_draft(draft)["errors"] == []
    canonical = canonicalize_confirmed_spec(draft)
    expected = {"X0": "启动按钮", "X1": "停止按钮", "Y0": "控制输出"}
    assert {r["address"]: r["label"] for r in canonical["io_table"]} == expected
    assert {b["address"]: b["label"] for b in canonical["io_bindings"]} == expected
    assert all(b["name"] != b["label"] for b in canonical["io_bindings"])
    projected = project_confirmed_specification(canonical)
    assert {b["address"]: b["label"] for b in projected["io_bindings"]} == expected
    ladder = expand_compact_ladder({"r": [{"b": [{"i": [
        {"or": [["NO X0"], ["NO Y0"]]}, "NO X1"], "o": ["COIL Y0"]}]}]}, projected)
    assert ladder["device_comments"] == expected
    assert canonicalize_confirmed_spec(canonical) == canonical
    assert draft == original


def test_io_purpose_edits_and_explicit_clear_win_after_reanalysis():
    canonical = canonicalize_confirmed_spec(_purpose_draft())
    start = next(r for r in canonical["io_table"] if r["address"] == "X0")
    start.update(address="X002", label="操作台启动")
    stop = next(r for r in canonical["io_table"] if r["address"] == "X1")
    stop["label"] = ""  # An explicit user deletion is not a missing suggestion.
    canonical = canonicalize_confirmed_spec(canonical)
    newer = _purpose_analysis()
    newer["missing_info"][0]["question"] = "另一个完全不同的问句？"
    newer["missing_info"][0]["io_binding"]["label"] = "模型的新标签"
    draft = build_review_draft(newer, canonical)
    assert next(p for p in draft["parameters"] if p["id"] == "start_device")["value"] == "X2"
    assert next(p for p in draft["parameters"] if p["id"] == "stop_device")["io_binding"]["label"] == ""
    result = canonicalize_confirmed_spec(draft)
    assert {r["address"]: r["label"] for r in result["io_table"]} == {"X2": "操作台启动", "X1": "", "Y0": "控制输出"}
    assert {b["address"]: b["label"] for b in result["io_bindings"]} == {"X2": "操作台启动", "X1": "", "Y0": "控制输出"}
    assert canonicalize_confirmed_spec(result) == result


def test_io_purpose_uses_identity_for_two_devices_with_the_same_question():
    from plc.specification.bindings import bind_answers
    parameters = [
        {"id": f"motor{n}", "name": "接哪个输入？", "value": address,
         "io_binding": {"binding_id": f"motor{n}.start", "kind": "X", "label": f"{n}号电机启动"}}
        for n, address in [(1, "X000"), (2, "X002")]
    ]
    rows, _, bindings, _ = bind_answers([], parameters)
    assert {r["address"]: r["label"] for r in rows} == {"X0": "1号电机启动", "X2": "2号电机启动"}
    assert len(bindings) == 2
    parameters[0]["value"], parameters[1]["value"] = "X002", "X000"
    rows, _, bindings, _ = bind_answers(rows, parameters, bindings)
    assert {r["address"]: r["label"] for r in rows} == {"X2": "1号电机启动", "X0": "2号电机启动"}


def test_io_purpose_exact_label_can_bind_an_unbound_row_without_renaming_it():
    from plc.specification.bindings import bind_answers
    rows = [{"kind": "X", "address": "X000", "label": "进料到位", "source": "user"}]
    parameter = {"id": "sensor", "name": "传感器应该接哪里？", "value": "X002",
                 "io_binding": {"binding_id": "infeed.sensor", "kind": "X", "label": "进料到位"}}
    rows, _, bindings, _ = bind_answers(rows, [parameter])
    assert len(rows) == 1 and rows[0]["address"] == "X2" and rows[0]["label"] == "进料到位"
    assert bindings[0]["label"] == "进料到位"


def test_io_purpose_never_promotes_a_question_or_malformed_label_to_a_comment():
    from plc.specification.bindings import bind_answers, binding_hint
    for label in (None, {"thought": "do not export"}, ["not a scalar"], 123):
        parameter = {"id": "sensor", "name": "输入点在哪，为什么这样安排？", "value": "X0",
                     "io_binding": {"binding_id": "sensor", "kind": "X", "label": label}}
        assert "label" not in binding_hint(parameter)
        rows, _, bindings, _ = bind_answers([], [parameter])
        assert rows[0]["label"] == bindings[0]["label"] == ""
        spec = {"plc_model": "FX3U", "io_table": rows, "parameters": []}
        assert validate_spec_draft(spec)["errors"] == []
    # Older records with no metadata cannot turn their question into a comment.
    rows, _, bindings, _ = bind_answers([], [{"id": "unknown", "name": "检测信号接哪个输入点？", "value": "X4"}])
    assert rows[0]["label"] == bindings[0]["label"] == ""


def test_io_purpose_does_not_guess_or_rewrite_historical_nonempty_labels():
    from plc.specification.bindings import bind_answers
    old = "启动按钮接在输入点，程序中使用何种触点极性"
    rows = [{"kind": "X", "address": "X0", "label": old, "source": "user"}]
    p = {"id": "start_input", "name": "接哪个输入？", "value": "X0",
         "io_binding": {"binding_id": "start", "kind": "X", "label": "启动按钮"}}
    result, _, bindings, _ = bind_answers(rows, [p])
    assert result[0]["label"] == bindings[0]["label"] == old
    assert rows[0]["label"] == old


def test_io_purpose_deleted_row_does_not_reappear_from_a_retained_answer():
    canonical = canonicalize_confirmed_spec(_purpose_draft())
    canonical["io_table"] = [r for r in canonical["io_table"] if r["address"] != "X1"]
    result = canonicalize_confirmed_spec(canonical)
    assert "X1" not in {r["address"] for r in result["io_table"]}
    assert "停止按钮" not in {b["label"] for b in result["io_bindings"]}


def test_io_purpose_optional_metadata_does_not_add_a_response_gate():
    import json
    from application.response_contracts import ANALYSIS_RESPONSE
    from model_runtime.responses import inspect_response
    from plc.specification.bindings import binding_hint
    for label in ("Pump 1 START", "启动按钮", {"explanation": "not a label"}):
        analysis = {"missing_info": [{"question": "接哪个输入？", "io_binding": {
            "binding_id": "start", "kind": "X", "label": label}}]}
        assert inspect_response(json.dumps(analysis, ensure_ascii=False), "zh-CN", ANALYSIS_RESPONSE) == ()
        hint = binding_hint(analysis["missing_info"][0])
        assert hint.get("label") == (label if isinstance(label, str) else None)


def test_io_purpose_public_projection_does_not_expose_arbitrary_metadata():
    from application.confirmed_generation_context import project_confirmed_specification
    spec = canonicalize_confirmed_spec(_purpose_draft())
    for binding in spec["io_bindings"]:
        binding["private"] = "PRIVATE_SENTINEL"
    projected = project_confirmed_specification(spec)
    assert all("private" not in b for b in projected["io_bindings"])
    spec["io_bindings"][0]["label"] = {"private": "PRIVATE_SENTINEL"}
    assert "label" not in project_confirmed_specification(spec)["io_bindings"][0]


def test_io_purpose_prompt_contract_is_present_in_both_modes_and_pinned():
    from application.analysis_context import assemble_analysis_prompt
    for mode in ("direct", "design"):
        for baseline in (None, {"selected_approach": {"name": "现有方案"}}):
            calls, audits = [], []
            prompt = assemble_analysis_prompt("起保停", plc_model="FX3U", confirmed_context=baseline,
                analysis_mode=mode, model_loader=lambda: {},
                knowledge_builder=lambda *a, **kw: (calls.append(kw) or ""),
                audit=lambda *a, **kw: audits.append((a, kw)))
            assert "# I/O purpose metadata" in prompt.system_prompt
            assert '"label":"启动按钮"' in prompt.system_prompt
            assert "不为注释新增确认问题" in prompt.system_prompt
            assert len(calls) == 1 and calls[0]["include_design"] == (mode == "design")
            base = next(args[1] for args, _ in audits if args[0] == "base_prompt")
            assert "# I/O purpose metadata" in base


def test_io_purpose_http_save_generate_and_export_keep_only_device_names(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from model_runtime.provider import TextDelta, SystemMessage
    from plc.ir import ir_to_ladder
    from test_web_api import ORIGIN, OPERATOR, _login, _complete

    class Provider:
        profile = {}

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            assert len(self.requests) == 1  # No annotation rewriting call.
            prompt = next(m.content for m in request.messages if isinstance(m, SystemMessage))
            spec, _ = json.JSONDecoder().raw_decode(prompt.split("# Confirmed project specification\n", 1)[1])
            expected = {"X0": "操作台启动", "X1": "停止按钮", "Y0": "控制输出"}
            assert {b["address"]: b["label"] for b in spec["io_bindings"]} == expected
            assert {r["address"]: r["label"] for r in spec["io_table"]} == expected
            # The fixture returns logic only. Production expansion supplies comments.
            yield TextDelta(json.dumps({"r": [{"b": [{"i": [
                {"or": [["NO X0"], ["NO Y0"]]}, "NO X1"], "o": ["COIL Y0"]}]}]}))

    provider = Provider()
    monkeypatch.setattr("application.generation_agent._build_knowledge_context", lambda *a, **k: "")
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (provider, {"model": "label-fixture"}))
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", headers=headers, json={"name": "label fixture"}).json()["id"]
        first = client.put(f"/api/projects/{pid}/spec", headers=headers,
                           json={"spec": _purpose_draft(), "expected_hash": None}).json()
        assert first["valid"] and provider.requests == []
        next(r for r in first["spec"]["io_table"] if r["address"] == "X0")["label"] = "操作台启动"
        second = client.put(f"/api/projects/{pid}/spec", headers=headers,
                            json={"spec": first["spec"], "expected_hash": first["hash"]}).json()
        assert second["valid"] and provider.requests == []
        persisted = client.get(f"/api/projects/{pid}").json()["confirmed_spec"]
        assert {b["address"]: b["label"] for b in persisted["io_bindings"]} == {
            "X0": "操作台启动", "X1": "停止按钮", "Y0": "控制输出"}
        _, output = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "generation", "project_id": pid, "request_id": "labels-once",
            "text": "按确认规格生成", "response_language": "zh-CN"}))
        assert output["status"] == "saved" and len(provider.requests) == 1
        vid = output["version_id"]
        ladder = ir_to_ladder(service.projects.program(pid, vid))
        assert ladder["device_comments"] == {"X0": "操作台启动", "X1": "停止按钮", "Y0": "控制输出"}
        for artifact, encoding in (("svg", "utf-8"), ("comment_csv", "utf-16")):
            text = service.projects.artifact(pid, vid, artifact).read_text(encoding=encoding)
            assert all(label in text for label in ladder["device_comments"].values())
            assert "程序中使用何种" not in text and "接在哪个" not in text


def test_io_purpose_copies_the_canonical_user_label_on_every_read():
    for value, expected in (("  自定用途  ", "自定用途"), (None, "")):
        spec = canonicalize_confirmed_spec(_purpose_draft())
        next(r for r in spec["io_table"] if r["address"] == "X0")["label"] = value
        result = canonicalize_confirmed_spec(spec)
        assert next(b for b in result["io_bindings"] if b["address"] == "X0")["label"] == expected
        assert canonicalize_confirmed_spec(result) == result

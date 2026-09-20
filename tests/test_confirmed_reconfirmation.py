"""Repeated confirmation must preserve the edited wiring through actual delivery."""
import copy
import itertools
import json
from types import SimpleNamespace

import pytest

from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec, validate_spec_draft
from test_confirmed_compatibility import operator_spec, profile_for


def _truth_table(ladder, polarity="NO", *, addresses=("X0", "X1", "Y0")):
    def evaluate(item, state):
        if item is None:
            return True
        if item["type"] == "parallel_block":
            return any(all(evaluate(child, state) for child in path) for path in item["branches"])
        assert item["type"] in {"NO", "NC"}
        return state[item["address"]] if item["type"] == "NO" else not state[item["address"]]
    start_address, stop_address, output_address = addresses
    for start, stop, previous in itertools.product((False, True), repeat=3):
        state = {start_address: start, stop_address: stop, output_address: previous}
        for rung in ladder["rungs"]:
            prefix = evaluate(rung.get("header_element"), state) and all(evaluate(i, state) for i in rung["shared_inputs"])
            for branch in rung["branches"]:
                enabled = prefix and all(evaluate(i, state) for i in branch["inputs"])
                for output in branch["outputs"]:
                    assert output["type"] == "COIL"
                    state[output["address"]] = enabled
        assert state[output_address] == ((start or previous) and (not stop if polarity == "NO" else stop))


@pytest.mark.parametrize("contact", ["常开", "常闭"])
def test_combined_contact_answer_never_reverts_direct_table_edit(contact):
    from test_spec_choice_metadata import _analysis
    spec = build_review_draft(_analysis())
    for p, value in zip(spec["parameters"], ["X0", f"X1，{contact}", "Y0"]):
        p.update(value=value, source="user")
    canonical = canonicalize_confirmed_spec(spec)
    next(row for row in canonical["io_table"] if row["address"] == "X1")["address"] = "X3"
    result = canonicalize_confirmed_spec(canonical)
    assert {r["address"] for r in result["io_table"]} == {"X0", "X3", "Y0"}
    assert result["parameters"][0]["value"] == f"X3，{contact}"
    assert next(b for b in result["io_bindings"] if b["role"] == "stop")["address"] == "X3"
    assert not validate_spec_draft(result)["errors"]
    assert canonicalize_confirmed_spec(result) == result


def test_contact_polarity_edit_and_table_address_edit_are_independent():
    from test_spec_choice_metadata import _analysis
    spec = build_review_draft(_analysis())
    for p, value in zip(spec["parameters"], ["X0", "X1，常闭", "Y0"]):
        p.update(value=value, source="user")
    canonical = canonicalize_confirmed_spec(spec)
    next(row for row in canonical["io_table"] if row["address"] == "X1")["address"] = "X3"
    canonical["parameters"][0]["value"] = "X1，常开"
    result = canonicalize_confirmed_spec(canonical)
    assert result["parameters"][0]["value"] == "X3，常开"
    # Conversely a new address explicitly entered in the answer is not stale.
    result["parameters"][0]["value"] = "X4，常闭"
    changed = canonicalize_confirmed_spec(result)
    assert changed["parameters"][0]["value"] == "X4，常闭"
    assert {r["address"] for r in changed["io_table"]} == {"X0", "X4", "Y0"}


@pytest.mark.parametrize("combined", [True, False])
def test_deleted_row_is_not_recreated_or_forwarded_from_persisted_binding(combined):
    spec = operator_spec()
    if combined:
        spec["parameters"][1]["value"] = "X1，常闭"
    canonical = canonicalize_confirmed_spec(spec)
    canonical["io_table"] = [r for r in canonical["io_table"] if r["address"] != "X1"]
    result = canonicalize_confirmed_spec(canonical)
    assert {r["address"] for r in result["io_table"]} == {"X0", "Y0"}
    assert all(b["address"] != "X1" for b in result["io_bindings"])
    assert all(p.get("id") != "stop_input" for p in result["parameters"])
    assert canonicalize_confirmed_spec(result) == result


def test_shared_physical_address_aliases_follow_the_bound_row_without_stale_copy():
    from plc.specification.bindings import bind_answers
    params = [{"id": key, "name": key, "value": "X0", "source": "user",
               "io_binding": {"binding_id": key, "kind": "X"}}
              for key in ("motor1_enable", "motor2_enable")]
    rows, _, bindings, _ = bind_answers([], params)
    assert len(rows) == 1 and len(bindings) == 2
    rows[0]["address"] = "X2"
    rows, _, bindings, _ = bind_answers(rows, [], bindings)
    assert {b["address"] for b in bindings} == {"X2"}
    assert bind_answers(rows, [], bindings)[2] == bindings


def test_deleting_all_rows_does_not_preserve_an_old_active_binding_list():
    canonical = canonicalize_confirmed_spec(operator_spec())
    canonical["io_table"] = []
    result = canonicalize_confirmed_spec(canonical)
    assert result["io_table"] == [] and not result.get("io_bindings")
    assert canonicalize_confirmed_spec(result) == result


def test_reanalysis_reuses_confirmed_binding_answers_not_new_model_defaults():
    from test_spec_choice_metadata import _analysis
    spec = build_review_draft(_analysis())
    for p, value in zip(spec["parameters"], ["X0", "X1，常闭", "Y0"]):
        p.update(value=value, source="user")
    canonical = canonicalize_confirmed_spec(spec)
    next(r for r in canonical["io_table"] if r["address"] == "X0")["address"] = "X2"
    canonical = canonicalize_confirmed_spec(canonical)
    analysis = _analysis()
    analysis["missing_info"][0].update(question="Start input address?", default="X4")
    analysis["missing_info"].append({"id": "unconfirmed_input", "question": "Which input resets the counter?",
                                     "default": "X5", "required": True})
    draft = build_review_draft(analysis, canonical)
    values = {p["id"]: p["value"] for p in draft["parameters"]}
    assert values["start_input"] == "X2"
    assert values["stop_input"] == "X1，常闭" and values["output_coil"] == "Y0"
    assert values["unconfirmed_input"] == ""
    # The suggestion is still shown as a suggestion; it has not overwritten the
    # answer the user already confirmed in the previous revision.
    start = next(p for p in draft["parameters"] if p["id"] == "start_input")
    assert start["suggested_default"] == "X4"


def test_reanalysis_cannot_undo_a_confirmed_address_with_an_old_suggestion():
    from test_spec_choice_metadata import _analysis
    draft = build_review_draft(_analysis())
    for p, value in zip(draft["parameters"], ["X0", "X1，常闭", "Y0"]):
        p.update(value=value, source="user")
    canonical = canonicalize_confirmed_spec(draft)
    next(r for r in canonical["io_table"] if r["address"] == "X0")["address"] = "X2"
    canonical = canonicalize_confirmed_spec(canonical)
    review = build_review_draft(_analysis(), canonical)
    final = canonicalize_confirmed_spec(review)
    assert {r["address"] for r in final["io_table"]} == {"X2", "X1", "Y0"}
    assert all(r["source"] == "user" for r in final["io_table"])
    assert {b["binding_id"]: b["address"] for b in final["io_bindings"]} == {
        b["binding_id"]: b["address"] for b in canonical["io_bindings"]}
    assert canonicalize_confirmed_spec(final) == final


def _explicit_io_reanalysis_fixture():
    from application.analysis_results import _normalize_analysis_result
    request = """I/O 约定：
X001：启动按钮
X003：停止按钮
Y000：输送带
"""
    analysis = _normalize_analysis_result(
        {"summary": "fixture", "approaches": [], "missing_info": [], "suggested_io": {}},
        "FX3U", request,
    )
    return request, canonicalize_confirmed_spec(build_review_draft(analysis))


def _reanalyze_explicit_io(request, previous):
    from application.analysis_results import _normalize_analysis_result
    analysis = _normalize_analysis_result(
        {
            "summary": "fixture", "approaches": [], "missing_info": [],
            "suggested_io": {
                "X": {"X001": "启动按钮", "X003": "停止按钮"},
                "Y": {"Y000": "输送带"},
            },
        },
        "FX3U", request, previous,
    )
    return canonicalize_confirmed_spec(build_review_draft(analysis, previous))


def test_reanalysis_does_not_undo_direct_io_address_edit():
    from plc.specification.confirmed import preserve_io_user_edits
    request, spec = _explicit_io_reanalysis_fixture()
    before = copy.deepcopy(spec)
    next(r for r in spec["io_table"] if r["address"] == "X1")["address"] = "X20"
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    assert "X1" in spec["io_user_overrides"]["removed_addresses"]
    result = _reanalyze_explicit_io(request, spec)
    assert "X20" in {r["address"] for r in result["io_table"]}
    assert "X1" not in {r["address"] for r in result["io_table"]}


def test_reanalysis_does_not_undo_direct_io_address_and_label_edit():
    from plc.specification.confirmed import preserve_io_user_edits
    request, spec = _explicit_io_reanalysis_fixture()
    before = copy.deepcopy(spec)
    row = next(r for r in spec["io_table"] if r["address"] == "X1")
    row.update(address="X20", label="操作台启动")
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    result = _reanalyze_explicit_io(request, spec)
    assert {r["address"]: r["label"] for r in result["io_table"]}["X20"] == "操作台启动"
    assert "X1" not in {r["address"] for r in result["io_table"]}


def test_reanalysis_does_not_resurrect_deleted_confirmed_io_row():
    from plc.specification.confirmed import preserve_io_user_edits
    request, spec = _explicit_io_reanalysis_fixture()
    before = copy.deepcopy(spec)
    spec["io_table"] = [r for r in spec["io_table"] if r["address"] != "X1"]
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    assert "X1" in spec["io_user_overrides"]["removed_addresses"]
    result = _reanalyze_explicit_io(request, spec)
    assert "X1" not in {r["address"] for r in result["io_table"]}
    assert {"X3", "Y0"}.issubset({r["address"] for r in result["io_table"]})


def test_deleted_model_suggested_io_row_stays_deleted_on_reanalysis():
    from application.analysis_results import _normalize_analysis_result
    from plc.specification.confirmed import preserve_io_user_edits
    first = _normalize_analysis_result(
        {
            "summary": "fixture", "approaches": [], "missing_info": [],
            "suggested_io": {"M": {"M10": "模型内部状态"}},
        },
        "FX3U", "运行控制",
    )
    spec = canonicalize_confirmed_spec(build_review_draft(first))
    before = copy.deepcopy(spec)
    spec["io_table"] = [r for r in spec["io_table"] if r["address"] != "M10"]
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    second = _normalize_analysis_result(
        {
            "summary": "fixture", "approaches": [], "missing_info": [],
            "suggested_io": {"M": {"M10": "模型内部状态"}},
        },
        "FX3U", "继续分析", spec,
    )
    result = canonicalize_confirmed_spec(build_review_draft(second, spec))
    assert "M10" not in {r["address"] for r in result["io_table"]}


def test_user_readding_removed_address_clears_tombstone():
    from plc.specification.confirmed import preserve_io_user_edits
    _request, spec = _explicit_io_reanalysis_fixture()
    before = copy.deepcopy(spec)
    spec["io_table"] = [r for r in spec["io_table"] if r["address"] != "X1"]
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    assert "X1" in spec["io_user_overrides"]["removed_addresses"]
    before = copy.deepcopy(spec)
    spec["io_table"].append({"kind": "X", "address": "X1", "label": "重新启用", "source": "user"})
    spec = canonicalize_confirmed_spec(preserve_io_user_edits(before, spec))
    assert "io_user_overrides" not in spec or "X1" not in spec["io_user_overrides"].get("removed_addresses", [])


def test_reanalysis_does_not_overwrite_user_edited_label_at_same_address():
    request, spec = _explicit_io_reanalysis_fixture()
    next(r for r in spec["io_table"] if r["address"] == "Y0")["label"] = "主输送带"
    spec = canonicalize_confirmed_spec(spec)
    result = _reanalyze_explicit_io(request, spec)
    assert next(r for r in result["io_table"] if r["address"] == "Y0")["label"] == "主输送带"


def test_same_address_new_row_does_not_reactivate_deleted_owner():
    canonical = canonicalize_confirmed_spec(operator_spec())
    canonical["io_table"] = [r for r in canonical["io_table"] if r["address"] != "X0"]
    canonical["io_table"].append({"kind": "X", "address": "X0", "label": "Reset input",
                                   "binding_id": "new_reset_input", "source": "user"})
    final = canonicalize_confirmed_spec(canonical)
    assert all(b["role"] != "start" for b in final["io_bindings"])


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("contact", ["常开", "常闭"])
def test_http_reconfirmation_after_address_edit_outputs_the_new_wiring(tmp_path, streaming, contact):
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from application.workbench import WorkbenchService
    from model_runtime.provider import OpenAICompatibleProvider
    from test_web_api import ORIGIN, OPERATOR, _login, _complete
    from test_spec_choice_metadata import _analysis
    from plc.ir import ir_to_ladder
    calls = []

    class Endpoint:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def with_options(self, **_options):
            return self

        def create(self, **params):
            calls.append(copy.deepcopy(params))
            assert len(calls) == 1
            prompt = next(m["content"] for m in params["messages"] if m["role"] == "system")
            projected, _ = json.JSONDecoder().raw_decode(prompt.split("# Confirmed project specification\n", 1)[1])
            bindings = {b["role"]: b["address"] for b in projected["io_bindings"]}
            assert bindings == {"start": "X0", "stop": "X3", "output": "Y0"}
            assert next(p["value"] for p in projected["parameters"] if p["id"] == "stop_input") == f"X3，{contact}"
            stop_term = "NC X3" if contact == "常开" else "NO X3"
            raw = json.dumps({"r": [{"s": None, "b": [{"i": [stop_term,
                         {"or": [["NO X0"], ["NO Y0"]]}], "o": ["COIL Y0"]}]}]})
            if params["stream"]:
                return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[i:i+1]), finish_reason=None)])
                             for i in range(len(raw))])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason="stop")])

    profile = profile_for("json_object", streaming=streaming)
    provider = OpenAICompatibleProvider(profile, "offline-fixture-key", client=Endpoint())
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (provider, {"model": profile["model"]}))
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", headers=headers, json={"name": "reconfirmation fixture"}).json()["id"]
        spec = build_review_draft(_analysis())
        for p, value in zip(spec["parameters"], ["X0", f"X1，{contact}", "Y0"]):
            p.update(value=value, source="user")
        first = client.put(f"/api/projects/{pid}/spec", headers=headers,
                           json={"spec": spec, "expected_hash": None}).json()
        assert first["valid"] and calls == []
        next(r for r in first["spec"]["io_table"] if r["address"] == "X1")["address"] = "X3"
        second = client.put(f"/api/projects/{pid}/spec", headers=headers,
                            json={"spec": first["spec"], "expected_hash": first["hash"]}).json()
        assert second["valid"] and calls == []
        assert {r["address"] for r in second["spec"]["io_table"]} == {"X0", "X3", "Y0"}
        _, output = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "generation", "project_id": pid, "request_id": "reconfirmed-once",
            "text": "按确认规格生成", "response_language": "zh-CN"}))
        assert output["status"] == "saved" and len(calls) == 1
        vid = output["version_id"]
        _truth_table(ir_to_ladder(service.projects.program(pid, vid)),
                     "NO" if contact == "常开" else "NC", addresses=("X0", "X3", "Y0"))
        for artifact in ("json", "ir", "svg", "program_csv", "comment_csv"):
            assert service.projects.artifact(pid, vid, artifact).stat().st_size > 0


# Locked-spec regenerate intent
import pytest

from application.request_intent import _is_regenerate_locked_spec_request


@pytest.mark.parametrize(
    "text",
    [
        "重新生成",
        "请重新生成程序",
        "再次生成方案",
        "重试生成一次！",
        "重新按当前已确认规格生成",
    ],
)
def test_plain_regenerate_commands_reuse_locked_spec(text):
    assert _is_regenerate_locked_spec_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "重新生成，并把X3改成急停",
        "重新分析需求",
        "生成一个新的多段速方案",
        "把控制方式改成RS-485后重新生成",
    ],
)
def test_requirement_changes_are_not_treated_as_plain_regenerate_commands(text):
    assert not _is_regenerate_locked_spec_request(text)


def test_http_known_address_polarity_survives_save_edit_and_generation(tmp_path):
    """Screenshot-shaped answers reach real HTTP storage/artifact paths, not a live PLC."""
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from application.workbench import WorkbenchService
    from model_runtime.provider import TextDelta, SystemMessage
    from test_web_api import ORIGIN, OPERATOR, _login, _complete
    from plc.ir import ir_to_ladder

    declarations = """FX3U。这里只验证启停输入与保持输出的绑定，其他行是已声明接口。
X001：启动按钮
X003：停止按钮
D0：Vision Sensor 分类结果
Y000：Entry conveyor
Y002：Exit conveyor
Y003：Sorter 1 turn
Y004：Sorter 1 belt
Y005：Sorter 2 turn
Y006：Sorter 2 belt
Y007：Sorter 3 turn
Y010：Sorter 3 belt
启动时保持Y000，停止时释放。"""
    answers = {
        "start_polarity": "常开（按下为 ON）", "stop_polarity": "常闭（按下为 OFF）",
        "classification_zero": "D0=0 表示无料，1~9表示分类结果",
    }

    class Provider:
        profile = {}

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if request.response_contract.name == "analysis":
                assert len(self.requests) == 1
                payload = {
                    "summary": "确认输入极性，启动保持输出，停止释放输出。", "approaches": [],
                    "missing_info": [
                        {"id": key, "question": f"{address} {label}的触点极性是？", "required": True,
                         "options": ["常开（按下为 ON）", "常闭（按下为 OFF）"],
                         "io_binding": {"binding_id": f"operator.{role}", "kind": "X", "role": role, "label": label}}
                        for key, address, role, label in (
                            ("start_polarity", "X001", "start", "启动按钮"),
                            ("stop_polarity", "X003", "stop", "停止按钮"),
                        )
                    ] + [{"id": "classification_zero", "question": "D0=0 时的含义？", "required": True,
                          "io_binding": {"binding_id": "vision.classification", "kind": "D", "label": "Vision Sensor 分类结果"}}],
                    "suggested_io": {},  # Core must preserve the user's declared rows even when the model omits them.
                }
            else:
                assert len(self.requests) == 2
                prompt = next(m.content for m in request.messages if isinstance(m, SystemMessage))
                projected, _ = json.JSONDecoder().raw_decode(prompt.split("# Confirmed project specification\n", 1)[1])
                rows = {r["address"]: r["label"] for r in projected["io_table"]}
                assert len(rows) == 11
                assert {"X1", "X5", "Y10", "D0"} <= rows.keys() and "X3" not in rows
                assert rows["X5"] == "停机输入" and rows["Y10"] == "Sorter 3 belt"
                bindings = {b["role"]: b for b in projected["io_bindings"]}
                assert bindings["start"]["address"] == "X1" and bindings["start"]["active_level"] == 1
                assert bindings["stop"]["address"] == "X5" and bindings["stop"]["active_level"] == 0
                assert {p["id"]: p["value"] for p in projected["parameters"]} == answers
                assert "NO X5" in prompt.split("# Settled input predicates", 1)[1]
                payload = {"r": [{"b": [{"i": [{"or": [["NO X1"], ["NO Y0"]]}, "NO X5"], "o": ["COIL Y0"]}]}]}
            yield TextDelta(json.dumps(payload, ensure_ascii=False))

    provider = Provider()
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (provider, {"model": "offline-binding-fixture"}))
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", headers=headers, json={"name": "极性绑定回归"}).json()["id"]
        _, analysis = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "analysis", "project_id": pid, "request_id": "analyze-polarities",
            "text": declarations, "response_language": "zh-CN"}))
        draft = analysis["spec_draft"]
        assert len(draft["io_table"]) == 11
        assert all(p["value"] == "" for p in draft["parameters"])
        for p in draft["parameters"]:
            p.update(value=answers[p["id"]], source="user")
        first = client.put(f"/api/projects/{pid}/spec", headers=headers,
                           json={"spec": draft, "expected_hash": None}).json()
        assert first["valid"] and len(provider.requests) == 1
        next(r for r in first["spec"]["io_table"] if r["address"] == "X3").update(address="X005", label="停机输入")
        second = client.put(f"/api/projects/{pid}/spec", headers=headers,
                            json={"spec": first["spec"], "expected_hash": first["hash"]}).json()
        assert second["valid"] and len(provider.requests) == 1
        _, output = _complete(client, service, client.post("/api/jobs", headers=headers, json={
            "kind": "generation", "project_id": pid, "request_id": "generate-polarities",
            "text": "按确认规格生成", "response_language": "zh-CN"}))
        assert output["status"] == "saved" and len(provider.requests) == 2
        vid = output["version_id"]
        ladder = ir_to_ladder(service.projects.program(pid, vid))
        _truth_table(ladder, "NC", addresses=("X1", "X5", "Y0"))
        assert ladder["device_comments"]["X1"] == "启动按钮"
        assert ladder["device_comments"]["X5"] == "停机输入"
        assert not {"X001", "X003", "X005", "X3"} & ladder["device_comments"].keys()
        svg = service.projects.artifact(pid, vid, "svg").read_text(encoding="utf-8")
        comments = service.projects.artifact(pid, vid, "comment_csv").read_text(encoding="utf-16")
        assert "停机输入" in svg and "停机输入" in comments
        assert "触点极性是" not in comments and "按下为" not in comments

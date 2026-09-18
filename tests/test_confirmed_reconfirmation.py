"""Repeated confirmation must preserve the edited wiring through actual delivery."""
import copy
import itertools
import json
from types import SimpleNamespace

import pytest

from confirmed_spec import build_review_draft, canonicalize_confirmed_spec, validate_spec_draft
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
    from spec_bindings import bind_answers
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
    from model_provider import OpenAICompatibleProvider
    from test_web_api import ORIGIN, OPERATOR, _login, _complete
    from test_spec_choice_metadata import _analysis
    from plc_ir import ir_to_ladder
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

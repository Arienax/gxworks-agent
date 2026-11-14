import json

import pytest

from application.conversation_model import ModelTurn
from application.generation_wire import expand_context_document, structured_context_text
from responses_fixture import response_body
from model_runtime.provider import AssistantMessage, ImageAttachment, ModelRequest, UserMessage, _chat_message, _response_input
from model_runtime.responses import ResponseContract
from shared.model_context import technical_block


def _request(rules, user, task):
    return ModelRequest.from_messages(
        [{"role": "system", "content": rules}, user],
        response_contract=ResponseContract(task, "json"),
    )


@pytest.mark.parametrize("responses", [True, False])
def test_model_turn_reuses_exact_prefix_and_only_appends_changes(tmp_path, responses):
    shared = {"kind": "example", "target": "timer", "value": {"description": "timer example " * 40}}
    new = {"kind": "example", "target": "queue", "value": {"description": "queue example"}}
    endpoint = "https://api.openai.com/v1" if responses else "https://api-inference.modelscope.cn/v1"
    profile = {"baseUrl": endpoint, "adapter": "openai_compatible"}
    first = ModelTurn(tmp_path, (), {"model": "offline"}, profile, "en", session_id="job_analysis")
    image = ImageAttachment("diagram.png", "image/png", b"diagram")
    original = "Original requirement " * 20
    common_rules = "# Shared timing\nMeasure from the event."
    analysis = first.prepare(_request("# Design rules\nPropose a design.\n" + common_rules + "\n" + technical_block(shared),
                                      UserMessage(original, (image,)), "analysis"))
    rows = [{"id": "r1", "text": "Preserve the event on stop."}]
    choice = {"approach_id": "selected", "name": "Event queue", "generation_guide": "Queue each event.",
              "instruction_choices": [{"opcode": "SFWR", "role": "Append an event."}]}
    reply = json.dumps({"requirements": rows, "approaches": [{"approach_id": "other"}, choice],
                        "open_questions": [{"id": "q", "text": "Use the queue?"}]})
    origin = (endpoint, "offline", "credential-scope")
    output = response_body(reply, encrypted="encrypted-provider-replay")["output"]
    thinking = "Provider reasoning for the original turn."
    replay_state = {"responses_items": output, "effort_update": "high"} if responses else {
        "message": {"reasoning_content": thinking}}
    first.accept(AssistantMessage(reply, _provider_fields={"origin": origin, "reasoning_tokens": 91, **replay_state}),
                 "analysis")
    first.save("job_analysis")

    second = ModelTurn(tmp_path, ("job_analysis",), {"model": "offline"}, profile, "en", session_id="job_analysis")
    spec = {"revision": 1, "requirements": rows, "selected_approach": choice,
            "open_questions": [{"id": "q", "text": "Use the queue?", "answer": "Yes"}],
            "intent_context": {"requests": [{"id": "initial", "text": original}]}}
    payload = {"context_schema_version": 3, "knowledge": [shared, new], "confirmed_spec": spec}
    rules = "# Generation rules\nWrite one program.\n" + common_rules
    generation = second.prepare(_request(rules, UserMessage(structured_context_text(payload)), "compact_ladder"))
    assert generation.messages[:len(analysis.messages)] == analysis.messages
    replay = generation.messages[len(analysis.messages)]
    assert replay == AssistantMessage(reply)
    another_account = (*origin[:2], "another-account")
    assistant = {"role": "assistant", "content": reply}
    if responses:
        assert len(analysis.options["prompt_cache_key"]) == 64
        assert generation.options["prompt_cache_key"] == analysis.options["prompt_cache_key"]
        assert _response_input(replay, origin, configure_effort=True) == [
            {"type": "configuration_update", "reasoning": {"effort": "high"}}, *output]
        assert _response_input(replay, another_account) == [assistant]
    else:
        assert "prompt_cache_key" not in generation.options
        assert _chat_message(replay, origin) == {**assistant, "reasoning_content": thinking}
        assert _chat_message(replay, another_account) == assistant
    assert json.loads(generation.messages[-2].content.split("\n", 1)[1])["rule_changes"] == {
        "remove": ["/Design rules"], "set": {"/Generation rules": "Write one program."}}
    suffix = expand_context_document(json.loads(generation.messages[-1].content))
    assert suffix["knowledge"] == [new]
    assert suffix["analysis_base"] == {"/confirmed_spec/requirements": "/requirements",
        "/confirmed_spec/open_questions": "/open_questions", "/confirmed_spec/selected_approach": "/approaches/1"}
    assert suffix["context_changes"]["set"] == {"/context_schema_version": 3, "/knowledge": [],
        "/confirmed_spec/revision": 1, "/confirmed_spec/open_questions/0/answer": "Yes",
        "/confirmed_spec/intent_context": {"requests": [{"id": "initial", "text": {"$analysis_request": True}}]}}
    second.accept(AssistantMessage('{"r":[]}'), "compact_ladder")
    second.save("job_generation")

    third = ModelTurn(tmp_path, ("job_generation", "job_analysis"), {"model": "offline"}, profile, "en", session_id="job_analysis")
    edited_choice = {**choice, "instruction_choices": [{"opcode": "SFWR", "role": "Append an event after debounce."}]}
    payload.update(confirmed_spec={**spec, "revision": 2, "open_questions": [], "selected_approach": edited_choice},
                   request="Debounce before appending an event")
    edit = third.prepare(_request(rules, UserMessage(structured_context_text(payload)), "ladder"))
    assert edit.messages[:len(generation.messages)] == generation.messages
    control = json.loads(edit.messages[-2].content.split("\n", 1)[1])
    assert control == {"task": "ladder"}
    suffix = expand_context_document(json.loads(edit.messages[-1].content))
    assert suffix == {"context_changes": {"remove": ["/confirmed_spec/open_questions/0"],
        "set": {"/confirmed_spec/revision": 2,
                "/confirmed_spec/selected_approach/instruction_choices/0/role": "Append an event after debounce.",
                "/request": "Debounce before appending an event"}}}

    switched = ModelTurn(tmp_path, ("job_generation", "job_analysis"), {"model": "other"}, {}, "en")
    assert not switched.history


@pytest.mark.parametrize("legacy", [False, True])
def test_instruction_evidence_is_reused_across_lookup_aspects(tmp_path, legacy):
    from application.workspace import canonical_hash
    from shared.model_context import model_knowledge_value

    shared = {"kind": "instruction", "target": "BMOV", "scope": {"models": ["FX3U"]},
              "context": {"language": "ladder", "aspects": ["programming_semantics", "effects"]},
              "value": {"family": "BMOV", "forms": [{"opcode": "BMOV", "operands": ["source", "destination", "count"]}]}}
    capability = {"kind": "instruction_capability", "name": "BMOV", "functions": ["block_move"]}
    model = {"model": "offline"}
    first = ModelTurn(tmp_path, (), model, {}, "en")
    analysis = first.prepare(_request("# Analysis\n" + technical_block(shared) + "\n" + technical_block(capability),
                                      UserMessage("Copy the sampled values at the end of each batch."), "analysis"))
    first.accept(AssistantMessage('{"approaches":[]}'), "analysis")
    if legacy:
        first.pending.pop("evidence_identity_version")
        first.pending["evidence"] = [canonical_hash(model_knowledge_value(unit)) for unit in (shared, capability)]
    first.save("job_analysis")

    second = ModelTurn(tmp_path, ("job_analysis",), model, {}, "en")
    same = {**shared, "context": {"language": "ladder", "aspects": ["programming_semantics", "execution"]}}
    different_scope = {**same, "scope": {"models": ["FX3UC"]}}
    more_facts = {**same, "value": {**same["value"], "overlapping_ranges": "requires validation"}}
    payload = {"context_schema_version": 3,
               "knowledge": [same, {**capability, "functions": ["explicit"]}, different_scope, more_facts]}
    generation = second.prepare(_request("# Generation\nUse the selected approach.",
        UserMessage(structured_context_text(payload)), "compact_ladder"))
    assert generation.messages[:len(analysis.messages)] == analysis.messages
    suffix = expand_context_document(json.loads(generation.messages[-1].content))
    assert suffix["knowledge"] == [different_scope, more_facts]


def test_ordered_ladder_edit_resumes_before_legacy_preview_and_appends_source_changes(tmp_path):
    model = {"model": "offline"}
    spec = {"requirements": [{"id": "r1", "text": "Preserve the event on stop."}]}
    generation = ModelTurn(tmp_path, (), model, {}, "en")
    context = {"context_schema_version": 3, "plc_model": "FX3U", "confirmed_spec": spec, "request": "Generate"}
    initial = generation.prepare(_request("Generate", UserMessage(json.dumps(context)), "compact_ladder"))
    generated = AssistantMessage('{"r":[]}')
    generation.accept(generated, "compact_ladder")
    generation.save("job_generation")

    legacy = ModelTurn(tmp_path, ("job_generation",), model, {}, "en")
    old = {"tool_schema": {}, "context": {"selected_source": {"language": "ladder", "svg": "<svg>legacy</svg>"}}}
    legacy.prepare(_request("Edit source", UserMessage(json.dumps(old)), "gxw_existing_edit"))
    legacy.accept(AssistantMessage('{"summary":"legacy edit"}'), "gxw_existing_edit")
    legacy.save("job_legacy_edit")

    jobs = ("job_legacy_edit", "job_generation")
    edit = ModelTurn(tmp_path, jobs, model, {}, "en")
    rows = [{"record_index": 0, "op": "LD", "args": ["X0"]},
            {"record_index": 1, "op": "OUT", "args": ["Y0"]}]
    packet = {**context, "request": "Change X0 to X1", "tool_schema": {},
              "current_model": {"program": "MAIN.Program.pou", "networks": [{"raw_instructions": rows}]}}
    request = edit.prepare(_request("Edit source", UserMessage(json.dumps(packet)), "gxw_existing_edit"))
    prefix = (*initial.messages, generated)
    assert edit.parent == "job_generation" and request.messages[:len(prefix)] == prefix
    assert not any("<svg>" in message.content for message in request.messages)
    assert json.loads(request.messages[-1].content)["context_changes"]["set"] == {
        "/request": packet["request"], "/tool_schema": {}, "/current_model": packet["current_model"]}
    edit.accept(AssistantMessage('{"summary":"changed input"}'), "gxw_existing_edit")
    edit.save("job_first_edit")

    rows[0]["args"] = ["X1"]
    packet["request"] = "Change Y0 to Y1"
    next_edit = ModelTurn(tmp_path, ("job_first_edit", *jobs), model, {}, "en")
    followup = next_edit.prepare(_request("Edit source", UserMessage(json.dumps(packet)), "gxw_existing_edit"))
    assert followup.messages[:len(request.messages)] == request.messages
    assert json.loads(followup.messages[-2].content.split("\n", 1)[1]) == {"task": "gxw_existing_edit"}
    assert json.loads(followup.messages[-1].content)["context_changes"]["set"] == {
        "/request": packet["request"], "/current_model/networks/0/raw_instructions/0/args/0": "X1"}

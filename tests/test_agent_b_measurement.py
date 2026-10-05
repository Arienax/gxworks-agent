"""Actual generation-path measurement contract; all providers here are offline."""
import copy
import json
from types import SimpleNamespace

import pytest

from model_profile_fixtures import offline_runtime_profile

from application.compact_protocol import canonical_compact_example, compact_response_schema, compact_protocol_prompt
from application.generation_agent import _FirstJSONObjectProvider
from model_runtime.provider import ModelRequest, OpenAICompatibleProvider, ReasoningDelta, TextDelta, Usage, UserMessage, _usage_event
from scripts.benchmark_agent_b import (
    ObservedProvider, evaluate_synthetic_case, load_cases, main, manual_text_context,
    preflight_pairs, run_case, schedule, summarize, without_candidate_usage,
)


@pytest.mark.parametrize("details", ["completion_tokens_details", "output_tokens_details"])
def test_reported_reasoning_usage_is_preserved_including_empty_choice_tail(details, monkeypatch):
    captured = []
    import shared.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, "emit", lambda event, **fields: captured.append((event, fields)))
    provider = OpenAICompatibleProvider({"adapter": "openai_compatible", "model": "offline"}, "offline-key", client=object())
    metering = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, details: {"reasoning_tokens": 15}}
    response = [
        {"choices": [{"delta": {"content": '{"r":[]}'}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": metering},
    ]
    events = list(provider._streaming_events(response))
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.reasoning_tokens == 15 and usage.raw_usage == metering
    assert captured[-1][1]["reasoning_tokens"] == 15 and captured[-1][1]["finish_seen"]


def test_missing_reasoning_meter_is_unknown_not_zero():
    usage = _usage_event({"prompt_tokens": 10, "completion_tokens": 20})
    assert usage.reasoning_tokens is None
    assert "reasoning_tokens" not in usage.raw_usage
    assert _usage_event({"input_tokens": 10, "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 0}}).reasoning_tokens == 0


def test_user_journey_dry_run_requires_no_provider_or_private_outputs(tmp_path, monkeypatch, capsys):
    from scripts.benchmark_user_path import main as journey_main
    import model_runtime.provider as providers
    monkeypatch.setattr(providers, "get_active_provider", lambda *args: pytest.fail("dry run loaded credentials"))
    assert journey_main(["--phase", "analysis", "--profile-id", "fixture", "--output", str(tmp_path / "private")]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0 and len(plan["cases"]) == 4
    assert not (tmp_path / "private").exists()


def test_user_journey_raw_directory_excludes_repository():
    from scripts.benchmark_user_path import ROOT, private_directory
    with pytest.raises(ValueError, match="outside the repository"):
        private_directory(ROOT / "benchmarks" / "raw")


@pytest.mark.parametrize("change", ["evidence", "specification", "temperature"])
def test_user_journey_preflight_rejects_non_example_changes(change, monkeypatch):
    from scripts import benchmark_user_path as journey
    request = {"messages": [{"role": "system", "content": "same evidence and specification"}], "temperature": 0.1}
    def run(case, *args, **kwargs):
        actual = copy.deepcopy(request)
        if case["construction_examples"]:
            if change == "temperature":
                actual["temperature"] = 0.2
            else:
                actual["messages"][0]["content"] += " changed " + change
        return {"actual_requests": [actual], "handoff": {"construction_examples": {"enabled": case["construction_examples"]}}}
    monkeypatch.setattr(journey, "run_case", run)
    with pytest.raises(ValueError, match="Non-example request content differs"):
        journey.preflight({"case_id": "fixture"}, {"summary": "same"}, SimpleNamespace(profile={"model": "offline"}, api_key="offline-key"))


def test_user_journey_only_removes_the_delimited_example_block():
    from scripts.benchmark_user_path import request_without_examples
    request = {"messages": [{"role": "system", "content": "facts\n# Routed construction examples (routed_construction/2)\nexample\n# End routed construction examples\nspec"}], "temperature": 0.1}
    stripped = request_without_examples(request)
    assert stripped == {"messages": [{"role": "system", "content": "factsspec"}], "temperature": 0.1}
    assert "example" in request["messages"][0]["content"]


def test_user_journey_no_rag_is_opt_in_and_keeps_the_two_existing_arms(tmp_path, capsys):
    from scripts.benchmark_user_path import generation_arms, main as journey_main
    assert generation_arms() == [("examples_off", False), ("examples_on", True)]
    assert generation_arms(True) == [*generation_arms(), ("no_rag", False)]
    assert journey_main(["--phase", "generation", "--profile-id", "fixture", "--include-no-rag",
                         "--output", str(tmp_path / "private"), "--repeats", "1"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["generation_arms"] == ["examples_off", "examples_on", "no_rag"]
    assert plan["network_calls"] == 0


@pytest.mark.parametrize("leak", ["# Retrieved PLC evidence", "[KNOWLEDGE {}]", "[INSTRUCTION FACTS CMP]",
                                 "OPERAND_SEMANTICS:", "# Routed construction examples",
                                 "# Confirmed operation effects", "# Compacted historical context",
                                 '"retrieved_text_present":true'])
def test_user_journey_no_rag_checks_final_request_not_only_empty_retrieval(leak):
    from scripts.benchmark_user_path import assert_no_rag_request
    contexts = [{"text": "", "manifest": {"records": [], "retrieval_enabled": False}}]
    with pytest.raises(ValueError):
        assert_no_rag_request({"messages": [{"content": "unchanged specification\n" + leak}]}, contexts)


def test_user_journey_rag_comparison_preserves_everything_except_observed_evidence():
    from scripts.benchmark_user_path import request_without_rag
    evidence = "\n# Retrieved PLC evidence\nmanual source bytes\n"
    suffix = '# Settled input predicates (not a new requirement)\n{"retrieved_text_present":true,"active":"X0=1"}\n\n# Provider JSON protocol\nunchanged schema'
    actual = {"model": "same", "temperature": 0.2, "messages": [
        {"role": "system", "content": "protocol" + evidence + "confirmed specification\n" + suffix},
        {"role": "user", "content": "fixed request"}]}
    expected = copy.deepcopy(actual)
    expected["messages"][0]["content"] = "protocolconfirmed specification\n" + suffix.replace(":true", ":false")
    assert request_without_rag(actual, evidence) == expected
    assert evidence in actual["messages"][0]["content"]
    with pytest.raises(ValueError, match="once"):
        request_without_rag(actual, "unobserved evidence")


def test_no_rag_actual_generation_skips_retriever_and_sqlite_planner_and_forces_examples_off(monkeypatch):
    from scripts.benchmark_agent_b import PreviewProvider
    from scripts.benchmark_user_path import assert_no_rag_request
    from application import generation_agent
    from knowledge import structured_facts
    provider = OpenAICompatibleProvider(offline_runtime_profile(), "fixture-key", client=object())
    case = next(case for case in _purpose_cases() if case["case_id"] == "wsfl-a")
    before = copy.deepcopy(case)
    original_planner = structured_facts.structured_fact_targets
    monkeypatch.setattr(generation_agent, "_build_knowledge_context",
                        lambda *a, **k: pytest.fail("No-RAG called the retriever"))
    monkeypatch.setattr(structured_facts, "_declared_instruction_alias_targets",
                        lambda *a, **k: pytest.fail("No-RAG queried SQLite aliases"))
    record = run_case({**case, "construction_examples": True}, "no_rag", provider=PreviewProvider(provider))
    assert len(record["actual_requests"]) == 1
    assert_no_rag_request(record["actual_requests"][0], record["evidence"])
    assert record["handoff"]["generation_evidence"]["records"] == []
    assert not record["handoff"]["construction_examples"]["enabled"]
    assert structured_facts.structured_fact_targets is original_planner
    assert case == before


def test_no_rag_workbench_journey_restores_retrieval_for_the_next_arm(tmp_path, monkeypatch):
    from scripts.benchmark_agent_b import PreviewProvider
    from scripts.benchmark_user_path import Journey
    from application import model_api
    from knowledge.evidence import KnowledgeContext
    calls = []
    def build(query, **kwargs):
        calls.append(str(query))
        return KnowledgeContext("", {"records": []})
    monkeypatch.setattr(model_api, "_build_knowledge_context", build)
    profile = {**offline_runtime_profile(), "id": "offline_profile"}
    provider = OpenAICompatibleProvider(profile, "fixture-key", client=object())
    case = next(case for case in _purpose_cases() if case["case_id"] == "hold-control")
    with Journey(tmp_path / "private", PreviewProvider(provider)) as journey:
        project = journey.service.create_project(name="isolated", plc_model="FX3U", target_mode="ladder")
        assert journey.service.set_spec(project["id"], case["confirmed_spec"], None)["valid"]
        command = {"kind": "generation", "project_id": project["id"], "text": "Generate confirmed spec",
                   "fresh_confirmed_generation": True, "construction_examples": True}
        baseline = journey.job(command, case_id=case["case_id"], arm="no_rag", repeat=0)
        assert calls == [] and len(baseline["actual_requests"]) == 1
        assert baseline["retrieval"][0]["manifest"]["retrieval_enabled"] is False
        journey.job({**command, "construction_examples": False}, case_id=case["case_id"], arm="examples_off", repeat=0)
        assert len(calls) == 1


@pytest.mark.parametrize('model,address', [('FX3U', 'X0'), ('FX5U', 'X18')])
@pytest.mark.parametrize('typed_level', [False, True])
def test_user_journey_preflight_reports_levels_delivered_instead_of_guessing_from_labels(typed_level, model, address, monkeypatch):
    from scripts import benchmark_user_path as journey
    spec = {'plc_model': model, 'io_table': [
        {'kind': 'X', 'address': address, 'label': '安全许可正常=1'},
        {'kind': 'Y', 'address': 'Y0', 'label': '输出'}]}
    if typed_level:
        spec['io_bindings'] = [{'binding_id': 'permit', 'kind': 'X', 'address': address,
                                'active_level': 1, 'inactive_level': 0}]
    before = copy.deepcopy(spec)
    provider = SimpleNamespace(profile={'model': 'offline'}, api_key='offline-key')
    monkeypatch.setattr(journey, 'run_case', lambda *args, **kwargs: {
        'actual_requests': [{'model': 'offline', 'messages': []}], 'handoff': {}})
    result = journey.preflight({'case_id': 'fixture'}, spec, provider)
    assert result['input_level_delivery'] == {
        'physical_input_count': 1, 'settled_predicate_count': int(typed_level),
        'missing_level_addresses': [] if typed_level else [address], 'unresolved_binding_ids': []}
    assert result['network_calls'] == 0 and spec == before


@pytest.mark.parametrize('raw,expected', [
    ({'prompt_cache_hit_tokens': 8, 'prompt_cache_miss_tokens': 2}, (8, 2)),
    ({'prompt_tokens_details': {'cached_tokens': 0}}, (0, None)),
    ({'input_tokens_details': {'cached_tokens': 6}}, (6, None)),
    ({'prompt_cache_hit_tokens': 8, 'prompt_tokens_details': {'cached_tokens': 2}}, (8, None)),
    ({}, None), ({'prompt_cache_hit_tokens': True}, None),
    ({'prompt_cache_hit_tokens': 11}, None), ({'prompt_cache_hit_tokens': -1}, None),
    ({'prompt_cache_hit_tokens': 8, 'prompt_cache_miss_tokens': 3}, None),
])
def test_prompt_cache_meter_preserves_reported_values_and_missingness(raw, expected):
    from scripts.benchmark_agent_b import prompt_cache_meter
    measured = prompt_cache_meter({'input_tokens': 10, 'raw_usage': raw})
    assert measured is None if expected is None else (
        measured['hit_tokens'], measured['miss_tokens']) == expected


def test_prompt_cache_summary_weights_reported_tokens_and_excludes_unknown_calls():
    rows = [{'arm': 'automatic', 'end_to_end_ms': 1, 'generation_status': 'completed',
             'behavior': {}, 'attempts': [{'usage': usage}]} for usage in [
        {'input_tokens': 100, 'raw_usage': {'prompt_cache_hit_tokens': 100, 'prompt_cache_miss_tokens': 0}},
        {'input_tokens': 900, 'raw_usage': {'prompt_cache_hit_tokens': 720}},
        {'input_tokens': 8000, 'raw_usage': {}},
    ]]
    measured = summarize(rows)['groups']['automatic']['prompt_cache']
    assert measured == {'known_calls': 2, 'unreported_or_invalid_calls': 1,
        'hit_tokens': 820, 'reported_miss_tokens': None, 'measured_input_tokens': 1000,
        'input_token_hit_rate': 0.82}


def _purpose_cases():
    from pathlib import Path
    return load_cases(Path(__file__).resolve().parents[1] / "benchmarks/agent_b_operand_purpose_cases.jsonl")


def _challenge_cases():
    from pathlib import Path
    return load_cases(Path(__file__).resolve().parents[1] / "benchmarks/agent_b_operand_purpose_challenge_cases.jsonl")


def _mapping_ladder(case, operands=None):
    from application.compact_protocol import expand_compact_ladder
    expected = case['evaluation']
    gate = expected.get('gate', {'op':'LD','args':['X0']})
    contact = {'LD':'NO','LDI':'NC','LDP':'P','LDF':'F'}[gate['op']]
    instruction = ' '.join([expected['opcode'],*(operands or expected['operands'])])
    return expand_compact_ladder({'r':[{'b':[{'i':[contact+' '+gate['args'][0]],'o':[instruction]}]}]})


def test_cmp_four_factor_preflight_uses_identical_specs_and_distinct_delivery():
    from scripts.benchmark_agent_b import FACTORIAL_ARMS,preflight_factorial
    provider=OpenAICompatibleProvider(offline_runtime_profile(),'fixture-key',client=object())
    cases=[c for c in _challenge_cases() if c['evaluation'].get('opcode')=='CMP']
    result=preflight_factorial(cases,provider=provider,evidence_cache={})
    assert result['passed'] and result['network_calls']==0
    for block in result['factorial_blocks']:
        assert list(block['candidate_counts'].values())==[0,3,0,3]
        counts = list(block['relation_counts'].values())
        assert counts[:2] == [0,0] and counts[2] == counts[3] and counts[2] > 0
        case=next(c for c in cases if c['case_id']==block['case_id'])
        for row in block['records']:
            message=json.dumps(row['actual_requests'][0]['messages'],ensure_ascii=False)
            assert ' '.join(['CMP',*case['evaluation']['operands']]) not in message
            delivered={r['dimension']:r['status'] for r in row['handoff']['fact_coverage']['requirements'] if r['dimension'] in
                       {'operation.result_mapping', 'execution.disabled_retention'}}
            assert set(delivered.values())==({'unresolved'} if row['arm'] in FACTORIAL_ARMS[:2] else {'candidate_evidence'})


def test_factorial_schedule_keeps_four_arms_adjacent_and_randomizes_blocks():
    from scripts.benchmark_agent_b import FACTORIAL_ARMS
    cases=[{'case_id':str(i)} for i in range(6)]
    tasks=schedule(cases,FACTORIAL_ARMS,2,20261003,blocked=True)
    assert len(tasks)==48 and tasks==schedule(cases,FACTORIAL_ARMS,2,20261003,blocked=True)
    for start in range(0,len(tasks),4):
        block=tasks[start:start+4]
        assert len({(c['case_id'],r) for c,a,r in block})==1
        assert {a for c,a,r in block}==set(FACTORIAL_ARMS)
    assert len({tuple(a for c,a,r in tasks[start:start+4]) for start in range(0,len(tasks),4)})>1


@pytest.mark.parametrize('left,right',[(a,b) for a in [-32768,-1,0,1,32767] for b in [-32768,-1,0,1,32767]])
def test_cmp_reference_retention_one_hot_and_swap_properties(left,right):
    from itertools import product
    from scripts.benchmark_agent_b import cmp_reference_state
    for previous in product([False,True],repeat=3):
        assert cmp_reference_state(left,right,previous,False)==previous
        active=cmp_reference_state(left,right,previous,True)
        assert sum(active)==1
        assert cmp_reference_state(right,left,previous,True)==active[::-1]
        if left==right:assert active==(False,True,False)


@pytest.mark.parametrize('case',[c for c in _challenge_cases() if c['evaluation'].get('opcode')=='CMP'],ids=lambda c:c['case_id'])
def test_cmp_truth_traces_reject_direction_swap_and_preserve_disabled_states(case):
    from scripts.benchmark_agent_b import evaluate_cmp_behavior
    expectation=case['evaluation']['cmp_behavior']
    report=evaluate_cmp_behavior(expectation,_mapping_ladder(case))
    assert report['status']=='verified' and len(report['traces'])==11
    swapped=list(case['evaluation']['operands']);swapped[0],swapped[1]=swapped[1],swapped[0]
    bad=evaluate_cmp_behavior(expectation,_mapping_ladder(case,swapped))
    assert bad['status']=='failed'
    assert sum(not t['passed'] for t in bad['traces'])==2
    assert all(t['passed'] for t in bad['traces'] if t['id'].startswith('disabled-'))


def test_cmp_task_paraphrase_and_direction_change_have_independent_expectations():
    from scripts.benchmark_agent_b import evaluate_cmp_behavior
    case=copy.deepcopy(next(c for c in _challenge_cases() if c['case_id']=='cmp-negative-reference'))
    ladder=_mapping_ladder(case)
    # Two equivalent task descriptions share the independently frozen truth
    # table. This checks the oracle organization, not live language invariance.
    for summary in ['实测值低于阈值时首位为ON','阈值高于实测值时首位为ON']:
        case['confirmed_spec']['summary']=summary
        assert evaluate_cmp_behavior(case['evaluation']['cmp_behavior'],ladder)['status']=='verified'
    opposite=copy.deepcopy(case['evaluation']['cmp_behavior'])
    for trace in opposite['scenarios']:
        if trace['id'].startswith('enabled-'):trace['expected']=trace['expected'][::-1]
    assert evaluate_cmp_behavior(opposite,ladder)['status']=='failed'
    operands=[case['evaluation']['operands'][1],case['evaluation']['operands'][0],case['evaluation']['operands'][2]]
    assert evaluate_cmp_behavior(opposite,_mapping_ladder(case,operands))['status']=='verified'


def test_cmp_behavior_cannot_pass_without_independent_truth_traces():
    from scripts.benchmark_agent_b import evaluate_cmp_behavior
    case=next(c for c in _challenge_cases() if c['case_id']=='cmp-negative-reference')
    assert evaluate_cmp_behavior({'result_devices':['M610','M611','M612'],'scenarios':[]},_mapping_ladder(case))['status']=='failed'


def test_usage_ablation_preserves_native_slots_verified_owner_and_manual_bytes():
    from knowledge.evidence import KnowledgeContext
    view = {"opcode": "WSFL", "slots": [
        {"position": 1, "symbol": "S", "symbol_status": "source_verified", "value": "D40",
         "purpose_status": "source_verified", "usage_facts": [
             {"facet": "purpose", "value": "original verified purpose", "status": "source_verified", "sources": [{"id": "verified"}]}]},
        {"position": 3, "symbol": "N1", "symbol_status": "source_verified", "purpose_status": "candidate_evidence",
         "usage_facts": [{"facet": "purpose", "value": "new candidate purpose", "status": "candidate_evidence", "source_refs": [{"source": 0}]}]},
    ]}
    original = 'MODEL: FX3U\nOPERAND_SEMANTICS: ' + json.dumps(view) + '\nOriginal manual: do not invert; 17 words, 3 moves.\nS | D | N1 | N2\n'
    context = KnowledgeContext(original, {"records": []})
    stripped = manual_text_context(context)
    value = json.loads(stripped.splitlines()[1].split(': ', 1)[1])
    assert value["slots"][0]["usage_facts"][0]["value"] == "original verified purpose"
    assert value["slots"][1] == {"position": 3, "symbol": "N1", "symbol_status": "source_verified", "purpose_status": "unresolved"}
    assert stripped.splitlines()[0:1] + stripped.splitlines()[2:] == original.splitlines()[0:1] + original.splitlines()[2:]
    assert without_candidate_usage(stripped) == stripped
    assert context == original


@pytest.mark.parametrize("case_id", ["wsfl-a", "wsfl-b", "ivck-a", "tcmp-b", "hold-control"])
def test_paired_preflight_compares_real_final_requests_and_does_not_use_transport(case_id):
    from knowledge import core
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    provider = OpenAICompatibleProvider(offline_runtime_profile(), "fixture-key", client=object())
    case = next(case for case in _purpose_cases() if case["case_id"] == case_id)
    original = copy.deepcopy(case)
    result = preflight_pairs([case], provider=provider, evidence_cache={})
    assert result["passed"] and result["network_calls"] == 0 and case == original
    pair = result["pairs"][0]
    raw, bound = [record["actual_requests"][0] for record in pair["records"]]
    assert {k:v for k,v in raw.items() if k != 'messages'} == {k:v for k,v in bound.items() if k != 'messages'}
    for a,b in zip(raw['messages'],bound['messages']):
        assert a['role'] == b['role']
        # Independent check: all other lines, including complete manual bodies,
        # confirmed specification, CPU facts and examples, match byte for byte.
        def independent_content(text):
            import re
            def remove(match):
                groups = json.loads(match[1])['groups']
                remaining = [g for g in groups if not (g['status'] == 'candidate_evidence' and g['value'].get('facet'))]
                return json.dumps(remaining, ensure_ascii=False, sort_keys=True) if remaining else ''
            result = re.sub(r'(?m)^\[INSTRUCTION FACTS [^\n]+\]\n(\{[^\n]+\})\n\[/INSTRUCTION FACTS\]', remove, text)
            return [line for line in result.splitlines() if line.strip() and not line.startswith('OPERAND_SEMANTICS: ')]
        assert independent_content(a['content']) == independent_content(b['content'])
    if case_id != 'hold-control':
        assert pair['candidate_counts']['manual_text'] == 0 < pair['candidate_counts']['usage_bound']
        supplied = json.dumps(raw['messages'],ensure_ascii=False)
        assert ' '.join([case['evaluation']['opcode'],*case['evaluation']['operands']]) not in supplied
    else:
        assert raw == bound and pair['candidate_counts'] == {'manual_text':0,'usage_bound':0}


@pytest.mark.parametrize("case", _challenge_cases()[:-1], ids=lambda case:case['case_id'])
def test_challenge_preflight_compares_final_requests_without_prefilled_parameters(case):
    provider = OpenAICompatibleProvider(offline_runtime_profile(), 'fixture-key', client=object())
    result = preflight_pairs([case], provider=provider, evidence_cache={})
    assert result['passed'] and result['network_calls'] == 0
    for record in result['pairs'][0]['records']:
        supplied = json.dumps(record['actual_requests'][0]['messages'],ensure_ascii=False)
        assert ' '.join([case['evaluation']['opcode'],*case['evaluation']['operands']]) not in supplied


@pytest.mark.parametrize("case", _purpose_cases()[:-1]+_challenge_cases()[:-1], ids=lambda case:case['case_id'])
def test_independent_mapping_answers_reject_swapped_values(case):
    expected = case['evaluation']
    assert evaluate_synthetic_case(case,{'ladder':_mapping_ladder(case)})['status'] == 'verified'
    swapped = list(expected['operands'])
    swapped[0],swapped[-1] = swapped[-1],swapped[0]
    assert evaluate_synthetic_case(case,{'ladder':_mapping_ladder(case,swapped)})['status'] == 'failed'


@pytest.mark.parametrize('location', ['same_branch','parallel_branch','later_rung'])
@pytest.mark.parametrize('copies', [1,3])
def test_mapping_evaluator_counts_equal_call_occurrences(location,copies):
    case = next(row for row in _purpose_cases() if row['case_id']=='wsfl-a')
    ladder = _mapping_ladder(case)
    rung = ladder['rungs'][0]
    branch = rung['branches'][0]
    for _ in range(copies):
        if location=='same_branch':
            branch['outputs'].append(copy.deepcopy(branch['outputs'][0]))
        elif location=='parallel_branch':
            rung['branches'].append(copy.deepcopy(branch))
        else:
            ladder['rungs'].append(copy.deepcopy(rung))
    result = evaluate_synthetic_case(case,{'ladder':ladder})
    assert result['status']=='failed' and result['call_count']==copies+1
    assert result['checks']['operand_mapping'] is True
    assert result['checks']['exactly_one_designated_call'] is False
    assert len(result['actual'])==copies+1


def test_mapping_evaluator_rejects_missing_designated_call():
    case = next(row for row in _purpose_cases() if row['case_id']=='cmp-b')
    ladder = _mapping_ladder(case)
    ladder['rungs'][0]['branches'][0]['outputs'].clear()
    result = evaluate_synthetic_case(case,{'ladder':ladder})
    assert result['status']=='failed' and result['call_count']==result['output_count']==0
    assert result['checks']['exactly_one_designated_call'] is False


@pytest.mark.parametrize('extra', [
    {'type':'COIL','address':'M480'},
    {'type':'COIL','address':'Y2'},
    {'type':'APP_INSTR','opcode':'MOV','operands':['K0','D610']},
    {'type':'APP_INSTR','opcode':'MOV','operands':['K0','D999']},
    {'type':'APP_INSTR','opcode':'RST','operands':['M480']},
    {'type':'APP_INSTR','opcode':'SET','operands':['Y2']},
    {'type':'TIMER','address':'T0','value':'K10'},
    {'type':'COUNTER','address':'C0','value':'K10'},
])
@pytest.mark.parametrize('before', [False,True])
def test_mapping_evaluator_rejects_any_additional_output_even_in_the_result_region(extra,before):
    case = next(row for row in _purpose_cases() if row['case_id']=='cmp-b')
    ladder = _mapping_ladder(case)
    outputs = ladder['rungs'][0]['branches'][0]['outputs']
    outputs.insert(0 if before else 1,extra)
    result = evaluate_synthetic_case(case,{'ladder':ladder})
    assert result['status']=='failed' and result['output_count']==2
    assert result['checks']['operand_mapping'] is result['checks']['exactly_one_designated_call'] is True
    assert result['checks']['no_additional_outputs'] is False


@pytest.mark.parametrize('wrong', ['address','polarity','ungated','duplicate_contact'])
def test_mapping_evaluator_uses_independent_gate_expectation(wrong):
    case = next(row for row in _challenge_cases() if row['case_id']=='cmp-low-limit-off')
    ladder = _mapping_ladder(case)
    inputs = ladder['rungs'][0]['branches'][0]['inputs']
    if wrong=='address': inputs[0]['address']='X4'
    elif wrong=='polarity': inputs[0]['type']='NO'
    elif wrong=='ungated': inputs.clear()
    else: inputs.append(copy.deepcopy(inputs[0]))
    result = evaluate_synthetic_case(case,{'ladder':ladder})
    assert result['status']=='failed'
    assert result['checks']['operand_mapping'] is True
    assert result['checks']['specified_direct_gate'] is False


@pytest.mark.parametrize('extra,verified', [('NC M8024', True), ('NO M8024', False),
                                           ('NC M10', False)])
def test_independent_mapping_gate_honors_only_explicit_case_assumptions(extra, verified):
    from application.compact_protocol import expand_compact_ladder
    from pathlib import Path
    case = next(json.loads(row) for row in (Path(__file__).resolve().parents[1] /
                'benchmarks/agent_b_instruction_effect_cases.jsonl').read_text(encoding='utf-8').splitlines()
                if json.loads(row)['case_id'] == 'effect-bmov')
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0', extra], 'o': ['BMOV D20 D300 K7']}]}]})
    receipt = evaluate_synthetic_case(case, {'ladder': ladder})
    assert (receipt['status'] == 'verified') is verified


def test_binding_summary_preserves_matched_first_failures_and_stage_use():
    rows = []
    for repeat, baseline_ok, bound_ok in [(0, False, True), (1, True, False)]:
        for arm, usable in [('native_parameters', baseline_ok), ('core_binding', bound_ok)]:
            rows.append({'case_id': 'synthetic', 'repeat': repeat, 'arm': arm,
                'generation_status': 'completed' if usable else 'failed', 'behavior': {},
                'attempts': [{'usage': {}}], 'end_to_end_ms': 10 + repeat,
                'first_candidate': {'usable': usable}, 'operation_binding': {'receipts': [
                    {'binding_mode': 'operation_reference'}] if usable else []}})
    receipt = summarize(rows)
    pairs = receipt['binding_contrasts']
    assert len(pairs) == 2 and [(p['baseline_usable'], p['treatment_usable']) for p in pairs] == [(False, True), (True, False)]
    assert [p['binding_reference_used'] for p in pairs] == [True, False]
    assert receipt['groups']['core_binding']['total_tokens']['median'] is None


def test_native_direction_failure_is_rebound_and_original_model_error_stays_measurable(monkeypatch):
    from knowledge.evidence import KnowledgeContext
    from pathlib import Path
    import application.generation_agent as agent
    monkeypatch.setattr(agent, '_build_knowledge_context', lambda *a, **k: KnowledgeContext(''))
    case = next(json.loads(row) for row in (Path(__file__).resolve().parents[1] /
        'benchmarks/agent_b_instruction_effect_cases.jsonl').read_text(encoding='utf-8').splitlines()
        if json.loads(row)['case_id'] == 'effect-cmp')
    calls = []
    class Provider:
        profile = offline_runtime_profile()
        def stream(self, request):
            calls.append(request)
            yield TextDelta(json.dumps({'r': [{'b': [{'i': ['NO X0'], 'o': ['CMP D1450 K-37 M610']}]}]}))
    record = run_case(case, 'native_parameters', provider=Provider())
    first = record['first_candidate']
    assert len(calls) == record['model_calls'] == 1
    assert first['usable'] and first['semantic']['status'] == 'verified'
    assert first['raw_model_candidate']['semantic']['status'] == 'failed'
    assert first['raw_model_candidate']['contract_status'] == 'violated'
    receipt = first['Core_binding']['receipts'][0]
    assert receipt['input_call']['operands'] == ['D1450', 'K-37', 'M610']
    assert receipt['output_call']['operands'] == ['K-37', 'D1450', 'M610']
    totals = summarize([record])['groups']['native_parameters']
    assert totals['first_pass_usable'] == totals['native_parameter_rebindings'] == 1
    assert totals['raw_model_semantic_correct'] == 0
    assert totals['raw_model_native_evaluated_runs'] == 1


def _effect_challenge_cases():
    from pathlib import Path
    return [row for row in map(json.loads, (Path(__file__).resolve().parents[1] /
        'benchmarks/agent_b_instruction_effect_cases.jsonl').read_text(encoding='utf-8').splitlines())
        if row['case_id'].startswith('challenge-')]


def _reviewed_effect_cases():
    from pathlib import Path
    return load_cases(Path(__file__).resolve().parents[1] / 'benchmarks/agent_b_instruction_effect_cases.jsonl')


@pytest.mark.parametrize('case', _reviewed_effect_cases(), ids=lambda c: c['case_id'])
def test_every_reviewed_effect_case_executes_independent_fixed_traces(case):
    evaluation = case['evaluation']
    assert evaluation['effect_traces'] and evaluation['reference']['expected_source']
    result = evaluate_synthetic_case(case, {'ladder': _mapping_ladder(case)})
    assert result['status'] == 'verified', result
    if case['confirmed_spec']['operation_intents'][0]['execution']['trigger'] == 'rising':
        assert any(row['enabled'] and row.get('previous_enabled') is True and not row['executed']
                   for row in evaluation['effect_traces'])


def test_reviewed_effect_coverage_requires_all_forms_and_independent_traces():
    from scripts.benchmark_agent_b import reviewed_effect_case_coverage
    cases = _reviewed_effect_cases()
    coverage = reviewed_effect_case_coverage(cases)
    assert coverage['required_reviewed_forms'] > 60
    assert coverage['represented_reviewed_forms'] == coverage['required_reviewed_forms'] and not coverage['missing']
    missing = reviewed_effect_case_coverage([case for case in cases if case['evaluation']['opcode'] != 'DMULP'])
    assert missing['missing'] == [{'target_model': 'FX3U', 'opcode': 'DMULP'}]
    damaged = copy.deepcopy(cases)
    for case in damaged:
        if case['evaluation']['opcode'] == 'DMULP':
            case['evaluation'].pop('effect_traces')
    assert reviewed_effect_case_coverage(damaged)['missing'] == missing['missing']


def test_reference_trace_sequence_preserves_state_and_previous_enable():
    from scripts.benchmark_agent_b import evaluate_effect_traces
    traces = [
        {'id': 'off', 'sequence': 'counter', 'enabled': False, 'executed': False,
         'memory': {'D1710': 32767}, 'writes': {}, 'memory_after': {'D1710': 32767}},
        {'id': 'first-edge', 'sequence': 'counter', 'enabled': True, 'executed': True,
         'writes': {'D1710': -32768}, 'memory_after': {'D1710': -32768}},
        {'id': 'held', 'sequence': 'counter', 'enabled': True, 'executed': False,
         'writes': {}, 'memory_after': {'D1710': -32768}},
        {'id': 'disabled', 'sequence': 'counter', 'enabled': False, 'executed': False,
         'writes': {}, 'memory_after': {'D1710': -32768}},
        {'id': 'second-edge', 'sequence': 'counter', 'enabled': True, 'executed': True,
         'writes': {'D1710': -32767}, 'memory_after': {'D1710': -32767}},
    ]
    call = {'opcode': 'INCP', 'operands': ['D1710']}
    assert evaluate_effect_traces(traces, [call], 'FX3U')['status'] == 'verified'
    damaged = copy.deepcopy(traces)
    damaged[2]['memory_after']['D1710'] = -32767
    assert evaluate_effect_traces(damaged, [call], 'FX3U')['status'] == 'failed'


@pytest.mark.parametrize('contacts,valid', [
    (['NC M8024', 'NC X5'], True), (['NC X5', 'NC M8024'], True),
    (['NO M8024', 'NC X5'], False), (['NC M8024', 'NO X5'], False),
    (['NC M99', 'NC X5'], False),
])
def test_independent_gate_oracle_accepts_confirmed_true_conditions_in_either_order(contacts, valid):
    from application.compact_protocol import expand_compact_ladder
    case = next(c for c in _effect_challenge_cases() if c['case_id'] == 'challenge-bmov-2')
    evaluation = case['evaluation']
    ladder = expand_compact_ladder({'r': [{'b': [{'i': contacts,
        'o': [' '.join([evaluation['opcode'], *evaluation['operands']])]}]}]})
    receipt = evaluate_synthetic_case(case, {'ladder': ladder})
    assert (receipt['status'] == 'verified') == valid


def test_operation_reference_raw_candidate_requires_Core_instead_of_counting_as_a_native_failure(monkeypatch):
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent, '_build_knowledge_context', lambda *a, **k: KnowledgeContext(''))
    case = next(c for c in _effect_challenge_cases() if c['case_id'] == 'challenge-cmp-1')
    class Provider:
        profile = offline_runtime_profile()
        def stream(self, request):
            yield TextDelta(json.dumps({'r': [{'b': [{'i': ['NO X3'], 'o': ['OP operation']}]}]}))
    record = run_case(case, 'core_binding', provider=Provider())
    assert record['first_candidate']['usable']
    assert record['first_candidate']['raw_model_candidate']['status'] == 'operation_reference'
    totals = summarize([record])['groups']['core_binding']
    assert totals['raw_model_semantic_correct'] == totals['raw_model_native_evaluated_runs'] == 0


@pytest.mark.parametrize('case', _effect_challenge_cases(), ids=lambda case: case['case_id'])
def test_effect_challenges_execute_independent_fixed_result_traces(case):
    from application.compact_protocol import expand_compact_ladder
    expected = case['evaluation']
    contact = ('NC ' if expected['gate']['op'] == 'LDI' else 'NO ') + expected['gate']['args'][0]
    ladder = expand_compact_ladder({'r': [{'b': [{'i': [contact],
        'o': [' '.join([expected['opcode'], *expected['operands']])]}]}]})
    receipt = evaluate_synthetic_case(case, {'ladder': ladder})
    assert receipt['status'] == 'verified'
    assert receipt['effect_traces']['status'] == 'verified'
    assert receipt['effect_traces']['hardware_effect'] == 'not_tested'


def test_independent_result_traces_detect_a_bad_definition_even_when_operand_mapping_matches(monkeypatch):
    from copy import deepcopy
    from dataclasses import replace
    from types import SimpleNamespace
    import plc.instructions as instructions
    from application.compact_protocol import expand_compact_ladder
    case = next(c for c in _effect_challenge_cases() if c['case_id'] == 'challenge-cmp-1')
    form = instructions.DEFAULT_INSTRUCTION_REGISTRY.resolve_form('CMP', cpu='FX3U')
    facts = []
    for g in form.spec.definition_facts:
        if g.id == 'behavior:effect':
            bad = deepcopy(g.value)
            bad['outputs'][0]['expression']['op'] = 'lt'
            facts.append(replace(g, value=bad))
        else:
            facts.append(g)
    bad_form = replace(form, spec=replace(form.spec, definition_facts=tuple(facts)))
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', SimpleNamespace(resolve_form=lambda *a, **k: bad_form))
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X3'], 'o': ['CMP K-32767 D2815 M1120']}]}]})
    receipt = evaluate_synthetic_case(case, {'ladder': ladder})
    assert receipt['checks']['operand_mapping']
    assert not receipt['checks']['effect_traces'] and receipt['status'] == 'failed'


@pytest.mark.parametrize('case_id,position,literal,correct', [
    ('cmp-negative-reference',0,'HFFDB',True),
    ('cmp-negative-reference',0,'h0ffdb',True),
    ('cmp-negative-reference',0,'K65500',False),
    ('cmp-negative-reference',0,'K65499',False),
    ('cmp-negative-measurement',1,'HFFAC',True),
    ('cmp-low-limit-off',0,'HC003',True),
    ('cmp-hex-upper-limit',1,'K32545',True),
    ('deco-32-flags',2,'H5',True),
    ('deco-32-flags',2,'K32',False),
])
def test_mapping_evaluator_uses_only_reviewed_constant_aliases(case_id,position,literal,correct):
    case = next(row for row in _challenge_cases() if row['case_id']==case_id)
    operands = list(case['evaluation']['operands'])
    operands[position]=literal
    result = evaluate_synthetic_case(case,{'ladder':_mapping_ladder(case,operands)})
    assert (result['status']=='verified') is correct


@pytest.mark.parametrize('outputs', [[],['CMP K215 D610 M480','COIL M480'],['CMP K215 D610 M480','CMP K215 D610 M480']])
def test_first_candidate_applies_single_call_and_side_effect_checks(monkeypatch,outputs):
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent,'_build_knowledge_context',lambda *a,**k:KnowledgeContext(''))
    case = next(row for row in _purpose_cases() if row['case_id']=='cmp-b')
    class Provider:
        profile = offline_runtime_profile()
        def stream(self,request):
            yield TextDelta(json.dumps({'r':[{'b':[{'i':['NO X4'],'o':outputs}]}]}))
    record = run_case(case,'usage_bound',provider=Provider())
    # An empty output can be rejected by the existing decoder before an
    # independent semantic evaluation; it still cannot become a usable pass.
    assert record['first_candidate']['semantic']['status']!='verified'
    assert not record['first_candidate']['usable']


def test_mapping_fixture_refuses_prepopulated_operands(tmp_path):
    case = _purpose_cases()[0]
    case['confirmed_spec']['selected_approach']['instruction_instances'] = [{'opcode':'SFTL','operands':case['evaluation']['operands']}]
    path = tmp_path/'cases.jsonl'
    path.write_text(json.dumps(case),encoding='utf-8')
    with pytest.raises(ValueError,match='must not prefill'):
        load_cases(path)


def test_first_candidate_does_not_count_a_later_duplicate_as_a_repair(monkeypatch):
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent,'_build_knowledge_context',lambda *a,**k:KnowledgeContext(''))
    case = next(case for case in _purpose_cases() if case['case_id']=='wsfl-a')
    class Provider:
        profile = offline_runtime_profile()
        def stream(self,request):
            yield TextDelta('{"r":[{"b":[{"i":["P X0"],"o":["WSFL D40 D300 K3 K17"]}]}]}')
            yield TextDelta('{"r":[{"b":[{"i":["P X0"],"o":["WSFL D40 D300 K17 K3"]}]}]}')
            yield Usage(10,20,30,None)
    record = run_case(case,'usage_bound',provider=Provider())
    assert record['model_calls'] == 1 and record['first_candidate']['retries'] == 0
    assert record['first_candidate']['semantic']['status'] == 'failed'
    assert not record['first_candidate']['usable']
    assert 'K17 K3' in record['attempts'][0]['raw_content']


def test_required_endpoint_rejects_wrong_saved_profile_before_transport(tmp_path,monkeypatch):
    import model_runtime.provider as providers
    provider = SimpleNamespace(profile={'baseUrl':'https://other.invalid/v1'})
    monkeypatch.setattr(providers,'get_active_provider',lambda:provider)
    path = tmp_path/'cases.jsonl'
    path.write_text(json.dumps({'case_id':'control','confirmed_spec':{'summary':'hold'}}),encoding='utf-8')
    with pytest.raises(SystemExit):
        main([str(path),'--live','--require-endpoint','https://api-inference.modelscope.cn/v1','--output',str(tmp_path/'result.jsonl')])
    assert not (tmp_path/'result.jsonl').exists()


def test_json_framing_does_not_drop_reasoning_or_usage_and_observation_sees_duplicates():
    class Provider:
        profile = {}
        def stream(self, request):
            yield ReasoningDelta("先核对事实")
            yield TextDelta('{"r":[]}')
            yield TextDelta('{"r":["duplicate"]}')
            yield ReasoningDelta("保留提供商实际返回的后续记录")
            yield Usage(1, 2, 3, 1)
    observed = ObservedProvider(Provider())
    request = ModelRequest((UserMessage("fixture"),), options={"reasoning_effort": "high"}, stream=True)
    events = list(_FirstJSONObjectProvider(observed).stream(request))
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == '{"r":[]}'
    assert len([event for event in events if isinstance(event, ReasoningDelta)]) == 2
    assert any(isinstance(event, Usage) and event.reasoning_tokens == 1 for event in events)
    attempt = observed.attempts[0]
    assert 'duplicate' in attempt["raw_content"] and attempt["transport_completed"]
    assert attempt["first_content_ms"] <= attempt["json_complete_ms"] <= attempt["transport_ms"]
    assert request.options == {"reasoning_effort": "high"}


def test_prompt_and_schema_have_one_canonical_required_shape():
    import jsonschema
    example = canonical_compact_example()
    jsonschema.validate(example, compact_response_schema())
    schema = compact_response_schema()
    prompt = compact_protocol_prompt()
    rung = schema["properties"]["r"]["items"]
    branch = rung["properties"]["b"]["items"]
    assert "顶层对象必填字段：" + "、".join(schema["required"]) in prompt
    assert "每个梯级必填字段：" + "、".join(rung["required"]) in prompt
    assert "每个输出分支必填字段：" + "、".join(branch["required"]) in prompt
    assert '"root"' not in prompt
    assert "这些字段不省略" in compact_protocol_prompt()


@pytest.mark.parametrize("root_key", ["r", "root"])
def test_runner_uses_real_single_call_path_without_changing_effort(monkeypatch, root_key):
    from test_confirmed_input_protocol import old_confirmed_spec, compact
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: KnowledgeContext("", {"records": []}))
    class Provider:
        profile = offline_runtime_profile()
        def __init__(self):
            self.requests = []
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({root_key: compact()["r"]}))
            yield Usage(10, 20, 30, 12)
    provider = Provider()
    record = run_case({"case_id": "hold", "confirmed_spec": old_confirmed_spec()}, "automatic", provider=provider, effort="high")
    assert record["generation_status"] == "completed", record
    assert record["behavior"] == {"status": "not_covered", "reason": "no_behavior_evaluator"}
    assert record["structural_valid"] and record["semantic_validation"]["legacy_compatibility"] is True
    assert record["model_calls"] == 1 and provider.requests[0].max_retries == 0
    assert "reasoning_effort" not in provider.requests[0].options
    assert record["attempts"][0]["usage"]["reasoning_tokens"] == 12
    assert agent._build_knowledge_context("fixture").manifest == {"records": []}


@pytest.mark.parametrize("behavior_status", ["verified", "failed", "not_covered"])
def test_benchmark_keeps_semantic_receipts_separate_from_behavior(monkeypatch, behavior_status):
    from test_generation_agent_boundary import OneShotProvider, _spec
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    specification = _spec()
    specification["selected_approach"] = {
        "approach_id": "direct", "name": "direct",
        "implementation_semantics": [{"kind": "structure", "status": "required", "value": "direct_logic"}],
        "explicit_user_constraints": {"required_opcodes": ["MOV"]},
    }
    inspected = []
    def evaluate(case, result):
        inspected.append(result["ladder"])
        return {"status": behavior_status, "source": "offline_evaluator"}
    provider = OneShotProvider()
    record = run_case({"case_id": "semantic-receipt", "confirmed_spec": specification},
                      "automatic", provider=provider, evaluator=evaluate)
    assert record["generation_status"] == "completed", record
    assert record["structural_valid"]
    assert record["semantic_validation"]["status"] == "violated"
    assert record["semantic_validation"]["violations"]
    assert record["behavior"] == {"status": behavior_status, "source": "offline_evaluator"}
    assert len(inspected) == len(provider.requests) == record["model_calls"] == 1


def test_dry_run_never_loads_provider_or_calls_model(tmp_path, monkeypatch, capsys):
    import model_runtime.provider as provider
    monkeypatch.setattr(provider, "get_active_provider", lambda: pytest.fail("Dry-run must not access credentials"))
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"case_id": "fixture", "confirmed_spec": {"summary": "example"}}), encoding="utf-8")
    assert main([str(path), "--repeat", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["scheduled_runs"] == 4


def test_failure_and_unknown_usage_are_not_silently_dropped():
    rows = [{"arm": "automatic", "end_to_end_ms": 100, "generation_status": "failed",
             "behavior": {"status": "not_covered"}, "attempts": [{"usage": None}]},
            {"arm": "automatic", "end_to_end_ms": 200, "generation_status": "completed",
             "behavior": {"status": "verified"}, "attempts": [{"usage": {"reasoning_tokens": 20}}]}]
    result = summarize(rows)
    assert result["groups"]["automatic"]["runs"] == 2
    assert result["groups"]["automatic"]["reasoning_usage_known_runs"] == 1
    assert result["performance_acceptance"] == "not_established"
    tasks = schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)
    assert len(tasks) == 12 and tasks == schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)


def test_transcript_metering_and_custom_headers_remain_credential_redacted(tmp_path):
    from shared.tracing import sanitize, record_model_response
    headers = {"extra_headers": {"X-Api-Key": "nonstandard-secret", "Cookie": "session-secret"}}
    assert sanitize(headers) == {"extra_headers": {"X-Api-Key": "<redacted>", "Cookie": "<redacted>"}}
    request = ModelRequest((UserMessage("fixture"),))
    from model_runtime.provider import AssistantMessage, RawModelResponse
    raw = RawModelResponse(AssistantMessage("{}"), Usage(10, 20, 30, 12, {"completion_tokens_details": {"reasoning_tokens": 12}}), (), False, "")
    assert record_model_response(tmp_path, "job_fixture", 1, 1, raw, request)
    row = json.loads((tmp_path / "diagnostics/job_fixture.transcript.jsonl").read_text())
    assert row["usage"]["reasoning_tokens"] == 12
    assert row["usage"]["raw_usage"]["completion_tokens_details"]["reasoning_tokens"] == 12


@pytest.mark.parametrize("case_id", ["design-conveyor", "typed-parameter-identity", "user-edited-prose", "legacy-generation-view", "historical-review-recovery", "servo-typed-handoff"])
def test_offline_context_replay_uses_real_components_and_single_completions(case_id):
    from scripts.context_replay import load_cases, run_case
    case = next(row for row in load_cases() if row["case_id"] == case_id)
    result = run_case(case, include_content=True)
    assert result["passed"], result
    assert result["real_model_calls"] == 0
    assert result["provider_fixture_calls"] == (2 if "analysis" in case else 1)
    assert result["live_latency_improvement"] == "not_measured"
    spec = result["content"]["generation_spec"]
    if case_id == "typed-parameter-identity":
        params = {p["id"]: p for p in spec["parameters"]}
        assert params["duration"]["value"] == 0 and type(params["duration"]["value"]) is int
        assert params["enabled"]["value"] is False
        assert params["transport_mode"]["semantic_key"] == "transport.mode"
    if case_id == "servo-typed-handoff":
        assert "module" not in {p["id"] for p in spec["parameters"]}
        assert all(b["active_level"] == 1 for b in spec["io_bindings"] if b["kind"] == "X")
        assert len([b for b in spec["io_bindings"] if b["kind"] == "X"]) == 3
    if case_id == "legacy-generation-view":
        assert spec["summary"] == case["confirmed_spec"]["summary"]
        assert "note" not in spec["parameters"][0]


def test_offline_replay_refuses_network_and_ambiguous_archives(tmp_path):
    import socket
    import zipfile
    from scripts.context_replay import offline_environment, archive_case
    with offline_environment() as attempts:
        with pytest.raises(RuntimeError):
            socket.create_connection(("provider.invalid", 443))
    assert len(attempts) == 1
    path = tmp_path / "diagnostic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("job.json", "{}")
        archive.writestr("transcript.jsonl", "")
    with pytest.raises(ValueError, match="confirmed specification"):
        archive_case(path)


def test_offline_archive_reader_never_uses_recorded_credentials_or_reconstructs_analysis(tmp_path):
    import zipfile
    from scripts.context_replay import archive_case
    path = tmp_path / "diagnostic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("job.json", json.dumps({"snapshot": {"project": {"confirmed_spec": {"summary": "保留"}}, "api_key": "SECRET_DO_NOT_USE"}}))
        archive.writestr("transcript.jsonl", json.dumps({"event": "model_response", "content": '{"r":[]}'}))
    case = archive_case(path)
    assert "SECRET_DO_NOT_USE" not in json.dumps(case)
    assert "analysis" not in case and case["capture_scope"].startswith("generation_only")


def test_archive_replay_result_is_attached_atomically_and_replaces_prior_member(tmp_path):
    import zipfile
    from scripts.context_replay import REPLAY_MEMBER, attach_replay_result
    path = tmp_path / "gxworks-interaction.zip"
    job = json.dumps({"id": "job_fixture", "snapshot": {"project": {"confirmed_spec": {"summary": "keep"}}}})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("job.json", job)
        archive.writestr("transcript.jsonl", "{}\n")
        archive.writestr("README.txt", "operator export")
    attach_replay_result(path, {"schema_version": 1, "passed": False, "marker": 1})
    attach_replay_result(path, {"schema_version": 1, "passed": True, "marker": 2})
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist().count(REPLAY_MEMBER) == 1
        assert archive.read("job.json").decode() == job
        assert archive.read("README.txt").decode() == "operator export"
        assert json.loads(archive.read(REPLAY_MEMBER)) == {
            "schema_version": 1, "passed": True, "marker": 2
        }
    assert not list(tmp_path.glob("*.replay.tmp"))


def test_archive_cli_writes_back_by_default_but_output_keeps_archive_read_only(tmp_path, monkeypatch, capsys):
    import zipfile
    import scripts.context_replay as replay

    def make_archive(name):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("job.json", "{}")
            archive.writestr("transcript.jsonl", "{}\n")
        return path

    monkeypatch.setattr(replay, "archive_case", lambda path: {"case_id": "operator_archive"})
    monkeypatch.setattr(replay, "run_case", lambda case, include_content=False: {
        "case_id": "operator_archive", "passed": True
    })

    attached = make_archive("attached.zip")
    assert replay.main(["--archive", str(attached)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["member"] == replay.REPLAY_MEMBER and status["passed"] is True
    with zipfile.ZipFile(attached) as archive:
        assert replay.REPLAY_MEMBER in archive.namelist()

    detached_source = make_archive("detached-source.zip")
    detached = tmp_path / "detached.json"
    assert replay.main(["--archive", str(detached_source), "--output", str(detached)]) == 0
    assert json.loads(detached.read_text(encoding="utf-8"))["passed"] is True
    with zipfile.ZipFile(detached_source) as archive:
        assert replay.REPLAY_MEMBER not in archive.namelist()


@pytest.mark.parametrize("operation", ["getaddrinfo", "gethostbyname", "gethostbyname_ex", "sendto"])
def test_offline_replay_blocks_dns_and_datagram_paths_and_restores_patches(operation):
    import socket
    from scripts.context_replay import offline_environment
    owner = socket.socket if operation == "sendto" else socket
    original = getattr(owner, operation)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        with offline_environment() as attempts:
            with pytest.raises(RuntimeError, match="Offline replay forbids"):
                if operation == "sendto":
                    channel.sendto(b"fixture", ("127.0.0.1", 9))
                elif operation == "getaddrinfo":
                    socket.getaddrinfo("provider.invalid", 443)
                else:
                    getattr(socket, operation)("provider.invalid")
        assert attempts == ["blocked_network_or_provider_access"]
    assert getattr(owner, operation) is original


@pytest.mark.parametrize("include_content", [False, True])
def test_failed_candidate_replay_keeps_consumed_response_and_evidence_counts(include_content):
    from scripts.context_replay import load_cases, run_case as replay_case
    case = copy.deepcopy(next(row for row in load_cases() if row["case_id"] == "legacy-generation-view"))
    # Synthetic malformed instruction, never a deployable motion program.
    case["completion"] = {"r": [{"h": None, "s": [], "b": [
        {"i": ["NO M0"], "o": ["ZRN X0 X0 Y0 K2000 K500"]}
    ]}]}
    before = copy.deepcopy(case)
    report = replay_case(case, include_content=include_content)
    assert not report["passed"]
    assert report["failure_stage"] == "candidate_validation"
    assert report["error_type"] == "PLCJsonValidationError"
    assert report["provider_fixture_calls"] == 1 and report["real_model_calls"] == 0
    assert report["generation_evidence_count"] > 0
    assert report["generation_prompt_chars"] > 0
    assert report["checks"]["no_network_attempts"] and report["checks"]["input_unchanged"]
    assert case == before
    if include_content:
        assert "ZRN" in report["content"]["error_message"]
        assert "4 operand" in report["content"]["error_message"]
        assert report["content"]["generation_evidence"]
    else:
        assert "content" not in report and "error_message" not in report


def test_replay_failure_before_provider_creation_has_zero_consumptions(monkeypatch):
    from scripts.context_replay import run_case as replay_case
    import knowledge.scope as scope
    def unavailable(*args, **kwargs):
        raise RuntimeError("Bearer private-test-secret-123456")
    monkeypatch.setattr(scope, "retrieval_plan", unavailable)
    report = replay_case({"case_id": "setup-failure"})
    assert not report["passed"] and report["failure_stage"] == "runtime_setup"
    assert report["provider_fixture_calls"] == report["generation_evidence_count"] == 0
    assert "private-test-secret" not in json.dumps(report)


def _curated_fixture():
    return {"review": {"performed_by": "Codex", "human_signoff": False,
        "method": "Read original source and inspect table", "completed_at": "2026-10-05T00:00:00Z"},
        "required_fact_ids": ["timer.base"], "records": [{"id": "source.fixture",
        "source": "synthetic_manual.pdf", "manual_number": "fixture", "revision": "R",
        "pdf_page": 100, "page": "98", "plc_models": ["FX3U"], "fact_ids": ["timer.base"],
        "conditions": ["ordinary timer"], "text": "FX3U ordinary timer; 100ms count time."}]}


def test_evidence_diagnostic_dry_run_is_twelve_cases_and_108_jobs(tmp_path, monkeypatch, capsys):
    from scripts.benchmark_user_path import main as journey_main
    import model_runtime.provider as providers
    monkeypatch.setattr(providers, "get_active_provider", lambda *args: pytest.fail("loaded credentials"))
    assert journey_main(["--experiment", "evidence-value", "--phase", "generation",
        "--profile-id", "fixture", "--output", str(tmp_path / "private")]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["cases"]) == 12 and plan["scheduled_jobs"] == 108
    assert plan["generation_arms"] == ["no_manual", "current_rag", "curated_evidence"]
    assert plan["network_calls"] == 0 and not (tmp_path / "private").exists()


@pytest.mark.parametrize("defect", ["human", "cpu", "condition", "fact", "page", "filled", "program", "duplicate"])
def test_curated_evidence_rejects_leakage_wrong_cpu_missing_conditions_and_false_signoff(defect):
    from scripts.benchmark_agent_b import curated_evidence_context
    packet = _curated_fixture()
    row = packet["records"][0]
    if defect == "human": packet["review"]["human_signoff"] = True
    elif defect == "cpu": row["plc_models"] = ["FX5U"]
    elif defect == "condition": row["conditions"] = ["missing condition"]
    elif defect == "fact": packet["required_fact_ids"].append("missing.fact")
    elif defect == "page": row["pdf_page"] = 0
    elif defect == "filled": row["text"] += '\n{"operands":["D0","D1"]}'
    elif defect == "program": row["text"] += '\nOP known_answer\n'
    else: packet["records"].append(copy.deepcopy(row))
    with pytest.raises(ValueError):
        curated_evidence_context({"plc_model": "FX3U", "curated_evidence": packet})


def test_evidence_preflight_real_compiler_keeps_core_and_suppresses_examples():
    from scripts.benchmark_user_path import evidence_preflight
    case = copy.deepcopy(_purpose_cases()[0])
    case["curated_evidence"] = _curated_fixture()
    # The fixture is merely a delivery check, not a manually reviewed fact oracle.
    provider = OpenAICompatibleProvider(offline_runtime_profile(), "fixture-key", client=object())
    before = copy.deepcopy(case)
    flight = evidence_preflight(case, case["confirmed_spec"], provider)
    assert flight["passed"] and flight["Core_binding_policy_identical"]
    assert flight["network_calls"] == 0 and case == before
    a, b, c = (flight["requests"][arm] for arm in ("no_manual", "current_rag", "curated_evidence"))
    assert "# Retrieved PLC evidence" not in a["messages"][0]["content"]
    assert "synthetic_manual.pdf" in c["messages"][0]["content"]
    assert a != b and b != c


@pytest.mark.parametrize("defect", ["tuning", "core", "budget", "examples"])
def test_evidence_preflight_rejects_drift_and_budget_loss(defect, monkeypatch):
    from scripts import benchmark_user_path as journey
    from scripts.benchmark_agent_b import curated_evidence_context
    case = {"case_id": "fixture", "plc_model": "FX3U", "curated_evidence": _curated_fixture()}
    context = curated_evidence_context(case)
    def fake_run(case, arm, **kwargs):
        evidence = context if arm != "no_manual" else ""
        request = {"model": "fixture", "temperature": 0.1, "messages": [
            {"role": "system", "content": "common Core" + str(evidence)}]}
        if arm == "curated_evidence":
            if defect == "tuning": request["temperature"] = 0.2
            if defect == "core": request["messages"][0]["content"] += " changed binding"
            if defect == "budget":
                evidence = "truncated"
                request["messages"][0]["content"] = "common Coretruncated"
            if defect == "examples": request["messages"][0]["content"] += "# Routed construction examples"
        return {"actual_requests": [request], "evidence": [{"text": str(evidence), "manifest": {"records": []}}]}
    monkeypatch.setattr(journey, "run_case", fake_run)
    with pytest.raises(ValueError):
        journey.evidence_preflight(case, {}, SimpleNamespace(profile={"model": "fixture"}, api_key="fixture"))


def test_request_check_rejects_before_transport_and_forwards_valid_stream():
    from scripts.benchmark_user_path import RequestCheckedProvider
    calls = []
    class Provider:
        def _request_params(self, request): return {"model": "fixed", "temperature": request.temperature}
        def stream(self, request): calls.append(request); yield TextDelta("{}")
    request = SimpleNamespace(response_contract=SimpleNamespace(name="compact_ladder"), temperature=0.1)
    checked = RequestCheckedProvider(Provider(), lambda: {"model": "fixed", "temperature": 0.1})
    assert len(list(checked.stream(request))) == 1 and len(calls) == 1
    request.temperature = 0.2
    with pytest.raises(ValueError, match="frozen offline preflight"):
        list(checked.stream(request))
    assert len(calls) == 1


def test_case_cluster_statistics_do_not_treat_three_repetitions_as_three_tasks():
    from scripts.benchmark_user_path import case_cluster_test
    cases = [{"case_id": "a"}, {"case_id": "b"}]
    grading = {(c["case_id"], r, arm): {"final": "failed" if arm == "no_manual" else "passed"}
        for c in cases for r in range(3) for arm in ("no_manual", "current_rag")}
    result = case_cluster_test(grading, cases, 3, "current_rag", "final")
    assert result["eligible_cases"] == 2 and result["mean_difference"] == 1
    assert result["two_sided_p"] == 0.5  # two independent cases, not six independent repeats
    grading[("a", 1, "current_rag")]["final"] = "unverified"
    result = case_cluster_test(grading, cases, 3, "current_rag", "final")
    assert result["excluded_cases"] == ["a"] and result["two_sided_p"] == 1


@pytest.mark.parametrize("baseline,treatment,expected", [
    ("passed", "failed", "harm"), ("failed", "passed", "rescue"),
    ("passed", "passed", "both_pass"), ("not_delivered", "failed", "both_fail"),
    ("unverified", "passed", "undetermined"), ("missing", "failed", "undetermined")])
def test_paired_whole_program_outcomes_preserve_unknowns(baseline, treatment, expected):
    from scripts.benchmark_user_path import paired_outcome
    assert paired_outcome(baseline, treatment) == expected


def test_resume_never_repeats_started_jobs_and_recovers_blind_export(tmp_path, monkeypatch):
    import zipfile
    from scripts import benchmark_user_path as journey
    identity = {"profile_id": "fixture", "profile_model": "model", "endpoint": "endpoint",
        "user_model_settings": {}, "seed": 7}
    case = {"case_id": "a", "category": "ordinary", "name": "a", "plc_model": "FX3U",
        "confirmed_spec": {}, "acceptance": []}
    tasks = [{"case_id": "a", "arm": "no_manual", "repeat": r, "block": r,
        "run_id": f"a.{r}", "blind_id": f"blind{r}"} for r in range(3)]
    journey.write_new(tmp_path / "experiment.json", {"identity": identity, "cases": [case],
        "tasks": tasks, "expected_requests": {}})
    with zipfile.ZipFile(tmp_path / "materials_snapshot.zip", "w") as archive:
        archive.write(tmp_path / "experiment.json", "experiment.json")
    journey.write_new(tmp_path / "runs/a.0.started.json", {"started": True})
    completed = {"first_candidate": {}, "job": {"status": "failed"}, "attempts": []}
    journey.write_new(tmp_path / "runs/a.1.json", completed)
    calls = []
    class OfflineJourney:
        def __init__(self, *args, **kwargs):
            self.service = SimpleNamespace(create_project=lambda **kwargs: {"id": "project"},
                set_spec=lambda *args: {"valid": True, "spec": {}})
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def job(self, *args, **kwargs): calls.append(kwargs["repeat"]); return copy.deepcopy(completed)
    monkeypatch.setattr(journey, "Journey", OfflineJourney)
    monkeypatch.setattr(journey, "assert_frozen_code", lambda directory: None)
    provider = SimpleNamespace(profile={"id": "fixture", "model": "model", "baseUrl": "endpoint", "userModelSettings": {}})
    journey.run_evidence_experiment(tmp_path, provider)
    assert calls == [2] and (tmp_path / "blind/blind1.json").exists()
    journey.run_evidence_experiment(tmp_path, provider)
    assert calls == [2]
    frozen = json.loads((tmp_path / "experiment.json").read_text())
    frozen["cases"][0]["confirmed_spec"] = {"summary": "drift"}
    (tmp_path / "experiment.json").write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="schedule changed"):
        journey.run_evidence_experiment(tmp_path, provider)


@pytest.mark.parametrize("defect", ["duplicate", "missing_stage", "empty_stage", "empty_review"])
def test_diagnostic_summary_requires_every_criterion_and_retains_missing_jobs(tmp_path, defect):
    from scripts import benchmark_user_path as journey
    case = {"case_id": "a", "category": "ordinary", "acceptance": [{"id": "safety", "requirement": "safe"}]}
    tasks = [{"case_id": "a", "arm": arm, "repeat": repeat, "run_id": f"a.{repeat}.{arm}",
              "blind_id": f"blind.{repeat}.{arm}"} for repeat in range(2) for arm in journey.EVIDENCE_ARMS]
    journey.write_new(tmp_path / "experiment.json", {"identity": {"repeats": 2}, "cases": [case], "tasks": tasks})
    for task, status in zip(tasks[:3], ("passed", "failed", "unverified")):
        journey.write_new(tmp_path / "runs" / (task["run_id"] + ".json"), {**task,
            "category": "ordinary", "wall_ms": 10, "attempts": [], "job": {"status": "completed"},
            "output": {"status": "saved", "generation": {"validation_profile": "generation_structural"}},
            "ladder": {"rungs": ["fixture"]}, "first_candidate": {"ladder": {"rungs": ["fixture"]}, "structural_valid": True},
            "provider_results": [{"model": "response-model"}]})
        checks = [{"id": "safety", "status": status, "evidence": "rung 1 static assessment"}]
        journey.write_new(tmp_path / "reviews" / (task["blind_id"] + ".json"), {"blind_id": task["blind_id"],
            "performed_by": "Codex", "human_signoff": False, "native_execution": "not_measured",
            "method": "blinded static review", "completed_at": "2026-10-05T00:00:00Z", "first": checks, "final": checks})
    result = journey.summarize_evidence_experiment(tmp_path)
    a = result["groups"]["all"]["no_manual"]
    assert a["scheduled_runs"] == 2 and a["missing_runs"] == 1 and a["final_full_pass_rate"] == 0.5
    assert result["contrast_counts"]["all"]["current_rag"]["final"]["harm"] == 1
    assert result["contrast_counts"]["all"]["curated_evidence"]["final"]["undetermined"] == 2
    assert result["response_models"] == ["response-model"] and result["response_model_known_jobs"] == 3
    path = tmp_path / "reviews" / (tasks[0]["blind_id"] + ".json")
    review = json.loads(path.read_text())
    if defect == "duplicate":
        review["final"].append(copy.deepcopy(review["final"][0]))
    elif defect == "missing_stage":
        del review["first"]
    elif defect == "empty_stage":
        review["final"] = []
    else:
        review = {}
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="exactly once|provenance"):
        journey.summarize_evidence_experiment(tmp_path)


@pytest.mark.parametrize("output,job_status,ladder,expected", [
    ({"status": "saved", "generation": {}}, "completed", {"rungs": ["fixture"]}, "delivered"),
    ({"status": "saved_invalid", "generation": {}}, "completed", {"rungs": ["fixture"]}, "diagnostic_only"),
    ({"status": "saved", "generation": {"diagnostic_only": True}}, "completed", {"rungs": ["fixture"]}, "diagnostic_only"),
    ({"status": "saved", "generation": {"validation": {"status": "invalid_candidate"}}}, "completed", {"rungs": ["fixture"]}, "diagnostic_only"),
    ({"status": "saved", "generation": {"validation_profile": "rejected_diagnostic"}}, "completed", {"rungs": ["fixture"]}, "diagnostic_only"),
    ({"status": "saved", "generation": {"validation": {"profile": "rejected_diagnostic"}}}, "completed", {"rungs": ["fixture"]}, "diagnostic_only"),
    (None, "completed", {"rungs": ["fixture"]}, "unverified"),
    ({"status": "saved", "generation": {}}, "failed", {"rungs": ["fixture"]}, "not_delivered"),
    ({"status": "saved", "generation": {}}, "completed", None, "not_delivered"),
])
def test_evidence_formal_delivery_does_not_confuse_completed_diagnostic_saving(output, job_status, ladder, expected):
    from scripts.benchmark_user_path import final_delivery_status
    assert final_delivery_status({"output": output, "job": {"status": job_status}, "ladder": ladder}) == expected


def test_blind_evidence_export_preserves_diagnostic_program_without_arm_or_retrieval(tmp_path):
    from scripts.benchmark_user_path import export_blind_candidate
    ladder = {"rungs": ["fixture"]}
    export_blind_candidate(tmp_path, {"blind_id": "anonymous", "arm": "curated_evidence"},
        {"plc_model": "FX5U", "confirmed_spec": {}, "acceptance": []},
        {"job": {"status": "completed"}, "output": {"status": "saved_invalid"}, "ladder": ladder,
         "attempts": [], "retrieval": [{"text": "private treatment evidence"}]})
    packet = json.loads((tmp_path / "blind/anonymous.json").read_text(encoding="utf-8"))
    assert packet["final_candidate"]["ladder"] == ladder
    assert packet["final_candidate"]["delivered"] is False
    assert packet["final_candidate"]["delivery_status"] == "diagnostic_only"
    assert "curated_evidence" not in json.dumps(packet) and "private treatment" not in json.dumps(packet)


@pytest.mark.parametrize("output,expected", [
    ({"status": "saved_invalid", "generation": {"repair_attempts": 0}}, "not_delivered"),
    (None, "unverified"),
    ({"status": "saved", "generation": {"repair_attempts": 0}}, "passed"),
])
@pytest.mark.parametrize("structural_valid", [True, False])
def test_evidence_summary_separates_static_full_pass_and_formal_delivery(tmp_path, output, expected, structural_valid):
    from scripts import benchmark_user_path as journey
    task = {"case_id": "a", "arm": "curated_evidence", "repeat": 0, "run_id": "a.0", "blind_id": "anonymous"}
    case = {"case_id": "a", "category": "boundary", "acceptance": [{"id": "timer", "requirement": "correct timing"}]}
    journey.write_new(tmp_path / "experiment.json", {"identity": {"repeats": 1}, "cases": [case], "tasks": [task]})
    journey.write_new(tmp_path / "runs/a.0.json", {**task, "category": "boundary", "wall_ms": 20,
        "attempts": [], "job": {"status": "completed"}, "output": output, "ladder": {"rungs": ["fixture"]},
        "first_candidate": {"ladder": {"rungs": ["fixture"]}, "structural_valid": structural_valid}})
    check = [{"id": "timer", "status": "passed", "evidence": "official source and complete program reviewed"}]
    journey.write_new(tmp_path / "reviews/anonymous.json", {"blind_id": "anonymous", "performed_by": "Codex",
        "human_signoff": False, "native_execution": "not_measured", "method": "blinded static review",
        "completed_at": "2026-10-05T00:00:00Z", "first": check, "final": check})
    group = journey.summarize_evidence_experiment(tmp_path)["groups"]["all"]["curated_evidence"]
    assert group["job_completed"] == 1
    assert group["whole_program_static"]["final"]["passed"] == 1
    assert group["whole_program_static"]["first"]["passed"] == 1
    assert group["first_pass_semantic_correct"] == 1
    assert group["raw_model_semantic_correct"] is None
    assert group["raw_model_semantic_reviewed_runs"] == 0
    assert group["first_usable_rate"] == (1 if structural_valid else 0)
    assert group["whole_program"]["final"][expected] == 1
    assert group["final_full_pass_rate"] == (1 if expected == "passed" else 0)
    assert group["formal_delivery"][journey.final_delivery_status({"output": output, "job": {"status": "completed"}, "ladder": {"rungs": ["fixture"]}})] == 1
    assert group["repair_attempts"]["known_runs"] == (0 if output is None else 1)


def test_evidence_summary_refuses_unblinding_before_anonymous_reviews(tmp_path):
    from scripts import benchmark_user_path as journey
    task = {"case_id": "a", "arm": "no_manual", "repeat": 0, "run_id": "a.0", "blind_id": "anonymous"}
    journey.write_new(tmp_path / "experiment.json", {"identity": {"repeats": 1},
        "cases": [{"case_id": "a", "category": "ordinary", "acceptance": [{"id": "safe"}]}], "tasks": [task]})
    journey.write_new(tmp_path / "runs/a.0.json", {**task, "ladder": {"rungs": ["fixture"]}})
    with pytest.raises(ValueError, match="anonymous reviews"):
        journey.summarize_evidence_experiment(tmp_path)


def test_evidence_request_exposure_marks_identical_current_rag_controls():
    from scripts.benchmark_user_path import request_exposure
    common = {"model": "same", "messages": [{"role": "system", "content": "same Core"}]}
    expected = {"a": {"no_manual": common, "current_rag": copy.deepcopy(common),
        "curated_evidence": {"model": "same", "messages": [{"role": "system", "content": "same Core plus source"}]}}}
    assert request_exposure(expected, "a", "current_rag") == "same_request"
    assert request_exposure(expected, "a", "curated_evidence") == "manual_evidence_added"
    assert request_exposure({}, "a", "current_rag") == "unverified"


def _construction_measurement_fixture():
    case = copy.deepcopy(_purpose_cases()[0])
    spec = case['confirmed_spec']
    spec['behavior_constraints'] = [{'id':'boot','kind':'initialize','status':'confirmed','values':{'M100':False},
        'execution_context':{'program_type':'scan','initial_execution_program':False},'provenance':{'source':'user_authored','evidence':['首扫描清内部状态']}}]
    spec.setdefault('selected_approach',{})['construction_plan'] = {'instances':[
        {'id':'init','requirement_id':'boot','method':'first_scan_isolation'}], 'internal_ranges':[],
        'execution_context':{'program_type':'scan','initial_execution_program':False}}
    return case


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_construction_preflight_keeps_real_compiled_requests_equal_except_mechanism(model):
    from scripts.benchmark_construction import construction_preflight, common_construction_request
    from scripts.benchmark_user_path import CONSTRUCTION_ARMS
    case = _construction_measurement_fixture()
    case['plc_model'] = model
    provider = OpenAICompatibleProvider(offline_runtime_profile(),'fixture-key',client=object())
    flight = construction_preflight(case,case['confirmed_spec'],provider)
    assert flight['passed'] and flight['network_calls'] == 0
    assert flight['candidate_decode_validated'] and flight['runtime_cpu'] == case['plc_model']
    assert flight['candidate_check_cpu_validated'] and flight['complete_relation_and_method_values_validated']
    a,b = (flight['requests'][arm] for arm in CONSTRUCTION_ARMS)
    assert a != b and common_construction_request(a) == common_construction_request(b)
    for missing in ('', '\n# Core construction delivery\nincomplete'):
        with pytest.raises(ValueError): common_construction_request({'messages':[{'content':missing}]})


@pytest.mark.parametrize('damage,message',[
    ('relation','Complete confirmed relations'),
    ('plan','Complete confirmed relations'),
    ('method','Complete construction method'),
])
def test_construction_preflight_rejects_equal_arm_requests_with_ids_but_changed_values(monkeypatch,damage,message):
    from scripts import benchmark_construction as runner
    original = runner.run_case
    def damaged_request(*args,**kwargs):
        record = original(*args,**kwargs)
        wire = record['actual_requests'][0]['messages'][0]
        content = wire['content']
        if damage == 'method':
            block = runner._BLOCK.search(content)
            # Keep every instance ID and the entire common request intact.
            altered = block.group().replace('"method":"first_scan_isolation"','"method":"unsupported"')
            assert altered != block.group()
            wire['content'] = content[:block.start()]+altered+content[block.end():]
        else:
            marker = '\n# Confirmed project specification\n'
            prefix,tail = content.split(marker,1)
            body = tail.lstrip()
            spec,end = json.JSONDecoder().raw_decode(body)
            if damage == 'relation':
                spec['behavior_constraints'][0]['values']['M100'] = True
            else:
                spec['selected_approach']['construction_plan']['instances'][0]['method'] = 'source_history'
            wire['content'] = prefix+marker+json.dumps(spec,ensure_ascii=False)+body[end:]
        return record
    monkeypatch.setattr(runner,'run_case',damaged_request)
    case = _construction_measurement_fixture()
    provider = OpenAICompatibleProvider(offline_runtime_profile(),'fixture-key',client=object())
    with pytest.raises(ValueError,match=message):
        runner.construction_preflight(case,case['confirmed_spec'],provider)


def test_construction_schedule_excludes_all_started_tasks_and_preserves_worker_pairs(tmp_path):
    from scripts.benchmark_construction import pending_construction_tasks
    tasks = [{'run_id':str(i),'block':i//2} for i in range(8)]
    (tmp_path/'runs').mkdir()
    (tmp_path/'runs/0.started.json').write_text('{}')
    (tmp_path/'runs/3.json').write_text('{}')
    frozen = {'tasks':tasks}
    assert [t['run_id'] for t in pending_construction_tasks(tmp_path,frozen,worker_index=0,workers=2)] == ['1','4','5']
    assert [t['run_id'] for t in pending_construction_tasks(tmp_path,frozen,worker_index=1,workers=2)] == ['2','6','7']
    with pytest.raises(ValueError): pending_construction_tasks(tmp_path,frozen,worker_index=2,workers=2)


def test_construction_offline_recheck_retains_original_observations_and_separates_reference_gate(tmp_path,monkeypatch):
    import zipfile
    from application.compact_protocol import expand_compact_ladder
    from plc.ir import build_plc_ir
    from scripts import benchmark_construction as runner
    from scripts.benchmark_user_path import CONSTRUCTION_ARMS,freeze_code,write_new
    import model_runtime.provider as providers
    import simulator.bounded as bounded
    monkeypatch.setattr(providers,'get_active_provider',lambda *a,**k:pytest.fail('Offline recheck loaded a provider'))
    source,destination = tmp_path/'original',tmp_path/'recheck'
    source.mkdir();destination.mkdir()
    case = _construction_measurement_fixture()
    case['plc_model'] = 'FX5U'
    tasks = [{'case_id':case['case_id'],'arm':arm,'repeat':0,'run_id':arm} for arm in CONSTRUCTION_ARMS]
    write_new(source/'experiment.json',{'cases':[case],'tasks':tasks})
    with zipfile.ZipFile(source/'materials_snapshot.zip','x') as archive:
        archive.write(source/'experiment.json','experiment.json')
    # Independent golden program, not obtained from the constructor.
    ladder = expand_compact_ladder({'r':[{'h':None,'s':[],'b':[{'i':['NO SM402'],'o':['RST M100']}]}]})
    for task in tasks:
        receipt = {'reference_violations':[{'instance_id':'init','reason':'missing_reference'}]
                   if task['arm']==CONSTRUCTION_ARMS[1] else []}
        write_new(source/'runs'/(task['run_id']+'.json'),{'first_candidate':{'ladder':ladder,
            'structural_valid':True,'construction_binding':receipt,'behavior_check':{'status':'unverified'}},
            'program':build_plc_ir(ladder,plc_model='FX5U'),'version_id':'v0001'})
    before = {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
    freeze_code(destination)
    result = runner.recheck_frozen_construction_candidates(source,destination)
    assert result['network_calls'] == 0 and len(result['observations']) == 4
    for observation in result['observations']:
        assert observation['plc_model'] == 'FX5U'
        assert observation['execution_status'] == 'no_violation_found_in_tested_scope'
        assert observation['activation_blocked'] is (observation['arm']==CONSTRUCTION_ARMS[1])
    assert {str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()} == before
    monkeypatch.setattr(bounded,'check_confirmed_behavior',lambda *a,**k:pytest.fail('Completed offline check reran'))
    assert runner.recheck_frozen_construction_candidates(source,destination) == result


def test_construction_summary_preserves_unknown_usage_and_checks_complete_blind_reviews(tmp_path):
    from scripts import benchmark_construction as runner
    from scripts.benchmark_user_path import CONSTRUCTION_ARMS, write_new
    case={'case_id':'a','category':'ordinary','confirmed_spec':{},'acceptance':[{'id':'progress'}]}
    tasks=[{'case_id':'a','arm':arm,'repeat':0,'run_id':arm,'blind_id':arm+'.anonymous'} for arm in CONSTRUCTION_ARMS]
    requests={'a':{arm:{'model':'fixture'} for arm in CONSTRUCTION_ARMS}}
    write_new(tmp_path/'experiment.json',{'identity':{'repeats':1},'cases':[case],'tasks':tasks,'expected_requests':requests})
    for task in tasks:
        write_new(tmp_path/'runs'/(task['run_id']+'.json'),{**task,'wall_ms':None if task['arm']==CONSTRUCTION_ARMS[0] else 12,
            'attempts':[],'actual_requests':[requests['a'][task['arm']]],'confirmed_spec_after':{},
            'first_candidate':{'ladder':{'rungs':['fixture']},'structural_valid':True},'ladder':{'rungs':['fixture']},
            'job':{'status':'completed'},'output':{'status':'saved'}})
    with pytest.raises(ValueError,match='anonymous reviews'): runner.summarize_construction_experiment(tmp_path)
    for task in tasks:
        status='failed' if task['arm']==CONSTRUCTION_ARMS[0] else 'passed'
        checks=[{'id':'progress','status':status,'evidence':'independent complete-program review'}]
        write_new(tmp_path/'reviews'/(task['blind_id']+'.json'),{'blind_id':task['blind_id'],'performed_by':'Codex',
            'human_signoff':False,'native_execution':'not_measured','method':'anonymous static review',
            'completed_at':'2026-10-05T00:00:00Z','first':checks,'final':checks})
    result=runner.summarize_construction_experiment(tmp_path)
    assert result['contrast_counts']['first']['rescue'] == 1
    assert result['case_cluster_tests']['first']['eligible_cases'] == 1
    assert result['case_cluster_tests']['first']['two_sided_p'] == 1
    assert result['arms'][CONSTRUCTION_ARMS[0]]['latency_ms']['median'] is None
    assert result['arms'][CONSTRUCTION_ARMS[1]]['raw_model_semantic_correct'] is None
    assert result['arms'][CONSTRUCTION_ARMS[1]]['prompt_cache']['hit_tokens'] is None
    path=tmp_path/'reviews'/(tasks[0]['blind_id']+'.json')
    review=json.loads(path.read_text()); review['first']*=2; path.write_text(json.dumps(review))
    with pytest.raises(ValueError,match='exactly once'): runner.summarize_construction_experiment(tmp_path)


def _construction_supplement_source(tmp_path, *, cases=1, repeats=1):
    import zipfile
    from scripts import benchmark_construction as runner
    from scripts.benchmark_user_path import CONSTRUCTION_ARMS, write_new
    source=tmp_path/'original'; source.mkdir()
    identity={'experiment':'construction-value','repeats':repeats,'seed':41004,
        'frozen_at_utc':'2026-10-05T08:33:46Z','endpoint':'https://api-inference.modelscope.cn/v1',
        'profile_model':'deepseek-ai/DeepSeek-V4.1-Flash','profile_id':'original','user_model_settings':{'parameters':{}}}
    materials, tasks, requests = [], [], {}
    for index in range(cases):
        case={'case_id':f'case_{index}','category':'ordinary','name':'Synthetic','plc_model':'FX3U',
              'confirmed_spec':{},'acceptance':[{'id':'progress','requirement':'normal progress'}],
              'frozen_evidence':{'text':'source evidence','manifest':{}}}
        materials.append(case)
        requests[case['case_id']]={arm:{'model':identity['profile_model'],
            'messages':[{'role':'system','content':'same evidence and confirmed requirements'}],'stream':True}
            for arm in CONSTRUCTION_ARMS}
        for repeat in range(repeats):
            for arm in CONSTRUCTION_ARMS:
                run_id=f'{case["case_id"]}.{repeat}.{arm}'
                tasks.append({'case_id':case['case_id'],'arm':arm,'repeat':repeat,'block':len(tasks)//2,
                              'run_id':run_id,'blind_id':'original_'+str(len(tasks))})
        write_new(source/'preflight'/(case['case_id']+'.json'),{'complete_relation_and_method_values_validated':True})
    frozen={'identity':identity,'cases':materials,'tasks':tasks,'expected_requests':requests}
    write_new(source/'experiment.json',frozen)
    with zipfile.ZipFile(source/'materials_snapshot.zip','x') as archive:
        archive.write(source/'experiment.json','experiment.json')
    with zipfile.ZipFile(source/'code_snapshot.zip','x') as archive:
        archive.write(runner.ROOT/'resources/plc_models.json','resources/plc_models.json')
    provider=SimpleNamespace(profile={'id':'official','model':'deepseek-flash','baseUrl':'https://api.deepseek.com',
                                    'userModelSettings':{'parameters':{}}}, _request_params=lambda r:{})
    return source,frozen,provider


def _supplement_preview(monkeypatch, frozen, *, damage=None):
    from scripts import benchmark_construction as runner
    def preview(case,spec,provider):
        requests=copy.deepcopy(frozen['expected_requests'][case['case_id']])
        for value in requests.values():
            value['model']=provider.profile['model']
            if damage=='messages':value['messages'][0]['content']='different facts'
            if damage=='parameters':value['temperature']=0.2
        return {'passed':True,'network_calls':0,'requests':requests,'frozen_evidence':case['frozen_evidence']}
    monkeypatch.setattr(runner,'construction_preflight',preview)


def test_construction_supplement_selects_52_and_preserves_success_diagnostics(tmp_path):
    from scripts import benchmark_construction as runner
    from scripts.benchmark_user_path import write_new
    source,frozen,_=_construction_supplement_source(tmp_path,cases=12,repeats=3)
    for index,task in enumerate(frozen['tasks'][:55]):
        write_new(source/'runs'/(task['run_id']+'.started.json'),{'run_id':task['run_id']})
        if index==54:continue
        record=({'job':{'status':'completed'},'first_candidate':{'ladder':{'rungs':['reviewed program']}},
                 'output':{'status':'saved_invalid'}} if index<20 else
                {'job':{'status':'failed'},'transport_errors':[{'type':'RateLimitError','message':'insufficient balance'}]})
        write_new(source/'runs'/(task['run_id']+'.json'),record)
    before={str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()}
    tasks=runner.select_construction_supplement_tasks(source,frozen)
    assert len(tasks)==52 and {t['run_id']for t in tasks}.isdisjoint({t['run_id']for t in frozen['tasks'][:20]})
    assert {s:sum(t['source_status']==s for t in tasks)for s in ('transport_failed','interrupted','unstarted')} == {
        'transport_failed':34,'interrupted':1,'unstarted':17}
    assert all(t['blind_id']!=t['source_blind_id']and t['source_run_id']==t['run_id']for t in tasks)
    assert {str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()}==before


@pytest.mark.parametrize('damage',['messages','parameters'])
def test_construction_supplement_rejects_request_drift_before_freezing(tmp_path,monkeypatch,damage):
    from scripts import benchmark_construction as runner
    source,frozen,provider=_construction_supplement_source(tmp_path)
    _supplement_preview(monkeypatch,frozen,damage=damage)
    destination=tmp_path/'official'
    with pytest.raises(ValueError,match='beyond model identity'):
        runner.prepare_construction_supplement(destination,source,provider)
    assert not (destination/'experiment.json').exists()


def test_official_alignment_omits_only_known_defaults_and_restores_resolver(tmp_path):
    from scripts.benchmark_construction import source_aligned_official_request
    _,frozen,provider=_construction_supplement_source(tmp_path)
    wire={'model':'deepseek-flash','stream':True,'messages':['same'],
          'response_format':{'type':'json_object'},'extra_body':{'thinking':{'type':'enabled'}},'temperature':0.4}
    original=lambda r:copy.deepcopy(wire)
    provider._request_params=original
    with source_aligned_official_request(provider,frozen):
        assert provider._request_params(None)=={'model':'deepseek-flash','stream':True,'messages':['same'],'temperature':0.4}
    assert provider._request_params is original
    assert wire['extra_body']=={'thinking':{'type':'enabled'}}
    provider.profile['userModelSettings']['parameters']={'temperature':0.4}
    with pytest.raises(ValueError,match='saved model parameters'):
        with source_aligned_official_request(provider,frozen):pass


def test_construction_supplement_freezes_originals_and_summary_excludes_cross_endpoint_pair(tmp_path,monkeypatch):
    from scripts import benchmark_construction as runner
    from scripts.benchmark_user_path import CONSTRUCTION_ARMS,write_new
    source,frozen,provider=_construction_supplement_source(tmp_path,cases=2)
    for index,task in enumerate(frozen['tasks']):
        write_new(source/'runs'/(task['run_id']+'.started.json'),task)
        record={**task,'arm':task['arm'],'attempts':[],'actual_requests':[frozen['expected_requests'][task['case_id']][task['arm']]],
                'wall_ms':10,'job':{'status':'failed'},'transport_errors':[{'type':'RateLimitError','message':'insufficient balance'}]}
        if index==0:
            record.update(job={'status':'completed'},transport_errors=[],confirmed_spec_after={},
                first_candidate={'ladder':{'rungs':['independent golden']},'structural_valid':True},
                ladder={'rungs':['independent golden']},output={'status':'saved'})
            checks=[{'id':'progress','status':'passed','evidence':'confirmed progress'}]
            write_new(source/'reviews'/(task['blind_id']+'.json'),{'blind_id':task['blind_id'],'performed_by':'Codex',
                'human_signoff':False,'native_execution':'not_measured','method':'anonymous review','completed_at':'now',
                'first':checks,'final':checks})
        write_new(source/'runs'/(task['run_id']+'.json'),record)
    before={str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()}
    _supplement_preview(monkeypatch,frozen)
    destination=tmp_path/'official'
    prepared=runner.prepare_construction_supplement(destination,source,provider)
    assert prepared['scheduled_jobs']==3 and prepared['retained_source_jobs']==1
    with pytest.raises(ValueError,match='prepared again'):
        runner.prepare_construction_supplement(destination,source,provider)
    supplement=runner._frozen_materials(destination)
    for task in supplement['tasks']:
        status='failed'if task['case_id']=='case_1'and task['arm']==CONSTRUCTION_ARMS[0]else'passed'
        record={**task,'wall_ms':20,'attempts':[],'actual_requests':[supplement['expected_requests'][task['case_id']][task['arm']]],
            'confirmed_spec_after':{},'first_candidate':{'ladder':{'rungs':['golden']},'structural_valid':True},
            'ladder':{'rungs':['golden']},'job':{'status':'completed'},'output':{'status':'saved'}}
        write_new(destination/'runs'/(task['run_id']+'.json'),record)
        checks=[{'id':'progress','status':status,'evidence':'independently frozen requirement'}]
        write_new(destination/'reviews'/(task['blind_id']+'.json'),{'blind_id':task['blind_id'],'performed_by':'Codex',
            'human_signoff':False,'native_execution':'not_measured','method':'anonymous review','completed_at':'now',
            'first':checks,'final':checks})
    result=runner.summarize_construction_experiment(destination)
    assert result['scheduled_jobs']==result['recorded_jobs']==result['reviewed_jobs']==4
    assert result['pair_provider_counts']=={'modelscope':0,'deepseek_official':1,'cross_endpoint':1}
    assert result['contrast_counts']['first']['both_pass']==1
    assert result['same_endpoint_contrast_counts']['first']['both_pass']==0
    assert result['same_endpoint_contrast_counts']['first']['rescue']==1
    assert result['case_cluster_tests']['first']['excluded_cases']==['case_0']
    assert result['case_cluster_tests']['first']['eligible_cases']==1
    assert result['call_history']['original_transport_failed_jobs']==3
    assert result['provider_metrics']['deepseek_official'][CONSTRUCTION_ARMS[0]]['prompt_cache']['hit_tokens']is None
    assert result['provider_metrics']['deepseek_official'][CONSTRUCTION_ARMS[0]]['raw_model_semantic_correct'] is None
    assert result['provider_metrics']['deepseek_official'][CONSTRUCTION_ARMS[0]]['input_tokens']['total'] is None
    assert result['provider_metrics']['deepseek_official'][CONSTRUCTION_ARMS[1]]['whole_program']['first']['passed']==2
    assert 'latency_ms'not in result['arms'][CONSTRUCTION_ARMS[0]]
    assert {str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()}==before
    write_new(source/'runs/new.started.json',{})
    with pytest.raises(ValueError,match='inventory changed'):runner.summarize_construction_experiment(destination)


@pytest.mark.parametrize('kind,message,expected',[
    ('RateLimitError','insufficient balance','quota_exhausted'),
    ('RateLimitError','burst rate exceeded',None),
    ('AuthenticationError','invalid key','authentication_or_permission_failure'),
    ('PermissionDeniedError','not permitted','authentication_or_permission_failure'),
    ('RemoteProtocolError','closed chunked stream',None),
])
def test_construction_fatal_transport_classification(kind,message,expected):
    from scripts.benchmark_construction import fatal_construction_transport_failure
    assert fatal_construction_transport_failure({'transport_errors':[{'type':kind,'message':message}]})==expected


def test_construction_worker_stops_on_quota_and_never_restarts_started_jobs(tmp_path,monkeypatch):
    from scripts import benchmark_construction as runner
    source,frozen,provider=_construction_supplement_source(tmp_path)
    monkeypatch.setattr(runner,'assert_frozen_code',lambda *a:None)
    # Use the original execution loop with a profile matching its frozen identity.
    provider.profile={'id':'original','model':frozen['identity']['profile_model'],
        'baseUrl':frozen['identity']['endpoint'],'userModelSettings':{'parameters':{}}}
    calls=[]
    class Service:
        def create_project(self,**kwargs):return {'id':'p'}
        def set_spec(self,project_id,spec,value):return {'valid':True,'spec':spec}
    class FakeJourney:
        def __init__(self,*a,**k):self.service=Service()
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def job(self,*a,**k):
            calls.append(k)
            return {'job':{'status':'failed'},'attempts':[],'actual_requests':[],
                    'transport_errors':[{'type':'RateLimitError','message':'insufficient balance'}]}
    monkeypatch.setattr(runner,'Journey',FakeJourney)
    result=runner.run_construction_experiment(source,provider)
    assert result=={'stopped':True,'reason':'quota_exhausted'} and len(calls)==1
    assert len(list((source/'runs').glob('*.started.json')))==1
    runner.run_construction_experiment(source,provider)
    assert len(calls)==1
    assert len(runner.pending_construction_tasks(source,frozen))==1

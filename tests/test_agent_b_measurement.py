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
        assert list(block['relation_counts'].values())==[0,0,1,1]
        case=next(c for c in cases if c['case_id']==block['case_id'])
        for row in block['records']:
            message=json.dumps(row['actual_requests'][0]['messages'],ensure_ascii=False)
            assert ' '.join(['CMP',*case['evaluation']['operands']]) not in message
            delivered={r['dimension']:r['status'] for r in row['handoff']['fact_coverage']['requirements'] if '.' in r['dimension']}
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
        assert [line for line in a['content'].splitlines() if not line.startswith('OPERAND_SEMANTICS: ')] == [
            line for line in b['content'].splitlines() if not line.startswith('OPERAND_SEMANTICS: ')]
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

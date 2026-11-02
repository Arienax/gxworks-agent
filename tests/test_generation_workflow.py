import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import application.model_api as api
from application.generation import (
    GenerationDependencies, GenerationError, GenerationRequest, GenerationValidationError, GenerationWorkflow,
)
from shared.i18n import set_language
from model_runtime.provider import ModelProviderError, TextDelta


def _ladder():
    return {
        "device_comments": {"X0": "Input", "Y0": "Output"},
        "rungs": [{"rung_id": 1, "header_element": None, "shared_inputs": [],
                   "branches": [{"branch_id": 1, "y_offset_level": 0,
                                 "inputs": [{"type": "NO", "address": "X0", "label": "Input"}],
                                 "outputs": [{"type": "COIL", "address": "Y0", "label": "Output"}]}]}],
    }


@pytest.mark.parametrize("model", ["FX1N", "Q00J", "Q03UDV", "L02", "FX3U", "FX5U"])
def test_st_shape_validation_receives_selected_cpu(tmp_path, monkeypatch, model):
    import application.generation as generation
    from plc.validation import validate_st_json
    seen = []
    def validate(data, plc_model="FX3U"):
        seen.append(plc_model)
        return validate_st_json(data, plc_model=plc_model)
    monkeypatch.setattr(generation, "validate_st_json", validate)
    result = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", target_mode="st", plc_model=model, model_name="offline"),
        tmp_path, dependencies=GenerationDependencies(
            stream_response=lambda *a, **k: ("", json.dumps({"st_code": "Y0 := X0;"})),
        ),
    ).run()
    assert seen == [model]
    assert result["validation"]["status"] == "candidate_ready"
    assert result["validation"]["static"] == "passed"
    assert result["semantic_validation"]["requirements"] == "unverified"
    assert (tmp_path / "program.st").read_text(encoding="utf-8") == "Y0 := X0;"


def test_generation_runs_with_all_gui_imports_blocked(tmp_path):
    script = '\nimport importlib.abc, json, sys\nclass BlockGUI(importlib.abc.MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname == "main" or fullname == "qt_compat" or fullname.startswith(("PyQt", "PySide")):\n            raise AssertionError("GUI import: " + fullname)\nsys.meta_path.insert(0, BlockGUI())\nfrom application.generation import GenerationWorkflow, GenerationRequest, GenerationDependencies\nfrom application.review import InspectionWorkflow\nfrom application.planning import EvidenceDebugPlanWorkflow, SimulatorTestPlanWorkflow\nresult = GenerationWorkflow(\n    GenerationRequest("X0 controls Y0", target_mode="st", model_name="offline"), sys.argv[1],\n    dependencies=GenerationDependencies(stream_response=lambda *a, **k: ("", json.dumps({"st_code":"Y0 := X0;"}))),\n).run()\nassert result["artifacts"] == {"st": "program.st"}\nassert \'ui.desktop.main_window\' not in sys.modules and \'ui.desktop.qt\' not in sys.modules\n'
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                               env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (tmp_path / "program.st").read_text(encoding="utf-8") == "Y0 := X0;"


@pytest.mark.parametrize('cpu', ['FX1N', 'Q00J', 'Q03UDV', 'L02'])
def test_source_profile_ladder_generation_exports_csv_without_fx3u_only_gate(tmp_path, cpu):
    result = GenerationWorkflow(
        GenerationRequest('X0 controls Y0', plc_model=cpu, model_name='offline'), tmp_path,
        dependencies=GenerationDependencies(stream_response=lambda *a, **k: ('', json.dumps(_ladder()))),
    ).run()
    assert (tmp_path / result['artifacts']['program_csv']).is_file()
    assert (tmp_path / result['artifacts']['comment_csv']).is_file()


def test_generation_trusts_confirmed_spec_without_posthoc_approach_rejection(tmp_path):
    ladder = _ladder()
    spec = {"selected_approach": {"name": "MOV approach", "generation_contract": {
        "required_opcodes": ["MOV"], "enforce": True}}}
    request = GenerationRequest("X0 controls Y0", confirmed_context=spec, model_name="offline")
    workflow = GenerationWorkflow(request, tmp_path, dependencies=GenerationDependencies(
        stream_response=lambda *a, **k: ("", json.dumps(ladder)),
        generate_json=lambda *a, **k: pytest.fail("Generation must not enter a semantic repair loop"),
    ))
    spec.clear()
    request.confirmed_context.clear()
    result = workflow.run()
    assert result["validation"]["status"] == "candidate_ready"
    assert result["validation_profile"] == "generation_structural"
    assert result["repair_attempts"] == 0
    assert result["contract_mismatch"] is None
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == ladder
    assert (tmp_path / result["artifacts"]["program_csv"]).is_file()


def test_edit_generation_sends_current_program_and_prefers_partial_output(tmp_path):
    base = _ladder()
    changed_rung = copy.deepcopy(base["rungs"][0])
    changed_rung["branches"][0]["inputs"][0]["type"] = "NC"
    partial = {
        "mode": "partial",
        "device_comments": {},
        "rungs": [changed_rung],
        "delete_rung_ids": [],
    }
    observed = {}

    def stream(user_input, *args, **kwargs):
        observed["user_input"] = user_input
        observed["current_version_json"] = copy.deepcopy(kwargs.get("current_version_json"))
        observed["is_edit_mode"] = kwargs.get("is_edit_mode")
        return "", json.dumps(partial, ensure_ascii=False)

    result = GenerationWorkflow(
        GenerationRequest("把 X0 改成常闭", previous_json=base, model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(stream_response=stream),
    ).run()

    assert observed["is_edit_mode"] is True
    assert observed["current_version_json"] == base
    assert '优先返回 mode="partial"' in observed["user_input"]
    assert "不要重复输出未修改梯级" in observed["user_input"]
    persisted = json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8"))
    assert persisted["rungs"][0]["branches"][0]["inputs"][0]["type"] == "NC"
    assert result["repair_attempts"] == 0


def test_edit_generation_full_json_remains_accepted_without_retry(tmp_path):
    base = _ladder()
    full = copy.deepcopy(base)
    full["rungs"][0]["branches"][0]["inputs"][0]["type"] = "NC"
    calls = []

    def stream(*args, **kwargs):
        calls.append((args, kwargs))
        return "", json.dumps(full, ensure_ascii=False)

    result = GenerationWorkflow(
        GenerationRequest("把 X0 改成常闭", previous_json=base, model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *a, **k: pytest.fail("Full edit response must not trigger retry"),
        ),
    ).run()

    assert result["validation"]["status"] == "candidate_ready"
    assert result["repair_attempts"] == 0
    assert len(calls) == 1
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == full


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('edit_kind', ['logic', 'comments', 'delete'])
def test_compact_edit_one_request_preserves_other_rungs_and_exports_csv(monkeypatch, tmp_path, strict, edit_kind):
    from model_profile_fixtures import offline_runtime_profile
    from model_runtime.contract import contract_scope
    from application.generation_wire import expand_context_document
    from jsonschema import Draft202012Validator
    from plc.ir import lower_rung_instructions

    base = _ladder()
    first = base['rungs'][0]
    first['rung_id'] = 7
    other = copy.deepcopy(first)
    other['rung_id'] = 21
    other['branches'][0]['inputs'][0]['address'] = 'X2'
    other['branches'][0]['outputs'][0]['address'] = 'Y2'
    base['rungs'].append(other)
    patch = {'mode': 'partial', 'r': [], 'device_comments': [], 'delete_rung_ids': []}
    if edit_kind == 'logic':
        patch['r'] = [{'rung_id': 7, 'c': [], 'b': [{'i': ['NC X0'], 'o': ['COIL Y0']}]}]
    elif edit_kind == 'comments':
        patch['device_comments'] = [{'address': 'Y0', 'comment': '电机'}]
    else:
        patch['delete_rung_ids'] = [7]
    class Provider:
        def __init__(self):
            self.profile = offline_runtime_profile()
            if strict:
                self.profile['capabilityContract'] = {'schema_version': 3,
                    'scope': contract_scope(self.profile, {}, model='offline', api_key=None),
                    'parameters': {}, 'capabilities': {'structured_output': {
                        'status': 'supported', 'source': 'manual', 'modes': ['json_schema']}}}
            self.requests = []
        def stream(self, request):
            self.requests.append(request)
            context = expand_context_document(json.loads(request.messages[1].content))
            assert context['current_program']['rungs'][0]['rung_id'] == 7
            if strict:
                assert 'output_schema' not in context
                schema = request.options['response_format']['json_schema']['schema']
            else:
                schema = context['output_schema']
            assert 'r' in schema['properties'] and 'rungs' not in schema['properties']
            Draft202012Validator(schema).validate(patch)
            yield TextDelta(json.dumps(patch, ensure_ascii=False))
    provider = Provider()
    monkeypatch.setattr(api, '_build_knowledge_context', lambda *a, **k: '')
    before = copy.deepcopy(base)
    result = GenerationWorkflow(GenerationRequest('修改梯级7', previous_json=base, model_name='offline'),
        tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1 and provider.requests[0].max_retries == 0
    assert result['repair_attempts'] == 0 and base == before
    saved = json.loads((tmp_path / 'ladder.json').read_text(encoding='utf-8'))
    assert saved['rungs'][-1] == other
    if edit_kind == 'logic':
        assert [(row['op'], row['args']) for row in lower_rung_instructions(saved['rungs'][0])] == [
            ('LDI', ['X0']), ('OUT', ['Y0'])]
    elif edit_kind == 'comments':
        assert saved['rungs'] == base['rungs'] and saved['device_comments']['Y0'] == '电机'
    else:
        assert [row['rung_id'] for row in saved['rungs']] == [21]
    csv = (tmp_path / result['artifacts']['program_csv']).read_text(encoding='utf-16')
    assert 'Y002' in csv and ('LDI' in csv) == (edit_kind == 'logic')


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('mode', ['direct', 'edit'])
def test_compact_complex_request_reaches_csv_once(monkeypatch, tmp_path, strict, mode):
    from gxworks2.csv_importer import parse_gxworks2_csv
    import application.generation_agent as agent
    from model_profile_fixtures import offline_runtime_profile
    from model_runtime.contract import contract_scope
    from jsonschema import Draft202012Validator

    rows = [
        {'c': ['NO M0'], 'b': [
            {'i': [{'or': [['NO M1', 'NC M2'], ['>= D0 K5']]}],
             'o': ['MOV K-7 D10', 'BMOV D20 D100 K8']},
            {'i': ['NC M3'], 'o': ['SFTL M100 M1000 K128 K1']},
            {'i': [], 'o': ['COIL M200']}]},
        {'c': [], 'b': [{'i': ['P M4'], 'o': ['DMOV D0Z0 D200', 'COIL M201']}]},
    ]
    program = {'r': rows, 'device_comments': []}
    base = _ladder()
    base['rungs'][0]['rung_id'] = 21
    before = copy.deepcopy(base)
    if mode == 'edit':
        program.update(mode='partial', delete_rung_ids=[])
        for row, identity in zip(rows, [7, 31]):
            row['rung_id'] = identity
        answer = program
    else:
        answer = {'result': {'program': program, 'decisions': [], 'open_items': []}}

    class Provider:
        def __init__(self):
            self.profile = offline_runtime_profile()
            if strict:
                self.profile['capabilityContract'] = {'schema_version': 3,
                    'scope': contract_scope(self.profile, {}, model='offline', api_key=None),
                    'parameters': {}, 'capabilities': {'structured_output': {
                        'status': 'supported', 'source': 'manual', 'modes': ['json_schema']}}}
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if strict:
                Draft202012Validator(request.options['response_format']['json_schema']['schema']).validate(answer)
            yield TextDelta(json.dumps(answer, ensure_ascii=False))

    monkeypatch.setattr(api, '_build_knowledge_context', lambda *a, **k: '')
    monkeypatch.setattr(agent, '_build_knowledge_context', lambda *a, **k: '')
    provider = Provider()
    result = GenerationWorkflow(GenerationRequest('生成串并联多输出程序', model_name='offline',
        previous_json=base if mode == 'edit' else None, direct_generation=mode == 'direct'),
        tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1 and provider.requests[0].max_retries == 0
    assert result['repair_attempts'] == 0
    assert result['validation']['status'] == 'candidate_ready'
    saved = json.loads((tmp_path / 'ladder.json').read_text(encoding='utf-8'))
    expected = ['LD M0', 'MPS', 'LD M1', 'ANI M2', 'OR>= D0 K5', 'ANB',
        'MOV K-7 D10', 'BMOV D20 D100 K8', 'MRD', 'ANI M3',
        'SFTL M100 M1000 K128 K1', 'MPP', 'OUT M200']
    if mode == 'edit':
        assert base == before
        assert [rung['rung_id'] for rung in saved['rungs']] == [7, 21, 31]
        assert saved['rungs'][1] == base['rungs'][0]
        expected.extend(['LD X000', 'OUT Y000'])
    expected.extend(['LDP M4', 'DMOV D0Z0 D200', 'OUT M201'])
    imported = parse_gxworks2_csv(tmp_path / result['artifacts']['program_csv'],
                                 tmp_path / result['artifacts']['comment_csv'])
    assert [' '.join([row['op'], *row['args']])
            for network in imported.network_instructions for row in network] == expected


@pytest.mark.parametrize('row', [
    pytest.param({'c': ['NO M0'], 'b': [{'i': [], 'o': ['ZRST M100']}]}, id='missing-operand'),
    pytest.param({'c': ['NO M0'], 'b': [{'i': [], 'o': ['ZRST M100 M115 M120']}]}, id='extra-operand'),
    pytest.param({'c': ['NO M0'], 'b': [{'i': [], 'o': ['ADD D0 K1 K7']}]}, id='literal-destination'),
    pytest.param({'c': [{'or': [['NO M0'], ['NO M1']]}], 'b': [
        {'i': [], 'o': ['COIL M100']}, {'i': [], 'o': ['COIL M101']}]}, id='parallel-common-to-multiple-outputs'),
    pytest.param({'c': [], 'b': [{'i': [{'or': [[{'or': [['NO M0'], ['NO M1']]}], ['NO M2']]}],
                                 'o': ['COIL M100']}]}, id='nested-parallel'),
])
def test_compact_invalid_operands_and_parallel_shapes_stop_before_export(tmp_path, row):
    from application.compact_protocol import CompactProtocolError
    patch = {'mode': 'partial', 'r': [{'rung_id': 1, **row}], 'delete_rung_ids': [], 'device_comments': []}
    calls = []
    def stream(*args, **kwargs):
        calls.append(kwargs)
        return '', json.dumps(patch)
    workflow = GenerationWorkflow(GenerationRequest('修改梯级', previous_json=_ladder(), model_name='offline'),
        tmp_path, dependencies=GenerationDependencies(stream_response=stream,
            generate_json=lambda *a, **k: pytest.fail('Invalid edit must not silently retry')))
    with pytest.raises((GenerationValidationError, CompactProtocolError)):
        workflow.run()
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["delete", "rung", "device", "comment", "full"])
def test_contract_repair_rejects_scope_escape_without_hidden_retry(tmp_path, mutation):
    ladder = _ladder()
    partial = {"mode": "partial", "rungs": [], "delete_rung_ids": [], "device_comments": {}}
    if mutation == "delete":
        partial["delete_rung_ids"] = [1]
    elif mutation == "rung":
        partial["rungs"] = [{"rung_id": 2}]
    elif mutation == "device":
        partial["rungs"] = ladder["rungs"]
        partial["rungs"][0]["branches"][0]["inputs"][0]["address"] = "X1"
    elif mutation == "comment":
        partial["device_comments"] = {"D100": "Unrelated device"}
    else:
        partial = ladder
    workflow = GenerationWorkflow(
        GenerationRequest("Repair current approach", previous_json=_ladder(),
                          task_type="contract_repair", repair_mode=True,
                          allowed_rung_ids=[1], allowed_addresses=["X0", "Y0"], model_name="offline"),
        tmp_path, dependencies=GenerationDependencies(
            stream_response=lambda *a, **k: ("", json.dumps(partial)),
            generate_json=lambda *a, **k: pytest.fail("Contract repair secretly retried"),
        ),
    )
    with pytest.raises(GenerationValidationError):
        workflow.run()
    # Explicit repair scope is still enforced, but failure does not trigger
    # another hidden model call.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("target_mode", ["ladder", "st"])
def test_rejected_response_cannot_fallback_or_create_artifacts(monkeypatch, tmp_path, target_mode):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    events = []
    calls = []
    candidate = _ladder() if target_mode == "ladder" else {"st_code": "(* 正在检查输入并准备输出。 *)\nY0 := X0;"}
    if target_mode == "ladder":
        candidate["device_comments"]["Y0"] = "正在检查输入并准备输出。"
    class Provider:
        def stream(self, request):
            calls.append(request)
            yield TextDelta(json.dumps(candidate, ensure_ascii=False))
    workflow = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", target_mode=target_mode, model_name="offline", response_language="en"),
        tmp_path, lambda kind, payload: events.append((kind, payload)),
        GenerationDependencies(provider=Provider(),
                               generate_json=lambda *a, **k: pytest.fail("Rejected response retried")),
    )
    with pytest.raises(GenerationError):
        workflow.run()
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []
    assert {kind for kind, payload in events} == {"progress"}


def test_provider_and_language_remain_bound_across_transport_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    calls = []
    accepted = json.dumps({"st_code": "(* The input controls the output. *)\nY0 := X0;"})
    class Provider:
        def stream(self, request):
            calls.append(request)
            if request.stream:
                set_language("ja")
                monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("Provider drift"))
                raise ModelProviderError("Offline stream rejection", code="stream_not_supported")
            yield TextDelta(accepted)
    provider = Provider()
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    set_language("en")
    request = GenerationRequest("X0 controls Y0", target_mode="st", model_name="offline")
    set_language("zh-CN")
    events = []
    result = GenerationWorkflow(request, tmp_path, lambda *event: events.append(event)).run()
    assert result["validation"]["status"] == "candidate_ready"
    assert [(call.stream, call.response_language) for call in calls] == [(True, "en"), (False, "en")]
    assert not any("Unaccepted partial" in str(payload) for _, payload in events)


def test_provider_scope_is_nested_and_restored(monkeypatch):
    first, second, default = object(), object(), object()
    monkeypatch.setattr(api, "get_active_provider", lambda: default)
    with api.provider_scope(first):
        assert api._workflow_provider() is first
        with api.provider_scope():
            assert api._workflow_provider() is first
        with api.provider_scope(second):
            assert api._workflow_provider() is second
        assert api._workflow_provider() is first
    assert api._workflow_provider() is default

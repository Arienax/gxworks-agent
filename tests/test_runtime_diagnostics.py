"""Diagnostic observation must not alter acceptance, messages or retries."""
import io
import json
from pathlib import Path
from types import SimpleNamespace as S
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
import zipfile
import pytest
import shared.diagnostics as d
from application.jobs import JobContext
from plc.candidate_repair import GenerationValidationError
from plc.validation import PLCJsonValidationError, validate_ladder_candidate_structure
from model_runtime.provider import (OpenAICompatibleProvider, ModelRequest, SystemMessage, UserMessage,
    ResponseRejectedError, ResponseContract, collect_response, ModelProviderError)


def rows(root, job='job_test'):
    return [json.loads(x) for x in (root/'diagnostics'/f'{job}.jsonl').read_text().splitlines()]


def provider(reply):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return reply
    client = S(chat=S(completions=S(create=create)))
    return OpenAICompatibleProvider({'adapter':'openai_compatible','model':'fixture-model',
        'baseUrl':'https://private.invalid/PRIVATE_ENDPOINT/v1',
        'generationDefaults':{'response_format':{'type':'json_object'},'max_tokens':999}},
        'sk-PRIVATE_CREDENTIAL', client=client), calls


def request(stream=False):
    return ModelRequest((UserMessage('PRIVATE_PROMPT'),), model='fixture-model', stream=stream,
                        response_contract=ResponseContract('ladder','json'))


@pytest.mark.parametrize('text,status,envelope', [
    ('', 'empty','empty'), ('   ', 'empty','empty'),
    ('{"x":', 'syntax_error','object'), ('{"x":1,}', 'syntax_error','object'),
    ('{"x":1}{"x":2}', 'syntax_error','object'), ('[]','non_object','array'),
    ('"example"','non_object','other_text'), ('<think>secret</think>{}', 'syntax_error','markup'),
    ('\ufeff{}', 'syntax_error','bom'), ('```json\n{}\n```','valid_object','fenced'),
    ('{"x":"PRIVATE_RESPONSE"}', 'valid_object','object'),
])
def test_json_shape_without_content(text,status,envelope):
    value = d.json_diagnostic(text)
    assert value['json_status'] == status and value['envelope'] == envelope
    assert 'PRIVATE' not in json.dumps(value) and 'secret' not in json.dumps(value)


def test_json_locations_include_original_fence_and_leading_whitespace():
    value = d.json_diagnostic(' \n```json\n{\n"a": 1,\n}\n```')
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads('{\n"a": 1,\n}')
    assert value['line'] == caught.value.lineno and value['column'] == caught.value.colno
    assert value['original_line'] == caught.value.lineno + 2
    assert value['original_column'] == caught.value.colno
    assert value['fenced'] is True


def test_extra_data_reports_complete_object_shape_and_tail_class_without_content():
    value = d.json_diagnostic('{"device_comments":{},"rungs":[{"rung_id":1}]};')
    assert value['json_status'] == 'syntax_error'
    assert value['json_error'] == 'Extra data'
    assert value['prefix_complete_object'] is True
    assert value['punctuation_only_tail'] is True
    assert value['tail_class'] == 'punctuation'
    assert value['suffix_chars'] == 1
    assert value['rung_count'] == 1
    assert value['device_comment_count'] == 0
    assert value['distance_from_end'] == 1
    semantic = d.json_diagnostic('{"rungs":[]}x')
    assert semantic['prefix_complete_object'] is True
    assert semantic['tail_class'] == 'semantic'
    second = d.json_diagnostic('{"rungs":[]}{"rungs":[]}')
    assert second['prefix_complete_object'] is True
    assert second['tail_class'] == 'second_json'
    raw = json.dumps([value, semantic, second])
    assert 'rung_id' not in raw and 'device_comments' not in raw


def test_model_request_records_role_sizes_without_message_text(tmp_path):
    req = ModelRequest((SystemMessage('SYSTEM_PRIVATE'), UserMessage('USER_PRIVATE')),
                       model='fixture-model', response_contract=ResponseContract('ladder','json'))
    with d.diagnostic_scope(tmp_path,'job_test'):
        d.begin_request(req, object())
    event = next(x for x in rows(tmp_path) if x['event']=='model_request')
    assert event['system_messages'] == 1 and event['user_messages'] == 1
    assert event['system_chars'] == len('SYSTEM_PRIVATE')
    assert event['user_chars'] == len('USER_PRIVATE')
    assert event['message_chars'] == len('SYSTEM_PRIVATE') + len('USER_PRIVATE')
    assert 'SYSTEM_PRIVATE' not in json.dumps(event) and 'USER_PRIVATE' not in json.dumps(event)



def test_invalid_app_instr_opcode_is_exported_as_bounded_observed_value(tmp_path):
    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR",
                    "opcode": "NOT_A_REAL_OPCODE",
                    "operands": ["PRIVATE_OPERAND"],
                    "label": None,
                }],
            }],
        }],
    }
    with pytest.raises(PLCJsonValidationError) as caught:
        validate_ladder_candidate_structure(
            ladder, plc_model="FX3U", require_catalogued_instructions=True,
        )
    assert caught.value.observed_opcode == "NOT_A_REAL_OPCODE"
    failure = GenerationValidationError(
        [caught.value], attempts=0, max_attempts=0,
        language="zh-CN", stop_reason="final_validation",
    )
    with d.diagnostic_scope(tmp_path, "job_test"):
        d.exception_record(failure)

    baseline = json.loads(json.dumps(ladder))
    outputs = baseline["rungs"][0]["branches"][0]["outputs"]
    outputs[0]["opcode"] = "DADD"
    outputs.append({
        "type": "APP_INSTR", "opcode": "MOV",
        "operands": ["PRIVATE_SECOND_OPERAND"], "label": None,
    })
    private_record = {
        "id": "job_test",
        "snapshot": {
            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],
            "project": {"plc_model": "FX3U"},
        },
    }
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "job_test.json").write_text(json.dumps(private_record), encoding="utf-8")
    # Match the real HTTP path: the exporter receives a public job without snapshot.
    job = {"id": "job_test", "status": "failed", "kind": "generation"}
    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:
        summary = json.loads(archive.read("summary.json"))
        log = archive.read("diagnostics.jsonl").decode()
        payload = archive.read("summary.json").decode() + log + archive.read("README.txt").decode()
    assert summary["validation_values_included"] is True
    workflow = summary["failure_analysis"]["workflow_exception"]
    violation = workflow["exceptions"][0]["violations"][0]
    assert violation["baseline_app_instrs"] == [
        {"output_index": 0, "opcode": "DADD", "rung_id": 1, "branch_id": 1},
        {"output_index": 1, "opcode": "MOV", "rung_id": 1, "branch_id": 1},
    ]
    assert "baseline_opcode" not in violation
    assert violation["observed_opcode"] == "NOT_A_REAL_OPCODE"
    assert violation["allowed_by_registry"] is False
    assert '"baseline_app_instrs"' in log
    assert '"opcode": "DADD"' in log
    assert '"opcode": "MOV"' in log
    assert '"observed_opcode": "NOT_A_REAL_OPCODE"' in log
    assert '"allowed_by_registry": false' in log
    assert "PRIVATE_OPERAND" not in payload
    assert "PRIVATE_SECOND_OPERAND" not in payload


def test_observed_opcode_diagnostic_redacts_secret_like_tokens(tmp_path):
    error = PLCJsonValidationError("$.rungs[0].branches[0].outputs[0].opcode: invalid")
    error.observed_opcode = "SK-PRIVATE_TOKEN"
    with d.diagnostic_scope(tmp_path, "job_test"):
        d.exception_record(error)
    item = next(x for x in rows(tmp_path) if x["event"] == "workflow_exception")["exceptions"][0]
    assert item["observed_opcode"] == "redacted"
    assert "PRIVATE_TOKEN" not in json.dumps(rows(tmp_path))


def test_generation_validation_exception_records_attempts_stop_and_paths(tmp_path):
    cause = json.JSONDecodeError('Extra data', '{};', 2)
    failure = GenerationValidationError([cause], attempts=0, max_attempts=0,
                                        language='zh-CN', stop_reason='final_validation')
    with d.diagnostic_scope(tmp_path,'job_test'):
        d.exception_record(failure)
    item = next(x for x in rows(tmp_path) if x['event']=='workflow_exception')['exceptions'][0]
    assert item['attempt_count'] == 0 and item['max_attempts'] == 0
    assert item['stop_reason'] == 'final_validation'
    assert item['violation_count'] == 1
    assert item['violations'][0]['reason'] == 'invalid_json_object'
    assert item['violations'][0]['path'].startswith('content$')


@pytest.mark.parametrize('stream', [False,True])
@pytest.mark.parametrize('finish', ['length','stop','content_filter','insufficient_system_resource',None])
def test_raw_adapter_finish_and_rejection_are_observed_not_repaired(tmp_path,stream,finish):
    body='{"rungs": [PRIVATE_RESPONSE'
    msg={'content':body,'reasoning_content':'PRIVATE_REASONING'}
    reply=({'model':'fixture-model','choices':[{'message':msg,'finish_reason':finish}]}
           if not stream else iter([
             {'model':'fixture-model','choices':[{'delta':{'reasoning_content':'PRIVATE_REASONING'},'finish_reason':None}]},
             {'choices':[{'delta':{'content':body},'finish_reason':None}]},
             {'choices':[{'delta':None,'finish_reason':finish}], 'usage':{'prompt_tokens':8,'completion_tokens':12,'total_tokens':20}},
           ]))
    p,calls=provider(reply); emitted=[]
    with d.diagnostic_scope(tmp_path,'job_test'):
        with pytest.raises(ResponseRejectedError) as failure:
            collect_response(p,request(stream),on_content_chunk=emitted.append,
                             fallback_to_non_stream=True)
        d.exception_record(failure.value)
    log=rows(tmp_path)
    result=next(x for x in log if x['event']=='provider_result')
    assert result['finish_reason'] == (finish or 'unknown')
    assert result['finish_seen'] == (finish is not None)
    assert next(x for x in log if x['event']=='model_response')['json']['json_status']=='syntax_error'
    assert any(x['event']=='response_rejected' for x in log)
    exception = next(x for x in log if x['event']=='workflow_exception')['exceptions'][0]
    assert exception['violation_count'] >= 1
    assert exception['violations'][0]['path'] == 'content'
    assert exception['violations'][0]['reason'] == 'invalid_json_object'
    assert len(calls)==1 and emitted==[]
    raw=json.dumps(log)
    assert all(secret not in raw for secret in ['PRIVATE_RESPONSE','PRIVATE_REASONING','PRIVATE_PROMPT','PRIVATE_CREDENTIAL','PRIVATE_ENDPOINT'])
    assert 'model_runtime/provider.py' in raw and 'response_acceptance' in raw
    assert calls[0]['messages'][-1]['content']=='PRIVATE_PROMPT'


def test_empty_content_with_reasoning_is_diagnosed(tmp_path):
    p,_=provider({'choices':[{'message':{'content':None,'reasoning_content':'reasoning'},'finish_reason':'length'}]})
    with d.diagnostic_scope(tmp_path,'job_test'):
        with pytest.raises(ResponseRejectedError): collect_response(p,request())
    response=next(x for x in rows(tmp_path) if x['event']=='model_response')
    assert response['content_chars']==0 and response['reasoning_chars']==9
    assert response['json']['json_status']=='empty'


def test_valid_response_bytes_and_event_callbacks_unchanged(tmp_path):
    body='```json\n{"result":"PRIVATE_RESULT"}\n```'
    p,_=provider({'choices':[{'message':{'content':body},'finish_reason':'stop'}]})
    emitted=[]
    with d.diagnostic_scope(tmp_path,'job_test'):
        result=collect_response(p,request(),on_content_chunk=emitted.append)
    assert result.message.content==body and emitted==[body]
    assert any(x['event']=='model_accepted' for x in rows(tmp_path))
    assert 'PRIVATE_RESULT' not in json.dumps(rows(tmp_path))


def test_stream_failure_records_metadata_and_original_cause(tmp_path):
    def broken():
        yield {'choices':[{'delta':{'content':'{"x":'}}]}
        raise TimeoutError('PRIVATE_CREDENTIAL in SDK request')
    p,calls=provider(broken())
    with d.diagnostic_scope(tmp_path,'job_test'):
        with pytest.raises(ModelProviderError): collect_response(p,request(True))
    records=rows(tmp_path)
    assert any(x['event']=='provider_exception' for x in records)
    assert any(x['event']=='provider_result' and not x['finish_seen'] for x in records)
    assert len(calls)==1
    assert 'PRIVATE_CREDENTIAL' not in json.dumps(records)


def test_export_includes_operator_details_but_redacts_sensitive_fields(tmp_path):
    with d.diagnostic_scope(tmp_path, 'job_test'):
        d.emit('model_request', model='fixture-model')
    job = {'id':'job_test','status':'failed','kind':'generation',
           'snapshot':{'password':'do-not-export','text':'operator requirement'}}
    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as z:
        assert set(z.namelist()) == {
            'summary.json','diagnostics.jsonl','job.json','transcript.jsonl',
            'operator_actions.jsonl','README.txt'
        }
        exported_job = json.loads(z.read('job.json'))
        assert exported_job['snapshot']['text'] == 'operator requirement'
        assert exported_job['snapshot']['password'] == '<redacted>'
        summary = json.loads(z.read('summary.json'))
        assert summary['capture_status'] == 'captured'
        assert summary['content_included'] is True
        assert 'model_request' in summary['failure_analysis']

def test_old_job_export_does_not_fabricate_evidence(tmp_path):
    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path,{'id':'job_old'}))) as z:
        assert json.loads(z.read('summary.json'))['capture_status']=='not_captured'
        assert z.read('diagnostics.jsonl')==b''


@pytest.mark.parametrize('name',['../private','job_../bad','job_x/y','job_x:y','job_\\secret',''])
def test_path_traversal_rejected(tmp_path,name):
    with pytest.raises(ValueError): d.export_diagnostics(tmp_path,{'id':name})


def test_file_limit_and_io_failure_do_not_replace_workflow(tmp_path,monkeypatch):
    with d.diagnostic_scope(tmp_path,'job_test'):
        monkeypatch.setattr(d.os,'open',lambda *a,**k: (_ for _ in ()).throw(PermissionError('PRIVATE_KEY')))
        p,_=provider({'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
        assert collect_response(p,request()).message.content=='{}'


def test_concurrent_scopes_do_not_cross_jobs(tmp_path):
    def work(i):
        with d.diagnostic_scope(tmp_path,f'job_{i}'):
            p,_=provider({'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
            collect_response(p,request())
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(work,range(8)))
    for i in range(8):
        assert all(x['job_id']==f'job_{i}' for x in rows(tmp_path,f'job_{i}'))
        assert [x['request_index'] for x in rows(tmp_path,f'job_{i}') if x['event']=='model_request']==[1]


# Context-audit projection is part of runtime diagnostic observation, not a
# separate context-compilation contract.
class _Manager:
    def __init__(self):
        self._record_lock = RLock()

    def _load(self, job_id):
        return {"id": job_id, "cancel_requested": False, "snapshot": {}}

    def emit(self, job_id, event_type, data):
        return {"job_id": job_id, "type": event_type, "data": data}


def test_context_audit_is_mirrored_as_bounded_diagnostic_metadata(tmp_path):
    report = {
        "request_index": 1,
        "message_text_chars": 66000,
        "messages": [
            {"role": "system", "text_chars": 50000, "images": 0},
            {"role": "user", "text_chars": 16000, "images": 0},
        ],
        "sections": [
            {"section": "manual_chunk:1688", "status": "included", "chars": 4200},
            {"section": "manual_chunk:703", "status": "excluded", "chars": 3000},
            {"section": "base_prompt", "status": "included", "chars": 18000},
        ],
        "dropped_sections": 0,
    }
    with d.diagnostic_scope(tmp_path, "job_test", kind="generation"):
        JobContext(_Manager(), "job_test").emit("context_audit", report)

    rows = [
        json.loads(line)
        for line in (tmp_path / "diagnostics" / "job_test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event = next(row for row in rows if row["event"] == "context_audit")
    assert "policy" not in event
    assert event["message_chars"] == 66000
    assert event["context_section_count"] == 3
    assert event["included_section_count"] == 2
    assert event["excluded_section_count"] == 1
    assert event["retrieval_section_count"] == 2
    assert event["retrieval_context_chars"] == 4200
    assert event["included_section_chars"] == 22200
    assert event["excluded_section_chars"] == 3000

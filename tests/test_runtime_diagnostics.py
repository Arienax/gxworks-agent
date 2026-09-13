"""Diagnostic observation must not alter acceptance, messages or retries."""
import io
import json
from pathlib import Path
from types import SimpleNamespace as S
from concurrent.futures import ThreadPoolExecutor
import zipfile
import pytest
import runtime_diagnostics as d
from application.generation_repair import GenerationValidationError
from model_provider import (OpenAICompatibleProvider, ModelRequest, SystemMessage, UserMessage,
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
        'baseUrl':'https://private.example/v1?token=PRIVATE_ENDPOINT',
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
    assert 'model_provider.py' in raw and 'response_acceptance' in raw
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


def test_export_reprojects_log_and_never_reads_snapshot_config_or_body(tmp_path):
    with d.diagnostic_scope(tmp_path,'job_test'):
        d.emit('model_request', model='fixture-model', prompt='PRIVATE_PROMPT', api_key='PRIVATE_KEY')
    path=tmp_path/'diagnostics/job_test.jsonl'
    with path.open('a') as f:
        f.write(json.dumps({'schema_version':1,'event':'model_request','job_id':'job_test',
                'model':'sk-PRIVATE_CREDENTIAL','api_key':'PRIVATE_KEY','prompt':'PRIVATE_PROMPT'})+'\n')
    job={'id':'job_test','status':'failed','kind':'generation','snapshot':{'password':'PRIVATE_PASSWORD'}}
    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path,job))) as z:
        assert set(z.namelist())=={'summary.json','diagnostics.jsonl','README.txt'}
        payload=''.join(z.read(n).decode() for n in z.namelist())
        assert 'PRIVATE_' not in payload
        summary = json.loads(z.read('summary.json'))
        assert summary['capture_status']=='captured'
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
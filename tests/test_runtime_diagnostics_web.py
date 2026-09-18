"""Actual Windows application/job/HTTP integration; provider output is fixed."""
import io
import json
import zipfile
import pytest
import application.model_api as api
from fastapi.testclient import TestClient
from application.workbench import WorkbenchService
from model_runtime.provider import TextDelta
from test_web_api import ORIGIN, AGENT, _app, _login


class InvalidProvider:
    def __init__(self): self.calls=0
    def stream(self,request):
        self.calls+=1
        yield TextDelta('{"rungs": [PRIVATE_INCOMPLETE')


@pytest.mark.parametrize('policy',['legacy','adaptive'])
def test_failed_job_export_contains_user_and_model_timeline(tmp_path, monkeypatch, policy):
    monkeypatch.setenv('GXWORKS_CONTEXT_POLICY', policy)
    monkeypatch.setattr(api, 'load_full_config', lambda: {})
    monkeypatch.setattr(api, 'get_active_provider', lambda: pytest.fail('Live provider is forbidden'))
    p = InvalidProvider()
    service = WorkbenchService(tmp_path/'workspace', tmp_path/'state',
        model_factory=lambda:(p,{'model':'offline-diagnostics'}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as c:
        headers = _login(c)
        pid = c.post('/api/projects', json={'name':'Diagnostics'}, headers=headers).json()['id']
        spec = {'summary':'X0 controls Y0','io_table':[],'parameters':[]}
        saved = c.put(f'/api/projects/{pid}/spec', headers=headers,
                      json={'spec':spec,'expected_hash':None})
        assert saved.status_code == 200 and saved.json()['valid'] is True
        result = c.post('/api/jobs', headers=headers, json={
            'project_id':pid,'kind':'generation','request_id':'diagnostic-export-'+policy,
            'text':'operator requirement text','response_language':'en'
        })
        assert result.status_code == 202
        jid = result.json()['id']
        service.jobs._futures[jid].result(timeout=30)
        response = c.get(f'/api/jobs/{jid}/diagnostics')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            meta = json.loads(z.read('summary.json'))
            log = [json.loads(x) for x in z.read('diagnostics.jsonl').decode().splitlines()]
            transcript = [json.loads(x) for x in z.read('transcript.jsonl').decode().splitlines()]
            actions = [json.loads(x) for x in z.read('operator_actions.jsonl').decode().splitlines()]
            job_record = json.loads(z.read('job.json'))
            assert meta['content_included'] is True and meta['transcript_count'] >= 2
            assert any(x['event']=='response_rejected' for x in log)
            assert 'operator requirement text' in json.dumps(job_record, ensure_ascii=False)
            assert any('rungs' in str(x.get('content','')) for x in transcript
                       if x.get('event') == 'model_response')
            assert any(x.get('action') == 'spec_update' for x in actions)
            assert any(x.get('action') == 'job_submit' and x.get('job_id') == jid for x in actions)
        assert p.calls == 1

def test_history_without_sidecar_export_is_explicit(tmp_path):
    service=WorkbenchService(tmp_path/'workspace',tmp_path/'state')
    with TestClient(_app(service.store.base_dir,service.state_dir,service=service),base_url=ORIGIN) as c:
        _login(c)
        def broken(ctx): raise ValueError('PRIVATE_WORKFLOW_MESSAGE')
        job=service.jobs.submit('generation',{},broken)
        service.jobs._futures[job['id']].result(timeout=10)
        f=service.state_dir/'diagnostics'/(job['id']+'.jsonl')
        assert f.is_file()
        f.unlink() # Simulate a saved task from the preceding release.
        response=c.get('/api/jobs/'+job['id']+'/diagnostics')
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert json.loads(z.read('summary.json'))['capture_status']=='not_captured'

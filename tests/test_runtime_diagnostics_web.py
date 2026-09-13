"""Actual Windows application/job/HTTP integration; provider output is fixed."""
import io
import json
import zipfile
import pytest
import api
from fastapi.testclient import TestClient
from application.workbench import WorkbenchService
from model_provider import TextDelta
from test_web_api import ORIGIN, AGENT, _app, _login


class InvalidProvider:
    def __init__(self): self.calls=0
    def stream(self,request):
        self.calls+=1
        yield TextDelta('{"rungs": [PRIVATE_INCOMPLETE')


@pytest.mark.parametrize('policy',['legacy','adaptive'])
def test_failed_job_has_downloadable_log_without_private_content(tmp_path,monkeypatch,policy):
    monkeypatch.setenv('GXWORKS_CONTEXT_POLICY',policy)
    monkeypatch.setattr(api,'load_full_config',lambda:{})
    monkeypatch.setattr(api,'get_active_provider',lambda:pytest.fail('Live provider is forbidden'))
    p=InvalidProvider()
    service=WorkbenchService(tmp_path/'workspace',tmp_path/'state',
        model_factory=lambda:(p,{'model':'offline-diagnostics'}))
    with TestClient(_app(service.store.base_dir, service.state_dir,service=service),base_url=ORIGIN) as c:
        assert c.get('/api/jobs/job_unknown/diagnostics').status_code==401
        h=_login(c)
        pid=c.post('/api/projects',json={'name':'Diagnostics'},headers=h).json()['id']
        service.store.set_confirmed_spec(pid,{'summary':'X0 controls Y0','io_table':[],'parameters':[]})
        result=c.post('/api/jobs',headers=h,json={'project_id':pid,'kind':'generation',
            'request_id':'diagnostic_'+policy,'text':'PRIVATE_USER_TEXT','response_language':'en'})
        assert result.status_code==202,result.text
        jid=result.json()['id'];service.jobs._futures[jid].result(timeout=30)
        job=c.get('/api/jobs/'+jid).json()
        # Completed ladder replies rejected only for JSON syntax are retained as
        # private repair candidates, so the generation UI can offer explicit
        # repair instead of throwing away the entire model result.
        assert job['status']=='failed' and job['error_code']=='generation_validation_failed'
        assert job['error_details']['violations'][0]['reason']=='invalid_json_object'
        candidate=service.state_dir/'staging'/jid/'repair_candidate.json'
        assert candidate.is_file()
        assert candidate.read_text(encoding='utf-8')=='{"rungs": [PRIVATE_INCOMPLETE'
        assert service.projects.project(pid)['version_count']==0 and p.calls==1
        response=c.get(f'/api/jobs/{jid}/diagnostics')
        assert response.status_code==200 and response.headers['content-type']=='application/zip'
        assert response.headers['cache-control']=='no-store'
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            meta=json.loads(z.read('summary.json'))
            log=[json.loads(x) for x in z.read('diagnostics.jsonl').decode().splitlines()]
            assert meta['job_id']==jid and meta['capture_status']=='captured'
            assert any(x['event']=='response_rejected' for x in log)
            assert any(x['event']=='workflow_exception' for x in log)
            assert any(x['event']=='model_response' and x['json']['json_status']=='syntax_error' for x in log)
            assert any(x['event']=='job_started' and x['policy']==policy for x in log)
            payload=''.join(z.read(n).decode() for n in z.namelist())
            assert 'PRIVATE_' not in payload
        # Downloading cannot run generation, repair or create a version.
        assert p.calls==1 and service.projects.project(pid)['version_count']==0
        assert c.get('/api/jobs/job_unknown/diagnostics').status_code==404
        c.cookies.clear()
        assert c.get(f'/api/jobs/{jid}/diagnostics',headers={'Authorization':'Bearer '+AGENT}).status_code in (401,403)


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

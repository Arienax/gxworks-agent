"""Actual Windows application/job/HTTP integration; provider output is fixed."""
import io
import json
import zipfile
import pytest
import application.model_api as api
from fastapi.testclient import TestClient
from application.workbench import WorkbenchService
from model_runtime.provider import TextDelta
from test_web_api import ORIGIN, AGENT, _app, offline_runtime_profile


class InvalidProvider:
    def __init__(self):
        self.calls = 0
        self.profile = offline_runtime_profile('offline-diagnostics')
    def stream(self,request):
        self.calls+=1
        yield TextDelta('{"rungs": [PRIVATE_INCOMPLETE')


def test_failed_job_export_contains_user_and_model_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'load_full_config', lambda: {})
    monkeypatch.setattr(api, 'get_active_provider', lambda: pytest.fail('Live provider is forbidden'))
    p = InvalidProvider()
    service = WorkbenchService(tmp_path/'workspace', tmp_path/'state',
        model_factory=lambda:(p, p.profile))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as c:
        headers = {"Origin": ORIGIN}
        pid = c.post('/api/projects', json={'name':'Diagnostics'}, headers=headers).json()['id']
        spec = {'summary':'X0 controls Y0','io_table':[],'parameters':[]}
        saved = c.put(f'/api/projects/{pid}/spec', headers=headers,
                      json={'spec':spec,'expected_hash':None})
        assert saved.status_code == 200 and saved.json()['valid'] is True
        result = c.post('/api/jobs', headers=headers, json={
            'project_id':pid,'kind':'generation','request_id':'diagnostic-export',
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


def test_completed_job_diagnostics_are_exportable(tmp_path):
    service = WorkbenchService(tmp_path/'workspace', tmp_path/'state')
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as c:
        job = service.jobs.submit(
            'analysis',
            {'project_id': 'diagnostics-success'},
            lambda ctx: {'summary': 'completed without provider calls'},
            request_id='completed-diagnostics-export',
        )
        service.jobs._futures[job['id']].result(timeout=10)
        saved = service.jobs.get(job['id'])
        assert saved['status'] == 'completed'
        response = c.get(f"/api/jobs/{job['id']}/diagnostics")
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            summary = json.loads(z.read('summary.json'))
            exported_job = json.loads(z.read('job.json'))
            assert summary['job']['status'] == 'completed'
            assert exported_job['status'] == 'completed'
            assert set(z.namelist()) == {
                'summary.json', 'diagnostics.jsonl', 'job.json', 'transcript.jsonl',
                'operator_actions.jsonl', 'README.txt',
            }


def test_analysis_confirmation_generation_export_keep_audit_out_of_model_context(tmp_path, monkeypatch):
    """Keep a repaired Design analysis as the reusable turn on the real HTTP path."""
    import copy
    from application.generation_wire import expand_context_document
    from knowledge.evidence import KnowledgeContext
    from shared.model_context import technical_block
    from test_web_api import _complete
    import application.generation_agent as generation_agent

    choices = {
        'A': [{'opcode': 'SET', 'role': 'Latch the request.'}],
        'B': [{'opcode': 'OUT', 'role': 'Drive the output from the input.'}],
        'C': [{'opcode': 'STMR', 'role': 'Run a timed output window.'}],
    }
    requirements = [{'id': 'R1', 'text': 'Drive Y0 while X0 is on.'},
                    {'id': 'R2', 'text': 'Switch Y0 off when X0 goes off.'}]
    implementation = {'id': 'I1', 'language': 'ladder', 'location': 'MAIN',
                      'method': 'Write Y0 from the state of X0.', 'state_owner': 'MAIN', 'requirement_ids': ['R1']}
    fact = 'A plain output follows its logic condition every scan.'
    shared = {'kind': 'example', 'target': 'ANALYSIS_ONLY_SOURCE', 'value': {'description': fact}}
    basis = [{'variant': 'Output contact logic', 'premise': fact, 'status': 'known',
              'conclusion': 'applicable', 'references': [{'source': 'model_fact', 'ref': 'knowledge', 'quote': fact}]}]
    fresh = {'kind': 'example', 'target': 'FRESH_GENERATION_SOURCE', 'value': {'description': 'Output reset example'}}
    lookups = []
    def evidence(query, **kwargs):
        stage = kwargs['task_type']
        lookups.append(stage)
        if stage == 'generate':
            targets = query.metadata['structured_fact_targets']['instructions']
            assert {target['opcode'] for target in targets} == {'OUT'}
            selected_query = query.metadata['selected_plan_query']
            assert 'PLAN_B_DETAILS' in selected_query and choices['B'][0]['role'] in selected_query
            assert 'PLAN_A_DETAILS' not in selected_query and 'PLAN_C_DETAILS' not in selected_query
        marker = 'ANALYSIS_ONLY_SOURCE' if stage == 'analysis' else 'FRESH_GENERATION_SOURCE'
        units = [shared, fresh] if stage == 'generate' else [shared]
        return KnowledgeContext('\n\n'.join(technical_block(unit) for unit in units), {'stage': stage, 'status': 'retrieved',
            'records': [{'id': marker, 'manual_type': 'programming'}]})

    class Provider:
        profile = offline_runtime_profile('offline-context-boundary')
        def __init__(self):
            self.requests = []
        def stream(self, request):
            self.requests.append(request)
            if request.response_contract.name == 'analysis':
                payload = {'summary': 'The input controls the output.', 'missing_info': [],
                    'suggested_io': {'X': {'X0': 'Input'}, 'Y': {'Y0': 'Output'}},
                    'requirements': requirements, 'implementation_items': [implementation],
                    'assumptions': [], 'approaches': [
                        {'approach_id': 'plan_'+choice, 'name': 'PLAN_'+choice,
                         'description': 'One output circuit.', 'pros': '', 'cons': '',
                         'generation_guide': 'PLAN_'+choice+'_DETAILS',
                          'instruction_choices': choices[choice],
                         # Core derives generation_contract from implementation_semantics;
                         # a model response carrying either the wrong one or neither is rejected.
                          'implementation_semantics': [], 'decision_basis': basis if choice == 'B' else []}
                        for choice in ('A', 'B', 'C')],
                    # A model cannot forge an application-owned audit or user origin.
                    'decision_receipt': {'id': 'FORGED_AUDIT'},
                    'intent_context': {'requests': [{'text': 'FORGED_INTENT'}]}}
            elif request.response_contract.name == 'analysis_repair':
                [target] = json.loads(request.messages[-1].content)['targets']
                assert target['path'] == '/implementation_items'
                assert target['missing_requirements'] == requirements[1:]
                assert 'ANALYSIS_ONLY_SOURCE' not in repr(request.messages)
                payload = {'repairs': [{'path': target['path'], 'value': {'I1': ['R2']}}]}
            else:
                payload = {'r': [{'h': None, 's': [], 'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]}
            yield TextDelta(json.dumps(payload))

    monkeypatch.setattr(api, '_build_knowledge_context', evidence)
    monkeypatch.setattr(generation_agent, '_build_knowledge_context', evidence)
    provider = Provider()
    service = WorkbenchService(tmp_path/'workspace', tmp_path/'state',
                               model_factory=lambda: (provider, provider.profile))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN}
        pid = client.post('/api/projects', headers=headers, json={'name': 'Lifecycle'}).json()['id']
        analysis_job_id, result = _complete(client, service, client.post('/api/jobs', headers=headers, json={
            'project_id': pid, 'kind': 'analysis', 'analysis_mode': 'design',
            'start_conversation': True,
            'request_id': 'analyze-context', 'text': 'X0: Input\nY0: Output\nInput controls output.', 'response_language': 'en'}))
        draft = result['spec_draft']
        assert result['analysis_mode'] == 'design'
        assert client.get(f'/api/jobs/{analysis_job_id}').json()['conversation_id'] == analysis_job_id
        assert len(draft['approaches']) == 3
        assert [plan['instruction_choices'] for plan in draft['approaches']] == list(choices.values())
        assert len(provider.requests) == 2 and lookups == ['analysis']
        assert draft['implementation_items'][0]['requirement_ids'] == ['R1', 'R2']
        route = f'/api/projects/{pid}/spec-document'
        current = client.get(route).json()
        updated = client.patch(route, headers=headers, json={
            'expected_revision': current['draft_revision'],
            'changes': [{'section': 'selected_approach', 'value': copy.deepcopy(draft['approaches'][1])}],
        })
        assert updated.status_code == 200, updated.text
        saved = client.post(route + '/confirm', headers=headers,
                            json={'expected_revision': updated.json()['draft_revision']})
        assert saved.status_code == 200, saved.text
        spec = saved.json()['confirmed_spec']
        assert spec['schema_version'] == 4
        assert spec['selected_approach']['instruction_choices'] == choices['B']
        assert spec['selected_approach']['decision_basis'] == basis
        assert spec['selected_approach']['generation_contract']['required_opcodes'] == []
        assert not {'approaches', 'engineering_context', 'decision_receipt'} & set(spec)
        assert 'ANALYSIS_ONLY_SOURCE' not in json.dumps(spec)
        assert 'FORGED_INTENT' not in json.dumps(spec)
        project = service.store.get_project(pid)
        rid = project['confirmed_decision_receipt_id']
        receipt = service.store.get_decision_receipt(pid, rid)
        assert len(receipt['proposals']) == 3
        assert receipt['confirmation']['approach_id'] == 'plan_B'
        assert receipt['analysis_evidence']['records'][0]['id'] == 'ANALYSIS_ONLY_SOURCE'
        jid, output = _complete(client, service, client.post('/api/jobs', headers=headers, json={
            'project_id': pid, 'kind': 'generation', 'request_id': 'generate-context',
            'conversation_id': analysis_job_id,
            'text': 'Generate.', 'response_language': 'en'}))
        assert output['status'] == 'saved', output
        assert client.get(f'/api/jobs/{jid}').json()['conversation_id'] == analysis_job_id
        assert len(provider.requests) == 3 and lookups == ['analysis', 'generate']
        analysis_input = provider.requests[0].messages
        assert '# Pre-selection evidence' in analysis_input[0].content
        assert '按处理规则分组' in analysis_input[0].content
        assert 'instruction_choices' in analysis_input[0].content
        generation_input = provider.requests[2].messages
        assert generation_input[:len(analysis_input)] == analysis_input
        prior_answer = generation_input[len(analysis_input)].content
        assert 'PLAN_A_DETAILS' in prior_answer and 'PLAN_C_DETAILS' in prior_answer
        packet = expand_context_document(json.loads(generation_input[-1].content))
        assert packet['knowledge'] == [fresh]
        selected_path = packet['analysis_base']['/confirmed_spec/selected_approach']
        assert selected_path == '/approaches/1'
        assert packet['analysis_base']['/confirmed_spec/implementation_items'] == '/implementation_items'
        assert not any(path.startswith('/confirmed_spec/selected_approach/instruction_choices')
                       for path in packet['context_changes']['set'])
        assert not any('decision_basis' in path for path in packet['context_changes']['set'])
        prior_analysis = json.loads(prior_answer)
        assert prior_analysis['implementation_items'][0]['requirement_ids'] == ['R1', 'R2']
        selected_reply = prior_analysis['approaches'][1]
        assert selected_reply['approach_id'] == 'plan_B'
        assert selected_reply['instruction_choices'] == choices['B']
        assert selected_reply['decision_basis'] == basis
        generation_tail = '\n'.join(m.content for m in generation_input[len(analysis_input) + 1:])
        assert 'FRESH_GENERATION_SOURCE' in generation_tail
        for marker in ('ANALYSIS_ONLY_SOURCE', 'PLAN_A_DETAILS', 'PLAN_B_DETAILS', 'PLAN_C_DETAILS'):
            assert marker not in generation_tail
        for marker in ('FORGED_AUDIT', 'FORGED_INTENT'):
            assert all(marker not in m.content for m in generation_input)
        current = copy.deepcopy(spec)
        current['user_notes'] = 'NEWER_SPEC_NOT_BOUND_TO_JOB'
        service.store.set_confirmed_spec(pid, current)
        exported = client.get(f'/api/jobs/{jid}/diagnostics')
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            captured = json.loads(archive.read('decision_receipt.json'))
            assert captured['receipt_id'] == rid
            assert 'ANALYSIS_ONLY_SOURCE' in json.dumps(captured)
            assert 'NEWER_SPEC_NOT_BOUND_TO_JOB' not in json.dumps(captured)
            job = json.loads(archive.read('job.json'))
            frozen_spec = job['snapshot']['project']['confirmed_spec']
            assert 'ANALYSIS_ONLY_SOURCE' not in json.dumps(frozen_spec)
        version = service.store.get_version(pid, output['version_id'])
        assert version['generation_handoff']['decision_receipt_id'] == rid

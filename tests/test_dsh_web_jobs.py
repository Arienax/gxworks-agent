# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.
"""Drive Web jobs through the DSH tool bridge with an offline model substitute."""

import json
from itertools import count
from threading import Event
from types import SimpleNamespace
from urllib.request import ProxyHandler, Request, build_opener

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi.testclient import TestClient

from application.read_delivery import assemble_read_pages
from application.workbench import WorkbenchService
from gxw.project_templates import blank_ladder_baseline
from integrations.web.app import create_app
from knowledge.evidence import KnowledgeContext
from design_output_examples import contact_output_en


ORIGIN = 'http://127.0.0.1:8765'
HEADERS = {'Origin': ORIGIN}
REQUIREMENT = 'FX3U X0 controls Y0.'
MANUAL_TEXT = 'Offline manual fixture. ' + 'The input contact is read before the output is written. ' * 20


class FakeDshRuntime:
    def __init__(self, url, token, handler, instances, call_ids):
        self.url, self.token, self.handler = url, token, handler
        self.started = False
        self.resumed = None
        self.closed = Event()
        self.call_ids = call_ids
        self.reused_units = set()
        self.opener = build_opener(ProxyHandler({}))
        instances.append(self)

    def start(self):
        self.started = True

    def resume(self, session_id):
        assert self.started
        self.resumed = session_id

    def close(self):
        self.closed.set()

    def request(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.url + path, data=data,
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def invoke(self, name, **args):
        return self.request('/invoke', {'session_id': self.session_id, 'call_id': str(next(self.call_ids)),
                                        'name': name, 'arguments': args})

    def tool(self, name, **args):
        envelope = self.invoke(name, **args)
        assert envelope['ok'], envelope
        return envelope['data']

    def read(self, name, *, repeated_arguments=None, **args):
        pages, units = [], {}
        while True:
            data = self.tool(name, **args)
            entries = []
            for key, unit_id in data.get('conversation_unit_ids', {}).items():
                if data['context_schema_version'] == 4:
                    parts = key.strip('/').split('/')
                    container = data['document']
                    for part in parts[:-1]:
                        container = container[int(part) if isinstance(container, list) else part]
                    key = int(parts[-1]) if isinstance(container, list) else parts[-1]
                else:
                    container = data['objects']
                entries.append((container, key, unit_id))
            missing = list(dict.fromkeys(unit_id for container, key, unit_id in entries
                                        if '$conversation_ref' in container[key]))
            if missing:
                self.reused_units.update(missing)
                units.update(self.tool('gxworks_read_context_unit', ids=missing)['units'])
            for container, key, unit_id in entries:
                if '$conversation_ref' in container[key]:
                    container[key] = units[unit_id]
                else:
                    units[unit_id] = container[key]
            pages.append({'data': data})
            step = data['delivery']['next']
            if step is None:
                if repeated_arguments:
                    assert len(pages) > 1
                return pages[0]['data']['delivery']['document_id'], assemble_read_pages(pages), units
            if repeated_arguments:
                assert set(step['arguments']) == {'cursor'}
            name, args = step['tool'], {**step['arguments'], **(repeated_arguments or {})}

    def events(self, *frames):
        assert self.request('/event', {'session_id': self.session_id, 'frames': frames})['ok']

    def run(self, blocks, *, session_id, on_notification):
        assert self.started and not self.closed.is_set()
        self.session_id = session_id
        self.prompt = json.loads(blocks[0]['text'])
        on_notification(SimpleNamespace(method='session.event', payload={
            'sessionId': session_id, 'event': {'type': 'agent/inbox/spliced'},
        }))
        self.handler(self)
        return SimpleNamespace(final_response='Offline response')


@pytest.fixture
def offline_web(tmp_path, monkeypatch):
    monkeypatch.setattr('application.dsh_jobs.runtime_config', lambda *_: {})
    monkeypatch.setattr('application.gxw_delivery.create_blank',
                        lambda _out, _lang, cpu, **_kwargs: blank_ladder_baseline(cpu))
    monkeypatch.setattr('application.gxw_delivery.installation', lambda: None)
    knowledge_requests = []

    def knowledge(query, **_kwargs):
        from knowledge.evidence import evidence_record
        from shared.model_context import technical_knowledge_text
        knowledge_requests.append(str(query))
        row = {'id': 'contact-source', 'source': 'Manual fixture', 'source_file': 'manuals/contact.pdf',
               'manual_id': 'offline-manual', 'revision': 'M', 'pdf_page': 3, 'text': MANUAL_TEXT}
        block = technical_knowledge_text(row)
        return KnowledgeContext(block, {'records': [{**evidence_record(row, include_origin=True), 'model_text': block}]})

    monkeypatch.setattr('application.generation_context._build_knowledge_context', knowledge)
    instances, call_ids = [], count()
    workspace, state = tmp_path / 'workspace', tmp_path / 'state'

    def open_web(handler):
        def factory(_config, url, token, _home, _workspace):
            return FakeDshRuntime(url, token, handler, instances, call_ids)
        service = WorkbenchService(workspace, state, generation_runtime='dsh',
                                   dsh_runtime_factory=factory, model_factory=lambda: (None, {'model': 'offline'}))
        app = create_app(workspace, state_dir=state, service=service, origin=ORIGIN)
        return service, TestClient(app, base_url=ORIGIN, headers=HEADERS)

    return open_web, instances, knowledge_requests


def create_project(client):
    response = client.post('/api/projects', json={'name': 'FX3U offline project', 'plc_model': 'FX3U'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def submit(client, project_id, kind, request_id, **fields):
    response = client.post('/api/jobs', json={'project_id': project_id, 'kind': kind,
                                            'request_id': request_id, 'response_language': 'en', **fields})
    assert response.status_code == 202, response.text
    return response.json()['id']


def completed(client, service, job_id):
    job = service.jobs.wait(job_id, timeout=25)
    assert job['status'] == 'completed', json.dumps(job, ensure_ascii=False)
    response = client.get(f'/api/jobs/{job_id}/output')
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('mode', ['design', 'direct'])
def test_dsh_web_analysis_confirm_generate_and_edit_keep_one_session(offline_web, mode, monkeypatch):
    from application import generation_context
    from shared.model_context import technical_knowledge_text

    open_web, instances, knowledge_requests = offline_web
    evidence = {}
    retrieve = generation_context._build_knowledge_context
    shared = 'The input contact is sampled once per scan.'
    new = 'Output contact timing is relevant to implementation.'

    def retrieve_with_delta(query, **kwargs):
        base = retrieve(query, **kwargs)
        facts = [shared, new] if kwargs.get('task_type') == 'generate' else [shared]
        return KnowledgeContext('\n'.join([base, *(technical_knowledge_text({'text': fact}) for fact in facts)]),
                                base.manifest)

    monkeypatch.setattr(generation_context, '_build_knowledge_context', retrieve_with_delta)

    def analyze(runtime):
        names = {row['function']['name'] for row in runtime.request('/tools')['tools']}
        assert {'get_design_context', 'propose_design_plan'} <= names
        assert {'confirm_design_plan', 'select_project', 'create_project'}.isdisjoint(names)
        assert not runtime.invoke('create_program_candidate', ladder={})['ok']
        _, context, units = runtime.read('get_design_context', user_requirement='Invented by the model.',
                                         instruction_details=True)
        assert context['analysis_mode'] == mode
        assert MANUAL_TEXT.strip() in json.dumps(context['knowledge'])
        source = next(unit for unit in context['knowledge'] if MANUAL_TEXT.strip() == unit.get('text'))
        citation = source['citation_id']
        evidence.update({key: value for key, value in units.items() if MANUAL_TEXT.strip() in json.dumps(value)})
        assert evidence
        analysis = {
            'summary': REQUIREMENT, 'execution_intent_claims': [], 'explicit_constraint_claims': [],
            'approaches': [{'approach_id': 'contact', 'name': 'Input contact', 'description': 'Drive the output.',
                            'implementation_semantics': [], 'decision_basis': [{
                                'variant': 'Direct contact', 'premise': 'Input is read before output.',
                                'status': 'known', 'conclusion': 'applicable',
                                'references': [{'source': 'model_fact', 'ref': citation}],
                            }]}],
            'suggested_io': {'X': {'X0': 'Input'}, 'Y': {'Y0': 'Output'}},
        }
        data = runtime.tool('propose_design_plan', analysis=analysis, analysis_id='old-analysis')
        assert data['status'] == 'repair_required', data
        [target] = data['repair_targets']
        assert target['path'] == '/approaches/0/output_logic'
        assert target['value_schema']['items']['required']
        assert service.projects.raw_project(project_id)['confirmed_spec'] is None
        data = runtime.tool('propose_design_plan', repair_id='old-repair',
                            repairs=[{'path': target['path'], 'value': contact_output_en()}])
        assert data['user_requirement'] == REQUIREMENT
        assert not data['issues']['errors']
        receipt = data['review']['approaches'][0]['decision_basis'][0]['references'][0]
        assert receipt['ref'] == citation and MANUAL_TEXT.strip().startswith(receipt['quote'])
        from application.workspace import read_json
        [path] = (service.state_dir / 'mcp_design' / 'web').glob('*/' + project_id + '.json')
        evidence_record = read_json(path)['analysis']['decision_receipt']['fact_citations'][citation]
        assert evidence_record['text'] == MANUAL_TEXT.strip()
        assert evidence_record['plc_model'] == 'FX3U' and evidence_record['language'] == 'ladder'
        assert evidence_record['path'] == '/knowledge/0' and evidence_record['fact_version']
        [witness] = evidence_record['sources']
        assert witness['source_file'] == 'manuals/contact.pdf' and witness['pdf_page'] == 3
        assert witness['revision'] == 'M' and witness['source_text'] == MANUAL_TEXT.strip()
        assert 'manuals/contact.pdf' not in json.dumps(runtime.prompt)

    def generate(runtime):
        doc_id, context, _ = runtime.read('get_generation_context', instruction_details=True)
        assert context['output_contract']['protocol'] == 'compact_ladder/1.4'
        assert 'form_ref' in json.dumps(context['generation_input']['output_schema'])
        assert 'output_logic' in json.dumps(context)
        assert 'Assign Y0 from X0 once each scan.' in json.dumps(context)
        assert evidence.keys() & runtime.reused_units
        assert runtime.tool('get_current_project')['project_id'] == project_id
        manual_id, _, _ = runtime.read('search_plc_manual', query='LD', plc_model='FX3U', language='ladder', top_k=1)
        program = {'r': [{'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]}
        review = {'context_document': doc_id, 'manual_documents': [doc_id, manual_id],
                  'selected': 'Input contact', 'alternatives': []}
        store = service.projects.runtime.registry._read_delivery
        with store._lock:
            del store._documents[doc_id]
            del store._documents[manual_id]
        result = runtime.tool('create_program_candidate', generation_context_id='old-context',
                              implementation_review=review, ladder=program)
        assert result['implementation_review']['context_document'] == doc_id
        assert result['implementation_review']['manual_documents'] == [manual_id]

    service, client = open_web(lambda runtime: analyze(runtime) if runtime.prompt['stage'] == 'analysis' else generate(runtime))
    with client:
        project_id = create_project(client)
        analysis_id = submit(client, project_id, 'analysis', 'analysis', analysis_mode=mode,
                             start_conversation=True, text=REQUIREMENT)
        draft = completed(client, service, analysis_id)
        assert draft['analysis_mode'] == mode
        assert draft['spec_draft']['selected_approach']['approach_id'] == 'contact'
        assert service.projects.raw_project(project_id)['confirmed_spec'] is None
        assert instances[0].closed.is_set()
        document = f'/api/projects/{project_id}/spec-document'
        revision = client.get(document).json()['draft_revision']
        response = client.post(document + '/confirm', json={'expected_revision': revision})
        assert response.status_code == 200, response.text
        assert response.json()['confirmed_spec']['selected_approach']['approach_id'] == 'contact'
        generated = completed(client, service, submit(client, project_id, 'generation', 'generate',
                              conversation_id=analysis_id, text='Implement the confirmed design.'))
        version_id = generated['version_id']
        assert service.projects.raw_version(project_id, version_id)['artifacts']['gxw']
        retrievals = len(knowledge_requests)

    def edit(runtime):
        for key, value in evidence.items():
            assert runtime.tool('gxworks_read_context_unit', id=key) == value
        source = runtime.tool('read_gxw_source')
        assert source['version_id'] == version_id
        contact = next(row for row in source['source_records'] if row.get('op') == 'LD' and row.get('args') == ['X0'])
        runtime.tool('patch_program', patch={'instruction_edits': [{
            'operation': 'replace', 'record_index': contact['record_index'],
            'expected': {'op': 'LD', 'args': ['X0']}, 'instructions': [{'op': 'LDI', 'args': ['X0']}],
        }]})

    service, client = open_web(edit)
    with client:
        text = 'Invert the X0 contact and retain Y0.'
        edit_id = submit(client, project_id, 'generation', 'edit', conversation_id=analysis_id,
                         version_id=version_id, generation_action='edit', text=text)
        edited = completed(client, service, edit_id)
        assert service.projects.raw_version(project_id, edited['version_id'])['parent_version_id'] == version_id
        source = client.get('/api/gxw/source', params={'project_id': project_id, 'version_id': edited['version_id']})
        assert source.status_code == 200, source.text
        assert any(row.get('op') == 'LDI' and row.get('args') == ['X0'] for row in source.json()['source_records'])

    analysis, generation, modification = instances
    assert analysis.resumed is None and analysis.prompt['host']['confirmed_spec'] is None
    assert generation.resumed == modification.resumed == analysis.session_id
    assert generation.session_id == modification.session_id == analysis.session_id
    assert generation.prompt['host_changes']
    assert new in {unit.get('text') for unit in generation.prompt['new_knowledge']}
    assert shared not in json.dumps(generation.prompt)
    assert MANUAL_TEXT.strip() not in json.dumps(generation.prompt)
    assert all('knowledge' not in change['path'].split('/') for change in generation.prompt['engineering_changes'])
    assert 'new_knowledge' not in modification.prompt
    assert modification.prompt['user_request'] == text
    assert 'host' not in modification.prompt
    delta = {change['path']: change for change in modification.prompt['host_changes']}
    assert delta['/version_id'] == {'op': 'replace', 'path': '/version_id', 'value': version_id}
    assert set(delta) <= {'/version_id', '/plc_model'}
    assert REQUIREMENT not in json.dumps(modification.prompt)
    assert MANUAL_TEXT.strip() not in json.dumps(modification.prompt)
    assert all(text in query for query in knowledge_requests[retrievals:])


def test_mcp_design_read_reopens_the_saved_task_without_reretrieval(offline_web, tmp_path, monkeypatch):
    from agent_runtime.messages import ToolCall
    from application.engineering_session import EngineeringSession
    from integrations.mcp.tool_adapter import invoke_host_read

    _, _, retrievals = offline_web
    workspace, state = tmp_path / 'mcp-workspace', tmp_path / 'mcp-state'
    sequence = count()
    manual_row = {'id': 'contact-query', 'manual_id': 'contact-manual', 'source': 'Reference manual',
                  'revision': 'J', 'pdf_page': 18, 'text': 'LD reads the input contact before processing the output.'}
    monkeypatch.setattr('knowledge.retriever.retrieve_fact_aware_knowledge', lambda *_args, **_kw: [manual_row])

    def invoke(session, tool, **args):
        return invoke_host_read(ToolCall(f'design-resume-{next(sequence)}', tool, args), session.invoke, 32000).data

    with EngineeringSession(workspace, state_dir=state) as session:
        created = invoke(session, 'create_project', name='Restart Design', plc_model='FX3U', generation_mode='design')
        assert created['ok']
        project_id = session.project_id
        first = invoke(session, 'get_design_context', user_requirement=REQUIREMENT)
        assert first['ok'] and first['data']['delivery']['complete']
        document = first['data']['document']
        manual = invoke(session, 'search_plc_manual', query='LD', language='ladder')
        citation = manual['data']['document']['results'][0]['knowledge'][0]['citation_id']
    before = list(retrievals)
    with EngineeringSession(workspace, state_dir=state, project_id=project_id) as session:
        recovered = invoke(session, 'get_design_context')
        assert recovered['ok'] and recovered['data']['document'] == document
        explicit = invoke(session, 'get_design_context', analysis_id=document['analysis_id'])
        assert explicit['ok'] and explicit['data']['document'] == document
        proposal = invoke(session, 'propose_design_plan', analysis={
            'summary': REQUIREMENT, 'approaches': [{
                'approach_id': 'contact', 'name': 'Contact', 'description': 'Drive the output.',
                'implementation_semantics': [], 'output_logic': contact_output_en(), 'decision_basis': [{
                    'variant': 'Contact', 'premise': 'LD reads the input before the output.', 'status': 'known',
                    'conclusion': 'applicable', 'references': [{'source': 'model_fact', 'ref': citation}],
                }],
            }],
        })
        assert proposal['ok'] and proposal['data']['draft_id'], proposal
        from application.workspace import read_json
        draft = read_json(session.design._task_path(project_id, document['analysis_id']))
        [witness] = draft['analysis']['decision_receipt']['fact_citations'][citation]['sources']
        assert witness['source_text'] == manual_row['text'] and witness['pdf_page'] == 18
    assert retrievals == before


def test_dsh_direct_reuses_bound_user_answers_for_generation_and_edit(offline_web):
    open_web, instances, _ = offline_web
    requirement = 'X1：启动按钮；X3：停止按钮；Y0：运行输出。用 X1 启动，X3 停止。'

    def author(runtime):
        if runtime.resumed is None:
            result = runtime.invoke('gxworks_request_input', questions=[{
                'id': 'stop_level', 'question': 'X3按下停止按钮时为1还是0？',
                'options': ['1', '0'], 'required': True,
            }])
            assert result['status'] == 'waiting_for_user'
            return
        if runtime.prompt.get('user_answers') == {'stop_level': '0'}:
            assert runtime.invoke('gxworks_request_input', questions=[{
                'id': 'start_level', 'question': 'X1按下启动按钮时为1还是0？',
                'options': ['1', '0'], 'required': True,
            }])['status'] == 'waiting_for_user'
            return
        context = runtime.tool('get_generation_context')['document']
        owner = context['generation_input'] if runtime.prompt['stage'] == 'direct_generation' else context
        stop = next(row for row in owner['input_conditions']['level_predicates']
                    if row['address'] == 'X3' and 'run_permit_when' in row)
        assert stop['active_when'] == 'NC X3' and stop['run_permit_when'] == 'NO X3'
        condition = {'condition_ref': stop['binding_id']}
        if runtime.prompt['stage'] == 'direct_generation':
            assert runtime.prompt['user_answers'] == {'start_level': '1'}
            assert owner['decision_references']['answer'] == {'stop_level': '0', 'start_level': '1'}
            assert '/generation_instructions' not in {change['path'] for change in runtime.prompt['engineering_changes']}
            result = runtime.invoke('create_program_candidate', ladder={'r': [{
                'c': [{**condition, 'when': 'run_permit'}, 'NO X1'],
                'b': [{'i': [], 'o': ['COIL Y0']}],
            }]})
        else:
            assert not any('prior_user_input' in change['path'] for change in runtime.prompt['engineering_changes'])
            source = runtime.tool('read_gxw_source')
            row = next(item for item in source['source_records'] if item.get('args') == ['X3'])
            result = runtime.invoke('patch_program', patch={'instruction_edits': [{
                'operation': 'replace', 'record_index': row['record_index'],
                'expected': {'op': row['op'], 'args': row['args']},
                'instructions': [{'op': row['op'], **condition, 'when': 'active'}],
            }]})
        assert result['ok'] and result['status'] == 'saved', result

    service, client = open_web(author)
    with client:
        project_id = create_project(client)
        first = submit(client, project_id, 'direct_generation', 'ask-level', start_conversation=True, text=requirement)
        assert completed(client, service, first)['status'] == 'needs_input'
        answer = submit(client, project_id, 'direct_generation', 'answer-level', clarification_job_id=first,
                        clarification_answers={'stop_level': '0'})
        assert completed(client, service, answer)['status'] == 'needs_input'
        final_answer = submit(client, project_id, 'direct_generation', 'answer-start', clarification_job_id=answer,
                              clarification_answers={'start_level': '1'})
        saved = completed(client, service, final_answer)['version_id']
        handoff = service.projects.raw_version(project_id, saved)['generation_handoff']
        assert handoff['direct_answers'] == {'stop_level': '0', 'start_level': '1'}
        recorded_input = {'original_request': requirement, 'answers': [
            {'id': 'stop_level', 'question': 'X3按下停止按钮时为1还是0？', 'answer': '0'},
            {'id': 'start_level', 'question': 'X1按下启动按钮时为1还是0？', 'answer': '1'},
        ]}
        assert handoff['direct_requirements'] == recorded_input
        edit = submit(client, project_id, 'generation', 'edit-level', conversation_id=first, version_id=saved,
                      generation_action='edit', text='Use the active X3 level in the existing first condition.')
        revised = completed(client, service, edit)['version_id']
        result = client.get('/api/gxw/source', params={'project_id': project_id, 'version_id': revised}).json()
        assert any(row.get('op') in {'LDI', 'ANI'} and row.get('args') == ['X3'] for row in result['source_records'])
        assert not service.projects.raw_project(project_id)['confirmed_spec']
        from agent_runtime.messages import ToolCall
        from application.engineering_session import EngineeringSession
        with EngineeringSession.attach(service, project_id=project_id, version_id=revised) as session:
            view = session.invoke(ToolCall('reopen-user-input', 'get_generation_context', {
                'user_requirement': 'Change the run output.', 'read_budget_tokens': 64000})).data
            assert view['ok'] and view['data']['delivery']['complete'], view
            document = view['data']['document']
            assert document['prior_user_input'] == recorded_input
            assert 'generation_handoff' not in document
    assert len({instance.session_id for instance in instances}) == 1


def test_dsh_direct_uses_original_host_text_and_converts_usage(offline_web, monkeypatch):
    from shared.model_context import technical_knowledge_text

    knowledge = KnowledgeContext('\n'.join(technical_knowledge_text({'text': f'Reference {i}. {MANUAL_TEXT}'})
                                            for i in range(24)))
    monkeypatch.setattr('application.generation_context._build_knowledge_context', lambda *_, **__: knowledge)
    open_web, instances, _ = offline_web

    def direct(runtime):
        data = runtime.tool('get_generation_context', cursor='read-old:1', read_budget_tokens=1000)
        assert data['delivery']['complete'] and data['delivery']['next'] is None
        context = data['document']
        assert context['generation_input']['request'] == REQUIREMENT
        assert context['workflow_mode'] == 'direct_generation'
        usage = {'type': 'chunk', 'attemptId': 'first', 'chunk': {'type': 'usage', 'usage': {
            'inputTokens': 7, 'outputTokens': 3, 'cacheReadTokens': 11, 'cacheWriteTokens': 2,
        }}}
        runtime.events({'type': 'start'}, usage, usage, {'type': 'end'})
        question = runtime.invoke('gxworks_request_input', questions=[{'id': 'reset', 'question': 'Reset method?'}])
        assert question['ok'] and question['status'] == 'waiting_for_user', question

    service, client = open_web(direct)
    with client:
        project_id = create_project(client)
        job_id = submit(client, project_id, 'direct_generation', 'direct', text=REQUIREMENT)
        output = completed(client, service, job_id)
        assert output['status'] == 'needs_input'
        assert output['generation']['missing_info'] == [{'id': 'reset', 'question': 'Reset method?'}]
        usage = [event['payload'] for event in service.jobs.events(job_id)
                 if event['event_type'] == 'model_token_usage']
        assert usage == [{'input_tokens': 20, 'output_tokens': 3, 'cached_input_tokens': 11}]
    assert instances[0].prompt['user_request'] == REQUIREMENT


def test_dsh_project_change_ends_job_instead_of_asking_model_to_rebind(offline_web):
    from application.workspace import atomic_json

    open_web, _, _ = offline_web

    def changed_project(runtime):
        path = service.projects.store.project_path(project_id)
        project = service.projects.raw_project(project_id)
        project['plc_model'] = 'FX3G'
        atomic_json(path, project)
        result = runtime.invoke('create_program_candidate',
                                ladder={'r': [{'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]})
        assert result == {'ok': False, 'cancelled': True}

    service, client = open_web(changed_project)
    with client:
        project_id = create_project(client)
        job_id = submit(client, project_id, 'direct_generation', 'changed', text=REQUIREMENT)
        job = service.jobs.wait(job_id, timeout=25)
        assert job['status'] == 'failed'
        assert job['error_code'] == 'input_conflict'
        assert not service.projects.raw_project(project_id)['versions']


def test_dsh_direct_and_ordered_edit_bind_timer_presets_locally(offline_web, monkeypatch):
    open_web, instances, _ = offline_web
    bmov = {'id': 'bmov-fixture', 'fact_kind': 'instruction', 'fact_target': 'BMOV',
            'text': json.dumps({'kind': 'instruction', 'target': 'BMOV', 'scope': {'models': ['FX3U']},
                                'value': {'family': 'BMOV', 'forms': [{'opcode': 'BMOV'}]}})}
    monkeypatch.setattr('knowledge.retriever.retrieve_fact_aware_knowledge', lambda *_args, **_kw: [bmov])

    def author(runtime):
        context = runtime.prompt.get('engineering_context') or runtime.tool('get_generation_context')['document']
        assert 'form_ref' in context['generation_instructions']
        if runtime.prompt['stage'] == 'direct_generation':
            facts = runtime.tool('search_plc_manual', query='BMOV', language='ladder')['document']
            form, = facts['instruction_forms']
            assert [param['name'] for param in form['parameters']] == ['S', 'D', 'N']
            call = {'form_ref': form['form_ref'], 'parameters': [
                {'name': 'N', 'value': 'K4'}, {'name': 'D', 'value': 'D20'}, {'name': 'S', 'value': 'D0'},
            ]}
            timer = {'timer': 'T0', 'duration_ms': 600, 'tick_ms': 10}
            program = {'r': [{'c': ['NO X0'], 'b': [{'i': [], 'o': [timer]}]},
                             {'c': ['NO T0'], 'b': [{'i': [], 'o': ['COIL Y0']}]},
                             {'c': ['NO X0'], 'b': [{'i': [], 'o': [call]}]}]}
            invalid = runtime.invoke('create_program_candidate', ladder=program)
            diagnostic = invalid['error']['binding_diagnostic']
            assert diagnostic['reason'] == 'timer_timebase_mismatch' and diagnostic['expected']['tick_ms'] == 100
            timer['tick_ms'] = 100
            result = runtime.invoke('create_program_candidate', ladder=program)
        else:
            source = runtime.tool('read_gxw_source')
            timer = next(row for row in source['source_records'] if row.get('args') == ['T0', 'K6'])
            patch = {'instruction_edits': [{
                'operation': 'replace', 'record_index': timer['record_index'],
                'instructions': [{'timer': 'T0', 'duration_ms': 1200, 'tick_ms': 100}],
            }]}
            invalid_timer = patch['instruction_edits'][0]['instructions'][0]
            invalid_timer['tick_ms'] = 10
            invalid = runtime.invoke('patch_program', patch=patch)['error']['binding_diagnostic']
            assert invalid['path'] == 'content.instruction_edits.0.instructions.0'
            assert invalid['reason'] == 'timer_timebase_mismatch' and invalid['expected']['tick_ms'] == 100
            invalid_timer['tick_ms'] = 100
            result = runtime.invoke('patch_program', patch=patch)
        assert result['ok'] and result['status'] == 'saved', result
        assert result['data']['program_size']['steps'] > 0

    service, client = open_web(author)
    with client:
        project_id = create_project(client)
        first = submit(client, project_id, 'direct_generation', 'timer', start_conversation=True,
                       text='When X0 is held for 600 ms, turn on Y0 using T0. Copy D0 through D3 to D20 through D23 while X0 is on.')
        saved = completed(client, service, first)['version_id']
        second = submit(client, project_id, 'generation', 'timer-edit', conversation_id=first,
                        version_id=saved, generation_action='edit', text='Increase the T0 delay to 1200 ms.')
        revised = completed(client, service, second)['version_id']
        source = client.get('/api/gxw/source', params={'project_id': project_id, 'version_id': revised}).json()
        assert any(row.get('op') == 'OUT' and row.get('args') == ['T0', 'K12'] for row in source['source_records'])
        assert any(row.get('op') == 'BMOV' and row.get('args') == ['D0', 'D20', 'K4'] for row in source['source_records'])
        handoff = service.projects.raw_version(project_id, revised)['generation_handoff']
        assert handoff['external_context_id'] and handoff['change_request'] == 'Increase the T0 delay to 1200 ms.'
        assert instances[0].session_id == instances[1].session_id


def test_dsh_direct_binds_fact_ids_to_saved_candidate(offline_web):
    open_web, instances, _ = offline_web

    def direct(runtime):
        if runtime.prompt['stage'] == 'generation':
            source = runtime.tool('read_gxw_source')
            contact = next(row for row in source['source_records'] if row.get('op') == 'LD')
            patched = runtime.invoke('patch_program', patch={'instruction_edits': [{
                'operation': 'replace', 'record_index': contact['record_index'],
                'expected': {'op': 'LD', 'args': ['X0']}, 'instructions': [{'op': 'LDI', 'args': ['X0']}],
            }]})
            assert patched['ok'] and patched['status'] == 'saved_behavior_draft', patched
            assert patched['data']['activation_blocked']
            assert patched['data']['behavior_result']['address'] == 'Y0'
            return
        knowledge = runtime.prompt['engineering_context']['generation_input']['knowledge']
        unit = next(row for row in knowledge if row.get('text') == MANUAL_TEXT.strip())
        citation = unit['citation_id']
        candidate = {'method': 'Direct contact', 'tradeoff': 'No persistent state is needed.',
                     'decision_basis': [{'variant': 'Direct contact', 'premise': 'Read the contact first.',
                         'status': 'known', 'conclusion': 'applicable',
                         'references': [{'source': 'model_fact', 'ref': citation}]}]}
        ladder = {'result': {
            'program': {'r': [{'b': [{'i': ['NC X0'], 'o': ['COIL Y0']}]}]},
            'decisions': {'candidates': [candidate], 'selected': 0, 'rationale': 'Direct output.'},
            'open_items': [],
        }}
        rejected = runtime.invoke('create_program_candidate', ladder=ladder)
        assert rejected['error']['code'] == 'CANDIDATE_BEHAVIOR_MISMATCH'
        behavior = rejected['error']['behavior_result']
        assert behavior['case_id'] == 'output-on' and behavior['address'] == 'Y0'
        assert behavior['at_ms'] == 10 and behavior['expected'] == 1 and behavior['actual'] is False
        ladder['result']['program']['r'][0]['b'][0]['i'] = ['NO X0']
        response = runtime.invoke('create_program_candidate', ladder=ladder)
        assert response['ok'] and response['status'] == 'saved', response
        data = response['data']
        assert not data['activation_blocked'] and data['program_size']['steps'] == 3
        assert data['behavior_result']['status'] == 'passed_in_cases'

    service, client = open_web(direct)
    with client:
        project_id = create_project(client)
        from application.workspace import atomic_json
        atomic_json(service.store.project_dir(project_id) / 'acceptance-cases.json', {
            'schema_version': 1, 'project_id': project_id, 'plc_model': 'FX3U', 'spec_revision': None,
            'cases': [{'case_id': 'output-on', 'scan_ms': 10, 'steps': [
                {'at_ms': 0, 'set': {'X0': 1}},
                {'at_ms': 10, 'expect': {'Y0': 1}},
            ]}],
        })
        job_id = submit(client, project_id, 'direct_generation', 'direct-with-fact',
                        text=REQUIREMENT, start_conversation=True)
        result = completed(client, service, job_id)
        version = service.projects.raw_version(project_id, result['version_id'])
        assert result['status'] == 'saved'
        assert version['program_size']['basis'] == 'gxw_instruction_widths'
        assert service.projects.raw_project(project_id)['active_version_id'] == result['version_id']
        notes = version['generation_handoff']['generator_notes']
        assert not notes.get('decision_basis_issues')
        reference = notes['selection']['candidates'][0]['decision_basis'][0]['references'][0]
        assert reference['ref'] in notes['fact_citations']
        assert notes['fact_citations'][reference['ref']]['text'] == MANUAL_TEXT.strip()
        [witness] = notes['fact_citations'][reference['ref']]['sources']
        assert witness['manual_id'] == 'offline-manual' and witness['source_text'] == MANUAL_TEXT.strip()
        assert 'manuals/contact.pdf' not in json.dumps(instances[0].prompt)
        edit_id = submit(client, project_id, 'generation', 'edit-with-case',
                         conversation_id=job_id, version_id=result['version_id'], generation_action='edit',
                         text='Invert the X0 input.')
        edited = completed(client, service, edit_id)
        assert edited['status'] == 'saved_behavior_draft'
        assert edited['generation']['behavior_result']['case_id'] == 'output-on'
        assert service.projects.raw_project(project_id)['active_version_id'] == result['version_id']


def test_dsh_returns_the_local_counterexample_when_no_candidate_was_saved(offline_web):
    from application.workspace import atomic_json

    open_web, _, _ = offline_web

    def direct(runtime):
        result = runtime.invoke('create_program_candidate', ladder={
            'r': [{'b': [{'i': ['NC X0'], 'o': ['COIL Y0']}]}]})
        assert result['error']['code'] == 'CANDIDATE_BEHAVIOR_MISMATCH'

    service, client = open_web(direct)
    with client:
        project_id = create_project(client)
        atomic_json(service.store.project_dir(project_id) / 'acceptance-cases.json', {
            'schema_version': 1, 'project_id': project_id, 'plc_model': 'FX3U', 'spec_revision': None,
            'cases': [{'case_id': 'output-on', 'scan_ms': 10, 'steps': [
                {'at_ms': 0, 'set': {'X0': 1}},
                {'at_ms': 10, 'expect': {'Y0': 1}},
            ]}],
        })
        job_id = submit(client, project_id, 'direct_generation', 'reject-with-case',
                        text=REQUIREMENT, start_conversation=True)
        result = completed(client, service, job_id)
        assert result['status'] == 'behavior_mismatch' and not result.get('version_id')
        behavior = result['generation']['behavior_result']
        assert behavior['case_id'] == 'output-on' and behavior['at_ms'] == 10
        assert behavior['address'] == 'Y0' and behavior['expected'] == 1 and behavior['actual'] is False
        assert behavior['network_indexes'] == [0]
        assert service.projects.raw_project(project_id)['active_version_id'] is None


def test_manual_receipt_remains_valid_after_the_specification_changes(offline_web):
    from agent_runtime.messages import ToolCall
    from application.engineering_session import EngineeringSession

    open_web, _, _ = offline_web
    service, client = open_web(None)
    with client:
        project_id = create_project(client)
        with EngineeringSession.attach(service, project_id=project_id) as session:
            calls = count()

            def invoke(tool, **args):
                return session.invoke(ToolCall('binding-test-' + str(next(calls)), tool, args)).data

            def read(tool, **args):
                pages = []
                while True:
                    result = invoke(tool, **args)
                    assert result['ok'], result
                    pages.append(result)
                    step = result['data']['delivery']['next']
                    if not step:
                        return pages[0]['data']['delivery']['document_id'], assemble_read_pages(pages)
                    tool, args = step['tool'], step['arguments']

            manual_id, _ = read('search_plc_manual', query='LD', plc_model='FX3U', language='ladder', top_k=1)
            path = f'/api/projects/{project_id}/spec-document'
            revision = client.get(path).json()['draft_revision']
            changed = client.patch(path, json={'expected_revision': revision,
                                   'changes': [{'section': 'summary', 'value': 'Updated project specification.'}]})
            assert changed.status_code == 200, changed.text
            doc_id, context = read('get_generation_context', user_requirement=REQUIREMENT, workflow='direct_generation')
            result = invoke('create_program_candidate', generation_context_id=context['generation_context_id'],
                            implementation_review={'context_document': doc_id, 'manual_documents': [manual_id]},
                            ladder={'r': [{'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]})
            assert result['ok'] and result['status'] == 'saved', result
            assert result['data']['implementation_review']['manual_documents'] == [manual_id]


@pytest.mark.parametrize('references', ['explicit_manual', 'fact_only', 'lost_document', 'lost_receipt'])
def test_completed_receipts_reopen_without_retrieval_or_model_calls(offline_web, monkeypatch, references):
    from agent_runtime.messages import ToolCall
    from application.engineering_session import EngineeringSession
    from integrations.mcp.tool_adapter import invoke_host_read

    open_web, instances, knowledge_requests = offline_web
    ids = count()
    manual_row = {'id': 'ld-original', 'manual_id': 'ld-manual', 'source': 'Programming manual',
                  'revision': 'G', 'pdf_page': 30, 'text': 'LD reads the specified input bit.'}
    monkeypatch.setattr('knowledge.retriever.retrieve_fact_aware_knowledge', lambda *_args, **_kw: [manual_row])

    def call(session, tool, **args):
        return invoke_host_read(ToolCall(f'reopen-{next(ids)}', tool, args), session.invoke, 64000).data

    service, client = open_web(None)
    with client:
        project_id = create_project(client)
        other_project_id = create_project(client)
        with EngineeringSession.attach(service, project_id=project_id) as session:
            context = call(session, 'get_generation_context', user_requirement=REQUIREMENT,
                           workflow='direct_generation')
            manual = call(session, 'search_plc_manual', query='LD', plc_model='FX3U', language='ladder', top_k=1)
            assert context['ok'] and manual['ok']
            receipt = context['data']['document']['generation_context_id']
            document = context['data']['delivery']['document_id']
            manual_id = manual['data']['delivery']['document_id']
            citation = manual['data']['document']['results'][0]['knowledge'][0]['citation_id']
            assert 'ld-manual' not in json.dumps(manual)
        if references == 'lost_document':
            # The canonical read is still available through the project index.
            service.receipt_store.remove('read_ids', receipt)
    reads = len(knowledge_requests)

    service, client = open_web(None)
    with client:
        submitted_receipt = 'generation-context-' + '0' * 32 if references == 'lost_receipt' else receipt
        submitted_document = 'read-' + 'f' * 32 if references == 'lost_document' else document
        manual_documents = {'explicit_manual': [manual_id], 'lost_document': ['read-' + '0' * 32]}.get(references, [])
        def submit_candidate(session):
            return call(session, 'create_program_candidate', generation_context_id=submitted_receipt,
                        implementation_review={'context_document': submitted_document,
                            'manual_documents': manual_documents},
                        ladder={'result': {'program': {'r': [{'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]},
                            'decisions': {'selected': 0, 'rationale': 'Direct output.', 'candidates': [{
                                'method': 'Input contact', 'tradeoff': 'No retained state.', 'decision_basis': [{
                                    'variant': 'Input contact', 'premise': 'LD reads the input.', 'status': 'known',
                                    'conclusion': 'applicable', 'references': [{'source': 'model_fact', 'ref': citation}],
                                }],
                            }]}, 'open_items': []}})

        with EngineeringSession.attach(service, project_id=other_project_id) as session:
            wrong_project = submit_candidate(session)
            assert wrong_project['error']['code'] == 'INSTRUCTION_REVIEW_REQUIRED'
        with EngineeringSession.attach(service, project_id=project_id) as session:
            result = submit_candidate(session)
            assert result['ok'] and result['status'] == 'saved', result
            assert result['data']['implementation_review']['context_document'] == document
            assert result['data']['implementation_review']['manual_documents'] == (
                [manual_id] if references == 'explicit_manual' else [])
            version = service.projects.raw_version(project_id, result['data']['version_id'])
            assert version['generation_handoff']['external_context_id'] == receipt
            [source] = version['generation_handoff']['generator_notes']['fact_citations'][citation]['sources']
            assert (source['manual_id'], source['revision'], source['pdf_page']) == ('ld-manual', 'G', 30)
            assert source['source_text'] == manual_row['text']
    assert len(knowledge_requests) == reads and not instances


def test_dsh_web_cancel_interrupts_runtime_without_saving(offline_web):
    open_web, instances, _ = offline_web
    entered = Event()

    def wait_for_cancel(runtime):
        entered.set()
        assert runtime.closed.wait(10)
        raise EOFError('Stopped offline runtime')

    service, client = open_web(wait_for_cancel)
    with client:
        project_id = create_project(client)
        job_id = submit(client, project_id, 'direct_generation', 'cancel', text=REQUIREMENT)
        assert entered.wait(10)
        response = client.post(f'/api/jobs/{job_id}/cancel')
        assert response.status_code == 200, response.text
        assert service.jobs.wait(job_id, timeout=10)['status'] == 'cancelled'
        assert instances[0].closed.is_set()
        assert service.projects.raw_project(project_id)['active_version_id'] is None


def test_dsh_persistence_failure_does_not_report_a_resumable_success(offline_web, monkeypatch):
    open_web, instances, _ = offline_web

    def close(runtime):
        if not runtime.closed.is_set():
            runtime.closed.set()
            raise RuntimeError('Unable to persist session')

    monkeypatch.setattr(FakeDshRuntime, 'close', close)

    def ask(runtime):
        assert runtime.invoke('gxworks_request_input', questions=[{'id': 'input', 'question': 'Input?'}])['ok']

    service, client = open_web(ask)
    with client:
        project_id = create_project(client)
        job_id = submit(client, project_id, 'direct_generation', 'persist-error', text=REQUIREMENT)
        assert service.jobs.wait(job_id, timeout=10)['status'] == 'failed'
        assert not (service.state_dir / 'outputs' / (job_id + '.json')).exists()
        assert not service._dsh_jobs._active
        assert instances[0].closed.is_set()
        assert any(event['payload'].get('stage') == 'harness_error'
                   for event in service.jobs.events(job_id) if event['event_type'] == 'progress')


@pytest.mark.parametrize('kind', ['analysis', 'generation', 'direct_generation'])
def test_dsh_clarification_resumes_parent_kind_binding_and_only_user_answers(offline_web, kind):
    open_web, instances, _ = offline_web

    def ask(runtime):
        result = runtime.invoke('gxworks_request_input', questions=[{
            'id': 'level', 'question': 'Which input level?', 'required': True,
            'options': ['Normally open', 'Normally closed'],
        }])
        assert result['status'] == 'waiting_for_user'

    service, client = open_web(ask)
    with client:
        project_id = create_project(client)
        first = submit(client, project_id, kind, 'first', text=REQUIREMENT,
                       analysis_mode='design', start_conversation=True)
        assert completed(client, service, first)['status'] == 'needs_input'
        second = submit(client, project_id, kind, 'answer', clarification_job_id=first,
                        clarification_answers={'level': 'Normally closed'})
        assert completed(client, service, second)['status'] == 'needs_input'
        snapshot = service.jobs.record(second)['snapshot']
        if kind == 'analysis':
            assert snapshot['analysis_mode'] == 'design'
        assert snapshot['conversation_id'] == first
        assert REQUIREMENT in snapshot['user_fact_text']
        assert 'Normally closed' in snapshot['user_fact_text']
        assert 'Normally open' not in snapshot['user_fact_text']
        assert instances[1].resumed == instances[0].session_id
        assert instances[1].prompt['user_answers'] == {'level': 'Normally closed'}
        assert REQUIREMENT not in json.dumps(instances[1].prompt)
        wrong = client.post('/api/jobs', json={'project_id': project_id, 'kind': kind,
            'request_id': 'wrong-version', 'clarification_job_id': second,
            'version_id': 'v9999', 'clarification_answers': {'level': 'Normally open'}})
        assert wrong.status_code == 409, wrong.text

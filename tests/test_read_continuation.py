"""Saved-program repairs and readable document delivery share a stable contract."""
import copy

import pytest

from application.read_delivery import (
    ReadDeliveryStore, ReadDeliveryReceiver, assemble_read_pages, _fits,
)
from agent_runtime.tool_errors import ToolPreconditionError


def collect(store, page, binding):
    pages = [page]
    while pages[-1]['data']['delivery']['next']:
        follow = pages[-1]['data']['delivery']['next']
        pages.append(store.resume(follow['tool'], follow['arguments'], binding))
    return pages


def assert_cited_document(actual, expected, version):
    from plc.specification.fact_citations import cite_document
    expected = copy.deepcopy(expected)
    citations = cite_document(expected, model=expected['plc_model'],
                              language=expected['target_mode'], version=version)
    assert citations and actual == expected


def test_large_resolved_documents_keep_readable_facts_and_schema_references():
    schema = {'type': 'object', '$defs': {'item': {'type': 'string'}},
              'properties': {'value': {'$ref': '#/$defs/item'}}}
    document = {'plc_model': 'FX3U', 'target_mode': 'ladder',
                'generation_input': {'output_schema': schema, 'knowledge': [
                    {'kind': 'instruction', 'target': f'OP{i}', 'value': {'rule': str(i) + '资料' * 180}}
                    for i in range(18)]}}
    store = ReadDeliveryStore(max_tokens=1000, max_bytes=5000)
    pages = collect(store, store.prepare({'ok': True, 'tool': 'get_generation_context',
        'data': document}, {}, ('v1',)), ('v1',))
    assert len(pages) > 1
    assert all(p['data']['context_schema_version'] == 4 and 'objects' not in p['data'] for p in pages)
    assert all(_fits(p, 1000, 5000) for p in pages)
    assert_cited_document(assemble_read_pages(pages), document, pages[0]['data']['delivery']['fact_version'])
    receiver = ReadDeliveryReceiver()
    for i, page in enumerate(pages):
        receiver.receive('get_generation_context', page, binding='v1',
            cursor=None if i == 0 else page['data']['delivery']['document_id'] + ':' + str(i))
    with pytest.raises(ValueError):
        ReadDeliveryReceiver().receive('get_generation_context', pages[1], binding='v1', cursor='bad')


def test_explicit_legacy_paging_remains_available():
    store = ReadDeliveryStore(max_tokens=1000, max_bytes=5000)
    document = {'knowledge': [{'fact': 'x' * 800 + str(i)} for i in range(20)]}
    pages = collect(store, store.prepare({'ok': True, 'tool': 'search_plc_manual', 'data': document},
        {'read_format': 'paged'}, ('facts',)), ('facts',))
    assert all(p['data']['context_schema_version'] == 3 for p in pages)
    assert assemble_read_pages(pages) == document


@pytest.mark.parametrize('lost_reads', [1, 2])
def test_host_recovers_lost_cursor_without_delivering_partial_documents(lost_reads):
    from application.read_delivery import collect_host_read

    tool = 'search_plc_manual'
    store = ReadDeliveryStore(max_tokens=1000, max_bytes=5000)
    query = {'query': 'BMOV', 'top_k': 4}
    starts = []

    def read_page(read_id, args):
        if 'cursor' not in args:
            assert args == query
            starts.append(read_id)
            return store.prepare({'ok': True, 'tool': tool, 'data': {
                'query': query['query'], 'knowledge': [
                    {'fact': '资料' * 180, 'index': i, 'read': len(starts)} for i in range(16)],
            }}, args, ('FX3U', 'ladder', 'v1'))
        if len(starts) <= lost_reads:
            return {'ok': False, 'error': {'code': 'STALE_READ_CURSOR'}}
        return store.resume(tool, args, ('FX3U', 'ladder', 'v1'))

    result = collect_host_read(tool, query, 'request', read_page)
    assert starts == ['request', 'request:restart']
    if lost_reads == 2:
        assert result['error']['code'] == 'ENGINEERING_CONTEXT_CHANGED'
        assert 'data' not in result and 'next_actions' not in result['error']
    else:
        assert result['ok'] and result['data']['delivery']['next'] is None
        assert [row['index'] for row in result['data']['document']['knowledge']] == list(range(16))
        assert {row['read'] for row in result['data']['document']['knowledge']} == {2}


def test_reopened_generation_automatically_uses_only_a_unique_context(tmp_path):
    from application.generation_receipts import ToolReceiptStore

    receipts = ToolReceiptStore(tmp_path)
    binding = 'a' * 64
    store = ReadDeliveryStore(receipt_store=receipts)

    def read(store, suffix):
        receipt = 'generation-context-' + suffix * 32
        result = store.prepare({'tool': 'get_generation_context', 'ok': True, 'data': {
            'generation_context_id': receipt, 'knowledge': [{'text': f'Task {suffix}'}],
        }}, {}, ('project', 'version', 'facts'), review_binding=binding)
        return result['data']['delivery']['document_id'], receipt

    first = read(store, 'b')
    store = ReadDeliveryStore(receipt_store=receipts)
    assert store.check_review(binding, None)['generation_context_id'] == first[1]
    assert store.check_review('e' * 64, None) is None

    second = read(store, 'c')
    store = ReadDeliveryStore(receipt_store=receipts)
    with pytest.raises(ToolPreconditionError) as ambiguous:
        store.check_review(binding, None)
    assert {(row['context_document'], row['generation_context_id'])
            for row in ambiguous.value.details['context_candidates']} == {first, second}
    for document_id, receipt in (first, second):
        explicit = {'context_document': document_id, 'manual_documents': []}
        assert store.check_review(binding, explicit)['generation_context_id'] == receipt


def test_paged_read_resumes_from_saved_position_and_checks_project_revision(tmp_path):
    from application.generation_receipts import ToolReceiptStore

    receipts = ToolReceiptStore(tmp_path)
    tool = 'get_generation_context'
    original_binding = ('project-a', 'confirmed-v1', 'FX3U', 'facts-v1')
    document = {'plc_model': 'FX3U', 'knowledge': [
        {'text': '某一独立操作数条件。' * 75, 'index': index} for index in range(10)],
        'generation_context_id': 'generation-context-' + 'c' * 32,
    }
    first_store = ReadDeliveryStore(max_tokens=1100, receipt_store=receipts)
    first = first_store.prepare({'ok': True, 'tool': tool, 'data': document}, {}, original_binding)
    cursor = first['data']['delivery']['next']['arguments']
    second = first_store.resume(tool, cursor, original_binding)
    next_cursor = second['data']['delivery']['next']['arguments']
    reopened = ReadDeliveryStore(max_tokens=1100, receipt_store=ToolReceiptStore(tmp_path))
    with pytest.raises(ToolPreconditionError) as error:
        reopened.resume(tool, next_cursor, ('project-a', 'confirmed-v2', 'FX3U', 'facts-v1'))
    assert error.value.code == 'STALE_READ_CURSOR'
    pages = [first, second, *collect(reopened, reopened.resume(tool, next_cursor, original_binding), original_binding)]
    assert [row['index'] for row in assemble_read_pages(pages)['knowledge']] == list(range(10))
    assert not list((tmp_path / 'pending_reads').glob('*.json'))
    assert not list((tmp_path / 'read_progress').glob('*.json'))


def test_mcp_generation_continues_the_same_read_after_restarting(tmp_path, monkeypatch):
    from dataclasses import replace
    from itertools import count
    from agent_runtime.messages import ToolCall
    from application.engineering_session import EngineeringSession

    workspace, state = tmp_path / 'workspace', tmp_path / 'state'
    tool = 'get_generation_context'
    calls = count()
    knowledge = [{'text': '当前型号的一项完整工程事实。' * 50, 'index': index} for index in range(9)]

    def invoke(session, name, args):
        result = session.invoke(ToolCall(f'restart-{next(calls)}', name, args))
        assert not result.is_error, result.data
        return result.data

    with EngineeringSession(workspace, state_dir=state) as session:
        invoke(session, 'create_project', {'name': 'Resume context', 'plc_model': 'FX3U'})
        project_id = session.project_id
        registry = session.service.projects.runtime.registry
        monkeypatch.setitem(registry._tools, tool, replace(registry._tools[tool],
            handler=lambda _context, _args: {'plc_model': 'FX3U', 'target_mode': 'ladder', 'knowledge': knowledge}))
        first = invoke(session, tool, {'user_requirement': 'Original requirement', 'read_budget_tokens': 1500})

    pages = [first]
    assert first['data']['delivery']['next']
    with EngineeringSession(workspace, state_dir=state, project_id=project_id) as session:
        while follow := pages[-1]['data']['delivery']['next']:
            pages.append(invoke(session, follow['tool'], follow['arguments']))
    assert {page['data']['delivery']['document_id'] for page in pages} == {first['data']['delivery']['document_id']}
    assert_cited_document(assemble_read_pages(pages), {'plc_model': 'FX3U', 'target_mode': 'ladder', 'knowledge': knowledge},
                          first['data']['delivery']['fact_version'])


def test_host_budget_is_checked_after_reusing_delivered_facts():
    from agent_runtime.messages import ToolCall, ToolResult
    from application.context_units import ContextUnits
    from integrations.mcp.tool_adapter import invoke_host_read, to_mcp_result

    name = 'search_plc_manual'
    store, units = ReadDeliveryStore(), ContextUnits()
    fact = {'text': '原生接口的行为及操作数约束。' * 145}
    calls = []

    def invoke(call):
        calls.append(call.arguments)
        if 'cursor' in call.arguments:
            page = store.resume(name, call.arguments, ('facts-v1',))
        else:
            page = store.prepare({'ok': True, 'tool': name, 'data': {
                'plc_model': 'FX3U', 'language': 'ladder',
                'results': [{'knowledge': [fact]} for _ in range(4 if call.arguments['query'] == 'broader' else 1)],
            }}, call.arguments, ('facts-v1',))
        return ToolResult(call.id, name, '', page)

    budget = 3200
    first = invoke_host_read(ToolCall('one', name, {'query': 'known'}), invoke, budget, read_units=units)
    assert not first.is_error
    first_fact, = first.data['data']['document']['results'][0]['knowledge']
    second = invoke_host_read(ToolCall('two', name, {'query': 'broader'}), invoke, budget, read_units=units)
    assert not second.is_error
    assert any('cursor' in arguments for arguments in calls)
    assert not to_mcp_result(second, read_units=units).is_error
    data = second.data['data']
    assert data['delivery']['complete'] and data['delivery']['next'] is None
    refs = [row['knowledge'][0]['$conversation_ref'] for row in data['document']['results']]
    assert len(refs) == 4 and len(set(refs)) == 1
    assert units.restore({'id': refs[0]})['data'] == first_fact


def test_host_rejects_oversize_mcp_wrapper_without_caching_undelivered_facts():
    from agent_runtime.messages import ToolCall, ToolResult
    from application.context_units import ContextUnits
    from application.read_delivery import serialized_size
    from integrations.mcp.tool_adapter import _read_result, invoke_host_read

    name = 'search_plc_manual'
    store, units = ReadDeliveryStore(), ContextUnits()
    fact = {'text': '操作数的位置和写法。' * 220}
    payload = {'ok': True, 'tool': name, 'data': {
        'plc_model': 'FX3U', 'language': 'ladder', 'results': [{'knowledge': [fact]}]}}

    def invoke(call):
        return ToolResult(call.id, name, '', store.prepare(payload, call.arguments, ('facts-v1',)))

    query = ToolCall('read', name, {'query': 'operand'})
    body = invoke(query).data
    wire = _read_result(body).model_dump(by_alias=True, exclude_none=True)
    budget = serialized_size(wire)['estimated_tokens']
    assert 1000 <= budget < 6000
    assert serialized_size(body)['estimated_tokens'] < budget
    blocked = invoke_host_read(query, invoke, budget - 1, read_units=units)
    assert blocked.is_error and blocked.data['error']['code'] == 'HOST_CONTEXT_TOO_LARGE'
    assert not units.units

    complete = invoke_host_read(query, invoke, budget, read_units=units)
    assert not complete.is_error
    result = complete.data['data']['document']['results'][0]['knowledge'][0]
    assert result['text'] == fact['text']


def test_only_host_delivered_contexts_are_eligible_for_implicit_submission(tmp_path):
    from agent_runtime.messages import ToolCall, ToolResult
    from application.generation_receipts import ToolReceiptStore
    from integrations.mcp.tool_adapter import invoke_host_read

    receipts = ToolReceiptStore(tmp_path)
    store = ReadDeliveryStore(receipt_store=receipts)
    tool, project, binding = 'get_generation_context', 'project-a', 'c' * 64
    original_binding = (project, 'version')

    def invoke(call):
        if call.arguments.get('cursor'):
            body = store.resume(tool, call.arguments, original_binding)
        else:
            large = call.arguments['user_requirement'] == 'oversize'
            receipt = 'generation-context-' + ('a' if large else 'b') * 32
            body = store.prepare({'ok': True, 'tool': tool, 'data': {
                'generation_context_id': receipt, 'knowledge': [{'text': 'a' * 1200}] * (32 if large else 1),
            }}, call.arguments, original_binding, fact_owner=project, review_binding=binding,
                defer_read=call.defer_read)
        return ToolResult(call.id, tool, '', body)

    def finish(name, identity, accepted):
        reopened = ReadDeliveryStore(receipt_store=receipts)
        assert reopened.check_review(binding, None) is None
        assert reopened.finish_host_read(name, identity, 'other-project', accepted=True) is None
        return store.finish_host_read(name, identity, project, accepted=accepted)

    denied = invoke_host_read(ToolCall('large', tool, {'user_requirement': 'oversize'}), invoke, 1000,
                              finish_read=finish)
    assert denied.data['error']['code'] == 'HOST_CONTEXT_TOO_LARGE'
    assert store.check_review(binding, None) is None
    assert not list((tmp_path / 'pending_reads').glob('*.json'))

    delivered = invoke_host_read(ToolCall('small', tool, {'user_requirement': 'small'}), invoke, 16000,
                                 finish_read=finish)
    assert not delivered.is_error
    reopened = ReadDeliveryStore(receipt_store=receipts)
    assert reopened.check_review(binding, None)['generation_context_id'] == 'generation-context-' + 'b' * 32


def test_referenced_facts_match_project_cpu_language_and_library_version(tmp_path):
    from application.generation_receipts import ToolReceiptStore

    tool = 'search_plc_manual'
    scope = ('FX3U', 'ladder', 'search-db', 'owner-db', 'facts-v1')
    receipts = ToolReceiptStore(tmp_path)
    store = ReadDeliveryStore(max_tokens=1300, receipt_store=receipts)
    read = store.prepare({'ok': True, 'tool': tool, 'data': {'results': [
        {'knowledge': [{'text': '当前型号操作数边界及作用。' * 70, 'index': i}]} for i in range(6)],
    }}, {}, scope, manual_scope=scope, fact_owner='project-a')
    citation = read['data']['document']['results'][0]['knowledge'][0]['citation_id']
    assert read['data']['delivery']['next']
    reopened = ReadDeliveryStore(receipt_store=receipts)
    assert not reopened.referenced_citations('project-a', scope, {citation})
    collect(store, read, scope)
    assert citation in reopened.referenced_citations('project-a', scope, {citation})
    assert not reopened.referenced_citations('project-b', scope, {citation})
    for different_scope in [('Q03UDE', *scope[1:]), (scope[0], 'st', *scope[2:]), (*scope[:-1], 'facts-v2')]:
        assert not reopened.referenced_citations('project-a', different_scope, {citation})


def test_default_read_pages_fit_codex_wrapper_without_losing_body():
    import json
    from agent_runtime.messages import ToolResult
    from integrations.mcp.tool_adapter import to_mcp_result
    from application.read_delivery import serialized_size
    # Repeated nested JSON and Chinese text exercise the escaping used when a
    # Codex host prints the MCP result object instead of its text content.
    document = {'plc_model': 'Q03UDE', 'target_mode': 'ladder', 'knowledge': [
        {'target': str(i), 'value': {'rule': '条件仅约束此构造；现场参数未知。' * 70,
                                   'parameters': [{'name': 'S', 'role': 'input'}]}}
        for i in range(20)]}
    store = ReadDeliveryStore()
    pages = collect(store, store.prepare({'ok': True, 'tool': 'get_generation_context',
        'data': document}, {}, ('v1',)), ('v1',))
    received = []
    for page in pages:
        mcp = to_mcp_result(ToolResult('call', 'get_generation_context', '', page))
        wire = mcp.model_dump(by_alias=True, exclude_none=True)
        assert mcp.structured_content is None and not mcp.is_error
        assert serialized_size(wire)['estimated_tokens'] <= 6000
        assert serialized_size(wire)['utf8_bytes'] <= 32 * 1024
        # Exercise both documented text-only printing and historical whole-result
        # printing; this is serialization coverage, not a claim about a tokenizer.
        printed = json.dumps(wire, ensure_ascii=False)
        received.append(json.loads(json.loads(printed)['content'][0]['text']))
    assert len(pages) > 1
    assert_cited_document(assemble_read_pages(received), document, received[0]['data']['delivery']['fact_version'])


def test_large_multi_axis_document_negotiates_budget_without_reducing_knowledge():
    from application.context_units import ContextUnits
    # Independent axes and one indivisible interface larger than a default
    # page. No PLC instruction or sorter-specific path participates here.
    interface = {'axis_parameters': {f'parameter_{i}': {
        'type': 'DINT', 'unit': 'pulse', 'description': '接口参数说明' * 8}
        for i in range(600)}}
    document = {'plc_model': 'Q03UDE', 'target_mode': 'ladder',
                'generation_input': {'call_interface': {'positioning': interface},
                    'knowledge': [{'axis': i, 'interface': 'positioning',
                                   'rules': '独立状态及诊断。' * 180} for i in range(24)]}}
    store = ReadDeliveryStore()
    args = {'user_requirement': '多轴独立定位与同步控制', 'read_format': 'resolved'}
    with pytest.raises(ToolPreconditionError) as caught:
        store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': document}, args, ('v1',))
    error = caught.value
    assert error.code == 'READ_BUDGET_TOO_SMALL'
    retry = error.next_actions[0]['arguments']
    assert {k: v for k, v in retry.items() if k != 'read_budget_tokens'} == args
    assert error.details['host_output_tokens'] == retry['read_budget_tokens'] * 2
    pages = collect(store, store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': document},
                                        retry, ('v1',)), ('v1',))
    assert_cited_document(assemble_read_pages(pages), document, pages[0]['data']['delivery']['fact_version'])
    assert len(assemble_read_pages(pages)['generation_input']['knowledge']) == 24

    # A cached large fact can be recovered completely with the same host
    # budget negotiation, rather than asking the model to narrow its content.
    units = ContextUnits()
    units.units['large-interface'] = interface
    with pytest.raises(ToolPreconditionError) as caught:
        units.restore({'id': 'large-interface'})
    restored = units.restore(caught.value.next_actions[0]['arguments'])
    assert restored['data'] == interface


def test_manual_facts_survive_program_save_but_not_model_language_or_fact_changes():
    store = ReadDeliveryStore()
    scope = ('FX3U', 'ladder', 'facts-1')
    manual = store.prepare({'ok': True, 'tool': 'search_plc_manual', 'data': {
        'knowledge': [{'kind': 'instruction', 'target': 'BMOV', 'value': {'arity': 3}}]}},
        {}, scope, manual_scope=scope)
    context = store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': {
        'generation_context_id': 'after-save'}}, {}, ('v2',))
    review = {'context_document': context['data']['delivery']['document_id'],
              'manual_documents': [manual['data']['delivery']['document_id']]}
    result = store.check_review(('v2',), review, 'after-save', manual_scope=scope)
    assert any(f.get('target') == 'BMOV' for f in result['model_facts'])
    for changed in [('Q', 'ladder', 'facts-1'), ('FX3U', 'st', 'facts-1'), ('FX3U', 'ladder', 'facts-2')]:
        result = store.check_review(('v2',), copy.deepcopy(review), 'after-save', manual_scope=changed)
        assert not any(f.get('target') == 'BMOV' for f in result['model_facts'])


def test_context_unit_restore_reloads_local_body_and_returns_partial_availability(tmp_path):
    from application.context_units import ContextUnits

    path = tmp_path / 'context-units.json'
    reader = ContextUnits(path)
    ContextUnits(path).remember({'recorded': {'text': 'Manual body'}})
    result = reader.restore({'ids': ['recorded', 'unavailable']})
    assert result['ok']
    assert result['data'] == {'units': {'recorded': {'text': 'Manual body'}}, 'unavailable_ids': ['unavailable']}
    assert reader.restore({'id': 'recorded'})['data'] == {'text': 'Manual body'}


def test_mcp_reuses_facts_across_resolved_pages():
    from application.context_units import ContextUnits
    store = ReadDeliveryStore(max_tokens=1000, max_bytes=5000)
    units = ContextUnits()
    document = {'plc_model': 'FX3U', 'target_mode': 'ladder', 'knowledge': [
        {'kind': 'instruction', 'target': str(i), 'value': {'text': str(i) + 'x' * 1000}}
        for i in range(15)]}
    def read():
        return collect(store, store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': document},
            {}, ('v1',)), ('v1',))
    for page in read():
        units.project(page)
    repeated = [units.project(p) for p in read()]
    rows = [r for p in repeated for r in p['data']['document'].get('knowledge', [])]
    assert len(rows) == 15 and all('$conversation_ref' in r for r in rows)


def test_saved_compact_program_accepts_local_correction_without_retrieval(tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from application import generation_context
    from tests.test_mcp import _mcp_body
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        adapter = LocalMCPToolAdapter(session)
        def call(name, args, identity):
            result = _mcp_body(adapter.call_tool(name, args, identity))
            assert result['ok'], result
            return result
        call('create_project', {'name': 'Partial correction', 'plc_model': 'FX3U',
                               'generation_mode': 'direct'}, 'project')
        manual_pages = []
        args = {'query': 'LD OUT', 'plc_model': 'FX3U', 'language': 'ladder', 'top_k': 1}
        while True:
            manual = call('search_plc_manual', args, 'manual-' + str(len(manual_pages)))
            manual_pages.append(manual)
            follow = manual['data']['delivery']['next']
            if not follow:
                break
            args = follow['arguments']
        manual_id = manual_pages[0]['data']['delivery']['document_id']
        saved = call('create_program_candidate', {'ladder': {'r': [
            {'c': ['NO X0'], 'b': [{'i': [], 'o': ['COIL Y0']}]},
            {'c': ['NO X1'], 'b': [{'i': [], 'o': ['COIL Y1']}]},
        ]}}, 'first')
        assert saved['status'] == 'saved'
        assert saved['data']['continuation']['version_id'] == saved['version_id']
        def no_retrieval(*args, **kwargs):
            pytest.fail('A saved local correction must not rebuild RAG or repeat selection')
        monkeypatch.setattr(generation_context, '_build_knowledge_context', no_retrieval)
        pages = []
        args = {'workflow': 'direct_generation', 'user_requirement': 'Y0 改为 X0 或 X2 驱动'}
        while True:
            page = call('get_generation_context', args, 'context-' + str(len(pages)))
            pages.append(page)
            follow = page['data']['delivery']['next']
            if not follow:
                break
            args = follow['arguments']
        ctx = assemble_read_pages(pages)
        assert ctx['output_contract']['protocol'] == 'compact_ladder/1.4'
        assert ctx['output_contract']['mode'] == 'partial'
        assert ctx['generation_input']['current_program']['r']
        corrected = call('create_program_candidate', {
            'generation_context_id': ctx['generation_context_id'],
            'implementation_review': {'context_document': pages[0]['data']['delivery']['document_id'],
                                      'manual_documents': [manual_id]},
            'ladder': {'mode': 'partial', 'delete_rung_ids': [], 'device_comments': [], 'r': [
                {'rung_id': 1, 'c': [], 'b': [{'i': [{'or': [['NO X0'], ['NO X2']]}], 'o': ['COIL Y0']}]}]},
        }, 'correct')
        assert corrected['status'] == 'saved' and corrected['version_id'] != saved['version_id']
        program = session.service.projects.program(session.project_id, corrected['version_id'])
        operations = [(i['op'], i['args']) for n in program['networks'] for i in n['instructions']]
        assert ('OR', ['X2']) in operations
        assert ('OUT', ['Y1']) in operations and ('LD', ['X1']) in operations
        stale = _mcp_body(adapter.call_tool('create_program_candidate', {
            'generation_context_id': ctx['generation_context_id'], 'ladder': {'r': []}}, 'stale'))
        assert not stale['ok']  # An unrelated later save is never silently overwritten.

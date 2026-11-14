"""Focused specification tool and shared proposal dispatch checks."""
from contextlib import nullcontext, closing
from types import SimpleNamespace
from unittest.mock import Mock

import unittest

from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from agent_runtime.runtime import build_default_tool_runtime, public_tool_result_data
from application.proposal_workflow import ProposalWorkflow
from application.workspace import ConflictError


def test_spec_read_is_project_scoped_and_preserves_plan_fields():
    document = {'project_id': 'project_1', 'draft_revision': 'r1',
        'draft': {'implementation_items': [{'id': 'i1', 'method': 'TON', 'state_owner': 'MAIN'}],
                  'parameters': [{'id': 'p1', 'value': {'custom_time': 15}}]},
        'version_spec': {'requirements': [{'id': 'old'}]}}
    context = build_tool_context({'id': 'project_1', '_spec_document': document})
    result = build_default_tool_runtime().invoke(ToolCall('read1', 'get_project_spec', {}), context)
    assert public_tool_result_data(result)['data'] == document
    result.data['data']['draft']['parameters'].clear()
    assert len(context.project['_spec_document']['draft']['parameters']) == 1


def test_spec_missing_context_does_not_create_or_infer_project():
    registry = build_default_tool_registry()
    assert registry.call('get_project_spec', {}, build_tool_context({}))['error']['code'] == 'NO_PROJECT_SELECTED'
    assert registry.call('get_project_spec', {}, build_tool_context({'id': 'project_1'}))['error']['code'] == 'SPEC_CONTEXT_UNAVAILABLE'


def test_update_is_pending_and_confirmation_requires_true_and_string_revision():
    registry = build_default_tool_registry()
    context = build_tool_context({'id': 'project_1'}, version={'id': 'historic'})
    arguments = {'expected_revision': 'r1', 'changes': [{'section': 'parameters', 'id': 'p1', 'value': 25}]}
    result = registry.call('update_project_spec', arguments, context)
    assert result['status'] == 'confirmation_required'
    assert result['data']['pending_action'] == {'type': 'update_project_spec', 'project_id': 'project_1', **arguments}
    for arguments in ({'expected_revision': 'r1'}, {'expected_revision': 'r1', 'user_confirmed': False},
                      {'expected_revision': 1, 'user_confirmed': True}):
        assert registry.call('confirm_project_spec', arguments, context)['error']['code'] == 'INVALID_ARGUMENTS'
    assert registry.call('confirm_project_spec', {'expected_revision': 'r1', 'user_confirmed': True}, context)['ok']


def workflow(mode='ask'):
    proposals = Mock()
    proposals.create.return_value = {'id': 'proposal_1', 'action': 'spec_update', 'status': 'pending'}
    pending = {'type': 'update_project_spec', 'project_id': 'project_1',
        'expected_revision': 'r1', 'changes': [{'section': 'parameters', 'id': 'p1', 'value': 25}]}
    proposals.read_private.return_value = pending
    commands = Mock()
    commands.get_spec_document.return_value = {'draft_revision': 'r1'}
    commands.update_spec_document.return_value = {'project_id': 'project_1'}
    def accept(_id, executor, **_kwargs):
        return {'id': 'proposal_1', 'action': 'spec_update', 'status': 'accepted', 'result': executor(pending, _id)}
    proposals.accept.side_effect = accept
    instance = ProposalWorkflow(projects=SimpleNamespace(store=Mock()), state_dir=None,
        lock=SimpleNamespace(thread_lock=nullcontext()), proposals=proposals, jobs=Mock(),
        execution=Mock(), approval=SimpleNamespace(read=lambda: {'mode': mode, 'revision': 0}),
        previews=Mock(), require_writable=Mock(), complete_native=Mock(), project_commands=commands)
    return instance, proposals, commands, pending


def test_service_ask_preserves_proposal_and_local_uses_shared_command():
    instance, proposals, commands, pending = workflow()
    assert instance.pending_proposal(pending, 'request_1')['status'] == 'pending'
    commands.update_spec_document.assert_not_called()
    assert instance.pending_proposal(pending, 'request_2', direct_request=True)['status'] == 'accepted'
    commands.update_spec_document.assert_called_once_with('project_1', pending['changes'], 'r1')


def test_completed_spec_proposal_replay_does_not_revalidate_old_revision():
    instance, proposals, commands, pending = workflow()
    commands.get_spec_document.return_value = {'draft_revision': 'r2'}
    saved = {'id': 'proposal_1', 'action': 'spec_update', 'status': 'accepted'}
    assert instance._accept_spec(saved) == saved
    commands.get_spec_document.assert_not_called()
    proposals.accept.assert_not_called()


def test_draft_revision_conflict_stops_before_execution():
    instance, proposals, commands, pending = workflow()
    commands.get_spec_document.return_value = {'draft_revision': 'r2'}
    with unittest.TestCase().assertRaises(ConflictError):
        instance.pending_proposal(pending, 'request_1', direct_request=True)
    commands.update_spec_document.assert_not_called()
    proposals.accept.assert_not_called()


def test_local_mcp_roundtrip_and_context_read_only(tmp_path=None):
    import tempfile
    from pathlib import Path
    from application.engineering_session import EngineeringSession
    from application.spec_document import document_path
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    from storage.session import SessionStore
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with EngineeringSession(root / 'workspace', state_dir=root / 'state') as session:
            adapter = LocalMCPToolAdapter(session)
            def call(name, args, identity):
                result = adapter.call_tool(name, args, identity)
                assert not result.is_error, result
                return result.structured_content
            created = call('create_project', {'name': 'spec-local', 'plc_model': 'FX3U'}, 'create')
            assert created['data']['project']['target_mode'] == 'ladder'
            before = call('get_project_spec', {}, 'read')['data']
            assert Path(before['path']).is_file()
            assert isinstance(before['draft_revision'], str)
            changes = [
                {'section': 'requirements', 'id': 'r1', 'value': {'text': 'X0 drives Y0', 'source': 'user'}},
                {'section': 'implementation_items', 'id': 'i1', 'value': {'requirement_ids': ['r1'],
                    'language': 'ladder', 'location': 'MAIN/N1', 'method': 'direct contact', 'state_owner': 'MAIN'}}]
            updated = call('update_project_spec', {'changes': changes, 'expected_revision': before['draft_revision']}, 'update')
            assert updated['status'] == 'saved'
            assert updated['data']['draft']['requirements'][0]['text'] == 'X0 drives Y0'
            assert updated['data']['confirmed_spec'] is None
            current_revision = updated['data']['draft_revision']
            confirmed = call('confirm_project_spec', {'expected_revision': current_revision, 'user_confirmed': True}, 'confirm')
            assert confirmed['status'] == 'saved'
            assert confirmed['data']['confirmed_spec']['implementation_items'][0]['id'] == 'i1'
            replay = call('confirm_project_spec', {'expected_revision': current_revision, 'user_confirmed': True}, 'confirm')
            assert replay == confirmed
        # Older workspaces can have a project without a specification file.
        store = SessionStore(root / 'context')
        project = store.create_project(name='context', plc_model='FX3U')
        spec_path = document_path(store, project['id'])
        assert not spec_path.exists()
        provider = SessionToolContextProvider(root / 'context', project['id'])
        adapter = MCPToolAdapter(build_default_tool_runtime(), provider)
        result = adapter.call_tool('get_project_spec', {}, 'read')
        assert not result.is_error, result
        document = result.structured_content['data']
        assert document['path'] == str(spec_path)
        assert isinstance(document['differences'], list)
        result = adapter.call_tool('update_project_spec', {'expected_revision': document['draft_revision'],
            'changes': [{'section': 'requirements', 'id': 'r1', 'value': {'text': 'draft'}}]}, 'update')
        assert not result.is_error, result
        assert result.structured_content['status'] == 'confirmation_required'
        assert not spec_path.exists()


def test_service_mcp_ask_requires_operator_approval():
    import tempfile
    from pathlib import Path
    from application.workbench import WorkbenchService
    from integrations.mcp.service_client import ServiceMCPToolAdapter
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with closing(WorkbenchService(root / 'workspace', root / 'state').start()) as service:
            project = service.create_project(name='service', plc_model='FX3U')
            class Bridge:
                def call_tool(self, name, arguments, call_id, *, project_id, version_id=None):
                    response = service.agent_call({'project_id': project_id, 'version_id': version_id,
                        'name': name, 'arguments': arguments, 'call_id': call_id})
                    data = response['data']
                    if response.get('proposal_id'):
                        data['proposal_id'] = response['proposal_id']
                    return ToolResult(call_id, name, '', data, is_error=response['is_error'])
            adapter = ServiceMCPToolAdapter(Bridge(), project['id'])
            read = adapter.call_tool('get_project_spec', {}, 'read').structured_content['data']
            result = adapter.call_tool('update_project_spec', {'expected_revision': read['draft_revision'],
                'changes': [{'section': 'requirements', 'id': 'r1', 'value': {'text': 'user request'}}]}, 'update')
            assert not result.is_error, result
            data = result.structured_content
            assert service.proposals.get(data['proposal_id'])['status'] == 'pending'
            assert not service.project_commands.get_spec_document(project['id'])['draft'].get('requirements')
            service.decide(data['proposal_id'], 'accept')
            assert service.project_commands.get_spec_document(project['id'])['draft']['requirements'][0]['id'] == 'r1'


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
        if name.startswith('test_') and callable(value))


if __name__ == '__main__':
    unittest.main()

"""New ST source candidates share Web validation and ordinary version saving."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from application.native_st import prepare_generated_candidate, validate_candidate


class STGenerationTests(unittest.TestCase):
    def test_new_managed_st_requires_confirmation_before_preparation(self):
        context = build_tool_context({'id': 'p1', 'plc_model': 'FX3U', 'target_mode': 'st',
            '_spec_document': {'draft': {'requirements': [{'id': 'r1', 'text': 'draft'}]}}})
        registry = build_default_tool_registry()
        with patch('application.native_st.prepare_generated_candidate') as prepare:
            result = registry.call('create_st_candidate', {'operation': 'generate', 'st_code': 'Y0 := X0;'}, context)
        self.assertEqual(result['error']['code'], 'SPEC_CONFIRMATION_REQUIRED')
        prepare.assert_not_called()

    def test_generated_source_is_checked_and_cannot_claim_native_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = prepare_generated_candidate(directory, st_code='Y0 := X0;', plc_model='FX3U')
            self.assertEqual(set(payload['artifacts']), {'st'})
            self.assertEqual(payload['metadata']['validation']['gx_compile'], 'not_run')
            self.assertEqual(payload['metadata']['validation']['native_compile'], 'unverified')
            source = (Path(directory) / 'program.st').read_bytes()
            validate_candidate({'st': source}, plc_model='FX3U', metadata=payload['metadata'])
            payload['metadata']['validation']['source_binding']['kind'] = 'program'
            with self.assertRaises(ValueError):
                validate_candidate({'st': source}, plc_model='FX3U', metadata=payload['metadata'])
            with self.assertRaises(ValueError):
                prepare_generated_candidate(directory, st_code='  ', plc_model='FX3U')

    def test_new_st_context_returns_the_actual_source_submission_tool(self):
        from agent_runtime.generation_tools import _get_generation_context
        context = build_tool_context({'id': 'p1', 'plc_model': 'FX3U', 'target_mode': 'st',
            'confirmed_spec': {'plc_model': 'FX3U', 'target_mode': 'st',
                'requirements': [{'id': 'r1', 'text': 'X0 drives Y0'}]}})
        result = _get_generation_context(context, {'user_requirement': 'generate confirmed source'})
        self.assertEqual(result['output_contract']['tool'], 'create_st_candidate')
        self.assertEqual(result['output_contract']['operation'], 'generate')
        self.assertEqual(result['output_contract']['required_arguments'], ['operation', 'st_code'])
        self.assertEqual(result['output_contract']['delivery'], 'st_source_only')

    def test_web_uses_shared_source_preparation_without_a_model_call(self):
        from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
        with tempfile.TemporaryDirectory() as directory:
            dependencies = GenerationDependencies(stream_response=lambda *a, **kw: ('', json.dumps({'st_code': 'Y0 := X0;'})))
            request = GenerationRequest(user_input='source', target_mode='st', plc_model='FX3U', model_name='offline-test')
            with patch('application.native_st.prepare_generated_candidate', wraps=prepare_generated_candidate) as prepare:
                result = GenerationWorkflow(request, directory, dependencies=dependencies).run()
            prepare.assert_called_once()
            self.assertEqual(result['artifacts'], {'st': 'program.st'})
            self.assertEqual(result['validation']['source_binding']['kind'], 'generated_st_source')

    def test_local_mcp_confirm_generate_read_and_replace_source_version(self):
        from application.engineering_session import EngineeringSession
        from integrations.mcp.local_adapter import LocalMCPToolAdapter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with EngineeringSession(root / 'workspace', state_dir=root / 'state') as session:
                adapter = LocalMCPToolAdapter(session)
                counter = 0
                def call(name, args):
                    nonlocal counter
                    counter += 1
                    result = adapter.call_tool(name, args, str(counter))
                    self.assertFalse(result.is_error, result)
                    return result.structured_content
                call('create_project', {'name': 'new-st', 'plc_model': 'FX3U', 'target_mode': 'st'})
                document = call('get_project_spec', {})['data']
                changes = [{'section': 'requirements', 'id': 'r1', 'value': {'text': 'X0 drives Y0', 'source': 'user'}},
                    {'section': 'implementation_items', 'id': 'i1', 'value': {'requirement_ids': ['r1'], 'language': 'st',
                        'location': 'MAIN', 'method': 'ST assignment', 'state_owner': 'MAIN'}}]
                document = call('update_project_spec', {'expected_revision': document['draft_revision'], 'changes': changes})['data']
                call('confirm_project_spec', {'expected_revision': document['draft_revision'], 'user_confirmed': True})
                with patch('application.fbd._native_candidate', side_effect=AssertionError('Native operation is forbidden')):
                    result = call('create_st_candidate', {'operation': 'generate', 'st_code': 'Y0 := X0;'})
                self.assertEqual(result['status'], 'saved')
                first = result['data']['version_id']
                version = session.service.projects.raw_version(session.project_id, first)
                self.assertEqual(version['confirmed_spec_snapshot']['requirements'][0]['id'], 'r1')
                self.assertEqual(version['validation']['native_compile'], 'unverified')
                self.assertNotIn('gxw', version['artifacts'])
                source = call('read_st_project', {})['data']
                self.assertEqual(source['programs'][0]['st_text'], 'Y0 := X0;')
                result = call('create_st_candidate', {'operation': 'generate', 'st_code': 'Y0 := NOT X0;'})
                second = result['data']['version_id']
                self.assertEqual(session.service.projects.raw_version(session.project_id, second)['parent_version_id'], first)


if __name__ == '__main__':
    unittest.main()

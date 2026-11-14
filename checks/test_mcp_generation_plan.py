"""First-generation planning preconditions without models or native execution."""
import unittest
from unittest.mock import Mock, patch

from agent_runtime.generation_tools import GenerationToolHandlers, _create_program_candidate
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from agent_runtime.tool_context import _generation_spec, _require_generation_plan
from agent_runtime.tool_errors import ToolPreconditionError


def context(*, version=None, confirmed=None, managed=True):
    project = {'id': 'p1', 'plc_model': 'FX3U', 'target_mode': 'ladder'}
    if confirmed:
        project['confirmed_spec'] = confirmed
    if managed:
        project['_spec_document'] = {'draft': {'requirements': [{'id': 'r1', 'text': 'unconfirmed'}]},
            'confirmed_spec': confirmed, 'differences': [{'path': '$.requirements'}]}
    return build_tool_context(project, version=version)


class GenerationPlanTests(unittest.TestCase):
    def test_raw_ladder_and_fbd_candidates_block_before_preparation(self):
        registry = build_default_tool_registry()
        new = context()
        for name, arguments in [('create_program_candidate', {'ladder': {}}),
                                 ('create_fbd_candidate', {'operation': 'generate', 'model': {}})]:
            if name == 'create_fbd_candidate':
                new = build_tool_context({**new.project, 'target_mode': 'fbd'})
            with self.subTest(name=name), patch('plc.core.PLCCore') as core, \
                    patch('application.fbd.prepare_candidate') as prepare:
                result = registry.call(name, arguments, new)
                self.assertEqual(result['error']['code'], 'SPEC_CONFIRMATION_REQUIRED')
                self.assertIn('confirm_project_spec', [row['tool'] for row in result['error']['next_actions']])
                core.assert_not_called()
                prepare.assert_not_called()

    def test_compatible_direct_maintenance_and_embedding_paths_remain(self):
        for current, args, handoff in [
            (context(), {'workflow': 'direct_generation'}, None),
            (context(), {}, {'mode': 'direct_generation'}),
            (context(version={'id': 'historic'}), {}, None),
            (context(managed=False), {}, None),
            (context(confirmed={'requirements': [{'id': 'approved'}]}), {}, None),
        ]:
            with self.subTest(args=args, handoff=handoff):
                _require_generation_plan(current, args, generation_handoff=handoff)
        with self.assertRaises(ToolPreconditionError):
            _require_generation_plan(context(), {'workflow': 'detailed'})
        with self.assertRaises(ToolPreconditionError):
            _require_generation_plan(context(), {'workflow': 'detailed'},
                generation_handoff={'mode': 'direct_generation'})

    def test_current_confirmation_and_historical_program_remain_separate(self):
        approved = {'requirements': [{'id': 'approved', 'text': 'use current confirmed constraints'}]}
        old = {'requirements': [{'id': 'old'}]}
        current = build_tool_context({**context(confirmed=approved).project},
            version={'id': 'historic', 'confirmed_spec_snapshot': old},
            program_ir={'program_name': 'OLD_SOURCE'})
        self.assertEqual(_generation_spec(current), approved)
        core = Mock()
        core.create_program_candidate.side_effect = ValueError('stop after inspecting bound inputs')
        with patch('plc.core.PLCCore', return_value=core), self.assertRaisesRegex(ValueError, 'bound inputs'):
            _create_program_candidate(current, {'ladder': {}})
        self.assertEqual(core.create_program_candidate.call_args.kwargs['confirmed_spec'], approved)
        self.assertEqual(core.create_program_candidate.call_args.kwargs['previous_program'], current.program_ir)
        self.assertNotIn('unconfirmed', str(core.create_program_candidate.call_args.kwargs['confirmed_spec']))

    def test_explicit_direct_schema_is_limited_to_generation_operations(self):
        registry = build_default_tool_registry()
        definition = registry._tools['create_program_candidate']
        registry._validate_arguments(definition.parameters, {'ladder': {}, 'workflow': 'direct_generation'})
        definition = registry._tools['create_fbd_candidate']
        registry._validate_arguments(definition.parameters,
            {'operation': 'generate', 'model': {}, 'workflow': 'direct_generation'})
        with self.assertRaises(ValueError):
            registry._validate_arguments(definition.parameters,
                {'operation': 'compile', 'workflow': 'direct_generation'})

    def test_only_matching_direct_handoff_can_select_compatibility_path(self):
        def builder(current, args, generation_handoff=None):
            _require_generation_plan(current, args, generation_handoff=generation_handoff)
            return {'accepted': True}
        handlers = GenerationToolHandlers(lambda c, a: {'generation_handoff': {'mode': 'direct_generation'}},
            builder, builder, builder)
        current = context()
        receipt = handlers.context(current, {'workflow': 'direct_generation'})['generation_context_id']
        self.assertTrue(handlers.candidate(current, {'generation_context_id': receipt})['accepted'])
        with self.assertRaises(ToolPreconditionError):
            handlers.candidate(current, {'generation_context_id': 'expired'})


if __name__ == '__main__':
    unittest.main()

# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""External-model entry to the Web Design analysis and confirmation workflow."""
from uuid import uuid4
import json

from agent_runtime.tool_errors import ToolPreconditionError
from application.projects import public_spec_hash
from application.workspace import atomic_json, read_json


class DesignSession:
    def __init__(self, service):
        self.service = service

    def _path(self, project_id):
        return self.service.state_dir / 'mcp_design' / (project_id + '.json')

    def start(self, project_id):
        atomic_json(self._path(project_id), {'stage': 'get_design_context'})

    def guard_generation(self, project_id, arguments):
        path = self._path(project_id)
        if not path.is_file():
            return
        state = read_json(path)
        stage = state['stage']
        if stage != 'confirmed':
            raise ToolPreconditionError('DESIGN_PENDING', '先完成当前 Design 计划并由用户确认。',
                                        next_actions=[{'tool': stage}])
        if arguments.get('workflow') == 'direct_generation' and not arguments.get('cursor'):
            raise ToolPreconditionError('DESIGN_CONFIRMED', '请按已确认规格实施。',
                next_actions=[{'tool': 'get_generation_context', 'arguments': {'workflow': 'detailed'}}])

    def context(self, project_id, requirement, *, manual_context=None):
        from application.analysis_context import assemble_analysis_prompt
        from application.analysis_results import attach_analysis_evidence
        from application.generation_context import _build_knowledge_context, _load_plc_models
        from knowledge.evidence import KnowledgeQuery
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        from shared.model_context import technical_context_units

        project = self.service.projects.raw_project(project_id)
        model = project['plc_model']
        spec = project.get('confirmed_spec')

        def knowledge(query, **options):
            return _build_knowledge_context(KnowledgeQuery(query, precompiled=True,
                metadata={'target_mode': project['target_mode']}), **options)

        prompt = assemble_analysis_prompt(requirement, plc_model=model, confirmed_context=spec,
            analysis_mode='design', model_loader=_load_plc_models, knowledge_builder=knowledge,
            resolve_opcode=lambda op: DEFAULT_INSTRUCTION_REGISTRY.resolve_form(op, cpu=model))
        state = {'stage': 'propose_design_plan', 'analysis_id': uuid4().hex, 'requirement': requirement,
                 'contract_stage': prompt.contract_stage, 'base_hash': public_spec_hash(spec),
                 'analysis_evidence': attach_analysis_evidence({}, prompt.knowledge_context)['decision_receipt']['analysis_evidence']}
        atomic_json(self._path(project_id), state)
        instructions = prompt.system_prompt
        if prompt.knowledge_context.strip():
            instructions = instructions.replace(prompt.knowledge_context.strip(), 'See the knowledge field.', 1)
        extra = {}
        if manual_context is not None:
            from application.instruction_research import include_manual_facts, research_plan
            from knowledge.retriever import discover_capabilities
            language = project['target_mode']
            discovery = discover_capabilities(requirement, plc_model=model, target_mode=language)
            plan = research_plan({'knowledge': discovery['candidates']})
            extra['instruction_research'] = include_manual_facts(plan, manual_context, language)
        return {'analysis_id': state['analysis_id'], 'analysis_mode': 'design', 'plc_model': model, **extra,
                'target_mode': project['target_mode'], 'analysis_instructions': instructions,
                'knowledge': technical_context_units(prompt.knowledge_context),
                'next_actions': [{'tool': 'propose_design_plan'}]}

    def _pending(self, project_id):
        path = self._path(project_id)
        state = read_json(path) if path.is_file() else {}
        project = self.service.projects.raw_project(project_id)
        if (state.get('stage') not in {'propose_design_plan', 'confirm_design_plan'}
                or public_spec_hash(project.get('confirmed_spec')) != state.get('base_hash')):
            raise ToolPreconditionError('DESIGN_CONTEXT_REQUIRED', '请用用户原文获取当前工程的 Design 上下文。',
                                        next_actions=[{'tool': 'get_design_context'}])
        return state, project

    def propose(self, project_id, args):
        from application.analysis_response import _parse_analysis_response
        from plc.specification.confirmed import build_review_draft, validate_spec_draft

        state, project = self._pending(project_id)
        if 'analysis' in args:
            if state['analysis_id'] != args.get('analysis_id'):
                raise ValueError('analysis_id 必须来自当前工程的 get_design_context。')
            result = _parse_analysis_response(json.dumps(args['analysis'], ensure_ascii=False),
                project['plc_model'], state['requirement'], project.get('confirmed_spec'),
                contract_stage=state['contract_stage'])
            result['analysis_mode'] = 'design'
            result['decision_receipt']['analysis_evidence'] = state['analysis_evidence']
            draft = build_review_draft(result, project.get('confirmed_spec'))
            ids = [a.get('approach_id') for a in draft['approaches']]
            if None in ids or len(set(ids)) != len(ids) or len(ids) > 3:
                raise ValueError('每个设计方案需要独立的 approach_id，最多提供三个方案。')
            state.update(draft=draft, draft_id=uuid4().hex, stage='confirm_design_plan')
            atomic_json(self._path(project_id), state)
        elif args or 'draft' not in state:
            raise ValueError('提交模型的 analysis 和 analysis_id；已有草稿可以用空参数重新查看。')
        draft = state['draft']
        review = {k: draft[k] for k in ('summary', 'approaches', 'io_table', 'io_bindings', 'parameters',
                  'hardware_requirements', 'hardware_context', 'execution_semantics', 'behavior_constraints',
                  'operation_intents', 'user_notes') if draft.get(k)}
        return {'draft_id': state['draft_id'], 'user_requirement': state['requirement'], 'review': review,
                'issues': validate_spec_draft(draft, project['plc_model']),
                'next_actions': [{'tool': 'confirm_design_plan'}] if draft.get('approaches') else []}

    def confirm(self, project_id, draft_id, approach_id):
        state, project = self._pending(project_id)
        if state.get('draft_id') != draft_id:
            raise ValueError('草稿已替换，请重新查看当前计划。')
        draft = state['draft']
        choice = next((a for a in draft['approaches'] if a['approach_id'] == approach_id), None)
        if not choice:
            raise ValueError('所选方案不在当前草稿中。')
        draft['selected_approach'] = choice
        saved = self.service.set_spec(project_id, draft, expected_hash=state['base_hash'])
        if not saved['valid']:
            return {'confirmed': False, 'issues': saved['issues'], 'next_actions': [{'tool': 'propose_design_plan'}]}
        atomic_json(self._path(project_id), {'stage': 'confirmed'})
        return {'confirmed': True, 'project_id': project_id, 'target_mode': project['target_mode'],
                'approach_id': approach_id, 'spec_hash': saved['hash'],
                'decision_receipt_id': self.service.projects.raw_project(project_id)['confirmed_decision_receipt_id'],
                'next_actions': [{'tool': 'get_generation_context', 'arguments': {'workflow': 'detailed'}}]}

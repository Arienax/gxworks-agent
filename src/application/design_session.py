# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""External-model entry to the Web Design analysis and confirmation workflow."""
from uuid import uuid4
import copy
import json

from agent_runtime.tool_errors import ToolPreconditionError
from application.projects import public_spec_hash
from application.workspace import atomic_json, read_json


def _should_auto_drop_claim_target(target):
    if target.get('mode') != 'claim':
        return False
    current = target.get('current_value')
    if isinstance(current, dict) and current.get('inferred') is True:
        return True
    preserve = target.get('must_preserve') or {}
    if preserve.get('unsupported_target') or preserve.get('unsupported_candidate'):
        return True
    return not any(preserve.get(key) for key in ('targets', 'source_devices', 'effect_devices', 'explicit_scan_evidence'))


class DesignSession:
    def __init__(self, service):
        self.service = service

    def _path(self, project_id):
        return self.service.state_dir / 'mcp_design' / (project_id + '.json')

    def start(self, project_id):
        atomic_json(self._path(project_id), {'stage': 'get_design_context'})

    def is_read_complete(self, project_id, analysis_id):
        path = self._path(project_id)
        if not analysis_id or not path.is_file():
            return False
        state = read_json(path)
        return state.get('analysis_id') == analysis_id and bool(state.get('read_complete'))

    def mark_read_complete(self, project_id, analysis_id):
        path = self._path(project_id)
        if not analysis_id or not path.is_file():
            return
        state = read_json(path)
        if state.get('analysis_id') == analysis_id and not state.get('read_complete'):
            state['read_complete'] = True
            atomic_json(path, state)

    def guard_generation(self, project_id, arguments, *, tool_name=None):
        path = self._path(project_id)
        if not path.is_file():
            return
        project = self.service.projects.raw_project(project_id)
        if project.get('versions'):
            return
        state = read_json(path)
        stage = state['stage']
        if stage != 'confirmed':
            if (tool_name in (None, 'get_generation_context')
                    and arguments.get('workflow') == 'direct_generation'
                    and str(arguments.get('user_requirement') or '').strip()
                    and not project.get('confirmed_spec')):
                path.unlink(missing_ok=True)
                return
            raise ToolPreconditionError(
                'DESIGN_PENDING',
                '当前工程处于未确认的 Design 流程：开放式工艺方案请先完成 Design 并由用户确认；若为跨品牌/跨语言代码移植转写或规格已完整给定的任务，可调用 get_generation_context(workflow="direct_generation", user_requirement=...) 切换为直接生成。',
                next_actions=[
                    {'tool': stage},
                    {'tool': 'get_generation_context', 'arguments': {'workflow': 'direct_generation'}},
                ],
            )
        if arguments.get('workflow') == 'direct_generation' and not arguments.get('cursor'):
            raise ToolPreconditionError('DESIGN_CONFIRMED', '请按已确认规格实施。',
                next_actions=[{'tool': 'get_generation_context', 'arguments': {'workflow': 'detailed'}}])

    def context(self, project_id, requirement, *, manual_context=None):
        from application.analysis_context import assemble_analysis_prompt
        from application.analysis_repair import design_submission_schema
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
                'target_mode': project['target_mode'], 'analysis_instructions': instructions, 'analysis_schema': design_submission_schema(),
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
        if 'analysis' in args or 'repairs' in args:
            from application.analysis_repair import apply_analysis_repair, plan_analysis_repair
            if state['analysis_id'] != args.get('analysis_id'):
                raise ValueError('analysis_id 必须来自当前工程的 get_design_context。')
            if 'repairs' in args and 'analysis' in args:
                raise ValueError('请选择提交初稿或按路径修正。')
            requirement = state['requirement']
            model = project['plc_model']
            spec = project.get('confirmed_spec')
            if 'repairs' in args:
                if 'repair' not in state or state.get('repair_id') != args.get('repair_id'):
                    raise ValueError('修正必须对应当前待修草稿。')
                payload, _ = apply_analysis_repair(state['repair'], {'repairs': args['repairs']}, requirement,
                                                   plc_model=model, confirmed_spec=spec)
                dropped = list(state.get('dropped_claims') or [])
            else:
                payload = args['analysis']
                dropped = []
            plan = plan_analysis_repair(payload, requirement, plc_model=model, confirmed_spec=spec,
                                        contract_stage=state['contract_stage'])
            state.pop('dropped_claims', None)
            # Inferred or unanchored prose claims cannot bind; drop them to the
            # receipt while keeping user-anchored claims for structural repair.
            rejected_claims = {
                target['path']: ((target.get('errors') or [{}])[0].get('code') or 'unanchored_claim')
                for target in plan['targets'] if _should_auto_drop_claim_target(target)
            }
            if rejected_claims:
                for field in ('explicit_constraint_claims', 'execution_intent_claims'):
                    if isinstance(plan['base'].get(field), list):
                        for i, claim in enumerate(plan['base'][field]):
                            path_key = f'/{field}/{i}'
                            if path_key in rejected_claims:
                                entry = {'field': field, 'reason': rejected_claims[path_key],
                                         'claim': copy.deepcopy(claim)}
                                if entry not in dropped:
                                    dropped.append(entry)
                        plan['base'][field] = [claim for i, claim in enumerate(plan['base'][field])
                                               if f'/{field}/{i}' not in rejected_claims]
                plan = plan_analysis_repair(plan['base'], requirement, plc_model=model, confirmed_spec=spec,
                                            contract_stage=state['contract_stage'])
            if dropped:
                state['dropped_claims'] = dropped
            if plan['targets']:
                state.pop('draft', None)
                state.pop('draft_id', None)
                state.update(repair=plan, repair_id=uuid4().hex, stage='propose_design_plan')
                atomic_json(self._path(project_id), state)
                return self._repair_request(state)
            result = _parse_analysis_response(json.dumps(plan['base'], ensure_ascii=False),
                model, requirement, spec, contract_stage=state['contract_stage'])
            state.pop('repair', None)
            state.pop('repair_id', None)
            result['analysis_mode'] = 'design'
            result['decision_receipt']['analysis_evidence'] = state['analysis_evidence']
            # Claims the request cannot support stay in the receipt: the window
            # shows them, and the confirmed specification still records what the
            # model proposed but could not ground.
            dropped = state.get('dropped_claims') or []
            for field in ('explicit_constraint_receipt', 'execution_intent_receipt'):
                for row in (result.get(field) or {}).get('rejected') or []:
                    claim = row.get('claim')
                    if not isinstance(claim, dict):
                        details = row.get('details') or []
                        actual = details[0].get('actual') if details and isinstance(details[0], dict) else None
                        claim = actual if isinstance(actual, dict) else {'summary': str(row.get('reason') or '')}
                    entry = {'field': field.replace('_receipt', ''), 'reason': str(row.get('reason') or ''),
                             'claim': copy.deepcopy(claim)}
                    if entry not in dropped:
                        dropped.append(entry)
            state['dropped_claims'] = dropped
            if dropped:
                result['decision_receipt']['dropped_claims'] = copy.deepcopy(dropped)
            draft = build_review_draft(result, project.get('confirmed_spec'))
            ids = [a.get('approach_id') for a in draft['approaches']]
            if None in ids or len(set(ids)) != len(ids) or len(ids) > 3:
                raise ValueError('每个设计方案需要独立的 approach_id，最多提供三个方案。')
            state.update(draft=draft, draft_id=uuid4().hex, stage='confirm_design_plan')
            atomic_json(self._path(project_id), state)
        elif not args and 'repair' in state:
            return self._repair_request(state)
        elif args or 'draft' not in state:
            raise ValueError('提交模型的 analysis 和 analysis_id；已有草稿可以用空参数重新查看。')
        draft = state['draft']
        review = {k: draft[k] for k in ('summary', 'approaches', 'io_table', 'io_bindings', 'parameters',
                  'hardware_requirements', 'hardware_context', 'execution_semantics', 'behavior_constraints',
                  'operation_intents', 'user_notes') if draft.get(k)}
        return {'draft_id': state['draft_id'], 'user_requirement': state['requirement'], 'review': review,
                'issues': validate_spec_draft(draft, project['plc_model']),
                **({'dropped_claims': state['dropped_claims']} if state.get('dropped_claims') else {}),
                'next_actions': [{'tool': 'confirm_design_plan'}] if draft.get('approaches') else []}

    @staticmethod
    def _repair_request(state):
        keys = ('path', 'mode', 'current_value', 'must_preserve', 'unsupported_structure', 'errors')
        targets = [{k: item[k] for k in keys if k in item} for item in state['repair']['targets']]
        return {'status': 'repair_required', 'analysis_id': state['analysis_id'], 'repair_id': state['repair_id'],
                'repair_targets': targets, 'next_actions': [{'tool': 'propose_design_plan',
                    'arguments': {'analysis_id': state['analysis_id'], 'repair_id': state['repair_id']},
                    'instruction': 'Supply repairs [{path,value}] for just these fields. Use exact user evidence. '
                                   'For claim targets return an array. Preserve the rest of the saved analysis.'}]}

    def confirm(self, project_id, draft_id, approach_id, parameter_answers=None):
        state, project = self._pending(project_id)
        if state.get('draft_id') != draft_id:
            raise ValueError('草稿已替换，请重新查看当前计划。')
        draft = state['draft']
        choice = next((a for a in draft['approaches'] if a['approach_id'] == approach_id), None)
        if not choice:
            raise ValueError('所选方案不在当前草稿中。')
        parameters = {row['id']: row for row in draft.get('parameters', []) if row.get('id')}
        for key, value in (parameter_answers or {}).items():
            if key not in parameters or not value.strip():
                raise ValueError('参数答案必须对应当前草稿中的参数 ID，且不能为空。')
            parameters[key].update(value=value.strip(), source='user')
        draft['selected_approach'] = choice
        saved = self.service.set_spec(project_id, draft, expected_hash=state['base_hash'])
        if not saved['valid']:
            atomic_json(self._path(project_id), state)
            errors = saved['issues'].get('errors') or []
            parameter_only = bool(errors) and all(str(item.get('path') or '').startswith('$.parameters') for item in errors)
            next_tool = ({'tool': 'confirm_design_plan', 'arguments': {'draft_id': draft_id, 'approach_id': approach_id}}
                         if parameter_only else {'tool': 'propose_design_plan'})
            return {'confirmed': False, 'issues': saved['issues'], 'next_actions': [next_tool]}
        atomic_json(self._path(project_id), {'stage': 'confirmed'})
        return {'confirmed': True, 'project_id': project_id, 'target_mode': project['target_mode'],
                'approach_id': approach_id, 'spec_hash': saved['hash'],
                'decision_receipt_id': self.service.projects.raw_project(project_id)['confirmed_decision_receipt_id'],
                'next_actions': [{'tool': 'get_generation_context', 'arguments': {'workflow': 'detailed'}}]}

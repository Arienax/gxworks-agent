# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Model-facing comparison of task-relevant instructions before a new design."""
import re

CANDIDATE_TOOLS = frozenset({'create_program_candidate', 'create_st_candidate',
                             'create_fbd_candidate', 'create_sfc_candidate'})

_TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 4000}
_DOCUMENT = {'type': 'string', 'pattern': r'read-[0-9a-f]{32}'}
REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'description': '新设计的选型记录。按实际型号查证每组相关指令，读完分页后填写；引用 delivery.document_id。',
    'properties': {'context_document': _DOCUMENT,
                   'manual_documents': {'type': 'array', 'items': _DOCUMENT, 'maxItems': 32},
                   'limitations': {**_TEXT, 'description': '查询覆盖不完整时，具体说明仍未知的条件和对选型的影响。'},
                   'selected': {**_TEXT, 'description': '最终采用的实现与理由。'},
                   'alternatives': {'type': 'array', 'maxItems': 32, 'items': {'type': 'object',
                       'properties': {'method': _TEXT, 'tradeoff': _TEXT}, 'required': ['method', 'tradeoff'],
                       'additionalProperties': False},
                       'description': '列出其他可行或已排除的方法，逐项比较具体指令、行为和资源取舍，说明未证实条件。'}},
    'required': ['context_document', 'manual_documents', 'selected', 'alternatives']}


def new_candidate(tool, arguments):
    return tool == 'create_program_candidate' or (
        tool in CANDIDATE_TOOLS and arguments.get('operation') == 'generate')


def research_plan(data):
    """Group actual CPU catalogue entries for implementation research."""
    groups = {}
    inputs = data.get('generation_input', {})
    cards = [*(data.get('knowledge') or []), *(inputs.get('knowledge') or [])]
    spec = data.get('confirmed_spec') or inputs.get('confirmed_spec') or {}
    selected = spec.get('selected_approach') or {}
    gaps = []
    if selected and data.get('workflow_mode') != 'direct_generation':
        from knowledge.retriever import discover_capabilities
        # The architecture is settled. Discover implementation instructions
        # for its guide; rejected architectures must not become search inputs.
        guide = ' '.join(str(selected.get(key) or '') for key in ('name', 'description', 'generation_guide'))
        try:
            discovery = discover_capabilities(guide, plc_model=data['plc_model'], target_mode=data['target_mode'])
            for candidate in discovery['candidates']:
                # Explicit mentions in a model-authored implementation guide are research leads.
                # Only the original request can require a particular mnemonic.
                functions = ['selected_approach' if f == 'explicit' else f for f in candidate.get('functions', [])]
                cards.append({**candidate, 'functions': functions})
            gaps = discovery.get('gaps', [])
        except (OSError, LookupError, ValueError) as error:
            gaps = [{'reason': 'discovery_unavailable', 'error_type': type(error).__name__}]
    for card in cards:
        if not isinstance(card, dict) or card.get('kind') != 'instruction' or not card.get('name'):
            continue
        for function in card.get('functions', []):
            names = groups.setdefault(function, [])
            if card['name'] not in names:
                names.append(card['name'])
    comparison = ('沿用用户已选的总体方案，只在这个方案内比较实际指令、数据更新、时序和资源。'
                  if selected and data.get('workflow_mode') != 'direct_generation' else
                  '在写程序前，对各个整体方案使用相同的时基、输入节拍与边界条件，'
                  '估算随工艺规模增长的网络、步数、状态和资源。')
    return {'families': groups, **({'gaps': gaps} if gaps else {}),
            'instructions': '按实际 CPU 和语言检索每组最相关的代表及配套操作；按任务补查列表外的相关指令；一组不适用时查证后说明原因。工程 FB 以当前目录接口为准。'
                '同一查询可列出多个助记符。读完每份资料的全部分页。' + comparison + '内部假设不能当成用户限制。'
                '在满足实际要求的方案中优先选择重复少、资源少且易维护的实现。比较普通实现与高级指令的语义、成本、限制，'
                '将所选方案和取舍填入 implementation_review。'}


def include_manual_facts(plan, context, language):
    """Read the local functional candidates together, preserving fact scopes and gaps."""
    from agent_runtime.manual_tools import _search_plc_manual
    names = list(dict.fromkeys(name for family in plan['families'].values() for name in family))
    if not names:
        return plan
    manual = _search_plc_manual(context, {'query': ' '.join(names), 'language': language,
        'aspects': ['purpose', 'arity', 'order', 'roles', 'devices', 'execution', 'constraints'],
        'top_k': 1})
    return {**plan, 'manual': {'plc_model': manual['plc_model'], 'language': language,
            'coverage': manual['coverage'],
            'knowledge': [unit for row in manual['results'] for unit in row['knowledge']]}}


def missing_families(groups, researched, review):
    text = ' '.join([review['selected'], *(row['method'] + ' ' + row['tradeoff'] for row in review['alternatives'])])
    mentioned = set(re.findall(r'[A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)*', text.upper()))
    considered = researched & mentioned
    missing = [group for group, names in groups.items() if group != 'explicit' and considered.isdisjoint(names)]
    return [*missing, *sorted(set(groups.get('explicit', [])) - considered)]

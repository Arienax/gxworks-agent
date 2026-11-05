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
    'properties': {'assumptions': {'type': 'array', 'maxItems': 32, 'items': {
                       'type': 'object', 'additionalProperties': False,
                       'properties': {'condition': _TEXT, 'source': {'enum': ['user', 'implementation', 'unverified']},
                                      'quote': _TEXT}, 'required': ['condition', 'source']},
                       'description': '容量、节拍等新增实现选择记 implementation；user 附原消息逐字 quote。无法核实出处的转述记 unverified，不写入用户需求，也不阻止候选处理。'},
                   'context_document': _DOCUMENT,
                   'manual_documents': {'type': 'array', 'items': _DOCUMENT, 'maxItems': 32},
                   'reviewed_instructions': {'type': 'array', 'maxItems': 96,
                       'items': {'type': 'string', 'pattern': r'[A-Z][A-Z0-9_.]*', 'maxLength': 32},
                       'description': '各相关功能已读的代表助记符。方案取舍在 alternatives 只写一次。'},
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
                  '若准备按元素或通道复制逻辑，用一两句话比较另一种状态表示：什么保存状态、什么事件推进状态，'
                  '哪条原始要求使其中一种不适用。一个用法的缺点不能排除该指令的其他用法。')
    return {'families': groups, **({'gaps': gaps} if gaps else {}),
            'instructions': '按实际 CPU 和语言检索每组最相关的代表及配套操作；按任务补查列表外的相关指令；一组不适用时查证后说明原因。工程 FB 以当前目录接口为准。'
                '同一查询可列出多个助记符。读完每份资料的全部分页。' + comparison + '内部假设不能当成用户限制。'
                '在满足实际要求的方案中优先选择重复少且易维护的实现。用 reviewed_instructions 记已查指令，'
                '在 implementation_review 中只记录会改变选择的取舍。'}


def direct_choice_input(inputs, research):
    """Use the task manual and its executable examples in place of speculative search hits."""
    if not research.get('executable_fragments'):
        return inputs
    requested = {row.get('target') for row in (inputs.get('knowledge_coverage') or {}).get('targets', [])}
    relevant = requested | {op for ops in research['families'].values() for op in ops}
    knowledge = []
    for card in inputs.get('knowledge', []):
        if isinstance(card, dict):
            target = card.get('target')
            # A fuzzy example or unnamed search suggestion cannot override an explicit target.
            if card.get('kind') == 'example' and target not in requested:
                continue
            if card.get('kind') == 'instruction' and target and target not in relevant:
                continue
        knowledge.append(card)
    return {**inputs, 'knowledge': knowledge}


def _describes_region_update(fact):
    if fact.get('selection_applicability') != 'applicable':
        return False
    dimension = fact['dimension']
    if dimension.startswith('effects.range_') or dimension == 'constraints.shift_lengths':
        return True
    if dimension.endswith('.purpose'):
        operand = fact['value'].get('value')
        return isinstance(operand, dict) and str(operand.get('semantic_role', '')).endswith('_region_length')
    return False


def include_manual_facts(plan, context, language):
    """Read the local functional candidates together, preserving fact scopes and gaps."""
    from agent_runtime.manual_tools import _search_plc_manual
    names = list(dict.fromkeys(name for family in plan['families'].values() for name in family))
    if not names:
        return plan
    collection = {name for group in ('queue', 'shift', 'block_move') for name in plan['families'].get(group, [])}
    aspects = ['purpose', 'arity', 'order', 'roles', 'devices', 'execution', 'constraints']
    if collection:
        aspects.append('effects')
    manual = _search_plc_manual(context, {'query': ' '.join(names), 'language': language, 'aspects': aspects,
        'top_k': 1})
    units = [unit for row in manual['results'] for unit in row['knowledge']]
    group_operations = []
    for unit in units:
        name = unit.get('target')
        if name not in collection or name in group_operations:
            continue
        facts = unit.get('value', {}).get('facts', [])
        if any(_describes_region_update(fact) for fact in facts):
            group_operations.append(name)
    from application.instruction_fragments import executable_fragments
    fragments = executable_fragments(plan, plc_model=manual['plc_model'], target_mode=language)
    return {**plan, **({'executable_fragments': fragments} if fragments else {}),
            **({'group_operations': group_operations} if group_operations else {}),
            'manual': {'plc_model': manual['plc_model'], 'language': language,
            'coverage': manual['coverage'],
            'knowledge': units}}


def missing_families(groups, researched, review):
    text = ' '.join([review['selected'], *(row['method'] + ' ' + row['tradeoff'] for row in review['alternatives'])])
    mentioned = set(re.findall(r'[A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)*', text.upper()))
    considered = researched & (mentioned | set(review.get('reviewed_instructions', ())))
    missing = [group for group, names in groups.items() if group != 'explicit' and considered.isdisjoint(names)]
    return [*missing, *sorted(set(groups.get('explicit', [])) - considered)]


def validate_review_assumptions(review, *, user_requirement=None):
    """Keep unverifiable attributions out of user facts without blocking generation."""
    from plc.instruction_definition import DefinitionError
    rows = review.get('assumptions', [])
    if not isinstance(rows, list) or len(rows) > 32:
        raise DefinitionError('Implementation assumptions must be a bounded array')
    result = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {'condition', 'source', 'quote'}
                or row.get('source') not in {'user', 'implementation', 'unverified'}
                or not isinstance(row.get('condition'), str) or not row['condition'].strip()
                or len(row['condition']) > 4000):
            raise DefinitionError('Invalid implementation assumption')
        quote = row.get('quote')
        if quote is not None and (not isinstance(quote, str) or not quote.strip() or len(quote) > 4000):
            raise DefinitionError('Invalid assumption quotation')
        normalized = dict(row)
        if row['source'] == 'user' and (not quote or not user_requirement or quote not in user_requirement):
            normalized['source'] = 'unverified'
        result.append(normalized)
    return result

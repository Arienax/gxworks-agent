# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.
"""Source-scoped executable range templates; model choices remain model choices."""
from __future__ import annotations

import copy
import re

from plc.instruction_definition import DefinitionError, Expression, ValueType, select_fact_dependencies
from plc.instruction_effects import validate_behavior


def executable_fragments(plan, *, plc_model, target_mode, registry=None):
    """Offer at most two relevant formal range operations for this exact CPU/language."""
    if target_mode != 'ladder':
        return []  # Other languages need their own source-level lowering contract.
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    names = dict.fromkeys(name for names in plan.get('families', {}).values() for name in names)
    result, kinds = [], set()
    for opcode in names:
        form = registry.resolve_form(opcode, cpu=plc_model)
        if form is None or not form.spec.supports_cpu(plc_model) or not form.spec.native_operand_order:
            continue
        for group in form.spec.definition_facts:
            if not group.dimension.startswith('effects.') or group.value.get('behavior') not in {'range_copy', 'range_shift'}:
                continue
            closure = select_fact_dependencies(form.spec.definition_facts, [group.id], opcode=opcode, model=plc_model)
            if closure['gaps'] or not closure['source_verification_complete']:
                continue
            behavior = validate_behavior(group.value)
            kind = (behavior['behavior'], str(sorted(behavior['element_type'].items())))
            if kind in kinds:
                continue
            trigger = ('rising' if form.pulse or form.spec.execution_form in
                       {'pulse', 'single', 'pulse_single', 'single_pulse'} else 'level')
            identity = 'range-' + opcode.lower()
            memory = 'M' if behavior['element_type']['kind'] == 'bool' else 'D'
            samples = {behavior['source']: memory + '100',
                       behavior.get('destination') or behavior.get('region'): memory + '200'}
            def sample(expression, default):
                expression = Expression.from_mapping(expression)
                if expression.op == 'parameter':
                    samples[expression.name] = default
            sample(behavior['count'], 'K16')
            if behavior['behavior'] == 'range_shift':
                sample(behavior['shift'], 'K1')
            example = 'FRAG ' + identity + ' ' + ' '.join(samples.get(p, '<' + p + '>') for p in form.spec.native_operand_order)
            meaning = ('把源区前 shift 个元素送入 region，并按 direction 推进该 count 长度区域。' if
                       behavior['behavior'] == 'range_shift' else '把 source 起的 count 个元素复制到 destination 连续区。')
            # A one-event example also shows when the source is sampled. Applications
            # choose their own event and time base; addresses below are illustrative.
            event_contact = 'P X0' if trigger == 'level' else 'NO X0'
            sampling = ('每个推进事件只采样当时的源值。两次推进之间发生又消失的输入不会留在历史区；'
                        '需要保留这些事件时先锁存或记录其发生时刻。同一使能持续多次扫描会重复推进。'
                        if behavior['behavior'] == 'range_shift' and trigger == 'level' else
                        '每次调用采样当时的源区；两次调用间被覆盖的数据不会保留。')
            result.append({'id': identity, 'plc_model': plc_model, 'target_mode': target_mode,
                'opcode': opcode, 'parameters': list(form.spec.native_operand_order),
                'trigger': trigger, 'behavior': copy.deepcopy(behavior), 'fact_group': group.id,
                'source_verification': 'source_checked', 'effect_description': meaning,
                'example': {'rung': {'c': [event_contact], 'b': [{'i': [], 'o': [example]}]},
                            'when': '示例在 X0 上升沿执行一次。' + sampling,
                            'scope': '示例地址、长度和事件由工程实际条件替换'},
                'syntax': 'FRAG ' + identity + ' ' + ' '.join('<' + p + '>' for p in form.spec.native_operand_order),
                'instructions': '按形参顺序提供地址/常数；梯级输入给出完整使能。触发=' + trigger +
                    '；required_state 必须由程序满足；范围、重叠及容量按 behavior 边界检查。内部容量或节拍选择单独记作 implementation 假设。'})
            kinds.add(kind)
            break
    # Prefer a shift and a copy when both are relevant; otherwise retain
    # distinct bit/word templates within the same operation family.
    preferred, behaviors = [], set()
    for row in result:
        if row['behavior']['behavior'] not in behaviors:
            preferred.append(row)
            behaviors.add(row['behavior']['behavior'])
    return (preferred + [row for row in result if row not in preferred])[:2]


def _instantiate(template, operands, *, plc_model, confirmed_spec):
    from plc.instruction_binding import _bind_operation_intent
    behavior = template['behavior']
    names = template['parameters']
    if len(operands) != len(names):
        raise DefinitionError('Fragment operand count does not match delivered parameters')
    parameter_types = {}
    def collect(raw):
        expression = Expression.from_mapping(raw)
        def walk(expr):
            if expr.op == 'parameter':
                parameter_types[expr.name] = expr.value_type
            for child in expr.args:
                walk(child)
        walk(expression)
    collect(behavior['count'])
    if behavior['behavior'] == 'range_shift':
        collect(behavior['shift'])
    for condition in behavior.get('preconditions', []):
        collect(condition)
    bindings = {}
    for name, token in zip(names, operands):
        typ = parameter_types.get(name, ValueType('opaque'))
        if re.fullmatch(r'(?:SM|SD|[MDR])\d+', token, re.I):
            value = Expression('device', typ, name=token.upper())
        elif re.fullmatch(r'K[+-]?\d+', token, re.I):
            value = Expression('constant', typ, value=int(token[1:]))
        elif re.fullmatch(r'H[0-9A-F]+', token, re.I):
            value = Expression('constant', typ, value=int(token[1:], 16))
        else:
            raise DefinitionError('Fragment requires a plain memory address or integer constant')
        # Validate representation/range before passing values to the shared matcher.
        value = Expression.from_mapping(value.as_mapping())
        bindings[name] = value
    def substitute(raw):
        expr = raw if isinstance(raw, Expression) else Expression.from_mapping(raw)
        if expr.op == 'parameter':
            if expr.name not in bindings:
                raise DefinitionError('Fragment has an unbound parameter')
            return bindings[expr.name]
        return Expression(expr.op, expr.value_type, tuple(substitute(a) for a in expr.args),
                          expr.name, expr.value, expr.overflow)
    target = behavior.get('destination') or behavior.get('region')
    effect = {'kind': behavior['behavior'], 'target': {'device': bindings[target].name,
        'kind': 'bit' if behavior['element_type']['kind'] == 'bool' else 'word'},
        'source': bindings[behavior['source']].as_mapping(), 'count': substitute(behavior['count']).as_mapping()}
    if behavior['behavior'] == 'range_shift':
        effect.update(shift=substitute(behavior['shift']).as_mapping(), direction=behavior['direction'])
    intent = {'id': template['id'], 'opcode': template['opcode'], 'effects': [effect],
        'enable': Expression('constant', ValueType('bool'), value=True).as_mapping(),
        'parameters': {name: value.as_mapping() for name, value in bindings.items()},
        'status': 'candidate', 'provenance': {'source': 'model_candidate'},
        'execution': {'trigger': template['trigger'], 'required_state': behavior.get('required_state', {})}}
    receipt = _bind_operation_intent(intent, target_model=plc_model,
                                    confirmed_spec=confirmed_spec, model_fragment=True)
    if receipt['status'] != 'bound' or receipt['source_verification'] != 'source_checked':
        raise DefinitionError('Fragment binding failed: ' + receipt.get('reason', 'unverified_fact'))
    return receipt


def materialize_instruction_fragments(ladder, research, *, plc_model, target_mode='ladder', confirmed_spec=None):
    """Lower only explicitly referenced, delivered templates through the shared binder."""
    def branches(program):
        for rung in program.get('r', program.get('root', program.get('rungs', []))):
            yield from rung.get('b', rung.get('branches', []))
    if not any(isinstance(output, str) and output.startswith('FRAG ')
               for branch in branches(ladder) for output in branch.get('o', branch.get('outputs', []))):
        return ladder, None
    value = copy.deepcopy(ladder)
    delivered = {row['id']: row for row in research.get('executable_fragments', [])}
    # Re-resolve from the current owner; delivered JSON is never its own authority.
    current = {row['id']: row for row in executable_fragments(research, plc_model=plc_model, target_mode=target_mode)}
    receipts = []
    for branch in branches(value):
        outputs = branch.get('o', branch.get('outputs', []))
        for index, output in enumerate(outputs):
            if not isinstance(output, str) or not output.startswith('FRAG '):
                continue
            parts = output.split()
            if len(parts) < 2 or parts[1] not in delivered or delivered[parts[1]] != current.get(parts[1]):
                raise DefinitionError('Unknown or stale instruction fragment')
            receipt = _instantiate(current[parts[1]], parts[2:], plc_model=plc_model, confirmed_spec=confirmed_spec)
            receipts.append({**receipt, 'binding_mode': 'model_instruction_fragment',
                'fragment_id': parts[1], 'user_fact': False,
                'required_state': copy.deepcopy(current[parts[1]]['behavior'].get('required_state', {})),
                'required_state_verification': 'not_established'})
            outputs[index] = ' '.join([receipt['opcode'], *receipt['operands']])
    return value, ({'stage': 'Core_instruction_fragments', 'receipts': receipts, 'model_calls': 0} if receipts else None)

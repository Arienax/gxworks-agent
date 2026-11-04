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
    ordinary = (preferred + [row for row in result if row not in preferred])[:2]
    families = plan.get('families', {})
    if 'timer' in families and any(name in families for name in ('queue', 'shift')):
        history = next((_history_template(row, plc_model, registry) for row in result
                        if row['behavior']['behavior'] == 'range_shift'
                        and row['behavior']['element_type'] == {'kind': 'bool'}
                        and row['behavior'].get('direction') == 'left'
                        and row['behavior'].get('read_mode', 'snapshot') == 'snapshot'
                        and row['behavior'].get('scope_limits', {}).get('memory') == 'ordinary_M_bit_regions'
                        and not row['behavior'].get('required_state')
                        and row['trigger'] == 'level'), None)
        if history:
            return [history, *ordinary[:1]]
    return ordinary


def _history_example(identity, plc_model):
    from plc.runtime_semantics import timer_runtime_fact
    fact = timer_runtime_fact(plc_model, 'T0')
    rows = []
    timing = {'source': 'external_single_scan_tick',
              'condition': 'tick 必须为工程已验证的共用单扫描脉冲；不推定任何毫秒周期。'}
    if fact and fact.get('reset_on_disable') and fact.get('unit_ms'):
        rows = [
            {'c': ['NO X2'], 'b': [{'o': ['TIMER T0 K32767']}]},
            {'c': ['NO X2', '<> T0 D900'], 'b': [{'o': ['COIL M1']}]},
            {'c': ['NO X2'], 'b': [{'o': ['MOV T0 D900']}]},
            {'c': ['NO X2', 'NO T0'], 'b': [{'o': ['RST T0', 'MOV K0 D900']}]},
            {'c': ['NC X2'], 'b': [{'o': ['RST T0', 'MOV K0 D900']}]},
        ]
        timing = {'source': 'T0_elapsed_count_change', 'timer_fact': fact,
                  'nominal_tick_ms': fact['unit_ms'], 'pulse_width': '一个 PLC 扫描',
                  'condition': '首次投入前须有停用扫描清除状态；扫描间隔须小于时基，计数不能一次跨多个单位。'
                      'T0自由累计，每次当前值变化产生tick；仅到32767或停用时复位，不每tick复位。',
                  'sampling_error': '事件先锁存，下一tick采入；采样等待0至1个tick，加扫描观察误差。'
                      '时间分辨率为unit_ms；不保证任意现场扫描条件下的精确墙钟延迟。'}
    rows += [
        {'c': ['P X0'], 'b': [{'o': [f'FRAG {identity} X2 M1 M10 M100 K8']}]},
        {'c': ['P X1'], 'b': [{'o': [f'FRAG {identity} X2 M1 M11 M200 K8']}]},
        {'c': ['NO X2', 'NO M102'], 'b': [{'o': ['COIL Y0']}]},
        {'c': ['NO X2'], 'b': [{'i': [{'or': [['NO M203'], ['NO M204'], ['NO M205']]}], 'o': ['COIL Y1']}]},
    ]
    return {'program': {'r': rows, 'device_comments': []}, 'timing': timing,
            'observations': '两个通道可同tick采入；若通道A在tick1和tick2各采入一次，Y0在tick3和tick4导通。'
                '通道B在tick1采入时，Y1在tick4至tick6导通；若tick2再采入，窗口重叠并延长至tick7。'
                '任一通道多事件可同时占据不同历史位，无需为每件分配固定槽。',
            'scope': '示例地址、8位容量、索引2及3至5窗口都是演示选择；不是用户事实或现场节拍。'
                '事件必须发生在扫描可观察时刻；同通道同一tick间隔内多事件合并成一个布尔标记。'}


def _history_template(shift, plc_model, registry):
    from plc.runtime_semantics import control_runtime_facts
    control = control_runtime_facts(plc_model).get('control', {})
    reset = registry.resolve_form('ZRST', cpu=plc_model)
    if (not {'SET', 'RST', 'ZRST'}.issubset(control.get('forms', [])) or not control.get('sources')
            or reset is None or not reset.spec.supports_cpu(plc_model)
            or tuple(reset.spec.native_operand_order) != ('D1', 'D2')):
        return None
    boundaries = [group for group in reset.spec.definition_facts
                  if group.dimension.startswith('constraints.')
                  and group.value.get('rule') == 'ZRST_requires_same_device_kind'
                  and group.value.get('kind') == 'ordered_region'
                  and select_fact_dependencies(reset.spec.definition_facts, [group.id], opcode='ZRST',
                                               model=plc_model)['source_verification_complete']]
    if not boundaries:
        return None
    identity = 'history-' + shift['opcode'].lower()
    return {**shift, 'id': identity, 'construction': 'boolean_event_history',
            'native_parameters': shift['parameters'],
            'parameters': ['run_bit', 'tick_bit', 'pending_bit', 'base_bit', 'length'],
            'syntax': f'FRAG {identity} <run_bit> <tick_bit> <pending_bit> <base_bit> K<length>',
            'reset': {'opcode': 'ZRST', 'fact_groups': [group.id for group in boundaries],
                      'control_sources': copy.deepcopy(control['sources'])},
            'effect_description': '原梯级条件表示事件，只在run时锁存pending。每个run且tick扫描将pending采入base[0]，'
                '旧base[k]推进到base[k+1]，随后清pending。停用清pending及申请的全部普通M历史区。',
            'instructions': '必须放在独立单分支单输出事件梯级。run/tick是已有位；pending/base是独占普通M存储且互不重叠。'
                '共享tick须为单扫描脉冲，各通道共用时基但分开pending和历史区。base[k]代表采入后经过k次tick；'
                '若tick间隔Δ，k位对应kΔ的离散历史，事件到采入仍有0至Δ加扫描误差。'
                '长度至少覆盖最大使用索引+1；自行选择的容量、周期必须标为implementation假设。'
                '同通道同拍多事件会合并；要求每事件可区分身份或同拍计数时改用事件记录/时间戳，不适用此布尔构造。',
            'example': _history_example(identity, plc_model)}


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
    histories = {}
    claimed, controls = set(), set()
    rows_key = next((key for key in ('r', 'root', 'rungs') if key in value), 'r')
    for source_index, rung in enumerate(value.get(rows_key, [])):
        for branch in rung.get('b', rung.get('branches', [])):
            for output in branch.get('o', branch.get('outputs', [])):
                if not isinstance(output, str) or not output.startswith('FRAG '):
                    continue
                parts = output.split()
                if len(parts) < 2 or parts[1] not in delivered or delivered[parts[1]] != current.get(parts[1]):
                    raise DefinitionError('Unknown or stale instruction fragment')
                template = current[parts[1]]
                if template.get('construction') != 'boolean_event_history':
                    continue
                if (rows_key == 'rungs' or value.get('mode') == 'partial'
                        or len(rung.get('b', [])) != 1 or len(branch.get('o', [])) != 1):
                    raise DefinitionError('History fragment requires an independent single-output compact rung')
                generated, receipt, owned, inputs = _materialize_history(
                    rung, template, parts[2:], plc_model=plc_model, confirmed_spec=confirmed_spec)
                if claimed & (owned | inputs) or owned & controls:
                    raise DefinitionError('History storage aliases another history or its controls')
                claimed.update(owned)
                controls.update(inputs)
                histories[source_index] = (generated, receipt)
    # All history storage is exclusively owned by its construction. Reads of its
    # delayed bits are intended, but independent writes would corrupt the trace.
    if claimed:
        for source_index, rung in enumerate(value[rows_key]):
            if source_index in histories:
                continue
            for branch in rung.get('b', rung.get('branches', [])):
                for output in branch.get('o', branch.get('outputs', [])):
                    if claimed & _output_writes(output, current, plc_model, confirmed_spec):
                        raise DefinitionError('History storage aliases another program output')
    for branch in branches(value):
        outputs = branch.get('o', branch.get('outputs', []))
        for index, output in enumerate(outputs):
            if not isinstance(output, str) or not output.startswith('FRAG '):
                continue
            parts = output.split()
            if len(parts) < 2 or parts[1] not in delivered or delivered[parts[1]] != current.get(parts[1]):
                raise DefinitionError('Unknown or stale instruction fragment')
            if current[parts[1]].get('construction') == 'boolean_event_history':
                continue
            receipt = _instantiate(current[parts[1]], parts[2:], plc_model=plc_model, confirmed_spec=confirmed_spec)
            receipts.append({**receipt, 'binding_mode': 'model_instruction_fragment',
                'fragment_id': parts[1], 'user_fact': False,
                'required_state': copy.deepcopy(current[parts[1]]['behavior'].get('required_state', {})),
                'required_state_verification': 'not_established'})
            outputs[index] = ' '.join([receipt['opcode'], *receipt['operands']])
    mapping = []
    expanded = []
    for source_index, rung in enumerate(value.get(rows_key, [])):
        if source_index in histories:
            generated, receipt = histories[source_index]
            expanded.extend(generated)
            mapping.extend([source_index] * len(generated))
            receipts.append(receipt)
        else:
            expanded.append(rung)
            mapping.append(source_index)
    value[rows_key] = expanded
    return value, ({'stage': 'Core_instruction_fragments', 'receipts': receipts, 'model_calls': 0,
                    'source_network_indexes': mapping} if receipts else None)


def _materialize_history(rung, template, operands, *, plc_model, confirmed_spec):
    from plc.device_identity import canonical_device
    from plc.device_policy import device_address
    from plc.instruction_binding import offset_device
    from plc.validation import is_bit_device
    if len(operands) != 5 or not re.fullmatch(r'K\d+', operands[-1], re.I):
        raise DefinitionError('History requires run tick pending base Klength')
    run, tick, pending, base = [canonical_device(token) for token in operands[:4]]
    length = int(operands[-1][1:])
    if not 1 <= length <= template['behavior'].get('max_count', 1024):
        raise DefinitionError('History length is outside the formal range')
    if not all(re.fullmatch(r'M\d+', address) for address in (pending, base)):
        raise DefinitionError('History storage requires ordinary M bit regions')
    region = {offset_device(base, i) for i in range(length)}
    for address in region | {pending}:
        device_address(address, plc_model, access='reset')
    for address in (run, tick):
        device_address(address, plc_model)
        if not is_bit_device(address, plc_model):
            raise DefinitionError('History run and tick must be bit devices')
    if run == tick or pending in region or ({run, tick} & (region | {pending})):
        raise DefinitionError('History parameters alias state or control memory')
    behavior = template['behavior']
    count = Expression.from_mapping(behavior['count'])
    shift = Expression.from_mapping(behavior['shift'])
    if count.op != 'parameter' or shift.op != 'parameter':
        raise DefinitionError('History requires parameterized count and shift')
    args = {behavior['source']: pending, behavior['region']: base,
            count.name: 'K' + str(length), shift.name: 'K1'}
    native = {**template, 'parameters': template['native_parameters']}
    receipt = _instantiate(native, [args[p] for p in native['parameters']],
                           plc_model=plc_model, confirmed_spec=confirmed_spec)
    capture = copy.deepcopy(rung)
    capture['b'][0]['i'] = [*capture['b'][0].get('i', []), 'NO ' + run]
    capture['b'][0]['o'] = ['SET ' + pending]
    def row(conditions, output):
        return {'c': conditions, 'b': [{'i': [], 'o': [output]}]}
    generated = [capture,
        row(['NO ' + run, 'NO ' + tick], ' '.join([receipt['opcode'], *receipt['operands']])),
        row(['NO ' + run, 'NO ' + tick], 'RST ' + pending),
        row(['NC ' + run], 'RST ' + pending),
        row(['NC ' + run], 'ZRST ' + base + ' ' + offset_device(base, length - 1))]
    return generated, {**receipt, 'binding_mode': 'boolean_event_history', 'fragment_id': template['id'],
                       'user_fact': False, 'owned_devices': sorted(region | {pending}),
                       'tick_contract': 'one_scan_strobe', 'reset': copy.deepcopy(template['reset'])}, region | {pending}, {run, tick}


def _output_writes(output, fragments, plc_model, confirmed_spec):
    from application.compact_protocol import _output
    from plc.device_identity import canonical_device
    from plc.instruction_binding import instruction_write_footprint, materialize_operation_references
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    if isinstance(output, str) and output.startswith('OP '):
        lowered, _ = materialize_operation_references({'r': [{'b': [{'o': [output]}]}]}, confirmed_spec,
                                                      target_model=plc_model, bind_native=False)
        output = lowered['r'][0]['b'][0]['o'][0]
    if isinstance(output, str) and output.startswith('FRAG '):
        parts = output.split()
        template = fragments.get(parts[1])
        if template is None:
            raise DefinitionError('Unknown instruction fragment')
        bound = _instantiate(template, parts[2:], plc_model=plc_model, confirmed_spec=confirmed_spec)
        output = ' '.join([bound['opcode'], *bound['operands']])
    parsed = _output(output, '$.history_alias') if isinstance(output, str) else output
    if parsed.get('type') != 'APP_INSTR':
        return {canonical_device(parsed['address'])} if parsed.get('address') else set()
    writes, _ = instruction_write_footprint(parsed, plc_model, DEFAULT_INSTRUCTION_REGISTRY)
    return writes

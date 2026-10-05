"""Three finite Core constructions, instantiated through the existing ladder IR.

No process templates are stored here. Confirmed relations determine effects;
CPU source facts, explicit allocation regions and dependencies determine whether
a method can be used. Unknown methods remain visible gaps in a draft.
"""
from __future__ import annotations

import copy
import itertools
import json
import re

from plc.device_identity import canonical_device, decimal_region_address
from plc.device_policy import device_address, stimulus_value
from plc.instruction_definition import DefinitionError, Expression, UnknownInstructionSemantics
from plc.instruction_binding import bind_operation_intent
from plc.runtime_semantics import control_runtime_facts, first_scan_fact
from plc.specification.behavior import (
    METHODS, initialization_values, normalize_behavior_constraints, normalize_construction_plan,
)


def expression_devices(raw):
    expr = raw if isinstance(raw, Expression) else Expression.from_mapping(raw)
    devices = set()
    if expr.op == 'device':
        count = max(1, (expr.value_type.bits or 1) // 16) if expr.value_type.kind != 'bool' else 1
        devices.update(decimal_region_address(canonical_device(expr.name), offset) for offset in range(count))
    return devices.union(
        *(expression_devices(a) for a in expr.args))


def effect_devices(effect):
    target = decimal_region_address(effect['target']['device'], effect['target'].get('offset', 0))
    if effect.get('kind', 'value') != 'value':
        return {target}
    value = Expression.from_mapping(effect['value'])
    count = max(1, (value.value_type.bits or 1) // 16) if value.value_type.kind != 'bool' else 1
    return {decimal_region_address(target, offset) for offset in range(count)}


def _paths(raw, negate=False):
    """Bounded exact DNF for contacts; no language or similarity inference."""
    expr = raw if isinstance(raw, Expression) else Expression.from_mapping(raw)
    if expr.op == 'not':
        return _paths(expr.args[0], not negate)
    if expr.op == 'constant' and expr.value_type.kind == 'bool':
        return [[]] if bool(expr.value) != negate else []
    if expr.op == 'device' and expr.value_type.kind == 'bool':
        return [[('NC ' if negate else 'NO ') + expr.name]]
    if expr.op in {'and', 'or'}:
        conjunction = (expr.op == 'and') != negate
        parts = [_paths(a, negate) for a in expr.args]
        rows = ([list(itertools.chain.from_iterable(row)) for row in itertools.product(*parts)]
                if conjunction else list(itertools.chain.from_iterable(parts)))
        if len(rows) > 128 or any(len(p) > 64 for p in rows):
            raise UnknownInstructionSemantics('condition_expansion_budget')
        return rows
    if expr.op == 'xor':
        a, b = expr.args
        pairs = [(False, True), (True, False)] if not negate else [(False, False), (True, True)]
        return [left+right for x,y in pairs for left in _paths(a,x) for right in _paths(b,y)]
    if expr.op in {'eq', 'ne', 'gt', 'ge', 'lt', 'le'}:
        if any(a.op not in {'device', 'constant'} or a.value_type.kind != 'int' or a.value_type.bits != 16 for a in expr.args):
            raise UnknownInstructionSemantics('comparison_outside_16bit_contact_subset')
        if any(not a.value_type.signed for a in expr.args):
            raise UnknownInstructionSemantics('unsigned_comparison_materialization_unverified')
        op = {'eq': '=', 'ne': '<>', 'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}[expr.op]
        if negate:
            op = {'=': '<>', '<>': '=', '>': '<=', '>=': '<', '<': '>=', '<=': '>'}[op]
        args = [a.name if a.op == 'device' else 'K'+str(a.value) for a in expr.args]
        return [[op+' '+' '.join(args)]]
    raise UnknownInstructionSemantics('condition_not_materializable')


def _condition(raw, *, always_on, negate=False):
    paths = _paths(raw, negate)
    if not paths:
        return ['NC '+always_on]
    if any(not p for p in paths):
        return ['NO '+always_on]
    return paths[0] if len(paths) == 1 else [{'or': paths}]


def _rung(inputs, outputs):
    return {'h': None, 's': [], 'b': [{'i': copy.deepcopy(inputs), 'o': outputs}]}


def _output_access(element, model):
    from plc.ir import analyze_instruction_access, lower_rung_instructions
    instructions = lower_rung_instructions({'branches': [{'inputs': [], 'outputs': [element]}]})
    if len(instructions) != 1:
        raise UnknownInstructionSemantics('output_footprint_unverified')
    instruction = instructions[0]
    return analyze_instruction_access(instruction['op'], instruction['args'], plc_model=model)


class Allocation:
    def __init__(self, model, ranges, occupied=()):
        self.model, self.occupied, self.allocations = model, set(occupied), []
        self.pool = []
        for region in ranges:
            prefix = region['start'][0]
            for index in range(int(region['start'][1:]), int(region['end'][1:])+1):
                address = prefix+str(index)
                device_address(address, model, access='stimulus')
                if address not in self.pool:
                    self.pool.append(address)

    def take(self, owner, kind='M', words=1):
        for address in self.pool:
            extent = [decimal_region_address(address, offset) for offset in range(words)]
            if address.startswith(kind) and all(d in self.pool and d not in self.occupied for d in extent):
                self.occupied.update(extent)
                self.allocations.extend({'device': d, 'owner': owner} for d in extent)
                return address
        raise UnknownInstructionSemantics('internal_resource_conflict_or_exhaustion')


def _write_effect(effect, *, model, requirement_id, snapshot=None):
    if effect.get('kind', 'value') != 'value':
        raise UnknownInstructionSemantics('transition_effect_outside_point_write_subset')
    target = decimal_region_address(effect['target']['device'], effect['target'].get('offset', 0))
    device_address(target, model, access='reset')
    value = Expression.from_mapping(effect['value'])
    if value.value_type.kind == 'bool' and value.op == 'constant':
        return [('SET ' if value.value else 'RST ')+target]
    if snapshot:
        effect = copy.deepcopy(effect)
        effect['value'] = {'op': 'device', 'name': snapshot, 'type': value.value_type.as_mapping()}
        value = Expression.from_mapping(effect['value'])
    intent = {'id': requirement_id, 'opcode': 'MOV' if value.op in {'constant', 'device'} and value.value_type.bits == 16 else '',
              'status': 'confirmed', 'provenance': {'source': 'user_confirmed', 'evidence': ['confirmed behavior relation']},
              'enable': {'op': 'constant', 'type': {'kind': 'bool'}, 'value': True}, 'effects': [effect]}
    receipt = bind_operation_intent(intent, target_model=model)
    if receipt.get('status') == 'bound' and receipt.get('source_verification') == 'source_checked':
        return [receipt['opcode']+' '+' '.join(receipt['operands'])]
    # Shared source-checked MOV subset enables the FX5U method without promoting
    # unrelated registry candidates. Bit copying is compiled separately.
    facts = control_runtime_facts(model)
    if value.value_type.kind == 'int' and value.value_type.bits == 16 and value.op in {'constant', 'device'} and 'MOV' in facts.get('control', {}).get('forms', []):
        operand = value.name if value.op == 'device' else 'K'+str(value.value)
        return ['MOV '+operand+' '+target]
    raise UnknownInstructionSemantics('effect_binding_not_source_checked')


def compile_constructions(spec, *, occupied_devices=()):
    rows = normalize_behavior_constraints(spec.get('behavior_constraints', []))
    plan = normalize_construction_plan((spec.get('selected_approach') or {}).get('construction_plan'))
    model = spec.get('plc_model')
    result = {'stage': 'Core_construction', 'target_model': model, 'instances': [], 'gaps': [],
              'allocations': [], 'model_calls': 0, 'reference_violations': [], 'initialization': {}}
    if plan is None:
        return result
    facts = control_runtime_facts(model)
    by_id = {row['id']: row for row in rows}
    occupied = set(plan['occupied_devices']) | set(occupied_devices)
    occupied.update(row.get('address') for row in spec.get('io_table', []) if row.get('address'))
    for row in rows:
        if row['kind'] == 'initialize':
            # Persistent source history cannot also be a required zero/constant
            # end-of-first-scan target. Reserve every declared initialization cell.
            occupied.update(initialization_values(row))
        for key in ('source', 'accept', 'enable', 'predicate', 'when'):
            if key in row:
                occupied.update(expression_devices(row[key]))
        if row.get('output'):
            occupied.add(row['output'])
        for transition in row.get('transitions', []):
            occupied.update(expression_devices(transition['when']))
            for effect in transition['effects']:
                occupied.update(effect_devices(effect))
                if effect.get('value'):
                    occupied.update(expression_devices(effect['value']))
    try:
        allocator = Allocation(model, plan['internal_ranges'], occupied)
    except ValueError as error:
        result['gaps'] = [{'id': i['id'], 'reason': str(error)} for i in plan['instances']]
        return result
    compiled, event_outputs, target_owners = {}, {}, {}
    for instance in plan['instances']:
        identity, requirement_id = instance['id'], instance['requirement_id']
        row = by_id.get(requirement_id)
        before = copy.deepcopy(allocator)
        try:
            if not row or row['status'] != 'confirmed':
                raise UnknownInstructionSemantics('relation_not_confirmed')
            if METHODS.get(row['kind']) != instance['method']:
                raise UnknownInstructionSemantics('method_does_not_implement_relation')
            if not facts.get('control'):
                raise UnknownInstructionSemantics('missing_cpu_control_facts')
            if any(d not in compiled for d in instance['depends_on']):
                raise UnknownInstructionSemantics('dependency_unavailable_or_reordered')
            body, writes = [], set()
            always = facts['always_on'][0]
            first = first_scan_fact(model, row.get('execution_context', plan['execution_context']))
            boot = first['devices'][0] if first['available'] else None
            if row['kind'] == 'initialize':
                if not first['available']:
                    raise UnknownInstructionSemantics(first['reason'])
                values = initialization_values(row)
                if result['initialization']:
                    raise UnknownInstructionSemantics('multiple_initialization_owners')
                packed = set()
                for prefix in ('M', 'D'):
                    zeros = sorted(int(d[1:]) for d,v in values.items() if d.startswith(prefix) and v == 0)
                    for _, run in itertools.groupby(enumerate(zeros), lambda pair: pair[1]-pair[0]):
                        contiguous = [pair[1] for pair in run]
                        if len(contiguous) >= 2:
                            body.append(_rung(['NO '+boot], ['ZRST '+prefix+str(contiguous[0])+' '+prefix+str(contiguous[-1])]))
                            packed.update(prefix+str(n) for n in contiguous)
                for address, value in values.items():
                    device_address(address, model, access='reset')
                    if address in packed:
                        continue
                    if address.startswith(('M', 'Y')):
                        if value not in {0, 1, False, True}:
                            raise DefinitionError('Bit initialization is not Boolean')
                        output = ('SET ' if value else 'RST ')+address
                    elif address.startswith(('T', 'C')):
                        if value != 0:
                            raise UnknownInstructionSemantics('nonzero_timer_counter_initialization')
                        output = 'RST '+address
                    else:
                        stimulus_value(address, value, model)
                        output = 'MOV K'+str(value)+' '+address
                    body.append(_rung(['NO '+boot], [output]))
                writes.update(values)
                result['initialization'] = {'requirement_id': requirement_id, 'instance_id': identity,
                                            'device': boot, 'values': values, 'source': first['source']}
            elif row['kind'] == 'event':
                if not boot:
                    raise UnknownInstructionSemantics(first['reason'])
                history, armed, signal = (allocator.take(identity) for _ in range(3))
                source = _condition(row['source'], always_on=always)
                opposite = _condition(row['source'], always_on=always, negate=row['edge'] == 'rising')
                accept = _condition(row['accept'], always_on=always)
                # Source is sampled independently of accept, including disabled
                # scans. The output is a scan event and has no P/PLS consumer.
                body.append(_rung(source, ['COIL '+signal]))
                rising = row['edge'] == 'rising'
                event = [('NO ' if rising else 'NC ')+signal, ('NC ' if rising else 'NO ')+history]
                if row['startup_policy'] == 'require_opposite':
                    event += ['NO '+armed, 'NC '+boot]
                    body.append(_rung(['NO '+boot], ['RST '+armed]))
                    body.append(_rung(opposite, ['SET '+armed]))
                else:
                    event = [{'or': [event+['NC '+boot], [('NO ' if rising else 'NC ')+signal, 'NO '+boot]]}]
                body.append(_rung(event+accept, ['COIL '+row['output']]))
                body.append(_rung(['NO '+signal], ['COIL '+history]))
                writes.add(row['output'])
                event_outputs[requirement_id] = row['output']
            elif row['kind'] == 'merge_events':
                if any(e not in event_outputs for e in row['events']):
                    raise UnknownInstructionSemantics('source_events_unavailable')
                body.append(_rung([{'or': [['NO '+event_outputs[e]] for e in row['events']]}], ['COIL '+row['output']]))
                writes.add(row['output'])
                event_outputs[requirement_id] = row['output']
            else:
                if not boot:
                    raise UnknownInstructionSemantics(first['reason'])
                winners = [allocator.take(identity) for _ in row['transitions']]
                snapshots = []
                for transition, winner in zip(row['transitions'], winners):
                    body.append(_rung(['NC '+boot]+_condition(row['enable'], always_on=always)+
                                      _condition(transition['when'], always_on=always), ['COIL '+winner]))
                    effect_snapshots = []
                    for effect in transition['effects']:
                        expression = Expression.from_mapping(effect.get('value'))
                        snapshot = None
                        if expression.op != 'constant':
                            snapshot = allocator.take(identity, 'M' if expression.value_type.kind == 'bool' else 'D',
                                                      max(1, (expression.value_type.bits or 1)//16))
                            if expression.value_type.kind == 'bool':
                                body.append(_rung(_condition(expression, always_on=always), ['COIL '+snapshot]))
                            else:
                                capture = copy.deepcopy(effect)
                                capture['target'] = {'device': snapshot, 'kind': 'word', 'offset': 0}
                                body.append(_rung(['NO '+always], _write_effect(capture, model=model, requirement_id=requirement_id)))
                        effect_snapshots.append(snapshot)
                    snapshots.append(effect_snapshots)
                for index, (transition, winner) in enumerate(zip(row['transitions'], winners)):
                    guard = ['NO '+winner]+['NC '+w for w in winners[:index]]
                    for effect, snapshot in zip(transition['effects'], snapshots[index]):
                        target = decimal_region_address(effect['target']['device'], effect['target'].get('offset', 0))
                        value = Expression.from_mapping(effect['value'])
                        if value.value_type.kind == 'bool' and snapshot:
                            body.append(_rung(guard+['NO '+snapshot], ['SET '+target]))
                            body.append(_rung(guard+['NC '+snapshot], ['RST '+target]))
                        else:
                            body.append(_rung(guard, _write_effect(effect, model=model, requirement_id=requirement_id, snapshot=snapshot)))
                        writes.update(effect_devices(effect))
            if row['kind'] != 'initialize':
                conflicts = writes.intersection(target_owners)
                if conflicts:
                    raise UnknownInstructionSemantics('construction_owner_conflict:'+','.join(sorted(conflicts)))
                target_owners.update({d: identity for d in writes})
            record = {**instance, 'status': 'instantiated', 'requirement': copy.deepcopy(row),
                      'rungs': body, 'writes': sorted(writes), 'sources': facts['control']['sources']}
            compiled[identity] = record
            result['instances'].append(record)
        except (ValueError, KeyError) as error:
            allocator = before
            result['gaps'].append({**instance, 'status': 'unverified', 'reason': str(error)})
    result['allocations'] = allocator.allocations
    return result


def construction_prompt(spec, *, delivery='instantiate'):
    if not (spec.get('selected_approach') or {}).get('construction_plan'):
        return ''
    compiled = compile_constructions(spec)
    visible = {key: value for key, value in compiled.items() if key not in {'initialization', 'reference_violations'}}
    visible['instances'] = [{key: value for key, value in row.items() if key not in {'rungs', 'sources'}}
                            for row in compiled['instances']]
    mechanism = ('可实例化项在r的梯级位置各引用一次 {"construct":"实例ID"}；引用对象替代h/s/b，普通必填字段只用于自由梯级。按depends_on排序，初始化放在所有正常写入前。'
                 'Core展开其内部关系；自由梯级不可写构造的writes或allocations，也不可对event/merge结果再次P/F/PLS取沿。'
                 if delivery == 'instantiate' else
                 '按确认关系与方法自行输出普通完整梯级，不使用construct引用。构造资源分配供实现使用。')
    return '\n# Core construction delivery\n'+mechanism+' 缺少前提的项仍生成草稿并保留未验证范围。\n'+json.dumps(
        visible, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n# End Core construction delivery\n'


def materialize_construction_references(compact, spec, *, delivery='instantiate', output_expander=None):
    compiled = compile_constructions(spec)
    if not (spec.get('selected_approach') or {}).get('construction_plan') or delivery != 'instantiate':
        return copy.deepcopy(compact), compiled
    records = {r['id']: r for r in compiled['instances']}
    unavailable = {r['id'] for r in compiled['gaps']}
    result, seen, locations, free = [], set(), {}, []
    rows = compact.get('r') if isinstance(compact, dict) else None
    if not isinstance(rows, list):
        compiled['reference_violations'].append({'reason': 'construction_requires_compact_rung_positions'})
        return copy.deepcopy(compact), compiled
    init_ids = {r['id'] for r in compiled['instances'] if r['requirement']['kind'] == 'initialize'}
    for index, row in enumerate(rows):
        if isinstance(row, dict) and 'construct' in row:
            identity = row['construct']
            if set(row) != {'construct'} or not isinstance(identity, str) or identity not in records:
                if isinstance(identity, str) and identity in unavailable and set(row) == {'construct'}:
                    compiled['gaps'].append({'id': identity, 'reason': 'reference_method_unavailable', 'position': index})
                else:
                    compiled['reference_violations'].append({'reason': 'unknown_reference', 'position': index})
                continue
            record = records[identity]
            reasons = []
            if identity in seen:
                reasons.append('duplicate_reference')
            if set(record['depends_on']) - seen:
                reasons.append('dependency_reference_order')
            if record['requirement']['kind'] == 'initialize' and index != 0:
                reasons.append('initialization_reference_order')
            if not init_ids.issubset(seen) and identity not in init_ids:
                reasons.append('initialization_dependency_order')
            for reason in reasons:
                compiled['reference_violations'].append({'instance_id': identity, 'reason': reason, 'position': index})
            seen.add(identity)
            locations[identity] = [len(result), len(result)+len(record['rungs'])]
            result.extend(copy.deepcopy(record['rungs']))
        else:
            free.append((len(result), copy.deepcopy(row)))
            result.append(copy.deepcopy(row))
    for identity in records.keys()-seen:
        compiled['reference_violations'].append({'instance_id': identity, 'reason': 'missing_reference'})
    owned = {d for r in compiled['instances'] if r['requirement']['kind'] != 'initialize' for d in r['writes']}
    owned.update(a['device'] for a in compiled['allocations'])
    event_devices = {r['requirement']['output'] for r in compiled['instances'] if r['requirement']['kind'] in {'event', 'merge_events'}}
    from plc.ir import analyze_instruction_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    for index, row in free:
        for branch in row.get('b', []) if isinstance(row, dict) else []:
            for output in branch.get('o', []):
                try:
                    if output_expander is None:
                        raise UnknownInstructionSemantics('free_output_decoder_unavailable')
                    expanded = output_expander(output, 'construction_free_output')
                    _reads, writes = _output_access(expanded, spec['plc_model'])
                    if set(writes) & owned:
                        compiled['reference_violations'].append({'reason': 'free_write_to_owned_resource', 'position': index,
                            'devices': sorted(set(writes) & owned)})
                except ValueError:
                    compiled['gaps'].append({'reason': 'free_write_footprint_unverified', 'position': index})
            text = json.dumps([row.get('h'), row.get('s'), branch.get('i'), branch.get('o')])
            if any(re.search(r'\b(?:P|F|PLS|PLF|LDP|LDF|ANDP|ANDF)\s+'+re.escape(d)+r'\b', text) for d in event_devices):
                compiled['reference_violations'].append({'reason': 'event_reedged', 'position': index})
    compiled['rung_spans'] = locations
    compiled['free_positions'] = [i for i, _ in free]
    return {'r': result}, compiled


def isolate_initialization(ladder, spec, receipt):
    """Keep normal writers from clobbering the confirmed end-of-first-scan state.

COIL writes OFF on false. Its boot path therefore carries the initial bit,
rather than merely adding NC first-scan. Shared predicates are captured once;
each branch predicate is captured at its original execution position.
"""
    init = receipt.get('initialization')
    # Missing references already carry an activation-blocking diagnostic. A
    # partial reference set must still decode into a reviewable draft.
    if not init or init['instance_id'] not in receipt.get('rung_spans', {}):
        return ladder, receipt
    from plc.ir import analyze_instruction_access, lower_rung_instructions
    def contact(kind, address):
        return {'type': kind, 'address': address}
    values, boot, model = init['values'], init['device'], spec['plc_model']
    plan = normalize_construction_plan(spec['selected_approach']['construction_plan'])
    # Pure guard captures can borrow zero initialization cells if they restore
    # them at the end of boot. Persistent construction state was reserved above.
    live = set(plan['occupied_devices'])
    live.update(d for d,v in values.items() if bool(v))
    init_start, init_end = receipt['rung_spans'][init['instance_id']]
    for index, rung in enumerate(ladder['rungs']):
        if init_start <= index < init_end:
            continue
        for instruction in lower_rung_instructions(rung):
            reads, writes = analyze_instruction_access(instruction['op'], instruction['args'], plc_model=model)
            live.update(reads)
            live.update(writes)
            for operand in instruction.get('args', []):
                if re.fullmatch(r'(?:[XYMDTC]|SM|SD)\d+', str(operand)):
                    live.add(canonical_device(operand))
    allocator = Allocation(model, plan['internal_ranges'], live)
    guard_resources = {}
    def guard_resource(label):
        if label not in guard_resources:
            guard_resources[label] = allocator.take('initialization_guard.'+label)
        return guard_resources[label]
    output, changes = [], []
    for index, rung in enumerate(ladder['rungs']):
        if init_start <= index < init_end:
            output.append(copy.deepcopy(rung))
            continue
        footprints = []
        for branch in rung['branches']:
            writes = []
            for element in branch['outputs']:
                _reads, footprint = _output_access(element, model)
                writes.append(set(footprint))
            footprints.append(writes)
        if not any(w & values.keys() for branch in footprints for w in branch):
            output.append(copy.deepcopy(rung))
            continue
        try:
            prefix = []
            if rung.get('header_element'):
                prefix.append(copy.deepcopy(rung['header_element']))
            prefix += copy.deepcopy(rung.get('shared_inputs') or [])
            if prefix:
                cached = guard_resource('prefix')
                output.append({'header_element': None, 'shared_inputs': [], 'branches': [{'inputs': prefix, 'outputs': [{'type': 'COIL', 'address': cached}]}]})
                prefix = [contact('NO', cached)]
            for branch, writes in zip(rung['branches'], footprints):
                condition = guard_resource('branch')
                output.append({'header_element': None, 'shared_inputs': [], 'branches': [{'inputs': prefix+copy.deepcopy(branch['inputs']), 'outputs': [{'type': 'COIL', 'address': condition}]}]})
                for element, footprint in zip(branch['outputs'], writes):
                    inputs = [contact('NO', condition)]
                    protected = footprint & values.keys()
                    if protected:
                        inputs.append(contact('NC', boot))
                        if element.get('type') == 'COIL' and values.get(element.get('address')):
                            inputs = [{'type': 'parallel_block', 'branches': [[contact('NO', boot)], inputs]}]
                        changes.append({'requirement_id': init['requirement_id'], 'original_rung': index,
                                        'devices': sorted(protected), 'reason': 'normal_writer_first_scan_isolation'})
                    output.append({'header_element': None, 'shared_inputs': [], 'branches': [{'inputs': inputs, 'outputs': [copy.deepcopy(element)]}]})
        except UnknownInstructionSemantics as error:
            receipt['gaps'].append({'reason': str(error), 'position': index, 'stage': 'initialization_isolation'})
            return ladder, receipt
    # Scratch captures with range defaults need to end boot with those defaults.
    for allocation in allocator.allocations:
        if allocation['device'] in values:
            output.append({'header_element': None, 'shared_inputs': [], 'branches': [{'inputs': [contact('NO', boot)],
                'outputs': [{'type':'APP_INSTR','opcode':'RST','operands':[allocation['device']]}]}]})
    for index, rung in enumerate(output, 1):
        rung['rung_id'] = index
        for j, branch in enumerate(rung['branches'], 1):
            branch.update(branch_id=j, y_offset_level=j-1)
    receipt['initialization_isolation'] = changes
    receipt['allocations'].extend(allocator.allocations)
    return {**ladder, 'rungs': output}, receipt


def apply_construction_check(report, receipt):
    """Keep incomplete methods distinct from violations of confirmed references."""
    result = copy.deepcopy(report)
    if receipt.get('gaps'):
        result['construction_gaps'] = copy.deepcopy(receipt['gaps'])
        if result.get('status') not in {'violated', 'not_applied'}:
            result['status'] = 'unverified'
    if receipt.get('reference_violations'):
        result['construction_violations'] = [{**copy.deepcopy(v), 'program_version': result.get('program_version')}
                                             for v in receipt['reference_violations']]
        result['status'], result['activation_blocked'] = 'violated', True
    return result

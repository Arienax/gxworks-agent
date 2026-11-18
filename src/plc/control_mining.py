# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Conservative extraction of a snapshot-based word state machine.

This is an opt-in experiment over authoritative ordered PLC IR. It reuses the
ordered accumulator graph, dependency analysis and existing Core constructors.
Imported behavior remains a candidate, even when the source fits the subset.
The only equivalence claimed is scan-end state and outputs under explicit
single-program/input-image/ownership assumptions; scratch memory is excluded.
"""
from __future__ import annotations

import copy
import re

from plc.device_identity import canonical_device
from plc.device_policy import device_address
from plc.instruction_definition import canonical_expression
from plc.ir import ir_to_ladder, validate_plc_ir
from plc.ordered_source import ordered_circuit_rows, project_ordered_graph, source_record_kind
from plc.runtime_semantics import control_runtime_facts, first_scan_fact
from plc.specification.behavior import normalize_behavior_constraints, normalize_construction_plan
from plc.static_analysis import build_device_dependency_graph

_BOOL = {'kind': 'bool'}
_WORD = {'kind': 'int', 'bits': 16, 'signed': True}
_ASSUMPTIONS = ('single_cyclic_program', 'stable_input_image', 'exclusive_controller_ownership')
_OPS = {'LD', 'LDI', 'AND', 'ANI', 'LD=', 'AND=', 'MOV', 'OUT', 'END'}


class _Unknown(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Unknown(reason)


def _expr(op, *, name=None, value=None, args=(), word=False):
    result = {'op': op, 'type': dict(_WORD if word else _BOOL)}
    if name is not None:
        result['name'] = name
    if value is not None:
        result['value'] = value
    if args:
        result['args'] = list(args)
    return result


def _and(terms):
    return terms[0] if len(terms) == 1 else _expr('and', args=terms) if terms else _expr('constant', value=True)


def _literal(raw):
    _require(isinstance(raw, str) and re.fullmatch(r'K\d+', raw), 'nonconstant_or_signed_state_value')
    value = int(raw[1:])
    _require(0 <= value <= 32767, 'state_value_outside_nonnegative_int16')
    return value


def _contacts(node, anchors):
    kind = node['kind']
    if kind == 'reference':
        return _contacts(anchors[node['anchor']], anchors)
    if kind == 'and':
        return [term for child in node['children'] for term in _contacts(child, anchors)]
    if kind == 'contact':
        return [node]
    raise _Unknown('guard_outside_conjunction_subset')


def _term_expression(term, *, state=None, shadow=None):
    op, args = term['op'], term['args']
    if op in {'LD=', 'AND='}:
        _require(len(args) == 2 and args[0] in {state, shadow}, 'comparison_outside_state_register')
        return _expr('eq', args=[_expr('device', name=state, word=True),
                                _expr('constant', value=_literal(args[1]), word=True)])
    _require(op in {'LD', 'LDI', 'AND', 'ANI'} and len(args) == 1, 'guard_outside_level_contact_subset')
    _require(re.fullmatch(r'X[0-7]+', args[0]), 'guard_reads_mutable_or_unknown_device')
    value = _expr('device', name=canonical_device(args[0]))
    return _expr('not', args=[value]) if op in {'LDI', 'ANI'} else value


def _actions(ladder):
    """Keep contact evaluation positions, including frozen graph references."""
    flat = [row for rung in ladder['rungs'] for row in rung['ordered_instructions']]
    _require(0 < len(flat) <= 512, 'source_outside_instruction_budget')
    seen_end = False
    for row in flat:
        kind = source_record_kind(row)
        if kind in {'statement', 'note'}:
            continue
        _require(kind == 'instruction', 'opaque_label_or_unknown_source_record')
        _require(not seen_end, 'source_after_end')
        _require(row.get('op') in _OPS, 'instruction_outside_snapshot_subset:' + str(row.get('op')))
        seen_end = row['op'] == 'END'
    _require(seen_end, 'terminal_end_missing')
    actions = []
    offsets, offset = {}, 0
    for index, rung in enumerate(ladder['rungs']):
        offsets[index] = offset
        offset += len(rung['ordered_instructions'])
    for _, rows, _ in ordered_circuit_rows(ladder['rungs']):
        positions = [offsets[r['_rung_index']] + r['_instruction_index'] for r in rows]
        anchors = {}
        for event in project_ordered_graph({'ordered_instructions': rows}):
            if event['kind'] == 'expression':
                anchors[event['anchor']] = event['expression']
            elif event['kind'] == 'output':
                terms = _contacts(anchors[event['anchor']], anchors)
                actions.append({**event, 'position': positions[event['source_index']],
                                'terms': [{**t, 'position': positions[t['source_index']]} for t in terms]})
            else:
                _require(event['kind'] in {'annotation', 'end'}, 'unresolved_accumulator_flow')
    return actions


def _is_bit(term, address, positive=True):
    return term['args'] == [address] and term['op'] in ({'LD', 'AND'} if positive else {'LDI', 'ANI'})


def _relations(algorithm):
    def row(identity, kind, **fields):
        return {'id': identity, 'kind': kind, 'status': 'candidate',
                'provenance': {'source': 'imported_unverified',
                               'evidence': ['Observed ordered source in the snapshot-state subset']}, **fields}
    state = algorithm['state_device']
    result = [row('init', 'initialize', values={state: algorithm['initial_state']},
                  execution_context=algorithm['execution_context']),
              row('step', 'transition_group', enable=_expr('constant', value=True), transitions=[
                  {'id': t['id'], 'when': t['when'], 'effects': [
                      {'target': {'device': state, 'kind': 'word', 'offset': 0},
                       'value': _expr('constant', value=t['target_state'], word=True)}]}
                  for t in algorithm['transitions']])]
    result.extend(row('output.' + o['device'], 'assertion', when=_expr('constant', value=True),
                      predicate=_expr('eq', args=[_expr('device', name=o['device']), o['when']]))
                  for o in algorithm['outputs'])
    return normalize_behavior_constraints(result)


def _parameters(algorithm):
    devices = {'state': algorithm['state_device'], 'sample': algorithm['snapshot_device']}
    devices.update({'input_' + str(i): d for i, d in enumerate(algorithm['inputs'])})
    devices.update({'output_' + str(i): o['device'] for i, o in enumerate(algorithm['outputs'])})
    return {'devices': devices, 'states': {'state_' + str(i): s for i, s in enumerate(algorithm['states'])}}


def mine_snapshot_state_machine(program, *, target_model=None, execution_context=None, assumptions=None):
    """Recognize only a complete, ordered, single-owner FX3U controller.

    Missing assumptions leave observations unknown. They cannot be inferred
    from a single POU: interrupts, HMI writes and task scheduling are external.
    Exclusive ownership covers state, sample, outputs and allocated scratch.
    Single cyclic execution excludes task or interrupt interleaving. These
    assumptions do not make intermediate writes observable-equivalent.
    No requirement is confirmed here and no source program is modified.
    """
    result = {'schema_version': 1, 'status': 'unknown', 'reasons': [],
              'model_calls': 0, 'native_execution': 'not_run',
              'required_assumptions': list(_ASSUMPTIONS),
              'assumptions': copy.deepcopy(assumptions or {}), 'algorithm': None}
    try:
        validate_plc_ir(program)
        from plc.target_identity import target_members
        source_cpu = program['plc']['cpu']
        model = target_model or source_cpu
        _require(model == source_cpu or model in target_members(source_cpu), 'runtime_cpu_conflicts_with_source')
        _require(model == 'FX3U', 'cpu_outside_experiment_subset')
        result.update(source_cpu=source_cpu, target_model=model)
        ladder = ir_to_ladder(program)
        _require(all('ordered_instructions' in r for r in ladder['rungs']), 'authoritative_ordered_source_required')
        result['dependency_graph'] = build_device_dependency_graph(program['networks'], plc_model=model)
        actions = _actions(ladder)
        _require(actions, 'no_state_actions')
        context = dict(execution_context or {})
        _require(context.get('program_type') == 'scan' and context.get('initial_execution_program') is False,
                 'execution_context_unverified')
        facts, first = control_runtime_facts(model), first_scan_fact(model, context)
        _require(first['available'], 'first_scan_semantics_unverified')
        boot, always = first['devices'][0], facts['always_on'][0]
        captures = [a for a in actions if a['op'] == 'MOV' and len(a['args']) == 2 and
                    all(re.fullmatch(r'D\d+', d) for d in a['args'])]
        _require(len(captures) == 1, 'unique_state_snapshot_missing')
        capture = captures[0]
        state, shadow = map(canonical_device, capture['args'])
        _require(state != shadow, 'snapshot_aliases_state')
        for address in (state, shadow):
            device_address(address, model, access='reset')
        _require(len(capture['terms']) == 1 and _is_bit(capture['terms'][0], always), 'snapshot_not_unconditional')
        initializers = [a for a in actions if a['op'] == 'MOV' and a['args'][-1] == state and
                        len(a['terms']) == 1 and _is_bit(a['terms'][0], boot)]
        _require(len(initializers) == 1, 'unique_first_scan_initializer_missing')
        init = initializers[0]
        initial = _literal(init['args'][0])
        _require(init['position'] < capture['position'], 'initializer_after_snapshot')
        transitions, outputs, states, inputs = [], [], [initial], []
        state_writes = [a for a in actions if a['op'] == 'MOV' and a['args'][-1] == state and a is not init]
        _require(2 <= len(state_writes) <= 32, 'transition_count_outside_subset')
        last_write = max(a['position'] for a in state_writes)
        output_devices = set()
        for action in actions:
            if action is init or action is capture:
                continue
            op, args, terms = action['op'], action['args'], action['terms']
            if op == 'MOV':
                _require(len(args) == 2 and args[1] == state, 'extra_or_snapshot_write')
                _require(action['position'] > capture['position'], 'transition_before_snapshot')
                _require(any(_is_bit(t, boot, False) for t in terms), 'normal_transition_can_overwrite_boot')
                terms = [t for t in terms if not _is_bit(t, boot, False)]
                _require(terms, 'unguarded_transition')
                for t in terms:
                    if t['op'] in {'LD=', 'AND='}:
                        _require(t['args'][0] == shadow, 'transition_reads_live_state')
                        _require(t['position'] > capture['position'], 'snapshot_guard_evaluated_before_capture')
                        value = _literal(t['args'][1])
                        if value not in states:
                            states.append(value)
                expression = _and([_term_expression(t, state=state, shadow=shadow) for t in terms])
                destination = _literal(args[0])
                if destination not in states:
                    states.append(destination)
                transitions.append({'id': 'transition_' + str(len(transitions)), 'when': expression,
                                    'target_state': destination, 'source_order': action['position']})
            else:
                _require(op == 'OUT' and len(args) == 1 and re.fullmatch(r'Y[0-7]+', args[0]), 'effect_outside_state_or_output')
                output_device = canonical_device(args[0])
                _require(output_device not in output_devices, 'multiple_output_writers')
                _require(action['position'] > last_write and all(t['position'] > last_write for t in terms),
                         'output_observes_intermediate_state')
                _require(any(t['op'] in {'LD=', 'AND='} for t in terms), 'output_without_state_decode')
                for t in terms:
                    if t['op'] in {'LD=', 'AND='}:
                        _require(t['args'][0] == state, 'output_reads_snapshot_or_other_state')
                        _require(_literal(t['args'][1]) in states, 'output_state_not_declared_by_transitions')
                expression = _and([_term_expression(t, state=state) for t in terms])
                output_devices.add(output_device)
                outputs.append({'device': output_device, 'when': expression,
                                'source_order': action['position']})
            for t in terms:
                if len(t['args']) == 1 and re.fullmatch(r'X[0-7]+', t['args'][0]):
                    address = canonical_device(t['args'][0])
                    if address not in inputs:
                        inputs.append(address)
        _require(len(states) >= 2 and outputs, 'state_machine_or_outputs_missing')
        missing = [key for key in _ASSUMPTIONS if (assumptions or {}).get(key) is not True]
        _require(not missing, 'missing_scope_assumptions:' + ','.join(missing))
        # Source MOVs overwrite earlier results. Core's first-winner group must
        # therefore use the reverse order, even when most guards are exclusive.
        algorithm = {'kind': 'snapshot_word_state_machine', 'plc_model': model,
                     'state_device': state, 'snapshot_device': shadow, 'states': states,
                     'initial_state': initial, 'inputs': inputs, 'transitions': list(reversed(transitions)),
                     'outputs': outputs, 'execution_context': context,
                     'source_priority': 'last_enabled_write', 'construction_priority': 'first_matching_transition'}
        result.update(status='recognized', algorithm=algorithm, parameters=_parameters(algorithm),
                      behavior_constraints=_relations(algorithm),
                      equivalence={'status': 'conditional_structural_match',
                                   'observed_at': 'end_of_scan', 'observable_devices': [state, *sorted(output_devices)],
                                   'excluded_devices': [shadow], 'native_execution': 'not_run',
                                   'reason': 'stable snapshot guards; constant writes; reversed priority; terminal outputs'})
    except (ValueError, KeyError, IndexError, TypeError, RecursionError) as error:
        result['reasons'].append(str(error))
    return result


def rebind_snapshot_algorithm(mined, *, devices=None, states=None):
    """Rebind explicit device/state parameters; retain candidate provenance."""
    _require(mined.get('status') == 'recognized', 'unrecognized_algorithm')
    result, algorithm = copy.deepcopy(mined), copy.deepcopy(mined['algorithm'])
    parameters = _parameters(algorithm)
    known = set(parameters['devices'].values())
    devices = {canonical_device(k): canonical_device(v) for k, v in (devices or {}).items()}
    _require(set(devices) <= known, 'unknown_device_parameter')
    mapping = {d: devices.get(d, d) for d in known}
    _require(len(set(mapping.values())) == len(mapping), 'device_parameter_alias')
    for old, new in mapping.items():
        _require(old[0] == new[0], 'device_parameter_kind_changed')
        device_address(new, algorithm['plc_model'], access='read' if new.startswith('X') else 'reset')
    states = dict(states or {})
    _require(set(states) <= set(algorithm['states']), 'unknown_state_parameter')
    state_map = {v: states.get(v, v) for v in algorithm['states']}
    _require(all(type(v) is int and 0 <= v <= 32767 for v in state_map.values()), 'invalid_state_parameter')
    _require(len(set(state_map.values())) == len(state_map), 'state_parameter_alias')
    def replace_expression(raw):
        expr = copy.deepcopy(raw)
        if expr['op'] == 'device':
            expr['name'] = mapping[expr['name']]
        elif expr['op'] == 'constant' and expr['type']['kind'] == 'int':
            expr['value'] = state_map[expr['value']]
        if 'args' in expr:
            expr['args'] = [replace_expression(a) for a in expr['args']]
        return expr
    algorithm.update(state_device=mapping[algorithm['state_device']], snapshot_device=mapping[algorithm['snapshot_device']],
                     states=[state_map[s] for s in algorithm['states']], initial_state=state_map[algorithm['initial_state']],
                     inputs=[mapping[d] for d in algorithm['inputs']])
    for transition in algorithm['transitions']:
        transition['when'] = replace_expression(transition['when'])
        transition['target_state'] = state_map[transition['target_state']]
    for output in algorithm['outputs']:
        output['device'], output['when'] = mapping[output['device']], replace_expression(output['when'])
    result.update(algorithm=algorithm, parameters=_parameters(algorithm), behavior_constraints=_relations(algorithm))
    result['dependency_graph_scope'] = 'original_source_before_parameter_rebinding'
    result['equivalence'] = {**result['equivalence'], 'status': 'parameterized_candidate',
                             'observable_devices': [algorithm['state_device'], *[o['device'] for o in algorithm['outputs']]],
                             'excluded_devices': [algorithm['snapshot_device']]}
    return result


def _relation_key(row):
    kind = row['kind']
    if kind == 'initialize':
        return kind, row['values'], row['ranges'], row['execution_context']
    if kind == 'assertion':
        return kind, canonical_expression(row['when']), canonical_expression(row['predicate'])
    if kind == 'transition_group':
        _require(all(e.get('kind', 'value') == 'value' and 'value' in e
                     for t in row['transitions'] for e in t['effects']), 'effect_outside_point_write_subset')
        return kind, canonical_expression(row['enable']), [
            (canonical_expression(t['when']), [(e['target'], canonical_expression(e['value'])) for e in t['effects']])
            for t in row['transitions']]
    return kind, row


def bind_snapshot_construction(mined, confirmed_spec, *, internal_ranges, occupied_devices=()):
    """Match observations to an independently authored/confirmed contract.

    This never changes confirmation status. Extra or mismatched relations stay
    unknown. Construction references and output decoding use the existing Core
    protocol, not a second PLC emitter or an implicit generation-path switch.
    """
    result = {'status': 'unknown', 'reasons': [], 'model_calls': 0}
    try:
        _require(mined.get('status') == 'recognized', 'unrecognized_algorithm')
        algorithm = mined['algorithm']
        _require(confirmed_spec.get('plc_model') == algorithm['plc_model'], 'confirmed_cpu_mismatch')
        rows = normalize_behavior_constraints(confirmed_spec.get('behavior_constraints'))
        expected = _relations(algorithm)
        _require(len(rows) == len(expected), 'uncovered_or_missing_confirmed_behavior')
        matched, remaining = {}, list(rows)
        for observed in expected:
            candidates = [r for r in remaining if r['status'] == 'confirmed' and _relation_key(r) == _relation_key(observed)]
            _require(len(candidates) == 1, 'confirmed_behavior_mismatch:' + observed['id'])
            matched[observed['id']] = candidates[0]['id']
            remaining.remove(candidates[0])
        prior_plan = (confirmed_spec.get('selected_approach') or {}).get('construction_plan')
        prior_occupied = normalize_construction_plan(prior_plan)['occupied_devices'] if prior_plan is not None else []
        plan = normalize_construction_plan({
            'instances': [{'id': 'init', 'requirement_id': matched['init'], 'method': 'first_scan_isolation'},
                          {'id': 'step', 'requirement_id': matched['step'], 'method': 'priority_snapshot', 'depends_on': ['init']}],
            'internal_ranges': internal_ranges, 'occupied_devices': sorted(set(prior_occupied) | set(occupied_devices)),
            'execution_context': algorithm['execution_context']})
        spec = copy.deepcopy(confirmed_spec)
        spec.setdefault('selected_approach', {})['construction_plan'] = plan
        from plc.construction import compile_constructions, _condition
        receipt = compile_constructions(spec)
        _require(not receipt['gaps'], 'construction_unavailable:' + str(receipt['gaps']))
        always = control_runtime_facts(algorithm['plc_model'])['always_on'][0]
        compact = {'r': [{'construct': 'init'}, {'construct': 'step'}] + [
            {'h': None, 's': [], 'b': [{'i': _condition(o['when'], always_on=always), 'o': ['COIL ' + o['device']]}]}
            for o in algorithm['outputs']]}
        result.update(status='bound', confirmed_spec=spec, compact=compact, construction=receipt,
                      matched_requirements=matched, scope='explicit_behavior_relations_only')
    except (ValueError, KeyError, TypeError) as error:
        result['reasons'].append(str(error))
    return result

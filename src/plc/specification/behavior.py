# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Confirmed behavior relations, separate from Core implementation choices."""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping

from plc.device_identity import canonical_device
from plc.instruction_definition import DefinitionError, Expression
from plc.instruction_binding import normalize_operation_intents

KINDS = frozenset({'initialize', 'event', 'merge_events', 'transition_group', 'assertion'})
METHODS = {'initialize': 'first_scan_isolation', 'event': 'source_history',
           'merge_events': 'scan_union', 'transition_group': 'priority_snapshot'}
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}\Z')
_DEVICE = re.compile(r'(?:[XYMDTCS]|SM|SD)\d+\Z', re.I)


def _identity(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DefinitionError('Behavior relation needs an explicit ID')
    return value


def _device(value, *, write=False):
    if not isinstance(value, str) or not _DEVICE.fullmatch(value):
        raise DefinitionError('Behavior device must be explicit')
    result = canonical_device(value)
    if write and result.startswith(('X', 'SM', 'SD')):
        raise DefinitionError('Behavior cannot write an input/system device')
    return result


def _expression(value, *, boolean=False):
    expr = Expression.from_mapping(value)
    if boolean and expr.value_type.kind != 'bool':
        raise DefinitionError('Behavior condition must be Boolean')
    def clean(node):
        if node.op not in {'constant', 'device', 'and', 'or', 'xor', 'not', 'eq', 'ne', 'lt', 'le', 'gt', 'ge',
                           'add', 'sub', 'mul', 'div', 'mod', 'select', 'bit_and', 'bit_or', 'bit_xor'}:
            raise DefinitionError('Behavior expression is outside the explicit device subset')
        return Expression(node.op, node.value_type, tuple(clean(a) for a in node.args),
                          _device(node.name) if node.op == 'device' else node.name,
                          node.value, node.overflow)
    return clean(expr).as_mapping()


def normalize_behavior_constraints(raw, *, candidate=False, evidence_text=None):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 128:
        raise DefinitionError('Behavior constraints must be a bounded array')
    rows, seen = [], set()
    common = {'id', 'kind', 'status', 'provenance', 'label'}
    fields = {
        'initialize': {'values', 'ranges', 'execution_context'},
        'event': {'source', 'accept', 'edge', 'startup_policy', 'output'},
        'merge_events': {'events', 'output'},
        'transition_group': {'enable', 'transitions'},
        'assertion': {'predicate', 'when'},
    }
    for raw_row in raw:
        if not isinstance(raw_row, Mapping) or raw_row.get('kind') not in KINDS:
            raise DefinitionError('Unknown behavior kind')
        kind = raw_row['kind']
        if set(raw_row) - common - fields[kind]:
            raise DefinitionError('Unknown behavior fields')
        identity = _identity(raw_row.get('id'))
        if identity in seen:
            raise DefinitionError('Duplicate behavior ID')
        seen.add(identity)
        provenance = raw_row.get('provenance') or {}
        source = 'model_candidate' if candidate else provenance.get('source', 'model_candidate')
        status = 'candidate' if candidate else raw_row.get('status', 'candidate')
        if source not in {'model_candidate', 'user_confirmed', 'user_authored', 'imported_unverified'} or status not in {'candidate', 'confirmed'}:
            raise DefinitionError('Invalid behavior provenance')
        if status == 'confirmed' and source not in {'user_confirmed', 'user_authored'}:
            raise DefinitionError('A behavior claim cannot confirm itself')
        evidence = provenance.get('evidence', [])
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16 or any(not isinstance(e, str) or not e for e in evidence):
            raise DefinitionError('Behavior needs the corresponding user evidence')
        if evidence_text is not None and any(e not in evidence_text for e in evidence):
            raise DefinitionError('Behavior evidence is not in the user request')
        row = {'id': identity, 'kind': kind, 'status': status, 'label': str(raw_row.get('label', identity))[:240],
               'provenance': {'source': source, 'evidence': copy.deepcopy(evidence)}}
        if kind == 'initialize':
            values = raw_row.get('values', {})
            if not isinstance(values, Mapping) or len(values) > 512 or any(type(v) not in {bool, int} for v in values.values()):
                raise DefinitionError('Initialization needs explicit bounded values')
            row['values'] = {_device(k, write=True): v for k, v in values.items()}
            ranges = raw_row.get('ranges', [])
            if not isinstance(ranges, list) or len(ranges) > 16:
                raise DefinitionError('Initialization ranges exceed the bound')
            row['ranges'] = []
            for interval in ranges:
                if not isinstance(interval, Mapping) or set(interval) != {'start', 'end', 'value'} or type(interval['value']) not in {int, bool}:
                    raise DefinitionError('Initialization range needs a value')
                start, end = _device(interval['start'], write=True), _device(interval['end'], write=True)
                a, b = re.fullmatch(r'([MD])(\d+)', start), re.fullmatch(r'([MD])(\d+)', end)
                if not a or not b or a[1] != b[1] or not 0 <= int(b[2])-int(a[2]) <= 511:
                    raise DefinitionError('Initialization range must be an ascending ordinary M/D region')
                row['ranges'].append({'start': start, 'end': end, 'value': interval['value']})
            context = raw_row.get('execution_context', {})
            if not isinstance(context, Mapping) or set(context) - {'program_type', 'initial_execution_program', 'cpu_version'}:
                raise DefinitionError('Invalid execution context')
            row['execution_context'] = copy.deepcopy(dict(context))
            if not row['values'] and not row['ranges']:
                raise DefinitionError('Initialization is empty')
        elif kind == 'event':
            row.update(source=_expression(raw_row.get('source'), boolean=True), accept=_expression(raw_row.get('accept'), boolean=True),
                       output=_device(raw_row.get('output'), write=True), edge=raw_row.get('edge'), startup_policy=raw_row.get('startup_policy'))
            if row['edge'] not in {'rising', 'falling'} or row['startup_policy'] not in {'require_opposite', 'allow_initial_event'} or not row['output'].startswith('M'):
                raise DefinitionError('Event needs an edge, startup policy and ordinary M result')
        elif kind == 'merge_events':
            events = raw_row.get('events')
            if not isinstance(events, list) or not 1 <= len(events) <= 32 or len(set(events)) != len(events):
                raise DefinitionError('Event merge needs unique event identities')
            row.update(events=[_identity(e) for e in events], output=_device(raw_row.get('output'), write=True))
            if not row['output'].startswith('M'):
                raise DefinitionError('Merged event must be an ordinary M bit')
        elif kind == 'transition_group':
            row['enable'] = _expression(raw_row.get('enable'), boolean=True)
            transitions = raw_row.get('transitions')
            if not isinstance(transitions, list) or not 1 <= len(transitions) <= 32:
                raise DefinitionError('Priority group needs ordered transitions')
            row['transitions'] = []
            transition_ids = set()
            for transition in transitions:
                if not isinstance(transition, Mapping) or set(transition) != {'id', 'when', 'effects'}:
                    raise DefinitionError('Invalid transition fields')
                tid = _identity(transition['id'])
                if tid in transition_ids:
                    raise DefinitionError('Duplicate transition ID')
                transition_ids.add(tid)
                when = _expression(transition['when'], boolean=True)
                intent = normalize_operation_intents([{'id': tid, 'effects': transition['effects'], 'enable': when,
                    'status': status, 'provenance': {'source': source, 'evidence': evidence}}])[0]
                row['transitions'].append({'id': tid, 'when': when, 'effects': intent['effects']})
        else:
            row['predicate'] = _expression(raw_row.get('predicate'), boolean=True)
            row['when'] = _expression(raw_row.get('when'), boolean=True)
        rows.append(row)
    return rows


def confirm_behavior_constraints(raw, ids):
    rows = normalize_behavior_constraints(raw)
    if not isinstance(ids, list) or set(ids) - {r['id'] for r in rows}:
        raise DefinitionError('Unknown behavior confirmation ID')
    for row in rows:
        if row['id'] in ids:
            row['status'] = 'confirmed'
            row['provenance']['source'] = 'user_confirmed'
    return rows


def normalize_construction_plan(raw):
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) - {'instances', 'internal_ranges', 'occupied_devices', 'execution_context'}:
        raise DefinitionError('Invalid construction plan')
    instances = raw.get('instances', [])
    if not isinstance(instances, list) or len(instances) > 128:
        raise DefinitionError('Construction instances exceed the bound')
    result, seen = [], set()
    for instance in instances:
        if not isinstance(instance, Mapping) or set(instance) - {'id', 'requirement_id', 'method', 'depends_on'}:
            raise DefinitionError('Unknown construction instance fields')
        identity = _identity(instance.get('id'))
        if identity in seen or instance.get('method') not in METHODS.values():
            raise DefinitionError('Duplicate instance or unsupported construction method')
        seen.add(identity)
        dependencies = instance.get('depends_on', [])
        if not isinstance(dependencies, list) or len(dependencies) > 128:
            raise DefinitionError('Invalid construction dependencies')
        result.append({'id': identity, 'requirement_id': _identity(instance.get('requirement_id')),
                       'method': instance['method'], 'depends_on': [_identity(d) for d in dependencies]})
    ranges = raw.get('internal_ranges', [])
    if not isinstance(ranges, list) or len(ranges) > 16:
        raise DefinitionError('Internal allocation ranges exceed the bound')
    normalized = []
    for region in ranges:
        if not isinstance(region, Mapping) or set(region) != {'start', 'end'}:
            raise DefinitionError('Internal allocation region needs explicit bounds')
        start, end = _device(region['start'], write=True), _device(region['end'], write=True)
        a, b = re.fullmatch(r'([MD])(\d+)', start), re.fullmatch(r'([MD])(\d+)', end)
        if not a or not b or a[1] != b[1] or not 0 <= int(b[2])-int(a[2]) <= 511:
            raise DefinitionError('Internal allocation needs an ascending ordinary M/D range')
        normalized.append({'start': start, 'end': end})
    context = normalize_execution_context(raw.get('execution_context', {}))
    occupied = raw.get('occupied_devices', [])
    if not isinstance(occupied, list) or len(occupied) > 4096:
        raise DefinitionError('Occupied device set exceeds the bound')
    return {'instances': result, 'internal_ranges': normalized, 'occupied_devices': [_device(d) for d in occupied],
            'execution_context': copy.deepcopy(dict(context))}


def normalize_execution_context(raw):
    """Typed simulation inputs, not permission to run physical PLC hardware.

    Missing fields stay missing and therefore unknown at execution. In
    particular no clock, initial stop bit, PWM ownership or configuration is
    inferred from a model name or from a scan number.
    """
    allowed = {'program_type', 'initial_execution_program', 'cpu_version', 'software_version',
               'pwm', 'hardware', 'program_context', 'simulation'}
    if not isinstance(raw, Mapping) or set(raw) - allowed:
        raise DefinitionError('Invalid construction execution context')
    result = copy.deepcopy(dict(raw))
    # CPU firmware and engineering-software versions are separate evidence axes.
    for key in ('program_type', 'cpu_version', 'software_version', 'program_context'):
        if key in raw and (not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 128):
            raise DefinitionError('Execution context text needs an explicit bounded value')
    if 'initial_execution_program' in raw and type(raw['initial_execution_program']) is not bool:
        raise DefinitionError('Initial execution program must be Boolean')
    if 'hardware' in raw:
        hardware = raw['hardware']
        if not isinstance(hardware, Mapping) or set(hardware) - {'module_type', 'output_type'}:
            raise DefinitionError('Unknown execution hardware fields')
        if any(not isinstance(v, str) or not v or len(v) > 128 for v in hardware.values()):
            raise DefinitionError('Execution hardware values must be bounded names')
    if 'pwm' in raw:
        fields = {'execution_mode', 'source_binding', 'initially_idle', 'pulse_count',
                  'channel', 'output_device', 'configured_for_PWM', 'unused_channel_available',
                  'resource_owner', 'time_unit', 'specified_Y_number_in_use'}
        boolean = {'initially_idle', 'configured_for_PWM', 'unused_channel_available', 'specified_Y_number_in_use'}
        def pwm_config(value, *, binding=False):
            if not isinstance(value, Mapping) or set(value) - fields - (set() if binding else {'bindings'}):
                raise DefinitionError('Unknown PWM execution fields')
            normalized = copy.deepcopy(dict(value))
            for key, item in value.items():
                if key in boolean and type(item) is not bool:
                    raise DefinitionError('PWM configuration flags must be Boolean')
                if key in {'channel', 'pulse_count'} and (type(item) is not int or not 0 <= item <= 2147483647):
                    raise DefinitionError('PWM integer configuration is invalid')
                if key in {'execution_mode', 'source_binding', 'time_unit'} and (
                        not isinstance(item, str) or not item or len(item) > 256):
                    raise DefinitionError('PWM configuration identity is invalid')
                if key == 'resource_owner' and item is not None and (
                        not isinstance(item, str) or not item or len(item) > 256):
                    raise DefinitionError('PWM resource owner must be explicit or null')
                if key == 'output_device':
                    if not isinstance(item, str) or not re.fullmatch(r'Y[0-7]+', item, re.I):
                        raise DefinitionError('PWM output needs an explicit Y device')
                    normalized[key] = canonical_device(item)
            if 'bindings' in value:
                bindings = value['bindings']
                if not isinstance(bindings, Mapping) or len(bindings) > 128:
                    raise DefinitionError('PWM instruction bindings exceed the bound')
                normalized['bindings'] = {}
                for owner, configuration in bindings.items():
                    if not isinstance(owner, str) or not owner or len(owner) > 256:
                        raise DefinitionError('PWM binding needs an explicit instruction owner')
                    normalized['bindings'][owner] = pwm_config(configuration, binding=True)
            return normalized
        result['pwm'] = pwm_config(raw['pwm'])
    if 'simulation' in raw:
        simulation = raw['simulation']
        if not isinstance(simulation, Mapping) or set(simulation) - {'clock', 'initial_memory'}:
            raise DefinitionError('Unknown simulation assumptions')
        result['simulation'] = {}
        if 'clock' in simulation:
            clock = simulation['clock']
            if (not isinstance(clock, Mapping) or set(clock) != {'kind', 'timestamps_ms'}
                    or clock['kind'] != 'explicit_timestamps'):
                raise DefinitionError('Simulation clock requires explicit timestamps')
            times = clock['timestamps_ms']
            if (not isinstance(times, list) or not 1 <= len(times) <= 8
                    or any(type(t) not in {int, float} or not 0 <= t <= 10**15 or not math.isfinite(t) for t in times)
                    or any(a > b for a, b in zip(times, times[1:]))):
                raise DefinitionError('Simulation timestamps must be finite, nonnegative and monotonic')
            result['simulation']['clock'] = copy.deepcopy(dict(clock))
        if 'initial_memory' in simulation:
            memory = simulation['initial_memory']
            if not isinstance(memory, Mapping) or len(memory) > 512:
                raise DefinitionError('Simulation initial memory exceeds the bound')
            normalized = {}
            for device, value in memory.items():
                device = _device(device)
                if type(value) not in {bool, int} and value is not None:
                    raise DefinitionError('Simulation memory must contain explicit bits, words or unknowns')
                prefix = re.match(r'[A-Z]+', device)[0]
                if value is not None:
                    if prefix in {'D', 'SD'}:
                        if type(value) is not int or not -32768 <= value <= 65535:
                            raise DefinitionError('Simulation word must fit one explicit 16-bit word')
                    elif type(value) not in {bool, int} or value not in {0, 1}:
                        raise DefinitionError('Simulation bit must be Boolean or zero/one')
                if device in normalized:
                    raise DefinitionError('Duplicate simulation memory device alias')
                normalized[device] = value
            result['simulation']['initial_memory'] = normalized
    return result


def initialization_values(row):
    result = {}
    for interval in row.get('ranges', []):
        prefix = re.match(r'[MD]', interval['start'])[0]
        for index in range(int(interval['start'][1:]), int(interval['end'][1:])+1):
            address = prefix+str(index)
            if address in result:
                raise DefinitionError('Initialization ranges overlap')
            result[address] = interval['value']
    # Point values may intentionally override a range default, e.g. lead pump B.
    result.update(row.get('values', {}))
    return result

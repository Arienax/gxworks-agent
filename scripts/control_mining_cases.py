# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Small authored controllers for the offline control-mining experiment.

The instruction sources do not use Core construction or mining code. Projects
are created in memory from the shipped, captured blank Ladder project; callers
choose whether to save them. The separate oracle describes the intended scan
behavior without interpreting the source instructions.
"""
from __future__ import annotations

import copy


def synthetic_cases():
    """Return four synthetic sources with explicit address and state variants."""
    return [
        {
            'case_id': 'cycle3',
            'name': 'Three-state sequential controller',
            'plc_model': 'FX3U',
            'scenario': 'cycle',
            'state_device': 'D0',
            'snapshot_device': 'D1',
            'states': [0, 1, 2],
            'signals': ['X0', 'X1', 'X2'],
            'stop': 'X3',
            'outputs': {'Y0': 1, 'Y1': 2},
        },
        {
            'case_id': 'cycle3_renamed',
            'name': 'Renamed sequential controller with nonzero initial state',
            'plc_model': 'FX3U',
            'scenario': 'cycle',
            'state_device': 'D20',
            'snapshot_device': 'D21',
            'states': [10, 20, 30],
            'signals': ['X10', 'X11', 'X12'],
            'stop': 'X13',
            'outputs': {'Y10': 20, 'Y11': 30},
        },
        {
            'case_id': 'cycle4',
            'name': 'Four-state sequential controller with sparse state values',
            'plc_model': 'FX3U',
            'scenario': 'cycle',
            'state_device': 'D40',
            'snapshot_device': 'D41',
            'states': [0, 7, 11, 20],
            'signals': ['X20', 'X21', 'X22', 'X23'],
            'stop': 'X24',
            'outputs': {'Y20': 7, 'Y21': 11, 'Y22': 20},
        },
        {
            'case_id': 'priority_branch',
            'name': 'Two requests with explicit higher-priority second request',
            'plc_model': 'FX3U',
            'scenario': 'priority_branch',
            'state_device': 'D60',
            'snapshot_device': 'D61',
            'states': [0, 1, 2],
            'signals': ['X0', 'X1', 'X2'],
            'stop': 'X3',
            'outputs': {'Y0': 1, 'Y1': 2},
        },
    ]


def ordered_controller(case, *, snapshot=True):
    """Author ordinary GX instructions independently of the construction path.

    Normal writes are isolated from first-scan initialization. A source can be
    deliberately made scan-cascading with ``snapshot=False`` for a refusal case.
    The stop writer occurs last, and therefore has highest priority.
    """
    state = case['state_device']
    sampled = case['snapshot_device'] if snapshot else state
    states, signals = case['states'], case['signals']
    rows = []

    def emit(op, *args):
        rows.append({'op': op, 'args': list(args)})

    emit('LD', 'M8002')
    emit('MOV', 'K' + str(states[0]), state)
    if snapshot:
        emit('LD', 'M8000')
        emit('MOV', state, sampled)
    if case['scenario'] == 'cycle':
        transitions = [
            (source, states[(index + 1) % len(states)], signals[index])
            for index, source in enumerate(states)
        ]
    elif case['scenario'] == 'priority_branch':
        idle, low, high = states
        transitions = [
            (idle, low, signals[0]), (idle, high, signals[1]),
            (low, idle, signals[2]), (high, idle, signals[2]),
        ]
    else:
        raise ValueError('Unknown synthetic controller scenario')
    for source, destination, signal in transitions:
        emit('LD=', sampled, 'K' + str(source))
        emit('AND', signal)
        emit('ANI', 'M8002')
        emit('MOV', 'K' + str(destination), state)
    emit('LD', case['stop'])
    emit('ANI', 'M8002')
    emit('MOV', 'K' + str(states[0]), state)
    for output, active_state in case['outputs'].items():
        emit('LD=', state, 'K' + str(active_state))
        emit('OUT', output)
    emit('END')
    return rows


def oracle_step(case, current_state, inputs, *, first_scan=False):
    """Expected state and outputs after one scan of the stated control task.

    At most one normal transition is permitted in a scan. Stop takes priority
    over all requests. A high-priority branch wins simultaneous branch requests.
    Unknown state values are retained unless initialization or stop applies.
    """
    states, signals = case['states'], case['signals']
    next_state = current_state
    if first_scan or bool(inputs.get(case['stop'], False)):
        next_state = states[0]
    elif case['scenario'] == 'cycle':
        if current_state in states:
            index = states.index(current_state)
            if inputs.get(signals[index], False):
                next_state = states[(index + 1) % len(states)]
    elif case['scenario'] == 'priority_branch':
        idle, low, high = states
        if current_state == idle:
            if inputs.get(signals[1], False):
                next_state = high
            elif inputs.get(signals[0], False):
                next_state = low
        elif current_state in (low, high) and inputs.get(signals[2], False):
            next_state = idle
    else:
        raise ValueError('Unknown synthetic controller scenario')
    return {
        case['state_device']: next_state,
        **{address: int(next_state == value) for address, value in case['outputs'].items()},
    }


def synthetic_confirmed_spec(case):
    """Author a synthetic task contract independently of the mined program.

    ``user_authored`` is the existing schema's authored-requirement category;
    its evidence explicitly names this synthetic experiment, not a user chat.
    """
    from plc.specification.confirmed import canonicalize_confirmed_spec

    boolean = {'kind': 'bool'}
    integer = {'kind': 'int', 'bits': 16, 'signed': True}

    def device(name, kind=boolean):
        return {'op': 'device', 'type': dict(kind), 'name': name}

    def constant(value, kind=boolean):
        return {'op': 'constant', 'type': dict(kind), 'value': value}

    def operation(op, *args):
        return {'op': op, 'type': dict(boolean), 'args': list(args)}

    def relation(identity, kind, **fields):
        return {
            'id': identity, 'kind': kind, 'status': 'confirmed',
            'provenance': {'source': 'user_authored', 'evidence': [
                'Authored synthetic control-mining task: ' + case['case_id']]},
            **fields,
        }

    state, states, signals = case['state_device'], case['states'], case['signals']

    def transition(identity, condition, destination):
        return {'id': identity, 'when': condition, 'effects': [{
            'target': {'device': state, 'kind': 'word', 'offset': 0},
            'value': constant(destination, integer),
        }]}

    def state_and_signal(value, signal):
        return operation('and', operation('eq', device(state, integer), constant(value, integer)),
                         device(signal))

    transitions = [transition('stop', device(case['stop']), states[0])]
    if case['scenario'] == 'cycle':
        transitions.extend(
            transition('advance_' + str(index), state_and_signal(states[index], signals[index]),
                       states[(index + 1) % len(states)])
            for index in reversed(range(len(states)))
        )
    elif case['scenario'] == 'priority_branch':
        idle, low, high = states
        transitions.extend([
            transition('return_high', state_and_signal(high, signals[2]), idle),
            transition('return_low', state_and_signal(low, signals[2]), idle),
            transition('select_high', state_and_signal(idle, signals[1]), high),
            transition('select_low', state_and_signal(idle, signals[0]), low),
        ])
    else:
        raise ValueError('Unknown synthetic controller scenario')
    context = {'program_type': 'scan', 'initial_execution_program': False}
    relations = [
        relation('init', 'initialize', values={state: states[0]}, execution_context=context),
        relation('step', 'transition_group', enable=constant(True), transitions=transitions),
    ]
    relations.extend(
        relation('output.' + output, 'assertion', when=constant(True),
                 predicate=operation('eq', device(output),
                                     operation('eq', device(state, integer), constant(value, integer))))
        for output, value in case['outputs'].items()
    )
    io = [{'kind': 'X', 'address': address, 'label': 'Request ' + str(index)}
          for index, address in enumerate(signals)]
    io.append({'kind': 'X', 'address': case['stop'], 'label': 'Stop'})
    io.extend({'kind': 'Y', 'address': address, 'label': 'Active state ' + str(value)}
              for address, value in case['outputs'].items())
    return canonicalize_confirmed_spec({
        'schema_version': 4, 'plc_model': case['plc_model'], 'summary': case['name'],
        'io_table': io, 'parameters': [], 'behavior_constraints': relations,
        'io_bindings': [{'binding_id': 'input.' + row['address'], **row,
                         'source': 'user_request', 'active_level': 1, 'inactive_level': 0}
                        for row in io if row['kind'] == 'X'],
        'selected_approach': {
            'approach_id': 'synthetic_control', 'name': case['name'],
            'description': ('Initialize on first scan; at most one state transition per scan. '
                            'Stop has highest priority. Decode outputs after the state update.'),
            'generation_contract': {'required_structures': ['register_state_machine']},
            'construction_plan': {
                'instances': [
                    {'id': 'init', 'requirement_id': 'init', 'method': 'first_scan_isolation'},
                    {'id': 'step', 'requirement_id': 'step', 'method': 'priority_snapshot',
                     'depends_on': ['init']},
                ],
                'internal_ranges': [{'start': 'M200', 'end': 'M239'}],
                'occupied_devices': [], 'execution_context': context,
            },
        },
        'execution_semantics': [], 'operation_intents': [],
    })


def synthesize_gxw(case, *, instructions=None):
    """Insert authored rows into the captured blank FX3U Ladder source."""
    from gxw.offline_project import edit_ladder_project, read_offline_project, read_ordered_program
    from gxw.project_templates import blank_ladder_baseline

    rows = copy.deepcopy(instructions if instructions is not None else ordered_controller(case))
    if not rows or rows[-1] != {'op': 'END', 'args': []}:
        raise ValueError('Synthetic source requires one terminal END')
    if any(row.get('op') == 'END' for row in rows[:-1]):
        raise ValueError('Synthetic source cannot contain an early END')
    original = blank_ladder_baseline(case['plc_model'])
    ordered = read_ordered_program(read_offline_project(original))
    end = ordered['source_records'][0]
    result = edit_ladder_project(original, ordered['selected']['source_name'], [{
        'operation': 'insert_before',
        'record_index': end['record_index'],
        'expected': {'op': 'END', 'args': []},
        'instructions': rows[:-1],
    }])
    return result.data


def decode_case(case, *, instructions=None):
    """Exercise real GXW serialization and decoding before returning Core IR."""
    from gxw.offline_project import read_offline_project, read_ordered_program

    raw = synthesize_gxw(case, instructions=instructions)
    ordered = read_ordered_program(read_offline_project(raw))
    return {'raw': raw, 'ordered': ordered, 'program_ir': ordered['program_ir']}

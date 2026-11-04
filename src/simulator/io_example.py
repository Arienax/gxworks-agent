# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Replay one optional I/O example on the candidate, without a simulator connection."""
import time

def _signals(pattern):
    return {'type': 'object', 'maxProperties': 16, 'propertyNames': {'type': 'string', 'pattern': pattern},
            'additionalProperties': {'type': ['integer', 'boolean']}}


IO_EXAMPLE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['scan_ms', 'steps'],
    'description': 'Optional short I/O example from the request. Ordinary written memory and external X inputs initially zero. Specify the scan interval; sample outputs inside their windows. Inputs persist until changed.',
    'properties': {'scan_ms': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                   'steps': {'type': 'array', 'minItems': 2, 'maxItems': 16, 'items': {
                       'type': 'object', 'additionalProperties': False, 'required': ['at_ms'],
                       'properties': {'at_ms': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
                                      'set': _signals(r'[XD][0-9A-F]+'), 'expect': _signals(r'[YD][0-9A-F]+')}}}}}


def _related_networks(machine, output):
    required, indexes = {output}, set()
    for _ in range(len(machine.instructions)):
        before = len(required)
        for i, instructions in reversed(list(enumerate(machine.instructions))):
            if any(required.intersection(writes) for _, (_, writes) in instructions):
                indexes.add(i)
                for _, (reads, _) in instructions:
                    required.update(reads)
        if len(required) == before:
            break
    return sorted(indexes)


def check_io_example(ladder, plc_model, example):
    """A definite mismatch applies only to this explicit finite initial state and scan."""
    from plc.validation import device_address_status
    from simulator.bounded import ScanMachine

    def unknown(reason):
        return {'status': 'unverified', 'reason': reason}

    scan_ms, steps = example['scan_ms'], example['steps']
    times = [step['at_ms'] for step in steps]
    if times[0] != 0 or times != sorted(set(times)) or any(t % scan_ms for t in times):
        return unknown('expected_increasing_scan_aligned_times_starting_at_zero')
    if not any(step.get('expect') for step in steps):
        return unknown('no_observable_expectation')
    if times[-1] // scan_ms > 1200:
        return unknown('local_scan_budget')
    for step in steps:
        for key in ('set', 'expect'):
            for address, value in step.get(key, {}).items():
                if device_address_status(address, plc_model) != 'valid' or (
                        address[0] in 'XY' and value not in (0, 1)) or not -32768 <= value <= 65535:
                    return unknown('invalid_example_operand:' + address)
    machine = ScanMachine(ladder, plc_model=plc_model, initial={}, execution_context={'program_type': 'scan'})
    accesses = machine.execution_context['program_accesses']
    written = {addr for record in accesses for addr in record['writes']}
    inputs = {addr: 0 for record in accesses for addr in record['reads'] if addr.startswith('X')}
    inputs.update({addr: 0 for step in steps for addr in step.get('set', {})})
    if written.intersection(inputs):
        return unknown('input_also_written_by_program')
    # Only program-written storage and explicit test inputs start at zero.
    # CPU clocks and peripherals must come from known semantics or stay unknown.
    machine.memory.update({addr: 0 for addr in written})
    machine.memory.update({addr + '.contact': False for addr in written if addr.startswith(('T', 'C'))})
    machine.previous.update({addr: False for addr in written | inputs.keys()})
    machine.previous_enables.update({f'{i}.{j}': False for i, row in enumerate(machine.instructions)
                                     for j in range(len(row))})
    events = {s['at_ms']: s for s in steps}
    deadline = time.monotonic() + 2
    for t in range(0, times[-1] + 1, scan_ms):
        if time.monotonic() > deadline:
            return unknown('local_scan_budget')
        step = events.get(t, {})
        inputs.update(step.get('set', {}))
        machine.scan({'timestamp_ms': t, 'inputs': inputs}, first_scan=t == 0)
        if machine.gaps:
            return unknown(machine.gaps[0]['reason'])
        for address, expected in step.get('expect', {}).items():
            actual = machine.memory.get(address)
            if actual is None:
                return unknown('unknown_output:' + address)
            same = actual & 0xffff == expected & 0xffff if address.startswith('D') else actual == expected
            if not same:
                return {'status': 'mismatch', 'at_ms': t, 'address': address, 'expected': expected,
                        'actual': actual, 'network_indexes': _related_networks(machine, address)}
    return {'status': 'passed_in_example', 'scans': times[-1] // scan_ms + 1}

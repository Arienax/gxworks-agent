# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Replay one optional I/O example on the candidate, without a simulator connection."""
import time
import json
import os
from pathlib import Path

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

    from jsonschema import Draft202012Validator
    if not Draft202012Validator(IO_EXAMPLE_SCHEMA).is_valid(example):
        return unknown('invalid_io_example_shape')
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
                        'actual': actual, 'network_indexes': _related_networks(machine, address),
                        'scan_ms': scan_ms,
                        'input_sequence': [{'at_ms': s['at_ms'], 'set': dict(s['set'])}
                                           for s in steps if s['at_ms'] <= t and s.get('set')]}
    return {'status': 'passed_in_example', 'scans': times[-1] // scan_ms + 1}


def load_acceptance_cases(project_id, plc_model):
    """Load operator-owned expectations; generation arguments cannot select this file.

    GXWORKS_ACCEPTANCE_CASES_FILE names a UTF-8 JSON document containing
    schema_version=1, project_id, plc_model and cases. Each case has case_id
    plus the existing scan_ms/steps I/O example shape. No text is interpreted.
    """
    filename = os.environ.get('GXWORKS_ACCEPTANCE_CASES_FILE', '').strip()
    if not filename:
        return None, 'no_independent_acceptance_cases'
    try:
        path = Path(filename).expanduser()
        if path.stat().st_size > 262144:
            return None, 'acceptance_file_too_large'
        document = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None, 'acceptance_file_unreadable'
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        return None, 'invalid_acceptance_document'
    if not project_id or document.get('project_id') != project_id:
        return None, 'acceptance_project_mismatch'
    if document.get('plc_model') != plc_model:
        return None, 'acceptance_model_mismatch'
    cases = document.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 16:
        return None, 'invalid_acceptance_cases'
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('case_id'), str)
                or not 1 <= len(case['case_id']) <= 128 or case['case_id'] in ids
                or set(case) - {'case_id', 'scan_ms', 'steps'}):
            return None, 'invalid_acceptance_case'
        ids.add(case['case_id'])
    return cases, None


def check_independent_acceptance(ladder, plc_model, *, project_id, confirmed_spec=None):
    """Replay the independent operator file, retaining finite-scope limitations."""
    cases, reason = load_acceptance_cases(project_id, plc_model)
    result = {'source': 'operator_acceptance_file', 'status': 'unverified',
              'scope': 'finite explicit initial state and input sequences; not native or requirement-wide verification',
              'cases': []}
    if reason:
        if reason == 'no_independent_acceptance_cases' and confirmed_spec:
            from simulator.bounded import ScanMachine, check_bounded_traces, finite_behavior_traces
            import copy
            spec = copy.deepcopy(confirmed_spec)
            spec['behavior_constraints'] = [row for row in spec.get('behavior_constraints', [])
                if row.get('status') == 'confirmed' and
                row.get('provenance', {}).get('source') in {'user_authored', 'user_confirmed'}]
            if spec['behavior_constraints']:
                spec['plc_model'] = plc_model
                checked = check_bounded_traces(ladder, spec,
                    finite_behavior_traces(spec, max_traces=64), max_traces=64)
                result.update(source='confirmed_specification', confirmed_behavior_check=checked)
                witnesses = [w for w in checked.get('violations', []) if 'expected' in w and 'actual' in w]
                if (witnesses and not checked.get('unsupported_or_missing')
                        and not checked.get('checker_exceptions')):
                    witness = witnesses[0]
                    machine = ScanMachine(ladder, plc_model=plc_model, initial={}, execution_context={'program_type': 'scan'})
                    addresses = {w['device'] for w in witness.get('writes', []) if w.get('device')}
                    from plc.construction import expression_devices
                    rule = next((r for r in spec['behavior_constraints'] if r['id'] == witness.get('requirement_id')), {})
                    if rule.get('predicate'):
                        addresses.update(expression_devices(rule['predicate']))
                    result.update(status='mismatch', case_id=witness.get('requirement_id'),
                                  expected=witness.get('expected'), actual=witness.get('actual'),
                                  scan=witness.get('scan'), witness=witness,
                                  network_indexes=sorted({i for d in addresses for i in _related_networks(machine, d)}))
                    if witness.get('device'):
                        result['address'] = witness['device']
                    frames = witness.get('input_sequence', [])
                    if frames and 'timestamp_ms' in frames[-1]:
                        result['at_ms'] = frames[-1]['timestamp_ms']
                else:
                    result['reason'] = ('confirmed_behavior_missing_execution_semantics' if checked.get('unsupported_or_missing')
                                        else 'confirmed_behavior_finite_scope_only')
                return result
        result['reason'] = reason
        return result
    for case in cases:
        example = {k: v for k, v in case.items() if k != 'case_id'}
        checked = {'case_id': case['case_id'], **check_io_example(ladder, plc_model, example)}
        result['cases'].append(checked)
        if checked['status'] == 'mismatch':
            result.update(checked)
            return result
    missing = [c for c in result['cases'] if c['status'] == 'unverified']
    if missing:
        result['reason'] = missing[0]['reason']
    else:
        result['status'] = 'passed_in_cases'
    return result

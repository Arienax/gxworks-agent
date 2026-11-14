# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Replay one optional I/O example on the candidate, without a simulator connection."""
import time
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator

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

_OPERATOR_EXAMPLE_SCHEMA = {**IO_EXAMPLE_SCHEMA, 'properties': {
    **IO_EXAMPLE_SCHEMA['properties'], 'steps': {**IO_EXAMPLE_SCHEMA['properties']['steps'], 'maxItems': 128}}}


def _example_reason(example, plc_model, *, operator=False):
    from plc.validation import device_address_status

    schema = _OPERATOR_EXAMPLE_SCHEMA if operator else IO_EXAMPLE_SCHEMA
    if not Draft202012Validator(schema).is_valid(example):
        return 'invalid_io_example_shape'
    steps, scan_ms = example['steps'], example['scan_ms']
    times = [step['at_ms'] for step in steps]
    if times[0] != 0 or times != sorted(set(times)) or any(t % scan_ms for t in times):
        return 'expected_increasing_scan_aligned_times_starting_at_zero'
    if not any(step.get('expect') for step in steps):
        return 'no_observable_expectation'
    for step in steps:
        for key in ('set', 'expect'):
            for address, value in step.get(key, {}).items():
                if device_address_status(address, plc_model) != 'valid' or (
                        address[0] in 'XY' and value not in (0, 1)) or not -32768 <= value <= 65535:
                    return 'invalid_example_operand:' + address
    return None


def _valid_execution_context(context, plc_model):
    from plc.runtime_semantics import control_runtime_facts

    if not isinstance(context, dict) or set(context) - {'cpu_version', 'clock_phases_ms', 'check_cpu_version'}:
        return False
    if 'check_cpu_version' in context and type(context['check_cpu_version']) is not bool:
        return False
    if 'cpu_version' in context and (not isinstance(context['cpu_version'], str) or not 1 <= len(context['cpu_version']) <= 32):
        return False
    phases = context.get('clock_phases_ms', {})
    clocks = control_runtime_facts(plc_model).get('clock_relays', {})
    return isinstance(phases, dict) and all(address in clocks and type(phase) is int and
        0 <= phase < clocks[address]['on_ms'] + clocks[address]['off_ms'] for address, phase in phases.items())


def _instruction_writes(instruction, writes, plc_model):
    from plc.instruction_binding import instruction_write_footprint
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    expanded, _complete = instruction_write_footprint(
        {'opcode': instruction['op'], 'operands': instruction['args']}, plc_model, DEFAULT_INSTRUCTION_REGISTRY)
    return set(writes) | expanded


def _network_accesses(machine):
    accesses = []
    for row in machine.instructions:
        reads, writes = set(), set()
        for instruction, (sources, destinations) in row:
            reads.update(sources)
            writes.update(_instruction_writes(instruction, destinations, machine.model))
        accesses.append((reads, writes))
    return accesses


def _related_networks(accesses, output):
    required, indexes = {output}, set()
    for _ in range(len(accesses)):
        before = len(required)
        for i in reversed(range(len(accesses))):
            reads, writes = accesses[i]
            if required.intersection(writes):
                indexes.add(i)
                required.update(reads)
        if len(required) == before:
            break
    return sorted(indexes)


def source_networks_for_output(ladder, plc_model, address):
    from simulator.bounded import ScanMachine
    if not address:
        return []
    machine = ScanMachine(ladder, plc_model=plc_model, initial={}, execution_context={'program_type': 'scan'})
    return _related_networks(_network_accesses(machine), address)


MAX_COUNTEREXAMPLES = 8


def check_io_example(ladder, plc_model, example, *, acceptance_deadline=None, collect_counterexamples=False,
                     operator_context=None, _replay=None):
    """A definite mismatch applies only to this explicit finite initial state and scan."""
    from simulator.bounded import ScanMachine

    def unknown(reason):
        return {'status': 'unverified', 'reason': reason}

    if reason := _example_reason(example, plc_model, operator=operator_context is not None):
        return unknown(reason)
    if operator_context is not None and not _valid_execution_context(operator_context, plc_model):
        return unknown('invalid_acceptance_execution_context')
    scan_ms, steps = example['scan_ms'], example['steps']
    counterexamples, seen_failures, network_cache = [], set(), {}
    omitted = False

    def input_sequence(through_ms):
        return [{'at_ms': s['at_ms'], 'set': dict(s['set'])}
                for s in steps if s['at_ms'] <= through_ms and s.get('set')]

    def finish(reason=None, checked_through_ms=None):
        if not counterexamples:
            return {**unknown(reason), **({'checked_through_ms': checked_through_ms}
                    if reason == 'local_scan_budget' else {})}
        first = counterexamples[0]
        return {'status': 'mismatch', **first, 'scan_ms': scan_ms,
                'input_sequence': input_sequence(first['at_ms']),
                'counterexamples': counterexamples,
                'network_indexes': sorted({i for item in counterexamples for i in item['network_indexes']}),
                'case_input_sequence': input_sequence(counterexamples[-1]['at_ms']),
                'collection_complete': reason is None and not omitted,
                **({'collection_reason': reason or 'counterexample_limit'} if reason or omitted else {}),
                'checked_through_ms': checked_through_ms}
    times = [step['at_ms'] for step in steps]
    replay = _replay if _replay is not None else {}
    machine = ScanMachine(ladder, plc_model=plc_model, initial={},
                          execution_context={'program_type': 'scan', **(operator_context or {})},
                          instructions=replay.get('instructions'), applications=replay.setdefault('applications', {}),
                          static_cache=replay)
    if 'network_accesses' not in replay:
        replay['instructions'] = machine.instructions
        replay['network_accesses'] = _network_accesses(machine)
        replay['written'] = set().union(*(writes for _, writes in replay['network_accesses']))
        replay['inputs'] = {addr: 0 for record in machine.execution_context['program_accesses']
                            for addr in record['reads'] if addr.startswith('X')}
    written = replay['written']
    inputs = dict(replay['inputs'])
    inputs.update({addr: 0 for step in steps for addr in step.get('set', {})})
    if written.intersection(inputs):
        return unknown('input_also_written_by_program')
    # Only program-written storage and explicit test inputs start at zero.
    # CPU clocks and peripherals must come from known semantics or stay unknown.
    machine.memory.update({addr: 0 for addr in written})
    machine.memory.update({addr + '.contact': False for addr in written if addr.startswith(('T', 'C'))})
    clock_devices = (operator_context or {}).get('clock_phases_ms', {}).keys()
    machine.previous.update({addr: False for addr in written | inputs.keys() | clock_devices})
    machine.previous_enables.update({f'{i}.{j}': False for i, row in enumerate(machine.instructions)
                                     for j in range(len(row))})
    events = {s['at_ms']: s for s in steps}
    deadline = time.monotonic() + 2 if acceptance_deadline is None else acceptance_deadline
    for t in range(0, times[-1] + 1, scan_ms):
        if (acceptance_deadline is None and t // scan_ms > 1200) or time.monotonic() >= deadline:
            return finish('local_scan_budget', t - scan_ms if t else None)
        step = events.get(t, {})
        inputs.update(step.get('set', {}))
        machine.scan({'timestamp_ms': t, 'inputs': inputs}, first_scan=t == 0)
        if machine.gaps:
            gap = machine.gaps[0]
            if gap['reason'].startswith('constraint_failed:') and machine.constraint_violations and not counterexamples:
                violation = machine.constraint_violations[0]
                location = violation['location']
                return {'status': 'mismatch', 'reason': 'executed_instruction_constraint', 'at_ms': t,
                        'scan_ms': scan_ms, 'input_sequence': input_sequence(t),
                        'location': location, 'network_indexes': [location['rung_index']],
                        'findings': [row for row in violation['constraint']['findings'] if row['status'] == 'failed'][:4]}
            return finish(gap['reason'], t - scan_ms if t else None)
        for address, expected in step.get('expect', {}).items():
            actual = machine.memory.get(address)
            if actual is None:
                return finish('unknown_output:' + address, t)
            same = actual & 0xffff == expected & 0xffff if address.startswith('D') else actual == expected
            if not same:
                if address not in network_cache:
                    network_cache[address] = _related_networks(replay['network_accesses'], address)
                indexes = network_cache[address]
                witness = {'at_ms': t, 'address': address, 'expected': expected,
                           'actual': actual, 'network_indexes': indexes}
                if not collect_counterexamples:
                    return {'status': 'mismatch', **witness, 'scan_ms': scan_ms,
                            'input_sequence': input_sequence(t)}
                identity = (tuple(indexes) if indexes else address, expected, actual)
                if identity not in seen_failures:
                    seen_failures.add(identity)
                    if len(counterexamples) < MAX_COUNTEREXAMPLES:
                        counterexamples.append(witness)
                    else:
                        omitted = True
    if counterexamples:
        return finish(checked_through_ms=times[-1])
    return {'status': 'passed_in_example', 'scans': times[-1] // scan_ms + 1}


def _read_acceptance_document(path):
    try:
        with path.open('rb') as stream:
            data = stream.read(262145)
        if len(data) > 262144:
            return None, 'acceptance_file_too_large'
        return json.loads(data.decode('utf-8-sig')), None
    except FileNotFoundError:
        return None, 'no_independent_acceptance_cases'
    except (OSError, ValueError):
        return None, 'acceptance_file_unreadable'


def load_acceptance_cases(project_id, plc_model, *, project_file=None, spec_revision=None):
    """Read operator-owned cases for the active confirmed specification.

    A managed project supplies its own file and the confirmed base revision
    (null before initial confirmation). An absent file leaves the legacy
    environment entry available. A present but stale file is reported as such.
    """
    document, reason = (None, 'no_independent_acceptance_cases')
    stored = False
    if project_file is not None:
        document, reason = _read_acceptance_document(Path(project_file))
        stored = reason != 'no_independent_acceptance_cases'
    if not stored:
        filename = os.environ.get('GXWORKS_ACCEPTANCE_CASES_FILE', '').strip()
        if filename:
            document, reason = _read_acceptance_document(Path(filename).expanduser())
            if reason == 'no_independent_acceptance_cases':
                reason = 'acceptance_file_unreadable'
    if reason:
        return None, reason
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        return None, 'invalid_acceptance_document'
    if not project_id or document.get('project_id') != project_id:
        return None, 'acceptance_project_mismatch'
    if document.get('plc_model') != plc_model:
        return None, 'acceptance_model_mismatch'
    if (stored and 'spec_revision' not in document
            or 'spec_revision' in document and document['spec_revision'] != spec_revision):
        return None, 'acceptance_spec_revision_mismatch'
    cases = document.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 16:
        return None, 'invalid_acceptance_cases'
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('case_id'), str)
                or not 1 <= len(case['case_id']) <= 128 or case['case_id'] in ids
                or set(case) - {'case_id', 'scan_ms', 'steps', 'execution_context'}):
            return None, 'invalid_acceptance_case'
        example = {key: case[key] for key in ('scan_ms', 'steps') if key in case}
        if reason := _example_reason(example, plc_model, operator=True):
            return None, reason
        if not _valid_execution_context(case.get('execution_context', {}), plc_model):
            return None, 'invalid_acceptance_execution_context'
        ids.add(case['case_id'])
    return cases, None


def check_independent_acceptance(ladder, plc_model, *, project_id, confirmed_spec=None,
                                 loaded_cases=None, confirmed_check=None):
    """Replay the independent operator file, retaining finite-scope limitations."""
    cases, reason = loaded_cases if loaded_cases is not None else load_acceptance_cases(project_id, plc_model)
    result = {'source': 'operator_acceptance_file', 'status': 'unverified', 'plc_model': plc_model,
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
                checked = confirmed_check if confirmed_check is not None else check_bounded_traces(ladder, spec,
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
                    accesses = _network_accesses(machine)
                    result.update(status='mismatch', case_id=witness.get('requirement_id'),
                                  expected=witness.get('expected'), actual=witness.get('actual'),
                                  scan=witness.get('scan'), witness=witness,
                                  network_indexes=sorted({i for d in addresses for i in _related_networks(accesses, d)}),
                                  input_sequence=witness.get('input_sequence', []))
                    if witness.get('device'):
                        result['address'] = witness['device']
                    frames = witness.get('input_sequence', [])
                    if frames and 'timestamp_ms' in frames[-1]:
                        result['at_ms'] = frames[-1]['timestamp_ms']
                elif (checked.get('status') == 'no_violation_found_in_tested_scope'
                      and checked.get('execution_status') == 'executed_in_tested_scope'):
                    result['status'] = 'passed_in_cases'
                else:
                    result['reason'] = ('confirmed_behavior_missing_execution_semantics' if checked.get('unsupported_or_missing')
                                        else 'confirmed_behavior_finite_scope_only')
                return result
        result['reason'] = reason
        return result
    # The operator's cases share one time allowance. A long run at a short
    # scan interval can reach its last assertion without extending model self-checks.
    deadline = time.monotonic() + 24
    replay = {}
    for case in cases:
        example = {k: v for k, v in case.items() if k not in {'case_id', 'execution_context'}}
        checked = (check_io_example(ladder, plc_model, example, acceptance_deadline=deadline, collect_counterexamples=True,
                                    operator_context=case.get('execution_context', {}), _replay=replay)
                   if time.monotonic() < deadline else
                   {'status': 'unverified', 'reason': 'local_scan_budget', 'checked_through_ms': None})
        checked['case_id'] = case['case_id']
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

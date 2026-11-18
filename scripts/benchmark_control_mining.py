# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Offline synthetic GXW mining experiment using the existing Core runners.

This entry point makes no model calls. Optional request preparation measures
request size, not provider usage or model quality. Its JSON output must stay
outside the source checkout.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from application.generation_agent import prepare_model_candidate
from plc.control_mining import bind_snapshot_construction, mine_snapshot_state_machine
from plc.ir import ir_to_ladder, lower_rung_instructions
from scripts.control_mining_cases import (
    decode_case, oracle_step, ordered_controller, synthetic_cases, synthetic_confirmed_spec,
)
from simulator.bounded import check_bounded_traces

CONTEXT = {'program_type': 'scan', 'initial_execution_program': False}
ASSUMPTIONS = {'single_cyclic_program': True, 'stable_input_image': True,
               'exclusive_controller_ownership': True}
INTERNAL_RANGES = [{'start': 'M200', 'end': 'M239'}]


def _expected_predicate(address, value):
    kind = {'kind': 'int', 'bits': 16, 'signed': True} if address.startswith('D') else {'kind': 'bool'}
    return {'op': 'eq', 'type': {'kind': 'bool'}, 'args': [
        {'op': 'device', 'type': kind, 'name': address},
        {'op': 'constant', 'type': kind, 'value': value if address.startswith('D') else bool(value)},
    ]}


def oracle_traces(case):
    """Enumerate known states, one invalid state and all Boolean input vectors."""
    inputs = list(dict.fromkeys([*case['signals'], case['stop']]))
    initial_states = [*case['states'], next(v for v in range(32767, -1, -1) if v not in case['states'])]
    traces = []

    def append(identity, state, frames, boot):
        initial = {case['state_device']: state, case['snapshot_device']: 12345,
                   **dict.fromkeys(case['outputs'], True)}
        expectations = []
        for index, frame in enumerate(frames):
            expected = oracle_step(case, state, frame['inputs'], first_scan=boot and index == 0)
            state = expected[case['state_device']]
            expectations.extend({'scan': index, 'requirement_id': 'oracle.' + address,
                                 'predicate': _expected_predicate(address, value)}
                                for address, value in expected.items())
        traces.append({'id': identity, 'initial': initial, 'frames': frames,
                       'starts_in_run': boot, 'expectations': expectations})

    vectors = [dict(zip(inputs, values)) for values in itertools.product((False, True), repeat=len(inputs))]
    for state in initial_states:
        for boot in (False, True):
            for vector_index, values in enumerate(vectors):
                append(f'state_{state}.boot_{int(boot)}.input_{vector_index}', state,
                       [{'inputs': values}], boot)
    all_requests = dict.fromkeys(inputs, True)
    all_requests[case['stop']] = False
    stop = {**all_requests, case['stop']: True}
    idle = dict.fromkeys(inputs, False)
    append('held_requests_and_stop', initial_states[-1], [
        {'inputs': values} for values in [all_requests, all_requests, all_requests, stop,
                                        all_requests, idle, all_requests, all_requests]], True)
    sequential = [{**idle, signal: True} for signal in case['signals']]
    append('sequential_requests', case['states'][0], [{'inputs': idle}] +
           [{'inputs': values} for values in sequential] + [{'inputs': stop}, {'inputs': idle}], True)
    return traces


def _small_witness(row, traces):
    trace = next((t for t in traces if t['id'] == row.get('trace_id')), None)
    return {key: value for key, value in {
        'requirement_id': row.get('requirement_id'), 'reason': row.get('reason', 'value_mismatch'),
        'trace_id': row.get('trace_id'), 'scan': row.get('scan'),
        'initial': trace['initial'] if trace else row.get('initial'),
        'input_sequence': row.get('input_sequence'), 'predicate': row.get('predicate'),
        'device': row.get('device'), 'expected': row.get('expected'), 'actual': row.get('actual'),
        'writes': [{k: w[k] for k in ('device', 'before', 'after') if k in w} for w in row.get('writes', [])],
    }.items() if value is not None}


def evaluate_candidate(case, ladder, spec, *, traces=None):
    """Use Core's existing interpreter with independently authored expectations."""
    traces = oracle_traces(case) if traces is None else traces
    started = time.perf_counter()
    report = check_bounded_traces(ladder, spec, traces)
    oracle = [row for row in report['checks'] if row.get('scope') == 'explicit_trace_expectation']
    violations = [v for row in oracle for v in row.get('violations', [])]
    unknown = sum(row.get('unknown_observations', 0) for row in oracle)
    if violations:
        status = 'violated'
    elif (not oracle or unknown or report['execution_status'] != 'executed_in_tested_scope'
          or any(row['status'] == 'unverified' for row in oracle)):
        status = 'unverified'
    else:
        status = 'passed_in_tested_scope'
    ordinary = [row for row in report['checks'] if row.get('scope') != 'explicit_trace_expectation']
    return {
        'oracle_status': status, 'oracle_unknown_observations': unknown,
        'oracle_checks': [{'id': row['requirement_id'], 'status': row['status'],
                           'scans_tested': row['scans_tested'], 'unknown_observations': row['unknown_observations'],
                           'reasons': row['reasons']} for row in oracle],
        'counterexamples': [_small_witness(v, traces) for v in violations[:3]],
        'behavior_check_status': report['status'], 'execution_status': report['execution_status'],
        'behavior_checks': [{'id': row['requirement_id'], 'status': row['status'],
                             'reasons': row['reasons'],
                             'counterexamples': [_small_witness(v, traces) for v in row['violations'][:1]]}
                            for row in ordinary],
        'unsupported_or_missing': report['unsupported_or_missing'][:8],
        'checker_exceptions': report['checker_exceptions'], 'coverage': report['coverage'],
        'scans_requested': sum(len(t['frames']) for t in traces),
        'wall_ms': (time.perf_counter() - started) * 1000,
    }


def _shape(ladder):
    return {'rungs': len(ladder['rungs']),
            'instructions': sum(len(lower_rung_instructions(r)) for r in ladder['rungs'])}


def _mine(program):
    return mine_snapshot_state_machine(program, target_model='FX3U', execution_context=CONTEXT,
                                       assumptions=ASSUMPTIONS)


def run_experiment(*, preview_model=None, runtime_metadata=None):
    started = time.perf_counter()
    results, internal = [], []
    for case in synthetic_cases():
        stage = time.perf_counter()
        decoded = decode_case(case)
        gxw_ms = (time.perf_counter() - stage) * 1000
        stage = time.perf_counter()
        mined = _mine(decoded['program_ir'])
        mining_ms = (time.perf_counter() - stage) * 1000
        spec = synthetic_confirmed_spec(case)
        stage = time.perf_counter()
        bound = bind_snapshot_construction(mined, spec, internal_ranges=INTERNAL_RANGES)
        binding_ms = (time.perf_counter() - stage) * 1000
        result = {'case_id': case['case_id'], 'source_cpu': decoded['ordered']['plc_model'],
                  'execution_target': case['plc_model'], 'gxw_bytes': len(decoded['raw']),
                  'decode_coverage': decoded['ordered']['coverage'],
                  'mining': {k: mined[k] for k in ('status', 'reasons', 'model_calls')},
                  'binding': {k: bound[k] for k in ('status', 'reasons', 'model_calls')},
                  'timing_ms': {'gxw_encode_decode': gxw_ms, 'mining': mining_ms, 'binding': binding_ms}}
        if bound['status'] != 'bound':
            results.append(result)
            continue
        stage = time.perf_counter()
        candidate = prepare_model_candidate(bound['compact'], bound['confirmed_spec'], case['plc_model'])
        result['timing_ms']['core_expansion'] = (time.perf_counter() - stage) * 1000
        source_ladder = ir_to_ladder(decoded['program_ir'])
        traces = oracle_traces(case)
        result.update(parameters=mined['parameters'], equivalence=mined['equivalence'],
                      algorithm=mined['algorithm'], mined_behavior_constraints=mined['behavior_constraints'],
                      construction_plan=bound['confirmed_spec']['selected_approach']['construction_plan'],
                      model_candidate_compact=bound['compact'], allocations=bound['construction']['allocations'],
                      dependency_graph=mined['dependency_graph'],
                      source_shape=_shape(source_ladder), constructed_shape=_shape(candidate['ladder']),
                      dependency_summary={key: len(mined['dependency_graph'].get(key, []))
                                          for key in ('device_edges', 'network_edges')},
                      constructed_model_calls=bound['construction']['model_calls'],
                      source=evaluate_candidate(case, source_ladder, spec, traces=traces),
                      constructed=evaluate_candidate(case, candidate['ladder'], spec, traces=traces))
        if preview_model:
            from model_runtime.provider import OpenAICompatibleProvider
            from scripts.benchmark_construction import prepare_mining_requests
            provider = OpenAICompatibleProvider({'adapter': 'openai_compatible',
                'baseUrl': 'https://offline.invalid/v1', 'model': preview_model}, 'offline-preview', client=object())
            try:
                flight = prepare_mining_requests(case, bound['confirmed_spec'], provider)
                result['model_comparison'] = {k: v for k, v in flight.items() if k not in {'requests', 'evidence'}}
            except (ValueError, KeyError, RuntimeError) as error:
                result['model_comparison'] = {'status': 'unavailable', 'reason': str(error),
                                              'model_evaluation': 'not_run', 'network_calls': 0}
        internal.append((case, spec, bound))
        results.append(result)

    mutants = []
    first = synthetic_cases()[0]
    original = ordered_controller(first)
    output_start = len(original) - 1 - 2 * len(first['outputs'])
    mutations = {
        'live_state_cascade': ordered_controller(first, snapshot=False),
        'outputs_before_transitions': original[:4] + original[output_start:-1] + original[4:output_start] + original[-1:],
        'shadow_rewrite': original[:8] + [{'op': 'LD', 'args': ['M8000']},
                                          {'op': 'MOV', 'args': ['K2', first['snapshot_device']]}] + original[8:],
    }
    for name, rows in mutations.items():
        decoded = decode_case(first, instructions=rows)
        mined = _mine(decoded['program_ir'])
        mutants.append({'case_id': name, 'extraction_status': mined['status'], 'reasons': mined['reasons'],
                        'source': evaluate_candidate(first, ir_to_ladder(decoded['program_ir']), synthetic_confirmed_spec(first))})
    priority = next((row for row in internal if row[0]['case_id'] == 'priority_branch'), None)
    if priority:
        case, spec, bound = priority
        wrong_spec = copy.deepcopy(bound['confirmed_spec'])
        relation = next(row for row in wrong_spec['behavior_constraints'] if row['kind'] == 'transition_group')
        relation['transitions'].reverse()
        wrong = prepare_model_candidate(bound['compact'], wrong_spec, case['plc_model'])
        mutants.append({'case_id': 'incorrect_core_priority', 'intentional_corruption': True,
                        'constructed': evaluate_candidate(case, wrong['ladder'], spec)})
    constructed = [r['constructed'] for r in results if 'constructed' in r]
    return {
        'experiment': 'synthetic_gxw_snapshot_control_mining', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'platform': platform.system(), 'runtime_metadata': runtime_metadata,
        'assumptions': ASSUMPTIONS, 'observation_scope': 'end_of_scan_state_and_outputs; scratch_and_intermediate_writes_excluded',
        'model_evaluation': 'not_run', 'native_execution': 'not_run', 'network_calls': 0,
        'request_template_model': preview_model, 'actual_model': None,
        'cases': results, 'counterexamples': mutants,
        'summary': {'cases': len(results), 'recognized': sum(r['mining']['status'] == 'recognized' for r in results),
                    'bound': sum(r['binding']['status'] == 'bound' for r in results),
                    'deterministic_candidates_passed': sum(r['oracle_status'] == 'passed_in_tested_scope' for r in constructed),
                    'deterministic_candidates_unverified': sum(r['oracle_status'] == 'unverified' for r in constructed)},
        'wall_ms': (time.perf_counter() - started) * 1000,
        'limitations': [
            'Synthetic sources are authored examples, not evidence of prevalence in industrial projects.',
            'Finite scans check the declared subset, not all inputs, arbitrary state words or native PLC execution.',
            'The existing transition-group checker rejects repeated writes even when terminal state agrees; its result is retained separately.',
            'Prepared request token counts are deterministic estimates, not actual provider token consumption.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preview-model', help='Explicit model name for request preparation only; no transport calls')
    parser.add_argument('--runtime-metadata', type=Path, help='Optional JSON describing the installed released runtime facts')
    args = parser.parse_args(argv)
    destination = args.output.expanduser().resolve()
    if destination.is_relative_to(ROOT):
        parser.error('--output must be outside the source checkout')
    runtime = json.loads(args.runtime_metadata.read_text(encoding='utf-8')) if args.runtime_metadata else None
    report = run_experiment(preview_model=args.preview_model, runtime_metadata=runtime)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(destination), **report['summary'], 'wall_ms': report['wall_ms']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

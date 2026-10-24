"""Exact-source guard and semantic-shape regression tests.

These tests do not claim PLC execution. The optional offline evidence test uses
an explicitly supplied source bundle, not a private absolute path in production.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import unittest
from unittest.mock import patch
from contextlib import contextmanager
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / 'src')]
from tools import fact_fx5_lifecycle_backlog_review as review


def item(page, kind, line=None):
    matches = [v for k, v in review._ENTRIES.items()
               if k[0] == kind and k[1] == page and (line is None or k[2] == line)]
    assert matches
    return matches


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@contextmanager
def guarded():
    clause = 'When the selected input rises, retain the bound instance identity.'
    section = {'first_page': 1, 'section_token': 'guard_fixture', 'text': 'Fixture full section'}
    record = {'clause_sha256': hashlib.sha256(clause.encode()).hexdigest(),
              'section_sha256': hashlib.sha256(section['text'].encode()).hexdigest(),
              'first_page': 1, 'section_token': 'guard_fixture', 'required_operands': ['s'],
              'review': {'rules': [{'kind': 'state_transition', 'subject': {'operand': 's'}}],
                         'full_clause_review': True}}
    # clause_index is intentionally NOT supplied at invocation: the central
    # pipeline sets it after review. Matching uses full-clause SHA256 instead.
    with patch.object(review, '_ENTRIES', {('execution_timing', 1, 8, 2): record}), patch.object(review, '_NAVIGATION', {}):
        yield dict(kind='execution_timing', clause=clause, section=section,
                   block={'page': 1, 'line': 8}, value={}, declared={'s'})


def test_exact_full_clause_acceptance_without_late_clause_index(guarded):
    assert review.review_lifecycle_clause(**guarded)['full_clause_review']


def test_source_mutations_fail_closed(guarded, mutation):
    args = copy.deepcopy(guarded)
    if mutation == 'clause': args['clause'] += ' unless interrupted'
    if mutation == 'section_text': args['section']['text'] += ' changed context'
    if mutation == 'first_page': args['section']['first_page'] = 2
    if mutation == 'section_token': args['section']['section_token'] = 'other_instruction'
    if mutation == 'source_page': args['block']['page'] = 2
    if mutation == 'source_line': args['block']['line'] = 9
    if mutation == 'source_kind': args['kind'] = 'error'
    if mutation == 'operand': args['declared'] = set()
    assert review.review_lifecycle_clause(**args) is None


def test_results_are_independent_copies(guarded):
    first = review.review_lifecycle_clause(**guarded)
    first['rules'].clear()
    assert review.review_lifecycle_clause(**guarded)['rules']


def test_all_published_reviews_bound_and_nonexecuting():
    for entry in review._ENTRIES.values():
        result = entry['review']
        assert result['full_clause_review']
        assert result['value_updates']['core_execution_capability'] == 'not_established'
        required = set(entry['required_operands'])
        used = {n['operand'] for n in walk(result['rules']) if 'operand' in n}
        assert used == required
        assert len(entry['clause_sha256']) == len(entry['section_sha256']) == 64


def test_navigation_not_counted_as_semantic_closure():
    assert review._NAVIGATION
    for entry in review._NAVIGATION.values():
        result = entry['review']
        assert result['content_role'] in {'source_navigation', 'worked_example'}
        assert all(rule['kind'] == 'source_navigation' for rule in result['rules'])
        assert all(rule['target_source_key'] for rule in result['rules'])


def test_stmr_history_is_instruction_timer_not_fb():
    for entry in item(776, 'control_data'):
        for node in walk(entry['review']['rules']):
            if 'history_scope' in node:
                assert node['history_scope'] == 'instruction_timer_and_execution_command'
                assert node['timer_binding'] == {'operand': 's1'}


def test_edge_pulse_then_disabled_invocation_retains_output(page, name):
    relation = item(page, 'execution_timing')[0]['review']['rules'][0]
    assert 'next_scan' not in relation
    disabled = relation['branches'][2]
    assert disabled['forms'] == [name + '_E']
    assert disabled['when'] == {'operator': '=', 'left': {'operand': 'en'}, 'right': False}
    # Holding a previously TRUE pulse must not manufacture a reset in an EN-off scan.
    effect = disabled['effects'][0]
    assert effect == {'target': {'operand': 'd'}, 'update': 'hold'}
    previous = True
    current = previous if effect['update'] == 'hold' else None
    assert current is True
    assert relation['branches'][1]['reset']['at'] == 'next_enabled_invocation_without_new_edge'
    assert relation['first_invocation_history'] == 'not_established'
    assert relation['input_history_while_disabled'] == 'not_established'


def test_timer_timebase_never_scan_ordinal():
    timers = [v for k, v in review._ENTRIES.items() if 1389 <= k[1] <= 1395]
    assert timers
    for entry in timers:
        for node in walk(entry['review']['rules']):
            assert node.get('timebase') != 'scan_count'
            if node.get('timer') == 'start_or_continue':
                assert node['restart_while_running'] is False


def test_timer_one_ms_form_not_all_timer_family():
    for entry in item(1395, 'processing_qualifications'):
        assert entry['review']['forms'] == ['TIMER_1_FB_M']


def test_pid_mode_bound_fields_and_unknown_boundary_remain():
    # AB versus AG KD=100 remains absent. TS > scan is explicitly established.
    assert ('error', 963, 28, 0) not in review._ENTRIES
    sampling = review._ENTRIES[('error', 963, 30, 0)]['review']['rules'][0]
    assert sampling['required']['operator'] == '>'
    assert sampling['required']['right']['runtime_value'] == 'CPU_scan_time'
    pid = [v for k, v in review._ENTRIES.items() if k[1] in {963, 964}]
    assert any(n.get('bit') == 6 for v in pid for n in walk(v['review']['rules']))
    assert all(v['review'].get('additional_sources') for v in pid)
    assert all('supplementary_sources' not in v['review']['value_updates'] for v in pid)


def test_capacity_shared_and_version_separated():
    relations = [n for v in review._ENTRIES.values() for n in v['review']['rules']
                 if n['kind'] == 'resource_capacity']
    assert len(relations) == 3
    for relation in relations:
        assert relation['usage']['operator'] == '+'
        assert relation['on_unknown_capability'] == 'unverified'
        enhanced = relation['branches'][1]
        assert enhanced['required_cpu_firmware']['minimum'] == '1.015'
        assert enhanced['supported_engineering_tool']['minimum'] == '1.015R'
        assert enhanced['required_cpu_serial']['minimum_pattern'] == '158****'


def test_pwm_unknowns_not_accepted():
    assert not any(k[1] in {817, 818, 824, 825} for k in review._ENTRIES)


def test_real_source_bundle_when_supplied():
    path = os.environ.get('FX5_LIFECYCLE_EVIDENCE_DIR')
    if not path:
        raise unittest.SkipTest('Set FX5_LIFECYCLE_EVIDENCE_DIR for original-source guard regressions')
    path = Path(path)
    rows = json.loads((path / 'rows.json').read_text())
    sections = {s['outline']: s for s in json.loads((path / 'sections.json').read_text())}
    ledger = json.loads((path / 'ledger.json').read_text())
    tested = 0
    for row in ledger:
        if row['status'] == 'proof_gap':
            continue
        source = rows[row['row_index']]
        args = dict(kind=source['source_kind'], clause=source['clause'],
                    section=sections[source['outline']], block=source['block'],
                    value={}, declared=set(source['declared']))
        result = review.review_lifecycle_clause(**args)
        assert result is not None, source['key']
        assert review.review_lifecycle_clause(**{**args, 'clause': source['clause'] + ' altered'}) is None
        tested += 1
    assert tested == len(review._ENTRIES) + len(review._NAVIGATION)



def test_ist_step_activation_and_shared_reset_topology():
    relation = next(rule for entry in review._ENTRIES.values()
                    for rule in entry['review']['rules'] if rule['kind'] == 'equivalent_circuit_relations')
    nodes = relation['relations']
    step = next(n for n in nodes if n['target'] == {'device': 'S2'})
    assert step['operation'] == 'activate_initial_step'
    assert 'value' not in step
    assert not any(n.get('device') == 'SM8041' for n in walk(step['guard']))
    complete = next(n for n in nodes if n['target'] == {'device': 'SM8043'})
    reset = complete['separate_reset']
    assert reset['operator'] == 'or'
    assert any(n.get('device') == 'SM8041' for n in walk(reset))
    assert any(n.get('device') == 'S2' for n in walk(reset))


def test_mode_graph_retains_nc_guards_and_does_not_claim_generic_coverage():
    graphs = [rule for entry in review._ENTRIES.values() for rule in entry['review']['rules']
              if rule['kind'] == 'mode_transition_relations']
    assert len(graphs) == 2
    for graph in graphs:
        assert graph['source_scope'] == 'only_four_drawn_switch_paths'
        assert graph['contact_sampling_relative_to_scan'] == 'not_stated'
        transition = next(b for b in graph['branches'] if b['transition'] == ['automatic', 'zero_return'])
        assert transition['guard_at_switch'] == {'operator': 'not', 'argument': {'device': 'S2'}}
        assert graph['exception']['guard_at_switch'] == {'device': 'S2'}


def test_diagnostic_duplicate_threshold_does_not_license_two_definitions():
    error = next(rule for entry in review._ENTRIES.values() for rule in entry['review']['rules']
                 if rule['kind'] == 'program_structure_error')
    assert error['trigger']['right'] == 3
    assert error['absence_of_this_error_does_not_establish_legality']
    unique = next(rule for entry in review._ENTRIES.values() for rule in entry['review']['rules']
                  if rule.get('definition') == 'STL_step_state')
    assert unique['maximum_definitions'] == 1
    assert unique['excludes'] == 'OUT_SET_transfer_references'


def test_capacity_rules_survive_typed_projection_without_relaxed_grammar():
    from knowledge.fact_corpus import typed_value, UnresolvedExpression
    for page in (996, 999, 1002):
        entry = next(e for e in item(page, 'error')
                     if any(r['kind'] == 'resource_capacity' for r in e['review']['rules']))
        rules = entry['review']['rules']
        assert typed_value(rules) == rules
        capacity = next(r for r in rules if r['kind'] == 'resource_capacity')
        expanded = next(b for b in capacity['branches'] if b.get('capacity') == 32
                        and 'required_cpu_serial' in b)
        assert expanded['supported_engineering_tool'] == {'name': 'GX_Works3', 'minimum': '1.015R'}
        assert expanded['required_cpu_serial'] == {'minimum_pattern': '158****'}
        assert capacity['usage']['operator'] == '+'
        assert capacity['on_unknown_capability'] == 'unverified'
    try:
        typed_value('GX Works3')
    except UnresolvedExpression:
        pass
    else:
        raise AssertionError('Unclassified free text must still fail closed')



def test_call_style_never_creates_opcode_owner_in_production_projection():
    path = os.environ.get('FX5_LIFECYCLE_EVIDENCE_DIR')
    if not path:
        raise unittest.SkipTest('Set FX5_LIFECYCLE_EVIDENCE_DIR for production owner routing')
    from tools.fact_fx5_condition_review import condition_specs
    from tools.fact_fx5_programming_residual import (
        _declarations, _parse_section, _candidates, _partition_condition_owners, _fact)
    from knowledge.fact_corpus import project_definition
    sections = json.loads((Path(path) / 'sections.json').read_text())
    selected = {(k[1], k[2]): e for k, e in review._ENTRIES.items()
                if e['review'].get('value_updates', {}).get('operand_specification_style')}
    assert len(selected) == 4
    checked = set()
    qualifier = {'context': 'operand_specification_style', 'equals': 'FX3_compatible'}
    for section in sections:
        wanted = {k for k, e in selected.items() if e['first_page'] == section['first_page']}
        if not wanted:
            continue
        universe = set(_declarations(section))
        candidates = _candidates(section, _parse_section(section, universe), universe)
        assert candidates
        owners = {r['opcode'] for r in candidates}
        assert 'FX3_compatible_operand_specification' not in owners
        for record in candidates:
            specs = condition_specs(section, record, include_other_forms=True)
            own, related = _partition_condition_owners(specs, record['opcode'], owners)
            assert 'FX3_compatible_operand_specification' not in related
            for spec in own:
                identity = (spec['page'], spec['value'].get('source_line'))
                if identity not in wanted:
                    continue
                assert spec['status'] == 'source_verified'
                assert qualifier in spec['conditions']
                assert not set(spec.get('scope_forms', ())) - owners
                fact = _fact(spec['key'], spec['dimension'], spec['value'], [], ['FX5U'], status=spec['status'])
                fact['conditions'] = spec['conditions']
                projected, withheld = project_definition({'opcode': record['opcode'], 'facts': [fact]})
                assert not withheld, withheld
                assert qualifier in projected['facts'][0]['conditions']
                checked.add(identity)
    assert checked == set(selected), (checked, set(selected))



def test_operand_style_applicability_uses_existing_context_condition():
    from plc.instruction_constraints import _condition, UnknownInstructionSemantics
    condition = {'context': 'operand_specification_style', 'equals': 'FX3_compatible'}
    assert _condition(condition, {}, {'operand_specification_style': 'FX3_compatible'}) is True
    assert _condition(condition, {}, {'operand_specification_style': 'channel_number'}) is False
    try:
        _condition(condition, {}, {})
    except UnknownInstructionSemantics:
        pass
    else:
        raise AssertionError('Absent style must remain unknown, not universally applicable')


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    argument_tests = {'test_exact_full_clause_acceptance_without_late_clause_index',
                      'test_results_are_independent_copies', 'test_source_mutations_fail_closed',
                      'test_edge_pulse_then_disabled_invocation_retains_output'}
    for name, function in globals().copy().items():
        if name.startswith('test_') and callable(function) and name not in argument_tests:
            suite.addTest(unittest.FunctionTestCase(function))
    def with_fixture(function, *args):
        def run():
            with guarded() as fixture:
                function(fixture, *args)
        return run
    for function in [test_exact_full_clause_acceptance_without_late_clause_index,
                     test_results_are_independent_copies]:
        suite.addTest(unittest.FunctionTestCase(with_fixture(function), description=function.__name__))
    for mutation in ['clause', 'section_text', 'first_page', 'section_token', 'source_page',
                     'source_line', 'source_kind', 'operand']:
        suite.addTest(unittest.FunctionTestCase(with_fixture(test_source_mutations_fail_closed, mutation),
                                               description='reject modified ' + mutation))
    for page, name in [(1376, 'R_TRIG'), (1378, 'F_TRIG')]:
        suite.addTest(unittest.FunctionTestCase(
            lambda page=page, name=name: test_edge_pulse_then_disabled_invocation_retains_output(page, name),
            description=name + ' EN-off retains true pulse'))
    return suite


if __name__ == '__main__':
    unittest.main()

"""Research coverage is not a model-facing instruction restriction."""
import copy
import json

from shared.model_context import model_knowledge_value, model_review_view, technical_context_units, technical_block


def test_review_labels_do_not_remove_concrete_behavior_or_replace_it_with_cautions():
    raw = {'kind': 'instruction_fact', 'scope': {'models': ['FX3U']},
        'knowledge_status': 'declared_unverified', 'binding_status': 'unverified',
        'knowledge_status_by_field': {'behavior': 'unverified'},
        'unverified_fields': ['runtime_execution'],
        'avoid_relying_on': ['whole_instruction'],
        'required_prerequisites': ['review_complete'],
        'value': {'available': True, 'behavior': 'copy_source_to_destination'},
        'behavior': {'trigger': 'rising_edge'},
        'conditions': [{'context': 'device_state', 'device': 'M0', 'equals': True}],
        'limits': {'bits': 16, 'minimum': 1, 'maximum': 64},
        'note': 'Preserve the destination when disabled; unverified by the local executor.'}
    before = copy.deepcopy(raw)
    projected = model_knowledge_value(raw)
    assert projected['value'] == raw['value']
    assert projected['behavior'] == raw['behavior']
    assert projected['conditions'] == raw['conditions']
    assert projected['limits'] == raw['limits']
    assert projected['note'] == 'Preserve the destination when disabled'
    text = json.dumps(projected)
    assert all(word not in text for word in ('unverified', 'review_complete', 'avoid_relying_on'))
    assert raw == before
    assert model_knowledge_value(projected) == projected


def test_executor_coverage_is_not_instruction_semantics():
    raw = {'semantics': {
        'execution.core_execution_capability': {'supported': False, 'reason': 'not_implemented'},
        'effects.result': {'operation': 'shift_bits'},
        'constraints.operand_range': {'minimum': 1, 'maximum': 512},
    }}
    assert model_knowledge_value(raw) == {'semantics': {
        'effects.result': {'operation': 'shift_bits'},
        'constraints.operand_range': {'minimum': 1, 'maximum': 512}}}


def test_examples_keep_setup_and_strip_review_disclaimers_on_actual_context_route():
    raw = {'kind': 'example', 'target': 'delay', 'value': {
        'limitation': '显式提供经过时间，不把扫描次数换算成时间；未验证实机定时精度。',
        'frames': [{'timer_elapsed_ms': {'T0': 300}}],
        'expected': {'Y0': True}}}
    output, = technical_context_units(technical_block(raw))
    assert output['value']['limitation'] == '显式提供经过时间，不把扫描次数换算成时间'
    assert output['value']['frames'] == raw['value']['frames']
    assert output['value']['expected'] == raw['value']['expected']


def test_actual_model_restrictions_and_program_text_are_not_review_disclaimers():
    raw = {'scope': {'models': ['Q03UDE']}, 'value': {'available': False},
           'conditions': [{'context': 'project_mode', 'equals': 'structured'}],
           'limitation': 'Count must not exceed region length.',
           'program': {'source': '(* unverified is a user symbol *)'}}
    assert model_knowledge_value(raw) == raw


def test_tool_coverage_verdict_does_not_erase_real_operand_errors_or_claim_success():
    report = {'ok': True, 'data': {'status': 'unverified', 'valid': False,
        'reason': 'source_coverage_incomplete', 'version_id': 'v1',
        'findings': [{'severity': 'unverified', 'message': 'local executor missing'},
                     {'severity': 'error', 'code': 'OPERAND_COUNT', 'expected': 3, 'actual': 2}]}}
    before = copy.deepcopy(report)
    projected = model_review_view(report)
    assert projected == {'ok': True, 'data': {'version_id': 'v1', 'findings': [
        {'severity': 'error', 'code': 'OPERAND_COUNT', 'expected': 3, 'actual': 2}]}}
    assert report == before


def test_shared_tool_wire_projection_omits_review_only_verdicts():
    from agent_runtime.runtime import _model_tool_value
    report = {'ok': True, 'tool': 'validate_project', 'data': {
        'status': 'unverified', 'valid': False, 'reason': 'source_coverage_incomplete',
        'findings': [{'severity': 'error', 'code': 'OPERAND_TYPE', 'device': 'Y0'}]}}
    output = _model_tool_value('validate_project', report)
    assert 'unverified' not in json.dumps(output)
    assert 'source_coverage_incomplete' not in json.dumps(output)
    assert output['data']['findings'][0]['code'] == 'OPERAND_TYPE'
    assert 'passed' not in output['data']


def test_projection_preserves_delivery_terminator():
    value = {'data': {'delivery': {'complete': True, 'next': None}, 'samples': [1, None, 3]}}
    assert model_review_view(value) == value


def test_note_projection_preserves_technical_text_spacing():
    text = 'First operation. Second operation. Delay is 0.1 s.'
    assert model_knowledge_value({'note': text}) == {'note': text}
    assert model_knowledge_value({'note': text + ' Unverified by the executor.'}) == {'note': text}

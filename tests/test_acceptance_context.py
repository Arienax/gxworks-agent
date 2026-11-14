"""Lossless behavior examples and stable continuation changes on the wire."""
import copy
import json

from hypothesis import given, settings, strategies as st

from application.confirmed_generation_context import acceptance_cases_prompt
from application.conversation_model import _changes, _rule_sections
from application.generation_wire import expand_context_document


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@st.composite
def case_documents(draw):
    states = draw(st.lists(st.dictionaries(
        st.sampled_from(['Y0', 'Y2', 'Y3', 'Y10', 'D0', 'D2', 'D10', 'D20']),
        st.one_of(st.booleans(), st.integers(-32768, 65535)), max_size=8), min_size=1, max_size=4))
    cases = []
    for index in range(draw(st.integers(1, 3))):
        steps = []
        for tick in range(draw(st.integers(2, 32))):
            step = {'at_ms': tick * 10}
            if draw(st.booleans()):
                step['expect'] = copy.deepcopy(draw(st.sampled_from(states)))
            if draw(st.booleans()):
                step['set'] = {'D0': draw(st.integers(-32768, 65535))}
            steps.append(step)
        cases.append({'case_id': f'case-{index}', 'scan_ms': 10, 'steps': steps,
                      'execution_context': {'clock_phases_ms': {'M8011': index}}})
    return cases


@given(case_documents())
@settings(max_examples=100, deadline=None)
def test_case_context_preserves_every_value_and_never_grows(cases):
    before = encoded(cases)
    prompt = acceptance_cases_prompt(cases)
    document = json.loads(prompt.split('# Project acceptance cases\n', 1)[1])
    assert encoded(expand_context_document(document)) == before
    assert encoded(cases) == before
    original = '\n\n# Project acceptance cases\n' + json.dumps(cases, ensure_ascii=False, separators=(',', ':'))
    assert len(prompt.encode('utf-8')) <= len(original.encode('utf-8'))


def repeated_case():
    return [{'case_id': 'batch', 'scan_ms': 10, 'steps': [
        {'at_ms': tick*10, 'expect': {f'D{i}': i for i in range(16)}} for tick in range(20)]}]


def test_reference_encoding_does_not_change_continuation_state():
    cases = repeated_case()
    old = _rule_sections('# Project acceptance cases\n' + json.dumps(cases))
    prompt = acceptance_cases_prompt(cases)
    assert '"$context_reference":true' in prompt
    current = _rule_sections(prompt)
    assert _changes(old, current) == {}
    revised = copy.deepcopy(cases)
    revised[0]['steps'][0]['expect']['D0'] = 7
    changes = _changes(current, _rule_sections(acceptance_cases_prompt(revised)))
    assert changes == {'set': {'/Project acceptance cases/0/steps/0/expect/D0': 7}}
    assert current['Project acceptance cases'][0]['steps'][1]['expect']['D0'] == 0


def test_empty_and_small_cases_keep_legacy_shape():
    assert acceptance_cases_prompt([]) == ''
    cases = [{'case_id': 'start', 'scan_ms': 10, 'steps': [
        {'at_ms': 0, 'set': {'X0': 1}, 'expect': {'Y0': 1}}, {'at_ms': 10}]}]
    assert json.loads(acceptance_cases_prompt(cases).split('# Project acceptance cases\n')[1]) == cases

"""CPU version checks are opt-in; ordinary model/operand checks remain active."""
import pytest

from plc.instruction_constraints import check_fact_constraints
from plc.validation import _confirmed_instruction_versions


def fact(identity, dimension, value, conditions=(), depends_on=()):
    return {'id': identity, 'dimension': dimension, 'value': value,
            'scope': {'models': ['FX3G'], 'forms': ['TEST'], 'languages': ['ladder']},
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [{'source_id': 'version_policy_test'}],
            'conditions': list(conditions), 'depends_on': list(depends_on)}


def evaluate(context=None, extra=()):
    minimum = {'context': 'cpu_version', 'minimum': '1.10'}
    groups = [
        fact('version', 'constraints.cpu_version',
             {'models': ['FX3G'], 'minimum_series_version': 'Ver.1.10'}),
        fact('available', 'constraints.cpu_availability', {'model_states': {'FX3G': 'conditional'}},
             [minimum], ['version']),
        fact('old_exclusion', 'constraints.cpu_availability',
             {'model_states': {'FX3G': 'unsupported'}, 'rule': 'firmware_too_old'},
             [{'context': 'cpu_version', 'maximum_exclusive': '1.10'}]),
        fact('count', 'constraints.documented_restriction',
             {'ordered_bounds': [{'parameter': 'N', 'minimum': 1}]}, depends_on=['available']),
        *extra]
    return check_fact_constraints(groups, {'N': 1}, target='TEST', plc_model='FX3G',
                                  language='ladder', context=context)


def test_unspecified_cpu_version_is_not_checked_including_later_than_first_release():
    result = evaluate()
    assert result['status'] == 'passed'
    assert result['cpu_version_check'] == 'not_requested'
    assert {r['status'] for r in result['checks'] if r['fact_id'] != 'count'} == {'not_checked'}
    assert not result['findings']


@pytest.mark.parametrize('context,status', [({'check_cpu_version': True}, 'unverified'),
    ({'cpu_version': '1.00'}, 'failed'), ({'cpu_version': '1.10'}, 'passed')])
def test_explicit_request_or_version_enables_checks(context, status):
    result = evaluate(context)
    assert result['status'] == status
    assert result['cpu_version_check'] == 'enabled'


def test_ordinary_model_exclusions_and_operand_errors_remain_errors():
    excluded = fact('wrong_model', 'constraints.cpu_availability',
                    {'model_states': {'FX3G': 'unsupported'}, 'rule': 'not_supported_on_model'})
    bad_count = fact('bad_count', 'constraints.documented_restriction',
                     {'ordered_bounds': [{'parameter': 'N', 'minimum': 2}]})
    for constraint in (excluded, bad_count):
        assert evaluate(extra=[constraint])['status'] == 'failed'


def test_software_version_is_not_part_of_cpu_policy():
    software = fact('software', 'constraints.documented_restriction',
                    {'ordered_bounds': [{'parameter': 'N', 'minimum': 1}]},
                    [{'context': 'software_version', 'minimum': '2.00'}])
    assert evaluate(extra=[software])['status'] == 'unverified'


def test_user_version_request_survives_missing_or_conflicting_values():
    assert _confirmed_instruction_versions({}) == {}
    assert _confirmed_instruction_versions({'check_cpu_version': True}) == {'check_cpu_version': True}
    assert _confirmed_instruction_versions({'cpu_version': '1.00',
        'hardware_profile': {'cpu_version': '1.10'}}) == {'check_cpu_version': True}

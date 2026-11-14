import copy
import pytest
from tools.rag_execution_queue import initial_queue, update_headers


def test_scope_keeps_all_false_names_and_original_ids_without_promoting_legacy_true():
    ledger = {'excluded_models': ['FX5U'], 'units': [
        {'id': 'I-1', 'domain': 'instruction', 'forms': ['A'], 'models': ['FX3U'], 'remaining': []},
        {'id': 'I-2', 'domain': 'instruction', 'forms': ['B'], 'models': ['Q03UDV'], 'remaining': ['types']},
        {'id': 'B-1', 'domain': 'build', 'remaining': ['codec']}]}
    queue = initial_queue(ledger, [
        {'opcode': 'A', 'target_model': 'FX3U', 'call_available': True},
        {'opcode': 'B', 'target_model': 'Q03UDV', 'call_available': False},
        {'opcode': 'ONLY_FX5', 'target_model': 'FX5U', 'call_available': True}], ['ALIAS'])
    assert set(queue['instructions']) == {'A', 'B', 'ALIAS'}
    assert queue['original_unit_ids'] == ['I-1', 'I-2', 'B-1']
    assert queue['instructions']['A']['state'] == 'candidate_requires_source_and_build_check'
    assert queue['instructions']['B']['state'] == 'needs_first_callable_member'
    assert queue['content_unit_ids'] == ['I-2', 'B-1']
    assert queue['execution_policy']['family_completion'] == 'all_source_supported_original_members_forms_conditions'
    assert queue['execution_policy']['accepted_member_is_family_completion'] is False


def test_update_cannot_shrink_scope_or_turn_truthy_string_into_true():
    queue = initial_queue({'units': [], 'excluded_models': []}, [], ['A', 'B'])
    untouched = copy.deepcopy(queue['instructions']['B'])
    update_headers(queue, [{'opcode': 'A', 'target_model': 'FX3U', 'call_available': 'true'}], ['A'])
    assert queue['instructions']['A']['true_candidates'] == []
    assert queue['instructions']['B'] == untouched
    with pytest.raises(ValueError, match='fixed scope'):
        update_headers(queue, [], ['C'])
    assert set(queue['instructions']) == {'A', 'B'}

import json

from knowledge.fact_corpus import resolve


def payload(model):
    rows = resolve('device_family', ['B'], plc_model=model, task_type='analysis', domain_results=True)
    return [json.loads(row['text'].split('\n', 1)[1]) for row in rows
            if str(row.get('source_chunk_id')) in {'12244', '12245', '12246'}]


def test_link_relay_has_access_and_physical_address_constraints():
    q = next(row for row in payload('Q03UDV') if row['value'].get('source_section_count') == 1)
    assert q['value']['access'] == 'read/write'
    assert 'network refresh range' in q['value']['address_support']
    assert q['value']['source_constraints']['network_parameters_required_for_refresh'] is True


def test_classic_q_and_l_keep_their_own_point_settings():
    q = next(row for row in payload('Q02H') if row['value'].get('source_section_count') == 2)
    assert 'default 8192 points' in q['value']['address_support']
    assert '32768' in q['value']['address_support']
    l = next(row for row in payload('L02') if row['value'].get('source_section_count') == 1)
    assert 'default 8192 points' in l['value']['address_support']
    assert l['value']['source_constraints']['latch_behavior_follows_configured_range'] is True

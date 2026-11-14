"""Network review semantics and fail-closed source/context guards."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tools.fact_fx5_network_backlog_review import REVIEWS, review_network_backlog_clause


def _native_shape_issues(value, path='value'):
    import math
    issues = []
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                issues.append((path, 'nonstring_key', repr(key)))
            issues.extend(_native_shape_issues(child, path + '.' + str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_native_shape_issues(child, path + '[' + str(index) + ']'))
    elif isinstance(value, float) and not math.isfinite(value):
        issues.append((path, 'nonfinite', repr(value)))
    elif not isinstance(value, (str, int, float, bool, type(None))):
        issues.append((path, 'non_json_type', type(value).__name__))
    return issues


def _rows(page, line=None):
    return [v['review'] for k, v in REVIEWS.items() if k[2] == page and (line is None or k[3] == line)]


def _rules(page, kind):
    return [r for review in _rows(page) for r in review['rules'] if r['kind'] == kind]


class NetworkBacklogReviewTests(unittest.TestCase):
    def test_every_review_binds_complete_relations_not_raw_source(self):
        assert len(REVIEWS) >= 120
        for entry in REVIEWS.values():
            review = entry['review']
            assert review['full_clause_review'] is True
            assert review['rules']
            assert all(r['kind'] not in {'control_data_qualification', 'manual_cross_reference'} for r in review['rules'])
            assert not any('source_text' in r or 'manual_text' in r for r in review['rules'])
            assert len(entry['context_sha256']) == 64


    def test_open_continuation_recovers_selector_not_embedded_referenced_offset(self):
        for line in (3, 5):
            rule = _rows(1064, line)[0]['rules'][0]
            assert rule['subject']['offset'] == 0
            assert rule['allowed_values'] == [0, 32768]
            assert rule['branches'][1]['effect']['open_settings_source']['first_word'] == 2
            assert rule['branches'][1]['effect']['open_settings_source']['last_word'] == 6


    def test_protocol_count_and_packet_response_do_not_become_user_inputs(self):
        rules = _rules(1079, 'control_field_relation')
        counts = [r for r in rules if r['subject']['offset'] == 0]
        assert counts[0]['set_by'] == 'system'
        assert counts[0]['value']['includes_error_protocol'] is True
        packets = [r for r in rules if 'protocol_execution_ordinal' in r]
        assert {r['protocol_execution_ordinal'] for r in packets} == set(range(1, 9))
        for r in packets:
            n = r['protocol_execution_ordinal']
            assert r['subject']['offset'] == n + 9
            zero = r['branches'][0]
            assert {'communication_type': 'receive_only'} in zero['when']['any']
            assert {'protocol_execution_error': n} in zero['when']['any']
            if n > 1:
                assert {'executed_protocol_count_less_than': n} in zero['when']['any']


    def test_timeout_unit_selector_and_default_are_not_flat_interval(self):
        timer = _rows(1124, 50)[0]['rules'][0]
        sec, tick = timer['branches']
        assert sec['when']['value_interval'] == [0, 32767]
        assert tick['when']['value_interval'] == [0, 65535]
        assert sec['effect']['duration_ms'] == {'zero': 10000, 'nonzero_scale': 1000}
        assert tick['effect']['duration_ms'] == {'zero': 10000, 'nonzero_scale': 100}
        assert tick['when']['network'] == 'CC-Link_IE_TSN'


    def test_network_layout_preserves_station_and_IP_modes(self):
        rule = _rules(1124, 'conditional_address_layout')[0]
        field, station, ip = rule['branches']
        assert '126' in field['effect']['station_domain']['meanings']
        assert '126' not in station['effect']['station_domain']['meanings']
        assert ip['when']['selector']['bit'] == 15
        assert ip['effect']['fields'][0]['subject']['offset'] == 4
        assert ip['effect']['fields'][0]['high_byte_octet'] == 3
        assert ip['effect']['fields'][1]['high_byte_octet'] == 1


    def test_target_CPU_exception_is_retained(self):
        rule = _rows(1124, 60)[0]['rules'][0]
        assert rule['branches'][0] == {'when': {'target_CPU': 'QnACPU'}, 'effect': {'allowed_interval': [1, 480]}}
        assert rule['branches'][1]['effect']['allowed_interval'] == [1, 960]


    def test_slmp_arrival_and_error_data_are_independent_bits(self):
        r = _rows(1175, 3)[0]['rules'][0]
        assert r['allowed_values'] == [0, 1, 128, 129]
        assert [b['bit'] for b in r['bit_fields']] == [0, 7]
        assert r['bit_fields'][1]['target']['first_word'] == 13
        assert r['bit_fields'][1]['values']['0'] == 'clear'


    def test_servo_status_has_request_and_response_phase_not_static_zero_only(self):
        r = _rows(1195, 19)[0]['rules'][0]
        assert r['subject']['offset'] == 4
        assert r['phases'][0]['required_value'] == 0
        assert r['phases'][1]['value_condition'] == 'nonzero'
        assert r['phases'][2]['value'] == 0


    def test_source_clause_context_cpu_form_and_binding_guards(self):
        section = {'section_token': 'fixture', 'outline': 'FX5U', 'text': 'FORM(s)',
                   'first_page': 1, 'page_layouts': [(1, 'FX5U FORM(s)')], 'page_texts': [(1, 'FORM(s)')]}
        clause = 'complete authored fixture'
        key = ('fixture', 'control_data', 1, 1, hashlib.sha256(clause.encode()).hexdigest())
        entry = copy.deepcopy(next(iter(REVIEWS.values())))
        entry['review']['value_updates']['subjects'] = [{'operand': 's'}]
        entry['declared'] = ['s']
        entry['context_sha256'] = hashlib.sha256(json.dumps({k: section.get(k) for k in
            ('outline', 'text', 'page_layouts', 'page_texts', 'first_page')}, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        guard = patch.dict(REVIEWS, {key: entry})
        guard.start()
        self.addCleanup(guard.stop)
        args = dict(section=section, block={'page': 1, 'line': 1}, value={}, declared={'s'})
        assert review_network_backlog_clause('control_data', clause, **args)
        assert review_network_backlog_clause('control_data', clause + ' if busy', **args) is None
        assert review_network_backlog_clause('error', clause, **args) is None
        assert review_network_backlog_clause('control_data', clause, **{**args, 'declared': set()}) is None
        for field, replacement in [('outline', 'FX3U'), ('text', 'OTHER_FORM(s)'),
                                   ('page_layouts', [(1, 'different condition')]), ('first_page', 2)]:
            changed = {**section, field: replacement}
            assert review_network_backlog_clause('control_data', clause, **{**args, 'section': changed}) is None
        first = review_network_backlog_clause('control_data', clause, **args)
        first['rules'].clear()
        assert review_network_backlog_clause('control_data', clause, **args)['rules']

    def test_error_trigger_is_not_inverted_into_legal_CPU_channel_domain(self):
        r = _rows(1100, 12)[0]['rules'][0]
        assert r['kind'] == 'conditional_error'
        assert r['inverse_validity_not_implied'] is True
        assert r['branches'][1]['when']['outside_interval'] == [1, 4]
        assert 'allowed_interval' not in repr(r)

    def test_navigation_and_source_conflicts_are_distinct_dispositions(self):
        navigations = [v['review'] for v in REVIEWS.values()
                       if v['review'].get('content_role') == 'source_navigation']
        conflicts = [v['review'] for v in REVIEWS.values()
                     if v['review'].get('status') == 'conflict']
        assert len(navigations) == 5
        assert len(conflicts) == 14
        assert all(r['rules'][0]['technical_relation'] is False for r in navigations)
        assert all(r['value_updates']['gap_kind'] == 'source_conflict' for r in conflicts)
        assert all(r['rules'][0]['resolution'] == 'not_established' for r in conflicts)

    def test_ADPRW_six_and_eight_operand_device_matrices_do_not_mix(self):
        six = _rows(1112, 13)[0]['rules'][0]
        eight = _rows(1114, 26)[0]['rules'][0]
        assert six['call_form']['operand_count'] == 6
        assert eight['call_form']['operand_count'] == 8
        assert 'n' not in six['matrix'] and 'n' in eight['matrix']
        assert six['matrix']['s5_or_d1']['constant_classes'] == ['K', 'H']
        assert eight['matrix']['d1']['constant_classes'] == []
        for r in (six, eight):
            assert r['scope'] == 'native_device_specification'
            assert r['label_applicability'] == 'separate_setting_data_label_type_constraints'
            for operand in r['matrix'].values():
                assert not {'T', 'ST', 'C'} & set(operand['word_device_classes'])
            assert r['matrix']['d2']['indirect_specification'] is False
            assert r['violation_effect']['error_code'] == '2822H'

    def test_MODBUS_slave_mode_error_has_specific_official_supplement(self):
        r = _rows(1112, 12)[0]['rules'][0]
        assert r['when']['configured_station_role'] == 'slave'
        assert r['effect']['error_code'] == '3600H'
        assert 'supplemental_source' not in r
        assert _rows(1112, 12)[0]['additional_sources'] == [{
            'manual_id': 'fx5_communication_j', 'manual_number': 'SH-082625ENG',
            'revision': 'J', 'pdf_pages': [865]}]

    def test_flash_limits_have_different_counter_epochs(self):
        short = _rows(1206, 29)[0]['rules'][0]
        life = _rows(1206, 30)[0]['rules'][0]
        assert (short['maximum'], short['counter_epoch']) == (25, 'power_on_or_CPU_reset')
        assert (life['maximum'], life['counter_epoch']) == (100000, 'module_lifetime')
        assert short['violation_effect']['error_code'] == '1080H'
        assert life['violation_effect']['error_code'] == '1931H'

    def test_group_station_guards_bind_complete_fields(self):
        r = _rules(1137, 'conditional_address_layout')[0]
        branches = r['branches'][0]['effect']['station_domain']['additional_branches']
        group = branches[0]
        assert group['when']['execution_selector']['subject']['offset'] == 0
        assert group['when']['execution_selector']['bit'] == 0
        assert group['when']['CPU_type_selector']['subject']['offset'] == 3
        assert group['when']['station_selector']['subject']['offset'] == 5
        assert group['effect']['group_number']['left']['offset'] == 5
        assert group['effect']['group_number']['right'] == 128

    def test_module_reported_hardware_errors_do_not_claim_emulation(self):
        for page, line in [(1181, 49), (1181, 52), (1183, 3)]:
            r = _rows(page, line)[0]['rules'][0]
            assert r['kind'] == 'diagnostic_event'
            assert r['root_cause_identified'] is False
            assert r['software_emulation'] == 'not_established'

    def test_servo_start_status_nonzero_uses_canonical_not_equal_and_projects(self):
        from knowledge.fact_corpus import project_definition
        from tools.fact_fx5_programming_residual import _fact
        review = _rows(1197, 4)[0]
        rule = review['rules'][0]
        assert rule['operator'] == '<>'
        assert rule['left'] == {'operand': 's', 'offset': 4,
                                'offset_unit': 'word', 'width_bits': 16}
        assert rule['right'] == 0
        # This is a phase-qualified error predicate, not a requirement that
        # status remain zero during the system-owned communication phase.
        def error_trigger(phase, status):
            return (phase == rule['when']['phase']
                    and rule['operator'] == '<>' and status != rule['right'])
        assert error_trigger('servo_communication_start', 1)
        assert error_trigger('servo_communication_start', 65535)
        assert not error_trigger('servo_communication_start', 0)
        assert not error_trigger('communication_in_progress', 1)
        value = {**review['value_updates'], 'rules': review['rules']}
        fact = _fact('servo_status_start', 'constraints.documented_restriction',
                     value, [], ['FX5U'], status='source_verified')
        projected, withheld = project_definition({'opcode': 'G.ABRST1', 'facts': [fact]})
        assert not withheld, withheld
        assert projected['facts'][0]['value']['rules'][0]['operator'] == '<>'

    def test_all_network_authored_values_survive_production_value_projection(self):
        from knowledge.fact_corpus import project_definition, typed_value
        from tools.fact_fx5_programming_residual import _fact
        projected_count = 0
        for identity, entry in REVIEWS.items():
            review = entry['review']
            value = {**review['value_updates'], 'rules': review['rules']}
            # Conflict records remain unpublished, but their technical
            # representation must still be well formed and inspectable.
            # Validate native authored objects before any serialization.
            self.assertFalse(_native_shape_issues(value), identity)
            typed_value(value)
            if review.get('status') == 'conflict':
                continue
            fact = _fact('network_projection', 'constraints.documented_restriction',
                         value, [], ['FX5U'], status='source_verified')
            projected, withheld = project_definition({'opcode': 'TEST_NETWORK', 'facts': [fact]})
            assert not withheld, (identity, withheld)
            assert len(projected['facts']) == 1, identity
            projected_count += 1
        assert projected_count == 212  # 207 relations and 5 navigation records.

    def test_native_review_objects_have_only_string_keys_and_finite_json_leaves(self):
        import math
        def walk(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertIsInstance(key, str, path)
                    walk(child, path + '.' + key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, path + '[' + str(index) + ']')
            else:
                self.assertIsInstance(value, (str, int, float, bool, type(None)), path)
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value), path)
        for identity, entry in REVIEWS.items():
            walk(entry['review'], str(identity))
        rule = _rows(1188, 19)[0]['rules'][0]
        self.assertEqual(rule['bit_fields'][0]['values'], {'0': 'clear', '1': 'hold'})
        self.assertEqual(rule['bit_fields'][2]['values'], {'0': 'clear', '1': 'hold'})

    def test_real_condition_specs_native_projection_before_serialization(self):
        import os
        from pathlib import Path
        from knowledge.fact_corpus import project_definition, typed_value
        from tools.fact_fx5_programming_residual import _fact
        import tools.fact_fx5_condition_review as compiler
        fixture_root = os.environ.get('FX5_NETWORK_PRIVATE_FIXTURES')
        if not fixture_root:
            self.skipTest('Set FX5_NETWORK_PRIVATE_FIXTURES to the private source fixture directory')
        root = Path(fixture_root)
        sections = json.loads((root / 'sections.json').read_text())
        ledger = json.loads((root / 'ledger.json').read_text())
        expected = {(row['section'], row['key']) for row in ledger}
        seen = set()
        statuses = {}
        original = compiler._review_auxiliary_clause
        def reviewed(kind, clause, **kwargs):
            return review_network_backlog_clause(kind, clause, **kwargs) or original(kind, clause, **kwargs)
        with patch.object(compiler, '_review_auxiliary_clause', reviewed):
            for section in sections:
                for spec in compiler.condition_specs(section, {}):
                    identity = (section['section_token'], spec['key'])
                    if identity not in expected:
                        continue
                    seen.add(identity)
                    statuses[spec['status']] = statuses.get(spec['status'], 0) + 1
                    value = spec['value']
                    self.assertFalse(_native_shape_issues(value), identity)
                    typed_value(value)  # Native compiler result, no dump/load normalization.
                    if spec['status'] == 'conflict':
                        continue
                    fact = _fact('network_native_source_projection',
                                 'constraints.documented_restriction', value, [],
                                 ['FX5U'], status=spec['status'])
                    projected, withheld = project_definition({'opcode': 'TEST_NETWORK', 'facts': [fact]})
                    self.assertFalse(withheld, (identity, withheld))
                    self.assertEqual(len(projected['facts']), 1, identity)
        self.assertEqual(seen, expected)
        # These cached primary-manual fixtures intentionally lack the supplemental
        # source catalog. ADPRW must remain unknown, rather than borrowing trust.
        self.assertEqual(statuses, {'source_verified': 211, 'conflict': 14,
                                    'declared_unverified': 1})

import unittest
from copy import deepcopy
from tools.fact_fx5_condition_review import _PWM_STOP_QUALIFICATION_CLAUSES
from tools.fact_fx5_pwm_stop_review import review_pwm_stop_clause, REVIEWED, EARLY_REVIEWED


class PWMStopReviewTest(unittest.TestCase):
    def fixture(self, key):
        page = int(key.rsplit('_p', 1)[1].split('_')[0])
        form, first, title = ('PWM', 813, '16 bit binary pulse width modulation') if page < 820 else ('DPWM', 820, '32 bit binary pulse width modulation')
        section = {'section': title, 'first_page': first,
                   'outline': 'PART 3 CPU MODULE INSTRUCTIONS > 8 APPLICATION INSTRUCTION',
                   'text': f'FX5S FX5UJ FX5U FX5UC ENO:={form}(EN,s1,s2,d); '
                           '[FX5S/FX5UJ CPU module] [FX5U/FX5UC CPU module and high-speed pulse input/output module]'}
        return {'section': section, 'block': {'page': page}, 'declared': {'s1', 's2', 'd'}}

    def review(self, key, **changes):
        kwargs = self.fixture(key)
        kwargs.update(changes)
        return review_pwm_stop_clause('precautions', _PWM_STOP_QUALIFICATION_CLAUSES[key], **kwargs)

    def test_exactly_twelve_individual_source_relations(self):
        hits = [self.review(key) for key in _PWM_STOP_QUALIFICATION_CLAUSES]
        self.assertEqual(sum(result is not None for result in hits), 12)
        self.assertEqual(len(REVIEWED), 8)
        self.assertEqual(len(EARLY_REVIEWED), 4)
        for result in filter(None, hits):
            self.assertTrue(result['full_clause_review'])
            self.assertEqual(result['value_updates']['context_binding'], 'resolved')
            self.assertEqual(result['value_updates']['core_execution_capability'], 'not_established')
            self.assertEqual(result['value_updates']['physical_stop_instant'], 'not_established')
            self.assertEqual(result['value_updates']['final_electrical_level'], 'not_established')

    def test_four_early_clauses_require_chinese_y_and_preserve_nominal_boundary(self):
        for key in ('condition_precautions_p817_r4_c1', 'condition_precautions_p818_r7_c0',
                    'condition_precautions_p824_r5_c1', 'condition_precautions_p825_r10_c4'):
            result = self.review(key)
            rule = result['rules'][0]
            self.assertEqual(rule['when'], {'phase_duration_us': {'operator': '>=', 'value': 5},
                                           'elapsed_current_phase_us': {'operator': '<=', 'value': 5}})
            self.assertEqual(rule['effect']['delay'], {'value': 5, 'unit': 'microseconds',
                'anchor': 'output_stop_command', 'interpretation': 'documented_nominal_not_physical_tolerance'})
            self.assertEqual(rule['composition']['branch_priority'], 'not_established')
            self.assertEqual(rule['composition']['multiple_applicable_rules'], 'require_consistent_consequence_or_unverified')
            declared = result['additional_sources'][0]
            self.assertEqual((declared['manual_id'], declared['manual_number'], declared['revision']),
                             ('fx5_programming_zh_y', 'JY997D58901', 'Y'))
            self.assertIn(1402, declared['pdf_pages'])

    def test_missing_or_changed_chinese_catalog_downgrades_only_early_clauses(self):
        from tools.fact_fx5_supplemental_sources import bind_condition_source_catalog
        for key in _PWM_STOP_QUALIFICATION_CLAUSES:
            result = self.review(key)
            if result is None:
                continue
            claim = {'status': 'source_verified', 'value': {'full_source_relation_complete': True}}
            if result.get('additional_sources'):
                claim['additional_sources'] = result['additional_sources']
            bind_condition_source_catalog([claim], {})
            self.assertEqual(claim['status'], 'declared_unverified' if result.get('additional_sources') else 'source_verified')
            if result.get('additional_sources'):
                self.assertFalse(claim['value']['full_source_relation_complete'])
                declared = result['additional_sources'][0]
                bad_catalog = {f"{declared['manual_id']}:{page}": [dict(declared, revision='J')]
                               for page in declared['pdf_pages']}
                changed = {'status': 'source_verified', 'value': {}, 'additional_sources': [declared]}
                bind_condition_source_catalog([changed], bad_catalog)
                self.assertEqual(changed['status'], 'declared_unverified')

    def test_early_changed_delay_anchor_or_guard_is_not_reviewed(self):
        key = 'condition_precautions_p817_r4_c1'
        clause = _PWM_STOP_QUALIFICATION_CLAUSES[key]
        for changed in (clause.replace('in 5 μs', 'in 6 μs'), clause.replace('from the stop command', 'from phase start'),
                        clause.replace('or less time', 'or more time')):
            self.assertIsNone(review_pwm_stop_clause('precautions', changed, **self.fixture(key)))

    def test_negative_conjunction_is_sufficient_only_with_local_exception(self):
        clause = 'When a negative value is specified for the ON time by (s1) and the period by (s2), an operation error occurs. (In 16-bit instruction PWM, no error occurs.)'
        result = review_pwm_stop_clause('precautions', clause, **self.fixture('condition_precautions_p824_r1_c0'))
        self.assertTrue(result['full_clause_review'])
        self.assertEqual(result['forms'], ['DPWM'])
        rule = result['rules'][0]
        self.assertEqual(rule['when'], {'all': [{'subject': {'operand': name}, 'operator': '<', 'value': 0} for name in ('s1','s2')]})
        self.assertEqual(rule['implication'], 'sufficient_only_not_necessary_or_exclusive')
        self.assertEqual(rule['effect'], {'operation_error': True})
        self.assertFalse(rule['local_form_exception']['error_from_this_negative_check'])
        self.assertTrue(rule['local_form_exception']['other_error_conditions_still_apply'])
        self.assertEqual(rule['local_form_exception']['whole_instruction_validity'], 'not_established')
        self.assertEqual(rule['local_form_exception']['input_representation_and_literal_admissibility'], 'not_established')
        self.assertEqual(rule['independent_error_domain']['logical_connective'], 'OR')
        self.assertEqual(result['additional_sources'][0]['pdf_pages'], [783,787,789,1402])
        from tools.fact_fx5_supplemental_sources import bind_condition_source_catalog
        claim = {'status': 'source_verified', 'value': {}, 'additional_sources': result['additional_sources']}
        bind_condition_source_catalog([claim], {})
        self.assertEqual(claim['status'], 'declared_unverified')
        for changed in (clause.replace(' and ', ' or '), clause.replace('no error occurs', 'an error occurs')):
            self.assertIsNone(review_pwm_stop_clause('precautions', changed, **self.fixture('condition_precautions_p824_r1_c0')))

    def test_negative_local_exception_cannot_relax_existing_admission(self):
        from plc.pwm_contract import validate_pwm_request
        from plc.instruction_definition import UnknownInstructionSemantics
        from test_pwm_contract import context
        for on, period in [(-1,20),(10,-1),(-1,-1)]:
            with self.assertRaises(ValueError):
                validate_pwm_request('DPWM','FX5U',on,period,'Y0',context())
        # Existing PWM admission remains unknown/rejected; a local note cannot
        # promote a zero period, negative constant, or raw hex bit pattern.
        for on, period in [(10,0),(-1,20),('K-1',20),('HFFFF',70000)]:
            with self.assertRaises((ValueError, UnknownInstructionSemantics)):
                validate_pwm_request('PWM','FX5U',on,period,'Y0',context())

    def test_phase_and_units_are_context_bound_not_assumed_microseconds(self):
        on = self.review('condition_precautions_p817_r4_c0')['rules'][0]
        off = self.review('condition_precautions_p818_r5_c0')['rules'][0]
        self.assertEqual(on['phase_duration']['value'], {'operand': 's1'})
        self.assertEqual(off['phase_duration']['value'], {'operator': '-', 'left': {'operand': 's2'}, 'right': {'operand': 's1'}})
        for rule in (on, off):
            self.assertEqual(rule['phase_duration']['unit_from'], 'PWM_channel_time_unit_configuration')
            self.assertEqual(rule['phase_duration']['unknown_configuration'], 'unverified')
            self.assertEqual(rule['phase_duration']['binding_time'], 'current_output_phase')
            self.assertEqual(rule['trigger']['driver_equivalences'], 'not_established')
            self.assertEqual(rule['effect']['stop_event'], 'current_phase_end')

    def test_late_guard_and_event_keep_inclusive_endpoint(self):
        r = self.review('condition_precautions_p817_r4_c3')['rules'][0]
        self.assertEqual(r['when'], {'phase_duration_us': {'operator': '>=', 'value': 5}, 'elapsed_current_phase_us': {'operator': '>=', 'value': 5}})
        self.assertEqual(r['effect'], {'stop_event': 'with_output_stop_command'})
        self.assertEqual(r['composition']['branch_priority'], 'not_established')
        self.assertEqual(r['composition']['multiple_applicable_rules'], 'require_consistent_consequence_or_unverified')

    def test_merged_table_is_retained(self):
        result = self.review('condition_precautions_p824_r5_c0')
        self.assertTrue(result['split_pwm_hardware_table'])
        self.assertEqual(len(result['split_pwm_hardware_rows']), 5)
        self.assertEqual(result['split_pwm_hardware_rows'][0]['models'], ['FX5S', 'FX5UJ'])

    def test_foreign_section_page_form_operands_and_models_rejected(self):
        key = 'condition_precautions_p817_r4_c0'
        kwargs = self.fixture(key)
        clause = _PWM_STOP_QUALIFICATION_CLAUSES[key]
        mutations = [lambda k: k['section'].update(section='other'),
                     lambda k: k['section'].update(first_page=820),
                     lambda k: k['section'].update(outline='OTHER >'),
                     lambda k: k['block'].update(page=818),
                     lambda k: k['declared'].remove('d'),
                     lambda k: k['section'].update(text=k['section']['text'].replace('ENO:=PWM(', 'ENO:=DPWM(')),
                     lambda k: k['section'].update(text='FX5S FX5UJ FX5UC ENO:=PWM(EN,s1,s2,d);')]
        for mutate in mutations:
            candidate = deepcopy(kwargs)
            mutate(candidate)
            self.assertIsNone(review_pwm_stop_clause('precautions', clause, **candidate))
        self.assertIsNone(review_pwm_stop_clause('error', clause, **kwargs))

    def test_changed_condition_or_consequence_does_not_match(self):
        key = 'condition_precautions_p817_r4_c0'
        clause = _PWM_STOP_QUALIFICATION_CLAUSES[key]
        for changed in (clause.replace('5 μs', '6 μs'), clause.replace('or less', 'or more'), clause + ' Additional limitation.'):
            self.assertIsNone(review_pwm_stop_clause('precautions', changed, **self.fixture(key)))

    def test_whitespace_micro_symbol_normalization_only(self):
        key = 'condition_precautions_p817_r4_c0'
        clause = _PWM_STOP_QUALIFICATION_CLAUSES[key].replace('μ', 'µ').replace(' ', '  ')
        self.assertIsNotNone(review_pwm_stop_clause('precautions', clause, **self.fixture(key)))

    def test_merged_table_whitespace_is_normalized_before_parse(self):
        key = 'condition_precautions_p824_r5_c0'
        clause = _PWM_STOP_QUALIFICATION_CLAUSES[key].replace(' ', '  ')
        result = review_pwm_stop_clause('precautions', clause, **self.fixture(key))
        self.assertEqual(len(result['split_pwm_hardware_rows']), 5)

    def test_merged_table_requires_both_cpu_headings(self):
        key = 'condition_precautions_p824_r5_c0'
        kwargs = self.fixture(key)
        kwargs['section']['text'] = kwargs['section']['text'].replace('[FX5S/FX5UJ CPU module]', '')
        self.assertIsNone(review_pwm_stop_clause('precautions', _PWM_STOP_QUALIFICATION_CLAUSES[key], **kwargs))

if __name__ == '__main__':
    unittest.main()

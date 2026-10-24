import unittest
from tools.fact_fx5_pwm_diagnostics import pwm_diagnostic_code_relations

class PWMTableTest(unittest.TestCase):
    def test_merged_codes_select_channel_not_cause(self):
        for form, page in [('PWM',819), ('DPWM',826)]:
            value = pwm_diagnostic_code_relations(form)
            self.assertEqual(value['source']['pdf_page'], page)
            self.assertEqual(value['configuration']['code'], '3600H')
            channels = value['channel_errors']
            self.assertEqual(len(channels['codes_by_channel']), 12)
            self.assertEqual(channels['codes_by_channel']['1'], '3611H')
            self.assertEqual(channels['codes_by_channel']['12'], '361CH')
            self.assertTrue(all('code' not in c for c in channels['causes']))
    def test_no_invented_effect_or_negative_connective(self):
        value = pwm_diagnostic_code_relations('DPWM')
        causes = {c['id']:c for c in value['channel_errors']['causes']}
        for name in ['pulse_count_SD_outside_range','on_time_or_period_less_than_one']:
            self.assertEqual(causes[name]['output_effect'],'not_stated')
        self.assertEqual(causes['on_time_exceeds_period']['output_effect'],'stops')
        unknown = value['channel_errors']['unresolved_causes'][0]
        self.assertEqual(unknown['logical_connective'],'not_established')
        self.assertEqual(unknown['status'],'declared_unverified')
        self.assertEqual(pwm_diagnostic_code_relations('PWM')['channel_errors']['unresolved_causes'],[])
    def test_unknown_form_and_fresh_result(self):
        with self.assertRaises(ValueError):
            pwm_diagnostic_code_relations('PLSY')
        value = pwm_diagnostic_code_relations('PWM')
        value['channel_errors']['codes_by_channel'].clear()
        self.assertEqual(len(pwm_diagnostic_code_relations('PWM')['channel_errors']['codes_by_channel']),12)

if __name__ == '__main__':
    unittest.main()

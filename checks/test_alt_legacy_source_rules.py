"""ALT's section form declarations and explicit old-CPU pulse exclusion."""
import unittest
from tools.fact_applied_review import (_structured_pulse_form_states,
    _structured_variant_family_value, _structured_variant_table, _native_parameter_facts)


class AltLegacySourceRules(unittest.TestCase):
    def record(self):
        return dict(manual_id='fxcpu_basic_applied_m', opcode='ALT',
            manual_models=['FX0','FX0N','FX0S','FX1NC','FX2C','FXU','FX3U'],
            text=('ALT 16 bits Continuous ALT(EN, d);\nALTP 16 bits Pulse ALTP(EN, d);\n'
                  'FX0S, FX0, FX0N PLCs do not support the command of pulse execution form.'))

    def test_pulse_caution_does_not_remove_continuous_or_other_models(self):
        r = self.record()
        forms = _structured_variant_table(r)
        self.assertEqual({f['opcode'] for f in forms},{'ALT','ALTP'})
        states = _structured_pulse_form_states(r,forms)
        self.assertEqual(states,{'ALTP':{'FX0':'unsupported','FX0N':'unsupported','FX0S':'unsupported'}})
        for variant in forms:
            value = _structured_variant_family_value(r, forms, ['ALT','ALTP'], variant)
            self.assertEqual(value['section_variants'], ['ALT','ALTP'])
            self.assertEqual(value['section_variant_count'],2)
            self.assertEqual(value['variant_model_exclusions'],{'ALTP':['FX0','FX0N','FX0S']})
            self.assertNotIn('ALT',value['variant_model_exclusions'])

    def test_missing_caution_other_family_and_width_do_not_inherit_exclusion(self):
        r=self.record()
        forms=_structured_variant_table(r)
        r['text']=r['text'].split('FX0S,')[0]
        self.assertEqual(_structured_pulse_form_states(r,forms),{})
        self.assertNotIn('variant_model_exclusions',_structured_variant_family_value(r,forms,['ALT','ALTP'],forms[0]))
        r=self.record();r['opcode']='OTHER'
        self.assertEqual(_structured_pulse_form_states(r,forms),{})
        r=self.record();r['manual_id']='other'
        self.assertEqual(_structured_pulse_form_states(r,forms),{})
        r=self.record();r['manual_models']=['FX1NC','FX3U']
        self.assertEqual(_structured_pulse_form_states(r,forms),{})
    def test_legacy_native_pulse_reads_and_writes_its_own_single_bit(self):
        from unittest.mock import patch
        from tools import fact_applied_review as producer
        record = dict(id=1, manual_id='jy992d88101e', manual_number='JY992D88101',
            revision='E', pdf_pages=[212], opcode='ALT',
            text='The status of the destination device (D) is alternated on every operation of the ALT instruction.',
            parameter_conventions={'pdf_pages':[121]},
            conditions=[{'context':'project_mode','equals':'native_ladder_instruction'}])
        row=dict(name='D', type='BOOL', direction='output', description='',
                 type_basis='own_operand_device_column', direction_basis='destination_symbol')
        variant=dict(opcode='ALTP',width_bits=16)
        with patch.object(producer,'_legacy_native_parameter_rows',return_value={'rows':[row],'order':['D']}), \
             patch.object(producer,'_legacy_float_parameter_profiles',return_value=[]):
            facts=_native_parameter_facts(record,variant,['FX1S','FX1N','FX2N','FX2NC'])
            typed=next(f for f in facts if f['dimension']=='parameters.typed_operands')
            p=typed['value']['operands'][0]
            self.assertEqual((p['name'],p['type'],p['direction'],p['role']),('D','BOOL','in_out','read_write'))
            self.assertEqual(p['range'],{'device_prefixes':['Y','M','S'],'index_modification':False})
            self.assertEqual(p['purpose'],'bit_device_whose_state_is_inverted_on_pulse_execution')
            self.assertEqual(typed['id'],'authored:applied:native_typed_operands@jy992d88101e')
            self.assertEqual(typed['scope']['languages'],['ladder'])
            self.assertEqual(typed['conditions'],record['conditions'])
            # A form name or destination symbol alone does not prove read/write.
            record['text']='ALTP has a destination device.'
            no_body=_native_parameter_facts(record,variant,['FX1S'])
            p=next(f for f in no_body if f['dimension']=='parameters.typed_operands')['value']['operands'][0]
            self.assertEqual(p['direction'],'output')
            self.assertNotIn('purpose',p)


if __name__=='__main__':
    unittest.main()

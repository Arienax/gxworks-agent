"""Keep a declared STRING domain separate from a sourced variable storage span."""
import copy
import unittest

from tools.inventory_instruction_semantics import _layout_present, _storage_present, _type_present


class StringStorageTests(unittest.TestCase):
    def test_native_storage_head_does_not_prove_conditional_payload_extent(self):
        parameter = {'type': 'ANY16', 'passing': 'device_reference',
            'word_device_layout': {'device_unit_bits': 16, 'word_count': 1, 'head_storage_only': True},
            'conditional_read_layout': {'selector': 'SM772',
                'OFF': {'general_exact_span_verified': False},
                'ON': {'word_count_operand': 'n', 'general_exact_span_verified': True}}}
        self.assertTrue(_type_present(parameter, 'native_ladder_instruction'))
        self.assertFalse(_layout_present(parameter))
        self.assertFalse(_storage_present(parameter, 'native_ladder_instruction'))

    def test_array_element_reference_keeps_n_element_span_separate_from_scalar_bit(self):
        parameter = {'data_type': {'kind': 'bool'}, 'argument_passing': 'array_element_reference',
            'array_element_reference': {'element_type': {'kind': 'bool'}, 'start': 'selected_array_element',
                'extent': {'parameter': 'n', 'unit': 'logical_elements',
                           'allowed_counts': [4, 8, 12, 16], 'constant_only': True}}}
        for interface in ('st_call', 'fbd_call', 'structured_ladder_block', 'native_ladder_instruction'):
            self.assertTrue(_type_present(parameter, interface))
            self.assertTrue(_storage_present(parameter, interface))
        without_extent = copy.deepcopy(parameter)
        del without_extent['array_element_reference']['extent']
        self.assertTrue(_type_present(without_extent, 'st_call'))
        self.assertFalse(_storage_present(without_extent, 'st_call'))
        for key in parameter['array_element_reference']['extent']:
            incomplete = copy.deepcopy(parameter)
            del incomplete['array_element_reference']['extent'][key]
            with self.subTest(missing=key):
                self.assertTrue(_type_present(incomplete, 'fbd_call'))
                self.assertFalse(_storage_present(incomplete, 'fbd_call'))
        malformed = copy.deepcopy(parameter)
        malformed['array_element_reference']['element_type'] = {'kind': 'int', 'bits': 16}
        self.assertFalse(_type_present(malformed, 'fbd_call'))
        self.assertFalse(_storage_present(malformed, 'fbd_call'))
        self.assertFalse(_type_present({'data_type': {'kind': 'bool', 'array': True}}, 'st_call'))

    def test_abstract_domains_are_types_but_not_storage_widths(self):
        for declaration in ('ANY', {'kind': 'ANY'}, {'flexible_type': 'ANY'}, 'ANY_BIT', 'ANY_NUM', {'kind': 'ANY_BIT', 'encoding': 'bcd'},
                            {'flexible_type': 'ANY_NUM'}):
            parameter = {'data_type': declaration}
            for interface in ('st_call', 'fbd_call', 'structured_ladder_block'):
                with self.subTest(declaration=declaration, interface=interface):
                    self.assertTrue(_type_present(parameter, interface))
                    self.assertFalse(_storage_present(parameter, interface))
            parameter['word_device_layout'] = {'word_count': 2}
            self.assertTrue(_storage_present(parameter, 'fbd_call'))
        for declaration in ('ANY16', 'ANY32', 'WORD', 'DWORD'):
            self.assertTrue(_storage_present({'data_type': declaration}, 'fbd_call'))

    def test_highest_any_type_requires_its_exact_token_and_still_rejects_native(self):
        for declaration in ('ANY', {'kind':'ANY'}, {'flexible_type':'ANY'}):
            for field in ('type','data_type'):
                parameter={field:declaration}
                for interface in ('st_call','fbd_call','structured_ladder_block'):
                    with self.subTest(field=field,declaration=declaration,interface=interface):
                        self.assertTrue(_type_present(parameter,interface))
                        self.assertFalse(_storage_present(parameter,interface))
                self.assertFalse(_type_present(parameter,'native_ladder_instruction'))
                self.assertFalse(_storage_present(parameter,'native_ladder_instruction'))
        for typo in ('ANYX','ANYY','AN_Y','any','UNSPECIFIED','',{}, {'flexible_type':'ANYX'}):
            for interface in ('st_call','fbd_call','structured_ladder_block','native_ladder_instruction'):
                with self.subTest(typo=typo,interface=interface):
                    self.assertFalse(_type_present({'data_type':typo},interface))
        self.assertFalse(_type_present({'data_type':'ANY','type_completeness':'partial'},'st_call'))

    def test_existing_raw_sel_any_declarations_do_not_establish_storage_or_native_types(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from tools.inventory_instruction_semantics import inspect_owner
        for opcode in ('SEL','SEL_E'):
            entry=registry.definition_entry(opcode,cpu='FX3U')
            self.assertIsNotNone(entry)
            selected=[fact for fact in entry['facts'] if fact['id'].startswith('structured:'+opcode.lower()+':')
                      and fact['dimension']=='parameters.typed_operands']
            self.assertTrue(selected)
            for fact in selected:
                parameters=[p for p in fact['value']['operands'] if p.get('data_type')=={'flexible_type':'ANY'}]
                self.assertEqual([p['name'] for p in parameters],['_IN0','_IN1','Output_label'])
                for parameter in parameters:
                    for interface in ('st_call','fbd_call','structured_ladder_block'):
                        self.assertTrue(_type_present(parameter,interface))
                        self.assertFalse(_storage_present(parameter,interface,sources=fact['sources']))
                    self.assertFalse(_type_present(parameter,'native_ladder_instruction'))
            rows,_=inspect_owner(entry,registry,composed_facts=entry['facts'])
            for row in rows:
                if row['interface'] in ('st_call','fbd_call','structured_ladder_block'):
                    with self.subTest(opcode=opcode,interface=row['interface']):
                        self.assertEqual(row['states']['types'],'answered')
                        self.assertEqual(row['states']['storage'],'missing')

    def test_declared_string_without_storage_does_not_close_storage(self):
        for declaration in ('STRING', {'kind': 'string', 'encoding': 'binary'}):
            parameter = {'data_type': declaration}
            for interface in ('st_call', 'fbd_call', 'structured_ladder_block'):
                with self.subTest(declaration=declaration, interface=interface):
                    self.assertTrue(_type_present(parameter, interface))
                    self.assertFalse(_storage_present(parameter, interface))
            self.assertFalse(_type_present(parameter, 'native_ladder_instruction'))

    def test_source_declared_null_terminated_span_is_variable_not_fixed_width(self):
        parameter = {'data_type': {'kind': 'string', 'encoding': 'binary'},
            'string_layout': {'unit_bits': 8, 'terminator': 0,
                             'extent': 'through_first_terminator', 'within_device_range': True}}
        for interface in ('st_call', 'fbd_call', 'structured_ladder_block', 'native_ladder_instruction'):
            with self.subTest(interface=interface):
                self.assertTrue(_type_present(parameter, interface))
                self.assertTrue(_storage_present(parameter, interface))
        self.assertNotIn('word_count', parameter['string_layout'])
        for key in parameter['string_layout']:
            incomplete = copy.deepcopy(parameter)
            del incomplete['string_layout'][key]
            with self.subTest(missing=key):
                self.assertFalse(_layout_present(incomplete))

    def test_partial_or_invalid_string_type_is_not_a_known_type(self):
        for parameter in ({'type': 'STRING', 'type_completeness': 'partial'},
                          {'type': {'kind': 'string', 'bits': 16}},
                          {'type': 'STRING_MAYBE'}):
            with self.subTest(parameter=parameter):
                self.assertFalse(_type_present(parameter, 'st_call'))

    def test_comparison_short_circuit_layout_does_not_require_full_null_prescan(self):
        parameter = {'data_type': 'STRING', 'string_layout': {
            'unit_bits': 8, 'terminator': 0, 'extent': 'through_first_terminator',
            'device_range_check': 'as_compared',
            'short_circuit_extent': 'first_difference_or_both_terminators'}}
        self.assertTrue(_storage_present(parameter, 'native_ladder_instruction'))
        self.assertNotIn('within_device_range', parameter['string_layout'])
        for key in parameter['string_layout']:
            incomplete = copy.deepcopy(parameter)
            del incomplete['string_layout'][key]
            with self.subTest(missing=key):
                self.assertFalse(_storage_present(incomplete, 'native_ladder_instruction'))


class AbsConditionalStorageTests(unittest.TestCase):
    SOURCES = [
        {'id': 'abs-own', 'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [160, 161]},
        {'id': 'label-types', 'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [33]},
    ]

    def record(self):
        from tools import fact_structured_behavior_review as review
        record = next(r for r in review._all_records() if r['op'] == 'ABS')
        return dict(record, numeric_storage_sources=copy.deepcopy(self.SOURCES))

    def test_abs_plain_and_enabled_keep_numeric_domain_and_all_interface_spans(self):
        from tools import fact_structured_behavior_review as review
        from tools.inventory_instruction_semantics import _type_bits
        from shared.model_context import technical_value, model_knowledge_value
        record = self.record()
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                operands = review._operand_facts(record, enabled)
                numeric = [p for p in operands if p['data_type'].get('flexible_type') == 'ANY_NUM']
                self.assertEqual([p['name'] for p in numeric], ['_IN', 'Output_label'])
                for parameter in numeric:
                    self.assertEqual(parameter['data_type'], {'flexible_type': 'ANY_NUM'})
                    self.assertIsNone(_type_bits(parameter))
                    self.assertEqual(parameter['type_alternatives'], ['INT', 'DINT', 'REAL'])
                    spans = parameter['storage_by_type']
                    self.assertEqual(spans['selected_by'], 'actual_parameter_type')
                    self.assertEqual([(b['type'], b['occupied_bit_count']) for b in spans['branches']],
                                     [('INT', 16), ('DINT', 32), ('REAL', 32)])
                    self.assertTrue(all(b['sources'] == self.SOURCES for b in spans['branches']))
                    self.assertNotIn('occupied_bit_count', parameter)
                    for interface in ('st_call', 'fbd_call', 'structured_ladder_block'):
                        self.assertTrue(_storage_present(parameter, interface, sources=self.SOURCES))
                    self.assertFalse(_storage_present(parameter, 'native_ladder_instruction', sources=self.SOURCES))
                    displayed = model_knowledge_value(technical_value(parameter))
                    self.assertEqual(displayed['storage_by_type']['selected_by'], 'actual_parameter_type')
                    self.assertEqual([b['occupied_bit_count'] for b in displayed['storage_by_type']['branches']],
                                     [16, 32, 32])
                pins = [p for p in review._pins(record, enabled) if p['data_type'].get('flexible_type') == 'ANY_NUM']
                st = [p for p in review._st_parameters(record, enabled) if p['type'].get('flexible_type') == 'ANY_NUM']
                self.assertEqual(len(pins), 2)
                self.assertEqual(len(st), 2 if enabled else 1)
                for parameter in pins + st:
                    self.assertEqual(parameter['storage_by_type'], numeric[0]['storage_by_type'])
                returned = review._st_returns(record, enabled)
                shape = review._return_shape(record, enabled)
                if enabled:
                    self.assertEqual(returned['role'], 'ENO_execution_status')
                    self.assertNotIn('storage_by_type', returned)
                    self.assertNotIn('storage_by_type', shape)
                else:
                    self.assertEqual(returned['storage_by_type'], numeric[1]['storage_by_type'])
                    self.assertEqual(shape['storage_by_type'], numeric[1]['storage_by_type'])
                self.assertEqual(record['rule']['result_type'], 'same_as_source')

    def test_numeric_dictionary_domain_needs_each_branch_width_and_matching_sources(self):
        from tools import fact_structured_behavior_review as review
        original = review._operand_facts(self.record(), False)[0]
        original['data_type'] = {'kind': 'ANY_NUM'}
        self.assertTrue(_storage_present(original, 'st_call', sources=self.SOURCES))
        for case in ('no_layout', 'no_members', 'missing_int', 'missing_real_branch', 'no_width',
                     'wrong_real_width', 'missing_source', 'unrelated_source', 'wrong_page',
                     'bit_domain', 'mapping_member', 'extra_branch_field'):
            with self.subTest(case=case):
                parameter = copy.deepcopy(original)
                branches = parameter['storage_by_type']['branches']
                if case == 'no_layout': parameter.pop('storage_by_type')
                if case == 'no_members': parameter.pop('type_alternatives')
                if case == 'missing_int': parameter['type_alternatives'].remove('INT')
                if case == 'missing_real_branch': branches.pop(2)
                if case == 'no_width': branches[1].pop('occupied_bit_count')
                if case == 'wrong_real_width': branches[2]['occupied_bit_count'] = 64
                if case == 'missing_source': branches[0]['sources'] = []
                if case == 'unrelated_source': branches[1]['sources'][0]['manual_id'] = 'unrelated'
                if case == 'wrong_page': branches[2]['sources'][1]['pdf_pages'] = [34]
                if case == 'bit_domain': parameter['data_type'] = {'kind': 'ANY_BIT'}
                if case == 'mapping_member': parameter['type_alternatives'][0] = {'kind': 'int', 'bits': 16}
                if case == 'extra_branch_field': branches[1]['word_count'] = 2
                self.assertFalse(_storage_present(parameter, 'st_call', sources=self.SOURCES))
        self.assertFalse(_storage_present(original, 'st_call'))

    def test_abs_own_operation_members_and_scalar_width_witnesses_are_required(self):
        from tools import fact_structured_behavior_review as review
        record = self.record()
        for witness in (
            'absolute value of word [signed]/double word [signed]/float (single precision) data',
            'using the data type same as the data stored',
        ):
            self.assertIn(witness, record['witness'])
        text = ' '.join(review.ABS_STORAGE_WITNESSES)
        review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text}, review.ABS_STORAGE_WITNESSES, 'ABS widths')
        for witness in review.ABS_STORAGE_WITNESSES:
            with self.subTest(witness=witness), self.assertRaises(ValueError):
                review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text.replace(witness, '')},
                               review.ABS_STORAGE_WITNESSES, 'ABS widths')


class ArithmeticConditionalStorageTests(unittest.TestCase):
    def records(self):
        from tools import fact_structured_behavior_review as review
        for original in review._all_records():
            if original['section'] in review.ARITHMETIC_STORAGE_SECTIONS:
                sources = [
                    {'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': list(original['pages'])},
                    {'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [33]},
                ]
                yield dict(original, numeric_storage_sources=sources), sources

    def test_own_function_and_operator_domains_cover_all_parameters_and_returns(self):
        from tools import fact_structured_behavior_review as review
        from shared.model_context import model_knowledge_value, technical_value
        for record, sources in self.records():
            expected = ['INT', 'DINT'] if record['op'] == 'MOD' else ['INT', 'DINT', 'REAL']
            for _form, enabled in review._record_forms(record):
                with self.subTest(section=record['section'], enabled=enabled):
                    collections = (review._operand_facts(record, enabled), review._pins(record, enabled),
                                   review._st_parameters(record, enabled))
                    for parameters in collections:
                        numeric = [p for p in parameters if p.get('data_type', p.get('type', {})).get(
                            'flexible_type') in ('ANY_NUM', 'ANY_INT')]
                        self.assertTrue(numeric)
                        for parameter in numeric:
                            self.assertEqual(parameter['type_alternatives'], expected)
                            self.assertEqual([b['type'] for b in parameter['storage_by_type']['branches']], expected)
                            self.assertTrue(_storage_present(parameter, 'st_call', sources=sources))
                            self.assertFalse(_storage_present(parameter, 'native_ladder_instruction', sources=sources))
                            displayed = model_knowledge_value(technical_value(parameter))
                            self.assertEqual(displayed['storage_by_type']['selected_by'], 'actual_parameter_type')
                    returned, shape = review._st_returns(record, enabled), review._return_shape(record, enabled)
                    if enabled:
                        self.assertEqual(returned['role'], 'ENO_execution_status')
                        self.assertNotIn('storage_by_type', returned)
                        self.assertNotIn('storage_by_type', shape)
                    else:
                        self.assertEqual(returned['type_alternatives'], expected)
                        self.assertEqual(shape['type_alternatives'], expected)

    def test_plain_operator_equivalence_preserves_every_numeric_value_span(self):
        from tools import fact_structured_behavior_review as review
        records = {r['section']: (r, sources) for r, sources in self.records()}
        for (opcode, section), partner_section in review.PLAIN_FORM_OPERATOR_SECTIONS.items():
            if section not in records:
                continue
            record, sources = records[section]
            partner, _ = records[partner_section]
            row = {'id': 1, 'manual_id': 'fxcpu_application_k', 'manual_number': 'JY997D34801', 'revision': 'K',
                   'pdf_page': partner['pages'][0], 'pdf_page_end': partner['pages'][-1]}
            facts = review._plain_via_operator_facts(record, partner, row, 'FX3U', sources,
                                                    None, 'source_verified', row)
            signature = facts[0]
            with self.subTest(opcode=opcode):
                for parameter in signature['value']['parameters'] + [signature['value']['returns']]:
                    self.assertEqual(parameter['type_alternatives'], ['INT', 'DINT', 'REAL'])
                    self.assertTrue(_storage_present(parameter, 'st_call', sources=signature['sources']))

    def test_integer_domain_rejects_partial_widths_sources_and_unrelated_abstract_types(self):
        from tools import fact_structured_behavior_review as review
        record, sources = next((r, s) for r, s in self.records() if r['section'] == '7.5')
        original = review._operand_facts(record, False)[0]
        for case in ('missing_branch', 'missing_member', 'wrong_width', 'missing_source',
                     'unbound_source', 'real_branch', 'mapping_member', 'other_abstract'):
            with self.subTest(case=case):
                p = copy.deepcopy(original)
                branches = p['storage_by_type']['branches']
                if case == 'missing_branch': branches.pop()
                if case == 'missing_member': p['type_alternatives'].pop()
                if case == 'wrong_width': branches[1]['occupied_bit_count'] = 16
                if case == 'missing_source': branches[0]['sources'] = []
                if case == 'unbound_source': branches[0]['sources'][0]['pdf_pages'] = [173]
                if case == 'real_branch':
                    p['type_alternatives'].append('REAL')
                    branches.append(dict(type='REAL', occupied_bit_count=32, sources=sources))
                if case == 'mapping_member': p['type_alternatives'][0] = {'kind': 'int', 'bits': 16}
                if case == 'other_abstract': p['data_type'] = {'flexible_type': 'ANY_REAL'}
                self.assertFalse(_storage_present(p, 'st_call', sources=sources))

    def test_only_own_enumerated_sections_receive_numeric_storage(self):
        from tools import fact_structured_behavior_review as review
        for record, _sources in self.records():
            witnesses = review._arithmetic_storage_witnesses(record)
            text = ' '.join(witnesses)
            review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text}, witnesses, record['section'])
            for phrase in witnesses:
                with self.subTest(section=record['section'], missing=phrase), self.assertRaises(ValueError):
                    review._verify({'manual_id': review.FUNCTION_MANUAL, 'text': text.replace(phrase, '')},
                                   witnesses, record['section'])
        for op in ('EXPT', 'SEL'):
            record = next(r for r in review._all_records() if r['op'] == op)
            for _name, token, _marker, _role in record['inputs'] + record['outputs']:
                self.assertNotIn('storage_by_type', review._parameter_qualifiers(record, token))


class ExptConditionalStorageTests(unittest.TestCase):
    def record(self):
        from tools import fact_structured_behavior_review as review
        references = [
            {'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [173]},
            {'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [34]},
            {'manual_id': 'structured_fundamentals_o', 'revision': 'O', 'pdf_pages': [34]},
            {'manual_id': 'structured_fundamentals_o', 'revision': 'O', 'pdf_pages': [35]},
        ]
        original = next(r for r in review._all_records() if r['op'] == 'EXPT')
        return dict(original, numeric_storage_sources=references,
                    expt_elementary_reference=references[2]), references

    def test_full_exponent_domain_keeps_cross_manual_support_separate_from_own_expt_scope(self):
        from tools import fact_structured_behavior_review as review
        from shared.model_context import technical_value, model_knowledge_value
        record, sources = self.record()
        for enabled in (False, True):
            operands = review._operand_facts(record, enabled)
            exponent = next(p for p in operands if p['name'] == 'In2')
            self.assertEqual(exponent['data_type'], {'flexible_type': 'ANY_NUM'})
            self.assertEqual(exponent['type_alternatives'], ['INT', 'DINT', 'REAL', 'LREAL'])
            self.assertEqual([(b['type'], b['occupied_bit_count']) for b in exponent['storage_by_type']['branches']],
                             [('INT',16), ('DINT',32), ('REAL',32), ('LREAL',64)])
            for interface in ('st_call','fbd_call','structured_ladder_block'):
                self.assertTrue(_storage_present(exponent, interface, sources=sources))
            self.assertFalse(_storage_present(exponent, 'native_ladder_instruction', sources=sources))
            for item in operands:
                if item['name'] != 'In2': self.assertNotIn('storage_by_type', item)
            self.assertNotIn('storage_by_type', review._st_returns(record, enabled))
            self.assertNotIn('storage_by_type', review._return_shape(record, enabled))
            for parameters in (review._st_parameters(record, enabled), review._pins(record, enabled)):
                item = next(p for p in parameters if p.get('name',p.get('pin'))=='In2')
                self.assertEqual(item['storage_by_type'], exponent['storage_by_type'])
            projected = model_knowledge_value(technical_value(exponent))
            for original, visible in zip(exponent['storage_by_type']['branches'][2:],
                                         projected['storage_by_type']['branches'][2:]):
                support = visible['type_support_scope']
                for key in ('documented_models','documented_cpu_families','unlisted_targets','instruction_availability'):
                    self.assertEqual(support.get(key, [] if not original['type_support_scope'][key] else None),
                                     original['type_support_scope'][key])
            self.assertEqual(record['rule'], {'rule':'exponentiation','base_precision':'single'})

    def test_missing_or_forged_type_support_scope_cannot_close_storage(self):
        from tools import fact_structured_behavior_review as review
        record, sources = self.record()
        exponent = next(p for p in review._operand_facts(record, False) if p['name']=='In2')
        for case in ('no_lreal','no_real_scope','no_lreal_scope','wrong_fx_lreal','wrong_real_models',
                     'wrong_families','wrong_lreal_width','wrong_real_width','source_missing','source_wrong_page','scope_source_missing',
                     'unlisted_approved','instruction_approval','missing_scope_field'):
            with self.subTest(case=case):
                item=copy.deepcopy(exponent); branches=item['storage_by_type']['branches']
                real,lreal=branches[2],branches[3]
                if case=='no_lreal': branches.pop()
                if case=='no_real_scope': real.pop('type_support_scope')
                if case=='no_lreal_scope': lreal.pop('type_support_scope')
                if case=='wrong_fx_lreal': lreal['type_support_scope']['documented_models']=['FX3U']
                if case=='wrong_real_models': real['type_support_scope']['documented_models'].append('FX0')
                if case=='wrong_families': lreal['type_support_scope']['documented_cpu_families']=['fxcpu']
                if case=='wrong_lreal_width': lreal['occupied_bit_count']=32
                if case=='wrong_real_width': real['occupied_bit_count']=64
                if case=='source_missing': lreal['sources'].pop()
                if case=='source_wrong_page': lreal['sources'][2]['pdf_pages']=[33]
                if case=='scope_source_missing': lreal['type_support_scope']['sources']=[]
                if case=='unlisted_approved': lreal['type_support_scope']['unlisted_targets']='supported'
                if case=='instruction_approval': real['type_support_scope']['instruction_availability']='supported'
                if case=='missing_scope_field': real['type_support_scope'].pop('documented_models')
                self.assertFalse(_storage_present(item,'st_call',sources=sources))
        self.assertFalse(_storage_present(exponent,'st_call',sources=[]))

    def test_width_and_cpu_footnote_witnesses_are_required_together(self):
        from tools import fact_structured_behavior_review as review
        witnesses=review.EXPT_ELEMENTARY_SOURCE['witness']
        text=' '.join(witnesses)
        for phrase in witnesses:
            with self.subTest(missing=phrase),self.assertRaises(ValueError):
                review._verify({'manual_id':'structured_fundamentals_o','text':text.replace(phrase,'')},
                               witnesses,'EXPT elementary widths and support')


if __name__ == '__main__':
    unittest.main()

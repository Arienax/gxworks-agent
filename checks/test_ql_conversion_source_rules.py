"""Focused source-clause rules; source binding alone cannot imply semantics."""
from types import SimpleNamespace
import unittest
from tools.fact_ql_behavior_review import (_n_conversion_source_specs, _n_complete_error_table,
    _retain_uncovered_grouped_error, _build_n_behavior_detail_specs, _n_bitarr_branch,
    _n_bitarr_output_branch, _n_bitarr_array_element_reference, _n_bitarr_st_call_shape,
    _n_parameter_specs, _n_refined_pin, _publishable_documented_call, _guard_specs, _materialize,
    _n_time_add_applicability_header, _LEFT_ST_CALL_RULES)
from tools.fact_ql_behavior_review import (
    _n_bcd_result_type_alternative_specs, _n_apply_bcd_result_type_alternatives,
    _n_bcd_input_type_branch_specs, _n_apply_bcd_input_type_branches, _undetermined_fields,
)


def section(symbol, text):
    return SimpleNamespace(manual='sh080784engn', symbols=[symbol],
        flat_rows=lambda: [('synthetic', 1, text)], bind=lambda witness: [('synthetic', 1)])


def time_section(symbol, *, declaration=True, call=None, include_type_clause=True,
                 include_error_code=True, include_nonzero_clause=True):
    base = symbol[:-2] if symbol.endswith('_E') else symbol
    operation = {
        'ADD_TIME': ('Addition', (168, 169), 'addition', 'TIME',
                     'g_time3 := ADD_TIME(g_time1, g_time2);'),
        'SUB_TIME': ('Subtraction', (170, 171), 'subtraction', 'TIME',
                     'g_time3 := SUB_TIME(g_time1, g_time2);'),
        'MUL_TIME': ('Multiplication', (172, 173), 'multiplication', 'ANY_NUM',
                     'g_time2 := MUL_TIME(g_time1, g_int1);'),
        'DIV_TIME': ('Division', (174, 175), 'division', 'ANY_NUM',
                     'g_time2 := DIV_TIME(g_time1, g_int1);'),
    }[base]
    title, pages, verb, second_type, plain_call = operation
    enabled = symbol.endswith('_E')
    formal_rows = ([('EN', 'BOOL', 'input'), ('s1', 'TIME', 'input'),
                    ('s2', second_type, 'input'), ('ENO', 'BOOL', 'output'), ('d', 'TIME', 'output')]
                   if enabled else [('s1', 'TIME', 'input'), ('s2', second_type, 'input'),
                                    ('d', 'TIME', 'output')])
    rows = []
    for name, kind, direction in formal_rows:
        witness = f'{name} {direction.title()} {kind}'
        rows.append({'operand': {'name': name, 'type': kind, 'direction': direction},
                     'source_name': name, 'witness': witness})
    start_page = 'Performs ' + verb + ' on time type data. ' + ' '.join(row['witness'] for row in rows)
    if base in {'MUL_TIME', 'DIV_TIME'} and include_type_clause:
        start_page += (' The value to be input to (s2) is word (signed), double word (signed), '
                       'single-precision real or double-precision real type data.')
    if base == 'DIV_TIME':
        if include_nonzero_clause:
            start_page += ' The value to be input to (s2) must be other than 0.'
        start_page += ' The value to be input to (s2) is 0.'
        start_page += ' Remainder is rounded down.'
    end_page = []
    if base == 'DIV_TIME' and include_error_code:
        end_page.append('4100 The value to be input to (s2) is 0. (Division by 0)')
    execution_rows = []
    if base == 'DIV_TIME' and enabled:
        enabled_witness = 'TRUE TRUE Operation output value'
        disabled_witness = 'FALSE FALSE Undefined value'
        end_page.extend([enabled_witness, disabled_witness])
        execution_rows = [
            {'EN': True, 'ENO': True, 'output': 'Operation output value',
             'witness': enabled_witness, 'page': 175},
            {'EN': False, 'ENO': False, 'output': 'Undefined value',
             'witness': disabled_witness, 'page': 175},
        ]
    if declaration:
        end_page.append(f'Function with EN/ENO ({base}_E)' if enabled
                        else f'Function without EN/ENO ({base})')
    if call is None:
        result_name = 'g_time3' if second_type == 'TIME' else 'g_time2'
        second_argument = 'g_time2' if second_type == 'TIME' else 'g_int1'
        call = (f'g_bool3 := {base}_E(g_bool1, g_time1, {second_argument}, {result_name});'
                if enabled else plain_call)
    if call:
        end_page.append(call)
    text = start_page + '\n' + '\n'.join(end_page)
    chunks = [('time-section', pages[0], start_page), ('time-section', pages[1], '\n'.join(end_page))]
    def bind(witness):
        needle = ' '.join((witness or '').split())
        return [(chunk_id, page) for chunk_id, page, body in chunks
                if needle and needle in ' '.join(body.split())]
    fake = SimpleNamespace(manual='sh080784engn', revision='N', manual_number='SH080784ENG',
        symbols=[base, f'{base}_E'], chapter='5 APPLICATION FUNCTIONS > 5.8 Functions of Time Data Type',
        title=title, pages=list(pages), page_pairs=[pages], text=text, lines=text.splitlines(), bind=bind, _flat=chunks,
        _n_argument_contracts=[{'rows': rows, 'issues': []}], _n_execution_rows=execution_rows)
    return fake


class QLConversionSourceRules(unittest.TestCase):
    def test_str_to_bcd_result_types_require_both_own_page_97_branches(self):
        heading_word = 'STR_TO_BCD(_E) (when word (unsigned)/16-bit string is specified for (d))'
        heading_dword = 'STR_TO_BCD(_E) (when double word (unsigned)/32-bit string is specified for (d))'
        for symbol, enabled in (('STR_TO_BCD', False), ('STR_TO_BCD_E', True)):
            with self.subTest(symbol=symbol):
                word_call = ('g_word1 := STR_TO_BCD(g_string1);' if not enabled else
                             'g_bool3 := STR_TO_BCD_E(g_bool1, g_string1, g_word1);')
                dword_call = ('g_dword1:= STR_TO_BCD(g_string1);' if not enabled else
                              'g_bool3:= STR_TO_BCD_E(g_bool1, g_string1, g_dword1);')
                rows = [('source-95', 95, 'd Output ANY_BIT'),
                        ('source-97', 97, ' '.join((heading_word, word_call, heading_dword, dword_call)))]
                fake = SimpleNamespace(manual='sh080784engn', revision='N', symbols=[symbol],
                    pages=[95, 97], flat_rows=lambda: rows, _geometry_flat=[],
                    bind=lambda witness: [(row[0], row[1]) for row in rows if witness in row[2]])
                source_specs = _n_bcd_result_type_alternative_specs(fake, symbol)
                self.assertEqual(len(source_specs), 1)
                branch = source_specs[0]
                self.assertEqual(branch['key'], 'n_conversion_bcd_result_type_alternatives')
                self.assertEqual(branch['dimension'], 'effects.value_relation')
                self.assertEqual(branch['_required_source_pages'], [97])
                self.assertEqual(branch['value']['output_type_alternatives'], ['WORD', 'DWORD'])
                self.assertEqual([row['width_bits'] for row in branch['value']['source_branches']], [16, 32])

                signature_value = {
                    'returns': 'BOOL' if enabled else 'ANY_BIT',
                    'parameters': ([{'name': 'EN', 'type': 'BOOL'}, {'name': 's', 'type': 'STRING'},
                                    {'name': 'd', 'type': 'ANY_BIT'}] if enabled else
                                   [{'name': 's', 'type': 'STRING'}]),
                }
                signature = {'dimension': 'parameters.st_signature', 'languages': ('st',),
                             'value': signature_value}
                typed = {'dimension': 'parameters.typed_operands', 'languages': ('st', 'fbd'),
                         'value': {'operands': [{'name': 's', 'type': 'STRING'},
                                                {'name': 'd', 'type': 'ANY_BIT'}]}}
                _n_apply_bcd_result_type_alternatives(fake, symbol, source_specs, [signature, typed])
                self.assertEqual(typed['value']['operands'][1]['type_alternatives'], ['WORD', 'DWORD'])
                self.assertIn('authored:ql:n_conversion_bcd_result_type_alternatives:effects.value_relation',
                              typed['depends_on'])
                self.assertIn('authored:ql:n_conversion_bcd_result_type_alternatives:effects.value_relation',
                              signature['depends_on'])
                if enabled:
                    self.assertEqual(signature_value['returns'], 'BOOL')
                    self.assertEqual(signature_value['parameters'][-1]['type_alternatives'], ['WORD', 'DWORD'])
                else:
                    self.assertEqual(signature_value['returns'], 'ANY_BIT')
                    self.assertEqual(signature_value['return_type_alternatives'], ['WORD', 'DWORD'])

                missing_dword = SimpleNamespace(**{**fake.__dict__, 'flat_rows': lambda: [
                    rows[0], ('source-97', 97, ' '.join((heading_word, word_call, heading_dword)))
                ]})
                self.assertEqual(_n_bcd_result_type_alternative_specs(missing_dword, symbol), [])

    def test_bcd_to_str_records_only_own_page_verified_input_branches(self):
        source_text = (
            's(_BCD) Input ANY_BIT '
            'When word (unsigned)/16-bit string type is specified for (s). '
            'When double word (unsigned)/32-bit string type is specified for (s). '
            'Word (unsigned)/16-bit string type, double word (unsigned)/32-bit string type '
            'data can be specified for (d). Bit type cannot be specified.'
        )
        rows = [('n-bcd-to-str-p101', 101, source_text)]

        def bind(witness):
            return [(chunk_id, page) for chunk_id, page, text in rows if witness in text]

        fake = SimpleNamespace(
            manual='sh080784engn', revision='N', symbols=['BCD_TO_STR', 'BCD_TO_STR_E'],
            flat_rows=lambda: rows, _geometry_flat=[], bind=bind,
        )
        branch_fact_id = (
            'authored:ql:n_conversion_bcd_input_type_branches:effects.value_relation'
        )
        for symbol, enabled in (('BCD_TO_STR', False), ('BCD_TO_STR_E', True)):
            with self.subTest(symbol=symbol):
                branch_specs = _n_bcd_input_type_branch_specs(fake, symbol)
                self.assertEqual(len(branch_specs), 1)
                branch = branch_specs[0]
                self.assertEqual(branch['_required_source_pages'], [101])
                self.assertEqual(branch['value']['declared_parameter_type'], 'ANY_BIT')
                self.assertEqual(branch['value']['source_verified_type_branches'], ['WORD', 'DWORD'])
                self.assertFalse(branch['value']['branch_set_is_exhaustive'])
                self.assertEqual(branch['value']['source_wording_conflict'], {
                    'source_clause_parameter': 'd',
                    'table_parameter_direction': 'output',
                    'table_parameter_type': 'STRING',
                    'source_clause_types': ['WORD', 'DWORD'],
                    'source_clause_excludes': 'BIT',
                    'disposition': 'preserve_original_direction_without_retargeting_to_s',
                })
                self.assertEqual(_undetermined_fields(branch['value']), [])

                source = {'name': 's', 'symbol': 's', 'type': 'ANY_BIT',
                          'direction': 'input', 'role': 'source'}
                signature_value = {
                    'name': symbol,
                    'parameters': ([{'name': 'EN', 'symbol': 'EN', 'type': 'BOOL',
                                     'direction': 'input', 'role': 'enable'}, dict(source),
                                    {'name': 'd', 'symbol': 'd', 'type': 'STRING',
                                     'direction': 'output', 'role': 'result'}]
                                   if enabled else [dict(source)]),
                    'returns': 'BOOL' if enabled else 'STRING',
                    'enable': enabled, 'available': True, 'call_kind': 'function',
                    'instance_required': False,
                }
                signature = {'dimension': 'parameters.st_signature', 'languages': ('st',),
                             'value': signature_value, 'depends_on': []}
                typed = {'dimension': 'parameters.typed_operands', 'languages': ('st', 'fbd'),
                         'value': {'operands': [dict(source)]}, 'depends_on': []}
                typed_ladder = {'dimension': 'parameters.typed_operands', 'languages': ('ladder',),
                                'value': {'operands': [dict(source)]}, 'depends_on': []}
                _n_apply_bcd_input_type_branches(
                    fake, symbol, branch_specs, [signature, typed, typed_ladder])

                for fact, pin in ((signature, signature_value['parameters'][1] if enabled
                                   else signature_value['parameters'][0]),
                                  (typed, typed['value']['operands'][0]),
                                  (typed_ladder, typed_ladder['value']['operands'][0])):
                    self.assertEqual(pin['type'], 'ANY_BIT')
                    self.assertEqual(pin['source_verified_type_branches'], ['WORD', 'DWORD'])
                    self.assertEqual(pin['other_type_branches'], 'unresolved')
                    self.assertIn(branch_fact_id, fact['depends_on'])
                    self.assertEqual(_undetermined_fields(pin), [])
                self.assertEqual(signature_value['returns'], 'BOOL' if enabled else 'STRING')

        self.assertEqual(_n_bcd_input_type_branch_specs(fake, 'STR_TO_BCD'), [])

    def test_add_time_page_header_is_bound_only_to_exact_forms_and_model_set(self):
        section = SimpleNamespace(manual='sh080784engn', revision='N', manual_number='SH080784ENG',
            chapter='5 APPLICATION FUNCTIONS > 5.8 Functions of Time Data Type', title='Addition',
            symbols=['ADD_TIME', 'ADD_TIME_E'], page_pairs=[(168, 169)])
        page_text = 'ADD_TIME(_E)\nADD_TIME, ADD_TIME_E'
        header = {'basis': 'instruction_header', 'model_marks': [
            {'model': model, 'state': 'supported'} for model in ('Q03UDV', 'L02')]}
        verified = _n_time_add_applicability_header(section, page_text, header, ['Q03UDV', 'L02'])
        self.assertEqual(verified['opcodes'], ['ADD_TIME', 'ADD_TIME_E'])
        self.assertIsNone(_n_time_add_applicability_header(section, page_text, header, ['Q03UDV']))
        self.assertIsNone(_n_time_add_applicability_header(section,
            'ADD_TIME(_E)\nADD_TIME', header, ['Q03UDV', 'L02']))
        conditional = {'basis': 'instruction_header', 'model_marks': [
            {'model': 'Q03UDV', 'state': 'supported'}, {'model': 'L02', 'state': 'conditional'}]}
        self.assertIsNone(_n_time_add_applicability_header(
            section, page_text, conditional, ['Q03UDV', 'L02']))
        section.title = 'Subtraction'
        self.assertIsNone(_n_time_add_applicability_header(section, page_text, header, ['Q03UDV', 'L02']))

    def test_time_function_signatures_require_own_header_call_and_typed_table(self):
        expected_pages = {
            'ADD_TIME': {168, 169}, 'ADD_TIME_E': {168, 169},
            'SUB_TIME': {170, 171}, 'SUB_TIME_E': {170, 171},
            'MUL_TIME': {172, 173}, 'MUL_TIME_E': {172, 173},
            'DIV_TIME': {174, 175}, 'DIV_TIME_E': {174, 175},
        }
        for symbol, pages in expected_pages.items():
            with self.subTest(symbol=symbol):
                fake = time_section(symbol)
                signature = next(spec for spec in _n_parameter_specs(fake, symbol)
                                 if spec['dimension'] == 'parameters.st_signature')
                self.assertTrue(signature.get('source_verified_st_signature'))
                guarded = _guard_specs(fake, [signature], 'Q03UDV', 'supported', False, symbol)
                facts, unbound, _ = _materialize(fake, symbol, guarded, 'Q03UDV', verified=False)
                self.assertFalse(unbound)
                self.assertEqual(facts[0]['status'], 'source_verified')
                self.assertEqual({page for source in facts[0]['sources'] for page in source['pdf_pages']},
                                 pages)
                if symbol in {'DIV_TIME', 'DIV_TIME_E'}:
                    self.assertIn('authored:ql:n_review_nonzero_divisor_range:constraints.numeric_ranges:s2',
                                  signature.get('depends_on', ()))
                signature_value = signature['value']
                if symbol.startswith(('MUL_', 'DIV_')):
                    self.assertEqual(next(row['type'] for row in signature_value['parameters']
                                          if row['name'] == 's2'), 'ANY_NUM')
                    self.assertEqual(next(row['type_alternatives'] for row in signature_value['parameters']
                                          if row['name'] == 's2'), ['INT', 'DINT', 'REAL', 'LREAL'])
                    typed = next(spec for spec in _n_parameter_specs(fake, symbol)
                                 if spec['dimension'] == 'parameters.typed_operands'
                                 and 'st' in spec['languages'])
                    self.assertEqual(next(row['type_alternatives'] for row in typed['value']['operands']
                                          if row['name'] == 's2'), ['INT', 'DINT', 'REAL', 'LREAL'])
                    self.assertIn(min(pages), {page for witness in typed['patterns']
                                               for _, page in fake.bind(witness)})
                else:
                    self.assertEqual(next(row['type'] for row in signature_value['parameters']
                                          if row['name'] == 's2'), 'TIME')

    def test_time_function_signature_stays_candidate_without_its_exact_source_call(self):
        wrong_page = time_section('MUL_TIME')
        wrong_page.page_pairs = [(170, 171)]
        wrong_title = time_section('SUB_TIME')
        wrong_title.title = 'Addition'
        wrong_scope = time_section('DIV_TIME')
        wrong_scope.symbols = ['SUB_TIME', 'SUB_TIME_E']
        cases = (
            (time_section('SUB_TIME', declaration=False), 'SUB_TIME'),
            (time_section('MUL_TIME_E', call='g_bool3 := MUL_TIME_E(g_bool1, g_time1, g_int1);'),
             'MUL_TIME_E'),
            (wrong_page, 'MUL_TIME'),
            (wrong_title, 'SUB_TIME'),
            (wrong_scope, 'DIV_TIME'),
        )
        for fake, symbol in cases:
            with self.subTest(symbol=symbol):
                signature = next(spec for spec in _n_parameter_specs(fake, symbol)
                                 if spec['dimension'] == 'parameters.st_signature')
                self.assertFalse(signature.get('source_verified_st_signature', False))
                guarded = _guard_specs(fake, [signature], 'Q03UDV', 'supported', False, symbol)
                facts, _, _ = _materialize(fake, symbol, guarded, 'Q03UDV', verified=False)
                self.assertEqual(facts[0]['status'], 'candidate_evidence')

    def test_time_numeric_factor_types_need_the_exact_own_clause(self):
        for symbol in ('MUL_TIME', 'MUL_TIME_E', 'DIV_TIME', 'DIV_TIME_E'):
            with self.subTest(symbol=symbol):
                fake = time_section(symbol, include_type_clause=False)
                signature = next(spec for spec in _n_parameter_specs(fake, symbol)
                                 if spec['dimension'] == 'parameters.st_signature')
                self.assertFalse(signature.get('source_verified_st_signature', False))
                self.assertNotIn('type_alternatives', next(row for row in signature['value']['parameters']
                                                           if row['name'] == 's2'))

    def test_time_division_zero_code_is_bound_to_page_175_and_quotient_to_174(self):
        fake = time_section('DIV_TIME')
        facts = _build_n_behavior_detail_specs(fake, 'DIV_TIME')
        zero_facts = [fact for fact in facts if fact['key'] in {
            'n_review_division_zero', 'n_review_division_zero_structured_block'}]
        quotient_facts = [fact for fact in facts if fact['key'] in {
            'n_review_time_quotient', 'n_review_time_quotient_structured_block'}]
        self.assertEqual({fact['key'] for fact in zero_facts}, {
            'n_review_division_zero', 'n_review_division_zero_structured_block'})
        self.assertEqual({fact['key'] for fact in quotient_facts}, {
            'n_review_time_quotient', 'n_review_time_quotient_structured_block'})
        for zero in zero_facts:
            with self.subTest(fact=zero['key']):
                self.assertEqual(zero['_required_source_pages'], [174, 175])
                code_clause = next(witness for witness in zero['patterns'] if witness.startswith('4100 '))
                self.assertEqual({page for _, page in fake.bind(code_clause)}, {175})
        for quotient in quotient_facts:
            with self.subTest(fact=quotient['key']):
                self.assertEqual(quotient['_required_source_pages'], [174])
                self.assertEqual({page for witness in quotient['patterns'] for _, page in fake.bind(witness)},
                                 {174})
        numeric_range = next(fact for fact in facts if fact['key'] == 'n_review_nonzero_divisor_range')
        self.assertEqual(numeric_range['languages'], ['st'])
        self.assertEqual(numeric_range['depends_on'], [
            'authored:ql:cpu_applicability:constraints.cpu_availability'])
        self.assertEqual(numeric_range['_required_source_pages'], [174, 175])
        self.assertEqual(numeric_range['value']['ranges'], [
            {'binding': {'name': 's2'}, 'excluded_values': [0]}])
        materialized, unbound, _ = _materialize(
            fake, 'DIV_TIME', [*zero_facts, *quotient_facts, numeric_range], 'Q03UDV', verified=True)
        self.assertFalse(unbound)
        by_id = {fact['id']: fact for fact in materialized}
        for fact_id in (
                'authored:ql:n_review_division_zero:effects.error_response:none|none',
                'authored:ql:n_review_division_zero_structured_block:effects.error_response:none|none'):
            self.assertEqual({page for source in by_id[fact_id]['sources'] for page in source['pdf_pages']},
                             {174, 175})
        for fact_id in (
                'authored:ql:n_review_time_quotient:effects.remainder',
                'authored:ql:n_review_time_quotient_structured_block:effects.remainder'):
            self.assertEqual({page for source in by_id[fact_id]['sources'] for page in source['pdf_pages']},
                             {174})
        range_id = 'authored:ql:n_review_nonzero_divisor_range:constraints.numeric_ranges:s2'
        self.assertEqual({page for source in by_id[range_id]['sources'] for page in source['pdf_pages']},
                         {174, 175})
        self.assertEqual(by_id[range_id]['depends_on'], [
            'authored:ql:cpu_applicability:constraints.cpu_availability'])
        self.assertNotIn('conditions', by_id[range_id])
        div_signature = next(spec for spec in _n_parameter_specs(fake, 'DIV_TIME')
                             if spec['dimension'] == 'parameters.st_signature')
        self.assertIn(range_id, div_signature['depends_on'])

        enabled_fake = time_section('DIV_TIME_E')
        enabled_facts = _build_n_behavior_detail_specs(enabled_fake, 'DIV_TIME_E')
        enabled_range = next(fact for fact in enabled_facts
                             if fact['key'] == 'n_review_nonzero_divisor_range')
        en_guard = [{'op': 'parameter', 'name': 'EN', 'type': {'kind': 'bool'}}]
        self.assertEqual(enabled_range['conditions'], en_guard)
        off_witness = next(witness for witness in enabled_range['patterns']
                           if witness.startswith('FALSE FALSE'))
        self.assertEqual({page for _, page in enabled_fake.bind(off_witness)}, {175})
        enabled_materialized, enabled_unbound, _ = _materialize(
            enabled_fake, 'DIV_TIME_E', [enabled_range], 'Q03UDV', verified=True)
        self.assertFalse(enabled_unbound)
        self.assertEqual(next(fact for fact in enabled_materialized if fact['id'].endswith(
            'constraints.numeric_ranges:s2'))['conditions'], en_guard)

        missing_code = time_section('DIV_TIME', include_error_code=False)
        blocked = _build_n_behavior_detail_specs(missing_code, 'DIV_TIME')
        self.assertFalse(any(fact['key'] in {'n_review_division_zero',
                                            'n_review_division_zero_structured_block'} for fact in blocked))
        self.assertTrue(any(row.get('reason') == 'error_code_4100_not_bound_to_own_division_page'
                            for row in missing_code._n_behavior_review['source_present_not_extracted']))
        missing_nonzero = time_section('DIV_TIME', include_nonzero_clause=False)
        missing_specs = _build_n_behavior_detail_specs(missing_nonzero, 'DIV_TIME')
        self.assertFalse(any(fact['key'] == 'n_review_nonzero_divisor_range' for fact in missing_specs))
        missing_signature = next(spec for spec in _n_parameter_specs(missing_nonzero, 'DIV_TIME')
                                 if spec['dimension'] == 'parameters.st_signature')
        self.assertFalse(missing_signature.get('source_verified_st_signature', False))

        missing_off_row = time_section('DIV_TIME_E')
        missing_off_row._n_execution_rows = [row for row in missing_off_row._n_execution_rows
                                             if row['EN']]
        missing_off_facts = _build_n_behavior_detail_specs(missing_off_row, 'DIV_TIME_E')
        self.assertFalse(any(fact['key'] == 'n_review_nonzero_divisor_range'
                             for fact in missing_off_facts))
        self.assertTrue(any(item.get('reason') == 'enabled_execution_rows_not_bound_to_page_175'
                            for item in missing_off_row._n_behavior_review['source_present_not_extracted']))

    def test_bit_writers_do_not_invent_other_bit_retention(self):
        value = _n_conversion_source_specs(section('SET_BIT_OF_INT_E',
            'Writes a value specified for (s) to the nth bit of (d).'), 'SET_BIT_OF_INT_E')[0]['value']
        self.assertEqual(value['target_bit'], 'n')
        self.assertEqual(value['other_result_bits'], 'source_not_stated')

    def test_truncation_requires_semantic_tail_even_if_binder_accepts_prefix(self):
        symbol = 'DINT_TO_INT_E'
        self.assertFalse(_n_conversion_source_specs(section(symbol, 'High-order 16-bit data'), symbol))
        facts = _n_conversion_source_specs(section(symbol, 'High-order 16-bit data are discarded.'), symbol)
        self.assertEqual(facts[0]['value']['discarded_source_bits'], [16, 31])
        self.assertEqual(facts[1]['languages'], ('ladder',))
        self.assertEqual(facts[1]['conditions'], [{'context': 'project_mode', 'equals': 'structured_ladder_block'}])

    def test_boolean_needs_both_truth_cases(self):
        symbol = 'BOOL_TO_INT_E'
        first = 'When the input value is FALSE, 0 is output'
        self.assertFalse(_n_conversion_source_specs(section(symbol, first), symbol))
        facts = _n_conversion_source_specs(section(symbol, first +
            ' When the input value is TRUE, 1 is output'), symbol)
        self.assertEqual(facts[0]['value']['cases'], [{'input': False, 'output': 0}, {'input': True, 'output': 1}])

    def test_bool_to_time_does_not_infer_a_time_unit(self):
        for symbol in ('BOOL_TO_TIME', 'BOOL_TO_TIME_E'):
            with self.subTest(symbol=symbol):
                text = ('When the input value is FALSE, 0 is output. '
                        'When the input value is TRUE, 1 is output.')
                facts = _n_conversion_source_specs(section(symbol, text), symbol)
                relation = next(fact for fact in facts if fact['dimension'] == 'effects.value_relation')
                self.assertEqual(relation['value']['result_type'], 'TIME')
                self.assertEqual(relation['value']['cases'],
                                 [{'input': False, 'output': 0}, {'input': True, 'output': 1}])
                self.assertNotIn('unit', relation['value'])

    def test_grouped_string_capacity_choices_are_not_selected_by_mnemonic(self):
        choices = [6, 11]
        for symbol, name, direction in (
            ('INT_TO_STR', 'd', 'output'), ('DINT_TO_STR', 'd', 'output'),
            ('STR_TO_INT', 's', 'input'), ('STR_TO_DINT', 's', 'input')):
            with self.subTest(symbol=symbol):
                operand = {'name': name, 'type': 'STRING', 'direction': direction,
                           'declared_capacity_choices': choices}
                refined = _n_refined_pin(operand, [{'operand': operand, 'source_name': name}], symbol)
                self.assertEqual(refined['declared_capacity_choices'], choices)
                self.assertNotIn('declared_capacity', refined)

    def test_n_argument_table_keeps_formal_symbol_separate_from_source_alias(self):
        operand = {'name': 's', 'type': 'ANY_BIT', 'direction': 'input', 'role': 'source'}
        row = {'operand': dict(operand), 'source_name': 's(_BCD)'}
        refined = _n_refined_pin(operand, [row], 'BCD_TO_STR')
        self.assertEqual(refined['symbol'], 's')
        self.assertEqual(refined['source_aliases'], ['_BCD'])

    def test_error_row_cpu_marks_are_scoped_and_complete(self):
        symbol = 'INT_TO_BCD_E'
        text = 'value input exceeds 9999 in the execution of the INT_TO_BCD(_E)'
        cells = [{'text': 'Error code', 'bbox': [0, 0, 10, 10]}]
        for index, value in enumerate(['4100', text, '\uf0c9', *(['\uf0c6'] * 5)]):
            cells.append({'text': value, 'bbox': [index * 10, 10, (index + 1) * 10, 20]})
        fake = section(symbol, text)
        fake._n_behavior_tables = [{'cells': cells}]
        facts = _n_conversion_source_specs(fake, symbol)
        error = next(fact for fact in facts if 'error_4100' in fact['key'])
        self.assertIsNone(error['model_filter'])
        self.assertEqual(error['status'], 'unknown')
        self.assertEqual(error['value']['cpu_group_applicability_status'], 'pending_symbol_semantics')
        self.assertEqual(set(error['value']['table_cpu_group_applicability'].values()), {'unknown'})
        self.assertEqual(error['value']['table_cpu_group_marks']['Q00J/Q00/Q01'], 'U+F0C9')
        self.assertEqual(error['value']['table_cpu_group_marks']['QnU'], 'U+F0C6')
        self.assertEqual([c['raw_mark'] for c in error['value']['source_error_row']['cpu_columns']],
                         ['U+F0C9', *(['U+F0C6'] * 5)])
        self.assertEqual([c['column_index'] for c in error['value']['source_error_row']['cpu_columns']],
                         [3, 4, 5, 6, 7, 8])
        self.assertEqual(error['value']['any_of'][0]['value'], 9999)
        # Neither glyph can certify an error rule for a particular CPU. The
        # default delivery still carries the unresolved marks and their status.
        from plc.instruction_definition import select_fact_dependencies
        from shared.model_context import technical_fact_packet, model_knowledge_value
        fact = dict(id='pending-error', dimension=error['dimension'], value=error['value'],
                    status=error['status'], verification='source_checked', sources=[
                        dict(manual_id='sh080784engn', revision='N', pdf_pages=[53])],
                    scope=dict(models=['Q00', 'Q03UDV'], forms=[symbol], languages=['st', 'fbd']))
        for model in ['Q00', 'Q03UDV']:
            closure = select_fact_dependencies([fact], ['pending-error'], opcode=symbol, model=model, language='fbd')
            self.assertFalse(closure['source_verification_complete'])
        packet = dict(kind='instruction', target=symbol, scope={'models': ['Q03UDV']},
                      _knowledge_context={'model': 'Q03UDV', 'language': 'fbd'},
                      value={'opcode': symbol, 'target_model': 'Q03UDV', 'facts': [fact]})
        import json
        delivered = json.dumps(model_knowledge_value(technical_fact_packet(packet)))
        for token in ['pending_symbol_semantics', 'U+F0C6', 'U+F0C9']:
            self.assertIn(token, delivered)
        cells.pop()
        self.assertFalse(_n_conversion_source_specs(fake, symbol))

    def test_open_error_grid_uses_only_words_inside_existing_row(self):
        names = ['Error code', 'Description', 'Q00J/ Q00/ Q01', 'QnH', 'QnPH', 'QnPRH', 'QnU', 'LCPU']
        cells = [{'text': name, 'bbox': [i * 10, 0, (i + 1) * 10, 10]} for i, name in enumerate(names)]
        cells.append({'text': 'synthetic-condition', 'bbox': [10, 10, 20, 20]})
        words = [{'text': '4100', 'x0': 1, 'x1': 9, 'top': 11, 'bottom': 19},
                 {'text': '[GLYPH-F0C6]', 'x0': 71, 'x1': 79, 'top': 11, 'bottom': 19},
                 {'text': 'outside', 'x0': 1, 'x1': 9, 'top': 21, 'bottom': 29}]
        result = _n_complete_error_table({'cells': cells}, words)
        self.assertEqual([cell['text'] for cell in result['cells'][-2:]], ['4100', '\uf0c6'])
        self.assertEqual(len(cells), 9)

    def test_pending_error_row_is_runtime_public_without_geometric_fields(self):
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        symbol = 'INT_TO_BCD_E'
        text = 'value input exceeds 9999 in the execution of the INT_TO_BCD(_E)'
        cells = [{'text': 'Error code', 'bbox': [0, 0, 10, 10]}]
        for index, value in enumerate(['4100', text, '\uf0c9', *(['\uf0c6'] * 5)]):
            cells.append({'text': value, 'bbox': [index * 10, 10, (index + 1) * 10, 20]})
        fake = section(symbol, text)
        fake._n_behavior_tables = [{'cells': cells, 'page': 53}]
        rule = next(f for f in _n_conversion_source_specs(fake, symbol) if 'error_4100' in f['key'])
        OwnerStoreWriter._public(rule['value'])
        row = rule['value']['source_error_row']
        self.assertEqual(set(row), {'pdf_page', 'error_code', 'cpu_columns'})
        self.assertEqual((row['pdf_page'], row['error_code']), (53, 4100))
        self.assertTrue(all(set(c) == {'column_index', 'label', 'raw_mark'} for c in row['cpu_columns']))
        with self.assertRaises(ValueError):
            OwnerStoreWriter._public({'cpu_columns': [{'bbox': [0, 0, 10, 10]}]})

    def test_declared_dependency_resolves_printed_bound_without_native_claim(self):
        symbol = 'STR_TO_TIME_E'
        fake = section(symbol, 'STR_TO_TIME(_E): DDABIN '
            'When ASCII data specified for (s) is other than -2147483648 to 4147483647')
        self.assertFalse(_n_conversion_source_specs(fake, symbol))
        fake._n_ddabin_error_dependency = section('DDABIN',
            'When the DDABIN instruction is used: -2147483648 to 2147483647')
        facts = _n_conversion_source_specs(fake, symbol)
        error = next(fact for fact in facts if fact['key'] == 'n_conversion_ddabin_error_range')
        self.assertEqual(error['value']['any_of'][0]['range'], [-2147483648, 2147483647])
        self.assertEqual(len(error['evidence_sections']), 2)
        self.assertFalse(error['model_filter']('Q00'))
        self.assertTrue(error['model_filter']('Q03UDV'))
        self.assertEqual(error['depends_on'],
            ['authored:ql:n_conversion_ddabin_dependency:constraints.error_rule_references'])

    def test_grouped_fallback_requires_every_condition_and_language(self):
        symbol = 'STR_TO_INT_E'
        description = ("The input value is other than '30H' to '39H', '20H', '2DH', and '00H' of ASCII code. "
            'When the STR_TO_INT(_E) instruction is used, the input value is outside the range of -32768 to 32767. '
            'When the STR_TO_DINT(_E) instruction is used, the input value is outside the range of -2147483648 to 2147483647.')
        cells = [{'text': 'Error code', 'bbox': [0, 0, 10, 10]}]
        for index, value in enumerate(['4100', description, *(['\uf0c6'] * 6)]):
            cells.append({'text': value, 'bbox': [index * 10, 10, (index + 1) * 10, 20]})
        fake = section(symbol, description)
        fake._n_behavior_tables = [{'cells': cells}]
        numeric = {'operand': 's', 'relation': 'decoded_integer_outside_inclusive_range', 'range': [-32768, 32767]}
        ascii_case = {'operand': 's', 'relation': 'contains_disallowed_ascii_byte', 'allowed': [*range(48, 58), 32, 45, 0]}
        fallback = dict(key='error', dimension='effects.error_response', status='unknown',
            languages=('st', 'fbd'), value={'operation_error': 'documented'})
        typed = dict(key='typed', dimension='effects.error_response', reviewed=True,
            languages=('st', 'fbd'), value={'error_codes': [4100], 'any_of': [numeric]})
        def retained(candidate=typed):
            return _retain_uncovered_grouped_error(fake, symbol, [candidate, fallback])[-1].get(
                'model_filter', lambda model: True)('Q03UDV')
        self.assertTrue(retained())  # same code plus one case cannot hide the ASCII case
        typed['value']['any_of'].append(ascii_case)
        self.assertFalse(retained())
        other_owner = _retain_uncovered_grouped_error(fake, 'STR_TO_DINT_E', [typed, fallback])[-1]
        self.assertTrue(other_owner['model_filter']('Q03UDV'))
        self.assertTrue(retained(dict(typed, languages=('st',))))
        self.assertTrue(retained(dict(typed, model_filter=lambda model: False)))
        self.assertTrue(retained(dict(typed, value={'error_codes': [4101], 'any_of': [numeric, ascii_case]})))
        cells[2]['text'] += ' A further unreviewed error condition.'
        self.assertTrue(retained())

    def test_corrected_time_example_is_limited_to_positive_integer_example(self):
        fake = section('DIV_TIME_E', '')
        fake.title, fake.chapter, fake.text = 'synthetic', 'synthetic', ''
        fake.pages, fake.page_pairs, fake._n_execution_rows = [174], [(174, 174)], []
        facts = _build_n_behavior_detail_specs(fake, 'DIV_TIME_E')
        example = next(fact for fact in facts if fact['key'] == 'n_review_time_division_example_disagreement')
        quotient, remainder = divmod(95624055, 2)
        value = example['value']
        self.assertFalse(example.get('source_conflict', False))
        self.assertEqual(value['arithmetic_quotient_milliseconds'], quotient)
        self.assertEqual(value['arithmetic_remainder_milliseconds'], remainder)
        self.assertEqual(value['printed_quotient_TIME_fields']['seconds'], 63)
        self.assertEqual(value['corrected_quotient_TIME_fields']['seconds'], 52)
        self.assertEqual(value['resolution'], 'source_example_arithmetic_correction')
        self.assertEqual(value['correction_scope'], 'this_positive_integer_division_example_only')
        self.assertFalse(value['runtime_test_claimed'])

    def _error_rows(self, *rows):
        cells = [{'text': 'Error code', 'bbox': [0, 0, 10, 10]}]
        for row_index, (code, description) in enumerate(rows):
            y = 10 + row_index * 20
            for index, value in enumerate([str(code), description, *(['\uf0c9'] * 4), '\uf0c6', '\uf0c6']):
                cells.append({'text': value, 'bbox': [index * 10, y, (index + 1) * 10, y + 10]})
        return cells

    def test_lreal_to_integer_error_source_keeps_input_stage_and_exact_destination_bound(self):
        range_clause = '0, 2-1022 ≤|(s)|<21024'
        for symbol, bound_clause, bounds in (
            ('LREAL_TO_INT', 'When the LREAL_TO_INT(_E) instruction is used, the input value is outside the range of -32768.0 to 32767.0', [-32768.0, 32767.0]),
            ('LREAL_TO_DINT_E', 'When the LREAL_TO_DINT(_E) instruction is used, the input value is outside the range of -2147483648.0 to 2147483647.0', [-2147483648.0, 2147483647.0]),
        ):
            with self.subTest(symbol=symbol):
                text = ('The input value is -0 or outside the following range. ' + range_clause + ' ' + bound_clause
                        + ' The converted data is the value rounded double-precision real type data to the first digit after the decimal point.')
                fake = section(symbol, text)
                fake._n_behavior_tables = [{'cells': self._error_rows((4140, text)), 'page': 59}]
                facts = _n_conversion_source_specs(fake, symbol)
                error = next(fact for fact in facts if fact['key'] == 'n_conversion_error_4140')
                stage = next(fact for fact in facts if fact['key'] == 'n_conversion_error_stage_4140')
                rounding = next(fact for fact in facts if fact['key'] == 'n_conversion_lreal_integer_rounding')
                self.assertEqual(error['status'], 'unknown')
                self.assertEqual(error['value']['any_of'][1]['range'], bounds)
                self.assertEqual(error['value']['any_of'][1]['condition_stage'], 'input_value')
                self.assertEqual(error['value']['table_cpu_group_applicability']['QnU'], 'unknown')
                self.assertEqual(error['value']['source_error_row']['pdf_page'], 59)
                self.assertEqual(stage['value']['condition_stage'], 'input_value')
                self.assertEqual(stage['value']['conditions'][1]['range'], bounds)
                self.assertEqual(rounding['value']['rounding'], 'nearest_integer')
                self.assertEqual(rounding['value']['halfway_tie_rule'], 'source_not_stated')

    def test_real_lreal_conversion_error_rows_keep_input_and_operation_result_stages_separate(self):
        for symbol, source_range, overflow_code, overflow_limit, output_type, overflow_row in (
            ('REAL_TO_LREAL_E', '0, 2-126 ≤|(s)|<2128', 4141, {'base': 2, 'power': 1024}, 'LREAL', '21024 [GLYPH-F0A3] | operation result |'),
            ('LREAL_TO_REAL', '0, 2-1022 ≤|(s)|<21024', 4141, {'base': 2, 'power': 128}, 'REAL', '2128 [GLYPH-F0A3] | operation result |'),
        ):
            with self.subTest(symbol=symbol):
                input_text = 'The input value is -0 or outside the following range. ' + source_range
                result_text = 'The operation result exceeds the following range. (An overflow occurs.) ' + overflow_row
                fake = section(symbol, input_text + ' ' + result_text)
                fake._n_behavior_tables = [{'cells': self._error_rows((4140, input_text), (4141, result_text)), 'page': 61 if source_range.startswith('0, 2-126') else 63}]
                facts = _n_conversion_source_specs(fake, symbol)
                input_error = next(fact for fact in facts if fact['key'] == 'n_conversion_error_4140')
                input_stage = next(fact for fact in facts if fact['key'] == 'n_conversion_error_stage_4140')
                overflow = next(fact for fact in facts if fact['key'] == 'n_conversion_error_4141')
                overflow_stage = next(fact for fact in facts if fact['key'] == 'n_conversion_error_stage_4141')
                self.assertEqual(input_error['value']['any_of'][0]['operand'], 's')
                self.assertEqual(input_stage['value']['condition_stage'], 'input_value')
                self.assertEqual(overflow['value']['any_of'][0]['operand'], 'operation_result')
                self.assertEqual(overflow['value']['any_of'][0]['threshold_power_of_two'], overflow_limit)
                self.assertEqual(overflow['value']['any_of'][0]['destination_type'], output_type)
                self.assertEqual(overflow_stage['value']['condition_stage'], 'operation_result')
                self.assertEqual(overflow_stage['value']['error_code'], overflow_code)
                self.assertEqual(overflow['value']['source_error_row']['pdf_page'], 61 if source_range.startswith('0, 2-126') else 63)

    def test_lreal_error_extraction_requires_the_selected_conversion_bound(self):
        text = ('The input value is -0 or outside the following range. 0, 2-1022 ≤|(s)|<21024 '
                'When the LREAL_TO_DINT(_E) instruction is used, the input value is outside the range of '
                '-2147483648.0 to 2147483647.0')
        facts = _n_conversion_source_specs(section('LREAL_TO_INT', text), 'LREAL_TO_INT')
        self.assertFalse(any(fact['key'] == 'n_conversion_error_4140' for fact in facts))
        self.assertFalse(any(fact['key'] == 'n_conversion_error_stage_4140' for fact in facts))

    def test_wrong_manual_or_undeclared_symbol_is_not_inferred(self):
        fake = section('DINT_TO_INT_E', 'High-order 16-bit data are discarded.')
        self.assertFalse(_n_conversion_source_specs(fake, 'DWORD_TO_WORD_E'))
        fake.manual = 'other'
        self.assertFalse(_n_conversion_source_specs(fake, 'DINT_TO_INT_E'))

    def test_bit_array_own_labelled_body_not_name_or_sibling_binds_width(self):
        def body(base, word, bits, counts):
            return (base + ', ' + base + '_E Converts number of bits specified for n starting from '
                'the bit array element input to (s) into ' + word + ' (signed) type or ' + word +
                ' (unsigned)/' + str(bits) + '-bit string type data, and outputs the operation result from (d). '
                'Only a constant ' + counts + ' can be specified for n. '
                '0 is set for the output bits higher than the specified number of bits.')
        int_text = body('BITARR_TO_INT', 'word', 16, '4, 8, 12 or 16')
        dint_text = body('BITARR_TO_DINT', 'double word', 32, '4, 8, 12, 16, 20, 24, 28 or 32')
        for base, text, bits in [('BITARR_TO_INT', int_text, 16), ('BITARR_TO_DINT', dint_text, 32)]:
            for suffix in ['', '_E']:
                symbol = base + suffix
                with self.subTest(symbol=symbol):
                    fake = section(symbol, int_text + ' ' + dint_text)
                    branch = _n_bitarr_branch(fake, symbol)
                    self.assertEqual(branch['bits'], bits)
                    effect = _n_conversion_source_specs(fake, symbol)[0]['value']
                    self.assertEqual(effect['result_bits'], bits)
                    self.assertEqual(effect['element_to_result_bit_mapping'], 'source_not_stated')
                    self.assertEqual(effect['result_channel'], 'output_parameter' if suffix else 'function_return_value')
                    self.assertIsNone(_n_bitarr_branch(section(symbol, text.split('Only a constant')[0]), symbol))
                    sibling = dint_text if bits == 16 else int_text
                    self.assertIsNone(_n_bitarr_branch(section(symbol, sibling), symbol))
                    self.assertIsNone(_n_bitarr_branch(section(symbol, text.replace('/'+str(bits)+'-bit', '/'+str(48-bits)+'-bit')), symbol))
                    # One own shared argument table; no BITARR or packed-device ABI type is invented.
                    rows = [{'operand': dict(name=name, type=kind, direction=direction),
                             'source_name': 's(_BitArr)' if name == 's' else name, 'witness': name}
                            for name, kind, direction in [('EN','BOOL','input'),('s','BOOL','input'),
                                ('n','INT','input'),('ENO','BOOL','output'),('d','ANY16_OR_ANY32','output')]]
                    rows[-1]['operand']['type_alternatives'] = ['ANY16','ANY32']
                    fake._n_argument_contracts = [{'rows': rows, 'issues': []}]
                    fake.text = int_text + ' ' + dint_text
                    fake.chapter = 'Type Conversion Functions'
                    fake.call_forms = lambda: []
                    fake._flat = fake.flat_rows()
                    facts = _n_parameter_specs(fake, symbol)
                    typed = next(f['value']['operands'] for f in facts if f['dimension'] == 'parameters.typed_operands')
                    pins = {pin['name']: pin for pin in typed}
                    self.assertEqual(pins['n']['type'], 'INT')
                    self.assertEqual(pins['n']['role'], 'bit_count')
                    self.assertEqual(pins['d']['documented_type_alternatives'], ['ANY16','ANY32'])
                    self.assertEqual(pins['d']['form_width_bits'], bits)
                    self.assertEqual(pins['s']['array_element_reference']['extent']['allowed_counts'], list(range(4,bits+1,4)))
                    self.assertNotIn('word_device_layout', pins['s'])

    def test_integer_to_bit_array_own_clause_binds_exact_source_width_and_span(self):
        def body(base, source, word, bits, counts):
            return (base + ', ' + base + '_E Outputs low-order n bits of ' + word +
                    ' (signed) type or ' + word + ' (unsigned)/' + str(bits) +
                    '-bit string type data specified for (s) to (d). Only a constant ' +
                    counts + ' can be specified for n. The output bits higher than the specified number of bits do not change.')

        int_text = body('INT_TO_BITARR', 'INT', 'word', 16, '4, 8, 12 or 16')
        dint_text = body('DINT_TO_BITARR', 'DINT', 'double word', 32,
                         '4, 8, 12, 16, 20, 24, 28 or 32')
        for symbol, text, width, allowed in (
                ('INT_TO_BITARR', int_text, 16, [4, 8, 12, 16]),
                ('INT_TO_BITARR_E', int_text, 16, [4, 8, 12, 16]),
                ('DINT_TO_BITARR', dint_text, 32, [4, 8, 12, 16, 20, 24, 28, 32]),
                ('DINT_TO_BITARR_E', dint_text, 32, [4, 8, 12, 16, 20, 24, 28, 32])):
            with self.subTest(symbol=symbol):
                fake = section(symbol, text)
                fake.bind = lambda witness, text=text: [('source', 113)] if witness in text else []
                branch = _n_bitarr_output_branch(fake, symbol)
                self.assertEqual(branch['bits'], width)
                self.assertEqual(branch['counts'], allowed)
                reference = _n_bitarr_array_element_reference(branch)
                self.assertEqual(reference['extent']['parameter'], 'n')
                self.assertEqual(reference['extent']['allowed_counts'], allowed)
                self.assertTrue(reference['extent']['constant_only'])
                self.assertIsNone(_n_bitarr_output_branch(section(symbol, text.replace('bits higher than', 'bits at or higher than')), symbol))
                sibling = dint_text if width == 16 else int_text
                self.assertIsNone(_n_bitarr_output_branch(section(symbol, sibling), symbol))

    def test_bit_array_call_witnesses_preserve_plain_assignment_and_derive_only_missing_dint_call(self):
        def page(text, number):
            return SimpleNamespace(text=text, bind=lambda witness, text=text: [("source", number)]
                                   if witness in text else [])

        def make_section(symbol):
            enabled = symbol.endswith('_E')
            base = symbol[:-2] if enabled else symbol
            width = 16 if base.startswith('INT_') else 32
            word = 'word' if width == 16 else 'double word'
            counts = list(range(4, width + 1, 4))
            count_text = ', '.join(map(str, counts[:-1])) + ' or ' + str(counts[-1])
            heading = base + ', ' + base + '_E'
            operation = (heading + ' Outputs low-order n bits of ' + word + ' (signed) type or ' +
                word + ' (unsigned)/' + str(width) + '-bit string type data specified for (s) to (d). '
                'Only a constant ' + count_text + ' can be specified for n. The output bits higher than '
                'the specified number of bits do not change.')
            rows = [
                {'operand': {'name': 's', 'type': 'ANY16' if width == 16 else 'ANY32', 'direction': 'input'},
                 'source_name': 's', 'witness': 's input'},
                {'operand': {'name': 'n', 'type': 'INT', 'direction': 'input'},
                 'source_name': 'n', 'witness': 'n input'},
                {'operand': {'name': 'd', 'type': 'BOOL', 'direction': 'output'},
                 'source_name': 'd(_BitArr)', 'witness': 'd output'},
            ]
            if enabled:
                rows = [
                    {'operand': {'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                     'source_name': 'EN', 'witness': 'EN input'}, *rows,
                    {'operand': {'name': 'ENO', 'type': 'BOOL', 'direction': 'output'},
                     'source_name': 'ENO', 'witness': 'ENO output'},
                ]
            declarations = ('Function with EN/ENO (INT_TO_BITARR_E)\n'
                            'g_bool2 := INT_TO_BITARR_E(g_bool1, g_int1, 4, g_bool4[0]);') if enabled else (
                            'Function without EN/ENO (INT_TO_BITARR)\n'
                            'g_bool4[0] := INT_TO_BITARR(g_int1, 4);')
            page113 = page(operation + '\n' + heading, 113)
            page114 = page(declarations, 114)
            rules = {
                83: page(' '.join(_LEFT_ST_CALL_RULES[83]), 83),
                29: page(' '.join(_LEFT_ST_CALL_RULES[29]), 29),
            }
            fake = SimpleNamespace(manual='sh080784engn', revision='N', symbols=[symbol],
                flat_rows=lambda: [('section', 113, operation)], bind=lambda witness: [('section', 113)]
                    if witness in operation else [],
                _geometry_flat=[], _n_bitarr_page_views={113: page113, 114: page114},
                _n_bitarr_st_call_rule_sections=rules)
            signature_parameters = ([
                {'name': 'EN', 'symbol': 'EN', 'type': 'BOOL', 'direction': 'input'},
                {'name': 's', 'symbol': 's', 'type': 'ANY16' if width == 16 else 'ANY32', 'direction': 'input'},
                {'name': 'n', 'symbol': 'n', 'type': 'INT', 'direction': 'input'},
                {'name': 'd', 'symbol': 'd', 'type': 'BOOL', 'direction': 'output'},
            ] if enabled else [
                {'name': 's', 'symbol': 's', 'type': 'ANY16' if width == 16 else 'ANY32', 'direction': 'input'},
                {'name': 'n', 'symbol': 'n', 'type': 'INT', 'direction': 'input'},
            ])
            signature = {'name': symbol, 'parameters': signature_parameters, 'returns': 'BOOL',
                         'enable': enabled, 'call_kind': 'function', 'instance_required': False}
            return fake, rows, signature, operation

        for symbol in ('INT_TO_BITARR', 'INT_TO_BITARR_E', 'DINT_TO_BITARR', 'DINT_TO_BITARR_E'):
            with self.subTest(symbol=symbol):
                fake, rows, signature, operation = make_section(symbol)
                result = _n_bitarr_st_call_shape(fake, symbol, signature, rows)
                self.assertIsNotNone(result)
                if symbol == 'INT_TO_BITARR':
                    self.assertEqual(result['documented_call'], 'g_bool4[0]:=INT_TO_BITARR(g_int1,4);')
                    self.assertEqual(signature['return_channel'], 'function_return_value')
                    self.assertEqual(signature['return_binding']['argument_passing'], 'array_element_reference')
                    self.assertNotIn('ENO:=', result['documented_call'])
                elif symbol == 'INT_TO_BITARR_E':
                    self.assertEqual(result['documented_call'],
                                     'g_bool2:=INT_TO_BITARR_E(g_bool1,g_int1,4,g_bool4[0]);')
                    self.assertEqual(signature['return_channel'], 'ENO')
                else:
                    self.assertNotIn('documented_call', result)
                    self.assertEqual(signature['derived_call_shape']['argument_order'],
                                     ['EN', 's', 'n', 'd'] if symbol.endswith('_E') else ['s', 'n'])
                    self.assertEqual(signature['derived_call_shape']['return_binding'],
                                     'ENO' if symbol.endswith('_E') else 'function_return_value')

                integrated, integrated_rows, _, integrated_operation = make_section(symbol)
                integrated.chapter = '5 Type Conversion Functions'
                integrated.text = integrated_operation + '\n' + integrated._n_bitarr_page_views[114].text
                integrated._n_argument_contracts = [{'rows': integrated_rows, 'issues': []}]
                integrated_specs = _n_parameter_specs(integrated, symbol)
                integrated_signature = next(spec for spec in integrated_specs
                    if spec['dimension'] == 'parameters.st_signature')
                self.assertTrue(integrated_signature['source_verified_st_signature'])
                actual_parameters = integrated_signature['value']['parameters']
                expected_parameters = (
                    [('EN', 'BOOL', 'input', 'enable'),
                     ('s', 'ANY16' if symbol.startswith('INT_') else 'ANY32', 'input', 'source'),
                     ('n', 'INT', 'input', 'bit_count'), ('d', 'BOOL', 'output', 'bit_array_destination')]
                    if symbol.endswith('_E') else
                    [('s', 'ANY16' if symbol.startswith('INT_') else 'ANY32', 'input', 'source'),
                     ('n', 'INT', 'input', 'bit_count')])
                self.assertEqual([(row['name'], row['type'], row['direction'], row['role'])
                                  for row in actual_parameters], expected_parameters)
                self.assertEqual(actual_parameters[2 if symbol.endswith('_E') else 1]['allowed_values'],
                                 list(range(4, (32 if symbol.startswith('DINT_') else 16) + 1, 4)))
                if symbol.endswith('_E'):
                    self.assertEqual(actual_parameters[-1]['array_element_reference']['extent']['parameter'], 'n')
                else:
                    self.assertEqual(integrated_signature['value']['return_binding']['array_element_reference']['extent']['parameter'], 'n')
                if symbol == 'INT_TO_BITARR':
                    self.assertEqual(integrated_signature['value']['documented_call'],
                                     'g_bool4[0]:=INT_TO_BITARR(g_int1,4);')
                    self.assertNotIn('ENO:=', integrated_signature['value']['documented_call'])

        self.assertTrue(_publishable_documented_call('g_bool4[0]:=INT_TO_BITARR(g_int1,4);'))
        self.assertTrue(_publishable_documented_call(
            'g_bool2:=INT_TO_BITARR_E(g_bool1,g_int1,4,g_bool4[0]);'))
        self.assertFalse(_publishable_documented_call('g_bool4[0]:=INT_TO_BITARR(g_int1,n+4);'))
        self.assertFalse(_publishable_documented_call('g_bool4[0] := plausible prose;'))


if __name__ == '__main__':
    unittest.main()

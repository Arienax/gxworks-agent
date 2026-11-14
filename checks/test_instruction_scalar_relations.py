"""Source-shaped scalar relations; knowledge binding does not grant execution."""
import copy
import unittest

from plc.instruction_relations import bind_relation_facts, fact_context_intersection
from tools.inventory_instruction_semantics import _concrete_relation


def fact(identity, dimension, value, *, forms=('LD>=',), languages=('fbd', 'st', 'ladder')):
    return {'id': identity, 'dimension': dimension, 'value': value,
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [{'source_id': 'synthetic'}], 'depends_on': [], 'conditions': [],
            'scope': {'models': ['Q03UDV'], 'forms': list(forms), 'languages': list(languages)}}


class FactContextIntersectionTests(unittest.TestCase):
    def predicate(self, name):
        return {'op': 'state', 'name': name, 'type': {'kind': 'bool'}}

    def condition(self, expression):
        return {'kind': 'hardware_condition', 'predicate': expression}

    def test_exact_complement_and_conjunction_are_disjoint(self):
        p, q = self.predicate('shared_target'), self.predicate('slot_in_shared_range')
        for expression in (p, {'op': 'and', 'type': {'kind': 'bool'}, 'args': [p, q]}):
            negative = {'op': 'not', 'type': {'kind': 'bool'}, 'args': [expression]}
            positive = copy.deepcopy(expression)
            if positive['op'] == 'and':
                positive['args'].reverse()
            left, right = fact('s', 'parameters.typed_operands', {}), fact('e', 'effects.operation', {})
            left['conditions'] = [self.condition(negative)]
            right['conditions'] = [self.condition(positive)]
            before = copy.deepcopy((left, right))
            with self.subTest(expression=expression['op']):
                self.assertIsNone(fact_context_intersection(left, right))
                self.assertEqual((left, right), before)

    def test_negated_conjunction_does_not_negate_each_member(self):
        p, q = self.predicate('p'), self.predicate('q')
        left, right = fact('s', 'parameters.typed_operands', {}), fact('e', 'effects.operation', {})
        left['conditions'] = [self.condition({'op': 'not', 'type': {'kind': 'bool'}, 'args': [
            {'op': 'and', 'type': {'kind': 'bool'}, 'args': [p, q]}]})]
        right['conditions'] = [self.condition(p), {'kind': 'source_note', 'predicate': {'unknown': 'retained'}}]
        result = fact_context_intersection(left, right)
        self.assertIsNotNone(result)
        self.assertEqual(len(result[1]), 3)

    def test_parameter_usage_requires_compatible_branch(self):
        from plc.instruction_knowledge import parameter_usage_facts
        signature = fact('signature', 'parameters.typed_operands', {'operands': [
            {'name': 's', 'direction': 'input', 'type': 'DINT'}]})
        effect = fact('effect', 'effects.operation', {'parameter_usage': [
            {'parameter': 's', 'purpose': 'module memory offset'}]})
        p = self.predicate('shared_target')
        signature['conditions'] = [self.condition(p)]
        effect['conditions'] = [self.condition(p)]
        self.assertEqual(len(parameter_usage_facts([signature, effect])), 1)
        effect['conditions'] = [self.condition({'op': 'not', 'type': {'kind': 'bool'}, 'args': [p]})]
        self.assertFalse(parameter_usage_facts([signature, effect]))


class StructuredNumericContactTests(unittest.TestCase):
    def pair(self, domain='signed_BIN32', kind='DINT', composition='and'):
        signature = fact('signature', 'parameters.typed_operands', {'operands': [
            {'name': 's1', 'symbol': 'S1', 'direction': 'input', 'type': kind},
            {'name': 's2', 'symbol': 'S2', 'direction': 'input', 'type': kind}]})
        effect = fact('effect', 'effects.operation', {'operation': 'compose_comparison_contact',
            'left': 'S1', 'right': 'S2', 'data_domain': domain, 'composition': composition, 'comparison': '>='})
        return signature, effect

    def test_numeric_domains_and_compositions_bind_implicit_state(self):
        for domain, kind in [('signed_BIN16', 'ANY16'), ('signed_BIN32', 'DINT'),
                             ('REAL', 'REAL'), ('LREAL', 'LREAL')]:
            for composition in ('assign', 'and', 'or'):
                with self.subTest(domain=domain, composition=composition):
                    rows = self.pair(domain, kind, composition)
                    before = copy.deepcopy(rows)
                    relation, = bind_relation_facts(rows)
                    self.assertTrue(_concrete_relation(relation))
                    value = relation['value']
                    self.assertEqual(value['output'], 'operation_result')
                    self.assertEqual(value['outputs'][0]['expression']['op'],
                                     'ge' if composition == 'assign' else composition)
                    self.assertEqual(value['state_reads'], [] if composition == 'assign' else ['operation_result'])
                    self.assertNotIn('native_form', value)
                    self.assertEqual(rows, before)
                    self.assertNotIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'}, relation['conditions'])

    def test_canonical_numeric_mapping_signatures(self):
        for domain, mapping in [('signed_BIN16', {'kind': 'int', 'bits': 16, 'signed': True}),
                                ('signed_BIN32', {'kind': 'int', 'bits': 32, 'signed': True}),
                                ('REAL', {'kind': 'real', 'bits': 32}),
                                ('LREAL', {'kind': 'real', 'bits': 64, 'signed': False})]:
            with self.subTest(domain=domain):
                relation, = bind_relation_facts(self.pair(domain, mapping))
                self.assertTrue(_concrete_relation(relation))

    def test_non_numeric_and_invalid_or_unrelated_interfaces_are_rejected(self):
        for failure in ('string', 'date', 'time', 'unsigned', 'wrong_width', 'missing_input',
                        'extra_input', 'data_output', 'optional', 'duplicate', 'bad_composition',
                        'same_operand', 'unknown_symbol', 'missing_dependency', 'other_source',
                        'other_form', 'other_language', 'variant', 'bad_gate'):
            with self.subTest(failure=failure):
                signature, effect = self.pair()
                pins = signature['value']['operands']
                if failure in ('string', 'date', 'time'):
                    effect['value']['data_domain'] = {'string': 'ASCII_string', 'date': 'date_year_month_day',
                                                       'time': 'time_hour_minute_second'}[failure]
                elif failure == 'unsigned': pins[0]['type'] = 'DWORD'
                elif failure == 'wrong_width': pins[0]['type'] = 'INT'
                elif failure == 'missing_input': pins.pop()
                elif failure == 'extra_input': pins.append({'name': 's3', 'direction': 'input', 'type': 'DINT'})
                elif failure == 'data_output': pins.append({'name': 'd', 'direction': 'output', 'type': 'BOOL'})
                elif failure == 'optional': pins[0]['optional'] = True
                elif failure == 'duplicate': pins[1] = copy.deepcopy(pins[0])
                elif failure == 'bad_composition': effect['value']['composition'] = 'xor'
                elif failure == 'same_operand': effect['value']['right'] = 'S1'
                elif failure == 'unknown_symbol': effect['value']['left'] = 'absent'
                elif failure == 'missing_dependency': effect['depends_on'] = ['absent']
                elif failure == 'other_source': effect['sources'] = [{'source_id': 'other'}]
                elif failure == 'other_form': effect['scope']['forms'] = ['other']
                elif failure == 'other_language': effect['scope']['languages'] = ['native']
                elif failure == 'variant': effect['value']['instruction_variant'] = 'other'
                elif failure == 'bad_gate': pins.append({'name': 'EN', 'type': 'INT', 'direction': 'input', 'role': 'enable'})
                self.assertFalse(bind_relation_facts([signature, effect]))

    def test_native_contract_retains_its_existing_native_form_requirement(self):
        signature, effect = self.pair(kind='ANY32')
        signature['value']['representation'] = 'native_ladder_instruction'
        self.assertFalse(bind_relation_facts([signature, effect]))
        signature['value']['native_form'] = {'operand_order': ['s1', 's2']}
        self.assertTrue(bind_relation_facts([signature, effect]))


class DocumentedComparisonContactTests(unittest.TestCase):
    def pair(self, domain='ASCII_string', *, native=False, composition='and'):
        calendar = domain != 'ASCII_string'
        names = ['s1', 's2', 'n'] if calendar else ['s1', 's2']
        signature = fact('signature', 'parameters.typed_operands', {'operands': [
            {'name': name, 'symbol': name.upper(), 'direction': 'input',
             'type': 'ANY16' if calendar else 'STRING'} for name in names]})
        if native:
            signature['value']['native_form'] = {'operand_order': names}
        if calendar:
            fields = [('year', 1980, 2079), ('month', 1, 12), ('day', 1, 31)] if domain == 'date_year_month_day' else [
                ('hour', 0, 23), ('minute', 0, 59), ('second', 0, 59)]
            rules = {'kind': 'selected_calendar_components', 'selector': 'n',
                'components': [{'name': name, 'word_offset': i, 'select_bit': 2 - i, 'range': [low, high]}
                               for i, (name, low, high) in enumerate(fields)],
                'comparison_order': [name for name, _, _ in fields],
                'equality': 'all_selected_components_equal', 'reserved_zero_bit_range': {'first': 3, 'last': 14},
                'reserved_bits_nonzero_result': False, 'empty_selection_result': False,
                'invalid_selector_result': False, 'component_encoding': 'BIN16', 'comparison_span_words': 3,
                'current_clock_select_bit': 15, 'current_clock_ignores': 'S2',
                'current_clock_source': 'current_date' if domain == 'date_year_month_day' else 'current_time',
                'invalid_components': {'state': 'SM709', 'comparison_result': False,
                    'when': 'component_outside_declared_range_or_device_span_out_of_range',
                    'latch': 'until_reset_or_power_off', 'explicit_clear_allowed': True}}
            if domain == 'date_year_month_day':
                rules['calendar_validation'] = 'component_ranges_only_actual_date_existence_not_required'
        else:
            rules = {'kind': 'ascii_lexicographic_until_null', 'unit_bits': 8, 'terminator': 0,
                'order': 'first_different_character_code', 'equality': 'same_characters_and_terminator_position',
                'prefix_order': 'longer_is_greater', 'read_extent': 'first_difference_or_both_terminators',
                'device_range_check': 'as_compared'}
        effect = fact('effect', 'effects.operation', {'operation': 'compose_comparison_contact',
            'data_domain': domain, 'left': 'S1', 'right': 'S2', 'comparison': '<',
            'composition': composition, 'domain_comparison': rules})
        return signature, effect

    def test_ordering_and_selector_rules_survive_both_interface_bindings(self):
        for domain in ('ASCII_string', 'date_year_month_day', 'time_hour_minute_second'):
            for native in (False, True):
                for composition in ('assign', 'and', 'or'):
                    with self.subTest(domain=domain, native=native, composition=composition):
                        signature, effect = self.pair(domain, native=native, composition=composition)
                        relation, = bind_relation_facts([signature, effect])
                        self.assertTrue(_concrete_relation(relation))
                        value = relation['value']
                        self.assertEqual(value['result_relation']['domain_comparison'], effect['value']['domain_comparison'])
                        self.assertEqual(value['inputs'], ['s1', 's2'] if domain == 'ASCII_string' else ['s1', 's2', 'n'])
                        self.assertEqual(value['output'], 'operation_result')
                        self.assertNotIn('outputs', value)  # No finite-width execution expression.
                        self.assertEqual(value['state_reads'], [] if composition == 'assign' else ['operation_result'])
                        self.assertEqual(relation['depends_on'], ['effect', 'signature'])
                        missing = copy.deepcopy(effect)
                        missing['depends_on'] = ['absent']
                        self.assertFalse(bind_relation_facts([signature, missing]))

    def test_partial_or_incorrect_source_rules_never_claim_complete_comparison(self):
        for domain in ('ASCII_string', 'date_year_month_day', 'time_hour_minute_second'):
            signature, effect = self.pair(domain)
            for key in effect['value']['domain_comparison']:
                incomplete = copy.deepcopy(effect)
                del incomplete['value']['domain_comparison'][key]
                with self.subTest(domain=domain, missing=key):
                    self.assertFalse(bind_relation_facts([signature, incomplete]))
            for failure in ('extra_pin', 'wrong_type', 'output_selector', 'unknown_selector', 'same_input',
                            'missing_dependency', 'unrelated_source', 'reversed_selector_bits'):
                a, b = copy.deepcopy(signature), copy.deepcopy(effect)
                pins = a['value']['operands']
                if failure == 'extra_pin': pins.append({'name': 'extra', 'type': 'ANY16', 'direction': 'input'})
                elif failure == 'wrong_type': pins[0]['type'] = 'BOOL'
                elif failure == 'output_selector': pins[-1]['direction'] = 'output'
                elif failure == 'unknown_selector': b['value']['domain_comparison']['selector'] = 'absent'
                elif failure == 'same_input': b['value']['right'] = 'S1'
                elif failure == 'missing_dependency': b['depends_on'] = ['absent']
                elif failure == 'unrelated_source': b['sources'] = [{'source_id': 'other'}]
                elif failure == 'reversed_selector_bits' and domain != 'ASCII_string':
                    b['value']['domain_comparison']['components'][0]['select_bit'] = 0
                else:
                    continue
                # ASCII has no selector; unrelated extra metadata cannot change its two inputs.
                if failure == 'unknown_selector' and domain == 'ASCII_string':
                    continue
                with self.subTest(domain=domain, failure=failure):
                    self.assertFalse(bind_relation_facts([a, b]))


class TimeAndFindSourceRuleTests(unittest.TestCase):
    def build(self, opcode, *, complete=True, enabled=True):
        from tools import fact_structured_behavior_review as review
        record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS if r['op'] == opcode))
        if not complete:
            for key in ('boundary_behavior', 'result_flags', 'match_selection', 'string_conditions'):
                record['rule'].pop(key, None)
        source = {'source_id': 'synthetic'}
        rows = review._form_facts(record, opcode + ('_E' if enabled else ''), enabled,
            'FX3U', {'text': 'synthetic'}, [source], None, 'source_verified')
        return rows + review._structured_ladder_facts(rows)

    def test_actual_source_producer_signatures_keep_bound_qualifiers(self):
        for opcode in ('ADD_TIME', 'FIND'):
            for enabled in (False, True):
                with self.subTest(opcode=opcode, enabled=enabled):
                    rows = self.build(opcode, enabled=enabled)
                    relations = bind_relation_facts(rows)
                    self.assertEqual(len(relations), 2)
                    for relation in relations:
                        value = relation['value']
                        self.assertTrue(_concrete_relation(relation))
                        self.assertEqual(value['inputs'], ['_IN1', '_IN2'])
                        self.assertEqual(len(value['operand_binding']['inputs']), 2)
                        keys = ('boundary_behavior', 'result_flags') if opcode == 'ADD_TIME' else ('match_selection', 'string_conditions')
                        for key in keys: self.assertEqual(value[key], value['result_relation'][key])
                        if opcode == 'FIND':
                            self.assertEqual(value['result_relation']['position_origin'], 1)
                            self.assertEqual(value['result_relation']['not_found'], 0)
                        if enabled: self.assertTrue(value['operand_binding']['execution_arguments'])

    def test_missing_qualifiers_do_not_claim_complete_effects(self):
        for opcode in ('ADD_TIME', 'FIND'):
            relations = bind_relation_facts(self.build(opcode, complete=False))
            self.assertEqual(len(relations), 2)
            for relation in relations:
                self.assertFalse(_concrete_relation(relation))
                self.assertTrue(relation['value']['unresolved_items'])

    def test_labels_cannot_replace_complete_source_clauses(self):
        for opcode, key in [('ADD_TIME', 'boundary_behavior'), ('ADD_TIME', 'result_flags'),
                            ('FIND', 'match_selection'), ('FIND', 'string_conditions')]:
            rows = self.build(opcode)
            for effect in rows:
                if effect['dimension'] == 'effects.result_rule':
                    effect['value'][key] = 'documented'
            with self.subTest(opcode=opcode, key=key):
                relations = bind_relation_facts(rows)
                self.assertEqual(len(relations), 2)
                self.assertTrue(all(not _concrete_relation(relation) for relation in relations))

    def test_bad_signature_and_unresolved_dependency_never_bind(self):
        for opcode in ('ADD_TIME', 'FIND'):
            for failure in ('wrong_input_type', 'wrong_output_type', 'missing_input', 'extra_output',
                            'missing_dependency', 'wrong_explicit_binding'):
                rows = self.build(opcode)
                signatures = [f for f in rows if f['dimension'] == 'parameters.typed_operands']
                effects = [f for f in rows if f['dimension'] == 'effects.result_rule']
                for signature in signatures:
                    pins = signature['value']['operands']
                    data = [p for p in pins if not p.get('enable_pin')]
                    if failure == 'wrong_input_type': data[0]['type'] = 'BOOL'
                    elif failure == 'wrong_output_type': data[-1]['type'] = 'BOOL'
                    elif failure == 'missing_input': pins.remove(data[0])
                    elif failure == 'extra_output': pins.append({'name': 'other', 'direction': 'output', 'type': 'INT'})
                for effect in effects:
                    if failure == 'missing_dependency': effect.setdefault('depends_on', []).append('absent')
                    elif failure == 'wrong_explicit_binding': effect['value']['inputs'] = ['absent', '_IN2']
                with self.subTest(opcode=opcode, failure=failure): self.assertFalse(bind_relation_facts(rows))


if __name__ == '__main__': unittest.main()


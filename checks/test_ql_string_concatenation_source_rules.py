"""Source-bound native facts for the Q/L concatenation and transfer forms."""
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace

from tools.fact_ql_behavior_review import (
    reviewed_ql_behavior,
    _string_concatenation_details,
    _string_concatenation_parameter_storage,
)


class QLStringConcatenationSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        with sqlite3.connect((private/'fx3u_knowledge.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as source:
            cls.facts, _ = reviewed_ql_behavior(source,
                manuals=['sh080784engn','sh080809engx','sh080783engp'],
                opcodes=['CONCAT','CONCAT_E','$+','$+P','$MOV','$MOVP'],
                source_pdf_root=private/'sources')
        cls.rows = {opcode: cls.facts[('Q03UDV', opcode)]
                    for opcode in ('$+','$+P','$MOV','$MOVP')}
        cls.ql_functions = {opcode: cls.facts[('Q03UDV', opcode)]
                            for opcode in ('CONCAT','CONCAT_E')}

    def test_each_printed_P_form_has_its_own_source_bound_trigger(self):
        expected = {
            '$+': 'each_evaluation_when_execution_condition_true',
            '$+P': 'rising_edge_transition',
            '$MOV': 'each_evaluation_when_execution_condition_true',
            '$MOVP': 'rising_edge_transition',
        }
        for opcode, trigger in expected.items():
            facts = [f for f in self.rows[opcode] if f['dimension'] == 'execution.trigger']
            self.assertEqual(len(facts), 2)
            self.assertTrue(all(f['status'] == 'source_verified' for f in facts))
            self.assertTrue(all(f['value']['trigger'] == trigger for f in facts))
            self.assertTrue(all(f['value']['source_form'] == opcode for f in facts))
            self.assertTrue(all(any(s['manual_id'] == 'sh080783engp' and s['pdf_pages']
                                    for s in f['sources']) for f in facts))

    def test_native_word_heads_are_bounded_to_the_reviewed_D_subset(self):
        for opcode, expected_count in (('$+', 2), ('$+P', 2), ('$MOV', 1), ('$MOVP', 1)):
            typed = [f for f in self.rows[opcode]
                     if f['id'].startswith('authored:ql:native_arguments')
                     and f['dimension'] == 'parameters.typed_operands']
            self.assertEqual(len(typed), expected_count)
            for fact in typed:
                self.assertEqual(fact['status'], 'source_verified')
                self.assertEqual(fact['verification'], 'source_checked')
                for operand in fact['value']['operands']:
                    self.assertEqual(operand['type'], 'STRING')
                    self.assertEqual(operand['range']['device_prefixes'], ['D'])
                    self.assertEqual(operand['range']['construction_coverage'],
                                     'D_only_source_authorized_subset')
                    self.assertEqual(operand['string_layout']['character_order'],
                                     'low_byte_then_high_byte')
                self.assertTrue(any(s['manual_id'] == 'sh080809engx'
                                    for s in fact['sources']))

    def test_transfer_facts_keep_own_P_manual_extents_and_literal_limit(self):
        for opcode in ('$MOV', '$MOVP'):
            facts = [f for f in self.rows[opcode]
                     if f['id'] == 'authored:ql:string_transfer_processing:effects.operation']
            self.assertEqual(len(facts), 1)
            fact = facts[0]
            self.assertEqual(fact['status'], 'source_verified')
            self.assertEqual(fact['value']['operation'], 'copy_NULL_terminated_string')
            self.assertEqual(fact['value']['source_literal_maximum_characters'], 32)
            self.assertEqual(fact['value']['maximum_device_string_characters'], 16383)
            self.assertEqual(fact['value']['destination_extent'], 'include_first_00H')
            self.assertTrue(any(s['manual_id'] == 'sh080783engp' and 252 in s['pdf_pages']
                                for s in fact['sources']))

    def test_n_manual_concat_signatures_preserve_optional_tail_and_e_outputs(self):
        signatures = {}
        for opcode, facts in self.ql_functions.items():
            signatures[opcode] = next(fact for fact in facts
                if fact['id'] == 'authored:ql:st:parameters.st_signature')
            self.assertEqual(signatures[opcode]['status'], 'source_verified')
            self.assertTrue(any(source['manual_id'] == 'sh080784engn'
                                and 159 in source['pdf_pages']
                                for source in signatures[opcode]['sources']))

        plain = signatures['CONCAT']['value']
        self.assertEqual(plain['returns'], 'STRING')
        self.assertFalse(plain['enable'])
        self.assertEqual([p['name'] for p in plain['parameters']],
                         [f's{i}' for i in range(1, 29)])
        self.assertTrue(all(p.get('optional') is True for p in plain['parameters'][2:]))
        self.assertTrue(all(p.get('optional') is not True for p in plain['parameters'][:2]))

        enabled = signatures['CONCAT_E']['value']
        self.assertEqual(enabled['returns'], 'BOOL')
        self.assertTrue(enabled['enable'])
        self.assertEqual([p['name'] for p in enabled['parameters']],
                         ['EN', *[f's{i}' for i in range(1, 29)], 'd'])
        self.assertTrue(all(p.get('optional') is True for p in enabled['parameters'][3:-1]))
        self.assertEqual(enabled['parameters'][-1]['direction'], 'output')
        self.assertEqual(enabled['parameters'][-1]['name'], 'd')

        for opcode, expected in (('CONCAT', (2, 28)), ('CONCAT_E', (4, 30))):
            arity = next(fact for fact in self.ql_functions[opcode]
                         if fact['dimension'] == 'parameters.arity'
                         and fact['value'].get('count_kind') == 'ST_call_arguments')
            self.assertEqual((arity['value']['minimum'], arity['value']['maximum']), expected)

    def test_missing_clause_or_byte_drawing_cannot_synthesize_concatenation_storage(self):
        clauses = [
            'Connects the character string data specified for (s2) after the character string data specified for (s1) and stores the result to the variable specified for (d)',
            'This instruction ignores the 00H which indicates the end of character string data specified for (s1)',
            'The number of device points starting from the device specified in (d) is insufficient to store all character strings',
            'The device numbers specified by (s2) and (d) overlap',
            'The character string of (s1), (s2) and (d) exceeds 16383 characters', 'b15 b8','b7 b0']
        specs = [{'value':{'operands':[{'name':'d','type':'STRING'}]},'patterns':('own_argument',)}]
        def section(text):
            return SimpleNamespace(manual='sh080783engp', title='Character string data concatenation',
                symbols=['$+','$+P'], page_pairs=[(198,199)], text=text,
                bind=lambda witness:[('own',199)])
        for missing in range(len(clauses)):
            current = section(' '.join(c for i,c in enumerate(clauses) if i != missing))
            self.assertIsNone(_string_concatenation_details(current,'$+'))
            self.assertEqual(_string_concatenation_parameter_storage(current,'$+',specs),specs)
        current = section(' '.join(clauses))
        self.assertIsNotNone(_string_concatenation_details(current,'$+'))
        for manual, title, symbol, pages in (
            ('sh080809engx','Character string data concatenation','$+',[(198,199)]),
            ('sh080783engp','Other title','$+',[(198,199)]),
            ('sh080783engp','Character string data concatenation','CONCAT',[(198,199)]),
            ('sh080783engp','Character string data concatenation','$+P',[(197,197)]),
        ):
            current.manual, current.title, current.page_pairs = manual, title, pages
            self.assertEqual(_string_concatenation_parameter_storage(current,symbol,specs),specs)
        current = section(' '.join(clauses));current.bind=lambda witness:[]
        self.assertEqual(_string_concatenation_parameter_storage(current,'$+',specs),specs)


if __name__ == '__main__':
    unittest.main()

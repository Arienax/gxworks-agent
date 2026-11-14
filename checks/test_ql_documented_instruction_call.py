"""QL ST instruction calls retain only their literal, parsed source witness."""
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace

from tools.fact_ql_behavior_review import (
    _parameter_p_specs,
    _publishable,
    reviewed_ql_behavior,
)


def _source_section(symbol, witness):
    operands = [
        ('EN', 'BOOL', 'input', 'enable'),
        ('ENO', 'BOOL', 'output', 'execution_status'),
        ('s', 'REAL', 'input', 'source'),
        ('d', 'REAL', 'output', 'destination'),
    ]
    rows = [
        {'operand': {'name': name, 'type': kind, 'direction': direction, 'role': role},
         'witness': f'argument-table-row-{name}'}
        for name, kind, direction, role in operands
    ]
    call = {'name': symbol, 'arguments': ['EN', 's', 'd']}
    if witness is not None:
        call['witness'] = witness
    contract = {
        'page': 550,
        'allowed': [symbol],
        'rows': rows,
        'calls': [call],
        'block_forms': {symbol: ['EN', 'ENO', 's', 'd']},
        'issues': [],
        'shared_allowed': [],
    }
    section = SimpleNamespace(
        manual='sh080783engp',
        revision='P',
        symbols=[symbol],
        heading_line=f'{symbol} source heading',
        title=f'{symbol} source panel',
        text='',
        _parameter_contracts=[contract],
    )
    return section


class QLDocumentedInstructionCall(unittest.TestCase):
    def test_generic_instruction_signature_carries_exact_eno_assignment_and_location(self):
        witness = 'ENO:= SIN (EN, s, d);'
        signature = next(spec for spec in _parameter_p_specs(
            _source_section('SIN', witness), 'SIN')
            if spec['dimension'] == 'parameters.st_signature')

        self.assertEqual(signature['value']['documented_call'], 'ENO:=SIN(EN,s,d);')
        self.assertEqual(signature['value']['documented_call_source'],
                         {'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 550})
        self.assertTrue(_publishable(signature['value']))
        self.assertIn(witness, signature['patterns'])
        self.assertEqual(signature['value']['call_kind'], 'instruction')
        self.assertEqual(signature['value']['returns'], 'BOOL')

    def test_missing_or_non_assignment_witness_does_not_create_documented_call(self):
        for witness in (None, 'SIN(EN,s,d);', 'ENO:=COS(EN,s,d);'):
            with self.subTest(witness=witness):
                signature = next(spec for spec in _parameter_p_specs(
                    _source_section('SIN', witness), 'SIN')
                    if spec['dimension'] == 'parameters.st_signature')
                self.assertNotIn('documented_call', signature['value'])
                self.assertNotIn('documented_call_source', signature['value'])

    def test_identifier_argument_list_is_publishable_without_changing_witness_rules(self):
        self.assertTrue(_publishable({'documented_call': 'ENO:=RIGHT(EN,s,n,d);'}))
        self.assertFalse(_publishable({'documented_call': 'ENO:=RIGHT(EN,s,n,d+1);'}))


class QLRightLeftSourceCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        source = sqlite3.connect(
            (private / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1',
            uri=True)
        try:
            cls.facts, _receipt = reviewed_ql_behavior(
                source,
                manuals=['sh080783engp'],
                opcodes=['RIGHT', 'LEFT'],
                source_pdf_root=private / 'sources')
        finally:
            source.close()

    def test_right_call_from_page_529_is_not_dropped_for_four_arguments(self):
        signature = next(fact for fact in self.facts[('Q03UDV', 'RIGHT')]
                         if fact['dimension'] == 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['documented_call'], 'ENO:=RIGHT(EN,s,n,d);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 529})
        self.assertTrue(any(source.get('manual_id') == 'sh080783engp'
                            and source.get('revision') == 'P'
                            and 529 in source.get('pdf_pages', ())
                            for source in signature['sources']))

    def test_left_call_shape_is_derived_from_its_table_and_st_rules(self):
        signature = next(fact for fact in self.facts[('Q03UDV', 'LEFT')]
                         if fact['dimension'] == 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['source_panel_name'], 'RIGHT')
        self.assertNotIn('documented_call', signature['value'])
        self.assertNotIn('documented_call_source', signature['value'])
        self.assertEqual(signature['value']['derived_call_shape'], {
            'name': 'LEFT',
            'argument_order': ['EN', 's', 'n', 'd'],
            'call_kind': 'instruction',
            'returns': 'BOOL',
            'enable': True,
            'return_binding': 'ENO',
            'derivation': 'grouped_instruction_parameter_table_plus_general_st_call_rule',
        })
        self.assertCountEqual(signature['depends_on'], [
            'authored:ql:argument_table_st:parameters.arity',
            'authored:ql:argument_table_st:parameters.order',
            'authored:ql:cpu_applicability:constraints.cpu_availability',
        ])
        source_pages = {(source['manual_id'], source['revision'], tuple(source['pdf_pages']))
                        for source in signature['sources']}
        self.assertTrue({
            ('sh080783engp', 'P', (529,)),
            ('sh080783engp', 'P', (530,)),
            ('structured_fundamentals_o', 'O', (83,)),
            ('structured_fundamentals_o', 'O', (29,)),
        } <= source_pages)

        order = next(fact for fact in self.facts[('Q03UDV', 'LEFT')]
                     if fact['id'] == 'authored:ql:argument_table_st:parameters.order')
        self.assertEqual(order['value']['symbols'], ['EN', 's', 'n', 'd'])
        self.assertEqual(order['value']['order_source'],
                         'grouped_instruction_parameter_table_plus_general_st_call_rule')

    def test_left_keeps_explicit_cpu_rejections(self):
        for model in ('Q00J', 'Q00', 'Q01'):
            with self.subTest(model=model):
                availability = next(fact for fact in self.facts[(model, 'LEFT')]
                                    if fact['dimension'] == 'constraints.cpu_availability')
                self.assertEqual(availability['value']['model_states'][model], 'unsupported')


class QLAdrsetSourceCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        source = sqlite3.connect(
            (private / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1',
            uri=True)
        try:
            cls.facts, cls.receipt = reviewed_ql_behavior(
                source,
                manuals=['sh080783engp', 'sh080809engx'],
                opcodes=['ADRSET', 'ADRSETP'],
                source_pdf_root=private / 'sources')
        finally:
            source.close()

    def test_original_two_form_scope_and_cpu_headers_cover_all_53_ql_models(self):
        self.assertEqual(len(self.facts), 106)
        for opcode in ('ADRSET', 'ADRSETP'):
            owners = {(model, form) for model, form in self.facts if form == opcode}
            self.assertEqual(len(owners), 53)
            for model, form in owners:
                cpu = next(fact for fact in self.facts[(model, form)]
                           if fact['dimension'] == 'constraints.cpu_availability'
                           and fact['scope'].get('languages') == ['st', 'fbd', 'ladder'])
                self.assertEqual(cpu['status'], 'source_verified')
                self.assertEqual(cpu['value']['model_states'], {model: 'supported'})
                self.assertEqual(cpu['sources'][0]['pdf_pages'], [730])

    def test_adrset_literal_and_n_to_s_reconciliation_remain_page_bound(self):
        signature = next(fact for fact in self.facts[('Q03UDV', 'ADRSET')]
                         if fact['dimension'] == 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['documented_call'], 'ENO:=ADRSET(EN,s,d);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 730})
        self.assertEqual(signature['value']['source_disagreements'], [{
            'field': 'parameters.s.name',
            'printed_table_label': 'n',
            'call_and_processing_label': 's',
            'resolution': 'use_same_page_call_and_operation_label_s',
            'basis': 'same_page_call_panel_processing_description_setting_row_and_output_description',
            'manual_id': 'sh080783engp',
            'revision': 'P',
            'pdf_page': 730,
        }])

    def test_adrsetp_is_derived_from_its_own_form_list_not_a_fake_literal_call(self):
        signature = next(fact for fact in self.facts[('Q03UDV', 'ADRSETP')]
                         if fact['dimension'] == 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['source_panel_name'], 'ADRSET')
        self.assertNotIn('documented_call', signature['value'])
        self.assertNotIn('documented_call_source', signature['value'])
        self.assertEqual(signature['value']['derived_call_shape'], {
            'name': 'ADRSETP',
            'argument_order': ['EN', 's', 'd'],
            'call_kind': 'instruction',
            'returns': 'BOOL',
            'enable': True,
            'return_binding': 'ENO',
            'derivation': 'grouped_instruction_parameter_table_plus_general_st_call_rule',
        })
        self.assertTrue({
            'authored:ql:argument_table_st:parameters.arity',
            'authored:ql:argument_table_st:parameters.order',
            'authored:ql:adrset_execution_condition:execution.trigger',
        } <= set(signature.get('depends_on', ())))
        source_pages = {(source['manual_id'], source['revision'], tuple(source['pdf_pages']))
                        for source in signature['sources']}
        self.assertTrue({
            ('sh080783engp', 'P', (730,)),
            ('structured_fundamentals_o', 'O', (83,)),
            ('structured_fundamentals_o', 'O', (29,)),
        } <= source_pages)

    def test_p_execution_condition_rows_keep_each_form_trigger(self):
        expected = {
            'ADRSET': ('each_evaluation_when_execution_condition_true', 'EN_true'),
            'ADRSETP': ('rising_edge_transition', 'OFF_to_ON'),
        }
        for symbol, (trigger, enabled_when) in expected.items():
            with self.subTest(symbol=symbol):
                fact = next(fact for fact in self.facts[('Q03UDV', symbol)]
                            if fact['id'] == 'authored:ql:adrset_execution_condition:execution.trigger')
                self.assertEqual(fact['status'], 'source_verified')
                self.assertEqual(fact['value']['trigger'], trigger)
                self.assertEqual(fact['value']['execution_input'], 'EN')
                self.assertEqual(fact['value']['enabled_when'], enabled_when)
                self.assertEqual(fact['value']['source_form'], symbol)
                self.assertEqual(fact['sources'][0]['pdf_pages'], [730])


class QLAndpchkGroupedFormSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        source = sqlite3.connect(
            (private / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1',
            uri=True)
        try:
            cls.facts, _receipt = reviewed_ql_behavior(
                source,
                manuals=['sh080783engp'],
                opcodes=['ANDPCHK'],
                source_pdf_root=private / 'sources')
        finally:
            source.close()

    def test_exact_original_members_keep_their_own_CPU_column_state(self):
        owners = {(model, form) for model, form in self.facts if form == 'ANDPCHK'}
        self.assertEqual(len(owners), 53)
        supported = {
            'Q02', 'Q02H', 'Q02PH', 'Q06H', 'Q06PH', 'Q12H', 'Q12PH',
            'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH',
        }
        self.assertEqual({model for model, _form in owners
                          if next(f for f in self.facts[(model, 'ANDPCHK')]
                                  if f['dimension'] == 'constraints.cpu_availability'
                                  )['value']['available'] is True}, supported)
        self.assertEqual(len(owners) - len(supported), 42)

    def test_grouped_FBD_form_does_not_borrow_LDPCHK_ST_call(self):
        facts = self.facts[('Q02', 'ANDPCHK')]
        cpu = next(f for f in facts if f['dimension'] == 'constraints.cpu_availability')
        self.assertEqual(cpu['status'], 'source_verified')
        fbd = next(f for f in facts if f['dimension'] == 'parameters.fbd_signature')
        self.assertEqual(fbd['status'], 'source_verified')
        self.assertEqual(fbd['value']['representation'], 'fbd_instruction_block')
        self.assertEqual(fbd['value']['pins'], [
            {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'enable'},
            {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'role': 'execution_status'},
            {'name': 's', 'type': 'STRING', 'direction': 'input', 'role': 'file_name',
             'passing': 'device_reference', 'addressing': 'start_device'},
        ])
        self.assertFalse(any(f['dimension'] == 'parameters.st_signature'
                             for f in facts))
        self.assertFalse(any('documented_call' in f.get('value', {})
                             and f['value'].get('documented_call', '').startswith('ENO:=ANDPCHK(')
                             for f in facts))

    def test_source_negative_models_do_not_gain_FBD_or_ST_signatures(self):
        for model, form in sorted((model, form) for model, form in self.facts
                                  if form == 'ANDPCHK'):
            with self.subTest(model=model):
                facts = self.facts[(model, form)]
                cpu = next(f for f in facts if f['dimension'] == 'constraints.cpu_availability')
                if cpu['value']['available'] is True:
                    continue
                self.assertEqual(cpu['status'], 'source_verified')
                self.assertFalse(any(f['dimension'] in {'parameters.fbd_signature',
                                                        'parameters.st_signature'}
                                     for f in facts))


class QLBcddaRevisionPSourceCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        source = sqlite3.connect(
            (private / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1',
            uri=True)
        try:
            extracted, _receipt = reviewed_ql_behavior(
                source,
                manuals=['sh080783engp'],
                opcodes=['BCDDA', 'BCDDAP'],
                source_pdf_root=private / 'sources')
            cls.facts = {key: facts for key, facts in extracted.items()
                         if key[1] in {'BCDDA', 'BCDDAP'}}
        finally:
            source.close()

    def _fact(self, model, opcode, dimension):
        return next(fact for fact in self.facts[(model, opcode)]
                    if fact['dimension'] == dimension)

    def test_original_p_forms_retain_all_53_models_and_source_cpu_negatives(self):
        self.assertEqual(len(self.facts), 106)
        expected_unsupported = {'Q00', 'Q00J', 'Q01'}
        for opcode in ('BCDDA', 'BCDDAP'):
            owners = {model for model, form in self.facts if form == opcode}
            self.assertEqual(len(owners), 53)
            for model in owners:
                cpu = self._fact(model, opcode, 'constraints.cpu_availability')
                self.assertEqual(cpu['status'], 'source_verified')
                self.assertEqual(cpu['scope']['models'], [model])
                self.assertEqual(cpu['scope']['forms'], [opcode])
                if model in expected_unsupported:
                    self.assertEqual(cpu['value']['model_states'], {model: 'unsupported'})
                    self.assertFalse(any(fact['dimension'] == 'parameters.st_signature'
                                         and fact['value'].get('available') is True
                                         for fact in self.facts[(model, opcode)]))
                else:
                    self.assertEqual(cpu['value']['model_states'], {model: 'supported'})
                    self.assertTrue(any(fact['dimension'] == 'parameters.st_signature'
                                        and fact['value'].get('available') is True
                                        for fact in self.facts[(model, opcode)]))

    def test_bcdda_uses_its_literal_page_484_st_call(self):
        signature = self._fact('Q03UDV', 'BCDDA', 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['documented_call'], 'ENO:=BCDDA(EN,s,d);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 484})
        self.assertEqual(signature['value']['returns'], 'BOOL')
        self.assertEqual(signature['value']['source_panel_name'], 'BCDDA')
        self.assertTrue({
            'authored:ql:argument_table_st:parameters.typed_operands',
            'authored:ql:argument_table_st:parameters.arity',
            'authored:ql:argument_table_st:parameters.order',
            'authored:ql:cpu_applicability:constraints.cpu_availability',
        } <= set(signature.get('depends_on', ())))

    def test_bcddap_statement_binds_its_page_486_sample_without_invented_return(self):
        signature = self._fact('L02', 'BCDDAP', 'parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['invocation_style'], 'statement')
        self.assertEqual(signature['value']['documented_call'],
                         'BCDDAP(SM400,Var_W0,Var_D0);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 486})
        self.assertEqual(signature['value']['documented_parameter_panel_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 484})
        self.assertEqual(signature['value']['returns'], {
            'role': 'not_assigned_in_documented_statement'})
        self.assertEqual(signature['value']['execution_output'], {
            'name': 'ENO', 'type': 'BOOL', 'direction': 'output'})
        self.assertEqual(signature['value']['documented_argument_bindings'], [
            {'token': 'SM400', 'parameter': 'EN'},
            {'token': 'Var_W0', 'parameter': 's'},
            {'token': 'Var_D0', 'parameter': 'd'},
        ])
        sample = self._fact('L02', 'BCDDAP', 'parameters.source_call_sample')
        self.assertEqual(sample['sources'][0]['pdf_pages'], [486])
        self.assertFalse(sample['value']['result_assignment_present'])

        cpu = self._fact('L02', 'BCDDAP', 'constraints.cpu_availability')
        self.assertTrue(any(source.get('manual_id') == 'sh080783engp'
                            and source.get('revision') == 'P'
                            and 484 in source.get('pdf_pages', ())
                            for source in cpu['sources']))
        self.assertFalse(any(source.get('manual_id') == 'sh080783engp'
                             and 486 in source.get('pdf_pages', ())
                             for source in cpu['sources']))
        for dimension in ('parameters.arity', 'parameters.order'):
            fact = self._fact('L02', 'BCDDAP', dimension)
            page_sources = {(source['manual_id'], source['revision'], tuple(source['pdf_pages']))
                            for source in fact['sources']}
            self.assertIn(('sh080783engp', 'P', (484,)), page_sources)
            self.assertIn(('sh080783engp', 'P', (486,)), page_sources)


class QLBinhaRevisionPSourceCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private = Path('D:/gxworks-agent-private/resources/knowledge')
        source = sqlite3.connect(
            (private / 'fx3u_knowledge.sqlite').as_uri() + '?mode=ro&immutable=1',
            uri=True)
        try:
            extracted, _receipt = reviewed_ql_behavior(
                source,
                manuals=['sh080783engp'],
                opcodes=['BINHA', 'BINHAP'],
                source_pdf_root=private / 'sources')
            cls.facts = {key: facts for key, facts in extracted.items()
                         if key[1] in {'BINHA', 'BINHAP'}}
        finally:
            source.close()

    def _fact(self, model, opcode, fact_id):
        return next(fact for fact in self.facts[(model, opcode)]
                    if fact['id'] == fact_id)

    def _cpu_fact(self, model, opcode):
        return next(fact for fact in self.facts[(model, opcode)]
                    if fact['dimension'] == 'constraints.cpu_availability'
                    and 'st' in fact.get('scope', {}).get('languages', ()))

    def test_original_forms_keep_all_53_models_and_cpu_negatives(self):
        self.assertEqual(len(self.facts), 106)
        unsupported = {'Q00', 'Q00J', 'Q01'}
        for opcode in ('BINHA', 'BINHAP'):
            owners = {model for model, form in self.facts if form == opcode}
            self.assertEqual(len(owners), 53)
            for model in owners:
                cpu = self._cpu_fact(model, opcode)
                self.assertEqual(cpu['status'], 'source_verified')
                self.assertEqual(cpu['scope']['models'], [model])
                self.assertEqual(cpu['scope']['forms'], [opcode])
                signatures = [fact for fact in self.facts[(model, opcode)]
                              if fact['dimension'] == 'parameters.st_signature']
                if model in unsupported:
                    self.assertEqual(cpu['value']['model_states'], {model: 'unsupported'})
                    self.assertEqual(signatures, [])
                else:
                    self.assertEqual(cpu['value']['model_states'], {model: 'supported'})
                    self.assertEqual(len(signatures), 1)

    def test_binha_retains_its_own_page_481_eno_assignment(self):
        signature = self._fact('Q03UDV', 'BINHA',
            'authored:ql:argument_table_st:parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['documented_call'], 'ENO:=BINHA(EN,s,d);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 481})
        self.assertEqual(signature['value']['returns'], 'BOOL')
        self.assertEqual(signature['value']['source_panel_name'], 'BINHA')
        self.assertNotIn('invocation_style', signature['value'])
        self.assertNotIn('source_call_sample_fact_id', signature['value'])
        self.assertTrue({
            'authored:ql:argument_table_st:parameters.typed_operands',
            'authored:ql:argument_table_st:parameters.arity',
            'authored:ql:argument_table_st:parameters.order',
            'authored:ql:cpu_applicability:constraints.cpu_availability',
        } <= set(signature.get('depends_on', ())))

    def test_binhap_uses_page_483_statement_without_assigning_eno(self):
        signature = self._fact('Q03UDV', 'BINHAP',
            'authored:ql:argument_table_st:parameters.st_signature')
        self.assertEqual(signature['status'], 'source_verified')
        self.assertEqual(signature['value']['source_panel_name'], 'BINHA')
        self.assertEqual(signature['value']['invocation_style'], 'statement')
        self.assertEqual(signature['value']['documented_call'],
                         'BINHAP(SM400,Var_W0,Var_D0);')
        self.assertEqual(signature['value']['documented_call_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 483})
        self.assertEqual(signature['value']['documented_parameter_panel_source'], {
            'manual_id': 'sh080783engp', 'revision': 'P', 'pdf_page': 481})
        self.assertEqual(signature['value']['returns'], {
            'role': 'not_assigned_in_documented_statement'})
        self.assertEqual(signature['value']['execution_output'], {
            'name': 'ENO', 'type': 'BOOL', 'direction': 'output'})
        self.assertEqual(signature['value']['documented_argument_bindings'], [
            {'token': 'SM400', 'parameter': 'EN'},
            {'token': 'Var_W0', 'parameter': 's'},
            {'token': 'Var_D0', 'parameter': 'd'},
        ])
        arity = self._fact('Q03UDV', 'BINHAP',
            'authored:ql:argument_table_st:parameters.arity')
        self.assertEqual(arity['value'], {
            'count_kind': 'call_arguments', 'minimum': 3, 'maximum': 3})
        order = self._fact('Q03UDV', 'BINHAP',
            'authored:ql:argument_table_st:parameters.order')
        self.assertEqual(order['value'], {
            'order_source': 'documented_example_binding',
            'symbols': ['EN', 's', 'd']})
        for fact in (arity, order):
            page_sources = {(source['manual_id'], source['revision'], tuple(source['pdf_pages']))
                            for source in fact['sources']}
            self.assertIn(('sh080783engp', 'P', (481,)), page_sources)
            self.assertIn(('sh080783engp', 'P', (483,)), page_sources)
        sample = self._fact('Q03UDV', 'BINHAP',
            'authored:ql:binhap_st_example:parameters.source_call_sample')
        self.assertEqual(sample['status'], 'source_verified')
        self.assertEqual(sample['sources'][0]['pdf_pages'], [483])
        self.assertFalse(sample['value']['result_assignment_present'])
        self.assertIn(sample['id'], signature.get('depends_on', ()))
        self.assertTrue({
            'authored:ql:argument_table_st:parameters.typed_operands',
            'authored:ql:argument_table_st:parameters.arity',
            'authored:ql:argument_table_st:parameters.order',
            'authored:ql:cpu_applicability:constraints.cpu_availability',
        } <= set(signature.get('depends_on', ())))


if __name__ == '__main__':
    unittest.main()

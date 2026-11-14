"""FX K call signatures require a complete source example and exact header scope."""
import json
import unittest
import sqlite3
from pathlib import Path
from unittest import mock
from unittest import mock

from tools import fact_structured_behavior_review as review

PRIVATE = Path(r"D:\gxworks-agent-private")
SOURCE_DB = PRIVATE / "resources/knowledge/fx3u_knowledge.sqlite"
FUNCTION_PDF = PRIVATE / "resources/knowledge/sources/JY997D34801-K_FXCPU_Application_Functions.pdf"


class FxStructuredKCallSourceTests(unittest.TestCase):
    def test_character_string_forms_require_their_own_complete_k_call_samples(self):
        calls = {
            'MID': ('MID(Label1,D10,D20);',
                    'MID_E(X000,Label1,D10,D20,Label2);'),
            'CONCAT': ('Label3:=CONCAT(Label1,Label2);',
                       'CONCAT_E(X000,Label1,Label2,Label3);'),
            'INSERT': ('INSERT(Label1,Label2,D20);',
                       'INSERT_E(X000,Label1,Label2,D20,Label3);'),
            'DELETE': ('DELETE(Label1,D10,D20);',
                       'DELETE_E(X000,Label1,D10,D20,Label2);'),
            'REPLACE': ('REPLACE(Label1,Label2,D20,D30);',
                        'REPLACE_E(X000,Label1,Label2,D20,D30,Label3);'),
        }
        records = {record['op']: record for record in review._all_records()}
        for base, (plain_sample, enabled_sample) in calls.items():
            record = records[base]
            for form, call_form, sample in (
                    (base, record['st_plain'], plain_sample),
                    (base + '_E', record['st_enabled'], enabled_sample)):
                with self.subTest(form=form):
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    self.assertEqual(
                        review._call_verification(
                            call_form + ' ' + sample, call_form,
                            allow_complete_sample=True),
                        'documented_arguments_verified')

    def test_new_conversion_forms_require_same_name_samples_and_k_header_facts(self):
        targets = {
            'INT_TO_STR': ('_INT', 'Output_label'),
            'DINT_TO_STR': ('_DINT', 'Output_label'),
            'STR_TO_INT': ('_STRING', 'Output_label'),
            'STR_TO_DINT': ('_STRING', 'Output_label'),
            'BOOL_TO_TIME': ('_BOOL', 'Output_label'),
            'WORD_TO_TIME': ('_WORD', 'Output_label'),
            'DWORD_TO_TIME': ('_DWORD', 'Output_label'),
            'TIME_TO_DWORD': ('_TIME', 'Output_label'),
        }
        for base, (source, output) in targets.items():
            for form, call_form in (
                    (base, f'{base}({source})'),
                    (base + '_E', f'{base}_E(EN,{source},{output})')):
                with self.subTest(form=form):
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    sample = f'{form}(X000,Label1,Label2);' if form.endswith('_E') else f'{form}(Label1);'
                    self.assertEqual(
                        review._call_verification(
                            f'{call_form} {sample}', call_form, allow_complete_sample=True),
                        'documented_arguments_verified')

    def test_bit_output_conversion_forms_bind_own_k_samples_and_headers(self):
        expected = {
            'INT_TO_BOOL': ('5.9', (58, 59)),
            'DINT_TO_BOOL': ('5.10', (60, 60)),
            'WORD_TO_BOOL': ('5.26', (93, 94)),
            'DWORD_TO_BOOL': ('5.27', (95, 95)),
            'TIME_TO_BOOL': ('5.43', (128, 128)),
        }
        retained = set(review.BIT_OUTPUT_RETENTION_GROUPS['conversion'])
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {record['op']: record for record in review._all_records()}
            for base, (section, pages) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, section, pages)
                scope = review._model_scope(row)
                with self.subTest(base=base):
                    self.assertEqual(record['section'], section)
                    self.assertEqual(record['pages'], pages)
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(scope['supported'], [
                        'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                        'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S'])
                    self.assertEqual(scope['conditional'], [])
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    self.assertIn(base + '_E', retained)
                    for form, call_form in (
                            (base, record['st_plain']),
                            (base + '_E', record['st_enabled'])):
                        self.assertEqual(
                            review._call_verification(row['text'], call_form,
                                                      allow_complete_sample=True),
                            'documented_arguments_verified', form)
                        fact = review._fx_k_header_availability_fact(
                            form, 'FX3U', row, scope)
                        self.assertIsNotNone(fact, form)
                        self.assertEqual(fact['scope']['forms'], [form])
                        self.assertEqual(fact['sources'][0]['pdf_pages'], sorted(set(pages)))
        finally:
            connection.close()

    def test_numeric_conversion_forms_bind_complete_enabled_samples_and_k_headers(self):
        expected = {
            'BOOL_TO_WORD': ('5.4', (48, 49)),
            'BOOL_TO_DWORD': ('5.5', (50, 50)),
            'DINT_TO_INT': ('5.8', (56, 56)),
            'DINT_TO_WORD': ('5.16', (72, 72)),
            'DINT_TO_DWORD': ('5.18', (76, 76)),
            'INT_TO_WORD': ('5.15', (70, 71)),
            'INT_TO_DWORD': ('5.17', (74, 74)),
            'WORD_TO_DINT': ('5.29', (99, 99)),
            'DWORD_TO_INT': ('5.30', (101, 101)),
            'WORD_TO_DWORD': ('5.32', (105, 105)),
            'DWORD_TO_WORD': ('5.33', (107, 107)),
        }
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {record['op']: record for record in review._conversion_records()}
            expected_models = {
                'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S',
            }
            for base, (section, pages) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, section, pages)
                scope = review._model_scope(row)
                with self.subTest(base=base):
                    self.assertEqual((record['section'], record['pages']), (section, pages))
                    self.assertEqual(set(scope['supported']), expected_models)
                    self.assertEqual(scope['conditional'], [])
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    self.assertEqual(
                        review._call_verification(row['text'], record['st_plain']),
                        'documented_arguments_verified')
                    enabled_header_result = review._call_verification(
                        row['text'], record['st_enabled'])
                    self.assertIn(enabled_header_result, {
                        'documented_name_and_first_argument_verified',
                        'documented_arguments_verified',
                    })
                    self.assertEqual(
                        base in review.FX_K_CALL_SAMPLE_COMPLETION_OPS,
                        enabled_header_result == 'documented_name_and_first_argument_verified')
                    for form, call_form in (
                            (base, record['st_plain']),
                            (base + '_E', record['st_enabled'])):
                        self.assertEqual(
                            review._call_verification(
                                row['text'], call_form, allow_complete_sample=True),
                            'documented_arguments_verified', form)
                        for model in scope['supported']:
                            fact = review._fx_k_header_availability_fact(
                                form, model, row, scope)
                            self.assertIsNotNone(fact, (model, form))
                            self.assertEqual(fact['value']['available'], True)
                            self.assertEqual(fact['scope']['models'], [model])
                            self.assertEqual(fact['scope']['forms'], [form])
                            self.assertEqual(fact['sources'][0]['pdf_pages'], sorted(set(pages)))
        finally:
            connection.close()

    def test_real_and_time_conversion_forms_bind_own_k_samples_and_headers(self):
        expected = {
            'STR_TO_REAL': ('5.39', (119, 120), {'FX3U', 'FX3UC'}),
            'STR_TO_TIME': ('5.40', (122, 122), {'FX3U', 'FX3UC'}),
            'TIME_TO_INT': ('5.44', (130, 130), {
                'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S'}),
            'TIME_TO_DINT': ('5.45', (132, 132), {
                'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S'}),
        }
        fx_models = {
            'FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N',
            'FX2NC', 'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC', 'FXU',
        }
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {record['op']: record for record in review._conversion_records()}
            for base, (section, pages, supported) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, section, pages)
                scope = review._model_scope(row)
                with self.subTest(base=base):
                    self.assertEqual((record['section'], record['pages']), (section, pages))
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(set(scope['supported']), supported)
                    self.assertEqual(scope['conditional'], [])
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    for form, call_form in (
                            (base, record['st_plain']),
                            (base + '_E', record['st_enabled'])):
                        self.assertEqual(
                            review._call_verification(
                                row['text'], call_form, allow_complete_sample=True),
                            'documented_arguments_verified', form)
                        for model in supported:
                            fact = review._fx_k_header_availability_fact(
                                form, model, row, scope)
                            self.assertIsNotNone(fact, (model, form))
                            self.assertEqual(fact['value']['available'], True)
                            self.assertEqual(fact['scope']['models'], [model])
                            self.assertEqual(fact['scope']['forms'], [form])
                            self.assertEqual(
                                fact['sources'][0]['pdf_pages'], sorted(set(pages)))
                        for model in fx_models - supported:
                            self.assertIsNone(review._fx_k_header_availability_fact(
                                form, model, row, scope), (model, form))
        finally:
            connection.close()

    def test_string_and_time_followup_forms_bind_own_k_samples_and_headers(self):
        expected = {
            'STR_TO_BOOL': ('5.36', (113, 114), {'FX3U', 'FX3UC'}),
            'REAL_TO_STR': ('5.25', (90, 91), {'FX3U', 'FX3UC'}),
            'TIME_TO_STR': ('5.46', (134, 134), {'FX3U', 'FX3UC'}),
            'TIME_TO_WORD': ('5.47', (136, 136), {
                'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S'}),
        }
        fx_models = {
            'FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N',
            'FX2NC', 'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC', 'FXU',
        }
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {record['op']: record for record in review._conversion_records()}
            for base, (section, pages, supported) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, section, pages)
                scope = review._model_scope(row)
                with self.subTest(base=base):
                    self.assertEqual((record['section'], record['pages']), (section, pages))
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(set(scope['supported']), supported)
                    self.assertEqual(scope['conditional'], [])
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    for form, call_form in (
                            (base, record['st_plain']),
                            (base + '_E', record['st_enabled'])):
                        self.assertEqual(
                            review._call_verification(
                                row['text'], call_form, allow_complete_sample=True),
                            'documented_arguments_verified', form)
                        for model in supported:
                            fact = review._fx_k_header_availability_fact(
                                form, model, row, scope)
                            self.assertIsNotNone(fact, (model, form))
                            self.assertEqual(fact['value']['available'], True)
                            self.assertEqual(fact['scope']['models'], [model])
                            self.assertEqual(fact['scope']['forms'], [form])
                            self.assertEqual(
                                fact['sources'][0]['pdf_pages'], sorted(set(pages)))
                        for model in fx_models - supported:
                            self.assertIsNone(review._fx_k_header_availability_fact(
                                form, model, row, scope), (model, form))
        finally:
            connection.close()

    def test_bit_array_and_indexed_bit_forms_bind_own_k_scope_and_samples(self):
        expected = {
            'BITARR_TO_INT': ('5.49', (140, 140), 15),
            'BITARR_TO_DINT': ('5.50', (142, 142), 15),
            'INT_TO_BITARR': ('5.51', (144, 144), 15),
            'DINT_TO_BITARR': ('5.52', (146, 146), 15),
            'CPY_BITARR': ('5.53', (148, 148), 15),
            'GET_BIT_OF_INT': ('5.54', (150, 151), 2),
            'SET_BIT_OF_INT': ('5.55', (152, 153), 2),
            'CPY_BIT_OF_INT': ('5.56', (154, 155), 2),
        }
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {record['op']: record for record in review._conversion_records()}
            all_fx_models = {
                'FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N',
                'FX2NC', 'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC', 'FXU',
            }
            modern_fx_models = {'FX3U', 'FX3UC'}
            for base, (section, pages, supported_count) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, section, pages)
                scope = review._model_scope(row)
                supported = set(scope['supported']) | set(scope['conditional'])
                header_marks = json.loads(row.get('source_applicability_json') or '{}').get(
                    'model_marks', [])
                expected_supported = (all_fx_models if supported_count == 15
                                      else modern_fx_models)
                expected_unsupported = all_fx_models - expected_supported
                forms = (base, base + '_E')
                with self.subTest(base=base):
                    self.assertEqual((record['section'], record['pages']), (section, pages))
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(supported, expected_supported)
                    self.assertEqual({mark['model'] for mark in header_marks
                                      if mark['state'] == 'unsupported'}, expected_unsupported)
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    for form in forms:
                        call_form = record['st_enabled'] if form.endswith('_E') else record['st_plain']
                        self.assertEqual(
                            review._call_verification(row['text'], call_form,
                                                      allow_complete_sample=True),
                            'documented_arguments_verified', form)
                        for model in sorted(expected_supported):
                            fact = review._fx_k_header_availability_fact(
                                form, model, row, scope)
                            self.assertIsNotNone(fact, (model, form))
                            self.assertEqual(fact['value'], {
                                'available': True, 'model_states': {model: 'supported'}})
                            self.assertEqual(fact['scope'], {
                                'models': [model], 'forms': [form], 'languages': ['st']})
                            self.assertEqual(fact['sources'][0]['pdf_pages'], sorted(set(pages)))
                        for model in sorted(expected_unsupported):
                            self.assertIsNone(review._fx_k_header_availability_fact(
                                form, model, row, scope), (model, form))
        finally:
            connection.close()

    def test_bool_to_str_emits_exact_k_header_support_for_its_complete_samples(self):
        record = next(row for row in review._conversion_records()
                      if row['op'] == 'BOOL_TO_STR')
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            row = review._section_chunk(chunks, record['section'], record['pages'])
            scope = review._model_scope(row)
            header = json.loads(row.get('source_applicability_json') or '{}')
            supported = {'FX3U', 'FX3UC'}
            unsupported = {item['model'] for item in header['model_marks']
                           if item['state'] == 'unsupported'}

            self.assertIn('BOOL_TO_STR', review.FX_K_STRUCTURED_TARGET_OPS)
            self.assertEqual(scope['basis'], 'instruction_header')
            self.assertEqual(set(scope['supported']), supported)
            self.assertEqual(scope['conditional'], [])
            self.assertEqual(len(supported | unsupported), 15)
            self.assertEqual(header['evidence_pages'], [46])
            for form, call in (
                    ('BOOL_TO_STR', record['st_plain']),
                    ('BOOL_TO_STR_E', record['st_enabled'])):
                with self.subTest(form=form):
                    self.assertEqual(
                        review._call_verification(row['text'], call),
                        'documented_arguments_verified')
                    for model in sorted(supported):
                        fact = review._fx_k_header_availability_fact(form, model, row, scope)
                        self.assertIsNotNone(fact)
                        self.assertEqual(fact['value'], {
                            'available': True, 'model_states': {model: 'supported'}})
                        self.assertEqual(fact['scope'], {
                            'models': [model], 'forms': [form], 'languages': ['st']})
                        self.assertEqual(fact['sources'][0]['pdf_pages'], [46, 47])
                    for model in sorted(unsupported):
                        self.assertIsNone(review._fx_k_header_availability_fact(
                            form, model, row, scope))
        finally:
            connection.close()

    def test_layout_sample_override_is_bound_to_the_verified_k_page_and_text(self):
        record = next(r for r in review._conversion_records() if r['op'] == 'STR_TO_DINT')
        row = {
            'manual_id': review.FUNCTION_MANUAL, 'revision': review.FUNCTION_REVISION,
            'pdf_page': 117,
            'text': 'STR_TO_DINT_E(EN,_STRING, STR_TO_DINT_E(X000, Label1, Label2);',
        }
        self.assertTrue(review._fx_k_layout_sample_verified(
            record, 'STR_TO_DINT_E', row, record['st_enabled']))
        for changed in (
                {**row, 'pdf_page': 118},
                {**row, 'revision': 'J'},
                {**row, 'text': 'STR_TO_DINT_E(EN,_STRING, STR_TO_DINT_E(X000, Label1);'},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(review._fx_k_layout_sample_verified(
                    record, 'STR_TO_DINT_E', changed, record['st_enabled']))
        self.assertFalse(review._fx_k_layout_sample_verified(
            record, 'STR_TO_INT_E', row, record['st_enabled']))

    def test_time_arithmetic_owners_bind_exact_k_headers_and_complete_calls(self):
        from plc.structured_call_contract import resolve_structured_call_contract

        expected = {
            'SUB_TIME': ('13.2', (235, 235)),
            'MUL_TIME': ('13.3', (237, 237)),
            'DIV_TIME': ('13.4', (239, 239)),
        }
        fx_models = {
            'FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N',
            'FX2NC', 'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC', 'FXU',
        }
        original_records = review._all_records
        selected = tuple(record for record in original_records()
                         if record['op'] in expected)
        import pdfplumber
        with pdfplumber.open(FUNCTION_PDF) as manual:
            basic_types = manual.pages[32].extract_text() or ''
            generic_hierarchy = manual.pages[33].extract_text() or ''
        self.assertTrue(review._contains(
            basic_types, 'Word [signed] Integer -32768 to 32767 16 bits'))
        self.assertTrue(review._contains(
            basic_types, 'Double Word [signed] Double precision integer'))
        self.assertTrue(review._contains(basic_types, 'FLOAT (Single Precision) Real number'))
        for witness in ('ANY_NUM', 'ANY_REAL', 'ANY_INT', 'FLOAT', 'Single', 'Double',
                        'Word', 'signed', 'Double word'):
            self.assertTrue(review._contains(generic_hierarchy, witness), witness)
        with mock.patch.object(review, '_all_records', return_value=selected):
            connection = sqlite3.connect(
                SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
            connection.row_factory = sqlite3.Row
            try:
                facts_by_owner, receipt = review.reviewed_structured_behavior(connection)
                chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
                records = {record['op']: record for record in selected}
                self.assertEqual(receipt['residuals'], [])
                self.assertEqual(receipt['unresolved_headers'], [])
                self.assertEqual(receipt['conflicts'], [])
                self.assertEqual(len(facts_by_owner), 90)

                for base, (section, pages) in expected.items():
                    record = records[base]
                    row = review._section_chunk(chunks, section, pages)
                    scope = review._model_scope(row)
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(set(scope['supported']), fx_models)
                    self.assertEqual(scope['conditional'], [])
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertIn(base, review.FX_K_STRUCTURED_TARGET_OPS)
                    self.assertEqual(
                        review._call_verification(row['text'], record['st_plain'],
                                                   allow_complete_sample=True),
                        'documented_arguments_verified')
                    self.assertEqual(
                        review._call_verification(row['text'], record['st_enabled'],
                                                   allow_complete_sample=True),
                        'documented_arguments_verified')

                    for model in fx_models:
                        for form, enabled in ((base, False), (base + '_E', True)):
                            owner_facts = facts_by_owner[(model, form)]
                            prefix = f'structured:{form.lower()}:'
                            self.assertTrue(owner_facts)
                            self.assertTrue(all(fact['id'].startswith(prefix)
                                                for fact in owner_facts))
                            signatures = [fact for fact in owner_facts
                                          if fact['dimension'] == 'parameters.st_signature'
                                          and fact.get('scope', {}).get('languages') == ['st']]
                            headers = [fact for fact in owner_facts
                                       if fact['dimension'] == 'constraints.cpu_availability']
                            self.assertEqual(len(signatures), 1)
                            self.assertEqual(len(headers), 1)
                            signature = signatures[0]['value']
                            self.assertEqual(signature['call_form_verification'],
                                             'documented_arguments_verified')
                            expected_inputs = [review._parameter_type(token)
                                                for _name, token, _marker, _role
                                                in records[base]['inputs']]
                            actual_inputs = [item['type'] for item in signature['parameters']
                                             if item['name'] != 'EN'
                                             and item['direction'] == 'input']
                            self.assertEqual(actual_inputs, expected_inputs)
                            time_type = review._parameter_type('TIME')
                            if enabled:
                                self.assertEqual(signature['returns'], {
                                    'type': review._parameter_type('BOOL'),
                                    'role': 'ENO_execution_status'})
                                output = [item for item in signature['parameters']
                                          if item['direction'] == 'output']
                                self.assertEqual(len(output), 1)
                                self.assertEqual(output[0]['type'], time_type)
                            else:
                                self.assertEqual(signature['returns']['type'], time_type)
                            self.assertTrue(headers[0]['value']['available'])
                            self.assertEqual(headers[0]['scope']['models'], [model])
                            self.assertEqual(headers[0]['scope']['forms'], [form])
                            expected_limit_dimension, expected_limit_value = records[base]['limits'][0]
                            limit, = [fact for fact in owner_facts
                                      if fact['dimension'] == expected_limit_dimension
                                      and ':structured_ladder' not in fact['id']]
                            self.assertEqual(limit['value'], expected_limit_value)
                            if base == 'DIV_TIME':
                                errors, = [fact for fact in owner_facts
                                           if fact['dimension'] == 'resources.operation_error'
                                           and ':structured_ladder' not in fact['id']]
                                self.assertEqual(errors['value']['flag'], 'M8067')
                                self.assertEqual(errors['value']['conditions'], [
                                    'divisor_is_zero', 'result_exceeds_2147483647'])
                            resolved = resolve_structured_call_contract(
                                owner_facts, opcode=form, model=model)
                            self.assertTrue(resolved.accepted,
                                            (model, form, resolved.refusal_reasons))
                            if base in {'MUL_TIME', 'DIV_TIME'}:
                                expected_members = ['INT', 'DINT', 'REAL', 'LREAL']
                                factor = next(item for item in signature['parameters']
                                              if item.get('type', {}).get('flexible_type')
                                              == 'ANY_NUM')
                                self.assertEqual(factor['type_alternatives'], expected_members)
                                resolved_factor = next(item for item in resolved.contract.parameters
                                                       if item['name'] == factor['name'])
                                self.assertEqual(resolved_factor['type_alternatives'],
                                                 expected_members)
                                typed_operands, = [fact for fact in owner_facts
                                    if fact.get('dimension') == 'parameters.typed_operands'
                                    and fact['id'] == f'structured:{form.lower()}:operands@'
                                    + review.FUNCTION_MANUAL]
                                typed_factor = next(item for item in
                                    typed_operands['value']['operands']
                                    if item.get('name') == factor['name'])
                                self.assertEqual(typed_factor['type_alternatives'],
                                                 expected_members)
                                domain, = [fact for fact in owner_facts
                                    if fact.get('dimension') == 'parameters.type_domain'
                                    and fact['id'] == f'structured:{form.lower()}:'
                                    f'type_domain_any_num@{review.FUNCTION_MANUAL}']
                                self.assertEqual(domain['value']['members'], expected_members)
                                self.assertEqual(domain['value']['members_basis'],
                                    'own_manual_generic_numeric_hierarchy_and_basic_type_table')
                                self.assertNotIn('WORD', domain['value']['members'])
                                self.assertNotIn('DWORD', domain['value']['members'])
                                self.assertNotIn('label_rule', domain['value'])
                                self.assertNotIn('accepted_by_documented_operators', domain['value'])
                                self.assertEqual({tuple(source['pdf_pages'])
                                                  for source in domain['sources']}, {(33,), (34,)})
                                domain_id = f'structured:{form.lower()}:' \
                                            f'type_domain_any_num@{review.FUNCTION_MANUAL}'
                                self.assertIn(domain_id, signatures[0]['depends_on'])
                                self.assertIn(domain_id, typed_operands['depends_on'])
            finally:
                connection.close()

    def test_split_call_headers_are_completed_only_by_full_same_name_samples(self):
        examples = (
            ('ADD_TIME_E(EN,_IN1,_IN2,Output_label)',
             'ADD_TIME_E(EN,_IN1,_IN2, ADD_TIME_E(X000,Label1,Label2,Label3);'),
            ('BCD_TO_DINT_E(EN,_BCD,Output_label)',
             'BCD_TO_DINT_E(EN,_BCD, BCD_TO_DINT_E(X000,Label1,Label2);'),
            ('BCD_TO_INT_E(EN,_BCD,Output_label)',
             'BCD_TO_INT_E(EN,_BCD, BCD_TO_INT_E(X000,D0,D10);'),
            ('INT_TO_DINT_E(EN,_INT,Output_label)',
             'INT_TO_DINT_E(EN,_INT, INT_TO_DINT_E(X000,D0,Label);'),
            ('WORD_TO_INT_E(EN,_WORD,Output_label)',
             'WORD_TO_INT_E(EN,_WORD, WORD_TO_INT_E(X000,D0,D10);'),
        )
        for call_form, text in examples:
            with self.subTest(call_form=call_form):
                self.assertEqual(
                    review._call_verification(text, call_form, allow_complete_sample=True),
                    'documented_arguments_verified')
                self.assertEqual(
                    review._call_verification(text, call_form),
                    'documented_name_and_first_argument_verified')

    def test_sample_with_wrong_arity_does_not_complete_a_split_header(self):
        call_form = 'BCD_TO_INT_E(EN,_BCD,Output_label)'
        text = 'BCD_TO_INT_E(EN,_BCD, BCD_TO_INT_E(X000,D0);'
        self.assertEqual(
            review._call_verification(text, call_form, allow_complete_sample=True),
            'documented_name_and_first_argument_verified')

    def test_k_availability_is_scoped_to_its_supported_header_model_and_form(self):
        row = {
            'id': 'k-source:54', 'manual_id': review.FUNCTION_MANUAL,
            'manual_number': 'JY997D34801', 'revision': 'K',
            'pdf_page': 54, 'source_pages_json': '[54]',
        }
        scope = {'basis': 'instruction_header', 'supported': ['FX3U'], 'conditional': []}
        fact, = (review._fx_k_header_availability_fact('INT_TO_DINT_E', 'FX3U', row, scope),)
        self.assertEqual(fact['dimension'], 'constraints.cpu_availability')
        self.assertEqual(fact['value'], {
            'available': True, 'model_states': {'FX3U': 'supported'}})
        self.assertEqual(fact['scope'], {
            'models': ['FX3U'], 'forms': ['INT_TO_DINT_E'], 'languages': ['st']})
        self.assertEqual(fact['sources'][0]['pdf_pages'], [54])
        self.assertEqual(fact['status'], 'source_verified')
        self.assertIsNone(review._fx_k_header_availability_fact(
            'INT_TO_DINT_E', 'FX2N', row,
            {'basis': 'instruction_header', 'supported': [], 'conditional': ['FX2N']}))
        self.assertIsNone(review._fx_k_header_availability_fact(
            'INT_TO_DINT_E', 'FX3U', {**row, 'revision': 'J'}, scope))

    def test_expression_operator_availability_comes_from_its_own_k_header(self):
        expected = {
            'MOD': ('15.5', [269], 15),
            'POWER': ('15.6', [270], 2),
            'XOR': ('15.9', [273, 274], 15),
            'NOT': ('15.10', [275], 15),
            'AND': ('15.7', [271], 15),
        }
        self.assertEqual(review.FX_K_STRUCTURED_EXPRESSION_OPS, frozenset(expected))
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            for opcode, (section, pages, fx_count) in expected.items():
                record = next(row for row in review.FAMILY_RECORDS
                              if row['op'] == opcode and row['kind'] == 'operator')
                row = review._section_chunk(chunks, section, (pages[0],))
                scope = review._model_scope(row)
                models = scope['supported'] + scope['conditional']
                with self.subTest(opcode=opcode):
                    self.assertEqual(scope['basis'], 'instruction_header')
                    self.assertEqual(len([model for model in models if model.startswith('FX')]),
                                     fx_count)
                    self.assertEqual(scope['conditional'], [])
                    for model in models:
                        fact = review._fx_k_header_availability_fact(opcode, model, row, scope)
                        self.assertIsNotNone(fact)
                        self.assertEqual(fact['dimension'], 'constraints.cpu_availability')
                        self.assertEqual(fact['scope'], {
                            'models': [model], 'forms': [opcode], 'languages': ['st']})
                        self.assertEqual(fact['sources'][0]['pdf_pages'], pages)
                    self.assertIsNone(review._fx_k_header_availability_fact(
                        opcode, 'Q03UDV', row, scope))
        finally:
            connection.close()

    def test_and_variadic_queue_owner_maps_to_its_documented_infix_operator(self):
        queue_path = PRIVATE / 'tmp/rag-gap-implementation-20261029/execution-queue.json'
        queue = json.loads(queue_path.read_text(encoding='utf-8'))
        queued = queue['instructions']['AND(...)']
        fx_models = {
            'FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
            'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S',
        }
        self.assertEqual(queued['unit_ids'], ['I-00165', 'I-00166', 'I-00167'])
        self.assertEqual(set(queued['models']), fx_models)

        operator = next(row for row in review.FAMILY_RECORDS
                        if row['op'] == 'AND' and row['kind'] == 'operator')
        self.assertEqual(operator['operator']['symbol'], 'AND')
        self.assertEqual(operator['operator']['alternative'], '&')
        self.assertEqual(operator['operator']['expression'], 's1 AND s2')
        self.assertEqual(operator['inputs'], (('s1', 'ANY_BIT', 's1', 'logical_operand'),))
        self.assertEqual(operator['outputs'][0][:2], ('d', 'ANY_BIT'))
        self.assertEqual(operator['rule']['operand_pins'], {'minimum': 2, 'maximum': 28})

        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        selected = tuple(record for record in review._all_records()
                         if record['op'] == 'AND')
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            operator_row = review._section_chunk(chunks, '15.7', (271,))
            scope = review._model_scope(operator_row)
            self.assertEqual(scope['basis'], 'instruction_header')
            self.assertEqual(set(scope['supported']), fx_models)
            self.assertEqual(scope['conditional'], [])
            self.assertTrue(review._contains(operator_row['text'], 's1 AND s2'))
            self.assertTrue(review._contains(operator_row['text'], '2 to 28'))
            self.assertTrue(review._contains(operator_row['text'], 'M20:=M0 AND M10'))

            with mock.patch.object(review, '_all_records', return_value=selected):
                facts_by_owner, receipt = review.reviewed_structured_behavior(connection)
            self.assertEqual(
                {model for model, opcode in facts_by_owner if opcode == 'AND'}, fx_models)
            self.assertEqual(receipt['conflicts'], [])
            for model in fx_models:
                facts = facts_by_owner[(model, 'AND')]
                by_id = {fact['id']: fact for fact in facts}
                self.assertEqual(len(by_id), len(facts))
                prefix = 'structured:and:'
                self.assertTrue(all(identity.startswith(prefix) for identity in by_id))
                source_signature = by_id['structured:and:plain_via_operator@fxcpu_application_k']
                self.assertEqual(source_signature['value']['documented_call'], ['s1', 'AND', 's2'])
                self.assertEqual(source_signature['value']['operand_count_documented'],
                                 {'minimum': 2, 'maximum': 28})
                self.assertEqual(source_signature['value']['equivalent_function_forms'],
                                 ['AND_E(EN,_IN,_IN,Output_label)'])
                expression = by_id['structured:and:operator_operator_form@fxcpu_application_k']
                self.assertEqual(expression['value']['symbol'], {'name': 'AND'})
                self.assertEqual(expression['value']['expression_form'], ['s1', 'AND', 's2'])
                self.assertTrue(expression['value']['infix'])
                operator_result = by_id['structured:and:operator_result@fxcpu_application_k']
                self.assertEqual(operator_result['dimension'], 'effects.result_rule')
                self.assertEqual(operator_result['value']['operand_pins'],
                                 {'minimum': 2, 'maximum': 28})
                self.assertEqual(operator_result['sources'][0]['pdf_pages'], [271])
                header = by_id['structured:and:cpu_availability_header@fxcpu_application_k']
                self.assertEqual(header['value']['model_states'], {model: 'supported'})
                self.assertEqual(header['sources'][0]['pdf_pages'], [271])
        finally:
            connection.close()

    def test_arithmetic_and_bitwise_e_signatures_use_their_own_complete_samples(self):
        expected = {
            'ADD': ('7.1', 163), 'SUB': ('7.2', 165), 'MUL': ('7.3', 167),
            'DIV': ('7.4', 169), 'MOD': ('7.5', 171),
            'AND': ('9.1', 183), 'OR': ('9.2', 185), 'XOR': ('9.3', 187),
        }
        connection = sqlite3.connect(SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            for base, (section, page) in expected.items():
                record = next(row for row in review.FAMILY_RECORDS
                              if row['op'] == base and row['section'] == section)
                form = base + '_E'
                call = record['st_enabled']
                row = review._section_chunk(chunks, section, (page,))
                with self.subTest(form=form):
                    self.assertIn(base, review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
                    self.assertEqual(
                        review._call_verification(row['text'], call, allow_complete_sample=True),
                        'documented_arguments_verified')
                    self.assertIn(review._call_verification(row['text'], call), {
                        'documented_arguments_verified',
                        'documented_name_and_first_argument_verified'})
                    # A same-name sample completes the split format only when
                    # its argument count agrees with that exact signature.
                    wrong_arity = call.split(',', 1)[0] + ' ' + form + '(X000,D0,D10);'
                    self.assertEqual(
                        review._call_verification(wrong_arity, call, allow_complete_sample=True),
                        'documented_name_and_first_argument_verified')
        finally:
            connection.close()

    def test_standard_counters_use_exact_named_instance_calls_and_type_witnesses(self):
        from plc.st_frontend import parse_body

        connection = sqlite3.connect(SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1',
                                     uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            expected = {
                'CTU': ('CTU', 247), 'CTU_E': ('CTU', 247),
                'CTD': ('CTD', 249), 'CTD_E': ('CTD', 249),
                'CTUD': ('CTUD', 251), 'CTUD_E': ('CTUD', 251),
            }
            records = {row['op']: row for row in review._all_records()
                       if row['op'] in {'CTU', 'CTD', 'CTUD'}}
            for form, (base, page) in expected.items():
                record = records[base]
                row = review._section_chunk(chunks, record['section'], record['pages'])
                sample = review._fx_k_counter_instance_call(record, form)
                with self.subTest(form=form):
                    self.assertTrue(review._fx_k_counter_call_sample_verified(
                        record, form, row))
                    self.assertIn(form, review.FX_K_STRUCTURED_TARGET_FORMS)
                    scope = review._model_scope(row)
                    availability = review._fx_k_header_availability_fact(
                        form, 'FX3U', row, scope)
                    self.assertEqual(availability['dimension'], 'constraints.cpu_availability')
                    self.assertEqual(availability['value'], {
                        'available': True, 'model_states': {'FX3U': 'supported'}})
                    self.assertEqual(availability['scope'], {
                        'models': ['FX3U'], 'forms': [form], 'languages': ['st']})
                    for excluded_cpu in ('FX0', 'FX0N', 'FX0S'):
                        self.assertIsNone(review._fx_k_header_availability_fact(
                            form, excluded_cpu, row, scope))
                    self.assertEqual(len(parse_body(sample['call_quote']).body), 1)
                    self.assertEqual(sample['instance_type'], form)
                    self.assertEqual(sample['instance_type_source']['logical_name'], 'IEC.lif')
                    self.assertEqual(sample['instance_type_source']['definition_quote'],
                                     'FUNCTION_BLOCK ' + form)
                    self.assertNotEqual(sample['instance_name'], sample['instance_type'])

                    call_arguments = review._split_call_arguments(
                        sample['call_quote'].partition('(')[2].rsplit(')', 1)[0])
                    source_argument_order = [argument.partition(':=')[0]
                                             for argument in call_arguments]
                    self.assertTrue(all(':' in argument for argument in call_arguments))
                    fact = review._st_fact(
                        record, form, form.endswith('_E'),
                        record.get('st_enabled') if form.endswith('_E') else record.get('st_plain'),
                        'FX3U', [review._source_reference(row)], None, 'source_verified')
                    value = fact['value']
                    self.assertEqual(value['call_kind'], 'function_block')
                    self.assertTrue(value['instance_required'])
                    self.assertEqual(value['name'], form)
                    self.assertEqual(value['documented_call'], sample['call_quote'])
                    self.assertEqual(value['argument_order'], source_argument_order)
                    self.assertEqual(value['returns'], {
                        'role': 'no_return_value', 'outputs_via_arguments': True})
                    self.assertEqual(value['source_witness']['instance_name'],
                                     sample['instance_name'])
                    self.assertEqual(value['source_witness']['instance_type'], form)
                    self.assertEqual(value['source_witness']['pdf_pages'], [page])
                    self.assertEqual(value['source_witness']['quote']['call'],
                                     sample['call_quote'])
        finally:
            connection.close()

    def test_int_to_real_conditional_header_is_bound_to_its_ver_1_10_gate(self):
        from plc.structured_call_contract import resolve_structured_call_contract

        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            record = next(row for row in review._conversion_records()
                          if row['op'] == 'INT_TO_REAL')
            row = review._section_chunk(chunks, record['section'], record['pages'])
            scope = review._model_scope(row)
            self.assertEqual(scope['supported'],
                             ['FX3U', 'FX3UC', 'FX3S', 'FX2N', 'FX2NC'])
            self.assertEqual(scope['conditional'], ['FX3G', 'FX3GC'])
            self.assertIn('INT_TO_REAL', review.FX_K_CALL_SAMPLE_COMPLETION_OPS)
            self.assertIn('INT_TO_REAL', review.FX_K_STRUCTURED_TARGET_OPS)
            references = [review._source_reference(row)]

            for model in ('FX3G', 'FX3GC', 'FX3U', 'FX3UC', 'FX3S', 'FX2N', 'FX2NC'):
                gate = review._fx_k_conditional_version_gate(record, model)
                conditional = model in scope['conditional']
                if conditional:
                    self.assertEqual(gate, {
                        'condition': {'context': 'cpu_version', 'minimum': '1.10'},
                        'limit_index': 1,
                    })
                else:
                    self.assertIsNone(gate)
                for form, enabled in (('INT_TO_REAL', False), ('INT_TO_REAL_E', True)):
                    call = record['st_enabled'] if enabled else record['st_plain']
                    call_verification = review._call_verification(
                        row['text'], call, allow_complete_sample=True)
                    self.assertEqual(call_verification, 'documented_arguments_verified', form)
                    condition = gate['condition'] if gate else None
                    facts = review._form_facts(
                        record, form, enabled, model, row, references, condition,
                        'source_verified')
                    if call_verification != 'not_verified':
                        facts.append(review._st_fact(
                            record, form, enabled, call, model, references, condition,
                            'source_verified', call_verification))
                    availability = review._fx_k_header_availability_fact(
                        form, model, row, scope, record=record)
                    if availability:
                        facts.append(availability)
                    review._bind_fx_k_conditional_version_facts(facts, form, gate)

                    header = next(fact for fact in facts
                                  if fact['dimension'] == 'constraints.cpu_availability')
                    if conditional:
                        version_id = f'structured:{form.lower()}:limits_1@{review.FUNCTION_MANUAL}'
                        signature = next(fact for fact in facts
                                         if fact['dimension'] == 'parameters.st_signature'
                                         and fact.get('scope', {}).get('languages') == ['st'])
                        typed = next(fact for fact in facts
                                     if fact['dimension'] == 'parameters.typed_operands'
                                     and 'st' in fact.get('scope', {}).get('languages', []))
                        self.assertNotIn('available', header['value'])
                        self.assertEqual(header['value']['model_states'], {model: 'conditional'})
                        self.assertEqual(header['conditions'], [gate['condition']])
                        self.assertEqual(header['depends_on'], [version_id])
                        version_fact = next(fact for fact in facts if fact['id'] == version_id)
                        self.assertEqual(version_fact['value']['minimum_series_version'],
                                         'Ver.1.10')
                        self.assertNotIn('conditions', version_fact)
                        for fact in (signature, typed):
                            self.assertEqual(
                                fact['depends_on'], [header['id'], version_id])
                            self.assertEqual(fact['conditions'], [gate['condition']])
                    else:
                        self.assertIs(header['value']['available'], True)
                        self.assertEqual(header['value']['model_states'], {model: 'supported'})
                        self.assertNotIn('conditions', header)

                    resolution = resolve_structured_call_contract(
                        facts, opcode=form, model=model)
                    self.assertTrue(resolution.accepted,
                                    (model, form, resolution.refusal_reasons))

            for model in ('FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C'):
                for form in ('INT_TO_REAL', 'INT_TO_REAL_E'):
                    self.assertIsNone(review._fx_k_header_availability_fact(
                        form, model, row, scope, record=record))
        finally:
            connection.close()

    def test_get_address_conversion_keeps_exact_any_domain_and_own_header(self):
        expected = {
            'GET_BOOL_ADDR': ('5.57', 156, ['BIT', 'BIT_STRING'], 'BOOL'),
            'GET_INT_ADDR': ('5.58', 157,
                             ['INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME',
                              'BITARR_INT', 'BITARR_DINT', 'BITARR_WORD', 'BITARR_DWORD',
                              'BITARR_REAL', 'BITARR_TIME'], 'INT'),
            'GET_WORD_ADDR': ('5.59', 158,
                              ['INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME',
                               'BITARR_INT', 'BITARR_DINT', 'BITARR_WORD', 'BITARR_DWORD',
                               'BITARR_REAL', 'BITARR_TIME'], 'WORD'),
        }
        fx_models = ('FX3U', 'FX3UC', 'FX3G', 'FX3GC', 'FX3S', 'FX2N', 'FX2NC',
                     'FX1N', 'FX1NC', 'FX1S', 'FXU', 'FX2C', 'FX0N', 'FX0', 'FX0S')
        import pdfplumber
        with pdfplumber.open(FUNCTION_PDF) as manual:
            for page_index, section in ((155, '5.57 GET_BOOL_ADDR'),
                                        (156, '5.58 GET_INT_ADDR'),
                                        (157, '5.59 GET_WORD_ADDR')):
                page_text = manual.pages[page_index].extract_text() or ''
                self.assertIn(section, page_text)
            for page_index in (156, 157):
                self.assertIn('Use global labels when specifying labels',
                              manual.pages[page_index].extract_text() or '')
        connection = sqlite3.connect(
            SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            chunks = review._load_chunks(connection, review.FUNCTION_MANUAL)
            records = {row['op']: row for row in review._conversion_records()
                       if row['op'] in expected}
            for opcode, (section, page, input_types, output_type) in expected.items():
                record = records[opcode]
                row = review._section_chunk(chunks, section, (page,))
                scope = review._model_scope(row)
                self.assertEqual(scope['basis'], 'instruction_header')
                self.assertEqual(set(scope['supported']), set(fx_models))
                self.assertEqual(scope['conditional'], [])
                self.assertIn(opcode, review.FX_K_STRUCTURED_TARGET_OPS)
                self.assertEqual(
                    review._call_verification(row['text'], record['st_plain']),
                    'documented_arguments_verified')
                references = [review._source_reference(row)]
                for model in fx_models:
                    with self.subTest(opcode=opcode, model=model):
                        facts = review._form_facts(
                            record, opcode, False, model, row, references, None,
                            'source_verified')
                        facts.append(review._st_fact(
                            record, opcode, False, record['st_plain'], model,
                            references, None, 'source_verified',
                            'documented_arguments_verified'))
                        header = review._fx_k_header_availability_fact(
                            opcode, model, row, scope)
                        self.assertIsNotNone(header)
                        self.assertEqual(header['value'], {
                            'available': True, 'model_states': {model: 'supported'}})
                        facts.append(header)
                        signature, = [fact for fact in facts
                                      if fact['dimension'] == 'parameters.st_signature']
                        self.assertEqual(signature['value']['documented_call'],
                                         f'{opcode}(s)')
                        self.assertEqual(signature['value']['parameters'][0]['type'],
                                         {'flexible_type': 'ANY'})
                        self.assertNotIn('type_alternatives',
                                         signature['value']['parameters'][0])
                        domain, = [fact for fact in facts
                                   if fact['dimension'] == 'constraints.accepted_input_types']
                        self.assertEqual(domain['value']['input'], input_types)
                        self.assertEqual(domain['value']['output'], output_type)
                        self.assertEqual(domain['value']['literal_or_expression_support'],
                                         'not_stated')
                        self.assertIn('not stated', domain['note'])
                        returned = signature['value']['returns']
                        self.assertEqual(returned['type'],
                                         review.TYPES[output_type])
                        self.assertEqual(returned['result_semantics'],
                                         'start_data_reference')
                        self.assertEqual(returned['address_source_parameter'], 's')
                        self.assertEqual(signature['value']['argument_order'], ['s'])
                        typed, = [fact for fact in facts
                                  if fact['dimension'] == 'parameters.typed_operands']
                        self.assertEqual(typed['value']['operands'][0]['data_type'],
                                         {'flexible_type': 'ANY'})
                        self.assertNotIn('type_alternatives', typed['value']['operands'][0])
                        expected_limit_ids = [
                            f"structured:{opcode.lower()}:limits_{index}@{review.FUNCTION_MANUAL}"
                            for index in range(len(record['limits']))]
                        self.assertTrue(set(expected_limit_ids).issubset(
                            typed.get('depends_on', [])))
                        self.assertTrue(set(expected_limit_ids).issubset(
                            signature.get('depends_on', [])))
                        result, = [fact for fact in facts
                                   if fact['dimension'] == 'effects.result_rule']
                        self.assertEqual(result['value']['result_semantics'],
                                         'start_data_reference')
                        self.assertTrue(set(expected_limit_ids).issubset(
                            result.get('depends_on', [])))
                        label_limits = [fact for fact in facts
                                        if fact['dimension'] == 'constraints.label_requirement']
                        if opcode == 'GET_BOOL_ADDR':
                            self.assertEqual(label_limits, [])
                        else:
                            label, = label_limits
                            self.assertEqual(label['value'], {
                                'applies_to': 'array_and_32bit_data',
                                'sixteen_bit_device_directly': False,
                                'global_label_required': True,
                                'direct_32bit_counter': True,
                            })
                            self.assertIn('32-bit counters may be specified directly',
                                          domain['note'])
                        type_domain, = [fact for fact in facts
                                        if fact['id'].endswith(':type_domain_any@' +
                                                               review.FUNCTION_MANUAL)]
                        self.assertEqual(type_domain['value']['members'], input_types)
                        self.assertNotIn('DWORD', type_domain['value']['members'])
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()

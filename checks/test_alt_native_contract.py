"""Exact Native ALT admission; no native compiler or PLC execution claims."""
import copy
import json
from pathlib import Path
import unittest

from knowledge.instruction_compiler import call_admission_explanation, refresh_reviewed_call_admission
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.instruction_constraints import check_fact_constraints
from plc.validation import _validate_source_operand_types, PLCJsonValidationError


ALT_MODELS = ('FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC')
ALT_LEGACY_M_MODELS = ('FX0', 'FX0N', 'FX0S', 'FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N', 'FX2NC', 'FXU')
ALTP_LEGACY_M_MODELS = ('FX1N', 'FX1NC', 'FX1S', 'FX2C', 'FX2N', 'FX2NC', 'FXU')
ALTP_LEGACY_NATIVE_MODELS = ('FX1S', 'FX1N', 'FX2N', 'FX2NC')


class AltNativeContractTests(unittest.TestCase):
    def test_merged_alt_menu_preserves_different_source_notes_and_accepts_the_shared_call(self):
        from plc.ordered_source import validate_ordered_rung
        registry = DEFAULT_INSTRUCTION_REGISTRY
        group = 'FX1N/FX1NC'
        spec = registry.resolve('ALT', cpu=group, operand_count=1)
        self.assertTrue(spec.supports_cpu(group))
        self.assertEqual(spec.native_operand_order, ('D',))
        self.assertEqual(spec.operands[0].device_prefixes, ('Y', 'M', 'S'))
        self.assertEqual({s['manual_id'] for s in spec.contract_sources},
                         {'jy992d88101e', 'fx3_programming_r'})
        for model in ('FX1N', 'FX1NC'):
            self.assertIn(registry.resolve_catalog_form('ALT', cpu=model).spec.notes, spec.notes)
        for operand in ('Y7', 'M400', 'S100'):
            validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [
                {'op': 'LD', 'args': ['X0']}, {'op': 'ALT', 'args': [operand]}]}, plc_model=group)

    def test_merged_contract_still_rejects_real_operand_and_execution_differences(self):
        from dataclasses import replace
        from plc.instructions import InstructionRegistry
        left = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('ALT', cpu='FX1N').spec
        right = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('ALT', cpu='FX1NC').spec
        differences = [
            {'operands': (replace(right.operands[0], device_prefixes=('M',)),)},
            {'operands': (replace(right.operands[0], role=left.operands[0].role.__class__('write')),)},
            {'execution_form': 'pulse'},
            {'native_operand_order': ('S',)},
            {'max_operands': 2},
        ]
        for difference in differences:
            with self.subTest(difference=difference):
                registry = InstructionRegistry([left])
                registry._cpu_contracts = {
                    ('mitsubishi', 'ALT', 'FX1N'): left,
                    ('mitsubishi', 'ALT', 'FX1NC'): replace(right, **difference),
                }
                self.assertFalse(registry.resolve('ALT', cpu='FX1N/FX1NC').supports_cpu('FX1N/FX1NC'))

    def test_exact_five_cpu_native_contracts_and_source_owner_admission(self):
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for model in ALT_MODELS:
            with self.subTest(model=model):
                form = registry.resolve_catalog_form('ALT', cpu=model)
                spec = form.spec
                self.assertEqual(spec.cpu_support, {model})
                self.assertEqual(spec.native_operand_order, ('D',))
                self.assertEqual((spec.min_operands, spec.max_operands), (1, 1))
                self.assertEqual(len(spec.operands), 1)
                self.assertEqual((spec.operands[0].data_type, spec.operands[0].role.value,
                                  spec.operands[0].bit_count), ('bit', 'read_write', 1))
                self.assertEqual(spec.execution_form, 'continuous')
                self.assertEqual(call_admission_explanation(form, model)[0], 'admitted')
                self.assertIsNone(spec.st_call)
                self.assertFalse(spec.accepts_arity(0))
                self.assertFalse(spec.accepts_arity(2))
                self.assertTrue({'form_identity', 'cpu_applicability', 'arity', 'operand_order',
                                 'operand_roles', 'operand_types', 'device_classes'} <= set(spec.verified_fields))
                entry = registry.definition_entry('ALT', cpu=model)
                self.assertTrue(refresh_reviewed_call_admission(copy.deepcopy(entry), registry))
                sources = {(s['manual_id'], s['revision'], s['pdf_page']) for s in spec.contract_sources}
                self.assertTrue({('fx3_programming_r', 'R', 75), ('fx3_programming_r', 'R', 413)} <= sources)
                self.assertTrue(any(f['id'] == 'family:ALT:word_bit' for f in entry['facts']))
        path = Path(__file__).resolve().parents[1] / 'resources/instructions/mitsubishi/common.json'
        contracts = [r for r in json.loads(path.read_text(encoding='utf-8'))['cpu_contracts']
                     if r['instruction']['mnemonic'] == 'ALT']
        self.assertEqual({r['cpu'] for r in contracts}, set(ALT_MODELS) | {'FX1S', 'FX1N', 'FX1NC', 'FX2N', 'FX2NC'})
        self.assertEqual(len(contracts), 10)
        self.assertFalse(any(r['instruction'].get('modifier_rule') or r.get('st_call') for r in contracts))

    def test_fx1nc_catalogue_contract_is_native_single_bit_destination_only(self):
        registry = DEFAULT_INSTRUCTION_REGISTRY
        form = registry.resolve_catalog_form('ALT', cpu='FX1NC')
        self.assertEqual(form.spec.cpu_support, {'FX1NC'})
        self.assertEqual(form.spec.native_operand_order, ('D',))
        self.assertEqual((form.spec.min_operands, form.spec.max_operands), (1, 1))
        self.assertEqual(form.spec.operands[0].role.value, 'read_write')
        self.assertEqual(form.spec.operands[0].device_prefixes, ('Y', 'M', 'S'))
        self.assertEqual({(s['manual_id'], s['revision'], s['pdf_page']) for s in form.spec.contract_sources},
                         {('fx3_programming_r', 'R', 78), ('fx3_programming_r', 'R', 413)})
        self.assertIsNone(form.spec.st_call)
        for token in ('Y0', 'M0', 'S0'):
            _validate_source_operand_types(form.spec, [token], 'native', 'FX1NC')
        for token in ('D0.0', 'D0.0Z0', 'X0', 'D0'):
            with self.subTest(token=token), self.assertRaises(PLCJsonValidationError):
                _validate_source_operand_types(form.spec, [token], 'native', 'FX1NC')
        self.assertNotEqual(call_admission_explanation(registry.resolve_catalog_form('ALTP', cpu='FX1NC'), 'FX1NC')[0], 'admitted')

    def test_fx1nc_native_source_rule_needs_catalogue_symbol_and_own_bit_set_data(self):
        from unittest.mock import patch
        from tools import fact_applied_review as producer
        record = dict(id=1, manual_id='fx3_programming_r', manual_number='JY997D16601',
                      revision='R', opcode='ALT', pdf_pages=[413], text='', section='14.7 ALT')
        words = [dict(text=text,x0=x,x1=x+4,top=100,bottom=108)
                 for text,x in [('66',50),('ALT',90),('ALT',205),('D',233),('\uf050',464)]]
        def pages(source, row):
            return [dict(words=words)] if row['pdf_pages']==[78] else []
        row = dict(name='D',type='BOOL',description='Bit device number whose output is alternated')
        with patch.object(producer,'_parameter_source_pages',side_effect=pages), \
             patch.object(producer,'_source_table_groups',return_value=[]), \
             patch.object(producer,'_native_set_data_rows',return_value=[row]):
            facts = producer._alt_fx1nc_catalogue_facts(None,[record])
            self.assertEqual(len(facts),3)
            self.assertEqual({f['dimension'] for f in facts}, {'parameters.order','parameters.arity','parameters.typed_operands'})
            self.assertTrue(all(f['scope']=={'models':['FX1NC'],'forms':['ALT'],'languages':['ladder']} for f in facts))
            self.assertTrue(all(f['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}] for f in facts))
            self.assertEqual(facts[-1]['value']['operands'][0]['direction'],'in_out')
            from dataclasses import replace
            from plc.instruction_definition import InstructionFactGroup
            spec = replace(DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('ALT', cpu='FX1NC').spec,
                definition_facts=tuple(InstructionFactGroup.from_mapping(f) for f in facts))
            for token in ('Y0','M0','S0'):
                _validate_source_operand_types(spec,[token],'catalogue','FX1NC')
            for token in ('M0Z0','D0.0','D0.0Z0'):
                with self.subTest(token=token), self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(spec,[token],'catalogue','FX1NC')
            words[:] = [w for w in words if w['text']!='D']
            self.assertFalse(producer._alt_fx1nc_catalogue_facts(None,[record]))
            words.append(dict(text='D',x0=233,x1=237,top=100,bottom=108))
            words[:] = [w for w in words if w['text']!='\uf050']
            self.assertFalse(producer._alt_fx1nc_catalogue_facts(None,[record]))
            words.append(dict(text='\uf050',x0=464,x1=468,top=100,bottom=108))
            row['type']='unknown'
            self.assertFalse(producer._alt_fx1nc_catalogue_facts(None,[record]))
            self.assertFalse(producer._alt_fx1nc_catalogue_facts(None,[dict(record,opcode='ALTP')]))

    def test_native_bit_target_and_word_bit_model_index_limits(self):
        for model in ALT_MODELS:
            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('ALT', cpu=model).spec
            for token in ('Y0', 'M0', 'S0'):
                with self.subTest(model=model, token=token):
                    _validate_source_operand_types(spec, [token], 'native', model)
            for token in ('X0', 'T0', 'C0', 'D0', 'K1', 'D0.0Z0', 'D0.0V0'):
                with self.subTest(model=model, token=token):
                    with self.assertRaises(PLCJsonValidationError):
                        _validate_source_operand_types(spec, [token], 'native', model)
            if model in {'FX3U', 'FX3UC'}:
                _validate_source_operand_types(spec, ['D0.0'], 'native', model)
            else:
                with self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(spec, ['D0.0'], 'native', model)

    def test_source_versions_are_not_fabricated_and_old_claim_is_preserved(self):
        from plc.instruction_knowledge import compose_programming_facts
        for model in ALT_MODELS:
            registry = DEFAULT_INSTRUCTION_REGISTRY
            original = copy.deepcopy(registry.definition_entry('ALT', cpu=model))
            old = next(f for f in original['facts'] if f['id'] == 'family:ALT:availability')
            # Freeze the withdrawn authored claim so this remains a history
            # regression after the corrected package becomes the runtime.
            versions = {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40', 'FX3U': '2.20', 'FX3UC': '1.00'}
            old['value']['minimum_cpu_versions'] = versions
            old['conditions'] = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                                 {'context': 'cpu_version', 'minimum_by_model': versions}]
            facts, replaced = compose_programming_facts(registry, 'ALT', cpu=model, existing=original['facts'])
            availability = next(f for f in facts if f['id'] == 'family:ALT:availability')
            self.assertEqual(availability['value'], {'available': True})
            self.assertEqual(availability['conditions'],
                             [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}])
            self.assertTrue(any(f['id'] == 'family:ALT:availability'
                and f['value'].get('minimum_cpu_versions') for f in replaced))
            from plc.instruction_constraints import check_fact_constraints
            state = check_fact_constraints([availability], {}, target='ALT', plc_model=model,
                language='ladder', context={'project_mode': 'native_ladder_instruction'})
            self.assertFalse(any('version' in f.get('message', '').lower() for f in state['findings']))

    def test_other_cpu_native_admission_is_not_expanded(self):
        for model in ('FX0', 'Q03UDV', 'L02'):
            with self.subTest(model=model):
                form = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('ALT', cpu=model)
                self.assertEqual(call_admission_explanation(form, model)[0], 'target_not_in_declared_scope')



class AltSourceScopedExtensionsTests(unittest.TestCase):
    def compose(self, opcode, model, *, withdrawn_direction=False):
        from plc.instruction_knowledge import compose_programming_facts
        registry = DEFAULT_INSTRUCTION_REGISTRY
        original = copy.deepcopy(registry.definition_entry(opcode, cpu=model))
        if withdrawn_direction:
            native = next(f for f in original['facts']
                          if f['id'] == 'authored:applied:native_typed_operands@jy992d88101e')
            native['value']['operands'][0]['direction'] = 'output'
        facts, replaced = compose_programming_facts(registry, opcode, cpu=model, existing=original['facts'])
        original['facts'] = facts
        original.setdefault('superseded_facts', []).extend(replaced)
        return original

    def test_legacy_alt_uses_its_own_four_cpu_header_and_read_write_destination(self):
        for model in ('FX1S', 'FX1N', 'FX2N', 'FX2NC'):
            with self.subTest(model=model):
                registry = DEFAULT_INSTRUCTION_REGISTRY
                form = registry.resolve_catalog_form('ALT', cpu=model)
                self.assertEqual(call_admission_explanation(form, model)[0], 'admitted')
                self.assertEqual(form.spec.operands[0].role.value, 'read_write')
                self.assertEqual(form.spec.operands[0].device_prefixes, ('Y', 'M', 'S'))
                self.assertEqual({s['manual_id'] for s in form.spec.contract_sources}, {'jy992d88101e'})
                entry = self.compose('ALT', model, withdrawn_direction=True)
                self.assertTrue(refresh_reviewed_call_admission(entry, registry))
                native = next(f for f in entry['facts'] if f['id'] == 'authored:applied:native_typed_operands@jy992d88101e')
                self.assertEqual(native['value']['operands'][0]['direction'], 'in_out')
                self.assertEqual({s['manual_id'] for s in native['sources']}, {'jy992d88101e'})
                self.assertTrue(any(f['id'] == native['id'] and f['value']['operands'][0]['direction'] == 'output'
                                    for f in entry['superseded_facts']))
                for token in ('Y0', 'M0', 'S0'):
                    _validate_source_operand_types(form.spec, [token], 'legacy', model)
                for token in ('D0', 'D0.0', 'X0', 'D0.0Z0'):
                    with self.assertRaises(PLCJsonValidationError):
                        _validate_source_operand_types(form.spec, [token], 'legacy', model)

    def test_altp_five_cpu_alias_and_execution_remain_pulse_scoped(self):
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for model in ALT_MODELS:
            with self.subTest(model=model):
                form = registry.resolve_catalog_form('ALTP', cpu=model)
                self.assertEqual(form.base_mnemonic, 'ALT')
                self.assertTrue(form.pulse)
                self.assertFalse(form.double)
                self.assertEqual(form.spec.execution_form, 'pulse')
                self.assertEqual(form.spec.native_operand_order, ('D',))
                self.assertEqual(form.spec.cpu_support, {model})
                entry = self.compose('ALTP', model)
                self.assertTrue(refresh_reviewed_call_admission(entry, registry))
                trigger = next(f for f in entry['facts'] if f['id'] == 'family:ALT:trigger')
                self.assertEqual(trigger['value']['trigger'], 'execution_condition_rising_edge')
                self.assertEqual(trigger['value']['executions_per_edge'], 1)
                self.assertEqual({s['manual_id'] for s in form.spec.contract_sources}, {'fx3_programming_r'})
                _validate_source_operand_types(form.spec, ['M0'], 'pulse', model)
                if model in {'FX3U', 'FX3UC'}:
                    _validate_source_operand_types(form.spec, ['D0.0'], 'pulse', model)
                else:
                    with self.assertRaises(PLCJsonValidationError):
                        _validate_source_operand_types(form.spec, ['D0.0'], 'pulse', model)
                with self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(form.spec, ['D0.0Z0'], 'pulse', model)
        for model in ('FX0', 'FX0N', 'FX0S', 'FX1NC', 'FX2C', 'FXU', 'Q03UDV'):
            with self.subTest(unadmitted_pulse_model=model):
                self.assertFalse(registry.resolve_catalog_form('ALTP', cpu=model).spec.supports_cpu(model))


class AltStructuredGateRegressionTests(unittest.TestCase):
    def test_recomposed_alt_and_altp_keep_three_source_interfaces(self):
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for opcode in ('ALT', 'ALTP'):
            for model in ALT_MODELS:
                entry = registry.definition_entry(opcode, cpu=model)
                facts, _ = compose_programming_facts(registry, opcode, cpu=model, existing=entry['facts'])
                rows, _ = inspect_owner(entry, registry, composed_facts=facts)
                by_interface = {row['interface']: row for row in rows}
                previous, _ = inspect_owner(entry, registry, composed_facts=entry['facts'])
                previous = {row['interface']: row for row in previous}
                for interface in ('native_ladder_instruction',):
                    self.assertEqual(by_interface[interface]['states'], previous[interface]['states'])
                for facet, state in previous['st_call']['states'].items():
                    self.assertEqual(by_interface['st_call']['states'][facet],
                                     'answered' if facet == 'disabled' else state)
                for interface in ('fbd_call', 'structured_ladder_block', 'native_ladder_instruction'):
                    with self.subTest(opcode=opcode, model=model, interface=interface):
                        self.assertEqual(by_interface[interface]['states']['effects'], 'answered')
                        # ALTP's own diagram binds the retained device; the
                        # common EN table independently supplies ENO=False.
                        self.assertEqual(by_interface[interface]['states']['disabled'],
                                         'answered')
                for gate_id, signature_id in (
                    ('family:ALT:structured-enable', 'authored:applied:fbd_typed_operands@fxcpu_basic_applied_m'),
                    ('family:ALT:structured-ladder-enable', 'authored:applied:ladder_typed_operands@fxcpu_basic_applied_m')):
                    gate = next(f for f in facts if f['id'] == gate_id)
                    self.assertTrue(gate['value']['has_EN_ENO'])
                    self.assertEqual(gate['value']['enable_argument'], 'EN')
                    self.assertEqual(gate['value']['status_argument'], 'ENO')
                    self.assertEqual(gate['value']['operand_binding']['execution_arguments'], [
                        {'parameter': 'EN', 'role': 'execution_condition'},
                        {'parameter': 'ENO', 'role': 'execution_status'}])
                    self.assertNotIn('when_disabled', gate['value'])
                    self.assertNotIn('disabled_ENO', gate['value'])
                    self.assertEqual(gate['sources'][0]['pdf_pages'], [50, 342])
                    for dimension in ('effects.documented_processing', 'execution.disabled_state'):
                        self.assertTrue(any(f['dimension'] == dimension and (opcode == 'ALT' or gate_id in f.get('depends_on', []))
                            and signature_id in f.get('depends_on', []) for f in facts))
                native = next(f for f in facts if f['id'] == 'family:ALT:effect')
                self.assertFalse(any('structured' in identity for identity in native.get('depends_on', [])))

    def test_shared_block_relation_requires_its_actual_gate_source_and_interface(self):
        from plc.instruction_knowledge import _bind_shared_definition
        registry = DEFAULT_INSTRUCTION_REGISTRY
        definition = next(d for d in registry._family_knowledge if d['base_opcode'] == 'ALT')
        original = next(f for f in definition['facts'] if f['id'] == 'family:ALT:structured-effect-all')
        signature = copy.deepcopy(next(f for f in registry.definition_entry('ALTP', cpu='FX3U')['facts']
            if f['id'] == 'authored:applied:fbd_typed_operands@fxcpu_basic_applied_m'))
        gate = copy.deepcopy(next(f for f in definition['facts'] if f['id'] == 'family:ALT:structured-enable'))
        self.assertTrue(_bind_shared_definition(definition, original, [signature, gate]))
        for case in ('missing_gate', 'unrelated_source', 'wrong_language', 'wrong_cpu', 'wrong_pin_direction'):
            with self.subTest(case=case):
                current_signature, current_gate = copy.deepcopy(signature), copy.deepcopy(gate)
                if case == 'unrelated_source': current_gate['sources'][0]['pdf_pages'] = [50]
                if case == 'wrong_language': current_gate['scope']['languages'] = ['st']
                if case == 'wrong_cpu': current_gate['scope']['models'] = ['FX1S']
                if case == 'wrong_pin_direction': current_signature['value']['operands'][0]['direction'] = 'output'
                existing = [current_signature] if case == 'missing_gate' else [current_signature, current_gate]
                self.assertEqual(_bind_shared_definition(definition, original, existing), [])


class AltpStructuredDisabledOutputTests(unittest.TestCase):
    INTERFACES = (
        ('family:ALTP:structured-disabled-output', 'fbd', 'fbd_call',
         'authored:applied:fbd_typed_operands@fxcpu_basic_applied_m', 'family:ALT:structured-enable'),
        ('family:ALTP:structured-ladder-disabled-output', 'ladder', 'structured_ladder_block',
         'authored:applied:ladder_typed_operands@fxcpu_basic_applied_m', 'family:ALT:structured-ladder-enable'),
    )

    def source_facts(self):
        path = Path(__file__).resolve().parents[1] / 'resources/instructions/mitsubishi/instruction_families.json'
        family = next(f for f in json.loads(path.read_text(encoding='utf-8'))['instruction_families']
                      if f['base_opcode'] == 'ALT')
        return {f['id']: f for f in family['facts']}

    def test_two_exact_source_contracts_keep_one_device_and_separate_operation_results(self):
        facts = self.source_facts()
        for identity, language, interface, signature, gate in self.INTERFACES:
            with self.subTest(interface=interface):
                fact = facts[identity]
                self.assertEqual(fact['dimension'], 'execution.disabled_state')
                self.assertEqual(fact['scope'], {'models': list(ALT_MODELS), 'forms': ['ALTP'],
                                                  'languages': [language]})
                self.assertEqual(fact.get('conditions', []), [] if language == 'fbd' else
                                 [{'context': 'project_mode', 'equals': 'structured_ladder_block'}])
                self.assertEqual(fact['depends_on'], [gate])
                self.assertEqual((fact['status'], fact['verification']), ('source_verified', 'source_checked'))
                self.assertEqual(fact['value'], {
                    'when_disabled': 'retain_previous', 'outputs': ['d'], 'disabled_ENO': False,
                    'operation_results': {'context': 'generic_EN_ENO_table', 'EN': False, 'value': 'undefined'}})
                sources = fact['sources']
                self.assertTrue(sources)
                self.assertEqual({(s['manual_id'], s['manual_number'], s['revision']) for s in sources},
                                 {('fxcpu_basic_applied_m', 'JY997D34701', 'M')})
                self.assertEqual({p for s in sources for p in s['pdf_pages']}, {50, 342, 343})
                self.assertNotIn('do_not_use', json.dumps(fact['value']))

    def test_composed_off_coverage_needs_both_actual_device_and_status_bindings(self):
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for model in ALT_MODELS:
            entry = registry.definition_entry('ALTP', cpu=model)
            facts, _ = compose_programming_facts(registry, 'ALTP', cpu=model, existing=entry['facts'])
            for identity, language, interface, signature, gate in self.INTERFACES:
                with self.subTest(model=model, interface=interface):
                    actual = next(f for f in facts if f['id'] == identity)
                    self.assertEqual(actual['value']['outputs'], ['d'])
                    self.assertEqual(actual['depends_on'], [gate])
                    for case in ('complete', 'missing_device', 'missing_status', 'generic_result_only', 'missing_gate'):
                        changed = copy.deepcopy(facts)
                        target = next(f for f in changed if f['id'] == identity)
                        if case == 'missing_device': target['value'].pop('outputs')
                        if case == 'missing_status': target['value'].pop('disabled_ENO')
                        if case == 'generic_result_only':
                            target['value'] = {'operation_results': target['value']['operation_results']}
                        if case == 'missing_gate': changed = [f for f in changed if f['id'] != gate]
                        rows, _ = inspect_owner(entry, registry, composed_facts=changed)
                        row = next(r for r in rows if r['interface'] == interface)
                        if case == 'complete':
                            self.assertEqual(row['states']['disabled'], 'answered', (model, interface, case))
                        else:
                            self.assertNotEqual(row['states']['disabled'], 'answered', (model, interface, case))


class AltModernStructuredCompletionTests(unittest.TestCase):
    def test_source_additions_are_modern_and_st_has_no_exposed_eno_result(self):
        facts = AltpStructuredDisabledOutputTests().source_facts()
        identities = [
            'family:ALT:structured-disabled-output', 'family:ALT:structured-ladder-disabled-output',
            'family:ALTP:structured-purpose', 'family:ALTP:structured-ladder-purpose',
            'family:ALT:st-enable', 'family:ALT:st-disabled-output']
        for identity in identities:
            with self.subTest(identity=identity):
                fact = facts[identity]
                self.assertEqual(fact['scope']['models'], list(ALT_MODELS))
                self.assertEqual((fact['status'], fact['verification']), ('source_verified', 'source_checked'))
                self.assertEqual({s['manual_id'] for s in fact['sources']}, {'fxcpu_basic_applied_m'})
                self.assertNotIn('do_not_use', json.dumps(fact))
        for suffix in ('structured', 'structured-ladder'):
            off = facts[f'family:ALT:{suffix}-disabled-output']
            self.assertEqual(off['scope']['forms'], ['ALT'])
            self.assertEqual(off['value'], {
                'when_disabled': 'retain_previous', 'outputs': ['d'], 'disabled_ENO': False,
                'operation_results': {'context': 'generic_EN_ENO_table', 'EN': False, 'value': 'undefined'}})
            self.assertEqual(off['depends_on'], [f'family:ALT:{suffix}-enable'])
            self.assertEqual(off['sources'][0]['pdf_pages'], [50, 342, 343, 344])
            purpose = facts[f'family:ALTP:{suffix}-purpose']
            self.assertEqual(purpose['scope']['forms'], ['ALTP'])
            self.assertEqual(purpose['value']['parameter_usage'], [
                {'parameter': 'EN', 'purpose': 'Execution condition'},
                {'parameter': 'ENO', 'purpose': 'Execution state'},
                {'parameter': 'd', 'purpose': 'Bit device to be output alternately'}])
        gate = facts['family:ALT:st-enable']
        self.assertEqual(gate['value'], {'enable_argument': 'EN', 'operand_binding': {
            'execution_arguments': [{'parameter': 'EN', 'role': 'execution_condition'}]}})
        off = facts['family:ALT:st-disabled-output']
        self.assertEqual(off['scope']['forms'], ['ALT', 'ALTP'])
        self.assertEqual(off['scope']['languages'], ['st'])
        self.assertEqual(off['depends_on'], [gate['id']])
        self.assertEqual(off['value'], {'when_disabled': 'retain_previous', 'outputs': ['d'],
            'operation_results': {'context': 'generic_EN_ENO_table', 'EN': False, 'value': 'undefined'}})

    def test_actual_composition_keeps_statement_shape_and_source_purposes(self):
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for opcode in ('ALT', 'ALTP'):
            for model in ALT_MODELS:
                with self.subTest(opcode=opcode, model=model):
                    entry = registry.definition_entry(opcode, cpu=model)
                    facts, _ = compose_programming_facts(registry, opcode, cpu=model, existing=entry['facts'])
                    rows, _ = inspect_owner(entry, registry, composed_facts=facts)
                    rows = {row['interface']: row for row in rows}
                    for interface in ('fbd_call', 'structured_ladder_block', 'st_call'):
                        self.assertEqual(rows[interface]['states']['disabled'], 'answered')
                        self.assertEqual(rows[interface]['states']['purpose'], 'answered')
                    st = next(f for f in facts if f['id'] == 'authored:applied:st_typed_operands@fxcpu_basic_applied_m')
                    self.assertEqual([(p['name'], p['direction']) for p in st['value']['operands']],
                                     [('EN', 'input'), ('d', 'output')])
                    self.assertFalse(st['value'].get('returns'))
                    self.assertFalse(st['value'].get('return_shape'))
                    missing_st_device = copy.deepcopy(facts)
                    next(f for f in missing_st_device if f['id'] == 'family:ALT:st-disabled-output')['value'].pop('outputs')
                    changed_rows, _ = inspect_owner(entry, registry, composed_facts=missing_st_device)
                    self.assertNotEqual(next(row for row in changed_rows if row['interface'] == 'st_call')
                                        ['states']['disabled'], 'answered')
                    if opcode == 'ALT':
                        for suffix, interface in [('structured', 'fbd_call'),
                                                  ('structured-ladder', 'structured_ladder_block')]:
                            for missing in ('outputs', 'disabled_ENO'):
                                changed = copy.deepcopy(facts)
                                next(f for f in changed if f['id'] == f'family:ALT:{suffix}-disabled-output')\
                                    ['value'].pop(missing)
                                changed_rows, _ = inspect_owner(entry, registry, composed_facts=changed)
                                self.assertNotEqual(next(row for row in changed_rows if row['interface'] == interface)
                                                    ['states']['disabled'], 'answered')
                    if opcode == 'ALTP':
                        for suffix, signature in (
                            ('structured', 'authored:applied:fbd_typed_operands@fxcpu_basic_applied_m'),
                            ('structured-ladder', 'authored:applied:ladder_typed_operands@fxcpu_basic_applied_m')):
                            derived = [f for f in facts if f['dimension'] == 'parameters.typed_operands'
                                and signature in f.get('depends_on', [])
                                and f'family:ALTP:{suffix}-purpose' in f.get('depends_on', [])]
                            self.assertTrue(derived)
                            for name, text in [('EN', 'Execution condition'), ('ENO', 'Execution state'),
                                               ('d', 'Bit device to be output alternately')]:
                                self.assertTrue(any(text in json.dumps(next(p for p in f['value']['operands']
                                    if p['name'] == name).get('purpose')) for f in derived))


class AltLegacyMStructuredCompletionTests(unittest.TestCase):
    def test_m_source_extensions_are_only_alt_and_only_the_ten_legacy_models(self):
        facts = AltpStructuredDisabledOutputTests().source_facts()
        additions = [f for identity, f in facts.items() if identity.startswith('family:ALT:legacy-m-')]
        self.assertEqual(len(additions), 9)
        for fact in additions:
            with self.subTest(identity=fact['id']):
                self.assertEqual(fact['scope']['models'], list(ALT_LEGACY_M_MODELS))
                self.assertEqual(fact['scope']['forms'], ['ALT'])
                self.assertEqual((fact['status'], fact['verification']), ('source_verified', 'source_checked'))
                self.assertEqual({(s['manual_id'], s['revision']) for s in fact['sources']},
                                 {('fxcpu_basic_applied_m', 'M')})
                self.assertEqual(fact.get('conditions', []),
                    [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                    if fact['scope']['languages'] == ['ladder'] else [])
                if fact['dimension'] == 'execution.enable':
                    self.assertEqual(fact['value']['execution_form'], 'continuous')
                    self.assertEqual(fact['value']['trigger'], 'each_evaluation_when_execution_condition_true')
                    self.assertEqual(fact['value']['when_condition_false'], 'not_executed')
                if fact['dimension'] == 'effects.documented_processing':
                    self.assertEqual(fact['value']['definition_ref'], 'effect')
                    self.assertEqual(fact['value']['parameter_bindings'], {'D': 'd'})
        st_gate = facts['family:ALT:legacy-m-st-enable']
        self.assertEqual(st_gate['value']['operand_binding']['execution_arguments'],
                         [{'parameter': 'EN', 'role': 'execution_condition'}])
        st_off = facts['family:ALT:legacy-m-st-disabled-output']
        self.assertEqual(st_off['value']['outputs'], ['d'])
        self.assertNotIn('disabled_ENO', st_off['value'])
        self.assertNotIn('returns', st_off['value'])

    def test_actual_legacy_m_signatures_bind_three_interfaces_without_native_or_pulse_expansion(self):
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for model in ALT_LEGACY_M_MODELS:
            with self.subTest(model=model):
                entry = registry.definition_entry('ALT', cpu=model)
                before, _ = inspect_owner(entry, registry, composed_facts=entry['facts'])
                facts, _ = compose_programming_facts(registry, 'ALT', cpu=model, existing=entry['facts'])
                after, _ = inspect_owner(entry, registry, composed_facts=facts)
                before = {row['interface']: row for row in before}
                after = {row['interface']: row for row in after}
                if 'native_ladder_instruction' in before:
                    expected = dict(before['native_ladder_instruction']['states'])
                    if model == 'FX1NC': expected['effects'] = 'answered'
                    self.assertEqual(after['native_ladder_instruction']['states'], expected)
                for language, interface, suffix in [('fbd', 'fbd_call', 'structured'),
                    ('ladder', 'structured_ladder_block', 'structured-ladder'), ('st', 'st_call', 'st')]:
                    self.assertEqual(after[interface]['states']['effects'], 'answered')
                    self.assertEqual(after[interface]['states']['disabled'], 'answered')
                    signature = f'authored:applied:{language}_typed_operands@fxcpu_basic_applied_m'
                    own = next(f for f in facts if f['id'] == signature)
                    self.assertEqual(own['value']['executor_capability'], 'not_established')
                    self.assertEqual([(p['name'], p['type'], p['direction']) for p in own['value']['operands']],
                        [('EN', 'BOOL', 'input'), ('d', 'BOOL', 'output')] if language == 'st' else
                        [('EN', 'BOOL', 'input'), ('ENO', 'BOOL', 'output'), ('d', 'BOOL', 'output')])
                    bound = next(f for f in facts if f['dimension'] == 'effects.documented_processing'
                        and 'legacy-m-' in f['id'] and signature in f.get('depends_on', []))
                    self.assertIn(f'family:ALT:legacy-m-{suffix}-enable', bound['depends_on'])
                    rule = bound['value']['rules'][0]
                    self.assertEqual(rule['outputs'][0]['target']['parameter'], 'd')
                    self.assertEqual(rule['outputs'][0]['expression']['op'], 'not')
                    changed = copy.deepcopy(facts)
                    next(f for f in changed if f['id'] == f'family:ALT:legacy-m-{suffix}-disabled-output')\
                        ['value'].pop('outputs')
                    rows, _ = inspect_owner(entry, registry, composed_facts=changed)
                    self.assertNotEqual(next(row for row in rows if row['interface'] == interface)
                                        ['states']['disabled'], 'answered')
                pulse = registry.definition_entry('ALTP', cpu=model)
                pulse_facts, _ = compose_programming_facts(registry, 'ALTP', cpu=model,
                                                         existing=pulse['facts'] if pulse else [])
                self.assertFalse(any('family:ALT:legacy-m-' in f['id'] for f in pulse_facts))

    def test_m_result_binding_requires_own_typed_destination_and_actual_gate(self):
        from plc.instruction_knowledge import _bind_shared_definition
        registry = DEFAULT_INSTRUCTION_REGISTRY
        definition = next(d for d in registry._family_knowledge if d['base_opcode'] == 'ALT')
        result = next(f for f in definition['facts'] if f['id'] == 'family:ALT:legacy-m-structured-effect')
        entry = registry.definition_entry('ALT', cpu='FX1NC')
        signature = next(f for f in entry['facts']
                         if f['id'] == 'authored:applied:fbd_typed_operands@fxcpu_basic_applied_m')
        gate = next(f for f in definition['facts'] if f['id'] == 'family:ALT:legacy-m-structured-enable')
        self.assertTrue(_bind_shared_definition(definition, result, [signature, gate]))
        for case in ('no_signature', 'unknown_type', 'wrong_direction', 'no_gate', 'unrelated_source'):
            with self.subTest(case=case):
                own = copy.deepcopy(signature)
                if case == 'unknown_type': own['value']['operands'][-1]['type'] = 'unknown'
                if case == 'wrong_direction': own['value']['operands'][-1]['direction'] = 'input'
                if case == 'unrelated_source': own['sources'] = [{'manual_id': 'unrelated', 'pdf_pages': [342]}]
                # Remove source-declared gate roles as well when testing the
                # absence of both an own EN contract and a related gate source.
                if case == 'no_gate':
                    for pin in own['value']['operands']:
                        pin.pop('enable_pin', None)
                        pin.pop('role', None)
                existing = [gate] if case == 'no_signature' else [own] if case == 'no_gate' else [own, gate]
                self.assertFalse(_bind_shared_definition(definition, result, existing))


class AltpLegacyExactCompletionTests(unittest.TestCase):
    def test_m_pulse_extensions_use_only_seven_declared_models_and_three_real_interfaces(self):
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        source = AltpStructuredDisabledOutputTests().source_facts()
        additions = [f for identity, f in source.items() if identity.startswith('family:ALTP:legacy-m-')]
        self.assertEqual(len(additions), 12)
        for fact in additions:
            self.assertEqual(fact['scope']['models'], list(ALTP_LEGACY_M_MODELS))
            self.assertEqual(fact['scope']['forms'], ['ALTP'])
            if fact['dimension'] == 'execution.enable':
                self.assertEqual(fact['value']['execution_form'], 'pulse')
                self.assertEqual(fact['value']['trigger'], 'execution_condition_rising_edge')
                self.assertEqual(fact['value']['executions_per_edge'], 1)
        for model in ALTP_LEGACY_M_MODELS:
            with self.subTest(model=model):
                entry = registry.definition_entry('ALTP', cpu=model)
                facts, _ = compose_programming_facts(registry, 'ALTP', cpu=model, existing=entry['facts'])
                rows, _ = inspect_owner(entry, registry, composed_facts=facts)
                rows = {row['interface']: row for row in rows}
                for interface in ('fbd_call', 'structured_ladder_block', 'st_call'):
                    for facet in ('purpose', 'effects', 'disabled'):
                        self.assertEqual(rows[interface]['states'][facet], 'answered')
                st = next(f for f in facts if f['id'] == 'family:ALTP:legacy-m-st-disabled-output')
                self.assertEqual(st['value']['outputs'], ['d'])
                self.assertNotIn('disabled_ENO', st['value'])
                self.assertNotIn('ENO', json.dumps(source['family:ALTP:legacy-m-st-enable']['value']))
        for model in ('FX0', 'FX0N', 'FX0S'):
            with self.subTest(excluded=model):
                entry = registry.definition_entry('ALTP', cpu=model)
                facts, _ = compose_programming_facts(registry, 'ALTP', cpu=model, existing=entry['facts'])
                self.assertFalse(any('family:ALTP:legacy-m-' in f['id'] for f in facts))
                self.assertFalse(registry.resolve_catalog_form('ALTP', cpu=model).spec.supports_cpu(model))

    def test_four_exact_e_native_contracts_require_read_write_source_and_reject_word_bit_and_index(self):
        from dataclasses import replace
        from plc.instruction_definition import InstructionFactGroup
        from plc.instruction_knowledge import compose_programming_facts
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        for model in ALTP_LEGACY_NATIVE_MODELS:
            with self.subTest(model=model):
                form = registry.resolve_catalog_form('ALTP', cpu=model)
                self.assertEqual(form.spec.cpu_support, {model})
                self.assertTrue(form.pulse)
                self.assertEqual(form.spec.execution_form, 'pulse')
                self.assertEqual(form.spec.native_operand_order, ('D',))
                self.assertEqual(form.spec.operands[0].device_prefixes, ('Y', 'M', 'S'))
                self.assertIsNone(form.spec.st_call)
                self.assertEqual({s['manual_id'] for s in form.spec.contract_sources}, {'jy992d88101e'})
                entry = copy.deepcopy(registry.definition_entry('ALTP', cpu=model))
                own = next(f for f in entry['facts']
                           if f['id'] == 'authored:applied:native_typed_operands@jy992d88101e')
                # Freeze the independently reviewed E212 declaration, so the
                # relation test also runs before its source delta is installed.
                own['value']['operands'][0].update(direction='in_out', role='read_write',
                    purpose='bit_device_whose_state_is_inverted_on_pulse_execution',
                    range={'device_prefixes': ['Y', 'M', 'S'], 'index_modification': False})
                facts, _ = compose_programming_facts(registry, 'ALTP', cpu=model, existing=entry['facts'])
                entry['facts'] = facts
                self.assertTrue(refresh_reviewed_call_admission(entry, registry))
                rows, _ = inspect_owner(entry, registry, composed_facts=facts)
                native = next(row for row in rows if row['interface'] == 'native_ladder_instruction')
                self.assertEqual(native['states']['effects'], 'answered')
                self.assertEqual(native['states']['disabled'], 'missing')
                trigger = next(f for f in facts if f['id'] == 'family:ALT:legacy-native-pulse-trigger')
                self.assertEqual(trigger['value']['trigger'], 'execution_condition_rising_edge')
                self.assertEqual(trigger['value']['executions_per_edge'], 1)
                relation = next(f for f in facts if 'legacy-native-pulse-effect' in f['id'])
                self.assertIn(own['id'], relation['depends_on'])
                spec = replace(form.spec, definition_facts=tuple(InstructionFactGroup.from_mapping(f) for f in facts))
                for token in ('Y0', 'M0', 'S0'):
                    _validate_source_operand_types(spec, [token], 'native', model)
                for token in ('X0', 'D0', 'D0.0', 'M0Z0', 'D0.0Z0', 'K1'):
                    with self.subTest(token=token), self.assertRaises(PLCJsonValidationError):
                        _validate_source_operand_types(spec, [token], 'native', model)
        for model in ('FX0', 'FX0N', 'FX0S', 'FX1NC', 'FX2C', 'FXU'):
            self.assertNotEqual(call_admission_explanation(registry.resolve_catalog_form('ALTP', cpu=model), model)[0],
                                'admitted')

    def test_fx1nc_native_alt_result_requires_own_r_declaration_and_keeps_off_unspecified(self):
        from plc.instruction_knowledge import compose_programming_facts, _bind_shared_definition
        from tools.inventory_instruction_semantics import inspect_owner
        registry = DEFAULT_INSTRUCTION_REGISTRY
        entry = registry.definition_entry('ALT', cpu='FX1NC')
        facts, _ = compose_programming_facts(registry, 'ALT', cpu='FX1NC', existing=entry['facts'])
        rows, _ = inspect_owner(entry, registry, composed_facts=facts)
        native = next(row for row in rows if row['interface'] == 'native_ladder_instruction')
        self.assertEqual(native['states']['effects'], 'answered')
        self.assertEqual(native['states']['disabled'], 'missing')
        own_id = 'authored:applied:native_typed_operands@fx3_programming_r:fx1nc_native_catalogue'
        relation = next(f for f in facts if 'fx1nc-native-continuous-effect' in f['id'])
        self.assertIn(own_id, relation['depends_on'])
        self.assertEqual({s['manual_id'] for s in relation['sources']}, {'fx3_programming_r'})
        definition = next(d for d in registry._family_knowledge if d['base_opcode'] == 'ALT')
        original = next(f for f in definition['facts'] if f['id'] == 'family:ALT:fx1nc-native-continuous-effect')
        own = copy.deepcopy(next(f for f in entry['facts'] if f['id'] == own_id))
        self.assertTrue(_bind_shared_definition(definition, original, [own]))
        own['value']['operands'][0]['direction'] = 'output'
        self.assertFalse(_bind_shared_definition(definition, original, [own]))


if __name__ == '__main__': unittest.main()

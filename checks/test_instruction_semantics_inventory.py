"""One bounded regression for the offline library inventory classifier."""
import copy
import unittest


class EnabledCallDisabledCoverageTests(unittest.TestCase):
    def facts(self, *, native=False, multiple=False):
        language = 'ladder' if native else 'fbd'
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value, 'status': 'source_verified',
                'verification': 'source_checked', 'sources': [{'manual_id': 'synthetic', 'pdf_pages': [1]}],
                'scope': {'models': ['Q03UDV'], 'forms': ['SYNTH_ENABLE'], 'languages': [language]},
                'conditions': [], 'depends_on': []}
        pins = [{'name': 'x', 'type': 'INT', 'direction': 'input'},
                {'name': 'first', 'type': 'INT', 'direction': 'output'}]
        if multiple:pins.append({'name': 'second', 'type': 'INT', 'direction': 'output'})
        if not native:
            pins.insert(0, {'name': 'run', 'type': 'BOOL', 'direction': 'input', 'role': 'enable'})
            pins.append({'name': 'ok', 'type': 'BOOL', 'direction': 'output', 'role': 'execution_status'})
        signature = fact('signature', 'parameters.typed_operands', {'operands': pins})
        gate = fact('gate', 'execution.operation_condition', {'enabled_when': 'EN_true', 'when_disabled': 'operation_stopped'})
        return signature, gate, fact

    def states(self, facts, language='fbd'):
        from knowledge.structured_facts import _aspect_state
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner
        packet = {'kind': 'instruction', 'target': 'SYNTH_ENABLE', 'value': {'facts': facts}}
        shared = _aspect_state(packet, 'disabled', model='Q03UDV', language=language, query='')
        rows, _ = inspect_owner({'opcode': 'SYNTH_ENABLE', 'target_model': 'Q03UDV', 'facts': facts},
                                InstructionRegistry(), composed_facts=facts)
        return shared, rows[0]['states']['disabled']

    def test_stop_and_status_only_do_not_answer_data_output_OFF(self):
        from knowledge.structured_facts import _aspect_state
        for status_only in [False, True]:
            with self.subTest(status_only=status_only):
                signature, gate, _ = self.facts()
                if status_only:gate['value']['disabled_ENO'] = False
                facts = [signature, gate];before = copy.deepcopy(facts)
                self.assertEqual(self.states(facts), ('unresolved', 'unresolved'))
                self.assertEqual(_aspect_state({'target': 'SYNTH_ENABLE', 'value': {'facts': facts}},
                    'execution', model='Q03UDV', language='fbd', query=''), 'answered')
                self.assertEqual(facts, before)

    def test_explicit_all_output_states_or_EN_false_outcome_answer(self):
        for state in ['undefined_do_not_use', 'retain_previous', 'clear']:
            with self.subTest(state=state):
                signature, gate, fact = self.facts()
                off = fact('off', 'execution.disabled', {'when_disabled': state, 'outputs': ['first'], 'disabled_ENO': False})
                off['depends_on'] = ['signature', 'gate']
                self.assertEqual(self.states([signature, gate, off]), ('answered', 'answered'))
        signature, gate, fact = self.facts()
        outcome = fact('outcome', 'execution.outcome', {'cases': [{'EN': False, 'ENO': False, 'result': 'retain_previous'}]})
        self.assertEqual(self.states([signature, gate, outcome]), ('answered', 'answered'))

    def test_incomplete_unqualified_and_FB_output_rules_stay_unresolved(self):
        for case in ['data_only', 'partial_outputs', 'unknown_output', 'partial_rule', 'missing_dependency',
                     'conflict', 'wrong_language', 'wrong_form', 'unknown_FB_state']:
            with self.subTest(case=case):
                signature, gate, fact = self.facts(multiple=case == 'partial_outputs')
                off = fact('off', 'execution.disabled', {'when_disabled': 'retain_previous', 'outputs': ['first'], 'disabled_ENO': False})
                if case == 'data_only':off['value'].pop('disabled_ENO')
                if case == 'unknown_output':off['value']['when_disabled'] = 'unknown'
                if case == 'partial_rule':off['value']['full_source_relation_complete'] = False
                if case == 'missing_dependency':off['depends_on'] = ['missing']
                if case == 'conflict':off['status'] = 'conflict'
                if case == 'wrong_language':off['scope']['languages'] = ['st']
                if case == 'wrong_form':off['scope']['forms'] = ['OTHER']
                if case == 'unknown_FB_state':off['value']['unresolved_items'] = ['internal_state_when_disabled']
                shared, inventory = self.states([signature, gate, off])
                self.assertNotEqual(shared, 'answered')
                self.assertNotEqual(inventory, 'answered')

    def test_Native_without_EN_keeps_existing_disabled_state(self):
        signature, _, fact = self.facts(native=True)
        off = fact('off', 'execution.disabled_state', {'disabled_state': 'retained'})
        self.assertEqual(self.states([signature, off], language='ladder'), ('answered', 'answered'))

    def test_existing_enable_pin_and_bound_execution_roles_are_used_without_pin_names(self):
        for basis in ['enable_pin', 'operand_binding']:
            with self.subTest(basis=basis):
                signature, gate, fact = self.facts()
                signature['value']['operands'][0].pop('role')
                signature['value']['operands'][-1].pop('role')
                if basis == 'enable_pin':
                    signature['value']['operands'][0]['enable_pin'] = True
                    signature['value']['operands'][-1]['enable_pin'] = True
                    binding = []
                else:
                    binding = [fact('binding', 'effects.bound_relation', {'operand_binding': {'execution_arguments': [
                        {'parameter': 'run', 'role': 'execution_condition'}, {'parameter': 'ok', 'role': 'execution_status'}]}})]
                off = fact('off', 'execution.disabled', {'when_disabled': 'undefined_do_not_use', 'outputs': ['first'], 'disabled_ENO': False})
                self.assertEqual(self.states([signature, gate, *binding]), ('unresolved', 'unresolved'))
                self.assertEqual(self.states([signature, gate, *binding, off]), ('answered', 'answered'))

    def test_existing_FX_data_type_and_output_variable_spellings_require_real_coverage(self):
        from knowledge.structured_facts import _disabled_call_output_coverage
        signature, gate, fact = self.facts()
        for pin in signature['value']['operands']:
            if pin['type'] == 'BOOL':
                pin.pop('type');pin['data_type'] = {'kind': 'bool'}
            elif pin['direction'] == 'output':pin['direction'] = 'output_variable'
        base = [signature, gate]
        self.assertIs(_disabled_call_output_coverage(base, opcode='SYNTH_ENABLE', model='Q03UDV', language='fbd'), False)
        self.assertEqual(self.states(base), ('unresolved', 'unresolved'))
        off = fact('off', 'execution.disabled', {'when_disabled': 'undefined_do_not_use', 'outputs': ['first'], 'disabled_ENO': False})
        complete = [*base, off]
        self.assertIs(_disabled_call_output_coverage(complete, opcode='SYNTH_ENABLE', model='Q03UDV', language='fbd'), True)
        self.assertEqual(self.states(complete), ('answered', 'answered'))


class BoundComparisonInventoryTests(unittest.TestCase):
    def comparison(self, pair=False):
        names = ['s1', 's2'] if pair else ['s1', 's2', 's3']
        inputs = [{'parameter': name, 'type': 'ANY_SIMPLE', **({'optional': True} if i > 1 else {})}
                  for i, name in enumerate(names)]
        return {'id': 'assembly:relation:comparison', 'dimension': 'effects.bound_relation',
            'value': {'operation': 'compare_values', 'comparison': 'ne' if pair else 'gt',
                'aggregation': 'pair' if pair else 'all_adjacent_pairs', 'inputs': names,
                'input_count': {'minimum': 2, 'maximum': len(names)}, 'result': 'd', 'output': 'd',
                'operand_binding': {'inputs': inputs, 'output': {'parameter': 'd', 'type': 'BOOL'}}},
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [{'manual_id': 'synthetic', 'pdf_pages': [1]}],
            'scope': {'models': ['Q03UDV'], 'forms': ['SYNTH_COMPARE'], 'languages': ['fbd']},
            'conditions': [], 'depends_on': ['signature']}

    def test_complete_bound_comparison_chain_and_pair_are_concrete(self):
        from tools.inventory_instruction_semantics import _concrete_relation
        for pair in [False, True]:
            for comparison in ['eq', 'ne', 'gt', 'ge', 'lt', 'le']:
                with self.subTest(pair=pair, comparison=comparison):
                    fact = self.comparison(pair)
                    fact['value']['comparison'] = comparison
                    before = copy.deepcopy(fact)
                    self.assertTrue(_concrete_relation(fact))
                    self.assertEqual(fact, before)

    def test_incomplete_or_unbound_comparison_labels_are_not_concrete(self):
        from tools.inventory_instruction_semantics import _concrete_relation
        for name, mutate in [
            ('unassembled', lambda f: f.update(id='authored:comparison')),
            ('bare', lambda f: f.update(value={'operation': 'compare_values'})),
            ('no-binding', lambda f: f['value'].pop('operand_binding')),
            ('unknown-comparison', lambda f: f['value'].update(comparison='unknown')),
            ('malformed-comparison', lambda f: f['value'].update(comparison={})),
            ('no-aggregation', lambda f: f['value'].pop('aggregation')),
            ('bad-aggregation', lambda f: f['value'].update(aggregation='any_pair')),
            ('wrong-binding', lambda f: f['value']['operand_binding']['inputs'][0].update(parameter='other')),
            ('duplicate-input', lambda f: f['value']['inputs'].__setitem__(1, 's1')),
            ('no-input-type', lambda f: f['value']['operand_binding']['inputs'][0].pop('type')),
            ('wrong-output', lambda f: f['value']['operand_binding']['output'].update(parameter='other')),
            ('non-BOOL-result', lambda f: f['value']['operand_binding']['output'].update(type='INT')),
            ('no-count', lambda f: f['value'].pop('input_count')),
            ('malformed-count', lambda f: f['value'].update(input_count=[])),
            ('count-mismatch', lambda f: f['value']['input_count'].update(maximum=28)),
            ('required-count-mismatch', lambda f: f['value']['input_count'].update(minimum=3)),
            ('pair-has-three-inputs', lambda f: f['value'].update(aggregation='pair')),
            ('partial', lambda f: f['value'].update(full_source_relation_complete=False)),
            ('unresolved', lambda f: f['value'].update(unresolved_items=['aliasing']))]:
            with self.subTest(case=name):
                fact = self.comparison();mutate(fact)
                self.assertFalse(_concrete_relation(fact))

    def test_comparison_content_does_not_override_dependency_or_source_closure(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner
        effect = self.comparison(pair=True)
        signature = {**copy.deepcopy(effect), 'id': 'signature', 'dimension': 'parameters.typed_operands',
            'depends_on': [], 'value': {'operands': [{'name': 's1', 'type': 'ANY_SIMPLE', 'direction': 'input'},
                {'name': 's2', 'type': 'ANY_SIMPLE', 'direction': 'input'}, {'name': 'd', 'type': 'BOOL', 'direction': 'output'}]}}
        for case in ['complete', 'missing_dependency', 'conflict', 'partial']:
            with self.subTest(case=case):
                facts = [copy.deepcopy(signature), copy.deepcopy(effect)]
                if case == 'missing_dependency':facts[1]['depends_on'] = ['absent']
                if case == 'conflict':facts[1]['status'] = 'conflict'
                if case == 'partial':facts[1]['value']['full_source_relation_complete'] = False
                rows, _ = inspect_owner({'opcode': 'SYNTH_COMPARE', 'target_model': 'Q03UDV', 'facts': facts},
                                        InstructionRegistry(), composed_facts=facts)
                self.assertEqual(rows[0]['states']['effects'] == 'answered', case == 'complete')


class InstructionSemanticsInventoryTests(unittest.TestCase):
    def test_fixed_device_reference_steps_preserve_partial_content_and_loop_requirements(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import _typed_reference_step, _concrete_relation, inspect_owner
        integer = {'kind': 'int', 'bits': 16, 'signed': False}
        outputs = [{'target': {'kind': 'word', 'parameter': 'D', 'offset': i},
                    'expression': {'op': 'device', 'name': device, 'type': integer}}
                   for i, device in enumerate(['D8018', 'D8017', 'D8016', 'D8015', 'D8014', 'D8013', 'D8019'])]
        complete = {'operation': 'read_clock_register_fields', 'output': 'D',
                    'source_devices': [output['expression']['name'] for output in outputs],
                    'reference_step': {'behavior': 'expression_write', 'outputs': outputs}}
        loop = copy.deepcopy(complete)
        loop.update(state_bindings={'sample': {'source_device': 'D8018', 'type': integer}},
                    iteration={'count_expression': {'op': 'constant', 'value': 1, 'type': integer}})
        loop['reference_step']['outputs'][0]['expression'] = {'op': 'state', 'name': 'sample', 'type': integer}
        cases = [('complete', complete, True), ('existing-loop', loop, True)]
        for name, mutate in [
                ('unknown-state', lambda v: v['reference_step']['outputs'][0].update(
                    expression={'op': 'state', 'name': 'unbound', 'type': integer})),
                ('bad-target', lambda v: v['reference_step']['outputs'][0].update(target={'kind': 'device'})),
                ('empty-outputs', lambda v: v['reference_step'].update(outputs=[])),
                ('bad-expression', lambda v: v['reference_step']['outputs'][0].update(expression={'op': 'unrecognized'})),
                ('empty-bindings', lambda v: v.update(state_bindings={})),
                ('iteration-only', lambda v: v.update(iteration=loop['iteration'])),
                ('bindings-only', lambda v: v.update(state_bindings=loop['state_bindings'])),
                ('loop-unknown-state', lambda v: v.update(iteration=loop['iteration'], state_bindings={
                    'sample': {'derived_value': {'op': 'state', 'name': 'unbound', 'type': integer}}})),
        ]:
            value = copy.deepcopy(complete)
            mutate(value)
            cases.append((name, value, False))
        for name, value, expected in cases:
            with self.subTest(case=name):
                original = copy.deepcopy(value)
                self.assertEqual(_typed_reference_step(value), expected)
                self.assertEqual(value, original)
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value, 'status': 'source_verified',
                    'verification': 'source_checked', 'sources': [{'manual_id': 'synthetic', 'pdf_pages': [1]}],
                    'scope': {'models': ['FX3U'], 'forms': ['SYNTH_CLOCK'], 'languages': ['ladder']},
                    'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]}
        signature = fact('signature', 'parameters.typed_operands', {'representation': 'native_ladder_instruction',
            'operands': [{'name': 'D', 'type': 'ANY16', 'direction': 'output'}], 'operand_count': 1})
        for partial, expected in [(False, 'answered'), (True, 'unresolved')]:
            with self.subTest(partial=partial):
                value = copy.deepcopy(complete)
                if partial:
                    value.update(full_source_relation_complete=False, unresolved_items=['destination_aliases_not_reviewed'])
                effect = fact('clock-result', 'effects.documented_processing', value)
                self.assertTrue(_typed_reference_step(value))
                self.assertEqual(_concrete_relation(effect), not partial)
                facts = [signature, effect]
                rows, _ = inspect_owner({'vendor': 'mitsubishi', 'opcode': 'SYNTH_CLOCK', 'target_model': 'FX3U',
                                         'facts': facts}, InstructionRegistry(), composed_facts=facts)
                row = next(row for row in rows if row['interface'] == 'native_ladder_instruction')
                self.assertEqual(row['states']['effects'], expected)

    def test_partial_scan_duration_cannot_replace_complete_execution_trigger(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner, _execution_value_known, _known
        registry = InstructionRegistry()
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value,
                'scope': {'models': ['Q03UDV'], 'forms': ['SYNTH_PULSE'], 'languages': ['ladder']},
                'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
        signature = fact('signature', 'parameters.typed_operands', {
            'representation': 'native_ladder_instruction', 'declaration_kind': 'instruction',
            'operand_count': 1, 'operands': [{'name': 'D', 'type': 'BOOL', 'direction': 'output'}]})
        duration = {'full_source_relation_complete': False,
            'unresolved_items': ['same_callpoint_repeated_in_one_scan_history'],
            'pulse_duration': {'active_scan_count': 1, 'when': {
                'instances_for_same_destination_per_scan': 1, 'reached_in_successive_scans': True}},
            'same_callpoint_repeated_in_one_scan': {'history_update': 'not_determined'}}
        self.assertTrue(_known(duration))  # Complete-looking fields do not settle source coverage.
        trigger = {'trigger': 'execution_condition_rising_edge', 'executions_per_edge': 1,
                   'when_condition_false': 'not_executed'}
        for name, value, trigger_value, dependency, answered in [
                ('partial-duration-only', duration, None, None, False),
                ('partial-flag-only', {k: v for k, v in duration.items() if k != 'unresolved_items'},
                    None, None, False),
                ('unresolved-items-only', {k: v for k, v in duration.items()
                                          if k != 'full_source_relation_complete'}, None, None, False),
                ('partial-plus-complete-trigger', duration, trigger, None, True),
                ('partial-plus-unknown-edge-count', duration,
                    {**trigger, 'executions_per_edge': None}, None, False),
                ('partial-plus-missing-trigger-proof', duration, trigger, 'missing', False),
                ('partial-plus-unresolved-condition-proof', duration, trigger, 'unknown', False)]:
            with self.subTest(case=name):
                qualified = fact('duration', 'execution.same_scan_repeat', copy.deepcopy(value))
                facts = [copy.deepcopy(signature), qualified]
                if trigger_value is not None:
                    complete = fact('trigger', 'execution.trigger', copy.deepcopy(trigger_value))
                    if dependency:
                        complete['depends_on'] = [dependency]
                    facts.append(complete)
                if dependency == 'unknown':
                    proof = fact('unknown', 'constraints.cpu_availability', {'available': True})
                    proof['status'] = 'unknown'
                    proof['conditions'].append({'context': 'cpu_version_not_resolved'})
                    facts.append(proof)
                original = copy.deepcopy(facts)
                rows, _ = inspect_owner({'vendor': 'mitsubishi', 'opcode': 'SYNTH_PULSE',
                    'target_model': 'Q03UDV', 'facts': facts}, registry, composed_facts=facts)
                self.assertTrue(rows)
                self.assertTrue(all((row['states']['execution'] == 'answered') == answered for row in rows))
                self.assertFalse(_execution_value_known(qualified))
                self.assertEqual(facts, original)

    def test_effects_need_normal_results_not_error_rules_or_output_labels(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner
        from plc.instruction_effects import validate_behavior
        registry = InstructionRegistry()
        integer = {'kind': 'int', 'bits': 16, 'signed': False}
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value,
                'scope': {'models': ['Q03UDV'], 'forms': ['SYNTH_FILL'], 'languages': ['ladder']},
                'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
        signature = fact('signature', 'parameters.typed_operands', {
            'representation': 'native_ladder_instruction', 'declaration_kind': 'instruction',
            'operand_count': 2, 'operands': [
                {'name': 'S', 'type': 'ANY16', 'direction': 'input'},
                {'name': 'D', 'type': 'ANY16', 'direction': 'output'}]})
        output = {'target': {'kind': 'word', 'parameter': 'D', 'offset': 0},
                  'expression': {'op': 'parameter', 'name': 'S', 'type': integer}}
        complete = {'rules': [{'kind': 'expression_write', 'outputs': [output]}]}
        transitions = {'operation': 'state_update', 'inputs': ['S'], 'output': 'D',
            'state_accesses': [{'target': {'kind': 'state', 'parameter': 'D', 'offset': 0},
                                'access': 'write'}],
            'state_transitions': [{'condition': {'op': 'constant', 'type': {'kind': 'bool'},
                                                'value': True}, 'outputs': [output]}]}
        error_writes = {'rules': [{'kind': 'expression_write', 'outputs': [
            {'target': {'kind': 'bit', 'parameter': 'SM0', 'offset': 0},
             'expression': {'op': 'constant', 'type': {'kind': 'bool'}, 'value': True}},
            {'target': {'kind': 'word', 'parameter': 'SD0', 'offset': 0},
             'expression': {'op': 'constant', 'type': integer, 'value': 4101}}]}]}
        for name, dimension, value, status, dependency, answered in [
                ('error-only', 'effects.error_response', {'rules': [
                    {'kind': 'operation_error', 'error_code': 4101}]}, 'source_verified', None, False),
                ('error-with-typed-writes', 'effects.error_response', error_writes,
                    'source_verified', None, False),
                ('abstract-fill', 'effects.documented_processing', {'rules': [
                    {'kind': 'range_fill', 'source': 'S', 'destination': 'D', 'count_operand': 'n'}]},
                    'source_verified', None, False),
                ('output-label', 'effects.documented_processing', {'operation': 'range_fill',
                    'output': {'parameter': 'D'}}, 'source_verified', None, False),
                ('destination-label', 'effects.documented_processing', {'destination_operand': 'D'},
                    'source_verified', None, False),
                ('expression', 'effects.documented_processing', complete, 'source_verified', None, True),
                ('state-transitions', 'effects.documented_processing', transitions,
                    'source_verified', None, True),
                ('partial', 'effects.documented_processing', {**complete,
                    'full_source_relation_complete': False}, 'source_verified', None, False),
                ('partial-with-error', 'effects.documented_processing', {**complete,
                    'full_source_relation_complete': False}, 'source_verified', None, False),
                ('partial-with-error-and-unknown-dependency', 'effects.documented_processing', {**complete,
                    'full_source_relation_complete': False}, 'source_verified', 'unknown', False),
                ('unknown-result', 'effects.documented_processing', {**complete,
                    'unresolved_items': ['overlapping_source_sampling']}, 'source_verified', None, False),
                ('conflict', 'effects.documented_processing', complete, 'conflict', None, False),
                ('missing-dependency', 'effects.documented_processing', complete,
                    'source_verified', 'missing', False),
                ('unknown-dependency', 'effects.documented_processing', complete,
                    'source_verified', 'unknown', False)]:
            with self.subTest(case=name):
                effect = fact('result', dimension, copy.deepcopy(value))
                effect['status'] = status
                facts = [copy.deepcopy(signature), effect]
                if dependency:
                    effect['depends_on'] = [dependency]
                if dependency == 'unknown':
                    proof = fact('unknown', 'constraints.use_limits', {'required_state': 'unknown'})
                    proof['status'] = 'unknown'
                    facts.append(proof)
                if name.startswith('partial-with-error'):
                    facts.append(fact('error', 'effects.error_response', error_writes))
                if name == 'error-with-typed-writes':
                    validate_behavior({'behavior': 'expression_write',
                                       'outputs': error_writes['rules'][0]['outputs']})
                original = copy.deepcopy(facts)
                rows, _ = inspect_owner({'vendor': 'mitsubishi', 'opcode': 'SYNTH_FILL',
                    'target_model': 'Q03UDV', 'facts': facts}, registry, composed_facts=facts)
                row = next(row for row in rows if row['interface'] == 'native_ladder_instruction')
                self.assertEqual(row['states']['effects'] == 'answered', answered)
                if name.startswith('partial') or name == 'unknown-result':
                    self.assertNotEqual(row['states']['effects'], 'missing')
                self.assertEqual(facts, original)

    def test_continuous_trigger_optional_edge_count_preserves_unknowns_and_dependencies(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner, _known, _execution_value_known
        registry = InstructionRegistry()
        model, opcode = 'Q03UDV', 'SYNTH_CONVERT'
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value,
                'scope': {'models': [model], 'forms': [opcode], 'languages': ['st']},
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
        signature = fact('signature', 'parameters.typed_operands', {
            'representation': 'st_call', 'declaration_kind': 'instruction',
            'operand_count': 0, 'operands': []})
        level = {'trigger': 'each_evaluation_when_execution_condition_true',
                 'executions_per_edge': None, 'when_condition_false': 'not_executed'}
        self.assertFalse(_known(level))  # The generic predicate must stay strict.
        for name, value, dependency, answered in [
                ('level', level, False, True),
                ('scan-level', {**level, 'trigger': 'each_scan_when_execution_condition_true'}, False, True),
                ('edge-unknown-count', {**level, 'trigger': 'execution_condition_rising_edge'}, False, False),
                ('edge-known-count', {**level, 'trigger': 'execution_condition_rising_edge',
                                      'executions_per_edge': 1}, False, True),
                ('edge-explicit-unknown', {**level, 'trigger': 'execution_condition_rising_edge',
                                         'executions_per_edge': 'unknown'}, False, False),
                ('level-explicit-unknown', {**level, 'executions_per_edge': 'unknown'}, False, False),
                ('unknown-trigger', {**level, 'trigger': None}, False, False),
                ('other-unknown', {**level, 'when_condition_false': None}, False, False),
                ('missing-dependency', level, True, False)]:
            with self.subTest(case=name):
                trigger = fact('execution', 'execution.trigger', copy.deepcopy(value))
                self.assertEqual(_execution_value_known(trigger), answered or dependency)
                self.assertEqual(_execution_value_known({**trigger, 'dimension': 'execution.form'}),
                                 _known(value))
                if dependency:
                    trigger['depends_on'] = ['missing-source-proof']
                numeric_unknown = fact('numeric-unknown', 'execution.source_disagreement',
                    {'source_disagreement': {'subject': 'numeric_rounding', 'resolution': 'unknown'}})
                numeric_unknown['status'] = 'unknown'
                facts = [copy.deepcopy(signature), trigger, numeric_unknown]
                original = copy.deepcopy(facts)
                rows, _ = inspect_owner({'vendor': 'mitsubishi', 'opcode': opcode,
                    'target_model': model, 'facts': facts}, registry, composed_facts=facts)
                row = next(row for row in rows if row['interface'] == 'st_call')
                self.assertEqual(row['states']['execution'] == 'answered', answered)
                self.assertEqual(facts, original)

    def test_exclusive_version_bound_is_not_an_inclusive_threshold(self):
        from tools.inventory_instruction_semantics import _implies, _consistent, conditions_for_model
        exclusive = {'context': 'cpu_version', 'minimum_exclusive_by_model': {'FXU': '2.30'}}
        inclusive = {'context': 'cpu_version', 'minimum': '2.30'}
        higher = {'context': 'cpu_version', 'minimum': '2.31'}
        for branch, required, expected in [
                ([exclusive], [inclusive], True), ([inclusive], [exclusive], False),
                ([higher], [exclusive], True), ([exclusive], [higher], False)]:
            with self.subTest(branch=branch, required=required):
                self.assertIs(_implies(branch, required, 'FXU'), expected)
        self.assertEqual(conditions_for_model([exclusive], 'FXU'), [
            {'context': 'cpu_version', 'minimum_exclusive': '2.30'}])
        self.assertFalse(_consistent([exclusive, {'context': 'cpu_version', 'maximum_exclusive': '2.30'}], 'FXU'))
        self.assertTrue(_consistent([exclusive, {'context': 'cpu_version', 'maximum_exclusive': '2.31'}], 'FXU'))

    def test_verified_full_variant_table_resolves_only_covered_conflict_members(self):
        from tools.inventory_instruction_semantics import duplicate_variant_conflict_ids
        def variant(identity, names, status):
            return {'id': identity, 'dimension': 'parameters.variant_family', 'status': status,
                    'value': {'section_variants': names, 'section_variant_count': len(names)}}
        for complete, subset, extra, expected in [
                (['OP', 'DOP'], ['OP'], [], {'whole', 'subset', 'class'}),
                (['OP', 'OPP'], ['OP'], [], {'whole', 'subset', 'class'}),
                (['OP', 'DOP'], ['OTHER'], [], {'whole'}),
                (['OP', 'DOP'], ['OP'], [{'id': 'real-order', 'dimension': 'parameters.order',
                    'status': 'conflict', 'value': {'symbols': ['D', 'S']}}],
                 {'whole', 'subset', 'class'})]:
            with self.subTest(complete=complete, subset=subset, extra=extra):
                facts = [variant('reviewed', complete, 'source_verified'),
                    variant('whole', complete, 'conflict'), variant('subset', subset, 'conflict'),
                    {'id': 'instruction_class_review:class', 'dimension': 'identity.instruction_class',
                     'status': 'conflict'}, *extra]
                actual = duplicate_variant_conflict_ids(facts)
                expected = {'instruction_class_review:class' if name == 'class' else name for name in expected}
                self.assertEqual(actual, expected)
                self.assertNotIn('real-order', actual)

    def test_real_content_conditions_dependencies_and_family_members_remain_independent(self):
        from plc.instructions import InstructionRegistry
        from tools.inventory_instruction_semantics import inspect_owner, aggregate_units
        registry = InstructionRegistry()
        model, opcode = 'Q03UDV', 'SYNTH_COPY'
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        version = {'context': 'cpu_version', 'minimum': '2.20'}
        def fact(identity, dimension, value, *, conditions=None):
            return {'id': identity, 'dimension': dimension, 'value': value,
                'scope': {'models': [model], 'forms': [opcode], 'languages': ['ladder']},
                'conditions': copy.deepcopy([native, version] if conditions is None else conditions),
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [7]}]}
        signature = fact('signature', 'parameters.typed_operands', {
            'declaration_kind': 'instruction', 'representation': 'native_ladder_instruction',
            'operand_count': 2, 'operands': [
                {'name': 'S', 'type': 'ANY16', 'direction': 'input', 'purpose': 'value_to_copy'},
                {'name': 'D', 'type': 'ANY16', 'direction': 'output', 'purpose': 'copied_value'}]})
        effect = fact('effect', 'effects.documented_processing', {'rules': [{'kind': 'expression_write',
            'outputs': [{'target': {'kind': 'word', 'parameter': 'D', 'offset': 0}, 'expression': {
                'op': 'parameter', 'name': 'S', 'type': {'kind': 'int', 'bits': 16, 'signed': False}}}]}]})
        facts = [fact('cpu', 'constraints.cpu_availability', {'available': True}, conditions=[version]),
            fact('old-cpu', 'constraints.cpu_availability', {'available': False}, conditions=[
                {'context': 'cpu_version', 'maximum_exclusive': '2.20'}]), signature,
            fact('arity', 'parameters.arity', {'minimum': 2, 'maximum': 2}), effect,
            fact('execution', 'execution.trigger', {'trigger': 'each_scan_when_execution_condition_true'}),
            fact('hold', 'execution.disabled_state', {'when_not_executed': 'destination_unchanged'}),
            fact('limits', 'constraints.use_limits', {'items': ['Power-loss behavior is outside this source section.']})]
        entry = {'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model,
                 'call_available': False, 'gaps': ['effects_not_formalized'],
                 'semantic_completeness': 'not_established', 'facts': facts}
        originals = copy.deepcopy(entry)
        rows, native_evidence = inspect_owner(entry, registry, composed_facts=facts)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['remaining'])
        self.assertEqual(rows[0]['conditions'], [version, native])
        self.assertTrue(rows[0]['limitations'])
        self.assertIs(native_evidence['call_available'], False)
        self.assertTrue(native_evidence['native']['source_verified'])
        self.assertEqual(entry, originals)
        candidate = fact('chart-hold', 'execution.disabled_retention', {
            'action': 'retain', 'outputs': [{'kind': 'bit', 'offset': 0, 'parameter': 'D'}]})
        candidate.update(status='candidate_evidence', verification='not_performed')
        with_candidate = [*copy.deepcopy(facts), candidate]
        candidate_rows, _ = inspect_owner(entry, registry, composed_facts=with_candidate)
        self.assertFalse(candidate_rows[0]['remaining'])
        duplicate = copy.deepcopy(signature)
        duplicate['id'] = 'signature-old'
        duplicate['conditions'] = [native, {'basis': 'instruction_header_icon',
            'context': 'cpu_version_not_resolved'}, version]
        deduped, _ = inspect_owner(entry, registry, composed_facts=[*copy.deepcopy(facts), duplicate])
        self.assertEqual(len(deduped), 1)
        self.assertFalse(deduped[0]['remaining'])
        bare = copy.deepcopy(facts)
        bare[4]['value'] = {'operation': 'copy', 'inputs': ['S'], 'output': 'D',
                            'covered_aspects': ['parameters', 'execution', 'effects']}
        missing, _ = inspect_owner(entry, registry, composed_facts=bare)
        self.assertIn('effects', [item['facet'] for item in missing[0]['remaining']])
        partial = copy.deepcopy(facts)
        partial[2]['value']['operands'][0].update(type={'kind': 'integer'}, type_completeness='partial',
                                                word_device_layout={'word_count': 1})
        unresolved, native_partial = inspect_owner(entry, registry, composed_facts=partial)
        self.assertIn('types', [item['facet'] for item in unresolved[0]['remaining']])
        self.assertTrue(native_partial['native']['partial'])
        broken = copy.deepcopy(facts)
        broken[4]['depends_on'] = ['missing-source']
        broken_rows, _ = inspect_owner(entry, registry, composed_facts=broken)
        self.assertIn('effects', [item['facet'] for item in broken_rows[0]['remaining']])
        conflict = copy.deepcopy(facts); conflict[4]['status'] = 'conflict'
        conflict_rows, _ = inspect_owner(entry, registry, composed_facts=conflict)
        self.assertTrue(any(item['status'] == 'source_conflict_pending' for item in conflict_rows[0]['remaining']))
        first = copy.deepcopy(missing[0]); first['base_opcode'] = 'FAMILY'; first['opcode'] = 'FAMILY'
        second = copy.deepcopy(first); second['opcode'] = 'DFAMILYP'
        units = aggregate_units([first, second])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['forms'], ['DFAMILYP', 'FAMILY'])
        fixed = copy.deepcopy(first); fixed['remaining'] = []
        self.assertEqual(aggregate_units([fixed])[0]['id'], units[0]['id'])
        state = copy.deepcopy(facts)
        state[4]['value'] = {'operation': 'reset_selected_alarm', 'inputs': [],
            'state_accesses': [{'target': {'kind': 'state', 'parameter': 'alarms'}, 'access': 'read_write'}],
            'state_transitions': {'selection': 'lowest_active_alarm', 'new_value': False}}
        from tools.inventory_instruction_semantics import _concrete_relation
        self.assertTrue(_concrete_relation(state[4]))
        for value in [facts[4]['value'], state[4]['value'],
                      {'operation': 'copy', 'output': {'parameter': 'D'}},
                      {'operation': 'copy', 'destination_operand': 'D'}]:
            for marker in [{'full_source_relation_complete': False},
                           {'unresolved_items': ['valid_input_branch_not_defined']}]:
                with self.subTest(effect=value, incomplete=marker):
                    bounded = copy.deepcopy(facts)
                    bounded[4]['value'] = {**copy.deepcopy(value), **marker}
                    self.assertFalse(_concrete_relation(bounded[4]))
                    bounded_rows, _ = inspect_owner(entry, registry, composed_facts=bounded)
                    self.assertIn('effects', [item['facet'] for item in bounded_rows[0]['remaining']])
                    self.assertEqual(bounded[4]['value'], {**value, **marker})


    def test_block_ports_from_another_width_are_not_a_conflict(self):
        from tools.inventory_instruction_semantics import superseded_connection_conflict_ids
        def ports(identity, symbols):
            return {'id': identity, 'dimension': 'parameters.connection_role',
                    'status': 'conflict', 'value': {'symbols': symbols}}
        facts = [{'id': 'signature', 'dimension': 'parameters.typed_operands',
                  'status': 'source_verified', 'value': {'operands': [{}] * 5}},
                 ports('five', ['EN', 'ENO', 'd1', 's1', 's2']),
                 ports('six', ['EN', 'ENO', 'd', 'd1', 's1', 's2'])]
        self.assertEqual(superseded_connection_conflict_ids(facts), {'five', 'six'})
        ambiguous = copy.deepcopy(facts)
        ambiguous[2]['value']['symbols'] = ['EN', 'ENO', 'd2', 'd3', 'd4']
        self.assertEqual(superseded_connection_conflict_ids(ambiguous), set())


    def test_repeated_section_variant_is_not_a_source_conflict(self):
        from tools.inventory_instruction_semantics import duplicate_variant_conflict_ids
        def variant(identity, forms, status='conflict'):
            return {'id': identity, 'dimension': 'parameters.variant_family',
                    'status': status, 'value': {'section_variants': forms}}
        facts = [variant('family:ALT:forms', ['ALT', 'ALTP'], 'source_verified'),
                 variant('variant', ['ALT', 'ALTP']),
                 variant('variant:2', ['ALT']),
                 {'id': 'instruction_class_review:variant', 'dimension': 'identity.instruction_class',
                  'status': 'conflict', 'value': {'instruction_class': 'applied'}}]
        self.assertEqual(duplicate_variant_conflict_ids(facts),
                         {'variant', 'variant:2', 'instruction_class_review:variant'})
        genuine = copy.deepcopy(facts)
        genuine[2]['value'] = {'section_variants': ['OTHER']}
        self.assertEqual(duplicate_variant_conflict_ids(genuine), {'variant'})
        declared = copy.deepcopy(facts)
        declared[0]['status'] = 'declared_unverified'
        self.assertEqual(duplicate_variant_conflict_ids(declared), duplicate_variant_conflict_ids(facts))


    def test_digit_transfer_relation_establishes_its_word_output(self):
        from knowledge.instruction_presentation import substantive_effect
        from knowledge.structured_facts import _output_layout_complete
        value = {'knowledge_kind': 'manual_behavior', 'operation': 'digit_transfer',
                 'source_operand': 'S', 'destination_operand': 'D', 'count_operand': 'm2'}
        self.assertTrue(substantive_effect({'dimension': 'effects.documented_processing', 'value': value}))
        self.assertTrue(_output_layout_complete([
            {'dimension': 'parameters.typed_operands', 'value': {'operands': [
                {'name': 'D', 'direction': 'output',
                 'word_device_layout': {'word_count': 1}}]}},
            {'dimension': 'effects.documented_processing', 'value': value}]))

    def test_pulse_train_relation_establishes_its_bit_output(self):
        from knowledge.instruction_presentation import substantive_effect
        from knowledge.structured_facts import _output_layout_complete
        value = {'knowledge_kind': 'manual_behavior', 'operation': 'pulse_train',
                 'frequency_operand': 'S1', 'count_operand': 'S2',
                 'output': {'parameter': 'D', 'kind': 'bit', 'offset': 0}}
        self.assertTrue(substantive_effect({'dimension': 'effects.documented_processing', 'value': value}))
        self.assertTrue(_output_layout_complete([
            {'dimension': 'parameters.typed_operands', 'value': {'operands': [
                {'name': 'D', 'direction': 'output', 'occupied_bit_count': 1}]}},
            {'dimension': 'effects.documented_processing', 'value': value}]))


    def test_explicit_type_alternatives_require_every_actual_candidate(self):
        from tools.inventory_instruction_semantics import _type_present
        for alternatives, expected in [(['ANY16', 'ANY32'], True), ([], False),
                (['ANY16', 'unknown'], False), (['ANY16', 'ANY'], False)]:
            for interface in ('fbd_call', 'native_ladder_instruction'):
                for array in (False, True):
                    with self.subTest(alternatives=alternatives, interface=interface, array=array):
                        parameter = ({'type': 'ARRAY', 'array': {'length': 2},
                            'element_type': 'ANY16_OR_ANY32', 'element_type_alternatives': alternatives}
                            if array else {'type': 'ANY16_OR_ANY32', 'type_alternatives': alternatives})
                        self.assertEqual(_type_present(parameter, interface), expected)
        self.assertFalse(_type_present({'type': 'ANY16_OR_ANY32'}, 'fbd_call'))
        self.assertFalse(_type_present({'type': 'ARRAY', 'array': {'length': 2},
            'element_type': 'ANY16_OR_ANY32'}, 'fbd_call'))
        self.assertFalse(_type_present({'type': 'ANY16', 'type_alternatives': ['ANY16'],
            'type_completeness': 'partial'}, 'fbd_call'))

    def test_explicit_array_union_preserves_every_declared_storage_choice(self):
        from tools.inventory_instruction_semantics import _type_present, _layout_present
        alternatives = [{'type': 'ARRAY', 'array': {'length': 4}, 'element_type': 'ANY16'},
                        {'type': 'ARRAY', 'array': {'length': 2}, 'element_type': 'ANY32'}]
        for interface in ['fbd_call', 'structured_ladder_block', 'native_ladder_instruction']:
            for choices, expected in [(alternatives, True), ([], False),
                    ([alternatives[0], {'type': 'ARRAY', 'array': {'length': 2}, 'element_type': 'unknown'}], False),
                    ([alternatives[0], {'type': 'ARRAY', 'element_type': 'ANY32'}], False),
                    ([alternatives[0], 'ANY32'], False)]:
                with self.subTest(interface=interface, choices=choices):
                    parameter = {'type': 'UNION', 'alternatives': copy.deepcopy(choices)}
                    self.assertIs(_type_present(parameter, interface), expected)
                    self.assertIs(_layout_present(parameter), expected)
        self.assertFalse(_type_present({'type': 'UNION', 'alternatives': alternatives,
                                       'type_completeness': 'partial'}, 'fbd_call'))

    def test_literal_operator_and_instruction_block_are_separate_declarations(self):
        from knowledge.structured_facts import _aspect_state
        def signature(identity, available, representation, reason=''):
            return {'id': identity, 'dimension': 'parameters.fbd_signature',
                'status': 'source_verified', 'verification': 'source_checked',
                'scope': {'models': ['Q03UDV'], 'forms': ['*'], 'languages': ['fbd']},
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}],
                'value': {'available': available, 'representation': representation, 'reason': reason}}
        positive = signature('instruction', True, 'fbd_instruction_block')
        negative = signature('literal', False, 'literal_operator', 'literal_operator_has_no_FBD_block')
        for facts, expected in [([positive, negative], 'answered'),
                ([positive, signature('same-block', False, 'fbd_instruction_block')], 'unresolved'),
                ([negative], 'not_applicable')]:
            with self.subTest(expected=expected):
                original = copy.deepcopy(facts)
                state = _aspect_state({'kind': 'instruction', 'target': '*', 'value': {'facts': facts}},
                    'fbd_call', model='Q03UDV', language='fbd', query='')
                self.assertEqual(state, expected)
                self.assertEqual(facts, original)

    def test_real_ql_arithmetic_fbd_signatures_keep_source_dependencies_and_types(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from tools.inventory_instruction_semantics import inspect_owner
        for base in ('*', '+', '-', '/'):
            for form in (base, base + 'P', 'D' + base, 'D' + base + 'P'):
                with self.subTest(form=form):
                    entry = registry._definition_owner_store.get_entry(form, cpu='Q03UDV')
                    rows, _ = inspect_owner(entry, registry)
                    case, = [row for row in rows if row['interface'] == 'fbd_call']
                    self.assertTrue(all(case['states'][facet] == 'answered'
                        for facet in ('arity', 'order', 'roles', 'types', 'purpose', 'storage')), case)


if __name__ == '__main__':
    unittest.main()

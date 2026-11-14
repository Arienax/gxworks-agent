# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Public synthetic regression fixtures; no manual text or private evidence."""
import copy
import json
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from knowledge.fact_selection import project_packet, project_instruction_records
from knowledge.structured_facts import authored_packet, fact_record_coverage
from knowledge.instruction_facts import retrieve_instruction_facts
from knowledge.retriever import build_knowledge_context, retrieve_fact_aware_knowledge
from shared.model_context import technical_fact_packet


def fact(identity, dimension, **extra):
    return dict(dict(id=identity, dimension=dimension, value={'synthetic': True},
                     status='source_verified', sources=[{'source_id': 'synthetic'}],
                     verification='source_checked'), **extra)


def packet(facts, model='FX5U'):
    return dict(kind='instruction', target='PWM', scope={'models': [model]},
                content_origin='authored_fact', status='source_verified',
                value={'opcode': 'PWM', 'target_model': model, 'facts': facts})


def row(p):
    return dict(id='synthetic-pwm', text=json.dumps(p), content_origin='authored_fact',
                fact_kind='instruction', fact_target='PWM', source='synthetic',
                source_lane='fact', plc_models=['FX5U'], chunk_type='instruction',
                distribution_status='fact_representation_reviewed')


class InterfaceAlternativeProjectionTests(unittest.TestCase):
    def condition(self):
        return {'any_of': [
            {'context': '$LANGUAGE', 'equals': 'st'},
            {'context': '$LANGUAGE', 'equals': 'fbd'},
            {'context': 'project_mode', 'equals': 'structured_ladder_block'}]}

    def test_interface_alternatives_filter_native_without_losing_unknown_context(self):
        source = packet([
            fact('native', 'effects.operation', scope={'languages': ['ladder']},
                 conditions=[{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]),
            fact('structured', 'effects.operation', conditions=[self.condition()])])
        before = copy.deepcopy(source)
        for language, mode, expected in [
            ('ladder', 'native_ladder_instruction', {'native'}),
            ('ladder', 'structured_ladder_block', {'structured'}),
            ('st', None, {'structured'}), ('fbd', None, {'structured'}),
            ('ladder', None, {'native', 'structured'})]:
            with self.subTest(language=language, mode=mode):
                result = project_packet(source, plc_model='FX5U', language=language,
                                        project_mode=mode, aspects=['effects'])
                self.assertEqual({f['id'] for f in result['value']['facts']}, expected)
        self.assertEqual(source, before)

    def test_any_true_branch_resolves_unknown_but_all_false_reject(self):
        from plc.instruction_constraints import _condition
        from plc.instruction_definition import DefinitionError, UnknownInstructionSemantics
        unknown = {'context': 'missing', 'equals': True}
        match = {'context': '$LANGUAGE', 'equals': 'st'}
        self.assertTrue(_condition({'any_of': [unknown, match]}, {}, {'$LANGUAGE': 'st'}))
        self.assertFalse(_condition({'any_of': [match]}, {}, {'$LANGUAGE': 'ladder'}))
        with self.assertRaises(UnknownInstructionSemantics):
            _condition({'any_of': [unknown, match]}, {}, {'$LANGUAGE': 'ladder'})
        for malformed in ([], 'st'):
            with self.subTest(malformed=malformed), self.assertRaises(DefinitionError):
                _condition({'any_of': malformed}, {}, {})


class SemanticScopeProjectionTests(unittest.TestCase):
    def test_semantic_scope_string_is_preserved_beside_structural_scope(self):
        from knowledge.instruction_presentation import model_instruction_view
        source = {'kind': 'instruction', 'target': 'SYNTH', 'scope': {'models': ['FX3U']},
            'context': {'language': 'st'}, 'value': {'family': 'SYNTH',
                'forms': [{'opcode': 'SYNTH'}], 'parameter_tables': [],
                'additional_knowledge': [{'scope': {'models': ['FX3U'], 'languages': ['st']},
                    'semantics': {'execution.call_enable': {
                        'scope': 'call_condition_only', 'enable_gate': 'none'}}}]}}
        before = copy.deepcopy(source)
        result = model_instruction_view(source)
        claim = result['value']['additional_knowledge'][0]['semantics']['execution.call_enable']
        self.assertEqual(claim['scope'], 'call_condition_only')
        self.assertEqual(claim['enable_gate'], 'none')
        self.assertEqual(source, before)


class TaskProjectionTests(unittest.TestCase):
    def test_generation_keeps_operand_purposes_and_separate_width(self):
        from knowledge.structured_facts import generation_aspects
        source = packet([
            fact('order', 'parameters.source_order', value={'symbols': ['LOW', 'HIGH', 'VALUE', 'OUT']}),
            fact('width', 'parameters.source_width', value={'bits': 32}),
            *[fact('purpose-%s' % index, 'parameters.%s.purpose' % index,
                   value={'position': index, 'symbol': symbol, 'value': {'semantic_role': role}})
              for index, symbol, role in [(1, 'LOW', 'lower_bound'), (2, 'HIGH', 'upper_bound'),
                                          (3, 'VALUE', 'comparison_value'), (4, 'OUT', 'head_bit')]],
        ])
        for language in ('ladder', 'st', 'fbd'):
            with self.subTest(language=language):
                result = project_packet(source, plc_model='FX5U', language=language,
                                        aspects=generation_aspects('Use PWM', language=language))
                selected = {item['id'] for item in result['value']['facts']}
                self.assertTrue({'width', 'purpose-1', 'purpose-2', 'purpose-3', 'purpose-4'} <= selected)
        self.assertEqual(len(source['value']['facts']), 6)

    def test_generation_keeps_all_programming_dimensions_and_parameters(self):
        from knowledge.structured_facts import generation_aspects
        source = packet([
            fact('order', 'parameters.order', value={'symbols': ['S1', 'S2']}),
            fact('r1', 'parameters.1.range'), fact('r2', 'parameters.2.range'),
            fact('units', 'parameters.2.unit'), fact('encoding', 'parameters.1.encoding'),
            fact('memory', 'memory.layout'), fact('hardware', 'resources.hardware_dependency'),
            fact('execution', 'execution.trigger'), fact('condition', 'constraints.numeric_ranges'),
        ])
        selected = project_packet(source, plc_model='FX5U', language='ladder', query='PWM S1',
                                  aspects=generation_aspects('PWM S1', language='ladder'))
        self.assertEqual({f['id'] for f in selected['value']['facts']}, {f['id'] for f in source['value']['facts']})
        narrow = project_packet(source, plc_model='FX5U', language='ladder', query='PWM S1', aspects=['operand_range'])
        self.assertNotIn('r2', {f['id'] for f in narrow['value']['facts']})

    def test_narrow_roots_full_closure(self):
        source = packet([fact('root', 'parameters.1.range', depends_on=['dep']),
            fact('dep', 'execution.enable', conditions=[{'context': 'enabled', 'equals': True}]),
            fact('noise', 'effects.unrelated')])
        result = project_packet(source, plc_model='FX5U', aspects=['operand_range'])
        self.assertEqual([x['id'] for x in result['value']['facts']], ['root', 'dep'])
        self.assertEqual(result['value']['facts'][1]['selection_applicability'], 'unverified')
        self.assertTrue(result['fact_selection']['gaps'])
        self.assertEqual(len(source['value']['facts']), 3)
        self.assertEqual(result['value']['facts'][1]['conditions'], source['value']['facts'][1]['conditions'])

    def test_language_mismatch_dependency_not_admitted(self):
        p = packet([fact('root', 'effects.result', depends_on=['ld']),
                    fact('ld', 'parameters.order', scope={'languages': ['ladder']})])
        r = project_packet(p, plc_model='FX5U', language='st', aspects=['effects'])
        self.assertEqual(r['value']['facts'][1]['selection_applicability'], 'not_applicable')
        self.assertFalse(fact_record_coverage(row(r)))
        self.assertEqual(r['value']['facts'][0]['depends_on'], ['ld'])

    def test_known_conditions_and_project_mode(self):
        p = packet([fact('root', 'effects.result', conditions=[{'context': 'project_mode', 'equals': 'structured'}])])
        for mode, count in [('structured', 1), ('simple', 0), (None, 1)]:
            r = project_packet(p, plc_model='FX5U', aspects=['effects'], project_mode=mode)
            self.assertEqual(len(r['value']['facts']), count)
            if mode != 'structured': self.assertTrue(r['fact_selection']['gaps'])

    def test_empty_cpu_and_form_mismatch_are_gaps(self):
        for p in [packet([], 'FX5U'), packet([fact('a','effects.result')], 'FX3U'),
                  packet([fact('a', 'effects.result', scope={'forms': ['DPWM']})])]:
            r = project_packet(p, plc_model='FX5U', aspects=['effects'])
            self.assertFalse(r['value']['facts'])
            self.assertTrue(r['fact_selection']['gaps'])
            self.assertFalse(fact_record_coverage(row(r)))

    def test_common_aspects_and_exact_dimension(self):
        p = packet([fact('p','parameters.1.range'), fact('c','constraints.numeric_ranges'),
                    fact('x','execution.scan'), fact('e','effects.write')])
        for aspects, ids in [(['parameters'], ['p']), (['constraints'], ['c']),
            (['execution'], ['x']), (['effects'], ['e']), (['parameters.1.range'], ['p'])]:
            self.assertEqual([f['id'] for f in project_packet(p, plc_model='FX5U', aspects=aspects)['value']['facts']], ids)

    def test_generation_packer_and_query_aliases(self):
        p = packet([fact('p','parameters.1.range'), fact('e','effects.write')])
        for query in ['PWM 参数范围', 'PWM parameter range']:
            with patch('knowledge.structured_facts._runtime', return_value=(None,None,None,None,{})), \
                 patch('knowledge.fact_corpus.resolve', return_value=[row(p)]), \
                 patch('knowledge.structured_facts._adjudicated_rows', side_effect=lambda rows,*a:(rows,True)):
                results, report = retrieve_instruction_facts(query, plc_model='FX5U', task_type='generate',
                    targets=[{'opcode':'PWM','base_opcode':'PWM'}], language='st', aspects=['operand_range'], token_budget=1)
            self.assertEqual([f['id'] for f in authored_packet(results[0])['value']['facts']], ['p'])
            self.assertEqual(len(report['records']), 1)

    def test_mandatory_identity_does_not_mask_requested_gap(self):
        r = project_packet(packet([fact('identity','identity.name'),
            fact('cap','execution.core_execution_capability')]), plc_model='FX5U', aspects=['operand_range'])
        self.assertEqual(len(r['value']['facts']), 2)
        self.assertIn('requested_aspect_not_selected', [g['reason'] for g in r['fact_selection']['gaps']])
        self.assertFalse(fact_record_coverage(row(r)))

    def test_unknown_scope_field_cannot_claim_complete(self):
        r = project_packet(packet([fact('x','effects.result', scope={'project_modes':['structured']})]),
                           plc_model='FX5U', aspects=['effects'], project_mode='simple')
        self.assertTrue(r['fact_selection']['gaps'])
        self.assertFalse(fact_record_coverage(row(r)))

    def test_language_bound_operand_order(self):
        ld = fact('ld', 'parameters.order', scope={'languages':['ladder']})
        ld['value'] = {'symbols':['s1','s2']}
        st = fact('st', 'parameters.order', scope={'languages':['st']})
        st['value'] = {'symbols':['s2','s1']}
        p = packet([ld,st,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')])
        for language, expected in [('ladder','r1'),('st','r2')]:
            r = project_packet(p, plc_model='FX5U', language=language, aspects='operand_range', query='PWM s1')
            self.assertIn(expected, [f['id'] for f in r['value']['facts']])
            self.assertNotIn('r2' if expected=='r1' else 'r1', [f['id'] for f in r['value']['facts']])
        st['scope'] = ld['scope']
        r = project_packet(packet([ld,st,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')]),
            plc_model='FX5U', language='ladder', aspects=['operand_range'], query='PWM s1')
        self.assertEqual(len(r['value']['facts']), 4)
        self.assertIn('ambiguous_operand_order', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_unknown_missing_and_cyclic_dependencies_remain_unresolved(self):
        unknown = fact('u','execution.state')
        unknown['status'] = 'unknown'
        for dependency in [unknown, fact('u','execution.state',depends_on=['r'])]:
            r = project_packet(packet([fact('r','effects.write',depends_on=['u']),dependency]),
                               plc_model='FX5U', aspects=['effects'])
            self.assertEqual(len(r['value']['facts']), 2)
            self.assertTrue(r['fact_selection']['gaps'])
            self.assertFalse(fact_record_coverage(row(r)))
        r = project_packet(packet([fact('r','effects.write',depends_on=['missing'])]),
                           plc_model='FX5U', aspects=['effects'])
        self.assertIn('missing_dependency', [g['reason'] for g in r['fact_selection']['gaps']])
        self.assertEqual(r['value']['facts'][0]['depends_on'], ['missing'])

    def test_query_aliases_derive_same_slots(self):
        from knowledge.structured_facts import requested_aspects
        for query in ['PWM 参数范围', 'PWM parameter range']:
            self.assertIn('operand_range', requested_aspects(query))
        self.assertEqual(requested_aspects('', aspects='constraints'), ['constraints'])

    def test_cross_packet_dependency_same_owner_only(self):
        root = packet([fact('r','effects.write',depends_on=['dep'])])
        dep = packet([fact('dep','parameters.order')])
        wrong_cpu = packet([fact('dep','parameters.order')], model='FX3U')
        wrong_cpu['value']['facts'][0]['value'] = {'wrong_cpu': True}
        results = project_instruction_records([row(root),row(dep),row(wrong_cpu)],
            plc_model='FX5U', aspects=['effects'])
        selected = authored_packet(results[0])
        self.assertEqual([f['id'] for f in selected['value']['facts']], ['r','dep'])
        self.assertNotIn('wrong_cpu', str(selected['value']['facts']))
        self.assertFalse(selected['fact_selection']['gaps'])
        missing = authored_packet(project_instruction_records([row(root),row(wrong_cpu)],
            plc_model='FX5U', aspects=['effects'])[0])
        self.assertIn('missing_dependency', [g['reason'] for g in missing['fact_selection']['gaps']])

    def test_cross_packet_ambiguous_dependency_and_related_pool(self):
        root = packet([fact('r','effects.write',depends_on=['dep'])])
        dep = packet([fact('dep','parameters.order')])
        conflict = copy.deepcopy(dep)
        conflict['value']['facts'][0]['value'] = {'conflicting': True}
        r = authored_packet(project_instruction_records([row(root),row(dep),row(conflict)],
            plc_model='FX5U', aspects=['effects'])[0])
        self.assertIn('ambiguous_dependency_identity', [g['reason'] for g in r['fact_selection']['gaps']])
        root['value']['related_instructions'] = [dep['value']]
        r = authored_packet(project_instruction_records([row(root)], plc_model='FX5U', aspects=['effects'])[0])
        self.assertEqual([f['id'] for f in r['value']['facts']], ['r','dep'])
        self.assertNotIn('missing_dependency', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_operand_order_requires_complete_applicable_dependencies(self):
        order = fact('order', 'parameters.order', depends_on=['mapping'])
        order['value'] = {'symbols':['s1','s2']}
        conflict = fact('mapping','identity.mapping')
        conflict['status'] = 'conflict'
        unknown_condition = fact('mapping','identity.mapping', conditions=[{'context':'enabled','equals':True}])
        for deps in [[], [conflict], [unknown_condition]]:
            r = project_packet(packet([order,*deps,fact('r1','parameters.1.range'),fact('r2','parameters.2.range')]),
                plc_model='FX5U', language='st', aspects=['operand_range'], query='PWM s1')
            self.assertTrue({'r1','r2'} <= {f['id'] for f in r['value']['facts']})
            self.assertIn('operand_order_dependency_unresolved', [g['reason'] for g in r['fact_selection']['gaps']])

    def test_model_projection_preserves_limits_not_provenance(self):
        f = fact('cap', 'execution.core_execution_capability')
        f['value'] = {'core_execution_capability': 'unverified', 'source_page': 42,
                      'source_line': 1, 'clause_index': 3, 'complete_clause_review': True,
                      'manual_text': 'DO NOT LEAK', 'raw_text': 'DO NOT LEAK', 'unknown_condition': True}
        r = technical_fact_packet(project_packet(packet([f]), plc_model='FX5U', aspects=['parameters']))
        encoded = json.dumps(r)
        self.assertNotIn('DO NOT LEAK', encoded)
        self.assertNotIn('source_page', encoded)
        self.assertIn('unverified', encoded)
        self.assertIn('unknown_condition', encoded)
        self.assertFalse(r['selection']['whole_instruction_complete'])

    def test_real_retrieval_and_context_zero_top_k(self):
        p = packet([fact('p','parameters.1.range'),fact('e','effects.write')])
        with ExitStack() as stack:
            for name in ('resolve_device_family_records', 'resolve_device_records', 'resolve_error_records', 'resolve_positioning_records', 'resolve_language_records'):
                stack.enter_context(patch('knowledge.structured_facts.' + name, return_value=[]))
            # External Haystack source routing is orthogonal to the projection.
            stack.enter_context(patch('knowledge.scope.retrieval_plan', return_value={'facts':True,'design':False,'source_lanes':['fact','support','unclassified']}))
            stack.enter_context(patch('knowledge.scope.filter_records', side_effect=lambda rows,lanes:list(rows)))
            stack.enter_context(patch('knowledge.structured_facts._runtime', return_value=(None,None,None,None,{})))
            stack.enter_context(patch('knowledge.fact_corpus.resolve', side_effect=lambda kind,*a,**k:[row(p)] if kind=='instruction' else []))
            stack.enter_context(patch('knowledge.structured_facts._adjudicated_rows', side_effect=lambda rows,*a:(rows,True)))
            results = retrieve_fact_aware_knowledge('PWM 参数范围', plc_model='FX5U', language='st',
                aspects=['operand_range'], top_k=0, targets={'instructions':[{'opcode':'PWM','base_opcode':'PWM'}]})
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]['_explicit_target'])
            context = build_knowledge_context('PWM 参数范围', plc_model='FX5U', language='st',
                aspects=['operand_range'], task_type='generate', top_k=0)
            self.assertIn('parameters.1.range', str(context))
            self.assertNotIn('effects.write', str(context))


class NonInstructionConditionProjectionTests(unittest.TestCase):
    def test_error_device_process_and_example_conditions_reach_model_with_their_values(self):
        from shared.model_context import technical_fact_packet
        for kind in ('error','device','process','example'):
            with self.subTest(kind=kind):
                packet={'kind':kind,'target':'synthetic','scope':{'models':['FX3U']},
                    'conditions':[{'required_device_register':'D8061'},{'required_feature':'builtin_CC_Link_LT'}],
                    'requires':[{'module':'synthetic_module'}],'status':'candidate_evidence',
                    'sources':[{'manual_id':'private_source','pdf_page':7}],
                    'value':{'meaning':'synthetic_fault','recovery':{'actions':[{'action':'check_connections'}]}}}
                projected=technical_fact_packet(packet)
                self.assertEqual(projected['conditions'],packet['conditions'])
                self.assertEqual(projected['requires'],packet['requires'])
                self.assertEqual(projected['knowledge_status'],'candidate_evidence')
                self.assertNotIn('private_source',json.dumps(projected))
                self.assertEqual(projected['value']['recovery']['actions'][0]['action'],'check_connections')
                projected['conditions'][0]['required_device_register']='changed'
                self.assertEqual(packet['conditions'][0]['required_device_register'],'D8061')


    def test_equal_display_codes_keep_the_delivered_unit_model_scope(self):
        from shared.model_context import technical_knowledge_results
        packets=[{'kind':'error','target':'1020','scope':{'models':models},
            'value':{'register':register,'code':'1020','meaning':'synthetic'},'content_origin':'authored_fact'}
            for models,register in [(['FX3U'],'D8060'),(['FX3G','FX3U'],'D8449')]]
        records=[dict(row(packet),fact_kind='error',fact_target='1020',plc_models='FX3U') for packet in packets]
        results=technical_knowledge_results(records)
        self.assertEqual(len(results),2)
        self.assertEqual([r['plc_models'] for r in results],['FX3U','FX3G|FX3U'])
        self.assertEqual([r['knowledge'][0]['value']['register'] for r in results],['D8060','D8449'])


class SharedInstructionPresentationTests(unittest.TestCase):
    def test_literal_variant_takes_precedence_over_same_family_description(self):
        from knowledge.structured_facts import structured_fact_targets
        target = structured_fact_targets('使用 DZCPP 指令进行区间比较。', plc_model='FX3G', language='ladder')
        self.assertEqual([item['opcode'] for item in target['instructions']], ['DZCPP'])
        alias = structured_fact_targets('区间比较', plc_model='FX3G', language='ladder')
        self.assertEqual([item['opcode'] for item in alias['instructions']], ['ZCP'])

    def test_model_projection_keeps_other_families_parameters_and_output_rules(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        from shared.model_context import technical_block, technical_context_units
        for opcode in ('CMP', 'DCMP', 'MOV', 'DMOV', 'MUL', 'DMUL', 'BMOV'):
            with self.subTest(opcode=opcode):
                facts, _ = compose_programming_facts(registry, opcode, cpu='FX3G', existing=[])
                source = {'kind': 'instruction', 'target': opcode, 'scope': {'models': ['FX3G']},
                          'value': {'opcode': opcode, 'target_model': 'FX3G', 'facts': facts},
                          '_knowledge_context': {'model': 'FX3G', 'language': 'ladder', 'project_mode': 'native_ladder_instruction'}}
                before = copy.deepcopy(source)
                card = technical_fact_packet(source)
                projected, = technical_context_units(technical_block(card))
                original_table, = card['value']['parameter_tables']
                actual_table, = projected['value']['parameter_tables']
                fields = ('name', 'purpose', 'type', 'word_device_layout', 'occupied_bit_count')
                self.assertEqual([{key: p.get(key) for key in fields} for p in actual_table['parameters']],
                                 [{key: p.get(key) for key in fields} for p in original_table['parameters']])
                def rules(unit):
                    result = []
                    for group in unit['value'].get('additional_knowledge', []):
                        claims = group.get('semantics', {}).get('effects.documented_processing', [])
                        for claim in claims if isinstance(claims, list) else [claims]:
                            result.extend(claim.get('value', claim).get('rules', []))
                    return result
                self.assertTrue(rules(card))
                self.assertEqual(rules(projected), rules(card))
                self.assertEqual(source, before)

    def test_manual_delivery_selects_one_language_or_both_rewrite_sides(self):
        from unittest.mock import patch
        from agent_runtime.manual_tools import _search_plc_manual
        from agent_runtime.tool_context import ToolContext
        def retrieve(query, *, language, **options):
            signature = fact('signature-' + language, 'parameters.typed_operands',
                value={'representation': options.get('project_mode') or ('native_ladder_instruction' if language == 'ladder' else 'documented_interface'),
                       'operands': [{'name': 'S', 'direction': 'input', 'purpose': 'source', 'type': 'INT'}]},
                scope={'models': ['FX3G'], 'forms': ['MOV'], 'languages': [language]})
            source = {'kind': 'instruction', 'target': 'MOV', 'scope': {'models': ['FX3G']},
                      'content_origin': 'authored_fact', 'value': {'opcode': 'MOV', 'target_model': 'FX3G', 'facts': [signature]}}
            return [dict(row(source), fact_target='MOV', instruction_opcode='MOV')]
        with patch('knowledge.retriever.retrieve_fact_aware_knowledge', side_effect=retrieve) as fetch, \
                patch('knowledge.structured_facts.retrieval_coverage', return_value={'status': 'complete', 'targets': []}):
            for query, language, expected in [('用 FBD 编写 MOV', 'fbd', {'fbd'}),
                    ('用 ST 编写 MOV', 'st', {'st'}), ('MOV', None, {'ladder'}),
                    ('用 ST 编写 MOV', None, {'st'}),
                    ('用结构化梯形图编写 MOV', 'ladder', {'ladder'}),
                    ('把 MOV 从 ST 改写为梯形图', 'ladder', {'st', 'ladder'}),
                    ('比较 ST 和 FBD 的写法，使用 FBD', 'fbd', {'fbd'})]:
                with self.subTest(query=query):
                    fetch.reset_mock()
                    result = _search_plc_manual(ToolContext({'plc_model': 'FX3G', 'target_mode': 'ladder'}), {'query': query, 'language': language})
                    units = [unit for entry in result['results'] for unit in entry['knowledge']]
                    tables = [table for unit in units for table in unit.get('value', {}).get('parameter_tables', [])]
                    self.assertEqual({table['language'] for table in tables}, expected)
                    self.assertEqual({call.kwargs['language'] for call in fetch.call_args_list}, expected)
                    self.assertEqual(len(tables), len(expected))

    def test_default_view_keeps_effective_parameters_and_unique_behavior(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts, programming_system_prompt
        from shared.model_context import technical_block, technical_context_units
        facts, _ = compose_programming_facts(registry, 'DZCPP', cpu='FX3G', existing=[])
        source = {'kind': 'instruction', 'target': 'DZCPP', 'scope': {'models': ['FX3G']},
                  'value': {'opcode': 'DZCPP', 'target_model': 'FX3G', 'facts': facts},
                  '_knowledge_context': {'model': 'FX3G', 'language': 'ladder', 'project_mode': 'native_ladder_instruction'}}
        units = technical_context_units(technical_block(technical_fact_packet(source)))
        unit, = units
        self.assertEqual(unit['kind'], 'instruction')
        table, = unit['value']['parameter_tables']
        self.assertEqual(table['language'], 'ladder')
        self.assertEqual([parameter['name'] for parameter in table['parameters']], ['S1', 'S2', 'S', 'D'])
        self.assertTrue(all(parameter['purpose'] for parameter in table['parameters']))
        for parameter in table['parameters'][:3]:
            self.assertEqual(parameter['type'], {'kind': 'int', 'bits': 32, 'signed': True})
            self.assertEqual(parameter['word_device_layout']['word_count'], 2)
        self.assertEqual(table['parameters'][-1]['occupied_bit_count'], 3)
        text = json.dumps(unit)
        self.assertNotIn('parameter_table"', text)
        self.assertNotIn('common_rules', text)
        self.assertNotIn('parameters.variant_family', text)
        self.assertNotIn('execution.source_form', text)
        self.assertIn('effective_upper_equals_lower_bound', text)
        self.assertIn('(S1 > S)', text)
        self.assertIn('execution_condition_rising_edge', text)
        self.assertEqual(unit['value']['forms'][0]['modifiers'], {'double': True, 'pulse': True})
        state = next(group['semantics']['execution.disabled_state']
                     for group in unit['value']['additional_knowledge']
                     if 'execution.disabled_state' in group['semantics'])
        self.assertEqual(state, {'when_not_executed': 'retain_destination_values'})
        self.assertIn('D/P', programming_system_prompt())
        self.assertEqual(technical_context_units(technical_block(unit)), units)

    def test_model_form_preserves_resolved_modifiers_and_specific_state(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        from shared.model_context import technical_block, technical_context_units
        for opcode, double, pulse, disabled in [
                ('ZCP', False, False, 'retain_destination_values'),
                ('DZCP', True, False, 'retain_destination_values'),
                ('ZCPP', False, True, 'retain_destination_values'),
                ('DZCPP', True, True, 'retain_destination_values'),
                ('DANDP', True, True, 'no_destination_write')]:
            with self.subTest(opcode=opcode):
                facts, _ = compose_programming_facts(registry, opcode, cpu='FX3G', existing=[])
                source = {'kind': 'instruction', 'target': opcode, 'scope': {'models': ['FX3G']},
                          'value': {'opcode': opcode, 'target_model': 'FX3G', 'facts': facts},
                          '_knowledge_context': {'model': 'FX3G', 'language': 'ladder',
                                                 'project_mode': 'native_ladder_instruction'}}
                unit, = technical_context_units(technical_block(technical_fact_packet(source)))
                self.assertEqual(unit['value']['forms'][0]['modifiers'], {'double': double, 'pulse': pulse})
                states = [group['semantics']['execution.disabled_state']
                          for group in unit['value']['additional_knowledge']
                          if 'execution.disabled_state' in group['semantics']]
                self.assertEqual(states, [{'when_not_executed': disabled}])
        unit = {'kind': 'instruction', 'target': 'synthetic',
                'value': {'family': 'synthetic', 'forms': [{'opcode': 'synthetic'}]}}
        delivered, = technical_context_units(technical_block(unit))
        self.assertNotIn('modifiers', delivered['value']['forms'][0])
        self.assertFalse(delivered['value']['additional_knowledge'])

    def test_literal_forms_and_mapped_names_use_registry(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from tools.fact_applied_review import _root
        for name, base, double, pulse in [('ZCP', 'ZCP', False, False), ('DZCP', 'ZCP', True, False),
                ('ZCPP', 'ZCP', False, True), ('DZCPP', 'ZCP', True, True), ('DANDP', 'WAND', True, True)]:
            form = registry.resolve_catalog_form(name, cpu='FX3G')
            self.assertEqual((form.base_mnemonic, form.double, form.pulse), (base, double, pulse))
            self.assertEqual(_root(name), base)
        for literal in ('DPLS', 'DSWP', 'DDEG', 'ZCPPP', 'DBMOV'):
            self.assertIsNone(registry.resolve_catalog_form(literal, cpu='FX3G'))
            self.assertEqual(_root(literal), literal)
        self.assertEqual(_root('DEG'), 'DEG')

    def test_family_data_bind_width_and_output_layout(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        for name, bits in [('ZCP', 16), ('ZCPP', 16), ('DZCP', 32), ('DZCPP', 32),
                           ('MUL', 16), ('DMUL', 32)]:
            with self.subTest(name=name):
                groups = family_fact_groups(registry, name, cpu='FX3G')
                parameters = next(f for f in groups if f['dimension'] == 'parameters.typed_operands')['value']['operands']
                self.assertEqual(parameters[0]['type']['bits'], bits)
                self.assertEqual(parameters[0]['word_device_layout']['word_count'], bits // 16)
                if name.endswith('MUL'):
                    self.assertEqual(parameters[-1]['type']['bits'], bits * 2)
                    self.assertEqual(parameters[-1]['word_device_layout']['word_count'], bits // 8)
                else:
                    self.assertEqual(parameters[-1]['occupied_bit_count'], 3)
                    self.assertTrue(all(p['purpose'] for p in parameters))
        legacy = family_fact_groups(registry, 'DZCPP', cpu='FX1N')
        parameters = next(f['value']['operands'] for f in legacy if f['dimension'] == 'parameters.typed_operands')
        self.assertEqual(parameters[0]['word_device_layout']['word_count'], 2)
        self.assertEqual(parameters[-1]['occupied_bit_count'], 3)
        self.assertFalse(family_fact_groups(registry, 'DZCPP', cpu='FX5U'))

    def test_zone_relations_cover_signed_equal_and_reversed_bounds(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression
        cases = [(-10, -5, -12, [True, False, False]), (-10, -5, -5, [False, True, False]),
                 (-10, -5, -3, [False, False, True]), (10, 10, 10, [False, True, False]),
                 (10, 5, 7, [True, False, False]), (10, 5, 10, [False, True, False]),
                 (10, 5, 11, [False, False, True])]
        for name in ('ZCP', 'ZCPP', 'DZCP', 'DZCPP'):
            groups = family_fact_groups(registry, name, cpu='FX3G')
            behavior = next(f for f in groups if f['dimension'] == 'effects.documented_processing')['value']['rules'][0]
            for low, high, value, expected in cases:
                with self.subTest(form=name, bounds=(low, high), value=value):
                    actual = [evaluate_expression(output['expression'], {'S1': low, 'S2': high, 'S': value})
                              for output in behavior['outputs']]
                    self.assertEqual(actual, expected)
            source = {'kind': 'instruction', 'target': name, 'scope': {'models': ['FX3G']},
                      'value': {'opcode': name, 'target_model': 'FX3G', 'facts': groups}}
            wrong_interface = project_packet(source, plc_model='FX3G', language='ladder',
                aspects=['programming_semantics'], project_mode='structured_ladder_block')
            self.assertFalse(wrong_interface['value']['facts'])

    def test_modifier_execution_requires_source_scoped_family_proof(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        registry = copy.copy(registry)
        registry._family_knowledge = []
        proof = fact('forms', 'parameters.variant_family', value={'section_variants': ['ZCP', 'ZCPP']},
                     scope={'models': ['FX1N'], 'forms': ['ZCPP'], 'languages': ['ladder']})
        for extra, expected in [({}, True), ({'status': 'unknown'}, False),
                ({'conditions': [{'context': 'cpu_version', 'equals': '2.0'}]}, True),
                ({'depends_on': ['missing']}, False), ({'scope': {'models': ['FX3U']}}, False)]:
            groups, _ = compose_programming_facts(registry, 'ZCPP', cpu='FX1N', existing=[dict(proof, **extra)])
            selected = [f for f in groups if f['id'] == 'modifier:execution']
            self.assertEqual(bool(selected), expected)
            if selected:
                self.assertEqual(selected[0]['value']['executions_per_edge'], 1)
                self.assertEqual(selected[0]['depends_on'], ['forms'])

    def test_catalogue_name_does_not_supply_standard_function_semantics(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for cpu, name, language in [('Q03UDV', 'ADD', 'st'), ('Q03UDV', 'MUL', 'fbd'),
                                     ('FX5U', 'MUL', 'st'), ('FX3U', 'ADD', 'st')]:
            with self.subTest(cpu=cpu, name=name, language=language):
                value = registry.instruction_knowledge(name, cpu=cpu, language=language)
                self.assertEqual(value['binding_status'], 'catalogue_identity_only')
                self.assertFalse({'data_bits', 'execution', 'modifiers', 'knowledge_module'} & set(value))
        native = registry.instruction_knowledge('DADD', cpu='FX3U', language='ladder',
                    context={'project_mode': 'native_ladder_instruction'})
        self.assertEqual(native['data_bits'], 32)
        self.assertEqual(native['execution']['trigger'], 'each_evaluation_when_execution_condition_true')
        self.assertEqual(native['execution']['evaluation_frequency'], 'caller_control_flow')
        self.assertIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'}, native['conditions'])
        block = registry.instruction_knowledge('DADD', cpu='FX3U', language='ladder',
                    context={'project_mode': 'structured_ladder_block'})
        self.assertNotIn('data_bits', block)

    def test_modifier_rules_retain_parent_conditions_and_interface(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        registry = copy.copy(registry)
        registry._family_knowledge = []
        scope = {'models': ['FX1N'], 'forms': ['ZCPP'], 'languages': ['ladder']}
        version = {'context': 'cpu_version', 'equals': '2.0'}
        mode = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        parent = fact('source-binding', 'identity.binding', scope=scope, conditions=[version])
        proof = fact('forms', 'parameters.variant_family', scope=scope, conditions=[mode],
                     depends_on=['source-binding'], value={'section_variants': ['ZCP', 'ZCPP']})
        groups, _ = compose_programming_facts(registry, 'ZCPP', cpu='FX1N', existing=[parent, proof])
        rule = next(f for f in groups if f['id'] == 'modifier:execution')
        self.assertEqual(rule['scope'], scope)
        self.assertCountEqual(rule['conditions'], [mode, version])
        value = registry.instruction_knowledge('ZCPP', cpu='FX1N', language='ladder', facts=[parent, proof])
        self.assertCountEqual(value['conditions'], [mode, version])
        source = {'kind': 'instruction', 'target': 'ZCPP', 'scope': {'models': ['FX1N']},
                  'value': {'opcode': 'ZCPP', 'target_model': 'FX1N', 'facts': groups}}
        for actual, applicable in [('2.0', True), ('1.0', False), (None, None)]:
            selected = project_packet(source, plc_model='FX1N', language='ladder', aspects=['execution'],
                        project_mode='native_ladder_instruction',
                        instance_context={'cpu_version': actual} if actual is not None else {})
            rules = [f for f in selected['value']['facts'] if f['id'] == 'modifier:execution']
            if applicable is False:
                self.assertFalse(rules)
            else:
                self.assertTrue(rules)
                self.assertEqual(rules[0]['selection_applicability'], 'applicable' if applicable else 'unverified')
        bad = copy.deepcopy(parent); bad['sources'][0]['conditions'] = {'opaque': 'restriction'}
        bad_groups, _ = compose_programming_facts(registry, 'ZCPP', cpu='FX1N', existing=[bad, proof])
        self.assertFalse(any(f['id'] == 'modifier:execution' for f in bad_groups))

    def test_native_contracts_remain_separate_from_callable_tables(self):
        from plc.instruction_relations import normalize_native_interface_facts, normalize_declared_form_labels
        native = [fact('registry:parameters.order', 'parameters.order', value={'symbols': ['s', 'd']}),
                  fact('native:width', 'parameters.source_width', value={'bits': 16}),
                  fact('operand:1:purpose', 'parameters.1.purpose'),
                  fact('chart:results', 'effects.result_mapping', scope={'languages': ['ladder']}),
                  fact('authored:source:native', 'parameters.typed_operands', scope={'languages': ['ladder']},
                       value={'native_form': {'operand_order': ['s', 'd']}, 'operands': []})]
        call = fact('declaration', 'parameters.st_signature', value={'parameters': [{'name': 's', 'type': 'ANY_NUM'}],
                    'returns': 'ANY_NUM', 'available': True}, scope={'languages': ['st']})
        old = copy.deepcopy([*native, call])
        normalized, originals = normalize_native_interface_facts([*native, call])
        self.assertEqual(originals, native)
        self.assertEqual([*native, call], old)
        self.assertEqual(normalized[-1], call)
        repeated, duplicates = normalize_native_interface_facts(normalized)
        self.assertEqual(repeated, normalized)
        self.assertFalse(duplicates)
        for item in normalized[:-1]:
            self.assertEqual(item['scope']['languages'], ['ladder'])
            self.assertIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'}, item['conditions'])
        label = fact('authored:ql:form:identity.declared_form:pulse', 'identity.declared_form',
                     value={'opcode': 'CMP', 'form_variant': 'pulse'})
        timing = fact('timing', 'execution.trigger', value={'trigger': 'level'})
        corrected, history = normalize_declared_form_labels([label, timing])
        self.assertEqual(corrected[0]['value'], {'opcode': 'CMP'})
        self.assertEqual(corrected[1], timing)
        self.assertEqual(history, [label])

    def test_parameter_facets_do_not_cross_incompatible_interface_conditions(self):
        scope = {'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']}
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        structured = {'context': 'project_mode', 'equals': 'structured_ladder_block'}
        tables = [fact('native-table', 'parameters.typed_operands', scope=scope, conditions=[native],
                       value={'operands': [{'name': 'S1', 'type': 'INT', 'direction': 'input'}]}),
                  fact('block-table', 'parameters.typed_operands', scope=scope, conditions=[structured],
                       value={'operands': [{'name': 'S1', 'type': 'DINT', 'direction': 'input'}]})]
        purpose = fact('native-purpose', 'parameters.1.purpose', scope=scope, conditions=[native],
                       value={'symbol': 'S1', 'value': {'semantic_role': 'lower_bound'}})
        order = fact('conditional-order', 'parameters.order', scope=scope,
                     conditions=[{'context': 'interface_version', 'equals': '2'}], value={'symbols': ['S1']})
        result = technical_fact_packet(packet([*tables, purpose, order]))['value']['parameter_tables']
        self.assertEqual(len(result), 3)
        native_table = next(t for t in result if t.get('representation') == 'native_ladder_instruction')
        block_table = next(t for t in result if t.get('representation') == 'structured_ladder_block')
        self.assertIn('purpose', native_table['parameters'][0])
        self.assertNotIn('purpose', block_table['parameters'][0])
        self.assertNotIn('facet_alternatives', block_table['parameters'][0])
        self.assertTrue(any(t.get('conditions') == order['conditions'] for t in result))

    def test_native_constraints_use_existing_core_interface_name(self):
        from plc.instruction_constraints import check_fact_constraints
        from plc.instruction_relations import normalize_native_interface_facts
        root = fact('registry:constraints.numeric_ranges', 'constraints.numeric_ranges',
                    value={'ranges': [{'operand_index': 0, 'minimum': 1, 'maximum': 10}]})
        groups, _ = normalize_native_interface_facts([root])
        for mode, expected in [('native_ladder', 'failed'), ('native_ladder_instruction', 'failed'),
                               ('structured_ladder_block', 'not_applicable'), (None, 'unverified')]:
            result = check_fact_constraints(groups, {'$0': 20}, target='SYNTH', plc_model='FX5U',
                        language='ladder', context={'project_mode': mode} if mode else {})
            self.assertEqual(result['checks'][0]['status'], expected)

    def test_registered_source_forms_keep_name_mappings(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import registered_family_bindings
        bindings = registered_family_bindings(registry, models=['Q03UDV'])
        self.assertEqual(bindings['DANDP'], {'base_opcode': 'WAND', 'double': True, 'pulse': True, 'data_bits': 32})
        self.assertFalse({'DZCPPP', 'DBMOV', 'DPLS'} & set(bindings))

    def test_documented_families_do_not_expand_core_admission(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        for base in ('SQR', 'FLT'):
            for prefix, suffix, bits, pulse in [('', '', 16, False), ('D', '', 32, False),
                                               ('', 'P', 16, True), ('D', 'P', 32, True)]:
                name = prefix + base + suffix
                before = registry.resolve_catalog_form(name, cpu='FX3U')
                knowledge = registry.instruction_knowledge(name, cpu='FX3U', language='ladder')
                self.assertEqual(knowledge['family'], base)
                self.assertEqual(knowledge['data_bits'], bits)
                self.assertEqual(knowledge['modifiers']['pulse'], pulse)
                self.assertEqual(registry.resolve_catalog_form(name, cpu='FX3U'), before)
                groups = family_fact_groups(registry, name, cpu='FX3U')
                parameters = next(f['value']['operands'] for f in groups if f['dimension'] == 'parameters.typed_operands')
                self.assertEqual(parameters[0]['type']['bits'], bits)
                self.assertEqual(parameters[0]['direction'], 'input')
                result_bits = 32 if base == 'FLT' else bits
                self.assertEqual(parameters[-1]['type']['bits'], result_bits)
                self.assertEqual(parameters[-1]['word_device_layout']['word_count'], result_bits // 16)
                self.assertTrue(all(parameter['purpose'] for parameter in parameters))
        self.assertIsNone(registry.resolve_catalog_form('DSQRP', cpu='FX3U'))
        self.assertIsNone(registry.instruction_knowledge('DSQRPP', cpu='FX3U'))
        unsupported = family_fact_groups(registry, 'SQR', cpu='FX3G')
        self.assertFalse(any(f['dimension'] == 'parameters.typed_operands' for f in unsupported))
        self.assertTrue(any(f['value'].get('available') is False for f in unsupported))
        source = {'kind': 'instruction', 'target': 'SQR', 'scope': {'models': ['FX3G']},
                  'value': {'opcode': 'SQR', 'target_model': 'FX3G', 'facts': unsupported}}
        native = project_packet(source, plc_model='FX3G', language='ladder',
            aspects=['programming_semantics'], project_mode='native_ladder_instruction')
        from knowledge.structured_facts import _aspect_state
        self.assertEqual(_aspect_state(native, 'purpose', model='FX3G', language='ladder', query='SQR'), 'not_applicable')
        structured = project_packet(source, plc_model='FX3G', language='ladder',
            aspects=['programming_semantics'], project_mode='structured_ladder_block')
        self.assertFalse(structured['value']['facts'])

    def test_version_ranges_require_explicit_cpu_and_version_context(self):
        from plc.instruction_constraints import _condition
        from plc.instruction_definition import UnknownInstructionSemantics
        rule = {'context': 'cpu_version', 'minimum_by_model': {'FX3U': '2.20', 'FX3UC': '1.00'}}
        for model, version, expected in [('FX3U', '2.19', False), ('FX3U', '2.20', True),
                                          ('FX3U', '2.20.0', True), ('FX3UC', '1.00', True)]:
            self.assertIs(_condition(rule, {}, {'$PLC_MODEL': model, 'cpu_version': version}), expected)
        for context in ({}, {'$PLC_MODEL': 'FX3U'}, {'cpu_version': '2.20'},
                        {'$PLC_MODEL': 'FX3U', 'cpu_version': 2.2}):
            with self.assertRaises(UnknownInstructionSemantics):
                _condition(rule, {}, context)

    def test_cpu_table_binds_independent_cells_and_footnotes(self):
        from tools.fact_instruction_catalogue import cpu_table_rows, availability_fact
        def word(text, x, top=100):
            return {'text': text, 'x0': x - 2, 'x1': x + 2, 'top': top, 'bottom': top + 8}
        words = [word('48', 60), word('ROOT', 100), word('–', 300), word('*6', 315),
                 word('\uf050', 330), word('–', 360)]
        notes = {'6': {'kind': 'minimum_version', 'models': ['CPU_B'], 'version': '1.10'}}
        states = cpu_table_rows(words, columns={'CPU_A': 300, 'CPU_B': 315, 'CPU_C': 330,
                                                'CPU_D': 345, 'CPU_E': 360}, notes=notes)['ROOT']
        self.assertFalse(states['CPU_A']['supported'])
        self.assertTrue(states['CPU_B']['supported'])
        self.assertNotIn('CPU_D', states)
        claim = {**states['CPU_B'], 'base_opcode': 'ROOT', 'sources': [{'source_id': 'synthetic'}]}
        compiled = availability_fact('ROOT', 'CPU_B', claim)
        self.assertEqual(compiled['conditions'], [
            {'context': 'project_mode', 'equals': 'native_ladder_instruction'},
            {'context': 'cpu_version', 'minimum': '1.10'}])
        self.assertEqual(compiled['value']['model_states'], {'CPU_B': 'conditional'})

    def test_source_parameter_enrichment_keeps_richer_storage_type(self):
        from tools.fact_applied_review import enrich_compiled_native_parameters, _described_operand_direction
        old = fact('signature', 'parameters.typed_operands',
            scope={'models': ['CPU_A'], 'forms': ['ROOT']},
            value={'operands': [{'name': 'S', 'type': 'INT', 'direction': 'output',
                                  'direction_basis': 'own_operand_read_write_description'},
                                 {'name': 'D', 'type': 'REAL', 'direction': 'output'}]})
        new = copy.deepcopy(old)
        new['value']['operands'][0].update(direction='input', purpose='radicand',
            direction_basis='same_manual_explicit_source_destination_symbol_definition')
        new['value']['operands'][1]['type'] = 'unknown'
        rows, originals = enrich_compiled_native_parameters([old], [new])
        self.assertEqual(originals, [old])
        self.assertEqual(rows[0]['value']['operands'][1]['type'], 'REAL')
        self.assertEqual(rows[0]['value']['operands'][0]['direction'], 'input')
        self.assertEqual(rows[0]['value']['operands'][0]['purpose'], 'radicand')
        self.assertEqual(enrich_compiled_native_parameters(rows, [new]), (rows, []))
        self.assertEqual(old['value']['operands'][0]['direction'], 'output')
        sentence = 'A measurement stored in S is transformed and written to D.'
        self.assertEqual(_described_operand_direction('Measured input', 'S', sentence), 'unknown')
        self.assertEqual(_described_operand_direction('Computed output', 'D', sentence), 'output')

    def test_legacy_forms_use_registered_modifiers_including_base_names_ending_in_p(self):
        from tools.fact_applied_review import _legacy_variants
        cases = [
            ('CMP', 'CMP,CMPP: 7 steps DCMP,DCMPP: 13 steps',
             [('CMP', 16, 'continuous'), ('CMPP', 16, 'pulse'),
              ('DCMP', 32, 'continuous'), ('DCMPP', 32, 'pulse')]),
            ('ZCP', 'ZCP,Z CPP: 9 steps DZCP,DZCPP: 17 steps',
             [('ZCP', 16, 'continuous'), ('ZCPP', 16, 'pulse'),
              ('DZCP', 32, 'continuous'), ('DZCPP', 32, 'pulse')]),
            ('WAND', 'WAND,WANDP: 7 steps D A N D,DANDP: 13 steps',
             [('WAND', 16, 'continuous'), ('WANDP', 16, 'pulse'),
              ('DAND', 32, 'continuous'), ('DANDP', 32, 'pulse')]),
            ('BMOV', 'BMOV,BMOVP: 7 steps DBMOV: 13 steps',
             [('BMOV', 16, 'continuous'), ('BMOVP', 16, 'pulse')]),
        ]
        for base, text, expected in cases:
            with self.subTest(base=base):
                rows = _legacy_variants({'opcode': base, 'text': text,
                    'parameter_conventions': {'d_prefix_is_32_bit': True}})
                self.assertEqual([(r['opcode'], r['width_bits'], r['execution_form']) for r in rows], expected)

    def test_legacy_unknown_form_keeps_unknown_timing_and_width_convention_dependency(self):
        from tools.fact_applied_review import _legacy_variants
        unknown = _legacy_variants({'opcode': 'SYNTHP', 'text': 'SYNTHP: 3 steps 16 bit'})
        self.assertEqual([(r['width_bits'], r['execution_form']) for r in unknown], [(16, 'unknown')])
        rows = _legacy_variants({'opcode': 'CMP', 'text': 'CMP,CMPP: 7 steps DCMP,DCMPP: 13 steps'})
        self.assertTrue(all(r['width_bits'] == 'unknown' for r in rows))
        self.assertEqual([r['execution_form'] for r in rows], ['continuous', 'pulse', 'continuous', 'pulse'])

    def test_explicit_source_heading_owns_variants_even_with_standalone_catalogue_name(self):
        from tools.fact_applied_review import _programming_manual_variants
        record = {'opcode': 'FLT', 'text': '1. 16-bit operation (FLT and FLTP)\n'
                  '2. 32-bit operation (DFLT and DFLTP)', 'table_groups': []}
        rows = _programming_manual_variants(record)
        self.assertEqual({row['opcode']: row['width_bits'] for row in rows},
                         {'FLT': 16, 'FLTP': 16, 'DFLT': 32, 'DFLTP': 32})

    def test_programming_manual_table_binds_form_and_steps_to_width_columns(self):
        from tools.fact_applied_review import _programming_manual_variants

        def word(text, x, top):
            widths = {'16-bit': 17, '32-bit': 18, 'Instruction': 32,
                      'Mnemonic': 32, 'Operation': 31, 'Condition': 30}
            return {'text': text, 'x0': x, 'x1': x + widths.get(text, max(4, len(text) * 4)),
                    'top': top, 'bottom': top + 8}

        def record(opcode, groups):
            words = []
            for width, x, names, forms, steps in groups:
                mnemonic_x, operation_x, condition_x = ((238, 284, 316) if x < 300
                                                        else (425, 471, 504))
                step_x = x + (21 if width == 16 else 16)
                words.extend([word(f'{width}-bit', x, 170), word('Instruction', x + 20, 170),
                    word('Mnemonic', mnemonic_x, 170), word('Operation', operation_x, 170),
                    word('Condition', condition_x, 170), word(str(steps), step_x, 187),
                    word('steps', step_x + len(str(steps)) * 4 + 2, 187)])
                for name, form, top in zip(names, forms, [185, 199, 213, 227]):
                    name_x, form_x = ((236, 304) if x < 300 else (423, 491))
                    words.extend([word(name, name_x, top), word(form, form_x, top - 3)])
            return {'opcode': opcode, 'manual_id': 'fx3_programming_r',
                    'text': '\n'.join(f'{width}-bit operation ({" and ".join(names)})'
                                      for width, _, names, _, _ in groups),
                    'table_groups': [], 'parameter_pages': [{'page': 1, 'words': words}]}

        cases = [
            ('BON', [(16, 179, ['BON', 'BONP'], ['Continuous', 'Pulse'], 7),
                     (32, 367, ['DBON', 'DBONP'], ['Continuous', 'Pulse'], 13)],
             [('BON', 16, 'continuous', 7), ('BONP', 16, 'pulse', 7),
              ('DBON', 32, 'continuous', 13), ('DBONP', 32, 'pulse', 13)]),
            ('HTOS', [(16, 179, ['HTOS', 'HTOSP'], ['Continuous', 'Pulse'], 5),
                      (32, 367, ['DHTOS', 'DHTOSP'], ['Continuous', 'Pulse'], 9)],
             [('HTOS', 16, 'continuous', 5), ('HTOSP', 16, 'pulse', 5),
              ('DHTOS', 32, 'continuous', 9), ('DHTOSP', 32, 'pulse', 9)]),
            ('HOUR', [(16, 179, ['HOUR'], ['Continuous'], 7),
                      (32, 367, ['DHOUR'], ['Continuous'], 13)],
             [('HOUR', 16, 'continuous', 7), ('DHOUR', 32, 'continuous', 13)]),
            ('DEXP', [(16, 179, [], [], '-'),
                      (32, 367, ['DEXP', 'DEXPP'], ['Continuous', 'Pulse'], 9)],
             [('DEXP', 32, 'continuous', 9), ('DEXPP', 32, 'pulse', 9)]),
        ]
        for opcode, groups, expected in cases:
            with self.subTest(opcode=opcode):
                rows = _programming_manual_variants(record(opcode, groups))
                self.assertEqual([(row['opcode'], row['width_bits'], row['execution_form'],
                                   row.get('program_steps')) for row in rows], expected)

    def test_structured_literal_pulse_requires_its_own_heading_and_format_row(self):
        from tools.fact_applied_review import _structured_variant_table
        heading = '1. 16-bit operation (SYNTH, SYNTHP)\n'
        continuous = 'SYNTH 16 bits Continuous SYNTH(EN,s,n,d);\n'
        pulse = 'SYNTHP 16 bits Pulse SYNTHP(EN,s,n,d);\n'
        unrelated = 'OTHERP 16 bits Pulse OTHERP(EN,s,n,d);\n'
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form', return_value=None):
            rows = _structured_variant_table({'opcode': 'SYNTH', 'text': heading + continuous + pulse + unrelated})
            self.assertEqual([(r['opcode'], r['width_bits'], r['execution_form'], r['call_form']) for r in rows],
                             [('SYNTH', 16, 'continuous', 'SYNTH(EN,s,n,d)'),
                              ('SYNTHP', 16, 'pulse', 'SYNTHP(EN,s,n,d)')])
            for text in [continuous + pulse, heading + continuous,
                         '1. 16-bit operation (OTHER, OTHERP)\n' + continuous + pulse]:
                with self.subTest(text=text):
                    rows = _structured_variant_table({'opcode': 'SYNTH', 'text': text})
                    self.assertEqual([r['opcode'] for r in rows], ['SYNTH'])

    def test_structured_table_binds_separately_headed_double_word_variants(self):
        from tools.fact_applied_review import _structured_variant_table
        text = ('1. 16-bit operation (BON, BONP)\n'
                '2. 32-bit operation (DBON, DBONP)\n'
                'BON 16 bits Continuous BON(EN,s,n,d);\n'
                'BONP 16 bits Pulse BONP(EN,s,n,d);\n'
                'DBON 32 bits Continuous DBON(EN,s,n,d);\n'
                'DBONP 32 bits Pulse DBONP(EN,s,n,d);\n'
                'OTHER 32 bits Continuous OTHER(EN,s,n,d);\n')
        rows = _structured_variant_table({'opcode': 'BON', 'text': text})
        self.assertEqual([(r['opcode'], r['width_bits'], r['execution_form'], r['call_form'])
                          for r in rows], [
            ('BON', 16, 'continuous', 'BON(EN,s,n,d)'),
            ('BONP', 16, 'pulse', 'BONP(EN,s,n,d)'),
            ('DBON', 32, 'continuous', 'DBON(EN,s,n,d)'),
            ('DBONP', 32, 'pulse', 'DBONP(EN,s,n,d)'),
        ])

    def test_structured_table_binds_literal_double_word_row_under_bare_heading(self):
        from tools.fact_applied_review import _structured_variant_table
        text = ('HOUR 16 bits Continuous HOUR(EN,s,d1,d2);\n'
                'DHOUR 32 bits Continuous DHOUR(EN,s,d1,d2);\n'
                '1. 16-bit operation\n'
                '2. 32-bit operation\n')
        rows = _structured_variant_table({'opcode': 'HOUR', 'text': text})
        self.assertEqual([(r['opcode'], r['width_bits'], r['execution_form'], r['call_form'])
                          for r in rows], [
            ('HOUR', 16, 'continuous', 'HOUR(EN,s,d1,d2)'),
            ('DHOUR', 32, 'continuous', 'DHOUR(EN,s,d1,d2)'),
        ])

    def test_m_dhour_version_footnote_qualifies_only_its_exact_section_and_models(self):
        from tools.fact_applied_review import (
            _m_dhour_minimum_versions, _repair_m_applied_cpu_support_facts)
        record = {'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
                  'revision': 'M', 'id': 'chunk:fxcpu_basic_applied_m:100', 'opcode': 'HOUR',
                  'section': '21.9 HOUR / Hour Meter', 'pdf_pages': [565, 566, 567],
                  'manual_models': ['FX3U', 'FX2N', 'FX2NC'], 'text':
                  'HOUR 16 bits Continuous HOUR(EN,s,d1,d2);\n'
                  'DHOUR 32 bits Continuous DHOUR(EN,s,d1,d2);\n'
                  '1. 16-bit operation\n2. 32-bit operation\n',
                  'applicability': {'model_marks': [
                      {'model': 'FX3U', 'state': 'supported'},
                      {'model': 'FX2N', 'state': 'conditional'},
                      {'model': 'FX2NC', 'state': 'conditional'}]}}
        page_text = {'fxcpu_basic_applied_m:567':
                     'The instruction is provided in the FX2N and FX2NC PLCs Ver. 3.00 or later.'}
        versions = {'FX2N': '3.00', 'FX2NC': '3.00'}
        self.assertEqual(_m_dhour_minimum_versions(record, page_text), versions)
        self.assertEqual(_m_dhour_minimum_versions(
            {**record, 'section': '21.8 OTHER / Other'}, page_text), {})
        self.assertEqual(_m_dhour_minimum_versions(record, {}), {})

        facts = {(model, 'DHOUR'): [] for model in ('FX3U', 'FX2N', 'FX2NC')}
        self.assertEqual(_repair_m_applied_cpu_support_facts([record], facts, page_text), 8)
        self.assertEqual(next(row for row in facts[('FX3U', 'DHOUR')]
                              if row['id'].startswith('authored:applied:cpu_availability:header@'))
                         .get('conditions', []), [])
        for model, minimum in versions.items():
            rows = facts[(model, 'DHOUR')]
            self.assertIn({'context': 'cpu_version', 'minimum': minimum},
                          next(row for row in rows if row['id'].startswith(
                              'authored:applied:cpu_availability:header@'))['conditions'])
            self.assertIn({'context': 'cpu_version', 'maximum_exclusive': minimum},
                          next(row for row in rows if row['id'].startswith(
                              'authored:applied:cpu_availability:below_header_version@'))['conditions'])

    def test_m_structured_native_signature_omits_execution_enable_and_status_pins(self):
        from tools.fact_applied_review import _m_structured_native_parameter_facts
        record = {'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
                  'revision': 'M', 'id': 'chunk:fxcpu_basic_applied_m:100', 'opcode': 'DBON',
                  'section': '11.5 BON / Check Specified Bit Status', 'pdf_pages': [241, 242, 243]}
        typed = {'id': 'authored:applied:ladder_signature@fxcpu_basic_applied_m',
                 'dimension': 'parameters.ladder_signature', 'status': 'source_verified',
                 'scope': {'forms': ['DBON'], 'models': ['FX3U'], 'languages': ['ladder']},
                 'value': {'representation': 'structured_ladder_block', 'available': True,
                           'parameters': [
                     {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                     {'name': 's', 'type': 'ANY32', 'direction': 'input'},
                     {'name': 'n', 'type': 'ANY16', 'direction': 'input'},
                     {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'role': 'execution_status'},
                     {'name': 'd', 'type': 'BOOL', 'direction': 'output'}]}}
        facts = _m_structured_native_parameter_facts(record,
            {'opcode': 'DBON', 'width_bits': 32, 'call_form': 'DBON(EN,s,n,d)'}, ['FX3U'], [typed])
        native = next(fact for fact in facts if fact['id'].startswith('authored:applied:native_typed_operands@'))
        self.assertEqual(native['value']['native_form']['operand_order'], ['S', 'N', 'D'])
        self.assertEqual([(item['type'], item['direction']) for item in native['value']['operands']],
                         [('ANY32', 'input'), ('ANY16', 'input'), ('BOOL', 'output')])
        self.assertEqual(native['scope']['languages'], ['ladder'])
        self.assertEqual(facts[-1]['depends_on'], [typed['id']])

    def test_m_structured_native_array_pin_is_not_a_single_scalar(self):
        from tools.fact_applied_review import _m_structured_native_parameter_facts
        record = {'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
                  'revision': 'M', 'id': 'chunk:fxcpu_basic_applied_m:2050', 'opcode': 'DHTOS',
                  'section': '21.10 HTOS / Time Data Conversion', 'pdf_pages': [553, 554, 555]}
        signature = {'id': 'authored:applied:ladder_signature@fxcpu_basic_applied_m',
            'dimension': 'parameters.ladder_signature', 'status': 'source_verified',
            'scope': {'forms': ['DHTOS'], 'models': ['FX3U'], 'languages': ['ladder']},
            'value': {'representation': 'structured_ladder_block', 'available': True,
                'parameters': [
                    {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'execution_condition'},
                    {'name': 's', 'type': {'kind': 'array', 'element_type': 'ANY16',
                        'index_min': 0, 'index_max': 2}, 'direction': 'input'},
                    {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'role': 'execution_status'},
                    {'name': 'd', 'type': 'ANY32', 'direction': 'output'},
                ]}}
        facts = _m_structured_native_parameter_facts(record,
            {'opcode': 'DHTOS', 'width_bits': 32, 'call_form': 'DHTOS(EN,s,d)'},
            ['FX3U'], [signature])
        native = next(fact for fact in facts
                      if fact['id'] == 'authored:applied:native_typed_operands@fxcpu_basic_applied_m')
        source, destination = native['value']['operands']
        self.assertEqual(source['word_device_layout'], {
            'kind': 'independent_elements', 'device_unit_bits': 16, 'element_bits': 16,
            'element_count': 3, 'word_count': 3, 'single_scalar': False})
        self.assertNotIn('word_device_layout', destination)
        self.assertEqual(native['sources'][0]['pdf_pages'], [553, 554, 555])

    def test_m_positioning_header_availability_is_language_scoped_and_signature_bound(self):
        from tools.fact_applied_review import _repair_fx_m_structured_positioning_cpu_applicability
        signatures = {
            'st': 'authored:applied:st_signature@fxcpu_basic_applied_m',
            'fbd': 'authored:applied:fbd_signature@fxcpu_basic_applied_m',
            'ladder': 'authored:applied:ladder_signature@fxcpu_basic_applied_m',
        }
        rows = []
        for identity in signatures.values():
            rows.append({'id': identity, 'dimension': 'parameters.signature',
                         'value': {'kept': identity}, 'depends_on': ['own_typed_table']})
        before_values = {row['id']: copy.deepcopy(row['value']) for row in rows}
        facts = {('FX3S', 'DDRVI'): rows}
        record = {
            'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
            'revision': 'M', 'id': 'source-test:drvi', 'opcode': 'DRVI',
            'section': '20.1 DRVI / Relative Positioning', 'pdf_pages': [536, 537, 538],
            'text': ('DRVI 16 bits Continuous DRVI(EN,s1,s2,d1,d2);\n'
                     'DDRVI 32 bits Continuous DDRVI(EN,s1,s2,d1,d2);'),
            'applicability': {'model_marks': [{'model': 'FX3S', 'state': 'supported'}]},
        }
        with patch('tools.fact_applied_review._structured_variant_table',
                   return_value=[{'opcode': 'DDRVI', 'width_bits': 32}]):
            _repair_fx_m_structured_positioning_cpu_applicability([record], facts)
        result = facts[('FX3S', 'DDRVI')]
        header = next(row for row in result
                      if row['id'] == 'authored:applied:source_cpu_header@fxcpu_basic_applied_m')
        self.assertEqual(header['sources'][0]['pdf_pages'], [536])
        for language in ('st', 'fbd', 'ladder'):
            with self.subTest(language=language):
                availability_id = f'authored:applied:cpu_availability:{language}:header@fxcpu_basic_applied_m'
                availability = next(row for row in result if row['id'] == availability_id)
                self.assertEqual(availability['scope'], {
                    'models': ['FX3S'], 'forms': ['DDRVI'], 'languages': [language]})
                self.assertEqual(availability['depends_on'], [header['id']])
                self.assertEqual(next(row for row in result if row['id'] == signatures[language])['depends_on'],
                                 ['own_typed_table', availability_id])
                if language == 'ladder':
                    self.assertIn({'context': 'project_mode', 'equals': 'structured_ladder_block'},
                                  availability['conditions'])
                self.assertEqual(next(row for row in result if row['id'] == signatures[language])['value'],
                                 before_values[signatures[language]])

    def test_m_positioning_conditions_are_bound_to_own_header_and_range_pages(self):
        from tools.fact_applied_review import _repair_fx_m_structured_positioning_conditions

        class Result:
            def __init__(self, rows=(), one=None):
                self.rows = rows
                self.one = one

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.rows

        class Source:
            def execute(self, query, params=()):
                if 'sqlite_master' in query:
                    return Result(one={'name': 'page_artifacts'})
                return Result(rows=[
                    {'pdf_page': 536,
                     'clean_text': '20.7 DRVI / Drive to Increment; DRVI 16 bits Continuous; '
                                   'DDRVI 32 bits Continuous DDRVI(EN,s1,s2,d1,d2)'},
                    {'pdf_page': 537, 'clean_text': 'DRVI relative positioning'},
                    {'pdf_page': 538,
                     'clean_text': '-999,999 to +999,999; 10 to 10,000; 10 to 100,000; '
                                   '10 to 200,000; Y000; Y001; Y002; high speed output special adapter; '
                                   'differential line driver'},
                ])

        signature_ids = [
            'authored:applied:st_signature@fxcpu_basic_applied_m',
            'authored:applied:fbd_signature@fxcpu_basic_applied_m',
            'authored:applied:ladder_signature@fxcpu_basic_applied_m',
        ]
        facts = {('FX3S', 'DDRVI'): [
            {'id': identity, 'dimension': 'parameters.signature', 'status': 'source_verified',
             'scope': {'models': ['FX3S'], 'forms': ['DDRVI']}}
            for identity in signature_ids]}
        record = {
            'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
            'revision': 'M', 'id': 'source-test:drvi', 'opcode': 'DRVI',
            'section': '20.7 DRVI / Relative Positioning', 'pdf_pages': [536, 537, 538],
        }
        _repair_fx_m_structured_positioning_conditions(Source(), [record], facts)
        added = [row for row in facts[('FX3S', 'DDRVI')]
                 if row['id'].startswith('authored:applied:structured_')]
        self.assertEqual(len(added), 6)
        self.assertTrue(all(row['sources'][0]['pdf_pages'] == [536, 537, 538] for row in added))
        numeric = next(row for row in added if row['id'].endswith('numeric_ranges@fxcpu_basic_applied_m'))
        self.assertEqual([item['maximum'] for item in numeric['value']['ranges']], [999999, 100000])
        output = next(row for row in added if row['id'].endswith('output_requirements@fxcpu_basic_applied_m'))
        self.assertEqual(output['value']['kind'], 'structured_positioning_output_path')

    def test_set_data_order_keeps_lowercase_count_and_output_number_direction(self):
        from tools.fact_applied_review import _source_operand_declarations, _described_operand_direction
        text = ('2. Set data\n'
                'S Timer number used for this operation\n'
                'm Set value of the timer\n'
                'D Head bit number to which the set value is output\n'
                'Summary Synthetic table ends here\n'
                '3. Applicable devices\n')
        rows = _source_operand_declarations({'opcode': 'SYNTH', 'manual_id': 'fx3_programming_r'}, text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'parameters.source_order')
        self.assertEqual(rows[0][2]['symbols'], ['S', 'm', 'D'])
        self.assertEqual(_described_operand_direction(
            'Head bit number to which the set value is output (Four devices are occupied.)'), 'output')
        self.assertEqual(_described_operand_direction('Set value of the timer [1 to 32,767]'), 'input')
        descriptions = [
            ('Head device number storing hexadecimal code to be converted', 'input'),
            ('Head device number storing ASCII code to be converted', 'input'),
            ('Head device number storing converted ASCII code', 'output'),
            ('Head device number storing converted hexadecimal code', 'output'),
        ]
        for description, expected in descriptions:
            with self.subTest(description=description):
                self.assertEqual(_described_operand_direction(description), expected)

    def test_search_statistics_destination_precedes_numeric_input_description(self):
        from tools.fact_applied_review import _described_operand_direction
        cases = [
            ('Head device number storing number of matching items detected by search', 'output'),
            ('Head device number storing position of matching items detected by search', 'output'),
            ('Number of data entries to search', 'input'),
            ('Length of data stack', 'input'),
            ('Head device number storing hexadecimal code to be converted', 'input'),
            ('Head device number storing number of records to be searched', 'input'),
        ]
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(_described_operand_direction(description), expected)

    def test_bit_selection_and_toggle_use_shared_expression_model(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression
        for name, bits in [('BON', 16), ('BONP', 16), ('DBON', 32), ('DBONP', 32)]:
            groups = family_fact_groups(registry, name, cpu='FX3U')
            operands = next(f['value']['operands'] for f in groups if f['dimension'] == 'parameters.typed_operands')
            self.assertEqual(operands[-1]['range']['maximum'], bits - 1)
            self.assertEqual(operands[-1]['type']['bits'], 16)
            self.assertEqual(operands[-1]['word_device_layout']['word_count'], 1)
            self.assertEqual(operands[0]['type']['bits'], bits)
            self.assertEqual(operands[1]['type'], {'kind': 'bool'})
            expression = next(f['value']['rules'][0]['outputs'][0]['expression'] for f in groups
                              if f['dimension'] == 'effects.documented_processing')
            for value, position, expected in [(0, 0, False), (1, 0, True),
                    (1 << (bits - 1), bits - 1, True), ((1 << bits) - 1, bits - 2, True)]:
                self.assertIs(evaluate_expression(expression, {'S': value, 'N': position}), expected)
        for name in ('ALT', 'ALTP'):
            groups = family_fact_groups(registry, name, cpu='FX3U')
            expression = next(f['value']['rules'][0]['outputs'][0]['expression'] for f in groups
                              if f['dimension'] == 'effects.documented_processing')
            self.assertIs(evaluate_expression(expression, {'D': False}), True)
            self.assertIs(evaluate_expression(expression, {'D': True}), False)
        for name in ('DALT', 'DWSFR', 'DZRST'):
            self.assertFalse(family_fact_groups(registry, name, cpu='FX3U'))

    def test_reductions_and_ranges_keep_instruction_specific_differences(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        def behavior(name):
            groups = family_fact_groups(registry, name, cpu='FX3U')
            return next(f['value'] for f in groups if f['dimension'] == 'effects.documented_processing'), groups
        count, groups = behavior('DSUM')
        self.assertEqual(count['input_bits'], 32)
        self.assertEqual(count['double_word_result']['high_word'], 0)
        self.assertEqual(next(f['value']['operands'][-1]['word_device_layout']['word_count']
            for f in groups if f['dimension'] == 'parameters.typed_operands'), 2)
        mean, _ = behavior('DMEAN')
        self.assertEqual(mean['source_elements']['stride_words'], 2)
        self.assertEqual(mean['rounding'], 'truncate_toward_zero')
        reset, _ = behavior('ZRST')
        self.assertEqual(reset['when_first_exceeds_last'], {'clear_first_only': 'D1'})
        self.assertEqual(reset['output'], {'first': 'D1', 'last': 'D2', 'inclusive': True})
        right, right_groups = behavior('WSFR')
        left, left_groups = behavior('WSFL')
        from plc.instruction_definition import evaluate_expression
        self.assertEqual(evaluate_expression(right['insert_at_offset'], {'N1': 9, 'N2': 3}), 6)
        self.assertEqual(right['retained_elements']['source']['head'], 'D')
        self.assertEqual(right['inserted_elements']['source']['head'], 'S')
        self.assertEqual(left['insert_at_offset'], 0)
        for groups in (right_groups, left_groups):
            self.assertTrue(any(f['status']=='unknown' and f['value'].get('full_source_relation_complete') is False
                                for f in groups))

    def test_core_candidate_unknowns_are_not_programming_knowledge(self):
        from application.capability_context import _candidate_brief
        from knowledge.capability_discovery import _instruction_card
        candidate = {'kind': 'instruction', 'name': 'SYNTHETIC', 'purpose': 'synthetic',
                     'interface': [{'name': 'S', 'type': 'any'}], 'execution': None,
                     'unknown': ['operand_types'], 'functions': ['explicit']}
        brief = _candidate_brief(candidate, 'ladder')
        self.assertEqual(brief['kind'], 'instruction_capability')
        self.assertEqual(brief['core_unknown'], ['operand_types'])
        self.assertEqual(brief['core_interface'], candidate['interface'])
        self.assertNotIn('unknown', brief)
        self.assertEqual(_instruction_card('FLT', 'FX3U', {})['purpose'], 'FLT')

    def test_parameter_derivation_rejects_conflicting_project_interfaces(self):
        from plc.instruction_knowledge import parameter_usage_facts, compose_programming_facts
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        signature = fact('signature', 'parameters.typed_operands',
            value={'operands': [{'name': 'S', 'type': 'INT', 'direction': 'input'},
                                {'name': 'D', 'type': 'INT', 'direction': 'output'}]},
            scope={'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']})
        effect = fact('effect', 'effects.dataflow', value={'inputs': ['S'], 'output': 'D', 'operation': 'copy'},
                      scope=copy.deepcopy(signature['scope']))
        cached = parameter_usage_facts([signature, effect])
        self.assertEqual(len(cached), 1)
        signature['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        effect['conditions'] = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
        self.assertFalse(parameter_usage_facts([signature, effect]))
        current, superseded = compose_programming_facts(registry, 'PWM', cpu='FX5U',
            existing=[signature, effect, *cached])
        self.assertFalse(any(f['id'].startswith('assembly:parameters:') for f in current))
        self.assertIn(cached[0], superseded)

    def test_parameter_join_preserves_scopes_conditions_and_status(self):
        source = packet([
            fact('signature', 'parameters.typed_operands', value={'operands': [{'name': 'S', 'direction': 'input'}]},
                 scope={'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']}),
            fact('facet', 'parameters.1.purpose', value={'symbol': 'S', 'value': {'semantic_role': 'pulse_width'}},
                 scope={'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder'], 'versions': ['2.00']},
                 status='declared_unverified', conditions=[{'context': 'mode', 'equals': 2}]),
            fact('foreign', 'parameters.1.unit', value={'symbol': 'S', 'value': 'ms'}, scope={'languages': ['st']}),
        ])
        projected = technical_fact_packet(source)
        table = projected['value']['parameter_tables'][0]
        purpose = table['parameters'][0]['purpose']
        self.assertEqual(purpose['knowledge_status'], 'declared_unverified')
        self.assertEqual(purpose['scope']['versions'], ['2.00'])
        self.assertEqual(purpose['conditions'], [{'context': 'mode', 'equals': 2}])
        self.assertNotIn('unit', table['parameters'][0])
        self.assertIn('parameters.1.unit', str(projected['value']['additional_knowledge']))
        source['value']['facts'][0]['sources'][0]['conditions'] = [{'context': 'firmware', 'equals': '2.00'}]
        qualified = technical_fact_packet(source)['value']['parameter_tables'][0]
        self.assertEqual(qualified['applicability_conditions'], [[{'context': 'firmware', 'equals': '2.00'}]])

    def test_batch_mcp_and_model_use_same_family_view(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        from shared.model_context import technical_block, technical_context_units, technical_knowledge_results
        packets, records = [], []
        for name in ('ZCP', 'ZCPP', 'DZCP', 'DZCPP'):
            facts, _ = compose_programming_facts(registry, name, cpu='FX3G', existing=[])
            p = dict(kind='instruction', target=name, scope={'models': ['FX3G']}, content_origin='authored_fact',
                     value={'opcode': name, 'target_model': 'FX3G', 'facts': facts, 'call_available': True})
            context = {'model': 'FX3G', 'language': 'ladder', 'project_mode': 'native_ladder_instruction'}
            p['_knowledge_context'] = context
            packets.append(p)
            records.append(dict(row(p), id='unit-' + name, fact_target=name, _knowledge_context=context))
        model = technical_context_units('\n'.join(technical_block(technical_fact_packet(p)) for p in packets))
        mcp = technical_knowledge_results(records)
        delivered = []
        for record in mcp:
            delivered.extend(record['knowledge'])
        self.assertEqual(delivered, model)
        modules = [p for p in model if p['kind'] == 'instruction_modifiers']
        families = [p for p in model if p['kind'] == 'instruction']
        self.assertEqual(len(modules), 0)
        self.assertEqual(len(families), 1)
        self.assertEqual({f['opcode'] for f in families[0]['value']['forms']}, {'ZCP', 'ZCPP', 'DZCP', 'DZCPP'})
        self.assertEqual(len(families[0]['value']['parameter_tables']), 1)
        from knowledge.fact_coverage import included_evidence_ids
        from shared.model_context import technical_context_text, technical_knowledge_text
        descriptors = [dict(record, model_text=technical_knowledge_text(record)) for record in records]
        text = technical_context_text('\n'.join(record['model_text'] for record in descriptors))
        self.assertEqual(set(included_evidence_ids(text, descriptors)), {'unit-' + n for n in ('ZCP', 'ZCPP', 'DZCP', 'DZCPP')})
        broken = copy.deepcopy(model)
        next(unit for unit in broken if unit['kind'] == 'instruction')['value']['parameter_tables'][0]['parameters'][0].pop('purpose')
        text = '\n'.join(technical_block(unit) for unit in broken)
        self.assertFalse(included_evidence_ids(text, descriptors))

    def test_qualification_factoring_preserves_missing_and_unknown_fields(self):
        from knowledge.instruction_presentation import instruction_form_units, merge_instruction_units
        first = {'kind': 'instruction', 'target': 'A', 'scope': {'models': ['FX3G']},
                 'selection': {'partial_projection': True, 'unresolved_reasons': ['missing_dependency']},
                 'value': {'family': 'A', 'forms': [{'opcode': 'A'}], 'only_first': False}}
        second = {'kind': 'instruction', 'target': 'DA', 'scope': {'models': ['FX3G']},
                  'selection': {'partial_projection': False},
                  'value': {'family': 'A', 'forms': [{'opcode': 'DA'}], 'only_second': 0}}
        merged = merge_instruction_units([first, second])
        self.assertEqual(instruction_form_units(merged[0]), [first, second])
        self.assertEqual(merge_instruction_units(merged), merged)
        changed = copy.deepcopy(first)
        changed['selection']['unresolved_reasons'] = ['different_condition']
        self.assertEqual(len(merge_instruction_units([first, changed])), 2)

    def test_only_actual_relations_count_as_effects(self):
        from knowledge.instruction_presentation import substantive_effect
        for value in ({'covered_aspects': ['effects']}, {'rules': [{'kind': 'complete', 'covered_aspects': ['effects']}]},
                      {'knowledge_scope': 'operation_summary', 'operation': 'add'}, {'outputs': []},
                      {'rules': [{'kind': 'expression_write', 'outputs': [{'target': 'D'}]}]}):
            self.assertFalse(substantive_effect(fact('e', 'effects.result', value=value)))
        self.assertTrue(substantive_effect(fact('e', 'effects.result_composition', value={
            'combination': 'operation_and', 'operand_role': 'preceding_result', 'result_type': 'BOOL'})))

    def test_available_catalogue_marker_does_not_verify_applicability(self):
        from knowledge.structured_facts import retrieval_coverage
        p = packet([fact('availability', 'identity.applicability', value={'available': True}, status='unknown'),
                    fact('types', 'parameters.typed_operands', value={'operands': [{'name': 'S', 'type': 'INT'}]})])
        p['value']['call_available'] = True
        report = retrieval_coverage('PWM', [row(p)], plc_model='FX5U', language='ladder',
                                    aspects=['types'], targets={'instructions': ['PWM']})
        self.assertEqual(report['status'], 'incomplete')
        target = report['targets'][0]
        self.assertTrue(target['core_call_available'])
        self.assertNotEqual(next(a for a in target['aspects'] if a['aspect'] == 'applicability')['status'], 'answered')

    def test_zero_operand_purpose_is_not_a_missing_programming_slot(self):
        from knowledge.structured_facts import _aspect_state
        source = packet([fact('arity', 'parameters.arity', value={'minimum': 0, 'maximum': 0})])
        self.assertEqual(_aspect_state(source, 'purpose', model='FX5U', language='ladder', query='PWM'), 'not_applicable')
        source['value']['facts'][0]['status'] = 'unknown'
        self.assertNotEqual(_aspect_state(source, 'purpose', model='FX5U', language='ladder', query='PWM'), 'not_applicable')

    def test_declared_output_span_needs_all_results(self):
        from knowledge.structured_facts import _aspect_state
        signature = fact('parameters', 'parameters.typed_operands', value={'operands': [
            {'name': 'D', 'direction': 'output', 'type': {'kind': 'bool'}, 'occupied_bit_count': 3}]})
        effect = fact('effect', 'effects.documented_processing', value={'rules': [
            {'kind': 'conditional_results', 'outputs': [{'target': {'parameter': 'D', 'offset': 0, 'kind': 'bit'},
              'expression': {'op': 'constant', 'type': {'kind': 'bool'}, 'value': True}}]}]}, depends_on=['parameters'])
        p = packet([signature, effect])
        self.assertEqual(_aspect_state(p, 'effects', model='FX5U', language='ladder', query='PWM'), 'missing')
        output = effect['value']['rules'][0]['outputs'][0]
        effect['value']['rules'][0]['outputs'].extend([dict(copy.deepcopy(output), target=dict(output['target'], offset=index))
                                                     for index in (1, 2)])
        self.assertEqual(_aspect_state(p, 'effects', model='FX5U', language='ladder', query='PWM'), 'answered')
        effect['value']['full_source_relation_complete'] = False
        self.assertEqual(_aspect_state(p, 'effects', model='FX5U', language='ladder', query='PWM'), 'unresolved')
        effect['value'].pop('full_source_relation_complete')
        effect['value']['core_execution_capability'] = 'not_established'
        self.assertEqual(_aspect_state(p, 'effects', model='FX5U', language='ladder', query='PWM'), 'answered')

    def test_debug_packet_is_paged_without_a_monolithic_json_string(self):
        from agent_runtime.manual_tools import _search_plc_manual
        from agent_runtime.tool_context import ToolContext
        from application.read_delivery import ReadDeliveryStore, assemble_read_pages
        source = packet([fact('entry-' + str(index), 'effects.documented_processing',
                              value={'synthetic_relation': 'value_' * 160}) for index in range(80)])
        with patch('knowledge.retriever.retrieve_fact_aware_knowledge', return_value=[row(source)]):
            debug = _search_plc_manual(ToolContext({'plc_model': 'FX5U'}), {'query': 'PWM', 'debug': True})
        self.assertEqual(debug['results'][0]['fact_packet'], source)
        pager = ReadDeliveryStore()
        arguments = {'query': 'PWM', 'debug': True}
        pages = [pager.prepare({'ok': True, 'tool': 'search_plc_manual', 'data': debug}, arguments, {})]
        while not pages[-1]['data']['delivery']['complete']:
            followup = pages[-1]['data']['delivery']['next']
            pages.append(pager.resume(followup['tool'], followup['arguments'], {}))
        self.assertGreater(len(pages), 1)
        self.assertEqual(assemble_read_pages(pages)['results'][0]['fact_packet'], source)

    def test_original_case_retains_programming_scenario(self):
        source = {'kind': 'original_case', 'target': 'example', 'value': {
            'initial': {'D0': -4}, 'frames': [{'enable': True}], 'expected': {'M0': True},
            'limitations': ['one_scan_only'], 'manual_text': 'private original'}}
        result = technical_fact_packet(source)
        self.assertEqual(result['value'], {key: value for key, value in source['value'].items() if key != 'manual_text'})

    def test_factoring_roundtrip_for_generated_json_values(self):
        try:
            from hypothesis import given, settings, strategies as st
        except ImportError:
            self.skipTest('Hypothesis is optional for generated JSON roundtrip checks')
        from knowledge.instruction_presentation import factor_form_values, resolve_form_value, _merge_components
        scalar = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=12)
        values = st.recursive(scalar, lambda child: st.lists(child, max_size=4)
                              | st.dictionaries(st.text(alphabet='abcxyz', min_size=1, max_size=4), child, max_size=4), max_leaves=20)
        @settings(max_examples=200, deadline=None, database=None)
        @given(st.lists(values, min_size=1, max_size=4))
        def check(items):
            entries = [([str(index)], item) for index, item in enumerate(items)]
            factored = factor_form_values(entries)
            for forms, item in entries:
                self.assertEqual(json.dumps(resolve_form_value(factored, forms[0]), sort_keys=True),
                                 json.dumps(item, sort_keys=True))
            self.assertEqual(factor_form_values([([str(index)], resolve_form_value(factored, str(index)))
                                                for index in range(len(items))]), factored)
        check()
        components = [{'forms': ['A'], 'semantics': {'v': False}},
                      {'forms': ['A', 'B'], 'semantics': {'v': False}},
                      {'forms': ['C'], 'semantics': {'v': 0}}]
        merged = _merge_components(components, default_forms=['A', 'B', 'C'])
        self.assertEqual(merged[0]['forms'], ['A', 'B', 'C'])
        self.assertIs(resolve_form_value(merged[0]['semantics'], 'B')['v'], False)
        self.assertIs(type(resolve_form_value(merged[0]['semantics'], 'C')['v']), int)


class NativeClockPulseBindingTests(unittest.TestCase):
    def fixture(self, *, foreign_catalogue=False):
        from plc.instructions import InstructionRegistry, InstructionSpec
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        minimum = {'context': 'cpu_version', 'minimum': '2.20'}
        hardware = {'context': 'synthetic_hardware_condition', 'equals': True}
        proof = {'id': 'family:SYNTH:own_native_forms', 'dimension': 'parameters.variant_family',
            'scope': {'models': ['FX3U'], 'forms': ['SYNTHP'], 'languages': ['ladder']},
            'value': {'base_opcode': 'SYNTH', 'section_variants': ['SYNTH', 'SYNTHP'],
                'variant_bindings': {'SYNTH': {'double': False, 'pulse': False, 'data_bits': 16},
                                     'SYNTHP': {'double': False, 'pulse': True, 'data_bits': 16}}},
            'status': 'source_verified', 'verification': 'source_checked', 'conditions': [native, minimum, hardware],
            'sources': [{'manual_id': 'synthetic_native', 'revision': 'R', 'pdf_pages': [7]}]}
        structured = copy.deepcopy(proof); structured['id'] = 'old:structured:forms'
        structured['scope']['languages'] = ['ladder', 'fbd', 'st']; structured['conditions'] = []
        structured['value'] = {'section_variants': ['SYNTH', 'SYNTHP']}
        structured['sources'] = [{'manual_id': 'synthetic_structured', 'revision': 'M', 'pdf_pages': [9]}]
        registry = InstructionRegistry([InstructionSpec('SYNTHP', cpu_support=frozenset({'FX1N'}))] if foreign_catalogue else [])
        registry._family_knowledge = [{'base_opcode': 'SYNTH', 'vendor': 'mitsubishi', 'facts': [proof]}]
        registry._knowledge_profiles = [{'id': 'synthetic:pulse-profile', 'vendor': 'mitsubishi', 'models': ['FX3U'],
            'modifiers': {}, 'sources': [{'manual_id': 'synthetic_pulse', 'revision': 'P', 'pdf_pages': [1]}]}]
        return registry, proof, structured

    def test_foreign_CPU_catalogue_does_not_override_reviewed_native_identity(self):
        from plc.instruction_knowledge import _knowledge_form
        registry, proof, structured = self.fixture(foreign_catalogue=True)
        original = copy.deepcopy(structured)
        actual = _knowledge_form(registry, 'SYNTHP', cpu='FX3U', facts=[structured])
        self.assertTrue(actual['programming_binding'])
        self.assertEqual((actual['family'], actual['pulse'], actual['data_bits']), ('SYNTH', True, 16))
        self.assertEqual(actual['conditions'], proof['conditions'])
        foreign = registry.resolve_catalog_form('SYNTHP', cpu='FX1N')
        self.assertTrue(foreign.spec.supports_cpu('FX1N'))
        self.assertFalse(foreign.spec.supports_cpu('FX3U'))
        self.assertEqual(structured, original)

    def test_modifier_uses_its_qualified_identity_proof_not_unqualified_structured_row(self):
        from plc.instruction_knowledge import compose_programming_facts
        registry, proof, structured = self.fixture()
        rows, _ = compose_programming_facts(registry, 'SYNTHP', cpu='FX3U', existing=[structured])
        triggers = [f for f in rows if f['dimension'] == 'execution.trigger']
        self.assertTrue(triggers)
        for trigger in triggers:
            self.assertEqual(trigger['scope'], proof['scope'])
            self.assertEqual(trigger['conditions'], proof['conditions'])
            self.assertEqual(trigger['depends_on'], [proof['id']])
        self.assertIn(structured, rows)
        self.assertFalse(any(f['dimension'] == 'execution.disabled_state' for f in rows))

    def test_true_overlapping_identity_conflict_and_distinct_binding_scopes_are_retained(self):
        from plc.instruction_knowledge import _knowledge_form
        registry, proof, _ = self.fixture()
        opposing = copy.deepcopy(proof); opposing['id'] = 'conflicting:own-native'
        opposing['value']['variant_bindings']['SYNTHP']['pulse'] = False
        registry._family_knowledge[0]['facts'].append(opposing)
        actual = _knowledge_form(registry, 'SYNTHP', cpu='FX3U')
        self.assertFalse(actual['programming_binding'])
        self.assertEqual(actual['binding_status'], 'unresolved_source_bindings')
        opposing['value']['variant_bindings']['SYNTHP']['pulse'] = True
        opposing['scope']['languages'] = ['st']
        opposing['conditions'] = [{'context': 'project_mode', 'equals': 'structured_project'}]
        actual = _knowledge_form(registry, 'SYNTHP', cpu='FX3U')
        self.assertTrue(actual['programming_binding'])
        self.assertEqual(actual['binding_scopes'], [
            {'languages': ['ladder'], 'conditions': proof['conditions']},
            {'languages': ['st'], 'conditions': opposing['conditions']}])


class ReviewedStandalonePulseIdentityTests(unittest.TestCase):
    def test_six_reviewed_CPU_groups_keep_R_identity_without_turning_M_lists_into_false_inverse_proof(self):
        from plc.instructions import InstructionRegistry
        from plc.instruction_knowledge import _knowledge_form
        groups=[['FX3S'],['FX3G','FX3GC'],['FX3U','FX3UC']]
        registry=InstructionRegistry();contracts=[];all_models=sum(groups,[]);facts={}
        for opcode in ['FETCH_EDGE','SET_EDGE']:
            for models in groups:
                contracts.append({'method':'reviewed-manual-contract-v1','cpus':models,
                    'instruction':{'mnemonic':opcode,'cpu_support':models,'category':'action','semantic_kind':'vendor',
                        'contract_level':'signature','arity':{'min':1,'max':1},
                        'operands':[{'name':'record','role':'read','data_type':'word','optional':False,'word_count':7}]},
                    'native_order':['S'],'execution_form':'pulse','verified_fields':['arity','operand_order','execution_form'],
                    'sources':[{'models':models,'manual_id':'synthetic-native','manual':'SYNTH','revision':'A',
                        'pdf_page':1,'url':'https://example.invalid/source'}]})
            _,proof,structured=NativeClockPulseBindingTests().fixture()
            base='BASE_'+opcode
            proof['id']='own:'+opcode;proof['scope']['models']=all_models;proof['scope']['forms']=[opcode]
            proof['value']['base_opcode']=base;proof['value']['section_variants']=[base,opcode]
            proof['value']['variant_bindings']={base:{'double':False,'pulse':False,'data_bits':16},
                                                opcode:{'double':False,'pulse':True,'data_bits':16}}
            structured['id']='structured:'+opcode;structured['scope']['models']=all_models;structured['scope']['forms']=[opcode]
            structured['value']={'section_variants':[base,opcode]}
            registry._family_knowledge=(*registry._family_knowledge,{'base_opcode':base,'vendor':'mitsubishi','facts':[proof]})
            facts[opcode]=structured
        registry.load_target_contracts(contracts)
        for opcode in facts:
            self.assertFalse(opcode.endswith('P'))  # No suffix heuristic.
            for model in all_models:
                with self.subTest(opcode=opcode,model=model):
                    actual=_knowledge_form(registry,opcode,cpu=model,facts=[facts[opcode]])
                    self.assertTrue(actual['programming_binding'])
                    self.assertEqual(actual['family'],'BASE_'+opcode)
                    self.assertTrue(actual['pulse'])
                    self.assertEqual(actual['_proof_ids'],['own:'+opcode])

    def test_only_M_list_is_left_unresolved_without_trusting_unreviewed_pulse_labels(self):
        from dataclasses import replace
        from plc.instructions import InstructionRegistry,InstructionSpec
        from plc.instruction_knowledge import _knowledge_form
        _,_,structured=NativeClockPulseBindingTests().fixture()
        reviewed=InstructionSpec('SYNTHP',cpu_support=frozenset({'FX3U'}),execution_form='pulse',verified_fields=('execution_form',))
        registry=InstructionRegistry([reviewed])
        actual=_knowledge_form(registry,'SYNTHP',cpu='FX3U',facts=[structured])
        self.assertFalse(actual['programming_binding'])
        self.assertEqual(structured['value'],{'section_variants':['SYNTH','SYNTHP']})
        # An unreviewed label cannot activate the new rejection predicate.
        unreviewed=InstructionRegistry([replace(reviewed,verified_fields=())])
        self.assertTrue(_knowledge_form(unreviewed,'SYNTHP',cpu='FX3U',facts=[structured])['programming_binding'])
        continuous=InstructionRegistry([replace(reviewed,execution_form='continuous')])
        self.assertTrue(_knowledge_form(continuous,'SYNTHP',cpu='FX3U',facts=[structured])['programming_binding'])

    def test_true_catalogue_modifier_and_explicit_conflict_keep_their_meaning(self):
        from plc.instructions import InstructionRegistry,InstructionSpec
        from plc.instruction_knowledge import _knowledge_form
        registry,proof,structured=NativeClockPulseBindingTests().fixture()
        registry.register(InstructionSpec('SYNTH',cpu_support=frozenset({'FX3U'}),supports_pulse=True))
        self.assertTrue(registry.resolve_catalog_form('SYNTHP',cpu='FX3U').pulse)
        actual=_knowledge_form(registry,'SYNTHP',cpu='FX3U',facts=[structured])
        self.assertTrue(actual['programming_binding']);self.assertTrue(actual['pulse'])
        opposing=copy.deepcopy(proof);opposing['id']='opposing:explicit'
        opposing['value']['variant_bindings']['SYNTHP']['pulse']=False
        actual=_knowledge_form(registry,'SYNTHP',cpu='FX3U',facts=[opposing])
        self.assertFalse(actual['programming_binding'])
        self.assertEqual(actual['binding_status'],'unresolved_source_bindings')


class LegacyInstructionIdentityBoundaryTests(unittest.TestCase):
    def test_device_and_contact_labels_are_not_compiled_as_instruction_owners(self):
        import sqlite3
        from tools.fact_applied_review import _chunk_records, _instruction_records, _legacy_source_records

        source = sqlite3.connect(':memory:')
        source.row_factory = sqlite3.Row
        self.addCleanup(source.close)
        source.executescript('''
            CREATE TABLE manuals (manual_id TEXT, plc_models TEXT);
            CREATE TABLE chunks (
                id INTEGER, manual_id TEXT, section TEXT, pdf_page INTEGER,
                source_pages_json TEXT, text TEXT, source_applicability_json TEXT,
                chapter TEXT, outline_path TEXT, pdf_page_end INTEGER, block_index INTEGER,
                manual_number TEXT, revision TEXT
            );
            CREATE TABLE instructions (
                id INTEGER, opcode TEXT, title TEXT, manual_id TEXT,
                manual_number TEXT, revision TEXT, source_pages_json TEXT,
                operands_json TEXT, chunk_id INTEGER
            );
        ''')
        source.execute('INSERT INTO manuals VALUES (?,?)',
                       ('jy992d88101e', json.dumps(['FX1N'])))
        marks = json.dumps({'model_marks': [{'model': 'FX1N', 'state': 'supported'}]})
        rows = [
            (1, '4.11 High Speed Counters', 94,
             'Device Mnemonic: C\nAvailable forms: A driven coil sets internal PLC contacts.'),
            (2, '5.1 Contact Instructions', 135,
             'AND uses an A contact in series; A and B name contact categories.'),
            (3, '1.1 Q / Explicit Instruction', 136,
             'Q is a separately declared instruction form.'),
            (4, '5.1 A / Contact Details', 137,
             'A contact is a contact-category label, not a callable mnemonic.'),
            (5, '5.2 A / Instruction Details', 138,
             'Instruction A is described here; the text also mentions a contact in a separate example.'),
            (6, '5.3 A', 139,
             'This is the A instruction. Other device Device Mnemonic: C has its own contact and wiring notes.'),
        ]
        for chunk_id, section, page, text in rows:
            source.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (chunk_id, 'jy992d88101e', section, page, json.dumps([page]), text, marks,
                 'Applied Instructions', section, page, 0, 'JY992D88101', 'E'))
        for chunk_id, opcode in [(1, 'A'), (2, 'A'), (2, 'AND'), (3, 'Q'), (5, 'A'), (6, 'A')]:
            section, page, _ = next(row[1:] for row in rows if row[0] == chunk_id)
            source.execute('INSERT INTO instructions VALUES (?,?,?,?,?,?,?,?,?)',
                (chunk_id * 10 + len(opcode), opcode, opcode, 'jy992d88101e',
                 'JY992D88101', 'E', json.dumps([page]), '[]', chunk_id))
        source.commit()

        records = list(_instruction_records(source, ['jy992d88101e']))

        self.assertEqual({record['opcode'] for record in records}, {'A', 'AND', 'Q'})
        self.assertEqual({record['section'] for record in records if record['opcode'] == 'A'},
                         {'5.2 A / Instruction Details', '5.3 A'})
        self.assertEqual(next(record for record in records if record['opcode'] == 'AND')['pdf_pages'], [135])
        # A single-character name remains eligible when its source heading
        # declares an instruction rather than a device/contact category.
        self.assertEqual(next(record for record in records if record['opcode'] == 'Q')['section'],
                         '1.1 Q / Explicit Instruction')

        chunk_records = list(_chunk_records(source, {'jy992d88101e'}, set()))
        self.assertEqual({record['opcode'] for record in chunk_records}, {'A', 'Q'})
        self.assertEqual({record['section'] for record in chunk_records if record['opcode'] == 'A'},
                         {'5.2 A / Instruction Details', '5.3 A'})

        class LegacySource:
            def execute(self, *args, **kwargs):
                return source.execute(*args, **kwargs)

            def legacy_page(self, manual, page):
                if page != 94:
                    return None
                return {'legacy_prefix_text': '', 'legacy_continues': False,
                    'legacy_sections': [{'section': '4.11 High Speed Counters', 'opcode': 'A',
                        'text': 'Device Mnemonic: C. A driven coil is one described form.',
                        'model_marks': [], 'form_models': {}}]}

        original = {'id': 1, 'manual_id': 'jy992d88101e', 'revision': 'E',
                    'section': '4.11 High Speed Counters', 'opcode': 'A', 'pdf_pages': [94]}
        self.assertEqual(_legacy_source_records(LegacySource(), [original]), [])


class NativeCatalogueScopeProducerTests(unittest.TestCase):
    def test_catalogue_retains_checked_own_native_header_minimum(self):
        from tools.fact_instruction_catalogue import catalogue_availability_facts
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        ref = {'manual_id': 'fx3_programming_r', 'manual_number': 'JY997D16601', 'revision': 'R', 'pdf_pages': [578]}
        table_ref = dict(ref, pdf_pages=[75, 81])
        note = {'kind': 'function_change', 'models': ['FX3U'], 'version': '2.40', 'footnote': '9'}
        catalogue = {('fx3_programming_r', 'SYNTH'): {'states': {'FX3U': {
            'supported': True, 'qualifications': [note]}}, 'sources': [table_ref]}}
        header = {'id': 'authored:applied:source_cpu_header@fx3_programming_r',
            'dimension': 'constraints.source_cpu_header', 'status': 'source_verified', 'verification': 'source_checked',
            'scope': {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['ladder']},
            'conditions': [native], 'sources': [ref], 'depends_on': ['reviewed:header:context'],
            'value': {'model': 'FX3U', 'state': 'conditional', 'minimum_version': '2.20',
                      'basis': 'reviewed_own_instruction_header_cpu_version_badges'}}
        cases = ['checked', 'hardware', 'foreign_manual', 'wrong_revision', 'wrong_model', 'wrong_form',
                 'structured', 'missing_mode', 'unreviewed', 'unchecked', 'unknown_version', 'missing_header']
        for case in cases:
            with self.subTest(case=case):
                current = copy.deepcopy(header)
                if case == 'foreign_manual': current['sources'][0]['manual_id'] = 'other_manual'
                elif case == 'wrong_revision': current['sources'][0]['revision'] = 'S'
                elif case == 'wrong_model': current['scope']['models'] = ['FX3S']
                elif case == 'wrong_form': current['scope']['forms'] = ['OTHER']
                elif case == 'structured': current['conditions'] = [{'context': 'project_mode', 'equals': 'structured_project'}]
                elif case == 'missing_mode': current['conditions'] = []
                elif case == 'hardware': current['conditions'].append({'context': 'synthetic_hardware_condition', 'equals': True})
                elif case == 'unreviewed': current['status'] = 'declared_unverified'
                elif case == 'unchecked': current['verification'] = 'not_checked'
                elif case == 'unknown_version': current['value'].pop('minimum_version')
                facts = [] if case == 'missing_header' else [current]
                if case in {'checked', 'hardware'}:
                    version_fact = {'id': 'reviewed:cpu-version',
                        'dimension': 'constraints.cpu_version',
                        'value': {'models': ['FX3U'], 'minimum_series_version': 'Ver.2.20',
                                  'basis': 'reviewed_own_instruction_header_cpu_version_badges'},
                        'status': 'source_verified', 'verification': 'source_checked',
                        'scope': {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['ladder']},
                        'sources': [ref], 'depends_on': [current['id']]}
                    facts.append(version_fact)
                original = copy.deepcopy([facts, catalogue])
                result, = catalogue_availability_facts('SYNTH', facts, 'FX3U', catalogue)
                minimum = {'context': 'cpu_version', 'minimum': '2.20'}
                self.assertEqual(minimum in result['conditions'], case in {'checked', 'hardware'})
                self.assertEqual(result['value']['model_states'], {'FX3U': 'conditional'})
                self.assertNotIn('available', result['value'])
                self.assertIn(note, result['value']['qualifications'])
                self.assertIn(note, result['value']['unresolved_items'])
                if case in {'checked', 'hardware'}:
                    self.assertIn(ref, result['sources'])
                    self.assertIn(header['id'], result['depends_on'])
                    self.assertIn('reviewed:cpu-version', result['depends_on'])
                    self.assertEqual(sum(c == minimum for c in result['conditions']), 1)
                    self.assertIn('reviewed:header:context', result['depends_on'])
                    self.assertTrue(all(condition in result['conditions'] for condition in current['conditions']))
                self.assertEqual([facts, catalogue], original)

    def test_catalogue_cell_cannot_override_unreviewed_or_versioned_own_header(self):
        import sqlite3
        from tools import fact_applied_review as review
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.row_factory = sqlite3.Row
        source.execute('CREATE TABLE instructions (opcode)')
        source.execute("INSERT INTO instructions VALUES ('SYNTH')")
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('fx3_programming_r','FX3U')")
        source.execute('CREATE TABLE chunks (manual_id,document_plc_models)')
        reference = {'manual_id': 'fx3_programming_r', 'manual_number': 'JY997D16601',
                     'revision': 'R', 'pdf_pages': [3]}
        positive = {'states': {'FX3U': {'supported': True, 'qualifications': []}}, 'sources': [reference]}
        hardware = {'context': 'synthetic_hardware_condition', 'equals': True}
        variant = {'opcode': 'SYNTH', 'width_bits': 16, 'execution_form': 'continuous', 'call_form': None}
        cases = [
            ('unreviewed-own-header', positive, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('no-table', None, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('missing-cell', {'states': {}, 'sources': [reference]}, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('unread-mark', {'states': {'FX3U': {'qualifications': []}}, 'sources': [reference]}, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('version-footnote', {'states': {'FX3U': {'supported': True, 'qualifications': [{'kind': 'minimum_version', 'version': '2.20'}]}}, 'sources': [reference]}, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('missing-qualifications', {'states': {'FX3U': {'supported': True}}, 'sources': [reference]}, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('foreign-manual', positive, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('other-opcode', positive, None, 'ANY16', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('unsupported-cell', {'states': {'FX3U': {'supported': False, 'qualifications': []}}, 'sources': [reference]}, None, 'ANY16', None, None),
            ('unknown-header', positive, {'basis': 'instruction_header', 'model_marks': [{'model': 'FX3U', 'state': 'conditional'}]}, 'ANY16', 'declared_unverified', 'cpu_version_not_resolved'),
            ('version-header', positive, {'basis': 'instruction_header', 'model_marks': [{'model': 'FX3U', 'state': 'conditional', 'minimum_version': '2.20'}]}, 'ANY16', 'source_verified', 'cpu_version'),
            ('unknown-type', positive, None, 'unknown', 'declared_unverified', 'cpu_scope_only_from_manual_catalogue'),
            ('reviewed-version-header', positive, None, 'ANY16', 'source_verified', 'cpu_version'),
            ('reviewed-version-unknown-type', positive, None, 'unknown', 'declared_unverified', 'cpu_version'),
        ]
        for name, claim, header, kind, status, guard in cases:
            with self.subTest(case=name):
                record = {'id': 'synthetic-native-section', 'opcode': 'SYNTH', 'title': 'SYNTH',
                    'manual_id': 'fx3_programming_r', 'manual_number': 'JY997D16601', 'revision': 'R',
                    'chapter': 'Applied Instructions', 'section': '7.1 FNC 12 - SYNTH / Example',
                    'pdf_pages': [7], 'manual_models': ['FX3U'], 'manual_scope_basis': 'manual_catalogue',
                    'applicability': header or {'basis': 'manual_catalogue'},
                    'text': '1. 16-bit operation (SYNTH)', 'declared_operands': [], 'conditions': [hardware]}
                key = ('another_manual' if name == 'foreign-manual' else 'fx3_programming_r',
                       'OTHER' if name == 'other-opcode' else 'SYNTH')
                catalogue = {key: copy.deepcopy(claim)} if claim else {}
                header_reviews = []
                if name.startswith('reviewed-version'):
                    header_reviews = [{**{k: record[k] for k in ['manual_id', 'manual_number', 'revision', 'section']},
                        'pdf_page': 7, 'status': 'source_verified', 'verification': 'source_checked',
                        'model_marks': [{'model': 'FX3U', 'state': 'conditional', 'minimum_version': '2.20'}],
                        'source': {**{k: record[k] for k in ['manual_id', 'manual_number', 'revision']},
                            'pdf_pages': [7], 'representation': 'instruction_header_cpu_version_badges',
                            'review_artifact': 'synthetic-reviewed-native.png'}}]
                original = copy.deepcopy([record, catalogue])
                with ExitStack() as stack:
                    for function, result in [('_instruction_records', [record]), ('_chunk_records', []),
                            ('_source_table_groups', []), ('_parameter_source_pages', []),
                            ('_native_parameter_conventions', {}), ('_page_prose', ''),
                            ('_programming_manual_variants', [variant]),
                            ('_native_set_data_rows', [{'name': 'S', 'type': kind, 'direction': 'input'}]),
                            ('_native_diagram_order', ['S'])]:
                        stack.enter_context(patch.object(review, function, return_value=result))
                    facts, _ = review.reviewed_applied_behavior(source, manuals=['fx3_programming_r'],
                        page_text={}, page_tables={}, cpu_catalogue=catalogue, page_header_reviews=header_reviews)
                typed = [f for f in facts.get(('FX3U', 'SYNTH'), []) if f['dimension'] == 'parameters.typed_operands']
                if status is None:
                    self.assertFalse(typed)
                    continue
                self.assertEqual(len(typed), 1)
                current = typed[0]
                self.assertEqual(current['status'], status)
                self.assertEqual(current['value']['operands'][0]['type'], kind)
                self.assertEqual(current['value']['operands'][0]['direction'], 'input')
                self.assertIn(hardware, current.get('conditions', []))
                contexts = {c['context'] for c in current.get('conditions', [])}
                self.assertEqual(contexts - {hardware['context']}, {guard} if guard else set())
                self.assertNotIn(reference, current['sources'])
                if name.startswith('reviewed-version'):
                    self.assertIn({'context': 'cpu_version', 'minimum': '2.20'}, current['conditions'])
                    positive_header = [f for f in facts['FX3U', 'SYNTH'] if f['dimension']=='constraints.cpu_availability'
                                       and f['value'].get('available') is True]
                    negative_header = [f for f in facts['FX3U', 'SYNTH'] if f['dimension']=='constraints.cpu_availability'
                                       and f['value'].get('available') is False]
                    self.assertTrue(any({'context': 'cpu_version', 'minimum': '2.20'} in f.get('conditions', []) for f in positive_header))
                    self.assertTrue(any({'context': 'cpu_version', 'maximum_exclusive': '2.20'} in f.get('conditions', []) for f in negative_header))
                    self.assertTrue(all({'context': 'project_mode', 'equals': 'native_ladder_instruction'}
                                        in f.get('conditions', []) for f in [*positive_header, *negative_header]))
                self.assertEqual([record, catalogue], original)


class SignatureBindingTests(unittest.TestCase):
    def test_selected_owner_recompilation_copies_other_owners_without_reauthoring_them(self):
        from pathlib import Path
        import tempfile
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        from scripts.compile_instruction_semantics import compile_semantics
        raw = fixture()
        untouched = raw['entries'][1]
        untouched['superseded_facts'] = [copy.deepcopy(untouched['facts'][0])]
        selected = raw['entries'][0]
        addition = fact('selected_source', 'memory.synthetic', value={'input': 'synthetic'},
            scope={'models': [selected['target_model']], 'forms': [selected['opcode']]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = OwnerStoreWriter(root / 'input.sqlite', {k: v for k, v in raw.items() if k != 'entries'})
            for entry in raw['entries']:
                writer.add_entry(entry)
            manifest = root / 'instruction_definitions.manifest.json'
            writer.finalize(manifest)
            result = compile_semantics(manifest, root / 'output', version='synthetic:selected-owners',
                source_batches={(selected['target_model'], selected['opcode']): [addition]},
                source_owners_only=True, refresh_reviewed_calls=True)
            output = OwnerDefinitionStore(result['manifest'])
            try:
                self.assertEqual(output.get_entry(untouched['opcode'], cpu=untouched['target_model']), untouched)
                self.assertEqual(result['unchanged_owners_retained'], 1)
                self.assertTrue(result['incremental_seeded'])
                self.assertEqual(result['programming_owners_recompiled'], 1)
                self.assertNotIn('reviewed_call_contracts_refreshed', result)
            finally:
                output.close()

    def test_v4_recompilation_preserves_owners_admission_and_languages(self):
        from pathlib import Path
        import tempfile
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        from scripts.compile_instruction_semantics import compile_semantics
        raw = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = OwnerStoreWriter(root / 'input.sqlite', {k: v for k, v in raw.items() if k != 'entries'})
            for entry in raw['entries']:
                writer.add_entry(entry)
            for feature in raw.get('language_facts', []):
                writer.add_language_feature(feature)
            manifest = root / 'instruction_definitions.manifest.json'
            writer.finalize(manifest)
            owner = raw['entries'][0]
            addition = fact('source-batch', 'memory.synthetic_semantics', value={'state_owner': 'synthetic'},
                scope={'models': [owner['target_model']], 'forms': [owner['opcode']]})
            result = compile_semantics(manifest, root / 'output', version='synthetic:programming-semantics',
                source_batches={(owner['target_model'], owner['opcode']): [addition]})
            output = OwnerDefinitionStore(result['manifest'])
            try:
                self.assertEqual(result['owners'], len(raw['entries']))
                self.assertEqual(result['source_recompiled_owners'], 1)
                self.assertIn('source-batch', {f['id'] for f in output.get_entry(owner['opcode'], cpu=owner['target_model'])['facts']})
                self.assertEqual(output.language_features(), raw.get('language_facts', []))
                for original in raw['entries']:
                    current = output.get_entry(original['opcode'], cpu=original['target_model'])
                    for key in original:
                        if key not in {'facts', 'superseded_facts'}:
                            self.assertEqual(current[key], original[key])
                    retained = {f['id'] for f in current['facts'] + current.get('superseded_facts', [])}
                    self.assertTrue({f['id'] for f in original['facts']} <= retained)
            finally:
                output.close()

    def test_source_only_owners_are_retrievable_without_new_call_admission(self):
        from pathlib import Path
        import tempfile
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        from scripts.compile_instruction_semantics import compile_semantics
        raw = fixture()
        source = fact('new-source', 'effects.synthetic', scope={'models': ['FX5U'], 'forms': ['SYNTH_FN']})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = OwnerStoreWriter(root / 'input.sqlite', {k: v for k, v in raw.items() if k != 'entries'})
            for entry in raw['entries']:
                writer.add_entry(entry)
            manifest = root / 'instruction_definitions.manifest.json'
            writer.finalize(manifest)
            result = compile_semantics(manifest, root / 'output', version='synthetic:source-knowledge',
                        source_batches={('FX5U', 'SYNTH_FN'): [source]})
            store = OwnerDefinitionStore(result['manifest'])
            try:
                self.assertEqual(result['source_owners_added'], 1)
                self.assertEqual(result['owners'], len(raw['entries']) + 1)
                entry = store.get_entry('SYNTH_FN', cpu='FX5U')
                self.assertIs(entry['call_available'], False)
                self.assertFalse(entry['registry_member'])
                self.assertEqual(entry['support_status'], 'definition_only')
                self.assertEqual(entry['facts'], [source])
                for original in raw['entries']:
                    current = store.get_entry(original['opcode'], cpu=original['target_model'])
                    self.assertEqual(current, original)
            finally:
                store.close()

    def test_statement_and_block_parameters_keep_their_own_meaning(self):
        from tools.fact_applied_review import _signature_from_form, _structured_parameter_facts
        rows = {
            'EN': {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'description': 'Execution condition'},
            'VALUE': {'name': 'value', 'type': 'DINT', 'direction': 'input', 'description': 'Value selected by caller'},
            'RESULT': {'name': 'result', 'type': {'kind': 'array', 'element_type': 'BOOL', 'index_min': 0, 'index_max': 2},
                       'direction': 'output', 'description': 'Three result indicators'},
            'ENO': {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'description': 'Execution state'},
        }
        pins = [{key: row[key] for key in ('name', 'direction')} for row in rows.values()]
        record = {'id': 'synthetic', 'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'SYNTH',
                  'revision': 'A', 'pdf_pages': [1], 'structured_parameter_rows': {'SYNTH': rows},
                  'structured_parameter_pins': {'SYNTH': pins}}
        signature, unresolved = _signature_from_form(record, 'SYNTH', 'SYNTH(EN,value,result)', {}, {})
        self.assertFalse(unresolved)
        self.assertEqual(signature['parameters'][1]['purpose'], {'source_description': 'Value selected by caller'})
        self.assertTrue(signature['parameters'][0]['enable_pin'])
        self.assertEqual(signature['execution_output']['role'], 'execution_status')
        facts = _structured_parameter_facts(record, {'opcode': 'SYNTH'}, ['FX5U'])
        typed = next(f for f in facts if f['dimension'] == 'parameters.typed_operands' and f['scope']['languages'] == ['fbd'])
        expected_purposes = [
            {'role': 'execution_condition'}, {'source_description': 'Value selected by caller'},
            {'source_description': 'Three result indicators'}, {'role': 'execution_status'}]
        self.assertEqual([p['purpose'] for p in typed['value']['operands']], expected_purposes)
        self.assertEqual([p['name'] for p in typed['value']['operands'] if p.get('enable_pin')], ['EN', 'ENO'])
        source = {'kind': 'instruction', 'target': 'SYNTH', 'scope': {'models': ['FX5U']},
                  'value': {'opcode': 'SYNTH', 'target_model': 'FX5U', 'facts': facts}}
        for language, mode in [('fbd', None), ('ladder', 'structured_ladder_block')]:
            selected = project_packet(source, plc_model='FX5U', language=language,
                         aspects=['programming_semantics'], project_mode=mode)
            tables = technical_fact_packet(selected)['value']['parameter_tables']
            self.assertTrue(any([p.get('purpose') for p in table['parameters']] == expected_purposes for table in tables))
        self.assertEqual(rows['VALUE']['description'], 'Value selected by caller')

    def test_parameter_meaning_does_not_invent_missing_roles_or_override_types(self):
        from tools.fact_applied_review import _parameter_meaning
        self.assertEqual(_parameter_meaning({'name': 'S1', 'type': 'DINT', 'direction': 'input'}), {})
        self.assertNotIn('role', _parameter_meaning({'type': 'INT', 'direction': 'input', 'description': 'Execution condition'}))
        ordinary = _parameter_meaning({'type': 'BOOL', 'direction': 'input', 'description': 'Input data'})
        self.assertNotIn('enable_pin', ordinary)
        self.assertEqual(ordinary['purpose']['source_description'], 'Input data')

    def test_storage_head_runtime_purposes_exclude_private_original_evidence(self):
        from tools.fact_applied_review import _parameter_purpose
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        for direction, description, role in [
                ('input', 'The writing source of sampled values and the head device are specified (3 points occupied).', 'source_storage_head'),
                ('output', 'Specifies the head device number storing the generated record.', 'result_storage_head')]:
            with self.subTest(direction=direction):
                row = {'type': 'ANY16', 'direction': direction, 'description': description}
                original = copy.deepcopy(row)
                runtime = {'value': {'operands': [{'purpose': _parameter_purpose(row)}]}}
                OwnerStoreWriter._public(runtime)
                self.assertEqual(runtime['value']['operands'][0]['purpose'], {'semantic_role': role})
                self.assertEqual(row, original)
                for forbidden in ['manual_text', 'raw_text', 'layout_text', 'source_span', 'bbox', 'geometry_reference']:
                    with self.subTest(forbidden=forbidden):
                        polluted = copy.deepcopy(runtime)
                        polluted['value']['operands'][0]['purpose'][forbidden] = description
                        with self.assertRaisesRegex(ValueError, 'Private original evidence'):
                            OwnerStoreWriter._public(polluted)

    def test_explicit_output_storage_head_purpose_publishes_without_guessing_other_cells(self):
        from tools.fact_applied_review import _parameter_meaning
        from knowledge.fact_corpus import project_definition
        descriptions = [
            'Specifies the head device number storing the generated record. (Three devices are occupied.)',
            'The reading destination of sampled values and the head device are specified (3 points occupied).']
        for kind in ['ANY16', {'kind': 'array', 'element_type': 'ANY16', 'index_min': 0, 'index_max': 2}]:
            for description in descriptions:
                with self.subTest(kind=kind, description=description):
                    cell = {'name': 'result', 'type': kind, 'direction': 'output', 'description': description}
                    operand = {**cell, **_parameter_meaning(cell)}
                    operand.pop('description')
                    self.assertEqual(operand['purpose']['semantic_role'], 'result_storage_head')
                    self.assertEqual(operand['purpose'], {'semantic_role': 'result_storage_head'})
                    self.assertEqual(cell['description'], description)
                    entry = {'opcode': 'SYNTH_RECORD', 'target_model': 'FX3U', 'facts': [fact(
                        'record-table', 'parameters.typed_operands', value={'operands': [operand], 'operand_count': 1})]}
                    published, rejected = project_definition(entry)
                    self.assertFalse(rejected)
                    self.assertEqual(len(published['facts']), 1)
                    self.assertNotIn('manual_text', published['facts'][0]['value']['operands'][0]['purpose'])
        for changes in [
            {'direction': 'input'}, {'direction': 'in_out'}, {'type': 'unknown'},
            {'unresolved_type_qualifications': ['unresolved_model']}, {'type_completeness': 'partial'},
            {'description': 'Target variable'},
            {'description': 'Number of data entries to search'},
            {'description': 'Length of data stack'},
        ]:
            with self.subTest(changes=changes):
                cell = {'name': 'result', 'type': 'ANY16', 'direction': 'output',
                        'description': descriptions[0], **changes}
                self.assertEqual(_parameter_meaning(cell)['purpose'], {'source_description': cell['description']})

    def test_explicit_input_storage_head_purpose_preserves_direction_and_unknowns(self):
        from tools.fact_applied_review import _parameter_purpose
        from knowledge.fact_corpus import typed_value
        description = 'The writing source of sampled values and the head device are specified (3 points occupied).'
        for kind in ['ANY16', {'kind': 'array', 'element_type': 'ANY16', 'index_min': 0, 'index_max': 2}]:
            with self.subTest(kind=kind):
                cell = {'name': 'source', 'type': kind, 'direction': 'input', 'description': description}
                original = copy.deepcopy(cell)
                purpose = _parameter_purpose(cell)
                self.assertEqual(purpose, {'semantic_role': 'source_storage_head'})
                self.assertEqual(typed_value(purpose), {'semantic_role': 'source_storage_head'})
                self.assertEqual(cell, original)
        for change in [
                {'type': 'unknown'}, {'type_completeness': 'partial'},
                {'unresolved_type_qualifications': ['unresolved_model']},
                {'direction': 'output'}, {'direction': 'in_out'},
                {'description': 'The writing source or destination of sampled values and the head device are specified.'},
                {'description': 'Specifies the head device number to which the sampled values are written.'},
                {'description': 'Target variable'}]:
            with self.subTest(change=change):
                cell = {'name': 'source', 'type': 'ANY16', 'direction': 'input', 'description': description, **change}
                self.assertEqual(_parameter_purpose(cell), {'source_description': cell['description']})

    def test_native_output_storage_head_uses_the_same_parameter_purpose_rule(self):
        from tools.fact_applied_review import _native_parameter_facts
        description = 'Specifies the head device number storing the generated record.'
        row = {'name': 'D', 'type': 'ANY16', 'direction': 'output', 'description': description}
        record = {'id': 'synthetic', 'manual_id': 'fx3_programming_r', 'manual_number': 'SYNTH',
                  'revision': 'A', 'pdf_pages': [1], 'text': ''}
        with patch('tools.fact_applied_review._native_set_data_rows', return_value=[row]), \
                patch('tools.fact_applied_review._native_diagram_order', return_value=['D']):
            facts = _native_parameter_facts(record, {'opcode': 'SYNTH_RECORD', 'width_bits': 16}, ['FX3U'])
        typed = next(f for f in facts if f['dimension'] == 'parameters.typed_operands')
        self.assertEqual(typed['value']['operands'][0]['purpose'],
                         {'semantic_role': 'result_storage_head'})

    def test_exact_enable_cells_publish_without_accepting_other_prose(self):
        from tools.fact_applied_review import _parameter_meaning
        from knowledge.fact_corpus import project_definition
        def cell(name, direction, description, kind='BOOL'):
            return {'name': name, 'direction': direction, 'description': description, 'type': kind}
        gates = [cell('EN', 'input', ' Execution   condition '),
                 cell('ENO', 'output', 'Execution state', {'kind': 'bool'})]
        operands = [{**{k: row[k] for k in ('name', 'type', 'direction')}, **_parameter_meaning(row)}
                    for row in gates]
        self.assertEqual([p['purpose'] for p in operands], [
            {'role': 'execution_condition'}, {'role': 'execution_status'}])
        entry = {'opcode': 'SYNTH_GATE', 'target_model': 'FX3U', 'vendor': 'mitsubishi', 'facts': [
            fact('gate-table', 'parameters.typed_operands',
                 scope={'forms': ['SYNTH_GATE'], 'models': ['FX3U'], 'languages': ['fbd']},
                 value={'operand_count': 2, 'operands': operands})]}
        published, rejected = project_definition(entry)
        self.assertFalse(rejected)
        self.assertEqual(len(published['facts']), 1)
        for row in [cell('s', 'input', 'Execution condition'),
                    cell('EN', 'output', 'Execution condition'),
                    cell('EN', 'input', 'Execution condition', 'ANY16'),
                    cell('ENO', 'output', 'Execution result'),
                    cell('ENO', 'output', 'Execution state while enabled'),
                    cell('s', 'input', 'Target variable', 'ANY16'), cell('EN', 'input', '')]:
            with self.subTest(row=row):
                expected = ' '.join(row['description'].split())
                actual = _parameter_meaning(row)
                self.assertEqual(actual.get('purpose'), {'source_description': expected} if expected else None)
        mixed = copy.deepcopy(entry)
        mixed['facts'][0]['value']['operands'].append({
            'name': 's', 'type': 'ANY16', 'direction': 'input',
            **_parameter_meaning(cell('s', 'input', 'Target variable', 'ANY16'))})
        mixed['facts'][0]['value']['operand_count'] = 3
        published, rejected = project_definition(mixed)
        self.assertFalse(published['facts'])
        self.assertEqual(rejected[0]['reason'], 'unclassified_expression')

    def test_source_columns_keep_value_width_and_array_length(self):
        from tools.fact_fx5_signatures import _geometry_operands, _row_for, enrich_operand_storage
        def word(text, x, y):
            return {'text': text, 'x0': x, 'x1': x + 8, 'top': y, 'bottom': y + 6}
        words = [word(text, x, 10) for text, x in [('Operand', 50), ('Description', 130),
                 ('Data', 380), ('type', 410), ('Data', 470), ('type', 500)]]
        words += [word(text, x, 30) for text, x in [('(d)', 50), ('DMUL(P)', 80),
                  ('64-bit', 380), ('signed', 410), ('binary', 440), ('ANY32_S_ARRAY', 470)]]
        words += [word(text, x, 40) for text, x in [('Number', 470), ('of', 490), ('elements:', 510), ('2', 550)]]
        rows = _geometry_operands(words)
        row = _row_for(rows, 'd', 'DMUL', 'DMUL')
        self.assertEqual(row['value_type'], {'kind': 'int', 'bits': 64, 'signed': True})
        self.assertEqual(row['word_device_layout']['word_count'], 4)
        self.assertEqual(row['label_array_length'], 2)
        signature = fact('signature', 'parameters.typed_operands', value={'documented_page': 246,
                         'operands': [{'name': 'd', 'type': 'ANY32_S_ARRAY', 'direction': 'output'}]},
                         sources=[{'manual_id': 'fx5_programming_ab', 'revision': 'AB'}])
        self.assertEqual(enrich_operand_storage([signature], 'DMUL', {246: rows}), 3)
        self.assertEqual(signature['value']['operands'][0]['word_device_layout']['word_count'], 4)
        self.assertEqual(enrich_operand_storage([signature], 'DMUL', {246: rows}), 0)
        self.assertEqual(signature['status'], 'source_verified')

    def test_relation_based_parameter_purposes_are_source_bound(self):
        from plc.instruction_knowledge import parameter_usage_facts
        scope = {'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']}
        signature = fact('signature', 'parameters.typed_operands', scope=scope, value={'operand_count': 2,
                         'operands': [{'name': 'A', 'type': 'INT', 'direction': 'input'},
                                      {'name': 'R', 'type': 'INT', 'direction': 'output'}]})
        effect = fact('effect', 'effects.dataflow', scope=scope,
                      conditions=[{'native_operand_count': 2}], value={'operation': 'copy', 'inputs': ['a'], 'output': 'r'})
        groups = parameter_usage_facts([signature, effect])
        self.assertEqual(len(groups), 1)
        derived = groups[0]
        self.assertEqual(derived['depends_on'], ['signature', 'effect'])
        self.assertEqual(derived['scope'], scope)
        self.assertTrue(all(p['purpose']['uses'] for p in derived['value']['operands']))
        projected = project_packet(packet([signature, effect, derived]), plc_model='FX5U', language='ladder',
                                   aspects=['programming_semantics'], project_mode='native_ladder_instruction')
        self.assertTrue(all(f['selection_applicability'] == 'applicable' for f in projected['value']['facts']))
        table = technical_fact_packet(projected)['value']['parameter_tables']
        self.assertEqual(len(table), 1)
        self.assertTrue(all(p['purpose'] for p in table[0]['parameters']))
        self.assertFalse(parameter_usage_facts([signature, dict(effect, value={'operation': 'copy', 'knowledge_scope': 'operation_summary'})]))
        self.assertFalse(parameter_usage_facts([signature, dict(effect, scope=dict(scope, languages=['st']))]))
        unverified = parameter_usage_facts([dict(signature, status='unknown'), effect])[0]
        self.assertEqual(unverified['status'], 'unknown')
        self.assertEqual(unverified['conditions'], effect['conditions'])
        mismatch = project_packet(packet([signature, dict(effect, conditions=[{'native_operand_count': 3}])]),
            plc_model='FX5U', language='ladder', aspects=['effects'], project_mode='native_ladder_instruction')
        self.assertNotIn('effect', {f['id'] for f in mismatch['value']['facts']})

    def test_structural_conditions_require_applicable_signature_closure(self):
        from plc.instruction_constraints import bind_operand_signature
        signature = fact('signature', 'parameters.typed_operands', value={'operands': [{'name': 'S1'}, {'name': 'D'}]},
                         scope={'models': ['FX5U'], 'forms': ['PWM'], 'languages': ['ladder']}, depends_on=['mapping'])
        mapping = fact('mapping', 'identity.binding')
        effect = fact('effect', 'effects.result', conditions=[{'kind': 'operand_form', 'required_operands': ['s1', 'd']}])
        p = packet([signature, mapping, effect])
        result = project_packet(p, plc_model='FX5U', language='ladder', aspects=['effects'])
        self.assertEqual(next(f for f in result['value']['facts'] if f['id'] == 'effect')['selection_applicability'], 'applicable')
        self.assertTrue({'signature', 'mapping'} <= {f['id'] for f in result['value']['facts']})
        for bad in [[], [dict(mapping, status='unknown')], [dict(mapping, conditions=[{'context': 'mode', 'equals': 1}])]]:
            self.assertIsNone(bind_operand_signature([signature, *bad], target='PWM', plc_model='FX5U', language='ladder',
                                                    context={'$OPERAND_ROLES': ['S1', 'D']}))
        conflict = dict(signature, id='conflict', value={'operands': [{'name': 'OTHER'}]})
        self.assertIsNone(bind_operand_signature([signature, mapping, conflict], target='PWM', plc_model='FX5U', language='ladder'))
        mode_signature = dict(signature, conditions=[{'context': 'project_mode', 'equals': 'native'}])
        self.assertEqual(bind_operand_signature([mode_signature, mapping], target='PWM', plc_model='FX5U', language='ladder',
                                               context={'project_mode': 'native'}), ('S1', 'D'))
        self.assertIsNone(bind_operand_signature([mode_signature, mapping], target='PWM', plc_model='FX5U', language='ladder'))


class InstructionRelationBindingTests(unittest.TestCase):
    def test_relation_preserves_declared_type_alternatives(self):
        from plc.instruction_relations import bind_relation_facts
        for language in ('st', 'fbd', 'ladder'):
            with self.subTest(language=language):
                signature = self.signature(languages=(language,))
                signature['value']['operands'][0].update(
                    type='DWORD', type_alternatives=['WORD', 'DWORD'])
                signature['value']['operands'][-1].update(
                    type='DINT', type_alternatives=['INT', 'DINT'])
                effect = fact('operation', 'effects.operation', value={
                    'operation': 'convert_type', 'input': 's1', 'result': 'd'},
                    scope=copy.deepcopy(signature['scope']))
                before = copy.deepcopy(signature)
                relation, = bind_relation_facts([effect, signature])
                binding = relation['value']['operand_binding']
                self.assertEqual(binding['inputs'][0]['type_alternatives'], ['WORD', 'DWORD'])
                self.assertEqual(binding['output']['type_alternatives'], ['INT', 'DINT'])
                binding['inputs'][0]['type_alternatives'].append('BYTE')
                binding['output']['type_alternatives'].clear()
                self.assertEqual(signature, before)

    def test_operand_selectors_reject_cross_command_relations(self):
        from plc.instruction_relations import bind_relation_facts, fact_context_intersection
        signature = self.signature()
        effect = fact('operation', 'effects.operation', value={
            'operation': 'absolute_value', 'input': 's1', 'result': 'd'},
            scope=copy.deepcopy(signature['scope']))
        selector = lambda parameter, value: {
            'context': 'operand_value', 'parameter': parameter, 'equals': value}
        for first, second, compatible in [
            (selector('S1', 1), selector('S1', 8), False),
            (selector('S2', 0), selector('s2', 1), False),
            (selector('S1', 1), selector('s1', True), False),
            (selector('S1', 1), selector('s1', 1), True),
            (selector('S1', 8), selector('S2', 1), True),
        ]:
            with self.subTest(first=first, second=second):
                effect['conditions'] = [first]
                signature['conditions'] = [second]
                self.assertEqual(fact_context_intersection(effect, signature) is not None, compatible)
                self.assertEqual(bool(bind_relation_facts([effect, signature])), compatible)

    def signature(self, languages=('st',), count=1, output=True):
        operands = [{'name': 'a' + str(i), 'marker': 's' + str(i), 'direction': 'input',
                     'type': {'kind': 'int', 'bits': 32, 'signed': True}, **({'optional': True} if i > 2 else {})}
                    for i in range(1, count + 1)]
        if output:
            operands.append({'name': 'result', 'marker': 'd', 'direction': 'output', 'type': 'DINT'})
        return fact('declaration', 'parameters.typed_operands', value={'operands': operands},
                    scope={'models': ['FX5U'], 'forms': ['SYNTH'], 'languages': list(languages)})

    def test_result_aliases_bind_actual_parameters_and_keep_boundaries(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature()
        effect = fact('operation', 'effects.operation', value={'operation': 'absolute_value',
            'input': 's1', 'result': 'd', 'special_boundary': {'input': -99, 'output': -99}},
            scope=copy.deepcopy(signature['scope']))
        relation, = bind_relation_facts([effect, signature])
        self.assertEqual(relation['value']['inputs'], ['a1'])
        self.assertEqual(relation['value']['output'], 'result')
        self.assertEqual(relation['value']['special_boundary'], effect['value']['special_boundary'])
        self.assertEqual(relation['depends_on'], ['operation', 'declaration'])
        self.assertNotIn('behavior', relation['value'])
        self.assertNotIn('output', effect['value'])

    def test_state_accesses_bind_zero_input_results_and_state_only_calls(self):
        from plc.instruction_relations import bind_relation_facts
        for input_count, has_output in [(0, True), (1, False), (1, True)]:
            with self.subTest(input_count=input_count, has_output=has_output):
                signature = self.signature(count=input_count, output=has_output)
                effect = fact('state-operation', 'effects.documented_processing', value={
                    'operation': 'source_declared_state_operation',
                    'inputs': ['s1'] if input_count else [],
                    **({'output': 'd'} if has_output else {}),
                    'state_accesses': [{'target': {'kind': 'state', 'parameter': 'documented_state'},
                                        'access': 'read_write' if has_output else 'write'}]},
                    scope=copy.deepcopy(signature['scope']))
                before = copy.deepcopy(effect)
                relation, = bind_relation_facts([effect, signature])
                self.assertEqual(relation['value']['inputs'], ['a1'] if input_count else [])
                self.assertEqual(relation['value']['state_accesses'], effect['value']['state_accesses'])
                binding = relation['value']['operand_binding']
                self.assertEqual([p['parameter'] for p in binding['inputs']], ['a1'] if input_count else [])
                if has_output:
                    self.assertEqual(binding['output'], {'parameter': 'result', 'type': 'DINT'})
                else:
                    self.assertNotIn('output', binding)
                self.assertEqual(effect, before)
                self.assertNotIn('behavior', relation['value'])
                for invalid in ['missing_input', 'wrong_target', 'missing_state', 'bad_access']:
                    broken = copy.deepcopy(effect)
                    if invalid == 'missing_input': broken['value']['inputs'] = ['not_declared']
                    elif invalid == 'wrong_target': broken['value']['output'] = 'not_declared'
                    elif invalid == 'missing_state': broken['value']['state_accesses'][0]['target']['parameter'] = ''
                    else: broken['value']['state_accesses'][0]['access'] = 'inferred'
                    self.assertFalse(bind_relation_facts([broken, signature]), invalid)

    def test_variadic_range_is_resolved_against_its_declaration(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature(languages=('fbd',), count=4)
        effect = fact('operation', 'effects.operation', value={'operation': 'add', 'inputs': ['s1..s4'],
            'result': 'd', 'input_count': {'minimum': 2, 'maximum': 4}}, scope=copy.deepcopy(signature['scope']))
        relation, = bind_relation_facts([effect, signature])
        self.assertEqual(relation['value']['inputs'], ['a1', 'a2', 'a3', 'a4'])
        self.assertTrue(relation['value']['operand_binding']['inputs'][2]['optional'])
        effect['value']['inputs'] = ['s1..s5']
        self.assertFalse(bind_relation_facts([effect, signature]))

    def test_statement_label_without_source_binding_is_not_promoted(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature()
        effect = fact('operation', 'effects.documented_operation', value={'operation': 'absolute_value',
            'knowledge_scope': 'operation_summary'}, scope=copy.deepcopy(signature['scope']))
        self.assertFalse(bind_relation_facts([effect, signature]))
        effect.update(dimension='effects.result_rule', value={'rule': 'absolute_value'})
        self.assertEqual(len(bind_relation_facts([effect, signature])), 1)
        for extra in ({'sources': [{'source_id': 'other'}]}, {'scope': {'models': ['FX3U']}},
                      {'status': 'unknown'}, {'verification': 'not_performed'}):
            self.assertFalse(bind_relation_facts([dict(effect, **extra), signature]))
        signature['conditions'] = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
        effect['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        self.assertFalse(bind_relation_facts([effect, signature]))

    def test_function_return_keeps_original_call_arity(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature(output=False)
        returned = fact('call', 'parameters.st_signature', scope=copy.deepcopy(signature['scope']),
            value={'available': True, 'enable': False, 'parameters': copy.deepcopy(signature['value']['operands']),
                   'returns': {'name': 'd', 'type': 'DINT'}})
        effect = fact('operation', 'effects.documented_operation', scope=copy.deepcopy(signature['scope']),
                      value={'input': 'a1', 'output': 'd', 'operation': 'absolute_value'})
        relation, = bind_relation_facts([effect, signature, returned])
        self.assertEqual(relation['value']['operand_binding']['output']['kind'], 'return_value')
        self.assertIn('call', relation['depends_on'])
        self.assertEqual(len(signature['value']['operands']), 1)
        returned['value']['enable'] = True
        self.assertEqual(bind_relation_facts([effect, signature, returned])[0]['value']['output'], 'd')
        returned['value']['returns']['role'] = 'execution_status'
        self.assertFalse(bind_relation_facts([effect, signature, returned]))

    def test_shared_block_needs_explicit_interface_proof(self):
        from plc.instruction_relations import interface_relation_facts
        from plc.instruction_definition import select_fact_dependencies
        signature = self.signature(languages=('fbd',))
        pins = signature['value']['operands']
        fbd = fact('fbd', 'parameters.fbd_signature', scope=copy.deepcopy(signature['scope']),
                   value={'name': 'SYNTH', 'available': True, 'pins': pins})
        bridge = fact('bridge', 'parameters.ladder_signature',
            scope={**signature['scope'], 'languages': ['ladder']},
            value={'name': 'SYNTH', 'available': True, 'pins': copy.deepcopy(pins),
                   'required_context': [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]})
        effect = fact('operation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
                      value={'operation': 'absolute_value', 'input': 's1', 'result': 'd'})
        alias, = interface_relation_facts([fbd, bridge, effect])
        self.assertEqual(alias['scope']['languages'], ['ladder'])
        self.assertEqual(alias['conditions'], [{'context': 'project_mode', 'equals': 'structured_ladder_block'}])
        closure = select_fact_dependencies([fbd, bridge, effect, alias], [alias['id']],
                                          opcode='SYNTH', model='FX5U', language='ladder')
        self.assertFalse(closure['gaps'])
        self.assertTrue(closure['source_verification_complete'])
        for missing in ([bridge, effect], [fbd, effect]):
            self.assertFalse(interface_relation_facts(missing))
        bridge['value']['pins'][0]['name'] = 'different'
        self.assertFalse(interface_relation_facts([fbd, bridge, effect]))

    def test_composition_is_idempotent_and_keeps_original_for_debug(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        signature = self.signature()
        effect = fact('operation', 'effects.result_rule', value={'rule': 'absolute_value'},
                      scope=copy.deepcopy(signature['scope']))
        original = [signature, effect]
        composed, _ = compose_programming_facts(registry, 'SYNTH', cpu='FX5U', existing=original)
        again, replaced = compose_programming_facts(registry, 'SYNTH', cpu='FX5U', existing=composed)
        self.assertEqual(composed, again)
        self.assertFalse(replaced)
        self.assertTrue(any(f['dimension'] == 'effects.bound_relation' for f in composed))
        self.assertEqual(next(f for f in composed if f['id'] == effect['id']), effect)
        view = technical_fact_packet({'kind': 'instruction', 'target': 'SYNTH',
            'scope': signature['scope'], 'value': {'opcode': 'SYNTH', 'target_model': 'FX5U', 'facts': composed}})
        raw = next(g['semantics']['effects.result_rule'] for g in view['value']['additional_knowledge']
                   if 'effects.result_rule' in g['semantics'])
        self.assertIn('instruction_relation', raw['value'])

    def test_wide_integer_conversion_does_not_claim_unconditional_exactness(self):
        from plc.instruction_relations import normalize_precision_claims
        signature = self.signature()
        signature['value']['operands'][-1]['type'] = {'kind': 'real', 'bits': 32}
        effect = fact('operation', 'effects.result_rule', value={'rule': 'exact_integer_to_single',
            'significant_digits': 7}, scope=copy.deepcopy(signature['scope']))
        fixed, originals = normalize_precision_claims([signature, effect])
        self.assertEqual(originals, [effect])
        self.assertEqual(fixed[-1]['value']['rule'], 'integer_to_single')
        self.assertIn('integer_to_real_rounding_mode', fixed[-1]['value']['unresolved_items'])
        self.assertEqual(fixed[-1]['status'], effect['status'])
        signature['value']['operands'][0]['type']['bits'] = 16
        self.assertEqual(normalize_precision_claims([signature, effect]), ([signature, effect], []))

    def test_block_regions_keep_count_units_and_bind_every_reference(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature(languages=('ladder',), count=2)
        signature['value']['operands'][0].update(name='S', marker='S')
        signature['value']['operands'][1].update(name='n', marker='n')
        signature['value']['operands'][2].update(name='D', marker='D')
        effect = fact('operation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
                      value={'operation': 'copy_word_block', 'object': 'S_n_words', 'result': 'D_n_words'})
        relation, = bind_relation_facts([effect, signature])
        self.assertEqual(relation['value']['inputs'], ['S', 'n'])
        self.assertEqual(relation['value']['output_region'], {'head': 'D', 'count_operand': 'n', 'unit_bits': 16})
        effect['value']['result'] = 'D_other_words'
        self.assertFalse(bind_relation_facts([effect, signature]))

    def test_enable_status_pins_are_not_arithmetic_inputs_or_results(self):
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_knowledge import parameter_usage_facts
        signature = self.signature(languages=('fbd',))
        signature['value']['operands'].extend([
            {'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
            {'name': 'ENO', 'type': 'BOOL', 'direction': 'output'}])
        gate = fact('enable', 'execution.trigger', scope=copy.deepcopy(signature['scope']), value={'has_EN_ENO': True})
        effect = fact('operation', 'effects.result_rule', scope=copy.deepcopy(signature['scope']), value={'rule': 'absolute_value'})
        relation, = bind_relation_facts([effect, signature, gate])
        self.assertEqual(relation['value']['inputs'], ['a1'])
        self.assertEqual(relation['value']['output'], 'result')
        self.assertIn('enable', relation['depends_on'])
        purposes, = parameter_usage_facts([signature, relation])
        self.assertTrue(all(p.get('purpose') for p in purposes['value']['operands']))
        self.assertFalse(bind_relation_facts([effect, signature]))



class DeclaredLiteralTargetTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.source = sqlite3.connect(':memory:')
        self.source.row_factory = sqlite3.Row
        self.addCleanup(self.source.close)
        self.source.executescript("""
            CREATE TABLE entity_index(entity TEXT,chunk_id TEXT,PRIMARY KEY(entity,chunk_id));
            CREATE TABLE facts(id TEXT PRIMARY KEY,kind TEXT,target TEXT,plc_model TEXT,chunk_id TEXT,payload_json TEXT);
        """)
        for index, name in enumerate(('*', '*P', 'D*', 'D*P', 'ADD')):
            identity = 'synthetic:' + str(index)
            self.source.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                (identity,'instruction',name,'Q03UDV',identity,'{}'))
            self.source.execute('INSERT INTO entity_index VALUES (?,?)',(name,identity))
        self.source.execute("INSERT INTO entity_index VALUES ('MUL_ALIAS','synthetic:2')")

    def runtime(self, kind=None):
        if kind == 'instruction':
            return (None,None,self.source,{}, {'owner_store_serialization':'instruction-owner-store-v1'})
        return (None,None,None,None,{})

    def test_v4_literals_keep_exact_spelling_and_model_scope(self):
        from knowledge.structured_facts import _declared_instruction_alias_targets
        with patch('knowledge.structured_facts._runtime', side_effect=self.runtime):
            self.assertEqual([t['opcode'] for t in _declared_instruction_alias_targets('D*',plc_model='Q03UDV')], ['D*'])
            targets = _declared_instruction_alias_targets('* *P D* D*P',plc_model='Q03UDV')
            self.assertEqual({t['opcode'] for t in targets}, {'*','*P','D*','D*P'})
            self.assertEqual([t['opcode'] for t in _declared_instruction_alias_targets('MUL_ALIAS',plc_model='Q03UDV')], ['D*'])
            for query in ('D*PP','foo*bar','notes:\n* retain variable','please add notes'):
                self.assertFalse(_declared_instruction_alias_targets(query,plc_model='Q03UDV'), query)
            self.assertFalse(_declared_instruction_alias_targets('D*',plc_model='FX3G'))

    def test_literal_scope_prefix_reaches_generation_lookup(self):
        from knowledge.structured_facts import structured_fact_targets
        from knowledge.analysis_router import has_generation_fact_target
        with patch('knowledge.structured_facts._runtime', side_effect=self.runtime):
            targets = structured_fact_targets('Q03UDV: *',plc_model='Q03UDV',language='ladder')
            self.assertEqual([t['opcode'] for t in targets['instructions']], ['*'])
            self.assertTrue(has_generation_fact_target('Q03UDV: *',plc_model='Q03UDV'))
            self.assertFalse(has_generation_fact_target('notes:\n* retain variable',plc_model='Q03UDV'))

    def test_existing_natural_alias_identifies_the_v4_owner(self):
        import sqlite3
        from knowledge.structured_facts import _declared_instruction_alias_targets
        mixed = sqlite3.connect(':memory:')
        self.addCleanup(mixed.close)
        mixed.row_factory = sqlite3.Row
        mixed.execute('CREATE TABLE fact_aliases(alias,opcode,alias_type,plc_models)')
        mixed.execute('INSERT INTO fact_aliases VALUES (?,?,?,?)',('合成乘法','D*','zh_alias','["Q03UDV"]'))
        def runtime(kind=None):
            if kind == 'instruction': return self.runtime(kind)
            return (None,None,mixed,{}, {'corpus_kind':'authored_plc_facts_v1'})
        with patch('knowledge.structured_facts._runtime', side_effect=runtime):
            target, = _declared_instruction_alias_targets('合成乘法',plc_model='Q03UDV')
            self.assertEqual(target['opcode'], 'D*')
            self.assertEqual(target['target_resolution'], 'declared_manual_alias')
            self.assertFalse(_declared_instruction_alias_targets('合成乘法',plc_model='FX3G'))



class RelationDeliveryTests(unittest.TestCase):
    signature = InstructionRelationBindingTests.signature

    def test_parent_call_restores_its_documented_gate_arguments(self):
        from plc.instruction_relations import normalize_interface_parameters, bind_relation_facts
        signature = self.signature(output=False)
        signature['id'] = 'call:operands:FX5U'
        call = fact('call:FX5U', 'parameters.st_signature', scope=copy.deepcopy(signature['scope']),
            value={'enable': True, 'available': True, 'parameters': [
                {'name': 'EN', 'direction': 'input', 'type': 'BOOL'},
                {'name': 'ENO', 'direction': 'output', 'type': 'BOOL'},
                *copy.deepcopy(signature['value']['operands'])], 'returns': {'name': 'd', 'type': 'DINT'}})
        effect = fact('operation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'operation': 'absolute_value', 'input': 'a1', 'result': 'd'})
        fixed, originals = normalize_interface_parameters([signature, call, effect])
        self.assertEqual(originals, [signature])
        self.assertEqual([p['name'] for p in fixed[0]['value']['operands']], ['EN', 'ENO', 'a1'])
        self.assertEqual(fixed[0]['value']['operand_count'], 3)
        relation, = bind_relation_facts(fixed)
        self.assertEqual(relation['value']['inputs'], ['a1'])
        self.assertEqual(relation['value']['output'], 'd')
        self.assertEqual(len(relation['value']['operand_binding']['execution_arguments']), 2)
        self.assertEqual(normalize_interface_parameters(fixed), (fixed, []))
        unscoped = copy.deepcopy([signature, call])
        unscoped[0]['id'], unscoped[1]['id'] = 'call:operands', 'call'
        self.assertEqual(len(normalize_interface_parameters(unscoped)[0][0]['value']['operands']), 3)
        call['value']['parameters'].append({'name': 'other', 'type': 'INT', 'direction': 'input'})
        self.assertEqual(normalize_interface_parameters([signature, call]), ([signature, call], []))
        call['id'] = 'unrelated'
        self.assertEqual(normalize_interface_parameters([signature, call]), ([signature, call], []))

    def test_comparison_state_keeps_signed_values_and_logical_composition(self):
        import operator
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_definition import evaluate_expression
        from knowledge.instruction_presentation import substantive_effect
        signature = self.signature(languages=('ladder',), count=2, output=False)
        for p in signature['value']['operands']: p['type'] = 'ANY32'
        signature['value']['native_form'] = {'minimum_operands': 2, 'maximum_operands': 2}
        effect = fact('comparison', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'operation': 'compose_comparison_contact', 'left': 'S1', 'right': 'S2', 'data_domain': 'signed_BIN32'})
        for token, compare in [('=', operator.eq), ('<>', operator.ne), ('<', operator.lt),
                               ('<=', operator.le), ('>', operator.gt), ('>=', operator.ge)]:
            for composition in ('assign', 'and', 'or'):
                effect['value'].update(comparison=token, composition=composition)
                relation, = bind_relation_facts([signature, effect])
                self.assertTrue(substantive_effect(relation))
                self.assertNotIn('behavior', relation['value'])
                self.assertEqual(relation['value']['outputs'][0]['target']['kind'], 'state')
                for a, b in [(-2147483648, -1), (-7, -7), (0, -1), (2147483647, 0)]:
                    for previous in (False, True):
                        with self.subTest(operator=token, composition=composition, a=a, b=b, previous=previous):
                            expected = compare(a, b)
                            if composition == 'and': expected = previous and expected
                            if composition == 'or': expected = previous or expected
                            self.assertEqual(evaluate_expression(relation['value']['outputs'][0]['expression'],
                                {'a1': a, 'a2': b}, state={'operation_result': previous}), expected)
        effect['value']['data_domain'] = 'signed_BIN16'
        self.assertFalse(bind_relation_facts([signature, effect]))
        effect['value']['data_domain'] = 'signed_BIN32'
        signature['value'].pop('native_form')
        self.assertFalse(bind_relation_facts([signature, effect]))

    def test_component_names_without_offsets_do_not_establish_a_layout(self):
        from plc.instruction_relations import bind_relation_facts
        from knowledge.instruction_presentation import substantive_effect
        signature = self.signature(count=2)
        effect = fact('division', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'operation': 'divide', 'inputs': ['s1', 's2'], 'result': 'd',
                   'result_components': ['quotient', 'remainder'], 'covered_aspects': ['effects']})
        relation, = bind_relation_facts([signature, effect])
        self.assertIn('result_component_layout', relation['value']['unresolved_items'])
        self.assertFalse(substantive_effect(relation))

    def test_plain_st_evaluation_keeps_caller_owned_result_assignment(self):
        from plc.instruction_relations import bind_relation_facts, call_boundary_facts
        signature = self.signature(output=False)
        call = fact('call', 'parameters.st_signature', scope=copy.deepcopy(signature['scope']),
            value={'enable': False, 'parameters': copy.deepcopy(signature['value']['operands']),
                   'returns': {'name': 'd', 'type': 'DINT'}})
        effect = fact('operation', 'effects.result_rule', scope=copy.deepcopy(signature['scope']), value={'rule': 'absolute_value'})
        evaluation = fact('evaluation', 'execution.call_enable', scope=copy.deepcopy(signature['scope']),
            value={'enable_parameter': False, 'enable_gate': 'none', 'evaluation': 'at_call_site'})
        source = [signature, call, effect]
        relations = bind_relation_facts(source)
        self.assertFalse(call_boundary_facts(source + relations))
        wrong = copy.deepcopy(evaluation)
        wrong.update(id='wrong', scope={**wrong['scope'], 'languages': ['fbd']})
        boundary, = call_boundary_facts(source + [wrong, evaluation] + relations)
        self.assertEqual(boundary['scope']['languages'], ['st'])
        self.assertEqual(boundary['value']['when_not_executed'], 'no_call_result_is_produced')
        self.assertNotIn('destination_holds', boundary['value'])
        evaluation['value']['enable_gate'] = 'EN'
        self.assertFalse(call_boundary_facts(source + [evaluation] + relations))

    def test_legacy_binding_marker_keeps_its_data_purposes(self):
        from plc.instruction_knowledge import parameter_usage_facts
        signature = self.signature()
        effect = fact('operation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'operation': 'absolute_value', 'inputs': ['a1'], 'output': 'result', 'operand_binding': 'resolved'})
        purposes, = parameter_usage_facts([signature, effect])
        self.assertTrue(all(p.get('purpose') for p in purposes['value']['operands']))

    def test_selector_resolves_actual_aliases_and_boolean_control(self):
        from plc.instruction_relations import bind_relation_facts
        signature = self.signature(count=3)
        signature['value']['operands'][0]['type'] = 'BOOL'
        effect = fact('selection', 'effects.result_rule', scope=copy.deepcopy(signature['scope']),
            value={'rule': 'select_by_condition', 'condition_FALSE': 's2', 'condition_TRUE': 's3'})
        relation, = bind_relation_facts([signature, effect])
        self.assertEqual(relation['value']['selector'], 'a1')
        self.assertEqual(relation['value']['condition_FALSE'], 'a2')
        signature['value']['operands'][0]['type'] = 'DINT'
        self.assertFalse(bind_relation_facts([signature, effect]))

    def test_operation_storage_joins_table_without_erasing_label_choices(self):
        from plc.instruction_knowledge import compose_programming_facts
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        signature = self.signature(count=2)
        for p in signature['value']['operands'][:2]: p['type'] = 'ANY16'
        signature['value']['operands'][-1].update(type='ARRAY', element_type='ANY16_OR_ANY32',
            element_type_alternatives=['ANY16', 'ANY32'], array={'lower': 1, 'upper': 2, 'length': 2})
        effect = fact('operation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'operation': 'multiply', 'inputs': ['s1', 's2'], 'result': 'd',
                'input_value_type': {'kind': 'int', 'bits': 16, 'signed': True},
                'result_value_type': {'kind': 'int', 'bits': 32, 'signed': True},
                'output_layout': {'head': 'd', 'device_unit_bits': 16, 'value_bits': 32, 'word_count': 2}})
        composed, _ = compose_programming_facts(registry, 'SYNTH', cpu='FX5U', existing=[signature, effect])
        view = technical_fact_packet({'kind': 'instruction', 'target': 'SYNTH', 'scope': signature['scope'],
            'value': {'opcode': 'SYNTH', 'target_model': 'FX5U', 'facts': composed}})
        table, = view['value']['parameter_tables']
        self.assertEqual(table['parameters'][0]['operation_value_type']['bits'], 16)
        output = table['parameters'][-1]
        self.assertEqual(output['element_type_alternatives'], ['ANY16', 'ANY32'])
        self.assertEqual(output['result_value_type']['bits'], 32)
        self.assertEqual(output['write_layout']['word_count'], 2)
        self.assertEqual(output['write_layout']['head'], 'result')
        self.assertNotIn('write_layout', signature['value']['operands'][-1])

    def test_ql_operator_families_do_not_retype_st_expressions(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts, family_fact_groups
        from knowledge.structured_facts import generation_aspects
        from shared.model_context import model_knowledge_value
        for base, operation in [('*', 'multiply'), ('/', 'divide')]:
            for form, width, pulse in [(base,16,False),(base+'P',16,True),('D'+base,32,False),('D'+base+'P',32,True)]:
                with self.subTest(form=form):
                    info = registry.instruction_knowledge(form,cpu='Q03UDV',language='ladder')
                    self.assertEqual(info['family'],base)
                    self.assertEqual(info['data_bits'],width)
                    self.assertEqual(info['modifiers']['pulse'],pulse)
                    owner = registry._definition_owner_store.get_entry(form, cpu='Q03UDV', vendor='mitsubishi')
                    facts, _ = compose_programming_facts(registry, form, cpu='Q03UDV', existing=owner['facts'])
                    source = {'kind': 'instruction', 'target': form, 'scope': {'models': ['Q03UDV']},
                        'value': {'opcode': form, 'target_model': 'Q03UDV', 'facts': facts}}
                    selected = project_packet(source, plc_model='Q03UDV', language='st', query=form,
                        aspects=generation_aspects(form, language='st'))
                    selected['_knowledge_context'] = {'model': 'Q03UDV', 'language': 'st', 'delivery_languages': ['st']}
                    visible = model_knowledge_value(technical_fact_packet(selected))
                    for actual in visible['value']['forms']:
                        self.assertFalse({'data_bits', 'modifiers', 'execution'} & actual.keys(), actual)
                    self.assertFalse(any(f['dimension'] == 'execution.trigger' for f in selected['value']['facts']))
                    st_info = registry.instruction_knowledge(form, cpu='Q03UDV', language='st') or {}
                    self.assertFalse({'data_bits', 'modifiers', 'execution', 'knowledge_module', 'common_rules'} & st_info.keys())
                    # A Native catalogue identity does not establish an ST call.
                    catalogue = registry.resolve_catalog_form(form, cpu='Q03UDV')
                    self.assertIsNotNone(catalogue)
                    self.assertEqual(catalogue.opcode, form)
                    expressions = [f for f in selected['value']['facts'] if f['dimension'] == 'parameters.language_expression'
                        and f['value'].get('representation') == 'ST_expression' and f['value'].get('available') is True]
                    processing_groups = [f for f in family_fact_groups(registry, form, cpu='Q03UDV')
                                         if f['dimension'] == 'effects.operation']
                    if form == base:
                        self.assertTrue(expressions)
                        self.assertTrue(all(f['value']['operator'] == base and f['value']['returns'] == 'ANY_NUM' for f in expressions))
                        self.assertTrue(visible['value'].get('parameter_tables'))
                        # Real and wider integer ST expressions keep their own declared types.
                        from plc.st_frontend import parse_body
                        from plc.st_semantics import Symbols, expression_numeric_type
                        for declared, kind, bits in [('DINT', 'int', 32), ('REAL', 'real', 32), ('LREAL', 'real', 64)]:
                            unit = parse_body(f'VAR a : {declared}; b : {declared}; z : {declared}; END_VAR z := a {base} b;')
                            symbols = Symbols(unit)
                            actual = expression_numeric_type(unit.body[0][2], symbols, reference=symbols.reference)
                            self.assertEqual((actual.kind, actual.bits), (kind, bits))
                        scalar, = [f for f in processing_groups if f['scope']['languages'] == ['st']]
                        self.assertEqual(scalar['value']['output'], 'expression_value')
                        self.assertEqual(scalar['value']['type_family'], 'ANY_NUM')
                        self.assertNotIn('output_layout', scalar['value'])
                        if base == '/':
                            self.assertIs(scalar['value']['full_source_relation_complete'], False)
                            self.assertIn('signed_integer_division_rounding_policy', scalar['value']['unresolved_items'])
                    else:
                        self.assertFalse(expressions)
                    processing, = [f for f in processing_groups if 'ladder' in f['scope']['languages']]
                    self.assertEqual(processing['value']['operation'],operation)
                    self.assertEqual(processing['value']['output_layout']['word_count'],width//8)
                    if base=='/':
                        self.assertEqual([c['word_offset'] for c in processing['value']['output_layout']['components']],[0,width//16])
                        self.assertIn('signed_division_rounding_policy',processing['value']['unresolved_items'])
        self.assertIsNone(registry.instruction_knowledge('D*PP',cpu='Q03UDV',language='ladder'))


class SharedDefinitionBindingTests(unittest.TestCase):
    def test_partial_parameter_type_keeps_its_use_without_a_fabricated_finite_width(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        scope = {'models': ['FX2N'], 'forms': ['SYNTH'], 'languages': ['ladder']}
        definition = {'shared_values': {'parameters': {'parameters': ['n'], 'value': {'operands': [
            {'name': {'parameter_ref': 'n'}, 'direction': 'input', 'purpose': 'number_of_elements',
             'type': {'kind': 'integer'}, 'type_completeness': 'partial'}]}}}}
        binding = fact('use', 'parameters.usage', scope=scope, value={'definition_ref': 'parameters',
            'signature': {'parameter_types': {'n': {'kind': 'integer'}}, 'parameter_directions': {'n': 'input'}}})
        declaration = fact('declaration', 'parameters.typed_operands', scope=scope, status='declared_unverified',
            value={'operands': [{'name': 'n', 'direction': 'input', 'type': {'kind': 'integer'}, 'width_bits': 'unknown'}]})
        bound, = _bind_shared_definition(definition, binding, [declaration])
        self.assertEqual(bound['status'], 'declared_unverified')
        self.assertNotIn('operation_value_type', bound['value']['parameter_usage'][0])
        enriched, = parameter_usage_facts([bound, declaration])
        self.assertEqual(enriched['value']['operands'][0]['width_bits'], 'unknown')
        self.assertEqual(enriched['value']['operands'][0]['type'], {'kind': 'integer'})
        self.assertIn('number_of_elements', str(enriched['value']))

    def test_rule_details_attach_only_to_one_matching_relation_without_overriding_it(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        definition['shared_values']['model_detail'] = {'parameters': ['A', 'B', 'D'],
            'value': {'flag_device': 'M100'}}
        binding['value']['rule_detail_refs'] = {'expression_write': 'model_detail'}
        bound, = _bind_shared_definition(definition, binding, [signature])
        self.assertEqual(bound['value']['rules'][0]['flag_device'], 'M100')
        self.assertEqual(bound['value']['rules'][0]['outputs'][0]['target']['parameter'], 'out')
        self.assertNotIn('flag_device', definition['shared_values']['calculation']['value']['rules'][0])
        for broken in ['missing_rule', 'ambiguous_rule', 'override']:
            altered, request = copy.deepcopy(definition), copy.deepcopy(binding)
            if broken == 'missing_rule': request['value']['rule_detail_refs'] = {'other': 'model_detail'}
            if broken == 'ambiguous_rule': altered['shared_values']['calculation']['value']['rules'] *= 2
            if broken == 'override': altered['shared_values']['model_detail']['value'] = {'kind': 'changed'}
            with self.subTest(broken=broken), self.assertRaisesRegex(ValueError, 'one matching rule'):
                _bind_shared_definition(altered, request, [signature])

    def declarations(self):
        integer = {'kind': 'int', 'bits': 32, 'signed': True}
        expression = {'op': 'sub', 'type': integer, 'args': [
            {'op': 'parameter', 'name': 'A', 'type': integer},
            {'op': 'parameter', 'name': 'B', 'type': integer}]}
        definition = {'shared_values': {'calculation': {'parameters': ['A', 'B', 'D'],
            'value': {'knowledge_kind': 'manual_behavior', 'device_family': 'D', 'rules': [
                {'kind': 'expression_write', 'outputs': [
                    {'target': {'kind': 'word', 'parameter': 'D', 'offset': 0}, 'expression': expression}]}]}}}}
        binding = fact('binding', 'effects.documented_processing', scope={
            'models': ['FX5U'], 'forms': ['SYNTH'], 'languages': ['fbd']}, value={
            'definition_ref': 'calculation', 'parameter_bindings': {'A': 'x', 'B': 'y', 'D': 'out'},
            'signature': {'parameter_types': {'x': 'DINT', 'y': 'DINT', 'out': 'DINT'},
                'parameter_directions': {'x': 'input', 'y': 'input', 'out': 'output'},
                'word_device_convention': {'device_unit_bits': 16, 'low_word_offset': 0, 'high_words_follow': True}},
            'output_layout': {'head': 'out', 'word_count': 2}})
        # This interface deliberately declares the two inputs in reverse order.
        signature = fact('declaration', 'parameters.typed_operands', scope=copy.deepcopy(binding['scope']),
            value={'operands': [{'name': 'y', 'type': 'DINT', 'direction': 'input'},
                                {'name': 'x', 'type': 'DINT', 'direction': 'input'},
                                {'name': 'out', 'type': 'DINT', 'direction': 'output'}]})
        return definition, binding, signature

    def test_cross_family_body_reference_preserves_the_callers_scope_and_source(self):
        from plc.instruction_knowledge import validate_family_knowledge
        definition, binding, signature = self.declarations()
        source = dict(definition, base_opcode='SOURCE', facts=[])
        target = {'base_opcode': 'SYNTH', 'shared_values': {'calculation': {
            'shared_ref': {'family': 'SOURCE', 'name': 'calculation'}}}, 'facts': [binding]}
        original = copy.deepcopy([source, target])
        resolved = validate_family_knowledge([source, target], vendor='mitsubishi')
        self.assertEqual(resolved[1]['shared_values']['calculation'], source['shared_values']['calculation'])
        self.assertEqual(resolved[1]['facts'], [binding])
        self.assertEqual([source, target], original)
        resolved[1]['shared_values']['calculation']['value']['device_family'] = 'changed'
        self.assertEqual(resolved[0]['shared_values']['calculation']['value']['device_family'], 'D')
        for broken in ['missing_family', 'missing_body', 'reference_chain']:
            caller = copy.deepcopy(target)
            if broken == 'missing_family': caller['shared_values']['calculation']['shared_ref']['family'] = 'ABSENT'
            elif broken == 'missing_body': caller['shared_values']['calculation']['shared_ref']['name'] = 'absent'
            else: caller['shared_values']['calculation']['shared_ref']['family'] = 'SYNTH'
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                validate_family_knowledge([source, caller], vendor='mitsubishi')

    def test_equivalent_declarations_share_one_relation_and_keep_both_dependencies(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        other = copy.deepcopy(signature); other['id'] = 'reviewed_declaration'
        relation, = _bind_shared_definition(definition, binding, [signature, other])
        self.assertEqual(relation['depends_on'], ['declaration', 'reviewed_declaration'])
        other['status'] = 'unknown'
        self.assertEqual(len(_bind_shared_definition(definition, binding, [signature, other])), 2)
        other['status'] = signature['status']
        other['conditions'] = [{'context': 'program_context', 'equals': 'interrupt'}]
        self.assertEqual(len(_bind_shared_definition(definition, binding, [signature, other])), 2)

    def test_a_replaced_extraction_keeps_only_dependencies_still_used_outside_it(self):
        from plc.instruction_knowledge import merge_family_facts
        parameter = fact('parameter', 'parameters.order')
        behavior = fact('behavior', 'effects.operation', depends_on=['parameter'])
        replacement = fact('replacement', 'parameters.order', replaces=['parameter', 'behavior'])
        current, old = merge_family_facts([parameter, behavior], [replacement])
        self.assertEqual([f['id'] for f in current], ['replacement'])
        self.assertEqual({f['id'] for f in old}, {'parameter', 'behavior'})
        external = fact('constraint', 'constraints.expression', depends_on=['parameter'])
        current, old = merge_family_facts([parameter, behavior, external], [replacement])
        self.assertEqual({f['id'] for f in current}, {'parameter', 'constraint', 'replacement'})
        self.assertEqual([f['id'] for f in old], ['behavior'])

    def test_explicit_alias_binds_the_previous_destination_as_an_input(self):
        from plc.instruction_knowledge import _bind_shared_definition, _definition_value
        from plc.instruction_definition import evaluate_expression
        definition, binding, signature = self.declarations()
        binding['value'].update(parameter_bindings={'A': 'x', 'B': 'y', 'D': 'x'},
                                parameter_aliases=[['A', 'D']])
        binding['value'].pop('output_layout')
        binding['value']['signature'] = {'parameter_types': {'x': 'DINT', 'y': 'DINT'},
                                         'parameter_directions': {'x': 'in_out', 'y': 'input'}}
        signature['value']['operands'] = [{'name': 'y', 'type': 'DINT', 'direction': 'input'},
                                          {'name': 'x', 'type': 'DINT', 'direction': 'in_out'}]
        signature['status'] = 'unknown'
        relation, = _bind_shared_definition(definition, binding, [signature])
        output = relation['value']['rules'][0]['outputs'][0]
        self.assertEqual(output['target']['parameter'], 'x')
        self.assertEqual(evaluate_expression(output['expression'], {'x': 15, 'y': 4}), 11)
        self.assertEqual(relation['status'], 'unknown')
        self.assertEqual(relation['depends_on'], ['declaration'])
        wrong = copy.deepcopy(signature); wrong['value']['operands'][-1]['direction'] = 'output'
        self.assertEqual(_bind_shared_definition(definition, binding, [wrong]), [])
        for broken in ['implicit_alias', 'wrong_group', 'write_only', 'unbound']:
            bad = copy.deepcopy(binding)
            if broken == 'implicit_alias': bad['value'].pop('parameter_aliases')
            elif broken == 'wrong_group': bad['value']['parameter_aliases'] = [['A', 'B']]
            elif broken == 'write_only': bad['value']['signature']['parameter_directions']['x'] = 'output'
            else: bad['value'].pop('signature')
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                _bind_shared_definition(definition, bad, [signature])

    def test_reviewed_declarations_are_applied_before_binding_shared_relations(self):
        from types import SimpleNamespace
        from plc.instruction_knowledge import family_fact_groups, merge_family_facts
        definition, binding, signature = self.declarations()
        for group in (binding, signature):
            group['scope']['models'] = ['FX3U']
        definition.update(vendor='mitsubishi', base_opcode='SYNTH', facts=[binding, signature])
        registry = SimpleNamespace(_family_knowledge=[definition])
        form = {'vendor': 'mitsubishi', 'family': 'SYNTH', 'opcode': 'SYNTH', 'data_bits': 32, 'pulse': False}
        for old_type, old_status in [('ANY16', 'source_verified'), ('DINT', 'conflict')]:
            with self.subTest(old_type=old_type, old_status=old_status):
                old = copy.deepcopy(signature)
                old['value']['operands'][0]['type'] = old_type
                old['status'] = old_status
                with patch('plc.instruction_knowledge._knowledge_form', return_value=form):
                    first = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=[old])
                    relation, = [f for f in first if f['dimension'] == binding['dimension']]
                    self.assertEqual(relation['status'], 'source_verified')
                    self.assertEqual(relation['depends_on'], [signature['id']])
                    current, _ = merge_family_facts([old], first)
                    repeated = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=current)
                self.assertEqual(first, repeated)

    def test_stored_derived_table_cannot_keep_a_replaced_declaration_in_new_bindings(self):
        from types import SimpleNamespace
        from plc.instruction_knowledge import compose_programming_facts
        definition, binding, signature = self.declarations()
        for group in (binding, signature): group['scope']['models'] = ['FX3U']
        old = copy.deepcopy(signature); old['id'] = 'old_declaration'
        signature['replaces'] = [old['id']]
        definition.update(vendor='mitsubishi', base_opcode='SYNTH', facts=[binding, signature])
        stale = copy.deepcopy(old); stale.update(id='assembly:parameters:old', depends_on=[old['id']])
        registry = SimpleNamespace(_family_knowledge=[definition])
        form = {'vendor': 'mitsubishi', 'family': 'SYNTH', 'opcode': 'SYNTH', 'data_bits': 32, 'pulse': False}
        with patch('plc.instruction_knowledge._knowledge_form', return_value=form):
            rows, history = compose_programming_facts(registry, 'SYNTH', cpu='FX3U', existing=[old, stale])
            self.assertNotIn(old['id'], {f['id'] for f in rows})
            self.assertFalse(any(old['id'] in f.get('depends_on', []) for f in rows))
            self.assertIn(old['id'], {f['id'] for f in history})
            repeated, extra = compose_programming_facts(registry, 'SYNTH', cpu='FX3U', existing=rows)
        self.assertEqual(repeated, rows)
        self.assertFalse(extra)

    def test_shared_expression_uses_explicit_names_and_preserves_literals(self):
        from plc.instruction_knowledge import _bind_shared_definition, _definition_value
        from plc.instruction_definition import evaluate_expression, select_fact_dependencies
        definition, binding, signature = self.declarations()
        relation, = _bind_shared_definition(definition, binding, [signature])
        output = relation['value']['rules'][0]['outputs'][0]
        self.assertEqual(output['target']['parameter'], 'out')
        self.assertEqual(evaluate_expression(output['expression'], {'x': 7, 'y': -8}), 15)
        self.assertEqual(relation['value']['device_family'], 'D')
        self.assertEqual(relation['depends_on'], ['declaration'])
        closure = select_fact_dependencies([signature, relation], [relation['id']], opcode='SYNTH', model='FX5U', language='fbd')
        self.assertFalse(closure['gaps']); self.assertTrue(closure['source_verification_complete'])
        self.assertNotIn('behavior', relation['value'])
        original = definition['shared_values']['calculation']['value']
        self.assertEqual(_definition_value(definition, {'definition_ref': 'calculation'}), original)
        output['target']['parameter'] = 'changed'
        self.assertEqual(original['rules'][0]['outputs'][0]['target']['parameter'], 'D')

    def test_explicit_references_bind_nested_positions_and_keep_literal_tokens(self):
        from plc.instruction_knowledge import _definition_value
        definition, binding, _ = self.declarations()
        original = definition['shared_values']['calculation']['value']
        original.update(inputs=[{'parameter_ref': 'A'}, {'parameter_ref': 'B'}],
            output={'parameter_ref': 'D'}, literal='D', region={'first': {'parameter_ref': 'D'}},
            count={'op': 'sub', 'arguments': [{'parameter_ref': 'A'}, {'parameter_ref': 'B'}]})
        bound = _definition_value(definition, binding['value'])
        self.assertEqual(bound['inputs'], ['x', 'y']); self.assertEqual(bound['output'], 'out')
        self.assertEqual(bound['literal'], 'D'); self.assertEqual(bound['region']['first'], 'out')
        self.assertEqual(bound['count']['arguments'], ['x', 'y'])
        for bad in ({'parameter_ref': 'absent'}, {'parameter_ref': 'A', 'fallback': 'B'}, {'parameter_ref': []}):
            with self.subTest(reference=bad), self.assertRaises(ValueError):
                original['region'] = bad
                _definition_value(definition, binding['value'])

    def test_parameter_usage_preserves_port_direction_and_distinct_memory_access(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        definition, binding, signature = self.declarations()
        definition['shared_values']['calculation']['value'] = {'parameter_usage': [
            {'parameter': {'parameter_ref': 'A'}, 'purpose': 'retained_value', 'semantic_access': 'read_write',
             'operation_value_type': {'kind': 'int', 'bits': 32, 'signed': True}},
            {'parameter': {'parameter_ref': 'B'}, 'purpose': 'shift_count', 'semantic_access': 'read',
             'range': {'minimum': 0, 'maximum_operand': {'parameter_ref': 'A'}}},
            {'parameter': {'parameter_ref': 'D'}, 'purpose': 'result_head', 'semantic_access': 'write',
             'occupied_bits_operand': {'parameter_ref': 'B'}}]}
        signature['value']['operands'][0]['purpose'] = 'source_checked_caption'
        binding['dimension'] = 'parameters.usage'
        relation, = _bind_shared_definition(definition, binding, [signature])
        enriched, = parameter_usage_facts([signature, relation])
        by_name = {p['name']: p for p in enriched['value']['operands']}
        self.assertEqual(by_name['x']['direction'], 'input')
        self.assertEqual(by_name['x']['semantic_access'], 'read_write')
        self.assertEqual(by_name['y']['purpose'], 'source_checked_caption')
        self.assertEqual(by_name['y']['semantic_role'], 'shift_count')
        self.assertEqual(by_name['y']['range']['maximum_operand'], 'x')
        self.assertEqual(by_name['out']['occupied_bits_operand'], 'y')
        self.assertEqual(enriched['depends_on'], ['declaration', relation['id']])
        other = copy.deepcopy(signature); other['id'] = 'other-interface'
        self.assertFalse(parameter_usage_facts([other, relation]))
        source = {'kind': 'instruction', 'target': 'SYNTH', 'scope': {'models': ['FX5U']},
                 'value': {'opcode': 'SYNTH', 'target_model': 'FX5U', 'facts': [signature, relation, enriched]}}
        table, = technical_fact_packet(source)['value']['parameter_tables']
        self.assertEqual(len(table['parameters']), 3)
        self.assertEqual(table['parameters'][1]['semantic_access'], 'read_write')

    def test_range_only_claim_enriches_its_bound_parameter_without_inventing_a_purpose(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        definition,binding,signature=self.declarations()
        for group in (binding,signature):group['scope']['models']=['FX3U']
        definition['shared_values']['calculation']['value']={'parameter_usage':[
            {'parameter':{'parameter_ref':'A'},'range':{'minimum':0,'maximum':15}}]}
        binding['dimension']='parameters.usage'
        binding['conditions']=[{'context':'cpu_version','minimum':'2.20'}]
        binding['status']='unknown'
        relation,=_bind_shared_definition(definition,binding,[signature])
        enriched,=parameter_usage_facts([signature,relation])
        by_name={p['name']:p for p in enriched['value']['operands']}
        self.assertEqual(by_name['x']['range'],{'minimum':0,'maximum':15})
        self.assertNotIn('purpose',by_name['x'])
        self.assertNotIn('range',by_name['y'])
        self.assertEqual(enriched['status'],'unknown')
        self.assertEqual(enriched['conditions'],binding['conditions'])
        self.assertEqual(enriched['depends_on'],[signature['id'],relation['id']])
        wrong=dict(signature,id='other-interface')
        self.assertFalse(parameter_usage_facts([wrong,relation]))
        bad=copy.deepcopy(relation)
        bad['value']['parameter_usage'].append({'parameter':'missing','range':{'minimum':1}})
        self.assertFalse(parameter_usage_facts([signature,bad]))

    def test_shared_operand_contract_generates_usage_without_replacing_call_ports(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        definition, binding, signature = self.declarations()
        definition['shared_values']['calculation']['value'] = {'representation': 'native_ladder_instruction',
            'operands': [{'name': {'parameter_ref': 'A'}, 'direction': 'in_out', 'purpose': 'value_to_increment',
                'type': {'kind': 'int', 'bits': 32, 'signed': True},
                'word_device_layout': {'device_unit_bits': 16, 'word_count': 2}},
                {'name': {'parameter_ref': 'B'}, 'direction': 'input', 'purpose': 'shift_count', 'unit': 'bit'},
                {'name': {'parameter_ref': 'D'}, 'direction': 'output', 'purpose': 'device_range_end',
                 'type': {'kind': 'int', 'bits': 16, 'signed': False}, 'value_semantics': 'device_reference'}]}
        binding['dimension'] = 'parameters.usage'
        relation, = _bind_shared_definition(definition, binding, [signature])
        self.assertNotIn('operands', relation['value'])
        enriched, = parameter_usage_facts([signature, relation])
        self.assertEqual([p['name'] for p in enriched['value']['operands']], ['y', 'x', 'out'])
        by_name = {p['name']: p for p in enriched['value']['operands']}
        self.assertEqual(by_name['x']['direction'], 'input')
        self.assertEqual(by_name['x']['semantic_access'], 'read_write')
        self.assertEqual(by_name['x']['operation_value_type'], by_name['x']['result_value_type'])
        self.assertTrue(by_name['x']['word_device_layout']['high_words_follow'])
        self.assertEqual(by_name['x']['word_device_layout']['word_count'], 2)
        self.assertEqual(by_name['out']['type'], 'DINT')
        self.assertEqual(by_name['out']['value_semantics'], 'device_reference')
        self.assertNotIn('result_value_type', by_name['out'])

    def test_shared_composite_storage_and_parameter_conditions_reach_the_same_table(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        from knowledge.instruction_presentation import _parameter_tables
        definition, binding, signature = self.declarations()
        components = [{'purpose': 'first_result', 'offset_words': 0,
                       'type': {'kind': 'int', 'bits': 16, 'signed': True}},
                      {'purpose': 'second_result', 'offset_words': 1,
                       'type': {'kind': 'int', 'bits': 16, 'signed': True}}]
        definition['shared_values']['calculation']['value'] = {'operands': [
            {'name': {'parameter_ref': 'A'}, 'direction': 'input', 'condition': {'not_equal': 0}},
            {'name': {'parameter_ref': 'B'}, 'direction': 'input'},
            {'name': {'parameter_ref': 'D'}, 'direction': 'output', 'purpose': 'packed_results',
             'type': {'kind': 'int', 'bits': 32, 'signed': False}, 'numeric_input_gate': False,
             'components': components, 'word_device_layout': {'word_count': 2, 'device_unit_bits': 16}}]}
        binding['dimension'] = 'parameters.usage'
        binding['conditions'] = [{'context': 'device_state', 'device': 'M0', 'equals': False}]
        relation, = _bind_shared_definition(definition, binding, [signature])
        enriched, = parameter_usage_facts([signature, relation])
        params = {p['name']: p for p in enriched['value']['operands']}
        self.assertEqual(params['x']['condition'], {'not_equal': 0})
        self.assertEqual(params['out']['components'], components)
        self.assertFalse(params['out']['numeric_input_gate'])
        self.assertEqual(params['out']['type'], 'DINT')
        self.assertEqual(enriched['conditions'], binding['conditions'])
        tables, _ = _parameter_tables([signature, enriched], copy.deepcopy)
        packed = next(t for t in tables if any(p.get('components') for p in t['parameters']))
        self.assertEqual(next(p for p in packed['parameters'] if p['name'] == 'out')['components'], components)

    def test_two_result_offsets_do_not_replace_the_packed_parameter_type(self):
        from plc.instruction_knowledge import parameter_usage_facts
        definition, binding, signature = self.declarations()
        integer = {'kind': 'int', 'bits': 16, 'signed': True}
        effect = copy.deepcopy(signature)
        effect.update(id='two-results', dimension='effects.documented_processing', value={'rules': [
            {'kind': 'expression_write', 'outputs': [
                {'target': {'parameter': 'out', 'offset': offset, 'kind': 'word'},
                 'expression': {'op': 'constant', 'value': offset + 1, 'type': integer}}
                for offset in [0, 1]]}]})
        enriched, = parameter_usage_facts([signature, effect])
        result = next(p for p in enriched['value']['operands'] if p['name'] == 'out')
        self.assertEqual(result['type'], 'DINT')
        self.assertNotIn('result_value_type', result)
        self.assertEqual([u['offset'] for u in result['purpose']['uses']], [0, 1])

    def test_interface_binding_requires_its_own_shape_scope_and_source(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        self.assertFalse(_bind_shared_definition(definition, binding, []))
        changes = [({'scope': {'languages': ['ladder']}}, None),
                   ({'sources': [{'source_id': 'unrelated'}]}, None),
                   ({'verification': 'not_performed'}, None),
                   ({}, ('name', 'missing')), ({}, ('type', 'WORD')), ({}, ('direction', 'output'))]
        for fields, operand_change in changes:
            with self.subTest(fields=fields, change=operand_change):
                wrong = copy.deepcopy(signature); wrong.update(fields)
                if operand_change:
                    wrong['value']['operands'][0][operand_change[0]] = operand_change[1]
                self.assertFalse(_bind_shared_definition(definition, binding, [wrong]))
        wrong = dict(signature, conditions=[{'context': 'project_mode', 'equals': 'native_ladder_instruction'}])
        binding['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        self.assertFalse(_bind_shared_definition(definition, binding, [wrong]))

    def test_status_and_conditions_survive_interface_materialization(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        signature['status'] = 'candidate_evidence'
        signature['conditions'] = [{'context': 'cpu_version', 'equals': '2.0'}]
        result, = _bind_shared_definition(definition, binding, [signature])
        self.assertEqual(result['status'], 'candidate_evidence')
        self.assertEqual(result['conditions'], signature['conditions'])
        self.assertEqual(result['scope'], signature['scope'])

    def test_bad_reference_or_partial_operand_map_is_rejected(self):
        from plc.instruction_knowledge import _definition_value, _bind_shared_definition
        definition, binding, signature = self.declarations()
        for updates in ({'definition_ref': 'missing'}, {'parameter_bindings': {'A': 'x'}},
                        {'parameter_bindings': {'A': 'x', 'B': 'x', 'D': 'out'}}, {'rules': []}):
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    _definition_value(definition, dict(binding['value'], **updates))
        binding['value']['signature']['parameter_types'].pop('x')
        binding['value']['signature']['parameter_directions'].pop('x')
        with self.assertRaises(ValueError):
            _bind_shared_definition(definition, binding, [signature])

    def test_expression_types_and_documented_word_layout_reach_parameter_tables(self):
        from plc.instruction_knowledge import _bind_shared_definition, parameter_usage_facts
        definition, binding, signature = self.declarations()
        relation, = _bind_shared_definition(definition, binding, [signature])
        enriched, = parameter_usage_facts([signature, relation])
        for operand in enriched['value']['operands']:
            self.assertEqual(operand['type'], 'DINT')
            self.assertEqual(operand['word_device_layout']['word_count'], 2)
            self.assertEqual(operand['word_device_layout']['low_word_offset'], 0)
        output = next(p for p in enriched['value']['operands'] if p['name'] == 'out')
        self.assertEqual(output['write_layout']['head'], 'out')
        self.assertEqual(output['result_value_type']['bits'], 32)
        source = {'kind': 'instruction', 'target': 'SYNTH', 'scope': {'models': ['FX5U']},
                  'value': {'opcode': 'SYNTH', 'target_model': 'FX5U', 'facts': [signature, relation, enriched]}}
        rendered = technical_fact_packet(source)
        table, = rendered['value']['parameter_tables']
        self.assertEqual([p['name'] for p in table['parameters']], ['y', 'x', 'out'])
        self.assertEqual(table['parameters'][0]['operation_value_type']['bits'], 32)
        relation['value'].pop('word_device_convention')
        enriched, = parameter_usage_facts([signature, relation])
        self.assertTrue(all('word_device_layout' not in p for p in enriched['value']['operands']))

    def test_parameter_claims_join_only_under_the_same_source_conditions_and_status(self):
        from plc.instruction_knowledge import parameter_usage_facts
        _, _, signature = self.declarations()
        signature['value']['operands'][0].pop('purpose', None)
        first = fact('role', 'parameters.usage', scope=copy.deepcopy(signature['scope']), value={
            'parameter_usage': [{'parameter': 'y', 'purpose': 'second_value', 'semantic_access': 'read'}]})
        calculation = fact('calculation', 'effects.operation', scope=copy.deepcopy(signature['scope']),
            value={'inputs': ['y'], 'output': 'out', 'operation': 'copy_value'})
        joined, = parameter_usage_facts([signature, first, calculation])
        self.assertEqual(set(joined['depends_on']), {'declaration', 'role', 'calculation'})
        self.assertEqual(joined['value']['operands'][0]['semantic_access'], 'read')
        self.assertEqual(joined['value']['operands'][0]['purpose']['uses'], [
            {'operation': 'copy_value', 'input_position': 1, 'result': 'out'}, {'semantic_role': 'second_value'}])
        self.assertEqual(parameter_usage_facts([signature, calculation, first]), [joined])
        for change in ({'conditions': [{'context': 'cpu_version', 'equals': '2.0'}]}, {'status': 'candidate_evidence'}):
            with self.subTest(change=change):
                different = dict(first, **change)
                self.assertEqual(len(parameter_usage_facts([signature, different, calculation])), 2)
        boolean_guard = dict(first, conditions=[{'context': 'mode', 'equals': False}])
        numeric_guard = dict(calculation, conditions=[{'context': 'mode', 'equals': 0}])
        self.assertEqual(len(parameter_usage_facts([signature, boolean_guard, numeric_guard])), 2)
        conflicting = copy.deepcopy(first); conflicting['id'] = 'other-role'
        conflicting['value']['parameter_usage'][0]['semantic_access'] = 'write'
        self.assertEqual(len(parameter_usage_facts([signature, conflicting, first])), 2)

    def test_range_layout_keeps_stride_count_and_conditional_reverse_writes(self):
        from plc.instruction_knowledge import parameter_usage_facts
        _, _, signature = self.declarations()
        signature['value']['operands'] = [{'name': 'data', 'type': 'DINT', 'direction': 'input'},
            {'name': 'count', 'type': 'INT', 'direction': 'input'}, {'name': 'dest', 'type': 'DINT', 'direction': 'output'}]
        for kind in ('range_fill', 'range_copy'):
            with self.subTest(kind=kind):
                rule = {'kind': kind, 'source': 'data', 'destination': 'dest', 'count_operand': 'count', 'element_bits': 32}
                if kind == 'range_fill':rule['destination_step_words'] = 2
                else:
                    rule['direction_selector'] = {'device': 'M100', 'false': {'source': 'data', 'destination': 'dest'},
                                                  'true': {'source': 'dest', 'destination': 'data'}}
                effect = fact('range', 'effects.documented_processing', scope=copy.deepcopy(signature['scope']),
                    value={'knowledge_kind': 'manual_behavior', 'rules': [rule],
                           'word_device_convention': {'device_unit_bits': 16, 'low_word_offset': 0}})
                enriched, = parameter_usage_facts([signature, effect])
                operands = {p['name']: p for p in enriched['value']['operands']}
                self.assertEqual(operands['dest']['write_layout']['count_operand'], 'count')
                self.assertEqual(operands['dest']['word_device_layout']['word_count'], 2)
                if kind == 'range_copy':
                    self.assertEqual(operands['data']['direction'], 'input')
                    self.assertEqual(operands['data']['write_layout']['when_device_state'], {'device': 'M100', 'equals': True})
                    self.assertFalse(operands['dest']['write_layout']['when_device_state']['equals'])
                else:
                    self.assertEqual(operands['dest']['write_layout']['destination_step_words'], 2)
                    self.assertNotIn('write_layout', operands['data'])

    def test_shared_conditions_qualify_native_and_bound_interfaces(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        condition = {'context': 'cpu_version', 'minimum_by_model': {'FX5U': '2.0'}}
        definition['shared_values']['calculation']['conditions'] = [condition]
        native = copy.deepcopy(binding); native['value'] = {'definition_ref': 'calculation'}
        self.assertEqual(_bind_shared_definition(definition, native, [])[0]['conditions'], [condition])
        bound, = _bind_shared_definition(definition, binding, [signature])
        self.assertEqual(bound['conditions'], [condition])
        self.assertEqual(signature.get('conditions'), None)

    def test_core_behavior_with_native_contract_stays_in_native_context(self):
        from plc.instruction_relations import normalize_native_interface_facts
        order = fact('registry:parameters.order', 'parameters.order', value={'symbols': ['A', 'D']})
        behavior = fact('core', 'effects.synthetic', value={'behavior': 'copy_value'}, depends_on=[order['id']])
        unrelated = fact('documentation', 'effects.operation', value={'operation': 'copy_value'})
        corrected, originals = normalize_native_interface_facts([order, behavior, unrelated])
        self.assertEqual(originals, [order, behavior])
        self.assertEqual(corrected[1]['scope']['languages'], ['ladder'])
        self.assertEqual(corrected[1]['conditions'], [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}])
        self.assertEqual(corrected[1]['value'], behavior['value'])
        self.assertEqual(corrected[2], unrelated)
        self.assertEqual(normalize_native_interface_facts(corrected), (corrected, []))

    def test_family_refresh_retains_changed_claims_but_not_identical_source_copies(self):
        from plc.instruction_knowledge import merge_family_facts
        old = fact('shared', 'effects.synthetic', value={'flag': False})
        fresh = copy.deepcopy(old); fresh['value']['flag'] = 0
        current, originals = merge_family_facts([old], [fresh])
        self.assertEqual(originals, [old]); self.assertEqual(current, [fresh])
        repeated, originals = merge_family_facts(current, [fresh])
        self.assertEqual(repeated, current); self.assertFalse(originals)
        fresh = copy.deepcopy(old); fresh['sources'] = [{'source_id': 'new'}]
        current, originals = merge_family_facts([old], [fresh])
        self.assertFalse(originals); self.assertEqual(current[0]['sources'], [*old['sources'], *fresh['sources']])

    def test_unchanged_modifier_facts_are_not_added_to_history(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        proof = fact('forms', 'parameters.variant_family', value={'section_variants': ['ZCP', 'ZCPP']},
                     scope={'models': ['FX1N'], 'forms': ['ZCPP'], 'languages': ['ladder']})
        composed, _ = compose_programming_facts(registry, 'ZCPP', cpu='FX1N', existing=[proof])
        self.assertTrue(any(f['id'].startswith('modifier:execution') for f in composed))
        repeated, replaced = compose_programming_facts(registry, 'ZCPP', cpu='FX1N', existing=composed)
        self.assertEqual(repeated, composed); self.assertFalse(replaced)


class SourceFactRefreshTests(unittest.TestCase):
    def test_source_refinement_keeps_conditions_and_original(self):
        from scripts.compile_instruction_semantics import merge_source_facts
        old = fact('operation','effects.operation',value={'operation':'multiply','inputs':['a','b'],'output':'d'},
            scope={'models':['FX5U'],'forms':['SYNTH']},conditions=[{'context':'option','equals':True}])
        fresh = copy.deepcopy(old)
        fresh['value']['output_layout'] = {'head':'d','word_count':2}
        rows, originals = merge_source_facts([old],[fresh])
        self.assertEqual(originals,[old]); self.assertEqual(rows,[fresh])
        self.assertEqual(merge_source_facts(rows,[fresh]),(rows,[]))
        fresh['conditions'] = []
        rows, originals = merge_source_facts([old],[fresh])
        self.assertFalse(originals); self.assertEqual(len(rows),2); self.assertEqual(rows[0],old)

    def test_checked_refinement_resolves_missing_fields_without_conflict(self):
        from scripts.compile_instruction_semantics import merge_source_facts
        old = fact('parameters', 'parameters.typed_operands', status='candidate_evidence',
                   verification='not_performed', value={'operands': [{'name': 'result', 'type': 'unknown'}]})
        fresh = copy.deepcopy(old)
        fresh.update(status='source_verified', verification='source_checked')
        fresh['value']['operands'][0].update(type='DWORD', direction='output')
        rows, originals = merge_source_facts([old], [fresh])
        self.assertEqual(rows, [fresh])
        self.assertEqual(originals, [old])
        self.assertEqual(old['value']['operands'][0]['type'], 'unknown')
        conflict = dict(old, status='conflict')
        rows, originals = merge_source_facts([conflict], [fresh])
        self.assertFalse(originals)
        self.assertEqual(rows[0]['status'], 'conflict')
        self.assertEqual(len(rows), 2)

    def test_parameter_extraction_method_can_change_without_conflicting_values(self):
        from scripts.compile_instruction_semantics import merge_source_facts
        old = fact('parameters', 'parameters.typed_operands', value={'operands': [
            {'name': 'value', 'type': 'DINT', 'direction': 'input',
             'type_basis': 'old_cell_reader', 'direction_basis': 'old_group_reader'}]})
        fresh = copy.deepcopy(old)
        fresh['value']['operands'][0].update(type_basis='bound_type_cell',
            direction_basis='bound_pin_side', purpose={'source_description': 'Value to transfer'})
        rows, originals = merge_source_facts([old], [fresh])
        self.assertEqual(rows, [fresh]); self.assertEqual(originals, [old])
        self.assertEqual(merge_source_facts(rows, [fresh]), (rows, []))
        for field, changed in [('type', 'INT'), ('direction', 'output')]:
            with self.subTest(field=field):
                incompatible = copy.deepcopy(fresh)
                incompatible['value']['operands'][0][field] = changed
                rows, originals = merge_source_facts([old], [incompatible])
                self.assertFalse(originals)
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row['status'] == 'conflict' for row in rows))

    def test_source_only_refresh_retains_sources_without_duplicate_history(self):
        from scripts.compile_instruction_semantics import merge_source_facts, compact_fact_history
        old = fact('claim', 'effects.synthetic', value={'flag': False})
        fresh = copy.deepcopy(old); fresh['sources'].append({'source_id': 'second'})
        rows, originals = merge_source_facts([old], [fresh])
        self.assertEqual(rows, [fresh]); self.assertFalse(originals)
        changed = copy.deepcopy(old); changed['value']['flag'] = 0
        weaker = dict(old, status='unknown')
        scoped = dict(old, conditions=[{'context': 'mode', 'equals': 'other'}])
        dependent = dict(old, depends_on=['older-parent'])
        absent_source = dict(old, sources=[{'source_id': 'no-longer-present'}])
        history = [old, changed, weaker, scoped, dependent, absent_source]
        retained = compact_fact_history(rows, history)
        self.assertEqual(retained, history[1:])
        self.assertEqual(history[0], old)
        self.assertTrue(all(source in rows[0]['sources'] for source in old['sources']))

    def test_source_disagreement_is_never_treated_as_refinement(self):
        from scripts.compile_instruction_semantics import merge_source_facts
        old = fact('operation','effects.operation',value={'operation':'multiply','inputs':['a','b'],'output':'d'})
        fresh = copy.deepcopy(old); fresh['value']['output'] = 'other'
        rows, originals = merge_source_facts([old],[fresh])
        self.assertFalse(originals); self.assertEqual(len(rows),2)
        self.assertTrue(all(f['status']=='conflict' for f in rows))
        fresh = copy.deepcopy(old); fresh['sources'] = [{'source_id':'another'}]
        fresh['value']['output_layout'] = {'head':'d','word_count':2}
        rows, originals = merge_source_facts([old],[fresh])
        self.assertFalse(originals); self.assertEqual(rows[0],old); self.assertEqual(len(rows),2)


class SourceNamespaceRefreshTests(unittest.TestCase):
    def test_recompile_replaces_stale_claims_and_can_demote_incorrect_signatures(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        old = fact('authored:sample:call', 'parameters.st_signature', value={'available': True})
        stale = fact('authored:sample:stale', 'parameters.order')
        registry = fact('registry:availability', 'identity.applicability', value={'core_call_available': False})
        other = fact('authored:other:retained', 'effects.operation')
        fresh = copy.deepcopy(old); fresh.update(status='candidate_evidence', value={'available': 'unknown'})
        source = [registry, old, stale, other]
        current, history = replace_source_namespaces(source, [fresh], ['authored:sample:'])
        self.assertEqual(current, [registry, other, fresh]); self.assertEqual(history, [old, stale])
        self.assertEqual(source, [registry, old, stale, other])
        self.assertEqual(replace_source_namespaces(current, [fresh], ['authored:sample:']), (current, []))

    def test_current_snapshot_conflicts_remain_conflicts(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        one = fact('authored:sample:slot', 'parameters.types', status='conflict', value={'type': 'INT'})
        two = fact('authored:sample:slot:variant:2', 'parameters.types', status='conflict', value={'type': 'DINT'})
        current, history = replace_source_namespaces([], [one, two], ['authored:sample:'])
        self.assertEqual(current, [one, two]); self.assertFalse(history)
        with self.assertRaises(ValueError):replace_source_namespaces([], [one, one], ['authored:sample:'])

    def test_snapshot_cannot_remove_an_external_prerequisite(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        source = fact('authored:sample:binding', 'parameters.types')
        dependent = fact('core', 'effects.synthetic', depends_on=[source['id']])
        with self.assertRaisesRegex(ValueError, 'external prerequisites'):
            replace_source_namespaces([source, dependent], [], ['authored:sample:'])
        current, history = replace_source_namespaces([source], [], ['authored:sample:'])
        self.assertFalse(current); self.assertEqual(history, [source])
        with self.assertRaises(ValueError):replace_source_namespaces([source], [], [''])

    def test_unused_generated_bindings_are_rebuilt_but_external_chains_stay_protected(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        source = fact('authored:sample:old', 'parameters.types')
        generated = fact('assembly:parameters:old', 'parameters.typed_operands', depends_on=[source['id']])
        current, history = replace_source_namespaces([source, generated], [], ['authored:sample:'])
        self.assertEqual(current, [generated]); self.assertEqual(history, [source])
        external = fact('core', 'effects.synthetic', depends_on=[generated['id']])
        with self.assertRaisesRegex(ValueError, 'external prerequisites'):
            replace_source_namespaces([source, generated, external], [], ['authored:sample:'])

    def test_published_source_identity_prevents_duplicate_unchanged_history(self):
        from knowledge.fact_corpus import source_reference
        from scripts.compile_instruction_semantics import replace_source_namespaces
        source = {'id': '12', 'manual_id': 'synthetic', 'manual_number': 'SYNTH', 'revision': 'A', 'pdf_pages': [1]}
        old = fact('authored:sample:value', 'parameters.types', sources=[source_reference(source)])
        fresh = dict(old, sources=[source])
        current, history = replace_source_namespaces([old], [fresh], ['authored:sample:'])
        self.assertEqual(current, [old]); self.assertFalse(history)


class SourceFactBundleTests(unittest.TestCase):
    @staticmethod
    def write_bundle(root, name, data):
        path = root / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_multiple_packages_merge_owner_facts_and_retain_conflicts(self):
        from pathlib import Path
        import tempfile
        from scripts.compile_instruction_semantics import load_source_facts

        owner = ('FX5U', 'SYNTH')
        original = fact('authored:sample:claim', 'effects.operation', value={'operation': 'left'})
        disagreement = copy.deepcopy(original)
        disagreement['value'] = {'operation': 'right'}
        addition = fact('authored:other:claim', 'effects.operation', value={'operation': 'other'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_bundle(root, 'first.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [original]}]})
            second = self.write_bundle(root, 'second.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [disagreement, addition]}]})
            batches, languages = load_source_facts([first, second])

        self.assertIsNone(languages)
        by_id = {row['id']: row for row in batches[owner]}
        self.assertEqual(set(by_id), {original['id'], original['id'] + ':variant:2', addition['id']})
        self.assertEqual(by_id[original['id']]['status'], 'conflict')
        self.assertEqual(by_id[original['id'] + ':variant:2']['status'], 'conflict')
        self.assertEqual(by_id[original['id'] + ':variant:2']['value'], disagreement['value'])

    def test_replacement_namespaces_merge_across_packages_and_reject_ambiguous_snapshots(self):
        from pathlib import Path
        import tempfile
        from scripts.compile_instruction_semantics import load_source_facts

        owner = ('FX5U', 'SYNTH')
        alpha = fact('authored:alpha:first', 'effects.operation')
        beta = fact('authored:beta:second', 'effects.operation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_bundle(root, 'first.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [alpha]}]})
            second = self.write_bundle(root, 'second.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [beta]}]})
            batches, _ = load_source_facts(
                [first, second], replace_source_prefixes=['authored:alpha:', 'authored:beta:'])
            self.assertEqual({row['id'] for row in batches[owner]}, {alpha['id'], beta['id']})

            overlap = self.write_bundle(root, 'overlap.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1],
                 'facts': [fact('authored:alpha:third', 'effects.operation')]}]})
            with self.assertRaisesRegex(ValueError, 'Ambiguous replacement snapshot'):
                load_source_facts([first, overlap], replace_source_prefixes=['authored:alpha:'])

    def test_empty_source_snapshot_is_explicit_and_exact_owner_scoped(self):
        from pathlib import Path
        import tempfile
        from scripts.compile_instruction_semantics import load_source_facts

        owner = ('FX1N', 'A')
        prefix = 'source:cpu_availability:'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            correction = self.write_bundle(root, 'correction.json', {
                'entries': [{'target_model': owner[0], 'opcode': owner[1], 'facts': []}],
                'empty_source_replacements': [{'target_model': owner[0], 'opcode': owner[1],
                                               'prefix': prefix}]})
            batches, languages, replacements = load_source_facts(
                [correction], return_owner_replacements=True)
            self.assertEqual(batches, {owner: []})
            self.assertIsNone(languages)
            self.assertEqual(replacements, frozenset({(owner[0], owner[1], prefix)}))

            unmarked = self.write_bundle(root, 'unmarked.json', {
                'entries': [{'target_model': owner[0], 'opcode': owner[1], 'facts': []}]})
            batches, languages, replacements = load_source_facts(
                [unmarked], return_owner_replacements=True)
            self.assertEqual(batches, {owner: []})
            self.assertEqual(replacements, frozenset())

            missing_owner = self.write_bundle(root, 'missing-owner.json', {
                'entries': [], 'empty_source_replacements': [{'target_model': owner[0],
                    'opcode': owner[1], 'prefix': prefix}]})
            with self.assertRaisesRegex(ValueError, 'owner has no entry'):
                load_source_facts([missing_owner], return_owner_replacements=True)

            not_empty = self.write_bundle(root, 'not-empty.json', {
                'entries': [{'target_model': owner[0], 'opcode': owner[1], 'facts': [
                    fact(prefix + 'existing', 'constraints.cpu_availability')]}],
                'empty_source_replacements': [{'target_model': owner[0], 'opcode': owner[1],
                                               'prefix': prefix}]})
            with self.assertRaisesRegex(ValueError, 'contains facts'):
                load_source_facts([not_empty], return_owner_replacements=True)

            other_namespace = self.write_bundle(root, 'other-namespace.json', {
                'entries': [{'target_model': owner[0], 'opcode': owner[1],
                             'facts': [fact(prefix + 'incoming', 'constraints.cpu_availability')]}]})
            with self.assertRaisesRegex(ValueError, 'another --source-facts package'):
                load_source_facts([correction, other_namespace], return_owner_replacements=True)

    def test_language_facts_for_the_same_target_merge_across_packages(self):
        from pathlib import Path
        import tempfile
        from scripts.compile_instruction_semantics import load_source_facts

        def language_fact(identity, value):
            target, language = 'ST.CALL', 'st'
            return {'target': target, 'language': language, 'facts': [{
                'id': identity, 'dimension': 'parameters.1.range', 'value': {'limit': value},
                'scope': {'models': ['FX5U'], 'forms': [target], 'languages': [language]},
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_bundle(root, 'first.json', {'language_facts': [language_fact('lang:first', 1)]})
            second = self.write_bundle(root, 'second.json', {'language_facts': [language_fact('lang:second', 2)]})
            batches, languages = load_source_facts([first, second])

        self.assertEqual(batches, {})
        self.assertEqual({row['id'] for row in languages[0]['facts']}, {'lang:first', 'lang:second'})

    def test_cli_accepts_repeated_source_facts_and_forwards_selected_owner_refresh(self):
        from pathlib import Path
        import sys
        import tempfile
        from unittest.mock import patch
        import scripts.compile_instruction_semantics as compiler

        owner = ('FX5U', 'SYNTH')
        first_fact = fact('first', 'effects.operation')
        second_fact = fact('second', 'effects.operation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_bundle(root, 'first.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [first_fact]}],
                'empty_source_replacements': [{'target_model': owner[0], 'opcode': owner[1],
                                               'prefix': 'source:cpu_availability:'}]})
            second = self.write_bundle(root, 'second.json', {'entries': [
                {'target_model': owner[0], 'opcode': owner[1], 'facts': [second_fact]}]})
            argv = ['compile_instruction_semantics.py', '--input-manifest', str(root / 'input.manifest'),
                    '--source-facts', str(first), '--source-facts', str(second),
                    '--output-dir', str(root / 'output'), '--source-owners-only',
                    '--refresh-reviewed-calls', '--version', 'synthetic:multi-package']
            with patch.object(sys, 'argv', argv), \
                    patch.object(compiler, 'compile_semantics', return_value={'ok': True}) as compile_call, \
                    patch('builtins.print'):
                compiler.main()

        kwargs = compile_call.call_args.kwargs
        self.assertEqual({row['id'] for row in kwargs['source_batches'][owner]},
                         {first_fact['id'], second_fact['id']})
        self.assertTrue(kwargs['source_owners_only'])
        self.assertTrue(kwargs['refresh_reviewed_calls'])
        self.assertEqual(kwargs['language_batches'], None)
        self.assertEqual(kwargs['empty_source_replacements'],
                         frozenset({(owner[0], owner[1], 'source:cpu_availability:')}))

    def test_cli_keeps_legacy_single_source_fact_call(self):
        from pathlib import Path
        import sys
        import tempfile
        from unittest.mock import patch
        import scripts.compile_instruction_semantics as compiler

        source = fact('legacy', 'effects.operation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.write_bundle(root, 'source.json', {'entries': [
                {'target_model': 'FX5U', 'opcode': 'SYNTH', 'facts': [source]}]})
            argv = ['compile_instruction_semantics.py', '--input-manifest', str(root / 'input.manifest'),
                    '--source-facts', str(package), '--output-dir', str(root / 'output'),
                    '--version', 'synthetic:legacy-single-package']
            with patch.object(sys, 'argv', argv), \
                    patch.object(compiler, 'compile_semantics', return_value={'ok': True}) as compile_call, \
                    patch('builtins.print'):
                compiler.main()

        kwargs = compile_call.call_args.kwargs
        self.assertEqual(kwargs['source_batches'][('FX5U', 'SYNTH')], [source])
        self.assertFalse(kwargs['source_owners_only'])
        self.assertFalse(kwargs['refresh_reviewed_calls'])


class SourcePageGeometryTests(unittest.TestCase):
    def test_missing_geometry_uses_own_pdf_without_changing_source(self):
        import sqlite3
        from types import SimpleNamespace
        from tools.fact_source_geometry import SourcePageGeometry
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE chunks (manual_id,manual_number,revision,source_file)')
        source.execute("INSERT INTO chunks VALUES ('manual','SYNTH','A','synthetic.pdf')")
        words = [{'text':'data','x0':1,'x1':2,'top':1,'bottom':2}]
        page = unittest.mock.Mock(width=100,height=200)
        page.extract_words.return_value = words; page.find_tables.return_value = []
        document = SimpleNamespace(pages=[page],close=unittest.mock.Mock())
        opener = unittest.mock.Mock(return_value=document)
        rows = [{'pdf_page':1,'word_geometry_json':'[]','visual_json':json.dumps({'source_applicability':{'cpu':'unknown'}})}]
        sql = 'SELECT pdf_page,word_geometry_json,visual_json FROM page_artifacts WHERE manual_id=?'
        with patch.dict('sys.modules',{'pdfplumber':SimpleNamespace(open=opener)}):
            with SourcePageGeometry(source,'synthetic_sources',{'manual':{'number':'SYNTH','revision':'A'}}) as wrapper:
                actual = wrapper.hydrate_page_rows(rows,sql,('manual',))
                self.assertEqual(json.loads(actual[0]['word_geometry_json']),words)
                self.assertEqual(json.loads(actual[0]['visual_json'])['source_applicability'],{'cpu':'unknown'})
                self.assertEqual(wrapper.hydrate_page_rows(rows,sql,('manual',)),actual)
                self.assertEqual(wrapper.hydrate_page_rows(actual,sql,('manual',)),actual)
                self.assertEqual(wrapper.hydrate_page_rows(rows,sql,('other',)),rows)
        self.assertEqual(rows[0]['word_geometry_json'],'[]'); self.assertEqual(opener.call_count,1)
        self.assertEqual(page.extract_words.call_count,1); document.close.assert_called_once()

    def test_thin_final_border_keeps_output_and_merged_direction(self):
        try:
            import pdfplumber
        except ImportError:
            self.skipTest('Offline PDF compiler dependency is unavailable')
        from pathlib import Path
        import tempfile
        from tools.fact_source_geometry import SourcePageGeometry
        commands = []
        def line(x0, top, x1, bottom):
            commands.append(f'{x0} {300-bottom} {x1-x0 or 0.48} {bottom-top or 0.48} re f')
        def label(x, top, value):
            commands.append(f'BT /F1 6 Tf {x} {300-top} Td ({value}) Tj ET')
        for x in (20, 120, 160, 240, 300):
            line(x, 30, x, 55)
        for x in (120, 160, 240):
            line(x, 30, x, 135)
        for y, left in ((30,20),(55,20),(75,120),(95,20),(115,120),(135,20)):
            line(left, y, 300, y)
        # A distant navigation tab shares the default edge-snapping band.
        line(330, 132, 350, 132); line(330, 132, 330, 150); line(350, 132, 350, 150)
        for x, value in ((22,'Input/output argument'),(122,'Variable'),(162,'Description'),(242,'Data type')):
            label(x, 42, value)
        for y, name, description, kind in ((65,'s1','First input','INT'),(85,'s2','Second input','INT'),
                                           (105,'ENO','Status','BOOL'),(125,'d','Output','DWORD')):
            for x, value in ((122,name),(162,description),(242,kind)):
                label(x, y, value)
        label(22,65,'Input argument'); label(22,105,'Output argument')
        content = '\n'.join(commands).encode('ascii')
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 360 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
        data, offsets = b'%PDF-1.4\n', []
        for index, obj in enumerate(objects, 1):
            offsets.append(len(data)); data += str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        start = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets)
        data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'table.pdf'; path.write_bytes(data)
            with SourcePageGeometry(None, directory, {}) as wrapper:
                wrapper.documents['synthetic'] = pdfplumber.open(path)
                _, geometry = wrapper._page('synthetic', 1)
                cells = [cell for table in geometry['parameter_tables'] for cell in table['cells']]
                output = next(cell for cell in cells if cell['text'] == 'd')
                center = (output['bbox'][1] + output['bbox'][3])/2
                self.assertTrue(any(cell['text'] == 'DWORD' and cell['bbox'][1] <= center < cell['bbox'][3] for cell in cells))
                self.assertTrue(any(cell['text'] == 'Output argument' and cell['bbox'][1] <= center < cell['bbox'][3] for cell in cells))

    def test_parameter_reader_can_fill_missing_cells_without_replacing_indexed_words(self):
        import sqlite3
        from tools.fact_source_geometry import SourcePageGeometry
        from tools.fact_applied_review import _parameter_source_pages
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.row_factory = sqlite3.Row
        source.execute('CREATE TABLE page_artifacts (manual_id,pdf_page,layout_text,word_geometry_json,instruction_opcode,section,visual_json)')
        words = [{'text': 'original', 'x0': 1, 'x1': 2, 'top': 1, 'bottom': 2}]
        tables = [{'bbox': [0, 5, 10, 15], 'cells': []}]
        source.execute('INSERT INTO page_artifacts VALUES (?,?,?,?,?,?,?)',
            ('manual', 2, 'indexed layout', json.dumps(words), 'SYNTH', '',
             json.dumps({'source_applicability': {'cpu': 'unknown'}})))
        with SourcePageGeometry(source, 'synthetic_sources', {'manual': {}}) as wrapper:
            with patch.object(wrapper, '_page', return_value=([], {'parameter_tables': tables})) as read_pdf:
                actual = _parameter_source_pages(wrapper, {'manual_id': 'manual', 'pdf_pages': [2]})
                read_pdf.assert_called_once_with('manual', 2)
                self.assertEqual(actual[0]['words'], words)
                self.assertEqual(actual[0]['layout'], 'indexed layout')
                self.assertEqual(actual[0]['parameter_tables'], tables)
        self.assertEqual(source.execute('SELECT visual_json FROM page_artifacts').fetchone()[0],
                         json.dumps({'source_applicability': {'cpu': 'unknown'}}))

    def test_legacy_continuations_exclude_navigation_and_memo_but_keep_real_text(self):
        from tools.fact_source_geometry import _legacy_continuation_text
        header = 'FX Series Programmable Controllers Applied Instructions 5\n'
        self.assertEqual(_legacy_continuation_text(header + 'MEMO\n5-2'), '')
        self.assertEqual(_legacy_continuation_text(header + '5-2'), '')
        navigation = 'Applied Instructions:\nFNC 00 - 09 5-4\nFNC 10 - 19 5-8\nFNC 20 - 29 5-12'
        self.assertEqual(_legacy_continuation_text(header + navigation), '')
        detail = 'Input S is evaluated before output D is updated.\nS D\n3 4'
        self.assertEqual(_legacy_continuation_text(header + detail + '\n5-2'), detail)

    def test_changed_revision_is_not_used_as_geometry(self):
        import sqlite3
        from tools.fact_source_geometry import SourcePageGeometry
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE chunks (manual_id,manual_number,revision,source_file)')
        source.execute("INSERT INTO chunks VALUES ('manual','SYNTH','B','synthetic.pdf')")
        with SourcePageGeometry(source,'synthetic_sources',{'manual':{'number':'SYNTH','revision':'A'}}) as wrapper:
            with self.assertRaisesRegex(ValueError,'revision mismatch'):
                wrapper.hydrate_page_rows([{'pdf_page':1,'word_geometry_json':'[]'}],
                    'SELECT pdf_page,word_geometry_json FROM page_artifacts WHERE manual_id=?',('manual',))


class SourceInterfaceSemanticTests(unittest.TestCase):
    def fact(self, identity, dimension, value, language='ladder', **extra):
        return {'id':identity,'dimension':dimension,'value':value,
            'scope':{'models':['FX5U'],'forms':['ARBITRARY'],'languages':[language]},
            'status':'source_verified','verification':'source_checked',
            'sources':[{'manual_id':'synthetic','revision':'A','pdf_pages':[7]}],**extra}

    def signature(self, names=('a','b','result')):
        return self.fact('signature','parameters.typed_operands',{'declaration_kind':'instruction',
            'operands':[{'name':name,'type':{'kind':'int','bits':16,'signed':True},
                         'direction':'output' if i==len(names)-1 else 'input'} for i,name in enumerate(names)]})

    def test_same_names_cannot_attach_an_unbound_or_different_interface_relation(self):
        from plc.instruction_knowledge import parameter_usage_facts
        sig=self.signature()
        base=self.fact('effect','effects.dataflow',{'operation':'copy','inputs':['a'],'output':'result'})
        for mutation in ('missing_input','other_kind','other_variant','other_source'):
            effect=copy.deepcopy(base)
            if mutation=='missing_input':effect['value']['inputs']=['not_a_parameter']
            elif mutation=='other_kind':effect['value']['declaration_kind']='standard_function'
            elif mutation=='other_variant':effect['value']['instruction_variant']='other_overload'
            else:effect['sources'][0]['pdf_pages']=[99]
            with self.subTest(mutation=mutation):
                before=copy.deepcopy([sig,effect])
                self.assertEqual(parameter_usage_facts([sig,effect]),[])
                self.assertEqual([sig,effect],before)
        self.assertTrue(parameter_usage_facts([sig,base]))

    def test_explicit_relation_order_produces_typed_offsets_without_using_the_opcode(self):
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_definition import evaluate_expression
        sig=self.signature()
        effect=self.fact('comparison','effects.compare_outputs',{'operands':['a','b'],
            'relations':['greater','equal','less'],'result_start':'result','result_bit_count':3})
        relation=bind_relation_facts([sig,effect])[0]
        outputs=relation['value']['outputs']
        self.assertEqual([o['target']['offset'] for o in outputs],[0,1,2])
        for a,b,expected in [(5,2,[True,False,False]),(-4,-4,[False,True,False]),(-10,2,[False,False,True])]:
            self.assertEqual([evaluate_expression(o['expression'],{'a':a,'b':b}) for o in outputs],expected)
        self.assertNotIn('behavior',relation['value'])
        effect['value']['relations']=['less','greater','equal']
        outputs=bind_relation_facts([sig,effect])[0]['value']['outputs']
        self.assertEqual([evaluate_expression(o['expression'],{'a':5,'b':2}) for o in outputs],[False,True,False])

    def test_interval_bound_policy_is_source_data_and_unstated_policy_remains_unknown(self):
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_definition import evaluate_expression
        from knowledge.instruction_presentation import substantive_effect
        sig=self.signature(('lower','upper','tested','result'))
        effect=self.fact('zone','effects.zone_compare',{'lower':'lower','upper':'upper','value':'tested',
            'result_start':'result','result_bit_count':3,'reversed_bounds':'effective_upper_equals_lower'})
        value=bind_relation_facts([sig,effect])[0]['value']
        for lower,upper,tested,expected in [(-5,10,-6,[1,0,0]),(-5,10,-5,[0,1,0]),(-5,10,10,[0,1,0]),
            (-5,10,11,[0,0,1]),(7,7,7,[0,1,0]),(120,100,100,[1,0,0]),(120,100,120,[0,1,0]),(120,100,121,[0,0,1])]:
            actual=[evaluate_expression(o['expression'],{'lower':lower,'upper':upper,'tested':tested}) for o in value['outputs']]
            self.assertEqual(actual,list(map(bool,expected)))
        self.assertEqual(value['output_layout']['bit_count'],3)
        del effect['value']['reversed_bounds']
        partial=bind_relation_facts([sig,effect])[0]
        self.assertIn('reversed_bounds_behavior_not_stated',partial['value']['unresolved_items'])
        self.assertFalse(substantive_effect(partial))
        self.assertFalse(evaluate_expression(partial['value']['preconditions'][0],{'lower':120,'upper':100}))

    def test_variadic_binding_preserves_actual_arguments_return_and_normalization_idempotence(self):
        try:
            from hypothesis import given,settings,strategies as st,example
        except ImportError:
            self.skipTest('Hypothesis is optional')
        from plc.instruction_relations import normalize_interface_parameters,bind_relation_facts
        from plc.instruction_knowledge import parameter_usage_facts
        @settings(max_examples=40,deadline=None,database=None)
        @example(2,'s')
        @example(28,'s')
        @given(st.integers(min_value=2,max_value=28),st.sampled_from(['s','arg_','value']))
        def check(count,prefix):
            params=[{'name':prefix+str(i),'type':'INT','direction':'input'} for i in range(1,count+1)]
            group={'name_prefix':prefix,'minimum':2,'maximum':28}
            call=self.fact('call','parameters.st_signature',{'declaration_kind':'standard_function','available':True,
                'parameters':params,'returns':{'name':'answer','type':'INT'},'variadic_parameters':[group]},'st')
            signature=self.fact('call:operands','parameters.typed_operands',{'declaration_kind':'standard_function',
                'operands':copy.deepcopy(params),'operand_count':count},'st')
            effect=self.fact('operation','effects.documented_operation',{'declaration_kind':'standard_function',
                'operation':'product','inputs':[{'parameter_group':prefix}],'output':'answer'},'st')
            original=[call,signature,effect];before=copy.deepcopy(original)
            normalized,_=normalize_interface_parameters(original)
            again,changed=normalize_interface_parameters(normalized)
            self.assertEqual(normalized,again);self.assertEqual(changed,[]);self.assertEqual(original,before)
            relations=bind_relation_facts(normalized)
            self.assertEqual(len(relations),1)
            relation=relations[0]
            self.assertEqual(set(relation['value']['inputs']),{p['name'] for p in params})
            self.assertEqual(relation['value']['input_groups'],[{**group,'members':[p['name'] for p in params],
                                                               'selection':'supplied_arguments_in_order'}])
            self.assertEqual(relation['value']['operand_binding']['output']['kind'],'return_value')
            tables=parameter_usage_facts([*normalized,*relations])
            self.assertEqual(len(tables),1)
            self.assertEqual([p['name'] for p in tables[0]['value']['operands']],[p['name'] for p in params])
            self.assertTrue(all(p.get('purpose') for p in tables[0]['value']['operands']))
            # Removing the declaration of the parameter group must withhold the
            # group relation, even though all default argument names still match.
            lacking=[copy.deepcopy(f) for f in normalized]
            for fact in lacking:fact['value'].pop('variadic_parameters',None)
            self.assertEqual(bind_relation_facts(lacking),[])
        check()

    def test_identical_parameter_names_do_not_merge_different_declaration_kinds(self):
        from knowledge.instruction_presentation import _parameter_tables
        one=self.signature();two=copy.deepcopy(one)
        two['id']='standard';two['value']['declaration_kind']='standard_function'
        tables,used=_parameter_tables([one,two],copy.deepcopy)
        self.assertEqual(len(tables),2)
        self.assertEqual({t['declaration_kind'] for t in tables},{'instruction','standard_function'})
        self.assertNotEqual(used['signature'],used['standard'])

    def test_bound_native_guard_does_not_create_an_empty_order_table(self):
        from knowledge.instruction_presentation import _parameter_tables
        base=self.signature();base['value']['representation']='native_ladder_instruction'
        rich=copy.deepcopy(base);rich['id']='enriched'
        rich['conditions']=[{'context':'project_mode','equals':'native_ladder_instruction'}]
        rich['selection_applicability']='unverified'
        for parameter in rich['value']['operands']:parameter['purpose']='meaning_from_bound_source'
        order=self.fact('order','parameters.order',{'declaration_kind':'instruction',
            'representation':'native_ladder_instruction','symbols':['a','b','result']})
        tables,used=_parameter_tables([base,rich,order],copy.deepcopy)
        self.assertEqual(len(tables),1)
        self.assertTrue(all(p.get('purpose') and p.get('type') for p in tables[0]['parameters']))
        self.assertEqual(used['signature'],used['order'])
        self.assertEqual(tables[0]['conditions'],rich['conditions'])
        # A real hardware qualification cannot be discharged by a view label.
        rich['conditions'].append({'required_module':'external_module'})
        self.assertEqual(len(_parameter_tables([base,rich,order],copy.deepcopy)[0]),2)

    def test_complete_named_source_refresh_corrects_conditions_and_retains_old_claim(self):
        from scripts.compile_instruction_semantics import replace_source_namespaces
        old=self.fact('fx5prog:row','effects.transfer',{'source':'a','target':'result','operation':'copy'},
                      conditions=[{'context':'program_context','equals':'sfc'}])
        fresh=copy.deepcopy(old)
        fresh['conditions']=[{'operand':'a','device_kind':['S'],'addressing':'SFC_step_group'}]
        current,history=replace_source_namespaces([old],[fresh],['fx5prog:'])
        self.assertEqual(current,[fresh])
        self.assertEqual(history,[old])
        self.assertEqual(replace_source_namespaces(current,[fresh],['fx5prog:']),(current,[]))

    def test_SFC_step_reader_is_qualified_by_operand_in_both_program_contexts(self):
        from tools.fact_fx5_operation_review import section_conditions
        conditions=section_conditions({'section_token':'batch_reading_the_status_of_steps',
            'outline':'PART 3 > 11 SFC PROGRAM INSTRUCTIONS'})
        self.assertEqual(conditions,[{'operand':'s','device_kind':['S'],'addressing':'SFC_step_group'}])
        self.assertFalse(any(c.get('context')=='program_context' for c in conditions))


class SourceStepReadBindingTests(unittest.TestCase):
    def test_printed_width_and_count_bind_the_overload_without_opcode_prefix_rules(self):
        from tools.fact_fx5_operation_review import step_status_specs
        from plc.instruction_knowledge import parameter_usage_facts
        layouts=[(1,'A\nThese instructions batch-read (in units of 16-bit binary data) the status of steps.\nLadder diagram'),
                 (2,'B\nThese instructions batch-read (in units of 32-bit binary data) the status of steps.\nLadder diagram'),
                 (3,'C\nThese instructions batch-read (in units of the specified number of words starting from the specified step) the status of steps.\nLadder diagram')]
        section={'outline':'PART 3 > SFC','text':'synthetic',
            'page_texts':[(page,text.split('\n')[0]) for page,text in layouts],
            'page_layouts':[(page,'Annotated compact header'+text[text.find('\n'):]) for page,text in layouts]}
        def declarations(text,*args,**kwargs):return [text.split('\n')[0]]
        with patch('knowledge.instruction_document.instruction_declaration_names',side_effect=declarations),patch(
                'tools.fact_fx5_ladder_signatures._printed_forms',side_effect=lambda text,names:names):
            for name,bits,count in [('A',16,False),('B',32,False),('C',16,True)]:
                with self.subTest(name=name):
                    spec,=step_status_specs(section,{'opcode':name})
                    self.assertEqual(spec['value']['unit_bits'],bits)
                    self.assertEqual(spec['value']['nibble_specification']['maximum'],bits//4)
                    signature=fact('declaration','parameters.typed_operands',value={
                        'instruction_variant':'step_read','declaration_kind':'instruction',
                        'operands':[{'name':'s','direction':'input','type':'ANY'+str(bits)},
                                    {'name':'d','direction':'output','type':'ANY'+str(bits)}]})
                    if count:signature['value']['operands'].append({'name':'n','direction':'input','type':'ANY16'})
                    effect=fact('source',spec['dimension'],value={**spec['value'],
                        'instruction_variant':'step_read','declaration_kind':'instruction'})
                    enriched,=parameter_usage_facts([signature,effect])
                    table={p['name']:p for p in enriched['value']['operands']}
                    self.assertTrue(all(p.get('purpose') for p in table.values()))
                    self.assertEqual(table['s']['value_semantics'],'device_reference')
                    if count:
                        self.assertEqual(table['n']['range'],{'minimum':0,'maximum':65535})
                        self.assertNotIn('word_count',table['d']['write_layout'])
                    else:self.assertEqual(table['d']['write_layout']['word_count'],bits//16)
                    # The ordinary transfer overload cannot borrow SFC purposes.
                    signature['value'].pop('instruction_variant')
                    self.assertFalse(parameter_usage_facts([signature,effect]))


class ReferenceModelIdentityTests(unittest.TestCase):
    def test_every_core_model_has_a_reference_identity_without_native_width_admission(self):
        from plc.target_support import CORE_ONLY_TARGETS, normalize_target_cpu
        from plc.target_identity import reference_target_identity, target_series
        from plc.instruction_steps import instruction_step_width
        from knowledge.scope import query_is_out_of_scope
        self.assertEqual(CORE_ONLY_TARGETS, {'FX5S','FX5UJ','FX5U','FX5UC'})
        for model in CORE_ONLY_TARGETS:
            with self.subTest(model=model):
                self.assertEqual(reference_target_identity(' '+model.lower()+' '),model)
                self.assertEqual(reference_target_identity(model+'CPU'),model)
                self.assertEqual(normalize_target_cpu(model),model)
                self.assertIsNone(target_series(model))
                self.assertFalse(query_is_out_of_scope('CMP parameter usage',model))
                self.assertTrue(query_is_out_of_scope('Siemens S7 manual',model))
                width=instruction_step_width('MOV',['D0','D1'],plc_model=model)
                self.assertFalse(width.known)
                self.assertEqual(width.status,'no_profile')
        for invalid in ('FX5','FX5X','FX5U/FX5UC','FX5U/FX3U','FX5UJ/'):
            with self.subTest(invalid=invalid):
                self.assertIsNone(reference_target_identity(invalid))




class SharedExecutionKnowledgeTests(unittest.TestCase):
    def facts(self, opcode='PID', *, declaration_kind='instruction'):
        scope={'models':['FX3U'],'forms':[opcode],'languages':['ladder','st','fbd']}
        proof=fact('forms','parameters.variant_family',scope=scope,value={'section_variants':[opcode]})
        classification=fact('class','identity.instruction_class',scope=scope,depends_on=['forms'],
            value={'instruction_class':'applied','declaration_kind':declaration_kind})
        return [proof,classification]

    def knowledge(self, facts, opcode='PID', **context):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        return registry.instruction_knowledge(opcode,cpu='FX3U',language='ladder',facts=facts,context=context)

    def test_unmodified_instruction_receives_shared_rule_without_modifier_inference(self):
        value=self.knowledge(self.facts())
        self.assertNotIn('knowledge_module',value)
        module,=value['knowledge_modules']
        self.assertEqual(module['kind'],'instruction_execution')
        default=module['rules']['disabled_destination']
        self.assertEqual(default['destination_state'],'retained')
        self.assertEqual(default['unless'],'instruction_defines_other_behavior')
        self.assertEqual(default['internal_state_and_reenable'],'defined_by_instruction_family')
        self.assertEqual(value['common_rules'][0]['interface']['declaration_kind'],'instruction')

    def test_class_requires_source_closure_and_preserves_scope(self):
        base=self.facts()
        for mutation in [{'status':'unknown'},{'depends_on':['missing']},{'verification':'not_checked'},
                         {'scope':{'models':['FX5U'],'forms':['PID']}}, {'value':{'instruction_class':'applied','declaration_kind':'function'}}]:
            with self.subTest(mutation=mutation):
                altered=copy.deepcopy(base);altered[-1].update(mutation)
                self.assertNotIn('common_rules',self.knowledge(altered))
        bad=copy.deepcopy(base);bad[0]['sources'][0]['conditions']={'opaque':'restriction'}
        self.assertNotIn('common_rules',self.knowledge(bad))
        for opcode in ['OUT','OUT_T','OUT_C']:
            with self.subTest(opcode=opcode):
                self.assertNotIn('common_rules',self.knowledge([],opcode) or {})
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        self.assertNotIn('common_rules',registry.instruction_knowledge('PID',cpu='FX5U',language='ladder',facts=base) or {})

    def test_parent_conditions_and_unverified_status_are_not_promoted(self):
        facts=self.facts();facts[0]['conditions']=[{'context':'cpu_version','equals':'3.0'}]
        facts[0]['status']='declared_unverified'
        unknown=self.knowledge(facts)['common_rules'][0]
        self.assertEqual(unknown['selection_applicability'],'unverified')
        self.assertEqual(unknown['status'],'declared_unverified')
        self.assertEqual(unknown['conditions'],facts[0]['conditions'])
        self.assertNotIn('common_rules',self.knowledge(facts,cpu_version='2.0'))
        present=self.knowledge(facts,cpu_version='3.0')['common_rules'][0]
        self.assertEqual(present['selection_applicability'],'applicable')
        self.assertEqual(present['status'],'declared_unverified')

    def test_unspecified_language_shares_one_binding_without_broadening_scope(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        facts=self.facts();facts[0]['scope']['languages']=['ladder','st']
        value=registry.instruction_knowledge('PID',cpu='FX3U',facts=facts)
        binding,=value['common_rules']
        self.assertCountEqual(binding['scope']['languages'],['ladder','st'])

    def test_dependency_version_scope_is_retained_when_context_is_missing(self):
        facts=self.facts();facts[0]['scope']=dict(facts[0]['scope'],versions=['3.0'])
        unknown=self.knowledge(facts)['common_rules'][0]
        self.assertEqual(unknown['scope']['versions'],['3.0'])
        self.assertEqual(unknown['selection_applicability'],'unverified')
        self.assertNotIn('common_rules',self.knowledge(facts,version='2.0'))
        self.assertEqual(self.knowledge(facts,version='3.0')['common_rules'][0]['selection_applicability'],'applicable')

    def test_shared_rules_live_in_system_context_for_both_delivery_paths(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        from shared.model_context import technical_block,technical_context_units,technical_knowledge_results
        packets,records=[],[]
        for opcode in ['PID','DZCPP']:
            facts,_=compose_programming_facts(registry,opcode,cpu='FX3U',existing=self.facts(opcode))
            facts.append(fact('hidden-history','execution.unresolved_source',status='unknown',
                value={'unresolved_items':['internal_history_next_enable']},scope={'models':['FX3U'],'forms':[opcode]}))
            p=dict(kind='instruction',target=opcode,scope={'models':['FX3U']},content_origin='authored_fact',
                   value={'opcode':opcode,'target_model':'FX3U','facts':facts,'call_available':True,'gaps':['effects_not_formalized']},
                   _knowledge_context={'model':'FX3U','language':'ladder','project_mode':'native_ladder_instruction'})
            packets.append(p);records.append(dict(row(p),id=opcode,fact_target=opcode,_knowledge_context=p['_knowledge_context']))
        model=technical_context_units('\n'.join(technical_block(technical_fact_packet(p)) for p in packets))
        mcp=[unit for result in technical_knowledge_results(records) for unit in result['knowledge']]
        self.assertEqual(model,mcp)
        self.assertEqual(sum(unit['kind']=='instruction_execution' for unit in model),0)
        self.assertEqual(sum(unit['kind']=='instruction_modifiers' for unit in model),0)
        for unit in model:
            if unit['kind']=='instruction':
                self.assertNotIn('common_rules',unit['value']['forms'][0])
                self.assertNotIn('internal_history_next_enable',json.dumps(unit))
                self.assertNotIn('effects_not_formalized',json.dumps(unit))



class PulseFamilySemanticsTests(unittest.TestCase):
    def delivered(self,opcode,cpu='FX3U',language='ladder',mode=None):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        # Source signatures are synthetic; the expected semantic facts come
        # from reviewed family data rather than this helper's parameter values.
        names=['S1','S2','D'] if language=='ladder' else ['s1','s2','d']
        if opcode in ['PLSR','DPLSR']:names.insert(2,'S3' if language=='ladder' else 's3')
        if cpu.startswith(('Q','L')):names=['n1','n2','D' if language=='ladder' else 'd']
        bits=32 if opcode.startswith('D') else 16
        manual='fx3_programming_r' if language=='ladder' else 'fxcpu_basic_applied_m'
        page={'PLSY':377,'DPLSY':377,'PLSR':385,'DPLSR':385,'PWM':382}[opcode] if language=='ladder' else {'PLSY':298,'DPLSY':298,'PLSR':308,'DPLSR':308,'PWM':305}[opcode]
        if cpu.startswith(('Q','L')):
            manual='sh080809engx' if language=='ladder' else 'sh080783engp'
            page=(353 if opcode=='PLSY' else 355) if language=='ladder' else (313 if opcode=='PLSY' else 315)
        source={'manual_id':manual,'manual_number':{'fx3_programming_r':'JY997D16601','fxcpu_basic_applied_m':'JY997D34701','sh080809engx':'SH080809ENG','sh080783engp':'SH080783ENG'}[manual],
                'revision':{'fx3_programming_r':'R','fxcpu_basic_applied_m':'M','sh080809engx':'X','sh080783engp':'P'}[manual],'pdf_pages':[page]}
        parameters=[{'name':name,'type':'BOOL' if name in ['D','d'] else 'ANY'+str(bits),
                     'direction':'output' if name in ['D','d'] else 'input'} for name in names]
        signature=fact('source-signature','parameters.typed_operands',sources=[source],
            scope={'models':[cpu],'forms':[opcode],'languages':[language]},
            value={'declaration_kind':'instruction','operands':parameters,'operand_count':len(parameters)})
        groups,_=compose_programming_facts(registry,opcode,cpu=cpu,existing=[signature])
        context={'model':cpu,'language':language,'project_mode':mode or ('native_ladder_instruction' if language=='ladder' else None)}
        raw=dict(kind='instruction',target=opcode,scope={'models':[cpu]},content_origin='authored_fact',
            value={'opcode':opcode,'target_model':cpu,'facts':groups},_knowledge_context=context)
        projected=project_packet(raw,plc_model=cpu,language=language,aspects=['programming_semantics'],project_mode=context['project_mode'])
        projected['_knowledge_context']=context
        return groups,technical_fact_packet(projected)

    def walk(self,value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from self.walk(child)
        elif isinstance(value,list):
            for child in value:yield from self.walk(child)

    def test_width_changes_inputs_and_keeps_one_bit_output(self):
        for cpu in ['FX3S','FX3G','FX3GC','FX3U','FX3UC']:
            for opcode,bits,words in [('PLSY',16,1),('DPLSY',32,2),('PLSR',16,1),('DPLSR',32,2)]:
                with self.subTest(cpu=cpu,opcode=opcode):
                    groups,view=self.delivered(opcode,cpu)
                    parameters=[p for table in view['value']['parameter_tables'] for p in table.get('parameters',[])]
                    for name in ['S1','S2']+(['S3'] if 'PLSR' in opcode else []):
                        matches=[p for p in parameters if p['name']==name and p.get('purpose')]
                        self.assertTrue(matches,(cpu,opcode,name))
                        self.assertTrue(any(p.get('word_device_layout',{}).get('word_count')==words for p in matches))
                    self.assertTrue(any(p['name']=='D' and p.get('write_layout',{}).get('bits')==1 for p in parameters))
                    self.assertTrue(any(item.get('output')=={'parameter':'D','kind':'bit','offset':0} for item in self.walk(view)))
                    self.assertFalse(any(f['value'].get('behavior') for f in groups))

    def test_specific_stop_restart_and_parameter_updates_survive_projection(self):
        for opcode in ['PLSY','DPLSY']:
            for language in ['ladder','st','fbd']:
                with self.subTest(opcode=opcode,language=language):
                    groups,view=self.delivered(opcode,language=language)
                    rules=list(self.walk(view))
                    self.assertTrue(any(v.get('operation_stop')=='immediate' and v.get('restart_after_OFF_then_ON')=='restart_from_beginning' for v in rules))
                    self.assertTrue(any(v.get('while_running')=='frequency_changes' for v in rules))
                    self.assertTrue(any(v.get('while_running')=='takes_effect_on_next_activation' for v in rules))
                    self.assertTrue(any(v.get('drive_condition_OFF_alone_sufficient') is False for v in rules))
                    self.assertTrue(any('exact_state_if_instruction_not_evaluated_due_to_control_flow' in v.get('unresolved_items',[]) for v in rules))

    def test_PLSR_has_latched_inputs_ramp_constraints_and_no_zero_count_mode(self):
        groups,view=self.delivered('DPLSR')
        rules=list(self.walk(view))
        self.assertFalse(any(v.get('while_running')=='frequency_changes' for v in rules))
        self.assertTrue(any(v.get('zero_means')=='outside_declared_range' for v in rules))
        ramp=next(v for v in rules if v.get('operation')=='accelerate_then_decelerate_to_zero')
        self.assertEqual(ramp['acceleration_time'],'S3')
        self.assertEqual(ramp['deceleration_time'],'S3')
        self.assertFalse(any(v.get('recommended_minimum_ramp_time_ms') or v.get('ramp_time_minimum_multiple_of_maximum_scan_time') for v in rules))
        self.assertTrue(any('legacy_PLSR_step_count_and_ramp_formulas_not_established_for_FX3' in v.get('unresolved_items',[]) for v in rules))
        self.assertTrue(any(v.get('output_state') is False and v.get('operation_stop')=='immediate' for v in rules))
        adapter=next(v for v in rules if v.get('output_type_switch')=='PLS_DIR')
        self.assertEqual(adapter['CPU_models'],['FX3U'])
        self.assertEqual(adapter['pulse_routing'][0]['selector_OFF_pulse_terminal'],'Y004')
        self.assertTrue(any(v.get('interrupted_output_sets_completion') is False for v in rules))

    def test_Q_L_software_pulses_keep_their_own_timing_and_restrictions(self):
        for cpu in ['Q02H','Q02PH','Q03UDV','L02']:
            groups,view=self.delivered('PLSY',cpu)
            rules=list(self.walk(view))
            self.assertTrue(any(v.get('operation_start')=='execution_condition_rising_edge' and v.get('interrupt_interval_ms')==1 for v in rules))
            self.assertTrue(any(v.get('minimum_Hz')==1 and v.get('maximum_Hz')==100 for v in rules))
            self.assertTrue(any(v.get('maximum')==65535 for v in rules))
            self.assertTrue(any(v.get('maximum_instruction_occurrences')==1 for v in rules))
            self.assertTrue(any(v.get('while_command_ON')=='do_not_change' for v in rules))
            self.assertFalse(any(v.get('device')=='M8029' for v in rules))

    def test_PWM_boundary_relation_is_profile_data(self):
        for cpu,operator,width_min in [('FX3U','le',0),('Q03UDV','lt',1)]:
            groups,view=self.delivered('PWM',cpu)
            self.assertTrue(any(v.get('operator')==operator for v in self.walk(view)))
            self.assertTrue(any(v.get('minimum_ms')==width_min for v in self.walk(view)))
        groups,view=self.delivered('PWM','FX3U')
        self.assertTrue(any(v.get('width_equal_to_period_percent')==100 for v in self.walk(view)))
        conflict, = [f for f in groups if f['dimension']=='resources.hardware_source_disagreement']
        self.assertEqual(conflict['status'],'conflict')
        self.assertEqual({s['revision'] for s in conflict['sources']},{'M','R'})
        self.assertTrue(any(v.get('compatibility')=='unverified_for_relay_or_triac_main_unit_with_special_adapter' for v in self.walk(view)))
        adapter=next(v for v in self.walk(view) if v.get('output_type_switch')=='PLS_DIR')
        self.assertEqual(len(adapter['pulse_routing']),4)
        groups,_=self.delivered('PWM','FX3G')
        self.assertFalse(any(f['dimension']=='resources.hardware_source_disagreement' for f in groups))

    def test_basic_and_redundant_Q_exclusions_are_explicit(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        for cpu in ['Q00J','Q00','Q01','Q12PRH','Q25PRH']:
            for opcode in ['PLSY','PWM']:
                groups,_=compose_programming_facts(registry,opcode,cpu=cpu,existing=[])
                self.assertTrue(any(f['dimension']=='identity.applicability' and f['value'].get('available') is False for f in groups))
                self.assertFalse(any(f['dimension']=='effects.documented_processing' for f in groups))
        families=[d for d in registry._family_knowledge if d['base_opcode'] in ['PLSY','PLSR','PWM']]
        self.assertEqual(len(families),3)
        self.assertFalse(any(model.startswith(('FX5','R0','R1','R2','R3','R6')) for family in families for f in family['facts'] for model in f['scope']['models']))

class PositioningFamilySemanticsTests(unittest.TestCase):
    FORMS={'PLSV':('PLSV',16),'DPLSV':('PLSV',32),'DRVI':('DRVI',16),'DDRVI':('DRVI',32),'DRVA':('DRVA',16),'DDRVA':('DRVA',32)}
    MODULE='mitsubishi.builtin-pulse-positioning.fx'

    def delivered(self,opcode,cpu='FX3U',language='ladder',cpu_version='3.00',project_mode=None):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        base,bits=self.FORMS[opcode]
        native=language=='ladder' and project_mode!='structured_ladder_block'
        names=(['S1','D1','D2'] if base=='PLSV' else ['S1','S2','D1','D2']) if native else (['s','d1','d2'] if base=='PLSV' else ['s1','s2','d1','d2'])
        manual='fx3_programming_r' if native else 'fxcpu_basic_applied_m'
        page=({'PLSV':558,'DRVI':560,'DRVA':562} if native else {'PLSV':533,'DRVI':536,'DRVA':539})[base]
        signature=fact('checked-interface','parameters.typed_operands',scope={'models':[cpu],'forms':[opcode],'languages':[language]},
            sources=[{'manual_id':manual,'manual_number':'JY997D16601' if native else 'JY997D34701','revision':'R' if native else 'M','pdf_pages':[page]}],
            value={'declaration_kind':'instruction','operand_count':len(names),'operands':[
                {'name':name,'type':'BOOL' if name.upper().startswith('D') else 'ANY'+str(bits),
                 'direction':'output' if name.upper().startswith('D') else 'input'} for name in names]})
        groups,_=compose_programming_facts(registry,opcode,cpu=cpu,existing=[signature])
        context={'model':cpu,'language':language,'project_mode':project_mode or ('native_ladder_instruction' if native else None)}
        instance={} if cpu_version is None else {'cpu_version':cpu_version}
        context.update(instance)
        raw={'kind':'instruction','target':opcode,'scope':{'models':[cpu]},'content_origin':'authored_fact',
             'value':{'opcode':opcode,'target_model':cpu,'facts':groups},'_knowledge_context':context}
        selected=project_packet(raw,plc_model=cpu,language=language,aspects=['programming_semantics'],
                                project_mode=context['project_mode'],instance_context=instance)
        selected['_knowledge_context']=context
        return groups,technical_fact_packet(selected)

    def walk(self,value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from self.walk(child)
        elif isinstance(value,list):
            for child in value:yield from self.walk(child)

    def test_effective_parameter_contract_has_numeric_inputs_and_two_bit_outputs(self):
        for cpu in ['FX3S','FX3G','FX3GC','FX3U','FX3UC']:
            for opcode,(base,bits) in self.FORMS.items():
                with self.subTest(cpu=cpu,opcode=opcode):
                    _,view=self.delivered(opcode,cpu)
                    rows=[p for table in view['value']['parameter_tables'] for p in table['parameters']]
                    for name in ['S1']+([] if base=='PLSV' else ['S2']):
                        self.assertTrue(any(p['name']==name and p.get('purpose') and
                            p.get('operation_value_type',{}).get('bits')==bits and
                            p.get('word_device_layout',{}).get('word_count')==bits//16 for p in rows),(cpu,opcode,name))
                    for name in ['D1','D2']:
                        matches=[p for p in rows if p['name']==name and p.get('purpose')]
                        self.assertTrue(any(p.get('write_layout',{}).get('bits')==1 for p in matches))
                        self.assertFalse(any(p.get('word_device_layout',{}).get('word_count') for p in matches))
                    output=next(p for p in rows if p['name']=='D1' and p.get('range'))
                    self.assertEqual(output['range']['main_unit_outputs'],['Y000','Y001'] if cpu in ['FX3S','FX3GC'] else ['Y000','Y001','Y002'])

    def test_positioning_family_differences_survive_each_language_projection(self):
        expected={'PLSV':'variable_speed_pulse_output','DRVI':'relative_positioning','DRVA':'absolute_positioning'}
        for opcode,(base,_) in self.FORMS.items():
            for language in ['ladder','st','fbd']:
                with self.subTest(opcode=opcode,language=language):
                    _,view=self.delivered(opcode,language=language)
                    nodes=list(self.walk(view))
                    self.assertTrue(any(v.get('operation')==expected[base] for v in nodes))
                    if base=='PLSV':
                        self.assertTrue(any(v.get('while_running')=='frequency_magnitude_changes_live' for v in nodes))
                        self.assertTrue(any(v.get('changing_sign_while_pulses_output')=='not_permitted' for v in nodes))
                    else:
                        self.assertTrue(any(v.get('operation_stop')=='decelerates_then_stops' for v in nodes))
                        self.assertTrue(any(v.get('while_running')=='ignored_until_next_activation' for v in nodes))
                        self.assertTrue(any(v.get('after_requested_move_complete_while_drive_ON')=='no_second_move_until_new_activation' for v in nodes))

    def test_target_ranges_and_zero_meaning_are_real_variant_differences(self):
        for opcode in ['DRVI','DDRVI','DRVA','DDRVA']:
            _,view=self.delivered(opcode)
            ranges=[p['range'] for table in view['value']['parameter_tables'] for p in table['parameters'] if p['name']=='S1' and p.get('range')]
            self.assertTrue(ranges)
            bounds=ranges[0]
            self.assertEqual(bounds['maximum'],999999 if opcode in ['DDRVI','DDRVA'] else 32767)
            self.assertEqual(bounds['minimum'],-999999 if opcode in ['DDRVI','DDRVA'] else -32768)
            self.assertEqual(bounds['zero_allowed'],opcode in ['DRVA','DDRVA'])

    def test_PLSV_ramp_version_and_stop_cases_do_not_become_unconditional(self):
        groups,view=self.delivered('DPLSV','FX3UC',cpu_version=None)
        nodes=list(self.walk(view))
        self.assertTrue(any(v.get('cpu_version_minimum')=='2.20' and v.get('effective_acceleration_deceleration') is True for v in nodes))
        self.assertTrue(any(v.get('cpu_version_maximum_exclusive')=='2.20' and v.get('effective_acceleration_deceleration') is False for v in nodes))
        self.assertTrue(any(v.get('when_effective_acceleration_deceleration') is True and v.get('operation_stop')=='decelerates_then_stops' for v in nodes))
        self.assertTrue(any(v.get('when_effective_acceleration_deceleration') is False and v.get('operation_stop')=='immediate' for v in nodes))
        self.assertTrue(any(v.get('normal_completion_flag')=='M8029' and v.get('will_turn_ON') is False for v in nodes))
        self.assertTrue(any(v.get('scope')=='all_pulse_output_destinations_not_independent_per_axis' for v in nodes))
        self.assertTrue(any(v.get('selection_applicability')=='unverified' for v in nodes))
        self.assertFalse(any(v.get('behavior') for v in groups))

    def test_shared_module_is_source_scoped_and_once_per_multi_family_delivery(self):
        from knowledge.instruction_presentation import merge_instruction_units
        units=merge_instruction_units([self.delivered(opcode)[1] for opcode in self.FORMS])
        families=[u for u in units if u.get('kind')=='instruction']
        modules=[u for u in units if u.get('id')==self.MODULE]
        self.assertEqual(len(families),3)
        self.assertEqual(len(modules),1)
        self.assertEqual(sum(u.get('kind')=='instruction_modifiers' for u in units),1)
        self.assertEqual(sum(u.get('kind')=='instruction_execution' for u in units),2)
        common=modules[0]
        self.assertEqual(common['applies_to']['instruction_group'],'built_in_pulse_positioning')
        self.assertEqual(common['rules']['current_position']['type']['bits'],32)
        self.assertFalse(common['rules']['reuse_and_observation']['drive_condition_OFF_alone_sufficient'])
        for family in families:
            for form in family['value']['forms']:
                binding=next(b for b in form['common_rules'] if b['knowledge_module_id']==self.MODULE)
                self.assertEqual(binding['interface']['instruction_group'],'built_in_pulse_positioning')

    def test_shared_reference_survives_public_projection_and_native_details_stay_private(self):
        from agent_runtime.runtime import _model_tool_value
        _,view=self.delivered('DPLSV')
        public=_model_tool_value('search_plc_manual',{'knowledge':[view],'native_details':{'module':'private-native.dll','rva':123}})
        binding=next(b for b in public['knowledge'][0]['value']['forms'][0]['common_rules'] if b['knowledge_module_id']==self.MODULE)
        self.assertEqual(binding['interface']['instruction_group'],'built_in_pulse_positioning')
        self.assertEqual(public['native_details'],{})

    def test_structured_ladder_keeps_its_enable_pin_and_output_operands(self):
        for opcode in ['DPLSV','DDRVI','DDRVA']:
            with self.subTest(opcode=opcode):
                _,view=self.delivered(opcode,project_mode='structured_ladder_block')
                rows=[p for table in view['value']['parameter_tables'] for p in table['parameters']]
                self.assertTrue(any(p['name']=='EN' and p['type']=='BOOL' and p['direction']=='input' for p in rows))
                self.assertTrue(any(p['name']=='d1' and p.get('write_layout',{}).get('bits')==1 for p in rows))
                self.assertTrue(any(p['name']=='d2' and p.get('write_layout',{}).get('bits')==1 for p in rows))
                self.assertFalse(any(p['name']=='S1' for p in rows))

    def test_known_earlier_CPU_version_does_not_receive_positioning_module(self):
        _,view=self.delivered('DPLSV',cpu_version='2.10')
        self.assertFalse(any(v.get('id')==self.MODULE for v in self.walk(view)))
        self.assertFalse(any(v.get('operation')=='variable_speed_pulse_output' for v in self.walk(view)))
        _,view=self.delivered('DPLSV',cpu_version='2.20')
        self.assertTrue(any(v.get('id')==self.MODULE for v in self.walk(view)))

    def test_missing_sources_and_unregistered_forms_do_not_gain_shared_positioning_knowledge(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        for cpu,opcode in [('Q03UDV','DRVI'),('FX3U','DRVIP'),('FX3U','DDRVIP'),('FX3U','PLSVP'),('FX3U','DPLSVP')]:
            groups,_=compose_programming_facts(registry,opcode,cpu=cpu,existing=[])
            self.assertFalse(any(f['dimension']=='effects.documented_processing' for f in groups))
        for opcode in self.FORMS:
            groups,_=self.delivered(opcode)
            for f in groups:
                self.assertFalse(any(m.startswith('FX5') for m in f['scope'].get('models',[])))

    def test_ST_statement_metadata_is_preserved_without_inventing_a_return_type(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        for opcode in list(self.FORMS)+['PLSY','DPLSY','PLSR','DPLSR','PWM']:
            groups,_=compose_programming_facts(registry,opcode,cpu='FX3U',existing=[])
            calls=[f for f in groups if f['dimension']=='parameters.st_signature' and 'st' in f['scope'].get('languages',[])]
            self.assertTrue(calls,opcode)
            for f in calls:
                self.assertEqual(f['value']['call_kind'],'statement')
                self.assertEqual(f['value']['call_form'],opcode)
                self.assertNotIn('$opcode',json.dumps(f['value']))
                self.assertEqual(f['value']['argument_order'],[p['name'] for p in f['value']['parameters']])
                self.assertEqual(f['value']['returns'],{'role':'not_assigned_in_documented_statement'})
                self.assertFalse(any(p['name']=='ENO' for p in f['value']['parameters']))

    def test_absolute_direction_reference_uses_target_relative_to_current_position(self):
        try:
            from hypothesis import given,settings,strategies as st,example
        except ImportError:
            self.skipTest('Hypothesis is optional for the manual direction reference')
        from plc.instruction_definition import evaluate_expression
        rules={}
        for opcode in ['DRVA','DDRVA']:
            groups,_=self.delivered(opcode)
            relation=next(f for f in groups if f['dimension']=='effects.documented_processing')
            rules[opcode]=relation['value']['rules'][0]
        @st.composite
        def positions(draw):
            opcode=draw(st.sampled_from(['DRVA','DDRVA']))
            low,high=(-32768,32767) if opcode=='DRVA' else (-999999,999999)
            return opcode,draw(st.integers(low,high)),draw(st.integers(-2147483648,2147483647))
        @settings(max_examples=120,deadline=None,database=None)
        @given(positions())
        @example(('DRVA',-20,-30))
        @example(('DRVA',0,0))
        @example(('DDRVA',999999,-2147483648))
        @example(('DDRVA',-999999,2147483647))
        def check(case):
            opcode,target,current=case;rule=rules[opcode]
            values={'S1':target};state={'current_position_at_activation':current}
            # The source direction relation is independent of the target sign.
            self.assertEqual(evaluate_expression(rule['when']['predicate'],values,state=state),target!=current)
            self.assertEqual(evaluate_expression(rule['outputs'][0]['expression'],values,state=state),target>current)
        check()


class BitDataFamilySemanticsTests(unittest.TestCase):
    FX=['FX3S','FX3G','FX3GC','FX3U','FX3UC']
    BASES=['ROR','ROL','RCR','RCL','SMOV','SWAP']

    def forms(self,base):
        return [base,base+'P']+([] if base=='SMOV' else ['D'+base,'D'+base+'P'])

    def models(self,base):
        return self.FX if base in ['ROR','ROL','SMOV'] else ['FX3U','FX3UC']

    def delivered(self,opcode,cpu='FX3U',language='ladder',project_mode=None,cpu_version='3.00'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        groups,_=compose_programming_facts(registry,opcode,cpu=cpu,existing=[])
        mode=project_mode or ('native_ladder_instruction' if language=='ladder' else None)
        context={'model':cpu,'language':language,'project_mode':mode,'cpu_version':cpu_version}
        raw={'kind':'instruction','target':opcode,'scope':{'models':[cpu]},'content_origin':'authored_fact',
             'value':{'opcode':opcode,'target_model':cpu,'facts':groups},'_knowledge_context':context}
        selected=project_packet(raw,plc_model=cpu,language=language,aspects=['programming_semantics'],
                                project_mode=mode,instance_context={'cpu_version':cpu_version})
        selected['_knowledge_context']=context
        return groups,technical_fact_packet(selected)

    def walk(self,value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from self.walk(child)
        elif isinstance(value,list):
            for child in value:yield from self.walk(child)

    def operation(self,opcode,cpu='FX3U'):
        groups,_=self.delivered(opcode,cpu)
        return next(f['value'] for f in groups if f['dimension']=='effects.documented_processing'
                    and f['scope']['languages']==['ladder'] and f['value'].get('data_operand',f['value'].get('source_operand')) in ['D','S'])

    def test_parameter_uses_and_semantic_storage_access_survive_supported_interfaces(self):
        for base in self.BASES:
            for cpu in self.models(base):
                for opcode in self.forms(base):
                    bits=32 if opcode in ['D'+base,'D'+base+'P'] else 16
                    for language,mode in [('ladder',None),('st',None),('fbd',None),('ladder','structured_ladder_block')]:
                        with self.subTest(cpu=cpu,opcode=opcode,language=language,mode=mode):
                            _,view=self.delivered(opcode,cpu,language,mode)
                            rows=[p for table in view['value']['parameter_tables'] for p in table['parameters']]
                            native=language=='ladder' and mode is None
                            name=('S' if base=='SWAP' else 'D') if native else ('s' if base=='SWAP' else 'd')
                            self.assertTrue(any(p['name']==name and p.get('purpose') and p.get('semantic_access')=='read_write'
                                and p.get('word_device_layout',{}).get('word_count')==bits//16 for p in rows))
                            if base in ['ROR','ROL','RCR','RCL']:
                                self.assertTrue(any(p['name']==name and p.get('range',{}).get('digit_specified_bit_devices',{}).get('required_total_bits')==bits for p in rows))
                                self.assertTrue(any(p['name']=='n' and p.get('purpose') and
                                    p.get('word_device_layout',{}).get('word_count')==(bits//16 if native else 1) and
                                    p.get('range',{}).get('maximum')==bits for p in rows))
                            if base=='SMOV':
                                self.assertTrue(all(any(p['name']==name and p.get('purpose') for p in rows)
                                    for name in (['S','m1','m2','D','n'] if native else ['s','m1','m2','n','d'])))

    def test_rotations_bind_carry_loop_and_keep_zero_count_unknown(self):
        for base in ['ROR','ROL','RCR','RCL']:
            for opcode in self.forms(base):
                body=self.operation(opcode)
                self.assertEqual(body['carry_device'],'M8022')
                self.assertEqual(body['carry_participates_in_rotation_loop'],base in ['RCR','RCL'])
                self.assertEqual(body['iteration']['count_operand'],'n')
                self.assertEqual(body['iteration']['state_update'],'simultaneous_after_each_step')
                self.assertEqual(body['zero_count_state'],'not_stated_in_reviewed_source')
                self.assertEqual(body['defined_reference_domain']['count_minimum'],1)

    def test_modes_and_unchanged_digits_of_SMOV_are_delivered(self):
        for opcode in self.forms('SMOV'):
            for language in ['ladder','st','fbd']:
                _,view=self.delivered(opcode,language=language)
                body=next(v for v in self.walk(view) if v.get('operation')=='digit_transfer')
                self.assertEqual(body['mapping']['source_position'],'source_head - i')
                self.assertEqual(body['mapping']['destination_position'],'destination_head - i')
                self.assertEqual(body['mapping']['unselected_destination_digits'],'retained')
                self.assertEqual(body['mode_device'],'M8168')
                self.assertEqual({v['digit_radix'] for v in body['mode_cases']},{10,16})
                self.assertEqual(body['after_using_extension'],'return_M8168_to_OFF')
                self.assertEqual(body['when_drive_input_false'],'destination_unchanged')

    def test_swap32_preserves_word_order_and_reference_has_one_word_type(self):
        body=self.operation('DSWAPP')
        self.assertEqual(body['word_count'],2)
        self.assertEqual(body['word_order'],'unchanged')
        expression=body['rules'][0]['transition']['outputs'][0]['expression']
        self.assertEqual(expression['type']['bits'],16)
        _,view=self.delivered('DSWAPP',language='st')
        rows=[p for table in view['value']['parameter_tables'] for p in table['parameters'] if p['name']=='s']
        self.assertTrue(any(p.get('result_value_type',{}).get('bits')==32 for p in rows))
        self.assertFalse(any(p.get('result_value_type',{}).get('bits')==16 for p in rows))

    def test_literal_forms_firmware_and_model_scope_do_not_expand(self):
        for cpu,opcode in [('FX3S','RCR'),('FX3G','RCL'),('FX3GC','SWAP'),('Q03UDV','ROR'),
                           ('FX3U','DSMOV'),('FX3U','DSMOVP'),('FX3U','RORPP')]:
            _,view=self.delivered(opcode,cpu)
            self.assertFalse(any(v.get('operation') in ['bit_rotation','digit_transfer','swap_bytes_in_each_word'] for v in self.walk(view)))
        _,view=self.delivered('DROR',cpu_version='2.10')
        self.assertFalse(any(v.get('operation')=='bit_rotation' for v in self.walk(view)))
        _,view=self.delivered('DROR',cpu_version='2.20')
        self.assertTrue(any(v.get('operation')=='bit_rotation' for v in self.walk(view)))

    def test_shared_flags_are_once_and_do_not_claim_every_instruction_writes_them(self):
        from knowledge.instruction_presentation import merge_instruction_units
        views=[self.delivered(op)[1] for op in ['ROR','DRORP','RCL','SWAPP','SMOVP']]
        merged=merge_instruction_units(views)
        modules=[u for u in merged if u.get('id')=='mitsubishi.applied-instruction-execution.fx']
        self.assertEqual(len(modules),1)
        flags=modules[0]['rules']['general_flags']
        self.assertIn('M8022',flags['devices'])
        self.assertTrue(flags['not_every_instruction_writes_every_flag'])
        self.assertEqual(flags['when_instruction_not_driven'],'flag_unchanged_by_that_instruction')
        self.assertEqual(flags['unless'],'instruction_defines_other_behavior')

    def test_reference_rotation_matches_independent_bit_list_and_carry_oracle(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instruction_definition import evaluate_expression
        bodies={opcode:self.operation(opcode) for base in ['ROR','ROL','RCR','RCL'] for opcode in [base,'D'+base]}
        @st.composite
        def cases(draw):
            opcode=draw(st.sampled_from(list(bodies)));width=bodies[opcode]['data_bits']
            return opcode,draw(st.integers(0,(1<<width)-1)),draw(st.booleans()),draw(st.integers(1,width))
        @settings(max_examples=160,deadline=None,database=None)
        @given(cases())
        @example(('ROR',0xff00,True,4))
        @example(('ROL',0xff00,False,16))
        @example(('RCR',0,True,16))
        @example(('DRCL',0,True,32))
        def check(case):
            opcode,original,carry,count=case;body=bodies[opcode];width=body['data_bits']
            outputs=body['rules'][0]['outputs'];actual,actual_carry=original,carry
            for _ in range(count):
                before={'data_before_step':actual,'carry_before_step':actual_carry}
                actual=evaluate_expression(outputs[0]['expression'],{},state=before)
                actual_carry=evaluate_expression(outputs[1]['expression'],{},state=before)
            bits=[bool(original&(1<<i)) for i in range(width)];expected_carry=carry
            for _ in range(count):
                edge=bits[0] if body['direction']=='right' else bits[-1]
                incoming=expected_carry if body['carry_participates_in_rotation_loop'] else edge
                bits=bits[1:]+[incoming] if body['direction']=='right' else [incoming]+bits[:-1]
                expected_carry=edge
            expected=sum(int(bit)<<i for i,bit in enumerate(bits))
            self.assertEqual((actual,actual_carry),(expected,expected_carry))
        check()

    def test_reference_swap_matches_byte_pair_oracle_and_is_its_own_inverse(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instruction_definition import evaluate_expression
        expressions={bits:self.operation(op)['rules'][0]['transition']['outputs'][0]['expression']
                     for bits,op in [(16,'SWAP'),(32,'DSWAP')]}
        @settings(max_examples=100,deadline=None,database=None)
        @given(st.sampled_from([16,32]),st.integers(0,0xffffffff))
        @example(32,0x1234abcd)
        @example(16,0xff00)
        def check(width,value):
            value &= (1<<width)-1
            def apply(word):
                return sum(evaluate_expression(expressions[width],{},state={'word_before_swap':(word>>offset)&65535})<<offset
                           for offset in range(0,width,16))
            encoded=value.to_bytes(width//8,'little')
            expected=b''.join(encoded[i:i+2][::-1] for i in range(0,len(encoded),2))
            self.assertEqual(apply(value),int.from_bytes(expected,'little'))
            self.assertEqual(apply(apply(value)),value)
        check()


class SourceRotationRelationTests(unittest.TestCase):
    def test_inventory_checks_the_typed_reference_step_and_its_state_dependencies(self):
        from plc.instruction_relations import bind_relation_facts
        from tools.inventory_instruction_semantics import _concrete_relation
        signature, effect = self.pair()
        relation = bind_relation_facts([signature, effect])[0]
        self.assertTrue(_concrete_relation(relation))
        for failure in ['missing_count', 'missing_state', 'unparsed_derivation', 'unknown_target', 'bad_bit_result']:
            with self.subTest(failure=failure):
                broken = copy.deepcopy(relation)
                value = broken['value']
                if failure == 'missing_count':
                    value['iteration'].pop('count_expression')
                elif failure == 'missing_state':
                    value['state_bindings'].pop('data_before_step')
                elif failure == 'unparsed_derivation':
                    value['state_bindings']['active_data_mask']['derived_value'] = '(1 << bits) - 1'
                elif failure == 'unknown_target':
                    value['reference_step']['outputs'][0]['target']['kind'] = 'unknown'
                else:
                    value['reference_step']['outputs'][1]['expression'] = {
                        'op': 'constant', 'value': 1, 'type': {'kind': 'int', 'bits': 16, 'signed': False}}
                self.assertFalse(_concrete_relation(broken))

    def test_derived_active_span_is_publishable_and_handles_a_full_width_mask(self):
        from knowledge.fact_corpus import typed_value
        from plc.instruction_definition import evaluate_expression
        from plc.instruction_relations import bind_relation_facts
        for width in (16, 32):
            signature, effect = self.pair(width)
            body = bind_relation_facts([signature, effect])[0]['value']
            bindings = body['state_bindings']
            typed_value(bindings)
            for active in range(4, width + 1, 4):
                state = {'effective_operand_bits': active}
                highest = evaluate_expression(bindings['active_highest_bit']['derived_value'], {}, state=state)
                mask = evaluate_expression(bindings['active_data_mask']['derived_value'], {}, state=state)
                self.assertEqual(highest, active - 1)
                self.assertEqual(mask, int('1' * active, 2))
                self.assertEqual(bindings['active_data_mask']['type']['bits'], width)

    def section(self,bits=16,direction='right',carry=True):
        from types import SimpleNamespace
        lines=[f"Rotates {bits}-bit data (D), {'including' if carry else 'not including'} the carry flag, to the {direction}.",
               f'Use the remainder of steps / {bits}.',
               'For a bit device the rotation follows digit specification and the remainder of steps / (specified number of bits).',
               'Carry flag (SM42)']
        section=SimpleNamespace(chapter='Synthetic > Rotation Instructions',text=' '.join(lines),
            manual='synthetic',manual_number='SYNTH',revision='1',pages=[1],kind='ladder')
        section.statements=lambda _symbol:lines
        section.bind=lambda witness:[(1,1)] if witness in section.text else []
        return section,lines

    def pair(self,bits=16,direction='right',carry=True,data_direction='output',gates=False):
        from tools.fact_ql_behavior_review import _rotation_specs
        section,_=self.section(bits,direction,carry)
        value=_rotation_specs(section,'SYNTH')[0]['value']
        scope={'models':['Q03UDV'],'forms':['SYNTH'],'languages':['st']}
        operands=[{'name':'turns','marker':'steps','type':'ANY16','direction':'input'},
                  {'name':'buffer','marker':'D','type':'ANY'+str(bits),'direction':data_direction}]
        if gates:
            operands=[{'name':'EN','type':'BOOL','direction':'input','enable_pin':True},*operands,
                      {'name':'ENO','type':'BOOL','direction':'output','enable_pin':True}]
        signature=fact('declaration','parameters.typed_operands',scope=scope,value={'operands':operands})
        effect=fact('rule','effects.rotation',scope=copy.deepcopy(scope),value=copy.deepcopy(value))
        return signature,effect

    def test_source_class_requires_all_documented_clauses_and_ignores_symbol_spelling(self):
        from tools.fact_ql_behavior_review import _rotation_specs
        section,lines=self.section()
        self.assertEqual(_rotation_specs(section,'ALPHA'),_rotation_specs(section,'BETA'))
        for removed in [0,1,2,3]:
            original=lines[:];del lines[removed];section.text=' '.join(lines)
            self.assertFalse(_rotation_specs(section,'ALPHA'))
            lines[:]=original;section.text=' '.join(lines)
        lines.append('Rotates 16-bit data (D), not including the carry flag, to the left.')
        section.text=' '.join(lines)
        self.assertFalse(_rotation_specs(section,'ALPHA'))

    def test_materialization_requires_every_witness_and_preserves_known_unknown(self):
        from tools.fact_ql_behavior_review import _rotation_specs,_materialize
        section,_=self.section();specs=_rotation_specs(section,'SYNTH')
        for spec in specs:spec['languages']=['ladder']
        rows,missing,_=_materialize(section,'SYNTH',specs,'Q03UDV',verified=True)
        self.assertEqual(len(rows),2);self.assertFalse(missing)
        self.assertEqual(rows[1]['status'],'unknown')
        self.assertEqual(rows[1]['value']['state'],'unknown')
        incomplete=copy.deepcopy(specs[0]);incomplete['patterns']=(*incomplete['patterns'],'absent source clause')
        rows,missing,_=_materialize(section,'SYNTH',[incomplete],'Q03UDV',verified=True)
        self.assertFalse(rows);self.assertEqual(len(missing),1)

    def test_binding_requires_its_complete_relation_interface_and_source(self):
        from plc.instruction_relations import bind_relation_facts
        signature,effect=self.pair()
        self.assertEqual(len(bind_relation_facts([signature,effect])),1)
        for key in ['carry_device','count_policy','operand_width_policy','carry_result','reference_domain']:
            partial=copy.deepcopy(effect);partial['value'].pop(key)
            self.assertFalse(bind_relation_facts([signature,partial]),key)
        for change in ['source','language','data_width','count_width','extra_operand','unknown','variant','condition']:
            sig,rule=copy.deepcopy((signature,effect))
            if change=='source':rule['sources']=[{'source_id':'unrelated'}]
            elif change=='language':rule['scope']['languages']=['ladder']
            elif change=='data_width':sig['value']['operands'][1]['type']='ANY32'
            elif change=='count_width':sig['value']['operands'][0]['type']='ANY32'
            elif change=='extra_operand':sig['value']['operands'].append({'name':'unbound','type':'ANY16','direction':'input'})
            elif change=='unknown':rule['status']='unknown'
            elif change=='variant':sig['value']['instruction_variant']='different_overload'
            else:sig['conditions']=[{'context':'mode','equals':'first'}];rule['conditions']=[{'context':'mode','equals':'second'}]
            self.assertFalse(bind_relation_facts([sig,rule]),change)

    def test_bound_tables_keep_read_write_access_count_width_gates_and_conditions(self):
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_knowledge import parameter_usage_facts
        for direction in ['output','in_out']:
            signature,effect=self.pair(32,data_direction=direction,gates=True)
            effect['conditions']=[{'context':'cpu_serial_first_five','minimum':12345}]
            effect['status']='candidate_evidence'
            relation,=bind_relation_facts([signature,effect])
            self.assertEqual(relation['status'],'candidate_evidence')
            self.assertEqual(relation['conditions'],effect['conditions'])
            self.assertEqual(len(relation['value']['operand_binding']['execution_arguments']),2)
            enriched,=parameter_usage_facts([signature,relation])
            rows={p['name']:p for p in enriched['value']['operands']}
            self.assertEqual(rows['buffer']['direction'],direction)
            self.assertEqual(rows['buffer']['semantic_access'],'read_write')
            self.assertEqual(rows['buffer']['word_device_layout']['word_count'],2)
            self.assertEqual(rows['turns']['word_device_layout']['word_count'],1)
            self.assertTrue(rows['buffer']['range']['digit_specified_bit_devices']['partial_width_supported'])
            self.assertEqual(enriched['status'],'candidate_evidence')
            self.assertEqual(enriched['conditions'],effect['conditions'])
            self.assertNotIn('behavior',relation['value'])
        signature,effect=self.pair(32)
        signature['value']['operands'][0]['type']={'kind':'int','bits':16,'signed':True}
        signature['value']['operands'][1]['type']='DWORD'
        self.assertEqual(len(bind_relation_facts([signature,effect])),1)

    def test_partial_digit_rotation_matches_bit_list_with_data_width_modulus(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_definition import evaluate_expression
        bodies={}
        for width in [16,32]:
            for direction in ['left','right']:
                for carry in [False,True]:
                    s,e=self.pair(width,direction,carry)
                    bodies[(width,direction,carry)]=bind_relation_facts([s,e])[0]['value']
        @st.composite
        def cases(draw):
            width=draw(st.sampled_from([16,32]));active=draw(st.integers(1,width//4))*4
            return (width,active,draw(st.sampled_from(['left','right'])),draw(st.booleans()),
                    draw(st.integers(0,(1<<active)-1)),draw(st.booleans()),draw(st.integers(0,65535)))
        @settings(max_examples=180,deadline=None,database=None)
        @given(cases())
        @example((16,12,'right',True,0xabc,True,15))
        @example((16,16,'left',True,0,True,17))
        @example((32,24,'right',False,0xabcdef,False,31))
        @example((32,32,'left',True,0xffffffff,False,32))
        def check(case):
            width,active,direction,with_carry,original,carry,count=case
            body=bodies[(width,direction,with_carry)]
            normalized=evaluate_expression(body['iteration']['count_expression'],{'turns':count},state={'effective_operand_bits':active})
            self.assertEqual(normalized,count%active)
            self.assertEqual(body['iteration']['reference_domain'],'positive_effective_count')
            if normalized==0:return  # Carry at zero remains a source unknown.
            actual,actual_carry=original,carry
            out=body['reference_step']['outputs']
            for _ in range(normalized):
                state={'data_before_step':actual,'carry_before_step':actual_carry,'effective_operand_bits':active}
                for name in ('active_highest_bit', 'active_data_mask'):
                    state[name]=evaluate_expression(body['state_bindings'][name]['derived_value'],{},state=state)
                actual=evaluate_expression(out[0]['expression'],{},state=state)
                actual_carry=evaluate_expression(out[1]['expression'],{},state=state)
            bits=[bool(original&(1<<i)) for i in range(active)];expected_carry=carry
            for _ in range(count%active):
                edge=bits[0] if direction=='right' else bits[-1]
                inserted=expected_carry if with_carry else edge
                bits=bits[1:]+[inserted] if direction=='right' else [inserted]+bits[:-1]
                expected_carry=edge
            self.assertEqual((actual,actual_carry),(sum(int(b)<<i for i,b in enumerate(bits)),expected_carry))
        check()

    def test_byte_exchange_source_and_input_port_storage_side_effect(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _byte_exchange_specs
        from plc.instruction_relations import bind_relation_facts
        from plc.instruction_knowledge import parameter_usage_facts
        from plc.instruction_definition import evaluate_expression
        sentence='Exchanges the higher and lower 8 bits of the device specified for (data).'
        section=SimpleNamespace(kind='structured',statements=lambda _: [sentence])
        spec,=_byte_exchange_specs(section,'SYNTH')
        scope={'models':['Q03UDV'],'forms':['SYNTH'],'languages':['st']}
        for direction in ['input','output','in_out']:
            signature=fact('declaration','parameters.typed_operands',scope=scope,value={'declaration_kind':'instruction',
                'operands':[{'name':'data','type':'ANY16','direction':direction}]})
            effect=fact('operation','effects.operation',scope=scope,value=spec['value'])
            relation,=bind_relation_facts([signature,effect])
            self.assertEqual(evaluate_expression(relation['value']['outputs'][0]['expression'],{'data':0x1234}),0x3412)
            enriched,=parameter_usage_facts([signature,relation])
            self.assertEqual(enriched['value']['operands'][0]['semantic_access'],'read_write')
            self.assertEqual(enriched['value']['operands'][0]['direction'],direction)
            bad=copy.deepcopy(effect);bad['value'].pop('write_semantics')
            self.assertFalse(bind_relation_facts([signature,bad]))
        section.statements=lambda _: [sentence.replace('higher and lower 8 bits','higher 8 bits and lower 8 bits')]
        self.assertEqual(len(_byte_exchange_specs(section,'SYNTH')),1)
        section.statements=lambda _: ['A program example: '+sentence]
        self.assertFalse(_byte_exchange_specs(section,'SYNTH'))


    def test_old_fixed_word_summary_is_only_refined_by_its_complete_source_rule(self):
        from plc.instruction_knowledge import normalize_rotation_claims
        _,rule=self.pair(32,carry=False)
        old=fact('old-summary','effects.operation',scope=copy.deepcopy(rule['scope']),value={
            'operation':'rotate_bits','count':'n_modulo_32','width_bits':32,'direction':'right','carry_in_rotation':False})
        unchanged,history=normalize_rotation_claims([old])
        self.assertEqual((unchanged,history),([old],[]))
        changed,history=normalize_rotation_claims([old,rule])
        self.assertEqual(history,[old])
        self.assertEqual(changed[0]['value']['count'],'n_modulo_effective_operand_bits')
        self.assertEqual(changed[0]['depends_on'],[rule['id']])
        self.assertEqual(normalize_rotation_claims(changed),(changed,[]))
        other=copy.deepcopy(rule);other['sources']=[{'source_id':'unrelated'}]
        self.assertEqual(normalize_rotation_claims([old,other]),([old,other],[]))


    def test_ql_documented_family_bindings_keep_model_and_literal_form_boundaries(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _knowledge_form
        expected = {
            'ROR': ('ROR', False, False, 16), 'DRORP': ('ROR', True, True, 32),
            'ROL': ('ROL', False, False, 16), 'DROLP': ('ROL', True, True, 32),
            'RCR': ('RCR', False, False, 16), 'DRCRP': ('RCR', True, True, 32),
            'RCL': ('RCL', False, False, 16), 'DRCLP': ('RCL', True, True, 32),
            'SWAP': ('SWAP', False, False, 16), 'SWAPP': ('SWAP', False, True, 16),
        }
        for cpu in ('Q00J', 'Q06H', 'Q12PH', 'Q12PRH', 'Q03UDV', 'L06'):
            for opcode, (base, double, pulse, bits) in expected.items():
                with self.subTest(cpu=cpu, opcode=opcode):
                    actual = _knowledge_form(registry, opcode, cpu=cpu)
                    self.assertTrue(actual['programming_binding'])
                    self.assertEqual((actual['family'], actual['double'], actual['pulse'], actual['data_bits']),
                                     (base, double, pulse, bits))
            self.assertFalse((_knowledge_form(registry, 'DSWAP', cpu=cpu) or {}).get('programming_binding'))
        facts = [fact for definition in registry._family_knowledge for fact in definition['facts']
                 if ':ql:documented_forms:' in fact['id']]
        self.assertEqual(len(facts), 10)
        self.assertTrue(all(len(fact['scope']['models']) == 53 and
                            all(model.startswith(('Q', 'L')) for model in fact['scope']['models']) for fact in facts))
        self.assertIsNone(registry.resolve_catalog_form('DRORP', cpu='Q03UDV'))




class SharedInterfaceShapeTests(unittest.TestCase):
    declarations = SharedDefinitionBindingTests.declarations
    def test_shared_body_rejects_extra_data_and_foreign_overload(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature = self.declarations()
        for extra in [{'name':'extra','type':'DINT','direction':'input'},
                      {'name':'EN','type':'BOOL','direction':'input'}]:
            wrong=copy.deepcopy(signature);wrong['value']['operands'].append(extra)
            self.assertFalse(_bind_shared_definition(definition,binding,[wrong]))
        for field,value in [('instruction_variant','another_overload'),('declaration_kind','function_block')]:
            wrong=copy.deepcopy(signature);wrong['value'][field]=value
            definition['shared_values']['calculation']['value']['declaration_kind']='instruction'
            self.assertFalse(_bind_shared_definition(definition,binding,[wrong]))

    def test_source_declared_gates_keep_dependency_status_and_execution_arguments(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition,binding,signature=self.declarations()
        definition['shared_values']['calculation']['value']['operand_binding']={'inputs':[]}
        signature['value']['operands'] += [{'name':'EN','type':'BOOL','direction':'input'},
                                         {'name':'ENO','type':'BOOL','direction':'output'}]
        gate=fact('enable','execution.enable',scope=copy.deepcopy(signature['scope']),
                  status='unknown',value={'has_EN_ENO':True})
        self.assertFalse(_bind_shared_definition(definition,binding,[signature]))
        body,=_bind_shared_definition(definition,binding,[signature,gate])
        self.assertEqual(body['status'],'unknown')
        self.assertEqual(body['depends_on'],['declaration','enable'])
        self.assertEqual(body['value']['operand_binding']['execution_arguments'],[
            {'parameter':'EN','role':'execution_condition'},{'parameter':'ENO','role':'execution_status'}])

    def test_old_claim_is_replaced_only_after_binding_its_exact_interface(self):
        from plc.instruction_knowledge import _bind_shared_definition,merge_family_facts
        definition,binding,signature=self.declarations()
        old=fact('old','effects.operation',value={'operation':'weak_summary'})
        binding['replaces']=['old']
        wrong=copy.deepcopy(signature);wrong['sources']=[{'source_id':'another'}]
        merged,history=merge_family_facts([old],_bind_shared_definition(definition,binding,[wrong]))
        self.assertEqual((merged,history),([old],[]))
        merged,history=merge_family_facts([old],_bind_shared_definition(definition,binding,[signature]))
        self.assertEqual(history,[old]);self.assertNotIn('old',[f['id'] for f in merged])


class SpanAndTableKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        cls.definitions={d['base_opcode']:d for d in registry._family_knowledge
                         if d.get('shared_values',{}).get('operation',{}).get('value',{}).get('operation')
                         in {'shift_device_span','mutate_counted_word_table'}}

    def body(self,base,key='operation'):
        return self.definitions[base]['shared_values'][key]['value']

    def transition(self,body,memory,state):
        """Evaluate documented indexed expressions against a pre-write snapshot."""
        from plc.instruction_definition import evaluate_expression
        result=copy.deepcopy(memory)
        evaluate=lambda node,local:evaluate_expression(node,{},state=local)
        for rule in body['reference_transition']['writes']:
            domain=rule.get('index_domain')
            offsets=range(evaluate(domain['start'],state),evaluate(domain['end_exclusive'],state)) if domain else [None]
            for offset in offsets:
                local=dict(state)
                if domain:local[domain['name']]=offset
                read=rule.get('read_binding')
                if read:local['selected_old_element']=memory[read['source_parameter']['parameter_ref']][evaluate(read['offset'],local)]
                target=rule['target'];value=evaluate(rule['expression'],local)
                if target.get('device'):result[target['device']]=[value]
                else:
                    parameter=target['parameter']['parameter_ref']
                    index=evaluate(target['offset'],local) if 'offset' in target else 0
                    result[parameter][index]=value
        return result

    def test_source_span_extraction_requires_own_operation_fill_and_bit_flag(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _span_shift_specs
        lines=['Shifts data count points from device designated by (buffer) to the right by one bit.',
               'The device designated by (buffer) + (count - 1) is filled with 0.', 'Carry flag (SM42)']
        section=SimpleNamespace(kind='ladder',chapter='Synthetic > Shift Instructions',text=' '.join(lines))
        section.statements=lambda _symbol:lines
        first=_span_shift_specs(section,'ALPHA')
        self.assertEqual(len(first),2);self.assertEqual(first,_span_shift_specs(section,'OMEGA'))
        self.assertEqual(first[0]['value']['range_count_operand'],'count')
        for i in range(3):
            original=lines[:];del lines[i];section.text=' '.join(lines)
            self.assertFalse(_span_shift_specs(section,'ALPHA'))
            lines[:]=original;section.text=' '.join(lines)
        lines[:]=['Shifts data count points from device designated by (buffer) 1-word to the left.',
                  'The device designated by (buffer) becomes 0.']
        section.text=' '.join(lines)
        word=_span_shift_specs(section,'ALPHA')[0]['value']
        self.assertEqual(word['element_bits'],16);self.assertNotIn('carry_device',word)

    def test_explicit_forms_and_interface_directions_do_not_infer_name_modifiers(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _knowledge_form,_definition_value
        self.assertEqual(len(self.definitions),9)
        for base,definition in self.definitions.items():
            for cpu in ('Q00J','Q06H','Q12PH','Q12PRH','Q03UDV','L06'):
                for op in (base,base+'P'):
                    form=_knowledge_form(registry,op,cpu=cpu)
                    self.assertEqual((form['family'],form['pulse'],form['double']),(base,op==base+'P',False))
            self.assertTrue(all(all(model.startswith(('Q','L')) for model in f['scope']['models']) for f in definition['facts']))
            self.assertFalse((_knowledge_form(registry,base,cpu='FX5U') or {}).get('programming_binding'))
            for binding in definition['facts']:
                if binding['dimension']!='effects.documented_processing':continue
                resolved=_definition_value(definition,binding['value'])
                self.assertEqual(resolved['executor_capability'],'not_established');self.assertNotIn('behavior',resolved)
                if base in {'FIFR','FPOP','FDEL'}:
                    data=binding['value']['parameter_bindings']['DATA']
                    self.assertEqual(binding['value']['signature']['parameter_directions'][data],
                                     'input')
                    self.assertEqual(next(p for p in resolved['parameter_usage'] if p['parameter']==data)['semantic_access'],'write')

    def test_span_relations_match_sequence_slicing_and_shifted_out_edge(self):
        from hypothesis import given,settings,strategies as st,example
        @settings(max_examples=120,deadline=None,database=None)
        @given(st.lists(st.integers(0,65535),min_size=1,max_size=32))
        @example([65535])
        @example([0,32768,65535])
        def check(words):
            for base in ('BSFR','BSFL','DSFR','DSFL'):
                body=self.body(base);right=base.endswith('R')
                values=[bool(v&1) for v in words] if body['element_bits']==1 else words
                actual=self.transition(body,{'BUFFER':values,'SM700':[False]}, {'span_length':len(values)})
                expected=values[1:]+[0] if right else [0]+values[:-1]
                self.assertEqual(actual['BUFFER'],expected)
                if body['element_bits']==1:self.assertEqual(actual['SM700'],[values[0] if right else values[-1]])
                else:self.assertEqual(actual['SM700'],[False])
        check()

    def test_table_relations_match_independent_list_operations_and_clear_old_tail(self):
        from hypothesis import given,settings,strategies as st,example
        @st.composite
        def cases(draw):
            words=draw(st.lists(st.integers(0,65535),min_size=1,max_size=24))
            return words,draw(st.integers(0,65535)),draw(st.integers(1,len(words))),draw(st.integers(1,len(words)+1))
        @settings(max_examples=140,deadline=None,database=None)
        @given(cases())
        @example(([32768],65535,1,2))
        @example(([7,7,0],0,3,1))
        def check(case):
            words,scalar,delete_at,insert_at=case
            for base in ('FIFW','FIFR','FPOP','FDEL','FINS'):
                body=self.body(base);position=insert_at if base=='FINS' else delete_at
                expected=words[:];received=scalar
                if base=='FIFW':expected.append(scalar)
                elif base=='FIFR':received=expected.pop(0)
                elif base=='FPOP':received=expected.pop()
                elif base=='FDEL':received=expected.pop(position-1)
                else:expected.insert(position-1,scalar)
                memory={'TABLE':[len(words),*words,4567,5678],'DATA':[scalar]}
                state={'table_count_before':len(words),'position_value':position,'scalar_before':scalar}
                actual=self.transition(body,memory,state)
                self.assertEqual(actual['TABLE'][0],len(expected));self.assertEqual(actual['TABLE'][1:len(expected)+1],expected)
                self.assertEqual(actual['DATA'],[received]);self.assertEqual(actual['TABLE'][-1],5678)
                if base in {'FIFR','FPOP','FDEL'}:self.assertEqual(actual['TABLE'][len(words)],0)
        check()

    def test_structured_table_output_branch_limit_is_scoped_to_its_parameter(self):
        from plc.instruction_knowledge import _definition_value
        for base in ('FIFW','FDEL','FINS'):
            definition=self.definitions[base]
            facts=[f for f in definition['facts'] if f['dimension']=='parameters.2.condition']
            self.assertEqual({tuple(f['scope']['languages']) for f in facts},{('ladder',),('fbd',)})
            for f in facts:
                value=_definition_value(definition,f['value'])
                self.assertEqual(value['symbol'],'d');self.assertIs(value['value']['branching_permitted'],False)
                self.assertTrue(all(s.get('manual_id')=='sh080783engp' for s in f['sources']))

    def test_table_boundary_conditions_and_unknowns_remain_data_owned(self):
        from plc.instruction_definition import evaluate_expression
        for base in ('FIFW','FIFR','FPOP','FDEL','FINS'):
            body=self.body(base);insert=base in {'FIFW','FINS'};positioned=base in {'FDEL','FINS'}
            error=self.body(base,'errors')
            self.assertEqual(error['on_operation_error'],{'set_bit':'SM0','error_code_register':'SD0'})
            for count in (0,1,65535):
                for position in (0,1,count,count+1):
                    state={'table_count_before':count,'position_value':position}
                    expected=(count<65535 if insert else count>0) and (not positioned or 1<=position<=count+(1 if insert else 0))
                    valid=all(evaluate_expression(e,{},state=state) for e in body['valid_reference_conditions'])
                    self.assertEqual(valid,expected,(base,count,position))
                    codes=[case['error_code'] for case in error['cases'] if case['condition'].get('op') and evaluate_expression(case['condition'],{},state=state)]
                    if (insert and count==65535) or (not insert and count==0) or (positioned and position==0):self.assertIn(4100,codes)
                    if positioned and position>count+(1 if insert else 0):self.assertIn(4101,codes)
            unknown=self.body(base,'unresolved')['unresolved_items']
            self.assertIn('overlapping_scalar_and_mutable_table_storage',unknown)
            self.assertIn('state_after_operation_error',unknown)
            self.assertEqual(error['state_bindings']['table_count_before'],body['state_bindings']['table_count_before'])
        for base in ('FIFW','FINS'):
            actual=self.transition(self.body(base),{'TABLE':[0,4567,5678],'DATA':[32768]},
                {'table_count_before':0,'position_value':1,'scalar_before':32768})
            self.assertEqual(actual,{'TABLE':[1,32768,5678],'DATA':[32768]})
        for base in ('BSFR','BSFL','DSFR','DSFL'):
            self.assertIn('device_span_shift_with_zero_or_negative_count',self.body(base,'unresolved')['unresolved_items'])




class ScalarShiftAndCpuLabelTests(unittest.TestCase):
    def test_cpu_label_lists_match_each_group_without_absorbing_other_models(self):
        from tools.fact_ql_behavior_review import _model_matches_cpu_label
        groups='QnUDVCPU, QnUDPVCPU, LCPU'
        for model in ('Q03UDV','Q26UDV','Q04UDPV','Q26UDPV','L06','L26-P'):
            self.assertTrue(_model_matches_cpu_label(model,groups),model)
        for model in ('Q00J','Q06H','Q12PH','Q12PRH','Q03UD','Q03UDE','FX3U','FX5U','R04'):
            self.assertFalse(_model_matches_cpu_label(model,groups),model)
        self.assertTrue(_model_matches_cpu_label('L02S','LCPU'))
        self.assertFalse(_model_matches_cpu_label('L02S','LCPU with built-in Ethernet'))

    def test_serial_condition_binds_only_its_listed_cpu_groups_and_stays_unknown_elsewhere(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _cpu_conditions,_formal_conditions
        from plc.instruction_constraints import _condition
        section=SimpleNamespace(lines=['QnUDVCPU, QnUDPVCPU, LCPU: serial number "16112" or later.'])
        for model in ('Q03UDV','Q04UDPV','L06'):
            conditions,classified=_formal_conditions(_cpu_conditions(section,model,'conditional','SYNTH'))
            self.assertTrue(classified);self.assertEqual(len(conditions),1)
            self.assertFalse(_condition(conditions[0],{}, {'cpu_serial_first_five':16111}))
            self.assertTrue(_condition(conditions[0],{}, {'cpu_serial_first_five':16112}))
        for model in ('Q00U','Q03UDE','Q13UDH'):
            conditions,classified=_formal_conditions(_cpu_conditions(section,model,'conditional','SYNTH'))
            self.assertFalse(classified);self.assertEqual(conditions[0]['value'],'unknown')

    def test_scalar_shifts_keep_16_bit_counts_partial_device_width_and_literal_forms(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _knowledge_form,_definition_value
        for base in ('SFR','SFL'):
            definition=next(d for d in registry._family_knowledge if d['base_opcode']==base)
            bindings=[f for f in definition['facts'] if f['dimension']=='effects.documented_processing']
            self.assertEqual(len(bindings),4)
            for f in bindings:
                body=_definition_value(definition,f['value'])
                self.assertEqual(body['container_bits'],16);self.assertEqual(body['executor_capability'],'not_established')
                self.assertNotIn('behavior',body)
                count=next(u for u in body['parameter_usage'] if u['parameter']=='n')
                self.assertEqual(count['operation_value_type']['bits'],16)
                self.assertEqual(count['word_device_layout']['word_count'],1)
                self.assertEqual(body['state_bindings']['effective_operand_bits']['digit_specified_bit_device']['maximum_bits'],16)
                self.assertEqual(len(f['scope']['models']),53)
            for op in (base,base+'P'):
                form=_knowledge_form(registry,op,cpu='Q03UDV')
                self.assertEqual((form['family'],form['double'],form['pulse']),(base,False,op==base+'P'))
            self.assertEqual(_knowledge_form(registry,'D'+base,cpu='Q03UDV')['family'],'D'+base)

    def test_shift_relations_match_bit_lists_at_partial_width_and_modulo_boundaries(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_definition import evaluate_expression
        bodies={d['base_opcode']:d['shared_values']['operation']['value'] for d in registry._family_knowledge if d['base_opcode'] in {'SFR','SFL'}}
        @st.composite
        def cases(draw):
            width=draw(st.sampled_from([4,8,12,16]))
            return width,draw(st.integers(0,(1<<width)-1)),draw(st.integers(0,65535)),draw(st.booleans())
        @settings(max_examples=150,deadline=None,database=None)
        @given(cases())
        @example((8,0x80,15,True))
        @example((16,0xFFFF,16,True))
        @example((4,0xF,0,False))
        @example((12,0xFFF,31,False))
        def check(case):
            width,word,count,old_carry=case;steps=count%width
            bits=[bool(word&(1<<i)) for i in range(width)]
            for base,body in bodies.items():
                state={'data_before_shift':word,'effective_operand_bits':width,'active_data_mask':(1<<width)-1}
                state['effective_shift_count']=evaluate_expression(body['state_bindings']['effective_shift_count']['expression'],{'COUNT':count},state=state)
                self.assertEqual(state['effective_shift_count'],steps)
                rules=body['reference_transition']['writes']
                actual=evaluate_expression(rules[0]['expression'],{},state=state)
                carry=old_carry
                if evaluate_expression(rules[1]['when']['predicate'],{},state=state):
                    carry=evaluate_expression(rules[1]['expression'],{},state=state)
                expected=bits if not steps else bits[steps:]+[False]*steps if base=='SFR' else [False]*steps+bits[:-steps]
                expected_carry=old_carry if not steps else bits[steps-1] if base=='SFR' else bits[-steps]
                self.assertEqual((actual,carry),(sum(int(bit)<<i for i,bit in enumerate(expected)),expected_carry))
                if not steps:self.assertEqual(body['zero_effective_count']['carry_response'],'not_stated_in_reviewed_source')
                self.assertEqual(next(d for d in registry._family_knowledge if d['base_opcode']==base)['shared_values']['errors']['value']['operation_error'],'none')
        check()




class ConditionalRangeShiftTests(unittest.TestCase):
    def test_ql_forms_reuse_base_math_and_keep_exact_native_serial_scope(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _knowledge_form,_definition_value
        for base in ('SFTR','SFTL','WSFR','WSFL'):
            definition=next(d for d in registry._family_knowledge if d['base_opcode']==base)
            key='behavior' if base.startswith('SFT') else 'effect'
            ql=[f for f in definition['facts'] if f['id'].startswith('family:'+base+':ql:')]
            operation,=[f for f in ql if f['dimension']=='effects.documented_processing']
            self.assertEqual(operation['value']['definition_ref'],key)
            self.assertEqual(operation['scope']['languages'],['ladder'])
            self.assertEqual(len(operation['scope']['models']),19)
            self.assertTrue(all(m.startswith('L') or 'UDV' in m or 'UDPV' in m for m in operation['scope']['models']))
            self.assertTrue(all(s.get('manual_id')=='sh080809engx' for s in operation['sources']))
            self.assertTrue(any(c.get('minimum')=='16112' for c in operation['conditions']))
            self.assertTrue(all(f['value']['definition_ref']==key for f in definition['facts']
                                if f['dimension']=='effects.documented_processing'))
            body=_definition_value(definition,operation['value'])
            self.assertEqual(body['executor_capability'],'not_established');self.assertNotIn('behavior',body)
            self.assertNotIn('only_same_head_restriction_is_stated',json.dumps(body))
            for op in (base,base+'P'):
                allowed={'cpu_serial_first_five':16112,'project_mode':'native_ladder_instruction'}
                self.assertTrue(_knowledge_form(registry,op,cpu='Q03UDV',context=allowed)['programming_binding'])
                forbidden=dict(allowed,cpu_serial_first_five=16111)
                self.assertFalse((_knowledge_form(registry,op,cpu='Q03UDV',context=forbidden) or {}).get('programming_binding'))
            for cpu in ('Q00J','Q06H','Q03UDE','FX5U','R04'):
                self.assertFalse((_knowledge_form(registry,base,cpu=cpu) or {}).get('programming_binding'))

    def test_ql_error_predicates_cover_negative_range_order_and_real_overlap_span(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _definition_value
        from plc.instruction_definition import evaluate_expression
        for base in ('SFTR','SFTL','WSFR','WSFL'):
            d=next(d for d in registry._family_knowledge if d['base_opcode']==base)
            f=next(f for f in d['facts'] if f['id']=='family:'+base+':ql:ql_error_response')
            error=_definition_value(d,f['value']);maximum=1024 if base.startswith('SFT') else 512
            for n1,n2,code in [(-1,0,4100),(maximum+1,0,4100),(1,-1,4100),(2,3,4101)]:
                actual=[case['error_code'] for case in error['cases'] if case['condition'].get('op') and evaluate_expression(case['condition'],{'n1':n1,'n2':n2})]
                self.assertIn(code,actual)
            for n1,n2 in [(0,0),(maximum,0),(maximum,maximum)]:
                self.assertFalse(any(evaluate_expression(case['condition'],{'n1':n1,'n2':n2}) for case in error['cases'] if case['condition'].get('op')))
            overlap=next(case for case in error['cases'] if case['condition'].get('resource')=='memory_ranges_overlap')
            self.assertEqual(overlap['condition']['left'],{'head':'S','count_operand':'n2'})
            self.assertEqual(overlap['condition']['right'],{'head':'D','count_operand':'n1'})
            self.assertEqual(overlap['error_code'],4101)
            self.assertEqual(error['on_operation_error'],{'set_bit':'SM0','error_code_register':'SD0'})
            self.assertIn('zero_length_destination_state',d['shared_values']['ql_unresolved']['value']['unresolved_items'])
            if base.startswith('WS'):
                for name in ('S','D'):
                    self.assertIn('same_digit_count_as',d['shared_values']['ql_digits_'+name]['value']['value']['digit_specified_bit_devices'])

    def test_fx_specific_unknowns_remain_on_fx_scopes_after_shared_math_cleanup(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for base in ('SFTR','SFTL','WSFR','WSFL'):
            d=next(d for d in registry._family_knowledge if d['base_opcode']==base)
            limits=[f for f in d['facts'] if f['value'].get('definition_ref')=='fx_review_limits']
            self.assertTrue(limits)
            self.assertTrue(all(f['status']=='unknown' and all(m.startswith('FX3') for m in f['scope']['models']) for f in limits))
            self.assertEqual(d['shared_values']['fx_review_limits']['value']['gap_kind'],'reviewed_source_not_stated')




class SharedDefinitionDetailTests(unittest.TestCase):
    declarations = SharedDefinitionBindingTests.declarations

    def test_explicit_details_bind_the_same_operands_and_keep_their_conditions(self):
        from plc.instruction_knowledge import _definition_value,_bind_shared_definition
        definition,binding,signature=self.declarations()
        definition['shared_values']['device_difference']={'parameters':['A','B','D'],
            'value':{'mode_device':'SM42','selected_operand':{'parameter_ref':'A'}},
            'conditions':[{'context':'edition','equals':'with_mode'}]}
        binding['value']['detail_refs']=['device_difference']
        body,=_bind_shared_definition(definition,binding,[signature])
        self.assertEqual(body['value']['mode_device'],'SM42');self.assertEqual(body['value']['selected_operand'],'x')
        self.assertEqual(body['value']['device_family'],'D')
        self.assertEqual(body['conditions'],[{'context':'edition','equals':'with_mode'}])
        self.assertNotIn('detail_refs',body['value'])
        self.assertEqual(body['depends_on'],['declaration'])
        self.assertNotIn('mode_device',definition['shared_values']['calculation']['value'])
        self.assertEqual(_definition_value(definition,dict(binding['value'],detail_refs=[])),
                         _definition_value(definition,{k:v for k,v in binding['value'].items() if k!='detail_refs'}))

    def test_details_reject_unknown_references_conflicts_and_partial_operand_maps(self):
        from plc.instruction_knowledge import _definition_value
        for refs in ['platform',['missing'],['platform','platform'],['calculation']]:
            definition,binding,_=self.declarations()
            definition['shared_values']['platform']={'parameters':['A','B','D'],'value':{'flag':'SM42'}}
            binding['value']['detail_refs']=refs
            with self.subTest(refs=refs),self.assertRaises(ValueError):_definition_value(definition,binding['value'])
        for parameters,value in [(['A'],{'flag':'SM42'}),(['A','B','D'],{'rules':[]})]:
            definition,binding,_=self.declarations()
            definition['shared_values']['platform']={'parameters':parameters,'value':value}
            binding['value']['detail_refs']=['platform']
            with self.subTest(parameters=parameters,value=value),self.assertRaises(ValueError):
                _definition_value(definition,binding['value'])

    def test_an_explicit_device_name_does_not_become_a_numeric_quantity(self):
        from tools.fact_ql_behavior_review import _native_data_type
        self.assertEqual(_native_data_type('Start number of devices containing samples (Device name)')['type'],'DEVICE_REFERENCE')
        self.assertEqual(_native_data_type('Head number of a destination (device name)')['type'],'DEVICE_REFERENCE')
        self.assertEqual(_native_data_type('Number of devices to transfer')['type'],'INTEGER')
        self.assertEqual(_native_data_type('Number of digits (BIN 16 bits)')['type'],'ANY16')
        self.assertEqual(_native_data_type('Start number (Device name)','WORD')['type'],'WORD')


class SourceErrorStatementTests(unittest.TestCase):
    def test_a_shared_error_table_does_not_certify_each_distinct_operation(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _error_response_spec
        section = SimpleNamespace(symbols=['ALPHA', 'ALPHAP', 'BETA', 'BETAP'],
                                  symbol_tokens=['ALPHA(P)', 'BETA(P)'])
        rule = _error_response_spec(section, 'synthetic error statement', {'operation_error': 'documented'})
        self.assertEqual(rule['status'], 'unknown')
        self.assertEqual(rule['value']['form_applicability'], 'unknown')
        for symbols, tokens, value in [(['ALPHA', 'ALPHAP'], ['ALPHA(P)'], {'operation_error': 'documented'}),
                (section.symbols, section.symbol_tokens, {'operation_error': 'none'})]:
            selected = _error_response_spec(SimpleNamespace(symbols=symbols, symbol_tokens=tokens),
                'synthetic error statement', value)
            self.assertNotIn('status', selected)
            self.assertNotIn('form_applicability', selected['value'])

    def test_no_error_statement_is_not_an_operation_error_and_keeps_its_witness(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import Section,_witness,_NO_OPERATION_ERROR_PATTERN
        for sentence in ['There is no error in the ALPHA(P) or BETA(P) instruction.',
                         'There is no operation error.', 'No operation error occurs.']:
            section=SimpleNamespace(lines=['Operation error',sentence,'Program example'])
            block=Section.error_block(section)
            self.assertEqual(block['operation_error'],'none')
            self.assertTrue(_witness(block['_text'],(_NO_OPERATION_ERROR_PATTERN,)))
        positive=SimpleNamespace(lines=['Operation error',
            'An operation error occurs and the error flag (SM0) turns ON. Its code is stored in SD0.', 'Program example'])
        error=Section.error_block(positive)
        self.assertEqual(error['operation_error'],'documented')
        self.assertEqual(error['error_flags'],['SM0']);self.assertEqual(error['error_code_registers'],['SD0'])


class SmovSourceDifferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        cls.definition=next(d for d in registry._family_knowledge if d['base_opcode']=='SMOV')

    def test_both_source_groups_reuse_one_algorithm_and_bind_only_their_real_mode_device(self):
        from plc.instruction_knowledge import _definition_value
        d=self.definition
        operations=[f for f in d['facts'] if f['dimension']=='effects.documented_processing']
        self.assertTrue(operations);self.assertTrue(all(f['value'].get('definition_ref')=='operation' for f in operations))
        self.assertNotIn('mode_device',d['shared_values']['operation']['value'])
        for f in operations:
            body=_definition_value(d,f['value']);ql=f['id'].startswith('family:SMOV:ql:')
            self.assertEqual(body['mode_device'],'SM719' if ql else 'M8168')
            self.assertEqual({case['when_mode_device'] for case in body['mode_cases']},{False,True})
            self.assertEqual(body['mapping']['unselected_destination_digits'],'retained')
            self.assertEqual(body['executor_capability'],'not_established');self.assertNotIn('behavior',body)
            if ql:
                self.assertNotIn('after_using_extension',body)
                self.assertNotIn('M8168',json.dumps(body))
                self.assertEqual(f['scope']['languages'],['ladder'])
            else:self.assertEqual(body['after_using_extension'],'return_M8168_to_OFF')

    def test_native_scope_serials_and_parameter_storage_follow_their_own_source(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import _knowledge_form,_definition_value
        ql=[f for f in self.definition['facts'] if f['id'].startswith('family:SMOV:ql:')]
        for suffix,count,minimum in [('q',9,'16043'),('l',10,'16042')]:
            usage=next(f for f in ql if f['id']=='family:SMOV:ql:'+suffix+':parameters_ql')
            self.assertEqual(len(usage['scope']['models']),count)
            self.assertTrue(any(c.get('minimum')==minimum for c in usage['conditions']))
            body=_definition_value(self.definition,usage['value'])
            self.assertEqual([p['name'] for p in body['operands']],['S','n1','n2','D','n3'])
            for p in body['operands']:
                self.assertEqual(p['type']['bits'],16);self.assertEqual(p['word_device_layout']['word_count'],1)
                self.assertNotEqual(p.get('value_semantics'),'constant_in_documented_device_table')
            descriptor=usage['value']['signature']
            self.assertEqual(descriptor['parameter_types']['S'],'DEVICE_REFERENCE')
            self.assertEqual(descriptor['parameter_types']['D'],'DEVICE_REFERENCE')
        for cpu in ('Q03UDV','L06'):
            minimum=16043 if cpu.startswith('Q') else 16042
            for op in ('SMOV','SMOVP'):
                before={'cpu_serial_first_five':minimum-1,'project_mode':'native_ladder_instruction'}
                after=dict(before,cpu_serial_first_five=minimum)
                self.assertFalse((_knowledge_form(registry,op,cpu=cpu,context=before) or {}).get('programming_binding'))
                self.assertTrue(_knowledge_form(registry,op,cpu=cpu,context=after)['programming_binding'])
        for cpu in ('Q00J','Q06H','Q12PH','Q12PRH','Q03UDE','FX5U','R04'):
            self.assertFalse((_knowledge_form(registry,'SMOV',cpu=cpu) or {}).get('programming_binding'))

    def test_conversion_error_uses_mode_and_both_old_words_without_assuming_missing_state(self):
        from plc.instruction_knowledge import _definition_value
        from plc.instruction_definition import evaluate_expression,UnknownInstructionSemantics
        f=next(f for f in self.definition['facts'] if f['id']=='family:SMOV:ql:q:ql_error_response')
        body=_definition_value(self.definition,f['value']);error=body['cases'][0]
        self.assertEqual(error['error_code'],4100)
        for source in (0,9999,10000,65535):
            for destination in (0,9999,10000,65535):
                for mode in (False,True):
                    state={'source_word_before':source,'destination_word_before':destination,'conversion_inhibit_before':mode}
                    self.assertEqual(evaluate_expression(error['condition'],{},state=state),not mode and (source>9999 or destination>9999))
        with self.assertRaises(UnknownInstructionSemantics):
            evaluate_expression(error['condition'],{},state={'source_word_before':10000,'destination_word_before':0})
        self.assertEqual(body['cases'][1]['error_code'],4101)
        self.assertEqual(body['state_bindings']['conversion_inhibit_before']['source_device'],'SM719')
        self.assertEqual(body['on_operation_error'],{'set_bit':'SM0','error_code_register':'SD0'})
        self.assertIn('state_after_operation_error',self.definition['shared_values']['ql_unresolved']['value']['unresolved_items'])




class BitwiseSourceReferenceTests(unittest.TestCase):
    def test_inversion_matches_each_bit_inside_its_declared_container(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression
        expressions={}
        for op,bits in [('CML',16),('CMLP',16),('DCML',32),('DCMLP',32)]:
            f=next(f for f in family_fact_groups(registry,op,cpu='FX3U') if f['dimension']=='effects.documented_processing')
            expressions[op]=(bits,f['value']['rules'][0]['outputs'][0]['expression'])
        @settings(max_examples=100,deadline=None,database=None)
        @given(st.integers(0,0xffffffff))
        @example(0)
        @example(0xffffffff)
        @example(0x80008000)
        def check(word):
            for op,(bits,expression) in expressions.items():
                original=word&((1<<bits)-1)
                expected=sum(int(not bool(original&(1<<i)))<<i for i in range(bits))
                self.assertEqual(evaluate_expression(expression,{'S':original}),expected,op)
                self.assertEqual(expression['overflow'],'wrap')
        check()




class ConversionTransferFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        cls.registry=registry;cls.groups={}
        for base in ['BCD','BIN','CML','XCH']:
            for op in [base,base+'P','D'+base,'D'+base+'P']:
                entry=registry._definition_owner_store.get_entry(op,cpu='Q03UDV',vendor='mitsubishi')
                cls.groups[op]=family_fact_groups(registry,op,cpu='Q03UDV',existing=entry['facts'])

    def body(self,op):
        return next(f['value'] for f in self.groups[op] if f['dimension']=='effects.documented_processing'
                    and f['scope']['languages']==['ladder'] and
                    {'context':'project_mode','equals':'native_ladder_instruction'} in f.get('conditions',[]))

    def error(self,op):
        return next(f['value'] for f in self.groups[op] if f['dimension']=='effects.error_response'
                    and f['scope']['languages']==['ladder'] and
                    {'context':'project_mode','equals':'native_ladder_instruction'} in f.get('conditions',[]))

    def convert(self,op,value):
        from plc.instruction_definition import evaluate_expression
        rule=self.body(op)['rules'][0]
        state={name:binding.get('initial_value',value) for name,binding in rule['state_bindings'].items()}
        for _ in range(rule['reference_iteration']['count']):
            state={name:evaluate_expression(expr,{},state=state) for name,expr in rule['reference_step']['state_updates'].items()}
        return evaluate_expression(rule['reference_result']['expression'],{},state=state)

    def test_all_literal_forms_bind_real_interfaces_without_macro_or_flag_leaks(self):
        from plc.instruction_knowledge import _knowledge_form
        for op,groups in self.groups.items():
            base=next(base for base in ['BCD','BIN','CML','XCH'] if op in [base,base+'P','D'+base,'D'+base+'P'])
            width=32 if op in ['D'+base,'D'+base+'P'] else 16
            bodies=[f for f in groups if f['dimension']=='effects.documented_processing']
            self.assertEqual(len(bodies),4,op)
            for fact in bodies:
                self.assertEqual(fact['status'],'source_verified')
                self.assertEqual(fact['scope']['models'],['Q03UDV'])
                self.assertEqual(fact['value']['executor_capability'],'not_established')
                self.assertNotIn('M8160',json.dumps(fact['value']))
                self.assertNotIn('$',json.dumps(fact['value']))
            form=_knowledge_form(self.registry,op,cpu='Q03UDV')
            self.assertEqual((form['family'],form['data_bits'],form['pulse']),(base,width,op in [base+'P','D'+base+'P']))
            if base in ['BIN','BCD']:
                rule=self.body(op)['rules'][0]
                self.assertEqual(rule['digit_count'],width//4)
                self.assertEqual(rule['maximum_decimal_value'],10**(width//4)-1)
                usage=next(f['value']['parameter_usage'] for f in groups if f['dimension']=='parameters.usage'
                           and f['scope']['languages']==['ladder'] and f.get('conditions')==[{'context':'project_mode','equals':'native_ladder_instruction'}])
                source=next(p for p in usage if p['parameter']=='S')
                self.assertEqual(source['range']['decimal_maximum'],10**(width//4)-1)
        self.assertFalse(self.registry.instruction_knowledge('DBCDPP',cpu='Q03UDV'))

    def test_conversion_reference_matches_decimal_and_hex_digit_oracles(self):
        from hypothesis import given,settings,strategies as st,example
        @st.composite
        def cases(draw):
            width=draw(st.sampled_from([16,32]));number=draw(st.integers(0,10**(width//4)-1))
            return width,number
        @settings(max_examples=120,deadline=None,database=None)
        @given(cases())
        @example((16,0))
        @example((16,9999))
        @example((32,99999999))
        @example((32,80000000))
        def check(case):
            width,number=case;prefix='D' if width==32 else ''
            expected_bcd=int(str(number),16)
            for pulse in ['', 'P']:
                packed=self.convert(prefix+'BCD'+pulse,number)
                self.assertEqual(packed,expected_bcd)
                decoded=self.convert(prefix+'BIN'+pulse,packed)
                self.assertEqual(decoded,int(format(packed,'x'),10))
                self.assertEqual(decoded,number)
        check()

    def test_conversion_error_domains_keep_every_nibble_and_encoded_negative_inputs(self):
        from hypothesis import given,settings,strategies as st,example
        from plc.instruction_definition import evaluate_expression
        @settings(max_examples=100,deadline=None,database=None)
        @given(st.integers(0,0xffffffff))
        @example(0xa)
        @example(0xa0000000)
        @example(0x99999999)
        def check(word):
            for width,prefix in [(16,''),(32,'D')]:
                value=word&((1<<width)-1);error=self.error(prefix+'BIN');condition=error['cases'][0]['condition']
                actual=any(evaluate_expression(condition['predicate'],{},state={'source_word_before':value,'digit_index':index})
                           for index in range(condition['index_domain']['start'],condition['index_domain']['end_exclusive']))
                self.assertEqual(actual,any(character in 'abcdef' for character in format(value,'x')))
        check()
        for width,prefix in [(16,''),(32,'D')]:
            maximum=10**(width//4)-1;error=self.error(prefix+'BCD')
            for value,expected in [(0,False),(maximum,False),(maximum+1,True),((1<<width)-1,True)]:
                self.assertEqual(evaluate_expression(error['cases'][0]['condition'],{},state={'source_word_before':value}),expected)
            self.assertEqual(error['cases'][0]['error_code'],4100)
            self.assertEqual(error['on_reported_error'],{'set_bit':'SM0','error_code_register':'SD0'})

    def test_bin_suppression_never_executes_invalid_conversion_or_rearms_a_held_pulse(self):
        for op in ['BIN','BINP','DBIN','DBINP']:
            error=self.error(op)
            self.assertEqual(error['error_report_suppression']['device'],'SM722')
            self.assertEqual(error['invalid_input_execution'],'not_executed_regardless_of_SM722')
            self.assertIs(error['invalid_input_destination_write'],False)
            self.assertEqual(error['pulse_form_rearm_after_evaluation'],'execution_condition_OFF_then_ON_even_after_error')
            self.assertEqual(error['preexisting_error_flag_after_suppression'],'not_stated_in_reviewed_source')
            self.assertNotIn('M8067',json.dumps(error));self.assertNotIn('M8068',json.dumps(error))

    def test_exchange_keeps_one_base_relation_and_fx_mode_as_a_scoped_difference(self):
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression
        for op,width in [('XCH',16),('DXCHP',32)]:
            ql=self.body(op)
            raw=self.registry._definition_owner_store.get_entry(op,cpu='FX3U',vendor='mitsubishi')
            fx=next(f['value'] for f in family_fact_groups(self.registry,op,cpu='FX3U',existing=raw['facts'])
                    if f['dimension']=='effects.documented_processing' and f['scope']['languages']==['ladder'])
            self.assertEqual(ql['rules'],fx['rules'])
            self.assertEqual(fx['mode_device'],'M8160')
            self.assertEqual(fx['mode_cases'][0],{'when_mode_device':False,'execute':'base_rules'})
            extension=fx['mode_cases'][1]
            self.assertIs(extension['when_mode_device'],True)
            self.assertEqual(extension['rule']['operation'],'exchange_low_high_bytes_of_each_16_bit_word')
            self.assertEqual(extension['rule']['required_operand_relation'],{'same_device':['D1','D2']})
            self.assertEqual(extension['rule']['word_count'],width//16)
            self.assertNotIn('mode_device',ql);self.assertNotIn('mode_cases',ql)
            outputs=ql['rules'][0]['outputs']
            before={'D1':(1<<width)-1,'D2':1}
            self.assertEqual([evaluate_expression(o['expression'],before) for o in outputs],[1,(1<<width)-1])
            self.assertEqual(ql['reference_domain']['distinct_partial_overlap'],'not_stated_in_reviewed_source')
            self.assertEqual(self.error(op),{'operation_error':'none'})

    def test_cml_zero_extends_before_inversion_then_writes_the_selected_target_width(self):
        from plc.instruction_definition import evaluate_expression
        body=self.body('CML');access=body['device_access']
        self.assertEqual(access['source_missing_bits'],'zero_extend_before_inversion')
        self.assertEqual(access['destination_write'],'actual_selected_destination_width')
        self.assertEqual(access['unselected_destination_bits'],'unchanged')
        expression=body['rules'][0]['outputs'][0]['expression']
        self.assertEqual(evaluate_expression(expression,{'S':0xd0}),0xff2f)
        self.assertEqual(evaluate_expression(expression,{'S':0xa1})&0xfff,0xf5e)
        self.assertEqual(evaluate_expression(expression,{'S':0x80}),0xff7f)
        self.assertEqual(self.error('DCMLP'),{'operation_error':'none'})




class ParameterOrderPresentationTests(unittest.TestCase):
    def test_independent_order_references_one_partial_typed_table_without_promoting_types(self):
        from knowledge.instruction_presentation import _parameter_tables, _groups
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        version = {'context': 'cpu_version', 'minimum': '2.20'}
        for status, order_status in [(typed, ordering) for typed in ['declared_unverified', 'candidate_evidence']
                                     for ordering in ['source_verified', 'candidate_evidence']]:
            declaration, order = self.rows()
            declaration['status'] = status
            declaration['conditions'] = [native, version]
            declaration['selection_applicability'] = 'unverified'
            order['conditions'] = [native]
            order['selection_applicability'] = 'applicable'
            order['status'] = order_status
            order['verification'] = 'source_checked' if order_status == 'source_verified' else 'not_performed'
            order['value']['symbols'] = ['S', 'D']
            for source in [declaration, order]:
                source['scope']['languages'] = ['ladder']
            original = copy.deepcopy([declaration, order])
            with self.subTest(status=status, order_status=order_status):
                tables, used = _parameter_tables([declaration, order], copy.deepcopy)
                self.assertEqual(len(tables), 1)
                self.assertEqual(used['order'], used['declaration'])
                self.assertEqual(tables[0]['knowledge_status'], status)
                self.assertEqual(tables[0]['conditions'], [native, version])
                groups = _groups([declaration, order], copy.deepcopy, scope=declaration['scope'],
                                 parameter_bindings=used, tables=tables)
                verified = next(g for g in groups if 'parameters.order' in g['semantics'])
                self.assertEqual(verified['knowledge_status'], order_status)
                self.assertEqual(verified['conditions'], [native])
                self.assertEqual(verified['semantics']['parameters.order'], {'parameter_table': used['declaration']})
                self.assertEqual([declaration, order], original)
                unreviewed = copy.deepcopy(declaration)
                unreviewed['verification'] = 'not_performed'
                tables, _ = _parameter_tables([unreviewed, order], copy.deepcopy)
                self.assertEqual(len(tables), 2)
                first = copy.deepcopy(declaration)
                first['value']['instruction_variant'] = 'first'
                second = copy.deepcopy(declaration)
                second.update(id='second')
                second['value']['instruction_variant'] = 'second'
                tables, _ = _parameter_tables([first, second, order], copy.deepcopy)
                self.assertEqual(len(tables), 3)

    def rows(self):
        scope={'models':['CPU_A'],'forms':['SYNTH'],'languages':['st']}
        declaration=fact('declaration','parameters.typed_operands',scope=copy.deepcopy(scope),
            value={'declaration_kind':'instruction','operands':[
                {'name':'s','type':'WORD','direction':'input','purpose':'source_word'},
                {'name':'d','type':'WORD','direction':'output','purpose':'result_word'}]})
        order=fact('order','parameters.order',scope=copy.deepcopy(scope),
                   value={'symbols':['s','d'],'types_and_directions':'separate_facts'})
        return declaration,order

    def test_same_order_without_a_kind_uses_its_complete_interface_table(self):
        from knowledge.instruction_presentation import _parameter_tables
        declaration,order=self.rows()
        tables,used=_parameter_tables([declaration,order],copy.deepcopy)
        self.assertEqual(len(tables),1)
        self.assertEqual(used['order'],used['declaration'])
        self.assertTrue(all(p.get('type') and p.get('purpose') for p in tables[0]['parameters']))

    def test_explicit_role_inside_composite_uses_folds_the_same_literal_purpose(self):
        from knowledge.instruction_presentation import _parameter_tables
        declaration, _ = self.rows()
        enriched = copy.deepcopy(declaration); enriched['id'] = 'enriched'
        for parameter in enriched['value']['operands']:
            parameter['purpose'] = {'uses': [{'semantic_role': parameter['purpose']}, {'used_in': 'synthetic_operation'}]}
        for facts in [[declaration, enriched], [enriched, declaration]]:
            tables, used = _parameter_tables(facts, copy.deepcopy)
            self.assertEqual(len(tables), 1)
            self.assertEqual(used['declaration'], used['enriched'])
            self.assertEqual(tables[0]['parameters'][0]['purpose'], enriched['value']['operands'][0]['purpose'])
        for difference in ['purpose', 'condition', 'status']:
            other = copy.deepcopy(declaration)
            if difference == 'purpose': other['value']['operands'][0]['purpose'] = 'different_role'
            elif difference == 'condition': other['conditions'] = [{'context': 'edition', 'equals': 'other'}]
            else: other['status'] = 'unknown'
            with self.subTest(difference=difference):
                tables, _ = _parameter_tables([enriched, other], copy.deepcopy)
                self.assertEqual(len(tables), 2)


    def test_order_keeps_real_kind_overload_condition_and_status_differences(self):
        from knowledge.instruction_presentation import _parameter_tables
        for difference in ['kind','overload','condition','status','variadic']:
            declaration,order=self.rows()
            if difference=='kind':order['value']['declaration_kind']='function_block'
            elif difference=='overload':order['value']['instruction_variant']='other_overload'
            elif difference=='condition':order['conditions']=[{'context':'edition','equals':'different'}]
            elif difference=='status':order['status']='unknown'
            else:order['value']['variadic_parameters']={'name':'s','minimum':1}
            with self.subTest(difference=difference):
                tables,_=_parameter_tables([declaration,order],copy.deepcopy)
                self.assertEqual(len(tables),2)


    def embedded_facets(self):
        declaration, order = self.rows()
        order['status'] = 'candidate_evidence'
        facets = [fact('purpose-' + name, 'parameters.%s.purpose' % position,
            scope=copy.deepcopy(order['scope']), status='candidate_evidence',
            value={'symbol': name, 'value': {'semantic_role': 'candidate_' + name}})
            for position, name in [(1, 's'), (2, 'd')]]
        return declaration, order, facets

    def test_embedded_weaker_facets_reuse_one_table_without_promoting_their_status(self):
        from knowledge.instruction_presentation import _parameter_tables, _groups
        declaration, order, facets = self.embedded_facets()
        facts = [declaration, order, *facets]
        tables, used = _parameter_tables(facts, copy.deepcopy)
        self.assertEqual(len(tables), 1)
        self.assertEqual(used['order'], used['declaration'])
        self.assertTrue(all(used[f['id']] == used['declaration'] for f in facets))
        self.assertEqual(tables[0]['knowledge_status'], 'source_verified')
        for parameter in tables[0]['parameters']:
            claim, = parameter['facet_alternatives']['purpose']
            self.assertEqual(claim['knowledge_status'], 'candidate_evidence')
            self.assertEqual(claim['value'], {'semantic_role': 'candidate_' + parameter['name']})
        groups = _groups(facts, copy.deepcopy, scope={'models': ['CPU_A']}, parameter_bindings=used, tables=tables)
        self.assertTrue(any(g['knowledge_status'] == 'candidate_evidence' and 'parameters.order' in g['semantics'] for g in groups))
        self.assertEqual(order['status'], 'candidate_evidence')

    def test_partial_or_conditioned_weaker_facets_keep_their_separate_table(self):
        from knowledge.instruction_presentation import _parameter_tables
        for difference in ['partial', 'condition', 'metadata']:
            declaration, order, facets = self.embedded_facets()
            if difference == 'partial':
                facets = facets[:1]
            elif difference == 'condition':
                order['conditions'] = [{'context': 'mode', 'equals': 'other'}]
            else:
                order['value']['source_contract'] = 'separate_qualified_claim'
            with self.subTest(difference=difference):
                tables, _ = _parameter_tables([declaration, order, *facets], copy.deepcopy)
                self.assertEqual(len(tables), 2)

    def test_embedded_facets_reuse_a_conditional_table_without_narrowing_the_original_claims(self):
        from knowledge.instruction_presentation import _parameter_tables, _groups
        declaration, order, facets = self.embedded_facets()
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        version = {'context': 'cpu_version', 'minimum': '2.20'}
        declaration['conditions'] = [native, version]
        declaration['selection_applicability'] = 'unverified'
        declaration['value']['native_form'] = {'operand_order': ['s', 'd'],
                                              'minimum_operands': 2, 'maximum_operands': 2}
        for source in [declaration, order, *facets]:
            source['scope']['languages'] = ['ladder']
        for source in [order, *facets]:
            source['conditions'] = [native]
            source['selection_applicability'] = 'applicable'
        original = copy.deepcopy([declaration, order, *facets])
        tables, used = _parameter_tables([declaration, order, *facets], copy.deepcopy)
        self.assertEqual(len(tables), 1)
        self.assertEqual(used['order'], used['declaration'])
        self.assertEqual(tables[0]['conditions'], [native, version])
        self.assertEqual(tables[0]['selection_applicability'], 'unverified')
        for parameter in tables[0]['parameters']:
            claim, = parameter['facet_alternatives']['purpose']
            self.assertEqual(claim['conditions'], [native])
            self.assertEqual(claim['knowledge_status'], 'candidate_evidence')
        groups = _groups([declaration, order, *facets], copy.deepcopy,
                         scope=declaration['scope'], parameter_bindings=used, tables=tables)
        weak = [g for g in groups if g['knowledge_status'] == 'candidate_evidence']
        self.assertTrue(weak)
        self.assertTrue(all(g['conditions'] == [native] for g in weak))
        self.assertTrue(all(g.get('selection_applicability', 'applicable') == 'applicable' for g in weak))
        self.assertEqual([declaration, order, *facets], original)
        excluded = copy.deepcopy(declaration)
        excluded['selection_applicability'] = 'not_applicable'
        _, separate = _parameter_tables([excluded, order, *facets], copy.deepcopy)
        self.assertNotEqual(separate['order'], separate['declaration'])

    def test_a_separate_conditioned_order_does_not_hide_existing_facet_coverage(self):
        from knowledge.instruction_presentation import _parameter_tables
        declaration, order, facets = self.embedded_facets()
        conditioned = copy.deepcopy(order)
        conditioned.update(id='conditioned-order', status='declared_unverified',
                           conditions=[{'context': 'cpu_version_not_resolved'}])
        for source in facets:
            source['value']['value'] = {'semantic_role': 'source_word' if source['value']['symbol'] == 's' else 'result_word',
                                        'location': 'word_head'}
        tables, used = _parameter_tables([declaration, conditioned, order, *facets], copy.deepcopy)
        self.assertEqual(len(tables), 2)
        self.assertEqual(used['order'], used['declaration'])
        self.assertNotEqual(used['conditioned-order'], used['declaration'])
        complete = next(t for t in tables if t['id'] == used['declaration'])
        self.assertTrue(all(p['qualifications'][0]['knowledge_status'] == 'candidate_evidence' for p in complete['parameters']))
        self.assertNotIn('conditions', complete)

    def test_operand_order_case_reuses_one_table_without_changing_symbols_or_conditions(self):
        from knowledge.instruction_presentation import _parameter_tables
        for weaker in [False, True]:
            declaration, order, facets = self.embedded_facets()
            native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
            version = {'context': 'cpu_version', 'minimum': '2.20'}
            for source in [declaration, order, *facets]:
                source['scope']['languages'] = ['ladder']
                source['conditions'] = [native]
            if weaker:
                declaration['conditions'].append(version)
                declaration['selection_applicability'] = 'unverified'
                order['selection_applicability'] = 'applicable'
                for source in facets:
                    source['selection_applicability'] = 'applicable'
            else:
                order['status'] = declaration['status']
                facets = []
            declaration['value']['operands'].append(
                {'name': 'n', 'type': 'WORD', 'direction': 'input', 'purpose': 'element_count'})
            order['value']['symbols'] = ['S', 'D', 'N']
            if weaker:
                facets.append(fact('purpose-n', 'parameters.3.purpose',
                    scope=copy.deepcopy(order['scope']), status=order['status'],
                    conditions=copy.deepcopy(order['conditions']),
                    selection_applicability='applicable',
                    value={'symbol': 'N', 'value': {'semantic_role': 'candidate_count'}}))
            originals = copy.deepcopy([declaration, order, *facets])
            with self.subTest(weaker=weaker):
                tables, used = _parameter_tables([declaration, order, *facets], copy.deepcopy)
                self.assertEqual(len(tables), 1)
                self.assertEqual(used['order'], used['declaration'])
                self.assertEqual([p['name'] for p in tables[0]['parameters']], ['s', 'd', 'n'])
                self.assertEqual(tables[0]['conditions'], declaration['conditions'])
                self.assertEqual([declaration, order, *facets], originals)
                if weaker:
                    count = tables[0]['parameters'][2]['facet_alternatives']['purpose'][0]
                    self.assertEqual(count['knowledge_status'], 'candidate_evidence')
                    self.assertEqual(count['conditions'], [native])
            for names in [['D', 'S', 'N'], ['S', 'D', 'COUNT']]:
                changed = copy.deepcopy(order)
                changed['value']['symbols'] = names
                with self.subTest(weaker=weaker, names=names):
                    tables, _ = _parameter_tables([declaration, changed, *facets], copy.deepcopy)
                    self.assertEqual(len(tables), 2)

    def test_verified_order_reuses_a_guarded_table_when_candidate_facets_are_already_preserved(self):
        from knowledge.instruction_presentation import _parameter_tables, _groups
        declaration, order, facets = self.embedded_facets()
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        version = {'context': 'cpu_version', 'minimum': '2.20'}
        declaration['conditions'] = [native, version]
        declaration['selection_applicability'] = 'unverified'
        order['status'] = 'source_verified'
        for source in [declaration, order, *facets]:
            source['scope']['languages'] = ['ladder']
        for source in [order, *facets]:
            source['conditions'] = [native]
            source['selection_applicability'] = 'applicable'
        order['value']['symbols'] = ['S', 'D']
        originals = copy.deepcopy([declaration, order, *facets])
        tables, used = _parameter_tables([declaration, order, *facets], copy.deepcopy)
        self.assertEqual(len(tables), 1)
        self.assertEqual(used['order'], used['declaration'])
        self.assertEqual(tables[0]['conditions'], [native, version])
        self.assertEqual(tables[0]['selection_applicability'], 'unverified')
        groups = _groups([declaration, order, *facets], copy.deepcopy,
            scope=declaration['scope'], parameter_bindings=used, tables=tables)
        order_group = next(group for group in groups if 'parameters.order' in group['semantics'])
        self.assertEqual(order_group['knowledge_status'], 'source_verified')
        self.assertEqual(order_group['conditions'], [native])
        self.assertTrue(all(parameter['facet_alternatives']['purpose'][0]['knowledge_status']
                            == 'candidate_evidence' for parameter in tables[0]['parameters']))
        self.assertEqual([declaration, order, *facets], originals)

    def test_embedded_facets_do_not_choose_between_two_known_overloads(self):
        from knowledge.instruction_presentation import _parameter_tables
        declaration, order, facets = self.embedded_facets()
        other = copy.deepcopy(declaration); other['id'] = 'other'
        declaration['value']['instruction_variant'] = 'first'
        other['value']['instruction_variant'] = 'second'
        tables, _ = _parameter_tables([declaration, other, order, *facets], copy.deepcopy)
        self.assertEqual(len(tables), 3)


class LegacyInstructionLayoutTests(unittest.TestCase):
    @staticmethod
    def page():
        words, rectangles = [], []
        layout = {'kind': 'legacy_shading', 'cells': [
            {'label': 'AA', 'models': ['CPU_A']}, {'label': 'BB', 'models': ['CPU_B', 'CPU_B_ALIAS']}]}
        for number, name, top, active in [('5.1.1', 'SYNTHA', 100, 0), ('5.1.2', 'SYNTHB', 400, 1)]:
            for text, x in [(number, 50), (name, 100), ('(FNC', 170), ('10)', 200)]:
                words.append({'text': text, 'x0': x, 'x1': x + 20, 'top': top, 'bottom': top + 8})
            for i, label in enumerate(['AA', 'BB']):
                x = 360 + i * 32
                words.append({'text': label, 'x0': x + 5, 'x1': x + 20, 'top': top + 18, 'bottom': top + 26})
                rectangles.append({'x0': x, 'x1': x + 31, 'top': top + 15, 'bottom': top + 30,
                    'fill': i == active, 'non_stroking_color': [.9, .9, .9]})
        return words, rectangles, layout

    def test_each_heading_uses_its_own_cpu_strip(self):
        from knowledge.manual_applicability import legacy_instruction_sections
        words, rectangles, layout = self.page()
        sections = legacy_instruction_sections(words, rectangles, [], layout=layout)
        self.assertEqual([s['opcode'] for s in sections], ['SYNTHA', 'SYNTHB'])
        self.assertEqual([[m['model'] for m in s['model_marks'] if m['state'] == 'supported']
                          for s in sections], [['CPU_A'], ['CPU_B', 'CPU_B_ALIAS']])
        self.assertEqual(sections[0]['bottom'], sections[1]['top'])
        self.assertEqual(legacy_instruction_sections(words, rectangles, [], layout={'kind': 'other'}), [])

    def test_partial_polygon_badge_preserves_a_conditional_cpu_instead_of_exclusion(self):
        from knowledge.manual_applicability import legacy_instruction_sections
        from tools.fact_applied_review import _legacy_form_states
        words, rectangles, layout = self.page()
        triangle = {'x0': 392, 'x1': 423, 'top': 115, 'bottom': 130, 'fill': True,
            'non_stroking_color': [.9, .9, .9], 'pts': [(392, 130), (423, 130), (423, 115), (392, 130)]}
        result = legacy_instruction_sections(words, rectangles, [], layout=layout, polygons=[triangle])
        marks = {m['model']: m['state'] for m in result[0]['model_marks']}
        self.assertEqual(marks, {'CPU_A': 'supported', 'CPU_B': 'conditional', 'CPU_B_ALIAS': 'conditional'})
        record = {'manual_models': ['CPU_B'], 'applicability': {'model_marks': result[0]['model_marks']},
            'source_form_models': {'16': {'CPU_B': True}}}
        self.assertEqual(_legacy_form_states(record, {'width_bits': 16, 'execution_form': 'continuous'}),
                         {'CPU_B': 'unknown'})

    def test_numbered_chapter_heading_stops_an_instruction_without_another_fnc_heading(self):
        from knowledge.manual_applicability import legacy_instruction_sections, _legacy_heading_tops
        words, rectangles, layout = self.page()
        chapter = {'text': '5.2', 'x0': 50, 'x1': 70, 'top': 300, 'bottom': 312}
        sections = legacy_instruction_sections([*words, chapter], rectangles, [], layout=layout)
        self.assertEqual(sections[0]['bottom'], 300)
        self.assertEqual([s['opcode'] for s in sections], ['SYNTHA', 'SYNTHB'])
        self.assertEqual(_legacy_heading_tops([chapter]), [300])

    def test_form_cpu_cells_require_original_borders_labels_and_shading(self):
        from knowledge.manual_applicability import legacy_instruction_sections
        words, rectangles, layout = self.page()
        tables = [{'bbox': [100, 200, 340, 220], 'cells': [
            {'bbox': [100, 200, 220, 210], 'text': '16 BIT OPERATION'},
            {'bbox': [225, 200, 340, 210], 'text': 'PULSE-P'}]}]
        characters = []
        for x, label, shaded in [(100, 'AA', True), (160, 'BB', True), (220, 'AA', False), (280, 'BB', True)]:
            rectangles.append({'x0': x, 'x1': x + 60, 'top': 210, 'bottom': 220,
                               'fill': shaded, 'non_stroking_color': [0, 0, 0, .1]})
            characters.append({'text': label, 'x0': x + 15, 'x1': x + 30, 'top': 212, 'bottom': 218})
        result = legacy_instruction_sections(words, rectangles, tables, layout=layout, characters=characters)
        self.assertEqual(result[0]['form_models']['16'], {'CPU_A': True, 'CPU_B': True, 'CPU_B_ALIAS': True})
        self.assertEqual(result[0]['form_models']['pulse'], {'CPU_A': False, 'CPU_B': True, 'CPU_B_ALIAS': True})
        self.assertFalse(result[1]['form_models'])
        unresolved = legacy_instruction_sections(words, rectangles, tables, layout=layout, characters=characters[:-1])
        self.assertIsNone(unresolved[0]['form_models']['pulse'])
        unreadable = {'bbox': [100, 160, 220, 180], 'cells': [
            {'bbox': [100, 160, 220, 170], 'text': '16 BIT OPERATION'}]}
        mixed = legacy_instruction_sections(words, rectangles, [unreadable, *tables], layout=layout, characters=characters)
        self.assertIsNone(mixed[0]['form_models']['16'])

    def test_single_row_form_shading_survives_snapping_without_borrowing_the_cpu_header(self):
        from knowledge.manual_applicability import legacy_instruction_sections
        for offset in [0, 2.6]:
            with self.subTest(snapped_left_offset=offset):
                words, rectangles, layout = self.page()
                for rectangle in rectangles:
                    if rectangle['top'] == 115:
                        rectangle['fill'] = True
                headers = []
                for x, label, shaded in [(100, '16 BIT OPERATION', True),
                                          (220, '32 BIT OPERATION', False), (340, 'PULSE-P', True)]:
                    headers.append({'bbox': [x + offset, 200, x + 120, 225], 'text': label})
                    rectangles.append({'x0': x, 'x1': x + 120, 'top': 200, 'bottom': 225,
                                       'fill': shaded, 'non_stroking_color': [0, 0, 0, .1]})
                # The large page strip cannot replace a missing original cell.
                rectangles.append({'x0': 90, 'x1': 470, 'top': 199.5, 'bottom': 225.5,
                                   'fill': True, 'non_stroking_color': [.9, .9, .9]})
                tables = [{'bbox': [100 + offset, 200, 460, 225], 'cells': headers}]
                sections = legacy_instruction_sections(words, rectangles, tables, layout=layout)
                supported = {'CPU_A': True, 'CPU_B': True, 'CPU_B_ALIAS': True}
                self.assertEqual(sections[0]['form_models'],
                                 {'16': supported, '32': dict.fromkeys(supported, False), 'pulse': supported})
                self.assertEqual(sections[1]['form_models'], {})
                missing_frame = [rectangle for rectangle in rectangles
                                 if not (rectangle['x0'] == 220 and rectangle['top'] == 200)]
                unresolved = legacy_instruction_sections(words, missing_frame, tables, layout=layout)
                self.assertIsNone(unresolved[0]['form_models']['32'])
                self.assertEqual(unresolved[0]['form_models']['16'], supported)
                self.assertEqual(unresolved[0]['form_models']['pulse'], supported)


    def test_original_form_header_recovers_an_outer_column_missing_from_snapped_table(self):
        from knowledge.manual_applicability import legacy_instruction_sections
        words, rectangles, layout = self.page()
        rectangles.append({'x0': 100, 'x1': 220, 'top': 200, 'bottom': 210})
        chars = [{'text': 'PULSE-P', 'x0': 125, 'x1': 190, 'top': 202, 'bottom': 208}]
        for x, label, shaded in [(100, 'AA', False), (160, 'BB', True)]:
            rectangles.append({'x0': x, 'x1': x + 60, 'top': 210, 'bottom': 220,
                               'fill': shaded, 'non_stroking_color': [0, 0, 0, .1]})
            chars.append({'text': label, 'x0': x + 15, 'x1': x + 30, 'top': 212, 'bottom': 218})
        sections = legacy_instruction_sections(words, rectangles, [], layout=layout, characters=chars)
        self.assertEqual(sections[0]['form_models']['pulse'],
                         {'CPU_A': False, 'CPU_B': True, 'CPU_B_ALIAS': True})
        self.assertEqual(sections[1]['form_models'], {})


class LegacySourceCompilationTests(unittest.TestCase):
    def test_family_selection_filters_index_and_unindexed_records_before_geometry(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from tools import fact_applied_review as review
        records = [{'manual_id': 'jy992d48301j', 'opcode': opcode, 'section': opcode, 'pdf_pages': [number]}
                   for number, opcode in enumerate(['FLT', 'SQR'], 1)]
        records.append(dict(records[1], opcode='XCH', text='5.5.10 FLT (FNC 49)\nOwn body'))
        source = SimpleNamespace(execute=lambda sql, *args:
            [{'manual_id': 'jy992d48301j', 'plc_models': 'FX2C'}] if 'FROM manuals' in sql else [{'opcode': 'FLT'}])
        with patch.object(review, '_instruction_records', return_value=records), \
                patch.object(review, '_chunk_records', return_value=[dict(records[1], opcode='FMOV')]), \
                patch.object(review, '_native_parameter_conventions', return_value={}), \
                patch('tools.fact_behavior_review._manual_cpu_models', return_value={'jy992d48301j': {'FX2C'}}), \
                patch.object(review, '_legacy_source_records', return_value=[]) as recover:
            facts, receipt = review.reviewed_applied_behavior(source, manuals=['jy992d48301j'],
                                                              cpu_catalogue={}, families=['DFLTP'])
        self.assertEqual([record['opcode'] for record in recover.call_args.args[1]], ['FLT', 'XCH'])
        self.assertFalse(facts)
        self.assertEqual(receipt['sections'], 0)

    def test_explicit_owned_double_word_operation_can_share_the_instruction_cpu_scope(self):
        from tools.fact_applied_review import _legacy_float_parameter_profiles, _legacy_variants
        operands = [{'name': 'S', 'type': 'ANY32', 'direction': 'input'},
                    {'name': 'D', 'type': 'ANY32', 'direction': 'output'}]
        record = self.record(); record.update(opcode='SQR', text=(
            'SQR, SQRP: 5 steps DSQR, DSQRP: 9 steps. '
            'Operation 2: M8023 allows floating point answers. '
            'Operation 2 is only valid for double word (32 bit) operation.'),
            parameter_conventions={'pdf_pages': [1], 'd_prefix_is_32_bit': True})
        self.assertEqual({v['opcode'] for v in _legacy_variants(record)}, {'SQR', 'SQRP', 'DSQR', 'DSQRP'})
        profiles = _legacy_float_parameter_profiles(record, {'opcode': 'DSQRP', 'width_bits': 32},
                                                    operands, ['FX2C', 'FX2N'])
        self.assertEqual(len(profiles), 2)
        self.assertTrue(all(p['scope']['models'] == ['FX2C', 'FX2N'] for p in profiles))
        self.assertEqual([p['type'] for p in profiles[-1]['value']['operands']], ['REAL', 'REAL'])
        ordinary, = _legacy_float_parameter_profiles(record, {'opcode': 'SQR', 'width_bits': 16},
                                                     operands, ['FX2C'])
        self.assertNotIn('device_state', str(ordinary['conditions']))

    @staticmethod
    def record():
        return {'id': 'synthetic', 'opcode': 'CMP', 'title': 'Synthetic comparison',
            'manual_id': 'jy992d48301j', 'manual_number': 'SYNTH', 'revision': 'A',
            'pdf_pages': [1], 'section': '5.1.1 CMP (FNC 10)', 'chapter': 'Applied', 'outline_path': '',
            'text': '5.1.1 CMP (FNC 10) 16 bit. CMP,CMPP: 7 steps',
            'applicability': {'basis': 'instruction_header', 'model_marks': [
                {'model': 'FX0', 'state': 'supported'}, {'model': 'FX2C', 'state': 'supported'}]},
            'manual_models': ['FX0', 'FX2C'], 'manual_scope_basis': 'manual_catalogue', 'declared_operands': [],
            'source_form_models': {'16': {'FX0': True, 'FX2C': True}, 'pulse': {'FX0': False, 'FX2C': True}},
            'source_geometry_text': True,
            'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]}

    @classmethod
    def literal_record(cls):
        record = cls.record()
        record.update(opcode='SYNTH', text='5.1.1 SYNTH (FNC 10) 16 bit. Unrelated OTHERP: 3 steps',
                      parameter_conventions={'source_destination_symbols': True, 'd_prefix_is_32_bit': True,
                                             'pdf_pages': [2]})
        record['table_groups'] = [[['Mnemonic', 'Function', 'Operands', '', '', 'Program steps'],
                                  ['', '', 'S', 'D', 'n', ''],
                                  ['SYNTH FNC 10', 'Synthetic transformation', 'D', 'D', 'K, H',
                                   'SYNTH,SYNTHP: 7 steps']]]
        return record

    def test_legacy_literal_pulse_pair_requires_own_steps_and_pulse_column(self):
        from tools.fact_applied_review import _legacy_variants
        record = self.literal_record()
        before = copy.deepcopy(record)
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form', return_value=None):
            rows = _legacy_variants(record)
            self.assertEqual([r['opcode'] for r in rows], ['SYNTH', 'SYNTHP'])
            self.assertEqual([r['width_bits'] for r in rows], [16, 16])
            self.assertEqual(rows[1]['execution_form'], 'pulse')
            without_pulse = copy.deepcopy(record)
            without_pulse['source_form_models'].pop('pulse')
            self.assertEqual([r['opcode'] for r in _legacy_variants(without_pulse)], ['SYNTH'])
            without_declaration = copy.deepcopy(record)
            without_declaration['table_groups'][0][-1][-1] = 'SYNTH: 7 steps'
            self.assertEqual([r['opcode'] for r in _legacy_variants(without_declaration)], ['SYNTH'])
        self.assertEqual(record, before)

    def test_literal_pulse_does_not_give_the_legacy_count_a_fabricated_width(self):
        from tools.fact_applied_review import _legacy_variants, _native_parameter_facts
        record = self.literal_record()
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form', return_value=None):
            variants = _legacy_variants(record)
            for variant in variants:
                with self.subTest(form=variant['opcode']):
                    rows = _native_parameter_facts(record, variant, ['FX2C'])
                    typed = next(row for row in rows if row['dimension'] == 'parameters.typed_operands')
                    count = next(p for p in typed['value']['operands'] if p['name'] == 'n')
                    self.assertEqual(count['type'], {'kind': 'integer'})
                    self.assertEqual(count['type_completeness'], 'partial')
                    self.assertNotIn('bits', count['type'])
                    self.assertEqual(typed['status'], 'declared_unverified')
                    self.assertEqual(typed['value']['operands'][0]['type'], 'ANY16')

    def test_structured_own_pulse_exclusions_keep_base_and_conditional_cpu_claims(self):
        import sqlite3
        from tools import fact_applied_review as review
        models = ['FX0', 'FX0S', 'FX0N', 'FX2C', 'FX3U']
        for names, excluded in [('FX 0, FX0S or FX0N', {'FX0', 'FX0S', 'FX0N'}), ('FX 0N', {'FX0N'})]:
            with self.subTest(pulse_excluded=names):
                source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
                source.row_factory = sqlite3.Row
                source.execute('CREATE TABLE instructions (opcode)')
                source.execute("INSERT INTO instructions VALUES ('SYNTH')")
                source.execute('CREATE TABLE manuals (manual_id,plc_models)')
                source.execute('INSERT INTO manuals VALUES (?,?)', ('fxcpu_basic_applied_m', ','.join(models)))
                source.execute('CREATE TABLE chunks (manual_id,document_plc_models)')
                record = self.record()
                record.update(manual_id='fxcpu_basic_applied_m', opcode='SYNTH', section='11.2 SYNTH / Synthetic',
                              manual_models=models, conditions=[], text=(
                    '1. 16-bit operation (SYNTH, SYNTHP)\n'
                    'SYNTH 16 bits Continuous SYNTH(EN,s,n,d);\n'
                    'SYNTHP 16 bits Pulse SYNTHP(EN,s,n,d);\n'
                    'Instructions of pulse operation type are not provided in the ' + names + ' PLC.'))
                record['applicability']['model_marks'] = [
                    {'model': model, 'state': 'conditional' if model == 'FX2C' else 'supported'} for model in models]
                with ExitStack() as stack:
                    stack.enter_context(patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form', return_value=None))
                    stack.enter_context(patch.object(review, '_instruction_records', return_value=[record]))
                    stack.enter_context(patch.object(review, '_chunk_records', return_value=[]))
                    stack.enter_context(patch.object(review, '_source_table_groups', return_value=[]))
                    stack.enter_context(patch.object(review, '_parameter_source_pages', return_value=[]))
                    stack.enter_context(patch.object(review, '_native_parameter_conventions', return_value={}))
                    stack.enter_context(patch.object(review, '_page_prose', return_value=''))
                    facts, _ = review.reviewed_applied_behavior(source, manuals=['fxcpu_basic_applied_m'],
                                                               page_text={}, page_tables={}, cpu_catalogue={})
                for model in models:
                    base = next(f for f in facts[model, 'SYNTH'] if f['dimension'] == 'execution.form')
                    self.assertEqual(base['value']['form'], 'continuous')
                    pulse = facts[model, 'SYNTHP']
                    if model in excluded:
                        self.assertEqual([f['dimension'] for f in pulse], ['constraints.cpu_availability'])
                        self.assertIs(pulse[0]['value']['available'], False)
                    else:
                        execution = next(f for f in pulse if f['dimension'] == 'execution.form')
                        self.assertEqual(execution['value'], {'form': 'pulse', 'width_bits': 16})
                        expected = 'declared_unverified' if model == 'FX2C' else 'source_verified'
                        self.assertEqual(execution['status'], expected)
                        if model == 'FX2C':
                            self.assertTrue(all(f['status'] == 'declared_unverified' for f in pulse))
                            self.assertIn(review.HEADER_CONDITION, execution['conditions'])

    def test_recovered_missing_heading_and_continuation_keep_separate_ownership(self):
        import sqlite3
        from types import SimpleNamespace
        from tools.fact_applied_review import _legacy_source_records
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0,FX2C')")
        def heading(number, opcode, text):
            return {'section': number + ' ' + opcode + ' (FNC 10)', 'opcode': opcode, 'text': text,
                    'model_marks': [{'model': 'FX2C', 'state': 'supported'}],
                    'form_models': {'16': {'FX2C': True}}}
        pages = {1: {'legacy_sections': [heading('5.1.1', 'SYNTHA', 'body A'),
                                            heading('5.1.2', 'SYNTHB', 'body B')], 'legacy_prefix_text': ''},
                 2: {'legacy_sections': [heading('5.1.3', 'SYNTHC', 'body C')], 'legacy_prefix_text': 'continuation B'}}
        original = self.record(); original.update(opcode='SYNTHB', section='5.1.2 SYNTHB (FNC 10)', pdf_pages=[1, 2])
        before = copy.deepcopy(original)
        reader = SimpleNamespace(execute=source.execute, legacy_page=lambda m, n: pages[n])
        result = {r['opcode']: r for r in _legacy_source_records(reader, [original])}
        self.assertEqual(set(result), {'SYNTHA', 'SYNTHB', 'SYNTHC'})
        self.assertEqual(result['SYNTHA']['text'], 'body A')
        self.assertEqual(result['SYNTHB']['text'], 'body B\ncontinuation B')
        self.assertEqual(result['SYNTHB']['pdf_pages'], [1, 2])
        self.assertEqual(result['SYNTHC']['text'], 'body C')
        self.assertEqual(result['SYNTHC']['pdf_pages'], [2])
        self.assertEqual(original, before)

    def test_continuation_not_listed_in_the_old_index_is_owned_by_physical_order(self):
        import sqlite3
        from types import SimpleNamespace
        from tools.fact_applied_review import _legacy_source_records
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0,FX2C')")
        def heading(key, name):
            return {'section': key + ' ' + name, 'opcode': name, 'text': 'own ' + name,
                    'model_marks': [], 'form_models': {}}
        pages = {1: {'legacy_sections': [heading('5.1.1', 'SYNTHA')], 'legacy_continues': True},
                 2: {'legacy_sections': [heading('5.1.2', 'SYNTHB')], 'legacy_prefix_text': 'continued A',
                     'legacy_continues': False},
                 3: {'legacy_sections': [], 'legacy_prefix_text': 'unrelated chapter', 'legacy_continues': False}}
        first = self.record(); first.update(opcode='SYNTHA', section='5.1.1 SYNTHA', pdf_pages=[1])
        other = self.record(); other.update(opcode='UNINDEXED', section='6.1 UNINDEXED', pdf_pages=[3])
        reader = SimpleNamespace(execute=source.execute, legacy_page=lambda m, n: pages[n])
        result = {r['opcode']: r for r in _legacy_source_records(reader, [first, other])}
        self.assertEqual(result['SYNTHA']['pdf_pages'], [1, 2])
        self.assertEqual(result['SYNTHA']['text'], 'own SYNTHA\ncontinued A')
        self.assertEqual(result['SYNTHB']['text'], 'own SYNTHB')
        self.assertEqual(result['SYNTHB']['pdf_pages'], [2])

    def test_float_mode_uses_own_caption_labels_and_only_affected_widths(self):
        from tools.fact_applied_review import _legacy_float_parameter_profiles
        operands = [{'name': 'S1', 'type': 'ANY32', 'direction': 'input'},
                    {'name': 'S2', 'type': 'ANY32', 'direction': 'input'},
                    {'name': 'D', 'type': 'ANY64', 'direction': 'output'}]
        for caption in ['Operation 2: (Applicable units: FX(2C))',
                        'Operation 2: (Applicable units FX (2C) )']:
            with self.subTest(caption=caption):
                record = self.record(); record.update(opcode='MUL', text=caption + ' M8023 floating point',
                    source_cpu_labels=[{'label': 'FX(2C)', 'models': ['FX2C']}], parameter_conventions={'pdf_pages': [1]})
                profiles = _legacy_float_parameter_profiles(record, {'opcode': 'DMUL', 'width_bits': 32},
                                                            operands, ['FX0', 'FX2C'])
                by_id = {p['id'].rsplit(':', 1)[-1].split('@', 1)[0]: p for p in profiles}
                self.assertEqual(set(by_id), {'default', 'm8023_off', 'm8023_on'})
                self.assertEqual(by_id['default']['scope']['models'], ['FX0'])
                self.assertNotIn('device_state', str(by_id['default']['conditions']))
                self.assertEqual(by_id['m8023_on']['scope']['models'], ['FX2C'])
                self.assertEqual([p['type'] for p in by_id['m8023_on']['value']['operands']], ['REAL'] * 3)
                self.assertEqual(by_id['m8023_off']['value']['operands'][-1]['type'], 'ANY64')
                narrow, = _legacy_float_parameter_profiles(record, {'opcode': 'MUL', 'width_bits': 16},
                                                           operands, ['FX0', 'FX2C'])
                self.assertNotIn('device_state', str(narrow['conditions']))
                self.assertEqual(narrow['value']['operands'][-1]['type'], 'ANY32')

    def test_documented_bcd_width_uses_supported_unsigned_encoded_integer_type(self):
        from tools.fact_applied_review import _documented_type
        self.assertEqual(_documented_type('4-digit BCD'),
                         {'kind': 'int', 'bits': 16, 'signed': False, 'encoding': 'bcd'})
        self.assertEqual(_documented_type('16-bit BCD', 16),
                         {'kind': 'int', 'bits': 16, 'signed': False, 'encoding': 'bcd'})
        self.assertEqual(_documented_type('32-bit BCD', 32),
                         {'kind': 'int', 'bits': 32, 'signed': False, 'encoding': 'bcd'})

    def test_inherited_float_footnote_or_unknown_unit_cannot_grant_a_real_interface(self):
        from tools.fact_applied_review import _legacy_float_parameter_profiles
        record = self.record(); record.update(opcode='BIN', text='M8023 floating point footnote',
            source_cpu_labels=[{'label': 'FX(2C)', 'models': ['FX2C']}], parameter_conventions={'pdf_pages': [1]})
        operands = [{'name': 'S', 'type': 'unknown', 'direction': 'input'},
                    {'name': 'D', 'type': 'unknown', 'direction': 'output'}]
        ordinary, = _legacy_float_parameter_profiles(record, {'opcode': 'DBIN', 'width_bits': 32}, operands, ['FX1N'])
        self.assertEqual([p['type'] for p in ordinary['value']['operands']],
                         [{'kind': 'int', 'bits': 32, 'signed': False, 'encoding': 'bcd'}, 'ANY32'])
        self.assertNotIn('device_state', str(ordinary['conditions']))
        record['text'] = 'Operation 2: (Applicable units: UNRESOLVED) M8023 floating point'
        conditional, = _legacy_float_parameter_profiles(record, {'opcode': 'DBIN', 'width_bits': 32}, operands, ['FX1N'])
        self.assertIn({'context': 'device_state', 'device': 'M8023', 'equals': False}, conditional['conditions'])
        self.assertNotIn('REAL', str(conditional['value']))

    def test_repeated_heading_with_conflicting_cells_never_keeps_verified_scope(self):
        import sqlite3
        from types import SimpleNamespace
        from tools.fact_applied_review import _legacy_source_records, _legacy_form_states
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0')")
        original = self.record(); original['pdf_pages'] = [1, 2]
        def page(state):
            return {'legacy_sections': [{'section': original['section'], 'opcode': 'CMP', 'text': 'body',
                'model_marks': [{'model': 'FX0', 'state': 'supported' if state else 'unsupported'}],
                'form_models': {'16': {'FX0': state}}}], 'legacy_prefix_text': ''}
        reader = SimpleNamespace(execute=source.execute, legacy_page=lambda m, n: page(n == 1))
        recovered, = _legacy_source_records(reader, [original])
        self.assertFalse(recovered['applicability']['model_marks'])
        self.assertIsNone(recovered['source_form_models']['16'])
        self.assertEqual(_legacy_form_states(recovered, {'width_bits': 16, 'execution_form': 'continuous'}), {'FX0': 'unknown'})

    def test_form_scope_uses_header_width_and_trigger_without_book_membership_promotion(self):
        from tools.fact_applied_review import _legacy_form_states
        record = self.record(); record['manual_models'].append('FXU')
        record['applicability']['model_marks'].append({'model': 'FXU', 'state': 'unsupported'})
        record['source_form_models']['16']['FXU'] = True
        self.assertEqual(_legacy_form_states(record, {'width_bits': 16, 'execution_form': 'continuous'}),
                         {'FX0': 'supported', 'FX2C': 'supported', 'FXU': 'conflict'})
        self.assertEqual(_legacy_form_states(record, {'width_bits': 16, 'execution_form': 'pulse'}),
                         {'FX0': 'unsupported', 'FX2C': 'supported', 'FXU': 'conflict'})
        record.pop('source_form_models')
        self.assertEqual(set(_legacy_form_states(record, {'width_bits': 16, 'execution_form': 'continuous'}).values()), {'unknown'})

    def review(self, record):
        import sqlite3
        from tools.fact_applied_review import reviewed_applied_behavior
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close); source.row_factory = sqlite3.Row
        source.execute('CREATE TABLE instructions (opcode)'); source.execute("INSERT INTO instructions VALUES ('CMP')")
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0,FX2C')")
        source.execute('CREATE TABLE chunks (manual_id,document_plc_models)')
        grid = [['Mnemonic', 'Function', 'Operands', '', '', 'Program steps'],
                ['', '', 'S1', 'S2', 'D', ''], ['CMP FNC 10', 'Synthetic comparison', 'D', 'D', 'M', 'CMP,CMPP: 7 steps']]
        with ExitStack() as stack:
            stack.enter_context(patch('tools.fact_applied_review._instruction_records', return_value=[record]))
            stack.enter_context(patch('tools.fact_applied_review._chunk_records', return_value=[]))
            stack.enter_context(patch('tools.fact_applied_review._source_table_groups', return_value=[grid]))
            stack.enter_context(patch('tools.fact_applied_review._native_parameter_conventions', return_value={
                'd_prefix_is_32_bit': True, 'source_destination_symbols': True, 'pdf_pages': [1]}))
            stack.enter_context(patch('tools.fact_applied_review._page_prose', side_effect=AssertionError('fresh ownership replaced by old page cache')))
            return reviewed_applied_behavior(source, manuals=['jy992d48301j'], page_text={}, page_tables={}, cpu_catalogue={})

    def test_normal_reviewer_emits_native_exclusion_and_only_eligible_source_facts(self):
        rows, receipt = self.review(self.record())
        rejected = rows['FX0', 'CMPP']
        self.assertEqual([f['dimension'] for f in rejected], ['constraints.cpu_availability'])
        self.assertIs(rejected[0]['value']['available'], False)
        for owner, expected in [(('FX0', 'CMP'), 'continuous'), (('FX2C', 'CMPP'), 'pulse')]:
            form = next(f for f in rows[owner] if f['dimension'] == 'execution.form')
            self.assertEqual(form['value'], {'form': expected, 'width_bits': 16})
            self.assertEqual(form['status'], 'source_verified')
            self.assertEqual(form['conditions'], self.record()['conditions'])
        self.assertEqual(receipt['knowledge_coverage']['FX0|CMPP']['covered_aspects'], [])
        self.assertEqual(receipt['completeness_scope']['full_instruction_semantics'], 'not_established')

    def test_legal_renamed_double_form_is_not_a_source_identity_conflict(self):
        from tools.fact_applied_review import _legacy_variants, _table_parameter_facts
        record = self.record(); record.update(opcode='WAND', text='16 bit')
        record['parameter_conventions'] = {'d_prefix_is_32_bit': True}
        record['table_groups'] = [[['Mnemonic', 'Function', 'Operands', '', '', 'Program steps'],
            ['', '', 'S1', 'S2', 'D', ''],
            ['WAND FNC 26', 'Synthetic logical operation', 'D', 'D', 'D',
             'WAND,WANDP: 7 steps DAND,DANDP: 13 steps']]]
        self.assertEqual({v['opcode'] for v in _legacy_variants(record)}, {'WAND', 'WANDP', 'DAND', 'DANDP'})
        self.assertFalse(any(dimension == 'constraints.source_identity'
                             for dimension, _, _ in _table_parameter_facts(record)))

    def test_reviewer_keeps_index_only_scope_unverified(self):
        record = self.record(); record.pop('source_form_models')
        rows, receipt = self.review(record)
        self.assertEqual(receipt['knowledge_summary']['owners_knowledge_complete'], 0)
        self.assertFalse(any(f['status'] == 'source_verified' for facts in rows.values() for f in facts))
        self.assertTrue(any(f['dimension'] == 'parameters.typed_operands' for facts in rows.values() for f in facts))
        self.assertTrue(all(f['value']['available'] == 'unknown' for facts in rows.values() for f in facts
                            if f['dimension'] == 'constraints.cpu_availability'))


class LegacyWordLogicKnowledgeTests(unittest.TestCase):
    forms = {'WAND': ['WAND', 'WANDP', 'DAND', 'DANDP'],
             'WOR': ['WOR', 'WORP', 'DOR', 'DORP'],
             'WXOR': ['WXOR', 'WXORP', 'DXOR', 'DXORP']}

    def test_shared_word_logic_truth_tables_and_effective_parameter_storage(self):
        from hypothesis import given, settings, strategies as st
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression
        truth = {'bit_and': {('0', '0'): '0', ('0', '1'): '0', ('1', '0'): '0', ('1', '1'): '1'},
                 'bit_or': {('0', '0'): '0', ('0', '1'): '1', ('1', '0'): '1', ('1', '1'): '1'},
                 'bit_xor': {('0', '0'): '0', ('0', '1'): '1', ('1', '0'): '1', ('1', '1'): '0'}}
        expressions = {}
        for cpu in ['FX1N', 'FX3U']:
            for base, forms in self.forms.items():
                for opcode in forms:
                    bits = 32 if registry.resolve_catalog_form(opcode).double else 16
                    groups = family_fact_groups(registry, opcode, cpu=cpu)
                    body, = [f['value'] for f in groups if f['dimension'] == 'effects.documented_processing'
                        and {'context': 'project_mode', 'equals': 'native_ladder_instruction'} in f.get('conditions', [])]
                    self.assertNotIn('sources_unchanged', body['rules'][0])
                    table, = [f['value']['operands'] for f in groups if f['dimension'] == 'parameters.typed_operands'
                        and f['value'].get('representation') == 'native_ladder_instruction']
                    self.assertEqual([p['name'] for p in table], ['S1', 'S2', 'D'])
                    self.assertTrue(all(p['purpose'] and p['word_device_layout']['word_count'] == bits // 16 for p in table))
                    expressions[cpu, opcode] = bits, body['rules'][0]['outputs'][0]['expression']
        @settings(max_examples=60, deadline=None)
        @given(case=st.sampled_from(list(expressions)), left=st.integers(0, 2**32 - 1), right=st.integers(0, 2**32 - 1))
        def check(case, left, right):
            bits, expression = expressions[case]
            left %= 2**bits; right %= 2**bits
            expected = int(''.join(truth[expression['op']][a, b]
                for a, b in zip(format(left, '0%db' % bits), format(right, '0%db' % bits))), 2)
            self.assertEqual(evaluate_expression(expression, {'S1': left, 'S2': right}), expected)
        check()

    def test_legacy_pulse_exclusions_keep_legal_registered_names_without_positive_native_behavior(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        for cpu in ['FX0', 'FX0N', 'FX0S']:
            for base, forms in self.forms.items():
                for opcode in [forms[1], forms[3]]:
                    with self.subTest(cpu=cpu, opcode=opcode):
                        groups = family_fact_groups(registry, opcode, cpu=cpu)
                        self.assertTrue(any(f['dimension'] == 'constraints.cpu_availability' and f['value']['available'] is False for f in groups))
                        self.assertFalse(any(f['dimension'] == 'effects.documented_processing' for f in groups))
        self.assertFalse(family_fact_groups(registry, 'DWAND', cpu='FX2N'))
        self.assertFalse(family_fact_groups(registry, 'DANDPP', cpu='FX2N'))


class QlBcdResultRelationTests(unittest.TestCase):
    def test_native_double_bcd_results_keep_source_width_and_four_word_layout(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['DB*','DB*P','DB/','DB/P']:
            with self.subTest(opcode=opcode):
                raw = registry._definition_owner_store.get_entry(opcode,cpu='Q03UDV')
                typed, = [f for f in raw['facts'] if f['id'].startswith('authored:ql:native_arguments_')
                          and f['dimension']=='parameters.typed_operands']
                self.assertEqual([p['name'] for p in typed['value']['operands']], ['S1','S2','D'])
                self.assertEqual([p['type'] for p in typed['value']['operands']], ['ANY32','ANY32','ANY64'])
                self.assertEqual([p['decimal_digits'] for p in typed['value']['operands']], [8,8,16])
                self.assertEqual(typed['conditions'], [{'context':'project_mode','equals':'native_ladder_instruction'}])
                values = [f['value'] for f in self.facts(opcode) if f['dimension']=='effects.documented_processing'
                          and ':ql:BCD:result_relation:' in f['id']]
                native = next(v for v in values if v['output_layout']['when_destination']=='word_device')
                self.assertEqual(native['output_layout']['word_count'],4)
                source = dict(kind='instruction',target=opcode,scope={'models':['Q03UDV']},
                              content_origin='authored_fact',status='source_verified',value={**raw,'facts':self.facts(opcode)})
                projected = technical_fact_packet(project_packet(source,plc_model='Q03UDV',language='ladder',
                    project_mode='native_ladder_instruction',aspects=['parameters','effects']))
                encoded = json.dumps(projected)
                self.assertIn('ANY64',encoded)
                self.assertIn('result_relation',encoded)
                self.assertIs(raw['call_available'],True)
                self.assertEqual(native['executor_capability'],'not_established')

    def test_native_bcd_digit_annotation_establishes_width_without_a_prefix_guess(self):
        from tools.fact_ql_behavior_review import _native_data_type
        for digits, bits in [(4,16), (8,32), (16,64)]:
            for text in [f'Head number where the result is stored (BCD {digits} digits)',
                         f'BCD {digits}-digit operation result']:
                with self.subTest(text=text):
                    parsed = _native_data_type(text)
                    self.assertEqual(parsed, {'type':f'ANY{bits}', 'encoding':'BCD', 'decimal_digits':digits})
        self.assertEqual(_native_data_type('Value (BCD 4/8 digits)')['type_alternatives'], ['ANY16','ANY32'])
        for text in ['Head number of the result devices', 'BCD 16 bits', 'BCD 12 digits']:
            with self.subTest(text=text):
                self.assertNotEqual(_native_data_type(text).get('type'), 'ANY64')

    @staticmethod
    def facts(opcode, model='Q03UDV'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        entry = registry._definition_owner_store.get_entry(opcode, cpu=model)
        return compose_programming_facts(registry, opcode, cpu=model, existing=entry['facts'])[0]

    @staticmethod
    def value(expression, arguments):
        if isinstance(expression, int):
            return expression
        if isinstance(expression, str):
            return arguments[expression]
        operator = expression['operator']
        if operator == 'read_component':
            return arguments[expression['operand']][expression['index']]
        if operator == 'decode_packed_BCD':
            raw = arguments[expression['operand']]
            digits = [(raw >> (4 * i)) & 15 for i in range(expression['decimal_digits'])]
            if any(digit > 9 for digit in digits):
                raise ValueError('invalid BCD digit')
            return sum(digit * 10 ** i for i, digit in enumerate(digits))
        if operator == 'encode_packed_BCD':
            decimal = QlBcdResultRelationTests.value(expression['operand'], arguments)
            if not 0 <= decimal < 10 ** expression['decimal_digits']:
                raise ValueError('result exceeds its documented BCD digits')
            return int(str(decimal), 16)
        values = [QlBcdResultRelationTests.value(item, arguments) for item in expression['operands']]
        if operator == 'add':
            return sum(values)
        a, b = values
        return {'multiply': lambda: a * b, 'subtract': lambda: a - b, 'integer_quotient': lambda: a // b,
                'integer_remainder': lambda: a % b}[operator]()

    def test_documented_bcd_examples_keep_decimal_math_and_full_product_width(self):
        for opcode, expected, parameters in [
                ('B*', {'product': 0x04973928}, {'S1': 0x5678, 'S2': 0x0876}),
                ('B/', {'quotient': 0x0006, 'remainder': 0x0422}, {'S1': 0x5678, 'S2': 0x0876}),
                ('DB*', {'product': 0x9999999800000001}, {'S1': 0x99999999, 'S2': 0x99999999}),
                ('DB/', {'quotient': 0x99999999, 'remainder': 0}, {'S1': 0x99999999, 'S2': 1})]:
            for pulse in ['', 'P']:
                with self.subTest(opcode=opcode + pulse):
                    body = next(f['value'] for f in self.facts(opcode + pulse)
                                if ':ql:BCD:result_relation:' in f['id']
                                and f['dimension'] == 'effects.documented_processing'
                                and f['scope']['languages'] == ['ladder']
                                and any(c.get('equals') == 'native_ladder_instruction' for c in f.get('conditions', [])))
                    rule, = body['rules']
                    self.assertEqual({name: self.value(expr, parameters)
                                      for name, expr in rule['result_relation'].items()}, expected)
                    self.assertEqual(rule['inputs'], ['S1', 'S2'])
                    self.assertEqual(rule['output'], 'D')
                    self.assertEqual(body['executor_capability'], 'not_established')

    def test_native_remainder_and_structured_array_layouts_remain_distinct(self):
        for opcode, bits in [('B/', 16), ('B/P', 16), ('DB/', 32), ('DB/P', 32)]:
            with self.subTest(opcode=opcode):
                values = [f['value'] for f in self.facts(opcode) if ':ql:BCD:result_relation:' in f['id']
                          and f['dimension'] == 'effects.documented_processing']
                native = next(v for v in values if v['output_layout'].get('when_destination') == 'word_device')
                layout = native['output_layout']
                self.assertEqual(layout['word_count'], bits // 8)
                self.assertEqual([(c['name'], c['word_offset'], c['value_bits']) for c in layout['components']],
                                 [('quotient', 0, bits), ('remainder', bits // 16, bits)])
                self.assertEqual(layout['bit_device_destination']['remainder'], 'not_stored')
                arrays = [v['output_layout'] for v in values if 'array_layouts' in v['output_layout']]
                self.assertTrue(arrays)
                for array in arrays:
                    self.assertNotIn('bit_device_destination', array)
                    self.assertEqual(array['component_order'], ['quotient', 'remainder'])
                    self.assertEqual([r['element_type'] for r in array['array_layouts']], ['ANY16', 'ANY32'])
                if bits == 32:
                    self.assertEqual(arrays[0]['array_layouts'][0]['component_element_indices'], [[0, 1], [2, 3]])

    def test_normal_model_projection_delivers_the_bound_formula_without_core_execution_promotion(self):
        from knowledge.fact_corpus import project_definition
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['B*', 'DB*', 'B/', 'DB/']:
            for language, mode, names in [('ladder', 'native_ladder_instruction', ['S1', 'S2']),
                                          ('fbd', None, ['s1', 's2']),
                                          ('ladder', 'structured_ladder_block', ['s1', 's2'])]:
                with self.subTest(opcode=opcode, language=language, mode=mode):
                    facts = self.facts(opcode)
                    _, withheld = project_definition({'facts': facts})
                    self.assertEqual(withheld, [])
                    source = dict(kind='instruction', target=opcode, scope={'models': ['Q03UDV']},
                                  content_origin='authored_fact', status='source_verified',
                                  value={'opcode': opcode, 'target_model': 'Q03UDV', 'facts': facts})
                    projected = technical_fact_packet(project_packet(source, plc_model='Q03UDV', language=language,
                                                                     project_mode=mode, aspects=['effects']))
                    encoded = json.dumps(projected, ensure_ascii=False)
                    self.assertIn('decode_packed_BCD', encoded)
                    self.assertIn('encode_packed_BCD', encoded)
                    self.assertIn('result_relation', encoded)
                    self.assertIn(json.dumps(names), encoded)
                    self.assertIs(registry._definition_owner_store.get_entry(opcode, cpu='Q03UDV')['call_available'], True)
                    self.assertTrue(all(f['value']['executor_capability']=='not_established'
                        for f in facts if f['dimension']=='effects.documented_processing'
                        and ':ql:BCD:result_relation:' in f['id']))
        self.assertFalse(any(':ql:BCD:result_relation:' in f['id'] for f in self.facts('MUL', model='FX3U')))


class QlClockNativeContractTests(unittest.TestCase):
    def test_clock_contracts_bind_independent_word_records_for_every_source_cpu(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.storage.q_encoder import _header_choices
        from gxw.token_profiles import source_token_profile
        models=registry.resolve_catalog_form('DATE+',cpu='Q03UDV').base_spec.cpu_support
        self.assertEqual(len(models),53)
        for cpu in models:
            for opcode in ['DATE+','DATE+P','DATE-','DATE-P']:
                with self.subTest(cpu=cpu,opcode=opcode):
                    form=registry.resolve_form(opcode,cpu=cpu)
                    self.assertEqual(form.spec.native_operand_order,('S1','S2','D'))
                    self.assertEqual([o.word_count for o in form.spec.operands],[3,3,3])
                    self.assertEqual([o.data_type for o in form.spec.operands],['word']*3)
                    self.assertEqual([o.role.value for o in form.spec.operands],['read','read','write'])
                    self.assertIn(3,{n for _,n in _header_choices(opcode,profile=source_token_profile(cpu))})
                    owner=registry._definition_owner_store.get_entry(opcode,cpu=cpu)
                    self.assertIs(owner['call_available'],True)
                    typed,=[f for f in owner['facts'] if f['dimension']=='parameters.typed_operands'
                        and f['id'].startswith('authored:ql:native_arguments_')]
                    for p in typed['value']['operands']:
                        self.assertEqual(p['type'],'ANY16')
                        self.assertIs(p['word_device_layout']['single_scalar'],False)
                        self.assertEqual(p['word_device_layout']['element_count'],3)
                        self.assertEqual([f['role'] for f in p['referent_fields']],['hour','minute','second'])
        for cpu in ['FX3U','FX3UC','FX5U']:
            for opcode in ['DATE+','DATE+P','DATE-','DATE-P']:
                self.assertFalse(registry.resolve_catalog_form(opcode,cpu=cpu).spec.supports_cpu(cpu))


class QlBlockArithmeticTests(unittest.TestCase):
    def test_contracts_and_dynamic_regions_cover_all_documented_QL_forms(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.storage.q_encoder import _header_choices
        from gxw.token_profiles import source_token_profile
        from plc.ir import analyze_instruction_access
        from plc.device_identity import instruction_operand_regions
        models=registry.resolve_catalog_form('BK-',cpu='Q03UDV').base_spec.cpu_support
        self.assertEqual(len(models),53)
        for cpu in models:
            for opcode in ['BK+','BK+P','BK-','BK-P']:
                with self.subTest(cpu=cpu,opcode=opcode):
                    form=registry.resolve_form(opcode,cpu=cpu)
                    self.assertEqual(form.spec.native_operand_order,('S1','S2','D','n'))
                    self.assertEqual([p.role.value for p in form.spec.operands],['read','read','write','read'])
                    self.assertEqual(form.pulse,opcode.endswith('P'))
                    self.assertFalse(form.double)
                    self.assertIn(4,{n for _,n in _header_choices(opcode,profile=source_token_profile(cpu))})
                    self.assertIs(registry._definition_owner_store.get_entry(opcode,cpu=cpu)['call_available'],True)
        for opcode in ['BK+','BK+P','BK-','BK-P']:
            reads,writes=analyze_instruction_access(opcode,['D0','D10','D100','K3'],plc_model='Q03UDV')
            self.assertEqual(set(writes),{'D100','D101','D102'})
            self.assertEqual(set(reads),{'D0','D1','D2','D10','D11','D12'})
            reads,writes=analyze_instruction_access(opcode,['D0','K-2','D100','K3'],plc_model='Q03UDV')
            self.assertEqual(set(reads),{'D0','D1','D2'})
            self.assertEqual(set(writes),{'D100','D101','D102'})
            spec=registry.resolve_form(opcode,cpu='Q03UDV').spec
            regions=instruction_operand_regions(spec,opcode,['D0','K-2','D100','D200'],'Q03UDV')
            self.assertEqual({r['head'] for r in regions},{'D0','D100'})
            self.assertTrue(all('parameter_value:n' in r['requires'] for r in regions))
        for cpu in ['FX3U','FX3UC','FX5U']:
            for opcode in ['BK+P','BK-','BK-P']:
                self.assertFalse(registry.resolve_catalog_form(opcode,cpu=cpu).spec.supports_cpu(cpu))
        for invalid in ['BK+PP','BK-PP']:
            self.assertIsNone(registry.resolve_catalog_form(invalid))

    def test_identical_region_exception_does_not_relax_other_disjoint_contracts(self):
        from plc.instruction_constraints import check_fact_constraints
        for allow_identical in [None,False,True]:
            for destination,expected in [('D0','passed' if allow_identical is True else 'failed'),
                                         ('D1','failed'),('D3','passed'),('R0','passed')]:
                with self.subTest(allow_identical=allow_identical,destination=destination):
                    value={'rule':'source_destination_disjoint','source_operand_index':0,
                           'source_length_operand_index':3,'destination_operand_index':2,
                           'destination_length_operand_index':3}
                    if allow_identical is not None:value['allow_identical']=allow_identical
                    constraint=fact('overlap','constraints.disjoint_regions',value=value,
                        scope={'models':['Q03UDV'],'forms':['BK+'],'languages':['ladder']})
                    result=check_fact_constraints([constraint],{'$OPERANDS':['D0','D10',destination,'K3'],'$3':3},
                        target='BK+',plc_model='Q03UDV',language='ladder')
                    self.assertEqual(result['status'],expected)

    def test_source_vectors_keep_element_math_constant_repetition_and_signed_wrap(self):
        from plc.instruction_definition import evaluate_expression
        cases=[('BK+',[1234,4567,-2000,-1234,4000],[4000,1234,-1234,5000,4321],[5234,5801,-3234,3766,8321]),
               ('BK+',[1234,4567,-2000,-1234,4000],4321,[5555,8888,2321,3087,8321]),
               ('BK-',[8765,8888,9325,5000,4352],[1234,5678,9876,4321,4000],[7531,3210,-551,679,352]),
               ('BK-',[8765,8888,9325,5000,4352],8880,[-115,8,445,-3880,-4528]),
               ('BK+',[32767,-32767],[2,-2],[-32767,32767]),
               ('BK-',[-32768,32767],[2,-2],[32766,-32767])]
        for base,left,right,expected in cases:
            for pulse in ['', 'P']:
                with self.subTest(base=base,pulse=pulse,left=left,right=right):
                    facts=QlBcdResultRelationTests.facts(base+pulse)
                    bodies=[f['value'] for f in facts if ':ql:block_arithmetic:relation:' in f['id']
                            and f['dimension']=='effects.documented_processing']
                    self.assertEqual(len(bodies),3)
                    for body in bodies:
                        rule,=body['rules'];relation=rule['result_relation']
                        self.assertEqual(rule['inputs'],['S1','S2'] if rule['output']=='D' else ['s1','s2'])
                        self.assertNotIn(rule['count_operand'],rule['inputs'])
                        self.assertEqual(relation['expression_inputs']['right_element']['selection'],'literal_or_device')
                        actual=[evaluate_expression(relation['value'],{'left_element':a,'right_element':right[i] if isinstance(right,list) else right})
                                for i,a in enumerate(left)]
                        self.assertEqual(actual,expected)
                        self.assertEqual(body['boundary_state']['SM700'],'not_turned_on_by_this_operation')
                        self.assertEqual(body['executor_capability'],'not_established')

    def test_model_projection_keeps_the_count_and_operand_order_per_interface(self):
        for opcode in ['BK+','BK+P','BK-','BK-P']:
            for language,mode in [('ladder','native_ladder_instruction'),('fbd',None),('ladder','structured_ladder_block')]:
                with self.subTest(opcode=opcode,language=language,mode=mode):
                    source=dict(kind='instruction',target=opcode,scope={'models':['Q03UDV']},content_origin='authored_fact',
                                status='source_verified',value={'opcode':opcode,'target_model':'Q03UDV','facts':QlBcdResultRelationTests.facts(opcode)})
                    result=technical_fact_packet(project_packet(source,plc_model='Q03UDV',language=language,project_mode=mode,aspects=['parameters','effects','execution']))
                    text=json.dumps(result)
                    for token in ['block_point_count','literal_or_device','left_element','right_element','SM700','disjoint_or_identical']:
                        self.assertIn(token,text)


class QlClockIoNativeContractTests(unittest.TestCase):
    def test_seven_word_records_match_all_source_cpus_and_read_write_directions(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.storage.q_encoder import _header_choices
        from gxw.token_profiles import source_token_profile
        from plc.ir import analyze_instruction_access
        models=registry.resolve_catalog_form('DATERD',cpu='Q03UDV').base_spec.cpu_support
        self.assertEqual(len(models),53)
        fields=['year','month','day','hour','minute','second','day_of_week']
        for cpu in models:
            for opcode in ['DATERD','DATERDP','DATEWR','DATEWRP']:
                with self.subTest(cpu=cpu,opcode=opcode):
                    form=registry.resolve_form(opcode,cpu=cpu)
                    name='D' if opcode.startswith('DATERD') else 'S'
                    self.assertEqual(form.spec.native_operand_order,(name,))
                    self.assertEqual(form.pulse,opcode.endswith('P'))
                    self.assertFalse(form.double)
                    operand,=form.spec.operands
                    self.assertEqual((operand.data_type,operand.word_count,operand.role.value),
                                     ('word',7,'write' if name=='D' else 'read'))
                    self.assertIn(1,{n for _,n in _header_choices(opcode,profile=source_token_profile(cpu))})
                    owner=registry._definition_owner_store.get_entry(opcode,cpu=cpu)
                    self.assertIs(owner['call_available'],True)
                    typed,=[f for f in owner['facts'] if f['dimension']=='parameters.typed_operands'
                        and f['id'].startswith('authored:ql:native_arguments_')]
                    pin,=typed['value']['operands']
                    self.assertEqual(pin['type'],'ANY16')
                    self.assertIs(pin['word_device_layout']['single_scalar'],False)
                    self.assertEqual(pin['word_device_layout']['element_count'],7)
                    self.assertEqual([f['role'] for f in pin['referent_fields']],fields)
                    self.assertEqual([(f['minimum'],f['maximum']) for f in pin['referent_fields']],
                                     [(1980,2079),(1,12),(1,31),(0,23),(0,59),(0,59),(0,6)])
        for opcode in ['DATERD','DATERDP','DATEWR','DATEWRP']:
            reads,writes=analyze_instruction_access(opcode,['D100'],plc_model='Q03UDV')
            expected={'D'+str(i) for i in range(100,107)}
            self.assertEqual(set(writes if opcode.startswith('DATERD') else reads),expected)
            for cpu in ['FX3U','FX3UC','FX5U']:
                self.assertFalse(registry.resolve_catalog_form(opcode,cpu=cpu).spec.supports_cpu(cpu))
        for invalid in ['DDATERD','DDATEWR','DATERDPP','DATEWRPP']:
            self.assertIsNone(registry.resolve_catalog_form(invalid))

    def test_clock_transfer_relations_bind_the_CPU_state_and_each_interface_record(self):
        for opcode in ['DATERD','DATERDP','DATEWR','DATEWRP']:
            reading=opcode.startswith('DATERD')
            facts=QlBcdResultRelationTests.facts(opcode)
            bodies=[f['value'] for f in facts if ':ql:clock_io:transfer:' in f['id']
                    and f['dimension']=='effects.documented_processing']
            self.assertEqual(len(bodies),4)
            for body in bodies:
                self.assertEqual(body['executor_capability'],'not_established')
                rule,=body['rules']
                parameter,=body['parameter_usage']
                name=parameter['parameter']
                self.assertEqual(parameter['word_device_layout']['word_count'],7)
                self.assertEqual(parameter['semantic_access'],'write' if reading else 'read')
                self.assertEqual(rule['component_indexing'],'zero_based_record_offset')
                self.assertEqual(rule['state_accesses'][0]['access'],'read' if reading else 'write')
                for index,field in enumerate(['year','month','day','hour','minute','second','day_of_week']):
                    actual=rule['result_relation'][field]
                    self.assertEqual(actual,{'state_ref':'CPU_clock','component':field} if reading else
                        {'operator':'read_component','operand':name,'index':index})
                if reading:
                    self.assertEqual(rule['output'],name)
                    self.assertEqual(rule['calendar_compensation'],'automatic_for_leap_years')
                    layout=body['output_layout']
                    if layout['when_destination']=='seven_element_array':
                        self.assertEqual(layout['element_indices'],[1,2,3,4,5,6,7])
                        self.assertEqual(layout['processing_diagram_indices'],[0,1,2,3,4,5,6])
                else:
                    self.assertEqual(rule['output'],{'state_ref':'CPU_clock'})
                    self.assertEqual(rule['inputs'],[name])
                self.assertEqual(rule['day_of_week_values']['Sunday'],0)
                self.assertEqual(rule['day_of_week_values']['Saturday'],6)

    def test_default_model_projection_keeps_the_seven_native_fields(self):
        for opcode in ['DATERD','DATERDP','DATEWR','DATEWRP']:
            with self.subTest(opcode=opcode):
                source=dict(kind='instruction',target=opcode,scope={'models':['Q03UDV']},
                            content_origin='authored_fact',status='source_verified',
                            value={'opcode':opcode,'target_model':'Q03UDV','facts':QlBcdResultRelationTests.facts(opcode)})
                projected=technical_fact_packet(project_packet(source,plc_model='Q03UDV',language='ladder',
                    project_mode='native_ladder_instruction',aspects=['parameters']))
                text=json.dumps(projected)
                for field in ['year','month','day','hour','minute','second','day_of_week']:
                    self.assertIn(field,text)
                self.assertIn('independent_elements',text)
                self.assertIn('2079',text)


class QlBcdNativeContractTests(unittest.TestCase):
    forms = ['B+','B+P','DB+','DB+P','B-','B-P','DB-','DB-P',
             'B*','B*P','DB*','DB*P','B/','B/P','DB/','DB/P']

    def test_native_contracts_match_every_source_cpu_form_and_existing_header(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.storage.q_encoder import _header_choices
        from gxw.token_profiles import source_token_profile
        models=sorted(registry.resolve_catalog_form('B+',cpu='Q03UDV').base_spec.cpu_support)
        self.assertEqual(len(models),53)
        for cpu in models:
            for opcode in self.forms:
                with self.subTest(cpu=cpu,opcode=opcode):
                    form=registry.resolve_form(opcode,cpu=cpu)
                    self.assertTrue(form.spec.supports_cpu(cpu))
                    self.assertEqual(form.spec.semantic_kind.value,'vendor')
                    self.assertTrue({'arity','operand_order','operand_roles','operand_types',
                                     'form_identity','cpu_applicability','device_classes'} <= set(form.spec.verified_fields))
                    arities=[2,3] if opcode in self.forms[:8] else [3]
                    headers={n for _,n in _header_choices(opcode,profile=source_token_profile(cpu))}
                    for count in arities:
                        spec=form.spec.for_operand_count(count)
                        self.assertEqual(len(spec.operands),count)
                        self.assertIn(count,headers)
                    owner=registry._definition_owner_store.get_entry(opcode,cpu=cpu)
                    self.assertIs(owner['call_available'],True)
                    self.assertEqual(owner['support_status'],'reviewed_native_signature')
        for excluded in ['FX3U','FX3UC','FX5U']:
            for opcode in self.forms:
                with self.subTest(excluded=excluded,opcode=opcode):
                    self.assertFalse(registry.resolve_catalog_form(opcode,cpu=excluded).spec.supports_cpu(excluded))

    def test_native_source_roles_and_occupancy_keep_in_place_and_bit_destinations_separate(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.ir import analyze_instruction_access
        two=registry.resolve_form('DB-',cpu='Q03UDV').spec.for_operand_count(2)
        three=registry.resolve_form('DB-',cpu='Q03UDV').spec.for_operand_count(3)
        self.assertEqual([p.role.value for p in two.operands],['read','read_write'])
        self.assertEqual([p.role.value for p in three.operands],['read','read','write'])
        self.assertEqual([p.word_count for p in two.operands],[2,2])
        reads,writes=analyze_instruction_access('DB*',['D0','D10','D100'],plc_model='Q03UDV')
        self.assertTrue({'D0','D1','D10','D11'} <= set(reads))
        self.assertEqual(set(writes),{'D100','D101','D102','D103'})
        _,bits=analyze_instruction_access('DB/',['D0','D10','K8Y0'],plc_model='Q03UDV')
        self.assertEqual(len(bits),32)


class QlBcdAddSubRelationTests(unittest.TestCase):
    def test_source_numeric_diagrams_bind_both_native_arities_and_decimal_wrap(self):
        cases = [('B+',0x5678,0x1234,0x6912), ('B+',0x6432,0x3583,0x0015),
                 ('B-',0x0678,0x0234,0x0444), ('B-',0x0001,0x0003,0x9998),
                 ('DB+',0x56789123,0x01234567,0x58023690), ('DB+',0x99000000,0x01654321,0x00654321),
                 ('DB-',0x56789123,0x01234567,0x55554556), ('DB-',0x12345678,0x12345679,0x99999999)]
        for opcode,left,right,expected in cases:
            for pulse in ['', 'P']:
                with self.subTest(opcode=opcode+pulse,left=left,right=right):
                    bodies = [f['value'] for f in QlBcdResultRelationTests.facts(opcode+pulse)
                              if f['dimension']=='effects.documented_processing' and ':ql:BCD:result_relation:' in f['id']
                              and any(c.get('equals')=='native_ladder_instruction' for c in f.get('conditions',[]))]
                    self.assertEqual(len(bodies),2)
                    self.assertEqual({tuple(body['rules'][0]['inputs']) for body in bodies}, {('D','S'),('S1','S2')})
                    for body in bodies:
                        rule, = body['rules']
                        arguments=dict(zip(rule['inputs'],[left,right]))
                        self.assertEqual(QlBcdResultRelationTests.value(rule['result_relation']['value'],arguments),expected)
                        self.assertEqual(body['boundary_state']['overflow_or_underflow']['SM700'],
                                         'not_turned_on_by_this_operation')
                        if rule['inputs']==['D','S']:
                            usage = next(p for p in body['parameter_usage'] if p['parameter']=='D')
                            self.assertEqual(usage['semantic_access'],'read_write')

    def test_bcd_source_signatures_and_default_model_view_preserve_two_and_three_data_forms(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['B+','B+P','B-','B-P','DB+','DB+P','DB-','DB-P']:
            with self.subTest(opcode=opcode):
                raw=registry._definition_owner_store.get_entry(opcode,cpu='Q03UDV')
                tables=[f['value']['operands'] for f in raw['facts'] if f['dimension']=='parameters.typed_operands'
                        and f['id'].startswith('authored:ql:native_arguments_')]
                self.assertEqual(sorted([p['name'] for p in table] for table in tables),[['S','D'],['S1','S2','D']])
                source=dict(kind='instruction',target=opcode,scope={'models':['Q03UDV']},
                            content_origin='authored_fact',status='source_verified',
                            value={**raw,'facts':QlBcdResultRelationTests.facts(opcode)})
                projected=technical_fact_packet(project_packet(source,plc_model='Q03UDV',language='ladder',
                    project_mode='native_ladder_instruction',aspects=['parameters','effects']))
                encoded=json.dumps(projected)
                self.assertIn('decode_packed_BCD',encoded)
                self.assertIn('previous_destination_and_result',encoded)
                self.assertIn('["D", "S"]',encoded)
                self.assertIn('["S1", "S2"]',encoded)
                self.assertIs(raw['call_available'],True)
                self.assertTrue(all(f['value']['executor_capability']=='not_established'
                    for f in source['value']['facts'] if f['dimension']=='effects.documented_processing'
                    and ':ql:BCD:result_relation:' in f['id']))


class QlClockResultRelationTests(unittest.TestCase):
    def test_clock_source_examples_and_day_wrap_preserve_all_three_components(self):
        for opcode, left, right, expected in [
                ('DATE+', [6,32,40], [7,48,10], [14,20,50]),
                ('DATE+', [14,20,30], [20,20,20], [10,40,50]),
                ('DATE-', [10,40,20], [3,50,10], [6,50,10]),
                ('DATE-', [4,50,32], [10,42,12], [18,8,20]),
                ('DATE+', [23,59,59], [0,0,1], [0,0,0]),
                ('DATE-', [0,0,0], [0,0,1], [23,59,59])]:
            for pulse in ['', 'P']:
                for mode in ['word_device', 'three_element_array']:
                    with self.subTest(opcode=opcode+pulse, left=left, right=right, mode=mode):
                        facts = QlBcdResultRelationTests.facts(opcode+pulse)
                        body = next(f['value'] for f in facts if ':ql:clock:result_relation:' in f['id']
                                    and f['dimension'] == 'effects.documented_processing'
                                    and f['value']['output_layout']['when_destination'] == mode)
                        rule, = body['rules']
                        args = dict(zip(rule['inputs'], [left,right]))
                        actual = [QlBcdResultRelationTests.value(rule['result_relation'][field], args)
                                  for field in ['hour','minute','second']]
                        self.assertEqual(actual, expected)
                        self.assertEqual(body['output_layout']['word_count'], 3)
                        self.assertEqual([p['word_device_layout']['word_count'] for p in body['parameter_usage']], [3,3,3])
                        self.assertEqual([p['semantic_access'] for p in body['parameter_usage']], ['read','read','write'])

    def test_clock_model_view_keeps_formula_ranges_and_array_or_word_positions(self):
        for opcode in ['DATE+', 'DATE+P', 'DATE-', 'DATE-P']:
            for language, mode in [('ladder','native_ladder_instruction'), ('fbd',None),
                                   ('ladder','structured_ladder_block')]:
                with self.subTest(opcode=opcode, language=language, mode=mode):
                    source = dict(kind='instruction', target=opcode, scope={'models':['Q03UDV']},
                                  content_origin='authored_fact', status='source_verified',
                                  value={'opcode':opcode, 'target_model':'Q03UDV',
                                         'facts': QlBcdResultRelationTests.facts(opcode)})
                    projected = technical_fact_packet(project_packet(source, plc_model='Q03UDV', language=language,
                                                                     project_mode=mode, aspects=['effects']))
                    text = json.dumps(projected)
                    self.assertIn('86400', text)
                    self.assertIn('read_component', text)
                    self.assertIn('day_wrap_seconds', text)
                    self.assertIn('word_offsets' if mode == 'native_ladder_instruction' else 'element_indices', text)
                    self.assertIn('clock_time_components', text)


class QlNativeOverloadSourceTests(unittest.TestCase):
    def test_bcd_and_clock_registry_forms_keep_existing_modifier_ownership(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for base in ['B*', 'B+', 'B-', 'B/', 'DB+', 'DB-', 'DATE+']:
            for pulse in [False, True]:
                opcode = base + ('P' if pulse else '')
                with self.subTest(opcode=opcode):
                    form = registry.resolve_catalog_form(opcode, cpu='Q03UDV')
                    self.assertIsNotNone(form)
                    self.assertEqual(form.opcode, opcode)
                    self.assertEqual(form.pulse, pulse)
                    self.assertEqual(form.double, base.startswith('DB'))
                    self.assertTrue(form.spec.supports_cpu('Q03UDV'))
                    self.assertTrue(registry.resolve_catalog_form(opcode, cpu='L02').spec.supports_cpu('L02'))
                    for excluded in ['FX3U', 'FX3UC', 'FX5U']:
                        self.assertFalse(form.spec.supports_cpu(excluded))
        for invalid in ['DDB*', 'DDB/', 'DDATE+', 'B+PP', 'DB+PP']:
            with self.subTest(invalid=invalid):
                self.assertIsNone(registry.resolve_catalog_form(invalid))

    def test_wrapped_native_drawings_need_a_complete_corresponding_source_row(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_drawing_forms
        cases = [
            (['BK+', 'BK+P', 'BK-', 'BK-P'], ['S1', 'S2', 'D', 'n'],
             ['Command', 'BK+, BK- n', 'S1 S2 D', 'Command', 'BK+P, BK-P P S2 S2 D n']),
            (['DATE-', 'DATE-P'], ['S1', 'S2', 'D'],
             ['Command', 'DATE- S1 S2', 'DATE- D', 'Command', 'DATE-P DATE-P S1 S2 D'])]
        for symbols, order, header in cases:
            with self.subTest(symbols=symbols):
                section = SimpleNamespace(lines=[' '.join(order)])
                forms = _native_drawing_forms(section, header, symbols, dict.fromkeys(order, {}))
                self.assertTrue(all(form['order'] == order and form['minimum'] == len(order) for form in forms))
                self.assertEqual({s for form in forms for s in form['allowed']}, set(symbols))
                unresolved = _native_drawing_forms(SimpleNamespace(lines=[]), header, symbols, dict.fromkeys(order, {}))
                self.assertTrue(any(form['order'] != order for form in unresolved))
        # Two distinct parameter sets remain independent overloads.
        forms = _native_drawing_forms(SimpleNamespace(lines=['S1 S2 D']),
            ['Command', 'SYNTH S D', 'Command', 'SYNTHP S1 S2 D'],
            ['SYNTH', 'SYNTHP'], dict.fromkeys(['S', 'S1', 'S2', 'D'], {}))
        self.assertEqual([f['order'] for f in forms], [['S', 'D'], ['S1', 'S2', 'D']])

    def test_block_count_is_a_region_bound_and_never_a_scalar_addend(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_parameter_specs, _reviewed_literal_specs
        for symbol in ['BK+', 'BK+P', 'BK-', 'BK-P']:
            with self.subTest(symbol=symbol):
                sentence = ('Adds' if '+' in symbol else 'Subtracts') + ' n points of BIN data from (S1) and (S2).'
                section = SimpleNamespace(manual='sh080809engx', title='BIN 16-bit block operations',
                    chapter='6.2 Arithmetic Operation Instructions', heading_line='BK+(P), BK-(P)',
                    symbols=['BK+', 'BK+P', 'BK-', 'BK-P'], symbol_tokens=['BK+(P)', 'BK-(P)'],
                    text=sentence, statements=lambda _: [sentence],
                    _native_parameter_contracts=[{'allowed': [symbol], 'minimum': 4,
                        'order': ['S1', 'S2', 'D', 'n'], 'rows': {name: {
                            'description': ('Number of blocks' if name == 'n' else 'Head device data') + ' (BIN 16 bits)',
                            'witness': name} for name in ['S1', 'S2', 'D', 'n']}}])
                typed, = [s['value'] for s in _native_parameter_specs(section, symbol)
                          if s['dimension'] == 'parameters.typed_operands']
                self.assertEqual([p['type'] for p in typed['operands']], ['ANY16'] * 4)
                self.assertEqual([p['direction'] for p in typed['operands']], ['input', 'input', 'output', 'input'])
                self.assertEqual(typed['operands'][2]['write_layout']['count_operand'], 'n')
                for parameter in typed['operands'][:3]:
                    self.assertEqual(parameter['word_device_layout']['word_count']['name'],'n')
                    self.assertIs(parameter['word_device_layout']['single_scalar'],False)
                self.assertTrue(all(p.get('purpose') for p in typed['operands']))
                operation, = _reviewed_literal_specs(section, symbol)
                self.assertEqual(operation['value']['inputs'], ['S1', 'S2'])
                self.assertEqual(operation['value']['count_operand'], 'n')
                self.assertNotIn(operation['value']['operation'], ['add', 'subtract'])

    def test_native_clock_records_keep_all_three_documented_fields(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_parameter_specs
        for symbol in ['DATE+', 'DATE+P', 'DATE-', 'DATE-P']:
            with self.subTest(symbol=symbol):
                section = SimpleNamespace(title='Clock data subtraction operation', chapter='7.15 Clock Instructions',
                    heading_line='DATE-(P)', symbols=['DATE+', 'DATE+P', 'DATE-', 'DATE-P'], symbol_tokens=['DATE-(P)'], text='',
                    _geometry_flat=[('source', 693, 'S1 Hour S2 Hour D Hour '
                        'S1+1 Minute S2+1 Minute D+1 Minute S1+2 Second S2+2 Second D+2 Second')],
                    _native_parameter_contracts=[{'allowed': [symbol], 'minimum': 3,
                        'order': ['S1', 'S2', 'D'], 'rows': {name: {
                            'description': 'Head device clock time data (BIN 16 bits)', 'witness': name}
                            for name in ['S1', 'S2', 'D']}}])
                typed, = [s['value'] for s in _native_parameter_specs(section, symbol)
                          if s['dimension'] == 'parameters.typed_operands']
                for pin in typed['operands']:
                    self.assertEqual(pin['word_device_layout'], {'kind':'independent_elements', 'single_scalar':False,
                        'device_unit_bits':16, 'element_bits':16, 'element_count':3, 'word_count':3})
                    self.assertEqual([(f['offset'], f['role'], f['maximum']) for f in pin['referent_fields']],
                                     [(0, 'hour', 23), (1, 'minute', 59), (2, 'second', 59)])

    def test_clock_io_record_layout_requires_the_complete_seven_field_diagram(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_parameter_specs
        fields=['Year','Month','Day','Hour','Minute','Second','Day of week']
        for symbol in ['DATERD','DATERDP','DATEWR','DATEWRP']:
            name='D' if symbol.startswith('DATERD') else 'S'
            for complete in [True,False]:
                with self.subTest(symbol=symbol,complete=complete):
                    chart=' '.join(name+('+'+str(i) if i else '')+' '+label
                                   for i,label in enumerate(fields[:7 if complete else 6]))
                    section=SimpleNamespace(title='Reading clock data' if name=='D' else 'Writing clock data',
                        chapter='7.15 Clock Instructions',heading_line=symbol,symbols=[symbol],symbol_tokens=[symbol],
                        text='',_geometry_flat=[('source',687,chart)],_native_parameter_contracts=[{
                        'allowed':[symbol],'minimum':1,'order':[name],'rows':{name:{
                            'description':'Head number of devices where clock data is stored (BIN 16 bits)',
                            'witness':name}}}])
                    typed,=[s['value'] for s in _native_parameter_specs(section,symbol)
                            if s['dimension']=='parameters.typed_operands']
                    pin,=typed['operands']
                    if complete:
                        self.assertEqual(pin['word_device_layout']['word_count'],7)
                        self.assertEqual(len(pin['referent_fields']),7)
                    else:
                        self.assertNotIn('word_device_layout',pin)
                        self.assertNotIn('referent_fields',pin)

    def test_a_generic_header_keeps_the_source_proven_model_width(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        legacy = fact('legacy_header', 'parameters.variant_family', value={
            'base_opcode': 'SQR', 'section_variants': ['SQR', 'SQRP']},
            scope={'models': ['Q03UDV', 'FX3U'], 'forms': ['SQR', 'SQRP'], 'languages': ['ladder']})
        for cpu, bits in [('Q03UDV', 32), ('FX3U', 16)]:
            info = registry.instruction_knowledge('SQR', cpu=cpu, language='ladder', facts=[legacy])
            self.assertEqual((info['family'], info['data_bits']), ('SQR', bits))
        header_only = fact('literal_header', 'parameters.variant_family', value={
            'base_opcode': 'NOP', 'section_variants': ['NOP']},
            scope={'models': ['FX3U'], 'forms': ['NOP'], 'languages': ['ladder']})
        self.assertNotIn('data_bits', registry.instruction_knowledge('NOP', cpu='FX3U', language='ladder', facts=[
            header_only, {**copy.deepcopy(header_only), 'id': 'another_header'}]))

    def test_a_component_head_label_does_not_truncate_a_two_word_bcd_result(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_parameter_specs
        section = SimpleNamespace(title='BCD square roots', chapter='7.12 Special Function Instructions',
            heading_line='BSQR(P), BDSQR(P)',
            symbols=['BSQR', 'BSQRP', 'BDSQR', 'BDSQRP'], symbol_tokens=['BSQR(P)', 'BDSQR(P)'],
            text='The operation results of (D) and (D)+1 are stored as their respective BCD values of between 0 and 9999.',
            _native_parameter_contracts=[{'allowed': ['BSQR', 'BDSQR'], 'minimum': 2, 'order': ['S', 'D'],
                'rows': {'S': {'description': 'Source (BSQR(P): BCD 4 digits, BDSQR(P): BCD 8 digits)', 'witness': 'source'},
                         'D': {'description': 'Result head (BCD 4 digits)', 'witness': 'destination'}}}])
        for opcode, source_type in [('BSQR', 'ANY16'), ('BDSQR', 'ANY32')]:
            typed, = [s['value'] for s in _native_parameter_specs(section, opcode)
                      if s['dimension'] == 'parameters.typed_operands']
            self.assertEqual([p['type'] for p in typed['operands']], [source_type, 'ANY32'])
            self.assertEqual(typed['operands'][1]['storage_words'], 2)
            self.assertEqual(typed['operands'][1]['component_decimal_digits'], 4)

    def test_source_bound_width_can_differ_between_models_without_changing_the_family(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import registered_family_bindings
        q = registered_family_bindings(registry, models=['Q03UDV'])['SQR']
        fx = registered_family_bindings(registry, models=['FX3U'])['SQR']
        self.assertEqual((q['base_opcode'], q['data_bits'], q['double'], q['pulse']), ('SQR', 32, False, False))
        self.assertEqual((fx['base_opcode'], fx['data_bits'], fx['double'], fx['pulse']), ('SQR', 16, False, False))

    def test_operand_storage_annotations_win_over_the_conversions_purpose(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_data_type, _parameter_form_type
        section = SimpleNamespace(title='BIN to floating-point data (double precision)',
            symbols=['FLTD', 'FLTDP', 'DFLTD', 'DFLTDP'])
        source = _native_data_type('Integer data to be converted to 64-bit floating-point data '
                                   '(BIN 16/32 bits)', section=section)
        self.assertEqual(_parameter_form_type(section, 'FLTD', dict(source))['type'], 'ANY16')
        self.assertEqual(_parameter_form_type(section, 'DFLTDP', dict(source))['type'], 'ANY32')
        self.assertEqual(_native_data_type('Converted 64-bit floating-point data (real number)',
            section=section)['type'], 'LREAL')
        bcd = _native_data_type('Source (BSQR(P): BCD 4 digits, BDSQR(P): BCD 8 digits)')
        section.symbols = ['BSQR', 'BSQRP', 'BDSQR', 'BDSQRP']
        section.symbol_tokens = ['BSQR(P)', 'BDSQR(P)']
        self.assertEqual(_parameter_form_type(section, 'BDSQRP', bcd)['type'], 'ANY32')

    def test_own_cpu_clauses_keep_the_version_on_its_group_and_exclude_the_named_model(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _cpu_header_requirement
        section = SimpleNamespace(lines=[
            '• QnU(D)(H)CPU and QnUDE(H)CPU with a serial number (first five digits) of "10102" or later, '
            'Q00UJCPU, Q00UCPU, Q01UCPU, QnUDVCPU, and QnUDPVCPU: Supported'])
        for model in ['Q00UJ', 'Q00U', 'Q01U', 'Q03UDV', 'Q13UDPV']:
            with self.subTest(model=model):
                self.assertEqual(_cpu_header_requirement(section, model), {'state': 'supported', 'conditions': []})
        condition = _cpu_header_requirement(section, 'Q03UD')
        self.assertEqual(condition['state'], 'conditional')
        self.assertEqual(condition['conditions'][0]['minimum'], '10102')
        section.lines = ['• Universal model QCPU: Not supported for Q00UJCPU']
        self.assertEqual(_cpu_header_requirement(section, 'Q00UJ')['state'], 'unsupported')
        self.assertEqual(_cpu_header_requirement(section, 'Q03UDV')['state'], 'supported')
        self.assertIsNone(_cpu_header_requirement(section, 'L02'))

    def test_a_damaged_prefix_does_not_move_the_destination_before_the_source(self):
        from tools.fact_ql_behavior_review import _native_drawing_forms
        forms = _native_drawing_forms(None, ['Command', 'SYNTH D S D'], ['SYNTH'], {'S': {}, 'D': {}})
        self.assertEqual(forms[0]['order'], ['S', 'D'])

    def test_a_continuation_overload_needs_its_own_drawing_and_formal_legend(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _prepare_native_parameter_contracts
        pages = [['SYNTH(P)', 'Basic', 'Command', 'SYNTH S D',
                  '(S): Source data (BIN 16 bits)', '(D): Destination data (BIN 16 bits)'],
                 ['SYNTH(P)', 'Command', 'SYNTH S1 S2 D', '(S1): First source (BIN 16 bits)',
                  '(S2): Second source (BIN 16 bits)', '(D): Result data (BIN 16 bits)'],
                 ['Program example', 'SYNTH D100 D200 D300']]
        source = SimpleNamespace()
        section = SimpleNamespace(manual='sh080809engx', has_instruction_chunk=True,
            symbols=['SYNTH'], title='Arithmetic', pages=[1], page_pairs=[(1, 3)],
            rows=[{'id': 'source', 'pdf_page': 1, 'pdf_page_end': 3}])
        with patch('tools.fact_ql_behavior_review._fetch', return_value=[
                {'pdf_page': n, 'word_geometry_json': json.dumps(lines), 'visual_json': '{}'}
                for n, lines in enumerate(pages, 1)]), patch(
                'tools.fact_ql_behavior_review._parameter_word_lines', side_effect=lambda words: words), patch(
                'tools.fact_ql_behavior_review._is_instruction_overview', return_value=False), patch(
                'tools.fact_ql_behavior_review.declared_symbols', return_value=(['SYNTH'], ['SYNTH'])), patch(
                'tools.fact_ql_behavior_review.family_of', return_value='arithmetic'):
            _prepare_native_parameter_contracts(source, {'s': section})
        self.assertEqual([c['order'] for c in section._native_parameter_contracts], [['S', 'D'], ['S1', 'S2', 'D']])
        self.assertEqual([c['page'] for c in section._native_parameter_contracts], [1, 2])


class LegacyConversionBlockKnowledgeTests(unittest.TestCase):
    @staticmethod
    def effective(cpu, opcode):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        raw = registry.definition_entry(opcode, cpu=cpu)
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=raw['facts'])[0]

    def test_conversion_and_square_root_modes_preserve_format_and_memory_extents(self):
        for cpu, opcode, expected in [('FX2N', 'DFLTP', 1), ('FX2C', 'FLTP', 2),
                                      ('FX2C', 'DFLTP', 2), ('FX2NC', 'DSQRP', 2)]:
            with self.subTest(cpu=cpu, opcode=opcode):
                rows = self.effective(cpu, opcode)
                tables = [f for f in rows if f['dimension'] == 'parameters.typed_operands' and
                    f['id'].startswith('assembly:parameters:') and ':legacy:conversion_block:' in f['id']]
                self.assertEqual(len(tables), expected)
                for table in tables:
                    enabled = any(c.get('device') == 'M8023' and c.get('equals') is True for c in table.get('conditions', []))
                    ps = {p['name']: p for p in table['value']['operands']}
                    self.assertTrue(all(p.get('purpose') for p in ps.values()))
                    if opcode == 'FLTP':
                        self.assertEqual([ps[n]['word_device_layout']['word_count'] for n in ['S', 'D']],
                                         [2, 1] if enabled else [1, 2])
                    else:
                        self.assertEqual([ps[n]['word_device_layout']['word_count'] for n in ['S', 'D']], [2, 2])
                text = str([f for f in rows if ':legacy:conversion_block:' in f['id']])
                if 'SQR' in opcode:
                    self.assertIn('M8067', text)
                    self.assertIn('floating_point_square_root', text)
                elif cpu == 'FX2C': self.assertIn('binary_floating_point_to_integer', text)
                else: self.assertNotIn('binary_floating_point_to_integer', text)

    def test_block_semantics_keep_count_uncertainty_and_model_specific_direction(self):
        for opcode in ['BMOVP', 'DFMOVP']:
            rows = self.effective('FX2N', opcode)
            own = [f for f in rows if ':legacy:conversion_block:' in f['id']]
            self.assertNotIn('M8024', str(own))
            tables = [f for f in own if f['dimension'] == 'parameters.typed_operands' and f['id'].startswith('assembly:parameters:')]
            self.assertEqual(len(tables), 1)
            parameters = tables[0]['value']['operands']
            self.assertTrue(all(p.get('purpose') for p in parameters))
            count = next(p for p in parameters if p['name'] == 'n')
            self.assertEqual(count['width_bits'], 'unknown')
            self.assertNotIn('word_device_layout', count)
            self.assertEqual(tables[0]['status'], 'declared_unverified')
        modern = self.effective('FX3U', 'BMOVP')
        self.assertTrue(any(r.get('direction_selector', {}).get('device') == 'M8024'
            for f in modern for r in f['value'].get('rules', [])))
        conditional = [f for f in self.effective('FX1', 'DFMOV') if ':legacy:conversion_block:' in f['id']]
        self.assertTrue(any(f['dimension'] == 'constraints.cpu_availability' and f['value']['available'] == 'unknown' for f in conditional))
        self.assertFalse(any(f['dimension'] == 'effects.documented_processing' and f['status'] == 'source_verified' for f in conditional))


class LegacyArithmeticKnowledgeTests(unittest.TestCase):
    def test_shared_signed_arithmetic_matches_decimal_reference_and_modular_boundaries(self):
        from decimal import Decimal, ROUND_DOWN
        from hypothesis import given, settings, strategies as st
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups
        from plc.instruction_definition import evaluate_expression, UnknownInstructionSemantics
        expressions = {}
        for base in ['ADD', 'SUB', 'MUL', 'DIV', 'INC', 'DEC', 'NEG']:
            for opcode in [base, base + 'P', 'D' + base, 'D' + base + 'P']:
                groups = family_fact_groups(registry, opcode, cpu='FX2N')
                bodies = [f['value'] for f in groups if f['dimension'] == 'effects.documented_processing'
                          and any(r.get('kind') == 'expression_write' for r in f['value'].get('rules', []))]
                self.assertEqual(len(bodies), 1)
                bits = 32 if registry.resolve_catalog_form(opcode).double else 16
                expressions[opcode] = base, bits, bodies[0]['rules'][0]['outputs']
        @settings(max_examples=120, deadline=None, database=None)
        @given(case=st.sampled_from(list(expressions)), raw_left=st.integers(-2**31, 2**31 - 1),
               raw_right=st.integers(-2**31, 2**31 - 1))
        def check(case, raw_left, raw_right):
            base, bits, outputs = expressions[case]
            sign = 2**(bits - 1)
            left = (raw_left + sign) % (2**bits) - sign
            right = (raw_right + sign) % (2**bits) - sign
            values = {'S1': left, 'S2': right, 'D': left}
            if base == 'DIV' and right == 0:
                with self.assertRaises(UnknownInstructionSemantics): evaluate_expression(outputs[0]['expression'], values)
                return
            a, b = Decimal(left), Decimal(right)
            if base == 'ADD': expected = [int(a + b)]
            elif base == 'SUB': expected = [int(a - b)]
            elif base == 'MUL': expected = [int(a * b)]
            elif base == 'DIV':
                quotient = (a / b).to_integral_value(rounding=ROUND_DOWN)
                expected = [int(quotient), int(a - quotient * b)]
            else:
                exact = {'INC': left + 1, 'DEC': left - 1, 'NEG': -left}[base]
                expected = [(exact + sign) % (2**bits) - sign]
            for output, want in zip(outputs, expected):
                width = output['expression']['type']['bits']
                if -(2**(width - 1)) <= want < 2**(width - 1):
                    self.assertEqual(evaluate_expression(output['expression'], values), want)
                else:
                    with self.assertRaises(UnknownInstructionSemantics): evaluate_expression(output['expression'], values)
        check()
        for opcode in ['INC', 'DINC', 'DEC', 'DDEC', 'NEG', 'DNEG']:
            base, bits, outputs = expressions[opcode]; limit = 2**(bits - 1)
            old, expected = (limit - 1, -limit) if base == 'INC' else (-limit, limit - 1 if base == 'DEC' else -limit)
            self.assertEqual(evaluate_expression(outputs[0]['expression'], {'D': old}), expected)

    def test_modes_and_composite_results_keep_their_own_native_types_and_scopes(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import family_fact_groups, compose_programming_facts
        for cpu, opcode in [('FX0', 'DADD'), ('FX2C', 'ADD'), ('FX1N', 'DMUL'), ('FX2C', 'DMULP'), ('FX2C', 'DDIVP')]:
            raw = registry._definition_owner_store.get_entry(opcode, cpu=cpu, vendor='mitsubishi')
            groups, _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=raw['facts'])
            native = [f for f in groups if f['dimension'] == 'parameters.typed_operands'
                      and f['id'].startswith('assembly:parameters:') and ':legacy:arithmetic:' in f['id']]
            switched = cpu == 'FX2C' and opcode in {'DMULP', 'DDIVP'}
            self.assertEqual(len(native), 2 if switched else 1)
            for table in native:
                floating = any(c.get('device') == 'M8023' and c.get('equals') is True for c in table.get('conditions', []))
                self.assertFalse(floating and not switched)
                if not switched:
                    self.assertFalse(any(c.get('device') == 'M8023' for c in table.get('conditions', [])))
                result = table['value']['operands'][-1]
                if opcode in {'DMUL', 'DMULP', 'DDIVP'}:
                    self.assertEqual(result['word_device_layout']['word_count'], 2 if floating else 4)
                if opcode == 'DDIVP' and not floating:
                    self.assertEqual([(c['purpose'], c['offset_words']) for c in result['components']], [('quotient', 0), ('remainder', 2)])
                    self.assertFalse(result['numeric_input_gate'])
                if floating:
                    self.assertNotIn('components', result)
            self.assertFalse(any('M8304' in str(f['value']) or 'M8306' in str(f['value'])
                                 for f in groups if ':legacy:arithmetic:' in f['id']))
        for cpu, opcode in [('FX0', 'ADDP'), ('FX0N', 'DMULP'), ('FX0S', 'NEG')]:
            groups = family_fact_groups(registry, opcode, cpu=cpu)
            self.assertTrue(any(f['dimension'] == 'constraints.cpu_availability' and f['value']['available'] is False for f in groups))
            self.assertFalse(any(f['dimension'] == 'effects.documented_processing' for f in groups))


class LiteralArithmeticReferenceTests(unittest.TestCase):
    def test_queries_and_confirmed_instances_keep_every_registered_literal_name(self):
        from knowledge.instruction_facts import instruction_fact_targets
        from knowledge.structured_facts import structured_fact_targets
        names = [op for base in ['+', '-', '*', '/'] for op in [base, base + 'P', 'D' + base, 'D' + base + 'P']]
        for opcode in names:
            with self.subTest(opcode=opcode):
                self.assertEqual([target['opcode'] for target in instruction_fact_targets(opcode)], [opcode])
                self.assertEqual([target['opcode'] for target in instruction_fact_targets('使用 ' + opcode + ' 指令')], [opcode])
        query = '使用 ' + ' '.join(names) + ' 指令，保留参数用途'
        self.assertEqual([target['opcode'] for target in instruction_fact_targets(query)], names)
        targets = structured_fact_targets(query, plc_model='Q03UDV')
        self.assertEqual([target['opcode'] for target in targets['instructions']], names)

    def test_expressions_negative_constants_and_prohibitions_are_not_opcode_lookups(self):
        from knowledge.instruction_facts import instruction_fact_targets
        from knowledge.structured_facts import structured_fact_targets
        for query in ['D0 / D1', '使用 D0 + D1 计算', 'K-1', '不要使用 D*P 指令', 'D*PP', 'D/P/', '*/']:
            with self.subTest(query=query):
                self.assertEqual(instruction_fact_targets(query), [])
                self.assertEqual(structured_fact_targets(query, plc_model='Q03UDV')['instructions'], [])


class QlConversionBlockKnowledgeTests(unittest.TestCase):
    def facts(self, opcode, cpu='Q03UDV'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        raw = registry._definition_owner_store.get_entry(opcode, cpu=cpu)
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=raw['facts'])[0]

    def test_numeric_parameters_keep_the_manual_source_and_result_footprints(self):
        for base, wide, words in [('FLT', 'DFLT', [(1, 2), (2, 2)]),
                ('FLTD', 'DFLTD', [(1, 4), (2, 4)]), ('INT', 'DINT', [(2, 1), (2, 2)]),
                ('INTD', 'DINTD', [(4, 1), (4, 2)]), ('SQR', None, [(2, 2)]),
                ('BSQR', 'BDSQR', [(1, 2), (2, 2)])]:
            for opcode, expected in zip([base, wide] if wide else [base], words):
                for name in [opcode, opcode + 'P']:
                    with self.subTest(opcode=name):
                        facts = self.facts(name)
                        native, = [f for f in facts if f['id'].startswith('assembly:parameters:family:')
                            and ':ql:conversion_block:' in f['id'] and f['dimension'] == 'parameters.typed_operands'
                            and 'native_form' in f['value']]
                        pins = native['value']['operands']
                        self.assertEqual([p['name'] for p in pins], ['S', 'D'])
                        self.assertEqual(tuple(p['word_device_layout']['word_count'] for p in pins), expected)
                        self.assertTrue(all(p['purpose'] for p in pins))
                        if base == 'BSQR':
                            self.assertEqual([(c['purpose'], c['offset_words']) for c in pins[1]['components']],
                                [('integer_part', 0), ('fractional_part_scaled_by_10000', 1)])
        q = self.facts('SQR')
        self.assertTrue(any(f['value'].get('operation') == 'floating_point_square_root' for f in q))
        self.assertNotIn('M8023', json.dumps(q))
        fx = self.facts('SQR', 'FX3U')
        self.assertTrue(any(f['value'].get('operation') == 'integer_square_root' for f in fx))

    def test_negative_zero_and_rounding_differences_remain_explicit(self):
        for cpu, key in [('Q03UDV', 'negative_zero'), ('Q02H', 'configuration_parameter'), ('L02', 'negative_zero')]:
            bodies = [f['value']['numeric_preprocessing'] for f in self.facts('SQR', cpu)
                      if 'numeric_preprocessing' in f['value']]
            self.assertTrue(bodies, cpu)
            self.assertTrue(all(key in b for b in bodies), (cpu, bodies))
            if cpu == 'Q03UDV':
                self.assertTrue(all(b['operation_error_for_negative_zero'] is False for b in bodies))
            if cpu == 'L02':
                self.assertTrue(all(b['operation_error_for_negative_zero'] is True for b in bodies))
        integer = self.facts('DINTD')
        bodies = [f['value'] for f in integer if f['value'].get('operation') == 'floating_point_to_signed_integer']
        self.assertTrue(bodies)
        self.assertTrue(all(b['rounding']['halfway_tie_rule'] == 'not_stated' for b in bodies))
        self.assertTrue(any(f['status'] == 'unknown' and 'halfway_rounding_tie_rule' in
                            f['value'].get('unresolved_items', []) for f in integer))

    def test_block_counts_and_error_rules_do_not_import_fx_truncation_or_selectors(self):
        for opcode, count in [('BMOVP', 1), ('FMOVP', 1), ('DFMOVP', 2), ('RBMOVP', 1)]:
            with self.subTest(opcode=opcode):
                facts = self.facts(opcode)
                native, = [f for f in facts if f['dimension'] == 'parameters.typed_operands'
                    and f['id'].startswith('assembly:parameters:family:') and ':ql:conversion_block:' in f['id']
                    and 'native_form' in f['value']]
                self.assertEqual([p['word_device_layout']['word_count'] for p in native['value']['operands']],
                                 [count, count, 1])
                rules = [r for f in facts for r in f['value'].get('rules', [])
                         if {'context': 'project_mode', 'equals': 'native_ladder_instruction'} in f.get('conditions', [])]
                transfer, = [r for r in rules if r.get('kind') in {'range_copy', 'range_fill'}]
                self.assertNotEqual(transfer['out_of_range'], 'transfer_within_available_device_range')
                self.assertTrue(any(r.get('error_code') == 4101 for r in rules))
                self.assertNotIn('M8024', json.dumps(facts))
                if opcode in {'FMOVP', 'DFMOVP'}:
                    layouts = [f['value']['digit_specified_devices'] for f in facts
                               if 'digit_specified_devices' in f['value']]
                    self.assertTrue(layouts)
                    self.assertTrue(all(v['digit_specified_source_stride_bits'] == 'fixed_source_address_no_increment'
                                        for v in layouts))
                    self.assertTrue(any('source_sampling_when_source_overlaps_destination' in
                                        f['value'].get('unresolved_items', []) for f in facts))
                if opcode == 'DFMOVP':
                    self.assertTrue(any(r.get('kind') == 'not_executed' and r.get('condition', {}).get('equals') == 0 for r in rules))
                    self.assertTrue(any(r.get('error_code') == 4100 and r.get('condition', {}).get('less_than') == 0 for r in rules))

    def test_core_constraints_use_current_shared_cpu_rules_and_preserve_unknown_serials(self):
        from plc.instruction_constraints import check_instruction_constraints
        def cpu_checks(opcode, cpu, serial=None):
            context = {'project_mode': 'native_ladder_instruction'}
            if serial is not None:
                context['cpu_serial_first_five'] = serial
            report = check_instruction_constraints(opcode, {}, plc_model=cpu, language='ladder', context=context)
            return [c for c in report['checks'] if c.get('dimension') == 'constraints.cpu_availability']
        for opcode, cpu in [('DFMOV', 'Q00J'), ('FLTD', 'Q00J'), ('INTD', 'Q00J'),
                            ('RBMOV', 'Q00UJ'), ('RBMOV', 'L02')]:
            checks = cpu_checks(opcode, cpu)
            self.assertTrue(any(c['status'] == 'failed' for c in checks), (opcode, cpu, checks))
            self.assertFalse(any(c.get('reason') == "'rule'" for c in checks))
        self.assertTrue(all(c['status'] == 'passed' for c in cpu_checks('DFMOV', 'Q00U')))
        for opcode, cpu, lower, minimum in [('DFMOV', 'Q03UD', 10101, 10102), ('FLT', 'Q00J', 4121, 4122)]:
            self.assertTrue(any(c['status'] == 'failed' for c in cpu_checks(opcode, cpu, lower)))
            self.assertFalse(any(c['status'] == 'failed' for c in cpu_checks(opcode, cpu, minimum)))
            missing = cpu_checks(opcode, cpu)
            self.assertTrue(any(c['status'] == 'unverified' for c in missing))
            self.assertFalse(any(c['status'] == 'failed' for c in missing))


class QlArithmeticKnowledgeTests(unittest.TestCase):
    def facts(self, opcode):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        raw = registry._definition_owner_store.get_entry(opcode, cpu='Q03UDV')
        return compose_programming_facts(registry, opcode, cpu='Q03UDV', existing=raw['facts'])[0]

    def test_literal_forms_share_math_but_bind_the_previous_destination_only_in_place(self):
        from plc.instruction_definition import evaluate_expression
        for base, expected in [('+', -7), ('-', -15), ('*', -44)]:
            for opcode in [base, base + 'P', 'D' + base, 'D' + base + 'P']:
                with self.subTest(opcode=opcode):
                    facts = self.facts(opcode)
                    bodies = [f['value'] for f in facts if f['dimension'] == 'effects.documented_processing'
                        and ':ql:arithmetic:sh080809engx:' in f['id'] and f['value'].get('rules')]
                    self.assertEqual(len(bodies), 1 if base == '*' else 2)
                    for body in bodies:
                        output, = body['rules'][0]['outputs']
                        self.assertEqual(evaluate_expression(output['expression'],
                            {'S': 4, 'D': -11, 'S1': -11, 'S2': 4}), expected)
                    if base in {'+', '-'}:
                        self.assertTrue(any(f['value'].get('boundary_state', {}).get('on_signed_overflow') ==
                            'retain_low_data_bits_and_interpret_as_signed_twos_complement' for f in facts))
                    if base == '-':
                        self.assertTrue(any(f['value'].get('source_disagreement', {}).get('operation_basis') ==
                            'same_section_formal_bindings_and_numeric_diagrams' for f in facts))

    def test_division_storage_and_uncertainty_do_not_import_an_fx_signed_rounding_guarantee(self):
        from plc.instruction_definition import evaluate_expression
        for opcode, bits in [('/', 16), ('/P', 16), ('D/', 32), ('D/P', 32)]:
            facts = self.facts(opcode)
            body, = [f['value'] for f in facts if f['dimension'] == 'effects.documented_processing'
                and ':ql:arithmetic:sh080809engx:' in f['id'] and f['value'].get('rules')]
            self.assertEqual(body['reference_domain']['dividend'], 'nonnegative')
            self.assertEqual(body['reference_domain']['divisor'], 'positive')
            outputs = body['rules'][0]['outputs']
            self.assertEqual([o['target']['offset'] for o in outputs], [0, bits // 16])
            self.assertEqual([evaluate_expression(o['expression'], {'S1': 13, 'S2': 4}) for o in outputs], [3, 1])
            self.assertTrue(any(f['status'] == 'unknown' and 'signed_division_rounding_policy' in
                f['value'].get('unresolved_items', []) for f in facts))
            self.assertTrue(any(any(r.get('error_code') == 4100 and r.get('condition', {}).get('equals') == 0
                for r in f['value'].get('rules', [])) for f in facts))
            native = [f for f in facts if f['dimension'] == 'parameters.typed_operands'
                and f['id'].startswith('assembly:parameters:family:/:ql:arithmetic:sh080809engx:')]
            self.assertTrue(native)
            for table in native:
                result = table['value']['operands'][-1]
                self.assertEqual(result['word_device_layout']['word_count'], bits // 8)
                self.assertEqual([(c['purpose'], c['offset_words']) for c in result['components']],
                                 [('quotient', 0), ('remainder', bits // 16)])


class QlFloatingFamilyKnowledgeTests(unittest.TestCase):
    FORMS = {'E+': ['E+', 'E+P', 'ED+', 'ED+P'], 'E-': ['E-', 'E-P', 'ED-', 'ED-P'],
        'E*': ['E*', 'E*P', 'ED*', 'ED*P'], 'E/': ['E/', 'E/P', 'ED/', 'ED/P'],
        'ENEG': ['ENEG', 'ENEGP', 'EDNEG', 'EDNEGP'], 'EMOV': ['EMOV', 'EMOVP', 'EDMOV', 'EDMOVP'],
        'ECMP': ['ECMP', 'ECMPP', 'EDCMP', 'EDCMPP'], 'EZCP': ['EZCP', 'EZCPP', 'EDZCP', 'EDZCPP']}

    @staticmethod
    def facts(opcode, cpu='Q03UDV'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        owner = registry._definition_owner_store.get_entry(opcode, cpu=cpu)
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=(owner or {}).get('facts', []))[0]

    def test_every_registered_float_form_keeps_its_effective_width_purpose_and_three_bit_results(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from knowledge.instruction_facts import instruction_fact_targets
        for base, forms in self.FORMS.items():
            for index, opcode in enumerate(forms):
                with self.subTest(opcode=opcode):
                    self.assertEqual([target['opcode'] for target in instruction_fact_targets(opcode)], [opcode])
                    facts = self.facts(opcode)
                    ids = {f['id'] for f in facts}
                    for group in facts:
                        if ':ql:floating:' in group['id'] or any(':ql:floating:' in dep for dep in group.get('depends_on', [])):
                            self.assertFalse(set(group.get('depends_on', [])) - ids, group['id'])
                    knowledge = registry.instruction_knowledge(opcode, cpu='Q03UDV', language='ladder', facts=facts)
                    bits = 64 if index >= 2 else 32
                    self.assertEqual((knowledge['family'], knowledge['data_bits'], knowledge['modifiers']['pulse']),
                                     (base, bits, bool(index % 2)))
                    native = [f for f in facts if f['dimension'] == 'parameters.typed_operands'
                        and f['id'].startswith('assembly:parameters:family:') and ':ql:floating:' in f['id']
                        and f['scope']['languages'] == ['ladder'] and 'native_form' in f['value']]
                    self.assertEqual(len(native), 2 if base in {'E+', 'E-'} else 1)
                    for table in native:
                        for pin in table['value']['operands']:
                            self.assertTrue(pin.get('purpose'))
                            if pin['type'] == 'BOOL':
                                self.assertEqual(pin['occupied_bit_count'], 3)
                            else:
                                self.assertEqual(pin['type'], 'LREAL' if bits == 64 else 'REAL')
                                self.assertEqual(pin['word_device_layout']['word_count'], bits // 16)

    def test_structured_negation_keeps_declared_output_and_semantic_read_write(self):
        for opcode, words in [('ENEGP', 2), ('EDNEGP', 4)]:
            facts = self.facts(opcode)
            tables = [f for f in facts if f['dimension'] == 'parameters.typed_operands'
                and ':ql:floating:sh080783engp:' in f['id'] and f['id'].startswith('assembly:parameters:family:')]
            self.assertTrue(tables)
            for table in tables:
                pin, = [p for p in table['value']['operands'] if p['name'] == 'd']
                self.assertEqual(pin['direction'], 'output')
                self.assertEqual(pin['semantic_access'], 'read_write')
                self.assertEqual(pin['word_device_layout']['word_count'], words)

    def test_shared_numeric_module_is_once_and_does_not_apply_to_transfer_or_fx(self):
        from shared.model_context import technical_block, technical_context_units
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        packets = []
        for opcode in ['ED+P', 'EDNEGP', 'EDZCPP', 'EDMOVP']:
            p = {'kind': 'instruction', 'target': opcode, 'scope': {'models': ['Q03UDV']},
                '_knowledge_context': {'model': 'Q03UDV', 'language': 'ladder', 'project_mode': 'native_ladder_instruction'},
                'value': {'opcode': opcode, 'target_model': 'Q03UDV', 'facts': self.facts(opcode)}}
            packets.append(technical_block(technical_fact_packet(p)))
        units = technical_context_units('\n'.join(packets))
        self.assertEqual(sum(u.get('id') == 'mitsubishi.floating-point-inputs.ql' for u in units), 1)
        self.assertEqual(sum(u.get('kind') == 'instruction_modifiers' for u in units), 1)
        transfer = registry.instruction_knowledge('EDMOVP', cpu='Q03UDV', language='ladder', facts=self.facts('EDMOVP'))
        self.assertFalse(transfer.get('common_rules'))
        fx = registry.instruction_knowledge('ED+P', cpu='FX3U', language='ladder', facts=[])
        self.assertFalse((fx or {}).get('common_rules'))
        self.assertNotIn('data_bits', fx or {})

    def test_known_cpu_exclusions_and_unknown_serials_keep_their_source_scope(self):
        from plc.instruction_constraints import check_instruction_constraints
        from plc.instruction_knowledge import family_fact_groups
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['ED+P', 'EDMOVP', 'EDNEGP']:
            groups = family_fact_groups(registry, opcode, cpu='Q02H')
            self.assertTrue(any(f['value'].get('available') is False for f in groups if f['dimension'] == 'constraints.cpu_availability'))
            self.assertFalse(any(f['dimension'] == 'effects.documented_processing' for f in groups))
        for serial, expected in [(16111, 'failed'), (16112, 'passed'), (None, 'unverified')]:
            report = check_instruction_constraints('EDCMP', {'S1': 'D0', 'S2': 'D4', 'D': 'M0'},
                plc_model='Q03UDV', language='ladder', context={'project_mode': 'native_ladder_instruction',
                    **({} if serial is None else {'cpu_serial_first_five': serial})})
            cpu_checks = [c for c in report['checks'] if c.get('dimension') == 'constraints.cpu_availability']
            self.assertTrue(any(c['status'] == expected for c in cpu_checks), cpu_checks)
            if expected != 'failed':
                self.assertFalse(any(c['status'] == 'failed' for c in cpu_checks), cpu_checks)
        info = registry.instruction_knowledge('EDCMP', cpu='Q03UD', language='ladder', facts=self.facts('EDCMP', 'Q03UD'))
        self.assertFalse(any(c.get('minimum') == '16112' for c in (info or {}).get('conditions', [])))

    def test_underflow_unknowns_do_not_mask_the_documented_wide_multiply_and_divide_rule(self):
        for opcode in ['E*P', 'E/P', 'ED*P', 'ED/P']:
            facts = self.facts(opcode)
            gaps = {item for f in facts if ':ql:floating:' in f['id'] for item in f['value'].get('unresolved_items', [])}
            self.assertNotIn('legacy_CPU_overflow_response', gaps)
            if opcode.startswith('ED'):
                self.assertNotIn('operation_result_underflow_response', gaps)
                self.assertTrue(any(f['value'].get('when_result_is_negative_zero_or_underflow') == 'store_positive_zero' for f in facts))
            else:
                self.assertIn('operation_result_underflow_response', gaps)

    def test_structured_symbol_operators_do_not_inherit_a_callable_st_numeric_module(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['E+P', 'ED*P']:
            facts = self.facts(opcode)
            self.assertTrue(any(f['dimension'] == 'parameters.st_signature' and f['value'].get('available') is False for f in facts))
            info = registry.instruction_knowledge(opcode, cpu='Q03UDV', language='st', facts=facts)
            self.assertFalse((info or {}).get('common_rules'))


class FormalRealArithmeticTests(unittest.TestCase):
    @staticmethod
    def expression(op, bits):
        real = {'kind': 'real', 'bits': bits}
        return {'op': op, 'type': real, 'args': [
            {'op': 'parameter', 'name': name, 'type': real} for name in ['S1', 'S2']]}

    def test_real_relations_use_typed_expressions_without_enabling_an_executor(self):
        from plc.instruction_definition import Expression, UnknownInstructionSemantics, evaluate_expression
        from plc.instruction_effects import validate_behavior
        for bits in [32, 64]:
            for op in ['add', 'sub', 'mul', 'div']:
                with self.subTest(bits=bits, op=op):
                    raw = self.expression(op, bits)
                    parsed = Expression.from_mapping(raw)
                    self.assertEqual((parsed.op, parsed.value_type.kind, parsed.value_type.bits), (op, 'real', bits))
                    self.assertEqual(Expression.from_mapping(parsed.as_mapping()), parsed)
                    validate_behavior({'behavior': 'expression_write', 'outputs': [
                        {'target': {'parameter': 'D', 'kind': 'word'}, 'expression': raw}]})
                    with self.assertRaisesRegex(UnknownInstructionSemantics, 'rounding and exception'):
                        evaluate_expression(parsed, {'S1': 12.0, 'S2': 3.0})

    def test_real_arithmetic_rejects_integer_only_operators_mixed_types_and_integer_overflow_policies(self):
        from plc.instruction_definition import DefinitionError, Expression
        invalid = [self.expression(op, 32) for op in ['mod', 'bit_and', 'bit_or', 'bit_xor', 'shift_left', 'shift_right']]
        for bits in [16, 64]:
            mixed = self.expression('add', 32)
            mixed['args'][1]['type'] = {'kind': 'int', 'bits': 16} if bits == 16 else {'kind': 'real', 'bits': bits}
            invalid.append(mixed)
        invalid.extend(dict(self.expression('add', 32), overflow=policy) for policy in ['wrap', 'reject'])
        for raw in invalid:
            with self.subTest(expression=raw), self.assertRaises(DefinitionError):
                Expression.from_mapping(raw)

    def test_model_projection_preserves_real_width_math_and_unknown_rounding(self):
        source = packet([fact('effect', 'effects.documented_processing', value={'rules': [{
            'kind': 'expression_write', 'outputs': [{'target': {'parameter': 'D', 'kind': 'word'},
                'expression': self.expression('div', 64)}]}]}),
            fact('rounding', 'execution.unresolved_source', status='unknown',
                 value={'unresolved_items': ['exact_floating_point_rounding']})], model='Q03UDV')
        groups = technical_fact_packet(source)['value']['additional_knowledge']
        effect = next(group['semantics']['effects.documented_processing'] for group in groups
                      if 'effects.documented_processing' in group['semantics'])
        self.assertEqual(effect['rules'][0]['outputs'][0]['expression'], {
            'expression': '(S1 / S2)', 'type': {'kind': 'real', 'bits': 64}})
        self.assertTrue(any(group['knowledge_status'] == 'unknown'
                            and 'execution.unresolved_source' in group['semantics'] for group in groups))


class ScopedSemanticPresentationTests(unittest.TestCase):
    @staticmethod
    def unit(extra=None):
        tables, groups = [], []
        for index, (mode, source, destination, status) in enumerate([
                ('native_ladder_instruction', 'S', 'D', 'source_verified'),
                ('structured_ladder_block', 's', 'd', 'unknown')]):
            qualifiers = {'forms': ['EXAMPLE'], 'conditions': [{'context': 'project_mode', 'equals': mode}]}
            tables.append({**copy.deepcopy(qualifiers), 'id': 'table-' + str(index),
                'parameters': [{'name': source, 'semantic_role': 'input_value'},
                               {'name': destination, 'semantic_role': 'output_value'}]})
            body = {'operation': 'synthetic_transform', 'inputs': [source], 'output': destination,
                    'boundary_rules': [{'case': n, 'when': 'documented_condition',
                                        'result': 'retain_the_documented_source_value'} for n in range(8)],
                    'extra': copy.deepcopy(extra)}
            groups.append({**copy.deepcopy(qualifiers), 'knowledge_status': status, 'semantics': {
                'effects.documented_processing': {'id': 'claim-' + str(index), 'value': body,
                    'depends_on': ['prerequisite-' + str(index)]},
                'constraints.context': {'id': 'prerequisite-' + str(index), 'value': {'choice': index}}}})
        return {'kind': 'instruction', 'target': 'EXAMPLE', 'scope': {'models': ['FX3U']},
                'value': {'family': 'EXAMPLE', 'forms': [{'opcode': 'EXAMPLE'}],
                          'parameter_tables': tables, 'additional_knowledge': groups}}

    def test_selected_model_projection_keeps_combined_members_and_unknowns(self):
        states = {'FX3U': 'conditional', 'FX3UC': 'supported', 'Q03UDV': 'unsupported'}
        for model, expected in [('FX3U', {'FX3U': 'conditional'}),
                                ('FX3U/FX3UC', {'FX3U': 'conditional', 'FX3UC': 'supported'})]:
            source = packet([fact('cpu', 'constraints.cpu_availability',
                value={'model_states': states, 'available': True}, status='unknown')], model=model)
            before = copy.deepcopy(source)
            result = technical_fact_packet(source)
            group, = result['value']['additional_knowledge']
            self.assertEqual(group['semantics']['constraints.cpu_availability']['model_states'], expected)
            self.assertEqual(group['knowledge_status'], 'unknown')
            self.assertEqual(source, before)
        source = packet([fact('cpu', 'constraints.cpu_availability', value={'model_states': states})], model='FX3G')
        group, = technical_fact_packet(source)['value']['additional_knowledge']
        self.assertEqual(group['semantics']['constraints.cpu_availability']['model_states'], states)

    def test_non_instruction_payloads_keep_their_own_structure(self):
        from knowledge.instruction_presentation import merge_instruction_units
        for kind in ('device', 'language', 'error', 'original_case'):
            unit = {'kind': kind, 'target': 'synthetic', 'value': {
                'shared_semantics': {'literal_field': {'value': 0}},
                'conditions': [{'context': 'mode', 'equals': 2}], 'status': 'unknown'}}
            self.assertEqual(merge_instruction_units([unit]), [unit])

    def test_shared_body_keeps_each_interface_status_bindings_and_dependencies(self):
        from knowledge.instruction_presentation import expand_semantic_references, instruction_form_units, merge_instruction_units
        original = self.unit({'zero': 0, 'verified': False})
        before = copy.deepcopy(original)
        result, = merge_instruction_units([original])
        self.assertEqual(len(result['value']['shared_semantics']), 1)
        definition, = result['value']['shared_semantics'].values()
        self.assertEqual(definition['value']['inputs'], [{'parameter_role': 'input_value'}])
        for index, group in enumerate(result['value']['additional_knowledge']):
            claim = group['semantics']['effects.documented_processing']
            self.assertEqual(claim['depends_on'], ['prerequisite-' + str(index)])
            self.assertEqual(group['knowledge_status'], ['source_verified', 'unknown'][index])
            self.assertEqual(claim['value']['parameter_bindings']['input_value'], ['S', 's'][index])
        self.assertEqual(expand_semantic_references(result), original)
        self.assertEqual(instruction_form_units(result), [original])
        self.assertEqual(merge_instruction_units([result]), [result])
        self.assertEqual(original, before)
        self.assertLess(len(json.dumps(result)), len(json.dumps(original)))

    def test_real_differences_and_ambiguous_roles_are_not_normalized(self):
        from knowledge.instruction_presentation import expand_semantic_references, merge_instruction_units
        for field in ('different_type', 'ambiguous_roles', 'literal_placeholder'):
            original = self.unit(False)
            if field == 'different_type':
                original['value']['additional_knowledge'][1]['semantics']['effects.documented_processing']['value']['extra'] = 0
            elif field == 'ambiguous_roles':
                for table in original['value']['parameter_tables']:
                    table['parameters'][1]['semantic_role'] = 'input_value'
            else:
                for group in original['value']['additional_knowledge']:
                    group['semantics']['effects.documented_processing']['value']['literal'] = {'parameter_role': 'input_value'}
            result, = merge_instruction_units([original])
            self.assertNotIn('shared_semantics', result['value'])
            self.assertEqual(expand_semantic_references(result), original)

    def test_missing_shared_definition_is_not_counted_as_delivered(self):
        from knowledge.fact_coverage import included_evidence_ids
        from knowledge.instruction_presentation import merge_instruction_units
        from shared.model_context import technical_block
        original = self.unit()
        compact, = merge_instruction_units([original])
        records = [{'id': 'original', 'model_text': technical_block(original)}]
        self.assertEqual(included_evidence_ids(technical_block(compact), records), ['original'])
        compact['value']['shared_semantics'].clear()
        self.assertEqual(included_evidence_ids(technical_block(compact), records), [])

    def test_family_factoring_keeps_each_literal_form_after_shared_body_expansion(self):
        from knowledge.instruction_presentation import instruction_form_units, merge_instruction_units
        original = self.unit({'bits': 16})
        wide = self.unit({'bits': 32})
        wide['target'] = 'DEXAMPLE'
        wide['value']['forms'][0]['opcode'] = 'DEXAMPLE'
        for component in [*wide['value']['parameter_tables'], *wide['value']['additional_knowledge']]:
            component['forms'] = ['DEXAMPLE']
        merged, = merge_instruction_units([original, wide])
        self.assertIn('shared_semantics', merged['value'])
        self.assertEqual(instruction_form_units(merged), [original, wide])
        self.assertEqual(merge_instruction_units([merged]), [merged])

    def test_generated_payloads_roundtrip_with_qualifications_and_json_types(self):
        from hypothesis import example, given, settings, strategies as st
        from knowledge.instruction_presentation import expand_semantic_references, merge_instruction_units
        scalar = st.none() | st.booleans() | st.integers() | st.text(alphabet='qxyz', max_size=12)
        payloads = st.recursive(scalar, lambda child: st.lists(child, max_size=3)
            | st.dictionaries(st.text(alphabet='qxyz', min_size=1, max_size=4), child, max_size=3), max_leaves=12)
        @settings(max_examples=60, deadline=None, database=None)
        @given(payloads)
        @example({'zero': 0, 'false': False, 'missing': None, 'empty': []})
        def check(extra):
            original = self.unit(extra)
            result, = merge_instruction_units([original])
            self.assertEqual(len(result['value']['shared_semantics']), 1)
            self.assertEqual(json.dumps(expand_semantic_references(result), sort_keys=True),
                             json.dumps(original, sort_keys=True))
            self.assertEqual(merge_instruction_units([result]), [result])
        check()

    def test_operand_bindings_stay_per_interface_when_the_behavior_is_shared(self):
        from knowledge.instruction_presentation import expand_semantic_references, instruction_form_units, merge_instruction_units
        original = self.unit({'zero': 0, 'false': False})
        for index, group in enumerate(original['value']['additional_knowledge']):
            source, destination = [('S', 'D'), ('s', 'd')][index]
            interface = {'inputs': [{'parameter': source, 'type': ['WORD', 'ANY16'][index]}],
                         'output': {'parameter': destination, 'type': ['BOOL', 'BIT'][index]},
                         'literal_metadata': {'parameter_role': 'input_value'}}
            if index:
                interface['execution_arguments'] = [
                    {'parameter': 'EN', 'role': 'execution_condition'},
                    {'parameter': 'ENO', 'role': 'execution_status'}]
            group['semantics']['effects.documented_processing']['value']['operand_binding'] = interface
        before = copy.deepcopy(original)
        compact, = merge_instruction_units([original])
        definition, = compact['value']['shared_semantics'].values()
        self.assertEqual(definition['value']['operation'], 'synthetic_transform')
        self.assertNotIn('operand_binding', definition['value'])
        for actual, expected in zip(compact['value']['additional_knowledge'], original['value']['additional_knowledge']):
            reference = actual['semantics']['effects.documented_processing']['value']
            interface = expected['semantics']['effects.documented_processing']['value']['operand_binding']
            self.assertEqual(set(reference), {'semantic_ref', 'parameter_bindings', 'operand_binding'})
            self.assertEqual(reference['operand_binding'], interface)
            self.assertEqual(actual['knowledge_status'], expected['knowledge_status'])
            self.assertEqual(actual['conditions'], expected['conditions'])
            self.assertEqual(actual['semantics']['effects.documented_processing']['depends_on'],
                             expected['semantics']['effects.documented_processing']['depends_on'])
        self.assertEqual(json.dumps(expand_semantic_references(compact), sort_keys=True), json.dumps(original, sort_keys=True))
        self.assertEqual(instruction_form_units(compact), [original])
        self.assertEqual(merge_instruction_units([compact]), [compact])
        self.assertEqual(original, before)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))
        # Older two-key references may have their binding inside the stored
        # definition.  They still expand without dropping or rewriting it.
        legacy = copy.deepcopy(original)
        definitions = legacy['value']['shared_semantics'] = {}
        for index, group in enumerate(legacy['value']['additional_knowledge']):
            claim = group['semantics']['effects.documented_processing']
            identity = 'legacy-' + str(index)
            definitions[identity] = {'dimension': 'effects.documented_processing', 'value': claim['value']}
            claim['value'] = {'semantic_ref': identity, 'parameter_bindings': {}}
        self.assertEqual(expand_semantic_references(legacy), original)

    def test_generated_interface_metadata_is_lossless_and_does_not_hide_behavior_differences(self):
        from hypothesis import example, given, settings, strategies as st
        from knowledge.instruction_presentation import expand_semantic_references, merge_instruction_units
        scalar = st.none() | st.booleans() | st.integers() | st.text(alphabet='qxyz', max_size=12)
        metadata = st.recursive(scalar, lambda child: st.lists(child, max_size=3)
            | st.dictionaries(st.text(alphabet='qxyz', min_size=1, max_size=4), child, max_size=3), max_leaves=12)

        @settings(max_examples=40, deadline=None, database=None)
        @given(metadata, st.sampled_from(['both', 'second_only', 'empty', 'literal_null']), st.booleans())
        @example({'zero': 0, 'false': False, 'empty': []}, 'both', False)
        @example(None, 'second_only', False)
        def check(extra, mode, different_behavior):
            original = self.unit()
            for index, group in enumerate(original['value']['additional_knowledge']):
                body = group['semantics']['effects.documented_processing']['value']
                if mode == 'both' or mode == 'second_only' and index:
                    body['operand_binding'] = {'inputs': [{'parameter': ['S', 's'][index],
                        'type': ['WORD', 'ANY16'][index]}], 'interface_metadata': copy.deepcopy(extra)}
                elif mode == 'empty': body['operand_binding'] = {}
                elif mode == 'literal_null': body['operand_binding'] = None
                if index and different_behavior:
                    body['boundary_rules'][0]['result'] = 'a_different_documented_result'
            before = copy.deepcopy(original)
            compact, = merge_instruction_units([original])
            if different_behavior:
                self.assertNotIn('shared_semantics', compact['value'])
            else:
                self.assertEqual(len(compact['value']['shared_semantics']), 1)
            self.assertEqual(json.dumps(expand_semantic_references(compact), sort_keys=True),
                             json.dumps(original, sort_keys=True))
            self.assertEqual(merge_instruction_units([compact]), [compact])
            self.assertEqual(original, before)
        check()


class FBDOperatorDefaultProjectionTests(unittest.TestCase):
    def test_operator_source_resolution_keeps_technical_fields_without_review_attribution(self):
        from shared.model_context import model_knowledge_value
        for declared, conflict_state in [(True, 'retained_for_FX_GT_operator'),
                                         ('not_determined', 'not_established_by_example_label_names')]:
            with self.subTest(declared=declared):
                resolution = {'formal_pin_domains': {'FX_GT': 'ANY_BIT', 'QL_GT': 'ANY_SIMPLE'},
                    'pin_count': {'minimum': 2, 'maximum': 28}, 'QL_NE_and_not_equal_input_count': 2,
                    'extend_ANY_BIT_to_signed_or_real_from_example': False,
                    'FX_GT_example_uses_declared_INT': declared, 'conflict_state': conflict_state,
                    'FX_GT_operation_text_uses_word_devices': True}
                value = {'operators': {'GT': {'inputs': ['BOOL', 'WORD', 'DWORD'], 'output': ['BOOL']}},
                         'source_resolution': {'GT': resolution}, 'source_conflicts': {}}
                before = copy.deepcopy(value)
                output = model_knowledge_value(value)
                self.assertEqual(output['operators'], value['operators'])
                self.assertEqual(output['source_resolution']['GT'], {k: resolution[k] for k in (
                    'formal_pin_domains', 'pin_count', 'QL_NE_and_not_equal_input_count',
                    'extend_ANY_BIT_to_signed_or_real_from_example')})
                self.assertEqual(value, before)

    def test_active_operator_conflict_withholds_only_that_symbol_and_alias(self):
        from shared.model_context import model_knowledge_value
        value = {'scope': {'models': ['FX3U']}, 'conditions': [{'context': 'cpu_version', 'minimum': '1.00'}],
            'operators': {'GT': {'inputs': ['WORD']}, 'GE': {'inputs': ['INT']}, 'GT_E': {'inputs': ['INT']}},
            'source_conflicts': {'GT': {'reason': {'kind': 'explanation_reference', 'note_ref': 'source_conflicts.GT.reason'}}},
            'source_resolution': {'GT': {'pin_count': {'minimum': 2, 'maximum': 28}},
                                  'GE': {'pin_count': {'minimum': 2, 'maximum': 28}}},
            'standard_function_aliases': {'COMPARE': 'GT', 'GE_E': 'GE', 'GT_E': 'GT_E'}}
        before = copy.deepcopy(value)
        result = model_knowledge_value(value)
        self.assertEqual(result['operators'], {k: value['operators'][k] for k in ('GE', 'GT_E')})
        self.assertNotIn('GT', result.get('source_resolution', {}))
        self.assertNotIn('source_conflicts', result)
        self.assertEqual(result['standard_function_aliases'], {'GE_E': 'GE', 'GT_E': 'GT_E'})
        self.assertEqual(result['conditions'], value['conditions'])
        self.assertEqual(result['scope'], value['scope'])
        self.assertEqual(value, before)

    def test_unrelated_source_resolution_and_unknown_technical_fields_are_unchanged(self):
        from shared.model_context import model_knowledge_value
        value = {'source_resolution': {'sensor': {'calibration': 'not_determined'}},
                 'algorithm': {'precision': 'not_determined'}, 'pin_count': 7}
        self.assertEqual(model_knowledge_value(value), value)


class ModelKnowledgeDeliveryTests(unittest.TestCase):
    def test_default_delivery_keeps_programming_content_and_concrete_conditions_without_review_states(self):
        from shared.model_context import technical_block, technical_context_units
        source = packet([
            fact('legacy', 'identity.applicability', status='unknown', value={'available': True}),
            fact('cpu', 'constraints.cpu_availability', value={'available': True},
                 conditions=[{'kind': 'serial_number_minimum', 'digits': 5, 'minimum': '16043'}]),
            fact('effect', 'effects.result', value={'output': 'D', 'offsets': [0, 1, 2]}),
            fact('limits', 'execution.unresolved_source', status='unknown',
                 value={'unresolved_items': ['destination_state_after_operation_error']})], model='Q03UDV')
        before = copy.deepcopy(source)
        delivered, = technical_context_units(technical_block(technical_fact_packet(source)))
        text = json.dumps(delivered)
        self.assertNotIn('unknown', text)
        self.assertNotIn('unresolved', text)
        self.assertNotIn('knowledge_status', text)
        self.assertIn('16043', text)
        self.assertNotIn('destination_state_after_operation_error', text)
        self.assertIn('"offsets": [0, 1, 2]', text)
        self.assertNotIn('generation_requirements', text)
        self.assertNotIn('avoid_relying_on', text)
        self.assertEqual(source, before)
        self.assertEqual(before['value']['facts'][0]['status'], 'unknown')
        self.assertFalse(fact_record_coverage(row(source)))
        self.assertEqual(technical_context_units(technical_block(delivered)), [delivered])

    def test_an_unresolved_cpu_clause_cannot_become_unconditional_availability(self):
        from shared.model_context import technical_block, technical_context_units
        source = packet([fact('cpu', 'constraints.cpu_availability',
            value={'model_states': {'Q03UD': 'conditional'}},
            conditions=[{'kind': 'documented_condition', 'value': 'unknown'}])], model='Q03UD')
        result, = technical_context_units(technical_block(technical_fact_packet(source)))
        group, = result['value']['additional_knowledge']
        self.assertNotIn('generation_requirements', group)
        self.assertNotIn('available', group['semantics']['constraints.cpu_availability'])
        self.assertNotIn('unknown', json.dumps(result))
        self.assertEqual(group['semantics']['constraints.cpu_availability']['model_states'], {'Q03UD': 'conditional'})

    def test_default_coverage_separates_review_status_from_actual_delivery(self):
        from knowledge.evidence import KnowledgeContext
        from shared.model_context import model_knowledge_coverage, technical_knowledge_coverage
        coverage = {'status': 'incomplete', 'delivery': 'delivered', 'targets': [{'kind': 'instruction', 'target': 'EXAMPLE',
            'identity_found': True, 'status': 'incomplete', 'delivery': 'delivered', 'call_available': True,
            'aspects': [{'aspect': 'effects', 'status': 'unresolved', 'gap_reasons': ['source_review_pending']}]}]}
        before = copy.deepcopy(coverage)
        expected = model_knowledge_coverage(coverage)
        self.assertEqual(expected['delivery'], 'delivered')
        self.assertEqual(expected['targets'][0]['delivery'], 'delivered')
        self.assertNotIn('status', expected)
        self.assertNotIn('status', expected['targets'][0])
        self.assertNotIn('incomplete', json.dumps(expected))
        self.assertNotIn('generation_requirements', expected['targets'][0])
        self.assertNotIn('call_available', json.dumps(expected))
        self.assertNotIn('source_review_pending', json.dumps(expected))
        self.assertEqual(technical_knowledge_coverage(KnowledgeContext('', {'retrieval_coverage': coverage})), expected)
        self.assertEqual(coverage, before)

    def test_language_placeholders_do_not_become_model_requirements_when_rendered_again(self):
        from shared.model_context import technical_block, technical_context_text, technical_context_units
        source = {'kind': 'language', 'target': 'EXAMPLE', 'value': {'facts': [
            fact('syntax', 'language.syntax', status='unknown', value={'available': True})]}}
        first = technical_context_text(technical_block(technical_fact_packet(source)))
        result = technical_context_units(technical_context_text(first))
        claim, = result[0]['value']['facts']
        self.assertNotIn('generation_requirements', claim)
        self.assertNotIn('available', claim)
        self.assertNotIn('unknown', json.dumps(result))

    def test_fbd_context_delivers_use_limits_and_concrete_language_rules(self):
        from application.fbd_context import compose_fbd_context
        context = compose_fbd_context(request='MOV', plc_model='Q03UDV', catalog={})
        project_rules = context.payload['language_rules']['rules']['FBD.PROJECT']
        self.assertEqual(project_rules['missing_fb_options'], 'report_missing_conditions_not_default_values')
        self.assertEqual(context.language_rules['rules']['FBD.PROJECT']['unknown_fb_options'],
                         'report_missing_conditions_not_default_values')
        self.assertEqual({row['code'] for row in context.payload['use_limits']}, {
            'compile_option_array_unavailable',
            'native_compilation_not_run', 'plc_execution_not_run'})
        self.assertNotIn('unknown', json.dumps(context.payload).lower())
        self.assertNotIn('unknown', json.dumps(context.wire_messages).lower())
        native_limit = next(row for row in context.payload['use_limits'] if row['code'] == 'native_compilation_not_run')
        self.assertIn('未做原生编译', native_limit['message'])

    def test_review_and_conflict_metadata_never_reach_the_model_value(self):
        from shared.model_context import model_knowledge_value
        value = {'knowledge_status': 'source_review_pending', 'binding_status': 'partial',
                 'core_call_available': True, 'conflicts': ['variant'], 'gaps': ['effects'],
                 'value': {'available': True}, 'semantics': {'effects.result': {'output': 'D'}}}
        self.assertEqual(model_knowledge_value(value), {'semantics': {'effects.result': {'output': 'D'}}})
        self.assertIsNone(model_knowledge_value({'knowledge_status': 'conflict',
            'semantics': {'effects.result': {'output': 'D'}}}))

    def test_shared_limits_still_expand_for_each_call_form_after_placeholder_removal(self):
        from knowledge.instruction_presentation import instruction_form_units
        from shared.model_context import technical_block, technical_context_units
        limits = ['undocumented_boundary_case_' + str(index) for index in range(25)]
        source = packet([fact('first', 'execution.unresolved_source', status='unknown',
            value={'unresolved_items': limits}, conditions=[{'context': 'mode', 'equals': 1}]),
            fact('second', 'execution.unresolved_source', status='unknown',
            value={'unresolved_items': limits}, conditions=[{'context': 'mode', 'equals': 2}])])
        delivered, = technical_context_units(technical_block(technical_fact_packet(source)))
        self.assertNotIn('undocumented_boundary_case_', json.dumps(delivered))
        self.assertNotIn('shared_semantics', delivered['value'])
        self.assertNotIn('unknown', json.dumps(delivered))

    def test_diagnostic_placeholders_and_math_policy_do_not_masquerade_as_facts(self):
        from shared.model_context import model_knowledge_value
        value = {'meaning': 'documented_fault', 'cause': 'unknown',
            'knowledge_status_by_field': {'meaning': 'source_verified', 'cause': 'unknown'},
            'recovery': {'actions': [{'action': 'check_connections'}]},
            'expression': {'type': {'kind': 'int', 'bits': 32}, 'overflow': 'unknown'},
            'types': ['INT16', 'unknown', 'INT32']}
        result = model_knowledge_value(value)
        self.assertEqual(result['meaning'], 'documented_fault')
        self.assertEqual(result['recovery'], value['recovery'])
        self.assertNotIn('cause', result)
        self.assertEqual(result['expression'], {'type': {'kind': 'int', 'bits': 32}})
        self.assertEqual(result['types'], ['INT16', 'INT32'])
        self.assertNotIn('avoid_relying_on', json.dumps(result))
        self.assertNotIn('unknown', json.dumps(result))

    def test_stored_device_numbering_uses_current_numeric_units_without_changing_addresses(self):
        from knowledge.structured_facts import resolve_device_family_records
        region = {'minimum': 200, 'maximum': 255, 'bits': 32, 'minimum_operand_bits': 32,
                  'sources': [{'manual_id': 'synthetic_counter', 'pdf_pages': [7]}]}
        stored = {'kind': 'device_family', 'target': 'C', 'status': 'source_verified', 'content_origin': 'authored_fact',
                  'value': {'family': 'C', 'cpu': 'FX3U', 'range_basis': 'model_address_numbering',
                            'regions': [[0, 255]], 'maximum_number': 255}}
        original = copy.deepcopy(stored)
        source = {'id': 'counter', 'text': '[AUTHORED FACT]\n' + json.dumps(stored)}
        rules = {'cpu': 'FX3U', 'families': {'C': {'word_value_regions': [region]}}}
        with patch('knowledge.structured_facts._runtime', return_value=(None, None, None, None, {})), \
             patch('knowledge.fact_corpus.resolve', return_value=[source]), \
             patch('plc.device_profiles.device_address_rules', return_value=rules):
            result, = resolve_device_family_records(['C'], plc_model='FX3U')
        packet = json.loads(result['text'])
        self.assertEqual(packet['value'].pop('word_value_regions'), [region])
        self.assertEqual(packet, original)
        self.assertEqual(stored, original)


class QlTranscendentalFamilyTests(unittest.TestCase):
    families = {
        'SIN': ('SIN', 'SINP', 'SIND', 'SINDP'), 'COS': ('COS', 'COSP', 'COSD', 'COSDP'),
        'TAN': ('TAN', 'TANP', 'TAND', 'TANDP'), 'ASIN': ('ASIN', 'ASINP', 'ASIND', 'ASINDP'),
        'ACOS': ('ACOS', 'ACOSP', 'ACOSD', 'ACOSDP'), 'ATAN': ('ATAN', 'ATANP', 'ATAND', 'ATANDP'),
        'RAD': ('RAD', 'RADP', 'RADD', 'RADDP'), 'DEG': ('DEG', 'DEGP', 'DEGD', 'DEGDP'),
        'EXP': ('EXP', 'EXPP', 'EXPD', 'EXPDP'), 'LOG': ('LOG', 'LOGP', 'LOGD', 'LOGDP'),
    }

    @staticmethod
    def facts(opcode, cpu='Q03UDV'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        from plc.instruction_knowledge import compose_programming_facts
        return compose_programming_facts(DEFAULT_INSTRUCTION_REGISTRY, opcode, cpu=cpu, existing=[])[0]

    def test_registered_suffix_forms_keep_real_names_and_target_scope(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry, generation_app_instr_mnemonics
        for base, names in self.families.items():
            for index, name in enumerate(names):
                form = registry.resolve_catalog_form(name, cpu='Q03UDV')
                self.assertEqual((form.base_mnemonic, form.double, form.pulse), (base, index >= 2, index % 2 == 1))
                self.assertTrue(form.spec.supports_cpu('Q03UDV'))
                if index:
                    self.assertFalse(form.spec.supports_cpu('FX3U'))
                if index >= 2:
                    self.assertFalse(form.spec.supports_cpu('Q00J'))
        for invalid in ['DSIN', 'DSINP', 'DLOG', 'DRAD']:
            self.assertIsNone(registry.resolve_catalog_form(invalid))
        permitted = set(generation_app_instr_mnemonics('FX3U'))
        self.assertFalse(permitted & {name for names in self.families.values() for name in names[1:]})

    def test_purposes_units_and_numeric_storage_survive_shared_binding(self):
        for base, names in self.families.items():
            for index, name in enumerate(names):
                facts = self.facts(name)
                tables = [f['value'] for f in facts if f['dimension'] == 'parameters.typed_operands'
                    and f['id'].startswith('assembly:parameters:family:') and f['value'].get('native_form')]
                self.assertEqual(len(tables), 1, (base, name))
                source, destination = tables[0]['operands']
                bits = 64 if index >= 2 else 32
                for operand in [source, destination]:
                    self.assertTrue(operand.get('purpose'), (base, name, operand))
                    self.assertEqual(operand['word_device_layout']['word_count'], bits // 16)
                    self.assertEqual(operand['word_device_layout']['low_word_offset'], 0)
                relations = [f['value'] for f in facts if f['dimension'] == 'effects.bound_relation'
                             and ':ql:transcendental:' in f['id'] and f['value'].get('output') == 'D']
                self.assertTrue(relations, (base, name))
                for relation in relations:
                    value_type = relation['operand_binding']['inputs'][0]['value_type']
                    self.assertEqual((value_type['kind'], value_type['bits']), ('real', bits))
                if base in {'SIN', 'COS', 'TAN', 'DEG'}:
                    self.assertTrue(all(r['input_unit'] == 'radian' for r in relations))
                if base in {'ASIN', 'ACOS', 'ATAN', 'RAD'}:
                    self.assertTrue(all(r['result_unit'] == 'radian' for r in relations))

    def test_conversion_ratios_positive_log_domain_and_inclusive_inverse_domain_are_data_owned(self):
        for opcode in ['RAD', 'RADD', 'DEG', 'DEGD', 'LOG', 'LOGD', 'ASIN', 'ASIND', 'ACOS', 'ACOSD']:
            effects = [f['value'] for f in self.facts(opcode) if f['dimension'] == 'effects.bound_relation'
                       and ':ql:transcendental:' in f['id'] and f['value'].get('output') == 'D']
            effect = effects[0]
            if opcode in {'RAD', 'RADD'}:
                self.assertEqual(effect['scale_factor'], {'numerator': {'mathematical_constant': 'pi'}, 'denominator': 180})
            elif opcode in {'DEG', 'DEGD'}:
                self.assertEqual(effect['scale_factor'], {'numerator': 180, 'denominator': {'mathematical_constant': 'pi'}})
            elif opcode in {'LOG', 'LOGD'}:
                self.assertEqual(effect['operation'], 'evaluate_log')
                self.assertEqual(effect['input_domain'], {'minimum': 0, 'minimum_inclusive': False})
                self.assertEqual(effect['logarithm_base']['mathematical_constant'], 'e')
            else:
                self.assertEqual(effect['input_domain'], {'minimum': -1, 'maximum': 1,
                    'minimum_inclusive': True, 'maximum_inclusive': True})

    def test_cpu_serial_conditions_and_tangent_source_disagreement_are_preserved(self):
        facts = self.facts('SINP', 'Q00J')
        self.assertTrue(any(c.get('kind') == 'serial_number_minimum' and c.get('minimum') == '04122'
                            for f in facts for c in f.get('conditions', [])))
        self.assertTrue(any(f['value'].get('available') is False for f in self.facts('ASIN', 'Q00J')
                            if f['dimension'] == 'constraints.cpu_availability'))
        for opcode in ['TAN', 'TANDP']:
            statements = [f['value'] for f in self.facts(opcode) if f['dimension'] == 'execution.source_disagreement']
            self.assertTrue(statements)
            self.assertTrue(all(v['native_ladder_source_claims_operation_error'] is True
                and v['structured_source_claims_operation_error'] is False for v in statements))
            self.assertTrue(all('operation_error_response_at_tangent_poles' in v['avoid_relying_on'] for v in statements))

    def test_only_the_wide_forms_gain_the_documented_underflow_to_zero_rule(self):
        for opcode, expected in [('SIN', False), ('SIND', True), ('EXP', False), ('EXPD', True)]:
            facts = self.facts(opcode)
            self.assertEqual(any(f['dimension'] == 'execution.boundary_state'
                and f['value'].get('when_result_is_negative_zero_or_underflow') == 'store_positive_zero'
                for f in facts), expected)
            self.assertTrue(any(f['dimension'] == 'execution.disabled_state'
                and f['value'].get('when_not_executed') == 'retain_destination_values' for f in facts))

    def test_modifier_cpu_scopes_are_optional_and_validate_the_literal_form(self):
        from plc.instructions import InstructionRegistry, InstructionSpec
        payload = {'mnemonic': 'EXAMPLE', 'cpu_support': ['FX3U'],
            'modifier_rule': {'double': 'EXAMPLED', 'pulse': True,
                              'cpu_support_by_form': {'EXAMPLED': ['Q03UDV'], 'EXAMPLEP': ['Q03UDV']}}}
        registry = InstructionRegistry([InstructionSpec.from_mapping(payload)])
        self.assertTrue(registry.resolve_form('EXAMPLE').spec.supports_cpu('FX3U'))
        self.assertFalse(registry.resolve_form('EXAMPLED').spec.supports_cpu('FX3U'))
        self.assertTrue(registry.resolve_form('EXAMPLED').spec.supports_cpu('Q03UDV'))
        legacy = copy.deepcopy(payload)
        legacy['modifier_rule'].pop('cpu_support_by_form')
        self.assertTrue(InstructionRegistry([InstructionSpec.from_mapping(legacy)]).resolve_form('EXAMPLED').spec.supports_cpu('FX3U'))
        for scopes in [{'OTHER': ['Q03UDV']}, {'EXAMPLED': []}, {'EXAMPLED': 'Q03UDV'}, {'EXAMPLED': ['Q03UDV', 'q03udv']}]:
            invalid = copy.deepcopy(payload)
            invalid['modifier_rule']['cpu_support_by_form'] = scopes
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                InstructionSpec.from_mapping(invalid)

    def test_model_real_types_do_not_expose_the_internal_integer_signedness_field(self):
        from plc.instruction_definition import ValueType
        from shared.model_context import model_knowledge_value
        for bits in [32, 64]:
            internal = ValueType.from_mapping({'kind': 'real', 'bits': bits})
            self.assertEqual(internal.decode_bits(-0.5), -0.5)
            self.assertEqual(model_knowledge_value(internal.as_mapping()), {'kind': 'real', 'bits': bits})

    def test_source_positive_domain_binds_the_input_and_excludes_zero_in_the_common_checker(self):
        from tools.fact_ql_behavior_review import Section, _numeric_limit_specs
        from plc.instruction_constraints import check_fact_constraints
        class Source:
            manual = 'sh080809engx'
            symbols = ['EXAMPLE']
            symbol_tokens = ['EXAMPLE']
            lines = ['Only positive values can be specified for (s).']
            limits = Section.limits
        declaration = {'dimension': 'parameters.typed_operands', 'reviewed': True, 'value': {'operands': [
            {'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
            {'name': 's', 'type': 'REAL', 'direction': 'input'},
            {'name': 'd', 'type': 'REAL', 'direction': 'output'}]}}
        with patch('tools.fact_ql_behavior_review._section_languages', return_value=['st']):
            spec, = _numeric_limit_specs(Source(), 'EXAMPLE', [declaration])
        self.assertEqual(spec['value']['ranges'], [{'minimum': 0, 'minimum_inclusive': False, 'operand_index': 1}])
        rule = fact('positive', 'constraints.numeric_ranges', value=spec['value'],
                    scope={'models': ['FX3U'], 'forms': ['EXAMPLE'], 'languages': ['st']})
        for number, expected in [(-1.0, 'failed'), (0.0, 'failed'), (-0.0, 'failed'), (0.5, 'passed'),
                                 (float('nan'), 'failed'), (float('inf'), 'failed')]:
            self.assertEqual(check_fact_constraints([rule], {'$1': number}, target='EXAMPLE',
                plc_model='FX3U', language='st')['status'], expected)
        closed = copy.deepcopy(rule)
        closed['value']['ranges'][0].pop('minimum_inclusive')
        self.assertEqual(check_fact_constraints([closed], {'$1': 0.0}, target='EXAMPLE',
            plc_model='FX3U', language='st')['status'], 'passed')

    def test_installed_source_admission_delivers_only_the_correct_cpu_forms(self):
        from plc.instructions import generation_app_instr_mnemonics
        expected = {name for names in self.families.values() for name in names}
        for cpu in ['Q03UDV', 'L02']:
            self.assertTrue(expected <= set(generation_app_instr_mnemonics(cpu)), cpu)
        basic = set(generation_app_instr_mnemonics('Q00J'))
        allowed = {name for base in ['SIN', 'COS', 'TAN', 'RAD', 'DEG', 'EXP', 'LOG']
                   for name in self.families[base][:2]}
        self.assertTrue(allowed <= basic)
        self.assertFalse((expected - allowed) & basic)


class QlRandomFamilyTests(unittest.TestCase):
    families = {'RND': ('RND', 'RNDP'), 'SRND': ('SRND', 'SRNDP')}

    def facts(self, opcode, cpu='Q03UDV'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        entry = registry.definition_entry(opcode, cpu=cpu) or {}
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=entry.get('facts', []))[0]

    def test_serial_and_series_descriptions_are_seed_data_before_generic_counts(self):
        from tools.fact_ql_behavior_review import _parameter_role
        for description in ['Random number serial data', 'Random number series data']:
            self.assertEqual(_parameter_role(description, 'S', 'input'), 'random_seed')
        self.assertEqual(_parameter_role('Number of data items', 'n', 'input'), 'count')

    def test_registered_forms_keep_cpu_scope_and_do_not_invent_double_forms(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for base, forms in self.families.items():
            for index, opcode in enumerate(forms):
                form = registry.resolve_form(opcode, cpu='Q03UDV')
                self.assertEqual(form.base_mnemonic, base)
                self.assertEqual(form.pulse, bool(index))
                self.assertFalse(form.double)
                self.assertTrue(form.spec.supports_cpu('Q03UDV'))
        for opcode in ['DRND', 'DRNDP', 'DSRND', 'SRNDD']:
            self.assertIsNone(registry.resolve_catalog_form(opcode))
        self.assertFalse(registry.resolve_form('RNDP', cpu='FX3S').spec.supports_cpu('FX3S'))
        self.assertFalse(registry.resolve_form('SRND', cpu='FX3U').spec.supports_cpu('FX3U'))

    def test_state_effects_and_one_word_parameter_purposes_survive_binding(self):
        for base, forms in self.families.items():
            for opcode in forms:
                with self.subTest(opcode=opcode):
                    rows = self.facts(opcode)
                    tables = [f['value'] for f in rows if f['dimension'] == 'parameters.typed_operands'
                              and f['id'].startswith('assembly:parameters:family:') and ':ql:random:' in f['id']
                              and f['scope']['languages'] == ['ladder'] and 'native_form' in f['value']]
                    table, = tables
                    parameter, = table['operands']
                    self.assertEqual(parameter['word_device_layout']['word_count'], 1)
                    self.assertEqual(parameter['semantic_role'], 'random_series_data' if base == 'SRND' else 'random_number_destination')
                    effects = [f['value'] for f in rows if f['dimension'] == 'effects.bound_relation'
                               and ':ql:random:' in f['id'] and f['scope']['languages'] == ['ladder']
                               and {'context': 'project_mode', 'equals': 'native_ladder_instruction'} in f.get('conditions', [])]
                    effect, = effects
                    self.assertEqual(effect['state_accesses'][0]['target'], {'kind': 'state', 'parameter': 'random_number_series'})
                    if base == 'RND':
                        self.assertEqual(effect['inputs'], [])
                        self.assertEqual(effect['operand_binding']['output']['parameter'], 'D')
                        self.assertEqual(effect['output_value_range'], {'minimum': 0, 'maximum': 32767})
                    else:
                        self.assertEqual(effect['inputs'], ['S'])
                        self.assertNotIn('output', effect['operand_binding'])
                        self.assertTrue(effect['series_update_changes_generation_pattern'])
                    self.assertNotIn('behavior', effect)

    def test_basic_cpu_serial_conditions_remain_on_the_bound_semantics(self):
        for cpu in ['Q00J', 'Q00', 'Q01']:
            for opcode in ['RND', 'RNDP', 'SRND', 'SRNDP']:
                effects = [f for f in self.facts(opcode, cpu) if f['dimension'] == 'effects.bound_relation' and ':ql:random:' in f['id']]
                self.assertTrue(effects)
                self.assertTrue(all(any(c.get('minimum') == '04122' for c in f['conditions']) for f in effects))

    def test_installed_source_admission_keeps_the_new_calls_in_the_source_models(self):
        from plc.instructions import generation_app_instr_mnemonics
        names = {name for forms in self.families.values() for name in forms}
        for cpu in ['Q03UDV', 'Q00J', 'L02']:
            self.assertTrue(names <= set(generation_app_instr_mnemonics(cpu)))
        self.assertFalse({'SRND', 'SRNDP'} & set(generation_app_instr_mnemonics('FX3U')))


class FxRandomFamilyTests(unittest.TestCase):
    @staticmethod
    def facts(opcode, cpu='FX3U'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        entry = registry.definition_entry(opcode, cpu=cpu) or {}
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=entry.get('facts', []))[0]

    def test_native_parameters_and_state_differences_use_the_shared_random_family(self):
        for cpu, minimum in [('FX3U', '2.20'), ('FX3UC', '1.00')]:
            for opcode in ['RND', 'RNDP']:
                with self.subTest(cpu=cpu, opcode=opcode):
                    facts = self.facts(opcode, cpu)
                    table, = [f for f in facts if f['id'].startswith('assembly:parameters:family:')
                        and ':fx3:random:' in f['id'] and f['dimension'] == 'parameters.typed_operands'
                        and 'native_form' in f['value']]
                    parameter, = table['value']['operands']
                    self.assertEqual(parameter['semantic_role'], 'random_number_destination')
                    self.assertEqual(parameter['direction'], 'output')
                    self.assertEqual(parameter['word_device_layout']['word_count'], 1)
                    self.assertEqual(parameter['range']['address_forms'], ['buffer_memory'])
                    self.assertEqual(parameter['range']['address_form_limits'], {
                        'buffer_memory': {'maximum_base_address': 32766}})
                    self.assertIn({'context': 'cpu_version', 'minimum': minimum}, table['conditions'])
                    relation, = [f['value'] for f in facts if f['dimension'] == 'effects.bound_relation'
                                  and ':fx3:random:' in f['id']]
                    self.assertEqual(relation['inputs'], [])
                    self.assertEqual(relation['operand_binding']['output']['parameter'], 'D')
                    self.assertEqual(relation['state_bindings']['random_number_series']['devices'], ['D8310', 'D8311'])
                    self.assertEqual(relation['state_bindings']['random_number_series']['word_device_layout']['word_count'], 2)
                    self.assertEqual(relation['output_value_range'], {'minimum': 0, 'maximum': 32767})
                    self.assertEqual(relation['state_initialization']['user_initial_value'], {
                        'minimum': 0, 'maximum': 2147483647})
                    self.assertEqual(relation['state_initialization']['power_restored_initial_value'], 1)
                    self.assertEqual(relation['state_initialization']['user_write_timing'],
                                     'once_when_PLC_switches_from_STOP_to_RUN')
                    self.assertNotIn('behavior', relation)
                    self.assertTrue(any(f['dimension'] == 'execution.disabled_state'
                        and f['value'].get('when_not_executed') == 'retain_destination_values' for f in facts))
                    self.assertFalse(any('exact_random_number_formula_and_sequence' in
                        f['value'].get('unresolved_items', []) for f in facts if ':fx3:random:' in f['id']))
        for cpu in ['Q03UDV', 'L02']:
            self.assertFalse(any(f['value'].get('state_bindings') for f in self.facts('RND', cpu)))
        for cpu in ['FX3S', 'FX3G', 'FX3GC']:
            self.assertTrue(any(f['dimension'] == 'constraints.cpu_availability'
                and f['value'].get('available') is False for f in self.facts('RNDP', cpu)))
            self.assertFalse(any(':fx3:random:' in f['id'] and f['dimension'].startswith('effects.')
                for f in self.facts('RNDP', cpu)))

    def test_source_formula_matches_independent_word_arithmetic(self):
        from hypothesis import given, settings, strategies as st
        from plc.instruction_definition import evaluate_expression
        relation, = [f['value'] for f in self.facts('RND')
            if f['dimension'] == 'effects.bound_relation' and ':fx3:random:' in f['id']]
        update = relation['state_updates']['random_number_series']
        output = relation['result_expression']

        @settings(max_examples=128, deadline=None, database=None, derandomize=True)
        @given(st.integers(min_value=0, max_value=0xffffffff))
        def check(seed):
            # Independent 16-bit partial products and carry, rather than
            # evaluating the authored AST with the same whole-word formula.
            low, high = seed & 0xffff, seed >> 16
            low_product = low * 0x4e6d + 0x3039
            expected_low = low_product & 0xffff
            expected_high = (high * 0x4e6d + low * 0x41c6 + (low_product >> 16)) & 0xffff
            actual = evaluate_expression(update, {}, state={'random_number_series': seed})
            self.assertEqual((actual & 0xffff, actual >> 16), (expected_low, expected_high))
            result = evaluate_expression(output, {}, state={'random_number_series_after_update': actual})
            self.assertEqual(result, expected_high % 32768)
            self.assertTrue(0 <= result <= 32767)
        check()

    def test_reviewed_buffer_limits_stay_objects_and_require_the_device_receipt(self):
        from dataclasses import replace
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry, OperandSpec
        from knowledge.instruction_compiler import _legacy_facts
        for cpu in ['FX3U', 'FX3UC']:
            for opcode in ['RND', 'RNDP']:
                form = registry.resolve_catalog_form(opcode, cpu=cpu)
                devices, = [f for f in _legacy_facts(form, cpu) if f['dimension'] == 'parameters.devices']
                encoded = json.loads(json.dumps(devices['value']))
                self.assertEqual(encoded['address_forms'], [['buffer_memory']])
                self.assertEqual(encoded['address_form_limits'], [{'buffer_memory': {'maximum_base_address': 32766}}])
                unreviewed = replace(form, spec=replace(form.spec, verified_fields=tuple(
                    f for f in form.spec.verified_fields if f != 'device_classes')))
                weak, = [f for f in _legacy_facts(unreviewed, cpu) if f['dimension'] == 'parameters.devices']
                self.assertNotIn('address_forms', weak['value'])
                self.assertNotIn('address_form_limits', weak['value'])
        for limits in [
            {'buffer_memory': {'maximum_base_address': -1}},
            {'buffer_memory': {'maximum_base_address': 32768}},
            {'buffer_memory': {'maximum_base_address': True}},
            {'buffer_memory': {'maximum_base_address': '32766'}},
            {'buffer_memory': {'maximum_base_address': 32766, 'extra': 1}},
            {'other_form': {'maximum_base_address': 32766}},
        ]:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                OperandSpec.from_mapping({'name': 'D', 'role': 'write', 'data_type': 'word',
                    'address_forms': ['buffer_memory'], 'address_form_limits': limits}, 0)

    def test_exclusive_cpu_version_map_preserves_its_real_boundary(self):
        from plc.instruction_constraints import _condition, version_condition_bounds
        from plc.instruction_definition import UnknownInstructionSemantics
        rule = {'context': 'cpu_version', 'minimum_exclusive_by_model': {'FXU': '2.30'}}
        for model, version, expected in [
                ('FXU', '2.29', False), ('FXU', '2.30', False),
                ('FXU', '2.30.0', False), ('FXU', '2.30.1', True),
                ('FXU', '2.31', True), ('FXU', None, None),
                ('FX3U', '2.31', None), (None, '2.31', None)]:
            with self.subTest(model=model, version=version):
                context = {'$PLC_MODEL': model}
                if version is not None:
                    context['cpu_version'] = version
                if expected is None:
                    with self.assertRaises(UnknownInstructionSemantics):
                        _condition(rule, {}, context)
                else:
                    self.assertIs(_condition(rule, {}, context), expected)
        for malformed in [
                {'FXU': '2.30', 'FX3U': 'invalid'},
                {'FXU': 2.30}, {}, {None: '2.30'}]:
            with self.subTest(malformed=malformed), self.assertRaises(UnknownInstructionSemantics):
                version_condition_bounds(dict(rule, minimum_exclusive_by_model=malformed), model='FXU')
        with self.assertRaises(UnknownInstructionSemantics):
            version_condition_bounds(dict(rule, minimum='2.30'), model='FXU')

    def test_version_guards_bind_signatures_only_with_sufficient_context(self):
        from plc.instruction_constraints import check_fact_constraints, bind_operand_signature, version_condition_bounds
        from plc.instruction_definition import UnknownInstructionSemantics
        for cpu, minimum, lower in [('FX3U', '2.20', '2.19'), ('FX3UC', '1.00', '0.99')]:
            for opcode in ['RND', 'RNDP']:
                facts = self.facts(opcode, cpu)
                for version, expected in [(minimum, 'passed'), (minimum + '.0', 'passed'),
                                           (lower, 'failed'), (None, 'unverified')]:
                    context = {'project_mode': 'native_ladder_instruction'}
                    if version is not None:
                        context['cpu_version'] = version
                    report = check_fact_constraints(facts, {}, target=opcode, plc_model=cpu,
                        language='ladder', context=context)
                    states = [c['status'] for c in report['checks']
                              if c.get('dimension') == 'constraints.cpu_availability']
                    self.assertIn(expected, states, (cpu, opcode, version, states))
                    if expected != 'failed':
                        self.assertNotIn('failed', states)
                    bound = bind_operand_signature(facts, target=opcode, plc_model=cpu,
                        language='ladder', context=context)
                    self.assertEqual(bound is not None, expected == 'passed', (cpu, opcode, version))
        for condition in [
            {'context': 'cpu_version', 'minimum': 2.2},
            {'context': 'cpu_version', 'minimum': '2..20'},
            {'context': 'cpu_version', 'minimum': '2.20', 'maximum_exclusive': '2.20'},
            {'context': 'cpu_version', 'minimum_by_model': {'FX3UC': '1.00'}},
            {'context': 'cpu_version', 'minimum': '2.20', 'minimum_by_model': {'FX3U': '2.20'}}]:
            with self.subTest(condition=condition), self.assertRaises(UnknownInstructionSemantics):
                version_condition_bounds(condition, model='FX3U')

    def test_reviewed_admission_requires_an_exact_complementary_version_guard(self):
        from knowledge.instruction_compiler import refresh_reviewed_call_admission
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for cpu in ['FX3U', 'FX3UC']:
            for opcode in ['RND', 'RNDP']:
                facts = self.facts(opcode, cpu)
                good = {'target_model': cpu, 'opcode': opcode, 'facts': facts, 'call_available': False}
                self.assertTrue(refresh_reviewed_call_admission(good, registry), (cpu, opcode))
                self.assertEqual(good['call_admission_details']['applicability'], 'conditional')
                for negative in [
                    [{'context': 'cpu_version', 'maximum_exclusive': '9.99'}],
                    [{'context': 'cpu_version', 'minimum': '2.20'}],
                    [{'context': 'cpu_version', 'maximum_exclusive': 2.2}],
                    [],
                ]:
                    bad = copy.deepcopy(good)
                    bad['call_available'] = False
                    for f in bad['facts']:
                        if f['dimension'] == 'constraints.cpu_availability' and f['value'].get('available') is False:
                            f['conditions'] = negative
                    self.assertFalse(refresh_reviewed_call_admission(bad, registry), (cpu, opcode, negative))
                    self.assertFalse(bad['call_available'])

    def test_installed_admission_preserves_the_real_fx_model_and_form_scope(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry, generation_app_instr_mnemonics
        for cpu in ['FX3U', 'FX3UC']:
            self.assertTrue({'RND', 'RNDP'} <= set(generation_app_instr_mnemonics(cpu)))
            for opcode in ['RND', 'RNDP']:
                entry = registry._definition_owner_store.get_entry(opcode, cpu=cpu)
                self.assertTrue(entry['call_available'])
                self.assertEqual(entry['call_admission_details']['applicability'], 'conditional')
            self.assertFalse({'SRND', 'SRNDP', 'DRND', 'DRNDP'} & set(generation_app_instr_mnemonics(cpu)))
        for cpu in ['FX3S', 'FX3G', 'FX3GC']:
            self.assertFalse({'RND', 'RNDP'} & set(generation_app_instr_mnemonics(cpu)))


class QlSourceParameterCorrectionTests(unittest.TestCase):
    def test_native_byte_exchange_reads_and_writes_the_same_operand(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _native_direction
        section = SimpleNamespace(chapter='', title='Byte exchange',
                                  symbol_tokens=['SWAP(P)'])
        for symbol, name, count, expected in [
                ('SWAP', 'D', 1, 'in_out'), ('SWAPP', 'D', 1, 'in_out'),
                ('SWAPPP', 'D', 1, 'output'), ('MOV', 'S', 2, 'input'),
                ('MOV', 'D', 2, 'output')]:
            with self.subTest(symbol=symbol, name=name):
                self.assertEqual(_native_direction(section, symbol, name,
                    '16-bit data', 'destination_data', count), expected)

    def test_exclusive_logic_statements_keep_their_own_operation(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _generic_specs
        for operation, wording, bits in [
                ('xor', 'exclusive logical sum (XOR)', 16),
                ('xor', 'exclusive logical sum (XOR)', 32),
                ('xor', 'exclusive OR operation', 16),
                ('or', 'logical sum (OR)', 16),
                ('or', 'OR operation', 32),
                ('and', 'logical product (AND)', 16),
                ('xnor', 'exclusive NOR operation', 16),
                ('not', 'NOT operation', 16)]:
            with self.subTest(operation=operation, wording=wording, bits=bits):
                statement = f'Performs the {wording} on {bits}-bit data in (S) and (D).'
                section = SimpleNamespace(title='Synthetic logic',
                    statements=lambda symbol: [statement])
                result, = _generic_specs(section, 'SYNTH', 'synthetic')
                self.assertEqual(result['dimension'], 'effects.logic')
                self.assertEqual(result['value']['operation'], operation)
                self.assertEqual(result['value']['width_bits'], bits)

    def test_literal_operator_references_survive_the_publication_filter(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _link_n_behavior_references

        for symbol, page, reference_page in [('*', 209, 130), ('/', 211, 134)]:
            with self.subTest(symbol=symbol):
                operator = SimpleNamespace(manual='sh080784engn', revision='N',
                    has_instruction_chunk=True, pages=[page], symbols=[symbol])
                reference = SimpleNamespace(manual='sh080784engn', revision='N',
                    has_instruction_chunk=True, pages=[reference_page], symbols=['REFERENCE_E'])
                foreign = SimpleNamespace(manual='sh080783engp', revision='P',
                    has_instruction_chunk=True, pages=[reference_page], symbols=['OTHER'])

                _link_n_behavior_references({'operator': operator, 'foreign': foreign})
                self.assertIsNone(operator._n_reference)
                sections = {'operator': operator, 'reference': reference, 'foreign': foreign}
                _link_n_behavior_references(sections)
                self.assertIs(operator._n_reference, reference)

                selected = {key: section for key, section in sections.items()
                            if symbol in section.symbols}
                _link_n_behavior_references(selected)
                self.assertEqual(list(selected), ['operator'])
                self.assertIs(operator._n_reference, reference)

    def test_native_word_device_numbers_keep_their_identity_and_storage_type(self):
        from tools.fact_ql_behavior_review import _native_data_type
        for category, device_class in [('Timer', 'T'), ('Counter', 'C')]:
            with self.subTest(category=category):
                value = _native_data_type(category + ' number (word)')
                self.assertEqual(value, {'type': 'WORD', 'device_class': device_class,
                                         'passing': 'device_reference'})
                self.assertEqual(_native_data_type(category + ' number')['type'], 'BOOL')
                self.assertEqual(_native_data_type(category + ' number (word)', 'BIT')['type'], 'BOOL')

    def test_conflicting_table_role_is_preserved_beside_the_named_prose_count(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import (
            _materialize, _parameter_p_specs, _parameter_prose_role_bindings,
        )
        from shared.model_context import technical_block, technical_context_units
        body = 'The number of characters specified for limit controls the result span.'
        rows = [
            {'operand': {'name': name, 'type': kind, 'direction': direction, 'role': role},
             'description': description, 'witness': name + ' ' + description + ' ' + kind}
            for name, kind, direction, role, description in [
                ('EN', 'BOOL', 'input', 'enable', 'Execution condition'),
                ('ENO', 'BOOL', 'output', 'execution_status', 'Execution result'),
                ('s', 'STRING', 'input', 'source_data', 'Source device'),
                ('limit', 'ANY16', 'input', 'execution_status', 'Execution result'),
                ('d', 'STRING', 'output', 'destination_data', 'Destination device')]]
        original = copy.deepcopy(rows)
        table_witnesses = {row['witness'] for row in rows} | {'SYNTH(P)'}
        usage = 'number of characters specified for limit'
        def bind(witness):
            return [('same-chunk', 7)] if witness in table_witnesses else [('same-chunk', 8)] if witness == usage else []
        section = SimpleNamespace(manual='sh080783engp', revision='P', manual_number='SH080783ENG',
            kind='structured', title='Synthetic conversion', heading_line='SYNTH(P)', text=body,
            symbols=['SYNTH', 'SYNTHP'], symbol_tokens=['SYNTH(P)'], pages=[7, 8], bind=bind)
        contract = {'rows': rows, 'allowed': section.symbols, 'calls': [
            {'name': 'SYNTH', 'arguments': ['EN', 's', 'limit', 'd']}], 'issues': [],
            'block_forms': {'SYNTH': ['EN', 'ENO', 's', 'd', 'limit']}, 'st_available': True}
        conflicts = _parameter_prose_role_bindings(section, contract)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(rows[3]['description'], 'Execution result')
        self.assertEqual(rows[3]['role_binding']['disagreement']['table_declaration']['operand'], original[3]['operand'])
        self.assertEqual(rows[3]['operand']['role'], 'count')
        self.assertEqual(rows[3]['operand']['purpose'], {'semantic_role': 'count', 'unit': 'characters'})
        self.assertEqual(rows[1], original[1])
        section._parameter_contracts = [contract]
        facts, missing, _ = _materialize(section, 'SYNTH', _parameter_p_specs(section, 'SYNTH'),
                                        'Q03UDV', verified=True)
        self.assertEqual(missing, [])
        declarations = [f for f in facts if f['dimension'] == 'parameters.source_description_disagreement']
        self.assertEqual(len(declarations), 2)
        for declaration in declarations:
            self.assertEqual(declaration['status'], 'conflict')
            self.assertEqual(declaration['value']['table_declaration']['description_kind'], 'execution_result')
            self.assertEqual(declaration['value']['prose_usage'], {'role': 'count', 'count_unit': 'characters'})
            self.assertEqual({page for source in declaration['sources'] for page in source['pdf_pages']}, {7, 8})
        tables = [f for f in facts if f['dimension'] == 'parameters.typed_operands']
        self.assertEqual(len(tables), 3)
        for table in tables:
            pin, = [p for p in table['value']['operands'] if p['name'] == 'limit']
            self.assertEqual((pin['direction'], pin['role'], pin['count_unit']), ('input', 'count', 'characters'))
            self.assertEqual(table['status'], 'source_verified')
        source_packet = {'kind': 'instruction', 'target': 'SYNTH', 'value': {'facts': facts}}
        technical = technical_fact_packet(source_packet)
        encoded = json.dumps(technical)
        self.assertIn('execution_result', encoded)
        self.assertIn('characters', encoded)
        self.assertIn('effective_role_bound_to_explicit_named_prose_usage', encoded)
        self.assertNotIn('unknow', encoded.lower())
        selected = project_packet(source_packet, plc_model='Q03UDV', language='st')
        rendered = technical_context_units(technical_block(technical_fact_packet(selected)))
        model_encoded = json.dumps(rendered)
        self.assertIn('characters', model_encoded)
        self.assertNotIn('execution_result', model_encoded)
        self.assertNotIn('effective_role_bound_to_explicit_named_prose_usage', model_encoded)
        effective, = [parameter for unit in rendered
                      for table in unit['value']['parameter_tables']
                      for parameter in table['parameters'] if parameter.get('name') == 'limit']
        self.assertEqual(effective['type'], 'ANY16')
        self.assertEqual(effective['purpose'], {'semantic_role': 'count', 'unit': 'characters'})
        before = copy.deepcopy(contract)
        self.assertEqual(_parameter_prose_role_bindings(section, contract), [])
        self.assertEqual(contract, before)

    def test_ordinary_execution_results_and_unbound_usage_do_not_get_relabelled(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _parameter_prose_role_bindings
        cases = [
            ('input', 'limit', 'The execution result is used by the caller.', True),
            ('output', 'limit', 'The number of characters specified for limit controls the span.', True),
            ('input', 'EN', 'The number of characters specified for EN controls the span.', True),
            ('input', 'limit', 'The number of characters specified for limit2 controls the span.', True),
            ('input', 'limit', 'The number of characters specified for limit controls the span.', False),
            ('input', 'limit', 'The number of characters specified for limit differs from the number of words specified for limit.', True),
        ]
        for direction, name, text, bound in cases:
            with self.subTest(direction=direction, name=name, text=text, bound=bound):
                contract = {'rows': [{'operand': {'name': name, 'type': 'ANY16', 'direction': direction,
                                                'role': 'execution_status'},
                                     'description': 'Execution result', 'witness': 'table row'}],
                            'source_array_rows': [{'n': 1, 'bit_points': 1}]}
                before = copy.deepcopy(contract)
                section = SimpleNamespace(text=text, bind=lambda witness: [(1, 7)] if bound else [])
                self.assertEqual(_parameter_prose_role_bindings(section, contract), [])
                self.assertEqual(contract, before)


class ReviewedStorageAdmissionTests(unittest.TestCase):
    def native_case(self, *, data_type='word', word_count=1):
        from plc.instructions import InstructionRegistry
        cpu, opcode = 'Q03UDV', 'SYNTH_STORAGE'
        fields = ['form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types']
        contract = {'method': 'reviewed-manual-contract-v1', 'cpus': [cpu],
            'instruction': {'mnemonic': opcode, 'cpu_support': [cpu], 'contract_level': 'signature',
                'arity': {'min': 2, 'max': 2}, 'operands': [
                    {'name': 'D', 'role': 'write', 'data_type': data_type, 'word_count': word_count},
                    {'name': 'n', 'role': 'read', 'data_type': 'word', 'word_count': 1}]},
            'native_order': ['D', 'n'], 'verified_fields': fields,
            'sources': [{'models': [cpu], 'manual_id': 'synthetic_storage', 'manual': 'Synthetic storage',
                         'revision': 'A', 'pdf_page': 7, 'url': 'https://example.invalid/storage'}],
            'execution_form': 'continuous'}
        registry = InstructionRegistry(); registry.load_target_contracts([contract])
        scope = {'models': [cpu], 'forms': [opcode], 'languages': ['ladder']}
        def source_fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value, 'scope': copy.deepcopy(scope),
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic_storage', 'revision': 'A', 'pdf_pages': [7]}]}
        entry = {'target_model': cpu, 'opcode': opcode, 'call_available': False, 'gaps': ['effects_not_formalized'],
            'facts': [source_fact('cpu', 'constraints.cpu_availability', {'available': True}),
                source_fact('parameters', 'parameters.typed_operands', {
                    'representation': 'native_ladder_instruction', 'operands': [
                        {'name': 'D', 'type': 'ANY16', 'direction': 'output'},
                        {'name': 'n', 'type': 'ANY16', 'direction': 'input'}]})]}
        return registry, entry

    def admission(self, entry, registry):
        from knowledge.instruction_compiler import refresh_reviewed_call_admission
        candidate = copy.deepcopy(entry)
        result = refresh_reviewed_call_admission(candidate, registry)
        self.assertIs(candidate['call_available'], result)
        self.assertEqual(candidate['gaps'], ['effects_not_formalized'])
        return result

    def test_reviewed_admission_refreshes_only_verified_registry_metadata(self):
        from knowledge.instruction_compiler import refresh_reviewed_call_admission, _legacy_facts
        registry, entry = self.native_case()
        form = registry.resolve_catalog_form(entry['opcode'], cpu=entry['target_model'])
        legacy = _legacy_facts(form, entry['target_model'])
        stale = copy.deepcopy(next(f for f in legacy if f['dimension'] == 'parameters.roles'))
        stale.update(status='declared_unverified', verification='not_performed')
        stale['value'] = {'roles': ['read']}
        unreviewed = copy.deepcopy(next(f for f in legacy if f['dimension'] == 'parameters.devices'))
        unreviewed['value'] = {'prefixes': [['D']]}
        entry['facts'].extend([stale, unreviewed])
        originals = copy.deepcopy(entry['facts'][:2])
        rejected = copy.deepcopy(entry)
        rejected['facts'][1]['value']['operands'][0]['type'] = 'unknown'
        before = copy.deepcopy(rejected)
        self.assertFalse(refresh_reviewed_call_admission(rejected, registry))
        self.assertEqual(rejected, before)
        self.assertTrue(refresh_reviewed_call_admission(entry, registry))
        refreshed = next(f for f in entry['facts'] if f['id'] == stale['id'])
        self.assertEqual(refreshed['value']['roles'], ['write', 'read'])
        self.assertEqual(refreshed['status'], 'source_verified')
        self.assertIn(stale, entry['superseded_facts'])
        self.assertEqual(entry['facts'][:2], originals)
        self.assertIn(unreviewed, entry['facts'])

    def test_independent_word_records_cannot_be_admitted_as_a_wide_scalar(self):
        registry, entry = self.native_case(word_count=2)
        pin = entry['facts'][1]['value']['operands'][0]
        pin.update(passing='device_reference', word_device_layout={
            'kind': 'independent_elements', 'single_scalar': False, 'device_unit_bits': 16,
            'element_bits': 16, 'element_count': 2, 'word_count': 2})
        self.assertTrue(self.admission(entry, registry))
        scalar_registry, _ = self.native_case(data_type='dword', word_count=2)
        self.assertFalse(self.admission(entry, scalar_registry))
        for mutation in ['missing_layout', 'missing_passing', 'scalar_layout', 'missing_element_count',
                         'wrong_element_count', 'wrong_element_bits', 'unreviewed_source', 'wrong_direction']:
            altered = copy.deepcopy(entry); item = altered['facts'][1]['value']['operands'][0]
            if mutation == 'missing_layout': item.pop('word_device_layout')
            elif mutation == 'missing_passing': item.pop('passing')
            elif mutation == 'scalar_layout': item['word_device_layout']['single_scalar'] = True
            elif mutation == 'missing_element_count': item['word_device_layout'].pop('element_count')
            elif mutation == 'wrong_element_count': item['word_device_layout']['element_count'] = 3
            elif mutation == 'wrong_element_bits': item['word_device_layout']['element_bits'] = 32
            elif mutation == 'unreviewed_source':
                altered['facts'][1].update(status='declared_unverified', verification='not_performed')
            else: item['direction'] = 'input'
            with self.subTest(mutation=mutation):
                self.assertFalse(self.admission(altered, registry))

    def test_selected_compilation_persists_the_refreshed_registry_roles(self):
        from pathlib import Path
        import tempfile
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        from scripts.compile_instruction_semantics import compile_semantics
        from knowledge.instruction_compiler import _legacy_facts
        registry, entry = self.native_case()
        entry['vendor'] = 'mitsubishi'
        form = registry.resolve_catalog_form(entry['opcode'], cpu=entry['target_model'])
        stale = copy.deepcopy(next(f for f in _legacy_facts(form, entry['target_model'])
                                  if f['dimension'] == 'parameters.roles'))
        stale.update(status='declared_unverified', verification='not_performed')
        stale['value'] = {'roles': ['read']}
        entry['facts'].append(stale)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = OwnerStoreWriter(root / 'input.sqlite',
                {k: v for k, v in fixture().items() if k != 'entries'})
            writer.add_entry(entry)
            manifest = root / 'instruction_definitions.manifest.json'
            writer.finalize(manifest)
            with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', registry):
                result = compile_semantics(manifest, root / 'output', version='synthetic:reviewed-refresh',
                    source_batches={(entry['target_model'], entry['opcode']): []},
                    source_owners_only=True, refresh_reviewed_calls=True)
            output = OwnerDefinitionStore(result['manifest'])
            try:
                saved = output.get_entry(entry['opcode'], cpu=entry['target_model'])
                roles = next(f for f in saved['facts'] if f['id'] == stale['id'])
                self.assertEqual(roles['value']['roles'], ['write', 'read'])
                self.assertIn(stale, saved['superseded_facts'])
                self.assertTrue(saved['call_available'])
                self.assertEqual(result['reviewed_call_contracts_refreshed'], 1)
            finally:
                output.close()

    def test_failed_native_refresh_withdraws_legacy_admission_in_selected_compile(self):
        from pathlib import Path
        import tempfile
        from test_instruction_owner_store import fixture
        from knowledge.runtime_fact_publication import OwnerStoreWriter
        from plc.instruction_owner_store import OwnerDefinitionStore
        from scripts.compile_instruction_semantics import compile_semantics
        from plc.ordered_source import validate_ordered_rung
        registry, entry = self.native_case()
        entry.update(vendor='mitsubishi', call_available=True, support_status='existing_declared_scope')
        entry['facts'][1]['value']['operands'][0]['type'] = 'unknown'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = OwnerStoreWriter(root / 'input.sqlite',
                {k: v for k, v in fixture().items() if k != 'entries'})
            writer.add_entry(entry)
            manifest = root / 'instruction_definitions.manifest.json'
            writer.finalize(manifest)
            with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', registry):
                result = compile_semantics(manifest, root / 'output', version='synthetic:withdraw-native',
                    source_batches={(entry['target_model'], entry['opcode']): []},
                    source_owners_only=True, refresh_reviewed_calls=True)
            output = OwnerDefinitionStore(result['manifest'])
            try:
                saved = output.get_entry(entry['opcode'], cpu=entry['target_model'])
                self.assertFalse(saved['call_available'])
                self.assertEqual(saved['call_admission_details']['interfaces'], [])
                self.assertEqual(saved['call_admission_reason'], 'native_signature_not_admitted')
                self.assertEqual(result['unqualified_native_admissions_withdrawn'], 1)
                source = next(f for f in saved['facts'] if f['id'] == 'parameters')
                self.assertEqual(source['value']['operands'][0]['type'], 'unknown')
                registry._definition_owners[('mitsubishi', entry['opcode'], entry['target_model'])] = saved
                with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY', registry):
                    with self.assertRaisesRegex(ValueError, entry['opcode']):
                        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [
                            {'op': entry['opcode'], 'args': ['D0', 'D10']}]}, plc_model=entry['target_model'])
            finally:
                output.close()

    def test_native_withdrawal_preserves_only_separately_admitted_interfaces(self):
        from knowledge.instruction_compiler import withdraw_native_call_admission
        for marker, interface, status in (
            ('structured_call', 'st', 'reviewed_st_signature'),
            ('structured_expression', 'st', 'reviewed_st_expression'),
            ('fbd_call', 'fbd', 'reviewed_fbd_signature'),
        ):
            with self.subTest(marker=marker):
                entry = {'call_available': True, 'support_status': 'reviewed_native_signature',
                    'facts': [fact('kept', 'constraints.cpu_availability')],
                    'call_admission_details': {'interfaces': ['ladder', interface],
                        marker: {'available': True, 'graphical_interface_verified': True},
                        'applicability_cases': [[{'context': 'native_only'}]]}}
                source = copy.deepcopy(entry['facts'])
                self.assertTrue(withdraw_native_call_admission(entry))
                self.assertTrue(entry['call_available'])
                self.assertEqual(entry['support_status'], status)
                self.assertEqual(entry['call_admission_details']['interfaces'], [interface])
                self.assertNotIn('applicability_cases', entry['call_admission_details'])
                self.assertEqual(entry['facts'], source)
                entry['call_admission_details'][marker]['available'] = False
                withdraw_native_call_admission(entry)
                self.assertFalse(entry['call_available'])
                self.assertEqual(entry['call_admission_details']['interfaces'], [])

    def test_mixed_storage_heads_need_complete_positive_counts_and_bound_references(self):
        registry, entry = self.native_case(data_type='any')
        pin = entry['facts'][1]['value']['operands'][0]
        pin.update(type='DEVICE_REFERENCE', passing='device_reference')
        self.assertFalse(self.admission(entry, registry))
        for kind in ['DEVICE_REFERENCE', 'ANY_SIMPLE']:
            for count in [8, {'op': 'shift_left', 'type': {'kind': 'int', 'bits': 32}, 'args': [
                    {'op': 'constant', 'type': {'kind': 'int', 'bits': 32}, 'value': 1},
                    {'op': 'parameter', 'type': {'kind': 'int', 'bits': 16}, 'name': 'n'}]}]:
                good = copy.deepcopy(entry)
                good['facts'][1]['value']['operands'][0].update(type=kind, occupied_bit_count=count)
                with self.subTest(kind=kind, count=count):
                    self.assertTrue(self.admission(good, registry))
        for count in [True, False, 0, -1, {}, {'not_a_region': 8},
                      {'op': 'constant', 'type': {'kind': 'bool'}, 'value': True}]:
            altered = copy.deepcopy(entry)
            altered['facts'][1]['value']['operands'][0]['occupied_bit_count'] = count
            with self.subTest(count=count):
                self.assertFalse(self.admission(altered, registry))

    def test_string_heads_need_reviewed_word_layout_and_matching_direction(self):
        registry, entry = self.native_case()
        pin = entry['facts'][1]['value']['operands'][0]
        pin.update(type='STRING', passing='device_reference',
                   word_device_layout={'device_unit_bits': 16, 'word_count': 2})
        self.assertTrue(self.admission(entry, registry))
        for mutation in ['missing_layout', 'missing_count', 'missing_reference', 'wrong_unit',
                         'unreviewed_source', 'wrong_direction', 'wrong_page']:
            altered = copy.deepcopy(entry); item = altered['facts'][1]['value']['operands'][0]
            if mutation == 'missing_layout': item.pop('word_device_layout')
            elif mutation == 'missing_count': item['word_device_layout'].pop('word_count')
            elif mutation == 'missing_reference': item.pop('passing')
            elif mutation == 'wrong_unit': item['word_device_layout']['device_unit_bits'] = 32
            elif mutation == 'unreviewed_source':
                altered['facts'][1].update(status='declared_unverified', verification='not_performed')
            elif mutation == 'wrong_direction': item['direction'] = 'input'
            else: altered['facts'][1]['sources'][0]['pdf_pages'] = [8]
            with self.subTest(mutation=mutation):
                self.assertFalse(self.admission(altered, registry))
        for count in [True, 0, -1, {}, {'word_count': 2}]:
            altered = copy.deepcopy(entry)
            altered['facts'][1]['value']['operands'][0]['word_device_layout']['word_count'] = count
            with self.subTest(count=count):
                self.assertFalse(self.admission(altered, registry))


    def test_character_storage_literals_require_the_same_explicit_native_widths(self):
        from dataclasses import replace
        registry, entry = self.native_case()
        form = registry.resolve_catalog_form('SYNTH_STORAGE', cpu='Q03UDV')
        source = replace(form.spec.operands[1], integer_literal_widths=(('H', 16), ('K', 16)))
        literal_form = replace(form, spec=replace(form.spec, operands=(form.spec.operands[0], source)))
        pin = entry['facts'][1]['value']['operands'][1]
        pin.update(type='STRING', word_device_layout={'device_unit_bits': 16, 'word_count': 1},
                   range={'integer_literal_widths': {'K': 16, 'H': 16}})
        with patch.object(registry, 'resolve_catalog_form', return_value=literal_form):
            self.assertTrue(self.admission(entry, registry))
            for widths in [{}, {'K': 32, 'H': 16}, {'K': 16}]:
                altered = copy.deepcopy(entry)
                altered['facts'][1]['value']['operands'][1]['range']['integer_literal_widths'] = widths
                self.assertFalse(self.admission(altered, registry))


class NativeStringOperandBranchTests(unittest.TestCase):
    def contract(self, *, reviewed=True, literal=True, device=True):
        from dataclasses import replace
        from plc.instructions import InstructionSpec
        from plc.instruction_definition import InstructionFactGroup
        declaration = fact('synthetic-string-native', 'parameters.typed_operands',
            scope={'models': ['Q03UDV'], 'forms': ['SYNTH_STRING'], 'languages': ['ladder']},
            conditions=[{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
            value={'representation': 'native_ladder_instruction', 'operand_count': 2,
                   'operands': [
                {'name': 'S', 'type': 'STRING', 'direction': 'input', 'passing': 'device_reference',
                 'range': {'literal_prefixes': ['$'] if literal else [],
                           'device_prefixes': ['D'] if device else []}},
                {'name': 'D', 'type': 'STRING', 'direction': 'output', 'passing': 'device_reference',
                 'range': {'literal_prefixes': [], 'device_prefixes': ['D']}}]})
        if not reviewed:
            declaration.update(status='declared_unverified', verification='not_performed')
        spec = InstructionSpec.from_mapping({
            'mnemonic': 'SYNTH_STRING', 'arity': {'min': 2, 'max': 2},
            'cpu_support': ['Q03UDV'], 'operands': [
                {'name': 'S', 'role': 'read', 'data_type': 'word'},
                {'name': 'D', 'role': 'write', 'data_type': 'word'}]})
        return replace(spec, native_operand_order=('S', 'D'),
            verified_fields=('arity', 'operand_order', 'operand_roles', 'operand_types', 'device_classes'),
            cpu_support=frozenset({'Q03UDV'}),
            definition_facts=(InstructionFactGroup.from_mapping(declaration),))

    def test_only_reviewed_string_branches_accept_literals_or_word_heads(self):
        from plc.validation import _validate_source_operand_types, PLCJsonValidationError
        cases = [(['D0', 'D100'], True), (['"ABC"', 'D100'], True),
                 (['""', 'D100'], True), (['M0', 'D100'], False),
                 (['D0', 'M100'], False), (['K3', 'D100'], False),
                 (['D0', 'K3'], False), (['D0', '"ABC"'], False),
                 (['"unterminated', 'D100'], False), (['"A"B"', 'D100'], False)]
        for operands, accepted in cases:
            with self.subTest(operands=operands, accepted=accepted):
                if accepted:
                    _validate_source_operand_types(self.contract(), operands, 'instruction', 'Q03UDV')
                else:
                    with self.assertRaises(PLCJsonValidationError):
                        _validate_source_operand_types(self.contract(), operands, 'instruction', 'Q03UDV')
        for options, operands in [({'reviewed': False}, ['"ABC"', 'D100']),
                                  ({'literal': False}, ['"ABC"', 'D100']),
                                  ({'device': False}, ['D0', 'D100'])]:
            with self.subTest(options=options):
                with self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(self.contract(**options), operands, 'instruction', 'Q03UDV')

    def test_string_branch_keeps_source_scope_and_legacy_literal_contracts(self):
        from dataclasses import replace
        from plc.instruction_definition import InstructionFactGroup
        from plc.instructions import OperandSpec
        from plc.validation import _validate_source_operand_types, PLCJsonValidationError
        original = self.contract()
        for mutation in ['wrong_model', 'wrong_language', 'missing_dependency', 'missing_device_review', 'incompatible_head_type']:
            declaration = original.definition_facts[0].as_mapping()
            fields = original.verified_fields
            if mutation == 'wrong_model': declaration['scope']['models'] = ['FX3U']
            elif mutation == 'wrong_language': declaration['scope']['languages'] = ['st']
            elif mutation == 'missing_dependency': declaration['depends_on'] = ['not-present']
            elif mutation == 'missing_device_review': fields = tuple(f for f in fields if f != 'device_classes')
            spec = replace(original, verified_fields=fields,
                           definition_facts=(InstructionFactGroup.from_mapping(declaration),))
            if mutation == 'incompatible_head_type':
                spec = replace(spec, operands=(replace(spec.operands[0], data_type='bit'), spec.operands[1]))
            with self.subTest(mutation=mutation):
                with self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(spec, ['"ABC"', 'D100'], 'instruction', 'Q03UDV')
        literal_only = replace(original, definition_facts=(),
            operands=(OperandSpec('S', data_type='string'), original.operands[1]))
        _validate_source_operand_types(literal_only, ['"ABC"', 'D100'], 'instruction', 'Q03UDV')
        with self.assertRaises(PLCJsonValidationError):
            _validate_source_operand_types(literal_only, ['D0', 'D100'], 'instruction', 'Q03UDV')

    def test_string_devices_keep_word_views_and_destination_write_policy(self):
        from dataclasses import replace
        from plc.instruction_definition import InstructionFactGroup
        from plc.validation import _validate_source_operand_types, PLCJsonValidationError
        original = self.contract()
        raw = original.definition_facts[0].as_mapping()
        raw['value']['operands'][0]['range']['device_prefixes'].append('T')
        raw['value']['operands'][1]['range']['device_prefixes'].append('SD')
        spec = replace(original, definition_facts=(InstructionFactGroup.from_mapping(raw),))
        _validate_source_operand_types(spec, ['T0', 'D100'], 'instruction', 'Q03UDV')
        with self.assertRaises(PLCJsonValidationError):
            _validate_source_operand_types(spec, ['D0', 'SD0'], 'instruction', 'Q03UDV')
        string_spec = replace(original, operands=tuple(replace(p, data_type='string') for p in original.operands))
        for source in ['D0', '"ABC"']:
            with self.subTest(source=source):
                _validate_source_operand_types(string_spec, [source, 'D100'], 'instruction', 'Q03UDV')


class EncodingTimerFamilyTests(unittest.TestCase):
    def facts(self, opcode, cpu='FX3U'):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_knowledge import compose_programming_facts
        entry = registry.definition_entry(opcode, cpu=cpu) or {}
        return compose_programming_facts(registry, opcode, cpu=cpu, existing=entry.get('facts', []))[0]

    def native_table(self, opcode, cpu='FX3U'):
        tables = [f['value'] for f in self.facts(opcode, cpu)
            if f['dimension'] == 'parameters.typed_operands'
            and f['id'].startswith('assembly:parameters:family:') and ':encoding-timers:' in f['id']
            and 'native_form' in f['value']]
        self.assertEqual(len(tables), 1, (cpu, opcode))
        return tables[0]

    def test_literal_forms_and_native_parameter_bindings_keep_real_names(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for cpu, ascii_base in [('FX3U', 'ASCI'), ('FX3UC', 'ASCI'), ('Q03UDV', 'ASC'), ('L02', 'ASC')]:
            for base in ['DECO', 'ENCO', ascii_base, 'HEX', 'TTMR', 'STMR']:
                forms = [base] if base in {'TTMR', 'STMR'} else [base, base + 'P']
                for index, opcode in enumerate(forms):
                    with self.subTest(cpu=cpu, opcode=opcode):
                        form = registry.resolve_catalog_form(opcode, cpu=cpu)
                        self.assertEqual((form.base_mnemonic, form.pulse, form.double), (base, bool(index), False))
                        self.assertTrue(form.spec.supports_cpu(cpu))
                        table = self.native_table(opcode, cpu)
                        expected = ['D', 'n'] if base == 'TTMR' else ['S', 'm' if cpu.startswith('FX') else 'n', 'D'] if base == 'STMR' else ['S', 'D', 'n']
                        self.assertEqual(table['native_form']['operand_order'], expected)
                        self.assertTrue(all(p.get('purpose') and p.get('semantic_role') for p in table['operands']))
        self.assertNotEqual(registry.resolve_catalog_form('ASC', cpu='FX3U').base_mnemonic, 'ASCI')
        for opcode in ['DDECO', 'DENCO', 'DASCI', 'DHEX', 'TTMRP', 'STMRP']:
            self.assertIsNone(registry.resolve_catalog_form(opcode), opcode)

    def test_dynamic_counts_match_independent_bit_and_character_unit_arithmetic(self):
        from hypothesis import given, settings, strategies as st
        from plc.instruction_definition import evaluate_expression
        deco = {p['name']: p for p in self.native_table('DECO')['operands']}
        fx_ascii = {p['name']: p for p in self.native_table('ASCI')['operands']}
        ql_ascii = {p['name']: p for p in self.native_table('ASC', 'Q03UDV')['operands']}
        fx_hex = {p['name']: p for p in self.native_table('HEX')['operands']}
        ql_hex = {p['name']: p for p in self.native_table('HEX', 'Q03UDV')['operands']}

        @settings(max_examples=96, deadline=None)
        @given(st.integers(0, 8), st.integers(1, 256), st.booleans())
        def check(bits, characters, unpacked):
            self.assertEqual(evaluate_expression(deco['D']['occupied_bit_count'], {'n': bits}),
                             0 if bits == 0 else len(range(2 ** bits)))
            values = {'n': characters, 'M8161': unpacked}
            word_pairs = len(list(range(0, characters, 2)))
            nibble_words = len(list(range(0, characters, 4)))
            for pin, expected in [(fx_ascii['S'], nibble_words),
                                  (fx_ascii['D'], characters if unpacked else word_pairs),
                                  (fx_hex['S'], characters if unpacked else word_pairs),
                                  (fx_hex['D'], nibble_words), (ql_ascii['S'], nibble_words),
                                  (ql_ascii['D'], word_pairs), (ql_hex['S'], word_pairs),
                                  (ql_hex['D'], nibble_words)]:
                self.assertEqual(evaluate_expression(pin['word_device_layout']['word_count'], values), expected)
        check()

    def test_timer_storage_state_binding_and_OFF_processing_remain_distinct(self):
        for cpu in ['FX3U', 'FX3UC', 'Q03UDV', 'L02']:
            table = {p['name'].upper(): p for p in self.native_table('TTMR', cpu)['operands']}
            layout = table['D']['word_device_layout']
            self.assertEqual((layout['word_count'], layout['element_bits'], layout['element_count']), (2, 16, 2))
            self.assertIs(layout['single_scalar'], False)
            stmr = {p['name'].upper(): p for p in self.native_table('STMR', cpu)['operands']}
            self.assertEqual(stmr['D']['occupied_bit_count'], 4)
            effects = [f['value'] for f in self.facts('STMR', cpu) if f['dimension'] == 'effects.bound_relation'
                and ':encoding-timers:' in f['id'] and any(c == {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
                    for c in f.get('conditions', []))]
            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0]['inputs'], ['S', 'm' if cpu.startswith('FX') else 'n'])
            self.assertEqual(effects[0]['state_bindings']['selected_timer']['source_parameter'], 'S')
            if cpu.startswith('FX'):
                self.assertIn('timer_S', effects[0]['command_OFF']['immediate_reset'])
            else:
                self.assertTrue(effects[0]['execution_requirements']['call_regardless_of_command_ON_or_OFF'])
                self.assertEqual(effects[0]['timer_state']['command_fall_before_time_up'], 'continue_measurement')

    def test_source_conflicts_and_cpu_exclusions_survive_family_composition(self):
        self.assertTrue(any(f['dimension'] == 'parameters.source_array_span_disagreement' and f['status'] == 'conflict'
                            for f in self.facts('ENCO', 'Q03UDV')))
        self.assertTrue(any(f['dimension'] == 'parameters.source_statement_disagreement' and f['status'] == 'conflict'
                            for f in self.facts('TTMR', 'FX2N')))
        self.assertTrue(any(f['dimension'] == 'parameters.source_call_order_disagreement' and f['status'] == 'conflict'
                            for f in self.facts('TTMR', 'FX3U')))
        for cpu in ['FX3S', 'FX3G', 'FX3GC', 'Q00J', 'Q00', 'Q01', 'Q12PRH', 'Q25PRH']:
            for opcode in ['TTMR', 'STMR']:
                rows = self.facts(opcode, cpu)
                self.assertTrue(any(f['dimension'] == 'constraints.cpu_availability'
                    and f['value'].get('available') is False for f in rows))
                self.assertFalse(any(f['dimension'] == 'effects.bound_relation' and ':encoding-timers:' in f['id'] for f in rows))

    def test_device_expression_names_and_explicit_overflow_survive_default_rendering(self):
        from knowledge.instruction_presentation import _semantic_view
        from shared.model_context import model_knowledge_value
        count = {p['name']: p for p in self.native_table('ASCI')['operands']}['D']['word_device_layout']['word_count']
        view = _semantic_view({'count': count}, model_knowledge_value)
        text = json.dumps(view)
        self.assertIn('M8161', text)
        self.assertNotIn('device()', text)
        self.assertNotIn('unknow', text)
        self.assertTrue(any(p['name'] == 'M8161' and p['kind'] == 'device' for p in view['expression_parameters']))
        typed = copy.deepcopy(count)
        typed['args'][2]['args'][0]['overflow'] = 'wrap'
        explicit = _semantic_view({'count': typed}, model_knowledge_value)
        self.assertTrue(any(p.get('overflow') == 'wrap' for p in explicit['expression_types']))


class ZeroDataSharedDefinitionBindingTests(unittest.TestCase):
    def declarations(self):
        from plc.instruction_knowledge import validate_family_knowledge
        scope = {'models': ['FX3U'], 'forms': ['SYNTH_ZERO'], 'languages': ['ladder']}
        source = [{'manual_id': 'synthetic-zero-manual', 'revision': 'A', 'pdf_pages': [9]}]
        proof = fact('shared-proof', 'identity.instruction_class', scope=copy.deepcopy(scope),
                     sources=copy.deepcopy(source), value={'instruction_class': 'applied'})
        binding = fact('shared-binding', 'effects.documented_processing', scope=copy.deepcopy(scope),
            sources=copy.deepcopy(source), depends_on=[proof['id']],
            conditions=[{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
            value={'definition_ref': 'state_operation', 'parameter_bindings': {},
                   'signature': {'parameter_types': {}, 'parameter_directions': {}}})
        body = {'parameters': [], 'value': {'knowledge_kind': 'manual_behavior',
            'declaration_kind': 'instruction', 'operation': 'source_declared_state_transition',
            'operand_binding': {'inputs': []},
            'state_accesses': [{'target': {'kind': 'state', 'parameter': 'shared_state'}, 'access': 'read_write'}],
            'state_bindings': {'shared_state': {'devices': ['M100'], 'type': {'kind': 'bool'}}}}}
        source_family = {'base_opcode': 'SYNTH_BASE', 'shared_values': {'state_operation': body}, 'facts': []}
        caller = {'base_opcode': 'SYNTH_ZERO', 'shared_values': {'state_operation': {
            'shared_ref': {'family': 'SYNTH_BASE', 'name': 'state_operation'}}}, 'facts': [proof, binding]}
        _, definition = validate_family_knowledge([source_family, caller], vendor='mitsubishi')
        signature = fact('native-declaration', 'parameters.typed_operands', scope=copy.deepcopy(scope),
            sources=copy.deepcopy(source), depends_on=['source-contract'],
            conditions=[{'context': 'cpu_version', 'minimum': '2.20'}],
            value={'declaration_kind': 'instruction', 'representation': 'native_ladder_instruction',
                   'operands': [], 'operand_count': 0})
        contract = fact('source-contract', 'constraints.cpu_availability', scope=copy.deepcopy(scope),
                        sources=copy.deepcopy(source), value={'available': True})
        return definition, binding, signature, contract

    def test_explicit_native_zero_data_shared_ref_keeps_source_graph_and_guards(self):
        from plc.instruction_knowledge import _bind_shared_definition
        from plc.instruction_relations import _operands
        for explicit_native_form in (False, True):
            with self.subTest(explicit_native_form=explicit_native_form):
                definition, binding, signature, contract = self.declarations()
                if explicit_native_form:
                    signature['value'].update(arity={'minimum': 0, 'maximum': 0},
                        native_form={'operand_order': [], 'minimum_operands': 0, 'maximum_operands': 0})
                before = copy.deepcopy((definition, binding, signature, contract))
                self.assertEqual(_operands(signature), ([], [], {}))
                result, = _bind_shared_definition(definition, binding, [signature, contract])
                self.assertEqual(result['value']['operation'], 'source_declared_state_transition')
                self.assertEqual(result['value']['state_bindings'],
                                 definition['shared_values']['state_operation']['value']['state_bindings'])
                self.assertEqual(result['depends_on'], ['shared-proof', 'native-declaration'])
                self.assertEqual(signature['depends_on'], ['source-contract'])
                self.assertEqual(result['conditions'], [
                    {'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                    {'context': 'cpu_version', 'minimum': '2.20'}])
                self.assertEqual(result['scope'], signature['scope'])
                self.assertEqual(result['sources'], signature['sources'])
                self.assertEqual(result['status'], 'source_verified')
                self.assertNotIn('call_available', result['value'])
                self.assertNotIn('behavior', result['value'])
                self.assertNotIn('executor_capability', result['value'])
                self.assertEqual((definition, binding, signature, contract), before)
                signature['status'] = 'declared_unverified'
                weaker, = _bind_shared_definition(definition, binding, [signature, contract])
                self.assertEqual(weaker['status'], 'declared_unverified')
                self.assertEqual(weaker['depends_on'], result['depends_on'])

    def test_nonzero_and_ambiguous_counts_cannot_bind_zero_data_body(self):
        from hypothesis import given, settings, strategies as st
        from plc.instruction_knowledge import _bind_shared_definition

        @settings(max_examples=32, deadline=None)
        @given(st.one_of(st.integers(max_value=-1), st.integers(min_value=1)))
        def check(nonzero_count):
            definition, binding, signature, contract = self.declarations()
            signature['value']['operand_count'] = nonzero_count
            self.assertEqual(_bind_shared_definition(definition, binding, [signature, contract]), [])
        check()
        for count in [None, False, True, 0.0, '0', [], {}]:
            with self.subTest(count=count):
                definition, binding, signature, contract = self.declarations()
                signature['value']['operand_count'] = count
                self.assertEqual(_bind_shared_definition(definition, binding, [signature, contract]), [])
        for field, value in [('arity', {'minimum': 0, 'maximum': 1}),
                             ('arity', {'min': False, 'max': 0}), ('arity', {}),
                             ('arity', {'minimum': 0}), ('arity', {'count': 0}),
                             ('native_form', {'operand_order': ['payload']}),
                             ('native_form', {'minimum_operands': 1, 'maximum_operands': 1}),
                             ('native_form', {'minimum_operands': False}), ('native_form', [])]:
            with self.subTest(field=field, value=value):
                definition, binding, signature, contract = self.declarations()
                signature['value'][field] = value
                self.assertEqual(_bind_shared_definition(definition, binding, [signature, contract]), [])

    def test_empty_native_declarations_still_require_their_own_complete_interface(self):
        from plc.instruction_knowledge import _bind_shared_definition
        missing = object()
        cases = [('operands', missing), ('operands', None), ('operands', {}),
                 ('operand_count', missing), ('declaration_kind', missing),
                 ('declaration_kind', 'function'), ('declaration_kind', 'function_block'),
                 ('representation', missing), ('representation', 'structured_ladder_block'),
                 ('instruction_variant', 'foreign_overload')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                definition, binding, signature, contract = self.declarations()
                if value is missing:
                    signature['value'].pop(field, None)
                else:
                    signature['value'][field] = value
                self.assertEqual(_bind_shared_definition(definition, binding, [signature, contract]), [])
        for broken in ['source', 'revision', 'scope', 'language', 'condition', 'verification', 'extra_data', 'kind']:
            with self.subTest(broken=broken):
                definition, binding, signature, contract = self.declarations()
                if broken == 'source': signature['sources'][0]['manual_id'] = 'another-manual'
                elif broken == 'revision': signature['sources'][0]['revision'] = 'B'
                elif broken == 'scope': signature['scope']['models'] = ['FX3UC']
                elif broken == 'language': signature['scope']['languages'] = ['st']
                elif broken == 'condition': signature['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                elif broken == 'verification': signature['verification'] = 'not_performed'
                elif broken == 'extra_data': signature['value']['operands'] = [{'name': 'payload', 'direction': 'input', 'type': 'WORD'}]
                else: signature['dimension'] = 'parameters.usage'
                self.assertEqual(_bind_shared_definition(definition, binding, [signature, contract]), [])
        for broken in ['parameter_map', 'descriptor', 'body_parameter']:
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                definition, binding, signature, contract = self.declarations()
                if broken == 'parameter_map': binding['value']['parameter_bindings'] = {'undeclared': 'payload'}
                elif broken == 'descriptor':
                    binding['value']['signature'] = {'parameter_types': {'payload': 'WORD'},
                                                    'parameter_directions': {'payload': 'input'}}
                else: definition['shared_values']['state_operation']['value']['output'] = {'parameter_ref': 'undeclared'}
                _bind_shared_definition(definition, binding, [signature, contract])

    def test_structured_enable_only_interfaces_keep_the_existing_gate_path(self):
        from plc.instruction_knowledge import _bind_shared_definition
        definition, binding, signature, contract = self.declarations()
        for row in (binding, signature, contract):
            row['scope']['languages'] = ['fbd']
            row.pop('conditions', None)
        signature['value'] = {'declaration_kind': 'instruction', 'operand_count': 2, 'operands': [
            {'name': 'EN', 'type': 'BOOL', 'direction': 'input', 'enable_pin': True},
            {'name': 'ENO', 'type': 'BOOL', 'direction': 'output', 'enable_pin': True}]}
        before = copy.deepcopy(signature)
        result, = _bind_shared_definition(definition, binding, [signature, contract])
        self.assertEqual(result['value']['operand_binding']['execution_arguments'], [
            {'parameter': 'EN', 'role': 'execution_condition'},
            {'parameter': 'ENO', 'role': 'execution_status'}])
        self.assertEqual(result['depends_on'], ['shared-proof', 'native-declaration'])
        self.assertEqual(signature, before)


class FxSourceStateTableCorrectionTests(unittest.TestCase):
    def test_explicit_call_diagram_orders_arguments_without_reordering_classification_columns(self):
        from tools.fact_applied_review import _native_parameter_facts, _table_parameter_facts, _source_operand_declarations
        record = LegacySourceCompilationTests.literal_record()
        words = []
        def line(top, names):
            words.append({'text': 'SYNTH', 'x0': 60, 'x1': 100, 'top': top, 'bottom': top + 8})
            for index, name in enumerate(names):
                x = 130 + index * 35
                for text, left in [('[', x - 5), (name, x), (']', x + 10)]:
                    words.append({'text': text, 'x0': left, 'x1': left + 5,
                                  'top': top - 15, 'bottom': top - 7})
        words.append({'text': 'SYNTH', 'x0': 60, 'x1': 100, 'top': 100, 'bottom': 108})
        for index, name in enumerate(['S', 'D', 'n']):
            words.append({'text': name, 'x0': 130 + index * 35, 'x1': 140 + index * 35,
                          'top': 85, 'bottom': 93})
        line(300, ['S', 'n', 'D'])
        record['parameter_pages'] = [{'page': 1, 'words': words, 'parameter_tables': []}]
        before = copy.deepcopy(record)
        facts = _native_parameter_facts(record, {'opcode': 'SYNTH', 'width_bits': 16}, ['FX2C'])
        order = next(row for row in facts if row['dimension'] == 'parameters.order')
        self.assertEqual(order['value']['symbols'], ['S', 'n', 'D'])
        table = next(row for row in facts if row['dimension'] == 'parameters.typed_operands')
        self.assertEqual([item['name'] for item in table['value']['operands']], ['S', 'n', 'D'])
        self.assertEqual([item['direction'] for item in table['value']['operands']], ['input', 'input', 'output'])
        self.assertEqual(record, before)
        declarations = {dimension: value for dimension, _, value in _table_parameter_facts(record)}
        self.assertEqual(declarations['parameters.source_order']['symbols'], ['S', 'n', 'D'])
        self.assertEqual(declarations['parameters.source_classification']['symbols'], ['S', 'D', 'n'])
        self.assertIs(declarations['parameters.source_classification']['call_argument_order'], False)
        self.assertFalse(_source_operand_declarations(record, 'Mnemonic Function Program steps\nS D n\nOperation:'))
        line(400, ['S', 'D', 'n'])
        self.assertFalse(_native_parameter_facts(record, {'opcode': 'SYNTH', 'width_bits': 16}, ['FX2C']))
        self.assertNotIn('parameters.source_order', {row[0] for row in _table_parameter_facts(record)})

    def test_page_encoding_restores_numeric_named_glyphs_without_guessing_missing_cids(self):
        from knowledge.manual_applicability import numeric_named_glyph_characters
        characters = [{'fontname': 'diagram', 'text': '(cid:2)', 'x0': 10},
            {'fontname': 'diagram', 'text': '%', 'x0': 20},
            {'fontname': 'diagram', 'text': '(cid:99)', 'x0': 30},
            {'fontname': 'other', 'text': '(cid:2)', 'x0': 40}]
        encodings = [{'fontname': 'diagram', 'differences': [2, 'C83', 37, 'C110']}]
        before = copy.deepcopy(characters)
        decoded = numeric_named_glyph_characters(characters, encodings)
        self.assertEqual([row['text'] for row in decoded], ['S', 'n', '(cid:99)', '(cid:2)'])
        self.assertEqual([row['x0'] for row in decoded], [10, 20, 30, 40])
        self.assertEqual(characters, before)
        for bad in [dict(encodings[0], to_unicode=True),
                    dict(encodings[0], differences=[2, 'S']),
                    dict(encodings[0], differences=[2, 'C500']),
                    dict(encodings[0], differences=['C83'])]:
            with self.subTest(encoding=bad):
                self.assertEqual(numeric_named_glyph_characters(characters, [bad]), before)

    def test_reviewed_artwork_header_keeps_exact_versions_and_rejects_foreign_or_partial_reviews(self):
        from tools.fact_applied_review import _reviewed_header_records, _reviewed_header_facts, _models, _for_model
        record = LegacySourceCompilationTests.literal_record()
        record.update(manual_id='synthetic_artwork', manual_number='SYNTH_HEADER', revision='A',
            section='7.1 FNC 12 - SYNTH / Example', pdf_pages=[7, 8],
            manual_models=['FX3S', 'FX3U', 'FX3UC'],
            applicability={'basis': 'manual_scope', 'supported_models': ['FX3S', 'FX3U', 'FX3UC']})
        review = {key: record[key] for key in ['manual_id', 'manual_number', 'revision', 'section']}
        review.update(pdf_page=7, status='source_verified', verification='source_checked', model_marks=[
            {'model': 'FX3S', 'state': 'unsupported'},
            {'model': 'FX3U', 'state': 'conditional', 'minimum_version': '2.20'},
            {'model': 'FX3UC', 'state': 'conditional', 'minimum_version': '1.00'}], source={
                'manual_id': 'synthetic_artwork', 'manual_number': 'SYNTH_HEADER', 'revision': 'A',
                'pdf_pages': [7], 'representation': 'instruction_header_cpu_version_badges',
                'review_artifact': 'private-reviewed-page.png'})
        originals = copy.deepcopy([record, review])
        scoped, = _reviewed_header_records([record], [review])
        models, gap, conditional, guard = _models(scoped['applicability'], record['manual_models'])
        self.assertEqual(models, ['FX3U', 'FX3UC']); self.assertIsNone(gap)
        self.assertEqual(scoped['original_applicability'], record['applicability'])
        facts = _reviewed_header_facts(scoped, 'SYNTH')
        availability = [row for row in facts if row['dimension'] == 'constraints.cpu_availability']
        self.assertEqual(len(availability), 5)
        for cpu, version in [('FX3U', '2.20'), ('FX3UC', '1.00')]:
            own = [row for row in availability if row['scope']['models'] == [cpu]]
            self.assertEqual({tuple(condition.items())[1] for row in own for condition in row['conditions']
                              if condition['context'] == 'cpu_version'},
                             {('minimum', version), ('maximum_exclusive', version)})
            self.assertEqual({row['value']['available'] for row in own}, {True, False})
            self.assertTrue(all(row['status'] == 'source_verified' and row['depends_on'] for row in own))
            base = {'id': 'p', 'dimension': 'parameters.order', 'value': {'symbols': ['S']},
                    'scope': {'models': [cpu]}, 'status': 'source_verified',
                    'verification': 'source_checked', 'sources': []}
            bound, = _for_model([base], cpu, conditional, guard)
            self.assertEqual(bound['conditions'], [{'context': 'cpu_version', 'minimum': version}])
            self.assertEqual(bound['status'], 'source_verified')
            base['status'] = 'declared_unverified'
            self.assertEqual(_for_model([base], cpu, conditional, guard)[0]['status'], 'declared_unverified')
        excluded, = [row for row in availability if row['scope']['models'] == ['FX3S']]
        self.assertIs(excluded['value']['available'], False)
        self.assertEqual(excluded['conditions'], record['conditions'])
        self.assertEqual([record, review], originals)
        invalid = []
        for key in ['manual_id', 'manual_number', 'revision', 'section', 'pdf_page', 'verification', 'status']:
            altered = copy.deepcopy(review); altered[key] = 'foreign'
            invalid.append(altered)
        for mutation in ['source_page', 'source_revision', 'source_review', 'missing_model', 'missing_version', 'bad_version']:
            altered = copy.deepcopy(review)
            if mutation == 'source_page': altered['source']['pdf_pages'] = [8]
            elif mutation == 'source_revision': altered['source']['revision'] = 'B'
            elif mutation == 'source_review': altered['source'].pop('review_artifact')
            elif mutation == 'missing_model': altered['model_marks'].pop(0)
            elif mutation == 'missing_version': altered['model_marks'][1].pop('minimum_version')
            else: altered['model_marks'][1]['minimum_version'] = 'Ver.2.20'
            invalid.append(altered)
        for altered in invalid:
            with self.subTest(review=altered):
                unchanged, = _reviewed_header_records([record], [altered])
                self.assertEqual(unchanged, record)
                self.assertFalse(_reviewed_header_facts(unchanged, 'SYNTH'))

    def test_r_math_and_positioning_artwork_headers_bind_exact_models_and_versions(self):
        from tools.fact_applied_review import (_r_math_header_reviews,
            _r_positioning_header_reviews, _reviewed_header_records,
            _reviewed_header_facts, _models)
        expected = [
            (review, {'FX3U': '2.20', 'FX3UC': '1.00'})
            for review in _r_math_header_reviews()
        ] + [
            (review, {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40',
                      'FX3U': '2.20', 'FX3UC': '1.00'})
            for review in _r_positioning_header_reviews()
        ]
        manual_models = ['FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC']
        for review, versions in expected:
            with self.subTest(page=review['pdf_page']):
                record = {key: review[key] for key in
                          ('manual_id', 'manual_number', 'revision', 'section')}
                record.update(id=f"R-{review['pdf_page']}", pdf_pages=[review['pdf_page']],
                    manual_models=manual_models,
                    applicability={'basis': 'manual_scope'}, conditions=[{
                        'context': 'project_mode', 'equals': 'native_ladder_instruction'}])
                scoped, = _reviewed_header_records([record], [review])
                models, gap, conditional, guard = _models(
                    scoped['applicability'], manual_models)
                self.assertEqual(models, list(versions))
                self.assertIsNone(gap)
                self.assertEqual(guard, {'context': 'cpu_version',
                    'minimum_by_model': versions})
                facts = _reviewed_header_facts(scoped, 'SYNTH')
                availability = [fact for fact in facts
                    if fact['dimension'] == 'constraints.cpu_availability']
                self.assertEqual(len(availability), len(manual_models) + len(versions))
                for model in manual_models:
                    own = [fact for fact in availability
                           if fact['scope']['models'] == [model]]
                    if model in versions:
                        self.assertEqual({fact['value']['available'] for fact in own},
                                         {True, False})
                        self.assertEqual({condition['minimum'] for fact in own
                            for condition in fact['conditions']
                            if condition['context'] == 'cpu_version'
                            and 'minimum' in condition}, {versions[model]})
                    else:
                        self.assertEqual(len(own), 1)
                        self.assertFalse(own[0]['value']['available'])

    def test_r_bcd_header_binds_all_five_cpu_versions_to_its_own_page(self):
        from tools.fact_applied_review import (_r_bcd_header_reviews,
            _reviewed_header_records, _models)
        record = {'id': 'R-BCD', 'opcode': 'BCD', 'manual_id': 'fx3_programming_r',
            'manual_number': 'JY997D16601', 'revision': 'R', 'section': '9.9 FNC 18 – BCD / Conversion to Binary Coded Decimal',
            'pdf_pages': [266, 267, 268], 'manual_models': ['FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC'],
            'text': ('1. 16-bit operation (BCD and BCDP)\nBCD Continuous\nBCDP Pulse\n'
                     '2. 32-bit operation (DBCD and DBCDP)\nDBCD Continuous\nDBCDP Pulse'),
            'applicability': {'basis': 'manual_scope'}}
        review, = _r_bcd_header_reviews([record])
        self.assertEqual(review['pdf_page'], 266)
        self.assertEqual({row['model']: row.get('minimum_version') for row in review['model_marks']},
            {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40', 'FX3U': '2.20', 'FX3UC': '1.00'})
        scoped, = _reviewed_header_records([record], [review])
        models, gap, conditional, guard = _models(scoped['applicability'], record['manual_models'])
        self.assertIsNone(gap)
        self.assertEqual(set(models), set(record['manual_models']))
        self.assertEqual(guard, {'context': 'cpu_version', 'minimum_by_model': {
            'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40', 'FX3U': '2.20', 'FX3UC': '1.00'}})

    def test_r_bcdp_destination_direction_requires_same_manual_source_destination_legend(self):
        from unittest.mock import patch
        from tools.fact_applied_review import _repair_fx_bcdp_source
        record = {'opcode': 'BCD', 'manual_id': 'fx3_programming_r', 'revision': 'R',
            'pdf_pages': [266, 267, 268],
            'text': ('1. 16-bit operation (BCD and BCDP)\nBCD Continuous\nBCDP Pulse\n'
                     '2. 32-bit operation (DBCD and DBCDP)\nDBCD Continuous\nDBCDP Pulse')}
        pages = [{'layout': ('Word device number storing the conversion source (binary) data. '
                              'Word device number of the conversion destination (binary-coded decimal) data.')}]
        facts = {}
        for model in ('FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC'):
            facts[model, 'BCDP'] = [{'id': 'authored:applied:native_typed_operands@fx3_programming_r',
                'dimension': 'parameters.typed_operands', 'status': 'declared_unverified',
                'verification': 'not_performed', 'scope': {'models': [model], 'forms': ['BCDP']},
                'sources': [{'manual_id': 'fx3_programming_r', 'pdf_pages': [266, 267, 268]}],
                'conditions': [{'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}],
                'value': {'operands': [{'name': 'S', 'type': 'ANY16', 'direction': 'input'},
                                       {'name': 'D', 'type': 'ANY16', 'direction': 'input'}]}}]
        with patch('tools.fact_applied_review._parameter_source_pages', return_value=pages), \
             patch('tools.fact_applied_review._native_parameter_conventions', return_value={
                 'source_destination_symbols': True, 'pdf_pages': [176, 177]}):
            _repair_fx_bcdp_source(object(), [record], facts)
        for model, minimum in {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40',
                               'FX3U': '2.20', 'FX3UC': '1.00'}.items():
            row, = facts[model, 'BCDP']
            operands = {item['name']: item for item in row['value']['operands']}
            self.assertEqual(operands['D']['direction'], 'output')
            self.assertEqual(operands['D']['direction_basis'],
                             'same_manual_explicit_source_destination_symbol_definition')
            self.assertIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                          row['conditions'])
            self.assertIn({'context': 'cpu_version', 'minimum': minimum}, row['conditions'])
            self.assertIn({'manual_id': 'fx3_programming_r', 'pdf_pages': [176, 177]},
                          [{key: ref.get(key) for key in ('manual_id', 'pdf_pages')}
                           for ref in row['sources']])

    def test_r_bcd_continuous_destination_direction_uses_its_own_header_guard(self):
        from unittest.mock import patch
        from tools.fact_applied_review import _repair_fx_bcd_source
        record = {'opcode': 'BCD', 'manual_id': 'fx3_programming_r', 'revision': 'R',
            'pdf_pages': [266, 267, 268],
            'text': ('1. 16-bit operation (BCD and BCDP)\nBCD Continuous\nBCDP Pulse\n'
                     '2. 32-bit operation (DBCD and DBCDP)\nDBCD Continuous\nDBCDP Pulse')}
        pages = [{'layout': ('Word device number storing the conversion source (binary) data. '
                              'Word device number of the conversion destination (binary-coded decimal) data.')}]
        versions = {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40',
                    'FX3U': '2.20', 'FX3UC': '1.00'}
        facts = {}
        for model, minimum in versions.items():
            guard = {'context': 'cpu_version', 'minimum': minimum}
            facts[model, 'BCD'] = [
                {'id': 'authored:applied:cpu_availability:header@fx3_programming_r',
                 'dimension': 'constraints.cpu_availability', 'status': 'source_verified',
                 'verification': 'source_checked', 'scope': {'models': [model], 'forms': ['BCD']},
                 'sources': [{'manual_id': 'fx3_programming_r', 'pdf_pages': [266]}],
                 'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}, guard],
                 'value': {'available': True, 'model_states': {model: 'supported'}}},
                {'id': 'authored:applied:native_typed_operands@fx3_programming_r',
                 'dimension': 'parameters.typed_operands', 'status': 'declared_unverified',
                 'verification': 'not_performed', 'scope': {'models': [model], 'forms': ['BCD']},
                 'sources': [{'manual_id': 'fx3_programming_r', 'pdf_pages': [266, 267]}],
                 'conditions': [guard],
                 'value': {'operands': [{'name': 'S', 'type': 'ANY16', 'direction': 'input'},
                                       {'name': 'D', 'type': 'ANY16', 'direction': 'input'}]}}]
        with patch('tools.fact_applied_review._parameter_source_pages', return_value=pages), \
             patch('tools.fact_applied_review._native_parameter_conventions', return_value={
                 'source_destination_symbols': True, 'pdf_pages': [176, 177]}):
            _repair_fx_bcd_source(object(), [record], facts)
        for model, minimum in versions.items():
            typed, = [row for row in facts[model, 'BCD']
                      if row['dimension'] == 'parameters.typed_operands']
            operands = {item['name']: item for item in typed['value']['operands']}
            self.assertEqual(operands['S']['direction'], 'input')
            self.assertEqual(operands['D']['direction'], 'output')
            self.assertIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                          typed['conditions'])
            self.assertIn({'context': 'cpu_version', 'minimum': minimum}, typed['conditions'])
            self.assertEqual(typed['status'], 'source_verified')
            self.assertIn({'manual_id': 'fx3_programming_r', 'pdf_pages': [176, 177]},
                          [{key: ref.get(key) for key in ('manual_id', 'pdf_pages')}
                           for ref in typed['sources']])

    def test_j_bcd_heading_is_seeded_only_when_its_own_fx_mapping_is_present(self):
        import sqlite3
        from types import SimpleNamespace
        from tools.fact_applied_review import _legacy_j_bcd_heading_records
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close)
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0,FX0S,FX0N,FX1,FXU,FX2C,FX2N,FX2NC')")
        models = {'FX0': False, 'FX0S': False, 'FX0N': False, 'FXU': True,
                  'FX1': True, 'FX2C': True, 'FX2N': True, 'FX2NC': True}
        bcd = {'opcode': 'BCD', 'section': '5.2.9 BCD (FNC18)',
            'text': 'BCD converts binary source data into BCD destination data.',
            'model_marks': [{'model': model, 'state': 'supported'} for model in models],
            'form_models': {'16': {model: True for model in models}, 'pulse': models, '32': models}}
        other = {'opcode': 'BIN', 'section': '5.2.10 BIN (FNC 19)',
            'text': 'BIN converts BCD data into binary data.',
            'model_marks': [{'model': model, 'state': 'supported'} for model in models],
            'form_models': {'16': {model: True for model in models}, 'pulse': models, '32': models}}
        layout = {'cells': [{'label': 'FX', 'models': ['FXU', 'FX1']}]}
        reader = SimpleNamespace(execute=source.execute, instruction_layouts={'jy992d48301j': layout},
            legacy_page=lambda manual, page: {'legacy_sections': [bcd, other],
                'legacy_cpu_labels': layout['cells'], 'legacy_prefix_text': '', 'legacy_continues': False})
        recovered, = _legacy_j_bcd_heading_records(reader)
        self.assertEqual((recovered['manual_id'], recovered['revision'], recovered['pdf_pages']),
                         ('jy992d48301j', 'J', [148]))
        self.assertIn('FX1', recovered['manual_models'])
        self.assertIn('FXU', recovered['manual_models'])
        self.assertFalse(_legacy_j_bcd_heading_records(SimpleNamespace(
            execute=source.execute, instruction_layouts={'jy992d48301j': {'cells': []}},
            legacy_page=reader.legacy_page)))

    def test_j_bcd_operation_all_units_supports_only_its_mapped_continuous_models(self):
        from types import SimpleNamespace
        from tools.fact_applied_review import _repair_fx_bcd_source
        expected = ['FX0', 'FX0S', 'FX0N', 'FXU', 'FX1', 'FX2C', 'FX2N', 'FX2NC']
        cells = [
            {'label': 'FX0(S)', 'models': ['FX0', 'FX0S']},
            {'label': 'FX0N', 'models': ['FX0N']},
            {'label': 'FX', 'models': ['FXU', 'FX1']},
            {'label': 'FX(2C)', 'models': ['FX2C']},
            {'label': 'FX2N(C)', 'models': ['FX2N', 'FX2NC']},
        ]
        form_models = {
            '16': {model: True for model in expected},
            'pulse': {model: model not in {'FX0', 'FX0S', 'FX0N'} for model in expected},
        }
        heading = {'opcode': 'BCD', 'section': '5.2.9 BCD (FNC18)',
            'text': ('Operation 1: (Applicable to all units) The binary source data (S) is '
                     'converted into an equivalent BCD number and stored at the destination device (D).'),
            'form_models': form_models}
        source = SimpleNamespace(instruction_layouts={'jy992d48301j': {
            'kind': 'legacy_shading', 'cells': cells}},
            legacy_page=lambda manual, page: {'legacy_sections': [heading]})
        record = {'opcode': 'BCD', 'manual_id': 'jy992d48301j', 'revision': 'J',
            'pdf_pages': [148], 'manual_models': expected}
        facts = {}
        for model in expected:
            facts[model, 'BCD'] = [{'id': 'authored:applied:cpu_availability@jy992d48301j',
                'dimension': 'constraints.cpu_availability', 'status': 'unknown',
                'verification': 'source_checked', 'scope': {'models': [model], 'forms': ['BCD']},
                'sources': [{'manual_id': 'jy992d48301j', 'pdf_pages': [148]}],
                'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
                'value': {'available': 'unknown', 'model_states': {model: 'unknown'}}}]
        _repair_fx_bcd_source(source, [record], facts)
        for model in expected:
            fact, = facts[model, 'BCD']
            self.assertIs(fact['value']['available'], True)
            self.assertEqual(fact['value']['model_states'], {model: 'supported'})
            self.assertEqual(fact['status'], 'source_verified')
            self.assertIn({'representation': 'Operation_1_all_units_and_continuous_16_bit_BCD',
                'manual_id': 'jy992d48301j', 'manual_number': 'JY992D48301',
                'revision': 'J', 'pdf_pages': [148], 'id': 'page_text:jy992d48301j:148:operation_1'},
                fact['sources'])

        heading['text'] = heading['text'].replace('all units', 'FX(2C)')
        unsupported_record = dict(record, manual_models=expected)
        facts = {(model, 'BCD'): [{'id': 'authored:applied:cpu_availability@jy992d48301j',
            'dimension': 'constraints.cpu_availability', 'scope': {'models': [model], 'forms': ['BCD']},
            'sources': [{'manual_id': 'jy992d48301j', 'pdf_pages': [148]}],
            'value': {'available': 'unknown', 'model_states': {model: 'unknown'}}}]
            for model in expected}
        _repair_fx_bcd_source(source, [unsupported_record], facts)
        self.assertTrue(all(facts[model, 'BCD'][0]['value']['available'] == 'unknown'
                            for model in expected))

    def test_plain_bcd_native_ranges_bind_the_own_instruction_pages(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from tools.fact_applied_review import _repair_fx_bcd_native_operand_ranges

        domains = {'S': 'knx,kny,knm,kns,t,c,d,v,z',
                   'D': 'kny,knm,kns,t,c,d,v,z'}
        digit_pages = {'jy992d88101e': 110, 'jy992d48301j': 115}
        case_rows = (
            ('FX1N', 'jy992d88101e', 'E', 146),
            ('FX0', 'jy992d48301j', 'J', 148),
        )

        class Source:
            def execute(self, *_args):
                return SimpleNamespace(fetchone=lambda: {
                    'clean_text': 'If the converted BCD number exceeds the operational ranges '
                                  'of 0 to 9,999 (16 bit operation), an error will occur.'})

        records, facts = [], {}
        for model, manual, revision, page in case_rows:
            records.append({'manual_id': manual, 'revision': revision, 'opcode': 'BCD',
                            'pdf_pages': [page], 'text': 'BCD own 16-bit row'})
            facts[model, 'BCD'] = [{
                'id': f'authored:applied:native_typed_operands:default@{manual}',
                'dimension': 'parameters.typed_operands',
                'scope': {'models': [model], 'forms': ['BCD']},
                'sources': [{'manual_id': manual, 'pdf_pages': [page]}],
                'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
                'value': {'representation': 'native_ladder_instruction', 'operands': [
                    {'name': 'S', 'direction': 'input'}, {'name': 'D', 'direction': 'output'}]},
            }]

        def digit_rule(_source, manual):
            page = digit_pages[manual]
            return {'digit_groups_allowed': [1, 2, 3, 4],
                    'source': {'manual_id': manual, 'pdf_pages': [page]}}

        with patch('tools.fact_applied_review._source_table_groups', return_value=[]), \
             patch('tools.fact_applied_review._legacy_bcdp_device_table', return_value=domains), \
             patch('tools.fact_applied_review._bcdp_digit_group_rule', side_effect=digit_rule):
            _repair_fx_bcd_native_operand_ranges(Source(), records, facts)

        for model, manual, _revision, page in case_rows:
            typed, = facts[model, 'BCD']
            operands = {item['name']: item for item in typed['value']['operands']}
            self.assertEqual(operands['S']['range']['minimum'], 0)
            self.assertEqual(operands['S']['range']['maximum'], 9999)
            self.assertEqual(operands['S']['range']['digit_groups_allowed'], [1, 2, 3, 4])
            self.assertEqual(operands['D']['range']['digit_group_device_prefixes'], ['Y', 'M', 'S'])
            self.assertEqual(operands['S']['word_device_layout']['word_count'], 1)
            self.assertIn({'manual_id': manual, 'pdf_pages': [page]},
                          [{key: ref.get(key) for key in ('manual_id', 'pdf_pages')}
                           for ref in typed['sources']])

    def test_same_heading_support_and_all_blank_forms_are_a_reported_conflict_not_verified_exclusion(self):
        import sqlite3
        from tools.fact_applied_review import reviewed_applied_behavior
        record = LegacySourceCompilationTests.literal_record()
        record['text'] = '5.1.1 SYNTH (FNC 10) 16 bit. SYNTH: 7 steps'
        record['table_groups'][0][-1][-1] = 'SYNTH: 7 steps'
        record['applicability']['model_marks'][0]['state'] = 'unsupported'
        record['source_form_models'] = {kind: {cpu: False for cpu in record['manual_models']}
                                        for kind in ['16', '32', 'pulse']}
        source = sqlite3.connect(':memory:'); self.addCleanup(source.close); source.row_factory = sqlite3.Row
        source.execute('CREATE TABLE instructions (opcode)'); source.execute("INSERT INTO instructions VALUES ('SYNTH')")
        source.execute('CREATE TABLE manuals (manual_id,plc_models)')
        source.execute("INSERT INTO manuals VALUES ('jy992d48301j','FX0,FX2C')")
        source.execute('CREATE TABLE chunks (manual_id,document_plc_models)')
        with ExitStack() as stack:
            for name, value in [('_instruction_records', [record]), ('_chunk_records', []),
                ('_source_table_groups', record['table_groups']), ('_parameter_source_pages', []),
                ('_native_parameter_conventions', record['parameter_conventions'])]:
                stack.enter_context(patch('tools.fact_applied_review.' + name, return_value=value))
            stack.enter_context(patch('tools.fact_applied_review._legacy_variants', return_value=[
                {'opcode': 'SYNTH', 'width_bits': 16, 'execution_form': 'continuous', 'call_form': None}]))
            facts, receipt = reviewed_applied_behavior(source, manuals=['jy992d48301j'],
                                                       page_text={}, page_tables={}, cpu_catalogue={})
        rejected, = facts['FX0', 'SYNTH']
        self.assertEqual(rejected['status'], 'source_verified'); self.assertIs(rejected['value']['available'], False)
        conflicted = facts['FX2C', 'SYNTH']
        availability, = [row for row in conflicted if row['dimension'] == 'constraints.cpu_availability']
        self.assertEqual(availability['status'], 'conflict'); self.assertNotIn('available', availability['value'])
        self.assertEqual(availability['value']['model_states'], {'FX2C': 'conflict'})
        declarations = [row for row in conflicted if row['id'] in availability['depends_on']]
        self.assertEqual(len(declarations), 2)
        self.assertEqual({row['sources'][0]['representation'] for row in declarations},
                         {'instruction_header_cpu_strip', 'instruction_form_cpu_cells'})
        self.assertTrue(all(row['status'] == 'source_verified' for row in declarations))
        self.assertEqual(len(receipt['conflicts']), 1)
        self.assertTrue(any(row['dimension'] == 'parameters.typed_operands' and row['status'] == 'conflict'
                            for row in conflicted))


class ReviewedBinNativeSourceTests(unittest.TestCase):
    def test_legacy_bin_forms_use_their_own_width_and_pulse_marks(self):
        from unittest.mock import patch
        from tools.fact_applied_review import _legacy_bin_16bit_form_witness
        record = {'opcode': 'BIN', 'manual_id': 'jy992d48301j', 'revision': 'J',
            'source_form_models': {'16': {'FX2C': True}, 'pulse': {'FX2C': True}}}
        steps = {'documented_opcode': 'BIN',
                 'steps': 'BIN, BINP: 5 steps\nDBIN, DBINP: 9 steps'}
        with patch('tools.fact_applied_review._legacy_table_data', return_value=steps):
            self.assertTrue(_legacy_bin_16bit_form_witness(record, 'FX2C', 'BIN'))
            self.assertTrue(_legacy_bin_16bit_form_witness(record, 'FX2C', 'BINP'))
            record['source_form_models']['pulse']['FX2C'] = False
            self.assertFalse(_legacy_bin_16bit_form_witness(record, 'FX2C', 'BINP'))
            record['source_form_models']['pulse']['FX2C'] = True
            with patch('tools.fact_applied_review._legacy_table_data', return_value={
                    'documented_opcode': 'BIN', 'steps': 'BIN, BINP: 9 steps'}):
                self.assertFalse(_legacy_bin_16bit_form_witness(record, 'FX2C', 'BIN'))

    def test_r_bin_header_binds_both_forms_to_its_own_page_versions(self):
        from unittest.mock import patch
        from tools.fact_applied_review import _r_bin_header_reviews
        record = {'opcode': 'BIN', 'manual_id': 'fx3_programming_r', 'revision': 'R',
            'section': '9.10 FNC 19 – BIN / Conversion to Binary',
            'pdf_pages': [269, 270, 271]}
        with patch('tools.fact_applied_review._programming_manual_variants', return_value=[
                {'opcode': 'BIN', 'width_bits': 16, 'execution_form': 'continuous'},
                {'opcode': 'BINP', 'width_bits': 16, 'execution_form': 'pulse'}]):
            review, = _r_bin_header_reviews([record])
        self.assertEqual(review['pdf_page'], 269)
        self.assertEqual(review['source']['pdf_pages'], [269])
        self.assertEqual({row['model']: row.get('minimum_version') for row in review['model_marks']},
            {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40', 'FX3U': '2.20', 'FX3UC': '1.00'})
        with patch('tools.fact_applied_review._programming_manual_variants', return_value=[
                {'opcode': 'BIN', 'width_bits': 16, 'execution_form': 'continuous'}]):
            self.assertEqual(_r_bin_header_reviews([record]), [])

    def test_j_bin_all_units_claim_changes_only_continuous_availability(self):
        from types import SimpleNamespace
        from tools.fact_applied_review import _j_bin_all_units_models, _repair_fx_bin_source
        models = ['FX0', 'FX0S', 'FX0N', 'FXU', 'FX1', 'FX2C', 'FX2N', 'FX2NC']
        cells = [
            {'label': 'FX0(S)', 'models': ['FX0', 'FX0S']},
            {'label': 'FX0N', 'models': ['FX0N']},
            {'label': 'FX', 'models': ['FXU', 'FX1']},
            {'label': 'FX(2C)', 'models': ['FX2C']},
            {'label': 'FX2N(C)', 'models': ['FX2N', 'FX2NC']},
        ]
        heading = {'opcode': 'BIN', 'section': '5.2.10 BIN (FNC 19)',
            'text': ('Operation 1: (Applicable to all units) The BCD source data (S) is '
                     'converted into an equivalent binary number and stored at the destination device (D).'),
            'form_models': {'16': {model: True for model in models},
                            'pulse': {model: model not in {'FX0', 'FX0S', 'FX0N'} for model in models}}}
        source = SimpleNamespace(instruction_layouts={'jy992d48301j': {
            'kind': 'legacy_shading', 'cells': cells}},
            legacy_page=lambda manual, page: {'legacy_sections': [heading]})
        record = {'opcode': 'BIN', 'manual_id': 'jy992d48301j', 'revision': 'J',
            'section': '5.2.10 BIN (FNC 19)', 'pdf_pages': [148, 149], 'manual_models': models}
        self.assertEqual(set(_j_bin_all_units_models(source, record)), set(models))
        facts = {(model, 'BIN'): [{'id': 'authored:applied:cpu_availability@jy992d48301j',
            'dimension': 'constraints.cpu_availability', 'status': 'unknown',
            'verification': 'source_checked', 'scope': {'models': [model], 'forms': ['BIN']},
            'sources': [{'manual_id': 'jy992d48301j', 'pdf_pages': [148]}],
            'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
            'value': {'available': 'unknown', 'model_states': {model: 'unknown'}}}]
            for model in models}
        _repair_fx_bin_source(source, [record], facts)
        for model in models:
            availability, = facts[model, 'BIN']
            self.assertIs(availability['value']['available'], True)
            self.assertEqual(availability['value']['model_states'], {model: 'supported'})
            self.assertIn({'representation': 'Operation_1_all_units_and_continuous_16_bit_BIN',
                'manual_id': 'jy992d48301j', 'manual_number': 'JY992D48301',
                'revision': 'J', 'pdf_pages': [148], 'id': 'page_text:jy992d48301j:148:operation_1'},
                availability['sources'])
        self.assertEqual(facts.get(('FX0', 'BINP')), None)

        heading['text'] = heading['text'].replace('all units', 'FX(2C)')
        unknown = {'opcode': 'BIN', 'manual_id': 'jy992d48301j', 'revision': 'J',
            'section': '5.2.10 BIN (FNC 19)', 'pdf_pages': [148, 149], 'manual_models': models}
        self.assertEqual(_j_bin_all_units_models(source, unknown), [])

    def test_r_bin_source_binds_s_d_directions_and_version_for_both_forms(self):
        from unittest.mock import patch
        from tools.fact_applied_review import _repair_fx_bin_source
        record = {'opcode': 'BIN', 'manual_id': 'fx3_programming_r', 'revision': 'R',
            'pdf_pages': [269, 270, 271], 'text': '16-bit operation (BIN and BINP)'}
        pages = [{'layout': ('Word device number storing the conversion source (binary-coded decimal) data. '
                              'Word device number of the conversion destination (binary).')}]
        versions = {'FX3S': '1.00', 'FX3G': '1.00', 'FX3GC': '1.40',
                    'FX3U': '2.20', 'FX3UC': '1.00'}
        facts = {}
        for model, minimum in versions.items():
            for opcode in ('BIN', 'BINP'):
                facts[model, opcode] = [{'id': 'authored:applied:native_typed_operands@fx3_programming_r',
                    'dimension': 'parameters.typed_operands', 'status': 'declared_unverified',
                    'verification': 'not_performed', 'scope': {'models': [model], 'forms': [opcode]},
                    'sources': [{'manual_id': 'fx3_programming_r', 'pdf_pages': [269]}],
                    'conditions': [{'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}],
                    'value': {'operands': [{'name': 'S', 'type': 'ANY16', 'direction': 'input'},
                                           {'name': 'D', 'type': 'ANY16', 'direction': 'input'}]}}]
        with patch('tools.fact_applied_review._programming_manual_variants', return_value=[
                {'opcode': 'BIN', 'width_bits': 16, 'execution_form': 'continuous'},
                {'opcode': 'BINP', 'width_bits': 16, 'execution_form': 'pulse'}]), \
             patch('tools.fact_applied_review._parameter_source_pages', return_value=pages), \
             patch('tools.fact_applied_review._native_parameter_conventions', return_value={
                 'source_destination_symbols': True, 'pdf_pages': [176, 177]}):
            _repair_fx_bin_source(object(), [record], facts)
        for model, minimum in versions.items():
            for opcode in ('BIN', 'BINP'):
                typed, = facts[model, opcode]
                operands = {item['name']: item for item in typed['value']['operands']}
                self.assertEqual((operands['S']['direction'], operands['D']['direction']),
                                 ('input', 'output'))
                self.assertIn({'context': 'project_mode', 'equals': 'native_ladder_instruction'},
                              typed['conditions'])
                self.assertIn({'context': 'cpu_version', 'minimum': minimum}, typed['conditions'])
                self.assertEqual(typed['status'], 'source_verified')
                self.assertIn({'manual_id': 'fx3_programming_r', 'pdf_pages': [176, 177]},
                    [{key: ref.get(key) for key in ('manual_id', 'pdf_pages')} for ref in typed['sources']])

    def test_bin_ranges_are_own_form_and_keep_r_numeric_limit_separate(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from tools.fact_applied_review import _repair_fx_bin_native_operand_ranges
        versions = {'FX3U': '2.20'}
        manuals = [('jy992d88101e', 'E', 'FX1N', 147),
                   ('jy992d48301j', 'J', 'FX2C', 148),
                   ('fx3_programming_r', 'R', 'FX3U', 269)]
        records, facts = [], {}
        for manual, revision, model, page in manuals:
            records.append({'manual_id': manual, 'revision': revision, 'opcode': 'BIN',
                            'pdf_pages': [page], 'text': 'own BIN row'})
            for opcode in ('BIN', 'BINP'):
                conditions = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
                if manual == 'fx3_programming_r':
                    conditions.append({'context': 'cpu_version', 'minimum': versions[model]})
                facts[model, opcode] = [{'id': 'authored:applied:native_typed_operands'
                    + ('' if revision == 'R' else ':default') + '@' + manual,
                    'dimension': 'parameters.typed_operands', 'scope': {'models': [model], 'forms': [opcode]},
                    'sources': [{'manual_id': manual, 'pdf_pages': [page]}],
                    'conditions': conditions,
                    'value': {'representation': 'native_ladder_instruction', 'operands': [
                        {'name': 'S', 'type': 'ANY16', 'direction': 'input'},
                        {'name': 'D', 'type': 'ANY16', 'direction': 'output'}]}}]
        word_domains = {'S': {'KnX', 'KnY', 'KnM', 'KnS', 'T', 'C', 'D', 'V', 'Z'},
                        'D': {'KnY', 'KnM', 'KnS', 'T', 'C', 'D', 'V', 'Z'}}
        r_domains = {'S': word_domains['S'] | {'R', r'U\G', 'Modifier'},
                     'D': word_domains['D'] | {'R', r'U\G', 'Modifier'}}
        digit = lambda _source, manual: {'digit_groups_allowed': [1, 2, 3, 4],
            'source': {'manual_id': manual, 'pdf_pages': [162 if manual.endswith('_r') else
                110 if manual.endswith('_e') else 115]}}
        def legacy_variants(_record):
            return [{'opcode': 'BIN', 'width_bits': 16, 'execution_form': 'continuous'},
                    {'opcode': 'BINP', 'width_bits': 16, 'execution_form': 'pulse'}]
        with patch('tools.fact_applied_review._source_table_groups', return_value=[]), \
             patch('tools.fact_applied_review._legacy_bin_device_table', return_value=word_domains), \
             patch('tools.fact_applied_review._r_bin_device_table', return_value=r_domains), \
             patch('tools.fact_applied_review._legacy_bin_16bit_form_witness', return_value=True), \
             patch('tools.fact_applied_review._programming_manual_variants', side_effect=legacy_variants), \
             patch('tools.fact_applied_review._parameter_source_pages', return_value=[{
                 'layout': '16-bit operation (BIN and BINP); the data of S can be converted if it is ranging from K0 to K9999 (BCD)'}]), \
             patch('tools.fact_applied_review._bcdp_digit_group_rule', side_effect=digit):
            _repair_fx_bin_native_operand_ranges(SimpleNamespace(), records, facts)
        for manual, revision, model, page in manuals:
            for opcode in ('BIN', 'BINP'):
                typed, = facts[model, opcode]
                operands = {item['name']: item for item in typed['value']['operands']}
                for name in ('S', 'D'):
                    limits = operands[name]['range']
                    self.assertEqual(limits['digit_groups_allowed'], [1, 2, 3, 4])
                    self.assertEqual(limits['literal_prefixes'], [])
                    self.assertEqual(operands[name]['word_device_layout']['word_count'], 1)
                    if revision != 'R':
                        self.assertNotIn('minimum', limits)
                        self.assertNotIn('maximum', limits)
                if revision == 'R':
                    self.assertEqual((operands['S']['range']['minimum'], operands['S']['range']['maximum']),
                                     (0, 9999))
                    self.assertEqual(operands['S']['range']['address_forms'], ['buffer_memory'])
                    self.assertEqual(operands['D']['range']['address_forms'], ['buffer_memory'])
                    self.assertEqual(typed['value']['device_footnote_scope'], {
                        'R': ['FX3G', 'FX3GC', 'FX3U', 'FX3UC'],
                        r'U\G': ['FX3U', 'FX3UC'], 'source_page': 269})


class ReviewedArityTests(unittest.TestCase):
    def test_fx1n_p_forms_check_known_ranges_without_inferring_native_widths(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry, reviewed_native_operand_parameters
        from plc.validation import validate_ladder_full, PLCJsonValidationError
        def validate(opcode, operands):
            return validate_ladder_full({'device_comments': {}, 'rungs': [{'rung_id': 1,
                'branches': [{'inputs': [{'type': 'NO', 'address': 'X0'}], 'outputs': [
                    {'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}]}, plc_model='FX1N')
        for opcode, bit_operands in [('DECOP', ['D0', 'M100']), ('ENCOP', ['M100', 'D0'])]:
            for operands in [bit_operands + ['K0'], bit_operands + ['K8'], bit_operands + ['H8'],
                             ['D0', 'D10', 'K4'], ['D0', 'D10', 'H4']]:
                with self.subTest(opcode=opcode, operands=operands, accepted=True):
                    validate(opcode, operands)
            for operands in [bit_operands + ['K9'], bit_operands + ['H9'], bit_operands + ['K-1'],
                             ['D0', 'D10', 'K5'], ['D0', 'D10', 'H5'],
                             bit_operands + ['D20'], bit_operands + ['K1Z0']]:
                with self.subTest(opcode=opcode, operands=operands, accepted=False):
                    with self.assertRaises(PLCJsonValidationError):
                        validate(opcode, operands)
            spec = registry.resolve(opcode, cpu='FX1N')
            self.assertNotIn('operand_types', spec.verified_fields)
            descriptions = reviewed_native_operand_parameters(spec, opcode, bit_operands + ['K4'], 'FX1N')
            known_ranges = [p for p in descriptions[2] if p.get('range', {}).get('maximum') == 8]
            self.assertTrue(known_ranges)
            self.assertTrue(all(p.get('width_bits') == 'unknown' and p.get('type_completeness') == 'partial'
                                for p in known_ranges))

    def test_source_ranges_remain_enforced_when_operand_width_is_unresolved(self):
        from plc.instructions import InstructionSpec
        from plc.instruction_definition import InstructionFactGroup
        from plc.validation import _validate_source_operand_types, PLCJsonValidationError
        declaration = fact('synthetic-native-range', 'parameters.typed_operands',
            scope={'models': ['FX1N'], 'forms': ['SYNTH'], 'languages': ['ladder']},
            conditions=[{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
            value={'representation': 'native_ladder_instruction', 'operand_count': 3, 'operands': [
                {'name': 'S', 'direction': 'input', 'range': {'index_modification': False}},
                {'name': 'D', 'direction': 'output'},
                {'name': 'n', 'direction': 'input', 'width_bits': 'unknown',
                 'type_completeness': 'partial', 'range': {'minimum': 0, 'maximum': 8,
                 'literal_prefixes': ['K', 'H'], 'device_prefixes': [],
                 'maximum_by_operand': {'parameter': 'D', 'cases': [
                     {'device_prefixes': ['D'], 'maximum': 4}]}}}]})
        def spec(raw=declaration):
            return InstructionSpec('SYNTH', min_operands=3, max_operands=3,
                native_operand_order=('S', 'D', 'n'), cpu_support=frozenset({'FX1N'}),
                verified_fields=('arity', 'operand_order'),
                definition_facts=(InstructionFactGroup.from_mapping(raw),))
        original = spec()
        for operands in [['D0', 'M100', 'K8'], ['D0', 'M100', 'H8'],
                         ['D0', 'D10', 'K4'], ['D0', 'D10', 'K0']]:
            with self.subTest(operands=operands, accepted=True):
                _validate_source_operand_types(original, operands, 'instruction', 'FX1N')
        for operands in [['D0', 'M100', 'K9'], ['D0', 'M100', 'H9'],
                         ['D0', 'M100', 'K-1'], ['D0', 'D10', 'K5'],
                         ['D0', 'D10', 'H5'], ['D0Z0', 'M100', 'K4'],
                         ['D0', 'M100', 'D20']]:
            with self.subTest(operands=operands, accepted=False):
                with self.assertRaises(PLCJsonValidationError):
                    _validate_source_operand_types(original, operands, 'instruction', 'FX1N')
        self.assertNotIn('operand_types', original.verified_fields)
        self.assertEqual(original.operands, ())
        unverified = copy.deepcopy(declaration)
        unverified['status'] = 'declared_unverified'
        _validate_source_operand_types(spec(unverified), ['D0', 'D10', 'K9'], 'instruction', 'FX1N')
        _validate_source_operand_types(original, ['D0', 'D10', 'K9'], 'instruction', 'FX3U')

    def test_fx1n_p_forms_reject_wrong_counts_without_expanding_the_type_contract(self):
        from plc.ir import build_plc_ir, validate_plc_ir, PLCIRValidationError
        from plc.validation import PLCJsonValidationError
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        def validate(opcode, operands, cpu='FX1N'):
            program = {'device_comments': {}, 'rungs': [{'rung_id': 1, 'branches': [{
                'inputs': [{'type': 'NO', 'address': 'X0'}],
                'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}]}
            return validate_plc_ir(build_plc_ir(program, plc_model=cpu))
        for opcode, operands in [('DECOP', ['D0', 'M100', 'K4']), ('ENCOP', ['M100', 'D0', 'K4'])]:
            with self.subTest(opcode=opcode, kind='valid'):
                validate(opcode, operands)
                spec = registry.resolve(opcode, cpu='FX1N')
                self.assertEqual(spec.contract_coverage()['operand_types'], 'unresolved')
                self.assertEqual(spec.contract_coverage()['device_classes'], 'unresolved')
                self.assertNotIn('operand_types', spec.verified_fields)
            for wrong in [[], ['D0'], ['D0', 'D10', 'K4', 'D20']]:
                with self.subTest(opcode=opcode, operands=wrong):
                    with self.assertRaises((PLCIRValidationError, PLCJsonValidationError)):
                        validate(opcode, wrong)
            for cpu in ['FX0', 'FX0N', 'FX0S']:
                with self.subTest(opcode=opcode, cpu=cpu):
                    with self.assertRaises((PLCIRValidationError, PLCJsonValidationError)):
                        validate(opcode, operands, cpu)


class ReviewedReffTests(unittest.TestCase):
    def test_native_forms_preserve_the_complete_device_and_constant_domain(self):
        from plc.validation import validate_ladder_full, PLCJsonValidationError
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for cpu in ['FX3U', 'FX3UC']:
            for opcode in ['REFF', 'REFFP']:
                form = registry.resolve_form(opcode, cpu=cpu)
                self.assertEqual(form.pulse, opcode == 'REFFP')
                self.assertFalse(form.double)
                for args, accepted in [
                        (['D0'], True), (['R0'], True), (['K0'], True), (['K60'], True),
                        (['H0'], True), (['H3C'], True), ([], False), (['K1', 'K2'], False),
                        (['K-1'], False), (['K61'], False), (['H3D'], False),
                        (['X0'], False), (['T0'], False), (['C0'], False),
                        (['E1.0'], False), (['P0'], False), (['D0Z0'], False), (['R0V0'], False)]:
                    program = {'device_comments': {}, 'rungs': [{'rung_id': 1, 'branches': [{
                        'inputs': [{'type': 'NO', 'address': 'X0'}],
                        'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': args}]}]}]}
                    with self.subTest(cpu=cpu, opcode=opcode, operands=args):
                        if accepted:
                            validate_ladder_full(program, plc_model=cpu)
                        else:
                            with self.assertRaises(PLCJsonValidationError):
                                validate_ladder_full(program, plc_model=cpu)

    def test_runtime_filter_values_and_minimum_versions_remain_executable_constraints(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        from plc.instruction_constraints import check_fact_constraints, check_instruction_constraints
        for cpu, minimum, lower in [('FX3U', '2.20', '2.19'), ('FX3UC', '1.00', '0.99')]:
            for opcode in ['REFF', 'REFFP']:
                facts = registry.definition_entry(opcode, cpu=cpu)['facts']
                ranges = [f['id'] for f in facts if f['value'].get('rule') == 'REFF_FILTER_TIME_0_TO_60']
                versions = [f['id'] for f in facts if f['value'].get('rule') == 'REFF_MINIMUM_CPU_VERSION']
                self.assertTrue(ranges)
                self.assertTrue(versions)
                context = {'project_mode': 'native_ladder_instruction', 'program_context': 'ladder',
                           'hardware': {'cpu_version': minimum}}
                for value, expected in [(None, 'unverified'), (0, 'passed'), (60, 'passed'),
                                        (61, 'failed'), (-1, 'failed')]:
                    bound = {} if value is None else {'n': value}
                    with self.subTest(cpu=cpu, opcode=opcode, filter_value=value):
                        report = check_fact_constraints(facts, bound, target=opcode, plc_model=cpu,
                            language='ladder', context=context, root_ids=ranges)
                        self.assertEqual(report['status'], expected)
                        full = check_instruction_constraints(opcode, {**bound, '$OPERANDS': ['D0']},
                            plc_model=cpu, language='ladder', context=context, registry=registry)
                        if expected == 'failed':
                            self.assertEqual(full['status'], 'failed')
                for version, expected in [(None, 'unverified'), (lower, 'failed'), (minimum, 'passed')]:
                    selected = {k: v for k, v in context.items() if k != 'hardware'}
                    if version is not None:
                        selected['hardware'] = {'cpu_version': version}
                    with self.subTest(cpu=cpu, opcode=opcode, version=version):
                        report = check_fact_constraints(facts, {'n': 1}, target=opcode, plc_model=cpu,
                            language='ladder', context=selected, root_ids=versions)
                        self.assertEqual(report['status'], expected)


class ReviewedZeroDataAdmissionTests(unittest.TestCase):
    def test_explicit_empty_native_interface_restores_construction_admission_without_certifying_behavior(self):
        from knowledge.instruction_compiler import refresh_reviewed_call_admission
        from plc.instructions import InstructionRegistry
        cpu, opcode = 'FX3U', 'SYNTH_ZERO'
        contract = {'method': 'reviewed-manual-contract-v1', 'cpus': [cpu],
            'instruction': {'mnemonic': opcode, 'cpu_support': [cpu], 'contract_level': 'signature',
                            'arity': {'min': 0, 'max': 0}, 'operands': []},
            'native_order': [], 'execution_form': 'continuous',
            'verified_fields': ['form_identity', 'cpu_applicability', 'arity', 'operand_order',
                                'operand_roles', 'operand_types'],
            'sources': [{'models': [cpu], 'manual_id': 'synthetic_zero', 'manual': 'Synthetic zero',
                         'revision': 'A', 'pdf_page': 7, 'url': 'https://example.invalid/zero'}]}
        registry = InstructionRegistry(); registry.load_target_contracts([contract])
        scope = {'models': [cpu], 'forms': [opcode], 'languages': ['ladder']}
        def fact(identity, dimension, value):
            return {'id': identity, 'dimension': dimension, 'value': value, 'scope': copy.deepcopy(scope),
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic_zero', 'revision': 'A', 'pdf_pages': [7]}]}
        entry = {'target_model': cpu, 'opcode': opcode, 'call_available': False,
            'semantic_completeness': 'incomplete', 'support_status': 'definition_only',
            'core_execution_available': False, 'gaps': ['effects_not_formalized'], 'facts': [
                fact('cpu', 'constraints.cpu_availability', {'available': True}),
                fact('parameters', 'parameters.typed_operands', {'declaration_kind': 'instruction',
                    'representation': 'native_ladder_instruction', 'operand_count': 0, 'operands': [],
                    'arity': {'minimum': 0, 'maximum': 0},
                    'native_form': {'operand_order': [], 'minimum_operands': 0, 'maximum_operands': 0}})]}
        before = copy.deepcopy(entry)
        self.assertTrue(refresh_reviewed_call_admission(entry, registry))
        self.assertIs(entry['call_available'], True)
        self.assertEqual(entry['support_status'], 'reviewed_native_signature')
        for field in ['facts', 'gaps', 'semantic_completeness', 'core_execution_available']:
            self.assertEqual(entry[field], before[field])
        for mutation in ['missing_operands', 'missing_kind', 'missing_representation', 'missing_count',
            'boolean_count', 'string_count', 'wrong_kind', 'wrong_representation', 'wrong_arity',
            'wrong_native_order', 'wrong_native_count', 'weak_source', 'unchecked_source', 'foreign_source',
            'foreign_page', 'mismatched_condition']:
            changed = copy.deepcopy(before); source = changed['facts'][1]; value = source['value']
            if mutation.startswith('missing_'):
                key = {'missing_kind': 'declaration_kind', 'missing_count': 'operand_count'}.get(
                    mutation, mutation.removeprefix('missing_'))
                value.pop(key)
            elif mutation == 'boolean_count': value['operand_count'] = False
            elif mutation == 'string_count': value['operand_count'] = '0'
            elif mutation == 'wrong_kind': value['declaration_kind'] = 'function'
            elif mutation == 'wrong_representation': value['representation'] = 'structured_ladder_block'
            elif mutation == 'wrong_arity': value['arity']['maximum'] = 1
            elif mutation == 'wrong_native_order': value['native_form']['operand_order'] = ['D']
            elif mutation == 'wrong_native_count': value['native_form']['maximum_operands'] = 1
            elif mutation == 'weak_source': source['status'] = 'declared_unverified'
            elif mutation == 'unchecked_source': source['verification'] = 'not_performed'
            elif mutation == 'foreign_source': source['sources'][0]['manual_id'] = 'another_manual'
            elif mutation == 'foreign_page': source['sources'][0]['pdf_pages'] = [8]
            else: source['conditions'] = [{'context': 'cpu_version', 'minimum': '2.20'}]
            with self.subTest(mutation=mutation):
                self.assertFalse(refresh_reviewed_call_admission(changed, registry))
                self.assertIs(changed['call_available'], False)
                self.assertEqual(changed['facts'], [before['facts'][0], source])
        native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        guarded = copy.deepcopy(before)
        for source in guarded['facts']:
            source['conditions'] = [copy.deepcopy(native)]
        guarded_facts = copy.deepcopy(guarded['facts'])
        self.assertTrue(refresh_reviewed_call_admission(guarded, registry))
        self.assertEqual(guarded['facts'], guarded_facts)
        self.assertEqual(guarded['call_admission_details']['applicability_cases'], [[native]])
        self.assertIs(guarded['core_execution_available'], False)
        self.assertEqual(guarded['semantic_completeness'], 'incomplete')
        for conditions in [[{'context': 'project_mode', 'equals': 'structured_ladder_block'}],
                           [{'context': 'project_mode', 'equals': 'st'}],
                           [{**native, 'extra': True}],
                           [native, {'context': 'another_unresolved_requirement'}]]:
            changed = copy.deepcopy(before)
            for source in changed['facts']:
                source['conditions'] = copy.deepcopy(conditions)
            with self.subTest(conditions=conditions):
                self.assertFalse(refresh_reviewed_call_admission(changed, registry))


class FactAwareDiscoveryTests(unittest.TestCase):
    @staticmethod
    def reviewed_record(opcode, source=None):
        value = {'opcode': opcode, 'target_model': 'FX3U', 'facts': [],
                 'source_materials': [copy.deepcopy(source)] if source else []}
        body = {'kind': 'instruction', 'target': opcode, 'scope': {'models': ['FX3U']},
                'content_origin': 'authored_fact', 'value': value}
        return {'id': 'synthetic:' + opcode, 'text': json.dumps(body),
                'instruction_opcode': opcode, 'fact_kind': 'instruction', 'fact_target': opcode,
                'manual_type': '', 'chunk_type': 'instruction', 'source_lane': 'fact',
                'content_origin': 'authored_fact', 'distribution_status': 'fact_representation_reviewed'}

    def test_reviewed_fact_sql_and_post_filter_agree_without_trusting_lane_labels(self):
        import sqlite3
        from knowledge.scope import source_sql, filter_records, source_kind
        records = [dict(id=identity, manual_type=manual, chunk_type=chunk, manual_id='',
                        content_origin=origin, distribution_status=status, source_lane='fact')
                   for identity, manual, chunk, origin, status in [
                       ('reviewed', '', 'instruction', 'authored_fact', 'fact_representation_reviewed'),
                       ('raw', '', 'instruction', 'authored_fact', 'raw'),
                       ('unreviewed', '', 'instruction', 'authored_fact', 'unreviewed'),
                       ('wrong_origin', '', 'instruction', 'raw_manual', 'fact_representation_reviewed'),
                       ('label_only', '', 'instruction', '', ''),
                       ('legacy', 'programming', 'instruction', '', ''),
                       ('support', 'third_party_skill', 'instruction', 'authored_fact', 'fact_representation_reviewed'),
                       ('debug', 'debug_cases', 'debug_case', 'authored_fact', 'fact_representation_reviewed'),
                       ('design', 'curated_design', 'design_pattern', 'authored_fact', 'fact_representation_reviewed')]]
        self.assertEqual([r['id'] for r in filter_records(records, ('fact',))], ['reviewed', 'legacy'])
        self.assertEqual([source_kind(r) for r in records[1:5]], ['unclassified'] * 4)
        for modern in (False, True):
            with self.subTest(modern=modern), sqlite3.connect(':memory:') as db:
                columns = ['id', 'manual_type', 'chunk_type', 'manual_id']
                if modern:
                    columns += ['content_origin', 'distribution_status']
                db.execute('CREATE TABLE chunks (' + ','.join(name + ' TEXT' for name in columns) + ')')
                db.executemany('INSERT INTO chunks VALUES (' + ','.join('?' for _ in columns) + ')',
                               [[r.get(name, '') for name in columns] for r in records])
                schema = {'chunks': {'name': 'chunks', 'columns': set(columns)}}
                predicate, args = source_sql(db, schema, ('fact',))
                actual = [r[0] for r in db.execute('SELECT id FROM chunks WHERE ' + predicate, args)]
                self.assertEqual(actual, ['reviewed', 'legacy'] if modern else ['legacy'])

    def test_function_queries_keep_metadata_and_do_not_select_expansion_opcodes(self):
        from knowledge.capability_discovery import FUNCTION_TERMS
        from knowledge.evidence import KnowledgeQuery
        for function in ('loop', 'queue'):
            for explicit in (False, True):
                with self.subTest(function=function, explicit=explicit), ExitStack() as stack:
                    query = KnowledgeQuery('PLC ' + next(term[2] for term in FUNCTION_TERMS if term[0] == function),
                        metadata={'semantic_function': function, 'hardware_context': {'slot': 1}},
                        precompiled=True, truncated=True)
                    targets = {'instructions': [{'opcode': 'SYNTH', 'base_opcode': 'SYNTH'}]} if explicit else None
                    stack.enter_context(patch('knowledge.structured_facts.structured_fact_targets',
                        side_effect=AssertionError('Expansion vocabulary is not an explicit target')))
                    for name in ('resolve_device_records', 'resolve_device_family_records', 'resolve_error_records',
                                 'resolve_language_records', 'resolve_positioning_records'):
                        stack.enter_context(patch('knowledge.structured_facts.' + name, return_value=[]))
                    resolve = stack.enter_context(patch('knowledge.structured_facts.resolve_instruction_records', return_value=[]))
                    retrieve = stack.enter_context(patch('knowledge.retriever.retrieve_knowledge', return_value=[]))
                    retrieve_fact_aware_knowledge(query, plc_model='FX3U', task_type='generate', targets=targets,
                                                 top_k=8, source_lanes=('fact',))
                    self.assertEqual(resolve.call_args.args[0], targets['instructions'] if explicit else ())
                    residual = retrieve.call_args.args[0]
                    self.assertIsInstance(residual, KnowledgeQuery)
                    self.assertEqual(residual.metadata, query.metadata)
                    self.assertTrue(residual.precompiled and residual.truncated)

    @staticmethod
    def admitted_st_registry():
        from knowledge.instruction_compiler import refresh_reviewed_structured_call_admission
        from plc.instructions import InstructionRegistry
        from test_structured_call_contract import fx_abs_facts, fx_abs_e_facts

        model = "FX3U"
        registry = InstructionRegistry([])
        entries = {}
        for opcode, facts in (("ABS", fx_abs_facts(model)), ("ABS_E", fx_abs_e_facts(model))):
            entry = {"opcode": opcode, "target_model": model, "call_available": False,
                     "facts": copy.deepcopy(facts)}
            if opcode == "ABS":
                signature = next(f for f in entry["facts"] if f["dimension"] == "parameters.st_signature")
                signature["conditions"] = [{"context": "project_mode", "equals": "structured_text"}]
            if not refresh_reviewed_structured_call_admission(entry, registry):
                raise AssertionError(f"test fixture {opcode} should have a reviewed ST signature")
            registry._definition_owners[("mitsubishi", opcode, model)] = entry
            entries[opcode] = entry
        return registry, entries

    def test_st_only_explicit_discovery_preserves_contract_and_stays_out_of_ladder_and_fbd(self):
        from knowledge.capability_discovery import discover

        registry, entries = self.admitted_st_registry()
        with patch("plc.instructions.DEFAULT_INSTRUCTION_REGISTRY", registry):
            result = discover("ABS", plc_model="FX3U", target_mode="st",
                explicit_targets=[{"opcode": "ABS"}], retrieve=lambda *args, **kwargs: [])
            card, = result["candidates"]
            self.assertEqual(card["name"], "ABS")
            self.assertEqual(card["target_call"]["interfaces"], ["st"])
            self.assertEqual(card["interface"][0]["type_alternatives"], ["INT", "DINT", "REAL"])
            self.assertEqual(card["st_call"]["return_type_alternatives"], ["INT", "DINT", "REAL"])
            self.assertFalse(card["target_call"]["conditions_evaluated"])
            self.assertIn({"context": "project_mode", "equals": "structured_text"},
                          [condition for row in card["target_call"]["applicability_cases"]
                           for condition in row["conditions"]])
            self.assertIsNone(card["execution"])
            self.assertIn("execution_semantics", card["unknown"])

            for mode in ("ladder", "fbd"):
                with self.subTest(mode=mode):
                    rejected = discover("ABS", plc_model="FX3U", target_mode=mode,
                        explicit_targets=[{"opcode": "ABS"}], retrieve=lambda *args, **kwargs: [])
                    self.assertEqual(rejected["candidates"], [])

            entries["ABS"]["facts"] = []
            stale_source = discover("ABS", plc_model="FX3U", target_mode="st",
                explicit_targets=[{"opcode": "ABS"}], retrieve=lambda *args, **kwargs: [])
            self.assertEqual(stale_source["candidates"], [])

        # Correct source facts alone do not publish an owner whose admission
        # was explicitly refused.
        refused_registry, refused_entries = self.admitted_st_registry()
        refused_entries["ABS"]["call_available"] = False
        refused_entries["ABS"]["support_status"] = "unreviewed"
        with patch("plc.instructions.DEFAULT_INSTRUCTION_REGISTRY", refused_registry):
            refused = discover("ABS", plc_model="FX3U", target_mode="st",
                explicit_targets=[{"opcode": "ABS"}], retrieve=lambda *args, **kwargs: [])
        self.assertEqual(refused["candidates"], [])

    def test_st_owner_is_discovered_from_retrieval_and_relation_expansion(self):
        from knowledge.capability_discovery import discover

        registry, _ = self.admitted_st_registry()
        seed = {"id": "synthetic:st-abs", "instruction_opcode": "ABS",
                "semantic_functions": ["structured_math"], "section": "absolute value",
                "function_relations": [{"id": "synthetic:st-abs-relation", "kind": "requires",
                    "from": "instruction:ABS", "to": "instruction:ABS_E", "plc_models": ["FX3U"],
                    "target_modes": ["st"], "preconditions": ["synthetic language-routing fixture"]}]}
        related = {"id": "synthetic:st-abs-e", "instruction_opcode": "ABS_E",
                   "semantic_functions": ["structured_math"], "section": "enabled absolute value"}

        def retrieve(query, **kwargs):
            if getattr(query, "metadata", None):
                return [seed]
            return [related] if query == "PLC ABS_E" else []

        with patch("plc.instructions.DEFAULT_INSTRUCTION_REGISTRY", registry), \
             patch("knowledge.capability_discovery.functional_queries",
                   return_value=[{"function": "structured_math", "query": "PLC absolute value"}]):
            result = discover("absolute value", plc_model="FX3U", target_mode="st",
                              retrieve=retrieve, relation_limit=1)

        self.assertEqual({candidate["name"] for candidate in result["candidates"]}, {"ABS", "ABS_E"})
        related_card = next(candidate for candidate in result["candidates"] if candidate["name"] == "ABS_E")
        self.assertEqual(related_card["functions"], ["relation_neighbor"])
        self.assertEqual(related_card["target_call"]["interfaces"], ["st"])
        self.assertEqual(related_card["target_call"]["st_parameters"][1]["type_alternatives"],
                         ["INT", "DINT", "REAL"])
        self.assertEqual([relation["id"] for relation in result["relations"]],
                         ["synthetic:st-abs-relation"])

    def test_mixed_native_and_st_owner_prefers_and_projects_only_its_st_contract(self):
        from knowledge.capability_discovery import _instruction_card

        class MixedRegistry:
            def describe_call(self, opcode, *, cpu):
                return {
                    "target_model": cpu, "opcode": opcode,
                    "interfaces": ["ladder", "st"], "st_contract_verified": True,
                    "st_signature": "ABS(_IN)",
                    "st_parameters": [{"name": "_IN", "role": "read", "type": "ANY_NUM",
                                       "type_alternatives": ["INT", "DINT", "REAL"]}],
                    "st_call": {"name": "ABS", "kind": "function", "returns": "ANY_NUM",
                                "return_type_alternatives": ["INT", "DINT", "REAL"]},
                    "st_contract_sources": [{"manual_id": "st-manual", "revision": "R",
                                             "pdf_pages": [5]}],
                    "source_contract_fact_ids": ["st-signature"],
                    "applicability_cases": [{"conditions": [{"context": "cpu_version",
                                                               "minimum": "2.00"}]}],
                    "conditions_evaluated": False,
                    "native_signature": "ABS D", "contract_sources": [
                        {"manual_id": "native-manual", "revision": "A", "pdf_pages": [99]}],
                }

            def resolve_form(self, *args, **kwargs):
                raise AssertionError("ST discovery must resolve its independent contract first")

        with patch("plc.instructions.DEFAULT_INSTRUCTION_REGISTRY", MixedRegistry()):
            card = _instruction_card("ABS", "FX3U", {"section": "absolute value"},
                                     target_mode="st")

        self.assertEqual(card["interface"][0]["type_alternatives"], ["INT", "DINT", "REAL"])
        self.assertEqual(card["target_call"]["interfaces"], ["st"])
        self.assertEqual(card["target_call"]["contract_sources"], [
            {"manual_id": "st-manual", "revision": "R", "pdf_pages": [5]}])
        self.assertNotIn("native_signature", card["target_call"])
        self.assertNotIn("native-manual", str(card))
        self.assertFalse(card["target_call"]["conditions_evaluated"])
        self.assertIn("execution_semantics", card["unknown"])

    def test_compiled_annotations_require_one_matching_reviewed_source(self):
        from knowledge.function_relations import annotate_source_records
        for opcode, page, relation in [('FOR', 243, 'fx3_for_next_pair'), ('SFWR', 314, 'fx3_fifo_writer_reader')]:
            source = {'manual_id': 'fx3_programming_r', 'revision': 'R', 'pdf_pages': [page]}
            original = self.reviewed_record(opcode, source)
            self.assertEqual(annotate_source_records([original])[0]['function_relations'][0]['id'], relation)
            self.assertNotIn('function_relations', original)
            flat = {'id': 'legacy:' + opcode, 'instruction_opcode': opcode, 'manual_id': source['manual_id'],
                    'revision': 'R', 'pdf_page': page}
            self.assertEqual(annotate_source_records([flat])[0]['function_relations'][0]['id'], relation)
            for mutation in ('manual', 'revision', 'page', 'opcode', 'unreviewed', 'origin', 'split_source'):
                with self.subTest(opcode=opcode, mutation=mutation):
                    changed = copy.deepcopy(original)
                    body = json.loads(changed['text'])
                    material = body['value']['source_materials'][0]
                    if mutation == 'manual': material['manual_id'] = 'another_manual'
                    elif mutation == 'revision': material['revision'] = 'other'
                    elif mutation == 'page': material['pdf_pages'] = [page + 1]
                    elif mutation == 'opcode': changed['instruction_opcode'] = 'SYNTH'
                    elif mutation == 'unreviewed': changed['distribution_status'] = 'unreviewed'
                    elif mutation == 'origin': changed['content_origin'] = 'raw_manual'
                    else:
                        body['value']['source_materials'] = [{'manual_id': source['manual_id']},
                                                           {'revision': 'R', 'pdf_pages': [page]}]
                    changed['text'] = json.dumps(body)
                    self.assertNotIn('function_relations', annotate_source_records([changed])[0])

    def test_relations_keep_cpu_language_and_candidate_conditions_in_model_projection(self):
        from knowledge.function_relations import annotate_source_records, expand_relations
        from shared.model_context import technical_block
        for opcode, page, endpoint, identity in [('FOR', 243, 'NEXT', 'fx3_for_next_pair'),
                                               ('SFWR', 314, 'SFRD', 'fx3_fifo_writer_reader')]:
            records = annotate_source_records([self.reviewed_record(opcode,
                {'manual_id': 'fx3_programming_r', 'revision': 'R', 'pdf_pages': [page]})])
            for model, mode, reason in [('FX3U', 'ladder', None), ('FX3G', 'ladder', 'exact_cpu_source_missing'),
                                        ('FX3U', 'st', 'language_scope_mismatch'), ('FX3U', 'fbd', 'language_scope_mismatch')]:
                with self.subTest(opcode=opcode, model=model, mode=mode):
                    retrieve = unittest.mock.Mock(return_value=[self.reviewed_record(endpoint)])
                    result = expand_relations([{'id': 'instruction:' + opcode}], records,
                        plc_model=model, target_mode=mode, retrieve=retrieve,
                        instruction_card=lambda op, cpu, source: {'id': 'instruction:' + op}, limit=4)
                    if reason:
                        self.assertEqual(result['relations'], [])
                        self.assertEqual(result['relation_omissions'], [{'id': identity, 'reason': reason}])
                        retrieve.assert_not_called()
                    else:
                        self.assertEqual([r['id'] for r in result['relations']], [identity])
                        relation = result['relations'][0]
                        self.assertEqual(relation['confirmation'], 'candidate')
                        self.assertEqual(relation['binding_status'], 'unverified')
                        projected = technical_block(relation)
                        value = json.loads(projected.split('\n')[1])
                        self.assertEqual(value['confirmation'], 'candidate')
                        self.assertEqual(value['preconditions'], relation['preconditions'])
                        self.assertEqual(value['target_modes'], ['ladder'])
                        self.assertNotIn('sources', value)

    def test_generation_manifest_binds_annotations_before_source_projection(self):
        from knowledge.function_relations import annotate_source_records
        from application.capability_context import augment_generation_knowledge
        from application.composition_context import CompositionExperiment, composition_scope
        from shared.model_context import technical_context_units
        source = self.reviewed_record('FOR',
            {'manual_id': 'fx3_programming_r', 'revision': 'R', 'pdf_pages': [243]})
        source['_explicit_target'] = True
        with patch('knowledge.retriever.retrieve_fact_aware_knowledge', return_value=[source]):
            context = build_knowledge_context('PLC FOR', plc_model='FX3U', task_type='analysis',
                                              language='ladder', top_k=1)
        record = next(r for r in context.manifest['records'] if r['id'] == source['id'])
        self.assertEqual([r['id'] for r in record['function_relations']], ['fx3_for_next_pair'])
        self.assertNotIn('text', record)
        self.assertNotIn('fx3_programming_r', str(context))
        self.assertEqual(annotate_source_records([record])[0]['function_relations'], record['function_relations'])
        with composition_scope(CompositionExperiment('C')), \
                patch('knowledge.structured_facts.resolve_instruction_records', side_effect=lambda targets, **kwargs:
                      [self.reviewed_record('NEXT')] if any(t['opcode'] == 'NEXT' for t in targets) else []), \
                patch('knowledge.retriever.retrieve_knowledge', return_value=[]):
            delivered = augment_generation_knowledge('', context, plc_model='FX3U', target_mode='ladder')
        discovery = delivered.manifest['capability_discovery']
        self.assertIn('relation:fx3_for_next_pair', discovery['included_composition_units'])
        relation = next(unit for unit in technical_context_units(delivered)
                        if unit.get('from') == 'instruction:FOR' and unit.get('to') == 'instruction:NEXT')
        self.assertEqual(relation['confirmation'], 'candidate')
        self.assertEqual(relation['preconditions'], record['function_relations'][0]['preconditions'])
        self.assertNotIn('sources', relation)
        self.assertNotIn('fx3_programming_r', str(delivered))

    def test_discovery_resolves_related_endpoints_through_fact_aware_retrieval(self):
        from knowledge.retriever import discover_capabilities
        cases = [('程序循环遍历', 'loop', 'FOR', 243, 'NEXT', 'fx3_for_next_pair'),
                 ('先进先出队列', 'queue', 'SFWR', 314, 'SFRD', 'fx3_fifo_writer_reader')]
        for requirement, function, opcode, page, endpoint, relation in cases:
            with self.subTest(function=function), ExitStack() as stack:
                seed = {'id': 'synthetic:seed:' + opcode, 'manual_id': 'fx3_programming_r', 'revision': 'R',
                        'pdf_page': page, 'instruction_opcode': opcode, 'manual_type': 'programming',
                        'section': 'synthetic ' + function, 'semantic_functions': [function]}
                common = stack.enter_context(patch('knowledge.retriever.retrieve_knowledge',
                    side_effect=lambda query, **kwargs: [seed] if getattr(query, 'metadata', {}).get('semantic_function') else []))
                resolved = []
                def resolve(targets, **kwargs):
                    resolved.extend(t['opcode'] for t in targets)
                    return [self.reviewed_record(endpoint)] if any(t['opcode'] == endpoint for t in targets) else []
                stack.enter_context(patch('knowledge.structured_facts.resolve_instruction_records', side_effect=resolve))
                stack.enter_context(patch('knowledge.structured_facts.fact_record_coverage', return_value=True))
                for name in ('resolve_device_records', 'resolve_device_family_records', 'resolve_error_records',
                             'resolve_language_records', 'resolve_positioning_records'):
                    stack.enter_context(patch('knowledge.structured_facts.' + name, return_value=[]))
                stack.enter_context(patch('knowledge.capability_discovery._instruction_card', side_effect=lambda op, cpu, source, **kwargs:
                    {'id': 'instruction:' + op, 'kind': 'instruction', 'name': op, 'functions': []}))
                result = discover_capabilities(requirement, plc_model='FX3U', target_mode='ladder', relation_limit=4)
                self.assertEqual([r['id'] for r in result['relations']], [relation])
                self.assertEqual({c['name'] for c in result['candidates']}, {opcode, endpoint})
                self.assertEqual(resolved, [endpoint])
                self.assertTrue(any(getattr(call.args[0], 'metadata', {}).get('semantic_function') == function
                                    for call in common.call_args_list))


class ConstraintParameterCaseTests(unittest.TestCase):
    @staticmethod
    def rule(first='n', second='m2', **extra):
        integer = {'kind': 'int', 'bits': 16, 'signed': True}
        return fact('relation', 'constraints.parameter_relations',
            scope={'models': ['FX3U'], 'forms': ['EXAMPLE'], 'languages': ['ladder']},
            value={'rule': 'count_within_limit', 'predicate': {'op': 'le', 'type': {'kind': 'bool'},
                'args': [{'op': 'parameter', 'name': first, 'type': integer},
                         {'op': 'parameter', 'name': second, 'type': integer}]}}, **extra)

    def test_parameter_spelling_binds_case_insensitively_without_changing_the_rule(self):
        from plc.instruction_constraints import check_fact_constraints
        for first, second in [('n', 'm2'), ('N', 'M2'), ('Amount', 'Limit')]:
            rule = self.rule(first, second)
            original = copy.deepcopy(rule)
            for transform in [str.lower, str.upper, str.swapcase]:
                for a, b, expected in [(2, 2, 'passed'), (3, 2, 'failed'), (-3, -2, 'passed'),
                                        (-2, -3, 'failed'), (-32768, 32767, 'passed')]:
                    with self.subTest(first=first, second=second, casing=transform.__name__, a=a, b=b):
                        values = {transform(first): a, transform(second): b}
                        report = check_fact_constraints([rule], values, target='EXAMPLE',
                            plc_model='FX3U', language='ladder')
                        self.assertEqual(report['status'], expected)
                        self.assertEqual(values, {transform(first): a, transform(second): b})
            self.assertEqual(rule, original)

    def test_guards_and_missing_values_keep_their_actual_meaning(self):
        from plc.instruction_constraints import check_fact_constraints
        def check(rule, values, context=None):
            return check_fact_constraints([rule], values, target='EXAMPLE', plc_model='FX3U',
                language='ladder', context=context)
        for values in [{'n': 2}, {'n': 2, 'm2': None}, {'n': 2, 'm2': 'D10'}]:
            with self.subTest(values=values):
                self.assertEqual(check(self.rule(), values)['status'], 'unverified')
        boolean = {'kind': 'bool'}
        guard = {'op': 'and', 'type': boolean, 'args': [
            {'op': 'parameter', 'name': 'go', 'type': boolean},
            {'op': 'state', 'name': 'machineReady', 'type': boolean}]}
        guarded = self.rule(conditions=[guard])
        report = check(guarded, {'GO': False, 'N': 3, 'M2': 2}, {'machineReady': True})
        self.assertEqual(report['checks'][0]['status'], 'not_applicable')
        self.assertEqual(check(guarded, {'go': True, 'n': 3, 'm2': 2},
                               {'machineReady': True})['status'], 'failed')
        self.assertEqual(check(guarded, {'go': True, 'n': 2, 'm2': 2},
                               {'MACHINEREADY': True})['status'], 'unverified')

    def test_named_numeric_range_can_exclude_a_constant_without_bounds(self):
        from plc.instruction_constraints import check_fact_constraints
        rule = fact('nonzero_divisor', 'constraints.numeric_ranges',
            scope={'models': ['L02'], 'forms': ['DIV_TIME'], 'languages': ['st']},
            value={'ranges': [{'binding': {'name': 's2'}, 'excluded_values': [0]}]})

        def check(values):
            return check_fact_constraints([rule], values, target='DIV_TIME', plc_model='L02',
                                          language='st')

        names = {'$PARAMETER_NAMES': ['s1', 's2']}
        self.assertEqual(check({**names, '$1': 0})['status'], 'failed')
        self.assertEqual(check({**names, '$1': 7})['status'], 'passed')
        self.assertEqual(check(names)['status'], 'unverified')
        for excluded_values in ([], [True], [float('nan')], [float('inf')], ['0']):
            invalid = copy.deepcopy(rule)
            invalid['value']['ranges'][0]['excluded_values'] = excluded_values
            with self.subTest(excluded_values=excluded_values):
                self.assertEqual(check_fact_constraints([invalid], {**names, '$1': 7},
                    target='DIV_TIME', plc_model='L02', language='st')['status'], 'unverified')


class CounterTableRelationPublicationTests(unittest.TestCase):
    def test_compiled_counter_table_relations_bind_the_count_and_output_head(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['ABSD', 'DABSD']:
            with self.subTest(opcode=opcode):
                owner = registry._definition_owner_store.get_entry(opcode, cpu='FX3U', vendor='mitsubishi')
                roots = [f for f in owner['facts'] if f['id'].startswith('family:ABSD:review20261028:')
                         and f['dimension'] == 'effects.documented_operation']
                self.assertTrue(roots)
                for root in roots:
                    bindings = root['value']['parameter_bindings']
                    relations = [f for f in owner['facts'] if f['dimension'] == 'effects.bound_relation'
                                 and root['id'] in f.get('depends_on', [])]
                    self.assertTrue(relations, root['id'])
                    for relation in relations:
                        value = relation['value']
                        self.assertEqual(set(value['inputs']),
                                         {bindings['table'], bindings['counter'], bindings['count']})
                        self.assertEqual(value['output'], bindings['destination'])
                        self.assertEqual(value['relation'], root['value']['relation'])
                        self.assertEqual(value['relation']['table_entry_bits'], 16 if opcode == 'ABSD' else 32)
                        self.assertEqual(value['storage']['table']['count_expression']['operands'][0],
                                         2 if opcode == 'ABSD' else 4)
                        self.assertEqual(value['storage']['destination']['count_operand'], bindings['count'])
                        self.assertNotIn('behavior', value)  # Knowledge binding does not add an executor.
                        if relation['scope'].get('languages') == ['fbd']:
                            self.assertEqual(value['operand_binding']['execution_arguments'], [
                                {'parameter': 'EN', 'role': 'execution_condition'},
                                {'parameter': 'ENO', 'role': 'execution_status'}])

    def test_counter_table_events_survive_the_default_model_projection(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        owner = registry._definition_owner_store.get_entry('ABSD', cpu='FX3U', vendor='mitsubishi')
        p = packet(owner['facts'], model='FX3U')
        p['target'] = p['value']['opcode'] = 'ABSD'
        projected = technical_fact_packet(p)
        text = json.dumps(projected, ensure_ascii=False)
        for content in ['counter_threshold_event_output', 'on_event', 'off_event',
                        'destination_bit_offset', 'table_entry_bits']:
            self.assertIn(content, text)


class SharedControlRelationPublicationTests(unittest.TestCase):
    def test_control_parameter_tables_publish_with_their_gate_roles_and_purposes(self):
        from knowledge.fact_corpus import project_definition
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['RAMP', 'SORT', 'INCD']:
            with self.subTest(opcode=opcode):
                owner = registry._definition_owner_store.get_entry(opcode, cpu='FX3U', vendor='mitsubishi')
                if opcode == 'INCD':
                    tables = [f for f in owner['facts'] if f['id'].startswith('authored:applied:')
                              and f['dimension'] == 'parameters.typed_operands']
                else:
                    prefix = 'family:' + opcode + ':ramp-rotc-sort:fxcpu_basic_applied_m:FX3U:table_'
                    tables = [f for f in owner['facts'] if f['id'].startswith(prefix)]
                self.assertEqual(len(tables), 4 if opcode == 'INCD' else 3)
                published, withheld = project_definition(owner)
                accepted = {f['id'] for f in published['facts']}
                for table in tables:
                    self.assertIn(table['id'], accepted, withheld)
                    for parameter in table['value']['operands']:
                        self.assertTrue(parameter.get('purpose'), parameter)
                        self.assertNotIn('source_description', json.dumps(parameter.get('purpose')))
                        if parameter.get('enable_pin'):
                            self.assertIn(parameter['role'], ['execution_condition', 'execution_status'])

    def test_compiled_control_relations_keep_component_and_record_layouts(self):
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
        for opcode in ['RAMP', 'ROTC', 'SORT']:
            with self.subTest(opcode=opcode):
                owner = registry._definition_owner_store.get_entry(opcode, cpu='FX3U', vendor='mitsubishi')
                roots = [f for f in owner['facts'] if f['id'].startswith('family:' + opcode + ':')
                         and f['dimension'] == 'effects.documented_processing']
                self.assertTrue(roots)
                for root in roots:
                    value = root['value']
                    relation = value['relation']
                    self.assertNotIn('behavior', value)
                    if opcode == 'RAMP':
                        self.assertEqual(relation['result']['parameter'], value['output'])
                        self.assertEqual(relation['result']['word_offset'], 0)
                        self.assertEqual(relation['scan_count']['word_offset'], 1)
                        self.assertEqual(relation['scan_limit'], value['inputs'][-1])
                        self.assertIn('not_defined', relation['numeric_precision'])
                    elif opcode == 'ROTC':
                        self.assertEqual([x['source']['bit_offset'] for x in relation['feedback']], [0, 1, 2])
                        self.assertEqual([x['target']['bit_offset'] for x in relation['outputs']], list(range(3, 8)))
                        self.assertEqual({x['target']['parameter'] for x in relation['outputs']}, {value['output']})
                    else:
                        self.assertEqual(relation['destination_head'], value['output'])
                        self.assertEqual(relation['word_offset']['operands'][0]['operator'], 'multiply')
                        self.assertEqual(relation['key_field_zero_based']['operands'][-1], 1)
                        self.assertIn('same_mapped_source_record', relation['result_rule']['for_each_destination_record_and_field'])
                        self.assertIn('not_defined', relation['result_rule']['equal_key_order'])
                p = packet(owner['facts'], model='FX3U')
                p['target'] = p['value']['opcode'] = opcode
                projected = json.dumps(technical_fact_packet(p), ensure_ascii=False)
                self.assertIn(roots[0]['value']['relation']['kind'], projected)


class SourcePulseDurationExtractionTests(unittest.TestCase):
    def test_one_scan_width_does_not_declare_repeat_restrictions(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _state_from_prose

        statements = [
            'The specified device turns ON one scan.',
            'The specified device is ON for only one scan.',
            'The specified device is ON for one scan.',
            'When there is one instruction for the device during one scan, '
            'the specified device turns ON one scan.',
        ]
        for symbol in ['PLS', 'SYNTH']:
            for statement in statements:
                with self.subTest(symbol=symbol, statement=statement):
                    section = SimpleNamespace(statements=lambda _: [statement])
                    specs = _state_from_prose(section, symbol)
                    self.assertFalse(any(spec['dimension'] == 'execution.same_scan_repeat'
                                         for spec in specs), specs)

        section = SimpleNamespace(statements=lambda _: [
            'The conditions are retained. The specified device is ON for one scan. '
            'Do not execute the instruction in an interrupt program.'])
        specs = _state_from_prose(section, 'SYNTH')
        self.assertEqual({spec['dimension'] for spec in specs},
                         {'effects.state_change', 'constraints.context'})


class QlUniqueOutputLabelTests(unittest.TestCase):
    @staticmethod
    def source(output_names=('d1',), call_output='d', *, output_direction='output'):
        from types import SimpleNamespace
        rows = [{'operand': {'name': name, 'type': kind, 'direction': direction, 'role': role},
                 'witness': name + '_' + role, 'description': role}
                for name, kind, direction, role in [
                    ('EN', 'BOOL', 'input', 'enable'), ('ENO', 'BOOL', 'output', 'execution_status'),
                    ('s1', 'ANY16', 'input', 'source_data'), ('s2', 'ANY16', 'input', 'source_data'),
                    *[(name, 'ANY16', output_direction, 'result_data') for name in output_names]]]
        contract = {'rows': rows, 'allowed': ['SYNTH'], 'calls': [
            {'name': 'SYNTH', 'arguments': ['EN', 's1', 's2', call_output]}], 'issues': [],
            'block_forms': {'SYNTH': ['EN', 'ENO', 's1', call_output, 's2']}, 'st_available': True}
        section = SimpleNamespace(manual='sh080783engp', heading_line='SYNTH', symbols=['SYNTH'],
                                  symbol_tokens=['SYNTH'], _parameter_contracts=[contract])
        return section, contract

    def test_single_output_label_is_bound_in_each_own_interface(self):
        from tools.fact_ql_behavior_review import _parameter_p_specs
        section, contract = self.source()
        before = copy.deepcopy(contract)
        declarations = [s for s in _parameter_p_specs(section, 'SYNTH')
                        if s['dimension'] == 'parameters.typed_operands']
        self.assertEqual(len(declarations), 3)
        for spec in declarations:
            with self.subTest(languages=spec['languages']):
                output, = [p for p in spec['value']['operands'] if p['name'] == 'd']
                self.assertEqual((output['direction'], output['type']), ('output', 'ANY16'))
                self.assertEqual(output['source_aliases'], ['d1'])
                self.assertNotIn('gap_kind', output)
        self.assertEqual(contract, before)

    def test_block_geometry_keeps_unique_output_before_building_typed_pins(self):
        from tools.fact_ql_behavior_review import _parameter_block_forms, _parameter_p_specs
        section, contract = self.source()
        def word(text, x, y):
            return {'text': text, 'x0': x, 'x1': x + 20, 'top': y, 'bottom': y + 8}
        words = [word('Structured', 50, 10), word('SYNTH', 95, 30), word('ENO:=', 300, 30),
                 word('EN', 60, 50), word('ENO', 160, 50), word('s1', 60, 70),
                 word('d', 160, 70), word('s2', 60, 90), word('Any', 50, 110)]
        forms = _parameter_block_forms(words, ['SYNTH'], contract['rows'])
        self.assertEqual(forms, {'SYNTH': ['EN', 'ENO', 's1', 'd', 's2']})
        contract['block_forms'] = forms
        specs = _parameter_p_specs(section, 'SYNTH')
        for language in ('fbd', 'ladder'):
            table, = [s for s in specs if s['dimension'] == 'parameters.typed_operands'
                      and s['languages'] == [language]]
            self.assertEqual(table['value']['operand_count'], 5)
            self.assertEqual([p['name'] for p in table['value']['operands']], forms['SYNTH'])

    def test_ambiguous_or_unrelated_labels_keep_unknown(self):
        from tools.fact_ql_behavior_review import _parameter_p_specs
        for outputs, call_output, direction in [
                (('d1', 'd2'), 'd', 'output'), (('d1',), 'd2', 'output'),
                (('result1',), 'd', 'output'), (('d1',), 'd', 'unknown')]:
            with self.subTest(outputs=outputs, call=call_output, direction=direction):
                section, contract = self.source(outputs, call_output, output_direction=direction)
                specs = _parameter_p_specs(section, 'SYNTH')
                st, = [s for s in specs if s['dimension'] == 'parameters.typed_operands'
                       and s['languages'] == ['st']]
                output, = [p for p in st['value']['operands'] if p['name'] == call_output]
                self.assertEqual(output['type'], 'unknown')
                self.assertEqual(output['source_disposition'], 'source_conflict')

    def test_competing_panel_labels_or_missing_inputs_cannot_form_an_alias(self):
        from tools.fact_ql_behavior_review import _unique_output_label_aliases
        _, contract = self.source()
        for labels in [('EN', 's1', 's2', 'd', 'd2'), ('EN', 's1', 'd'),
                       ('EN', 's1', 's2', 'd1', 'd')]:
            with self.subTest(labels=labels):
                self.assertEqual(_unique_output_label_aliases(contract['rows'], labels), {})


class QlNRowAliasTests(unittest.TestCase):
    @staticmethod
    def rows(source_name):
        from tools.fact_ql_behavior_review import _n_argument_rows
        columns = [('Input/output argument', 0, 20), ('Name', 20, 40),
                   ('Description', 40, 80), ('Data type', 80, 100)]
        cells = [{'text': name, 'bbox': [left, 0, right, 10]} for name, left, right in columns]
        cells.extend({'text': text, 'bbox': [left, 10, right, 30]}
                     for text, left, right in [('Input argument', 0, 20), (source_name, 20, 40),
                                              ('Input data', 40, 80), ('ANY', 80, 100)])
        return _n_argument_rows({'cells': cells})

    def test_merged_name_cell_keeps_each_literal_alias_with_its_parameter(self):
        from tools.fact_ql_behavior_review import _n_refined_pin
        for text, expected in [
                ('s2(_IN0)\ns3(_IN1)', {'s2': ['_IN0'], 's3': ['_IN1']}),
                ('s3(_IN7), s2(_IN9)', {'s2': ['_IN9'], 's3': ['_IN7']}),
                ('s2(_IN0)\ns3', {'s2': ['_IN0'], 's3': None})]:
            rows = self.rows(text)
            original = copy.deepcopy(rows)
            for name, aliases in expected.items():
                with self.subTest(text=text, parameter=name):
                    result = _n_refined_pin({'name': name, 'type': 'ANY'}, rows, 'SYNTH')
                    if aliases is None:
                        self.assertNotIn('source_aliases', result)
                    else:
                        self.assertEqual(result['source_aliases'], aliases)
                    self.assertEqual(result['type'], 'ANY')
            self.assertEqual(rows, original)

    def test_shared_or_competing_aliases_are_not_assigned_by_order(self):
        from tools.fact_ql_behavior_review import _n_refined_pin
        for text, name in [('s2, s3(_IN0, _IN1)', 's2'), ('s2, s3(_IN0, _IN1)', 's3'),
                           ('s2(_A), s2(_B)', 's2'), ('s1 to s3(_INPUT)', 's1'),
                           ('s1 to s3(_INPUT)', 's3'), ('s2(_SAME) s3(_SAME)', 's2'),
                           ('s2(_GOOD, unclear alias)', 's2')]:
            with self.subTest(text=text, parameter=name):
                result = _n_refined_pin({'name': name, 'type': 'ANY'}, self.rows(text), 'SYNTH')
                self.assertNotIn('source_aliases', result)

    def test_explicit_single_parameter_alias_does_not_reinterpret_its_type(self):
        from tools.fact_ql_behavior_review import _n_refined_pin
        for text, expected in [('s(_LREAL)', ['_LREAL']), ('s(_LEFT, _RIGHT)', ['_LEFT', '_RIGHT'])]:
            with self.subTest(text=text):
                rows = self.rows(text)
                rows[0]['operand']['type'] = 'REAL'
                result = _n_refined_pin({'name': 's', 'type': 'REAL'}, rows, 'SYNTH')
                self.assertEqual(result['source_aliases'], expected)
                self.assertEqual(result['type'], 'REAL')


class QLStructuredEnableProducerTests(unittest.TestCase):
    """Own-source execution gates follow their actual block declaration."""

    def fixture(self, symbol='MOD_E'):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _reviewed_spec
        section=SimpleNamespace(manual='sh080784engn',revision='N',symbols=[symbol],
            _has_structured_block=True,error_block=lambda:None,serial_conditions=lambda:[])
        section.bind=lambda witness:[('own',7)] if witness in {'Structured ladder/FBD','own gate','own pins','own OFF'} else []
        gate=_reviewed_spec('enable_gate','execution.operation_condition',['own gate'],
            {'enabled_when':'EN_true','when_disabled':'operation_stopped','disabled_eno':False},('st','fbd'))
        typed=_reviewed_spec('typed_operands','parameters.typed_operands',['own pins'],
            {'operands':[{'name':'EN','type':'BOOL','direction':'input'},
                {'name':'s','type':'ANY_NUM','direction':'input'},
                {'name':'d','type':'ANY_NUM','direction':'output'}]},('ladder',))
        typed['conditions']=[{'context':'project_mode','equals':'structured_ladder_block'},
            {'context':'cpu_version','equals':'own_version'}]
        return section,gate,typed

    def produce(self, section, symbol, authored, contracts):
        from tools import fact_ql_behavior_review as producer
        empty=['_universal_specs','_span_shift_specs','_rotation_specs','_byte_exchange_specs',
            '_reviewed_literal_specs','_n_behavior_detail_specs','_numeric_limit_specs',
            '_reviewed_m_specs','_reviewed_i_specs','_reviewed_xp_specs']
        with ExitStack() as stack:
            for name in empty:stack.enter_context(patch.object(producer,name,return_value=[]))
            stack.enter_context(patch.object(producer,'_reviewed_n_specs',return_value=copy.deepcopy(authored)))
            stack.enter_context(patch.object(producer,'family_specs',return_value=([],None)))
            stack.enter_context(patch.object(producer,'_parameter_contract_specs',return_value=copy.deepcopy(contracts)))
            return producer._documented_facts(None,section,symbol,'synthetic')

    def test_mod_gate_follows_own_structured_EN_and_conditions(self):
        section,gate,typed=self.fixture()
        before=copy.deepcopy([gate,typed]);result=self.produce(section,'MOD_E',[gate],[typed])
        generated=[f for f in result if f['dimension']=='execution.operation_condition' and f['languages']==['ladder']]
        self.assertEqual(len(generated),1)
        self.assertEqual(generated[0]['value'],gate['value'])
        self.assertEqual(generated[0]['conditions'],typed['conditions'])
        self.assertIn('Structured ladder/FBD',generated[0]['patterns'])
        self.assertEqual([gate,typed],before)
        self.assertEqual([f for f in result if tuple(f['languages'])==('st','fbd')],
                         [{**gate,'languages':('st','fbd')}])
        self.assertFalse(any(f['languages']==['native'] for f in result))

    def test_existing_expt_structured_gate_and_disabled_are_not_duplicated(self):
        section,gate,typed=self.fixture('EXPT_E')
        block=copy.deepcopy(gate);block['languages']=['ladder'];block['conditions']=copy.deepcopy(typed['conditions'])
        disabled=copy.deepcopy(block);disabled.update(key='disabled',dimension='execution.disabled',patterns=('own OFF',),
            value={'when_disabled':'undefined_do_not_use','disabled_ENO':False,'outputs':['d']})
        result=self.produce(section,'EXPT_E',[gate,block,disabled],[typed])
        self.assertEqual(len([f for f in result if f['dimension']=='execution.operation_condition']),2)
        self.assertEqual(len([f for f in result if f['dimension']=='execution.disabled']),1)

    def test_own_disabled_value_is_cloned_without_inference(self):
        from tools.fact_ql_behavior_review import _reviewed_spec
        section,gate,typed=self.fixture()
        disabled=_reviewed_spec('disabled','execution.disabled',['own OFF'],
            {'when_disabled':'retain_previous','outputs':['d']},('st','fbd'))
        gate['conditions']=[{'context':'own_CPU_requirement','equals':'required'}]
        gate['model_filter']=lambda model:model=='own_CPU'
        gate['depends_on']=['own_gate_qualification']
        result=self.produce(section,'MOD_E',[gate,disabled],[typed])
        blocks=[f for f in result if f['dimension']=='execution.disabled' and f['languages']==['ladder']]
        self.assertEqual(len(blocks),1)
        self.assertEqual(blocks[0]['value'],disabled['value'])
        self.assertIn(gate['conditions'][0],blocks[0]['conditions'])
        self.assertTrue(blocks[0]['model_filter']('own_CPU'))
        self.assertFalse(blocks[0]['model_filter']('other_CPU'))
        self.assertIn('own_gate_qualification',blocks[0]['depends_on'])

    def test_missing_or_foreign_premises_never_create_a_block_gate(self):
        from types import SimpleNamespace
        cases=['ADD_without_gate','missing_label','unbound_gate','EN_not_BOOL','EN_not_input',
            'foreign_function','foreign_manual','foreign_evidence','different_page','CPU_conflict','native_typed']
        for case in cases:
            with self.subTest(case=case):
                section,gate,typed=self.fixture('ADD_E' if case=='ADD_without_gate' else 'MOD_E')
                authored=[gate]
                if case=='ADD_without_gate':authored=[]
                if case in {'missing_label','unbound_gate','different_page'}:
                    missing='Structured ladder/FBD' if case=='missing_label' else 'own gate'
                    section.bind=lambda witness,missing=missing,case=case:([('own',8)] if case=='different_page' and witness==missing else [] if witness==missing else [('own',7)])
                if case=='EN_not_BOOL':typed['value']['operands'][0]['type']='ANY16'
                if case=='EN_not_input':typed['value']['operands'][0]['direction']='output'
                if case=='foreign_function':section.symbols=['OTHER_E']
                if case=='foreign_manual':section.manual='sh080783engp'
                if case=='foreign_evidence':gate['evidence_sections']=[SimpleNamespace(manual='sh080784engn')]
                if case=='CPU_conflict':gate['conditions']=[{'context':'cpu_version','equals':'other_version'}]
                if case=='native_typed':typed['conditions']=[{'context':'project_mode','equals':'native_ladder_instruction'}]
                result=self.produce(section,section.symbols[0] if case=='ADD_without_gate' else 'MOD_E',authored,[typed])
                self.assertFalse(any(f['dimension']=='execution.operation_condition' and f['languages']==['ladder'] for f in result))


class QLArgumentEnableProducerTests(unittest.TestCase):
    def fixture(self):
        section,_,block=QLStructuredEnableProducerTests.fixture(self,'ADD_E')
        oldbind=section.bind
        section.bind=lambda witness:[('own',7)] if witness=='own EN definition' else oldbind(witness)
        section._n_argument_contracts=[{'page':7,'issues':[],'rows':[{'operand':{'name':'EN','type':'BOOL','direction':'input'},
            'description':'Executing condition (TRUE: Execution, FALSE: Stop)','witness':'own EN definition'}]}]
        call=copy.deepcopy(block);call['languages']=['st','fbd'];call['conditions']=[]
        return section,call,block

    def produce(self, section, contracts, authored=None):
        return QLStructuredEnableProducerTests.produce(self,section,'ADD_E',authored or [],contracts)

    def test_own_argument_definition_creates_call_and_structured_gates(self):
        section,call,block=self.fixture();before=copy.deepcopy([call,block])
        result=self.produce(section,[call,block])
        gates=[s for s in result if s['dimension']=='execution.operation_condition']
        self.assertEqual(len(gates),2)
        for gate in gates:
            self.assertEqual(gate['value'],{'enabled_when':'EN_true','when_disabled':'operation_stopped'})
            self.assertIn('own EN definition',gate['patterns'])
            self.assertNotIn('disabled_eno',gate['value'])
        self.assertEqual([call,block],before)
        generated=next(g for g in gates if tuple(g['languages'])==('ladder',))
        self.assertEqual(generated['conditions'],block['conditions'])
        self.assertFalse(any(s['dimension']=='execution.disabled' for s in result))

    def test_existing_gate_is_not_reauthored_or_given_new_OFF_values(self):
        from tools.fact_ql_behavior_review import _reviewed_spec
        section,call,block=self.fixture()
        gate=_reviewed_spec('enable_gate','execution.operation_condition',['own gate'],
            {'enabled_when':'EN_true','when_disabled':'operation_stopped','disabled_eno':False},('st','fbd'))
        result=self.produce(section,[call,block],[gate])
        self.assertFalse(any(s['key']=='argument_enable_gate' for s in result))
        self.assertEqual(len([s for s in result if s['dimension']=='execution.operation_condition']),2)

    def test_CPU_conditions_filters_and_dependencies_are_retained(self):
        section,call,_=self.fixture()
        call['conditions']=[{'context':'cpu_version_not_resolved','equals':True}]
        call['depends_on']=['own_CPU_qualification'];call['model_filter']=lambda model:model=='own_CPU'
        result=self.produce(section,[call])
        gates=[s for s in result if s['dimension']=='execution.operation_condition']
        self.assertEqual(len(gates),1)
        self.assertEqual(gates[0]['conditions'],call['conditions'])
        self.assertEqual(gates[0]['depends_on'],call['depends_on'])
        self.assertTrue(gates[0]['model_filter']('own_CPU'))
        self.assertFalse(gates[0]['model_filter']('other_CPU'))

    def test_missing_or_ambiguous_definition_does_not_create_a_gate(self):
        from types import SimpleNamespace
        for case in ['no_contract','unbound','foreign_page','row_type','row_direction','description',
            'typed_type','typed_unreviewed','foreign_evidence','foreign_symbol','foreign_manual','plain_form','native']:
            with self.subTest(case=case):
                section,call,block=self.fixture();contracts=[call,block]
                row=section._n_argument_contracts[0]['rows'][0]
                if case=='no_contract':section._n_argument_contracts=[]
                if case=='unbound':section.bind=lambda witness:[]
                if case=='foreign_page':section._n_argument_contracts[0]['page']=8
                if case=='row_type':row['operand']['type']='ANY16'
                if case=='row_direction':row['operand']['direction']='output'
                if case=='description':row['description']='Execution result (TRUE: Normal, FALSE: Error)'
                if case=='typed_type':
                    for typed in contracts:typed['value']['operands'][0]['type']='ANY16'
                if case=='typed_unreviewed':
                    for typed in contracts:typed['reviewed']=False
                if case=='foreign_evidence':
                    for typed in contracts:typed['evidence_sections']=[SimpleNamespace(manual='sh080784engn')]
                if case=='foreign_symbol':section.symbols=['OTHER_E']
                if case=='foreign_manual':section.manual='sh080783engp'
                if case=='plain_form':section.symbols=['ADD']
                if case=='native':contracts=[block];block['conditions']=[{'context':'project_mode','equals':'native_ladder_instruction'}]
                result=self.produce(section,contracts)
                self.assertFalse(any(s['dimension']=='execution.operation_condition' for s in result))


class NativeReviewedCpuHeaderConstraintTests(unittest.TestCase):
    def groups(self):
        scope = {'forms': ['TRD'], 'models': ['FX3U'], 'languages': ['ladder']}
        mode = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
        header = fact('reviewed-header', 'constraints.source_cpu_header', scope=scope,
            conditions=[mode], value={'model': 'FX3U', 'state': 'conditional',
                'minimum_version': '2.20',
                'basis': 'reviewed_own_instruction_header_cpu_version_badges'})
        def availability(identity, supported, condition):
            return fact(identity, 'constraints.cpu_availability', scope=scope,
                conditions=[mode, condition], depends_on=[header['id']],
                value={'available': supported, 'model_states': {
                    'FX3U': 'supported' if supported else 'unsupported'},
                    'rule': 'CPU_INSTRUCTION_SUPPORT'})
        return [header,
            availability('below-minimum', False, {'context': 'cpu_version', 'maximum_exclusive': '2.20'}),
            availability('supported-version', True, {'context': 'cpu_version', 'minimum': '2.20'})]

    def report(self, groups=None, context=None):
        from plc.instruction_constraints import check_fact_constraints
        return check_fact_constraints(self.groups() if groups is None else groups, {},
            target='TRD', plc_model='FX3U', language='ladder',
            context={'project_mode': 'native_ladder', **(context or {})})

    def test_reviewed_header_witness_keeps_low_supported_and_unknown_branches(self):
        for version, expected in [(None, 'unverified'), ('2.19', 'failed'),
                                  ('2.20', 'passed'), ('2.20.0', 'passed')]:
            with self.subTest(version=version):
                result = self.report(context={} if version is None else {'cpu_version': version})
                self.assertEqual(result['status'], expected)
                if version == '2.19':
                    self.assertTrue(any(c['fact_id'] == 'below-minimum' and c['status'] == 'failed'
                                        for c in result['checks']))
        for state in ['supported', 'unsupported']:
            header = self.groups()[0]
            header['value']['state'] = state
            header['value'].pop('minimum_version')
            with self.subTest(witness_state=state):
                self.assertEqual(self.report([header])['status'], 'passed')

    def test_header_proof_guards_are_not_bypassed(self):
        for difference in ['status', 'conflict', 'verification', 'scope', 'missing_scope', 'model', 'state', 'basis', 'shape',
                           'minimum', 'missing_minimum', 'source_condition', 'dependency',
                           'missing_source', 'unknown_condition', 'false_condition']:
            groups = self.groups(); header = groups[0]
            if difference == 'status': header['status'] = 'declared_unverified'
            elif difference == 'conflict': header['status'] = 'conflict'
            elif difference == 'verification': header['verification'] = 'not_performed'
            elif difference == 'scope': header['scope'] = {**header['scope'], 'models': ['FX3UC']}
            elif difference == 'missing_scope': header['scope'] = {}
            elif difference == 'model': header['value']['model'] = 'FX3UC'
            elif difference == 'state': header['value']['state'] = 'unknown'
            elif difference == 'basis': header['value']['basis'] = 'own_instruction_CPU_header'
            elif difference == 'shape': header['value']['unreviewed_extra'] = True
            elif difference == 'minimum': header['value']['minimum_version'] = 2.2
            elif difference == 'missing_minimum': header['value'].pop('minimum_version')
            elif difference == 'source_condition': header['sources'][0]['conditions'] = {'unknown': True}
            elif difference == 'dependency': header['depends_on'] = ['missing-source-proof']
            elif difference == 'missing_source': header['sources'] = []
            elif difference == 'unknown_condition': header['conditions'].append({'context': 'header_review', 'equals': 'confirmed'})
            else: header['conditions'].append({'context': 'cpu_version', 'minimum': '2.20'})
            with self.subTest(difference=difference):
                if difference in {'verification', 'missing_source'}:
                    from plc.instruction_definition import DefinitionError
                    with self.assertRaises(DefinitionError): self.report(groups, {'cpu_version': '2.19'})
                else:
                    self.assertEqual(self.report(groups, {'cpu_version': '2.19'})['status'], 'unverified')

    def test_unrelated_unformalized_constraint_stays_unverified(self):
        group = self.groups()[0]; group['dimension'] = 'constraints.other_source_observation'
        self.assertEqual(self.report([group], {'cpu_version': '2.20'})['status'], 'unverified')

    def ladder(self, ordered):
        if ordered:
            rung = {'rung_id': 0, 'ordered_instructions': [
                {'op': 'LD', 'args': ['M8000']}, {'op': 'TRD', 'args': ['D0']}]}
        else:
            rung = {'rung_id': 0, 'branches': [{'inputs': [{'type': 'NO', 'address': 'M8000'}],
                'outputs': [{'type': 'APP_INSTR', 'opcode': 'TRD', 'operands': ['D0']}]}]}
        return {'device_comments': {}, 'rungs': [rung]}

    def validate(self, confirmed, ordered):
        from plc.validation import validate_ladder_full
        from plc.instruction_constraints import check_fact_constraints
        captured = []
        def checked(opcode, values, **kwargs):
            if opcode != 'TRD':
                return {'status': 'passed', 'checks': [], 'findings': []}
            context = kwargs.get('context') or {}
            report = check_fact_constraints(self.groups(), values, target=opcode,
                plc_model=kwargs['plc_model'], language=kwargs.get('language'),
                version=kwargs.get('version'), context=context)
            captured.append({'context': context, 'version': kwargs.get('version'),
                             'status': report['status']})
            return report
        with patch('plc.instruction_constraints.check_instruction_constraints', side_effect=checked):
            validate_ladder_full(self.ladder(ordered), plc_model='FX3U', confirmed_spec=confirmed)
        return captured

    def test_confirmed_low_versions_reach_both_ladder_representations(self):
        from plc.validation import PLCJsonValidationError
        contexts = [
            {'hardware_profile': {'cpu_version': '2.19'}},
            {'hardware_profile': {'firmware': '2.19'}},
            {'cpu_version': '2.19'},
            {'project_context': {'cpu_version': '2.19'}},
            {'selected_approach': {'construction_plan': {'execution_context': {'cpu_version': '2.19'}}}}]
        for ordered in [False, True]:
            for confirmed in contexts:
                with self.subTest(ordered=ordered, confirmed=confirmed):
                    with self.assertRaises(PLCJsonValidationError): self.validate(confirmed, ordered)

    def test_unknown_conflicting_and_satisfied_contexts_keep_their_status(self):
        for ordered in [False, True]:
            for confirmed in [None, {}, {'hardware_profile': {'cpu_version': 'not-recorded'}},
                {'hardware_profile': {'cpu_version': '2.20', 'firmware': '2.19'}},
                {'cpu_version': '2.19', 'hardware_profile': {'cpu_version': '2.20'}}]:
                with self.subTest(ordered=ordered, confirmed=confirmed):
                    calls = self.validate(confirmed, ordered)
                    self.assertTrue(calls)
                    self.assertEqual(calls[-1]['status'], 'unverified')
            confirmed = {'hardware_profile': {'cpu_version': '2.20'}, 'selected_approach': {
                'construction_plan': {'execution_context': {'software_version': '1.99'}}}}
            calls = self.validate(confirmed, ordered)
            self.assertEqual(calls[-1]['status'], 'passed')
            self.assertEqual(calls[-1]['context']['cpu_version'], '2.20')
            self.assertEqual(calls[-1]['version'], '1.99')


class BoundArithmeticFoldClassificationTests(unittest.TestCase):
    def pair(self, operation='add', grouped=False):
        scope = {'models': ['Q03UDV'], 'forms': ['SYNTH'], 'languages': ['st']}
        operands = [{'name': 's%d' % n, 'type': 'ANY_NUM', 'direction': 'input',
                     **({'optional': True} if n > 2 else {})} for n in range(1, 29)]
        if grouped:
            operands = [{'name': 'values', 'type': 'ANY_NUM', 'direction': 'input'}]
        signature = fact('typed', 'parameters.typed_operands', scope=scope,
                         value={'operands': [*operands, {'name': 'd', 'type': 'ANY_NUM', 'direction': 'output'}]})
        value = ({'operation': operation, 'operand_pins': {'minimum': 2, 'maximum': 28}}
                 if grouped else {'operation': operation, 'inputs': ['s1..s28'], 'result': 'd',
                                  'input_count': {'minimum': 2, 'maximum': 28}, 'result_type': 'same_as_input'})
        effect = fact('operation', 'effects.result_rule' if grouped else 'effects.operation',
                      scope=scope, value=value)
        from plc.instruction_relations import bind_relation_facts
        relation, = bind_relation_facts([signature, effect])
        return signature, effect, relation

    def test_actual_bound_fold_spellings_and_grouped_pins(self):
        from tools.inventory_instruction_semantics import _concrete_relation
        for operation in ('add', 'multiply', 'addition', 'multiplication'):
            with self.subTest(operation=operation):
                self.assertTrue(_concrete_relation(self.pair(operation)[2]))
        for operation in ('addition', 'multiplication'):
            with self.subTest(operation=operation, grouped=True):
                self.assertTrue(_concrete_relation(self.pair(operation, grouped=True)[2]))

    def test_source_shaped_optional_pins_and_types_are_preserved(self):
        from plc.instruction_relations import bind_relation_facts
        from tools.inventory_instruction_semantics import _concrete_relation
        for operation in ('add', 'multiply'):
            signature, effect, relation = self.pair(operation)
            before = copy.deepcopy(relation)
            self.assertTrue(_concrete_relation(relation))
            self.assertEqual(relation, before)
            value = relation['value']
            self.assertEqual(value['input_count'], {'minimum': 2, 'maximum': 28})
            self.assertEqual(len(value['operand_binding']['inputs']), 28)
            self.assertEqual(sum(p.get('optional', False) for p in value['operand_binding']['inputs']), 26)
            self.assertEqual({p['type'] for p in value['operand_binding']['inputs']}, {'ANY_NUM'})
            self.assertEqual(value['operand_binding']['output']['type'], 'ANY_NUM')
            for wrong in ('input', 'result'):
                broken = copy.deepcopy(effect)
                broken['value']['inputs' if wrong == 'input' else 'result'] = ['absent'] if wrong == 'input' else 'absent'
                self.assertEqual(bind_relation_facts([signature, broken]), [])
        # The pre-existing canonical branch also describes wider results;
        # spelling normalization must not impose a new same-type restriction.
        signature, effect, _ = self.pair('multiplication', grouped=True)
        signature['value']['operands'][-1]['type'] = 'LREAL'
        self.assertTrue(_concrete_relation(bind_relation_facts([signature, effect])[0]))

    def test_unbound_labels_partial_and_unresolved_remain_rejected(self):
        from tools.inventory_instruction_semantics import _concrete_relation
        for operation in ('add', 'multiply', 'addition', 'multiplication'):
            for failure in ('unbound', 'unassembled', 'wrong_operation', 'partial', 'unresolved'):
                with self.subTest(operation=operation, failure=failure):
                    broken = copy.deepcopy(self.pair(operation)[2]); value = broken['value']
                    if failure == 'unbound': value.pop('operand_binding')
                    elif failure == 'unassembled': broken['id'] = 'source:summary'
                    elif failure == 'wrong_operation': value['operation'] = 'arithmetic_summary'
                    elif failure == 'partial': value['full_source_relation_complete'] = False
                    elif failure == 'unresolved': value['unresolved_items'] = ['source_arity']
                    self.assertFalse(_concrete_relation(broken))

    def test_content_does_not_bypass_source_cpu_or_dependency_closure(self):
        from tools.inventory_instruction_semantics import _closure, _concrete_relation
        for failure in ('missing_dependency', 'unreviewed_source', 'wrong_cpu', 'unresolved_cpu'):
            signature, effect, relation = self.pair()
            if failure == 'missing_dependency': relation['depends_on'].append('missing')
            elif failure == 'unreviewed_source': effect['status'] = 'declared_unverified'
            elif failure == 'wrong_cpu': signature['scope']['models'] = ['L02']
            else:
                condition = fact('cpu', 'constraints.cpu_availability', status='unknown',
                                 value={'available': True}, conditions=[{'context': 'cpu_version_not_resolved'}])
                signature['depends_on'] = ['cpu']
            facts = [signature, effect, relation] + ([condition] if failure == 'unresolved_cpu' else [])
            with self.subTest(failure=failure):
                self.assertTrue(_concrete_relation(relation))
                self.assertNotEqual(_closure(facts, relation['id'], opcode='SYNTH', model='Q03UDV', language='st')[0], 'answered')


class FxEnabledArithmeticProducerTests(unittest.TestCase):
    def build(self, opcode='ADD', *, record=None, enabled=True, condition=None):
        import tools.fact_structured_behavior_review as review
        if record is None:
            record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS
                if r['op'] == opcode and r['section'] == {
                    'ABS': '6.1', 'ADD': '7.1', 'SUB': '7.2', 'MUL': '7.3', 'DIV': '7.4', 'MOD': '7.5'}[opcode]))
        source = {'source_id': 'synthetic'}
        with patch.dict(review.GENERIC_TYPE_REFERENCES, {'synthetic': source}, clear=True):
            rows = review._form_facts(record, opcode + ('_E' if enabled else ''), enabled,
                'FX3U', {'text': 'synthetic'}, [source], condition, 'source_verified')
        return rows + review._structured_ladder_facts(rows)

    def test_own_fx_type_domain_is_abstract_and_excludes_lreal(self):
        from tools.inventory_instruction_semantics import _type_present
        for opcode in ('ABS', 'ADD', 'SUB', 'MUL', 'DIV', 'MOD'):
            with self.subTest(opcode=opcode):
                token = 'ANY_INT' if opcode == 'MOD' else 'ANY_NUM'
                members = ['INT', 'DINT'] if opcode == 'MOD' else ['INT', 'DINT', 'REAL']
                typed = [f for f in self.build(opcode) if f['dimension'] == 'parameters.typed_operands']
                self.assertEqual(len(typed), 2)
                for declaration in typed:
                    for parameter in declaration['value']['operands']:
                        self.assertTrue(_type_present(parameter, 'st_call'))
                        if not parameter['enable_pin']:
                            self.assertEqual(parameter['type'], token)
                            self.assertEqual(parameter['data_type'], {'flexible_type': token})
                            self.assertEqual(parameter['type_alternatives'], members)
                            self.assertFalse({'bits', 'width_bits', 'word_device_layout'} & parameter.keys())

    def test_fixed_unary_and_binary_calls_do_not_gain_variable_pins_or_inferred_types(self):
        import tools.fact_structured_behavior_review as review
        for opcode, count in [('ABS', 1), ('MOD', 2)]:
            with self.subTest(opcode=opcode):
                rows = self.build(opcode)
                for typed in [f for f in rows if f['dimension'] == 'parameters.typed_operands']:
                    self.assertEqual(len([p for p in typed['value']['operands']
                        if p['direction'] == 'input' and not p['enable_pin']]), count)
                self.assertFalse(any(f['dimension'] == 'execution.operand_pins' for f in rows))
                record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS
                    if r['op'] == opcode and r['section'] == {'ABS':'6.1','MOD':'7.5'}[opcode]))
                for enabled, members in [(True, {}), (False, record['type_members']),
                                         (True, {'ANY_BIT': ['BOOL', 'WORD']})]:
                    record['type_members'] = members
                    for fact in self.build(opcode, record=record, enabled=enabled):
                        if fact['dimension'] == 'parameters.typed_operands':
                            self.assertTrue(all('type' not in p for p in fact['value']['operands']
                                if not p['enable_pin']))

    def test_fold_binds_own_pin_count_and_preserves_interface_cpu_dependencies(self):
        from plc.instruction_relations import bind_relation_facts
        from tools.inventory_instruction_semantics import _closure, _concrete_relation
        condition = {'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}
        for opcode in ('ADD', 'MUL'):
            rows = self.build(opcode, condition=condition)
            relations = bind_relation_facts(rows)
            self.assertEqual(len(relations), 2)
            for relation in relations:
                value = relation['value']
                self.assertEqual(value['operand_pins'], {'minimum': 2, 'maximum': 28})
                self.assertEqual(value['input_groups'], [{'parameter': '_IN', 'minimum': 2, 'maximum': 28}])
                self.assertEqual(relation['scope']['models'], ['FX3U'])
                self.assertIn(condition, relation['conditions'])
                structured = relation['scope']['languages'] == ['ladder']
                if structured:
                    self.assertIn({'context': 'project_mode', 'equals': 'structured_ladder_block'}, relation['conditions'])
                effect = next(f for f in rows if f['dimension'] == 'effects.result_rule'
                    and (f['scope']['languages'] == ['ladder']) == structured)
                count, = effect['depends_on']
                self.assertEqual(count.endswith(':structured_ladder'), structured)
                self.assertEqual(next(f for f in rows if f['id'] == count)['dimension'], 'execution.operand_pins')
                self.assertTrue(_concrete_relation(relation))
                lang = relation['scope']['languages'][0]
                self.assertNotEqual(_closure([f for f in rows if f['id'] != count] + relations,
                    relation['id'], opcode=opcode+'_E', model='FX3U', language=lang)[0], 'answered')

    def test_unlisted_types_plain_forms_and_unbound_counts_are_not_promoted(self):
        import tools.fact_structured_behavior_review as review
        from plc.instruction_relations import bind_relation_facts
        record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS if r['op'] == 'ADD' and r['section'] == '7.1'))
        for change in ('unlisted_type', 'plain', 'no_count', 'other_operation'):
            with self.subTest(change=change):
                modified = copy.deepcopy(record)
                if change == 'unlisted_type': modified.pop('type_members', None)
                if change == 'no_count': modified['limits'] = ()
                if change == 'other_operation': modified['rule'] = {'rule': 'operation_summary'}
                rows = self.build(record=modified, enabled=change != 'plain')
                if change in ('plain', 'unlisted_type'):
                    self.assertTrue(all('type' not in p for f in rows if f['dimension'] == 'parameters.typed_operands'
                        for p in f['value']['operands'] if not p['enable_pin']))
                if change in ('plain', 'no_count', 'other_operation'):
                    self.assertTrue(all('operand_pins' not in f['value'] for f in rows if f['dimension'] == 'effects.result_rule'))
                    self.assertFalse(bind_relation_facts(rows))

    def test_changes_are_same_source_refinements_with_old_values_retained(self):
        from scripts.compile_instruction_semantics import merge_source_facts
        for opcode in ('ADD', 'MUL'):
            fresh = [f for f in self.build(opcode) if f['dimension'] in
                     {'parameters.typed_operands', 'effects.result_rule'}]
            old = copy.deepcopy(fresh)
            for f in old:
                if f['dimension'] == 'parameters.typed_operands':
                    for p in f['value']['operands']:
                        p.pop('type', None); p.pop('type_alternatives', None)
                else:
                    f['value'].pop('operand_pins', None); f.pop('depends_on', None)
            merged, history = merge_source_facts(old, fresh)
            self.assertEqual(len(merged), 4)
            self.assertEqual({f['id'] for f in merged}, {f['id'] for f in old})
            self.assertEqual(len(history), 4)
            self.assertEqual([f['value'] for f in merged], [f['value'] for f in fresh])


class FxEnabledArithmeticDisabledProducerTests(unittest.TestCase):
    def build(self, opcode='ADD', *, source=True, record=None, condition=None):
        import tools.fact_structured_behavior_review as review
        if record is None:
            record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS
                if r['op'] == opcode and r['section'] == {'ADD': '7.1', 'MUL': '7.3'}[opcode]))
        own = {'source_id': 'synthetic-own'}
        reference = {'manual_id': review.FUNCTION_MANUAL, 'revision': review.FUNCTION_REVISION,
                     'pdf_pages': [36]} if source is True else source if isinstance(source, dict) else None
        with patch.dict(review.GENERIC_TYPE_REFERENCES, {'synthetic': own}, clear=True):
            rows = review._form_facts(record, opcode + '_E', True, 'FX3U', {'text': 'synthetic'},
                [own], condition, 'source_verified', enable_reference=reference)
        return rows + review._structured_ladder_facts(rows)

    def test_false_row_is_indefinite_with_own_reference_and_global_template_unchanged(self):
        import tools.fact_structured_behavior_review as review
        original = copy.deepcopy(review.EN_SEMANTICS)
        for opcode in ('ADD', 'MUL'):
            for row in [f for f in self.build(opcode) if f['dimension'] == 'execution.enable']:
                disabled = next(r for r in row['value']['truth_table'] if r['enable'] == 'FALSE')
                self.assertEqual(disabled['operation_result'], 'indefinite_value')
                self.assertEqual(disabled['status'], 'FALSE')
                self.assertTrue(any(s.get('manual_id') == review.FUNCTION_MANUAL and s.get('pdf_pages') == [36]
                                    for s in row['sources']))
        self.assertEqual(review.EN_SEMANTICS, original)

    def test_disabled_binds_data_and_status_separately_with_cpu_and_interface_dependencies(self):
        condition = {'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}
        for opcode in ('ADD', 'MUL'):
            rows = self.build(opcode, condition=condition)
            disabled = [f for f in rows if f['dimension'] == 'execution.disabled']
            self.assertEqual(len(disabled), 2)
            for f in disabled:
                self.assertEqual(f['value'], {'when_disabled': 'indefinite_value',
                    'disabled_ENO': False, 'outputs': ['Output_label']})
                self.assertIn(condition, f['conditions'])
                structured = f['scope']['languages'] == ['ladder']
                self.assertEqual(f['scope']['models'], ['FX3U'])
                if structured:
                    self.assertIn({'context': 'project_mode', 'equals': 'structured_ladder_block'}, f['conditions'])
                for dep in f['depends_on']:
                    self.assertEqual(dep.endswith(':structured_ladder'), structured)
                self.assertEqual({next(r for r in rows if r['id'] == dep)['dimension']
                    for dep in f['depends_on']}, {'execution.enable', 'parameters.typed_operands'})
                typed = next(r for r in rows if r['id'] in f['depends_on']
                             and r['dimension'] == 'parameters.typed_operands')
                self.assertEqual(typed['value']['return_shape']['role'], 'ENO_execution_status')
                self.assertEqual(typed['value']['return_shape']['operation_result_via'], 'output_argument')

    def test_missing_source_other_manual_and_unreviewed_record_do_not_invent_off_result(self):
        import tools.fact_structured_behavior_review as review
        base = copy.deepcopy(next(r for r in review.FAMILY_RECORDS if r['op'] == 'ADD' and r['section'] == '7.1'))
        for failure in ('no_reference', 'other_manual', 'other_revision', 'no_record_rule', 'multiple_outputs'):
            with self.subTest(failure=failure):
                record = copy.deepcopy(base); source = True
                if failure == 'no_reference': source = False
                elif failure == 'other_manual': source = {'manual_id': 'other', 'revision': 'K', 'pdf_pages': [36]}
                elif failure == 'other_revision': source = {'manual_id': review.FUNCTION_MANUAL, 'revision': 'other', 'pdf_pages': [36]}
                elif failure == 'no_record_rule': record.pop('disabled_result', None)
                else: record['outputs'] = (*record['outputs'], ('Other', 'ANY_NUM', 'd2', 'other'))
                rows = self.build(record=record, source=source)
                self.assertFalse([f for f in rows if f['dimension'] == 'execution.disabled'])
                if failure in ('no_reference', 'other_manual', 'other_revision'):
                    self.assertTrue(all(next(r for r in f['value']['truth_table'] if r['enable'] == 'FALSE')
                        ['operation_result'] != 'not_updated' for f in rows if f['dimension'] == 'execution.enable'))


class FxEnabledBitwiseProducerTests(unittest.TestCase):
    def build(self, opcode, *, record=None, condition=None):
        import tools.fact_structured_behavior_review as review
        if record is None:
            record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS
                if r['op'] == opcode and r['section'].startswith('9.')))
        return FxEnabledArithmeticProducerTests().build(opcode, record=record, condition=condition)

    def test_own_any_bit_domain_is_BOOL_WORD_DWORD_without_fixed_payload_width(self):
        for opcode in ('AND', 'OR', 'XOR', 'NOT'):
            with self.subTest(opcode=opcode):
                rows = self.build(opcode)
                for fact in rows:
                    if fact['dimension'] != 'parameters.typed_operands': continue
                    for pin in fact['value']['operands']:
                        if pin['enable_pin']: continue
                        self.assertEqual(pin['data_type'], {'flexible_type': 'ANY_BIT'})
                        self.assertEqual(pin.get('type'), 'ANY_BIT')
                        self.assertEqual(pin.get('type_alternatives'), ['BOOL', 'WORD', 'DWORD'])
                        self.assertFalse({'bits', 'width_bits', 'occupied_bit_count'} & pin.keys())
                self.assertFalse([f for f in rows if f['dimension'] == 'execution.disabled'])

    def test_variadic_bitwise_folds_bind_own_count_and_preserve_conditions(self):
        from plc.instruction_relations import bind_relation_facts
        from tools.inventory_instruction_semantics import _concrete_relation
        condition = {'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}
        for opcode in ('AND', 'OR', 'XOR'):
            with self.subTest(opcode=opcode):
                rows = self.build(opcode, condition=condition)
                effects = [f for f in rows if f['dimension'] == 'effects.result_rule']
                self.assertEqual(len(effects), 2)
                for effect in effects:
                    self.assertEqual(effect['value'].get('operand_pins'), {'minimum': 2, 'maximum': 28})
                    self.assertEqual(len(effect['depends_on']), 1)
                relations = bind_relation_facts(rows)
                self.assertEqual(len(relations), 2)
                for relation in relations:
                    self.assertTrue(_concrete_relation(relation))
                    self.assertIn(condition, relation['conditions'])
                    self.assertEqual(relation['value']['input_groups'],
                        [{'parameter': '_IN', 'minimum': 2, 'maximum': 28}])

    def test_not_is_unary_and_missing_own_count_is_not_inferred(self):
        from plc.instruction_relations import bind_relation_facts
        rows = self.build('NOT')
        self.assertEqual(len(bind_relation_facts(rows)), 2)
        self.assertFalse([f for f in rows if 'operand_pins' in f['value']])
        import tools.fact_structured_behavior_review as review
        for opcode in ('AND', 'OR', 'XOR'):
            record = copy.deepcopy(next(r for r in review.FAMILY_RECORDS
                if r['op'] == opcode and r['section'].startswith('9.')))
            record['limits'] = ()
            self.assertFalse(bind_relation_facts(self.build(opcode, record=record)))


class FxNonBooleanEnableQualificationTests(unittest.TestCase):
    def record(self, op='SUB', section='7.2'):
        from tools import fact_structured_behavior_review as review
        return copy.deepcopy(next(r for r in review._all_records()
                                  if r['op'] == op and r['section'] == section))

    def row(self, record):
        from tools import fact_structured_behavior_review as review
        return {'manual_id': review.FUNCTION_MANUAL, 'revision': review.FUNCTION_REVISION,
                'text': record['st_enabled'].split(',')[0] + ','}

    def test_own_enabled_format_and_nonboolean_result_qualify_without_guessing_width(self):
        from tools import fact_structured_behavior_review as review
        for op, section in [('SUB', '7.2'), ('MOD', '7.5'), ('EXPT', '7.6'),
                            ('MID', '12.1'), ('BITARR_TO_INT', '5.49')]:
            with self.subTest(op=op):
                record = self.record(op, section)
                before = copy.deepcopy(record)
                self.assertTrue(review._reviewed_nonboolean_enable(record, self.row(record)))
                self.assertEqual(record, before)

    def test_rejects_boolean_unknown_domain_fb_multiple_results_and_unbound_format(self):
        from tools import fact_structured_behavior_review as review
        for failure in ('BOOL', 'BITARR', 'ANY', 'ANY_SIMPLE', 'ANY_BIT', 'bad_members',
                        'fb', 'multiple', 'missing_call', 'other_function', 'other_manual',
                        'other_revision'):
            with self.subTest(failure=failure):
                record = self.record(); row = self.row(record)
                if failure in review.TYPES:
                    record['outputs'] = (('Output_label', failure, 'd', 'result'),)
                elif failure == 'bad_members':
                    record['type_members'] = {'ANY_NUM': ['INT', 'BOOL']}
                elif failure == 'fb': record['kind'] = 'function_block'
                elif failure == 'multiple': record['outputs'] *= 2
                elif failure == 'missing_call': row['text'] = 'EN ENO Output_label'
                elif failure == 'other_function': row['text'] = 'MOD_E(EN,'
                elif failure == 'other_revision': row['revision'] = 'other'
                else: row['manual_id'] = 'other'
                self.assertFalse(review._reviewed_nonboolean_enable(record, row))

    def test_qualified_configuration_preserves_cpu_dependency_and_native_exclusion(self):
        from tools import fact_structured_behavior_review as review
        record = self.record()
        self.assertTrue(review._reviewed_nonboolean_enable(record, self.row(record)))
        record['disabled_result'] = 'indefinite_value'
        helper = FxEnabledArithmeticDisabledProducerTests()
        condition = copy.deepcopy(review.HEADER_CONDITION)
        rows = helper.build(opcode='SUB', record=record, condition=condition)
        disabled = [f for f in rows if f['dimension'] == 'execution.disabled']
        self.assertEqual(len(disabled), 2)
        for fact in disabled:
            self.assertIn(condition, fact['conditions'])
            self.assertNotIn('native', fact['scope']['languages'])
            self.assertEqual(len(fact['depends_on']), 2)


if __name__ == '__main__': unittest.main()


class QLNDisabledResultGeometryTests(unittest.TestCase):
    def geometry(self, *, header=('EN', 'ENO', '(d)'), candidates=1, ambiguous=False):
        from types import SimpleNamespace
        from tools.fact_source_geometry import SourcePageGeometry
        boxes = [(0, 0, 10, 10), (10, 0, 20, 10), (20, 0, 30, 10),
                 (0, 10, 10, 20), (10, 10, 20, 20), (20, 10, 30, 20),
                 (0, 20, 10, 30), (10, 20, 20, 30), (20, 20, 30, 30)]
        texts = [*header, 'TRUE (Operation execution)', 'TRUE', 'Operation output value',
                 'FALSE (Operation stop)', 'FALSE*1', 'Undefined value']
        if ambiguous:
            texts[6] = 'UNKNOWN'
        lookup = dict(zip(boxes, texts))
        table = SimpleNamespace(bbox=(0, 0, 30, 30), cells=boxes)
        page = unittest.mock.Mock(height=100)
        page.crop.side_effect = lambda box, **kw: SimpleNamespace(
            find_tables=lambda options: [table] * candidates,
            extract_text=lambda **kw: lookup.get(tuple(box), ''))
        wrapper = SourcePageGeometry(None, 'synthetic', {})
        wrapper._document = lambda manual: SimpleNamespace(pages=[page])
        original = {'bbox': [0, 0, 30, 30], 'cells': [
            {'bbox': list(box), 'text': text} for box, text in zip(boxes[:3], header)]}
        wrapper.pages[('own', 1)] = ([], {'behavior_tables': [copy.deepcopy(original)]})
        return wrapper, original, page

    def test_explicit_own_result_table_opt_in_preserves_default_cache(self):
        wrapper, original, page = self.geometry()
        before = copy.deepcopy(wrapper.pages)
        result, reason = wrapper.n_execution_result_table('own', 1, original)
        self.assertIsNone(reason)
        self.assertEqual(len(result['cells']), 9)
        self.assertEqual(result['bbox'], original['bbox'])
        self.assertEqual(wrapper.pages, before)
        self.assertEqual(len(original['cells']), 3)
        self.assertEqual(page.crop.call_args_list[0].args[0], (0, 0, 30, 30))

    def test_wrong_header_multiple_tables_or_unbound_rows_stay_unknown(self):
        for options in [{'header': ('EN', 'ENO', 'other')}, {'candidates': 0},
                        {'candidates': 2}, {'ambiguous': True}]:
            with self.subTest(options=options):
                wrapper, original, page = self.geometry(**options)
                before = copy.deepcopy(wrapper.pages)
                result, reason = wrapper.n_execution_result_table('own', 1, original)
                self.assertIsNone(result)
                self.assertTrue(reason)
                self.assertEqual(wrapper.pages, before)
                if 'header' in options:
                    page.crop.assert_not_called()


class QLNBcdSourceBindingTests(unittest.TestCase):
    def call_only_section(self, symbol):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import FAMILY_HEADINGS
        text = ('Function with EN/ENO (%s)' if symbol.endswith('_E')
                else 'Function without EN/ENO (%s)') % symbol
        return SimpleNamespace(manual='sh080784engn', title=symbol, text=text,
            chapter=next(heading for heading, family in FAMILY_HEADINGS if family == 'n_conversion'),
            symbols=[symbol], bind=lambda witness: [('synthetic-call', 1)] if witness in text else [],
            call_forms=lambda: [])

    def test_call_drawing_does_not_prove_bcd_conversion_rules(self):
        from tools.fact_ql_behavior_review import _reviewed_n_specs
        for symbol in ('BCD_TO_INT', 'BCD_TO_INT_E', 'BCD_TO_DINT', 'BCD_TO_DINT_E'):
            with self.subTest(symbol=symbol):
                self.assertEqual(_reviewed_n_specs(self.call_only_section(symbol), symbol), [])

    def test_other_conversions_do_not_acquire_bcd_rules_or_cross_interface_dependencies(self):
        from tools.fact_ql_behavior_review import _reviewed_n_specs
        for symbol in ('INT_TO_BCD', 'DINT_TO_BCD_E', 'INT_TO_REAL', 'REAL_TO_INT_E'):
            with self.subTest(symbol=symbol):
                specs = _reviewed_n_specs(self.call_only_section(symbol), symbol)
                operation, = [spec for spec in specs if spec['dimension'] == 'effects.operation']
                self.assertNotIn('depends_on', operation)
                self.assertNotIn('input_branches', operation['value'])
                self.assertNotIn('invalid_digit', operation['value'])


class QLNNonBoolDisabledProducerTests(unittest.TestCase):
    def fixture(self):
        from types import SimpleNamespace
        from tools.fact_ql_behavior_review import _reviewed_spec
        section = SimpleNamespace(manual='sh080784engn', symbols=['BOOL_TO_INT_E'],
            _n_execution_rows=[{'EN': False, 'ENO': False, 'output': 'Undefined value',
                                'witness': 'own false result row', 'page': 8}])
        section.bind = lambda witness: [('own', 8)] if witness == 'own false result row' else [('own', 7)]
        typed = _reviewed_spec('typed', 'parameters.typed_operands', ['own declaration'],
            {'operands': [{'name': 'EN', 'type': 'BOOL', 'direction': 'input'},
                          {'name': 'ENO', 'type': 'BOOL', 'direction': 'output'},
                          {'name': 'd', 'type': 'INT', 'direction': 'output'}]}, ('st', 'fbd'))
        typed['depends_on'] = ['own_CPU']
        block = copy.deepcopy(typed)
        block.update(languages=('ladder',), conditions=[{'context': 'project_mode', 'equals': 'structured_ladder_block'}])
        return section, typed, block

    def test_own_continuation_result_reaches_three_interfaces_without_new_value_rules(self):
        from tools.fact_ql_behavior_review import _n_own_disabled_specs
        section, typed, block = self.fixture()
        before = copy.deepcopy([typed, block])
        specs = _n_own_disabled_specs(section, 'BOOL_TO_INT_E', [typed, block])
        self.assertEqual(len(specs), 2)
        for spec in specs:
            self.assertEqual(spec['dimension'], 'execution.disabled')
            self.assertEqual(spec['value']['when_disabled'], 'undefined_do_not_use')
            self.assertEqual(spec['value']['disabled_ENO'], False)
            self.assertEqual(spec['value']['outputs'], ['d'])
            self.assertEqual(spec['depends_on'], ['own_CPU'])
        self.assertEqual(specs[1]['conditions'], block['conditions'])
        self.assertEqual([typed, block], before)
        self.assertEqual(_n_own_disabled_specs(section, 'BOOL_TO_INT_E', [typed, block, *specs]), [])

    def test_no_own_false_row_or_nonbool_declaration_never_infers_OFF(self):
        from tools.fact_ql_behavior_review import _n_own_disabled_specs
        for case in ['missing', 'ENO_true', 'unknown_output', 'BOOL', 'generic', 'foreign', 'plain',
                     'unreviewed', 'native', 'multiple_d']:
            with self.subTest(case=case):
                section, typed, _ = self.fixture()
                symbol = 'BOOL_TO_INT_E'
                if case == 'missing': section._n_execution_rows = []
                if case == 'ENO_true': section._n_execution_rows[0]['ENO'] = True
                if case == 'unknown_output': section._n_execution_rows[0]['output'] = 'unknown'
                if case == 'BOOL': typed['value']['operands'][-1]['type'] = 'BOOL'
                if case == 'generic': typed['value']['operands'][-1]['type'] = 'ANY'
                if case == 'foreign': section.manual = 'other'
                if case == 'plain': symbol = 'BOOL_TO_INT'
                if case == 'unreviewed': typed['reviewed'] = False
                if case == 'native':
                    typed['languages'] = ('ladder',)
                    typed['conditions'] = [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}]
                if case == 'multiple_d': typed['value']['operands'].append(copy.deepcopy(typed['value']['operands'][-1]))
                self.assertEqual(_n_own_disabled_specs(section, symbol, [typed]), [])

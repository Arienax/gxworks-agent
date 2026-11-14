"""Task meaning and explicit allocation checks against the saved FX3U source."""
import unittest
import copy
from unittest.mock import patch

from application.fbd import check_fbd_implementation_plan
from application.fbd_context import call_bindings, select_task_catalog
from gxw.object_model import generate_object_project, read_project_context
from gxw.project_templates import blank_fbd_baseline


class FBDPlanSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = blank_fbd_baseline('FX3U')
        cls.context = read_project_context(cls.baseline)
        cls.catalog = {'nodes': cls.context.catalog()}

    def test_process_off_delay_selects_real_iec_tof(self):
        selected = select_task_catalog(self.catalog, request='三路信号消失后分别延时3、5、7秒关闭')
        row = next(row for row in selected['nodes'] if row['template'] == 'function_block:TOF')
        self.assertEqual(row['origin']['stream'], 'IEC.lif')
        self.assertEqual(row['origin']['kind'], 'standard_iec')
        self.assertEqual(selected['selected']['function_block:TOF'], 'process_semantics')
        self.assertNotIn('function_block:TOF', selected['optional_templates'])
        self.assertEqual({p['name'] for p in row['ports']}, {'IN', 'PT', 'Q', 'ET'})

    def test_other_source_backed_meanings_and_unknown_body(self):
        for request, template in [('上升沿', 'function_block:R_TRIG'),
                                  ('下降沿', 'function_block:F_TRIG'),
                                  ('向下计数', 'function_block:CTD'),
                                  ('锁存', 'function_block:SR_E'),
                                  ('逻辑', 'function:AND_E-2'),
                                  ('比较', 'function:@EQ-2')]:
            with self.subTest(request=request):
                selected = select_task_catalog(self.catalog, request=request)
                self.assertIn(template, selected['selected'])
                self.assertEqual(selected['selected'][template], 'process_semantics')
        unknown = {'template': 'function_block:TOF_Custom', 'kind': 'function_block',
                   'description': 'off delay timer', 'ports': []}
        selected = select_task_catalog({'nodes': [unknown]}, request='信号消失后延时关闭')
        self.assertNotIn(unknown['template'], selected['selected'])

    def test_confirmed_selection_and_existing_interface_survive(self):
        spec = {'implementation_items': [{'id': 'off', 'language': 'fbd',
                 'template': 'function_block:TOF', 'instances': ['T1', 'T2', 'T3']}]}
        selected = select_task_catalog(self.catalog, request='', confirmed_spec=spec)
        self.assertEqual(selected['selected']['function_block:TOF'], 'confirmed_implementation')
        previous = {'nodes': [{'id': 'old', 'template': 'function_block:TOF', 'symbol': 'OldTimer'}]}
        selected = select_task_catalog(self.catalog, request='比较', previous=previous)
        self.assertEqual(selected['selected']['function_block:TOF'], 'current_graph_reference')
        binding = call_bindings(previous, self.catalog)[0]
        self.assertEqual(binding['instance'], 'OldTimer')
        self.assertEqual(binding['interface_status'], 'available')

    def test_discovery_keeps_provenance_and_does_not_rank_unrelated_catalog_first(self):
        from knowledge.capability_discovery import discover
        from application.fbd_context import _discovery_names
        result = discover('信号消失后延时关闭', plc_model='FX3U', target_mode='fbd',
                          catalog=self.catalog, retrieve=lambda *args, **kwargs: [])
        tof = next(row for row in result['candidates'] if row['template'] == 'function_block:TOF')
        self.assertEqual(tof['origin']['kind'], 'standard_iec')
        self.assertIn('off_delay', tof['functions'])
        self.assertEqual({p['name'] for p in tof['interface']}, {'IN', 'PT', 'Q', 'ET'})
        self.assertIn('TOF', _discovery_names(result))
        self.assertNotIn('BITARR_TO_INT', _discovery_names(result))

    def test_shared_check_binds_native_name_and_common_source_suffix(self):
        from application.fbd import attach_plan_check, FBDValidationError
        spec = {'implementation_items': [{'id': 'timer', 'language': 'fbd', 'location': 'Main.pou',
                'template': 'function_block:TOF', 'instances': ['T1']}]}
        graph = {'program': 'Main', 'nodes': [{'template': 'function_block:TOF', 'symbol': 'T1'}]}
        validation = {}
        attach_plan_check(validation, spec, {'Main.pou': graph})
        self.assertEqual(validation['implementation_plan']['status'], 'passed')
        other = {'program': 'Other', 'nodes': graph['nodes']}
        self.assertEqual(check_fbd_implementation_plan(spec, {'Main.pou': other})['status'], 'partial')
        self.assertEqual(check_fbd_implementation_plan(spec, {'Main': graph, 'Main.pou': graph})['status'], 'partial')
        with self.assertRaises(FBDValidationError):
            attach_plan_check(validation, spec, {'Main': {'program': 'Main', 'nodes': []}})
        self.assertEqual(validation['implementation_plan']['status'], 'failed')

    def test_specific_behavior_preferences_do_not_treat_opposites_as_equal(self):
        cases = [
            ('输入持续有效7秒后接通', 'function_block:TON', 'function_block:TOF'),
            ('输入断开后保持7秒', 'function_block:TOF', 'function_block:TON'),
            ('上升沿', 'function_block:R_TRIG', 'function_block:F_TRIG'),
            ('下降沿', 'function_block:F_TRIG', 'function_block:R_TRIG'),
            ('向上计数', 'function_block:CTU', 'function_block:CTD'),
            ('向下计数', 'function_block:CTD', 'function_block:CTU'),
            ('置位优先', 'function_block:SR_E', 'function_block:RS_E'),
            ('复位优先', 'function_block:RS_E', 'function_block:SR_E'),
            ('大于等于', 'function:@GE-2', 'function:@GT-2'),
            ('不大于', 'function:@LE-2', 'function:@GT-2'),
            ('不等于', 'function:@NE', 'function:@EQ-2'),
            ('大于', 'function:@GT-2', 'function:@GE-2'),
            ('等于', 'function:@EQ-2', 'function:@NE'),
            ('逻辑与', 'function:@AND-2', 'function:@OR-2'),
            ('逻辑或', 'function:@OR-2', 'function:@AND-2'),
            ('异或', 'function:@XOR-2', 'function:@OR-2'),
            ('逻辑非', 'function:NOT', 'function:@AND-2'),
        ]
        for request, preferred, opposite in cases:
            with self.subTest(request=request):
                selected = select_task_catalog(self.catalog, request=request)
                self.assertIn(preferred, selected['preferred_templates'])
                self.assertNotIn(opposite, selected['preferred_templates'])
                self.assertNotEqual(selected['selected'].get(opposite), 'process_semantics')
                row = next(row for row in selected['nodes'] if row['template'] == preferred)
                self.assertEqual(row['selection_priority'], 'behavior_match')

    def test_distinct_confirmed_owners_cannot_share_one_fb_instance(self):
        graph = {'program': 'Main', 'nodes': [{'template': 'function_block:TOF', 'symbol': 'Timer1'}]}
        spec = {'implementation_items': [
            {'id': f'channel{i}', 'language': 'fbd', 'location': 'Main',
             'template': 'function_block:TOF', 'instances': ['Timer1'], 'state_owner': f'Channel{i}.timer'}
            for i in range(1, 4)]}
        report = check_fbd_implementation_plan(spec, {'Main': graph})
        self.assertEqual(report['status'], 'failed')
        self.assertEqual({row['code'] for row in report['findings']}, {'fbd_plan_independent_state_conflict'})
        for item in spec['implementation_items']:
            item['state_owner'] = '共享定时状态'
        self.assertEqual(check_fbd_implementation_plan(spec, {'Main': graph})['status'], 'passed')
        for item in spec['implementation_items']:
            item.pop('state_owner')
        self.assertEqual(check_fbd_implementation_plan(spec, {'Main': graph})['status'], 'passed')

    def test_three_distinct_tof_instances_round_trip_and_static_conflicts(self):
        name = self.context.program.logical_name
        spec = {'implementation_items': [{'id': 'off', 'language': 'fbd', 'location': name,
                'template': 'function_block:TOF', 'instances': ['Delay1', 'Delay2', 'Delay3']}]}
        model = {'program': name, 'networks': [{'calls': [
            {'id': f'call{i}', 'type': 'TOF', 'instance': f'Delay{i}',
             'arguments': {'IN': f'M{i}', 'PT': f'T#{seconds}s', 'Q': f'M{i+10}'}}
            for i, seconds in enumerate((3, 5, 7), 1)]}]}
        saved = generate_object_project(model, baseline=self.baseline)
        readback = read_project_context(saved.data)
        graph = readback.object_model()
        actual = [node for node in graph['nodes'] if node.get('template') == 'function_block:TOF']
        self.assertEqual({node['symbol'] for node in actual}, {'Delay1', 'Delay2', 'Delay3'})
        from gxw.ladder_lowering import fbd_topology_from_context
        bindings = call_bindings(fbd_topology_from_context(readback), {'nodes': readback.catalog()})
        durations = {row['instance']: next(arg['value'] for arg in row['arguments'] if arg['name'] == 'PT')
                     for row in bindings if row['template'] == 'function_block:TOF'}
        self.assertEqual(durations, {'Delay1': 'T#3s', 'Delay2': 'T#5s', 'Delay3': 'T#7s'})
        self.assertEqual(check_fbd_implementation_plan(spec, {name: graph})['status'], 'passed')
        graph['nodes'].remove(actual[0])
        report = check_fbd_implementation_plan(spec, {name: graph})
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['findings'][0]['code'], 'fbd_plan_instance_missing')
        self.assertEqual(report['runtime_semantics'], 'not_checked')
        actual[1]['template'] = 'function_block:TON'
        report = check_fbd_implementation_plan(spec, {name: graph})
        self.assertIn('fbd_plan_type_conflict', [row['code'] for row in report['findings']])
        self.assertEqual(check_fbd_implementation_plan(spec, {})['status'], 'partial')

    def test_real_mcp_context_second_turn_preserves_instances_and_bound_vendor_block(self):
        """Authored fixture uses real FX3U IEC/MELSEC sources, not a customer GXW."""
        from agent_runtime.plc_tools import build_default_tool_registry
        from agent_runtime.tool_context import ToolContext
        from application.read_delivery import assemble_read_pages
        from application.fbd import build_fbd_generation_context
        from gxw.ladder_lowering import fbd_topology_from_context
        registry = build_default_tool_registry()
        wire_packets = []
        selection_reports = []

        def capture(*args, **kwargs):
            result = build_fbd_generation_context(*args, **kwargs)
            wire_packets.append(result['wire_messages'])
            selection_reports.append(result['generation_handoff']['catalog_report'])
            return result

        def request(raw, requirement, version):
            ctx = ToolContext(project={'id': 'selection-fixture', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
                              version={'id': version, 'target_mode': 'fbd', 'plc_model': 'FX3U'},
                              fbd_baseline=raw)
            with patch('application.fbd.build_fbd_generation_context', side_effect=capture), \
                    patch('application.model_api._request_model', side_effect=AssertionError('model call forbidden')):
                first = registry.call('get_generation_context', {'user_requirement': requirement}, ctx)
                self.assertTrue(first['ok'], first.get('error'))
                pages = [first]
                while not pages[-1]['data']['delivery']['complete']:
                    step = pages[-1]['data']['delivery']['next']
                    page = registry.call(step['tool'], step['arguments'], ctx)
                    self.assertTrue(page['ok'], page.get('error'))
                    pages.append(page)
            return assemble_read_pages(pages)

        first = request(self.baseline, '三路信号消失后分别延时3、5、7秒关闭', 'empty')
        first_catalog = first['generation_input']['catalog']
        tof = next(row for row in first_catalog if row['template'] == 'function_block:TOF')
        self.assertEqual(tof['origin']['kind'], 'standard_iec')
        self.assertEqual({p['name'] for p in tof['ports']}, {'IN', 'PT', 'Q', 'ET'})
        self.assertIn('function_block:TOF', str(wire_packets[-1]))
        name = self.context.program.logical_name
        model = {'program': name, 'networks': [{'calls': [
            {'id': f'call{i}', 'type': 'TOF', 'instance': f'Delay{i}',
             'arguments': {'IN': f'M{i}', 'PT': f'T#{seconds}s', 'Q': f'M{i+10}'}}
            for i, seconds in enumerate((3, 5, 7), 1)],
            'nodes': [{'id': 'vendor_move', 'template': 'function:MOV', 'symbol': 'MOV',
                       'arguments': {'EN': 'M20', 's': 'D30', 'd': 'D31'}}]}]}
        saved = generate_object_project(model, baseline=self.baseline)
        before = read_project_context(saved.data)
        second = request(saved.data, '只把第三路从7秒改9秒，另外两路及实例保持，沿用现有厂商 MOV 块', 'timers')
        payload = second['generation_input']
        self.assertIn('优先复用与确认方案一致且已绑定来源的厂商库/项目块', second['generation_instructions'])
        move = next(row for row in payload['catalog'] if row['template'] == 'function:MOV')
        self.assertEqual(move['origin']['kind'], 'bound_vendor_library')
        self.assertEqual(move['origin']['stream'], 'MELSEC.lif')
        selected = selection_reports[-1]['selected']
        self.assertEqual(selected['function:MOV'], 'current_graph_reference')
        timers = [row for row in payload['call_bindings'] if row['template'] == 'function_block:TOF']
        self.assertEqual({row['instance'] for row in timers}, {'Delay1', 'Delay2', 'Delay3'})
        self.assertTrue(all(row['interface_status'] == 'available' for row in timers))
        edited = copy.deepcopy(payload['previous'])
        # The actual MCP projection contains terminals and wires. Change the
        # terminal bound to Delay3.PT without reconstructing the other calls.
        touched = []
        for network in edited['networks']:
            nodes = {node['id']: node for node in network['nodes']}
            third = next((node for node in nodes.values() if node.get('symbol') == 'Delay3'), None)
            if third:
                for wire in network['wires']:
                    if wire['to'] == third['id'] + '.PT':
                        terminal = nodes[wire['from'].rsplit('.', 1)[0]]
                        self.assertEqual(terminal['symbol'], 'T#7s')
                        terminal['symbol'] = 'T#9s'
                        touched.append(terminal['id'])
        self.assertEqual(len(touched), 1)
        after_saved = generate_object_project(edited, baseline=saved.data)
        after = read_project_context(after_saved.data)
        before_bindings = call_bindings(fbd_topology_from_context(before), {'nodes': before.catalog()})
        after_bindings = call_bindings(fbd_topology_from_context(after), {'nodes': after.catalog()})

        def arguments(rows):
            return {(row['template'], row['instance']): {arg['name']: arg['value'] for arg in row['arguments']}
                    for row in rows}

        expected = arguments(before_bindings)
        expected[('function_block:TOF', 'Delay3')]['PT'] = 'T#9s'
        self.assertEqual(arguments(after_bindings), expected)
        self.assertEqual(len(wire_packets), 2)


if __name__ == '__main__':
    unittest.main()

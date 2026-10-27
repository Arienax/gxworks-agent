
from tools.offline_inputs import private_path
from copy import deepcopy
import base64
from dataclasses import replace
from functools import lru_cache
import json
from itertools import permutations
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import zlib

import pytest

from src.gxw.models import GXWFormatError, NodeKind
from src.gxw.object_model import (build_object_program, catalog_description, default_baseline,
    export_object_model, generate_object_project, read_project, read_project_context)
from src.gxw.ladder_lowering import ladder_to_object_model
from src.gxw.render import render_structured_svg
from src.gxw.fb_connectivity import fb_connectivity_model
from tests.test_gxw_declarations import baseline


def _blank_menu_cases():
    # The independently frozen New-dialog observations are the coverage oracle.
    fixture = json.loads((private_path('tests/fixtures/gxworks2_native_cpu_menu.json')).read_text(encoding='utf-8'))
    return sorted({r['cpu'] for r in fixture['project_mode_observation']['rows']
                   if r['selected_type'] == '结构化工程' and '结构化梯形图/FBD' in r['languages']})


@pytest.mark.parametrize('cpu', _blank_menu_cases())
def test_blank_fbd_templates_keep_each_native_cpu_and_support_an_independent_edit(cpu):
    from src.gxw.native_diagnostics import native_validation_source_plan
    from src.gxw.project_templates import blank_fbd_baseline, blank_fbd_models
    from gxworks2.project_profiles import native_project_profile
    raw = blank_fbd_baseline(cpu)
    context = read_project_context(raw)
    assert set(blank_fbd_models()) == set(_blank_menu_cases())
    assert native_project_profile(context.sources.cpu).cpu == cpu
    assert len(context.programs) == 1 and not context.program.nodes and not context.program.unknown_records
    assert all(not table.rows for table in context.declarations.values())
    assert sorted(table.scope for table in context.declarations.values()) == ['global', 'local']
    source = native_validation_source_plan(raw)
    assert len(source['programs']) == 1 and source['programs'][0]['program_kind'] == 208
    assert source['libraries'] == []
    scopes = context.task_scopes()
    owner = context.declarations[context.sources.local_table].owner_name
    assert scopes['tasks'][0]['programs'] == [dict(name=owner, line=1)]
    draft = context.empty_model()
    draft.update(nodes=[dict(id='signal', template='input', symbol='X0', x=2, y=2),
                        dict(id='coil', template='output', symbol='Y0', x=10, y=2)],
                 wires=[{'from': 'signal.OUT', 'to': 'coil.IN'}])
    global_table, = [name for name, table in context.declarations.items() if table.scope == 'global']
    draft['declaration_edits'] = {global_table: {'upserts': [dict(
        name='TemplateGlobal', data_type='BOOL', class_name='VAR_GLOBAL')]}}
    saved = read_project_context(generate_object_project(draft, baseline=raw).data)
    assert saved.sources.cpu == context.sources.cpu and saved.task_scopes() == scopes
    assert [(n.kind.value, n.symbol) for n in saved.program.nodes] == [('input', 'X0'), ('output', 'Y0')]
    assert len(saved.program.wires) == 1
    assert saved.object_model()['issues'] == []
    assert [(row.name, row.data_type) for row in saved.declarations[global_table].rows] == [('TemplateGlobal', 'BOOL')]


@pytest.mark.parametrize('cpu', ['FX5U', 'Q2A', 'unknown', 'LJ72GF15-T2', 'LJ72MS15', 'QJ72LP25'])
def test_blank_fbd_template_rejects_unknown_and_parameter_only_targets(cpu):
    from src.gxw.project_templates import blank_fbd_baseline
    with pytest.raises(GXWFormatError, match='CPU|parameter-only'):
        blank_fbd_baseline(cpu)


@pytest.mark.parametrize('index', [0, 41, 245, 472, 510, 600, 602, 603])
def test_offline_original_fbd_adds_variables_nodes_and_connections_in_one_source_transaction(index, monkeypatch):
    from src.gxw.connectivity import build_connectivity_graph
    from src.gxw.offline_project import read_offline_project
    from tests.test_gxw_structured_reader import offline_fbd_project
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('offline FBD started a process'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('offline FBD started a process'))
    raw, expected = offline_fbd_project(index)
    context = read_project_context(raw, expected['source'])
    model = context.object_model()
    assert generate_object_project(model, baseline=raw).data == raw
    old = read_offline_project(raw)
    table = context.sources.local_table
    assert table is not None
    name = 'OfflineVariable_With_Long_Name_0123456789'
    y = context.program.blocks[0].canvas_height + 4
    model['nodes'].extend([dict(id='offline_input', template='input', symbol=name, x=2, y=y),
                           dict(id='offline_output', template='output', symbol=name, x=15, y=y)])
    if model.get('blocks'):
        for node in model['nodes'][-2:]: node['block'] = 0
    wire = {'from':'offline_input.OUT', 'to':'offline_output.IN'}
    if model.get('blocks'): wire['block'] = 0
    model['wires'].append(wire)
    model['declaration_edits'] = {table:{'upserts':[dict(name=name, data_type='BOOL', comment='完整声明说明😀')]}}
    result = generate_object_project(model, baseline=raw)
    saved = read_project_context(result.data, expected['source'])
    new = read_offline_project(result.data)
    assert saved.sources.cpu == expected['cpu']
    assert len(saved.program.nodes) == len(context.program.nodes) + 2
    assert len(saved.program.wires) == len(context.program.wires) + 1
    assert any(row.name == name and row.comment == '完整声明说明😀' for row in saved.declarations[table].rows)
    for block_index, ((_, old_records), (_, new_records)) in enumerate(zip(context.program.block_records(), saved.program.block_records())):
        assert [record.raw for record in new_records[:len(old_records)]] == [record.raw for record in old_records]
        assert len(new_records) == len(old_records) + (3 if block_index == 0 else 0)
    assert [record.raw for record in saved.program.unknown_records] == [record.raw for record in context.program.unknown_records]
    assert [block.raw_header[4:-8] for block in saved.program.blocks] == [block.raw_header[4:-8] for block in context.program.blocks]
    input_node = next(node for node in saved.program.nodes if node.symbol == name and node.kind == NodeKind.INPUT)
    output_node = next(node for node in saved.program.nodes if node.symbol == name and node.kind == NodeKind.OUTPUT)
    assert build_connectivity_graph(saved.program).ports_connected(input_node.offset, 0, output_node.offset, 0)
    assert saved.task_scopes() == context.task_scopes()
    assert {key:value for key,value in old.streams.items() if key not in (table,expected['source'])} == {
        key:value for key,value in new.streams.items() if key not in (table,expected['source'])}
    assert result.report['write_backend'] == 'offline' and result.report['compile_state'] == 'stale'


@pytest.mark.parametrize('index', [0, 41, 600, 602])
def test_offline_original_existing_fb_new_instance_and_port_edit_preserve_definition_and_original_nodes(index):
    from src.gxw.offline_project import read_offline_project
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw, expected = offline_fbd_project(index)
    context = read_project_context(raw, expected['source'])
    prototype = next(node for node in context.program.nodes if node.kind == NodeKind.FUNCTION_BLOCK
                     and context.sources.callable(node))
    ports = context.sources.callable(prototype)['ports']
    endpoint = ports[0]['name']
    model = context.object_model()
    clone = dict(id='offline_fb', template='function_block:' + prototype.type_name,
                 prototype_offset=prototype.offset, symbol='OfflineExistingFB_Instance_Long_Name',
                 x=20, y=context.program.blocks[0].canvas_height + 4,
                 port_edits={endpoint:{'negated':not ports[0]['negated']}})
    if model.get('blocks'): clone['block'] = 0
    model['nodes'].append(clone)
    result = generate_object_project(model, baseline=raw)
    saved = read_project_context(result.data, expected['source'])
    instance = next(node for node in saved.program.nodes if node.symbol == clone['symbol'])
    assert instance.type_name == prototype.type_name and instance.symbol == clone['symbol']
    assert saved.sources.callable(instance)['ports'][0]['negated'] != ports[0]['negated']
    assert saved.sources.label(instance.symbol)[1].data_type == prototype.type_name
    assert [node.raw for node in saved.program.nodes if node.symbol != clone['symbol']] == [node.raw for node in context.program.nodes]
    assert [wire.raw for wire in saved.program.wires] == [wire.raw for wire in context.program.wires]
    old, new = read_offline_project(raw), read_offline_project(result.data)
    changed = {key for key in old.streams if old.streams[key] != new.streams[key]}
    assert changed == {expected['source'], context.sources.local_table}
    # Deleting the graphic keeps its now-unused declaration and every definition.
    draft = saved.object_model();draft['nodes'] = [node for node in draft['nodes'] if node['symbol'] != clone['symbol']]
    deleted = read_project_context(generate_object_project(draft, baseline=result.data).data, expected['source'])
    assert deleted.sources.label(instance.symbol)[1].data_type == prototype.type_name
    assert [node.raw for node in deleted.program.nodes] == [node.raw for node in context.program.nodes]


def test_offline_fbd_reader_preserves_usable_source_when_other_declaration_tables_are_unreadable():
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw, expected = offline_fbd_project(6)
    context = read_project_context(raw, expected['source'])
    graph = context.object_model()
    gaps = {issue['stream'] for issue in graph['issues'] if issue['code'] == 'declaration_source_gap'}
    assert gaps == set()  # History storage IDs resolve these original tables.
    assert context.program.nodes and graph['nodes']
    assert generate_object_project(graph, baseline=raw).data == raw


def test_offline_fbd_noop_and_unrelated_edits_do_not_repair_an_unresolved_fb_instance():
    from src.gxw.declarations import edit_declarations, serialize_declarations
    from src.gxw.project_writer import write_project_replacements
    from src.gxw.offline_project import read_offline_project
    raw = source_binding_baseline()
    context = read_project_context(raw)
    table = context.sources.local_table
    local = edit_declarations(context.declarations[table], remove=['TIMER_A'])
    project = read_offline_project(raw)
    broken = write_project_replacements(raw, {table:(project.mapping[table], local.raw, serialize_declarations(local))}).data
    context = read_project_context(broken)
    model = context.object_model()
    assert any(issue['code'] == 'callable_source_gap' for issue in model['issues'])
    assert generate_object_project(model, baseline=broken).data == broken
    model['nodes'].append(dict(id='independent', template='input', symbol='TRUE', x=2, y=30))
    saved = read_project_context(generate_object_project(model, baseline=broken).data)
    assert saved.sources.label('TIMER_A') is None
    assert saved.declarations[table].raw == context.declarations[table].raw


def test_offline_fbd_constant_only_ports_bind_the_cpu_selected_formal_and_reject_wrong_formals():
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw, expected = offline_fbd_project(602)
    context = read_project_context(raw, expected['source'])
    node = next(node for node in context.program.nodes if node.symbol == 'FMOV')
    ports = context.sources.callable(node)['ports']
    assert [(port['formal_name'],port['class_code']) for i,port in enumerate(ports)
            if node.ports[i].port_kind_code == 7] == [('n',11)]
    wrong = replace(node, ports=(replace(node.ports[0], port_kind_code=7), *node.ports[1:]))
    with pytest.raises(GXWFormatError, match='constant-only port'):
        context.sources.callable(wrong)


def test_offline_fbd_project_enable_options_bind_formals_and_construct_new_instances():
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw, expected = offline_fbd_project(245)
    context = read_project_context(raw, expected['source'])
    node = next(node for node in context.program.nodes if node.type_name == 'Mov4INT')
    assert len(node.ports) == 10
    ports = context.sources.callable(node)['ports']
    assert [port['formal_name'] for port in ports] == [
        'EN','i_In11','i_In12','i_In13','i_In14','ENO','i_In21','i_In22','i_In23','i_In24']
    assert [port['side'] for port in ports] == ['in']*5+['out']*5
    assert [port['data_type'] for port in ports] == ['BOOL']+['INT']*4+['BOOL']+['INT']*4
    assert context.sources.source_prototype(node)['binding'] == 'resolved'
    model = context.object_model()
    assert generate_object_project(model,baseline=raw).data == raw
    model['nodes'].append(dict(id='new_mov4',template='function_block:Mov4INT',symbol='NewMov4',
        x=2,y=context.program.blocks[0].canvas_height+3,block=0,port_edits={'EN':{'negated':True}}))
    saved = read_project_context(generate_object_project(model,baseline=raw).data,expected['source'])
    clone = next(item for item in saved.program.nodes if item.symbol=='NewMov4')
    assert [port['formal_name'] for port in saved.sources.callable(clone)['ports']] == [port['formal_name'] for port in ports]
    assert saved.sources.callable(clone)['ports'][0]['negated'] is True
    assert saved.sources.label('NewMov4')[1].data_type == 'Mov4INT'
    assert [item.raw for item in saved.program.nodes if item.symbol!='NewMov4'] == [item.raw for item in context.program.nodes]


@pytest.mark.parametrize('damage',['status','position','unbound','record_type','opaque_tail'])
def test_offline_callable_prototype_rejects_wrong_binding_and_incomplete_records(damage):
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw,expected=offline_fbd_project(245);context=read_project_context(raw,expected['source'])
    node=next(node for node in context.program.nodes if node.type_name=='Mov4INT')
    sources=context.sources
    if damage=='status':
        stream,definition=sources.definition(node.type_name,'FUNCTION_BLOCK')
        sources=sources.with_declarations({**sources.declarations,stream:replace(definition['declarations'],owner_pou_status=3)})
    elif damage=='position':node=replace(node,ports=(replace(node.ports[0],local_y=0),*node.ports[1:]))
    elif damage=='record_type':node=replace(node,raw=node.raw[:4]+b'\x02\0\0\0'+node.raw[8:])
    elif damage=='opaque_tail':node=replace(node,raw=node.raw+b'\0'*4)
    else:node=replace(node,symbol='MissingInstance')
    with pytest.raises(GXWFormatError):sources.source_prototype(node)


@pytest.mark.parametrize('change',['negated','formal_type'])
def test_offline_callable_prototype_keeps_negation_and_current_declared_types(change):
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw,expected=offline_fbd_project(245);context=read_project_context(raw,expected['source'])
    node=next(node for node in context.program.nodes if node.type_name=='Mov4INT')
    sources=context.sources
    if change=='formal_type':
        stream,definition=sources.definition(node.type_name,'FUNCTION_BLOCK');doc=definition['declarations']
        sources=sources.with_declarations({**sources.declarations,stream:replace(doc,
            rows=(replace(doc.rows[0],data_type='BOOL'),*doc.rows[1:]))})
    else:node=replace(node,ports=(replace(node.ports[0],port_kind_code=9),*node.ports[1:]))
    binding=sources.source_prototype(node)
    assert binding['ports'][0]['formal_name']=='EN'
    assert binding['ports'][0]['negated'] is (change=='negated')
    assert binding['ports'][1]['data_type']==('BOOL' if change=='formal_type' else 'INT')


@pytest.mark.parametrize('change', ['symbol','position','delete'])
def test_offline_fbd_unknown_node_kind_is_locally_preserved_and_cannot_be_targeted(change):
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    source, documents, _ = read_project(default_baseline())
    raw = bytearray(source.raw)
    import struct
    struct.pack_into('<I',raw,source.nodes[0].offset+8,0x501)
    source = parse_structured_pou(bytes(raw),logical_name=source.logical_name,preserve_unsupported_records=True)
    model = export_object_model(source,documents)
    assert any(issue['code']=='unsupported_graph_node' for issue in model['issues'])
    assert serialize_structured_pou(build_object_program(source,model)) == source.raw
    # Other known nodes remain editable in the same source.
    model['nodes'][1]['symbol'] = 'Y123'
    assert build_object_program(source,model).nodes[0].raw == source.nodes[0].raw
    if change=='delete':model['nodes'].pop(0)
    elif change=='symbol':model['nodes'][0]['symbol']='X999'
    else:model['nodes'][0]['x']+=1
    with pytest.raises(GXWFormatError,match='unsupported'):
        build_object_program(source,model)


@pytest.mark.parametrize('damage', ['direction','type'])
def test_offline_named_wire_edits_reject_known_direction_and_type_conflicts(damage):
    context=read_project_context(default_baseline())
    model=context.empty_model()
    model['nodes']=[dict(id='left',template='input',symbol='TRUE',x=2,y=25),
                    dict(id='right',template='output',symbol='INT#1' if damage=='type' else 'Y2',x=12,y=25)]
    model['wires']=[dict(**{'from':'right.IN' if damage=='direction' else 'left.OUT','to':'left.OUT' if damage=='direction' else 'right.IN'})]
    with pytest.raises(GXWFormatError,match='direction|types'):
        generate_object_project(model,baseline=context.raw)


@pytest.mark.parametrize('index,address,before_comments', [
    (0,'M0',{}), (602,'D11',{'D11':'DK_TC_SensorON'})])
def test_offline_fbd_graph_declaration_and_long_device_comment_share_one_transaction(monkeypatch,index,address,before_comments):
    import application.fbd as service
    from src.gxw.offline_project import read_offline_project
    from tests.test_gxw_structured_reader import offline_fbd_project
    monkeypatch.setattr(service, '_native_candidate', lambda *a, **kw: pytest.fail('implicit native FBD write'))
    raw, expected = offline_fbd_project(index)
    context = read_project_context(raw, expected['source'])
    model = context.object_model()
    model['nodes'].append(dict(id='joint', template='input', symbol='OfflineJoint',
        x=2, y=context.program.blocks[0].canvas_height+4, block=0))
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name='OfflineJoint',data_type='BOOL')]}}
    comment = '完整离线设备注释😀' * 24
    assert read_offline_project(raw).comments()[0] == before_comments
    result = service._write_candidate(model, baseline=raw, device_comments={address:comment})
    reopened = read_project_context(result.data, expected['source'])
    project = read_offline_project(result.data)
    assert reopened.sources.label('OfflineJoint')[1].data_type == 'BOOL'
    assert any(node.symbol == 'OfflineJoint' for node in reopened.program.nodes)
    assert project.comments()[0][address] == comment
    assert read_offline_project(raw).comments()[0] == before_comments
    with pytest.raises(ValueError):
        service._write_candidate(model, baseline=raw, device_comments={address:'invalid\0comment'})
    assert read_offline_project(raw).streams[expected['source']] == context.program.raw


def test_blank_fbd_aliases_follow_the_native_merged_menu_under_generated_spelling():
    from hypothesis import given, settings, strategies as st
    from src.gxw.project_templates import blank_fbd_baseline
    aliases = [(member, cpu) for cpu in _blank_menu_cases() for member in cpu.split('/')]

    @settings(max_examples=24, derandomize=True, database=None, deadline=None)
    @given(st.sampled_from(aliases), st.booleans(), st.sampled_from(['', ' ', '\t']))
    def check(pair, lower, padding):
        member, menu = pair
        spelling = member.lower() if lower else member
        assert blank_fbd_baseline(padding + spelling + padding) == blank_fbd_baseline(menu)

    check()


def test_blank_fbd_templates_replay_all_original_native_checks_and_saved_mcp_cases():
    from research.freeze_gxw_blank_fbd_templates import replay
    result = replay()
    assert result['menu_count'] == 57
    assert (result['observed_count'], result['passed_count'], result['rejected_count'], result['unverified_count']) == (50, 49, 1, 7)
    assert {row['cpu'] for row in result['cases'] + result['unverified']} == set(_blank_menu_cases())
    for row in result['cases']:
        assert row['compilation_completed'] and row['cold_source_read'] == 'current'
        assert row['exit_code'] == 0 and row['execution'] == 'not_requested'
        if row['cpu'] == 'FX1':
            assert row['compilation'] == 'rejected'
            assert row['public_check'] == dict(status='not_requested', completed=False)
            assert row['current_source_check'] == 'not_established'
            assert row['compile_errors'] == [dict(code=0x50f1034, name='Global1..', arguments=['TemplateGlobal'])]
        else:
            assert row['compilation'] == 'accepted'
            assert row['public_check'] == dict(status='passed', completed=True)
            assert row['current_source_check'] == 'established' and row['diagnostic_projection'] == 'completed'
    assert {row['cpu'] for row in result['mcp_acceptance']} == {'L02', 'FX3U/FX3UC', 'FXU/FX2C', 'Q03UDV'}


def composition_topology():
    return {"instances": [{"id": "signal", "template": "input", "symbol": "X2"},
                          {"id": "time", "template": "input", "symbol": "T#1s"},
                          {"id": "timer", "template": "function_block:TON", "symbol": "COMPOSITION_TIMER"},
                          {"id": "result", "template": "output", "symbol": "Y2"}],
            "connections": [{"from": "signal.OUT", "to": "timer.IN"},
                            {"from": "time.OUT", "to": "timer.PT"},
                            {"from": "timer.Q", "to": "result.IN"}], "execution_order": ["timer"]}


def test_topology_layout_and_cold_read_preserve_named_connections_and_imported_objects():
    from src.gxw.topology import assemble_topology, verify_topology
    context = read_project_context(default_baseline())
    old = context.object_model()
    model, receipt = assemble_topology(context, composition_topology(), plc_model="FX3U")
    assert model["nodes"][:len(old["nodes"])] == old["nodes"]
    assert model["wires"][:len(old["wires"])] == old["wires"]
    assert receipt["native_compile"] == "not_executed" and receipt["execution_order_status"] == "unverified_native_schedule"
    saved = generate_object_project(model, baseline=context.raw).data
    cold = read_project_context(saved)
    assert verify_topology(cold.program, model, composition_topology()["connections"])["status"] == "passed"
    assert any(r.name == "COMPOSITION_TIMER" and r.data_type == "TON" for r in cold.declarations[cold.sources.local_table].rows)
    assert [r.raw for r in cold.program.unknown_records] == [r.raw for r in context.program.unknown_records]


@pytest.mark.parametrize("output,expected", [("Y2", "passed"), ("Y3", "failed")])
def test_topology_independent_expectations_detect_a_legal_same_type_wrong_binding(output, expected):
    from src.gxw.topology import assemble_topology, check_binding_expectations
    graph = composition_topology()
    graph["instances"][-1]["symbol"] = output
    context = read_project_context(default_baseline())
    model, _ = assemble_topology(context, graph, plc_model="FX3U")
    saved = read_project_context(generate_object_project(model, baseline=context.raw).data)
    report = check_binding_expectations(saved, [{"from": {"symbol": "COMPOSITION_TIMER", "port": "Q"},
                                                "to": {"symbol": "Y2", "port": "IN"}}])
    assert report["status"] == expected and report["behavior"] == "unverified"


@pytest.mark.parametrize("failure", ["direction", "type", "dangling", "multiple_writers", "identity", "cpu"])
def test_topology_rejects_unbound_interfaces_without_rewiring_the_source(failure):
    from src.gxw.topology import assemble_topology
    graph = composition_topology()
    model = "FX3U"
    if failure == "direction": graph["connections"][0] = {"from": "timer.IN", "to": "signal.OUT"}
    elif failure == "type": graph["connections"][0]["to"] = "timer.PT"
    elif failure == "dangling": graph["connections"][0]["to"] = "missing.IN"
    elif failure == "multiple_writers": graph["connections"].append({"from": "signal.OUT", "to": "timer.IN"})
    elif failure == "identity": graph["instances"][2]["id"] = "signal"
    elif failure == "cpu": model = "Q03UDV"
    context = read_project_context(default_baseline())
    before = context.object_model()
    with pytest.raises(GXWFormatError): assemble_topology(context, graph, plc_model=model)
    assert context.object_model() == before


def test_topology_layout_translation_never_changes_the_requested_native_nets():
    from hypothesis import given, settings, strategies as st
    from src.gxw.topology import assemble_topology, verify_topology
    @settings(max_examples=12, deadline=None, derandomize=True)
    @given(x=st.integers(1, 30), y=st.integers(20, 45), order=st.permutations([0, 1, 2]))
    def exercise(x, y, order):
        graph = composition_topology()
        graph["connections"] = [graph["connections"][i] for i in order]
        context = read_project_context(default_baseline())
        before = context.object_model()
        model, _ = assemble_topology(context, graph, plc_model="FX3U", layout_origin=(x, y))
        saved = read_project_context(generate_object_project(model, baseline=context.raw).data)
        assert verify_topology(saved.program, model, graph["connections"])["nets"] == 3
        assert model["nodes"][:len(before["nodes"])] == before["nodes"]
    exercise()

@lru_cache(maxsize=1)
def root_actual_witnesses():
    return json.loads((private_path('tests/fixtures/gxw_root_actual_native.json')).read_text(encoding='utf-8'))


def root_actual_context(name='FX3U-FX3UC', *, stage='body'):
    """Core-only replay of original selected sources on their native input."""
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.project_writer import replace_project_stream
    case = next(r for r in root_actual_witnesses()['cases'] if r['name'] == name)
    raw = zlib.decompress(base64.b64decode(case['source_gxw_zlib_base64']))
    if stage != 'source':
        selected = case['created_source_streams'] if stage == 'created' else next(
            r['saved_source_streams'] for r in case['stages'] if r['phase'] == stage)
        outer = validate_cfb_streams(raw)
        mapping = logical_mapping(outer['projectdatalist.xml'])
        nested = outer['_hdb']
        for logical, payload in selected.items():
            nested, _ = replace_project_stream(nested, mapping[logical], base64.b64decode(payload))
        raw, _ = replace_project_stream(raw, '_hdb', nested)
        outer_sources = case['created_outer_streams'] if stage == 'created' else next(
            r['saved_outer_streams'] for r in case['stages'] if r['phase'] == stage)
        for name, payload in outer_sources.items():
            raw, _ = replace_project_stream(raw, name, base64.b64decode(payload))
    return read_project_context(raw, case['program']), case


def root_terminal_type(context, node):
    from src.gxw.declarations import _type_parts
    value = context.sources.structure_value(node.symbol)
    if value is not None:
        return value['data_type']
    binding = context.sources.label(node.symbol.split('[', 1)[0])
    assert binding is not None
    return _type_parts(binding[1].data_type)[0]


@pytest.mark.parametrize('name', ['FX3U-FX3UC', 'FX3U-FB', 'FX3G-independent', 'Q06UDV-motion'])
def test_root_local_creation_plan_and_saved_fields_follow_original_typed_constructor(name):
    from src.gxw.native_write import verify_native_save
    from research.freeze_gxw_flat_project_structures import root_actual_prepared
    context, case = root_actual_context(name, stage='source')
    prepared = root_actual_prepared(context.raw, case['original_native_plan'])
    saved, _ = root_actual_context(name, stage='created')
    assert verify_native_save(prepared, saved.raw, case['native_creation']).report['validation']['declarations'] == 'passed'
    assert saved.declarations[saved.sources.local_table].owner_pou_type == case['owner_pou_type']
    actuals = {r['name'] for r in case['actuals']}
    fields = ('name', 'data_type', 'class_code', 'initial_value', 'type_code', 'array_marker',
              'type_reference', 'unknown_u32', 'record_id', 'device')
    assert [{key:getattr(row, key) for key in fields} for row in saved.declarations[saved.sources.local_table].rows
            if row.name in actuals] == case['created_fields']


@pytest.mark.parametrize('name', ['FX3U-FX3UC', 'FX3U-FB', 'FX3G-independent', 'Q06UDV-motion'])
@pytest.mark.parametrize('phase', ['body', 'rename'])
def test_root_local_member_graph_and_rename_plans_match_native_source_bytes(name, phase):
    from src.gxw.native_write import verify_native_save
    from research.freeze_gxw_flat_project_structures import root_actual_prepared
    before, case = root_actual_context(name, stage='created' if phase == 'body' else 'body')
    original = next(r for r in case['stages'] if r['phase'] == phase)
    prepared = root_actual_prepared(before.raw, original['original_native_plan'], original['source_model'])
    saved, _ = root_actual_context(name, stage=phase)
    assert verify_native_save(prepared, saved.raw, original['native_save']).report['validation']['source_body'] == 'exact'
    assert not saved.object_model()['issues']
    if name == 'Q06UDV-motion':
        assert original['public_check'] != {'status':'passed', 'completed':True}
        if phase == 'rename':
            assert original['compilation'] == 'rejected'
            assert any(r['code'] == 0x500c5101 for r in original['compile_diagnostics'])


@pytest.mark.parametrize('name', ['FX3U-FX3UC', 'FX3U-FB', 'FX3G-independent', 'Q06UDV-motion'])
def test_root_local_terminal_types_and_directions_match_independent_native_references(name):
    context, case = root_actual_context(name)
    native = next(r for r in case['stages'] if r['phase'] == 'body')['native_references']
    projected = context.object_model()
    assert projected['schema_version'] == 2 and not projected['issues']
    for node, visible in zip(context.program.nodes, projected['nodes'], strict=True):
        matches = [r for r in native if r['name'] == node.symbol and
            (r['left'], r['top'], r['right'], r['bottom']) ==
            (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)]
        assert matches
        port, = visible['ports']
        assert {r['type'] for r in matches} == {root_terminal_type(context, node)}
        if '.' in node.symbol:
            assert port['data_type'] == root_terminal_type(context, node)
        assert port['name'] == ('OUT' if node.kind == NodeKind.INPUT else 'IN')
        assert {r['attribute'] for r in matches} == {1 if node.kind == NodeKind.INPUT else 2}
    assert generate_object_project(projected, baseline=context.raw).data == context.raw


def test_generated_root_local_rename_sequences_preserve_native_types_directions_and_other_sources():
    from hypothesis import given, settings, strategies as st
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.editor import edit_draft
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.project_writer import replace_project_stream

    @settings(max_examples=20, deadline=None, derandomize=True)
    @given(st.sampled_from(['FX3U-FX3UC', 'FX3U-FB', 'FX3G-independent']),
           st.lists(st.tuples(st.integers(min_value=0, max_value=3),
               st.text(alphabet='abcXYZ012_状态', min_size=1, max_size=4)), min_size=1, max_size=3))
    def exercise(name, edits):
        context, case = root_actual_context(name)
        original = next(r for r in case['stages'] if r['phase'] == 'body')['native_references']
        names = [r['name'] for r in case['actuals']]
        expected_symbols = [node.symbol for node in context.program.nodes]
        expected_types = [next(r['type'] for r in original if r['name'] == symbol) for symbol in expected_symbols]
        model = context.object_model()
        table = context.sources.local_table
        for step, (index, suffix) in enumerate(edits):
            # Native32-character root limit and the source codepage constrain
            # generated valid identifiers; indices/bounds are not edited here.
            if case['codepage'] != 936:
                suffix = suffix.replace('状', 's').replace('态', 't')
            index %= len(names)
            old, new = names[index], 'R' + str(step) + '_' + suffix + '_' + case['actuals'][index]['name']
            expected_symbols = [new + symbol[len(old):] if symbol.startswith((old + '.', old + '['))
                                else symbol for symbol in expected_symbols]
            model = edit_draft(model, dict(action='update_label', table=table, name=old, field='name', value=new),
                               context=context)['model']
            prepared = prepare_object_project(model, baseline=context.raw)
            plan = native_source_plan(prepared)
            assert not plan['create'] and not plan['remove'] and len(plan['updates']) == 1
            if prepared.requires_native_save:
                with pytest.raises(GXWFormatError, match='requires a native workspace save'):
                    generate_object_project(model, baseline=context.raw)
                # Protected independent sources cannot use the product's
                # generic writer. Rehydrate only a Core test snapshot from
                # the prepared source records, without claiming a saved GXW.
                nested = prepared.outer['_hdb']
                for _, (stream, _, payload) in prepared.replacements.items():
                    nested, _ = replace_project_stream(nested, stream, payload)
                saved, _ = replace_project_stream(context.raw, '_hdb', nested)
            else:
                saved = generate_object_project(model, baseline=context.raw).data
            before_outer, after_outer = validate_cfb_streams(context.raw), validate_cfb_streams(saved)
            before_map, after_map = logical_mapping(before_outer['projectdatalist.xml']), logical_mapping(after_outer['projectdatalist.xml'])
            before_data, after_data = validate_cfb_streams(before_outer['_hdb']), validate_cfb_streams(after_outer['_hdb'])
            assert before_map == after_map
            assert before_outer.keys() == after_outer.keys()
            assert all(before_outer[key] == after_outer[key] for key in before_outer if key not in ('_hdb', 'history.xml'))
            assert before_data.keys() == after_data.keys()
            edited_streams = {before_map[context.program.logical_name], before_map[table]}
            assert all(before_data[stream] == after_data[stream] for stream in before_data if stream not in edited_streams)
            assert [r.raw for r in read_project_context(saved, case['program']).program.unknown_records] == [
                r.raw for r in context.program.unknown_records]
            context = read_project_context(saved, case['program'])
            model = context.object_model()
            assert not model['issues']
            assert [n['symbol'] for n in model['nodes']] == expected_symbols
            assert [root_terminal_type(context, node) for node in context.program.nodes] == expected_types
            assert [n['ports'][0]['name'] for n in model['nodes']] == [
                'OUT' if node.kind == NodeKind.INPUT else 'IN' for node in context.program.nodes]
            assert [r.name for r in context.declarations[table].rows if r.name == new] == [new]
            names[index] = new
    exercise()


@pytest.mark.parametrize('patch', [dict(type_code=4), dict(array_marker=1), dict(type_reference='guessed'),
    dict(initial_value='0'), dict(device='D0'), dict(iec_address='%MW0'), dict(unknown_u32=1), dict(class_code=3)])
def test_root_local_member_binding_keeps_observed_storage_and_cache_guards(patch):
    context, _ = root_actual_context()
    table = context.sources.local_table
    document = context.declarations[table]
    changed = replace(document, rows=tuple(replace(row, **patch) if row.name == 'Root_Project_C6' else row for row in document.rows))
    with pytest.raises(GXWFormatError, match='observed local'):
        context.sources.with_declarations({**context.declarations, table:changed}).structure_member('Root_Project_C6.BoolIn[0]')


@pytest.mark.parametrize('data_type', ['WORD', 'ARRAY [0..1] OF INT', 'ARRAY [0..1,0..1] OF BOOL',
                                     'ARRAY [0..1] OF ST_MB_REG_50', 'MissingStructure_C6'])
def test_root_local_native_creation_rejects_shapes_without_current_native_evidence(data_type):
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project
    context, _ = root_actual_context('FX3G-independent', stage='source')
    model = context.object_model()
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [dict(
        name='Unverified_Root_C6', data_type=data_type,
        **({'kind':'structure'} if data_type.endswith('ST_MB_REG_50') or data_type == 'MissingStructure_C6' else {}))]}}
    with pytest.raises(GXWFormatError):
        native_source_plan(prepare_object_project(model, baseline=context.raw))


def test_root_local_native_readback_checks_zero_cache_even_when_the_type_text_matches():
    from src.gxw.declarations import serialize_declarations
    from src.gxw.native_write import verify_native_save
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_writer import replace_project_stream
    from research.freeze_gxw_flat_project_structures import root_actual_prepared
    before, case = root_actual_context(stage='source')
    prepared = root_actual_prepared(before.raw, case['original_native_plan'])
    saved, _ = root_actual_context(stage='created')
    table = saved.sources.local_table
    document = replace(saved.declarations[table], rows=tuple(
        replace(row, type_code=4) if row.name == 'Root_Words_C6' else row for row in saved.declarations[table].rows))
    outer = validate_cfb_streams(saved.raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested, _ = replace_project_stream(outer['_hdb'], mapping[table], serialize_declarations(document))
    corrupt, _ = replace_project_stream(saved.raw, '_hdb', nested)
    with pytest.raises(GXWFormatError, match='root value cache fields'):
        verify_native_save(prepared, corrupt, {})


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'Q04UDV', 'L02'])
def test_root_local_whole_arguments_keep_source_types_and_native_inout_sides(cpu):
    from src.gxw.object_model import read_project_context
    root = Path(__file__).resolve().parents[1]
    module = json.loads((private_path('research/results/gxw-root-actual-20261011.json')).read_text(encoding='utf-8'))
    row = next(r for r in module['CPU_rows'] if r['cpu'] == cpu)
    with zipfile.ZipFile(private_path(module['archive'])) as stored:
        raw = stored.read('cases/' + Path(row['checked_directory']).name + '/input.gxw')
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    model = context.object_model()
    assert not model['issues']
    for symbol in ('Root_Project_C6', 'Root_ProjectArray_C6'):
        value = context.sources.structure_value(symbol)
        binding = context.sources.label(symbol)
        assert value['source_table'] == context.sources.local_table
        assert value['data_type'] == binding[1].data_type
    call, = [node for node in context.program.nodes if node.kind == NodeKind.FUNCTION_BLOCK]
    ports = context.sources.callable(call)['ports']
    for formal in {port['formal_name'] for port in ports}:
        assert [(port['side'], port['class_code']) for port in ports if port['formal_name'] == formal] == [
            ('in', 5), ('out', 5)]
    assert generate_object_project(model, baseline=raw).data == raw


@lru_cache(maxsize=1)
def created_inout_witnesses():
    return json.loads((private_path('tests/fixtures/gxw_created_inout_native.json')).read_text(encoding='utf-8'))


def created_inout_context(cpu='FX3U/FX3UC', *, stage='caller', caller=False):
    from src.gxw.native_diagnostics import native_validation_source_plan
    from src.gxw.object_model import read_project_context
    case = next(row for row in created_inout_witnesses()['cases'] if row['cpu'] == cpu)
    key = {'source': 'source_gxw_zlib_base64', 'formal': 'formal_saved_gxw_zlib_base64',
           'caller': 'caller_gxw_zlib_base64'}[stage]
    raw = zlib.decompress(base64.b64decode(case[key]))
    logical = case['caller_program'] if caller else next(
        row['logical_name'] for row in native_validation_source_plan(raw, include_logical_names=True)['programs']
        if row['name'] == case['original_native_plan']['program_name'])
    return read_project_context(raw, logical), case


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'Q04UDV', 'Q06UDV'])
def test_created_inout_core_plan_and_readback_match_original_native_request(cpu):
    from src.gxw.declarations import BASIC_TYPES, _type_parts
    from src.gxw.native_write import native_source_plan, verify_native_save
    from src.gxw.object_model import prepare_object_project
    context, case = created_inout_context(cpu, stage='source')
    model = context.object_model()
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [
        dict(name=row['name'], data_type=row['data_type'], class_name='VAR_IN_OUT',
             **({'kind': 'structure'} if _type_parts(row['data_type'])[0] not in BASIC_TYPES else {}))
        for row in case['original_native_plan']['create']]}}
    prepared = prepare_object_project(model, baseline=context.raw)
    # The oracle is the request retained before the original typed native call.
    assert native_source_plan(prepared) == case['original_native_plan']
    saved = zlib.decompress(base64.b64decode(case['formal_saved_gxw_zlib_base64']))
    assert verify_native_save(prepared, saved, case['native_formal_save']).report['validation']['declarations'] == 'passed'
    after, _ = created_inout_context(cpu, stage='formal')
    assert [(row.class_code, row.initial_value, row.type_code, row.type_reference,
             row.array_marker, row.unknown_u32, row.record_id)
            for row in after.declarations[after.sources.local_table].rows] == [
        (5, '', 0, '', 0, 0, row['line']) for row in case['original_native_plan']['create']]


@pytest.mark.parametrize('cpu,source', [('Q04UDV', 'OwnedFlatStruct_图形_A1.str'),
    ('Q06UDV', 'AXIS_REF_JEC\\MotionControl_JEC_V100A.lns')])
def test_created_inout_native_readback_reports_unrequested_structure_source_changes(cpu, source):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import BASIC_TYPES, _type_parts
    from src.gxw.native_write import verify_native_save
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.project_writer import replace_project_stream
    context, case = created_inout_context(cpu, stage='source')
    model = context.object_model()
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [
        dict(name=row['name'], data_type=row['data_type'], class_name='VAR_IN_OUT',
             **({'kind': 'structure'} if _type_parts(row['data_type'])[0] not in BASIC_TYPES else {}))
        for row in case['original_native_plan']['create']]}}
    prepared = prepare_object_project(model, baseline=context.raw)
    saved = zlib.decompress(base64.b64decode(case['formal_saved_gxw_zlib_base64']))
    outer = validate_cfb_streams(saved)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    payload = validate_cfb_streams(outer['_hdb'])[mapping[source]]
    hdb, _ = replace_project_stream(outer['_hdb'], mapping[source], payload[:-1] + bytes([payload[-1] ^ 1]))
    corrupt, _ = replace_project_stream(saved, '_hdb', hdb)
    result = verify_native_save(prepared, corrupt, {})
    assert result.data == corrupt
    assert result.report['validation']['unedited_sources'] == 'changed'
    change, = [row for row in result.report['unedited_object_changes'] if row['object'] == source]
    assert change['kind'] == 'source' and change['binary_changes']
    assert result.report['validation']['gxworks_compile'] == 'not_run'


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'Q04UDV', 'Q06UDV'])
def test_new_local_inout_members_and_call_pairs_follow_current_source_and_native_references(cpu):
    from src.gxw.declarations import _type_parts
    context, case = created_inout_context(cpu)
    model = context.object_model()
    assert not model['issues']
    for node, projected in zip(context.program.nodes, model['nodes'], strict=True):
        native = [row for row in case['native_references'] if row['name'] == node.symbol
                  and (row['left'], row['top'], row['right'], row['bottom']) ==
                  (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)]
        port, = projected['ports']
        expected_type = port['data_type'] if '.' in node.symbol else _type_parts(next(
            row['data_type'] for row in case['original_native_plan']['create']
            if row['name'] == node.symbol.split('[', 1)[0]))[0]
        assert native and {row['type'] for row in native} == {expected_type}
        assert {row['attribute'] for row in native} == {1 if node.kind == NodeKind.INPUT else 2}
        if '.' in node.symbol:
            value = context.sources.structure_member(node.symbol)
            assert value['source_table'] == context.sources.local_table
            assert value['data_type'] == port['data_type']
    caller, _ = created_inout_context(cpu, caller=True)
    fb, = [node for node in caller.program.nodes if node.symbol == case['instance_name']]
    ports = caller.sources.callable(fb)['ports']
    assert ports == case['native_ports']
    for formal in case['original_native_plan']['create']:
        pairs = [port for port in ports if port['formal_name'] == formal['name']]
        assert [(p['name'], p['side'], p['data_type'], p['class_code']) for p in pairs] == [
            ('in.' + formal['name'], 'in', formal['data_type'], 5),
            ('out.' + formal['name'], 'out', formal['data_type'], 5)]
    assert case['source_equals_cold_input']
    if cpu == 'Q06UDV':
        assert case['public_check'] == dict(status='incomplete', completed=False)
        assert case['current_source_check'] == 'not_established'
        assert case['current_raw_source_check'] == 'established'
        assert case['raw_backend_check'] == 'completed-rejected'
    else:
        assert case['public_check'] == dict(status='passed', completed=True)
        assert case['current_source_check'] == 'established'


@pytest.mark.parametrize('cpu', ['Q04UDV', 'Q06UDV'])
@pytest.mark.parametrize('changes', [dict(class_code=3), dict(unknown_u32=1),
    dict(type_reference='guessed'), dict(array_marker=1), dict(type_code=4), dict(type_code=15)])
def test_scalar_local_structure_members_reject_unobserved_declaration_layouts(cpu, changes):
    context, case = created_inout_context(cpu)
    table = context.sources.local_table
    target = next(row for row in context.declarations[table].rows if row.data_type in
                  ('OwnedFlatStruct_图形_A1', 'AXIS_REF_JEC'))
    document = replace(context.declarations[table], rows=tuple(
        replace(row, **changes) if row.name == target.name else row for row in context.declarations[table].rows))
    sources = context.sources.with_declarations({**context.declarations, table: document})
    with pytest.raises(GXWFormatError, match='local IN_OUT scope'):
        sources.structure_member(target.name + ('.AxisNo' if cpu == 'Q06UDV' else '.Spare'))


@pytest.mark.parametrize('cpu,data_type,extra', [
    ('FX3U/FX3UC', 'INT', {}), ('FX3U/FX3UC', 'ARRAY[0..1] OF BOOL', {}),
    ('FX3U/FX3UC', 'ARRAY[0..1,0..1] OF WORD', {}),
    ('FX3U/FX3UC', 'MissingType', {'kind': 'structure'}),
    ('Q06UDV', 'ARRAY[0..1] OF AXIS_REF_JEC', {'kind': 'structure'}),
    ('FX3U/FX3UC', 'BOOL', {'initial_value': 'FALSE'}),
])
def test_created_inout_native_plan_rejects_unverified_shapes(cpu, data_type, extra):
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project
    context, _ = created_inout_context(cpu, stage='source')
    model = context.object_model()
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [dict(
        name='Unverified', data_type=data_type, class_name='VAR_IN_OUT', **extra)]}}
    with pytest.raises(GXWFormatError, match='native creation supports'):
        native_source_plan(prepare_object_project(model, baseline=context.raw))


def test_source_bound_editor_creates_a_named_inout_structure_from_the_current_type():
    from src.gxw.editor import edit_draft
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project
    context, _ = created_inout_context('Q06UDV', stage='source')
    model = context.object_model()
    for command in [dict(action='add_label', table=context.sources.local_table),
                    dict(action='update_label', table=context.sources.local_table, name='label_1',
                         field='class_name', value='VAR_IN_OUT'),
                    dict(action='update_label', table=context.sources.local_table, name='label_1',
                         field='data_type', value='AXIS_REF_JEC')]:
        model = edit_draft(model, command, context=context)['model']
    row, = model['declaration_edits'][context.sources.local_table]['upserts']
    assert row['kind'] == 'structure'
    assert native_source_plan(prepare_object_project(model, baseline=context.raw))['create'] == [
        dict(name='label_1', data_type='AXIS_REF_JEC', class_code=5, initial_value='', line=1)]


def test_created_inout_rename_sequences_preserve_native_edges_ports_and_opaque_bytes():
    from hypothesis import example, given, settings, strategies as st
    from src.gxw.connectivity import build_connectivity_graph
    from src.gxw.declarations import LabelRecord, parse_declarations
    from dataclasses import fields
    from src.gxw.editor import edit_draft
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project
    from src.gxw.structured_pou import parse_structured_pou
    contexts = [created_inout_context(cpu, caller=True) for cpu in ('FX3U/FX3UC', 'Q04UDV', 'Q06UDV')]

    def edges(program, fb):
        graph = build_connectivity_graph(program)
        return {index: sorted((node.kind.value, node.symbol, other) for node in program.nodes if node.offset != fb.offset
                for other in range(len(node.ports)) if graph.ports_connected(fb.offset, index, node.offset, other))
                for index in range(len(fb.ports))}

    @settings(max_examples=24, deadline=None, database=None, derandomize=True)
    @example(index=0, names=['中文', 'Long_Name', '中文'])
    @example(index=1, names=['结构实例'])
    @example(index=2, names=['Imported', 'Axis'])
    @given(index=st.integers(0, 2), names=st.lists(
        st.text(alphabet='ABCXYZ_中文变量', min_size=1, max_size=16), min_size=1, max_size=3))
    def check(index, names):
        context, case = contexts[index]
        model = context.object_model()
        table = context.sources.local_table
        old_name = case['instance_name']
        original_fb, = [node for node in context.program.nodes if node.symbol == old_name]
        expected_edges = edges(context.program, original_fb)
        original_row, = [row for row in context.declarations[table].rows if row.name == old_name]
        for value in names:
            name = 'Instance_' + value
            model = edit_draft(model, dict(action='update_label', table=table, name=old_name,
                field='name', value=name), context=context)['model']
            old_name = name
        prepared = prepare_object_project(model, baseline=context.raw)
        assert [row['after']['name'] for row in native_source_plan(prepared)['updates']] == [old_name]
        after = parse_structured_pou(prepared.replacements[context.program.logical_name][2],
            logical_name=context.program.logical_name, preserve_unsupported_records=True)
        fb, = [node for node in after.nodes if node.symbol == old_name]
        assert edges(after, fb) == expected_edges
        document = parse_declarations(prepared.replacements[table][2], logical_name=table)
        sources = context.sources.with_declarations({**context.declarations, table: document})
        assert sources.callable(fb)['ports'] == case['native_ports']
        row, = [row for row in document.rows if row.name == old_name]
        assert all(getattr(row, field.name) == getattr(original_row, field.name)
                   for field in fields(LabelRecord) if field.name not in ('name', 'offset', 'raw'))
        assert [node.raw for node in after.nodes if node.kind == NodeKind.UNKNOWN] == [
            node.raw for node in context.program.nodes if node.kind == NodeKind.UNKNOWN]
        assert [record.raw for record in after.unknown_records] == [record.raw for record in context.program.unknown_records]
        assert set(prepared.replacements) == {table, context.program.logical_name}
        assert all(row.raw == original_row.raw for row, original_row in zip(document.rows, context.declarations[table].rows)
                   if row.name != old_name)
    check()


@pytest.mark.parametrize('fixture_name', ['gxw_program_task_native.json', 'gxw_engineering_scope_native.json'])
def test_native_program_local_rename_sequences_preserve_task_and_other_sources(fixture_name):
    from dataclasses import fields
    from hypothesis import example, given, settings, strategies as st
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import parse_declarations
    from src.gxw.editor import edit_draft
    from src.gxw.native_write import native_source_plan
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.structured_pou import parse_structured_pou

    witness = json.loads((private_path('tests/fixtures') / fixture_name).read_text(encoding='utf-8'))
    contexts = [(read_project_context(zlib.decompress(base64.b64decode(case['gxw_zlib_base64'])),
        case['program']), case) for case in witness['cases']]
    assert [context.sources.cpu for context, _ in contexts] == ['FX3U/FX3UC', 'L02', 'Q06UDV']

    @settings(max_examples=18, deadline=None, database=None, derandomize=True)
    @example(index=0, names=['局部', 'LONG_NAME', '局部'])
    @example(index=1, names=['Flag', 'Flag'])
    @example(index=2, names=['A', '局部'])
    @given(index=st.integers(0, 2), names=st.lists(
        st.text(alphabet='ABCxyz0123_局部', min_size=1, max_size=20), min_size=1, max_size=3))
    def check(index, names):
        context, case = contexts[index]
        table = case['local_table']
        document = context.declarations[table]
        original_row, = document.rows
        binding = case['binding']
        # These owners were read by the original typed native interfaces.
        assert binding['pou_type'] == document.owner_pou_type == 0x01000000
        assert binding['element']['read_type']['data_type'] == 12
        assert binding['element']['read_name']['name'] == 'OwnedProgram_C2'
        if 'resource' in case:
            assert binding['resource']['read_name']['name'] == case['resource']
            assert binding['task']['read_name']['name'] == case['task']
        assert (original_row.name, original_row.data_type, original_row.class_code) == (case['flag'], 'BOOL', 1)
        original = context.object_model()
        model, name = original, original_row.name
        for suffix in names:
            updated = 'Task_' + suffix
            model = edit_draft(model, dict(action='update_label', table=table, name=name,
                field='name', value=updated), context=context)['model']
            name = updated
        prepared = prepare_object_project(model, baseline=context.raw)
        plan = native_source_plan(prepared)
        assert plan['program_name'] == 'OwnedProgram_C2'
        if 'resource' in case:
            assert case['resource'] + '.res' in prepared.mapping
            assert case['task'] + '.tsk' in prepared.mapping
        assert plan['create'] == plan['remove'] == []
        update, = plan['updates']
        assert update['before']['name'] == original_row.name and update['after']['name'] == name
        edited_document = parse_declarations(prepared.replacements[table][2], logical_name=table)
        row, = edited_document.rows
        assert edited_document.owner_pou_type == 0x01000000 and row.name == name
        assert all(getattr(row, field.name) == getattr(original_row, field.name)
            for field in fields(type(row)) if field.name not in ('name', 'raw', 'offset'))
        graph = parse_structured_pou(prepared.replacements[case['program']][2],
            logical_name=case['program'], preserve_unsupported_records=True)
        assert [node.symbol for node in graph.nodes] == ['M45', name, name, 'M46']
        assert [wire.raw for wire in graph.wires] == [wire.raw for wire in context.program.wires]
        assert [record.raw for record in graph.unknown_records] == [
            record.raw for record in context.program.unknown_records]
        baseline_streams = validate_cfb_streams(validate_cfb_streams(context.raw)['_hdb'])
        edited = {prepared.mapping[table], prepared.mapping[case['program']]}
        preserved = {key: value for key, value in baseline_streams.items() if key not in edited}
        assert preserved == {key: value for key, value in prepared.nested.items() if key not in edited}
        # Core packaging must leave the native task registration and foreign POUs exact.
        packed = generate_object_project(model, baseline=context.raw).data
        reopened = read_project_context(packed, case['program'])
        assert reopened.declarations[table].owner_pou_type == 0x01000000
        assert [row.name for row in reopened.declarations[table].rows] == [name]
        assert [node.symbol for node in reopened.program.nodes] == ['M45', name, name, 'M46']
        actual = validate_cfb_streams(validate_cfb_streams(packed)['_hdb'])
        assert preserved == {key: value for key, value in actual.items() if key not in edited}
        assert not any(item['template'] == 'function_block:OwnedProgram_C2' for item in reopened.catalog())
        assert context.object_model() == original
    check()


def source_binding_witness(index=0):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    from src.gxw.structured_pou import parse_structured_pou
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fbd-source-binding-20261001.zip')) as archive:
        case = json.loads(archive.read('witnesses.json'))['interfaces'][index]
    declarations = {name: parse_declarations(base64.b64decode(value), logical_name=name)
                    for name, value in case['declarations'].items()}
    libraries = {}
    for definition in case['definitions']:
        sections = definition['sections']
        text = (('(*$SECTION:' + ','.join(sections) + '*)\n').encode() if sections else b'')
        text += base64.b64decode(definition['declaration_prefix_base64'])
        text += ('END_' + definition['kind'] + '\n').encode()
        name = definition['source_stream']
        libraries[name] = libraries.get(name, b'') + text
    sources = ProjectCallableSources(case['cpu'], case['logical'], declarations, libraries)
    program = parse_structured_pou(base64.b64decode(case['program_base64']), logical_name=case['logical'],
                                   preserve_unsupported_records=True)
    return program, declarations, sources, case


def source_binding_baseline():
    """Owned graph/declarations plus a minimal declaration-only source library."""
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import serialize_declarations
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    from src.gxw.project_writer import replace_project_stream
    from src.gxw.structured_pou_writer import serialize_structured_pou
    program, declarations, _, _ = source_binding_witness()
    raw = default_baseline()
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    fixture = json.loads((private_path('tests/fixtures/gxw_fbd_source_library.json')).read_text())
    changes = {program.logical_name: serialize_structured_pou(program),
               **{name: serialize_declarations(doc) for name, doc in declarations.items()},
               'IECFunction.lif': inner[mapping['IECFunction.lif']][:20] + base64.b64decode(fixture['archive_base64'])}
    hdb = outer['_hdb']
    for name, data in changes.items():
        hdb, _ = replace_project_stream(hdb, mapping[name], data)
    history, _, _ = synchronize_history(outer['history.xml'],
        {name: (mapping[name], inner[mapping[name]], data) for name, data in changes.items()})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return raw


def project_rows_variant(raw, edits):
    """Change only named current directory fields, retaining payload/history bytes."""
    from xml.sax.saxutils import escape
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.project_metadata import current_rows
    outer = validate_cfb_streams(raw)
    xml = outer['projectdatalist.xml']
    rows, encoding = current_rows(xml, 'DSPROJECTDATA', 'D_Projectdata')
    replacements = []
    for row in rows:
        fields = row.fields()
        name = fields['szName'].text.strip()
        for key, value in edits.get(name, {}).items():
            field = fields[key]
            replacements.append((field.content_start, field.content_end, escape(value).encode(encoding)))
    for start, end, value in sorted(replacements, reverse=True):
        xml = xml[:start] + value + xml[end:]
    return replace_project_stream(raw, 'projectdatalist.xml', xml)[0]


def user_library_binding_baseline(*, damage=None):
    """Generated metadata boundary control, without native acceptance claims."""
    import struct
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import _string, edit_declarations, parse_declarations, serialize_declarations
    from src.gxw.project_metadata import current_rows, logical_mapping
    from src.gxw.source_header import source_payload_offset

    raw = default_baseline()
    program, documents, _ = read_project(raw)
    base = documents['1.Labels.lh']
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    slots = [name for name in mapping if name.endswith('.lif')] + ['Global1.gh']
    rows, encoding = current_rows(outer['projectdatalist.xml'], 'DSPROJECTDATA', 'D_Projectdata')
    changes, replacements = {}, []
    for index, library in enumerate(('Library_A', 'Library_B')):
        name = 'FIRST' if index == 0 or damage == 'duplicate_name' else 'SECOND'
        logical = name + '.Labels\\' + library + '.lnl'
        embedded = 'OTHER' if index == 1 and damage == 'embedded_owner' else name
        header = (base.header[:source_payload_offset(base.raw)] + _string(embedded)
            + struct.pack('<II', 0x1000003, 1) + _string('') + _string('BOOL') + struct.pack('<I', 0))
        document = parse_declarations(header + base.trailer, logical_name=logical)
        document = edit_declarations(document, upserts=[
            {'name': 'VALUE', 'data_type': 'DWORD', 'class_name': 'VAR_INPUT'},
            {'name': 'STATE', 'data_type': 'WORD', 'class_name': 'VAR_IN_OUT'}])
        declaration = serialize_declarations(document)
        if index == 1 and damage == 'unrecognized_kind':
            kind_offset = source_payload_offset(declaration) + len(_string(embedded))
            declaration = declaration[:kind_offset] + struct.pack('<I', 0x1000001) + declaration[kind_offset+4:]
        # Source names deliberately differ: metadata grouping binds them.
        source = 'DifferentBody.Program\\' + library + ('.lbo' if index == 1 and damage == 'missing_source' else '.lnb')
        for original, target, value, role in (
                (slots[2*index], logical, declaration, '1'),
                (slots[2*index+1], source, program.raw, '2')):
            changes[mapping[original]] = value
            row, = [row for row in rows if row.fields()['szName'].text.strip() == original]
            fields = row.fields()
            updates = {'szName': target, 'ucProductType': '1', 'ucFolderType': '92',
                       'uiFolderNo': '1', 'ucReserve': '0', 'ucFileType': role}
            for key, text in updates.items():
                field = fields[key]
                replacements.append((field.content_start, field.content_end, text.encode(encoding)))
    xml = outer['projectdatalist.xml']
    for start, end, replacement in sorted(replacements, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    hdb = outer['_hdb']
    for physical, value in changes.items():
        hdb, _ = replace_project_stream(hdb, physical, value)
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'projectdatalist.xml', xml)
    return raw


def test_user_library_interfaces_bind_namespaces_and_rebuild_pending_formals():
    from src.gxw.object_model import read_project_context
    from src.gxw.declarations import edit_declarations
    from src.gxw.callable_sources import ProjectCallableSources
    raw = user_library_binding_baseline()
    context = read_project_context(raw)
    labels = 'FIRST.Labels\\Library_A.lnl'
    other = 'SECOND.Labels\\Library_B.lnl'
    for name, stream in (('FIRST', labels), ('SECOND', other)):
        formals = context.sources.fixed_interface(name, 'FUNCTION')
        assert formals['source_stream'] == stream
        assert [(row['name'], row['class_code'], row['declared_type']) for row in formals['inputs']] == [
            ('VALUE', 3, 'DWORD'), ('STATE', 5, 'WORD')]
        assert [(row['name'], row['class_code'], row['declared_type']) for row in formals['outputs']] == [
            (name, 4, 'BOOL'), ('STATE', 5, 'WORD')]
    pending = context.draft_sources({labels: {'renames': {'VALUE': 'VALUE_LONG'}}})
    assert [row['name'] for row in pending.fixed_interface('FIRST', 'FUNCTION')['inputs']] == ['VALUE_LONG', 'STATE']
    assert [row['name'] for row in context.sources.fixed_interface('FIRST', 'FUNCTION')['inputs']] == ['VALUE', 'STATE']
    assert [row['name'] for row in pending.fixed_interface('SECOND', 'FUNCTION')['inputs']] == ['VALUE', 'STATE']
    changed = edit_declarations(context.declarations[labels], remove=['STATE'])
    rebuilt = ProjectCallableSources.from_project(raw, context.program.logical_name,
        {**context.declarations, labels: changed})
    assert [row['name'] for row in rebuilt.fixed_interface('FIRST', 'FUNCTION')['outputs']] == ['FIRST']


@pytest.mark.parametrize('damage', ['duplicate_name', 'missing_source', 'embedded_owner', 'unrecognized_kind'])
def test_user_library_ambiguity_and_unobserved_kinds_do_not_supply_a_function_interface(damage):
    from src.gxw.object_model import read_project_context
    context = read_project_context(user_library_binding_baseline(damage=damage))
    name = 'FIRST' if damage == 'duplicate_name' else 'SECOND'
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        context.sources.fixed_interface(name, 'FUNCTION')


def user_library_structure_baseline(*, namespace='Library_A', damage=None, collision=False):
    """Synthetic registered owners; the fixture does not certify native CPUs."""
    import struct
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import _string, parse_declarations, serialize_declarations
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.source_header import source_payload_offset
    raw = user_library_binding_baseline()
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    logical = 'FIRST.Labels\\Library_A.lnl'
    document = parse_declarations(inner[mapping[logical]], logical_name=logical)
    document = replace(document, rows=tuple(replace(row, data_type='AXIS_STATE', type_code=0)
        if row.name == 'STATE' else row for row in document.rows))
    changes = {mapping[logical]: serialize_declarations(document)}
    member = b''.join((_string('AxisNo'), _string('INT'), _string('0'), _string(''),
        struct.pack('<I', 1), _string(''), struct.pack('<II', 0, 2), _string('')))
    source = document.raw[:source_payload_offset(document.raw)] + struct.pack('<I', 1) + member + b'opaque'
    if damage == 'invalid_source':
        source = source[:source_payload_offset(source)] + struct.pack('<I', 0xffffffff)
    metadata = {}
    assignments = [('COMMENT.qcd', 'Library_A.lif', b'owned descriptor', '80'),
        ('SamplingTrace.qtd', 'AXIS_STATE\\' + namespace + '.lns', source, '90')]
    if namespace == 'Library_B' or collision:
        assignments.append(('MAIN.qdv', 'Library_B.lif', b'other descriptor', '80'))
    if collision:
        assignments.append(('AUTOEXEC.wbt', 'AXIS_STATE\\Library_B.lns', source, '90'))
    for original, name, value, folder in assignments:
        changes[mapping[original]] = value
        metadata[original] = {'szName': name, 'ucProductType': '1', 'ucFolderType': folder,
            'ucReserve': '0', 'ucFileType': '0'}
    if damage == 'missing_descriptor':
        metadata['COMMENT.qcd']['ucFolderType'] = '81'
    if damage == 'wrong_owner':
        metadata['SamplingTrace.qtd']['ucFolderType'] = '91'
    if damage == 'scrap':
        metadata['SamplingTrace.qtd']['bScrapFlag'] = 'true'
    if damage == 'project_collision':
        metadata['SystemLabel.sld'] = {'szName': 'AXIS_STATE.str'}
    hdb = outer['_hdb']
    for physical, value in changes.items():
        hdb, _ = replace_project_stream(hdb, physical, value)
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    return project_rows_variant(raw, metadata)


def test_library_compound_formals_keep_distinct_inout_sides_without_a_primitive_mask():
    from src.gxw.object_model import read_project_context, native_catalog
    context = read_project_context(user_library_structure_baseline())
    formals = context.sources.fixed_interface('FIRST', 'FUNCTION')
    assert [(row['name'], row['declared_type'], row['type_code']) for row in formals['inputs']] == [
        ('VALUE', 'DWORD', 0x40000), ('STATE', 'AXIS_STATE', None)]
    node = replace(native_catalog()['function:MOV'], symbol='FIRST')
    ports = context.sources.callable(node)['ports']
    assert [(port['name'], port['side'], port['data_type'], port['class_code']) for port in ports] == [
        ('VALUE', 'in', 'DWORD', 3), ('in.STATE', 'in', 'AXIS_STATE', 5),
        ('FIRST', 'out', 'BOOL', 4), ('out.STATE', 'out', 'AXIS_STATE', 5)]
    pending = context.draft_sources({'FIRST.Labels\\Library_A.lnl': {'renames': {'STATE': 'STATE_LONG'}}})
    assert [port['name'] for port in pending.callable(node)['ports']] == [
        'VALUE', 'in.STATE_LONG', 'FIRST', 'out.STATE_LONG']
    assert [port['name'] for port in context.sources.callable(node)['ports']] == [
        'VALUE', 'in.STATE', 'FIRST', 'out.STATE']


@pytest.mark.parametrize('damage', ['missing_descriptor', 'wrong_owner', 'invalid_source', 'scrap'])
def test_unbound_library_structures_cannot_supply_formal_types(damage):
    from src.gxw.object_model import read_project_context
    context = read_project_context(user_library_structure_baseline(damage=damage))
    with pytest.raises(GXWFormatError, match='formal type outside'):
        context.sources.fixed_interface('FIRST', 'FUNCTION')
    # Primitive interfaces in another namespace remain readable.
    assert context.sources.fixed_interface('SECOND', 'FUNCTION')['inputs'][1]['type_code'] == 0x20000


@pytest.mark.parametrize('namespace,collision,damage,project_resolves', [
    ('Library_A', False, None, True), ('Library_B', False, None, True),
    ('Library_A', True, None, False), ('Library_A', False, 'project_collision', False)])
def test_compound_types_follow_definition_namespace_and_unique_project_imports(namespace, collision, damage, project_resolves):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.object_model import read_project_context
    context = read_project_context(user_library_structure_baseline(namespace=namespace, collision=collision, damage=damage))
    if namespace == 'Library_A':
        assert context.sources.fixed_interface('FIRST', 'FUNCTION')['inputs'][1]['type_code'] is None
    else:
        with pytest.raises(GXWFormatError, match='formal type outside'):
            context.sources.fixed_interface('FIRST', 'FUNCTION')
    document = context.declarations['FIRST.Labels\\Library_A.lnl']
    project = ProjectCallableSources(context.sources.cpu, '1.Program.pou', {'FIRST.Labels.lh': document}, {},
        library_structures=context.sources.library_structures,
        project_structure_names=context.sources.project_structure_names)
    if project_resolves:
        assert project.fixed_interface('FIRST', 'FUNCTION')['inputs'][1]['type_code'] is None
    else:
        with pytest.raises(GXWFormatError, match='formal type outside'):
            project.fixed_interface('FIRST', 'FUNCTION')


def test_compound_inout_binding_property_retains_geometry_and_source_indexes():
    from hypothesis import given, settings, strategies as st
    from src.gxw.object_model import read_project_context, native_catalog
    context = read_project_context(user_library_structure_baseline())
    node = replace(native_catalog()['function:MOV'], symbol='FIRST')
    # Expected order comes from the independent declaration classes, paired
    # IN_OUT expansion and the saved MOV side/ordinate witness.
    expected = [('VALUE', 'in', 'DWORD', 3), ('in.STATE', 'in', 'AXIS_STATE', 5),
                ('FIRST', 'out', 'BOOL', 4), ('out.STATE', 'out', 'AXIS_STATE', 5)]
    @settings(max_examples=24, deadline=None, derandomize=True, database=None)
    @given(order=st.permutations(tuple(range(4))))
    def check(order):
        shuffled = replace(node, ports=tuple(node.ports[index] for index in order))
        ports = context.sources.callable(shuffled)['ports']
        for index, original in enumerate(order):
            port = ports[index]
            assert (port['name'], port['side'], port['data_type'], port['class_code']) == expected[original]
            assert (port['x'], port['y']) == (node.ports[original].local_x, node.ports[original].local_y)
        assert [port['name'] for port in context.sources.callable(node)['ports']] == [item[0] for item in expected]
    check()


def flat_project_structure_witnesses():
    return json.loads((private_path('tests/fixtures/gxw_flat_project_structures_native.json')).read_text(encoding='utf-8'))


def flat_project_structure_baseline(cpu='FX0N'):
    import zlib
    case = next(case for case in flat_project_structure_witnesses()['cases'] if case['cpu'] == cpu)
    return zlib.decompress(base64.b64decode(case['project_zlib_base64']))


@pytest.mark.parametrize('cpu', ['FX0N', 'Q04UDV'])
def test_current_project_structures_supply_member_types_and_compound_formal_identity(cpu):
    from src.gxw.object_model import read_project_context
    context = read_project_context(flat_project_structure_baseline(cpu), 'FBD_MATRIX.Program.pou')
    case = next(case for case in flat_project_structure_witnesses()['cases'] if case['cpu'] == cpu)
    type_name = case['native_type']['read_name']['name']
    assert context.sources.structure(type_name).logical_name == case['structure']['logical']
    model = context.object_model()
    assert model['schema_version'] == 2 and model['cpu'] == cpu and not model['issues']
    native_types = {row['name']: row['type'] for row in case['native_references']}
    terminals = [node for node in model['nodes'] if node['symbol'] in native_types]
    assert len(terminals) == 5
    assert all(node['ports'][0]['data_type'] == native_types[node['symbol']] for node in terminals)
    for node in [node for node in model['nodes'] if node['template'] == 'function_block:MODULE_FB']:
        ports = {port['name']: port for port in node['ports']}
        assert (ports['in.STATE']['side'], ports['out.STATE']['side']) == ('in', 'out')
        assert ports['in.STATE']['data_type'] == ports['out.STATE']['data_type'] == 'WORD'
    # This draft checks source type identity, without asserting native acceptance
    # of a new compound interface or assigning it a primitive mask.
    document = context.declarations['MODULE_FB.Labels.lh']
    draft = replace(document, rows=tuple(replace(row, data_type=type_name) if row.name == 'STATE' else row
                                        for row in document.rows))
    sources = context.sources.with_declarations({**context.declarations, document.logical_name: draft})
    inputs = sources.fixed_interface('MODULE_FB')['inputs']
    assert inputs[-1]['declared_type'] == type_name and inputs[-1]['type_code'] is None
    assert context.sources.fixed_interface('MODULE_FB')['inputs'][-1]['declared_type'] == 'WORD'


@pytest.mark.parametrize('edits', [
    {'ucProductType': '2'}, {'ucFolderType': '90'}, {'uiFolderNo': '1'},
    {'ucReserve': '1'}, {'ucFileType': '0'}, {'bStructuredStorage': 'false'},
    {'bScrapFlag': 'true'},
])
def test_foreign_project_type_roles_cannot_bind_current_members(edits):
    from src.gxw.object_model import read_project_context
    case = next(case for case in flat_project_structure_witnesses()['cases'] if case['cpu'] == 'FX0N')
    raw = project_rows_variant(flat_project_structure_baseline(), {case['structure']['logical']: edits})
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    model = context.object_model()
    assert all('data_type' not in node['ports'][0] for node in model['nodes']
               if node['symbol'].startswith('OwnedFlat_'))
    if 'bScrapFlag' in edits:
        assert context.sources.structure_member('OwnedFlat_A.BoolIn[1]') is None
    else:
        assert any(issue['code'] == 'project_structure_gap' for issue in model['issues'])
    # Unrelated primitive FB formals remain available.
    assert context.sources.fixed_interface('MODULE_FB')['inputs'][-1]['declared_type'] == 'WORD'


def test_project_structure_owner_collision_blocks_import_fallback_and_member_types():
    from src.gxw.declarations import parse_structure_declarations
    from src.gxw.object_model import read_project_context
    case = next(case for case in flat_project_structure_witnesses()['cases'] if case['cpu'] == 'FX0N')
    logical = case['structure']['logical']
    fields = {key: value for key, value in case['structure']['owner'].items()
              if key in ('ucProductType', 'ucFolderType', 'uiFolderNo', 'ucReserve', 'ucFileType', 'bStructuredStorage')}
    raw = project_rows_variant(flat_project_structure_baseline(),
                              {'SamplingTrace.qtd': {**fields, 'szName': logical.removesuffix('.str').upper() + '.str'}})
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    # A valid same-named import must not repair a broken/duplicate project owner.
    imported = parse_structure_declarations(base64.b64decode(case['structure']['raw_base64']), logical_name='Other\\Library.lns')
    context.sources.library_structures[('library', logical.removesuffix('.str').casefold())] = [imported]
    with pytest.raises(GXWFormatError, match='ambiguous current structure source'):
        context.sources.structure(logical.removesuffix('.str'))
    assert any(issue['code'] == 'project_structure_gap' for issue in context.object_model()['issues'])
    assert all('data_type' not in node['ports'][0] for node in context.object_model()['nodes']
               if node['symbol'].startswith('OwnedFlat_'))


def test_structure_terminal_edit_rebuilds_types_without_changing_type_or_global_bytes():
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping
    context = read_project_context(flat_project_structure_baseline(), 'FBD_MATRIX.Program.pou')
    model = context.object_model()
    for node in model['nodes']:
        if node['symbol'] == 'OwnedFlat_A.BoolIn[1]':
            node['symbol'] = 'OwnedFlat_A.BoolIn[0]'
    result = generate_object_project(model, baseline=context.raw)
    rebuilt = read_project_context(result.data, model['program'])
    terminal = next(node for node in rebuilt.object_model()['nodes'] if node['symbol'] == 'OwnedFlat_A.BoolIn[0]')
    assert terminal['ports'][0]['data_type'] == 'BOOL' and not rebuilt.object_model()['issues']
    assert 'OwnedFlat_A.BoolIn[0]' in rebuilt.svg()
    left, right = validate_cfb_streams(context.raw), validate_cfb_streams(result.data)
    old_mapping, new_mapping = logical_mapping(left['projectdatalist.xml']), logical_mapping(right['projectdatalist.xml'])
    old_payloads, new_payloads = validate_cfb_streams(left['_hdb']), validate_cfb_streams(right['_hdb'])
    for name in old_mapping:
        if name.endswith(('.str', '.gh')) or name.startswith('MODULE_FB.'):
            assert old_payloads[old_mapping[name]] == new_payloads[new_mapping[name]]
    assert context.sources.structure_member('OwnedFlat_A.BoolIn[1]')['constant_index'] == 1
    assert rebuilt.sources.structure_member('OwnedFlat_A.BoolIn[0]')['constant_index'] == 0


def test_native_zero_cache_array_counterexample_remains_untyped_with_source_gaps():
    from src.gxw.native_write import workspace_body
    from src.gxw.object_model import read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.structured_pou_writer import replace_node_symbol, serialize_structured_pou
    witness = flat_project_structure_witnesses()['index_counterexample']
    original = read_project_context(flat_project_structure_baseline(), 'FBD_MATRIX.Program.pou')
    program = original.program
    for node in program.nodes:
        if node.symbol in ('OwnedFlat_A.BoolIn[1]', 'OwnedFlat_A.WordsIn[1]'):
            symbol = node.symbol.replace('[1]', '[127]' if '.BoolIn' in node.symbol else '[64]')
            program = replace_node_symbol(program, node.symbol, symbol, node_offset=node.offset)
    assert workspace_body(serialize_structured_pou(program)) == workspace_body(base64.b64decode(witness['program_base64']))
    raw = build_gxw_project(original.raw, program).data
    context = read_project_context(raw, program.logical_name)
    model = context.object_model()
    gaps = [issue for issue in model['issues'] if issue['code'] == 'structure_source_gap']
    native_errors = [row for row in witness['native_reports'] if row['code'] == 0x500c1031]
    assert len(gaps) == len(native_errors) == 3
    for node in model['nodes']:
        if node['source_offset'] in {gap['node_offset'] for gap in gaps}:
            assert 'data_type' not in node['ports'][0]
    assert witness['compile'] == 'completed_rejected' and witness['check'] == 'not_requested_compile_rejected'
    assert any(row['code'] == 0x50010000 for row in witness['native_reports'])


@pytest.mark.parametrize('change', ['bounds', 'cache', 'truncated'])
def test_rebuilt_project_structure_context_uses_current_source_and_preserves_old_snapshot(change):
    import struct
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import _string
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping
    original = read_project_context(flat_project_structure_baseline(), 'FBD_MATRIX.Program.pou')
    symbol = 'OwnedFlat_A.BoolIn[1]'
    binding = original.sources.structure_member(symbol)
    document = original.sources.structure(original.sources.label('OwnedFlat_A')[1].data_type)
    member = next(member for member in document.members if member.name == 'BoolIn')
    raw = document.raw
    if change == 'bounds':
        start = member.offset + len(_string(member.name))
        raw = raw[:start] + _string('ARRAY[0..0] OF BOOL') + raw[start+len(_string(member.data_type)):]
    elif change == 'cache':
        start = member.offset + len(member.raw) - len(_string(member.type_reference)) - 8
        raw = raw[:start] + struct.pack('<II', 7, 99) + raw[start+8:]
    else:
        raw = raw[:member.offset+1]
    outer = validate_cfb_streams(original.raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    hdb, _ = replace_project_stream(outer['_hdb'], mapping[document.logical_name], raw)
    changed, _ = replace_project_stream(original.raw, '_hdb', hdb)
    rebuilt = read_project_context(changed, original.program.logical_name)
    assert any(issue['code'] == 'structure_source_gap' for issue in rebuilt.object_model()['issues'])
    terminal = next(node for node in rebuilt.object_model()['nodes'] if node['symbol'] == symbol)
    assert 'data_type' not in terminal['ports'][0]
    assert original.sources.structure_member(symbol) == binding
    assert original.sources.structure(document.logical_name.removesuffix('.str')).raw == document.raw


@pytest.mark.parametrize('symbol', [
    'OwnedFlat_A.Spare[0]', 'OwnedFlat_A.BoolIn', 'OwnedFlat_A.BoolIn[index]',
    'OwnedFlat_A.BoolIn[0,1]', 'OwnedFlat_A.Nested.BoolIn[0]', 'OwnedFlat_A.Absent',
])
def test_unobserved_current_project_member_forms_remain_read_gaps(symbol):
    from src.gxw.object_model import read_project_context
    context = read_project_context(flat_project_structure_baseline(), 'FBD_MATRIX.Program.pou')
    with pytest.raises(GXWFormatError):
        context.sources.structure_member(symbol)
    assert context.sources.structure_member('UNDECLARED_ROOT.BoolIn[0]') is None


def test_current_flat_member_binding_property_uses_native_oracles_across_order_and_indices():
    import re
    from hypothesis import given, settings, strategies as st
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations, parse_structure_declarations
    cases = flat_project_structure_witnesses()['cases']
    @settings(max_examples=100, deadline=None, derandomize=True, database=None)
    @given(which=st.integers(min_value=0, max_value=len(cases)-1),
           order=st.permutations((0, 1, 2, 3, 4)), index=st.integers(min_value=-1, max_value=129),
           member_name=st.sampled_from(('BoolIn', 'WordsIn')))
    def check(which, order, index, member_name):
        case = cases[which]
        source = case['structure']
        original = parse_structure_declarations(base64.b64decode(source['raw_base64']), logical_name=source['logical'])
        permuted = original.header + b''.join(original.members[i].raw for i in order) + original.trailer
        document = parse_structure_declarations(permuted, logical_name=source['logical'])
        declarations = {name: parse_declarations(base64.b64decode(value), logical_name=name)
                        for name, value in case['declarations'].items()}
        sources = ProjectCallableSources(case['cpu'], 'FBD_MATRIX.Program.pou', declarations, {},
                                        project_structures={source['logical'].removesuffix('.str').casefold(): (document,)})
        native = next(row for row in case['native_members'] if row['name'] == member_name)
        # The original getter's explicit initializer count independently bounds
        # these zero-based native arrays; expected types come from native refs.
        count = int(re.fullmatch(r'\[([0-9]+)\([^]]+\)\]', native['initial_value'])[1])
        expected_type = next(row['type'] for row in case['native_references'] if '.' + member_name + '[' in row['name'])
        symbol = 'OwnedFlat_A.' + member_name + '[' + str(index) + ']'
        if 0 <= index < count:
            found = sources.structure_member(symbol)
            assert found['data_type'] == expected_type and found['constant_index'] == index
            member = next(row for row in document.members if row.name == member_name)
            assert found['member_offset'] == member.offset and found['source_stream'] == source['logical']
            assert document.raw[member.offset:member.offset+len(member.raw)] == member.raw
        else:
            with pytest.raises(GXWFormatError, match='array bounds'):
                sources.structure_member(symbol)
    check()


def root_structure_array_cases():
    return flat_project_structure_witnesses()['root_array_controls']['cases']


def root_structure_array_context(name):
    """Core replay of native source streams; native verdicts use original files."""
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    case = next(case for case in root_structure_array_cases() if case['name'] == name)
    raw = flat_project_structure_baseline(case['cpu'])
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    payloads = {**{name: base64.b64decode(value) for name, value in case['declarations'].items()},
                case['structure']['logical']: base64.b64decode(case['structure']['raw_base64']),
                'FBD_MATRIX.Program.pou': base64.b64decode(case['program_base64'])}
    hdb = outer['_hdb']
    for logical, payload in payloads.items():
        hdb, _ = replace_project_stream(hdb, mapping[logical], payload)
    history, _, _ = synchronize_history(outer['history.xml'],
        {name: (mapping[name], inner[mapping[name]], payload) for name, payload in payloads.items()})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return read_project_context(raw, 'FBD_MATRIX.Program.pou'), case


@pytest.mark.parametrize('name', ['array', 'index0', 'bounds3', 'bounds-1', 'word-bits', 'core-edit', 'FX0N'])
def test_native_root_array_sources_supply_v2_types_without_mixing_in_out(name):
    context, case = root_structure_array_context(name)
    model = context.object_model()
    assert model['schema_version'] == 2 and model['cpu'] == case['cpu'] and not model['issues']
    native = case['native_references']
    assert len(native) == 3 and {row['attribute'] for row in native if '.Spare' in row['name']} == {1, 2}
    typed = [node for node in model['nodes'] if node['symbol'] in {row['name'] for row in native}]
    assert len(typed) == 3
    for row in native:
        binding = context.sources.structure_member(row['name'])
        assert binding['data_type'] == row['type']
        assert binding['root_bounds'] == (tuple(case['root_bounds']),)
        assert all(node['ports'][0]['data_type'] == row['type'] for node in typed if node['symbol'] == row['name'])
    blocks = [node for node in model['nodes'] if node['template'] == 'function_block:MODULE_FB']
    assert blocks
    for block in blocks:
        ports = {port['name']: port for port in block['ports']}
        assert (ports['in.STATE']['side'], ports['out.STATE']['side']) == ('in', 'out')
        assert ports['in.STATE']['data_type'] == ports['out.STATE']['data_type'] == 'WORD'
    assert case['compile'] == case['check'] == 'completed_accepted'
    assert case['current_source_check'] == 'established' or case['current_raw_source_check'] == 'established'


def test_native_out_of_range_root_keeps_three_untyped_terminals_at_original_error_positions():
    context, case = root_structure_array_context('index2')
    model = context.object_model()
    gaps = [issue for issue in model['issues'] if issue['code'] == 'structure_source_gap']
    errors = [row for row in case['native_reports'] if row['code'] == 0x500c1031]
    assert len(gaps) == len(errors) == 3
    offsets = {gap['node_offset'] for gap in gaps}
    untyped = [node for node in model['nodes'] if node['source_offset'] in offsets]
    assert len(untyped) == 3 and all('data_type' not in node['ports'][0] for node in untyped)
    positions = {(node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)
                 for node in context.program.nodes if node.offset in offsets}
    assert positions == {(row['left'], row['top'], row['right'], row['bottom']) for row in errors}
    assert case['compile'] == 'completed_rejected' and case['check'] == 'not_requested_compile_rejected'
    assert case['public_check'] == 'not_requested'
    assert case['current_source_check'] == case['current_raw_source_check'] == 'not_established'


@pytest.mark.parametrize('suffix', ['.Spare', '[index].Spare', '[0,1].Spare', '[2].Spare',
                                    '[0].Nested.Spare', '[0].Spare[0]', '[' + '9' * 5000 + '].Spare'])
def test_current_root_structure_array_requires_bounded_literal_root_and_member_forms(suffix):
    context, case = root_structure_array_context('array')
    with pytest.raises(GXWFormatError):
        context.sources.structure_member(case['root_name'] + suffix)
    with pytest.raises(GXWFormatError):
        context.sources.structure_member('OwnedFlat_A[0].Spare')
    assert context.sources.structure_member('AbsentRoot[0].Spare') is None


@pytest.mark.parametrize('changes', [
    {'data_type': 'ARRAY[0..0] OF OwnedFlatStruct_图形_A1'}, {'unknown_u32': 1},
    {'type_code': 1}, {'array_marker': 1}, {'type_reference': 'unknown'}, {'class_code': 9},
])
def test_root_array_binding_rebuild_uses_current_declaration_and_keeps_previous_snapshot(changes):
    context, case = root_structure_array_context('array')
    symbol = next(row['name'] for row in case['native_references'] if '.Spare' in row['name'])
    before = context.sources.structure_member(symbol)
    document = context.declarations[case['root_table']]
    changed = replace(document, rows=tuple(replace(row, **changes) if row.name == case['root_name'] else row for row in document.rows))
    rebuilt = context.sources.with_declarations({**context.declarations, document.logical_name: changed})
    with pytest.raises(GXWFormatError):
        rebuilt.structure_member(symbol)
    assert context.sources.structure_member(symbol) == before
    assert context.declarations[document.logical_name].raw == document.raw


def test_root_structure_array_property_uses_native_types_and_bounds_across_global_order():
    import re
    from hypothesis import given, settings, strategies as st
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations, parse_structure_declarations, serialize_declarations
    cases = [case for case in root_structure_array_cases() if case['compile'] == 'completed_accepted']
    @settings(max_examples=100, deadline=None, derandomize=True, database=None)
    @given(which=st.integers(0, len(cases)-1), order=st.permutations((0, 1, 2, 3)), index=st.integers(-4, 7))
    def check(which, order, index):
        case = cases[which]
        declarations = {name: parse_declarations(base64.b64decode(value), logical_name=name)
                        for name, value in case['declarations'].items()}
        document = declarations[case['root_table']]
        assert len(document.rows) == len(order)
        expected = document.header + b''.join(document.rows[i].raw for i in order) + document.trailer
        reordered = parse_declarations(serialize_declarations(replace(document, rows=tuple(document.rows[i] for i in order))),
                                       logical_name=document.logical_name)
        assert serialize_declarations(reordered) == expected
        declarations[document.logical_name] = reordered
        structure = parse_structure_declarations(base64.b64decode(case['structure']['raw_base64']), logical_name=case['structure']['logical'])
        sources = ProjectCallableSources(case['cpu'], 'FBD_MATRIX.Program.pou', declarations, {},
            project_structures={case['structure']['logical'].removesuffix('.str').casefold(): (structure,)})
        for native in case['native_references']:
            symbol = re.sub(r'\[-?[0-9]+\]', '[' + str(index) + ']', native['name'], count=1)
            if case['root_bounds'][0] <= index <= case['root_bounds'][1]:
                binding = sources.structure_member(symbol)
                assert binding['data_type'] == native['type'] and binding['root_constant_index'] == index
                assert binding['root_bounds'] == (tuple(case['root_bounds']),)
                assert binding['root_offset'] == next(row.offset for row in reordered.rows if row.name == case['root_name'])
            else:
                with pytest.raises(GXWFormatError, match='root index'):
                    sources.structure_member(symbol)
    check()


def compound_structure_context(name, *, local_formal=False):
    """Core replay only; original native verdicts remain linked separately."""
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    section = 'local_formal_fbd_controls' if local_formal else 'compound_formal_controls'
    case = next(row for row in flat_project_structure_witnesses()[section]['cases'] if row['name'] == name)
    raw = flat_project_structure_baseline(case['base_cpu'])
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    payloads = {name: base64.b64decode(value) for name, value in case['source_streams'].items()}
    hdb = outer['_hdb']
    for logical, value in payloads.items():
        hdb, _ = replace_project_stream(hdb, mapping[logical], value)
    history, _, _ = synchronize_history(outer['history.xml'],
        {name: (mapping[name], inner[mapping[name]], value) for name, value in payloads.items()})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return read_project_context(raw, case['selected_program'] if local_formal else 'FBD_MATRIX.Program.pou'), case


def created_fbd_context(name='Q04UDV', *, program=None, before_caller=False, source_streams=None):
    """Replay frozen source records; only the Q04UDV base is a native file."""
    import zlib
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.object_model import read_project_context
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    cases = flat_project_structure_witnesses()['created_fbd_controls']['cases']
    case = next(row for row in cases if row['name'] == name)
    base = next(row for row in cases if 'project_zlib_base64' in row)
    raw = zlib.decompress(base64.b64decode(base['project_zlib_base64']))
    outer = validate_cfb_streams(raw); mapping = logical_mapping(outer['projectdatalist.xml'])
    before = validate_cfb_streams(outer['_hdb'])
    payloads = {logical:base64.b64decode(value) for logical,value in case['source_streams'].items()}
    if before_caller:
        payloads.update({logical:base64.b64decode(value) for logical,value in case['caller_before'].items()})
    if source_streams:
        payloads.update({logical:base64.b64decode(value) for logical,value in source_streams.items()})
    nested = outer['_hdb']
    for logical,value in payloads.items():
        nested, _ = replace_project_stream(nested,mapping[logical],value)
    raw, _ = replace_project_stream(raw,'_hdb',nested)
    if 'history.xml' in outer:
        history,_,_ = synchronize_history(outer['history.xml'],
            {logical:(mapping[logical],before[mapping[logical]],value) for logical,value in payloads.items()},current_mapping=mapping)
        raw, _ = replace_project_stream(raw,'history.xml',history)
    return read_project_context(raw,program or case['selected_program']),case


def source_creation_witness(cpu='FX3U/FX3UC'):
    import zlib
    case = next(row for row in flat_project_structure_witnesses()['source_creation_controls']['cases']
                if row['cpu'] == cpu)
    return (zlib.decompress(base64.b64decode(case['source_gxw_zlib_base64'])),
            zlib.decompress(base64.b64decode(case['created_gxw_zlib_base64'])), case)


def replay_source_records(raw, payloads):
    """Container-only replay for Core tests; it does not reproduce native save state."""
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    before = validate_cfb_streams(outer['_hdb'])
    nested, replacements = outer['_hdb'], {}
    for logical, payload in payloads.items():
        stream = mapping[logical]
        replacements[logical] = (stream, before[stream], payload)
        nested, _ = replace_project_stream(nested, stream, payload)
    raw, _ = replace_project_stream(raw, '_hdb', nested)
    history, _, _ = synchronize_history(outer['history.xml'], replacements, current_mapping=mapping)
    return replace_project_stream(raw, 'history.xml', history)[0]


def source_creation_stage(cpu='FX3U/FX3UC', *, stage='formals', program=None):
    from src.gxw.object_model import read_project_context
    _, raw, case = source_creation_witness(cpu)
    owner = case['created_name']
    raw = project_rows_variant(raw, {case['created_program']: {'szName': owner + '.Program.pou'},
                                    case['created_declarations']: {'szName': owner + '.Labels.lh'}})
    raw = replay_source_records(raw, {name: base64.b64decode(payload)
        for name, payload in case['source_streams'][stage].items()})
    return read_project_context(raw, program or owner + '.Program.pou'), case


@pytest.mark.parametrize('cpu', ['Q04UDV', 'L02', 'FX1N/FX1NC', 'FX3U/FX3UC'])
def test_native_empty_fb_creation_binds_actual_identity_and_preserves_existing_streams(cpu):
    from src.gxw.native_write import native_source_creation_plan, verify_native_source_creation, workspace_body
    from src.gxw.object_model import read_project_context
    before, created, case = source_creation_witness(cpu)
    plan = native_source_creation_plan(before, case['created_name'])
    assert (plan['cpu'], plan['codepage'], plan['pou_type'], plan['program_kind']) == (cpu, 936, 0x1000002, 208)
    result = verify_native_source_creation(before, case['created_name'], created, case['native_creation'])
    context = read_project_context(result.data, result.report['created_program'])
    assert context.sources.local_table == case['created_declarations']
    assert not context.program.nodes and not context.declarations[context.sources.local_table].rows
    assert not context.object_model()['issues']
    assert workspace_body(context.program.raw) == base64.b64decode(case['native_creation']['creation']['body'])
    assert result.report['validation']['unedited_sources'] == 'preserved'
    assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    assert result.report['native_folder_renumbering'] == case['native_folder_renumbering']
    if cpu == 'FX3U/FX3UC':
        assert [row['pou_type'] for row in plan['programs']] == [0x1000000]
        assert case['native_folder_renumbering'] == [dict(program='1', before=2, after=1)]


@pytest.mark.parametrize('stem', ['FX3U-FX3UC', 'L02', 'Q06UDV-motion', 'FX3G-independent'])
def test_native_empty_program_creation_requires_explicit_kind_and_keeps_fb_catalog_separate(stem):
    from src.gxw.native_write import native_source_creation_plan, verify_native_source_creation
    from src.gxw.object_model import read_project_context
    prefix = ('native/native-current-program-task-create-FX3G-v1/' if stem == 'FX3G-independent'
              else 'native/native-current-program-module-' + stem + '-create-v1/')
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-program-task-20261010.zip')) as archive:
        before, created = (archive.read(prefix + stage + '.gxw') for stage in ('input', 'candidate'))
        observation = json.loads(archive.read(prefix + 'observation.json'))
        expected = json.loads(archive.read(prefix + 'request.json'))['source']
    name = expected['program_name']
    assert native_source_creation_plan(before, name, pou_kind='program') == expected
    result = verify_native_source_creation(before, name, created, observation, pou_kind='program')
    context = read_project_context(result.data, result.report['created_program'])
    assert context.declarations[context.sources.local_table].owner_pou_type == 0x01000000
    assert not context.program.nodes
    assert not any(item['template'] == 'function_block:' + name for item in context.catalog())
    assert result.report['pou_kind'] == 'program'
    assert result.report['validation']['unedited_sources'] == 'preserved'
    assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    with pytest.raises(GXWFormatError, match='not bound to the selected new POU'):
        verify_native_source_creation(before, name, created, observation)


@pytest.mark.parametrize('kind', [None, {}, True, 0x01000000, 'function', 'PROGRAM'])
def test_native_fbd_creation_kind_cannot_be_guessed_from_name_or_native_type(kind):
    from src.gxw.native_write import native_source_creation_plan
    before, _, _ = source_creation_witness()
    with pytest.raises(GXWFormatError, match='POU kind'):
        native_source_creation_plan(before, 'NewProgram_C1', pou_kind=kind)


@pytest.mark.parametrize('index', range(4))
def test_task_source_configuration_and_rows_match_original_native_getters(index):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping, read_task_source
    fixture = json.loads((private_path('tests/fixtures/gxw_task_configuration_native.json')).read_text(encoding='utf-8'))
    case = fixture['cases'][index]
    assert case['terminal']['returncode'] == 0 and fixture['native_version'] == '1.635.0.1'
    with zipfile.ZipFile(private_path(case['archive'])) as archive:
        outer = validate_cfb_streams(archive.read(case['candidate']))
        binding = json.loads(archive.read(case['binding']))['creation']
    payloads, mapping = validate_cfb_streams(outer['_hdb']), logical_mapping(outer['projectdatalist.xml'])
    for getter in case['task_getters']:
        name = getter['task']['read_name']['name']
        assert getter['task']['read_type']['data_type'] == 10
        parsed = read_task_source(payloads[mapping[name + '.tsk']])
        for field in ('event_name', 'interval', 'priority'):
            original = getter[field]
            assert original['hresult'] == original['code'] == 0
            assert parsed[field] == original['value']
        task, = [row for row in binding['after_tasks'] if row['task']['read_name']['name'] == name
                 and row['resource']['read_name']['name'] == getter['resource']['read_name']['name']]
        assert [(row['name'], row['line']) for row in parsed['rows']] == [
            (row['element']['read_name']['name'], row['line']) for row in task['programs']]


def test_task_source_variable_descriptions_and_unknown_suffix_keep_native_configuration():
    from hypothesis import given, settings, strategies as st
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping, read_task_source
    from src.gxw.source_header import source_payload_offset
    fixture = json.loads((private_path('tests/fixtures/gxw_task_configuration_native.json')).read_text(encoding='utf-8'))
    case = fixture['cases'][0]
    with zipfile.ZipFile(private_path(case['archive'])) as archive:
        outer = validate_cfb_streams(archive.read(case['candidate']))
    payloads, mapping = validate_cfb_streams(outer['_hdb']), logical_mapping(outer['projectdatalist.xml'])
    raw = payloads[mapping['OwnedTask_局部_C1.tsk']]
    payload = raw[source_payload_offset(raw):]
    expected, = [row for row in case['task_getters'] if row['task']['read_name']['name'] == 'OwnedTask_局部_C1']

    @settings(max_examples=20, deadline=None, database=None, derandomize=True)
    @given(description=st.text(alphabet='ABC_局部', min_size=0, max_size=30), suffix=st.binary(max_size=40))
    def check(description, suffix):
        text = description.encode('utf-16le') + b'\0\0'
        header = raw[:6] + (len(text) // 2).to_bytes(4, 'little') + text + raw[12:source_payload_offset(raw)]
        parsed = read_task_source(header + payload + suffix)
        assert parsed['header'] == header and parsed['suffix'] == suffix
        assert {field: parsed[field] for field in ('event_name', 'interval', 'priority')} == {
            field: expected[field]['value'] for field in ('event_name', 'interval', 'priority')}
        assert [(row['name'], row['line']) for row in parsed['rows']] == [('OwnedProgram_C2', 1)]
    check()


@pytest.mark.parametrize('damage', [None, 'priority', 'event', 'unknown-word', 'old-record', 'suffix', 'new-extra-text'])
def test_native_task_append_cannot_change_configuration_old_records_or_unknown_bytes(damage):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping, read_task_source, verify_task_source_append
    prefix = 'native/native-current-program-module-FX3U-FX3UC-bind-v1/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-program-task-20261010.zip')) as archive:
        blobs = []
        for phase in ('input', 'candidate'):
            outer = validate_cfb_streams(archive.read(prefix + phase + '.gxw'))
            blobs.append(validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])['TASK_MATRIX.tsk']])
        line = json.loads(archive.read(prefix + 'observation.json'))['creation']['line']
    before, after = blobs
    if damage is None:
        assert verify_task_source_append(before, after, program_name='OwnedProgram_C2', line=line)['configuration'] == 'exact'
        return
    parsed = read_task_source(after)
    modified = bytearray(after)
    if damage == 'priority':
        modified[parsed['count_offset'] - 8] ^= 1
    elif damage == 'unknown-word':
        modified[parsed['count_offset'] - 4] ^= 1
    elif damage == 'event':
        modified[len(parsed['header']) + 4] ^= 1
    elif damage == 'old-record':
        modified[parsed['count_offset'] + 8] ^= 1
    elif damage == 'suffix':
        modified += b'unedited-task-extension'
    else:
        modified[-6:] = b'\x02\x00\x00\x00X\x00\x00\x00'
    with pytest.raises(GXWFormatError):
        verify_task_source_append(before, bytes(modified), program_name='OwnedProgram_C2', line=line)


def task_binding_witness(index=0):
    """Original saved task append plus independent original configuration reads."""
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import parse_declarations
    from src.gxw.project_metadata import logical_mapping, project_source_groups
    fixture = json.loads((private_path('tests/fixtures/gxw_task_configuration_native.json')).read_text(encoding='utf-8'))
    case = fixture['cases'][index]
    prefix = case['binding'].removesuffix('observation.json')
    with zipfile.ZipFile(private_path(case['archive'])) as archive:
        before, after = [archive.read(prefix + phase + '.gxw') for phase in ('input', 'candidate')]
        request = json.loads(archive.read(prefix + 'request.json'))['source']
        observation = json.loads(archive.read(case['binding']))
    getters = {(row['resource']['read_name']['name'], row['task']['read_name']['name']): row
               for row in case['task_getters']}
    for phase in ('before_tasks', 'after_tasks'):
        for row in observation['creation'][phase]:
            getter = getters[row['resource']['read_name']['name'], row['task']['read_name']['name']]
            row['configuration'] = {field: getter[field] for field in ('event_name', 'interval', 'priority')}
    outer = validate_cfb_streams(before)
    mapping, payloads = logical_mapping(outer['projectdatalist.xml']), validate_cfb_streams(outer['_hdb'])
    program, = [logical for logical, group in project_source_groups(outer['projectdatalist.xml'])[0].items()
        if parse_declarations(payloads[mapping[group['labels']]], logical_name=group['labels']).owner_name == request['program_name']]
    return before, after, program, request, observation


def scope_creation_witness(index=0, scope_kind='task'):
    fixture = json.loads((private_path('tests/fixtures/gxw_task_configuration_native.json')).read_text(encoding='utf-8'))
    case = fixture['cases'][index]
    stems = ['FX3U-FX3UC', 'L02', 'independent-FX3G', 'independent-Q06UDV-motion']
    stem = stems[index]
    prefix = ('native/native-current-' + ('resource-scope-create-FX3U-FX3UC-v4/' if scope_kind == 'resource'
        else 'task-scope-create-FX3U-FX3UC-new-resource-a2-v4/') if index == 0 else
        'native/native-current-engineering-scope-' + stem + '-' + scope_kind + '-v1/')
    with zipfile.ZipFile(private_path(case['archive'])) as archive:
        before, after = [archive.read(prefix + phase + '.gxw') for phase in ('input', 'candidate')]
        request = json.loads(archive.read(prefix + 'request.json'))['source']
        observation = json.loads(archive.read(prefix + 'observation.json'))
    getters = {(row['resource']['read_name']['name'], row['task']['read_name']['name']): row
               for row in case['task_getters']}
    for phase in ('before_tasks', 'after_tasks'):
        for row in observation['creation'][phase]:
            getter = getters[row['resource']['read_name']['name'], row['task']['read_name']['name']]
            row['configuration'] = {field: getter[field] for field in ('event_name', 'interval', 'priority')}
    return before, after, request, observation


def empty_program_registration_witness(index=0):
    import zlib
    fixture = json.loads((private_path('tests/fixtures/gxw_task_configuration_native.json')).read_text(encoding='utf-8'))
    case = fixture['empty_program_registrations'][index]
    raw, saved = [zlib.decompress(base64.b64decode(case[key + '_gxw_zlib_base64'])) for key in ('input', 'candidate')]
    return raw, saved, case


@pytest.mark.parametrize('scope_mode', ['existing_task', 'new_task', 'new_resource'])
def test_program_creation_plan_uses_current_task_ownership_and_explicit_scope_mode(scope_mode):
    import zlib
    from src.gxw.native_write import native_program_creation_plan, native_task_scope_context
    _, _, case = empty_program_registration_witness()
    phase = {'existing_task': 'program', 'new_task': 'task', 'new_resource': 'resource'}[scope_mode]
    raw = zlib.decompress(base64.b64decode(case['creation_inputs'][phase]))
    name = 'OwnedProgram_Core_C4'
    plan = native_program_creation_plan(raw, name, resource=case['resource'], task=case['task'], scope_mode=scope_mode)
    assert plan['cpu'] == case['cpu'] and plan['source_name'] == name
    assert plan['create_resource'] == (scope_mode == 'new_resource')
    assert plan['create_task'] == (scope_mode != 'existing_task')
    choices = native_task_scope_context(raw)
    assert choices['status'] == 'available'
    if scope_mode == 'existing_task':
        selected = [row for row in choices['tasks'] if row['task'] == case['task']]
        assert len(selected) == 1 and selected[0]['resource'] == case['resource'] and not selected[0]['programs']
    with pytest.raises(GXWFormatError):
        native_program_creation_plan(raw, name, resource='MISSING', task=case['task'], scope_mode='existing_task')


@pytest.mark.parametrize('index', range(4))
def test_empty_program_registration_preserves_original_child_identity_and_bytes(index):
    from src.gxw.native_write import verify_native_program_task_binding
    from src.gxw.object_model import read_project_context
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    before, after, case = empty_program_registration_witness(index)
    result = verify_native_program_task_binding(before, case['program'], after, case['native_creation'],
        resource=case['resource'], task=case['task'])
    assert result.saved_object_name(case['program']) == result.report['program'] == case['saved_program']
    assert len(result.report['registered_object_renames']) == 2
    assert result.report['native_task_configuration'] == 'current'
    assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    outer = [validate_cfb_streams(raw) for raw in (before, after)]
    mappings = [logical_mapping(raw['projectdatalist.xml']) for raw in outer]
    payloads = [validate_cfb_streams(raw['_hdb']) for raw in outer]
    for row in result.report['objects']:
        a, b = mappings[0][row['object']], mappings[1][row['saved_object']]
        assert a == b == row['stream']
        assert payloads[0][a] == payloads[1][b]
    context = read_project_context(after, result.saved_object_name(case['program']))
    assert context.sources.cpu == case['cpu'] and not context.program.nodes
    assert context.declarations[context.sources.local_table].owner_pou_type == 0x01000000


@pytest.mark.parametrize('damage', ['folder', 'file-role', 'stream', 'foreign-child', 'source-bytes', 'declaration-bytes'])
def test_empty_program_registration_rejects_identity_replacement_and_hidden_byte_changes(damage):
    from src.gxw.native_write import verify_native_program_task_binding
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    before, after, case = empty_program_registration_witness()
    program = case['saved_program']
    if damage in ('source-bytes', 'declaration-bytes'):
        name = program if damage == 'source-bytes' else program.removesuffix('.Program.pou') + '.Labels.lh'
        outer = validate_cfb_streams(after)
        mapping = logical_mapping(outer['projectdatalist.xml'])
        changed = bytearray(validate_cfb_streams(outer['_hdb'])[mapping[name]])
        changed[-1] ^= 1
        after = replay_source_records(after, {name: bytes(changed)})
    else:
        fields = {'folder': {'uiFolderNo': '999'}, 'file-role': {'ucFileType': '1'},
            'stream': {'iID': '9999'}, 'foreign-child': {'szName': 'Foreign.Program.pou'}}[damage]
        after = project_rows_variant(after, {program: fields})
    with pytest.raises(GXWFormatError):
        verify_native_program_task_binding(before, case['program'], after, case['native_creation'],
            resource=case['resource'], task=case['task'])


@pytest.mark.parametrize('damage', [False, True])
def test_source_save_reports_registered_task_configuration_changes_without_rejecting_output(damage):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.object_model import read_project_context, prepare_object_project
    from src.gxw.native_write import verify_native_save
    _, raw, case = empty_program_registration_witness()
    context = read_project_context(raw, case['saved_program'])
    prepared = prepare_object_project(context.object_model(), baseline=raw)
    candidate = raw
    if damage:
        name = case['task'] + '.tsk'
        outer = validate_cfb_streams(raw)
        mapping = logical_mapping(outer['projectdatalist.xml'])
        original = validate_cfb_streams(outer['_hdb'])[mapping[name]]
        candidate = replay_source_records(raw, {name: original + b'changed-task-extension'})
    result = verify_native_save(prepared, candidate, {})
    assert result.data == candidate
    assert result.report['validation']['unedited_configuration'] == ('changed' if damage else 'preserved')
    assert result.report['validation']['unedited_sources'] == 'preserved'
    if damage:
        change, = result.report['unedited_object_changes']
        assert change['object'] == name and change['kind'] == 'configuration'
        assert change['binary_changes'] == [dict(old_offset=len(original), new_offset=len(original),
            old_length=0, new_length=22, before_hex='', after_hex=b'changed-task-extension'.hex())]
    else:
        assert result.report['unedited_object_changes'] == []


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('scope_kind', ['resource', 'task'])
def test_native_scope_creation_binds_original_constructor_and_retains_unedited_material(index, scope_kind):
    from src.gxw.native_write import native_scope_creation_plan, verify_native_scope_creation
    before, after, request, observation = scope_creation_witness(index, scope_kind)
    name = request[scope_kind]
    parent = request['resource'] if scope_kind == 'task' else None
    plan = native_scope_creation_plan(before, name, scope_kind=scope_kind, resource=parent)
    assert {key: value for key, value in plan.items() if key not in ('tasks', 'resources')} == request
    result = verify_native_scope_creation(before, name, after, observation, scope_kind=scope_kind, resource=parent)
    assert result.data == after and result.report['native_task_configuration'] == 'current'
    assert result.report['validation']['source_body'] == result.report['validation']['old_task_configuration_and_records'] == 'exact'
    assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    if scope_kind == 'task':
        assert result.report['resource_cache_change']['code_header_and_suffix'] == 'exact'
        assert result.report['resource_cache_change']['execution_order'] == 'unresolved'
    with pytest.raises(GXWFormatError, match='exists|unique'):
        native_scope_creation_plan(after, name, scope_kind=scope_kind, resource=parent)


@pytest.mark.parametrize('damage', ['created-type', 'collection-type', 'old-task', 'missing-source',
    'configuration-getter', 'resource-code', 'resource-suffix', 'resource-cache-name'])
def test_native_scope_creation_rejects_conflicting_original_identity_or_unedited_bytes(damage):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.native_write import verify_native_scope_creation
    from src.gxw.project_metadata import logical_mapping, read_resource_source
    before, after, request, observation = scope_creation_witness(2)
    created = observation['creation']
    if damage == 'created-type':
        created['created']['read_type']['data_type'] = 12
    elif damage == 'collection-type':
        created['collection']['read_type']['data_type'] = 7
    elif damage == 'old-task':
        created['after_tasks'][0]['task']['read_name']['name'] += '_changed'
    elif damage == 'missing-source':
        created['existing_sources'].pop()
    elif damage == 'configuration-getter':
        created['after_tasks'][-1]['configuration']['priority']['hresult'] = -1
    else:
        logical = request['resource'] + '.res'
        outer = validate_cfb_streams(after)
        data = validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])[logical]]
        parsed = read_resource_source(data)
        changed = bytearray(data)
        if damage == 'resource-code':
            changed[parsed['cache_offset'] - 4] ^= 1
        elif damage == 'resource-suffix':
            changed[-1] ^= 1
        else:
            changed[parsed['cache_offset'] + 8] ^= 1
        after = replay_source_records(after, {logical: bytes(changed)})
    with pytest.raises(GXWFormatError):
        verify_native_scope_creation(before, request['task'], after, observation,
            scope_kind='task', resource=request['resource'])


def test_resource_name_cache_boundaries_preserve_opaque_code_and_variable_header():
    import struct
    from hypothesis import given, settings, strategies as st
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping, read_resource_source
    from src.gxw.source_header import source_payload_offset
    before, _, request, _ = scope_creation_witness(2, 'resource')
    outer = validate_cfb_streams(before)
    data = validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])['MAIN.res']]
    header = data[:source_payload_offset(data)]

    @settings(max_examples=25, deadline=None, database=None, derandomize=True)
    @given(first=st.binary(max_size=80), second=st.binary(max_size=80), suffix=st.binary(max_size=40),
        names=st.lists(st.text(alphabet='ABC_局部', min_size=1, max_size=15), max_size=8),
        description=st.text(alphabet='ABC_局部', max_size=20))
    def check(first, second, suffix, names, description):
        text = (description + '\0').encode('utf-16le')
        variable_header = header[:6] + struct.pack('<I', len(text) // 2) + text + header[12:]
        prefix = variable_header + struct.pack('<I', len(first)) + first + bytes(8) + struct.pack('<I', len(second)) + second + bytes(8)
        records = b''.join(struct.pack('<I', len(encoded) // 2) + encoded
                           for name in names for encoded in [(name + '\0').encode('utf-16le')])
        raw = prefix + struct.pack('<I', len(names)) + records + suffix
        parsed = read_resource_source(raw)
        assert parsed['prefix'] == prefix and parsed['suffix'] == suffix
        assert parsed['cached_names'] == names
        assert [raw[start:start + size] for start, size in parsed['code_spans']] == [first, second]
    check()


@pytest.mark.parametrize('index', range(4))
def test_native_program_task_plan_and_readback_bind_original_source_and_configuration(index):
    from src.gxw.native_write import native_program_task_plan, verify_native_program_task_binding
    before, after, program, request, observation = task_binding_witness(index)
    plan = native_program_task_plan(before, program, resource=request['resource'], task=request['task'])
    assert {key: value for key, value in plan.items() if key != 'tasks'} == request
    result = verify_native_program_task_binding(before, program, after, observation,
        resource=request['resource'], task=request['task'])
    assert result.data == after and result.report['native_task_configuration'] == 'current'
    assert result.report['validation']['source_body'] == result.report['validation']['task_configuration'] == 'exact'
    assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    if index == 2:
        assert any(row['event_name'] == 'I750' for row in plan['tasks'])
        assert {'T#100ms', 'T#20ms'} <= {row['interval'] for row in plan['tasks']}
    with pytest.raises(GXWFormatError, match='already registered'):
        native_program_task_plan(after, program, resource=request['resource'], task=request['task'])
    with pytest.raises(GXWFormatError, match='ownership'):
        native_program_task_plan(before, program, resource='MAIN', task=request['task'])


@pytest.mark.parametrize('damage', ['source-type', 'source-owner', 'resource-owner', 'element-type',
    'element-reused', 'old-task-record', 'event-getter', 'priority-getter', 'interval-getter', 'missing-task'])
def test_native_program_task_binding_rejects_conflicting_original_getters_and_membership(damage):
    from src.gxw.native_write import verify_native_program_task_binding
    before, after, program, request, observation = task_binding_witness(2)
    created = observation['creation']
    if damage == 'source-type':
        created['pou_type'] = 0x01000002
    elif damage == 'source-owner':
        created['source']['owner']['id'][4] += 1
    elif damage == 'resource-owner':
        created['resource']['id'][3] += 1
    elif damage == 'element-type':
        created['element']['read_type']['data_type'] = 10
    elif damage == 'element-reused':
        previous = next(row['programs'][0] for row in created['before_tasks'] if row['programs'])
        created['element']['id'] = previous['element']['id']
    elif damage == 'old-task-record':
        previous = next(row['programs'][0] for row in created['after_tasks'] if row['programs'])
        previous['line'] += 1
    elif damage == 'missing-task':
        created['before_tasks'].pop()
    else:
        field = {'event-getter': 'event_name', 'priority-getter': 'priority', 'interval-getter': 'interval'}[damage]
        created['before_tasks'][0]['configuration'][field]['code'] = 0x2d010025
    with pytest.raises(GXWFormatError):
        verify_native_program_task_binding(before, program, after, observation,
            resource=request['resource'], task=request['task'])


def test_generated_task_append_preserves_original_configuration_and_all_existing_records():
    import struct
    from hypothesis import given, settings, strategies as st
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping, read_task_source, verify_task_source_append
    before, after, _, request, _ = task_binding_witness(2)
    blobs = []
    for value in (before, after):
        outer = validate_cfb_streams(value)
        blobs.append(validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])[request['task'] + '.tsk']])
    old, saved = blobs
    parsed = read_task_source(saved)
    new_record = parsed['rows'][-1]['raw']

    @settings(max_examples=25, deadline=None, database=None, derandomize=True)
    @given(names=st.lists(st.text(alphabet='ABC_局部', min_size=1, max_size=15), max_size=8),
           suffix=st.binary(max_size=40), unknown_word=st.binary(min_size=4, max_size=4))
    def check(names, suffix, unknown_word):
        records = []
        for line, name in enumerate(names, 1):
            encoded = (name + '\0').encode('utf-16le')
            records.append(struct.pack('<I', len(encoded) // 2) + encoded + struct.pack('<iI', line, 1) + b'\0\0')
        # Independent native row bytes supply the append encoding. Generated
        # rows vary only the old history; the expected native config is fixed.
        prefix = old[:parsed['count_offset'] - 4] + unknown_word
        original = prefix + struct.pack('<I', len(records)) + b''.join(records) + suffix
        created = bytearray(new_record)
        line_offset = 4 + struct.unpack_from('<I', created)[0] * 2
        struct.pack_into('<i', created, line_offset, len(records) + 1)
        candidate = prefix + struct.pack('<I', len(records) + 1) + b''.join(records) + created + suffix
        result = verify_task_source_append(original, candidate, program_name=request['program_name'], line=len(records) + 1)
        assert result['configuration'] == result['old_rows'] == result['unknown_header_and_suffix'] == 'exact'
        changed = bytearray(candidate)
        changed[parsed['count_offset'] - 8] ^= 1
        with pytest.raises(GXWFormatError, match='configuration'):
            verify_task_source_append(original, changed, program_name=request['program_name'], line=len(records) + 1)
    check()


@pytest.mark.parametrize('damage', ['body-owner', 'label-owner', 'getter-failure', 'language',
                                  'existing-type', 'missing-existing', 'body-bytes'])
def test_native_source_creation_rejects_conflicting_returned_objects(damage):
    from src.gxw.native_write import verify_native_source_creation
    before, created, case = source_creation_witness()
    observation = deepcopy(case['native_creation'])
    result = observation['creation']
    if damage == 'body-owner':
        result['source']['body_parent']['id'] = result['collection']['id']
    elif damage == 'label-owner':
        result['labels']['id'][4] += 1
    elif damage == 'getter-failure':
        result['source']['owner']['read_type']['code'] = 1
    elif damage == 'language':
        result['source']['language']['value'] = 193
    elif damage == 'existing-type':
        result['existing_sources'][0]['pou_type'] = 0x1000002
    elif damage == 'missing-existing':
        result['existing_sources'].clear()
    else:
        body = bytearray(base64.b64decode(result['body'])); body[-1] ^= 1
        result['body'] = base64.b64encode(body).decode()
    with pytest.raises(GXWFormatError):
        verify_native_source_creation(before, case['created_name'], created, observation)


def test_empty_native_creation_cannot_rewrite_an_existing_source_byte():
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.native_write import verify_native_source_creation
    before, created, case = source_creation_witness()
    outer = validate_cfb_streams(created)
    payload = bytearray(validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])['1.Program.pou']])
    payload[50] ^= 1
    changed = replay_source_records(created, {'1.Program.pou': bytes(payload)})
    with pytest.raises(GXWFormatError, match='changed an unedited original object'):
        verify_native_source_creation(before, case['created_name'], changed, case['native_creation'])


@pytest.mark.parametrize('damage', [None, 'opaque-unchanged', 'debug-prefix', 'debug-field', 'debug-suffix', 'source'])
def test_independent_native_creation_accepts_only_the_observed_debug_map_reordering(damage):
    from src.gxw.compiler_debug import parse_compiler_debug, parse_compiler_debug_search_maps
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.native_write import verify_native_source_creation

    prefix = 'cases/native-current-created-inout-imported-axis-create-Q06UDV-v1/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-created-inout-20261010.zip')) as archive:
        before, created = (archive.read(prefix + stage + '.gxw') for stage in ('input', 'candidate'))
        observation = json.loads(archive.read(prefix + 'observation.json'))
        original_refusal = json.loads(archive.read(prefix + 'core-verification.json'))
    assert original_refusal['Core_creation_status'] == 'rejected'
    assert original_refusal['Core_error'].endswith('DebugInformation2.dat')
    if damage == 'opaque-unchanged':
        opaque = b'unsupported-debug-format-preserved-without-parsing'
        before = replay_source_records(before, {'DebugInformation2.dat': opaque})
        created = replay_source_records(created, {'DebugInformation2.dat': opaque})
        result = verify_native_source_creation(before, 'FreshAxis_C1', created, observation)
        assert result.report['debug_map_changes'] == []
        assert result.report['validation']['unedited_sources'] == 'preserved'
    elif damage:
        outer = validate_cfb_streams(created)
        nested = validate_cfb_streams(outer['_hdb'])
        mapping = logical_mapping(outer['projectdatalist.xml'])
        logical = 'PrgAxis1.Program.pou' if damage == 'source' else 'DebugInformation2.dat'
        payload = bytearray(nested[mapping[logical]])
        if damage == 'source':
            payload[50] ^= 1
        elif damage == 'debug-suffix':
            payload += b'unedited-unknown-extension'
        else:
            parsed = parse_compiler_debug_search_maps(parse_compiler_debug(bytes(payload)))
            if damage == 'debug-prefix':
                offset = parsed.debug.offset_tables[0].offset + 8 + 20
            else:
                entry = parsed.maps[1].entries[0]
                offset = entry.offset + 4 + len(entry.key_bytes) + 1 + 4 + sum(4 + 4 * len(group) for group in entry.groups)
            payload[offset] ^= 1
        created = replay_source_records(created, {logical: bytes(payload)})
        with pytest.raises(GXWFormatError, match='changed an unedited original object'):
            verify_native_source_creation(before, 'FreshAxis_C1', created, observation)
    else:
        result = verify_native_source_creation(before, 'FreshAxis_C1', created, observation)
        assert result.report['debug_map_changes'] == [dict(object='DebugInformation2.dat',
            change='search-map-entry-order-only', offset_tables_and_elements='exact',
            group_order_and_fields='exact', unknown_suffix='exact')]
        assert result.report['validation']['unedited_sources'] == 'preserved'
        assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'


@pytest.mark.parametrize('name', ['', '1FB', 'WITH SPACE', 'PLC.OWNER', '1', 'FB_😀'])
def test_native_source_creation_requires_a_new_encodable_identifier(name):
    from src.gxw.native_write import native_source_creation_plan
    before, _, _ = source_creation_witness()
    with pytest.raises(GXWFormatError):
        native_source_creation_plan(before, name)


@pytest.mark.parametrize('cpu', ['Q04UDV', 'L02', 'FX1N/FX1NC', 'FX3U/FX3UC'])
def test_new_fb_bool_formal_plan_matches_saved_native_lines_and_direction(cpu):
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan, verify_native_save, workspace_body
    _, created, case = source_creation_witness(cpu)
    context = read_project_context(created, case['created_program'])
    model = context.object_model()
    model['canvas_height'] = 12
    model['nodes'] = [dict(id='sig', template='input', symbol='输入_Create_C1', x=3, y=2),
                      dict(id='result', template='output', symbol='输出_Create_C1', x=10, y=2)]
    model['wires'] = [{'from': 'sig.OUT', 'to': 'result.IN'}]
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [
        dict(name='输入_Create_C1', data_type='BOOL', class_name='VAR_INPUT'),
        dict(name='输出_Create_C1', data_type='BOOL', class_name='VAR_OUTPUT')]}}
    prepared = prepare_object_project(model, baseline=created)
    plan = native_source_plan(prepared)
    assert [(row['name'], row['class_code'], row['line']) for row in plan['create']] == [
        ('输入_Create_C1', 3, 1), ('输出_Create_C1', 4, 2)]
    saved, _ = source_creation_stage(cpu)
    assert base64.b64decode(plan['after_body']) == workspace_body(saved.program.raw)
    assert verify_native_save(prepared, saved.raw, {}).report['validation']['declarations'] == 'passed'
    for row in saved.declarations[saved.sources.local_table].rows:
        native, = [item for item in case['native_references'] if item['name'] == row.name]
        assert (row.class_code, row.data_type, row.record_id, row.type_code) == (native['class_code'], native['type'],
            1 if native['attribute'] == 1 else 2, 0)
    assert case['public_check'] == dict(status='passed', completed=True)
    assert case['current_source_check'] == 'established'


def test_new_fb_formal_rename_sequences_preserve_native_interface_and_unknown_bytes():
    hypothesis = pytest.importorskip('hypothesis')
    from hypothesis import strategies as st
    import struct
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import read_project_context, prepare_object_project
    from src.gxw.models import UnknownRecord
    from src.gxw.structured_pou_writer import serialize_structured_pou
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.native_write import native_source_plan
    context, case = source_creation_stage(stage='caller')
    opaque = UnknownRecord(max(r.offset for r in context.program.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    program = replace(context.program, unknown_records=(opaque,), record_count=context.program.record_count + 1)
    original = replay_source_records(context.raw, {program.logical_name: serialize_structured_pou(program)})
    suffix = st.text(alphabet='abcXYZ中文变量_012', min_size=1, max_size=18)

    @hypothesis.settings(max_examples=25, deadline=None, derandomize=True)
    @hypothesis.example([('中', '文'), ('LONG_NAME', '变量_012')])
    @hypothesis.given(st.lists(st.tuples(suffix, suffix), min_size=1, max_size=3))
    def check(edits):
        raw = original
        names = {r['class_code']: r['name'] for r in case['native_references']}
        for input_suffix, output_suffix in edits:
            current = read_project_context(raw, program.logical_name)
            graph = current.object_model()
            updated = {3: 'IN_' + input_suffix, 4: 'OUT_' + output_suffix}
            for direction in (3, 4):
                graph = edit_draft(graph, dict(action='update_label', table=current.sources.local_table,
                    name=names[direction], field='name', value=updated[direction]), context=current)['model']
            prepared = prepare_object_project(graph, baseline=raw)
            native_source_plan(prepared)
            candidate = replay_source_records(raw, {name: payload for name, (_, _, payload) in prepared.replacements.items()})
            refreshed = read_project_context(candidate, program.logical_name)
            assert [r.raw for r in refreshed.program.unknown_records] == [opaque.raw]
            assert [r.raw for r in refreshed.program.wires] == [r.raw for r in current.program.wires]
            caller = read_project_context(candidate, '1.Program.pou')
            call, = [node for node in caller.program.nodes if node.symbol == '实例_Create_C1']
            ports = caller.sources.callable(call)['ports']
            assert [(p['formal_name'], p['class_code'], p['data_type'], p['side']) for p in ports] == [
                (updated[r['class_code']], r['class_code'], r['type'], 'in' if r['attribute'] == 1 else 'out')
                for r in case['native_references']]
            before = validate_cfb_streams(validate_cfb_streams(raw)['_hdb'])
            after = validate_cfb_streams(validate_cfb_streams(candidate)['_hdb'])
            edited = {prepared.mapping[name] for name in prepared.replacements}
            assert {k: v for k, v in before.items() if k not in edited} == {k: v for k, v in after.items() if k not in edited}
            names, raw = updated, candidate
    check()


@pytest.mark.parametrize('name',[row['name'] for row in flat_project_structure_witnesses()['created_fbd_controls']['cases']])
def test_native_created_fbd_formals_use_current_source_types_and_native_direction_oracle(name):
    context,case = created_fbd_context(name)
    model = context.object_model()
    assert model['schema_version']==2 and model['cpu']==case['cpu'] and not model['issues']
    assert (case['compile'],case['check'],case['public_check'],case['current_source_check']) == (
        'completed_accepted','completed_accepted','passed','established')
    assert context.sources.local_table==case['local_table']
    assert context.declarations[context.sources.local_table].owner_pou_type==0x1000002
    assert all((r.type_code,r.array_marker,r.unknown_u32,r.type_reference)==(0,0,0,'')
               for r in context.declarations[context.sources.local_table].rows)
    for node in model['nodes']:
        if node['template'] not in ('input','output'):
            continue
        native, = [r for r in case['native_references'] if r['name']==node['symbol']]
        port, = node['ports']
        assert port['name']==('OUT' if native['attribute']==1 else 'IN')
        if node['symbol'].endswith('.Spare'):
            assert port['data_type']==native['type']
            member = context.sources.structure_member(node['symbol'])
            assert member['source_table']==case['local_table'] and member['data_type']==native['type']
        else:
            _,declaration = context.sources.label(node['symbol'])
            assert declaration.data_type==native['type']
    assert generate_object_project(model,baseline=context.raw).data==context.raw


def test_native_constructor_body_and_header_roles_resolve_by_registered_owner():
    from src.gxw.container_writer import validate_cfb_streams,replace_project_stream
    from src.gxw.project_metadata import logical_mapping,synchronize_history
    from src.gxw.object_model import read_project_context
    context,case = created_fbd_context()
    empty = case['empty_source']
    raw = project_rows_variant(context.raw,{case['selected_program']:{'szName':empty['program_name']},
        case['local_table']:{'szName':empty['labels_name']}})
    outer = validate_cfb_streams(raw); mapping = logical_mapping(outer['projectdatalist.xml'])
    before = validate_cfb_streams(outer['_hdb']); replacements = {}
    nested = outer['_hdb']
    for logical,field in ((empty['program_name'],'program_base64'),(empty['labels_name'],'labels_base64')):
        payload = base64.b64decode(empty[field])
        replacements[logical]=(mapping[logical],before[mapping[logical]],payload)
        nested, _ = replace_project_stream(nested,mapping[logical],payload)
    raw, _ = replace_project_stream(raw,'_hdb',nested)
    history,_,_ = synchronize_history(outer['history.xml'],replacements,current_mapping=mapping)
    raw, _ = replace_project_stream(raw,'history.xml',history)
    selected = read_project_context(raw,empty['program_name'])
    assert selected.sources.local_table==empty['labels_name']
    assert selected.declarations[empty['labels_name']].owner_pou_type==0x1000002
    assert not selected.declarations[empty['labels_name']].rows and not selected.program.nodes
    assert not selected.object_model()['issues']
    assert generate_object_project(selected.object_model(),baseline=raw).data==raw


@pytest.mark.parametrize('name',[row['name'] for row in flat_project_structure_witnesses()['created_fbd_controls']['cases']])
def test_native_project_fb_type_plan_and_saved_cache_match_original_native_transition(name):
    from src.gxw.object_model import prepare_object_project
    from src.gxw.native_write import native_source_plan,verify_native_save,workspace_body
    context,case = created_fbd_context(name,program='FBD_MATRIX.Program.pou',before_caller=True)
    model = context.object_model(); transition = case['caller_type_transition']
    node, = [n for n in model['nodes'] if n['symbol']==transition['instance']]
    node['template'] = 'function_block:'+transition['after']
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name=transition['instance'],
        data_type=transition['after'],kind='function_block',class_name='VAR')]}}
    prepared = prepare_object_project(model,baseline=context.raw)
    plan = native_source_plan(prepared)
    assert plan['updates']==[dict(before=dict(name='TIMER_A',data_type='MODULE_FB',class_code=1),
        after=dict(name='TIMER_A',data_type=transition['after'],class_code=1),initial_before='',initial_after='')]
    saved,_ = created_fbd_context(name,program='FBD_MATRIX.Program.pou')
    assert base64.b64decode(plan['after_body'])==workspace_body(saved.program.raw)
    native_row, = [r for r in saved.declarations[saved.sources.local_table].rows if r.name=='TIMER_A']
    assert (native_row.type_code,native_row.type_reference)==(15,'MODULE_FB')
    assert verify_native_save(prepared,saved.raw,{}).report['validation']['declarations']=='passed'


def test_native_zero_cache_fb_instance_transition_matches_frozen_source_and_save():
    from src.gxw.object_model import prepare_object_project
    from src.gxw.native_write import native_source_plan,verify_native_save,workspace_body
    control = flat_project_structure_witnesses()['created_fbd_controls']['zero_cache_instance_control']
    context,_ = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['before_source_streams'])
    model = context.object_model(); node, = [n for n in model['nodes'] if n['symbol']=='TIMER_A']
    node['template'] = 'function_block:MODULE_FB'
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name='TIMER_A',
        data_type='MODULE_FB',kind='function_block',class_name='VAR')]}}
    prepared = prepare_object_project(model,baseline=context.raw)
    plan = native_source_plan(prepared)
    saved,_ = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['after_source_streams'])
    assert base64.b64decode(plan['after_body'])==workspace_body(saved.program.raw)
    assert verify_native_save(prepared,saved.raw,{}).report['validation']['declarations']=='passed'
    assert control['public_check']=='passed' and control['current_source_check']=='established'


@pytest.mark.parametrize('factor',['source_language','unknown_cache_reference'])
def test_native_fb_type_transition_requires_current_fbd_project_sources_and_observed_cache(factor):
    from src.gxw.object_model import prepare_object_project
    from src.gxw.native_write import native_source_plan
    from src.gxw.source_header import source_payload_offset
    from src.gxw.declarations import serialize_declarations
    context,case = created_fbd_context(before_caller=True,program='FBD_MATRIX.Program.pou')
    if factor=='source_language':
        logical = case['selected_program']; payload = bytearray(base64.b64decode(case['source_streams'][logical]))
        payload[source_payload_offset(payload)] = 193
        streams = {logical:base64.b64encode(payload).decode()}
    else:
        logical = context.sources.local_table; document = context.declarations[logical]
        document = replace(document,rows=tuple(replace(r,type_reference='MISSING_FB') if r.name=='TIMER_A' else r
                                              for r in document.rows))
        streams = {logical:base64.b64encode(serialize_declarations(document)).decode()}
    context,_ = created_fbd_context(before_caller=True,program='FBD_MATRIX.Program.pou',source_streams=streams)
    model = context.object_model(); node, = [n for n in model['nodes'] if n['symbol']=='TIMER_A']
    node['template'] = 'function_block:'+case['caller_type_transition']['after']
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name='TIMER_A',
        data_type=case['caller_type_transition']['after'],kind='function_block',class_name='VAR')]}}
    with pytest.raises(GXWFormatError,match='type change is not verified'):
        native_source_plan(prepare_object_project(model,baseline=context.raw))


def test_native_fb_type_saved_cache_cannot_be_replaced_with_the_new_type_reference():
    from src.gxw.object_model import prepare_object_project
    from src.gxw.native_write import verify_native_save
    from src.gxw.project_writer import build_gxw_project
    context,case = created_fbd_context(before_caller=True,program='FBD_MATRIX.Program.pou')
    target = case['caller_type_transition']['after']; model = context.object_model()
    node, = [n for n in model['nodes'] if n['symbol']=='TIMER_A'];node['template']='function_block:'+target
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name='TIMER_A',data_type=target,
        kind='function_block',class_name='VAR')]}}
    prepared = prepare_object_project(model,baseline=context.raw)
    saved,_ = created_fbd_context(program='FBD_MATRIX.Program.pou')
    local = saved.sources.local_table; document = saved.declarations[local]
    document = replace(document,rows=tuple(replace(r,type_reference=target) if r.name=='TIMER_A' else r for r in document.rows))
    corrupt = build_gxw_project(saved.raw,declarations={local:document}).data
    with pytest.raises(GXWFormatError,match='changed native FB cache'):
        verify_native_save(prepared,corrupt,{})


def test_source_bound_editor_formal_type_and_instance_changes_match_native_core_control():
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import prepare_object_project
    from src.gxw.native_write import native_source_plan,verify_native_save,workspace_body
    control = flat_project_structure_witnesses()['created_fbd_controls']['core_edit_control']
    formal,case = created_fbd_context('FX3U/FX3UC',before_caller=True)
    renamed = edit_draft(formal.object_model(),dict(action='update_label',table=formal.sources.local_table,
        name='STATE',field='name',value=control['formal_name']),context=formal)['model']
    prepared = prepare_object_project(renamed,baseline=formal.raw)
    assert workspace_body(prepared.replacements[formal.program.logical_name][2])==workspace_body(
        base64.b64decode(control['after_formal_source_streams'][formal.program.logical_name]))
    context,_ = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['after_formal_source_streams'])
    model = context.object_model(); node, = [n for n in model['nodes'] if n['symbol']=='TIMER_A']
    edited = edit_draft(model,dict(action='update_node',id=node['id'],field='template',
        value='function_block:'+case['caller_type_transition']['after']),context=context)
    ports = next(row['ports'] for row in edited['presentation']['nodes'] if row['id']==node['id'])
    assert [(p['name'],p['side']) for p in ports]==[('SIGNAL','in'),('in.'+control['formal_name'],'in'),
        ('RESULT','out'),('out.'+control['formal_name'],'out')]
    row, = [r for r in edited['presentation']['rows'][context.sources.local_table] if r['name']=='TIMER_A']
    assert row['data_type']==case['caller_type_transition']['after'] and row['kind']=='function_block'
    model = edit_draft(edited['model'],dict(action='update_node',id=node['id'],field='symbol',
        value=control['instance_name']),context=context)['model']
    prepared = prepare_object_project(model,baseline=context.raw);plan = native_source_plan(prepared)
    assert plan['updates'][0]['after']['name']==control['instance_name']
    saved,_ = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['after_caller_source_streams'])
    assert base64.b64decode(plan['after_body'])==workspace_body(saved.program.raw)
    assert verify_native_save(prepared,saved.raw,{}).report['validation']['declarations']=='passed'
    assert all(r['instance']=='FBD_MATRIX.'+control['instance_name'] for r in control['native_references'])


@pytest.mark.parametrize('endpoint',['in.STATE','out.STATE'])
def test_template_type_change_preserves_named_in_out_endpoints_by_native_position(endpoint):
    from src.gxw.editor import edit_draft
    control = flat_project_structure_witnesses()['created_fbd_controls']['core_edit_control']
    context,case = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['after_formal_source_streams'])
    model = context.object_model(); target, = [n for n in model['nodes'] if n['symbol']=='TIMER_A']
    other, = [n for n in model['nodes'] if n['symbol']=='TIMER_B']
    incoming = endpoint.startswith('in.')
    wire = dict(from_=other['id']+'.out.STATE',to=target['id']+'.'+endpoint) if incoming else dict(
        from_=target['id']+'.'+endpoint,to=other['id']+'.in.STATE')
    wire['from']=wire.pop('from_');model['wires'].append(wire)
    target['port_edits']={endpoint:{'negated':False}}
    edited = edit_draft(model,dict(action='update_node',id=target['id'],field='template',
        value='function_block:'+case['caller_type_transition']['after']),context=context)
    current = edited['model']; updated, = [n for n in current['nodes'] if n['id']==target['id']]
    new_endpoint = ('in.' if incoming else 'out.')+control['formal_name']
    assert updated['port_edits']=={new_endpoint:{'negated':False}}
    assert current['wires'][-1]['to' if incoming else 'from']==target['id']+'.'+new_endpoint
    assert current['wires'][-1]['from' if incoming else 'to']==wire['from' if incoming else 'to']
    ports = next(row['ports'] for row in edited['presentation']['nodes'] if row['id']==target['id'])
    before, = [p for p in target['ports'] if p['name']==endpoint]
    after, = [p for p in ports if p['name']==new_endpoint]
    assert (before['x'],before['y'],before['side'])==(after['x'],after['y'],after['side'])
    assert model['wires'][-1]==wire and target['template']=='function_block:MODULE_FB'


@pytest.mark.parametrize('name', [row['name'] for row in flat_project_structure_witnesses()['local_formal_fbd_controls']['cases']])
def test_local_in_out_structure_array_members_bind_current_source_and_native_terminal_types(name):
    context, case = compound_structure_context(name, local_formal=True)
    model = context.object_model()
    assert model['cpu'] == case['cpu'] and model['schema_version'] == 2
    assert context.sources.local_table == 'MODULE_FB.Labels.lh'
    formal = next(row for row in context.declarations[context.sources.local_table].rows if row.name == case['formal_name'])
    assert (formal.class_code, formal.type_code, formal.array_marker, formal.unknown_u32, formal.type_reference) == (5, 4, 0, 0, '')
    assert case['native_body_reads'] and case['saved_bytes_equal_cold_input']
    if name in ('local-outside-array', 'local-unknown-member'):
        assert case['compile'] == 'completed_rejected' and case['public_check'] == 'not_requested'
        assert any(issue['code'] == 'structure_source_gap' for issue in model['issues'])
        code = 0x500c1031 if name == 'local-outside-array' else 0x500c1037
        assert sum(row['code'] == code and (row['left'], row['top'], row['right'], row['bottom']) == (11, 8, 13, 10)
                   for row in case['native_reports']) == 2
    else:
        assert case['compile'] == case['check'] == 'completed_accepted'
        assert case['public_check'] == 'passed' and case['current_source_check'] == 'established'
        assert not model['issues']
        projected = {node['source_offset']: node for node in model['nodes']}
        for node in context.program.nodes:
            if not node.symbol.endswith('.Spare'):
                continue
            bbox = (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)
            native = [row for row in case['native_references'] if row['source'] == 'MODULE_FB'
                      and (row['left'], row['top'], row['right'], row['bottom']) == bbox]
            assert native and {row['type'] for row in native} == {'WORD'}
            assert {row['attribute'] for row in native} == {1 if node.kind == NodeKind.INPUT else 2}
            port, = projected[node.offset]['ports']
            assert (port['data_type'], port['name']) == ('WORD', 'OUT' if node.kind == NodeKind.INPUT else 'IN')
            value = context.sources.structure_member(node.symbol)
            assert value['data_type'] == 'WORD' and value['member'] == 'Spare'
            if node.symbol.startswith(case['formal_name'] + '['):
                assert value['source_table'] == context.sources.local_table
    assert generate_object_project(model, baseline=context.raw).data == context.raw


@pytest.mark.parametrize('patch', [
    {'class_code': 3}, {'class_code': 4}, {'class_code': 0}, {'unknown_u32': 2},
    {'array_marker': 1}, {'type_code': 9}, {'type_reference': 'OTHER'},
    {'data_type': 'OwnedFlatStruct_图形_A1'},
    {'data_type': 'ARRAY[0..1,0..1] OF OwnedFlatStruct_图形_A1'},
])
@pytest.mark.parametrize('created',[False,True])
def test_local_structure_binding_rejects_unobserved_declaration_storage_and_cache(patch,created):
    context, _ = created_fbd_context() if created else compound_structure_context('Q04UDV', local_formal=True)
    table = context.sources.local_table
    document = context.declarations[table]
    declarations = {**context.declarations, table: replace(document, rows=tuple(
        replace(row, **patch) if row.name == 'STATE' else row for row in document.rows))}
    with pytest.raises(GXWFormatError):
        context.sources.with_declarations(declarations).structure_member('STATE[1].Spare')


@pytest.mark.parametrize('symbol', ['STATE', 'STATE[1]', 'STATE.Spare', 'STATE[2].Spare',
    'STATE[index].Spare', 'STATE[1].Spare.Child', 'STATE[1].Missing'])
def test_local_structure_member_requires_current_literal_bounds_and_member(symbol):
    context, _ = compound_structure_context('Q04UDV', local_formal=True)
    with pytest.raises(GXWFormatError):
        context.sources.structure_value(symbol)


def test_local_in_out_array_rename_and_index_sequences_preserve_native_type_oracle_and_opaque_bytes():
    import struct
    from hypothesis import given, settings, strategies as st
    from src.gxw.editor import edit_draft
    from src.gxw.models import UnknownRecord
    from src.gxw.object_model import read_project_context
    from src.gxw.project_writer import build_gxw_project
    names = [('local',name) for name in ('Q04UDV','FX3U/FX3UC','local-formal-rename','local-shifted-formal-bounds')]
    names += [('created',row['name']) for row in flat_project_structure_witnesses()['created_fbd_controls']['cases']]
    @settings(max_examples=20, deadline=None, derandomize=True, database=None)
    @given(st.sampled_from(names), st.lists(st.tuples(
        st.text(alphabet='ABC名字', min_size=1, max_size=8), st.integers(0, 1)), min_size=1, max_size=3))
    def check(source, edits):
        origin,name = source
        context, case = created_fbd_context(name) if origin=='created' else compound_structure_context(name, local_formal=True)
        owner = case['selected_program'].removesuffix('.Program.pou')
        native_types = {row['type'] for row in case['native_references'] if row['source'] == owner
                        and (row['left'], row['top']) in ((11, 8), (20, 8))}
        assert native_types == {'WORD'}
        opaque = UnknownRecord(max(row.offset for row in context.program.iter_records()) + 1, 12, 99,
                               struct.pack('<III', 12, 99, 0x12345678))
        program = replace(context.program, unknown_records=(opaque,), record_count=context.program.record_count + 1)
        context = read_project_context(build_gxw_project(context.raw, program).data, context.program.logical_name)
        untouched = {table: doc.raw for table, doc in context.declarations.items() if table != context.sources.local_table}
        wires = [wire.raw for wire in context.program.wires]
        structure = context.sources.structure('OwnedFlatStruct_图形_A1').raw
        old = case['formal_name']
        low = 3 if name == 'local-shifted-formal-bounds' else 0
        for suffix, index in edits:
            new = 'FORMAL_' + suffix
            model = edit_draft(context.object_model(), {'action': 'update_label', 'table': context.sources.local_table,
                'name': old, 'field': 'name', 'value': new}, context=context)['model']
            for node in model['nodes']:
                if node['symbol'].startswith(new + '['):
                    node['symbol'] = new + '[' + str(low + index) + '].Spare'
            context = read_project_context(generate_object_project(model, baseline=context.raw).data, model['program'])
            current = context.object_model()
            assert current['schema_version'] == 2 and not current['issues']
            assert [row.raw for row in context.program.unknown_records] == [opaque.raw]
            assert [wire.raw for wire in context.program.wires] == wires
            assert all(context.declarations[table].raw == raw for table, raw in untouched.items())
            assert context.sources.structure('OwnedFlatStruct_图形_A1').raw == structure
            formal = next(row for row in context.declarations[context.sources.local_table].rows if row.class_code == 5)
            assert formal.name == new and formal.data_type == case['formal_cache']['data_type']
            members = [node for node in current['nodes'] if node['symbol'].startswith(new + '[')]
            assert len(members) == 2 and {node['ports'][0]['data_type'] for node in members} == native_types
            assert {node['ports'][0]['name'] for node in members} == {'IN', 'OUT'}
            assert all(context.sources.structure_member(node['symbol'])['root_bounds'] == ((low, low + 1),) for node in members)
            if new != old:
                assert context.sources.label(old) is None
            old = new
    check()


@pytest.mark.parametrize('name', [row['name'] for row in flat_project_structure_witnesses()['compound_formal_controls']['cases']])
def test_current_whole_structure_values_and_array_formals_keep_native_source_identity(name):
    context, case = compound_structure_context(name)
    model = context.object_model()
    assert model['cpu'] == case['cpu'] and model['schema_version'] == 2
    assert not any(issue['code'] == 'callable_source_gap' for issue in model['issues'])
    formal = next(row for row in context.declarations['MODULE_FB.Labels.lh'].rows if row.name == 'STATE')
    # Native setters retained the old WORD cache even for structure arrays.
    assert formal.type_code == case['formal_cache']['type_code'] == 4
    assert formal.array_marker == 0 and formal.data_type == case['formal_cache']['data_type']
    for node in model['nodes']:
        if node['template'] == 'function_block:MODULE_FB':
            ports = {port['name']: port for port in node['ports']}
            assert [(ports[key]['formal_name'], ports[key]['side'], ports[key]['data_type'], ports[key]['class_code'])
                    for key in ('in.STATE', 'out.STATE')] == [('STATE', 'in', formal.data_type, 5), ('STATE', 'out', formal.data_type, 5)]
        elif node['symbol'] == 'OwnedFlat_A' or node['symbol'] in ('OwnedFlat_B_图形_A1[0]', 'OwnedFlat_B_图形_A1[1]'):
            assert node['ports'][0]['data_type'] == 'OwnedFlatStruct_图形_A1'
        elif node['symbol'] == 'OwnedFlat_B_图形_A1':
            assert node['ports'][0]['data_type'] == 'ARRAY[0..1] OF OwnedFlatStruct_图形_A1'
    if name == 'outside-root-output':
        assert any(issue['code'] == 'structure_source_gap' for issue in model['issues'])
    else:
        assert not model['issues']
    assert generate_object_project(model, baseline=context.raw).data == context.raw


def test_whole_array_and_element_keep_distinct_value_types_and_current_bounds():
    context, _ = compound_structure_context('array')
    whole = context.sources.structure_value('OwnedFlat_B_图形_A1')
    element = context.sources.structure_value('OwnedFlat_B_图形_A1[1]')
    assert whole['bounds'] == ((0, 1),) and whole['root_constant_index'] is None
    assert element['data_type'] == 'OwnedFlatStruct_图形_A1' and element['bounds'] == ()
    assert element['declared_type'] == whole['data_type'] == 'ARRAY[0..1] OF OwnedFlatStruct_图形_A1'
    assert element['root_bounds'] == ((0, 1),) and element['root_constant_index'] == 1
    for symbol in ('OwnedFlat_B_图形_A1[2]', 'OwnedFlat_B_图形_A1[index]', 'OwnedFlat_A[0]'):
        with pytest.raises(GXWFormatError):
            context.sources.structure_value(symbol)


def test_compound_value_v2_rename_sequences_preserve_opaque_records_and_formal_sources():
    import struct
    from hypothesis import given, settings, strategies as st
    from src.gxw.editor import edit_draft
    from src.gxw.models import UnknownRecord
    from src.gxw.object_model import read_project_context
    from src.gxw.project_writer import build_gxw_project
    names = ('scalar', 'array', 'shifted-formal-bounds', 'FX3U/FX3UC')
    @settings(max_examples=16, deadline=None, derandomize=True, database=None)
    @given(st.sampled_from(names), st.lists(st.text(alphabet='ABC名字', min_size=1, max_size=8), min_size=1, max_size=3))
    def check(name, suffixes):
        context, case = compound_structure_context(name)
        opaque = UnknownRecord(max(row.offset for row in context.program.iter_records()) + 1, 12, 99,
                               struct.pack('<III', 12, 99, 0x12345678))
        program = replace(context.program, unknown_records=(opaque,), record_count=context.program.record_count + 1)
        context = read_project_context(build_gxw_project(context.raw, program).data, context.program.logical_name)
        untouched = {table: doc.raw for table, doc in context.declarations.items() if table != context.sources.local_table}
        old = 'TIMER_B'
        formal_type = case['formal_cache']['data_type']
        for suffix in suffixes:
            new = 'INSTANCE_' + suffix
            model = context.object_model()
            model = edit_draft(model, {'action': 'update_label', 'table': context.sources.local_table,
                                      'name': old, 'field': 'name', 'value': new})['model']
            context = read_project_context(generate_object_project(model, baseline=context.raw).data, model['program'])
            model = context.object_model()
            assert not model['issues'] and model['schema_version'] == 2
            assert [row.raw for row in context.program.unknown_records] == [opaque.raw]
            assert all(context.declarations[table].raw == raw for table, raw in untouched.items())
            labels = context.declarations[context.sources.local_table].rows
            assert sum(row.name == new and row.data_type == 'MODULE_FB' for row in labels) == 1
            assert old == new or all(row.name != old for row in labels)
            for block in [node for node in model['nodes'] if node['template'] == 'function_block:MODULE_FB']:
                ports = {port['name']: port for port in block['ports']}
                assert ports['in.STATE']['data_type'] == ports['out.STATE']['data_type'] == formal_type
                assert (ports['in.STATE']['side'], ports['out.STATE']['side']) == ('in', 'out')
            old = new
    check()


def test_independent_imported_whole_structure_and_members_use_native_type_oracles():
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations, parse_structure_declarations
    witness = flat_project_structure_witnesses()['imported_structure_control']
    structure = witness['structure']
    document = parse_structure_declarations(base64.b64decode(structure['raw_base64']), logical_name=structure['logical'])
    declarations = {witness['global_table']: parse_declarations(base64.b64decode(witness['global_base64']), logical_name=witness['global_table'])}
    key = (witness['library'].casefold(), 'axis_ref_jec')
    sources = ProjectCallableSources(witness['cpu'], 'PrgAxis1.Program.pou', declarations, {}, library_structures={key: (document,)})
    for name, native_type in witness['native_types']:
        value = sources.structure_value(name)
        assert value['data_type'] == native_type and value['source_stream'] == structure['logical']
    ambiguous = ProjectCallableSources(witness['cpu'], 'PrgAxis1.Program.pou', declarations, {},
        library_structures={key: (document,), ('other', 'axis_ref_jec'): (document,)})
    with pytest.raises(GXWFormatError, match='ambiguous'):
        ambiguous.structure_value('Axis1')
    assert witness['controls'][0]['compile'] == 'completed_accepted'
    assert witness['controls'][0]['public_check'] == 'incomplete'
    assert any(row['code'] == 0x500c2025 for row in witness['controls'][1]['native_errors'])


def test_root_structure_array_v2_edit_sequence_preserves_sources_wires_and_port_directions():
    import re
    from hypothesis import given, settings, strategies as st
    from src.gxw.object_model import read_project_context
    names = ['array', 'index0', 'bounds3', 'bounds-1', 'word-bits', 'core-edit', 'FX0N']
    @settings(max_examples=16, deadline=None, derandomize=True, database=None)
    @given(st.sampled_from(names), st.lists(st.integers(0, 1), min_size=1, max_size=3))
    def check(name, indices):
        context, case = root_structure_array_context(name)
        declarations = {name: doc.raw for name, doc in context.declarations.items()}
        structure = context.sources.structure(case['structure']['logical'].removesuffix('.str')).raw
        wires = [wire.raw for wire in context.program.wires]
        opaque = [record.raw for record in context.program.unknown_records]
        for index in indices:
            model = context.object_model()
            value = case['root_bounds'][0] + index
            for node in model['nodes']:
                if node['symbol'].startswith(case['root_name'] + '['):
                    node['symbol'] = re.sub(r'\[-?[0-9]+\]', '[' + str(value) + ']', node['symbol'], count=1)
            context = read_project_context(generate_object_project(model, baseline=context.raw).data, model['program'])
            current = context.object_model()
            assert current['schema_version'] == 2 and not current['issues']
            assert {name: doc.raw for name, doc in context.declarations.items()} == declarations
            assert context.sources.structure(case['structure']['logical'].removesuffix('.str')).raw == structure
            assert [wire.raw for wire in context.program.wires] == wires
            assert [record.raw for record in context.program.unknown_records] == opaque
            for native in case['native_references']:
                symbol = re.sub(r'\[-?[0-9]+\]', '[' + str(value) + ']', native['name'], count=1)
                nodes = [node for node in current['nodes'] if node['symbol'] == symbol]
                assert nodes and all(node['ports'][0]['data_type'] == native['type'] for node in nodes)
                assert symbol in context.svg()
            blocks = [node for node in current['nodes'] if node['template'] == 'function_block:MODULE_FB']
            assert blocks
            for block in blocks:
                ports = {port['name']: port for port in block['ports']}
                assert (ports['in.STATE']['side'], ports['out.STATE']['side']) == ('in', 'out')
    check()


@pytest.mark.parametrize('change', ['program', 'declaration'])
def test_native_save_metadata_is_preserved_for_noop_and_refreshed_offline_for_edits(change):
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import edit_declarations
    from src.gxw.integrity import inspect_project_integrity
    from src.gxw.project_writer import build_gxw_project
    from tests.test_gxw_structured_reader import offline_fbd_project
    # Original author project, with its actual nonempty integrity table.
    raw, expected = offline_fbd_project(0)
    assert inspect_project_integrity(validate_cfb_streams(raw))['status'] == 'verified'
    source, documents, _ = read_project(raw, expected['source'])
    context = read_project_context(raw, expected['source'])
    assert context.catalog()
    assert build_gxw_project(raw, source).data == raw
    if change == 'program':
        first, *rest = source.nodes
        kwargs = {'programs': replace(source, nodes=(replace(first, symbol=first.symbol + '_EDIT'), *rest))}
    else:
        name = context.sources.local_table
        kwargs = {'declarations': {name: edit_declarations(documents[name], upserts=[
            {'name': 'work', 'data_type': 'WORD'}])}}
    result = build_gxw_project(raw, **kwargs)
    assert result.report['write_backend'] == 'offline'
    assert result.report['compile_state'] == 'stale'
    assert inspect_project_integrity(validate_cfb_streams(result.data))['status'] == 'verified'
    read_project_context(result.data, expected['source']).catalog()


def test_fbd_reader_refuses_a_fabricated_native_integrity_table():
    from src.gxw.container_writer import replace_project_stream
    raw, _ = replace_project_stream(default_baseline(), 'Project.gd2', b'0' * 64)
    with pytest.raises(GXWFormatError, match='native _hdb integrity trailer'):
        read_project_context(raw)


def test_prepared_native_edit_reuses_graph_declaration_binding_without_container_write():
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan, workspace_body
    from src.gxw.project_writer import write_prepared_project
    raw = generate_object_project(two_timers()).data
    context = read_project_context(raw)
    model = context.object_model()
    model['nodes'][0]['symbol'] = '中文_INSTANCE_LONG'
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': '中文_INSTANCE_LONG'}}}
    prepared = prepare_object_project(model, baseline=raw)
    plan = native_source_plan(prepared)
    assert prepared.requires_native_save is False
    assert plan['cpu'] == 'FX3U/FX3UC'
    assert plan['updates'] == [{'before': {'name': 'TIMER_A', 'data_type': 'TON', 'class_code': 1},
                               'after': {'name': '中文_INSTANCE_LONG', 'data_type': 'TON', 'class_code': 1},
                               'initial_before': '', 'initial_after': ''}]
    before, after = prepared.replacements['1.Program.pou'][1:]
    assert base64.b64decode(plan['before_body']) == workspace_body(before)
    assert '中文_INSTANCE_LONG'.encode('utf-16le') in base64.b64decode(plan['after_body'])
    result = write_prepared_project(prepared)
    assert read_project_context(result.data).program.nodes[0].symbol == '中文_INSTANCE_LONG'
    model['nodes'][0]['symbol'] = 'TIMER_A'
    with pytest.raises(GXWFormatError, match='renamed label still has a graph reference'):
        prepare_object_project(model, baseline=raw)


@pytest.mark.parametrize('field,value', [('device', 'M100'), ('initial_value', 'TRUE'), ('comment', 'changed')])
def test_native_source_plan_rejects_declaration_fields_it_cannot_execute(field, value):
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    raw = generate_object_project(two_timers()).data
    model = read_project_context(raw).object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [{'name': 'TIMER_A', field: value}]}}
    with pytest.raises(GXWFormatError, match='devices, initial values or comments'):
        native_source_plan(prepare_object_project(model, baseline=raw))


def test_native_save_cannot_turn_stale_source_or_changed_opaque_data_into_success():
    from src.gxw.container_writer import replace_project_stream, validate_cfb_streams
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import verify_native_save
    raw = generate_object_project(two_timers()).data
    model = read_project_context(raw).object_model()
    model['nodes'][0]['symbol'] = 'RENAMED'
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'RENAMED'}}}
    prepared = prepare_object_project(model, baseline=raw)
    with pytest.raises(GXWFormatError, match='saved native source differs'):
        verify_native_save(prepared, raw, {'status': 'saved'})
    saved = generate_object_project(model, baseline=raw).data
    assert verify_native_save(prepared, saved, {}).report['validation']['gxworks_compile'] == 'not_run'
    outer = validate_cfb_streams(saved)
    declarations = read_project_context(saved).declarations['1.Labels.lh']
    damaged = replace(declarations, rows=(replace(declarations.rows[0], unknown_u32=9), *declarations.rows[1:]))
    from src.gxw.declarations import serialize_declarations
    from src.gxw.project_metadata import logical_mapping
    physical = logical_mapping(outer['projectdatalist.xml'])['1.Labels.lh']
    hdb, _ = replace_project_stream(outer['_hdb'], physical, serialize_declarations(damaged))
    corrupted, _ = replace_project_stream(saved, '_hdb', hdb)
    with pytest.raises(GXWFormatError, match='saved native declaration fields differ'):
        verify_native_save(prepared, corrupted, {})


@pytest.mark.parametrize('kind,labels,expected', [(1, 0, 'simple'), (1, 1, 'simple'), (2, 1, 'structured')])
def test_saved_project_settings_distinguish_simple_labels_from_structured(kind, labels, expected):
    import struct
    from src.gxw.offline_project import read_offline_project
    from src.gxw.project_metadata import read_project_text_context, read_project_settings
    project = read_offline_project(default_baseline())
    metadata, = [raw for name, raw in project.streams.items() if name.endswith('.prj')]
    context = read_project_text_context(metadata)
    offset = context['codepage_offset'] - 4
    metadata = metadata[:offset] + struct.pack('<I', kind) + metadata[offset + 4:]
    def counted(value):
        raw = (value + '\0').encode('utf-16le')
        return struct.pack('<I', len(raw) // 2) + raw
    info = counted('') + counted(context['cpu']) + struct.pack('<II', kind, labels) + b'preserved tail'
    assert read_project_settings(metadata, info) == dict(native_project_type=expected, use_labels=bool(labels))
    assert read_project_settings(metadata, None) == dict(native_project_type=expected, use_labels=None)
    assert read_project_settings(metadata, info[:-len(b'preserved tail')]) == read_project_settings(metadata, info)
    with pytest.raises(GXWFormatError, match='project type differs'):
        read_project_settings(metadata, counted('') + counted(context['cpu']) + struct.pack('<II', 3 - kind, labels))
    with pytest.raises(GXWFormatError, match='CPU differs'):
        read_project_settings(metadata, counted('') + counted('other CPU') + struct.pack('<II', kind, labels))
    with pytest.raises(GXWFormatError, match='truncated project type'):
        read_project_settings(metadata, info[:len(info) - len(b'preserved tail') - 1])
    unknown = counted('') + counted(context['cpu']) + struct.pack('<II', kind, 2)
    assert read_project_settings(metadata, unknown)['use_labels'] is None


def test_native_save_metadata_update_retains_the_actual_vendor_candidate_and_requested_edit():
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.native_write import verify_native_save
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_metadata import logical_mapping, read_project_text_context
    witness = json.loads((private_path('tests/fixtures/gxworks2_native_save_metadata_change.json')).read_text(encoding='utf-8'))
    original = zlib.decompress(base64.b64decode(witness['original_gxw_zlib_base64']))
    candidate = zlib.decompress(base64.b64decode(witness['saved_gxw_zlib_base64']))
    prepared = prepare_object_project(witness['draft_model'], baseline=original)
    result = verify_native_save(prepared, candidate, witness['native_observation'])
    assert result.data == candidate
    assert result.report['validation']['native_save'] == 'passed'
    assert result.report['validation']['source_body'] == 'exact'
    assert result.report['validation']['declarations'] == 'passed'
    assert result.report['validation']['unedited_sources'] == 'preserved'
    assert result.report['validation']['unedited_configuration'] == 'changed'
    assert result.report['validation']['gxworks_compile'] == 'not_run'
    change, = result.report['unedited_object_changes']
    assert (change['object'], change['kind'], change['old_length'], change['new_length']) == (
        'CPU_MATRIX.prj', 'configuration', 518, 522)
    assert change['binary_changes'] == [dict(old_offset=172, new_offset=172, old_length=4, new_length=8,
        before_hex='01000000', after_hex='0200000000000710')]
    metadata = []
    for raw in (original, candidate):
        outer = validate_cfb_streams(raw)
        mapping = logical_mapping(outer['projectdatalist.xml'])
        nested = validate_cfb_streams(outer['_hdb'])
        metadata.append(nested[mapping['CPU_MATRIX.prj']])
        context = read_project_text_context(metadata[-1])
        assert (context['cpu'], context['codepage']) == ('FX3U/FX3UC', 1252)
    assert metadata[0][:172] == metadata[1][:172] and metadata[0][176:] == metadata[1][180:]
    context = read_project_context(result.data, witness['program'])
    instances = [row for row in context.object_model()['nodes'] if row['symbol'] in ('MODEL_STAGE_ALPHA', 'MODEL_STAGE_BETA')]
    assert [(row['symbol'], row['block']) for row in instances] == [('MODEL_STAGE_ALPHA', 0), ('MODEL_STAGE_BETA', 2)]
    assert len(context.program.blocks) == 3
    assert {'MODEL_STAGE_ALPHA', 'MODEL_STAGE_BETA'} <= {
        row.name for row in context.declarations[context.sources.local_table].rows}


def test_native_rename_plan_property_preserves_independent_formals_and_source_bytes():
    hypothesis = pytest.importorskip('hypothesis')
    from hypothesis import strategies as st
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    from src.gxw.native_write import verify_native_save
    from src.gxw.native_diagnostics import native_validation_source_plan
    from src.gxw.editor import edit_draft
    from src.gxw.models import UnknownRecord
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.structured_pou_writer import serialize_structured_pou
    import struct
    raw = source_binding_baseline()
    source, _, _ = read_project(raw)
    opaque = UnknownRecord(max(r.offset for r in source.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    raw = build_gxw_project(raw, replace(source, unknown_records=(opaque,),
        record_count=source.record_count + 1)).data
    # Frozen native formals are independent of the edited model/plan.
    _, _, _, witness = source_binding_witness()
    oracle = next(n['interface'] for n in witness['final_source_view']['nodes'] if 'interface' in n)

    @hypothesis.settings(max_examples=50, deadline=None, derandomize=True)
    @hypothesis.example('程序', '标签', '中文实例', 7)
    @hypothesis.given(
        st.text(alphabet='abcXYZ程序_-', min_size=1, max_size=12),
        st.text(alphabet='abcXYZ标签_-', min_size=1, max_size=12),
        st.text(alphabet='abcXYZ中文实例_', min_size=1, max_size=32),
        st.integers(min_value=1, max_value=10000))
    def check(source_child, label_child, suffix, saved_folder):
        program_name, local = '1.' + source_child + '.pou', '1.' + label_child + '.lh'
        variant = project_rows_variant(raw, {'1.Program.pou': {'szName': program_name},
                                             '1.Labels.lh': {'szName': local}})
        context = read_project_context(variant)
        original = context.object_model()
        fb = next(n for n in original['nodes'] if n['template'].startswith('function_block:'))
        assert context.sources.local_table == local
        assert context.sources.label(fb['symbol'])[0] == local
        renamed = 'PROP_' + suffix
        model = edit_draft(original, {'action': 'update_node', 'id': fb['id'],
            'field': 'symbol', 'value': renamed}, context=context)['model']
        assert set(model['declaration_edits']) == {local}
        assert model['declaration_edits'][local]['renames'] == {fb['symbol']: renamed}
        prepared = prepare_object_project(model, baseline=variant)
        plan = native_source_plan(prepared)
        assert plan['updates'][0]['after']['name'] == renamed
        validation_plan = native_validation_source_plan(variant)
        assert validation_plan['programs'][0]['name'] == '1'
        assert validation_plan['programs'][0]['body'] == plan['before_body']
        rebuilt = prepared.programs[context.program.logical_name]
        node = next(n for n in rebuilt.nodes if n.symbol == renamed)
        sources = context.sources.with_declarations({**context.declarations, **prepared.declarations})
        ports = sources.callable(node)['ports']
        assert [(p['formal_name'], p['class_code'], p['data_type'], p['side']) for p in ports] == [
            (p['name'], p['class_code'], p['declared_type'], p['side']) for p in oracle['ports']]
        assert [r.raw for r in rebuilt.unknown_records] == [opaque.raw]
        assert [wire.raw for wire in rebuilt.wires] == [wire.raw for wire in context.program.wires]
        assert base64.b64decode(plan['after_body']) in serialize_structured_pou(rebuilt)
        assert context.object_model() == original
        saved = generate_object_project(model, baseline=variant).data
        # The saved-name transition is an observed vendor operation. This
        # directory-only control is not native compilation/acceptance evidence.
        normalized = project_rows_variant(saved, {
            program_name: {'szName': '1.Program.pou', 'uiFolderNo': str(saved_folder)},
            local: {'szName': '1.Labels.lh', 'uiFolderNo': str(saved_folder)}})
        result = verify_native_save(prepared, normalized, {})
        assert result.report['validation']['unedited_sources'] == 'preserved'
        after = read_project_context(normalized)
        assert after.program.logical_name == '1.Program.pou'
        assert after.sources.label(renamed)[0] == '1.Labels.lh'
        assert after.declarations['1.Labels.lh'].rows[1].raw == context.declarations[local].rows[1].raw
        block = next(n for n in after.program.nodes if n.symbol == renamed)
        assert [(p['formal_name'], p['class_code'], p['data_type'], p['side'])
                for p in after.sources.callable(block)['ports']] == [
            (p['name'], p['class_code'], p['declared_type'], p['side']) for p in oracle['ports']]
        assert [r.raw for r in after.program.unknown_records] == [opaque.raw]
        before_payloads = validate_cfb_streams(validate_cfb_streams(variant)['_hdb'])
        after_payloads = validate_cfb_streams(validate_cfb_streams(normalized)['_hdb'])
        edited_streams = {prepared.mapping[program_name], prepared.mapping[local]}
        assert {k: v for k, v in before_payloads.items() if k not in edited_streams} == {
            k: v for k, v in after_payloads.items() if k not in edited_streams}

    check()


@pytest.mark.parametrize('damage', ['duplicate_role', 'different_folder', 'missing_role', 'different_owner'])
def test_project_declaration_binding_never_falls_back_from_invalid_native_groups(damage):
    from src.gxw.object_model import read_project_context
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import project_source_groups
    from src.gxw.native_diagnostics import native_validation_source_plan
    raw = source_binding_shadow_baseline(hiding=True)
    groups, _ = project_source_groups(validate_cfb_streams(raw)['projectdatalist.xml'])
    patch = {
        'duplicate_role': {'1.Program.pou': {'ucFileType': '1'}},
        'different_folder': {'1.Labels.lh': {'uiFolderNo': str(groups['1.Program.pou']['folder'] + 1)}},
        'missing_role': {'1.Labels.lh': {'ucFileType': '9'}},
        'different_owner': {'1.Labels.lh': {'szName': 'OTHER.Labels.lh'}},
    }[damage]
    variant = project_rows_variant(raw, patch)
    context = read_project_context(variant)
    assert context.sources.local_table is None
    assert context.sources.label('TIMER_A') is None
    assert any(issue['code'] == 'project_source_owner_gap' for issue in context.sources.issues)
    assert any(issue['code'] == 'callable_source_gap' for issue in context.object_model()['issues'])
    with pytest.raises(GXWFormatError):
        native_validation_source_plan(variant)


@pytest.mark.parametrize('damage', ['source_folder', 'declaration_role', 'foreign_child', 'foreign_configuration'])
def test_native_child_normalization_cannot_replace_registered_objects(damage):
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import verify_native_save
    raw = source_binding_baseline()
    prepared = prepare_object_project(read_project_context(raw).object_model(), baseline=raw)
    patch = {
        'source_folder': {'1.Program.pou': {'uiFolderNo': '987'}},
        'declaration_role': {'1.Labels.lh': {'ucFileType': '9'}},
        'foreign_child': {'1.Program.pou': {'szName': 'OTHER.Program.pou'}},
        'foreign_configuration': {'Global1.gh': {'szName': 'OTHER.gh'}},
    }[damage]
    with pytest.raises(GXWFormatError, match='registered project object identities|unverified project object name'):
        verify_native_save(prepared, project_rows_variant(raw, patch), {})


def test_native_row_lineage_does_not_use_shared_saved_record_id():
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    from src.gxw.project_writer import build_gxw_project
    raw = generate_object_project(two_timers()).data
    context = read_project_context(raw)
    table = '1.Labels.lh'
    from src.gxw.declarations import edit_declarations
    document = edit_declarations(context.declarations[table], upserts=[
        {'name': 'BOOL_A', 'data_type': 'BOOL'}, {'name': 'BOOL_B', 'data_type': 'BOOL'}])
    document = replace(document, rows=tuple(replace(r, record_id=0) if r.name.startswith('BOOL_') else r
                                          for r in document.rows))
    raw = build_gxw_project(raw, declarations={table: document}).data
    context = read_project_context(raw)
    model = context.object_model()
    model['declaration_edits'] = {table: {'renames': {'BOOL_A': 'BOOL_RENAMED'}, 'remove': ['BOOL_B']}}
    prepared = prepare_object_project(model, baseline=raw)
    plan = native_source_plan(prepared)
    assert [r['before']['name'] for r in plan['updates']] == ['BOOL_A']
    assert [r['name'] for r in plan['remove']] == ['BOOL_B']
    assert [r.name for r in prepared.declarations[table].rows] == ['TIMER_A', 'TIMER_B', 'BOOL_RENAMED']


def source_binding_shadow_baseline(*, hiding=True):
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import edit_declarations
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    from src.gxw.project_writer import build_gxw_project
    raw = source_binding_baseline()
    _, declarations, _ = read_project(raw)
    global_doc = edit_declarations(declarations['Global1.gh'], upserts=[
        {'name': 'TIMER_A', 'data_type': 'BOOL', 'class_name': 'VAR_GLOBAL',
         'device': 'Y2', 'iec_address': '%QX2'}])
    raw = build_gxw_project(raw, declarations={'Global1.gh': global_doc}).data
    witness = json.loads((private_path('tests/fixtures/gxw_fbd_source_library.json')).read_text())['label_hiding_native']
    control = next(r for r in witness['controls'] if r['global_variable_hiding'] is hiding)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    name = next(name for name in mapping if name.endswith('.prj'))
    value = base64.b64decode(control['metadata_base64'])
    hdb, _ = replace_project_stream(outer['_hdb'], mapping[name], value)
    history, _, _ = synchronize_history(outer['history.xml'], {name: (mapping[name], inner[mapping[name]], value)})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return raw


@pytest.mark.parametrize('hiding', [False, True])
def test_saved_native_hiding_option_controls_fb_source_binding_without_changing_declarations(hiding):
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline(hiding=hiding)
    context = read_project_context(raw)
    model = context.object_model()
    fb = next(n for n in model['nodes'] if n['symbol'] == 'TIMER_A')
    assert context.sources.global_variable_hiding is hiding
    assert [(r.name, r.data_type) for r in context.declarations['Global1.gh'].rows] == [('TIMER_A', 'BOOL')]
    if hiding:
        assert [p['name'] for p in fb['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
        assert context.sources.label('timer_a')[0] == '1.Labels.lh'
        assert generate_object_project(model, baseline=raw).data == raw
    else:
        assert all('formal_name' not in p for p in fb['ports'])
        with pytest.raises(GXWFormatError, match='ambiguous'):
            context.sources.label('TIMER_A')


@pytest.mark.parametrize('target', ['instance', 'local', 'hidden-global'])
def test_shadowed_label_rename_keeps_the_graph_and_the_other_scope_on_their_original_declaration(target):
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline()
    context = read_project_context(raw)
    model = context.object_model()
    fb = next(n for n in model['nodes'] if n['symbol'] == 'TIMER_A')
    name = '混合范围实例_STAGE_A'
    command = ({'action': 'update_node', 'id': fb['id'], 'field': 'symbol', 'value': name}
               if target == 'instance' else {'action': 'update_label', 'table':
               'Global1.gh' if target == 'hidden-global' else '1.Labels.lh',
               'name': 'TIMER_A', 'field': 'name', 'value': name})
    edited = edit_draft(model, command, context=context, templates=context.catalog())['model']
    after = read_project_context(generate_object_project(edited, baseline=raw).data)
    local_name = 'TIMER_A' if target == 'hidden-global' else name
    global_name = name if target == 'hidden-global' else 'TIMER_A'
    assert after.program.nodes[0].symbol == local_name
    assert after.declarations['1.Labels.lh'].rows[0].name == local_name
    assert [(r.name, r.data_type) for r in after.declarations['Global1.gh'].rows] == [(global_name, 'BOOL')]
    assert [p['name'] for p in after.object_model()['nodes'][0]['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    assert model['nodes'][0]['symbol'] == 'TIMER_A'


def test_hidden_global_edit_cannot_leave_a_renamed_local_fb_pointing_at_the_global_bool():
    from src.gxw.object_model import read_project_context
    context = read_project_context(source_binding_shadow_baseline())
    model = context.object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'LOCAL_NEXT'}}}
    with pytest.raises(GXWFormatError):
        generate_object_project(model, baseline=context.raw)
    model = context.object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': 'TIMER_A', 'data_type': 'BOOL', 'kind': 'variable'}]}}
    with pytest.raises(GXWFormatError):
        generate_object_project(model, baseline=context.raw)


@pytest.mark.parametrize('unrelated', ['范围TIMER_A', 'TIMER_A变', 'A_TIMER_A'])
def test_scoped_fb_rename_preserves_an_independent_unicode_identifier_containing_the_old_name(unrelated):
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline()
    context = read_project_context(raw)
    model = context.object_model()
    terminal = next(node for node in model['nodes'] if node['template'] == 'input')
    terminal['symbol'] = unrelated
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': unrelated, 'data_type': 'BOOL', 'class_name': 'VAR'}]}}
    raw = generate_object_project(model, baseline=raw).data
    context = read_project_context(raw)
    edited = edit_draft(context.object_model(), {'action': 'update_label', 'table': '1.Labels.lh',
        'name': 'TIMER_A', 'field': 'name', 'value': 'LOCAL_'}, context=context)['model']
    final = read_project_context(generate_object_project(edited, baseline=raw).data)
    assert any(node.symbol == unrelated for node in final.program.nodes)
    assert any(row.name == unrelated and row.data_type == 'BOOL' for row in final.declarations['1.Labels.lh'].rows)
    assert final.program.nodes[0].symbol == 'LOCAL_'
    assert any(row.name == 'TIMER_A' and row.data_type == 'BOOL' for row in final.declarations['Global1.gh'].rows)


@pytest.mark.parametrize("case", ["s", "fn", "f2", "t2c"])
def test_import_and_unchanged_object_export_preserve_every_project_byte(case):
    raw = baseline(case)
    p, labels, _ = read_project(raw)
    model = export_object_model(p, labels)
    assert generate_object_project(model, baseline=raw).data == raw
    ET.fromstring(render_structured_svg(p))


def two_timers():
    return {"schema_version": 1, "program": "1.Program.pou", "canvas_height": 10,
            "nodes": [
                {"id": "a", "template": "function_block:TON", "symbol": "TIMER_A", "x": 8, "y": 2},
                {"id": "b", "template": "function_block:TON", "symbol": "TIMER_B", "x": 21, "y": 2},
                {"id": "x", "template": "input", "symbol": "X0", "x": 6, "y": 3},
                {"id": "pt1", "template": "input", "symbol": "T#1s", "x": 6, "y": 4},
                {"id": "pt2", "template": "input", "symbol": "T#2s", "x": 19, "y": 4},
                {"id": "out", "template": "output", "symbol": "Y0", "x": 26, "y": 3},
            ], "wires": [{"start": [1, 0], "end": [1, 10]}, {"from": "a.Q", "to": "b.IN"}]}


def test_named_port_link_creates_native_wire_and_local_fb_bindings():
    result = generate_object_project(two_timers())
    p, labels, _ = read_project(result.data)
    net, = fb_connectivity_model(p)["nets"]
    assert net["connection"] == "wire_network"
    assert (net["sources"][0]["instance"], net["sinks"][0]["instance"]) == ("TIMER_A", "TIMER_B")
    assert [(r.name, r.type_reference) for r in labels['1.Labels.lh'].rows] == [("TIMER_A", "TON"), ("TIMER_B", "TON")]


@pytest.mark.parametrize('order', list(permutations(range(4))))
def test_project_callable_formals_follow_native_saved_numbers_after_display_reorder(order):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    witness = json.loads((private_path('tests/fixtures/gxw_fbd_source_library.json')).read_text())['reordered_project_formals_native']
    document = parse_declarations(base64.b64decode(witness['declarations_base64']), logical_name=witness['logical_name'])
    reordered = replace(document, rows=tuple(document.rows[i] for i in order))
    sources = ProjectCallableSources(witness['cpu'], 'AUTHOR_FBD.Program.pou',
                                    {witness['logical_name']: reordered}, {})
    interface = sources.fixed_interface('FB_DOUBLE_CLICK')
    assert [[r['name'], r['declared_type'], r['class_code']] for r in interface['inputs']] == witness['native_input_formals']
    assert [[r['name'], r['declared_type'], r['class_code']] for r in interface['outputs']] == witness['native_output_formals']
    assert [r['source_offset'] for r in interface['inputs']] == [412, 114]
    assert [r.name for r in document.rows] == witness['displayed_order']


def test_project_callable_cannot_guess_a_formal_order_from_duplicate_saved_numbers():
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    witness = json.loads((private_path('tests/fixtures/gxw_fbd_source_library.json')).read_text())['reordered_project_formals_native']
    document = parse_declarations(base64.b64decode(witness['declarations_base64']), logical_name=witness['logical_name'])
    document = replace(document, rows=tuple(replace(r, record_id=1) if r.name == 'PERIOD' else r for r in document.rows))
    sources = ProjectCallableSources(witness['cpu'], 'AUTHOR_FBD.Program.pou',
                                    {witness['logical_name']: document}, {})
    with pytest.raises(GXWFormatError, match='ambiguous saved declaration order'):
        sources.fixed_interface('FB_DOUBLE_CLICK')


FORMAL_ORDER_NATIVE = json.loads((private_path('tests/fixtures/gxw_formal_order_native.json')).read_text(encoding='utf-8'))['cases']


def assert_native_formal_order(case, row_order=None):
    from src.gxw.callable_sources import interface
    from src.gxw.declarations import parse_declarations
    from src.gxw.structured_pou import parse_structured_pou
    document = parse_declarations(base64.b64decode(case['declarations_base64']), logical_name=case['logical_name'])
    assert document.owner_pou_type == 0x1000002
    if row_order is not None:
        document = replace(document, rows=tuple(document.rows[i] for i in row_order))
    definition = {'source_kind': 'project_labels', 'declarations': document,
                  'name': document.owner_name, 'return_type_raw': document.owner_return_type}
    if case['core_issue']:
        # Native acceptance does not provide an unambiguous offline ordering
        # rule for tied numbers within the same formal group.
        assert case['native_compile'] == 'completed_accepted'
        with pytest.raises(GXWFormatError, match=case['core_issue']):
            interface(definition)
        return
    inputs, outputs = interface(definition)
    assert [r['name'] for r in inputs] == case['inputs']
    assert [r['name'] for r in outputs] == case['outputs']
    assert [(r['class_code'], r['type_code']) for r in inputs] == [(5, 1)]
    assert [(r['class_code'], r['type_code']) for r in outputs] == [(4, 1), (4, 1), (5, 1)]
    # Port counts/ordinates come from actual saved FBD bytes, independently of
    # the declaration ordering implementation.
    source = parse_structured_pou(base64.b64decode(case['caller_source_base64']), logical_name='FB_HYST.Program\\UtilsV5.lnb')
    node = next(n for n in source.nodes if n.symbol == 'fbNestedCooling_Control')
    assert [(p.local_x, p.local_y) for p in node.ports] == [(0, 2), (5, 2), (5, 3), (5, 4)]
    assert [r.type_code for r in document.rows if r.name in ('Z_First_Output', 'A_Second_Output')] == [0, 0]


@pytest.mark.parametrize('case', FORMAL_ORDER_NATIVE, ids=lambda c: c['name'])
def test_native_formal_groups_share_numbers_without_cross_group_ambiguity(case):
    assert_native_formal_order(case)


def test_native_formal_group_order_is_independent_of_displayed_row_permutations():
    from hypothesis import given, settings, strategies as st

    @settings(max_examples=80, deadline=None, derandomize=True, database=None)
    @given(case_index=st.integers(0, len(FORMAL_ORDER_NATIVE) - 1), row_order=st.permutations(tuple(range(5))))
    def check(case_index, row_order):
        assert_native_formal_order(FORMAL_ORDER_NATIVE[case_index], row_order)

    check()


@pytest.mark.parametrize('index', range(29))
def test_source_ports_match_frozen_native_formals_and_preserve_source_geometry(index):
    from src.gxw.structured_pou_writer import serialize_structured_pou
    from src.gxw.semantic import build_semantic_model
    program, declarations, sources, case = source_binding_witness(index)
    model = export_object_model(program, declarations, sources=sources)
    assert serialize_structured_pou(build_object_program(program, model, sources=sources)) == program.raw
    by_offset = {node.offset: node for node in program.nodes}
    for node in case['final_source_view']['nodes']:
        if 'interface' not in node:
            continue
        ports = sources.callable(by_offset[node['offset']])['ports']
        assert len({port['name'] for port in ports}) == len(ports)
        for expected in node['interface']['ports']:
            actual = ports[expected['index']]
            assert (actual['formal_name'], actual['side'], actual['data_type'], actual['class_code'], actual['occurrence']) == (
                expected['name'], expected['side'], expected['declared_type'], expected['class_code'], expected['occurrence'])
    semantics = build_semantic_model(program, function_block_instances=sources.semantic_specs(program))
    for block in semantics.function_blocks:
        assert block.type_known
        assert all(port.formal_name is not None for port in block.ports)


@pytest.mark.parametrize('case', json.loads((private_path('tests/fixtures/gxw_fbd_library_cpu_sections_native.json')).read_text())['cases'],
    ids=lambda case: case['cpu'])
def test_native_cpu_name_selects_its_source_section_without_family_fallback(case):
    from src.gxw.callable_sources import ProjectCallableSources
    template = ('(*$SECTION:{section}*)\nFUNCTION_BLOCK CPU_BOUND\n'
        'VAR_INPUT\n{formal}: {data_type};\nEND_VAR\nEND_FUNCTION_BLOCK\n')
    library = (template.format(section=case['section'],formal='MATCHED',data_type='BOOL') +
        template.format(section='UNRELATED_CPU',formal='WRONG',data_type='WORD')).encode()
    sources = ProjectCallableSources(case['cpu'],'MAIN.Program.pou',{}, {'cpu.lif':library})
    formals = sources.fixed_interface('CPU_BOUND')
    assert [(p['name'],p['declared_type']) for p in formals['inputs']] == [('MATCHED','BOOL')]
    unknown = ProjectCallableSources(case['cpu'] + '_UNTESTED','MAIN.Program.pou',{}, {'cpu.lif':library})
    with pytest.raises(GXWFormatError,match='missing or ambiguous'):
        unknown.fixed_interface('CPU_BOUND')


@pytest.mark.parametrize('endpoint', ['in.STATE', 'out.STATE'])
def test_source_inout_endpoints_bind_distinct_native_sides(endpoint):
    from src.gxw.connectivity import build_connectivity_graph
    program, declarations, sources, _ = source_binding_witness()
    model = export_object_model(program, declarations, sources=sources)
    fb = model['nodes'][0]
    selected = next(port for port in fb['ports'] if port['name'] == endpoint)
    model['nodes'].append({'id': 'target', 'template': 'output', 'symbol': 'D30',
                           'x': fb['x'] + selected['x'], 'y': fb['y'] + selected['y'] - 1})
    model['wires'].append({'from': fb['id'] + '.' + endpoint, 'to': 'target.IN'})
    result = build_object_program(program, model, sources=sources)
    target = next(node for node in result.nodes if node.symbol == 'D30')
    index = fb['ports'].index(selected)
    graph = build_connectivity_graph(result)
    assert graph.ports_connected(fb['source_offset'], index, target.offset, 0)
    assert not graph.ports_connected(fb['source_offset'], 3 if index == 1 else 1, target.offset, 0)
    model['wires'][-1]['from'] = fb['id'] + '.STATE'
    with pytest.raises(GXWFormatError, match='unknown wire endpoint'):
        build_object_program(program, model, sources=sources)


def test_source_bound_instance_rename_is_atomic_and_foreign_type_edit_is_rejected():
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.editor import edit_draft
    raw = source_binding_baseline()
    program, declarations, _ = read_project(raw)
    sources = ProjectCallableSources.from_project(raw, program.logical_name, declarations)
    original = export_object_model(program, declarations, sources=sources)
    assert generate_object_project(original, baseline=raw).data == raw
    renamed = edit_draft(original, {'action': 'update_label', 'table': '1.Labels.lh', 'name': 'TIMER_A',
                                   'field': 'name', 'value': 'SEQUENCE_DELAY_STAGE_A'})['model']
    assert original['nodes'][0]['symbol'] == 'TIMER_A'
    changed = generate_object_project(renamed, baseline=raw).data
    after, labels, _ = read_project(changed)
    assert after.nodes[0].symbol == 'SEQUENCE_DELAY_STAGE_A'
    assert [row.name for row in labels['1.Labels.lh'].rows] == ['SEQUENCE_DELAY_STAGE_A', 'TIMER_B']
    label_only = deepcopy(original)
    label_only['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'NEW_NAME'}}}
    with pytest.raises(GXWFormatError, match='update graph and declaration together'):
        generate_object_project(label_only, baseline=raw)
    wrong_type = deepcopy(original)
    wrong_type['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': 'TIMER_A', 'kind': 'function_block', 'data_type': 'TON'}]}}
    with pytest.raises(GXWFormatError, match='same instance name and type'):
        generate_object_project(wrong_type, baseline=raw)


@pytest.mark.parametrize('type_code,type_reference', [(15, 'TON'), (0, '')])
def test_current_declared_fb_type_binds_ports_with_retained_native_type_fields(type_code, type_reference):
    from src.gxw.declarations import parse_declarations, serialize_declarations, edit_declarations
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import ProjectSourceContext
    program, declarations, sources, _ = source_binding_witness()
    table = '1.Labels.lh'
    original = declarations[table]
    # Native SetVariableDataType and cold save retain the prior reference even
    # though the current declaration and graph both use the replacement FB.
    retained = replace(original, rows=tuple(
        replace(row, type_code=type_code, type_reference=type_reference) if row.name == 'TIMER_A' else row
        for row in original.rows))
    reread = parse_declarations(serialize_declarations(retained), logical_name=table)
    current_docs = {**declarations, table: reread}
    current = sources.with_declarations(current_docs)
    context = ProjectSourceContext(b'', program, current_docs, [program.logical_name], current)
    model = context.object_model()
    assert [p['name'] for p in model['nodes'][0]['ports']] == [
        'SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    assert not any(issue['code'] in ('callable_source_gap', 'unknown_function_block_type')
                   for issue in model['issues'])
    assert model['labels'][table][0]['kind'] == 'function_block'
    draft = edit_draft(model, {'action': 'update_label', 'table': table, 'name': 'TIMER_A',
        'field': 'name', 'value': 'FLOW_RENAMED'}, context=context)['model']
    updated = edit_declarations(reread, renames={'TIMER_A': 'FLOW_RENAMED'})
    pending = current.with_declarations({**current_docs, table: updated})
    rebuilt = build_object_program(program, draft, sources=pending, original_sources=current)
    assert rebuilt.nodes[0].symbol == 'FLOW_RENAMED'
    assert [p['name'] for p in pending.callable(rebuilt.nodes[0])['ports']] == [
        'SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    saved = parse_declarations(serialize_declarations(updated), logical_name=table)
    assert saved.rows[0].data_type == 'FLOW_PORTS'
    assert saved.rows[0].type_reference == type_reference
    assert saved.rows[0].type_code == type_code
    assert saved.rows[1].raw == reread.rows[1].raw
    from src.gxw.project_writer import _bind_function_blocks
    rebound = _bind_function_blocks({program.logical_name: program}, {table: reread}, {}, {})
    assert rebound[table] == reread
    # Type editing uses the resolved FB kind even when native bookkeeping is
    # uninitialized; callers still have to change the graphic interface too.
    typed = edit_draft(model, {'action': 'update_label', 'table': table, 'name': 'TIMER_A',
        'field': 'data_type', 'value': 'TON'}, context=context)['model']
    assert typed['declaration_edits'][table]['upserts'] == [
        {'name': 'TIMER_A', 'kind': 'function_block', 'data_type': 'TON'}]


def test_verified_source_prototype_adds_a_custom_fb_and_synchronizes_its_label():
    from src.gxw.callable_sources import ProjectCallableSources
    raw = source_binding_baseline()
    program, declarations, _ = read_project(raw)
    sources = ProjectCallableSources.from_project(raw, program.logical_name, declarations)
    model = export_object_model(program, declarations, sources=sources)
    prototype = model['nodes'][0]
    model['nodes'].append({'id': 'clone', 'template': prototype['template'],
        'prototype_offset': prototype['source_offset'], 'symbol': 'TIMER_C', 'x': 35, 'y': 2})
    clone = next(item for item in catalog_description(sources=sources, program=program)
                 if item.get('prototype_offset') == prototype['source_offset'])
    assert [port['name'] for port in clone['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    result = generate_object_project(model, baseline=raw)
    after, labels, _ = read_project(result.data)
    assert after.nodes[-1].type_name == 'FLOW_PORTS'
    assert [(row.name, row.type_reference) for row in labels['1.Labels.lh'].rows][-1] == ('TIMER_C', 'FLOW_PORTS')
    model['nodes'][-1].pop('prototype_offset')
    generated, generated_labels, _ = read_project(generate_object_project(model, baseline=raw).data)
    generic = generated.nodes[-1]
    assert generic.type_name == 'FLOW_PORTS'
    assert [port.port_kind_code for port in generic.ports] == [1, 1, 0, 0]
    assert [(row.name, row.type_reference) for row in generated_labels['1.Labels.lh'].rows][-1] == ('TIMER_C', 'FLOW_PORTS')


@pytest.mark.parametrize('endpoint', ['in.STATE', 'out.STATE'])
def test_project_fb_interface_comes_from_owner_kind_and_formal_rows_without_implicit_enable(endpoint):
    import struct
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import _string, parse_declarations, edit_declarations, serialize_declarations
    from tests.test_gxw_declarations import document
    base = document('d0')
    owner_end = 54 + len(_string(base.owner_name))
    raw = (base.raw[:54] + _string('LOCAL_FLOW') + struct.pack('<II',0x1000002,1)
           + base.raw[owner_end+8:])
    definition = parse_declarations(raw, logical_name='LOCAL_FLOW.Labels.lh')
    definition = edit_declarations(definition, upserts=[
        {'name':'SIGNAL','data_type':'BOOL','class_name':'VAR_INPUT'},
        {'name':'NUMBER','data_type':'STRING[20]','class_name':'VAR_OUTPUT'},
        {'name':'STATE','data_type':'WORD','class_name':'VAR_IN_OUT'},
        {'name':'WORK','data_type':'ARRAY [0..3] OF INT'}])
    definition = parse_declarations(serialize_declarations(definition),logical_name=definition.logical_name)
    source, declarations, _ = read_project(default_baseline())
    context = ProjectCallableSources('Q03UDV',source.logical_name,
        {**declarations, definition.logical_name:definition},{})
    model = export_object_model(source,declarations,sources=context)
    model['nodes'] = [{'id':'flow','template':'function_block:LOCAL_FLOW','symbol':'FLOW_A','x':8,'y':2}]
    model['wires'] = []
    rebuilt = build_object_program(source,model,sources=context)
    ports = context.callable(rebuilt.nodes[0],bind_instance=False)['ports']
    assert [(p['name'],p['side'],p['data_type'],p['class_code'],p['negated']) for p in ports] == [
        ('SIGNAL','in','BOOL',3,False),('in.STATE','in','WORD',5,False),
        ('out.STATE','out','WORD',5,False),('NUMBER','out','STRING[20]',4,False)]
    changed = edit_declarations(definition,renames={'STATE':'NEXT_STATE'})
    updated = context.with_declarations({**context.declarations,definition.logical_name:changed})
    assert [p['name'] for p in updated.callable(rebuilt.nodes[0],bind_instance=False)['ports']] == [
        'SIGNAL','in.NEXT_STATE','out.NEXT_STATE','NUMBER']
    # The source fields stay read-only while new named connections resolve
    # against pending formals, before a candidate is saved and reread.
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    from src.gxw.connectivity import build_connectivity_graph
    caller = edit_declarations(declarations['1.Labels.lh'], upserts=[
        {'name':'FLOW_A','data_type':'LOCAL_FLOW','kind':'function_block'}])
    source = parse_structured_pou(serialize_structured_pou(rebuilt), logical_name=source.logical_name)
    current = context.with_declarations({**context.declarations,'1.Labels.lh':caller})
    model = export_object_model(source,current.declarations,sources=current)
    target = model['nodes'][0]
    port = next(p for p in target['ports'] if p['name']=='out.STATE')
    model['nodes'].append({'id':'state_out','template':'output','symbol':'D30',
                          'x':target['x']+port['x']+1,'y':target['y']+port['y']-1})
    model['wires'] = [{'from':target['id']+'.out.NEXT_STATE','to':'state_out.IN'}]
    pending = current.with_declarations({**current.declarations,definition.logical_name:changed})
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import ProjectSourceContext
    draft_context = ProjectSourceContext(b'', source, current.declarations, [source.logical_name], current)
    original_draft = deepcopy(model)
    original_draft['wires'] = []
    port_edit = edit_draft(original_draft, {'action': 'update_port', 'id': target['id'],
        'port': endpoint, 'negated': True}, context=draft_context)['model']
    wired = edit_draft(port_edit, {'action': 'add_wire', 'from': target['id'] + '.' + endpoint,
        'to': 'state_out.IN'}, context=draft_context)['model']
    rebound = edit_draft(wired, {'action': 'update_label', 'table': definition.logical_name,
        'name': 'STATE', 'field': 'name', 'value': 'NEXT_STATE'}, context=draft_context)
    renamed_endpoint = endpoint.replace('STATE', 'NEXT_STATE')
    assert rebound['model']['nodes'][0]['port_edits'] == {renamed_endpoint: {'negated': True}}
    assert rebound['model']['wires'][0]['from'] == target['id'] + '.' + renamed_endpoint
    assert original_draft['nodes'][0]['ports'] == target['ports']
    rebound_program = build_object_program(source, rebound['model'], sources=pending, original_sources=current)
    rebound_block = rebound_program.nodes[0]
    rebound_ports = pending.callable(rebound_block)['ports']
    assert [p['name'] for p in rebound_ports if p['negated']] == [renamed_endpoint]
    rebound_terminal = next(n for n in rebound_program.nodes if n.symbol == 'D30')
    rebound_index = next(i for i, p in enumerate(rebound_ports) if p['name'] == renamed_endpoint)
    assert build_connectivity_graph(rebound_program).ports_connected(rebound_block.offset,
        rebound_index, rebound_terminal.offset, 0)
    model['declaration_edits'] = {definition.logical_name: {'renames': {'STATE': 'NEXT_STATE'}}}
    model['wires'] = []
    view = edit_draft(model, {'action': 'add_wire', 'from': target['id']+'.out.NEXT_STATE',
        'to': 'state_out.IN'}, context=draft_context,
        templates=draft_context.catalog(declaration_edits=model['declaration_edits']))
    assert [p['name'] for p in view['presentation']['nodes'][0]['ports']] == [
        'SIGNAL','in.NEXT_STATE','out.NEXT_STATE','NUMBER']
    assert [p['name'] for p in view['model']['nodes'][0]['ports']] == [
        'SIGNAL','in.STATE','out.STATE','NUMBER']
    model = view['model']
    connected = build_object_program(source,model,sources=pending,original_sources=current)
    terminal = next(n for n in connected.nodes if n.symbol=='D30')
    block = connected.nodes[0]
    next_index = next(i for i,p in enumerate(pending.callable(block)['ports']) if p['name']=='out.NEXT_STATE')
    assert build_connectivity_graph(connected).ports_connected(block.offset,next_index,terminal.offset,0)
    incompatible = current.with_declarations({**current.declarations,
        definition.logical_name:edit_declarations(definition,remove=['STATE'])})
    with pytest.raises(GXWFormatError,match='source formal interface differs'):
        build_object_program(source,model,sources=incompatible,original_sources=current)
    # Formal classes do not turn an ordinary program into a callable POU.
    as_program = replace(definition,owner_pou_type=0x1000000)
    unavailable = context.with_declarations({**context.declarations,definition.logical_name:as_program})
    with pytest.raises(GXWFormatError,match='missing or ambiguous'):
        unavailable.callable(rebuilt.nodes[0],bind_instance=False)


@pytest.mark.parametrize('endpoint', ['in.STATE','out.STATE'])
def test_callable_negation_is_explicit_and_independent_of_inout_binding(endpoint):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.editor import edit_draft
    raw = source_binding_baseline()
    source, docs, _ = read_project(raw)
    context = ProjectCallableSources.from_project(raw,source.logical_name,docs)
    model = export_object_model(source,docs,sources=context)
    target = model['nodes'][0]
    assert not next(p for p in target['ports'] if p['name']==endpoint)['negated']
    edited = edit_draft(model,{'action':'update_port','id':target['id'],'port':endpoint,'negated':True})['model']
    output = generate_object_project(edited,baseline=raw).data
    after, after_docs, _ = read_project(output)
    updated = ProjectCallableSources.from_project(output,after.logical_name,after_docs)
    actual = updated.callable(after.nodes[0])['ports']
    old_ports = context.callable(source.nodes[0])['ports']
    assert [(p['name'],p['class_code'],p['side']) for p in actual] == [(p['name'],p['class_code'],p['side']) for p in old_ports]
    assert [p['negated'] for p in actual] == [True if p['name']==endpoint else p['negated'] for p in old_ports]
    assert docs['1.Labels.lh'].rows == after_docs['1.Labels.lh'].rows
    invalid = deepcopy(edited)
    invalid['nodes'][0]['port_edits'] = {'STATE':{'negated':False}}
    with pytest.raises(GXWFormatError,match='source endpoint'):
        generate_object_project(invalid,baseline=raw)


@pytest.mark.parametrize('damage', ['missing', 'type', 'local_global', 'cpu', 'duplicate_library'])
def test_unresolved_source_bindings_remain_explicit_and_cannot_be_cloned(damage):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import edit_declarations
    program, declarations, sources, case = source_binding_witness()
    if damage == 'missing':
        declarations['1.Labels.lh'] = edit_declarations(declarations['1.Labels.lh'], remove=['TIMER_A'])
    elif damage == 'type':
        declarations['1.Labels.lh'] = edit_declarations(declarations['1.Labels.lh'], upserts=[
            {'name': 'TIMER_A', 'data_type': 'TON', 'kind': 'function_block'}])
    elif damage == 'local_global':
        declarations['Global1.gh'] = edit_declarations(declarations['Global1.gh'], upserts=[
            {'name': 'TIMER_A', 'data_type': 'FLOW_PORTS', 'kind': 'function_block', 'class_name': 'VAR_GLOBAL'}])
    elif damage == 'cpu':
        definition = case['definitions'][0]
        text = b'(*$SECTION:FX3U*)\n' + base64.b64decode(definition['declaration_prefix_base64']) + b'END_FUNCTION_BLOCK\n'
        sources = ProjectCallableSources('UNOBSERVED_CPU', program.logical_name, declarations, {'library.lif': text})
    else:
        name = program.nodes[0].type_name.casefold()
        sources.catalog[name].append(sources.catalog[name][0])
    sources = sources.with_declarations(declarations)
    model = export_object_model(program, declarations, sources=sources)
    node = model['nodes'][0]
    assert [port['name'] for port in node['ports']] == ['0', '1', '2', '3']
    assert any(issue['code'] == 'callable_source_gap' and issue.get('node_offset') == node['source_offset']
               for issue in model['issues'])
    model['nodes'].append({'id': 'clone', 'template': node['template'], 'prototype_offset': node['source_offset'],
                           'symbol': 'TIMER_C', 'x': 35, 'y': 2})
    with pytest.raises(GXWFormatError):
        build_object_program(program, model, sources=sources)


def test_unregistered_framed_source_record_is_preserved_during_known_object_edits():
    import struct
    from src.gxw.models import UnknownRecord
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    source, declarations, _ = read_project(default_baseline())
    opaque = UnknownRecord(max(record.offset for record in source.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    with_unknown = replace(source, unknown_records=(opaque,), record_count=source.record_count + 1)
    encoded = serialize_structured_pou(with_unknown)
    program = parse_structured_pou(encoded, logical_name=source.logical_name, preserve_unsupported_records=True)
    model = export_object_model(program, declarations)
    model['nodes'][0]['symbol'] = 'X3'
    changed = build_object_program(program, model)
    assert changed.unknown_records[0].raw == opaque.raw
    assert parse_structured_pou(serialize_structured_pou(changed), preserve_unsupported_records=True).unknown_records[0].raw == opaque.raw


@pytest.mark.parametrize("damage", ["node_id", "offset", "template", "port", "diagonal", "dimensions"])
def test_invalid_models_fail_explicitly(damage):
    model = two_timers()
    if damage == "node_id": model["nodes"][1]["id"] = "a"
    if damage == "offset": model["nodes"][0]["source_offset"] = 99999
    if damage == "template": model["nodes"][0]["template"] = "function_block:UNKNOWN"
    if damage == "port": model["wires"][1]["from"] = "a.FAKE"
    if damage == "diagonal": model["nodes"][1]["y"] += 1
    if damage == "dimensions": model["nodes"][0]["width"] = 100
    with pytest.raises(GXWFormatError):
        generate_object_project(model)


def test_preview_escapes_symbols_as_xml_text():
    p, _, _ = read_project(default_baseline())
    p = replace(p, nodes=(replace(p.nodes[0], symbol='<script>alert("x")</script>'), *p.nodes[1:]))
    svg = render_structured_svg(p)
    root = ET.fromstring(svg)
    assert not root.findall('.//{http://www.w3.org/2000/svg}script')
    assert '&lt;script&gt;' in svg


def test_ladder_conversion_retains_series_parallel_and_multiple_outputs():
    ladder = {"device_comments": {}, "rungs": [{"rung_id": 1, "shared_inputs": [{"type": "NO", "address": "X0"}],
        "branches": [{"inputs": [{"type": "parallel_block", "branches": [
            [{"type": "NO", "address": "X1"}], [{"type": "NC", "address": "X2"}]]}],
            "outputs": [{"type": "COIL", "address": "Y0"}, {"type": "COIL", "address": "Y1"}]}]},
        {"rung_id": 2, "branches": [{"inputs": [{"type": "NO", "address": "X3"}],
                                     "outputs": [{"type": "COIL", "address": "Y2"}]}]}]}
    result = generate_object_project(ladder_to_object_model(ladder))
    p, _, _ = read_project(result.data)
    from src.gxw.connectivity import build_connectivity_graph
    graph = build_connectivity_graph(p)
    by_name = {n.symbol: n for n in p.nodes}
    for port in (0, 1):
        assert graph.ports_connected(by_name['X1'].offset, port, by_name['X2'].offset, port)
    assert graph.ports_connected(by_name['X0'].offset, 1, by_name['X1'].offset, 0)
    assert graph.ports_connected(by_name['X1'].offset, 1, by_name['Y0'].offset, 0)
    assert graph.ports_connected(by_name['Y0'].offset, 0, by_name['Y1'].offset, 0)
    assert not graph.ports_connected(by_name['Y0'].offset, 0, by_name['Y2'].offset, 0)
    assert by_name['X2'].kind == NodeKind.CONTACT_NC


@pytest.mark.parametrize("case,marker", [
    ("coil-05-port-067", "S"),
    ("coil-07-port-003", "S"),
    ("coil-08-port-003", "R"),
    ("contact-17-port-003", "↑"),
    ("contact-18-port-003", "↓"),
    ("coil-07-port-011", None),
])
def test_native_ladder_modifiers_survive_object_roundtrip_and_preview(case, marker):
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou

    cases = json.loads((private_path('tests/fixtures/gxw_ladder_primitives.json')).read_text())["cases"]
    row = next(r for r in cases if r["case"] == case)
    raw = base64.b64decode(row["program_base64"])
    program = parse_structured_pou(raw, logical_name="1.Program.pou")
    model = export_object_model(program)
    rebuilt = build_object_program(program, model)
    assert serialize_structured_pou(rebuilt) == raw
    root = ET.fromstring(render_structured_svg(program))
    texts = [n.text for n in root.findall('.//{http://www.w3.org/2000/svg}text')]
    if marker:
        assert marker in texts
    else:
        # The native SET-kind/port-11 counterexample compiles to inverted OUT.
        assert "S" not in texts and "R" not in texts
    imported = next(n for n in model["nodes"] if n["symbol"] == row["target_symbol"])
    if row["kind"] in {7, 8, 17, 18}:
        imported.pop("source_offset")
        with pytest.raises(GXWFormatError, match="no verified native ABI template"):
            build_object_program(program, model)
def library_transfer_witness():
    import zipfile
    from pathlib import Path
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-native-transfer-20261013.zip')) as archive:
        return (archive.read('library-import-L02/input.gxw'), archive.read('author-Q06UDV.gxw'),
                archive.read('library-import-L02/candidate.gxw'))


def library_transfer_st_witness():
    """The same import with the only project POU replaced by a valid ST source.

    Both states receive the identical substitution, so the readback must accept
    an unchanged ST caller instead of reading it with the FBD-only parser.
    """
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping
    from src.gxw.source_header import source_payload_offset
    before, donor, saved = library_transfer_witness()
    program = 'POU_01.Program.pou'
    case = json.loads(private_path('tests/fixtures/gxw_st_text_native.json').read_text(encoding='utf-8'))['cases'][0]
    st_body = base64.b64decode(case['program_base64'])
    st_start = source_payload_offset(st_body)

    def convert(raw):
        outer = validate_cfb_streams(raw)
        payload = validate_cfb_streams(outer['_hdb'])[logical_mapping(outer['projectdatalist.xml'])[program]]
        start = source_payload_offset(payload)
        assert payload[start] == 208 and st_body[st_start] == 193
        return replay_source_records(raw, {program: payload[:start] + st_body[st_start:]})

    return convert(before), donor, convert(saved), program


def test_library_import_reads_real_definitions_and_retains_target_callers():
    from gxw.library_transfer import library_inventory, verify_library_import
    from gxw.object_model import read_project_context
    before, donor, saved = library_transfer_witness()
    result = verify_library_import(before, donor, 'MotionControl_JEC_V100A', saved, {})
    library = result.report['library_import']
    assert len(library['definitions']) == 16
    assert [row['name'] for row in library['structures']] == ['AXIS_REF_JEC']
    assert [row['name'] for row in library['global_tables']] == ['MC_DIRECTION_JEC']
    power, = [row for row in library['definitions'] if row['name'] == 'MC_Power_JEC']
    assert power['source_text'] and power['language'] == 'st'
    assert [(side, row['declared_type']) for side in ('inputs', 'outputs')
            for row in power['interface'][side] if row['name'] == 'Axis'] == [
                ('inputs', 'AXIS_REF_JEC'), ('outputs', 'AXIS_REF_JEC')]
    assert result.report['validation']['gx_compile'] == 'not_run'
    assert library_inventory(saved)['cpu'] == 'L02'
    # The saved project lists the confirmed user-library sources beside the
    # target program; only the project-owned program set is target state.
    program = 'POU_01.Program.pou'
    reopened = read_project_context(saved, program)
    assert reopened.programs == sorted([program, *[name for name, group in
        reopened.sources.source_groups.items() if group.get('library')]])
    assert [name for name, group in reopened.sources.source_groups.items()
            if group.get('library') is None] == [program]


def test_library_namespace_selection_is_case_insensitive_but_never_partial():
    from hypothesis import given, settings, strategies as st
    from gxw.models import GXWFormatError
    from gxw.library_transfer import library_inventory
    before, donor, _ = library_transfer_witness()
    name = 'MotionControl_JEC_V100A'

    @settings(max_examples=8, deadline=None, derandomize=True)
    @given(st.lists(st.booleans(), min_size=len(name), max_size=len(name)))
    def check(upper):
        query = ''.join(c.upper() if u else c.lower() for c, u in zip(name, upper))
        library, = library_inventory(donor, query)['libraries']
        assert library['name'] == name and len(library['definitions']) == 16
        with pytest.raises(GXWFormatError, match='missing or ambiguous'):
            library_inventory(donor, query + '_missing')
    check()
    assert library_inventory(before)['libraries'] == []


@pytest.mark.parametrize('case', ['missing', 'existing', 'wrong_readback'])
def test_library_import_rejects_wrong_source_or_target(case):
    from gxw.models import GXWFormatError
    from gxw.library_transfer import library_transfer_plan, verify_library_import
    before, donor, saved = library_transfer_witness()
    with pytest.raises(GXWFormatError):
        if case == 'missing':
            library_transfer_plan(before, donor, 'Missing')
        elif case == 'existing':
            library_transfer_plan(saved, donor, 'MotionControl_JEC_V100A')
        else:
            verify_library_import(before, donor, 'MotionControl_JEC_V100A', before, {})


@pytest.mark.parametrize('case,expected', [
    ('program-renamed', 'changed target program sources'),
    ('program-scrapped', 'changed target program sources'),
    ('program-ownership-missing', 'project program source ownership is missing or ambiguous'),
    ('duplicate-stream-identity', 'ambiguous current project name/stream mapping'),
    ('body', 'changed target caller'),
    ('declaration', 'changed target caller'),
    ('task', 'changed target task scopes')])
def test_library_import_readback_rejects_target_source_task_and_ownership_drift(case, expected):
    """Saved-state variants keep every non-library rejection independent."""
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.models import GXWFormatError
    from gxw.object_model import read_project_context
    from gxw.project_metadata import logical_mapping, read_task_source
    from gxw.structured_pou_writer import serialize_structured_pou
    from gxw.container_writer import validate_cfb_streams
    from gxw.library_transfer import verify_library_import
    before, donor, saved = library_transfer_witness()
    program, labels = 'POU_01.Program.pou', 'POU_01.Labels.lh'
    if case == 'program-renamed':
        mutant = project_rows_variant(saved, {program: {'szName': 'POU_02.Program.pou'}})
    elif case == 'program-scrapped':
        mutant = project_rows_variant(saved, {program: {'bScrapFlag': 'true'}})
    elif case == 'program-ownership-missing':
        mutant = project_rows_variant(saved, {labels: {'bScrapFlag': 'true'}})
    elif case == 'duplicate-stream-identity':
        mutant = project_rows_variant(saved, {labels: {'szName': program}})
    elif case == 'body':
        context = read_project_context(saved, program)
        mutant = replay_source_records(saved, {program: serialize_structured_pou(
            replace(context.program, canvas_height=context.program.canvas_height + 2))})
    elif case == 'declaration':
        context = read_project_context(saved, program)
        assert context.sources.source_groups[program]['labels'] == labels
        edited = edit_declarations(context.declarations[labels],
                                   upserts=[dict(name='CallerFlag', data_type='BOOL')])
        mutant = replay_source_records(saved, {labels: serialize_declarations(edited)})
    else:
        outer = validate_cfb_streams(saved)
        payloads = validate_cfb_streams(outer['_hdb'])
        task = bytearray(payloads[logical_mapping(outer['projectdatalist.xml'])['Task_01.tsk']])
        task[read_task_source(task)['count_offset'] - 8] ^= 1
        mutant = replay_source_records(saved, {'Task_01.tsk': bytes(task)})
    with pytest.raises(GXWFormatError, match=expected):
        verify_library_import(before, donor, 'MotionControl_JEC_V100A', mutant, {})


@pytest.mark.parametrize('case', ['retained', 'body', 'declaration', 'opaque-retained', 'opaque-declaration'])
def test_library_import_protects_an_existing_st_project_source(case):
    """An unchanged ST program is accepted through its text projection, not the FBD reader."""
    from gxw.declarations import edit_declarations, parse_declarations, serialize_declarations
    from gxw.models import GXWFormatError
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import logical_mapping
    from gxw.library_transfer import verify_library_import
    before, donor, saved, program = library_transfer_st_witness()
    if case.startswith('opaque-'):
        outer = validate_cfb_streams(saved)
        payloads = validate_cfb_streams(outer['_hdb'])
        mapping = logical_mapping(outer['projectdatalist.xml'])
        source = parse_st_pou(payloads[mapping[program]])
        opaque = bytearray(source.raw)
        opaque[source.text_offset:source.text_offset + 2] = b'\x00\xd8'
        assert parse_st_pou(opaque).text is None
        before = replay_source_records(before, {program: bytes(opaque)})
        saved = replay_source_records(saved, {program: bytes(opaque)})
    outer = validate_cfb_streams(saved)
    payloads = validate_cfb_streams(outer['_hdb'])
    mapping = logical_mapping(outer['projectdatalist.xml'])
    source = parse_st_pou(payloads[mapping[program]])
    assert (source.text is None) if case.startswith('opaque-') else source.text and 'D0:=1;' in source.text
    if case in {'retained', 'opaque-retained'}:
        result = verify_library_import(before, donor, 'MotionControl_JEC_V100A', saved, {})
        assert len(result.report['library_import']['definitions']) == 16
        return
    if case == 'body':
        mutant = replay_source_records(saved, {program: serialize_st_pou(source, source.text + 'D2:=3;')})
    else:
        labels = 'POU_01.Labels.lh'
        document = parse_declarations(payloads[mapping[labels]], logical_name=labels)
        edited = edit_declarations(document, upserts=[dict(name='CallerFlag', data_type='BOOL')])
        mutant = replay_source_records(saved, {labels: serialize_declarations(edited)})
    with pytest.raises(GXWFormatError, match='changed target caller: ' + program):
        verify_library_import(before, donor, 'MotionControl_JEC_V100A', mutant, {})


def test_project_definition_plan_preserves_authored_members_and_never_modifies_input():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.fb_authoring import authoring_plan
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U')

    @settings(max_examples=12, deadline=None, derandomize=True)
    @given(st.lists(st.sampled_from(['BOOL', 'WORD']), min_size=1, max_size=6))
    def check(types):
        members = [dict(name=f'Member_{i}', data_type=value) for i, value in enumerate(types)]
        command = dict(operation='create_structure', source_name='IndependentState', members=members)
        previous = deepcopy(command)
        plan, before, normalized, caller = authoring_plan(raw, command)
        assert command == previous and before is None and caller.endswith('.pou')
        assert [(row['name'], row['data_type']) for row in plan['rows']] == [(row['name'], row['data_type']) for row in members]
        assert [row['initial_value'] for row in normalized] == ['FALSE' if value == 'BOOL' else '0' for value in types]
        assert all(row['class_code'] == 1 for row in plan['rows'])
    check()


@pytest.mark.parametrize('command', [
    dict(operation='create_structure', source_name='State', members=[]),
    dict(operation='create_structure', source_name='State', members=[dict(name='A',data_type='BOOL'),dict(name='a',data_type='WORD')]),
    dict(operation='create_structure', source_name='State', members=[dict(name='A',data_type='MissingType')]),
    dict(operation='create_structure', source_name='State', members=[dict(name='A',data_type='ARRAY[0..1,0..1] OF WORD')]),
    dict(operation='create_st_fb', source_name='Fb', declarations=[], st_text='\0'),
    dict(operation='create_st_fb', source_name='Fb', declarations=[], st_text='\ud800'),
    dict(operation='create_st_fb', source_name='Fb', declarations=[dict(name='A',data_type='BOOL',class_name='VAR_GLOBAL')], st_text=''),
    dict(operation='edit_st_fb', source_name='Missing', st_text=''),
])
def test_project_definition_plan_rejects_unbound_or_malformed_definitions(command):
    from gxw.fb_authoring import authoring_plan
    from gxw.models import GXWFormatError
    from gxw.project_templates import blank_fbd_baseline
    with pytest.raises(GXWFormatError):
        authoring_plan(blank_fbd_baseline('FX3U'), command)


@lru_cache(maxsize=8)
def authored_fb_witness(version='v0005'):
    from pathlib import Path
    import zipfile
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        previous = None
        for row in json.loads(archive.read('trace.json')):
            if row['tool'] != 'create_fbd_candidate' or row['response'].get('status') != 'saved':
                continue
            current = row['response']['data']['version_id']
            saved = archive.read(f'versions/{current}/program.gxw')
            if current == version:
                arguments = {key:value for key,value in row['arguments'].items() if key in
                             ('operation','source_name','members','declarations','st_text')}
                report = json.loads(archive.read(f'versions/{current}/write_report.json'))
                return previous, saved, arguments, report
            previous = saved
    raise AssertionError(version)


@pytest.mark.parametrize('version', ['v0002','v0005','v0006','v0008','v0010'])
def test_authored_project_definition_roundtrip_uses_frozen_native_output(version):
    from gxw.fb_authoring import verify_authoring
    before, after, command, report = authored_fb_witness(version)
    result = verify_authoring(before, command, after, report['native_workspace'])
    assert result.report['validation']['definition_readback'] == 'matched'
    assert result.report['definition_after']['name'] == command['source_name']
    assert result.report['validation']['gx_compile'] == 'not_run'


def test_empty_fb_definition_is_readable_before_its_interface_is_written():
    from gxw.fb_authoring import definition_snapshot
    before, _, _, _ = authored_fb_witness('v0005')
    # The constructor-only version precedes the independently written graph.
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        raw = archive.read('versions/v0003/program.gxw')
    definitions = definition_snapshot(raw)['definitions']
    fb = next(row for row in definitions if row['name']=='GateLogic')
    assert fb['interface']['inputs'] == fb['interface']['outputs'] == []
    assert not fb['model']['nodes']


def test_authored_fb_readback_uses_saved_numbers_after_native_row_reordering():
    from gxw.fb_authoring import _context, definition_snapshot
    _, raw, _, _ = authored_fb_witness('v0008')
    sources = _context(raw)[3]
    table, _ = sources.definition('WordOffset','FUNCTION_BLOCK')
    assert [row.name for row in sources.declarations[table].rows] == ['Input','Value','Addend']
    fb, = definition_snapshot(raw,'WordOffset')['definitions']
    assert [row['name'] for row in fb['declarations']] == ['Input','Addend','Value']
    assert [row['name'] for row in fb['interface']['inputs']] == ['Input','Addend']


@pytest.mark.parametrize('field,value', [('cpu','L02'),('native_version','0.0'),('protocol_version',True),('status','failed')])
def test_authored_definition_cannot_promote_foreign_or_incomplete_native_save(field,value):
    from copy import deepcopy
    from gxw.fb_authoring import verify_authoring
    from gxw.models import GXWFormatError
    before, after, command, report = authored_fb_witness()
    observation = deepcopy(report['native_workspace'])
    observation[field] = value
    with pytest.raises(GXWFormatError,match='CPU/version'):
        verify_authoring(before,command,after,observation)


def test_authored_st_definition_never_accepts_another_requested_implementation():
    from hypothesis import given, settings, strategies as st
    from gxw.fb_authoring import verify_authoring
    from gxw.models import GXWFormatError
    before, after, command, report = authored_fb_witness('v0010')

    @settings(max_examples=6,deadline=None,derandomize=True)
    @given(st.text(alphabet='abcdef0123',min_size=1,max_size=4))
    def check(comment):
        changed = {**command,'st_text':command['st_text']+'(*'+comment+'*)'}
        with pytest.raises(GXWFormatError,match='readback differs'):
            verify_authoring(before,changed,after,report['native_workspace'])
    check()


def test_new_fb_inout_layout_matches_independent_authored_motion_ports():
    from src.gxw.object_model import _source_fb_template
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-instances-20261012.zip')) as archive:
        context = read_project_context(archive.read('checks/motion/original/input.gxw'), 'PrgAxis1.Program.pou')
    observed = []
    for node in context.program.nodes:
        if node.kind != NodeKind.FUNCTION_BLOCK:
            continue
        created = _source_fb_template('function_block:' + node.type_name, context.sources)
        # Width also depends on the GUI font; port sequence, side and row do not.
        ports = lambda item: [(p['formal_name'], p['side'], p['y']) for p in
                             context.sources.callable(item, bind_instance=False)['ports']]
        assert ports(created) == ports(node)
        assert created.bbox.bottom == node.bbox.bottom - node.bbox.top
        observed.append(node.type_name)
    assert len(observed) == 17


def test_generated_inout_layout_keeps_pairs_below_both_ordinary_sides():
    from hypothesis import given, settings, strategies as st
    from src.gxw.object_model import _source_fb_template

    @settings(max_examples=30, deadline=None, derandomize=True)
    @given(st.integers(0, 6), st.integers(0, 6), st.integers(1, 4))
    def check(input_count, output_count, shared_count):
        inputs = [dict(name=f'I{i}', class_code=3) for i in range(input_count)]
        outputs = [dict(name=f'O{i}', class_code=4) for i in range(output_count)]
        shared = [dict(name=f'S{i}', class_code=5) for i in range(shared_count)]
        class Sources:
            def fixed_interface(self, _name):
                return dict(inputs=inputs + shared, outputs=outputs + shared)
        node = _source_fb_template('function_block:Generated', Sources())
        ordinary = node.ports[:input_count] + node.ports[input_count+2*shared_count:]
        pairs = node.ports[input_count:input_count+2*shared_count]
        assert len(node.ports) == input_count + output_count + 2*shared_count
        for left, right in zip(pairs[::2], pairs[1::2]):
            assert left.local_x == 0 and right.local_x == node.bbox.right
            assert left.local_y == right.local_y
            assert all(left.local_y > port.local_y for port in ordinary)
        assert all(0 < port.local_y < node.bbox.bottom for port in node.ports)
    check()


@pytest.mark.parametrize('before_version,after_version', [('v0012','v0015'), ('v0017','v0018')])
def test_native_save_registers_gui_files_and_preserves_them_on_later_edits(before_version, after_version):
    from src.gxw.native_write import verify_native_save
    from src.gxw.object_model import prepare_object_project
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        before = archive.read(f'versions/{before_version}/program.gxw')
        after = archive.read(f'versions/{after_version}/program.gxw')
        if after_version == 'v0018':
            trace = json.loads(archive.read('gui-open-fix/paired-inout-corrected/trace.json'))
            model, = [row['arguments']['model'] for row in trace if row['tool'] == 'create_fbd_candidate']
        else:
            model = read_project_context(before, 'FBD_MATRIX.Program.pou').object_model()
        prepared = prepare_object_project(model, baseline=before)
        result = verify_native_save(prepared, after, {})
        assert result.data == after
        assert result.report['validation']['native_save'] == 'passed'
        assert result.report['validation']['source_body'] == 'exact'
        assert result.report['validation']['declarations'] == 'passed'


def test_parameter_calls_expand_source_inout_and_preserve_the_requested_actuals():
    from src.gxw.ladder_lowering import fbd_topology_to_object_model
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        raw = archive.read('versions/v0018/program.gxw')
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    parameters = dict(program=context.program.logical_name, networks=[dict(calls=[dict(id='gate', type='GateLogic', instance='Gate_Main',
        arguments=dict(Enable='M0', Inhibit='M1', State='State_Main', Allowed='Y0'))])])
    previous = deepcopy(parameters)
    model = fbd_topology_to_object_model(parameters, context)
    assert parameters == previous and len(model['blocks']) == 1
    call = model['nodes'][0]
    assert call['template'] == 'function_block:GateLogic'
    values = [node['symbol'] for node in model['nodes'][1:]]
    assert values.count('State_Main') == 2 and set(values) == {'M0','M1','State_Main','Y0'}
    parameters['networks'][0]['calls'][0]['arguments'] = dict(reversed(list(previous['networks'][0]['calls'][0]['arguments'].items())))
    assert fbd_topology_to_object_model(parameters, context) == model
    result = generate_object_project(parameters, baseline=raw)
    current = read_project_context(result.data, context.program.logical_name)
    node = next(node for node in current.program.nodes if node.kind == NodeKind.FUNCTION_BLOCK)
    shared = [port for port in current.sources.callable(node)['ports'] if port['class_code'] == 5]
    assert [(port['name'], port['y']) for port in shared] == [('in.State',4),('out.State',4)]


def test_parameter_layout_fanout_and_merge_keep_exact_electrical_nets():
    from hypothesis import given, settings, strategies as st
    from src.gxw.connectivity import build_connectivity_graph
    from gxw.project_templates import blank_fbd_baseline
    from src.gxw.ladder_lowering import fbd_topology_from_context
    raw = blank_fbd_baseline('FX3U')

    @settings(max_examples=12, deadline=None, derandomize=True)
    @given(st.integers(1, 6))
    def check(count):
        nodes = [dict(id='start',template='input',symbol='M0')]
        nodes += [dict(id=f'gate{i}',template='contact_nc',symbol=f'M{i+10}') for i in range(count)]
        nodes.append(dict(id='finish',template='output',symbol='Y0'))
        wires = [wire for i in range(count) for wire in [
            {'from':'start.OUT','to':f'gate{i}.IN'}, {'from':f'gate{i}.OUT','to':'finish.IN'}]]
        parameters = dict(networks=[dict(nodes=nodes,wires=wires)])
        saved = generate_object_project(parameters, baseline=raw)
        context = read_project_context(saved.data)
        nets = {frozenset((p.node_symbol,p.port_index) for p in net.ports)
                for net in build_connectivity_graph(context.program).nets if len(net.ports)>1}
        assert nets == {frozenset([('M0',0)]+[(f'M{i+10}',0) for i in range(count)]),
                        frozenset([('Y0',0)]+[(f'M{i+10}',1) for i in range(count)])}
        assert len(context.program.blocks) == 1
        roundtrip = generate_object_project(fbd_topology_from_context(context), baseline=saved.data)
        restored = read_project_context(roundtrip.data)
        assert {frozenset((p.node_symbol,p.port_index) for p in net.ports)
                for net in build_connectivity_graph(restored.program).nets if len(net.ports)>1} == nets
    check()


@pytest.mark.parametrize('network', [
    dict(nodes=[dict(id='a',template='input',symbol='M0',x=1)]),
    dict(calls=[dict(id='a',type='MissingImplementation',instance='A',arguments={})]),
    dict(nodes=[dict(id='a',template='input',symbol='M0')],wires=[{'from':'a.OUT','to':'missing.IN'}]),
    dict(nodes=[dict(id='a',template='contact',symbol='M0')],wires=[{'from':'a.OUT','to':'a.IN'}]),
])
def test_parameter_input_rejects_geometry_missing_definitions_and_unresolved_connections(network):
    from gxw.project_templates import blank_fbd_baseline
    with pytest.raises(ValueError):
        generate_object_project(dict(networks=[network]), baseline=blank_fbd_baseline('FX3U'))


def test_parameter_input_splits_independent_calls_into_native_blocks():
    from gxw.project_templates import blank_fbd_baseline
    parameters = dict(networks=[dict(calls=[dict(id=f't{i}',type='TON',instance=f'Timer{i}',
        arguments=dict(IN=f'M{i}',PT='T#1s',Q=f'Y{i}')) for i in range(2)])])
    result = generate_object_project(parameters, baseline=blank_fbd_baseline('FX3U'))
    context = read_project_context(result.data)
    assert len(context.program.blocks) == 2
    assert {node.symbol for node in context.program.nodes if node.kind == NodeKind.FUNCTION_BLOCK} == {'Timer0','Timer1'}


def test_parameter_callable_chains_and_shared_values_keep_independent_port_nets():
    from hypothesis import given, settings, strategies as st
    from src.gxw.connectivity import build_connectivity_graph
    from src.gxw.object_model import _ports
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U')

    @settings(max_examples=10, deadline=None, derandomize=True)
    @given(st.integers(2, 5), st.booleans())
    def check(count, shared_enable):
        nodes = [dict(id='enable',template='input',symbol='M0'), dict(id='value',template='input',symbol='D0')]
        nodes += [dict(id=f'call{i}',template='function:MOV',symbol='MOV') for i in range(count)]
        nodes += [dict(id=f'out{i}',template='output',symbol=f'D{100+i}') for i in range(count)]
        wires = [{'from':'enable.OUT','to':'call0.EN'}]
        for i in range(count):
            wires += [{'from':'value.OUT','to':f'call{i}.s'}, {'from':f'call{i}.d','to':f'out{i}.IN'}]
            if i:
                wires.append({'from':'enable.OUT' if shared_enable and i==1 else f'call{i-1}.ENO','to':f'call{i}.EN'})
        parameters = dict(networks=[dict(nodes=nodes,wires=wires)])
        result = generate_object_project(parameters,baseline=raw)
        context = read_project_context(result.data)
        by_offset = {node.offset:node for node in context.program.nodes}
        # Native offsets identify each repeated MOV independently; symbols cannot.
        calls = [node for node in context.program.nodes if node.kind==NodeKind.FUNCTION]
        identity = {node.offset:f'call{i}' for i,node in enumerate(calls)}
        identity.update({node.offset:'enable' if node.symbol=='M0' else 'value' if node.symbol=='D0' else
                         'out'+str(int(node.symbol[1:])-100) for node in context.program.nodes if node not in calls})
        actual = {frozenset(identity[p.node_offset]+'.'+_ports(by_offset[p.node_offset],context.sources)[p.port_index]['name']
                            for p in net.ports) for net in build_connectivity_graph(context.program).nets if len(net.ports)>1}
        expected = {frozenset(['value.OUT']+[f'call{i}.s' for i in range(count)])}
        expected.add(frozenset(['enable.OUT','call0.EN']+(['call1.EN'] if shared_enable else [])))
        expected.update(frozenset([f'call{i}.d',f'out{i}.IN']) for i in range(count))
        expected.update(frozenset([f'call{i-1}.ENO',f'call{i}.EN']) for i in range(1 if not shared_enable else 2,count))
        assert actual == expected
        assert len(context.program.blocks)==1
    check()


@pytest.mark.parametrize('reserved',['comment0','timer_arg1'])
def test_parameter_generated_ids_do_not_consume_later_network_identifiers(reserved):
    from gxw.project_templates import blank_fbd_baseline
    parameters = dict(networks=[dict(comment='first',calls=[dict(id='timer',type='TON',instance='Timer0',
        arguments=dict(IN='M0',PT='T#1s',Q='Y0'))]), dict(comment='second',calls=[dict(id=reserved,type='TON',instance='Timer1',
        arguments=dict(IN='M1',PT='T#1s',Q='Y1'))])])
    result = generate_object_project(parameters,baseline=blank_fbd_baseline('FX3U'))
    context = read_project_context(result.data)
    assert {node.symbol for node in context.program.nodes if node.kind==NodeKind.FUNCTION_BLOCK}=={'Timer0','Timer1'}
    assert [node.symbol for node in context.program.nodes if node.kind_code==0x10]==['first','second']


@lru_cache(maxsize=8)
def motion_authoring_witness(version):
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        witness, = [row for row in json.loads(archive.read('motion/authoring-witnesses.json')) if row['after']==version]
        return (archive.read(f"motion/versions/{witness['before']}/program.gxw"),
                archive.read(f'motion/versions/{version}/program.gxw'), witness['command'],
                json.loads(archive.read(f'motion/versions/{version}/write_report.json')))


@pytest.mark.parametrize('version',['v0030','v0036','v0037','v0040'])
def test_motion_authoring_checks_requested_rows_and_configuration_against_native_output(version):
    from gxw.fb_authoring import verify_authoring
    from gxw.models import GXWFormatError
    before,after,command,report = motion_authoring_witness(version)
    result = verify_authoring(before,command,after,report['native_workspace'])
    assert result.report['validation']['definition_readback']=='matched'
    changed = deepcopy(command)
    if version=='v0040':
        changed['options']['program_capacity']=16000
    elif version=='v0036':
        changed['declarations'][0]['member_devices']['bnRX']='M1400'
    else:
        changed['declarations'][0]['initial_value']='2' if version=='v0037' else 'TRUE'
    with pytest.raises(GXWFormatError,match='differ'):
        verify_authoring(before,changed,after,report['native_workspace'])


@lru_cache(maxsize=1)
def motion_compilation_witness():
    from tests.native_fixtures import load_frozen_native_json
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        summary=json.loads(archive.read('motion/run-summary.json'))
        return (archive.read(f"motion/versions/{summary['source_version']}/program.gxw"),
            archive.read(f"motion/versions/{summary['version_id']}/program.gxw"),
            load_frozen_native_json(archive.read(f"motion/versions/{summary['version_id']}/write_report.json")))


def test_compile_save_replays_actual_build_evidence_and_cold_saved_code():
    from gxw.native_diagnostics import project_native_validation
    from gxw.native_write import verify_native_compilation_save
    import base64
    before,after,report=motion_compilation_witness()
    evidence=report['native_evidence']
    current=project_native_validation(before,evidence['observation'],
        {name:base64.b64decode(value) for name,value in evidence['snapshots'].items()})
    assert current['save']=={'completed':True,'exported':True}
    result=verify_native_compilation_save(before,after,current,report['cold_validation'],report['native_workspace'])
    assert result.report['validation']['saved_code']=='matched'
    assert result.report['validation']['gx_compile']=='passed'
    assert result.report['validation']['gui_configuration']=='missing'
    channel=report['cold_validation']['inspection']['saved_code']['resources'][0]['channels'][0]
    assert channel['saved_bytes']==channel['generated_bytes']==143766


@pytest.mark.parametrize('failure',['check','source','code','channel','channel_count'])
def test_compile_save_never_promotes_incomplete_check_or_saved_code(failure):
    from gxw.native_write import verify_native_compilation_save
    from gxw.models import GXWFormatError
    before,after,original=motion_compilation_witness()
    report=deepcopy(original)
    cold=report['cold_validation']
    if failure=='check':cold['public_check']['completed']=False
    elif failure=='source':cold['current_source_check']='not_established'
    elif failure=='code':cold['inspection']['saved_code']['status']='differs'
    elif failure=='channel':cold['inspection']['saved_code']['resources'][0]['channels'][0]['saved_bytes']+=1
    else:cold['inspection']['saved_code']['resources'][0]['channels'].pop()
    with pytest.raises(GXWFormatError):
        verify_native_compilation_save(before,after,report['native_validation'],cold,report['native_workspace'])


@pytest.mark.parametrize('phase', ['save', 'cold_reopen'])
def test_compile_save_failure_keeps_the_original_compiler_code_and_phase(phase):
    from gxw.native_write import verify_native_compilation_save
    from gxw.models import GXWFormatError
    before, after, original = motion_compilation_witness()
    report = deepcopy(original)
    failed = report['native_validation' if phase == 'save' else 'cold_validation']
    failed['compilation'].update(acceptance='rejected', diagnostics=[{'kind': 2, 'code': 0x500c5150}])
    failed['public_check'] = {'status': 'not_requested', 'completed': False}
    with pytest.raises(GXWFormatError, match=phase + r'.*diagnostics=0x500c5150'):
        verify_native_compilation_save(before, after, report['native_validation'],
            report['cold_validation'], report['native_workspace'])


def test_gui_range_projection_does_not_substitute_workspace_or_host_defaults():
    from gxw.project_metadata import read_gui_system_variables, project_gui_configuration
    assert read_gui_system_variables(None)['ranges'] == {}
    raw = '[App]\n\tFileVersion:\n\t\tProjectDataVersion\tTYPE_DWORD\t9\n'.encode('utf-16le')
    assert read_gui_system_variables(raw)['ranges'] == {}
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        old = archive.read('motion/versions/v0043/program.gxw')
    result = project_gui_configuration(old)
    assert result['status'] == 'missing' and 'D' in result['missing']


def test_compile_save_does_not_certify_the_old_gui_configuration_counterexample():
    from gxw.native_write import verify_native_compilation_save
    from gxw.models import GXWFormatError
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        before=archive.read('motion/versions/v0042/program.gxw')
        saved=archive.read('motion/versions/v0043/program.gxw')
        report=json.loads(archive.read('motion/versions/v0043/write_report.json'))
    assert report['native_validation']['compilation']['acceptance']=='accepted'
    assert report['cold_validation']['inspection']['saved_code']['status']=='matches'
    with pytest.raises(GXWFormatError,match='GUI allocation configuration'):
        verify_native_compilation_save(before,saved,report['native_validation'],report['cold_validation'],report['native_workspace'])


@pytest.mark.parametrize('newline',['\n','\r\n'])
@pytest.mark.parametrize('terminated',[False,True])
def test_gui_range_updates_preserve_unrelated_options_and_sections(newline,terminated):
    from gxw.project_metadata import read_gui_system_variables, write_gui_system_variables
    original = newline.join(['\ufeff[Option]','\tSystemVariable:', '\t\tDMin\tTYPE_DWORD\t512',
        '\t\tDMax\tTYPE_DWORD\t999','\t\tUnknownFlag\tTYPE_STRING\t"retain me"',
        '\tOptionBild:', '\t\tStopWarnings\tTYPE_DWORD\t100','[WatchWindow]',
        '\tWatchWnd1:', '\t\tEntrySize\tTYPE_WORD\t0','']) + ('\0' if terminated else '')
    updated = write_gui_system_variables(original.encode('utf-16le'), {'D':(2000,7999)})
    assert read_gui_system_variables(updated)['ranges']['D'] == (2000,7999)
    text = updated.decode('utf-16le')
    assert '\t\tUnknownFlag\tTYPE_STRING\t"retain me"'+newline in text
    assert original[original.index('\tOptionBild:'):] == text[text.index('\tOptionBild:'):]
    assert text.startswith('\ufeff[Option]'+newline)
    assert write_gui_system_variables(updated,{'D':(2000,7999)}) == updated


def test_fx_gui_range_writer_uses_native_100t_and_preserves_inactive_backups():
    from gxw.project_metadata import read_gui_system_variables, write_gui_system_variables
    from gxw.fb_authoring import _context
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    _config, streams, *_rest = _context(_visible_cross_reference_witness())
    original = streams['Gppw2.gpj']
    before = read_gui_system_variables(original, cpu='FX3U/FX3UC')
    disabled = (0xffffffff, 0xffffffff)
    changed = write_gui_system_variables(original, {'D': (2100, 2600), 'M': (4000, 4500),
        'T': (101, 180), 'R': disabled, 'W': disabled, 'B': disabled, 'ST': disabled}, cpu='FX3U/FX3UC')
    after = read_gui_system_variables(changed, cpu='FX3U/FX3UC')
    assert after['ranges']['T'] == (101, 180)
    assert after['stored_ranges']['100T'] == (101, 180)
    assert after['stored_ranges']['T'] == before['stored_ranges']['T']
    for name in ('R', 'W', 'B', 'ST'):
        assert after['stored_ranges'][name] == before['stored_ranges'][name]
    fields = {'DMin', 'DMax', 'MMin', 'MMax', '100TMin', '100TMax'}
    assert {k: v for k, v in before['retained_fields'].items() if k not in fields} == {
        k: v for k, v in after['retained_fields'].items() if k not in fields}
    assert after['word_device'] == 0 and after['bit_device'] == 3
    # The original two-argument API still edits raw generic T fields.
    raw_changed = write_gui_system_variables(original, {'T': (101, 180)})
    assert read_gui_system_variables(raw_changed)['ranges']['T'] == (101, 180)
    assert read_gui_system_variables(raw_changed, cpu='FX3U/FX3UC')['ranges']['T'] == (100, 199)


@pytest.mark.parametrize('name', ['R', 'W', 'B'])
def test_fx_gui_range_writer_rejects_enabling_a_device_outside_the_selected_native_profile(name):
    from gxw.project_metadata import write_gui_system_variables
    from gxw.models import GXWFormatError
    from gxw.fb_authoring import _context
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    _config, streams, *_rest = _context(_visible_cross_reference_witness())
    with pytest.raises(GXWFormatError, match='inactive FX3U selector'):
        write_gui_system_variables(streams['Gppw2.gpj'], {name: (512, 7999)}, cpu='FX3U/FX3UC')


def test_fx_gui_application_save_plan_uses_metadata_cpu_and_retains_native_timer_backups():
    from gxw.project_metadata import gui_application_save_plan, read_gui_system_variables
    from gxw.fb_authoring import _context
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    raw = _visible_cross_reference_witness()
    _config, streams, *_rest = _context(raw)
    previous = read_gui_system_variables(streams['Gppw2.gpj'], cpu='FX3U/FX3UC')
    projected = read_gui_system_variables(gui_application_save_plan(raw, {'operation': 'configure_project',
        'ranges': [{'device': 'D', 'after': [2100, 2400]}]}), cpu='FX3U/FX3UC')
    assert projected['ranges']['D'] == (2100, 2400)
    assert projected['ranges']['T'] == (100, 199)
    assert projected['stored_ranges']['R'] == previous['stored_ranges']['R'] == (512, 7999)
    assert projected['stored_ranges']['T'] == previous['stored_ranges']['T']


@pytest.mark.parametrize('text',['[Option]','[App]\n\tFileVersion:\n\t\tProjectDataVersion\tTYPE_DWORD\t9\n[Option]'])
def test_gui_range_writer_adds_a_subsection_after_a_header_without_final_newline(text):
    from gxw.project_metadata import read_gui_system_variables,write_gui_system_variables
    changed=write_gui_system_variables(text.encode('utf-16le'),{'D':(2000,7999)})
    assert '[Option]\n\tSystemVariable:\n' in changed.decode('utf-16le')
    assert read_gui_system_variables(changed)['ranges']['D']==(2000,7999)


def test_gui_range_writer_matches_independent_bounds_for_generated_ranges():
    from hypothesis import given,settings,strategies as st
    from gxw.project_metadata import read_gui_system_variables,write_gui_system_variables

    @settings(max_examples=30,derandomize=True,deadline=None)
    @given(st.integers(0,7999),st.integers(0,7999),st.booleans())
    def check(a,b,existing):
        lower,upper = sorted((a,b))
        prior = ('[Option]\n\tSystemVariable:\n\t\tDMin\tTYPE_DWORD\t512\n'
                 '\t\tDMax\tTYPE_DWORD\t999\n') .encode('utf-16le') if existing else None
        changed = write_gui_system_variables(prior,{'D':[lower,upper]})
        parsed = read_gui_system_variables(changed)
        assert parsed['ranges']['D'] == (lower,upper)
        assert f'\t\tDMin\tTYPE_DWORD\t{lower}\n' in changed.decode('utf-16le')
        assert f'\t\tDMax\tTYPE_DWORD\t{upper}\n' in changed.decode('utf-16le')
        assert changed.decode('utf-16le').count('\t\tDMin\t') == 1
        assert write_gui_system_variables(changed,{'D':[lower,upper]}) == changed
    check()


def test_empty_program_removal_binds_the_current_task_and_keeps_a_real_caller():
    from gxw.fb_authoring import authoring_plan
    from gxw.models import GXWFormatError
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        raw=archive.read('motion/versions/v0043/program.gxw')
    plan,before,after,caller=authoring_plan(raw,{'operation':'remove_empty_program','source_name':'FBD_MATRIX.Program.pou'})
    assert plan['source_name']=='FBD_MATRIX' and before['kind']=='empty_program' and after['kind']=='removed_program'
    assert caller=='PrgAxis1.Program.pou'
    assert plan['tasks'][0]['programs']==[{'name':'FBD_MATRIX','line':1},{'name':'PrgAxis1','line':2},{'name':'PrgAxis2','line':3}]
    with pytest.raises(GXWFormatError,match='only an empty'):
        authoring_plan(raw,{'operation':'remove_empty_program','source_name':'PrgAxis1.Program.pou'})


def test_empty_program_removal_replays_saved_source_and_task_membership():
    from gxw.fb_authoring import verify_authoring
    from gxw.models import GXWFormatError
    before,after,command,report=motion_authoring_witness('v0047')
    result=verify_authoring(before,command,after,report['native_workspace'])
    assert result.report['definition_after']['kind']=='removed_program'
    assert result.report['caller']=='PrgAxis1.Program.pou'
    with pytest.raises(GXWFormatError,match='removal changed'):
        verify_authoring(before,command,before,report['native_workspace'])


@pytest.mark.parametrize('field',['options','members','declarations','st_text'])
def test_empty_program_removal_rejects_an_edit_mixed_into_the_operation(field):
    from application.fbd import authoring_command
    with pytest.raises(ValueError):
        authoring_command({'operation':'remove_empty_program','source_name':'FBD_MATRIX.Program.pou',field:{}})


@lru_cache(maxsize=2)
def st_program_semantic_witness(st_only=False):
    """Replay a frozen native ST envelope into the ordinary Program witness.

    The task append and Program identity come from native saves; substitution
    is only a Core semantic test and does not claim native ST creation.
    """
    from gxw.fb_authoring import _context
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-program-task-20261010.zip')) as archive:
        prefix = 'native/native-current-program-module-FX3U-FX3UC-'
        before = archive.read(prefix + 'create-v1/input.gxw')
        saved = archive.read(prefix + 'bind-v1/candidate.gxw')
    case = json.loads(private_path('tests/fixtures/gxw_st_text_native.json').read_text(encoding='utf-8'))['cases'][0]
    st_body = base64.b64decode(case['program_base64'])
    values = []
    for raw in (before, saved):
        _, _streams, groups, sources, _ = _context(raw)
        updates = {logical: st_body for logical, group in groups.items()
                   if st_only or sources.declarations[group['labels']].owner_name == 'OwnedProgram_C2'}
        values.append(replay_source_records(raw, updates) if updates else raw)
    return *values, 'Y0:=X0;\r\nD0:=1;\r\n'


@pytest.mark.parametrize('st_only', [False, True])
def test_st_program_snapshot_distinguishes_ordinary_owners_and_keeps_actual_text_and_tasks(st_only):
    from gxw.fb_authoring import st_program_snapshot
    _, saved, text = st_program_semantic_witness(st_only)
    snapshot = st_program_snapshot(saved)
    program, = [row for row in snapshot['programs'] if row['name'] == 'OwnedProgram_C2']
    assert program['kind'] == 'program' and program['language'] == 'st'
    assert program['st_text'] == text
    assert program['task_bindings'] == [{'resource': 'MAIN', 'task': 'TASK_MATRIX', 'line': 2}]
    assert not {'MODULE_FB', 'Created_Source_原生_C1'} & {row['name'] for row in snapshot['programs']}
    assert snapshot['compilation'] == snapshot['execution'] == 'not_run'


def test_st_program_creation_plan_accepts_generated_encodable_names_and_reuses_declaration_types():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.fb_authoring import st_program_authoring_plan
    before, _, _ = st_program_semantic_witness(True)

    @settings(max_examples=18, deadline=None, derandomize=True)
    @given(st.text(alphabet='ABCxyz_中文0123456789', min_size=0, max_size=80))
    def check(suffix):
        name = '任意_ST_' + suffix
        command = dict(operation='create_st_program', source_name=name, declarations=[
            dict(name='Count', data_type='INT'), dict(name='Limit', data_type='INT',
                class_name='VAR_CONSTANT', initial_value='2')], st_text='Count := Limit;',
            resource='MAIN', task='TASK_MATRIX', scope_mode='existing_task')
        original = deepcopy(command)
        plan, prior, expected, _ = st_program_authoring_plan(before, command)
        assert command == original and prior is None
        assert (plan['source_name'], plan['pou_type'], plan['program_kind']) == (name, 0x1000000, 193)
        assert [(row['name'], row['class_code'], row['initial_value']) for row in plan['rows']] == [
            ('Count', 1, '0'), ('Limit', 2, '2')]
        assert [row['kind'] for row in expected] == ['variable', 'variable']
        assert (plan['resource'], plan['task'], plan['line']) == ('MAIN', 'TASK_MATRIX', 2)
        assert not plan['create_task'] and not plan['create_resource']
    check()


@pytest.mark.parametrize('patch', [
    {'source_name': ''}, {'source_name': '12Bad'}, {'source_name': 'With Space'},
    {'source_name': 'A' * 256}, {'source_name': 'Rocket\U0001f680'},
    {'source_name': 'module_fb'}, {'source_name': 'FBD_MATRIX'},
    {'scope_mode': 'new_task'}, {'scope_mode': 'new_resource'}, {'scope_mode': None},
    {'resource': 'Missing'}, {'task': 'Missing'}, {'declarations': None},
    {'declarations': [dict(name='Input', data_type='BOOL', class_name='VAR_INPUT')]},
    {'declarations': [dict(name='Unknown', data_type='UnknownType')]},
    {'st_text': '\0'}, {'st_text': '\ud800'}, {'language': 'st'},
])
def test_st_program_creation_rejects_unbound_scope_names_types_and_fb_interfaces(patch):
    from gxw.fb_authoring import st_program_authoring_plan
    from gxw.models import GXWFormatError
    before, _, _ = st_program_semantic_witness(True)
    command = dict(operation='create_st_program', source_name='Additional_ST', declarations=[],
        st_text=';', resource='MAIN', task='TASK_MATRIX', scope_mode='existing_task')
    with pytest.raises(GXWFormatError):
        st_program_authoring_plan(before, {**command, **patch})


@pytest.mark.parametrize('st_only', [False, True])
def test_st_program_body_edit_preserves_declarations_tasks_and_other_pous_for_generated_text(st_only):
    from hypothesis import given, settings, strategies as st
    from gxw.fb_authoring import _context, st_program_authoring_plan, verify_st_program_readback
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    _, raw, _ = st_program_semantic_witness(st_only)
    plan, before, expected, _ = st_program_authoring_plan(raw, dict(
        operation='edit_st_program', source_name='ownedprogram_c2', st_text=';'))
    assert plan['source_name'] == 'OwnedProgram_C2' and not plan['write_declarations']
    assert expected == before['declarations']
    streams = _context(raw, require_fbd=False)[1]
    source = parse_st_pou(streams[plan['program']])

    @settings(max_examples=9, deadline=None, derandomize=True)
    @given(st.text(alphabet='abcXYZ012中文\U0001f680 \t', max_size=45))
    def check(comment):
        text = 'Y0:=X0;\r\n(*' + comment + '*)\nD0:=1;\r'
        saved = replay_source_records(raw, {plan['program']: serialize_st_pou(source, text)})
        result = verify_st_program_readback(raw, dict(operation='edit_st_program',
            source_name='OwnedProgram_C2', st_text=text.replace('\r\n', '\n').replace('\r', '\n')), saved)
        assert result['program']['st_text'] == text
        assert result['validation'] == dict(source_readback='matched', unedited_sources='preserved',
            task_configuration='exact', task_membership='matched', compilation='not_run', execution='not_run')
    check()


@pytest.mark.parametrize('damage', [None, 'unknown_text', 'device'])
def test_st_program_explicit_declaration_edit_retains_existing_addresses_and_unknown_fields(damage):
    from dataclasses import replace
    from gxw.fb_authoring import _context, st_program_snapshot, verify_st_program_readback
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.models import GXWFormatError
    _, raw, text = st_program_semantic_witness(True)
    program, = st_program_snapshot(raw, 'OwnedProgram_C2')['programs']
    declarations = [{**program['declarations'][0], 'comment': 'Requested comment'},
                    dict(name='Count', data_type='INT', class_name='VAR')]
    command = dict(operation='edit_st_program', source_name='OwnedProgram_C2',
                   declarations=declarations, st_text=text)
    document = _context(raw, require_fbd=False)[3].declarations[program['labels']]
    edited = edit_declarations(document, upserts=declarations, native_fbd_locals=True)
    if damage:
        edited = replace(edited, rows=(replace(edited.rows[0], **{damage: 'Unexpected'}), *edited.rows[1:]))
    saved = replay_source_records(raw, {program['labels']: serialize_declarations(edited)})
    if damage:
        with pytest.raises(GXWFormatError, match='retained addresses or unknown fields'):
            verify_st_program_readback(raw, command, saved)
    else:
        result = verify_st_program_readback(raw, command, saved)
        assert [(row['name'], row['comment']) for row in result['program']['declarations']] == [
            (program['declarations'][0]['name'], 'Requested comment'), ('Count', '')]


@pytest.mark.parametrize('damage', ['body', 'local', 'other-body', 'other-label', 'task-priority',
                                   'task-suffix', 'owner-type', 'global'])
def test_st_program_readback_rejects_source_declaration_task_and_owner_drift(damage):
    from dataclasses import replace
    from gxw.fb_authoring import _context, verify_st_program_readback
    from gxw.declarations import serialize_declarations
    from gxw.models import GXWFormatError
    from gxw.project_metadata import read_task_source
    from gxw.source_header import source_payload_offset
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    _, raw, text = st_program_semantic_witness(True)
    _, streams, groups, sources, _ = _context(raw, require_fbd=False)
    logical = 'OwnedProgram_C2.Program.pou'
    labels = groups[logical]['labels']
    updates = {}
    if damage in ('body', 'other-body'):
        selected = logical if damage == 'body' else 'FBD_MATRIX.Program.pou'
        updates[selected] = serialize_st_pou(parse_st_pou(streams[selected]), text + 'D0:=2;')
    elif damage in ('local', 'other-label', 'global'):
        selected = labels if damage == 'local' else groups['MODULE_FB.Program.pou']['labels']
        if damage == 'global':
            selected = next(name for name, doc in sources.declarations.items() if doc.scope == 'global' and doc.rows)
        document = sources.declarations[selected]
        changed = replace(document, rows=(replace(document.rows[0], comment='Unexpected'), *document.rows[1:]))
        updates[selected] = serialize_declarations(changed)
    elif damage == 'owner-type':
        value = bytearray(streams[labels])
        start = source_payload_offset(value)
        type_offset = start + 4 + 2 * int.from_bytes(value[start:start + 4], 'little')
        value[type_offset:type_offset + 4] = (0x1000002).to_bytes(4, 'little')
        updates[labels] = value
    else:
        task = bytearray(streams['TASK_MATRIX.tsk'])
        if damage == 'task-suffix':
            task += b'ChangedOpaqueSuffix'
        else:
            task[read_task_source(task)['count_offset'] - 8] ^= 1
        updates['TASK_MATRIX.tsk'] = task
    saved = replay_source_records(raw, updates)
    with pytest.raises(GXWFormatError):
        verify_st_program_readback(raw, dict(operation='edit_st_program', source_name='OwnedProgram_C2', st_text=text), saved)


@pytest.mark.parametrize('missing_binding', [False, True])
def test_st_program_create_requires_the_saved_explicit_task_append(missing_binding):
    from gxw.fb_authoring import _context, st_program_snapshot, verify_st_program_readback
    from gxw.models import GXWFormatError
    before, saved, text = st_program_semantic_witness()
    program, = st_program_snapshot(saved, 'OwnedProgram_C2')['programs']
    command = dict(operation='create_st_program', source_name='OwnedProgram_C2',
        declarations=program['declarations'], st_text=text, resource='MAIN', task='TASK_MATRIX', scope_mode='existing_task')
    if missing_binding:
        saved = replay_source_records(saved, {'TASK_MATRIX.tsk': _context(before)[1]['TASK_MATRIX.tsk']})
        with pytest.raises(GXWFormatError, match='task plan'):
            verify_st_program_readback(before, command, saved)
    else:
        assert verify_st_program_readback(before, command, saved)['program']['task_bindings'] == [
            dict(resource='MAIN', task='TASK_MATRIX', line=2)]


@pytest.mark.parametrize('damage', [None, 'prefix', 'status', 'date', 'weekday', 'opaque-word', 'old-row', 'suffix'])
def test_visible_st_program_task_append_bounds_observed_native_save_metadata(damage):
    import struct
    from gxw.fb_authoring import _context, st_program_snapshot, verify_st_program_readback
    from gxw.models import GXWFormatError
    from gxw.project_metadata import read_task_source
    before, saved, text = st_program_semantic_witness()
    program, = st_program_snapshot(saved, 'OwnedProgram_C2')['programs']
    values = []
    for raw, hour, minute, second, pending in ((before, 14, 30, 0, 0), (saved, 15, 38, 34, 1)):
        task = bytearray(_context(raw)[1]['TASK_MATRIX.tsk'])
        task[34:54] = struct.pack('<8HI', 2026, 10, 6, 17, hour, minute, second, 0, pending)
        values.append((raw, task))
    original = values[0][1]
    actual = values[1][1]
    parsed = read_task_source(actual)
    if damage == 'prefix': actual[12] ^= 1
    elif damage == 'status': actual[50:54] = (2).to_bytes(4, 'little')
    elif damage == 'date': actual[36:38] = (13).to_bytes(2, 'little')
    elif damage == 'weekday': actual[38:40] = (0).to_bytes(2, 'little')
    elif damage == 'opaque-word': actual[parsed['count_offset'] - 4] ^= 1
    elif damage == 'old-row': actual[parsed['count_offset'] + 8] ^= 1
    elif damage == 'suffix': actual += b'Unexpected'
    before = replay_source_records(before, {'TASK_MATRIX.tsk': original})
    saved = replay_source_records(saved, {'TASK_MATRIX.tsk': actual})
    command = dict(operation='create_st_program', source_name='OwnedProgram_C2',
        declarations=program['declarations'], st_text=text, resource='MAIN', task='TASK_MATRIX', scope_mode='existing_task')
    if damage:
        with pytest.raises(GXWFormatError):
            verify_st_program_readback(before, command, saved)
    else:
        result = verify_st_program_readback(before, command, saved)
        assert result['task_header_changes'] == [dict(task='TASK_MATRIX.tsk',
            status='native_header_metadata_inference',
            timestamp_before=[2026, 10, 6, 17, 14, 30, 0, 0], timestamp_after=[2026, 10, 6, 17, 15, 38, 34, 0],
            compile_pending_before=0, compile_pending_after=1)]
        assert result['validation']['task_configuration'] == 'exact'


@pytest.mark.parametrize('patch', [dict(source_name='MODULE_FB'), dict(source_name='FBD_MATRIX'),
    dict(source_name=None), dict(resource='MAIN'), dict(task='TASK_MATRIX'), dict(scope_mode='existing_task')])
def test_st_program_edit_does_not_reclassify_fbd_fb_or_move_task_membership(patch):
    from gxw.fb_authoring import st_program_authoring_plan
    from gxw.models import GXWFormatError
    _, raw, _ = st_program_semantic_witness()
    with pytest.raises(GXWFormatError):
        st_program_authoring_plan(raw, {**dict(operation='edit_st_program', source_name='OwnedProgram_C2', st_text=';'), **patch})


@pytest.mark.parametrize('operation', ['create_st_program', 'edit_st_program'])
def test_visible_st_program_plans_do_not_claim_legacy_offline_author_definition_support(operation):
    from gxw.fb_authoring import authoring_plan
    from gxw.models import GXWFormatError
    before, _, _ = st_program_semantic_witness()
    with pytest.raises(GXWFormatError, match='unsupported project definition operation'):
        authoring_plan(before, dict(operation=operation, source_name='Additional_ST', st_text=';'))


def test_st_only_visible_context_can_reuse_existing_definition_plans_without_expanding_offline_defaults():
    from gxw.fb_authoring import authoring_plan, definition_snapshot
    from gxw.models import GXWFormatError
    before, _, _ = st_program_semantic_witness(True)
    command = dict(operation='create_st_fb', source_name='NewSTDefinition', declarations=[], st_text=';')
    with pytest.raises(GXWFormatError, match='current FBD caller'):
        authoring_plan(before, command)
    plan, _, expected, _ = authoring_plan(before, command, require_fbd=False)
    assert plan['operation'] == 'create_st_fb' and plan['st_text'] == ';' and expected == []
    definitions = definition_snapshot(before, require_fbd=False)['definitions']
    assert any(row['kind'] == 'function_block' and row['language'] == 'st' for row in definitions)


@pytest.mark.parametrize('stage', ['saved', 'unbound', 'wrong_name', 'uncommitted'])
def test_native_visible_st_program_creation_matches_original_gui_task_append_and_rejects_partial_saves(stage):
    from gxw.fb_authoring import verify_st_program_readback
    from gxw.models import GXWFormatError
    witness = json.loads(private_path('tests/fixtures/gxw_program_task_native.json').read_text(
        encoding='utf-8'))['visible_st_program_creation']
    assert (witness['cpu'], witness['native_version']) == ('FX3U/FX3UC', '1.635.0.1')
    with zipfile.ZipFile(private_path(witness['archive'])) as archive:
        before = archive.read(witness['baseline'])
        if stage != 'saved':
            index = ['unbound', 'wrong_name', 'uncommitted'].index(stage)
            actual = archive.read(witness['rejected_saves'][index]['saved'])
            with pytest.raises(GXWFormatError):
                verify_st_program_readback(before, witness['command'], actual)
            return
        actual = archive.read(witness['saved'])
        observation = json.loads(archive.read('015-task-replace/observation.json'))
    assert observation['after_edit'] == 'CalcOutput_ST' and observation['modified'] == 1
    result = verify_st_program_readback(before, witness['command'], actual)
    assert result['program'] == witness['expected_program']
    assert result['task_header_changes'] == witness['expected_task_header_changes']
    assert result['task_header_changes'][0]['status'] == 'native_header_metadata_inference'
    assert result['validation'] == dict(source_readback='matched', unedited_sources='preserved',
        task_configuration='exact', task_membership='matched', compilation='not_run', execution='not_run')


def test_single_st_program_without_fbd_or_fb_can_edit_an_existing_native_owner():
    from gxw.fb_authoring import _context, st_program_authoring_plan, verify_st_program_readback
    from gxw.project_templates import blank_fbd_baseline
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    raw = blank_fbd_baseline('FX3U')
    _, streams, groups, sources, _ = _context(raw)
    logical, = groups
    owner = sources.declarations[groups[logical]['labels']].owner_name
    case = json.loads(private_path('tests/fixtures/gxw_st_text_native.json').read_text(encoding='utf-8'))['cases'][0]
    raw = replay_source_records(raw, {logical: base64.b64decode(case['program_base64'])})
    command = dict(operation='edit_st_program', source_name=owner, st_text='Y0 := NOT X0;')
    plan, before, expected, _ = st_program_authoring_plan(raw, command)
    assert plan['source_name'] == owner and before['task_bindings'] and expected == []
    body = parse_st_pou(_context(raw, require_fbd=False)[1][logical])
    saved = replay_source_records(raw, {logical: serialize_st_pou(body, command['st_text'])})
    assert verify_st_program_readback(raw, command, saved)['program']['st_text'] == 'Y0 := NOT X0;'


@pytest.mark.parametrize('data_type, accepted', [
    ('ARRAY[0..2] OF INT', True), ('ARRAY [0..2] OF INT', True),
    ('ARRAY[1..3] OF INT', False), ('ARRAY[-1..1] OF INT', False),
    ('ARRAY[0..3] OF INT', False), ('ARRAY[0..2] OF DINT', False),
    ('ARRAY[0..2] OF WORD', False), ('array[0..2] OF INT', False),
    ('ARRAY  [0..2] OF INT', False), ('ARRAY[0 .. 2] OF INT', False),
])
def test_native_st_program_array_readback_only_accepts_observed_array_keyword_gap(data_type, accepted):
    from copy import deepcopy
    from gxw.fb_authoring import verify_st_program_readback
    from gxw.models import GXWFormatError
    witness = json.loads(private_path('tests/fixtures/gxw_program_task_native.json').read_text(
        encoding='utf-8'))['visible_st_program_array']
    with zipfile.ZipFile(private_path(witness['archive'])) as archive:
        before, actual = archive.read(witness['baseline']), archive.read(witness['saved'])
    command = deepcopy(witness['command'])
    command['declarations'][0]['data_type'] = data_type
    if not accepted:
        with pytest.raises(GXWFormatError, match='readback differs'):
            verify_st_program_readback(before, command, actual)
        return
    result = verify_st_program_readback(before, command, actual)
    assert result['program']['declarations'][0]['data_type'] == witness['expected_native_type']
    assert result['program']['declarations'][0]['initial_value'] == witness['expected_native_initial']
    assert result['task_header_changes'] == []
    assert result['validation']['task_configuration'] == 'exact'


def test_existing_st_program_body_only_edit_retains_nondefault_local_initial_value():
    from dataclasses import replace
    from gxw.fb_authoring import _context, st_program_authoring_plan, verify_st_program_readback
    from gxw.declarations import serialize_declarations
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    _, raw, _ = st_program_semantic_witness(True)
    program = 'OwnedProgram_C2.Program.pou'
    _, streams, groups, sources, _ = _context(raw, require_fbd=False)
    labels = groups[program]['labels']
    document = sources.declarations[labels]
    changed = replace(document, rows=(replace(document.rows[0], initial_value='TRUE'), *document.rows[1:]))
    raw = replay_source_records(raw, {labels: serialize_declarations(changed)})
    command = dict(operation='edit_st_program', source_name='OwnedProgram_C2', st_text='D0 := 2;')
    plan, _before, expected, _caller = st_program_authoring_plan(raw, command)
    assert not plan['write_declarations'] and expected[0]['initial_value'] == 'TRUE'
    saved = replay_source_records(raw, {program: serialize_st_pou(parse_st_pou(streams[program]), command['st_text'])})
    assert verify_st_program_readback(raw, command, saved)['program']['declarations'][0]['initial_value'] == 'TRUE'


@lru_cache(maxsize=2)
def background_st_program_witness(create=False):
    """Offline adapter contract data; native acceptance is a separate gate."""
    if create:
        witness = json.loads(private_path('tests/fixtures/gxw_program_task_native.json').read_text(
            encoding='utf-8'))['visible_st_program_creation']
        with zipfile.ZipFile(private_path(witness['archive'])) as archive:
            before, saved = archive.read(witness['baseline']), archive.read(witness['saved'])
        command = deepcopy(witness['command'])
    else:
        from gxw.fb_authoring import _context
        from gxw.text_pou import parse_st_pou, serialize_st_pou
        _, before, _ = st_program_semantic_witness(True)
        command = dict(operation='edit_st_program', source_name='OwnedProgram_C2', st_text='Y0:=NOT X0;\r\nD0:=2;\r\n')
        logical = 'OwnedProgram_C2.Program.pou'
        streams = _context(before, require_fbd=False)[1]
        saved = replay_source_records(before, {logical: serialize_st_pou(parse_st_pou(streams[logical]), command['st_text'])})
    names = ['OpenProjectEX2', 'SaveProject', 'ExportOneFileProject', 'Definition.SetSTBody',
             'Definition.GetPOUType', 'Definition.GetProgramLanguage',
             'Definition.Create' if create else 'Definition.GetOwner']
    if create:
        names += ['Workspace.GetTaskEventName', 'Workspace.GetTaskInterval', 'Workspace.GetTaskPriority',
                  'Workspace.CreateTaskElement', 'Workspace.SetTaskElementName', 'Workspace.SetTaskElementLine']
    observed = dict(status='saved', protocol_version=1, native_version='1.635.0.1', cpu='FX3U/FX3UC',
        compile='not_requested', check='not_requested',
        calls=[dict(operation=name, hresult=0, code=0) for name in names])
    return before, saved, command, observed


@pytest.mark.parametrize('st_only', [False, True])
def test_current_project_context_reads_globals_local_program_fb_and_actual_task_owners(st_only):
    from gxw.st_program import project_context_snapshot
    _, raw, text = st_program_semantic_witness(st_only)
    snapshot = project_context_snapshot(raw, 'OwnedProgram_C2.Program.pou')
    selected, = [row for row in snapshot['pous'] if row['program'] == snapshot['selected_source']]
    assert snapshot['origin'] == 'supplied_saved_project'
    assert (snapshot['cpu'], snapshot['codepage']) == ('FX3U/FX3UC', 936)
    assert selected['kind'] == 'program' and selected['language'] == 'st' and selected['st_text'] == text
    assert selected['task_bindings'] == [{'resource': 'MAIN', 'task': 'TASK_MATRIX', 'line': 2}]
    assert snapshot['globals'] and all(row['source'].endswith('.gh') for row in snapshot['globals'])
    assert snapshot['structures'] and all('storage' in member for structure in snapshot['structures']
                                          for member in structure['members'])
    assert any(row['kind'] == 'function_block' and row['interface'] for row in snapshot['pous'])
    assert all('declarations' in row and row['labels'].endswith('.lh') for row in snapshot['pous'])
    assert snapshot['compilation'] == snapshot['execution'] == 'not_run'
    assert project_context_snapshot(raw, 'ownedprogram_c2')['selected_source'] == selected['program']


def test_current_project_context_uses_changed_source_and_global_rows_without_a_template_fallback():
    from gxw.fb_authoring import _context
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.st_program import project_context_snapshot
    from gxw.models import GXWFormatError
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    _, raw, _ = st_program_semantic_witness(True)
    _, streams, _, sources, _ = _context(raw, require_fbd=False)
    global_name, document = next((name, doc) for name, doc in sources.declarations.items() if doc.scope == 'global')
    updated = edit_declarations(document, upserts=[dict(name='CurrentGlobalProbe', data_type='BOOL',
        class_name='VAR_GLOBAL', initial_value='TRUE', comment='current saved source')])
    logical = 'OwnedProgram_C2.Program.pou'
    changed = replay_source_records(raw, {global_name: serialize_declarations(updated),
        logical: serialize_st_pou(parse_st_pou(streams[logical]), 'Y0 := CurrentGlobalProbe;')})
    snapshot = project_context_snapshot(changed, logical)
    assert next(row for row in snapshot['pous'] if row['program'] == logical)['st_text'] == 'Y0 := CurrentGlobalProbe;'
    rows = next(table['declarations'] for table in snapshot['globals'] if table['source'] == global_name)
    assert next(row for row in rows if row['name'] == 'CurrentGlobalProbe')['initial_value'] == 'TRUE'
    assert not any(row['name'] == 'CurrentGlobalProbe' for table in project_context_snapshot(raw)['globals']
                   for row in table['declarations'])
    with pytest.raises(GXWFormatError, match='missing or ambiguous current project source'):
        project_context_snapshot(changed, 'A_Source_That_Does_Not_Exist')


def test_current_project_context_retains_unreadable_fb_text_and_structure_array_address_gaps():
    from gxw.fb_authoring import _context
    from gxw.st_program import project_context_snapshot
    from gxw.text_pou import parse_st_pou
    _, raw, _ = st_program_semantic_witness(True)
    _, streams, groups, sources, _ = _context(raw, require_fbd=False)
    logical = next(name for name, group in groups.items()
                   if sources.declarations[group['labels']].owner_pou_type == 0x1000002)
    source = parse_st_pou(streams[logical])
    opaque = source.raw[:source.text_offset] + b'\x00\xd8' + source.raw[source.text_offset + 2:]
    snapshot = project_context_snapshot(replay_source_records(raw, {logical: opaque}), logical)
    selected, = [row for row in snapshot['pous'] if row['program'] == logical]
    assert snapshot['status'] == 'partial' and selected['source_status'] == 'unavailable'
    assert selected['st_text'] is None and 'undecodable' in selected['source_diagnostic']
    assert selected['declarations'] and selected['interface']
    arrays = [row for table in snapshot['globals'] for row in table['declarations']
              if row.get('address_storage_status') == 'unresolved']
    assert arrays and all(row['name'] and row['data_type'] for row in arrays)
    assert any(row['storage']['value_extension_base64'] for row in arrays)
    assert any(issue['code'] == 'global_member_address_gap' for issue in snapshot['issues'])


def test_current_project_context_keeps_local_unknown_fields_and_an_unavailable_fb_interface():
    from dataclasses import replace
    from gxw.declarations import serialize_declarations
    from gxw.fb_authoring import _context
    from gxw.st_program import project_context_snapshot
    _, raw, _ = st_program_semantic_witness(True)
    _, _, groups, sources, _ = _context(raw, require_fbd=False)
    program = 'OwnedProgram_C2.Program.pou'
    document = sources.declarations[groups[program]['labels']]
    original = document.rows[0]
    changed = replace(document, rows=(replace(original, device='M2', iec_address='%MX2',
                                              unknown_text='Retain opaque text'), *document.rows[1:]))
    fb = 'MODULE_FB.Program.pou'
    interface = sources.declarations[groups[fb]['labels']]
    no_ports = replace(interface, rows=(replace(interface.rows[0], class_code=1),))
    snapshot = project_context_snapshot(replay_source_records(raw, {
        document.logical_name: serialize_declarations(changed),
        interface.logical_name: serialize_declarations(no_ports)}), program)
    selected, = [row for row in snapshot['pous'] if row['program'] == program]
    row, = [row for row in selected['declarations'] if row['name'] == original.name]
    assert (row['device'], row['iec_address']) == ('M2', '%MX2')
    assert row['storage']['unknown_text'] == 'Retain opaque text'
    assert row['storage']['type_code'] == original.type_code
    assert row['storage']['array_marker'] == original.array_marker
    blocked, = [row for row in snapshot['pous'] if row['program'] == fb]
    assert blocked['interface_status'] == 'unavailable' and 'interface' not in blocked
    assert blocked['declarations'] and blocked['source_status'] == 'available'
    assert snapshot['status'] == 'partial' and any(issue['code'] == 'pou_interface_gap' for issue in snapshot['issues'])
    json.dumps(snapshot)


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'L02', 'Q03UDV'])
def test_current_project_context_keeps_current_ranges_options_and_private_parameter_bytes(cpu):
    import struct
    from application.projects import public
    from gxw.fb_authoring import _context
    from gxw.project_metadata import read_project_compile_options, read_project_system_variables
    from gxw.project_templates import blank_fbd_baseline
    from gxw.st_program import project_context_snapshot

    raw = blank_fbd_baseline(cpu)
    _, streams, _, _, _ = _context(raw)
    metadata, = [name for name in streams if name.endswith('.prj')]
    value = bytearray(streams[metadata])
    struct.pack_into('<II', value, read_project_system_variables(value)['offset'], 2333, 2444)
    options = read_project_compile_options(value)
    values = [*options['values'][:-1], 0x12345678, 0x10010001, 0xffffffff]
    offset, end = options['count_offset'], options['count_offset'] + 4 + 4 * len(options['values'])
    value = value[:offset] + struct.pack('<I', len(values)) + struct.pack('<' + 'I' * len(values), *values) + value[end:]
    parameter = bytearray(streams['Param.wpa'])
    if cpu == 'FX3U/FX3UC':
        parameter[704] = 64
    parameter[-1] ^= 1
    changed = replay_source_records(raw, {metadata: value, 'Param.wpa': parameter})
    snapshot = project_context_snapshot(changed)
    configuration = snapshot['configuration']
    assert configuration['name'] == metadata and configuration['ranges']['D'] == [2333, 2444]
    assert configuration['compile_options'] == {'global_variable_hiding': True}
    if cpu == 'FX3U/FX3UC':
        assert configuration['program_capacity'] == 64000
    else:
        assert 'program_capacity' not in configuration
    internal = snapshot['_configuration_storage']
    assert internal['compile_options']['values'] == tuple(values)
    assert internal['parameter_present'] and base64.b64decode(internal['parameter_source_base64']) == parameter
    original = project_context_snapshot(raw)
    assert configuration['ranges'] != original['configuration']['ranges']
    assert internal != original['_configuration_storage']
    exposed = public(snapshot)
    assert exposed['configuration'] == configuration and '_configuration_storage' not in exposed
    assert internal['parameter_source_base64'] not in json.dumps(exposed)


@pytest.mark.parametrize('create', [False, True])
def test_background_st_program_plan_and_readback_reuse_current_source_and_task_rules(create):
    from gxw.st_program import background_st_program_plan, verify_background_st_program_save
    before, saved, command, observed = background_st_program_witness(create)
    original = deepcopy(command)
    plan, prior, expected, _ = background_st_program_plan(before, command)
    assert command == original and bool(prior is None) == create
    assert plan['programs'] and plan['tasks'] and plan['st_text'] == command['st_text']
    assert not {'pou_type', 'program_kind', 'scope_mode', 'create_task', 'create_resource'} & plan.keys()
    if create:
        assert (plan['resource'], plan['task'], plan['line']) == ('MAIN', 'Task_01', 2)
        assert plan['write_declarations'] and expected == command['declarations']
    else:
        assert plan['before_body'] and plan['after_body'] and not plan['write_declarations']
        assert expected == prior['declarations']
    result = verify_background_st_program_save(before, command, saved, observed)
    assert result.data == saved and result.report['definition_after']['kind'] == 'program'
    assert result.report['definition_after']['language'] == 'st'
    assert result.report['validation']['task_membership'] == 'matched'
    assert result.report['validation']['gx_compile'] == 'not_run'


def test_background_st_program_creation_keeps_nondefault_initial_values_and_source_bound_fb_types():
    from gxw.st_program import background_st_program_plan
    before, _, _ = st_program_semantic_witness(True)
    command = dict(operation='create_st_program', source_name='Backend_ST', resource='MAIN', task='TASK_MATRIX',
        scope_mode='existing_task', st_text='Count := Count + 1;', declarations=[
            dict(name='Flag', data_type='BOOL', initial_value='TRUE', comment='retained initial value'),
            dict(name='Count', data_type='INT', initial_value='123'),
            dict(name='Instance', data_type='MODULE_FB', kind='function_block')])
    plan, prior, rows, _ = background_st_program_plan(before, command)
    assert prior is None and [(row['name'], row['initial_value']) for row in plan['rows']][:2] == [
        ('Flag', 'TRUE'), ('Count', '123')]
    assert rows[2]['kind'] == 'function_block' and rows[2]['data_type'] == 'MODULE_FB'


def test_current_project_context_roundtrips_generated_st_text_with_frozen_unedited_owners():
    from hypothesis import given, settings, strategies as st
    from gxw.fb_authoring import _context
    from gxw.st_program import project_context_snapshot
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    _, raw, _ = st_program_semantic_witness(True)
    logical = 'OwnedProgram_C2.Program.pou'
    source = parse_st_pou(_context(raw, require_fbd=False)[1][logical])
    original = project_context_snapshot(raw, logical)
    unedited = [row for row in original['pous'] if row['program'] != logical]

    @settings(max_examples=12, deadline=None, derandomize=True)
    @given(st.text(alphabet='abcdXYZ012中文\U0001f680 \t', max_size=55))
    def check(comment):
        text = 'Y0:=X0;\r\n(*' + comment + '*)\nD0:=3;\r'
        saved = replay_source_records(raw, {logical: serialize_st_pou(source, text)})
        current = project_context_snapshot(saved, logical)
        assert next(row['st_text'] for row in current['pous'] if row['program'] == logical) == text
        assert [row for row in current['pous'] if row['program'] != logical] == unedited
        assert current['globals'] == original['globals'] and current['tasks'] == original['tasks']
    check()


@pytest.mark.parametrize('patch', [
    {'source_name': 'MissingProgram', 'operation': 'edit_st_program'},
    {'resource': 'MissingResource'}, {'task': 'MissingTask'},
    {'scope_mode': 'new_task'}, {'scope_mode': 'new_resource'},
    {'declarations': [dict(name='Unbound', data_type='MissingType')]},
    {'declarations': [dict(name='Interface', data_type='BOOL', class_name='VAR_INPUT')]},
    {'st_text': '\0'}, {'st_text': '\ud800'},
])
def test_background_st_program_rejects_missing_source_type_task_and_unsupported_scope(patch):
    from gxw.models import GXWFormatError
    from gxw.st_program import background_st_program_plan
    before, _, command, _ = background_st_program_witness(True)
    if patch.get('operation') == 'edit_st_program':
        command = {key: value for key, value in command.items() if key not in ('resource', 'task', 'scope_mode')}
    with pytest.raises(GXWFormatError):
        background_st_program_plan(before, {**command, **patch})


@pytest.mark.parametrize('damage', ['cpu', 'version', 'compile', 'missing_getter', 'missing_append', 'hresult', 'boolean_code', 'missing_operation'])
def test_background_st_program_requires_complete_version_bound_native_save_evidence(damage):
    from gxw.models import GXWFormatError
    from gxw.st_program import verify_background_st_program_save
    before, saved, command, observed = background_st_program_witness(True)
    observed = deepcopy(observed)
    if damage == 'cpu': observed['cpu'] = 'Q03UDV'
    elif damage == 'version': observed['native_version'] = 'other'
    elif damage == 'compile': observed['compile'] = 'passed'
    elif damage == 'missing_getter':
        observed['calls'] = [row for row in observed['calls'] if row['operation'] != 'Definition.GetProgramLanguage']
    elif damage == 'missing_append':
        observed['calls'] = [row for row in observed['calls'] if row['operation'] != 'Workspace.CreateTaskElement']
    elif damage == 'hresult': observed['calls'][0]['hresult'] = -1
    elif damage == 'boolean_code': observed['calls'][0]['code'] = False
    elif damage == 'missing_operation': observed['calls'][0].pop('operation')
    with pytest.raises(GXWFormatError, match='native save evidence'):
        verify_background_st_program_save(before, command, saved, observed)


@pytest.mark.parametrize('suffix', ['.lnb', '.lns', '.lng'])
def test_background_st_program_save_preserves_imported_library_bodies_structures_and_globals(suffix):
    from gxw.fb_authoring import _context
    from gxw.models import GXWFormatError
    from gxw.source_header import source_payload_offset
    from gxw.st_program import project_context_snapshot, verify_background_st_program_save
    # Real imported library bytes; the ST substitution and call log only test
    # the Core preservation contract, without claiming native ST acceptance.
    _, _, raw = library_transfer_witness()
    _, streams, groups, sources, _ = _context(raw, require_fbd=False)
    logical = next(name for name, group in groups.items()
                   if sources.declarations[group['labels']].owner_pou_type == 0x1000000)
    case = json.loads(private_path('tests/fixtures/gxw_st_text_native.json').read_text(encoding='utf-8'))['cases'][0]
    st_body = base64.b64decode(case['program_base64'])
    raw = replay_source_records(raw, {logical: streams[logical][:source_payload_offset(streams[logical])]
                                     + st_body[source_payload_offset(st_body):]})
    selected, = [row for row in project_context_snapshot(raw, logical)['pous'] if row['program'] == logical]
    command = dict(operation='edit_st_program', source_name=selected['name'], st_text=selected['st_text'])
    observed = {**background_st_program_witness()[3], 'cpu': 'L02'}
    assert verify_background_st_program_save(raw, command, raw, observed).data == raw
    library = next(name for name in streams if name.endswith(suffix))
    if suffix == '.lnb':
        from gxw.text_pou import parse_st_pou, serialize_st_pou
        source = parse_st_pou(streams[library])
        # Keep the library ST envelope valid so the check reaches the intended
        # preservation boundary, rather than failing earlier during parsing.
        changed_library = serialize_st_pou(source, source.text + '\r\n(* UnrequestedLibraryChange *)')
    else:
        changed_library = streams[library] + b'UnrequestedLibraryChange'
    changed = replay_source_records(raw, {library: changed_library})
    # Library ST is now a resolved POU, so it reaches the semantic POU guard
    # before the remaining opaque library/structure stream comparison.
    reason = 'unrelated POU or its declarations' if suffix == '.lnb' else 'library or structure source bytes'
    with pytest.raises(GXWFormatError, match=reason):
        verify_background_st_program_save(raw, command, changed, observed)


def test_background_st_program_save_rejects_unrequested_allocation_range_change():
    import struct
    from gxw.fb_authoring import _context
    from gxw.models import GXWFormatError
    from gxw.project_metadata import read_project_system_variables
    from gxw.st_program import verify_background_st_program_save
    before, saved, command, observed = background_st_program_witness()
    logical, project = next((name, data) for name, data in _context(saved, require_fbd=False)[1].items()
                            if name.endswith('.prj'))
    ranges = read_project_system_variables(project)
    changed = bytearray(project)
    struct.pack_into('<I', changed, ranges['offset'], ranges['ranges']['D'][0] + 1)
    saved = replay_source_records(saved, {logical: changed})
    with pytest.raises(GXWFormatError, match='unrelated current project configuration'):
        verify_background_st_program_save(before, command, saved, observed)


@lru_cache(maxsize=1)
def background_st_program_retained_native_witness():
    """Root-captured isolated adapter output; tests never run the adapter."""
    root = private_path('research/experiments/native-live-session-20261018')
    case = root / '012-backend-st-retained'
    files = [case / 'input.gxw', case / 'candidate.gxw', case / 'native-observation.json',
             root / '011-backend-st-create/command.json']
    if not all(path.is_file() for path in files):
        pytest.skip('private retained ordinary ST adapter witness is unavailable')
    return (files[0].read_bytes(), files[1].read_bytes(),
            json.loads(files[3].read_text(encoding='utf-8')), json.loads(files[2].read_text(encoding='utf-8')))


def test_background_st_program_save_accepts_only_existing_role_bound_library_relocations():
    from gxw.fb_authoring import _context
    from gxw.st_program import verify_background_st_program_save
    before, saved, command, observed = background_st_program_retained_native_witness()
    old, new = [_context(raw, require_fbd=False)[1] for raw in (before, saved)]
    expected = [('MELSECFunction.lif', 'MELSEC.lif', 130422), ('IECFunction.lif', 'IEC.lif', 10385)]
    for previous, current, size in expected:
        assert len(old[previous]) == len(new[current]) == size and old[previous] == new[current]
    result = verify_background_st_program_save(before, command, saved, observed)
    assert result.report['library_relocations'] == [dict(before=old, after=new) for old, new, _ in expected]
    count, = [row for row in result.report['definition_after']['declarations'] if row['name'] == 'Count']
    assert count['initial_value'] == '123' and result.report['validation']['native_save'] == 'passed'
    assert result.report['validation']['gx_compile'] == 'not_run'


def test_visible_ide_restores_only_the_two_witnessed_role_bound_library_names():
    from gxw.fb_authoring import _context
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import logical_mapping
    from gxw.native_write import _saved_object_names
    root = private_path('research/experiments/native-live-session-20261018/104-native-library-name-return')
    before, saved = [(root/name).read_bytes() for name in ('original.gxw', 'native-saved.gxw')]
    metadata_a, metadata_b = [validate_cfb_streams(raw)['projectdatalist.xml'] for raw in (before, saved)]
    mapping_a, mapping_b = [{name:key for name,key in logical_mapping(metadata).items() if name.endswith('.lif')}
                            for metadata in (metadata_a, metadata_b)]
    names = _saved_object_names(metadata_a, mapping_a, metadata_b, mapping_b, {}, {})
    assert {old:new for old,new in names.items() if old != new} == {
        'MELSEC.lif':'MELSECFunction.lif', 'IEC.lif':'IECFunction.lif'}
    old, new = [_context(raw, require_fbd=False)[1] for raw in (before, saved)]
    for original, current in names.items():
        assert old[original] == new[current]


@pytest.mark.parametrize('damage', ['swapped_names', 'missing', 'unknown_name', 'wrong_role',
                                    'duplicate_name', 'changed_byte'])
def test_background_st_program_library_relocation_rejects_swapped_missing_or_changed_sources(damage):
    from gxw.fb_authoring import _context
    from gxw.models import GXWFormatError
    from gxw.st_program import verify_background_st_program_save
    before, saved, command, observed = background_st_program_retained_native_witness()
    if damage == 'swapped_names':
        saved = project_rows_variant(saved, {'MELSEC.lif': {'szName': 'IEC.lif'},
                                             'IEC.lif': {'szName': 'MELSEC.lif'}})
    elif damage == 'missing':
        saved = project_rows_variant(saved, {'MELSEC.lif': {'szName': 'RemovedLibrary.dat'}})
    elif damage == 'unknown_name':
        saved = project_rows_variant(saved, {'MELSEC.lif': {'szName': 'AnotherLibrary.lif'}})
    elif damage == 'wrong_role':
        saved = project_rows_variant(saved, {'MELSEC.lif': {'ucFolderType': '49'}})
    elif damage == 'duplicate_name':
        saved = project_rows_variant(saved, {'IEC.lif': {'szName': 'MELSEC.lif'}})
    else:
        from gxw.container_writer import replace_project_stream, validate_cfb_streams
        from gxw.project_metadata import logical_mapping
        library = bytearray(_context(saved, require_fbd=False)[1]['MELSEC.lif'])
        library[4] ^= 1  # Even the preserved library header is not freely normalized.
        # Adversarial output, without a fake history synchronization: native
        # history still uses MELSECFunction.lif and that name is preserved.
        outer = validate_cfb_streams(saved)
        physical = logical_mapping(outer['projectdatalist.xml'])['MELSEC.lif']
        nested, _ = replace_project_stream(outer['_hdb'], physical, bytes(library))
        saved, _ = replace_project_stream(saved, '_hdb', nested)
    with pytest.raises(GXWFormatError):
        verify_background_st_program_save(before, command, saved, observed)


def live_native_context_witness(raw, identity=None):
    """Controlled current-page observations over saved fixture semantics, no IDE."""
    import csv
    import io
    from gxw.active_context import read_active_labels_csv, read_active_st_text
    from gxw.fb_authoring import _context
    from gxw.native_write import native_task_scope_context
    from gxw.project_metadata import read_project_system_variables
    from gxw.source_header import source_payload_offset
    from gxw.text_pou import parse_st_pou
    identity = identity or dict(process_id=701, window_handle=9001, process_started='123456',
        executable='D:/GXWORKS2/GD2.exe', software_version='1.635.0.1', project_path='D:/Lab/live.gxw', cpu_menu='FX3U/FX3UC')
    config, streams, groups, sources, _ = _context(raw, require_fbd=False)
    inventory, tables, st, graphs = dict(globals=[], pous=[], structures=[]), [], [], []
    names = {value: key for key, value in __import__('gxw.declarations', fromlist=['CLASS_CODES']).CLASS_CODES.items()}

    def table(name, kind, rows):
        columns = (['类'] if kind != 'structure' else []) + ['标签名', '数据类型', '常量']
        if kind == 'global':
            columns += ['软元件', '地址']
        columns += ['注释']
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer, delimiter='\t', lineterminator='\r\n')
        writer.writerow(['native table fixture'])
        writer.writerow(columns)
        for row in rows:
            code = getattr(row, 'class_code', None)
            fields = {'类': names.get(code, ''), '标签名': row.name, '数据类型': row.data_type,
                '常量': row.initial_value if code in (2, 9, 11) else '', '注释': row.comment,
                '软元件': getattr(row, 'device', ''), '地址': getattr(row, 'iec_address', '')}
            writer.writerow([fields[column] for column in columns])
        title = ('全局标签设置 ' + name if kind == 'global' else '结构体设置 ' + name if kind == 'structure' else
                 ('局部标签设置 ' if kind == 'program' else '函数/FB标签设置 ') + name + (' [PRG]' if kind == 'program' else ' [FB]'))
        return read_active_labels_csv(buffer.getvalue().encode('utf-16'), owner=name, scope=kind,
            identity=identity, document_handle=100 + len(tables), document_title=title)
    for logical, document in sources.declarations.items():
        if document.scope == 'global':
            name = logical.removesuffix('.gh')
            inventory['globals'].append(name)
            tables.append(table(name, 'global', document.rows))
    for documents in sources.project_structures.values():
        document, = documents
        name = document.logical_name.removesuffix('.str')
        inventory['structures'].append(name)
        tables.append(table(name, 'structure', document.members))
    for logical, group in groups.items():
        document = sources.declarations[group['labels']]
        name, kind = document.owner_name, 'program' if document.owner_pou_type == 0x1000000 else 'fb'
        language = 'st' if streams[logical][source_payload_offset(streams[logical])] == 193 else 'fbd'
        inventory['pous'].append(dict(owner=name, owner_kind=kind, language=language))
        tables.append(table(name, kind, document.rows))
        title = name + (' [PRG]' if kind == 'program' else ' [FB]') + ' 程序本体 '
        if language == 'st':
            st.append(read_active_st_text(parse_st_pou(streams[logical]).text, owner=name, kind=kind,
                identity=identity, document_handle=300+len(st), document_title=title+'[ST]', editor_handle=400+len(st)))
        else:
            from gxw.object_model import read_project_context
            from gxw.models import StructuredNode, StructuredWire
            program = read_project_context(raw, logical).program
            networks = []
            for index, (_, records) in enumerate(program.block_records()):
                nodes, wires = [], []
                for record in records:
                    if isinstance(record, StructuredNode):
                        nodes.append(dict(index=len(nodes), address=0x10000+record.offset, record=record.raw.hex(),
                            name=record.symbol, type_name=record.symbol if record.kind_code == 1 else record.type_name,
                            source_kind_code=record.kind_code, bounds=[record.bbox.left, record.bbox.top,
                                record.bbox.right, record.bbox.bottom], selected=False))
                    elif isinstance(record, StructuredWire):
                        wires.append(dict(index=len(wires), address=0x10000+record.offset,
                            record=record.raw.hex(), start=[record.start.x,record.start.y],
                            end=[record.end.x,record.end.y], selected=False))
                networks.append(dict(index=index,nodes=nodes,wires=wires,junctions=[]))
            graphs.append(dict(status='observed', protocol_version=1, native_version='1.635.0.1',
                identity=deepcopy(identity), owner=name, owner_kind=kind, active_network=0, networks=networks))
    type_sources = [row for row in tables if row['owner_kind'] in ('structure','fb')]
    tables = [read_active_labels_csv(row['raw_bytes'], owner=row['owner'], scope=row['owner_kind'], identity=identity,
        document_handle=row['source_binding']['document_handle'], document_title=row['source_binding']['document_title'],
        type_sources=type_sources) for row in tables]
    metadata, = [value for name,value in streams.items() if name.endswith('.prj')]
    ranges = {key:list(value) for key,value in read_project_system_variables(metadata)['ranges'].items() if key in ('D','M')}
    configuration = dict(status='available',origin='active_native_configuration',identity=deepcopy(identity),
        cpu=config['cpu'],coverage='verified_D_M_controls_only',ranges=ranges,
        raw_fields={key:[dict(control_id=10+i,text=str(value),enabled=True) for i,value in enumerate(pair)] for key,pair in ranges.items()})
    tasks = {**native_task_scope_context(raw), 'origin':'validated_saved_baseline_and_confirmed_native_appends','confirmed_appends':[]}
    return dict(identity=deepcopy(identity), inventory=inventory,tables=tables,st=st,graphs=graphs,configuration=configuration,tasks=tasks)


@lru_cache(maxsize=1)
def pure_st_compiled_checkpoint_witness():
    root = private_path('research/experiments/st-enhancement-20261019/sample-l02-02')
    names = ['baseline.gxw', 'compiled.gxw', 'native-save-reopen-response.json']
    if any(not (root / name).is_file() for name in names):
        pytest.skip('private pure ST compile/save checkpoint unavailable')
    def decode(value):
        if set(value) == {'kind', 'base64'} and value['kind'] == 'exact_bytes':
            return base64.b64decode(value['base64'], validate=True)
        return value
    response = json.loads((root / names[2]).read_text(encoding='utf-8'), object_hook=decode)
    return (root / names[0]).read_bytes(), (root / names[1]).read_bytes(), response['_stage_context'], response['_stage_compile']


@pytest.mark.parametrize('damage', [None, 'no_compile', 'stale_compile', 'no_grid', 'grid_total',
    'grid_range', 'grid_unknown', 'already_initialized', 'retained_timer'])
def test_pure_st_pointer_materialization_requires_current_compile_and_complete_native_grid(damage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from gxw.project_metadata import read_project_system_variables
    before, saved, frozen_context, frozen_compile = pure_st_compiled_checkpoint_witness()
    context, compilation = deepcopy(frozen_context), deepcopy(frozen_compile)
    # This is a synthetic before-range variant of the real saved checkpoint.
    # The independent native allocation grid supplies the resulting bounds.
    streams = _context(before, require_fbd=False)[1]
    name, = [name for name in streams if name.endswith('.prj')]
    metadata = bytearray(streams[name]); offset = read_project_system_variables(metadata)['offset']
    struct.pack_into('<II', metadata, offset + 8 * 8,
                     *( (1, 2) if damage == 'already_initialized' else (0xffffffff, 0xffffffff)))
    if damage == 'retained_timer': struct.pack_into('<II', metadata, offset + 3 * 8, 1, 2)
    before = replay_source_records(before, {name: bytes(metadata)})
    if damage == 'no_compile': compilation = None
    elif damage == 'stale_compile': compilation['revision'] -= 1
    elif damage == 'no_grid': context['configuration'].pop('grid_rows')
    elif damage and damage.startswith('grid_'):
        row, = [row for row in context['configuration']['grid_rows']['allocation'] if row['cells'][2]['value'] == 'P']
        if damage == 'grid_total': row['cells'][7]['value'] = '1'
        elif damage == 'grid_range': row['cells'][5]['value'] = '1'
        else: row['cells'][5]['state'] |= 1
    if damage:
        with pytest.raises(ValueError): verify_active_saved_context(saved, context, before, compilation=compilation)
    else:
        report = verify_active_saved_context(saved, context, before, compilation=compilation)
        assert report['status'] == 'matched' and report['native_port_nodes_matched'] == 0
        assert report['native_range_initializations'][0]['origin'] == 'current_native_st_pointer_default_materialized'
        assert report['compiled_header_run_id'] == frozen_compile['compile_run_id']


def test_live_current_context_freezes_only_actual_source_and_stage_save_matches_saved_bytes():
    from gxw.live_context import live_context_state, verify_active_saved_context
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U/FX3UC')
    current = live_native_context_witness(raw)
    assert verify_active_saved_context(raw, current, raw)['status'] == 'matched'
    rebuilt = deepcopy(current)
    for row in rebuilt['tables']:
        row['source_binding']['document_handle'] += 900
        row['export_file'] = 'D:/private/export.csv'
    for graph in rebuilt['graphs']:
        graph['data_id'] = 0x12345
        graph['active_network'] = 0
    assert live_context_state(rebuilt) == live_context_state(current)
    assert 'raw_bytes' not in str(live_context_state(current)) and 'document_handle' not in str(live_context_state(current))


def compiled_visible_header_witness():
    # Exact native 054 before/after prefixes, independently captured from POU
    # and Task sources. Reuse an authored empty body; no original project is
    # required to reproduce the common-header and compile-receipt boundary.
    before = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000ea070a00000012000a003a003100000001000000')
    after = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000ea070a00000012000d001b000600000000000000')
    task_before = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000ea070a00000012000a001e001200000001000000')
    task_after = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000ea070a00000012000d000f000700000000000000')
    return before, after, task_before, task_after


def compiled_stage_save_witness():
    from gxw.fb_authoring import _context
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U/FX3UC')
    _, streams, groups, _, _ = _context(raw, require_fbd=False)
    pou, = groups
    task, = [name for name in streams if name.endswith('.tsk')]
    before, after, task_before, task_after = compiled_visible_header_witness()
    raw = replay_source_records(raw, {pou: before + streams[pou][54:], task: task_before + streams[task][54:]})
    saved = replay_source_records(raw, {pou: after + streams[pou][54:], task: task_after + streams[task][54:]})
    context = live_native_context_witness(saved)
    context['_revision'] = 10
    compilation = dict(status='passed', errors=0, origin='current_native_compile_and_active_source',
        identity=deepcopy(context['identity']), source_unchanged=True, revision=10,
        compile_run_id='633c63e9c6aa4855b5c3dcd349b12a41')
    return raw, saved, context, compilation


def test_live_compiled_stage_save_binds_only_measured_source_and_task_header_clearing_to_current_compile():
    from gxw.live_context import verify_active_saved_context
    raw, saved, context, compilation = compiled_stage_save_witness()
    with pytest.raises(ValueError, match='unverified native header'):
        verify_active_saved_context(saved, context, raw)
    validated = verify_active_saved_context(saved, context, raw, compilation=compilation)
    assert validated['status'] == 'matched'
    assert validated['compiled_header_revision'] == 10
    assert validated['compiled_header_run_id'] == compilation['compile_run_id']
    assert len(validated['source_header_changes']) == 2
    assert all(change['compile_pending_before'] == 1 and change['compile_pending_after'] == 0
               and change['native_compiled_permission_used'] is True for change in validated['source_header_changes'])
    assert validated['compilation'] == 'not_requested' and validated['execution'] == 'not_run'


@pytest.mark.parametrize('damage', ['missing', 'not_run', 'failed', 'errors', 'boolean_errors', 'float_errors',
    'missing_errors', 'origin', 'changed_source', 'numeric_unchanged', 'missing_identity', 'foreign_identity',
    'missing_run', 'empty_run', 'control_run', 'old_revision', 'boolean_revision', 'missing_context_revision'])
def test_live_compiled_header_clear_requires_exact_current_compile_receipt(damage):
    from gxw.live_context import verify_active_saved_context
    raw, saved, context, compilation = compiled_stage_save_witness()
    if damage == 'missing': compilation = None
    elif damage in ('not_run', 'failed'): compilation['status'] = damage
    elif damage == 'errors': compilation['errors'] = 1
    elif damage == 'boolean_errors': compilation['errors'] = False
    elif damage == 'float_errors': compilation['errors'] = 0.0
    elif damage == 'missing_errors': compilation.pop('errors')
    elif damage == 'origin': compilation['origin'] = 'saved_offline_compile'
    elif damage == 'changed_source': compilation['source_unchanged'] = False
    elif damage == 'numeric_unchanged': compilation['source_unchanged'] = 1
    elif damage == 'missing_identity': compilation.pop('identity')
    elif damage == 'foreign_identity': compilation['identity']['process_started'] = 'another start'
    elif damage == 'missing_run': compilation.pop('compile_run_id')
    elif damage == 'empty_run': compilation['compile_run_id'] = ''
    elif damage == 'control_run': compilation['compile_run_id'] = 'run\nother'
    elif damage == 'old_revision': compilation['revision'] = 9
    elif damage == 'boolean_revision': compilation['revision'] = True
    elif damage == 'missing_context_revision': context.pop('_revision')
    with pytest.raises(ValueError, match='unverified native header'):
        verify_active_saved_context(saved, context, raw, compilation=compilation)
    # Invalid/not-yet-run compilation grants no clearing exception, but also
    # cannot prevent an ordinary uncompiled Save under the existing policy.
    uncompiled = live_native_context_witness(raw)
    assert verify_active_saved_context(raw, uncompiled, raw, compilation=compilation)['status'] == 'matched'


@pytest.mark.parametrize('damage', ['prefix', 'length', 'description', 'before_flag', 'after_flag', 'month',
    'weekday', 'millisecond', 'cpu', 'non_boolean_permission'])
def test_native_compiled_save_header_permission_never_normalizes_unknown_header_fields(damage):
    import struct
    from gxw.source_header import normalize_visible_save_header
    before, after, _, _ = compiled_visible_header_witness()
    expected, observed = normalize_visible_save_header(before, after, cpu='FX3U/FX3UC', native_compiled=True)
    assert expected == before and observed['compile_pending_after'] == 0
    before, after, cpu, permission = bytearray(before), bytearray(after), 'FX3U/FX3UC', True
    if damage == 'prefix': after[24] ^= 1
    elif damage == 'length': after += b'\0'
    elif damage == 'description': before[0] = after[0] = 2
    elif damage == 'before_flag': struct.pack_into('<I', before, 50, 2)
    elif damage == 'after_flag': struct.pack_into('<I', after, 50, 2)
    elif damage == 'month': struct.pack_into('<H', after, 36, 13)
    elif damage == 'weekday': struct.pack_into('<H', after, 38, 1)
    elif damage == 'millisecond': struct.pack_into('<H', after, 48, 1000)
    elif damage == 'cpu': cpu = 'Q03UDV'
    elif damage == 'non_boolean_permission': permission = 1
    with pytest.raises(ValueError):
        normalize_visible_save_header(before, after, cpu=cpu, native_compiled=permission)


def test_native_compiled_header_prefix_is_fixed_for_every_uninterpreted_byte():
    from hypothesis import given, settings, strategies as st
    from gxw.source_header import normalize_visible_save_header
    original, saved, _, _ = compiled_visible_header_witness()
    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=33), st.integers(min_value=1, max_value=255))
    def check(offset, mask):
        before, after = bytearray(original), bytearray(saved)
        before[offset] ^= mask
        after[offset] ^= mask
        # Identical foreign prefixes do not grant a metadata-normalization
        # exception, even though the only difference between them is a date.
        with pytest.raises(ValueError):
            normalize_visible_save_header(before, after, cpu='FX3U/FX3UC', native_compiled=True)
    check()


@pytest.mark.parametrize('before_flag,after_flag,authorized', [(0,0,True),(1,0,True),(0,1,False),(1,1,False)])
def test_visible_header_default_policy_still_requires_pending_one_after_changes(before_flag, after_flag, authorized):
    import struct
    from gxw.source_header import normalize_visible_save_header
    before, after, _, _ = compiled_visible_header_witness()
    before, after = bytearray(before), bytearray(after)
    struct.pack_into('<I',before,50,before_flag)
    struct.pack_into('<I',after,50,after_flag)
    normalized, _ = normalize_visible_save_header(before,after,cpu='FX3U/FX3UC',native_compiled=authorized)
    assert normalized == before
    if after_flag == 0:
        with pytest.raises(ValueError): normalize_visible_save_header(before,after,cpu='FX3U/FX3UC')


def test_first_visible_save_populates_the_frozen_background_st_default_timestamp():
    from gxw.fb_authoring import _context
    from gxw.source_header import normalize_visible_save_header, source_payload_offset
    root = private_path('research/experiments/native-live-session-20261018/104-native-library-name-return')
    a, b = [_context((root/name).read_bytes(), require_fbd=False)[1]['Backend_ST_Program.Program.pou']
            for name in ('original.gxw', 'native-saved.gxw')]
    before, after = a[:source_payload_offset(a)], b[:source_payload_offset(b)]
    assert before[34:50] == bytes(16) and after[34:50] != bytes(16)
    normalized, receipt = normalize_visible_save_header(before, after, cpu='FX3U/FX3UC', native_compiled=True)
    assert normalized == before and receipt['timestamp_before_status'] == 'unset_native_default'
    assert receipt['compile_pending_before'] == 1 and receipt['compile_pending_after'] == 0
    malformed = bytearray(before); malformed[34] = 1
    with pytest.raises(ValueError, match='timestamp'):
        normalize_visible_save_header(malformed, after, cpu='FX3U/FX3UC', native_compiled=True)
    cleared = bytearray(after); cleared[34:50] = bytes(16)
    with pytest.raises(ValueError, match='timestamp'):
        normalize_visible_save_header(after, cleared, cpu='FX3U/FX3UC', native_compiled=True)


@pytest.mark.parametrize('layer', ['node_flags', 'node_reserved', 'wire_prefix', 'wire_suffix', 'task_payload', 'st_envelope'])
def test_current_compile_header_permission_keeps_other_source_storage_strict(layer):
    from dataclasses import replace
    from gxw.live_context import verify_active_saved_context
    from gxw.fb_authoring import _context
    from gxw.object_model import read_project_context
    from gxw.structured_pou_writer import serialize_structured_pou
    from gxw.source_header import source_payload_offset
    if layer == 'st_envelope':
        before, saved, command, _ = background_st_program_witness(False)
        current = live_native_context_witness(saved)
        streams = _context(saved, require_fbd=False)[1]
        logical = command['source_name'] + '.Program.pou'
        value = bytearray(streams[logical])
        value[source_payload_offset(value) + 9] ^= 1  # Legal but retained opaque ST envelope flag.
        damaged = replay_source_records(saved, {logical: value})
    elif layer == 'task_payload':
        before, saved, current, _ = compiled_stage_save_witness()
        streams = _context(saved, require_fbd=False)[1]
        logical, = [name for name in streams if name.endswith('.tsk')]
        value = bytearray(streams[logical]); value[-1] ^= 1
        damaged = replay_source_records(saved, {logical: value})
    else:
        before, current = live_graph_context_witness()
        source = read_project_context(before)
        if layer.startswith('node_'):
            node = source.program.nodes[0]
            node = replace(node, **{('object_flag' if layer == 'node_flags' else 'reserved'):
                (node.object_flag if layer == 'node_flags' else node.reserved) ^ 1})
            program = replace(source.program, nodes=(node, *source.program.nodes[1:]))
        else:
            wire = source.program.wires[0]
            if layer == 'wire_prefix': wire = replace(wire, prefix_fields=(*wire.prefix_fields[:-1], wire.prefix_fields[-1] ^ 1))
            else: wire = replace(wire, suffix=wire.suffix ^ 1)
            program = replace(source.program, wires=(wire, *source.program.wires[1:]))
        damaged = replay_source_records(before, {source.program.logical_name: serialize_structured_pou(program)})
    current['_revision'] = 6
    compilation = dict(status='passed', errors=0, origin='current_native_compile_and_active_source',
        identity=deepcopy(current['identity']), source_unchanged=True, revision=6, compile_run_id='current-receipt')
    with pytest.raises(ValueError): verify_active_saved_context(damaged, current, before, compilation=compilation)


@pytest.mark.parametrize('layer', ['inventory','declaration','st','graph','configuration','tasks','identity'])
def test_live_change_refuses_unrelated_current_source_changes(layer):
    from gxw.live_context import prepare_live_command, verify_live_change
    from gxw.models import GXWFormatError
    from gxw.project_templates import blank_fbd_baseline
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.fb_authoring import _context
    raw = blank_fbd_baseline('FX3U/FX3UC')
    before = live_native_context_witness(raw)
    name = before['inventory']['globals'][0]
    command = dict(operation='edit_global_labels',source_name=name,declarations=[dict(name='LiveFlag',data_type='BOOL')])
    prepared = prepare_live_command(raw,before,command,device_profile='FX3U')
    document = _context(raw)[3].declarations[name+'.gh']
    changed = replay_source_records(raw,{name+'.gh':serialize_declarations(edit_declarations(document,upserts=command['declarations']))})
    after = live_native_context_witness(changed)
    assert verify_live_change(before,after,command,prepared)['status'] == 'matched'
    if layer == 'identity':
        after['identity']['process_started'] = 'Another start'
    elif layer == 'configuration':
        after['configuration']['ranges']['D'] = [42,88]
    elif layer == 'tasks':
        after['tasks']['tasks'][0]['priority'] += 1
    elif layer == 'inventory':
        after['inventory']['structures'].append('UnobservedStructure')
    elif layer == 'declaration':
        local = next(row for row in after['tables'] if row['owner_kind']=='program')
        local['declarations'].append(dict(raw_fields={'标签名':'Injected'}))
    elif layer == 'st':
        after['st'].append(dict(owner='Foreign',owner_kind='program'))
    else:
        after['graphs'][0]['networks'][0]['wires'].append(dict(index=0,start=[2,3],end=[8,3],selected=False))
    with pytest.raises((GXWFormatError,KeyError)):
        verify_live_change(before,after,command,prepared)


@pytest.mark.parametrize('cpu', _blank_menu_cases())
def test_global_declaration_plan_matches_saved_members_to_the_existing_native_menu(cpu):
    from gxw.callable_sources import ProjectCallableSources
    from gxw.fb_authoring import _global_plan
    names = {cpu,*cpu.split('/')}
    for saved_cpu in names:
        sources = ProjectCallableSources(saved_cpu,'POU_01.Program.pou',{}, {})
        for selected_cpu in names:
            command = dict(operation='create_global_labels',source_name='NewGlobals',
                declarations=[dict(name='NewFlag',data_type='BOOL')],device_profile=selected_cpu)
            plan,_,rows,_ = _global_plan(dict(cpu=saved_cpu,codepage=936),sources,command,'POU_01.Program.pou')
            assert plan['cpu']==saved_cpu  # Do not rewrite the actual saved CPU.
            assert plan['create_table'] is True and plan['source_name']=='NewGlobals'
            assert len(rows)==1 and rows[0]['name']=='NewFlag' and rows[0]['data_type']=='BOOL'


@pytest.mark.parametrize('saved_cpu,selected_cpu', [
    ('L02','L06/L06-P'),('L06','L26/L26-P'),('L26','Q03UDV'),('Q03UDV','FX3S'),
    ('FX3S','FX3G/FX3GC'),('FX3G/FX3GC','FX3U/FX3UC'),('UNKNOWN','FX3S'),('L02','UNKNOWN'),
])
def test_global_declaration_plan_refuses_a_different_or_unknown_native_menu(saved_cpu,selected_cpu):
    from gxw.fb_authoring import _global_plan
    with pytest.raises(ValueError,match='global device profile differs'):
        _global_plan(dict(cpu=saved_cpu,codepage=936),None,dict(operation='create_global_labels',
            source_name='NewGlobals',declarations=[],device_profile=selected_cpu),'POU_01.Program.pou')


@pytest.mark.parametrize('compiled', [False, True])
@pytest.mark.parametrize('damage', ['body','global','range','parameter','task','library','compile_option'])
def test_live_stage_saved_readback_refuses_wrong_data_and_retained_unknown_fields(damage, compiled):
    from gxw.live_context import verify_active_saved_context
    from gxw.models import GXWFormatError
    from gxw.fb_authoring import _context
    from gxw.project_templates import blank_fbd_baseline
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.project_metadata import read_project_compile_options, read_project_system_variables, read_task_source
    import struct
    raw = blank_fbd_baseline('FX3U/FX3UC')
    context = live_native_context_witness(raw)
    context['_revision'] = 4
    compilation = (dict(status='passed', errors=0, origin='current_native_compile_and_active_source',
        identity=deepcopy(context['identity']), source_unchanged=True, revision=4, compile_run_id='current-receipt')
        if compiled else None)
    _,streams,groups,sources,_ = _context(raw)
    if damage=='body':
        graph = context['graphs'][0]
        graph['networks'][0]['wires'].append(dict(index=0,start=[2,3],end=[8,3],selected=False))
        changed = raw
    else:
        if damage=='global':
            logical,doc = next((name,row) for name,row in sources.declarations.items() if row.scope=='global')
            value = serialize_declarations(edit_declarations(doc,upserts=[dict(name='Hidden',data_type='BOOL')]))
        elif damage=='range':
            logical, = [name for name in streams if name.endswith('.prj')]
            value = bytearray(streams[logical])
            struct.pack_into('<II',value,read_project_system_variables(value)['offset'],2000,2999)
        elif damage=='parameter':
            logical='Param.wpa';value=bytearray(streams[logical]);value[-1]^=1
        elif damage=='task':
            logical, = [name for name in streams if name.endswith('.tsk')]
            value=bytearray(streams[logical]);value[read_task_source(value)['count_offset']-8]^=1
        elif damage=='compile_option':
            logical, = [name for name in streams if name.endswith('.prj')]
            value=bytearray(streams[logical]);value[read_project_compile_options(value)['count_offset']+4]^=1
        else:
            logical=next(name for name in streams if name.endswith('.lif'))
            value=streams[logical]+b'UnrequestedBytes'
        changed = replay_source_records(raw,{logical:value})
    with pytest.raises(GXWFormatError):
        verify_active_saved_context(changed,context,raw,compilation=compilation)


@pytest.mark.parametrize('create', [False, True])
def test_live_stage_save_keeps_current_st_body_tasks_and_unobserved_initializers(create):
    from gxw.live_context import prepare_live_command, verify_active_saved_context, verify_live_change
    before,saved,command,_ = background_st_program_witness(create)
    original,current = live_native_context_witness(before),live_native_context_witness(saved)
    if create:
        task = next(row for row in original['tasks']['tasks'] if (row['resource'],row['task'])==(command['resource'],command['task']))
        current['tasks']['confirmed_appends'].append(dict(resource=command['resource'],task=command['task'],
            program=command['source_name'],line=1+max([0]+[row['line'] for row in task['programs']])))
    prepared=prepare_live_command(before,original,command,device_profile='FX3U')
    assert verify_live_change(original,current,command,prepared)['status']=='matched'
    result=verify_active_saved_context(saved,current,before)
    assert result['status']=='matched' and result['ordinary_initial_values']=='unavailable_in_csv'
    assert result['tasks']=='matched' and result['unobserved_ordinary_initializers']>=0


def live_graph_context_witness():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context,generate_object_project
    from tests.test_gxw_active_graph import native_connections
    raw=blank_fbd_baseline('FX3U/FX3UC')
    source=read_project_context(raw)
    model=source.empty_model()
    model.update(nodes=[dict(id='signal',template='input',symbol='X0',x=3,y=2),
                        dict(id='coil',template='output',symbol='Y0',x=18,y=2)],
                 wires=[{'from':'signal.OUT','to':'coil.IN'}])
    raw=generate_object_project(model,baseline=raw).data
    context=live_native_context_witness(raw)
    graph=context['graphs'][0]
    graph.update(data_id=123,document_handle=200,view_handle=201,
                 native_edit_state=dict(base_mode=0x643,inline_editor=0,network_clipboard_active=0))
    for node in graph['networks'][0]['nodes']:
        flags=2 if node['source_kind_code']==13 else 3
        node['connections']=native_connections([('',2 if flags==2 else 0,1,flags)])
    return raw,context


def test_live_context_ignores_lazy_connection_cache_but_refuses_observed_port_contradictions():
    from gxw.live_context import live_context_state,verify_live_observations
    from gxw.models import GXWFormatError
    from hypothesis import given,settings,strategies as st
    _,current=live_graph_context_witness()
    lazy=deepcopy(current)
    for node in lazy['graphs'][0]['networks'][0]['nodes']:
        node['connections']=dict(status='not_built',ports=[])
    assert live_context_state(lazy)==live_context_state(current)
    assert verify_live_observations(lazy,current)['source']=='unchanged'
    @settings(max_examples=15,deadline=None,derandomize=True)
    @given(address=st.integers(0x20000,0x70000000),selection=st.booleans())
    def check(address,selection):
        rebuilt=deepcopy(current)
        graph=rebuilt['graphs'][0]
        graph.update(data_id=address,document_handle=address+10)
        for index,node in enumerate(graph['networks'][0]['nodes']):
            node.update(address=address+index*100,record='transient memory',selected=selection)
        assert verify_live_observations(current,rebuilt)['status']=='matched'
    check()
    changed=deepcopy(current)
    changed['graphs'][0]['networks'][0]['nodes'][0]['connections']['ports'][0]['local_x']+=1
    assert live_context_state(current)==live_context_state(changed)
    with pytest.raises(GXWFormatError,match='became unconfirmed'):
        verify_live_observations(current,changed)


@pytest.mark.parametrize('action',[dict(action='rebind_terminal',node='b0:n0',symbol='X1'),
    dict(action='move_node',node='b0:n0',x=4,y=6),dict(action='delete_terminal',node='b0:n0'),
    dict(action='delete_wire',wire='b0:w0'),
    {'action':'add_wire','id':'newPath','from':{'node':'b0:n0','port':'OUT'},'to':{'node':'b0:n1','port':'IN'},'via':[]}])
def test_live_graph_dispatch_uses_current_types_and_preserves_unrelated_context(action):
    from gxw.live_context import prepare_live_command,verify_live_change
    from gxw.models import GXWFormatError
    raw,before=live_graph_context_witness()
    network=before['graphs'][0]['networks'][0]
    if action['action']=='add_wire':network['wires']=[]
    command=dict(operation='edit_fbd_graph',caller=before['graphs'][0]['owner']+'.Program.pou',actions=[{**action,'network':0}])
    prepared=prepare_live_command(raw,before,command,device_profile='FX3U')
    after=deepcopy(before);target=after['graphs'][0]['networks'][0]
    kind=action['action']
    if kind=='rebind_terminal':target['nodes'][0]['name']='X1'
    elif kind=='move_node':target['nodes'][0]['bounds']=[4,6,6,8]
    elif kind=='delete_terminal':
        target['nodes'].pop(0);target['nodes'][0]['index']=0
    elif kind=='delete_wire':target['wires']=[]
    else:target['wires']=[dict(index=0,start=[5,3],end=[18,3],selected=False)]
    assert verify_live_change(before,after,command,prepared)['status']=='matched'
    after['configuration']['ranges']['D']=[123,456]
    with pytest.raises(GXWFormatError,match='unrelated'):
        verify_live_change(before,after,command,prepared)
    with pytest.raises(GXWFormatError,match='exactly one action'):
        prepare_live_command(raw,before,{**command,'actions':command['actions']*2},device_profile='FX3U')


@lru_cache(maxsize=1)
def live_new_st_fb_call_witness():
    """The FB and its caller were created after this lab session's baseline."""
    root = private_path('research/experiments/native-live-session-20261018')
    baseline = (root / 'baseline.gxw').read_bytes()
    current = live_native_context_witness((root / '054-restore-measured-compile-option/native-restored.gxw').read_bytes())
    from gxw.live_context import active_callable_sources
    from gxw.fb_authoring import _rows
    sources = active_callable_sources(baseline, current['tables'], owner='POU_01', identity=current['identity'])
    rows = [{key: value for key, value in row.items() if key != 'initial_value'}
            for row in _rows(sources.declarations['Live_ST_FB.Labels.lh'], sources)]
    body = next(row['st_text'] for row in current['st'] if row['owner'] == 'Live_ST_FB')
    return baseline, current, dict(operation='edit_st_fb', source_name='Live_ST_FB', declarations=rows, st_text=body)


@lru_cache(maxsize=1)
def live_label_delete_native_witness():
    root=private_path('research/experiments/native-live-session-20261018')
    baseline=(root/'054-restore-measured-compile-option/native-restored.gxw').read_bytes()
    before=json.loads((root/'064-unused-local-first-delete-command/before.json').read_text(encoding='utf-8'))
    after=json.loads((root/'066-reconcile-observed-cursor-row-delete/after.json').read_text(encoding='utf-8'))
    return baseline,before,after,dict(operation='delete_label',scope='program',source_name='POU_01',name='LocalCounter')


def _live_delete_st_source(context,owner,text):
    from gxw.active_context import read_active_st_text
    next(row for row in context['inventory']['pous'] if row['owner']==owner)['language']='st'
    context['graphs']=[row for row in context['graphs'] if row['owner']!=owner]
    context['st']=[row for row in context['st'] if row['owner']!=owner]
    context['st'].append(read_active_st_text(text,owner=owner,kind='program',identity=context['identity'],
        document_handle=700,document_title=owner+' [PRG] 程序本体 [ST]',editor_handle=701))


@pytest.mark.parametrize('data_type',['INT','BOOL'])
def test_live_label_delete_checks_real_native_single_row_deletion_and_scalar_bool_core_variant(data_type):
    from gxw.live_context import prepare_live_command,verify_active_label_delete,verify_live_change
    baseline,original,removed,command=live_label_delete_native_witness()
    before,after=deepcopy(original),deepcopy(removed)
    if data_type=='BOOL':
        table=next(row for row in before['tables'] if row['owner']=='POU_01')
        fields=deepcopy([row['raw_fields'] for row in table['declarations']])
        next(row for row in fields if row['标签名']=='LocalCounter')['数据类型']='BOOL'
        before['tables'][before['tables'].index(table)]=_live_fb_guard_table(before,'POU_01','program',fields)
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    assert prepared['kind']=='active_label_delete' and prepared['scope']=='program'
    assert prepared['before']==next(row for row in before['tables'] if row['owner']=='POU_01')
    assert (prepared['plan']['source_name'],prepared['plan']['name'],prepared['plan']['data_type'],prepared['plan']['csv_row'])==(
        'POU_01','LocalCounter',data_type,1)
    assert 'rows' not in prepared['plan']  # A native row command, not a replacement/import.
    table=next(row for row in after['tables'] if row['owner']=='POU_01')
    assert verify_active_label_delete(prepared,table)['status']=='matched'
    result=verify_live_change(before,after,command,prepared)
    assert result['status']=='matched' and result['unrelated_sources']=='matched' and not result['save_performed']
    assert sum(len(network['nodes']) for graph in after['graphs'] for network in graph['networks'])==8


@pytest.mark.parametrize('damage',['global','fb','wrong_owner','missing_name','duplicate_name','constant','formal',
    'fb_instance','structure','array','dint','device','iec_address','missing_mapping_columns','row_order','incomplete_context'])
def test_live_label_delete_rejects_unproven_scope_and_nonordinary_or_ambiguous_rows(damage):
    from gxw.live_context import prepare_live_command
    baseline,original,_,original_command=live_label_delete_native_witness()
    before,command=deepcopy(original),deepcopy(original_command)
    table=next(row for row in before['tables'] if row['owner']=='POU_01')
    target=table['declarations'][1]
    if damage in ('global','fb'):command['scope']=damage
    elif damage=='wrong_owner':command['source_name']='Global1'
    elif damage=='missing_name':command['name']='Missing'
    elif damage=='duplicate_name':table['declarations'].append({**deepcopy(target),'csv_row':2,'name':'localcounter'})
    elif damage=='constant':target.update(class_code=2,class_name='VAR_CONSTANT',csv_constant='1')
    elif damage=='formal':target.update(class_code=3,class_name='VAR_INPUT')
    elif damage=='fb_instance':command['name']='Live_Inst'
    elif damage=='structure':target.update(data_type='UnknownStruct',kind='structure')
    elif damage=='array':target['data_type']='ARRAY [0..1] OF INT'
    elif damage=='dint':target['data_type']='DINT'
    elif damage=='device':target['device']='D100'
    elif damage=='iec_address':target['iec_address']='%MW0.100'
    elif damage=='missing_mapping_columns':table['csv_columns'].remove('软元件')
    elif damage=='row_order':target['csv_row']=True
    elif damage=='incomplete_context':before['st'].pop()
    with pytest.raises(ValueError):prepare_live_command(baseline,before,command,device_profile='FX3U')


@pytest.mark.parametrize('syntax',['direct_st','direct_graph','qualified','indexing','pointer','indexed_device',
                                 'call','unknown_symbol','unclosed_comment','unknown_graph','nonempty_attribute'])
def test_live_label_delete_refuses_current_reference_or_uncovered_indirect_source(syntax):
    from gxw.live_context import prepare_live_command
    baseline,original,_,command=live_label_delete_native_witness()
    before=deepcopy(original)
    bodies={'direct_st':'LocalCounter := LocalCounter + 1;',
        'qualified':'LocalCounter := Other.Value;', 'indexing':'Other[0] := 1;',
        'pointer':'Other^ := 1;', 'indexed_device':'D0Z0 := 1;',
        'call':'UnknownFunction();', 'unknown_symbol':'Unknown := 1;', 'unclosed_comment':'(* LocalCounter'}
    if syntax in bodies:_live_delete_st_source(before,'POU_01',bodies[syntax])
    elif syntax=='direct_graph':
        before['graphs'][0]['networks'][0]['nodes'][1]['name']='LocalCounter'
    elif syntax=='unknown_graph':before['graphs'][0]['networks'][0]['nodes'][1]['source_kind_code']=0x50
    else:
        table=next(row for row in before['tables'] if row['owner_kind']=='global')
        table['declarations'][0]['raw_fields']['属性']='unknown indirect association'
    with pytest.raises(ValueError):prepare_live_command(baseline,before,command,device_profile='FX3U')


def test_live_label_delete_ignores_comments_strings_and_identifier_prefixes_with_known_scope():
    from gxw.live_context import prepare_live_command
    baseline,original,_,command=live_label_delete_native_witness()
    before=deepcopy(original)
    table=next(row for row in before['tables'] if row['owner']=='POU_01')
    fields=deepcopy([row['raw_fields'] for row in table['declarations']])
    fields.extend([{'类':'VAR','标签名':'LocalCounter2','数据类型':'INT','常量':'','注释':''},
                   {'类':'VAR','标签名':'TextValue','数据类型':'STRING','常量':'','注释':''}])
    before['tables'][before['tables'].index(table)]=_live_fb_guard_table(before,'POU_01','program',fields)
    _live_delete_st_source(before,'POU_01',"(* LocalCounter in a valid block comment *)\n// LocalCounter\n"
        "TextValue := 'LocalCounter';\nLocalCounter2 := LocalCounter2 + 1;")
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    assert prepared['plan']['name']=='LocalCounter'


@pytest.mark.parametrize('source', ['(* outer (* nested *) *) LocalCounter2 := 1;',
    '(* unterminated comment LocalCounter', 'IF TRUE THEN LocalCounter2 := 1;'])
def test_live_label_delete_cannot_prove_absence_through_invalid_gx_source(source):
    from gxw.live_context import _label_delete_atoms
    from gxw.models import GXWFormatError
    with pytest.raises(GXWFormatError, match='fully covered direct ST syntax'):
        _label_delete_atoms(source, body=True)


def test_live_label_delete_preserves_a_used_same_named_local_in_another_program_scope():
    from gxw.live_context import prepare_live_command,verify_live_change
    baseline,original,_,command=live_label_delete_native_witness()
    before=deepcopy(original)
    table=next(row for row in before['tables'] if row['owner']=='Live_ST_Program')
    fields=deepcopy([row['raw_fields'] for row in table['declarations']])
    fields.append({'类':'VAR','标签名':'LocalCounter','数据类型':'INT','常量':'','注释':'other owner'})
    before['tables'][before['tables'].index(table)]=_live_fb_guard_table(before,'Live_ST_Program','program',fields)
    _live_delete_st_source(before,'Live_ST_Program','LocalCounter := LocalCounter + 1;')
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    after=deepcopy(before)
    table=next(row for row in after['tables'] if row['owner']=='POU_01')
    fields=[row['raw_fields'] for row in table['declarations'] if row['name']!='LocalCounter']
    after['tables'][after['tables'].index(table)]=_live_fb_guard_table(after,'POU_01','program',fields)
    assert verify_live_change(before,after,command,prepared)['status']=='matched'
    retained=next(row for row in after['tables'] if row['owner']=='Live_ST_Program')
    assert any(row['name']=='LocalCounter' for row in retained['declarations'])


def test_live_label_delete_refuses_ambiguous_global_local_name_before_dispatch():
    from gxw.live_context import prepare_live_command
    baseline,original,_,command=live_label_delete_native_witness()
    before=deepcopy(original)
    table=next(row for row in before['tables'] if row['owner_kind']=='global')
    fields=deepcopy([row['raw_fields'] for row in table['declarations']])
    fields.append({'类':'VAR_GLOBAL','标签名':'LocalCounter','数据类型':'INT','常量':'','注释':''})
    before['tables'][before['tables'].index(table)]=_live_fb_guard_table(before,table['owner'],'global',fields)
    with pytest.raises(ValueError,match='ambiguous local/global'):
        prepare_live_command(baseline,before,command,device_profile='FX3U')


@pytest.mark.parametrize('column',['软元件','地址'])
def test_live_label_delete_refuses_explicit_raw_mapping_even_with_a_conflicting_empty_projection(column):
    from gxw.live_context import prepare_live_command
    baseline,original,_,command=live_label_delete_native_witness()
    before=deepcopy(original)
    table=next(row for row in before['tables'] if row['owner']=='POU_01')
    table['declarations'][1]['raw_fields'][column]='D100' if column=='软元件' else '%MW0.100'
    with pytest.raises(ValueError,match='unmapped ordinary scalar'):
        prepare_live_command(baseline,before,command,device_profile='FX3U')


@pytest.mark.parametrize('field,value',[('scope','fb'),('source_name','Live_ST_Program'),('name','Live_Inst')])
def test_live_label_delete_readback_cannot_rebind_the_prepared_command_to_another_target(field,value):
    from gxw.live_context import prepare_live_command,verify_live_change
    baseline,before,after,command=live_label_delete_native_witness()
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    with pytest.raises(ValueError,match='prepared exact scope and target'):
        verify_live_change(before,after,{**command,field:value},prepared)


def test_live_label_delete_keeps_every_non_target_native_csv_field_exact():
    from hypothesis import given,settings,strategies as st
    from gxw.live_context import prepare_live_command,verify_active_label_delete
    baseline,before,after,command=live_label_delete_native_witness()
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    native=next(row for row in after['tables'] if row['owner']=='POU_01')
    @settings(max_examples=20,deadline=None,derandomize=True)
    @given(column=st.sampled_from(native['csv_columns']),text=st.text(alphabet='ABC 012注释\t\n"',max_size=20))
    def check(column,text):
        changed=deepcopy(native)
        changed['declarations'][0]['raw_fields'][column]='mutation:'+text
        with pytest.raises(ValueError,match='preserve all other fields'):
            verify_active_label_delete(prepared,changed)
    check()


@pytest.mark.parametrize('damage',['no_delete','wrong_row','extra_row','retained_field','reordered','identity','binding_owner',
                                  'columns','title','other_table','st','graph','ports','tasks','configuration'])
def test_live_label_delete_readback_requires_exact_row_removal_and_preserves_all_other_pages(damage):
    from gxw.live_context import prepare_live_command,verify_live_change
    baseline,before,original,command=live_label_delete_native_witness()
    prepared=prepare_live_command(baseline,before,command,device_profile='FX3U')
    after=deepcopy(original)
    table=next(row for row in after['tables'] if row['owner']=='POU_01')
    if damage=='no_delete':table['declarations']=deepcopy(prepared['before']['declarations'])
    elif damage=='wrong_row':table['declarations']=[{**deepcopy(prepared['target']),'csv_row':0}]
    elif damage=='extra_row':table['declarations']=[]
    elif damage=='retained_field':table['declarations'][0]['raw_fields']['注释']='Unexpected edit'
    elif damage=='reordered':table['declarations'][0]['csv_row']=1
    elif damage=='identity':table['source_binding']['identity']['process_started']='foreign'
    elif damage=='binding_owner':table['source_binding']['owner']='Live_ST_Program'
    elif damage=='columns':table['csv_columns']=list(reversed(table['csv_columns']))
    elif damage=='title':table['csv_title']='foreign export'
    elif damage=='other_table':after['tables'][0]['declarations'][0]['raw_fields']['注释']='Unexpected edit'
    elif damage=='st':after['st'][0]['st_text']+='\nProgramCount := 0;'
    elif damage=='graph':after['graphs'][0]['networks'][0]['nodes'][1]['name']='Live_Enable'
    elif damage=='ports':after['graphs'][0]['networks'][0]['nodes'][0]['connections']['ports'][0]['name']='WrongPort'
    elif damage=='tasks':after['tasks']['tasks'][0]['priority']+=1
    else:after['configuration']['ranges']['D'][0]+=1
    with pytest.raises(ValueError):verify_live_change(before,after,command,prepared)


def test_live_new_st_fb_then_instance_and_current_call_allows_incremental_body_edit():
    from gxw.live_context import prepare_live_command, verify_live_change
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    assert 'Live_ST_FB' not in {row['owner'] for row in live_native_context_witness(baseline)['inventory']['pous']}
    initial = deepcopy(current)
    for key in ('tables', 'st'):
        initial[key] = [row for row in initial[key] if row['owner'] != 'Live_ST_FB']
    initial['inventory']['pous'] = [row for row in initial['inventory']['pous'] if row['owner'] != 'Live_ST_FB']
    caller = next(row for row in initial['tables'] if row['owner'] == 'POU_01')
    caller['declarations'] = [row for row in caller['declarations'] if row['name'] != 'Live_Inst']
    for graph in initial['graphs']:
        for network in graph['networks']:
            network['nodes'] = [row for row in network['nodes'] if row['source_kind_code'] != 2]
            for index, row in enumerate(network['nodes']): row['index'] = index
    created = prepare_live_command(baseline, initial, {**command, 'operation':'create_st_fb'}, device_profile='FX3U')
    assert created['create'] and created['existing_fb_calls'] == []
    declared = deepcopy(initial)
    declared['inventory']['pous'].append(next(row for row in current['inventory']['pous'] if row['owner']=='Live_ST_FB'))
    declared['tables'].append(next(row for row in current['tables'] if row['owner']=='Live_ST_FB'))
    declared['st'].append(next(row for row in current['st'] if row['owner']=='Live_ST_FB'))
    labels = prepare_live_command(baseline, declared, dict(operation='edit_pou_labels', source_name='POU_01',
        declarations=[dict(name='Live_Inst',data_type='Live_ST_FB',kind='function_block')]), device_profile='FX3U')
    assert next(row for row in labels['plan']['rows'] if row['name']=='Live_Inst')['data_type']=='Live_ST_FB'
    command.pop('declarations')
    command['st_text'] += '\nAccum := Accum + 1;\n'
    prepared = prepare_live_command(baseline, current, command, device_profile='FX3U')
    call, = prepared['existing_fb_calls']
    assert (call['caller'],call['instance'],call['declaration_source'],call['definition']) == (
        'POU_01','Live_Inst','POU_01.Labels.lh','Live_ST_FB')
    after = deepcopy(current)
    next(row for row in after['st'] if row['owner']=='Live_ST_FB')['st_text'] = command['st_text']
    assert not prepared['plan']['write_declarations']
    assert verify_live_change(current,after,command,prepared)['status']=='matched'


@pytest.mark.parametrize('change', ['internal_add','internal_type','internal_order','formal_comment'])
def test_live_called_st_fb_allows_internal_var_and_comment_edits_without_changing_ports(change):
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    rows = command['declarations']
    if change=='internal_add': rows.append(dict(name='PrivateCount',data_type='INT',class_name='VAR'))
    elif change=='internal_type': next(row for row in rows if row['name']=='Accum')['data_type']='DINT'
    elif change=='internal_order': rows.insert(0,rows.pop())
    elif change=='formal_comment': rows[0]['comment']='same Enable input, updated comment'
    prepared = prepare_live_command(baseline,current,command,device_profile='FX3U')
    assert prepared['existing_fb_calls'][0]['instance']=='Live_Inst'
    assert prepared['plan']['write_declarations']


@pytest.mark.parametrize('change', ['input_name','input_type','input_class','input_order','output_order',
                                   'formal_order','formal_remove','input_add','inout_add','output_type'])
def test_live_called_st_fb_rejects_interface_migration_before_native_apply(change):
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    rows = command['declarations']
    if change=='input_name': rows[0]['name']='RenamedEnable'
    elif change=='input_type': rows[0]['data_type']='INT'
    elif change=='input_class': rows[0]['class_name']='VAR'
    elif change=='input_order': rows[0],rows[1]=rows[1],rows[0]
    elif change=='output_order': rows[2],rows[3]=rows[3],rows[2]
    elif change=='formal_order': rows.insert(0,rows.pop(2))
    elif change=='formal_remove': rows.pop(0)
    elif change=='input_add': rows.append(dict(name='AdditionalInput',data_type='INT',class_name='VAR_INPUT'))
    elif change=='inout_add': rows.append(dict(name='Shared',data_type='WORD',class_name='VAR_IN_OUT'))
    elif change=='output_type': rows[3]['data_type']='DINT'
    with pytest.raises(ValueError,match='interface migration.*POU_01/Live_Inst'):
        prepare_live_command(baseline,current,command,device_profile='FX3U')


def test_live_called_st_fb_signature_keeps_formal_order_independent_of_internal_var_position():
    from hypothesis import example, given, settings, strategies as st
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    native_order=('Enable','Value','Q','NextValue')
    @settings(max_examples=20,deadline=None,derandomize=True)
    @example(order=native_order,internal=0)
    @example(order=('Value','Enable','Q','NextValue'),internal=4)
    @given(order=st.permutations(native_order),internal=st.integers(0,4))
    def check(order,internal):
        command=deepcopy(original_command)
        rows={row['name']:row for row in command['declarations']}
        requested=[rows[name] for name in order]
        requested.insert(internal,rows['Accum'])
        command['declarations']=requested
        if tuple(order)==native_order:
            assert prepare_live_command(baseline,original,command,device_profile='FX3U')['existing_fb_calls'][0]['instance']=='Live_Inst'
        else:
            with pytest.raises(ValueError,match='interface migration.*POU_01/Live_Inst'):
                prepare_live_command(baseline,original,command,device_profile='FX3U')
    check()


@pytest.mark.parametrize('damage',['missing_instance','different_declared_type','different_native_type','unknown_port_type'])
def test_live_st_fb_guard_rejects_unresolved_current_call_or_interface(damage):
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    instance = next(row for row in current['tables'] if row['owner']=='POU_01')
    if damage=='missing_instance':
        instance['declarations']=[row for row in instance['declarations'] if row['name']!='Live_Inst']
    elif damage=='different_declared_type':
        row=next(row for row in instance['declarations'] if row['name']=='Live_Inst')
        row.update(data_type='INT',kind='variable')
    elif damage=='different_native_type':
        current['graphs'][0]['networks'][0]['nodes'][0]['type_name']='Other_FB'
    else:
        row=next(row for row in next(table for table in current['tables'] if table['owner']=='Live_ST_FB')['declarations'] if row['name']=='Value')
        row.update(data_type='UnknownType',kind='variable')
    command.pop('declarations')
    with pytest.raises(ValueError,match='POU_01/Live_Inst'):
        prepare_live_command(baseline,current,command,device_profile='FX3U')


def _live_fb_guard_table(context, owner, scope, rows, columns=None):
    import csv
    import io
    from gxw.active_context import read_active_labels_csv
    old = next((row for row in context['tables'] if (row['owner'],row['owner_kind'])==(owner,scope)),None)
    columns = columns or old['csv_columns']
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer,delimiter='\t',lineterminator='\r\n')
    writer.writerow([old['csv_title'] if old else 'scope guard fixture']);writer.writerow(columns)
    for row in rows:writer.writerow([row.get(column,'') for column in columns])
    title = ({'global':'全局标签设置 ','program':'局部标签设置 ','fb':'函数/FB标签设置 '}[scope]
             +owner+(' [PRG]' if scope=='program' else ' [FB]' if scope=='fb' else ''))
    return read_active_labels_csv(buffer.getvalue().encode('utf-16'),owner=owner,scope=scope,
        identity=context['identity'],document_handle=500+len(context['tables']),document_title=title,
        type_sources=[row for row in context['tables'] if row['owner_kind'] in ('fb','structure')])


def _live_fb_guard_other_definition(context):
    from gxw.active_context import read_active_st_text
    target = next(row for row in context['tables'] if row['owner']=='Live_ST_FB')
    other = _live_fb_guard_table(context,'Other_ST_FB','fb',
        [row['raw_fields'] for row in target['declarations']],target['csv_columns'])
    context['tables'].append(other)
    context['inventory']['pous'].append(dict(owner='Other_ST_FB',owner_kind='fb',language='st'))
    body = next(row for row in context['st'] if row['owner']=='Live_ST_FB')
    context['st'].append(read_active_st_text(body['st_text'],owner='Other_ST_FB',kind='fb',
        identity=context['identity'],document_handle=600,document_title='Other_ST_FB [FB] 程序本体 [ST]',editor_handle=601))


@pytest.mark.parametrize('same_definition',[False,True])
def test_live_st_fb_guard_keeps_same_named_instances_in_distinct_program_scopes(same_definition):
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    _live_fb_guard_other_definition(current)
    table = next(row for row in current['tables'] if row['owner']=='Live_ST_Program')
    rows = [row['raw_fields'] for row in table['declarations']]
    rows.append({'类':'VAR','标签名':'Live_Inst','数据类型':'Live_ST_FB' if same_definition else 'Other_ST_FB','常量':'','注释':''})
    current['tables'][current['tables'].index(table)] = _live_fb_guard_table(current,'Live_ST_Program','program',rows)
    next(row for row in current['inventory']['pous'] if row['owner']=='Live_ST_Program')['language']='fbd'
    current['st']=[row for row in current['st'] if row['owner']!='Live_ST_Program']
    graph=deepcopy(current['graphs'][0]);graph['owner']='Live_ST_Program'
    graph['networks']=graph['networks'][:1]
    graph['networks'][0]['nodes']=graph['networks'][0]['nodes'][:1]
    graph['networks'][0]['nodes'][0]['type_name']='Live_ST_FB' if same_definition else 'Other_ST_FB'
    graph['networks'][0]['wires']=[]
    current['graphs'].append(graph)
    body={key:value for key,value in command.items() if key!='declarations'}
    prepared=prepare_live_command(baseline,current,body,device_profile='FX3U')
    owners={row['caller'] for row in prepared['existing_fb_calls']}
    assert owners==({'POU_01','Live_ST_Program'} if same_definition else {'POU_01'})
    command['declarations'][0]['data_type']='INT'
    with pytest.raises(ValueError,match='interface migration') as error:
        prepare_live_command(baseline,current,command,device_profile='FX3U')
    assert ('Live_ST_Program/Live_Inst' in str(error.value)) is same_definition


def test_live_st_fb_without_a_bound_call_keeps_existing_interface_edit_support():
    from gxw.live_context import prepare_live_command
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    _live_fb_guard_other_definition(current)
    table = next(row for row in current['tables'] if row['owner']=='POU_01')
    fields=deepcopy([row['raw_fields'] for row in table['declarations']])
    next(row for row in fields if row['标签名']=='Live_Inst')['数据类型']='Other_ST_FB'
    current['tables'][current['tables'].index(table)]=_live_fb_guard_table(current,'POU_01','program',fields)
    current['graphs'][0]['networks'][0]['nodes'][0]['type_name']='Other_ST_FB'
    command['declarations'][0]['data_type']='INT'
    prepared=prepare_live_command(baseline,current,command,device_profile='FX3U')
    assert prepared['existing_fb_calls']==[]
    assert prepared['plan']['rows'][0]['data_type']=='INT'


@pytest.mark.parametrize('hiding',[False,True])
def test_live_st_fb_guard_uses_core_global_local_shadowing_option(hiding):
    import struct
    from gxw.live_context import prepare_live_command
    from gxw.fb_authoring import _context
    from gxw.project_metadata import read_project_compile_options
    baseline, original, original_command = live_new_st_fb_call_witness()
    current, command = deepcopy(original), deepcopy(original_command)
    _live_fb_guard_other_definition(current)
    streams=_context(baseline,require_fbd=False)[1]
    logical,= [name for name in streams if name.endswith('.prj')]
    source=streams[logical];options=read_project_compile_options(source);offset=options['count_offset']
    values=[value for value in options['values'][:-1] if value&0xffff0000!=0x10010000]+[0x10010000|int(hiding),0xffffffff]
    old_end=offset+4+4*len(options['values'])
    source=source[:offset]+struct.pack('<I',len(values))+struct.pack('<'+'I'*len(values),*values)+source[old_end:]
    baseline=replay_source_records(baseline,{logical:source})
    table=next(row for row in current['tables'] if row['owner']=='POU_01')
    fields=deepcopy([row['raw_fields'] for row in table['declarations']])
    next(row for row in fields if row['标签名']=='Live_Inst')['数据类型']='Other_ST_FB'
    current['tables'][current['tables'].index(table)]=_live_fb_guard_table(current,'POU_01','program',fields)
    global_table=next(row for row in current['tables'] if row['owner_kind']=='global')
    fields=deepcopy([row['raw_fields'] for row in global_table['declarations']])
    fields.append({'类':'VAR_GLOBAL','标签名':'Live_Inst','数据类型':'Live_ST_FB','常量':'','软元件':'','地址':'','注释':''})
    current['tables'][current['tables'].index(global_table)]=_live_fb_guard_table(current,global_table['owner'],'global',fields)
    current['graphs'][0]['networks'][0]['nodes'][0]['type_name']='Other_ST_FB'
    command['declarations'][0]['data_type']='INT'
    if hiding:
        assert prepare_live_command(baseline,current,command,device_profile='FX3U')['existing_fb_calls']==[]
    else:
        with pytest.raises(ValueError,match='POU_01/Live_Inst.*ambiguous local/global'):
            prepare_live_command(baseline,current,command,device_profile='FX3U')


@pytest.mark.parametrize('change',['internal_var','formal_comment','formal_type','formal_class','formal_add'])
def test_live_fb_labels_share_the_st_existing_call_interface_guard(change):
    from gxw.live_context import prepare_live_command
    baseline, original, _ = live_new_st_fb_call_witness()
    current=deepcopy(original)
    row=(dict(name='AdditionalInput',data_type='INT',class_name='VAR_INPUT') if change=='formal_add' else
         dict(name='PrivateCount',data_type='INT',class_name='VAR') if change=='internal_var' else
         dict(name='Enable',data_type='BOOL',comment='same input, updated comment') if change=='formal_comment' else
         dict(name='Enable',data_type='INT') if change=='formal_type' else
         dict(name='Enable',data_type='BOOL',class_name='VAR'))
    command=dict(operation='edit_pou_labels',source_name='Live_ST_FB',declarations=[row])
    if change in ('internal_var','formal_comment'):
        assert prepare_live_command(baseline,current,command,device_profile='FX3U')['existing_fb_calls'][0]['instance']=='Live_Inst'
    else:
        with pytest.raises(ValueError,match='interface migration.*POU_01/Live_Inst'):
            prepare_live_command(baseline,current,command,device_profile='FX3U')


def test_live_st_body_only_operation_does_not_allow_a_target_interface_change():
    from gxw.live_context import prepare_live_command,verify_live_change
    from gxw.models import GXWFormatError
    before,saved,command,_=background_st_program_witness()
    initial,current=live_native_context_witness(before),live_native_context_witness(saved)
    prepared=prepare_live_command(before,initial,command,device_profile='FX3U')
    table=next(row for row in current['tables'] if row['owner']==command['source_name'])
    table['declarations'][0]['raw_fields']['注释']='Unrequested interface change'
    with pytest.raises(GXWFormatError,match='Body-only ST edit'):
        verify_live_change(initial,current,command,prepared)


def test_live_stage_save_binds_observed_native_ports_to_saved_direction_and_geometry():
    from dataclasses import replace
    from gxw.live_context import verify_active_saved_context
    from gxw.models import GXWFormatError
    from gxw.object_model import read_project_context
    from gxw.structured_pou_writer import serialize_structured_pou
    raw,current=live_graph_context_witness()
    validation=verify_active_saved_context(raw,current,raw)
    assert validation['native_port_nodes_matched']==2
    source=read_project_context(raw)
    first=source.program.nodes[0]
    changed=replace(first,ports=(replace(first.ports[0],port_kind_code=first.ports[0].port_kind_code^1),))
    program=replace(source.program,nodes=(changed,*source.program.nodes[1:]))
    damaged=replay_source_records(raw,{source.program.logical_name:serialize_structured_pou(program)})
    with pytest.raises(GXWFormatError,match='Saved graph ports differ'):
        verify_active_saved_context(damaged,current,raw)


@lru_cache(maxsize=1)
def q03udv_english_full_saved_checkpoint_witness():
    root = private_path('research/experiments/native-live-multitarget-20261018/q03udv-en-saved-checkpoint-01')
    files = ('original-baseline.gxw', 'saved-after.gxw', 'current-context.json')
    if any(not (root / name).is_file() for name in files):
        pytest.skip('private Q03UDV/en native source-save checkpoint unavailable')
    def decode(value):
        if set(value) == {'kind', 'base64'} and value['kind'] == 'exact_bytes':
            return base64.b64decode(value['base64'], validate=True)
        return value
    return ((root/files[0]).read_bytes(), (root/files[1]).read_bytes(),
        json.loads((root/files[2]).read_text(encoding='utf-8-sig'), object_hook=decode))


def test_q03udv_english_native_checkpoint_matches_all_pages_and_observed_pointer_default():
    from gxw.live_context import verify_active_saved_context
    before, saved, frozen = q03udv_english_full_saved_checkpoint_witness()
    context = deepcopy(frozen)
    result = verify_active_saved_context(saved, context, before)
    assert context == frozen
    assert all(result[key] == 'matched' for key in ('status', 'st', 'graphs', 'tasks', 'configuration'))
    assert result['native_port_nodes_matched'] == 5 and result['native_port_nodes_unavailable'] == 0
    assert result['native_range_initializations'] == [dict(device='P', before=[-1,-1], after=[2048,4095],
        origin='current_native_fbd_pointer_default_materialized', cpu_menu='Q03UDV', native_ui_language='en')]
    assert [(row['source'], row['native_binding']['operation']) for row in result['source_header_changes']] == [
        ('POU_01.Program.pou', 'edited_source_save'), ('Task_01.tsk', 'task_append')]
    assert result['compiled_header_revision'] is None and result['execution'] == 'not_run'


@pytest.mark.parametrize('damage', ['pointer_saved_range', 'retained_timer_range', 'pointer_ui_range',
    'pointer_ui_total', 'pointer_ui_unavailable', 'missing_pointer_grid', 'missing_append',
    'untouched_source_timestamp', 'compiled_flag', 'opaque_prefix'])
def test_q03udv_english_checkpoint_rejects_unobserved_defaults_or_source_save_changes(damage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from gxw.project_metadata import read_project_system_variables
    before, saved, frozen = q03udv_english_full_saved_checkpoint_witness()
    context = deepcopy(frozen)
    streams = _context(saved, require_fbd=False)[1]
    if damage in ('pointer_saved_range', 'retained_timer_range'):
        name, = [name for name in streams if name.endswith('.prj')]
        raw = bytearray(streams[name])
        start = read_project_system_variables(raw)['offset']
        index = 8 if damage == 'pointer_saved_range' else 3
        struct.pack_into('<II', raw, start + index*8,
            *( (2049,4096) if damage == 'pointer_saved_range' else (65,2047)))
        saved = replay_source_records(saved, {name:bytes(raw)})
    elif damage.startswith('pointer_ui_'):
        row, = [row for row in context['configuration']['grid_rows']['allocation'] if row['cells'][2]['value']=='P']
        if damage == 'pointer_ui_range': row['cells'][5]['value']='2049'
        elif damage == 'pointer_ui_total': row['cells'][7]['value']='2047'
        else: row['cells'][5]['state'] |= 1
    elif damage == 'missing_pointer_grid': context['configuration'].pop('grid_rows')
    elif damage == 'missing_append': context['tasks']['confirmed_appends']=[]
    else:
        name = 'LiveCycle.Program.pou' if damage == 'untouched_source_timestamp' else 'POU_01.Program.pou'
        raw = bytearray(streams[name])
        if damage == 'untouched_source_timestamp': raw[46:48]=(36).to_bytes(2,'little')
        elif damage == 'compiled_flag': struct.pack_into('<I',raw,50,0)
        else: raw[24] ^= 1
        saved = replay_source_records(saved,{name:bytes(raw)})
    with pytest.raises(ValueError):
        verify_active_saved_context(saved, context, before)


@pytest.mark.parametrize('damage', [None, 'no_identity', 'language', 'version', 'menu', 'codepage',
                                  'other_measured_codepage', 'st', 'partial'])
def test_q03udv_pointer_initialization_requires_exact_live_save_scope(damage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.project_metadata import native_fbd_pointer_initialization, read_project_system_variables
    before, _, context = q03udv_english_full_saved_checkpoint_witness()
    streams = _context(before, require_fbd=False)[1]
    name, = [name for name in streams if name.endswith('.prj')]
    metadata = bytearray(streams[name]);identity=deepcopy(context['identity']);kind=208
    if damage == 'no_identity': identity=None
    elif damage == 'language': identity['native_ui_language']='ja'
    elif damage == 'version': identity['software_version']='1.634.0.1'
    elif damage == 'menu': identity['cpu_menu']='Q04UDV'
    elif damage == 'codepage': identity['project_codepage']=1252
    elif damage == 'other_measured_codepage': identity['project_codepage']=932
    elif damage == 'st': kind=193
    elif damage == 'partial': struct.pack_into('<I',metadata,read_project_system_variables(metadata)['offset']+64,0)
    observed = native_fbd_pointer_initialization(metadata,kind,native_identity=identity)
    assert observed == (dict(offset=68,before=[-1,-1],after=[2048,4095]) if damage is None else None)


# One independently frozen Q03UDV English native Save; no native compile was
# performed. The task timestamp exception below is not a Q-family capability.
@lru_cache(maxsize=1)
def q03udv_english_native_stage_save_witness():
    from gxw.fb_authoring import _context
    root = private_path('research/experiments/native-live-multitarget-20261018/'
                        'q03udv-en-st-save-witness-continue2')
    if any(not (root / leaf).is_file() for leaf in
           ('baseline.gxw', 'saved-after.gxw', 'current-context-before-save.json')):
        pytest.skip('private Q03UDV English native stage-save witness unavailable')
    before, after = [(root / leaf).read_bytes() for leaf in ('baseline.gxw', 'saved-after.gxw')]
    context = json.loads((root / 'current-context-before-save.json').read_text(encoding='utf-8-sig'))
    tasks = [_context(raw, require_fbd=False)[1]['Task_01.tsk'] for raw in (before, after)]
    return before, after, context, *tasks


def test_q03udv_english_native_stage_save_matches_exact_current_inventory_and_task_append():
    from gxw.live_context import verify_active_saved_context
    from gxw.project_metadata import read_task_source
    before, after, frozen_context, task_before, task_after = q03udv_english_native_stage_save_witness()
    context = deepcopy(frozen_context)
    result = verify_active_saved_context(after, context, before)
    assert result['status'] == result['st'] == result['graphs'] == result['tasks'] == 'matched'
    assert result['configuration'] == 'matched' and result['libraries_and_structures'] == 'byte_exact'
    assert result['compiled_header_revision'] is None and result['compiled_header_run_id'] is None
    assert result['compilation'] == 'not_requested' and result['execution'] == 'not_run'
    assert result['source_header_changes'] == [dict(source='Task_01.tsk',
        status='native_header_metadata_inference', timestamp_before=[2026, 10, 0, 18, 19, 48, 17, 0],
        timestamp_after=[2026, 10, 0, 18, 20, 22, 57, 0], compile_pending_before=1, compile_pending_after=1,
        native_binding=dict(cpu_menu='Q03UDV', software_version='1.635.0.1',
                            native_ui_language='en', operation='task_append'))]
    assert read_task_source(task_before)['rows'][0]['name'] == 'POU_01'
    assert [(row['name'], row['line']) for row in read_task_source(task_after)['rows']] == [
        ('POU_01', 1), ('LiveCycle', 2)]
    assert context == frozen_context
    # Reversing saved/baseline is the original witness-runner mistake, not a
    # reason to loosen current-tree ownership or tolerate missing objects.
    with pytest.raises(ValueError, match='project objects differ'):
        verify_active_saved_context(before, context, after)


def test_q03udv_english_task_header_normalization_preserves_every_existing_task_field():
    from gxw.fb_authoring import _visible_task_append_header
    from gxw.project_metadata import read_task_source, verify_task_source_append
    _, _, context, before, after = q03udv_english_native_stage_save_witness()
    normalized, receipt = _visible_task_append_header(before, after, cpu='Q03UDV',
        native_identity=context['identity'])
    assert receipt['compile_pending_before'] == receipt['compile_pending_after'] == 1
    assert read_task_source(normalized)['header'] == read_task_source(before)['header']
    assert normalized[54:] == after[54:]
    assert verify_task_source_append(before, normalized, program_name='LiveCycle', line=2) == dict(
        configuration='exact', unknown_header_and_suffix='exact', old_rows='exact',
        created_program='LiveCycle', created_line=2)


@pytest.mark.parametrize('damage', ('missing_identity', 'empty_identity', 'missing_language',
    'missing_version', 'foreign_menu', 'other_q_menu', 'l_menu', 'chinese', 'japanese',
    'locale_alias', 'old_version', 'unknown_operation', 'source_save', 'missing_operation'))
def test_q03udv_save_header_exception_requires_the_exact_native_operation_binding(damage):
    from gxw.project_metadata import read_task_source
    from gxw.source_header import normalize_visible_save_header
    _, _, context, task_before, task_after = q03udv_english_native_stage_save_witness()
    before, after = [read_task_source(raw)['header'] for raw in (task_before, task_after)]
    cpu, identity, operation = 'Q03UDV', deepcopy(context['identity']), 'task_append'
    if damage == 'missing_identity': identity = None
    elif damage == 'empty_identity': identity = {}
    elif damage == 'missing_language': identity.pop('native_ui_language')
    elif damage == 'missing_version': identity.pop('software_version')
    elif damage == 'foreign_menu': identity['cpu_menu'] = 'Q04UDV'
    elif damage in ('other_q_menu', 'l_menu'):
        cpu = identity['cpu_menu'] = 'Q04UDV' if damage == 'other_q_menu' else 'L02/L02-P'
    elif damage in ('chinese', 'japanese', 'locale_alias'):
        identity['native_ui_language'] = {'chinese': 'zh-CN', 'japanese': 'ja', 'locale_alias': 'en-US'}[damage]
    elif damage == 'old_version': identity['software_version'] = '1.634.0.1'
    elif damage == 'unknown_operation': operation = 'create_st_program'
    elif damage == 'source_save': operation = 'source_save'
    elif damage == 'missing_operation': operation = None
    with pytest.raises(ValueError, match='unverified native header'):
        normalize_visible_save_header(before, after, cpu=cpu, native_identity=identity, operation=operation)


@pytest.mark.parametrize('damage', ('before_flag_zero', 'before_flag_other', 'after_flag_zero',
    'after_flag_other', 'initial_unset_timestamp', 'final_unset_timestamp', 'prefix', 'extra_header_byte'))
def test_q03udv_english_task_save_witness_does_not_grant_compile_clearing_or_unknown_metadata(damage):
    import struct
    from gxw.project_metadata import read_task_source
    from gxw.source_header import normalize_visible_save_header
    _, _, context, task_before, task_after = q03udv_english_native_stage_save_witness()
    before, after = [bytearray(read_task_source(raw)['header']) for raw in (task_before, task_after)]
    if damage == 'before_flag_zero': struct.pack_into('<I', before, 50, 0)
    elif damage == 'before_flag_other': struct.pack_into('<I', before, 50, 2)
    elif damage == 'after_flag_zero': struct.pack_into('<I', after, 50, 0)
    elif damage == 'after_flag_other': struct.pack_into('<I', after, 50, 2)
    elif damage == 'initial_unset_timestamp': before[34:50] = bytes(16)
    elif damage == 'final_unset_timestamp': after[34:50] = bytes(16)
    elif damage == 'prefix': after[24] ^= 1
    else: after.append(0)
    # An otherwise bound compile receipt cannot expand this uncompiled native
    # witness to a Q flag-clearing or initial-timestamp normalization policy.
    with pytest.raises(ValueError):
        normalize_visible_save_header(before, after, cpu='Q03UDV', native_compiled=True,
            native_identity=context['identity'], operation='task_append')


def test_q03udv_task_save_policy_keeps_every_uninterpreted_prefix_byte_exact():
    from hypothesis import given, settings, strategies as st
    from gxw.project_metadata import read_task_source
    from gxw.source_header import normalize_visible_save_header
    _, _, context, a, b = q03udv_english_native_stage_save_witness()
    original, saved = [read_task_source(raw)['header'] for raw in (a, b)]

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=33), st.integers(min_value=1, max_value=255))
    def check(offset, mask):
        before, after = bytearray(original), bytearray(saved)
        before[offset] ^= mask
        after[offset] ^= mask
        with pytest.raises(ValueError, match='unverified native header'):
            normalize_visible_save_header(before, after, cpu='Q03UDV',
                native_identity=context['identity'], operation='task_append')
    check()


@pytest.mark.parametrize('damage', ('opaque_task_word', 'task_suffix', 'retained_source_timestamp',
                                   'missing_task_append_receipt'))
def test_q03udv_english_stage_save_still_rejects_unmeasured_storage_or_unconfirmed_append(damage):
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from gxw.project_metadata import read_task_source
    from gxw.source_header import source_payload_offset
    before, saved, frozen_context, _, _ = q03udv_english_native_stage_save_witness()
    context = deepcopy(frozen_context)
    streams = _context(saved, require_fbd=False)[1]
    if damage == 'missing_task_append_receipt':
        context['tasks']['confirmed_appends'] = []
    elif damage in ('opaque_task_word', 'task_suffix'):
        task = bytearray(streams['Task_01.tsk'])
        if damage == 'opaque_task_word': task[read_task_source(task)['count_offset'] - 4] ^= 1
        else: task += b'opaque suffix'
        saved = replay_source_records(saved, {'Task_01.tsk': bytes(task)})
    else:
        pou = bytearray(streams['POU_01.Program.pou'])
        assert source_payload_offset(pou) == 54
        pou[34:50] = bytes.fromhex('ea070a00000012001400160039000000')
        saved = replay_source_records(saved, {'POU_01.Program.pou': bytes(pou)})
    with pytest.raises(ValueError):
        verify_active_saved_context(saved, context, before)


@lru_cache(maxsize=1)
def q03udv_english_compiled_checkpoint_witness():
    root = private_path('research/experiments/native-live-multitarget-20261018/'
                        'q-en5-compiled-save-checkpoint-01')
    files = ('original-baseline.gxw', 'saved-after.gxw', 'current-context.json', 'compile-observation.json')
    if any(not (root / leaf).is_file() for leaf in files):
        pytest.skip('private Q03UDV/en CP932 compiled-save checkpoint unavailable')
    def decode(value):
        if set(value) == {'kind', 'base64'} and value['kind'] == 'exact_bytes':
            return base64.b64decode(value['base64'], validate=True)
        return value
    return (*( (root/leaf).read_bytes() for leaf in files[:2]),
        *(json.loads((root/leaf).read_text(encoding='utf-8-sig'), object_hook=decode) for leaf in files[2:]))


def test_q03udv_english_cp932_native_compile_save_matches_current_pages_and_retained_fields():
    from gxw.live_context import verify_active_saved_context
    before, saved, frozen_context, frozen_compile = q03udv_english_compiled_checkpoint_witness()
    context, compilation = deepcopy(frozen_context), deepcopy(frozen_compile)
    result = verify_active_saved_context(saved, context, before, compilation=compilation)
    assert all(result[key] == 'matched' for key in ('status', 'st', 'graphs', 'tasks', 'configuration',
                                                   'retained_source_fields'))
    assert result['native_port_nodes_matched'] == 5 and result['native_port_nodes_unavailable'] == 0
    assert result['native_range_initializations'] == [dict(device='P', before=[-1,-1], after=[2048,4095],
        origin='current_native_fbd_pointer_default_materialized', cpu_menu='Q03UDV', native_ui_language='en')]
    assert [(row['source'], row['native_binding']['operation']) for row in result['source_header_changes']] == [
        ('POU_01.Program.pou', 'edited_source_save'), ('Task_01.tsk', 'compiled_task_save')]
    assert all(row['compile_pending_before'] == 1 and row['compile_pending_after'] == 0
               and row['native_compiled_permission_used'] is True for row in result['source_header_changes'])
    assert result['compiled_header_revision'] == context['_revision'] == 8
    assert result['compiled_header_run_id'] == compilation['compile_run_id'] == '25b0caefc1a9453ba4f7c682561b1a8e'
    assert result['libraries_and_structures'] == 'byte_exact'
    assert result['compilation'] == 'not_requested' and result['execution'] == 'not_run'
    assert context == frozen_context and compilation == frozen_compile


@pytest.mark.parametrize('damage', ('missing', 'failed', 'errors', 'boolean_errors', 'origin', 'source_changed',
    'foreign_process', 'foreign_language', 'foreign_codepage', 'old_revision', 'empty_run', 'control_run'))
def test_q03udv_cp932_compiled_save_requires_a_current_independently_bound_native_compile(damage):
    from gxw.live_context import verify_active_saved_context
    before, saved, frozen_context, frozen_compile = q03udv_english_compiled_checkpoint_witness()
    context, compilation = deepcopy(frozen_context), deepcopy(frozen_compile)
    if damage == 'missing': compilation = None
    elif damage == 'failed': compilation['status'] = 'failed'
    elif damage == 'errors': compilation['errors'] = 1
    elif damage == 'boolean_errors': compilation['errors'] = False
    elif damage == 'origin': compilation['origin'] = 'saved_offline_compile'
    elif damage == 'source_changed': compilation['source_unchanged'] = False
    elif damage == 'foreign_process': compilation['identity']['process_started'] = 'foreign process'
    elif damage == 'foreign_language': compilation['identity']['native_ui_language'] = 'zh-CN'
    elif damage == 'foreign_codepage': compilation['identity']['project_codepage'] = 936
    elif damage == 'old_revision': compilation['revision'] -= 1
    elif damage == 'empty_run': compilation['compile_run_id'] = ''
    elif damage == 'control_run': compilation['compile_run_id'] += '\n'
    with pytest.raises(ValueError, match='unverified native header'):
        verify_active_saved_context(saved, context, before, compilation=compilation)


@pytest.mark.parametrize('damage', ('pointer_saved_range', 'retained_timer_range', 'pointer_ui_range',
    'pointer_ui_total', 'pointer_ui_unavailable', 'missing_pointer_grid', 'missing_append',
    'pou_prefix', 'task_prefix', 'task_suffix', 'before_flag', 'after_flag', 'initial_timestamp', 'weekday'))
def test_q03udv_cp932_native_compile_does_not_authorize_unobserved_saved_storage(damage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from gxw.project_metadata import read_project_system_variables
    before, saved, frozen_context, frozen_compile = q03udv_english_compiled_checkpoint_witness()
    context, compilation = deepcopy(frozen_context), deepcopy(frozen_compile)
    streams = _context(saved, require_fbd=False)[1]
    if damage in ('pointer_saved_range', 'retained_timer_range'):
        name, = [name for name in streams if name.endswith('.prj')]
        raw = bytearray(streams[name]); offset = read_project_system_variables(raw)['offset']
        index, pair = (8, (2049,4096)) if damage == 'pointer_saved_range' else (3, (65,2047))
        struct.pack_into('<II', raw, offset + index*8, *pair)
        saved = replay_source_records(saved, {name:bytes(raw)})
    elif damage.startswith('pointer_ui_'):
        row, = [row for row in context['configuration']['grid_rows']['allocation'] if row['cells'][2]['value']=='P']
        if damage == 'pointer_ui_range': row['cells'][5]['value'] = '2049'
        elif damage == 'pointer_ui_total': row['cells'][7]['value'] = '2047'
        else: row['cells'][5]['state'] |= 1
    elif damage == 'missing_pointer_grid': context['configuration'].pop('grid_rows')
    elif damage == 'missing_append': context['tasks']['confirmed_appends'] = []
    else:
        name = 'Task_01.tsk' if damage.startswith('task_') else 'POU_01.Program.pou'
        raw = bytearray(streams[name])
        if damage in ('pou_prefix', 'task_prefix'): raw[24] ^= 1
        elif damage == 'task_suffix': raw += b'unknown suffix'
        elif damage == 'before_flag':
            raw = bytearray(_context(before, require_fbd=False)[1][name])
            struct.pack_into('<I', raw, 50, 2)
            before = replay_source_records(before, {name:bytes(raw)})
        elif damage == 'after_flag': struct.pack_into('<I', raw, 50, 2)
        elif damage == 'initial_timestamp': raw[34:50] = bytes(16)
        elif damage == 'weekday': struct.pack_into('<H', raw, 38, 0)
        if damage != 'before_flag': saved = replay_source_records(saved, {name:bytes(raw)})
    with pytest.raises(ValueError):
        verify_active_saved_context(saved, context, before, compilation=compilation)


@pytest.mark.parametrize('operation', ('edited_source_save', 'compiled_task_save'))
@pytest.mark.parametrize('damage', ('missing_compile', 'other_codepage', 'missing_codepage', 'other_cpu',
                                   'other_language', 'other_version', 'untouched_source'))
def test_q03udv_cp932_compile_header_exception_keeps_exact_native_operation_scope(operation, damage):
    from gxw.fb_authoring import _context
    from gxw.source_header import normalize_visible_save_header
    before, saved, context, _ = q03udv_english_compiled_checkpoint_witness()
    name = 'Task_01.tsk' if operation == 'compiled_task_save' else 'POU_01.Program.pou'
    a, b = [_context(raw, require_fbd=False)[1][name][:54] for raw in (before, saved)]
    identity, compiled, cpu = deepcopy(context['identity']), True, 'Q03UDV'
    if damage == 'missing_compile': compiled = False
    elif damage == 'other_codepage': identity['project_codepage'] = 936
    elif damage == 'missing_codepage': identity.pop('project_codepage')
    elif damage == 'other_cpu': cpu = identity['cpu_menu'] = 'Q04UDV'
    elif damage == 'other_language': identity['native_ui_language'] = 'zh-CN'
    elif damage == 'other_version': identity['software_version'] = '1.634.0.1'
    elif damage == 'untouched_source': operation = 'source_save'
    with pytest.raises(ValueError, match='unverified native header'):
        normalize_visible_save_header(a, b, cpu=cpu, native_compiled=compiled,
                                      native_identity=identity, operation=operation)


@lru_cache(maxsize=None)
def sampled_native_saved_checkpoint_witness(directory):
    root = private_path('research/experiments/native-live-multitarget-20261018/' + directory)
    files = ('original-baseline.gxw', 'saved-after.gxw', 'current-context.json', 'compile-observation.json')
    if any(not (root/leaf).is_file() for leaf in files):
        pytest.skip('private sampled CPU/language native checkpoint unavailable')
    def decode(value):
        if set(value) == {'kind', 'base64'} and value['kind'] == 'exact_bytes':
            return base64.b64decode(value['base64'], validate=True)
        return value
    return (*( (root/leaf).read_bytes() for leaf in files[:2]),
        *(json.loads((root/leaf).read_text(encoding='utf-8-sig'), object_hook=decode) for leaf in files[2:]))


@pytest.mark.parametrize('directory,revision,run_id,header_operations,previous_flag', [
    ('s18-fx3s-en-save1-01', 8, '6fd4def0618b47c2a5fc8ef7ec78041c', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-fx3s-en-save2-01', 16, '7ebd3202767d46918b3289a85f16e60d', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-q03udv-en-header-second-01', 13, '3337f21ac207446cbbba18eaef947b46', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-l02-en-header-first-03', 8, '555c2f7fea4046a4a43bf99e95b20918', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-l02-en-header-second-03', 13, 'f799582b286f4ccea62d22304f9c812b', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-q00u-en-header-first-01', 8, 'fa0afe12b36c413f9622a3fa72528a8a', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-q00u-en-header-second-01', 13, '0c740875dcbe4a518a9fb6cf49fbc8c1', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-q06udeh-en-header-first-01', 8, 'aad8c92871974128b5a58ef30d8060e9', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-q06udeh-en-header-second-01', 13, 'e799f1bc1135407a8a17843683d6aca4', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-fx3g-en-header-first-01', 8, '1d023b2a81e64ce18e2b4012cd274529', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-fx3g-en-header-second-01', 13, '06142c4dbb384a5e94e1ee01294154d1', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-fx3u-en-header-first-01', 8, '0dca435d59884f348958e06040d6ca1a', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-fx3u-en-header-second-01', 13, '5a2df85bd1a04709be88be3c21ff1c62', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
    ('s18-l06-en-header-first-01', 8, 'c466af775c80433e990e6f231b42c04e', [
        ('POU_01.Program.pou','edited_source_save'),('Task_01.tsk','compiled_task_save')], 1),
    ('s18-l06-en-header-second-01', 13, '72f9820439864aa98b083a63e0e56b17', [
        ('POU_01.Program.pou','edited_source_save'),('PilotGate.Program.pou','edited_st_source_save'),
        ('PilotCycle.Program.pou','edited_st_source_save')], 0),
])
def test_sampled_english_current_native_compile_save_matches_full_saved_source(
        directory, revision, run_id, header_operations, previous_flag):
    from gxw.live_context import verify_active_saved_context
    before, saved, context, compilation = sampled_native_saved_checkpoint_witness(directory)
    if previous_flag == 0:
        before = private_path('research/experiments/native-live-multitarget-20261018/' + directory +
                              '/previous-stage-saved.gxw').read_bytes()
    result = verify_active_saved_context(saved, deepcopy(context), before, compilation=deepcopy(compilation))
    assert result['status'] == result['st'] == result['graphs'] == result['tasks'] == result['configuration'] == 'matched'
    assert result['native_port_nodes_matched'] == 5 and result['native_port_nodes_unavailable'] == 0
    assert result['compiled_header_revision'] == revision
    assert result['compiled_header_run_id'] == run_id
    assert [(row['source'],row['native_binding']['operation']) for row in result['source_header_changes']] == header_operations
    assert all(row['compile_pending_before']==previous_flag and row['compile_pending_after']==0
        and row['native_compiled_permission_used'] for row in result['source_header_changes'])


@pytest.mark.parametrize('damage', ['missing_compile','stale_compile','different_run_identity','language',
    'version','codepage','cpu_menu','prefix','after_flag','untouched_source_operation'])
@pytest.mark.parametrize('directory,cpu,previous_stage', [
    ('s18-fx3s-en-save1-01','FX3S',False),
    ('s18-fx3s-en-save2-01','FX3S',True),
    ('s18-q03udv-en-header-second-01','Q03UDV',True),
    ('s18-l02-en-header-first-03','L02',False),
    ('s18-l02-en-header-second-03','L02',True),
    ('s18-q00u-en-header-first-01','Q00U',False),
    ('s18-q00u-en-header-second-01','Q00U',True),
    ('s18-q06udeh-en-header-first-01','Q06UDEH',False),
    ('s18-q06udeh-en-header-second-01','Q06UDEH',True),
    ('s18-fx3g-en-header-first-01','FX3G',False),
    ('s18-fx3g-en-header-second-01','FX3G',True),
    ('s18-fx3u-en-header-first-01','FX3U',False),
    ('s18-fx3u-en-header-second-01','FX3U',True),
    ('s18-l06-en-header-first-01','L06',False),
    ('s18-l06-en-header-second-01','L06',True),
])
def test_sampled_compiled_native_header_scope_does_not_grant_another_target_or_unknown_storage(
        damage,directory,cpu,previous_stage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from gxw.source_header import normalize_visible_save_header
    before, saved, frozen_context, frozen_compile = sampled_native_saved_checkpoint_witness(directory)
    if previous_stage:
        before = private_path('research/experiments/native-live-multitarget-20261018/' + directory +
                              '/previous-stage-saved.gxw').read_bytes()
    context, compilation = deepcopy(frozen_context), deepcopy(frozen_compile)
    if damage == 'missing_compile': compilation = None
    elif damage == 'stale_compile': compilation['revision'] -= 1
    elif damage == 'different_run_identity': compilation['identity']['process_started'] = 'another start'
    elif damage in ('language','version','codepage','cpu_menu'):
        key,value={'language':('native_ui_language',
                       'en' if context['identity']['native_ui_language']=='zh-CN' else 'zh-CN'),
                   'version':('software_version','1.634.0.1'),
                   'codepage':('project_codepage',932 if context['identity']['project_codepage']==936 else 936),
                   'cpu_menu':('cpu_menu',
                       'FX3S' if context['identity']['cpu_menu']=='FX3G/FX3GC' else 'FX3G/FX3GC')}[damage]
        context['identity'][key] = value
    else:
        name = 'PilotGate.Program.pou' if previous_stage else 'POU_01.Program.pou'
        a,b=[_context(raw,require_fbd=False)[1][name][:54] for raw in (before,saved)]
        if damage == 'untouched_source_operation':
            with pytest.raises(ValueError,match='unverified native header'):
                normalize_visible_save_header(a,b,cpu=cpu,native_compiled=True,
                    native_identity=context['identity'],operation='source_save')
            return
        raw=bytearray(_context(saved,require_fbd=False)[1][name])
        if damage=='prefix': raw[24]^=1
        else: struct.pack_into('<I',raw,50,1)
        saved=replay_source_records(saved,{name:bytes(raw)})
    with pytest.raises(ValueError):
        verify_active_saved_context(saved,context,before,compilation=compilation)


@pytest.mark.parametrize('damage', [None,'language','codepage','missing_codepage','version','cpu',
    'partial_range','explicit_range','st_program'])
@pytest.mark.parametrize('directory,foreign_cpu,pointer_range', [
    ('s18-l02-en-header-first-03','L06/L06-P',[2048,4095]),
    ('s18-q00u-en-header-first-01','Q03UDV',[256,511]),
    ('s18-q06udeh-en-header-first-01','Q03UDV',[2048,4095]),
    ('s18-l06-en-header-first-01','L02/L02-P',[2048,4095]),
])
def test_native_sampled_pointer_materialization_requires_its_own_menu_language_codepage_and_missing_range(
        damage,directory,foreign_cpu,pointer_range):
    import struct
    from gxw.fb_authoring import _context
    from gxw.project_metadata import native_fbd_pointer_initialization,read_project_system_variables
    before,_,context,_ = sampled_native_saved_checkpoint_witness(directory)
    streams = _context(before,require_fbd=False)[1]
    name, = [name for name in streams if name.endswith('.prj')]
    metadata,identity,kind = bytearray(streams[name]),deepcopy(context['identity']),208
    if damage=='language':identity['native_ui_language']='zh-CN'
    elif damage=='codepage':identity['project_codepage']=936
    elif damage=='missing_codepage':identity.pop('project_codepage')
    elif damage=='version':identity['software_version']='1.634.0.1'
    elif damage=='cpu':identity['cpu_menu']=foreign_cpu
    elif damage=='st_program':kind=193
    elif damage in ('partial_range','explicit_range'):
        offset=read_project_system_variables(metadata)['offset']+8*8
        struct.pack_into('<II',metadata,offset,0,0xffffffff if damage=='partial_range' else 2047)
    result=native_fbd_pointer_initialization(bytes(metadata),kind,native_identity=identity)
    assert result == (dict(offset=68,before=[-1,-1],after=pointer_range) if damage is None else None)


def _unchanged_st_compiled_save_case(directory):
    """Change only save metadata on an independently frozen, compiled ST source."""
    import struct
    from gxw.fb_authoring import _context
    from gxw.source_header import source_payload_offset
    _, baseline, context, compilation = sampled_native_saved_checkpoint_witness(directory)
    source = 'PilotGate.Program.pou'
    payload = bytearray(_context(baseline, require_fbd=False)[1][source])
    assert source_payload_offset(payload) == 54 and payload[54] == 193
    assert int.from_bytes(payload[50:54], 'little') == 0
    # Tuesday, 2026-10-20. The frozen source body, declarations and tasks stay exact.
    struct.pack_into('<8H', payload, 34, 2026, 10, 2, 20, 3, 11, 12, 0)
    saved = replay_source_records(baseline, {source: bytes(payload)})
    return baseline, saved, deepcopy(context), deepcopy(compilation), source


@pytest.mark.parametrize('directory', [
    's18-fx3s-en-save2-01', 's18-fx3g-en-header-second-01',
    's18-fx3u-en-header-second-01', 's18-q00u-en-header-second-01',
    's18-q03udv-en-header-second-01', 's18-q06udeh-en-header-second-01',
    's18-l02-en-header-second-03', 's18-l06-en-header-second-01',
])
def test_unchanged_st_current_compilation_allows_only_bound_save_metadata(directory):
    from gxw.live_context import verify_active_saved_context
    baseline, saved, context, compilation, source = _unchanged_st_compiled_save_case(directory)
    result = verify_active_saved_context(saved, context, baseline, compilation=compilation)
    assert result['status'] == result['retained_source_fields'] == 'matched'
    change, = result['source_header_changes']
    assert change['source'] == source
    assert change['native_binding']['operation'] == 'compiled_st_source_save'
    assert change['compile_pending_before'] == change['compile_pending_after'] == 0
    assert change['native_compiled_permission_used'] is True
    assert result['compiled_header_run_id'] == compilation['compile_run_id']


@pytest.mark.parametrize('damage', [
    'missing_compile', 'failed_compile', 'compile_errors', 'source_changed', 'old_revision',
    'foreign_process', 'foreign_language', 'foreign_codepage', 'foreign_cpu', 'old_version',
    'prefix', 'invalid_flag', 'pending_after', 'unset_timestamp', 'wrong_weekday', 'body',
])
def test_unchanged_st_compiled_save_rejects_unbound_or_unobserved_changes(damage):
    import struct
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    baseline, saved, context, compilation, source = _unchanged_st_compiled_save_case(
        's18-fx3g-en-header-second-01')
    if damage == 'missing_compile': compilation = None
    elif damage == 'failed_compile': compilation['status'] = 'failed'
    elif damage == 'compile_errors': compilation['errors'] = 1
    elif damage == 'source_changed': compilation['source_unchanged'] = False
    elif damage == 'old_revision': compilation['revision'] -= 1
    elif damage.startswith('foreign_') or damage == 'old_version':
        key, value = {
            'foreign_process': ('process_started', 'another start'),
            'foreign_language': ('native_ui_language', 'ja'),
            'foreign_codepage': ('project_codepage', 936),
            'foreign_cpu': ('cpu_menu', 'FX3S'),
            'old_version': ('software_version', '1.634.0.1'),
        }[damage]
        compilation['identity'][key] = value
    else:
        raw = bytearray(_context(saved, require_fbd=False)[1][source])
        if damage == 'prefix': raw[24] ^= 1
        elif damage == 'invalid_flag': struct.pack_into('<I', raw, 50, 2)
        elif damage == 'pending_after': struct.pack_into('<I', raw, 50, 1)
        elif damage == 'unset_timestamp': raw[34:50] = bytes(16)
        elif damage == 'wrong_weekday': struct.pack_into('<H', raw, 38, 3)
        else:
            from gxw.text_pou import parse_st_pou, serialize_st_pou
            raw = serialize_st_pou(parse_st_pou(raw), 'X0 := TRUE;')
        saved = replay_source_records(saved, {source: bytes(raw)})
    with pytest.raises(ValueError):
        verify_active_saved_context(saved, context, baseline, compilation=compilation)


def test_compiled_st_save_metadata_preserves_unknown_header_bytes_for_generated_timestamps():
    from datetime import datetime
    import struct
    from hypothesis import given, settings, strategies as st
    from gxw.source_header import normalize_visible_save_header
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    identity = dict(cpu_menu='FX3G/FX3GC', software_version='1.635.0.1',
                    native_ui_language='en', project_codepage=932)

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.datetimes(min_value=datetime(2027, 1, 1), max_value=datetime(2099, 12, 31)),
           st.integers(0, 1), st.integers(0, 33), st.integers(1, 255))
    def check(timestamp, pending, offset, mask):
        before = prefix + struct.pack('<8HI', 2026, 10, 1, 19, 21, 55, 0, 0, pending)
        after = prefix + struct.pack('<8HI', timestamp.year, timestamp.month,
            (timestamp.weekday() + 1) % 7, timestamp.day, timestamp.hour,
            timestamp.minute, timestamp.second, timestamp.microsecond // 1000, 0)
        options = dict(cpu='FX3G', native_identity=identity, operation='compiled_st_source_save')
        normalized, receipt = normalize_visible_save_header(before, after, native_compiled=True, **options)
        assert normalized == before
        assert receipt['timestamp_after'] == list(struct.unpack('<8H', after[34:50]))
        assert receipt['compile_pending_after'] == 0
        with pytest.raises(ValueError):
            normalize_visible_save_header(before, after, native_compiled=False, **options)
        changed = bytearray(after)
        changed[offset] ^= mask
        with pytest.raises(ValueError):
            normalize_visible_save_header(before, changed, native_compiled=True, **options)
    check()



def test_offline_fbd_draft_reports_known_type_conflicts_without_losing_structure_checks():
    from src.gxw.object_model import prepare_object_project, source_checks
    from src.gxw.project_writer import write_prepared_project
    context=read_project_context(default_baseline())
    model=context.empty_model()
    model['declaration_edits']={context.sources.local_table: {'upserts': [dict(name='Number', data_type='INT')]}}
    model['nodes']=[dict(id='left',template='input',symbol='TRUE',x=2,y=2),
                    dict(id='right',template='output',symbol='Number',x=12,y=2)]
    model['wires']=[{'from':'left.OUT','to':'right.IN'}]
    with pytest.raises(GXWFormatError,match='type'):
        prepare_object_project(model,baseline=context.raw)
    result=write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft'))
    saved=read_project_context(result.data)
    checks=source_checks(saved)
    assert checks['structure']=='passed' and checks['status']=='failed'
    conflict=next(row for row in checks['findings'] if row['code']=='fbd_net_type_conflict')
    assert {row['data_type'] for row in conflict['ports']}=={'BOOL','INT'}
    model['wires'][0]['to']='missing.IN'
    with pytest.raises(GXWFormatError,match='unknown wire endpoint'):
        prepare_object_project(model,baseline=context.raw,save_policy='draft')


def test_offline_fbd_network_commands_keep_membership_and_second_network_connections():
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_writer import write_prepared_project
    from src.gxw.structured_pou import structured_block_text
    context=read_project_context(default_baseline())
    model=context.object_model()
    model['blocks']=[dict(source_offset=context.program.blocks[0].offset,canvas_height=context.program.canvas_height)]
    model=edit_draft(model,dict(action='add_block',title='New network',comment='long network comment'))['model']
    model['nodes'].extend([dict(id='source2',template='input',symbol='TRUE',x=2,y=3,block=1),
                          dict(id='sink2',template='output',symbol='Y20',x=12,y=3,block=1)])
    model=edit_draft(model,dict(action='add_wire',**{'from':'source2.OUT','to':'sink2.IN'}))['model']
    assert model['wires'][-1]['block']==1
    model=edit_draft(model,dict(action='move_block',index=1,target=0))['model']
    assert all(node['block']==0 for node in model['nodes'][-2:])
    result=write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft'))
    saved=read_project_context(result.data)
    assert structured_block_text(saved.program.blocks[0].raw_header)==('New network','long network comment')
    assert [n.symbol for n in saved.program.block_views().__next__().nodes]==['TRUE','Y20']
    old_records=list(context.program.iter_records())
    assert [r.raw for r in list(saved.program.block_records())[1][1]]==[r.raw for r in old_records]
    draft=saved.object_model()
    draft=edit_draft(draft,dict(action='delete_block',index=0))['model']
    restored=read_project_context(write_prepared_project(prepare_object_project(draft,baseline=result.data,save_policy='draft')).data)
    assert len(restored.program.blocks)==1
    assert [r.raw for r in restored.program.iter_records()]==[r.raw for r in old_records]


def test_offline_fbd_network_text_growth_and_shrink_have_independent_utf16_framing():
    from hypothesis import given,settings,strategies as st
    from src.gxw.structured_pou import edit_structured_block_text,structured_block_text
    import struct
    context=read_project_context(default_baseline());header=context.program.blocks[0].raw_header
    @settings(max_examples=20,deadline=None)
    @given(title=st.text(alphabet='ABC_中文😀\n',max_size=40),comment=st.text(alphabet='abc012注释😀\t',max_size=80))
    def check(title,comment):
        result=edit_structured_block_text(header,title=title,comment=comment)
        title_bytes=(title+'\0').encode('utf-16le');comment_bytes=(comment+'\0').encode('utf-16le')
        assert result==header[:4]+struct.pack('<I',len(title_bytes)//2)+title_bytes+struct.pack('<I',len(comment_bytes)//2)+comment_bytes+header[-8:]
        assert structured_block_text(result)==(title,comment)
        assert edit_structured_block_text(result,title='',comment='')==header[:4]+struct.pack('<I',1)+bytes(2)+struct.pack('<I',1)+bytes(2)+header[-8:]
    check()


def test_offline_fbd_editor_keeps_explicit_declaration_order_through_add_rename_delete():
    from src.gxw.editor import edit_draft,declaration_rows
    model={'schema_version':1,'program':'test.Program.pou','canvas_height':12,'nodes':[],'wires':[],
           'labels':{'test.Labels.lh':[dict(name='one'),dict(name='two')]},'declaration_edits':{}}
    def command(**value):
        nonlocal model
        model=edit_draft(model,dict(table='test.Labels.lh',**value))['model']
    command(action='reorder_labels',order=['two','one'])
    assert [r['name'] for r in declaration_rows(model,'test.Labels.lh')]==['two','one']
    command(action='add_label')
    command(action='update_label',name='two',field='name',value='renamed')
    command(action='delete_label',name='one')
    assert [r['name'] for r in declaration_rows(model,'test.Labels.lh')]==['renamed','label_3']


def test_offline_fbd_general_function_constructor_binds_extensible_mux_and_intrinsic_ports():
    from src.gxw.object_model import source_node_template
    context=read_project_context(default_baseline())
    mux=source_node_template('function:MUX-3',context.sources)
    ports=context.sources.callable(mux,bind_instance=False)['ports']
    assert [p['formal_name'] for p in ports if p['side']=='in']==['_K','_IN','_IN','_IN']
    assert [p['occurrence'] for p in ports if p['formal_name']=='_IN']==[1,2,3]
    intrinsic=source_node_template('function:@ADD-3',context.sources)
    bound=context.sources.callable(intrinsic,bind_instance=False)
    assert bound['binding']=='serialized_operator'
    assert [(p['name'],p['side']) for p in bound['ports']]==[('0','in'),('1','in'),('2','in'),('3','out')]
    assert all(p['formal_name'] is None and p['data_type'] is None for p in bound['ports'])
    assert [(p.local_x,p.local_y) for p in intrinsic.ports]==[(0,2),(0,3),(0,4),(5,2)]


def test_offline_fbd_callable_resize_and_refresh_preview_use_the_encoded_connector_positions():
    from src.gxw.editor import edit_draft,presentation
    context=read_project_context(source_binding_baseline())
    model=context.object_model()
    node=next(n for n in model['nodes'] if n['template'].startswith('function_block:'))
    old_ports=next(n['ports'] for n in presentation(model,context=context)['nodes'] if n['id']==node['id'])
    width=node['width']+4
    model=edit_draft(model,dict(action='resize_node',id=node['id'],width=width),context=context)['model']
    current=next(n['ports'] for n in presentation(model,context=context)['nodes'] if n['id']==node['id'])
    assert [p['x'] for p in current if p.get('side')=='out']==[width]*sum(p.get('side')=='out' for p in old_ports)
    encoded=build_object_program(context.program,model,sources=context.sources)
    edited=next(n for n in encoded.nodes if n.offset==node['source_offset'])
    assert edited.bbox.right-edited.bbox.left==width
    assert all(p.local_x in (0,width) for p in edited.ports)



@pytest.mark.parametrize('index,expected_codes',[(30,{9:'jump',10:'return'}),(33,{19:'contact_rising_negated',20:'contact_falling_negated'})])
def test_offline_fbd_original_control_records_support_move_insert_delete_without_semantic_promotion(index,expected_codes):
    from tests.test_gxw_structured_reader import offline_fbd_source,offline_fbd_corpus
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    expected=offline_fbd_corpus()['sources'][index]
    source=parse_structured_pou(offline_fbd_source(index),logical_name=expected['source'],preserve_unsupported_records=True)
    selected=[n for n in source.nodes if n.kind_code in expected_codes]
    assert {n.kind_code:n.kind.value for n in selected}==expected_codes
    for original in selected:
        assert original.object_flag==1 and original.reserved==0
        assert [(p.port_kind_code,p.local_x,p.local_y) for p in original.ports]==(
            [(3,0,1)] if original.kind_code in (9,10) else [(3,0,1),(2,2,1)])
    target=selected[0];model=export_object_model(source)
    model['nodes']=[row for row in model['nodes'] if row['source_offset']!=target.offset]
    model['nodes'].append(dict(id='new_control',template=expected_codes[target.kind_code],
        symbol=target.symbol,x=40,y=source.blocks[0].canvas_height+5,block=0))
    if len(model.get('blocks',[]))<2:model['nodes'][-1].pop('block')
    saved=parse_structured_pou(serialize_structured_pou(build_object_program(source,model,save_policy='draft')),logical_name=source.logical_name,preserve_unsupported_records=True)
    inserted=next(n for n in saved.nodes if n.bbox.left==40 and n.kind_code==target.kind_code)
    assert inserted.kind.value==expected_codes[target.kind_code] and inserted.symbol==target.symbol
    assert len(saved.nodes)==len(source.nodes)
    assert [r.raw for r in saved.unknown_records]==[r.raw for r in source.unknown_records]
    assert any(row['code']=='graph_execution_semantics_unverified' for row in export_object_model(saved)['issues'])
    draft=export_object_model(saved);draft['nodes']=[row for row in draft['nodes'] if row['source_offset']!=inserted.offset]
    final=parse_structured_pou(serialize_structured_pou(build_object_program(saved,draft,save_policy='draft')),logical_name=source.logical_name,preserve_unsupported_records=True)
    assert len(final.nodes)==len(source.nodes)-1



@pytest.mark.parametrize('row',json.loads(private_path('tests/fixtures/gxw_fbd_box_controls.json').read_text(encoding='utf-8'))['rows'])
def test_offline_fbd_primitive_constructors_match_independent_native_box_controls(row):
    from src.gxw.object_model import source_node_template
    keys={3:'contact',9:'jump',10:'return',15:'inout',19:'contact_rising_negated',20:'contact_falling_negated'}
    node=source_node_template(keys[row['requested']],None)
    assert node.kind_code==row['kind']
    assert (node.bbox.right-node.bbox.left,node.bbox.bottom-node.bbox.top)==(row['width'],row['height'])
    assert node.object_flag==row['object_flag'] and node.reserved==0
    assert [dict(flags=p.port_kind_code,x=p.local_x,y=p.local_y) for p in node.ports]==row['ports']


def test_offline_fbd_inout_variable_connections_save_and_keep_execution_unverified():
    from src.gxw.object_model import prepare_object_project,source_checks
    from src.gxw.project_writer import write_prepared_project
    context=read_project_context(default_baseline());model=context.empty_model()
    model['nodes']=[dict(id='source',template='input',symbol='X0',x=2,y=2),
        dict(id='state',template='inout',symbol='Y0',x=7,y=2),
        dict(id='sink',template='output',symbol='Y1',x=14,y=2)]
    model['wires']=[{'from':'source.OUT','to':'state.IN'},{'from':'state.OUT','to':'sink.IN'}]
    saved=read_project_context(write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft')).data)
    state=next(node for node in saved.program.nodes if node.kind==NodeKind.INOUT)
    assert state.kind_code==15 and state.symbol=='Y0' and len(state.ports)==2
    assert source_checks(saved)['status']=='unverified'
    draft=saved.object_model();next(row for row in draft['nodes'] if row['template']=='inout')['symbol']='Y2'
    final=read_project_context(write_prepared_project(prepare_object_project(draft,baseline=saved.raw,save_policy='draft')).data)
    assert next(node for node in final.program.nodes if node.kind==NodeKind.INOUT).symbol=='Y2'



def test_offline_fbd_explicit_unresolved_callable_connectors_save_as_draft_and_remain_editable():
    from src.gxw.object_model import prepare_object_project,source_checks
    from src.gxw.project_writer import write_prepared_project
    context=read_project_context(default_baseline());model=context.empty_model()
    model['nodes']=[dict(id='source',template='input',symbol='TRUE',x=2,y=2),
        dict(id='call',template='function:UnresolvedUserCall',symbol='UnresolvedUserCall',x=7,y=1,
             port_layout=dict(width=7,height=5,ports=[dict(flags=3,x=0,y=2),dict(flags=2,x=7,y=2)]),
             port_edits={'0':{'negated':True}}),
        dict(id='sink',template='output',symbol='Y0',x=18,y=2)]
    model['wires']=[{'from':'source.OUT','to':'call.0'},{'from':'call.1','to':'sink.IN'}]
    with pytest.raises(GXWFormatError,match='source callable'):
        prepare_object_project(model,baseline=context.raw)
    result=write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft'))
    saved=read_project_context(result.data);call=next(n for n in saved.program.nodes if n.symbol=='UnresolvedUserCall')
    assert [(p.port_kind_code,p.local_x,p.local_y) for p in call.ports]==[(11,0,2),(2,7,2)]
    projection=next(n for n in saved.object_model()['nodes'] if n['source_offset']==call.offset)
    assert [(p['name'],p['side'],p['data_type']) for p in projection['ports']]==[('0','in',None),('1','out',None)]
    assert any(row['code']=='callable_source_gap' for row in source_checks(saved)['findings'])
    draft=saved.object_model();draft['nodes'].append(dict(id='clone',template='function:UnresolvedUserCall',
        prototype_offset=call.offset,symbol=call.symbol,x=7,y=20))
    cloned=read_project_context(write_prepared_project(prepare_object_project(draft,baseline=saved.raw,save_policy='draft')).data)
    assert sum(n.symbol==call.symbol for n in cloned.program.nodes)==2
    model['nodes'][1]['port_layout']['ports'][0]['flags']=2
    with pytest.raises(GXWFormatError,match='flags or side'):
        prepare_object_project(model,baseline=context.raw,save_policy='draft')


@pytest.mark.parametrize('index,offset,point,block',[(510,1941,[22,2],1),(603,3575,[13,3],6)])
def test_offline_original_fbd_fixpoint_can_move_add_delete_and_change_network_in_two_saves(index,offset,point,block):
    import struct
    from src.gxw.editor import edit_draft
    from src.gxw.offline_project import read_offline_project
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_writer import write_prepared_project
    from tests.test_gxw_structured_reader import offline_fbd_project
    raw,expected=offline_fbd_project(index)
    context=read_project_context(raw,expected['source'])
    original=next(w for w in context.program.wires if w.record_class==3)
    # Independent source framing and the SC_EDIT FIXPOINT ctor/common Store
    # define this record; no tested encoder supplies the expected bytes.
    record=lambda p:struct.pack('<IIIIHHIIIIII',44,3,0,1,0,1,0,*p,*p,0)
    assert original.offset==offset and original.raw==record(point)
    assert not context.program.unknown_records
    assert generate_object_project(context.object_model(),baseline=raw).data==raw
    model=context.object_model()
    target=next(i for i,w in enumerate(model['wires']) if w.get('kind')=='junction')
    assert model['wires'][target]['block']==block
    moved=[point[0]+3,point[1]+2]
    model=edit_draft(model,dict(action='move_junction',index=target,point=moved),context=context)['model']
    model=edit_draft(model,dict(action='add_junction',point=[40,8],block=0),context=context)['model']
    first=write_prepared_project(prepare_object_project(model,baseline=raw,save_policy='draft'))
    saved=read_project_context(first.data,expected['source'])
    junctions=[w for w in saved.program.wires if w.record_class==3]
    assert sorted(w.raw for w in junctions)==sorted([record(moved),record([40,8])])
    assert [n.raw for n in saved.program.nodes]==[n.raw for n in context.program.nodes]
    assert [w.raw for w in saved.program.wires if w.record_class==2]==[w.raw for w in context.program.wires if w.record_class==2]
    old,new=read_offline_project(raw),read_offline_project(first.data)
    assert {k:v for k,v in old.streams.items() if k!=expected['source']}=={k:v for k,v in new.streams.items() if k!=expected['source']}
    draft=saved.object_model()
    target=next(i for i,w in enumerate(draft['wires']) if w.get('kind')=='junction' and w['start']==moved)
    draft=edit_draft(draft,dict(action='delete_wire',index=target),context=saved)['model']
    target=next(i for i,w in enumerate(draft['wires']) if w.get('kind')=='junction')
    draft=edit_draft(draft,dict(action='move_junction',index=target,point=[7,4]),context=saved)['model']
    draft=edit_draft(draft,dict(action='update_wire',index=target,field='block',value=2),context=saved)['model']
    second=write_prepared_project(prepare_object_project(draft,baseline=first.data,save_policy='draft'))
    final=read_project_context(second.data,expected['source'])
    assert [w.raw for w in final.program.wires if w.record_class==3]==[record([7,4])]
    assert next(w for w in final.object_model()['wires'] if w.get('kind')=='junction')['block']==2
    assert raw==offline_fbd_project(index)[0]


def test_offline_fbd_junction_edit_sequences_preserve_crossing_connectivity_and_native_point_bytes():
    import struct
    from hypothesis import example,given,settings,strategies as st
    from src.gxw.connectivity import build_connectivity_graph
    from src.gxw.editor import edit_draft
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    source=read_project_context(default_baseline()).program
    base=export_object_model(source);base['nodes']=[];base['wires']=[
        dict(start=[2,10],end=[22,10]),dict(start=[12,2],end=[12,20])]
    base.pop('blocks',None)
    @settings(max_examples=24,deadline=None,derandomize=True)
    @example([(12,10),(4,10),(12,4),(3,7)])
    @given(st.lists(st.tuples(st.integers(2,22),st.integers(2,20)),min_size=1,max_size=5))
    def check(points):
        model=deepcopy(base)
        model=edit_draft(model,dict(action='add_junction',point=list(points[0])))['model']
        for x,y in points:
            model=edit_draft(model,dict(action='move_junction',index=2,point=[x,y]))['model']
            built=build_object_program(source,model,save_policy='draft')
            data=serialize_structured_pou(built)
            saved=parse_structured_pou(data,preserve_unsupported_records=True)
            point,=[w for w in saved.wires if w.record_class==3]
            assert point.raw==struct.pack('<IIIIHHIIIIII',44,3,0,1,0,1,0,x,y,x,y,0)
            # Interior crossings stay separate until a FIXPOINT is placed at
            # their intersection. A point lying on one line joins only that line.
            nets=build_connectivity_graph(saved)
            horizontal,vertical=saved.wires[:2]
            assert (nets.net_for_wire(horizontal.offset).index==nets.net_for_wire(vertical.offset).index)==((x,y)==(12,10))
            on_horizontal=y==10
            on_vertical=x==12
            expected_count=1 if on_horizontal and on_vertical else 2 if on_horizontal or on_vertical else 3
            assert len(nets.nets)==expected_count
        model=edit_draft(model,dict(action='delete_wire',index=2))['model']
        final=build_object_program(source,model,save_policy='draft')
        assert len(final.wires)==2 and len(build_connectivity_graph(final).nets)==2
    check()


@pytest.mark.parametrize('damage',[
    {'kind':'junction','start':[2,3],'end':[3,3]},
    {'kind':'junction','start':[2,3],'end':[2,3],'via':[]},
    {'kind':'junction','start':[32768,3],'end':[32768,3]},
    {'start':[2,3],'end':[2,3]},
])
def test_offline_fbd_junction_rejects_invalid_storage_geometry(damage):
    source=read_project_context(default_baseline()).program
    model=export_object_model(source);model['wires'].append(damage)
    with pytest.raises(GXWFormatError):
        build_object_program(source,model,save_policy='draft')


def test_visible_graph_keeps_fixpoint_identity_during_unrelated_node_edit():
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_writer import write_prepared_project
    from src.gxw.visible_graph import prepare_graph_edit,graph_edit_step,verify_graph_edit
    raw=default_baseline();context=read_project_context(raw)
    model=context.object_model()
    model['nodes'].append(dict(id='terminal',template='output',symbol='M14',x=35,y=9))
    model=edit_draft(model,dict(action='add_junction',point=[30,10]),context=context)['model']
    with_point=write_prepared_project(prepare_object_project(model,baseline=raw,save_policy='draft')).data
    current=read_project_context(with_point)
    terminal=next(n for n in current.program.nodes if n.kind==NodeKind.OUTPUT)
    command={'operation':'edit_fbd_graph','caller':current.program.logical_name,'actions':[
        dict(action='rebind_terminal',node='n'+str(terminal.offset),symbol='M15')]}
    plan=prepare_graph_edit(with_point,command)
    assert any(net['geometry']==[['p',30,10,10]] for net in plan['initial']['nets'])
    assert graph_edit_step(with_point,plan,0)['symbol']=='M15'
    changed=write_prepared_project(prepare_object_project(plan['model'],baseline=with_point,save_policy='draft')).data
    assert verify_graph_edit(with_point,command,changed,prepared=plan)['status']=='matched'


def test_offline_fbd_source_checks_preserve_terminal_profile_and_retain_execution_gaps():
    from src.gxw.object_model import prepare_object_project,source_checks
    from src.gxw.project_writer import write_prepared_project
    context=read_project_context(default_baseline());model=context.empty_model()
    table=context.sources.local_table
    model['nodes']=[dict(id='unknown_width',template='input',symbol='X0',x=2,y=2),
                    dict(id='known_bit',template='output',symbol='M0',x=10,y=2)]
    model['wires']=[{'from':'unknown_width.OUT','to':'known_bit.IN'}]
    model['declaration_edits']={table:{'upserts':[dict(name='Remembered',data_type='BOOL',class_name='VAR_RETAIN')]}}
    saved=read_project_context(write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft')).data)
    checks=source_checks(saved)
    assert checks['structure']=='passed' and checks['status']=='unverified'
    assert [r['symbol'] for r in checks['findings'] if r['code']=='fbd_terminal_type_unverified']==['X0']
    retained,=[r for r in checks['findings'] if r['code']=='fbd_retain_execution_unverified']
    assert retained['name']=='Remembered' and retained['stream']==table
    assert saved.declarations[table].rows[0].class_code==10


def test_offline_fbd_instance_type_and_case_bind_to_the_existing_source_definition():
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_writer import write_prepared_project
    raw=source_binding_baseline();context=read_project_context(raw)
    prototype=next(n for n in context.program.nodes if n.kind==NodeKind.FUNCTION_BLOCK)
    model=context.object_model()
    model['nodes'].append(dict(id='case_clone',template='function_block:'+prototype.type_name.lower(),
        symbol='CaseClone',x=40,y=30))
    saved=read_project_context(write_prepared_project(prepare_object_project(model,baseline=raw)).data)
    clone=next(n for n in saved.program.nodes if n.symbol=='CaseClone')
    assert saved.sources.callable(clone)['source_stream']==context.sources.callable(prototype)['source_stream']
    assert saved.sources.label('caseclone')[1].data_type.casefold()==prototype.type_name.casefold()


@pytest.mark.parametrize('name,count',[('@SUB',2),('@DIV',2),('@NE',2),('@AND-7',7),('@EQ-4',4)])
def test_offline_fbd_new_operator_constructor_uses_native_fixed_and_repeated_arity(name,count):
    from src.gxw.object_model import source_node_template
    context=read_project_context(default_baseline());node=source_node_template('function:'+name,context.sources)
    assert len(node.ports)==count+1
    assert [(p.local_x,p.local_y) for p in node.ports]==[(0,i+2) for i in range(count)]+[(5,2)]
    assert context.sources.callable(node,bind_instance=False)['binding']=='serialized_operator'
    wrong=replace(node,ports=node.ports[:-1])
    with pytest.raises(GXWFormatError,match='serialized arity'):
        context.sources.callable(wrong,bind_instance=False)



def test_offline_fbd_explicit_connector_sequences_preserve_native_mode_bits_and_diagnostics():
    from hypothesis import given,settings,strategies as st
    from src.gxw.object_model import source_checks
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    context=read_project_context(default_baseline())
    @settings(max_examples=15,deadline=None)
    @given(st.lists(st.booleans(),min_size=2,max_size=8),st.booleans())
    def check(negated,output):
        count=len(negated);flags=[3|(8 if value else 0) for value in negated]+[2|(8 if output else 0)]
        positions=[(0,index+2) for index in range(count)]+[(5,2)]
        model=context.empty_model();model['nodes']=[dict(id='call',template='function:@AND-'+str(count),
            symbol='@AND-'+str(count),x=5,y=3,port_layout=dict(width=5,height=count+2,
                ports=[dict(flags=flag,x=x,y=y) for flag,(x,y) in zip(flags,positions)]))]
        built=build_object_program(context.program,model,sources=context.sources,save_policy='draft')
        saved=parse_structured_pou(serialize_structured_pou(built),logical_name=context.program.logical_name)
        node=saved.nodes[0]
        assert [p.port_kind_code for p in node.ports]==flags
        assert [(p.local_x,p.local_y) for p in node.ports]==positions
        assert (node.bbox.left,node.bbox.top)==(5,3)
        assert any(row['code']=='intrinsic_operator_semantics_unverified'
                   for row in export_object_model(saved,sources=context.sources)['issues'])
    check()


def test_offline_fbd_multiline_comment_resize_keeps_text_and_old_model_template_alias():
    from src.gxw.object_model import prepare_object_project
    from src.gxw.project_writer import write_prepared_project
    context=read_project_context(default_baseline());model=context.object_model()
    model['nodes'].append(dict(id='note',template='comment',symbol='first\n  second & <third> 😀',x=2,y=20,
        resize=dict(width=30,height=8)))
    saved=read_project_context(write_prepared_project(prepare_object_project(model,baseline=context.raw,save_policy='draft')).data)
    comment=next(row for row in saved.object_model()['nodes'] if row['template']=='comment')
    assert (comment['width'],comment['height'])==(30,8)
    assert '<tspan' in saved.svg() and '  second &amp; &lt;third&gt; 😀' in saved.svg()
    draft=saved.object_model();note=next(row for row in draft['nodes'] if row['template']=='comment')
    note['template']='unknown';note['symbol']=''
    final=read_project_context(write_prepared_project(prepare_object_project(draft,baseline=saved.raw,save_policy='draft')).data)
    assert next(n for n in final.program.nodes if n.kind_code==16).symbol==''
    invalid=context.empty_model();invalid['nodes']=[dict(id='node',template='input',symbol='TRUE',x=32767,y=1)]
    with pytest.raises(GXWFormatError,match='signed 16-bit'):
        prepare_object_project(invalid,baseline=context.raw,save_policy='draft')


# --- FBD network identity and native header preservation ---------------------
# The projection separates the two saved block strings (title = network label a
# Jump addresses, block_comment = network comment) from the graphical comment
# records drawn inside the block, and lowering keeps the native block identity
# through ``source_block`` -> object-model ``source_offset``.


def test_fbd_header_identity_projection_separates_header_text_from_comment_records():
    from src.gxw.generation_contract import FBD_PARAMETER_MODEL_SCHEMA
    from src.gxw.ladder_lowering import fbd_topology_from_context
    from src.gxw.project_templates import blank_fbd_baseline
    from src.gxw.structured_pou import structured_block_text
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Start:', block_comment='first network comment', comment='graphical note',
             calls=[dict(id='call', type='TON', instance='Timer0',
                         arguments=dict(IN='M0', PT='T#1s', Q='Y0'))]),
        dict(title='Second:', nodes=[dict(id='a', template='contact', symbol='X1'),
                                     dict(id='b', template='coil', symbol='Y1')],
             wires=[dict(**{'from': 'a.OUT', 'to': 'b.IN'})])]), baseline=raw)
    context = read_project_context(saved.data)
    model = fbd_topology_from_context(context)
    assert model['preserve_blocks'] == [] and model['preserve_block_details'] == []
    assert [structured_block_text(block.raw_header) for block in context.program.blocks] == [
        ('Start:', 'first network comment'), ('Second:', '')]
    assert [network['source_block'] for network in model['networks']] == [
        block.offset for block in context.program.blocks]
    first, second = model['networks']
    assert (first['title'], first['block_comment'], first['comment']) == (
        'Start:', 'first network comment', 'graphical note')
    assert (second['title'], second['block_comment']) == ('Second:', '')
    assert 'comment' not in second
    assert all(node['template'] != 'comment'
               for network in model['networks'] for node in network['nodes'])
    properties = FBD_PARAMETER_MODEL_SCHEMA['properties']
    assert {'title', 'block_comment'} <= set(properties['networks']['items']['properties'])
    assert 'preserve_block_details' in properties
    assert FBD_PARAMETER_MODEL_SCHEMA['required'] == ['networks']


@pytest.mark.parametrize('text,expected', [('K0', {'BOOL','INT','DINT','WORD','DWORD'}),
    ('-32768', {'INT','DINT'}), ('16#FFFF', {'INT','DINT','WORD','DWORD'}),
    ('TRUE', {'BOOL'}), ('T#1s', {'TIME'}), ('"ABC"', {'STRING'}), ('2.5', {'REAL'})])
def test_fbd_semantics_literal_domains_use_current_cpu_language_facts(text, expected):
    from plc.fbd_semantics import parse_literal
    actual = parse_literal(text, 'FX3U')
    assert set(actual['types']) == expected
    assert actual['constant'] and not actual['writable']


@pytest.mark.parametrize('text', ['INT#0', 'BOOL#TRUE', 'TIME#1s', 'T#--1s', '2#102',
                                  'K21474836480', 'T#1ms1s', 'T#0.0001s',
                                  '3.4028235677973367E38', 'T#1.000000000000000000000000000001s'])
def test_fbd_semantics_rejects_malformed_literals_without_rewriting(text):
    from plc.fbd_semantics import parse_literal, FBDSemanticError
    with pytest.raises(FBDSemanticError) as error:
        parse_literal(text, 'FX3U')
    assert error.value.finding['expression'] == text
    assert error.value.finding['code'].startswith('fbd_literal_')


def test_fbd_semantics_integer_range_domain_property():
    from hypothesis import given, settings, strategies as st
    from plc.fbd_semantics import parse_literal
    @settings(max_examples=32, derandomize=True, deadline=None)
    @given(st.integers(-2147483648, 4294967295))
    def check(value):
        actual = parse_literal('K' + str(value), 'FX3U')
        assert ('INT' in actual['types']) == (-32768 <= value <= 32767)
        assert ('DINT' in actual['types']) == (-2147483648 <= value <= 2147483647)
        assert ('WORD' in actual['types']) == (0 <= value <= 65535)
        assert ('DWORD' in actual['types']) == (0 <= value <= 4294967295)
    check()


def test_fbd_semantics_actuals_keep_array_shape_member_owner_and_device_storage():
    context, _ = compound_structure_context('Q04UDV', local_formal=True)
    value = context.sources.actual_value('STATE[1].Spare')
    assert value['types'] == ['WORD']
    assert value['source_table'] == context.sources.local_table and value['member_path']
    whole = context.sources.actual_value('STATE')
    assert whole['shape'] and whole['types'][0].startswith('ARRAY') and 'STRUCT:' in whole['types'][0]
    context = read_project_context(default_baseline())
    assert set(context.sources.actual_value('K4M0')['types']) == {'INT','WORD'}
    assert context.sources.actual_value('K4M0')['storage']['bits'] == 16
    assert context.sources.actual_value('X0')['writable'] is False
    assert context.sources.actual_value('D0')['storage']['bits'] == 16


@pytest.mark.parametrize('entry', ['named', 'coordinate', 'parameters', 'transaction', 'candidate'])
@pytest.mark.parametrize('policy', ['checked', 'draft'])
def test_fbd_semantics_all_write_entries_reject_new_illegal_literals(entry, policy, tmp_path):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import prepare_object_project
    raw = blank_fbd_baseline('FX3U')
    context = read_project_context(raw)
    model = context.empty_model()
    model['nodes'] = [dict(id='s', template='input', symbol='INT#0', x=2, y=2),
                      dict(id='d', template='output', symbol='D0', x=12, y=2)]
    model['wires'] = [{'from':'s.OUT','to':'d.IN'}]
    if entry == 'coordinate':
        model['wires'] = [dict(start=[4,3], end=[12,3])]
    if entry == 'parameters':
        model = dict(program=context.program.logical_name, networks=[dict(nodes=[
            dict(id='move',template='function:MOV',symbol='MOV',arguments=dict(EN='TRUE',s='INT#0',d='D0'))])])
    with pytest.raises(ValueError) as error:
        if entry == 'transaction':
            from gxw.offline_edit import prepare_offline_edit
            prepare_offline_edit(raw, [dict(source_name=context.program.logical_name,
                command=dict(operation='edit', model=model))], save_policy=policy)
        elif entry == 'candidate':
            from application.fbd import prepare_candidate
            prepare_candidate(tmp_path / 'candidate', model=model, baseline=raw, save_policy=policy)
        else:
            prepare_object_project(model, baseline=raw, save_policy=policy)
    assert any(row['code'] == 'fbd_literal_syntax' and row.get('expression') == 'INT#0'
               for row in error.value.findings)
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('pt,accepted', [('T#1s', True), ('T#150ms', False)])
def test_fbd_semantics_ton_precision_is_distinct_from_valid_time_syntax(pt, accepted):
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U')
    model = dict(networks=[dict(calls=[dict(id='t',type='TON',instance='Timer',
        arguments=dict(IN='M0',PT=pt,Q='Y0'))])])
    if accepted:
        assert generate_object_project(model, baseline=raw).data
    else:
        with pytest.raises(ValueError) as error:
            generate_object_project(model, baseline=raw)
        assert any(row['code'] == 'fbd_timer_resolution' for row in error.value.findings)


def test_fbd_semantics_shared_storage_and_instance_order_reaches_physical_layout():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import source_checks
    raw = blank_fbd_baseline('FX3U')
    model = dict(networks=[dict(execution_order=['second','first'], calls=[
        dict(id='first',type='TON',instance='Timer',arguments=dict(IN='M0',PT='T#1s',Q='M1')),
        dict(id='second',type='TON',instance='Timer',arguments=dict(IN='M1',PT='T#2s',Q='M2'))])])
    saved = read_project_context(generate_object_project(model, baseline=raw).data)
    calls = sorted((n for n in saved.program.nodes if n.kind.value == 'function_block'), key=lambda n:n.bbox.top)
    report = source_checks(saved)['execution']
    assert report['order'] == [n.offset for n in calls]
    assert any(edge['kind']=='instance_state' for edge in report['dependencies'])
    assert any(edge['kind']=='shared_storage' for edge in report['dependencies'])
    assert all(item['en_disabled_is_in_false'] is False and item['en_disabled_is_reset'] is False
               for item in report['instances'])


def test_fbd_semantics_generic_multi_driver_is_error_even_without_concrete_type():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import prepare_object_project, source_checks
    from gxw.project_writer import write_prepared_project
    raw = blank_fbd_baseline('FX3U')
    model = dict(networks=[dict(nodes=[dict(id=name,template='function:MOV',symbol='MOV')
                                      for name in ('a','b','c')],
        wires=[{'from':'a.d','to':'c.s'}, {'from':'b.d','to':'c.s'}])])
    with pytest.raises(ValueError) as error:
        prepare_object_project(model, baseline=raw)
    assert any(row['code']=='fbd_multiple_drivers' for row in error.value.findings)
    saved = write_prepared_project(prepare_object_project(model, baseline=raw, save_policy='draft'))
    assert any(row['code']=='fbd_multiple_drivers' for row in source_checks(read_project_context(saved.data))['findings'])


def test_fbd_header_identity_symbol_edit_keeps_saved_positions_and_headers():
    from src.gxw.ladder_lowering import fbd_topology_from_context
    from src.gxw.project_templates import blank_fbd_baseline
    from src.gxw.structured_pou import structured_block_text
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Start:', block_comment='first network comment', comment='graphical note',
             nodes=[dict(id='a', template='contact', symbol='M0'),
                    dict(id='b', template='coil', symbol='Y0')],
             wires=[dict(**{'from': 'a.OUT', 'to': 'b.IN'})]),
        dict(title='Second:', nodes=[dict(id='c', template='contact', symbol='X1'),
                                     dict(id='d', template='coil', symbol='Y1')],
             wires=[dict(**{'from': 'c.OUT', 'to': 'd.IN'})])]), baseline=raw)
    context = read_project_context(saved.data)
    model = fbd_topology_from_context(context)
    before = [(node.symbol, node.bbox.left, node.bbox.top) for node in context.program.nodes]
    edited = deepcopy(model)
    target = next(node for node in edited['networks'][0]['nodes'] if node['symbol'] == 'M0')
    target['symbol'] = 'M5'
    current = read_project_context(generate_object_project(edited, baseline=saved.data).data)
    assert [structured_block_text(block.raw_header) for block in current.program.blocks] == [
        ('Start:', 'first network comment'), ('Second:', '')]
    after = [(node.symbol, node.bbox.left, node.bbox.top) for node in current.program.nodes]
    assert after == [('M5' if symbol == 'M0' else symbol, x, y) for symbol, x, y in before]


def test_fbd_header_identity_parameterized_edit_keeps_native_header_and_neighbours():
    from src.gxw.ladder_lowering import fbd_topology_from_context
    from src.gxw.project_templates import blank_fbd_baseline
    from src.gxw.structured_pou import structured_block_text
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Start:', block_comment='first network comment',
             calls=[dict(id='call', type='TON', instance='Timer0',
                         arguments=dict(IN='M0', PT='T#1s', Q='Y0'))]),
        dict(title='Second:', nodes=[dict(id='a', template='contact', symbol='X1'),
                                     dict(id='b', template='coil', symbol='Y1')],
             wires=[dict(**{'from': 'a.OUT', 'to': 'b.IN'})])]), baseline=raw)
    context = read_project_context(saved.data)
    model = fbd_topology_from_context(context)
    first, second = model['networks']
    changed = dict(model, networks=[
        dict(source_block=first['source_block'], title=first['title'],
             block_comment=first['block_comment'],
             calls=[dict(id='timer', type='TON', instance='Timer0',
                         arguments=dict(IN='M3', PT='T#3s', Q='Y0'))]), second])
    current = read_project_context(generate_object_project(changed, baseline=saved.data).data)
    assert [structured_block_text(block.raw_header) for block in current.program.blocks] == [
        ('Start:', 'first network comment'), ('Second:', '')]
    symbols = [node.symbol for node in current.program.nodes]
    assert 'M3' in symbols and 'T#3s' in symbols
    assert [symbol for symbol in symbols if symbol in ('X1', 'Y1')] == ['X1', 'Y1']


def test_fbd_header_identity_split_keeps_label_once_and_block_order():
    from src.gxw.ladder_lowering import fbd_topology_from_context
    from src.gxw.models import StructuredNode
    from src.gxw.project_templates import blank_fbd_baseline
    from src.gxw.structured_pou import structured_block_text
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Start:', block_comment='first network comment',
             nodes=[dict(id='a', template='contact', symbol='X0'),
                    dict(id='b', template='coil', symbol='Y0')],
             wires=[dict(**{'from': 'a.OUT', 'to': 'b.IN'})]),
        dict(title='Second:', nodes=[dict(id='c', template='contact', symbol='X1'),
                                     dict(id='d', template='coil', symbol='Y1')],
             wires=[dict(**{'from': 'c.OUT', 'to': 'd.IN'})])]), baseline=raw)
    context = read_project_context(saved.data)
    model = fbd_topology_from_context(context)
    first, second = model['networks']
    split = dict(source_block=first['source_block'], title=first['title'],
                 block_comment=first['block_comment'],
                 nodes=[dict(id='p', template='contact', symbol='X2'),
                        dict(id='q', template='coil', symbol='Y2'),
                        dict(id='r', template='contact', symbol='X3'),
                        dict(id='s', template='coil', symbol='Y3')],
                 wires=[dict(**{'from': 'p.OUT', 'to': 'q.IN'}),
                        dict(**{'from': 'r.OUT', 'to': 's.IN'})])
    current = read_project_context(generate_object_project(
        dict(model, networks=[split, second]), baseline=saved.data).data)
    assert [structured_block_text(block.raw_header) for block in current.program.blocks] == [
        ('Start:', 'first network comment'), ('', ''), ('Second:', '')]
    assert [[record.symbol for record in records if isinstance(record, StructuredNode)]
            for _, records in current.program.block_records()] == [
        ['X2', 'Y2'], ['X3', 'Y3'], ['X1', 'Y1']]


def test_fbd_header_identity_preserved_block_reports_readable_and_editable_scope():
    from src.gxw.ladder_lowering import fbd_topology_from_context, fbd_topology_to_object_model
    from src.gxw.models import UnknownRecord
    from src.gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Target:', block_comment='saved comment',
             nodes=[dict(id='a', template='contact', symbol='X0'),
                    dict(id='b', template='coil', symbol='Y0')],
             wires=[dict(**{'from': 'a.OUT', 'to': 'b.IN'})]),
        dict(title='Second:', nodes=[dict(id='c', template='contact', symbol='X1'),
                                     dict(id='d', template='coil', symbol='Y1')],
             wires=[dict(**{'from': 'c.OUT', 'to': 'd.IN'})])]), baseline=raw)
    context = read_project_context(saved.data)
    program = context.program
    first, _ = next(iter(program.block_records()))
    import struct
    opaque = UnknownRecord(999999, 8, 77, struct.pack('<II', 8, 77))
    program = replace(program, unknown_records=program.unknown_records + (opaque,),
                      blocks=(replace(first, record_offsets=first.record_offsets + (999999,)),
                              *program.blocks[1:]))
    context = replace(context, program=program)
    model = fbd_topology_from_context(context)
    assert model['preserve_blocks'] == [first.offset]
    detail, = model['preserve_block_details']
    assert (detail['source_block'], detail['source_offset']) == (first.offset, first.offset)
    assert (detail['label'], detail['parameterizable']) == ('Target', False)
    assert [reason['code'] for reason in detail['reasons']] == ['opaque_records']
    assert {'block_header', 'opaque_records'} <= set(detail['readable'])
    assert {'network_title', 'network_comment', 'declaration_edits'} <= set(detail['editable'])
    assert 'parameter_model_regeneration' in detail['preserve_scope']
    assert {'kind': 'jump_target', 'label': 'Target'} in detail['dependencies']
    assert detail['counts']['opaque_records'] == 1
    assert [network['source_block'] for network in model['networks']] == [
        block.offset for block in program.blocks[1:]]
    # The offset list stays authoritative; the explanation is read-only metadata.
    retained = fbd_topology_to_object_model(
        dict(networks=[], preserve_blocks=model['preserve_blocks'],
             preserve_block_details=deepcopy(model['preserve_block_details'])), context)
    assert [block['source_offset'] for block in retained['blocks']] == [first.offset]
    with pytest.raises(GXWFormatError, match='non-preserved'):
        fbd_topology_to_object_model(dict(networks=[dict(source_block=first.offset,
            nodes=[dict(id='z', template='contact', symbol='X9')])]), context)


def test_fbd_semantics_existing_illegal_literal_stays_readable_and_unrelated_editable(tmp_path):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.project_writer import build_gxw_project
    from gxw.structured_pou_writer import replace_node_symbol
    from gxw.object_model import read_project_context, generate_object_project, source_checks
    from gxw.ladder_lowering import fbd_topology_from_context
    from application.fbd import prepare_candidate
    raw = blank_fbd_baseline('FX3U')
    valid = generate_object_project(dict(networks=[dict(title='Keep:', nodes=[
        dict(id='move', template='function:MOV', symbol='MOV',
             arguments=dict(EN='TRUE', s='K0', d='D0'))])]), baseline=raw).data
    context = read_project_context(valid)
    # A controlled malformed historical source; the public writer must never
    # create it, but reading and an unrelated edit must remain possible.
    program = replace_node_symbol(context.program, 'K0', 'INT#0')
    old = build_gxw_project(valid, program).data
    context = read_project_context(old)
    assert source_checks(context)['status'] == 'failed'
    imported = prepare_candidate(tmp_path / 'import', imported=old)
    assert imported['metadata']['validation']['status'] == 'failed'
    model = fbd_topology_from_context(context)
    next(node for node in model['networks'][0]['nodes'] if node['symbol'] == 'D0')['symbol'] = 'D2'
    saved = prepare_candidate(tmp_path / 'edit', model=model, baseline=old)
    assert saved['metadata']['validation']['status'] == 'failed'
    assert any(row.get('expression') == 'INT#0' for row in saved['metadata']['validation']['source_checks']['findings'])
    next(node for node in model['networks'][0]['nodes'] if node['symbol'] == 'INT#0')['symbol'] = 'INT#1'
    with pytest.raises(ValueError) as error:
        prepare_candidate(tmp_path / 'new_error', model=model, baseline=old, save_policy='draft')
    assert any(row.get('expression') == 'INT#1' and row.get('introduced') for row in error.value.findings)


def test_fbd_header_identity_small_mov_edit_preserves_jump_target_and_execution_positions():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.ladder_lowering import fbd_topology_from_context
    from gxw.object_model import read_project_context, generate_object_project, source_checks
    from gxw.structured_pou import structured_block_text
    raw = blank_fbd_baseline('FX3U')
    saved = generate_object_project(dict(networks=[
        dict(title='Retry:', block_comment='Keep this network', comment='inside the graph', nodes=[
            dict(id='first', template='function:MOV', symbol='MOV', arguments=dict(EN='M0', s='K0', d='D0')),
            dict(id='second', template='function:MOV', symbol='MOV', arguments=dict(EN='M1', s='D0', d='D2'))]),
        dict(nodes=[dict(id='enable', template='contact', symbol='M2'), dict(id='jump',template='jump',symbol='Retry')],
             wires=[{'from':'enable.OUT','to':'jump.IN'}])]), baseline=raw).data
    before = read_project_context(saved)
    model = fbd_topology_from_context(before)
    next(node for network in model['networks'] for node in network['nodes'] if node['symbol'] == 'K0')['symbol'] = 'K1'
    after = read_project_context(generate_object_project(model, baseline=saved).data)
    assert [structured_block_text(block.raw_header) for block in after.program.blocks] == [
        structured_block_text(block.raw_header) for block in before.program.blocks]
    assert [(node.symbol, node.bbox) for node in after.program.nodes] == [
        ('K1' if node.symbol == 'K0' else node.symbol, node.bbox) for node in before.program.nodes]
    assert [(wire.start, wire.end) for wire in after.program.wires] == [(wire.start, wire.end) for wire in before.program.wires]
    assert not any(row['code'] == 'fbd_jump_target_unresolved' for row in source_checks(after)['findings'])


def test_fbd_semantics_new_network_does_not_turn_old_diagnostics_into_new_errors():
    from gxw.fbd_validation import introduced_findings
    error = dict(code='fbd_literal_syntax', severity='error', source_name='MAIN/Program.pou',
                 symbol='INT#0', expression='INT#0', message='invalid constant', block=1, node_offset=50)
    assert introduced_findings([error], [dict(error, block=2, node_offset=150)]) == []
    assert len(introduced_findings([error], [error, dict(error, block=2, node_offset=150)])) == 1


@pytest.mark.parametrize('output_type,accepted', [('INT', True), ('WORD', False)])
def test_fbd_semantics_iec_move_generic_binds_input_and_output_to_the_same_type(output_type, accepted):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context, generate_object_project
    raw = blank_fbd_baseline('FX3U')
    context = read_project_context(raw)
    model = dict(networks=[dict(nodes=[dict(id='copy',template='function:MOVE',symbol='MOVE',
        arguments={'_IN':'InputValue', 'MOVE':'OutputValue'})])], declaration_edits={
            context.sources.local_table: {'upserts': [dict(name='InputValue',data_type='INT'),
                                                    dict(name='OutputValue',data_type=output_type)]}})
    if accepted:
        assert generate_object_project(model,baseline=raw).data
    else:
        with pytest.raises(ValueError) as error:
            generate_object_project(model,baseline=raw)
        assert any(row['code']=='fbd_call_type_conflict' for row in error.value.findings)


def test_fbd_semantics_melsec_doubleword_device_view_does_not_cast_declared_int():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context, generate_object_project
    raw = blank_fbd_baseline('FX3U')
    context = read_project_context(raw)
    model = dict(networks=[dict(nodes=[dict(id='copy', template='function:DMOV',symbol='DMOV',
        arguments=dict(EN='TRUE',s='D0',d='D2'))])])
    assert generate_object_project(model,baseline=raw).data
    model['networks'][0]['nodes'][0]['arguments']['s'] = 'Narrow'
    model['declaration_edits'] = {context.sources.local_table:{'upserts':[dict(name='Narrow',data_type='INT',device='D0')]}}
    with pytest.raises(ValueError) as error:
        generate_object_project(model,baseline=raw)
    assert any(row['code']=='fbd_net_type_conflict' for row in error.value.findings)


def test_fbd_semantics_overlapping_device_spans_impose_execution_order():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context, generate_object_project, source_checks
    from plc.fbd_semantics import storage_keys
    raw = blank_fbd_baseline('FX3U')
    context = read_project_context(raw)
    assert storage_keys(context.sources.actual_value('K4M0'), plc_model='FX3U') & storage_keys(
        context.sources.actual_value('M15'), plc_model='FX3U')
    model = dict(networks=[dict(nodes=[
        dict(id='write',template='function:DMOV',symbol='DMOV',arguments=dict(EN='M0',s='D10',d='D0')),
        dict(id='read',template='function:MOV',symbol='MOV',arguments=dict(EN='M1',s='D1',d='D4'))])])
    context = read_project_context(generate_object_project(model,baseline=raw).data)
    assert len(context.program.blocks) == 1
    assert any(edge['kind']=='shared_storage' for edge in source_checks(context)['execution']['dependencies'])


@pytest.mark.parametrize('symbol,left,right,result,accepted', [
    ('@EQ-2','INT','INT','BOOL',True), ('@EQ-2','INT','WORD','BOOL',False),
    ('@ADD-2','BOOL','BOOL','BOOL',False), ('@ADD-2','INT','INT','INT',True),
    ('@AND-2','BOOL','BOOL','BOOL',True)])
def test_fbd_semantics_serialized_operator_contract_is_not_borrowed_from_st(symbol,left,right,result,accepted):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context, generate_object_project
    raw = blank_fbd_baseline('FX3U')
    context = read_project_context(raw)
    model = dict(networks=[dict(nodes=[dict(id='op',template='function:'+symbol,symbol=symbol,
        arguments={'0':'LeftValue','1':'RightValue','2':'ResultValue'})])], declaration_edits={
            context.sources.local_table:{'upserts':[dict(name=name,data_type=kind) for name,kind in
                [('LeftValue',left),('RightValue',right),('ResultValue',result)]]}})
    if accepted:
        assert generate_object_project(model,baseline=raw).data
    else:
        with pytest.raises(ValueError) as error:
            generate_object_project(model,baseline=raw)
        assert any(row['code'] in ('fbd_net_type_conflict','fbd_call_type_conflict') for row in error.value.findings)

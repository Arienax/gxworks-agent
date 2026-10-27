"""Current-object resolution uses native observations, never historical pointers."""
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
import json

import pytest
from hypothesis import given, settings, strategies as st

from gxw.active_graph import (active_graph_connections, active_graph_projection, graph_source_state,
                              graph_target_anchor, graph_wire_anchor, resolve_graph_target,
                              resolve_graph_wire_target, prepare_active_graph_action,
                              verify_active_graph_action)
from gxw.callable_sources import ProjectCallableSources
from gxw.declarations import DeclarationDocument, LabelRecord
from gxw.models import GXWFormatError


def observed(nodes=None):
    return {'status': 'observed', 'protocol_version': 1, 'native_version': '1.635.0.1',
        'owner': 'POU_01', 'owner_kind': 'program', 'active_network': 0,
        'networks': [{'index': 0, 'nodes': nodes or [
            {'index': 0, 'address': 0x120000, 'record': 'first-record', 'name': 'Probe_A001',
             'source_kind_code': 13, 'bounds': [3, 2, 5, 4], 'selected': True},
            {'index': 1, 'address': 0x120078, 'record': 'second-record', 'name': 'Y0',
             'source_kind_code': 14, 'bounds': [18, 2, 20, 4], 'selected': False}],
            'wires': [{'index': 0, 'start': [5, 3], 'end': [18, 3], 'selected': False}], 'junctions': []}]}


def test_projection_reuses_core_terminal_ports():
    graph = active_graph_projection(observed())
    source, sink = graph['networks'][0]['nodes']
    assert source['ports'] == [{'name': 'OUT', 'x': 2, 'y': 1}]
    assert sink['ports'] == [{'name': 'IN', 'x': 0, 'y': 1}]
    assert graph['networks'][0]['wires'][0]['start'] == [5, 3]


@given(base=st.integers(0x200000, 0x70000000), reverse=st.booleans())
@settings(max_examples=80)
def test_object_reconstruction_and_new_addresses_resolve_original_semantic_target(base, reverse):
    before = observed()
    anchor = graph_target_anchor(before, {'network': 0, 'id': 'b0:n0'})
    rebuilt = deepcopy(before)
    nodes = rebuilt['networks'][0]['nodes']
    if reverse:
        nodes.reverse()
    for index, node in enumerate(nodes):
        node.update(index=index, address=base + 120 * index, selected=False)
    target = resolve_graph_target(rebuilt, anchor)
    chosen = nodes[target['index']]
    assert chosen['name'] == 'Probe_A001'
    assert chosen['bounds'] == [3, 2, 5, 4]
    assert target['expected_record'] == 'first-record'
    assert set(target) == {'network', 'index', 'expected_record'}


@given(offset=st.integers(0, 1000), selected=st.booleans())
def test_selection_and_process_addresses_do_not_change_source_semantics(offset, selected):
    before, after = observed(), observed()
    for node in after['networks'][0]['nodes']:
        node['address'] += offset
        node['selected'] = selected
    assert graph_source_state(before) == graph_source_state(after)


@pytest.mark.parametrize('change', ['owner', 'kind', 'symbol', 'position', 'duplicate'])
def test_reconstructed_target_with_wrong_owner_or_meaning_is_rejected(change):
    before = observed()
    anchor = graph_target_anchor(before, {'network': 0, 'id': 'b0:n0'})
    after = deepcopy(before)
    if change == 'owner':
        after['owner'] = 'POU_02'
    elif change == 'kind':
        after['owner_kind'] = 'fb'
    elif change == 'symbol':
        after['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
    elif change == 'position':
        after['networks'][0]['nodes'][0]['bounds'] = [4, 2, 6, 4]
    else:
        duplicate = deepcopy(after['networks'][0]['nodes'][0])
        duplicate.update(index=2, address=0x130000)
        after['networks'][0]['nodes'].append(duplicate)
    with pytest.raises(GXWFormatError):
        resolve_graph_target(after, anchor)


def test_unknown_node_and_changed_dimensions_do_not_invent_ports():
    data = observed()
    data['networks'][0]['nodes'][0].update(source_kind_code=0, bounds=[3, 2, 7, 5])
    node = active_graph_projection(data)['networks'][0]['nodes'][0]
    assert node['template'] == 'unknown'
    assert node['ports'] == []
    assert node['ports_status'] == 'unavailable'


def test_same_address_cannot_be_in_two_networks():
    data = observed()
    second = deepcopy(data['networks'][0])
    second['index'] = 1
    data['networks'].append(second)
    with pytest.raises(GXWFormatError, match='more than one'):
        active_graph_projection(data)


def native_connections(ports):
    return {'status': 'observed', 'header_index': 0, 'port_count': len(ports),
            'ports': [dict(index=index, name=name, local_x=x, local_y=y, flags=flags,
                           native_type_mask=1 if name else 0,
                           record='00' * 22) for index, (name, x, y, flags) in enumerate(ports)]}


def current_native_terminals():
    value = observed()
    for node, port in zip(value['networks'][0]['nodes'], [('', 2, 1, 2), ('', 0, 1, 3)]):
        node['connections'] = native_connections([port])
    value['networks'][0]['wires'][0].update(record='native-wire-record', address=0x190000)
    return value


def action_sources(*, target_type='BOOL'):
    table = DeclarationDocument('POU_01.Labels.lh', 'local', 'POU_01', 0,
        (label('Probe_A001', 'BOOL', 1), label('Probe_B002', target_type, 1),
         label('Count', 'INT', 1, type_code=4)), b'', b'', b'')
    return ProjectCallableSources('FX3U/FX3UC', 'POU_01.Program.pou', {table.logical_name: table}, {})


def prepare_action(value, action, *, sources=None):
    return prepare_active_graph_action(value, {'network': 0, **action},
                                       sources=sources or action_sources(), device_profile='FX3U')


@pytest.mark.parametrize('scope', ['fb', 'second_network'])
@pytest.mark.parametrize('kind', ['move_node', 'add_wire'])
def test_unverified_coordinate_scopes_are_rejected_by_core_before_execution(scope, kind):
    value = current_native_terminals()
    network = 0
    if scope == 'fb':
        value['owner_kind'] = 'fb'
    else:
        extra = deepcopy(value['networks'][0])
        extra['index'] = network = 1
        for node in extra['nodes']:
            node['address'] += 0x40000
        value['networks'].append(extra)
        value['active_network'] = network
    action = dict(action=kind, network=network, node=f'b{network}:n0', x=3, y=6)
    if kind == 'add_wire':
        value['networks'][network]['wires'] = []
        action = dict(action=kind, network=network, **{'from': dict(node=f'b{network}:n0', port='OUT'),
                      'to': dict(node=f'b{network}:n1', port='IN')}, via=[])
    with pytest.raises(GXWFormatError, match='Program network zero'):
        prepare_active_graph_action(value, action, sources=action_sources(), device_profile='FX3U')


@pytest.mark.parametrize('kind', ['rebind_terminal', 'delete_terminal', 'move_node', 'delete_wire', 'add_wire'])
def test_each_active_action_verifies_only_the_requested_independent_native_effect(kind):
    before = current_native_terminals()
    action = {'action': kind, 'node': 'b0:n0'}
    if kind == 'rebind_terminal':
        action['symbol'] = 'Probe_B002'
    elif kind == 'move_node':
        action.update(x=3, y=6)
    elif kind == 'delete_wire':
        action = {'action': kind, 'wire': 'b0:w0'}
    elif kind == 'add_wire':
        before['networks'][0]['wires'] = []
        action = {'action': kind, 'from': {'node': 'b0:n0', 'port': 'OUT'},
                  'to': {'node': 'b0:n1', 'port': 'IN'}, 'via': []}
    original = deepcopy(before)
    prepared = prepare_action(before, action)
    assert before == original
    assert prepared['kind'] == 'active_graph_action'
    assert 'model' not in prepared and 'raw' not in prepared
    after = deepcopy(before)
    network = after['networks'][0]
    if kind == 'rebind_terminal':
        network['nodes'][0]['name'] = 'Probe_B002'
    elif kind == 'delete_terminal':
        network['nodes'].pop(0)
        network['nodes'][0]['index'] = 0
    elif kind == 'move_node':
        network['nodes'][0]['bounds'] = [3, 6, 5, 8]
    elif kind == 'delete_wire':
        network['wires'] = []
    else:
        # Accept a reversed native representation and collinear subdivision.
        network['wires'] = [dict(index=0, start=[11, 3], end=[5, 3]),
                            dict(index=1, start=[18, 3], end=[11, 3])]
        assert prepared['endpoints']['from']['point'] == [5, 3]
        assert prepared['endpoints']['to']['point'] == [18, 3]
    assert verify_active_graph_action(before, after, prepared, sources=action_sources()) == {
        'status': 'verified', 'action': kind, 'network': 0, 'owner': 'POU_01',
        'owner_kind': 'program', 'diagnostics': [], 'save_performed': False}
    assert verify_active_graph_action(before, before, prepared, sources=action_sources())['status'] == 'unconfirmed'


@pytest.mark.parametrize('kind', ['delete_terminal', 'move_node'])
def test_native_auto_rewiring_on_node_action_remains_unconfirmed(kind):
    before = current_native_terminals()
    action = {'action': kind, 'node': 'b0:n0'}
    if kind == 'move_node':
        action.update(x=3, y=6)
    prepared = prepare_action(before, action)
    after = deepcopy(before)
    if kind == 'move_node':
        after['networks'][0]['nodes'][0]['bounds'] = [3, 6, 5, 8]
        after['networks'][0]['wires'][0]['start'] = [5, 7]
        after['networks'][0]['wires'][0]['end'] = [18, 7]
    else:
        after['networks'][0]['nodes'].pop(0)
        after['networks'][0]['nodes'][0]['index'] = 0
        after['networks'][0]['wires'] = []
    assert verify_active_graph_action(before, after, prepared, sources=action_sources())['status'] == 'unconfirmed'


@pytest.mark.parametrize('change', ['other_node', 'other_network', 'interface', 'binding', 'current_declaration'])
def test_active_action_cannot_hide_unrequested_source_or_session_changes(change):
    before = current_native_terminals()
    second = deepcopy(before['networks'][0])
    second['index'] = 1
    for node in second['nodes']:
        node['address'] += 0x500000
    before['networks'].append(second)
    before['identity'], before['data_id'] = {'process_id': 1234}, 'current-owner'
    prepared = prepare_action(before, {'action': 'rebind_terminal', 'node': 'b0:n0', 'symbol': 'Probe_B002'})
    after = deepcopy(before)
    after['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
    sources = action_sources()
    if change == 'other_node':
        after['networks'][0]['nodes'][1]['name'] = 'Y1'
    elif change == 'other_network':
        after['networks'][1]['wires'] = []
    elif change == 'interface':
        after['networks'][0]['nodes'][1]['connections']['ports'][0]['flags'] = 1
    elif change == 'binding':
        after['identity']['process_id'] = 5678
    else:
        sources = action_sources(target_type='INT')
    assert verify_active_graph_action(before, after, prepared, sources=sources)['status'] == 'unconfirmed'


@given(reverse=st.booleans(), translated=st.integers(0, 20), split=st.integers(1, 12))
@settings(max_examples=60, deadline=None)
def test_active_action_reconstruction_uses_semantics_and_conductive_geometry(reverse, translated, split):
    before = current_native_terminals()
    for node in before['networks'][0]['nodes']:
        node['bounds'] = [v+translated if i % 2 == 0 else v for i, v in enumerate(node['bounds'])]
    before['networks'][0]['wires'][0].update(start=[5+translated, 3], end=[18+translated, 3])
    anchor = graph_target_anchor(before, {'network': 0, 'id': 'b0:n0'})
    prepared = prepare_action(before, {'action': 'rebind_terminal', 'node': anchor, 'symbol': 'Probe_B002'})
    after = deepcopy(before)
    after['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
    nodes = after['networks'][0]['nodes']
    if reverse:
        nodes.reverse()
    for index, node in enumerate(nodes):
        node.update(index=index, address=0x800000+index*200, selected=False)
    cut = 5+translated+split
    after['networks'][0]['wires'] = [dict(index=0, start=[cut, 3], end=[5+translated, 3]),
                                    dict(index=1, start=[18+translated, 3], end=[cut, 3])]
    assert verify_active_graph_action(before, after, prepared, sources=action_sources())['status'] == 'verified'


@pytest.mark.parametrize('change', ['missing_network', 'another_network', 'foreign_anchor', 'unbuilt_ports',
                                   'junction', 'wrong_profile', 'same_symbol', 'new_type', 'unsupported'])
def test_invalid_active_action_preconditions_are_rejected(change):
    before = current_native_terminals()
    action = {'action': 'rebind_terminal', 'node': 'b0:n0', 'symbol': 'Probe_B002', 'network': 0}
    kwargs = {'sources': action_sources(), 'device_profile': 'FX3U'}
    if change == 'missing_network':
        action.pop('network')
    elif change == 'another_network':
        action['network'] = 1
    elif change == 'foreign_anchor':
        action['node'] = graph_target_anchor(before, {'network': 0, 'id': 'b0:n0'})
        action['node']['owner'] = 'POU_02'
    elif change == 'unbuilt_ports':
        before['networks'][0]['nodes'][0]['connections'].update(status='not_built', ports=[])
    elif change == 'junction':
        before['networks'][0]['junctions'] = [{'record': 'unknown-fixpoint'}]
    elif change == 'wrong_profile':
        kwargs['device_profile'] = 'Q03UDV'
    elif change == 'same_symbol':
        action['symbol'] = 'Probe_A001'
    elif change == 'new_type':
        action['symbol'] = 'Count'
    else:
        action['action'] = 'add_terminal'
    with pytest.raises(GXWFormatError):
        prepare_active_graph_action(before, action, **kwargs)


@pytest.mark.parametrize('change', ['direction', 'type', 'diagonal', 'duplicate', 'unintended_T', 'interior', 'wire_tap'])
def test_active_add_wire_uses_existing_port_type_and_connectivity_rules(change):
    before = current_native_terminals()
    before['networks'][0]['wires'] = []
    action = {'action': 'add_wire', 'from': {'node': 'b0:n0', 'port': 'OUT'},
              'to': {'node': 'b0:n1', 'port': 'IN'}, 'via': []}
    if change == 'direction':
        action['from'], action['to'] = action['to'], action['from']
    elif change == 'type':
        before['networks'][0]['nodes'][0]['name'] = 'Count'
    elif change == 'diagonal':
        action['via'] = [[10, 4]]
    elif change == 'duplicate':
        before['networks'][0]['wires'] = [dict(index=0, start=[5, 3], end=[18, 3])]
    elif change == 'unintended_T':
        before['networks'][0]['wires'] = [dict(index=0, start=[10, 3], end=[10, 8])]
    elif change == 'interior':
        other = deepcopy(before['networks'][0]['nodes'][0])
        other.update(index=2, address=0x700000, name='Probe_B002', bounds=[9, 2, 11, 4])
        before['networks'][0]['nodes'].append(other)
    else:
        action['from'] = {'wire': 'b0:w0', 'point': [5, 3]}
    with pytest.raises(GXWFormatError):
        prepare_action(before, action)


def test_active_add_wire_rejects_a_second_writer_already_in_the_receiving_net():
    before = current_native_terminals()
    other = deepcopy(before['networks'][0]['nodes'][0])
    other.update(index=2, address=0x700000, name='Probe_B002', bounds=[3, 6, 5, 8])
    before['networks'][0]['nodes'].append(other)
    with pytest.raises(GXWFormatError, match='multiple port writers'):
        prepare_action(before, {'action': 'add_wire', 'from': {'node': 'b0:n2', 'port': 'OUT'},
                               'to': {'node': 'b0:n1', 'port': 'IN'}, 'via': [[18, 7]]})


def test_native_subdivision_at_an_interior_crossing_cannot_masquerade_as_same_topology():
    before = current_native_terminals()
    before['networks'][0]['wires'].append(dict(index=1, start=[10, 1], end=[10, 5]))
    prepared = prepare_action(before, {'action': 'rebind_terminal', 'node': 'b0:n0', 'symbol': 'Probe_B002'})
    after = deepcopy(before)
    after['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
    # Conductive interval coverage is identical, but this new endpoint at the
    # crossing turns an isolated wire into a T on the data net.
    after['networks'][0]['wires'][1]['end'] = [10, 3]
    after['networks'][0]['wires'].append(dict(index=2, start=[10, 3], end=[10, 5]))
    receipt = verify_active_graph_action(before, after, prepared, sources=action_sources())
    assert receipt['status'] == 'unconfirmed'
    assert receipt['diagnostics'][0]['code'] == 'active_graph_effect_unconfirmed'


def test_explicit_new_wire_can_cross_an_unrelated_wire_interior_without_joining_it():
    before = current_native_terminals()
    before['networks'][0]['wires'] = [dict(index=0, start=[10, 1], end=[10, 5])]
    prepared = prepare_action(before, {'action': 'add_wire', 'from': {'node': 'b0:n0', 'port': 'OUT'},
                                      'to': {'node': 'b0:n1', 'port': 'IN'}, 'via': []})
    after = deepcopy(before)
    after['networks'][0]['wires'].append(dict(index=1, start=[5, 3], end=[18, 3]))
    assert verify_active_graph_action(before, after, prepared, sources=action_sources())['status'] == 'verified'


@pytest.mark.parametrize('kind,reverse_ports', [(1, False), (1, True), (2, False), (2, True)])
def test_active_callable_endpoints_use_actual_names_and_current_instance_source(kind, reverse_ports):
    value, sources = callable_observation(kind, reverse_ports)
    terminal_value = current_native_terminals()
    left, right = deepcopy(terminal_value['networks'][0]['nodes'])
    left.update(index=0, name='TRUE', bounds=[3, 3, 5, 5])
    value['networks'][0]['nodes'][0]['index'] = 1
    right.update(index=2, bounds=[18, 3, 20, 5])
    value['networks'][0]['nodes'] = [left, value['networks'][0]['nodes'][0], right]
    value['networks'][0]['wires'] = [dict(index=0, start=[16, 4], end=[18, 4])]
    prepared = prepare_action(value, {'action': 'add_wire', 'from': {'node': 'b0:n0', 'port': 'OUT'},
                                      'to': {'node': 'b0:n1', 'port': 'IN'}, 'via': []}, sources=sources)
    assert prepared['endpoints']['to']['point'] == [10, 4]
    after = deepcopy(value)
    after['networks'][0]['wires'].append(dict(index=1, start=[5, 4], end=[10, 4]))
    assert verify_active_graph_action(value, after, prepared, sources=sources)['status'] == 'verified'


def label(name, data_type, class_code, number=1, type_code=1):
    return LabelRecord(name, data_type, class_code, '', '', 0, '', '', number, '', 0, type_code, '')


def callable_observation(kind=2, reverse_ports=False):
    name = 'Logic' if kind == 2 else 'LogicFn'
    rows = (label('IN', 'BOOL', 3), label('OUT', 'BOOL', 4))
    main = DeclarationDocument('POU_01.Labels.lh', 'local', 'POU_01', 0,
        (label('FB1', name, 1, type_code=15),), b'', b'', b'')
    definition = DeclarationDocument(name + '.Labels.lh', 'local', name, 0, rows,
        b'', b'', b'', owner_pou_type=0x1000002 if kind == 2 else 0x1000003)
    sources = ProjectCallableSources('FX3U/FX3UC', 'POU_01.Program.pou',
        {main.logical_name: main, definition.logical_name: definition}, {})
    ports = [('IN', 0, 2, 1 if kind == 2 else 3), ('OUT', 6, 2, 0 if kind == 2 else 2)]
    if reverse_ports:
        ports.reverse()
    node = {'index': 0, 'address': 0x140000, 'record': 'callable-record',
            'name': 'FB1' if kind == 2 else '', 'source_kind_code': kind,
            'type_name': name, 'bounds': [10, 2, 16, 6], 'selected': False,
            'connections': native_connections(ports)}
    value = observed([node])
    value['networks'][0]['wires'] = []
    return value, sources


def test_actual_unnamed_terminal_ports_are_cross_checked_before_core_names():
    value = current_native_terminals()
    source, sink = active_graph_projection(value)['networks'][0]['nodes']
    assert source['ports'] == [{'name': 'OUT', 'x': 2, 'y': 1, 'native_index': 0,
                               'native_name': '', 'flags': 2, 'side': 'out',
                               'native_type_mask': 0, 'native_type_status': 'unavailable'}]
    assert sink['ports'][0]['name'] == 'IN'
    assert sink['ports'][0]['flags'] == 3
    assert sink['ports'][0]['side'] == 'in'
    assert source['ports_status'] == sink['ports_status'] == 'resolved'


@pytest.mark.parametrize('change', ['not_built', 'unavailable', 'direction', 'position', 'count', 'name'])
def test_unobserved_or_mismatched_native_terminal_ports_do_not_borrow_template(change):
    value = current_native_terminals()
    connection = value['networks'][0]['nodes'][0]['connections']
    if change in ('not_built', 'unavailable'):
        connection.update(status=change, ports=[])
    elif change == 'direction':
        connection['ports'][0]['flags'] = 3
    elif change == 'position':
        connection['ports'][0]['local_x'] = 1
    elif change == 'count':
        connection['port_count'] = 2
    else:
        connection['ports'][0]['name'] = 'IN'
    node = active_graph_projection(value)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert node['ports'] == []
    assert node['ports_diagnostic']['message']


@pytest.mark.parametrize('kind', [1, 2])
@pytest.mark.parametrize('reverse_ports', [False, True])
def test_callable_ports_bind_actual_names_and_keep_native_order(kind, reverse_ports):
    value, sources = callable_observation(kind, reverse_ports)
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    expected = ['OUT', 'IN'] if reverse_ports else ['IN', 'OUT']
    assert [p['name'] for p in node['ports']] == expected
    assert [p['native_name'] for p in node['ports']] == expected
    assert [p['native_index'] for p in node['ports']] == [0, 1]
    assert all(p['data_type'] == 'BOOL' for p in node['ports'])
    if kind == 1:
        assert node['symbol'] == 'LogicFn'


@pytest.mark.parametrize('formal,native', [('Next', "@'Next'"), ('Next', "@'nExT'"),
    ('Next', 'nExT'), ('END_IF', "@'end_if'"), ('_Next', "@'_NEXT'"), ('変数', "@'変数'")])
def test_complete_native_identifier_quote_matches_current_formal_and_preserves_raw_name(formal, native):
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(definition.rows[0], replace(definition.rows[1], name=formal)))})
    value['networks'][0]['nodes'][0]['connections']['ports'][1]['name'] = native
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    assert node['ports'][1]['name'] == node['ports'][1]['formal_name'] == formal
    assert node['ports'][1]['native_name'] == native


@given(name=st.from_regex(r'[^\W\d]\w{0,23}', fullmatch=True).filter(lambda name: name.upper() not in ('EN', 'ENO')))
@settings(max_examples=60, deadline=None)
def test_complete_unicode_identifier_wrapper_preserves_current_formal_identity(name):
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(definition.rows[0], replace(definition.rows[1], name=name)))})
    actual = "@'" + name + "'"
    value['networks'][0]['nodes'][0]['connections']['ports'][1]['name'] = actual
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    assert node['ports'][1]['formal_name'] == name
    assert node['ports'][1]['native_name'] == actual


@pytest.mark.parametrize('native', ["@'Next", "@Next'", "@'Next'extra", " @'Next'", "@'Next' ",
    "@@'Next'", "@''", "@'Ne''xt'", "@'Next\n'", "@'Next$'", "@'1Next'", "'Next'"])
def test_partial_or_unknown_native_name_escaping_is_never_guessed(native):
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(definition.rows[0], replace(definition.rows[1], name='Next')))})
    value['networks'][0]['nodes'][0]['connections']['ports'][1]['name'] = native
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert node['ports'] == []


@pytest.mark.parametrize('names', [('Next', "@'Next'"), ("@'Next'", "@'NEXT'"), ('Next', 'NEXT')])
def test_quote_or_case_normalization_cannot_merge_two_native_ports_on_the_same_side(names):
    value, sources = callable_observation()
    raw = value['networks'][0]['nodes'][0]
    raw['bounds'][3] = 7
    raw['connections'] = native_connections([('IN', 0, 2, 1), (names[0], 6, 2, 0), (names[1], 6, 3, 0)])
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert 'ambiguous' in node['ports_diagnostic']['message']


def test_quote_normalization_also_rejects_colliding_current_source_formals():
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(definition.rows[0], label('Next', 'BOOL', 4),
                                                           label("@'NEXT'", 'BOOL', 4, number=2)))})
    raw = value['networks'][0]['nodes'][0]
    raw['bounds'][3] = 7
    raw['connections'] = native_connections([('IN', 0, 2, 1), ('Next', 6, 2, 0), ('Distinct', 6, 3, 0)])
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert 'ambiguous after quote normalization' in node['ports_diagnostic']['message']


def test_native_reserved_formal_quote_keeps_inout_opposite_directions_distinct():
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(label('Next', 'BOOL', 5),))})
    raw = value['networks'][0]['nodes'][0]
    raw['connections'] = native_connections([("@'Next'", 0, 2, 1), ("@'NEXT'", 6, 2, 0)])
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    assert [p['name'] for p in node['ports']] == ['in.Next', 'out.Next']
    assert [p['native_name'] for p in node['ports']] == ["@'Next'", "@'NEXT'"]


def test_live_fb_four_port_witness_with_quoted_next_preserves_interface_types_and_geometry():
    # Minimal public semantic witness from the independently observed native
    # Live_ST_FB insertion and current CSV, without process bytes or pointers.
    rows = (label('Enable', 'BOOL', 3), label('Value', 'INT', 3, number=2, type_code=4),
            label('Q', 'BOOL', 4), label('Next', 'INT', 4, number=2, type_code=4))
    main = DeclarationDocument('POU_01.Labels.lh', 'local', 'POU_01', 0,
        (label('Live_Inst', 'Live_ST_FB', 1, type_code=15),), b'', b'', b'')
    definition = DeclarationDocument('Live_ST_FB.Labels.lh', 'local', 'Live_ST_FB', 0, rows,
        b'', b'', b'', owner_pou_type=0x1000002)
    sources = ProjectCallableSources('FX3U/FX3UC', 'POU_01.Program.pou',
                                     {main.logical_name: main, definition.logical_name: definition}, {})
    raw = dict(index=0, address=0x140000, record='interface-only-witness', name='Live_Inst',
        type_name='Live_ST_FB', source_kind_code=2, bounds=[30, 1, 39, 5], selected=False,
        connections=native_connections([('Enable',0,2,1), ('Value',0,3,1), ('Q',9,2,0), ("@'Next'",9,3,0)]))
    for index, mask in enumerate((1,4,1,4)):
        raw['connections']['ports'][index]['native_type_mask'] = mask
    value = observed([raw])
    value['networks'][0]['wires'] = []
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    assert [(p['name'], p['data_type'], p['x'], p['y'], p['side']) for p in node['ports']] == [
        ('Enable','BOOL',0,2,'in'), ('Value','INT',0,3,'in'), ('Q','BOOL',9,2,'out'), ('Next','INT',9,3,'out')]
    assert node['ports'][3]['native_name'] == "@'Next'"
    assert node['native_type_status'] == 'matched'


@pytest.mark.parametrize('change', ['no_sources', 'wrong_owner', 'instance_type', 'native_name',
                                  'empty_name', 'direction', 'duplicate_name', 'definition_type'])
def test_callable_source_or_native_port_mismatch_remains_unavailable(change):
    value, sources = callable_observation()
    raw = value['networks'][0]['nodes'][0]
    if change == 'no_sources':
        sources = None
    elif change == 'wrong_owner':
        sources.program_name = 'Other.Program.pou'
    elif change == 'instance_type':
        main = sources.declarations['POU_01.Labels.lh']
        sources = sources.with_declarations({**sources.declarations,
            main.logical_name: replace(main, rows=(replace(main.rows[0], data_type='Other'),))})
    elif change == 'definition_type':
        raw['type_name'] = 'Other'
    elif change == 'native_name':
        raw['connections']['ports'][0]['name'] = 'STALE'
    elif change == 'empty_name':
        raw['connections']['ports'][0]['name'] = ''
    elif change == 'direction':
        raw['connections']['ports'][0]['flags'] = 0
    else:
        extra = deepcopy(raw['connections']['ports'][0])
        extra.update(index=2, local_y=3)
        raw['connections']['ports'].append(extra)
        raw['connections']['port_count'] = 3
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports'] == []
    assert node['ports_status'] == 'unavailable'
    assert node['ports_diagnostic']['code'] == 'active_ports_unavailable'


def test_inout_native_names_can_repeat_only_across_opposite_directions():
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(*definition.rows, label('IO', 'BOOL', 5)))})
    raw = value['networks'][0]['nodes'][0]
    raw['bounds'][3] = 7
    raw['connections'] = native_connections([('IN', 0, 2, 1), ('IO', 0, 3, 1),
                                              ('IO', 6, 3, 0), ('OUT', 6, 2, 0)])
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'resolved'
    assert [p['name'] for p in node['ports']] == ['IN', 'in.IO', 'out.IO', 'OUT']


@pytest.mark.parametrize('mask', [None, 0, 1, 4])
def test_callable_native_mask_does_not_invent_compiler_type_acceptance(mask):
    value, sources = callable_observation()
    value['networks'][0]['nodes'][0]['connections']['ports'][0]['native_type_mask'] = mask
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    if mask == 4:
        assert node['ports_status'] == 'unavailable'
        assert 'type mask' in node['ports_diagnostic']['message']
    else:
        assert node['ports_status'] == 'resolved'
        assert node['ports'][0]['data_type'] == 'BOOL'
        assert node['ports'][0]['native_type_mask'] == mask
        assert node['native_type_status'] == ('matched' if mask == 1 else 'unavailable')


def test_changed_formal_type_with_old_nonzero_native_mask_is_unavailable():
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    changed = replace(definition.rows[0], data_type='INT', type_code=2)
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(changed, definition.rows[1]))})
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert 'type mask' in node['ports_diagnostic']['message']


def test_csv_row_order_cannot_override_actual_native_port_names():
    value, sources = callable_observation()
    definition = sources.declarations['Logic.Labels.lh']
    sources = sources.with_declarations({**sources.declarations,
        definition.logical_name: replace(definition, rows=(label('FIRST', 'BOOL', 3, number=2),
            label('SECOND', 'BOOL', 3, number=1), definition.rows[1]))})
    raw = value['networks'][0]['nodes'][0]
    raw['bounds'][3] = 7
    raw['connections'] = native_connections([('FIRST', 0, 2, 1), ('SECOND', 0, 3, 1), ('OUT', 6, 2, 0)])
    node = active_graph_projection(value, sources=sources)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert 'name' in node['ports_diagnostic']['message']


@pytest.mark.parametrize(('field', 'value'), [('flags', True), ('flags', 65536),
    ('local_x', -1), ('local_y', 32768), ('native_type_mask', -1), ('index', True)])
def test_invalid_native_connection_bounds_are_explicitly_unavailable(field, value):
    observation = current_native_terminals()
    observation['networks'][0]['nodes'][0]['connections']['ports'][0][field] = value
    node = active_graph_projection(observation)['networks'][0]['nodes'][0]
    assert node['ports_status'] == 'unavailable'
    assert node['ports_diagnostic']


def test_unbound_callable_native_interface_changes_are_visible_in_source_guard():
    before, _ = callable_observation()
    after = deepcopy(before)
    after['networks'][0]['nodes'][0]['connections']['ports'][0]['local_y'] = 3
    assert graph_source_state(before) != graph_source_state(after)


@given(reverse=st.booleans(), offset=st.integers(0, 100000), leading=st.booleans())
@settings(max_examples=80, deadline=None)
def test_wire_anchor_survives_reverse_geometry_reconstruction_and_new_native_index(reverse, offset, leading):
    before = current_native_terminals()
    anchor = graph_wire_anchor(before, {'network': 0, 'start': [18, 3], 'end': [5, 3]})
    after = deepcopy(before)
    wire = after['networks'][0]['wires'][0]
    if reverse:
        wire['start'], wire['end'] = wire['end'], wire['start']
    wire['address'] += offset
    if leading:
        after['networks'][0]['wires'].insert(0,
            {'index': 0, 'start': [1, 9], 'end': [2, 9], 'record': 'other', 'selected': False})
        wire['index'] = 1
    target = resolve_graph_wire_target(after, anchor)
    assert target['index'] == int(leading)
    assert target['expected_record'] == 'native-wire-record'
    assert target['element_kind'] == 'wire'


def test_duplicate_reverse_wire_geometry_and_foreign_owner_are_rejected():
    value = current_native_terminals()
    anchor = graph_wire_anchor(value, {'network': 0, 'id': 'b0:w0'})
    duplicate = deepcopy(value['networks'][0]['wires'][0])
    duplicate.update(index=1, start=[18, 3], end=[5, 3])
    value['networks'][0]['wires'].append(duplicate)
    with pytest.raises(GXWFormatError, match='Duplicate'):
        graph_wire_anchor(value, {'network': 0, 'id': 'b0:w0'})
    with pytest.raises(GXWFormatError, match='ambiguous'):
        resolve_graph_wire_target(value, anchor)
    value['owner'] = 'Other'
    with pytest.raises(GXWFormatError, match='another'):
        resolve_graph_wire_target(value, anchor)


@given(dx=st.integers(0, 500), dy=st.integers(0, 500), reverse=st.booleans(), tee=st.booleans())
@settings(max_examples=80, deadline=None)
def test_native_topology_preserves_translation_reversal_and_cross_vs_tee(dx, dy, reverse, tee):
    value = current_native_terminals()
    network = value['networks'][0]
    network['wires'].append({'index': 1, 'start': [10, 0], 'end': [10, 3 if tee else 6], 'selected': False})
    for node in network['nodes']:
        left, top, right, bottom = node['bounds']
        node['bounds'] = [left+dx, top+dy, right+dx, bottom+dy]
    for wire in network['wires']:
        wire['start'] = [wire['start'][0]+dx, wire['start'][1]+dy]
        wire['end'] = [wire['end'][0]+dx, wire['end'][1]+dy]
        if reverse:
            wire['start'], wire['end'] = wire['end'], wire['start']
    result = active_graph_connections(value)
    assert result['status'] == 'resolved'
    assert len(result['nets']) == (1 if tee else 2)
    signal = next(net for net in result['nets'] if net['ports'])
    assert {(p['node'], p['port']) for p in signal['ports']} == {('b0:n0', 'OUT'), ('b0:n1', 'IN')}
    assert set(signal['wires']) == ({'b0:w0', 'b0:w1'} if tee else {'b0:w0'})
    assert not {'raw', 'program', 'source_bytes', 'saved_project'} & result.keys()


@given(network_count=st.integers(2, 8))
@settings(deadline=None)
def test_same_geometry_in_different_native_networks_stays_electrically_separate(network_count):
    value = current_native_terminals()
    original = value['networks'][0]
    for index in range(1, network_count):
        network = deepcopy(original)
        network['index'] = index
        for node in network['nodes']:
            node['address'] += index*0x10000
        value['networks'].append(network)
    result = active_graph_connections(value)
    assert len(result['nets']) == network_count
    for net in result['nets']:
        assert len(net['ports']) == 2
        assert all(p['node'].startswith(f"b{net['network']}:") for p in net['ports'])
        assert net['wires'] == [f"b{net['network']}:w0"]


def test_unavailable_port_or_unparsed_junction_marks_connections_partial():
    value = current_native_terminals()
    value['networks'][0]['nodes'][0]['connections']['status'] = 'not_built'
    value['networks'][0]['junctions'] = [{'record': 'unknown-fixpoint'}]
    result = active_graph_connections(value)
    assert result['status'] == 'partial'
    assert len(result['unresolved']) == 2
    assert all(p['node'] != 'b0:n0' for net in result['nets'] for p in net['ports'])


def test_diagonal_native_wire_is_rejected_by_existing_connectivity_rules():
    value = current_native_terminals()
    value['networks'][0]['wires'][0]['end'][1] += 1
    with pytest.raises(GXWFormatError, match='diagonal'):
        active_graph_connections(value)


def _native_compile_line_order_witness():
    from tools.offline_inputs import private_path
    root = private_path('research/experiments/native-live-session-20261018')
    after = json.loads((root/'100-inspect-mcp-compile-source-change/current-observation.json').read_text('utf-8'))
    before = deepcopy(after)
    # Native 098 captured the three original LINE records; 100 independently
    # observed their changed order after native compile. Keep all other pages.
    original = json.loads((root/'098-recover-original-mcp-wire/after.json').read_text('utf-8'))
    before['graphs'][0]['networks'][0]['wires'] = original['networks'][0]['wires']
    return before, after


def test_native_compile_line_array_reordering_preserves_source_and_public_actual_indices():
    from gxw.live_context import live_context_state, verify_live_observations
    before, after = _native_compile_line_order_witness()
    a, b = live_context_state(before), live_context_state(after)
    assert a['graphs'][0]['networks'][0]['wires'][0]['end'] == [18, 3]
    assert b['graphs'][0]['networks'][0]['wires'][0]['end'] == [16, 8]
    assert verify_live_observations(before, after)['source'] == 'unchanged'


@pytest.mark.parametrize('damage', ['endpoint', 'missing', 'extra', 'subdivision', 'st', 'port'])
def test_line_order_normalization_still_rejects_actual_source_or_port_changes(damage):
    from gxw.live_context import verify_live_observations
    before, after = _native_compile_line_order_witness()
    network = after['graphs'][0]['networks'][0]
    if damage == 'endpoint':
        network['wires'][0]['end'][1] += 1
    elif damage == 'missing':
        network['wires'].pop()
    elif damage == 'extra':
        network['wires'].append(deepcopy(network['wires'][0]))
        network['wires'][-1]['index'] = 3
    elif damage == 'subdivision':
        network['wires'][0]['end'] = [16, 5]
        network['wires'].append(dict(index=3, start=[16, 5], end=[16, 8]))
    elif damage == 'st':
        after['st'][0]['st_text'] += '\nQ := FALSE;'
    else:
        network['nodes'][0]['connections']['ports'][0]['local_y'] += 1
    with pytest.raises(GXWFormatError):
        verify_live_observations(before, after)


@pytest.mark.parametrize('name,requested', [('original.gxw', False), ('native-unconfirmed.gxw', True)])
def test_current_compile_plan_uses_the_frozen_native_project_check_option(name, requested):
    from tools.offline_inputs import private_path
    from gxw.live_context import live_compile_plan
    raw = private_path('research/experiments/native-live-session-20261018/102-save-compile-option-mismatch').joinpath(name).read_bytes()
    assert live_compile_plan(raw, 11) == dict(revision=11, public_checks_requested=requested)


def test_unknown_saved_check_option_refuses_a_native_compile_plan():
    import struct
    from tools.offline_inputs import private_path
    from gxw.fb_authoring import _context
    from gxw.live_context import live_compile_plan
    from tests.test_gxw_object_model import replay_source_records
    raw = private_path('research/experiments/native-live-session-20261018/102-save-compile-option-mismatch/original.gxw').read_bytes()
    streams = _context(raw, require_fbd=False)[1]
    name, = [name for name in streams if name.endswith('.prj')]
    payload = streams[name]
    assert payload.count(struct.pack('<I', 0x20010001)) == 1
    payload = payload.replace(struct.pack('<I', 0x20010001), struct.pack('<I', 0x20010002))
    with pytest.raises(GXWFormatError, match='checkbox mapping'):
        live_compile_plan(replay_source_records(raw, {name: payload}), 11)


@lru_cache(maxsize=1)
def _frozen_live_call_witness():
    """Actual CSV declarations and independently captured native graph stages."""
    from tools.offline_inputs import private_path
    from gxw.active_context import read_active_labels_csv
    root = private_path('research/experiments/native-live-session-20261018')
    initial = root/'036-active-fb-call-insert'
    before = json.loads((initial/'before.json').read_text('utf-8'))
    command = json.loads((initial/'command.json').read_text('utf-8'))

    def tables(folder):
        pairs = [(table, (folder/f'labels-{index:03d}'/'before.csv').read_bytes())
                 for index, table in enumerate(before['tables'], 1)]
        def parse(table, raw, types=None):
            binding = table['source_binding']
            return read_active_labels_csv(raw, owner=table['owner'], scope=table['owner_kind'],
                identity=before['identity'], document_handle=binding['document_handle'],
                document_title=binding['document_title'], type_sources=types)
        first = [parse(table, raw) for table, raw in pairs]
        types = [table for table in first if table['owner_kind'] in ('structure', 'fb')]
        return [parse(table, raw, types) for table, raw in pairs]
    before['tables'] = tables(initial)
    after = deepcopy(before)
    final = root/'042-remaining-fb-output-bindings'
    after['tables'] = tables(final)
    after['graphs'] = [json.loads((final/'Next-after.json').read_text('utf-8'))]
    return (root/'baseline.gxw').read_bytes(), before, after, command


def live_call_witness():
    raw, before, after, command = _frozen_live_call_witness()
    return raw, deepcopy(before), deepcopy(after), deepcopy(command)


@lru_cache(maxsize=1)
def _frozen_l02_pending_native_call():
    import base64
    from tools.offline_inputs import private_path
    root = private_path('research/experiments/native-live-multitarget-20261018')
    saved = root/'s18-l02-en-partial-readback-01'
    files = (saved/'baseline.gxw',saved/'current-context.json',
             root/'s18-l02-en-mcp-02/005-program-instance-graph-request.json')
    if any(not path.is_file() for path in files):
        pytest.skip('private L02 native pre-placement checkpoint unavailable')
    def decode(value):
        if set(value)=={'kind','base64'} and value['kind']=='exact_bytes':
            return base64.b64decode(value['base64'],validate=True)
        return value
    context = json.loads(files[1].read_text(encoding='utf-8'),object_hook=decode)
    command = json.loads(files[2].read_text(encoding='utf-8'))['arguments']['plan']['commands'][-1]
    return files[0].read_bytes(),context,command


@pytest.mark.parametrize('device_profile', ['L02','L02-P','L02/L02-P'])
def test_native_l02_call_plan_keeps_saved_cpu_and_resolves_merged_menu_identity(device_profile):
    from gxw.live_fbd_call import prepare_active_fbd_call
    raw,frozen_context,frozen_command = _frozen_l02_pending_native_call()
    context,command = deepcopy(frozen_context),deepcopy(frozen_command)
    plan = prepare_active_fbd_call(raw,context,command,device_profile=device_profile)
    assert plan['plan']['cpu']=='L02' and context['identity']['cpu_menu']=='L02/L02-P'
    assert plan['plan']['instance']=='PilotInst' and plan['plan']['definition']=='PilotGate'
    assert [row['name'] for row in plan['plan']['interface']['inputs']]==['Enable','Value']
    assert [row['name'] for row in plan['plan']['interface']['outputs']]==['Q','NextValue']
    assert plan['owner']=='POU_01' and plan['network']==0
    assert context==frozen_context and command==frozen_command


@pytest.mark.parametrize('field,value', [('cpu_menu','L06/L06-P'),('cpu_menu','UNKNOWN'),
    ('device_profile','L06/L06-P'),('device_profile','Q03UDV'),('device_profile','UNKNOWN')])
def test_native_l02_call_plan_refuses_foreign_menu_or_unrecognized_cpu(field,value):
    from gxw.live_fbd_call import prepare_active_fbd_call
    raw,frozen_context,command = _frozen_l02_pending_native_call()
    context,device_profile = deepcopy(frozen_context),'L02/L02-P'
    if field=='cpu_menu':context['identity'][field]=value
    else:device_profile=value
    # A changed root identity can be rejected even earlier by declaration
    # ownership; either boundary must prevent planning a native placement.
    with pytest.raises(GXWFormatError):
        prepare_active_fbd_call(raw,context,deepcopy(command),device_profile=device_profile)


def test_live_fb_call_verifies_the_frozen_native_insert_and_four_actual_argument_bindings():
    from gxw.live_fbd_call import prepare_active_fbd_call, verify_active_fbd_call
    raw, before, after, command = live_call_witness()
    assert before['tables'][2]['declarations'][0]['kind'] == 'function_block'
    before_copy = deepcopy(before)
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    assert before == before_copy
    assert prepared['kind'] == 'active_fbd_call'
    assert prepared['plan']['owner'] == 'POU_01' and prepared['plan']['network'] == 0
    assert not {'model', 'saved_project', 'source_bytes'} & prepared.keys()
    receipt = verify_active_fbd_call(before, after, command, prepared)
    assert receipt['status'] == 'matched'
    assert receipt['save_performed'] is False and receipt['compilation'] == 'not_run'
    assert receipt['graph_preservation'] == 'complete_old_graph_and_port_nets'
    assert [(row['formal'], row['symbol'], row['side'], row['data_type']) for row in receipt['connections']] == [
        ('Enable','Live_Enable','in','BOOL'), ('Value','Live_Value','in','INT'),
        ('Q','Probe_B002','out','BOOL'), ('Next','Live_Result','out','INT')]
    assert receipt['connections'][3]['native_name'] == "@'Next'"
    assert len({row['terminal'] for row in receipt['connections']}) == 4
    # The native editor used coincident FB/terminal ports, no new wire at all.
    assert len(before['graphs'][0]['networks'][0]['wires']) == len(after['graphs'][0]['networks'][0]['wires']) == 1


@pytest.mark.parametrize('caller', ['POU_01', 'POU_01.Program.pou', 'POU_01.Body.pou'])
def test_live_fb_call_reuses_current_native_caller_alias_resolution(caller):
    from gxw.live_fbd_call import prepare_active_fbd_call, verify_active_fbd_call
    raw, before, after, command = live_call_witness()
    command['caller'] = caller
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    assert verify_active_fbd_call(before, after, command, prepared)['owner'] == 'POU_01'


@pytest.mark.parametrize('stage', ['036-active-fb-call-insert/inserted.json',
    '039-active-fb-arguments-target-page/Enable-after.json', '041-reconcile-existing-value-chooser/after.json',
    '042-remaining-fb-output-bindings/Q-after.json'])
def test_live_fb_call_does_not_accept_partial_native_placement_or_parameter_entry(stage):
    from tools.offline_inputs import private_path
    from gxw.live_fbd_call import prepare_active_fbd_call, verify_active_fbd_call
    raw, before, after, command = live_call_witness()
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    after['graphs'] = [json.loads(private_path('research/experiments/native-live-session-20261018',).joinpath(stage).read_text('utf-8'))]
    with pytest.raises(GXWFormatError):
        verify_active_fbd_call(before, after, command, prepared)


@pytest.mark.parametrize('damage', ['instance_missing', 'instance_type', 'argument_missing', 'argument_extra',
    'argument_type', 'caller_missing', 'caller_ambiguous', 'active_network', 'ports_unbuilt', 'fixpoint', 'profile', 'already_called'])
def test_live_fb_call_rejects_unresolved_current_preconditions_before_any_native_work(damage):
    from gxw.live_fbd_call import prepare_active_fbd_call
    raw, before, after, command = live_call_witness()
    profile = 'FX3U'
    if damage == 'instance_missing':
        command['instance'] = 'Missing_Instance'
    elif damage == 'instance_type':
        before['tables'][2]['declarations'][0]['data_type'] = 'Another_FB'
    elif damage == 'argument_missing':
        command['arguments'].pop('Value')
    elif damage == 'argument_extra':
        command['arguments']['Unknown'] = 'Live_Value'
    elif damage == 'argument_type':
        command['arguments']['Value'] = 'Live_Enable'
    elif damage == 'caller_missing':
        command['caller'] = 'Missing.Program.pou'
    elif damage == 'caller_ambiguous':
        before['graphs'].append(deepcopy(before['graphs'][0]))
    elif damage == 'active_network':
        extra = deepcopy(before['graphs'][0]['networks'][0])
        extra.update(index=1, nodes=[], wires=[])
        before['graphs'][0]['networks'].append(extra)
        before['graphs'][0]['active_network'] = 1
    elif damage == 'ports_unbuilt':
        before['graphs'][0]['networks'][0]['nodes'][0]['connections'].update(status='not_built', ports=[])
    elif damage == 'fixpoint':
        before['graphs'][0]['networks'][0]['junctions'] = [dict(index=0, record='unparsed-fixpoint')]
    elif damage == 'profile':
        profile = 'Q03UDV'
    else:
        before['graphs'] = after['graphs']
    with pytest.raises(GXWFormatError):
        prepare_active_fbd_call(raw, before, command, device_profile=profile)


@pytest.mark.parametrize('damage', ['old_name', 'old_position', 'old_port', 'old_wire', 'old_wire_subdivision',
    'missing_terminal', 'wrong_symbol', 'missing_call', 'extra_call', 'native_formal_name', 'native_mask',
    'unknown_mask', 'native_direction', 'negated', 'extra_node', 'extra_wire', 'old_net_branch',
    'declarations', 'st', 'configuration', 'tasks', 'identity', 'changed_request'])
def test_live_fb_call_refuses_unrequested_sources_or_an_incomplete_actual_net_contract(damage):
    from gxw.live_fbd_call import prepare_active_fbd_call, verify_active_fbd_call
    raw, before, after, command = live_call_witness()
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    network = after['graphs'][0]['networks'][0]
    if damage == 'old_name':
        network['nodes'][1]['name'] = 'Probe_B002'
    elif damage == 'old_position':
        network['nodes'][1]['bounds'] = [4,2,6,4]
    elif damage == 'old_port':
        network['nodes'][1]['connections']['ports'][0]['flags'] |= 8
    elif damage == 'old_wire':
        network['wires'] = []
    elif damage == 'old_wire_subdivision':
        network['wires'][0]['end'] = [11,3]
        network['wires'].append(dict(index=1,start=[11,3],end=[18,3]))
    elif damage == 'missing_terminal':
        network['nodes'].pop(6)
    elif damage == 'wrong_symbol':
        network['nodes'][4]['name'] = 'Probe_A001'
    elif damage == 'missing_call':
        network['nodes'].pop(0)
    elif damage in ('extra_call','extra_node'):
        extra = deepcopy(network['nodes'][0 if damage == 'extra_call' else 4])
        extra.update(index=len(network['nodes']),address=0x78000000)
        extra['bounds'] = [v+10 for v in extra['bounds']]
        network['nodes'].append(extra)
    elif damage == 'native_formal_name':
        network['nodes'][0]['connections']['ports'][3]['name'] = 'StaleNext'
    elif damage == 'native_mask':
        network['nodes'][0]['connections']['ports'][3]['native_type_mask'] = 1
    elif damage == 'unknown_mask':
        network['nodes'][0]['connections']['ports'][3]['native_type_mask'] = 0
    elif damage == 'native_direction':
        network['nodes'][0]['connections']['ports'][0]['flags'] = 0
    elif damage == 'negated':
        network['nodes'][0]['connections']['ports'][0]['flags'] |= 8
    elif damage in ('extra_wire','old_net_branch'):
        start,end = ([50,10],[55,10]) if damage == 'extra_wire' else ([10,3],[10,6])
        network['wires'].append(dict(index=1,start=start,end=end))
    elif damage == 'declarations':
        after['tables'][0]['declarations'][0]['raw_fields']['注释'] = 'unrequested edit'
    elif damage == 'st':
        after['st'][0]['st_text'] += '\r\nProgramCount := 0;'
    elif damage == 'configuration':
        after['configuration']['ranges']['D'][0] += 1
    elif damage == 'tasks':
        after['tasks']['tasks'][0]['programs'].append(dict(name='Live_ST_FB',line=99))
    elif damage == 'identity':
        after['identity']['process_id'] += 1
    else:
        command['arguments']['Q'] = 'Probe_A001'
    for index,node in enumerate(network['nodes']):
        node['index'] = index
    with pytest.raises(GXWFormatError):
        verify_active_fbd_call(before, after, command, prepared)


@given(dx=st.integers(1,20), dy=st.integers(0,12), wider=st.integers(0,8), reverse=st.booleans())
@settings(max_examples=32, deadline=None)
def test_live_fb_call_contract_uses_actual_topology_instead_of_fixed_native_placement(dx,dy,wider,reverse):
    from gxw.live_fbd_call import prepare_active_fbd_call, verify_active_fbd_call
    raw, before, after, command = live_call_witness()
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    network = after['graphs'][0]['networks'][0]
    for node in network['nodes']:
        if node['index'] in (1,2):
            continue
        node['bounds'] = [v+(dx if i%2 == 0 else dy) for i,v in enumerate(node['bounds'])]
        if node['index'] in (4,6):
            node['bounds'][0] += wider
            node['bounds'][2] += wider
    call = network['nodes'][0]
    call['bounds'][2] += wider
    for port in call['connections']['ports']:
        if not port['flags'] & 1:
            port['local_x'] += wider
    if reverse:
        network['nodes'].reverse()
    for index,node in enumerate(network['nodes']):
        node.update(index=index,address=0x60000000+200*index)
    receipt = verify_active_fbd_call(before, after, command, prepared)
    assert receipt['status'] == 'matched' and len(receipt['connections']) == 4


@pytest.mark.parametrize(('stage', 'observed_formals'), [
    ('036-active-fb-call-insert/inserted.json', []),
    ('039-active-fb-arguments-target-page/Enable-after.json', ['Enable']),
    ('041-reconcile-existing-value-chooser/after.json', ['Enable', 'Value']),
    ('042-remaining-fb-output-bindings/Q-after.json', ['Enable', 'Value', 'Q']),
    ('042-remaining-fb-output-bindings/Next-after.json', ['Enable', 'Value', 'Q', 'Next'])])
def test_live_fb_call_binding_steps_resolve_actual_stage_nets_and_only_exact_placeholders(stage, observed_formals):
    from tools.offline_inputs import private_path
    from gxw.live_context import active_callable_sources
    from gxw.live_fbd_call import active_fbd_call_binding_steps, prepare_active_fbd_call
    raw, before, current, command = live_call_witness()
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    current['graphs'] = [json.loads(private_path('research/experiments/native-live-session-20261018').joinpath(stage).read_text('utf-8'))]
    unchanged = deepcopy(current)
    steps = active_fbd_call_binding_steps(current, prepared)
    assert current == unchanged
    assert [row['formal'] for row in steps if row['status'] == 'observed'] == observed_formals
    assert [(row['formal'], row['symbol'], row['side'], row['data_type']) for row in steps] == [
        ('Enable', 'Live_Enable', 'in', 'BOOL'), ('Value', 'Live_Value', 'in', 'INT'),
        ('Q', 'Probe_B002', 'out', 'BOOL'), ('Next', 'Live_Result', 'out', 'INT')]
    for step in steps:
        assert set(step) == {'formal', 'symbol', 'side', 'data_type', 'status', 'target'}
        assert set(step['target']) == {'owner', 'owner_kind', 'semantic'}
        target = resolve_graph_target(current['graphs'][0], step['target'])
        actual = current['graphs'][0]['networks'][0]['nodes'][target['index']]
        assert actual['name'] == (step['symbol'] if step['status'] == 'observed' else '?')
        assert actual['source_kind_code'] == (13 if step['side'] == 'in' else 14)
    pending = next((step for step in steps if step['status'] == 'pending'), None)
    if pending is not None:
        sources = active_callable_sources(raw, current['tables'], owner='POU_01', kind='program', identity=current['identity'])
        action = {'action': 'rebind_terminal', 'network': 0, 'node': pending['target'], 'symbol': pending['symbol']}
        binding = prepare_active_graph_action(current['graphs'][0], action, sources=sources, device_profile='FX3U')
        changed = deepcopy(current)
        index = resolve_graph_target(changed['graphs'][0], pending['target'])['index']
        changed['graphs'][0]['networks'][0]['nodes'][index]['name'] = pending['symbol']
        assert verify_active_graph_action(current['graphs'][0], changed['graphs'][0], binding, sources=sources)['status'] == 'verified'
        reread_steps = active_fbd_call_binding_steps(changed, prepared)
        assert [row['formal'] for row in reread_steps if row['status'] == 'observed'] == observed_formals + [pending['formal']]


@pytest.mark.parametrize('damage', ['other_symbol', 'whitespace_placeholder', 'empty_placeholder',
    'old_node', 'old_port', 'old_wire', 'old_net_branch', 'extra_terminal', 'missing_terminal',
    'joined_formals', 'mask', 'interface_name', 'foreign_network', 'source', 'configuration'])
def test_live_fb_call_binding_steps_refuse_unsafe_partial_graphs(damage):
    from tools.offline_inputs import private_path
    from gxw.live_fbd_call import active_fbd_call_binding_steps, prepare_active_fbd_call
    raw, before, current, command = live_call_witness()
    prepared = prepare_active_fbd_call(raw, before, command, device_profile='FX3U')
    current['graphs'] = [json.loads(private_path('research/experiments/native-live-session-20261018/036-active-fb-call-insert/inserted.json').read_text('utf-8'))]
    network = current['graphs'][0]['networks'][0]
    if damage in ('other_symbol', 'whitespace_placeholder', 'empty_placeholder'):
        network['nodes'][3]['name'] = {'other_symbol': 'Probe_A001', 'whitespace_placeholder': ' ? ', 'empty_placeholder': ''}[damage]
    elif damage == 'old_node':
        network['nodes'][1]['name'] = 'Probe_B002'
    elif damage == 'old_port':
        network['nodes'][1]['connections']['ports'][0]['flags'] |= 8
    elif damage == 'old_wire':
        network['wires'][0]['end'] = [17, 3]
    elif damage == 'old_net_branch':
        network['wires'].append(dict(index=1, start=[10, 3], end=[10, 6]))
    elif damage == 'extra_terminal':
        extra = deepcopy(network['nodes'][3])
        extra.update(index=7, address=0x78900000)
        network['nodes'].append(extra)
    elif damage == 'missing_terminal':
        network['nodes'].pop(6)
    elif damage == 'joined_formals':
        network['wires'].append(dict(index=1, start=[30, 3], end=[30, 4]))
    elif damage == 'mask':
        network['nodes'][0]['connections']['ports'][0]['native_type_mask'] = 0
    elif damage == 'interface_name':
        network['nodes'][0]['connections']['ports'][3]['name'] = 'OldNext'
    elif damage == 'foreign_network':
        current['graphs'][0]['networks'].append(dict(index=1, nodes=[], wires=[], junctions=[]))
        current['graphs'][0]['active_network'] = 1
    elif damage == 'source':
        current['st'][0]['st_text'] += '\r\nProgramCount := 1;'
    else:
        current['configuration']['ranges']['D'][0] += 1
    with pytest.raises(GXWFormatError):
        active_fbd_call_binding_steps(current, prepared)


@lru_cache(maxsize=1)
def _frozen_live_input_witness():
    """062's independently captured single click, native stages and real CSV."""
    from tools.offline_inputs import private_path
    from gxw.active_context import read_active_labels_csv
    from gxw.live_context import active_callable_sources
    folder = private_path('research/experiments/native-live-session-20261018/062-single-input-after-cleared-instance')
    context = json.loads((folder/'context.json').read_text('utf-8'))
    result = json.loads((folder/'result.json').read_text('utf-8'))
    from pathlib import Path
    baseline = Path(result['disk']['baseline']).read_bytes()
    pairs = [(table, (folder/f'labels-{index:03d}/before.csv').read_bytes())
             for index, table in enumerate(context['tables'], 1)]
    def parse(table, raw, types=None):
        binding = table['source_binding']
        return read_active_labels_csv(raw, owner=table['owner'], scope=table['owner_kind'],
            identity=context['identity'], document_handle=binding['document_handle'],
            document_title=binding['document_title'], type_sources=types)
    types = [table for table in (parse(table, raw) for table, raw in pairs) if table['owner_kind'] in ('fb', 'structure')]
    tables = [parse(table, raw, types) for table, raw in pairs]
    sources = active_callable_sources(baseline, tables, owner='POU_01', identity=context['identity'])
    return ({name: json.loads((folder/f'{name}.json').read_text('utf-8'))
             for name in ('before', 'placed', 'idle', 'after', 'placement')}, sources)


def live_input_witness():
    values, sources = _frozen_live_input_witness()
    return deepcopy(values), deepcopy(sources)


def input_action(**changes):
    return dict(action='add_terminal', network=0, template='input', symbol='Live_Enable', x=3, y=7) | changes


def test_add_terminal_uses_actual_062_oracle_and_never_fabricates_native_records():
    from gxw.active_graph import active_graph_terminal_target, validate_add_terminal_action
    values, sources = live_input_witness()
    before = deepcopy(values['before'])
    action = input_action()
    assert validate_add_terminal_action(action) is None
    prepared = prepare_active_graph_action(before, action, sources=sources, device_profile='FX3U')
    assert before == values['before']
    assert prepared['action'] == action and prepared['expected_data_type'] == 'BOOL'
    assert prepared['expected_node'] == dict(network=0, template='input', symbol='Live_Enable',
        x=3,y=7,width=2,height=2,ports=[dict(name='OUT',x=2,y=1,side='out',data_type='BOOL')])
    serialized = json.dumps(prepared)
    assert all('"'+field+'"' not in serialized for field in ('address','record','raw','source_bytes','expected_state'))
    for stage in ('placed','idle'):
        target = active_graph_terminal_target(values[stage], prepared, placeholder=True)
        assert target['semantic']['symbol'] == '?'
        assert resolve_graph_target(values[stage], target)['index'] == 7
        assert verify_active_graph_action(before, values[stage], prepared, sources=sources)['status'] == 'unconfirmed'
    assert verify_active_graph_action(before, values['after'], prepared, sources=sources)['status'] == 'verified'
    # A plan's semantic-only fields also survive an internal JSON checkpoint.
    assert verify_active_graph_action(before, values['after'], json.loads(serialized), sources=sources)['status'] == 'verified'


@pytest.mark.parametrize('change', [{'template':'output'}, {'symbol':'?'}, {'symbol':'Live_Enable '},
    {'symbol':'1Bad'}, {'symbol':'X.Y'}, {'symbol':'BOOL#TRUE'}, {'symbol':''}, {'symbol':'A'*256},
    {'x':True}, {'x':-1}, {'y':4095}, {'network':False}, {'network':512}, {'unexpected':0}])
def test_add_terminal_syntax_is_shared_and_does_not_resolve_sources(change):
    from gxw.active_graph import validate_add_terminal_action
    action = input_action()
    action.update(change)
    with pytest.raises(GXWFormatError):
        validate_add_terminal_action(action)


@pytest.mark.parametrize('damage', ['missing_label','raw_device','literal','non_bool','wrong_sources',
    'other_network','network_one','node_overlap','port_touch','wire_touch','wire_edge','unbuilt_ports','other_fixpoint'])
def test_add_terminal_preflight_rejects_unresolved_labels_or_occupied_current_geometry(damage):
    values,sources = live_input_witness()
    before,action = values['before'],input_action()
    if damage == 'missing_label':
        action['symbol'] = 'Missing_Label'
    elif damage == 'raw_device':
        action['symbol'] = 'M50'
    elif damage == 'literal':
        action['symbol'] = 'TRUE'
    elif damage == 'non_bool':
        action['symbol'] = 'Live_Value'
    elif damage == 'wrong_sources':
        sources.program_name = 'Other.Program.pou'
    elif damage in ('other_network','network_one'):
        before['networks'].append(dict(index=1,nodes=[],wires=[],junctions=[]))
        before['active_network'] = 1
        if damage == 'network_one':
            action['network'] = 1
    elif damage == 'node_overlap':
        action.update(x=3,y=2)
    elif damage == 'port_touch':
        action.update(x=5,y=2)
    elif damage in ('wire_touch','wire_edge'):
        action.update(x=7,y=2 if damage=='wire_touch' else 3)
    elif damage == 'unbuilt_ports':
        before['networks'][0]['nodes'][0]['connections'].update(status='not_built',ports=[])
    else:
        before['networks'].append(dict(index=1,nodes=[],wires=[],junctions=[dict(index=0,record='unknown')]))
    with pytest.raises(GXWFormatError):
        prepare_active_graph_action(before, action, sources=sources, device_profile='FX3U')


@pytest.mark.parametrize('damage', ['wrong_symbol','placeholder','position','kind','direction','negation','native_mask',
    'missing_connections','extra_node','extra_wire','old_node','old_port','old_wire','old_subdivision',
    'old_net_connection','current_type','inactive','binding'])
def test_add_terminal_verification_requires_only_one_actual_independent_input_and_complete_old_graph(damage):
    values,sources = live_input_witness()
    before,after = values['before'],values['after']
    prepared = prepare_active_graph_action(before, input_action(), sources=sources, device_profile='FX3U')
    network = after['networks'][0]
    node = network['nodes'][7]
    if damage in ('wrong_symbol','placeholder'):
        node['name'] = 'Probe_A001' if damage=='wrong_symbol' else '?'
    elif damage == 'position':
        node['bounds'] = [4,7,6,9]
    elif damage == 'kind':
        node['source_kind_code'] = 14
    elif damage == 'direction':
        node['connections']['ports'][0]['flags'] = 3
    elif damage == 'negation':
        node['connections']['ports'][0]['flags'] |= 8
    elif damage == 'native_mask':
        node['connections']['ports'][0]['native_type_mask'] = 4
    elif damage == 'missing_connections':
        node['connections'].update(status='not_built',ports=[])
    elif damage == 'extra_node':
        extra=deepcopy(node)
        extra.update(index=8,address=0x78000000,bounds=[8,7,10,9])
        network['nodes'].append(extra)
    elif damage == 'extra_wire':
        network['wires'].append(dict(index=1,start=[7,12],end=[12,12]))
    elif damage == 'old_node':
        network['nodes'][1]['name'] = 'Probe_B002'
    elif damage == 'old_port':
        network['nodes'][0]['connections']['ports'][0]['name'] = 'StaleEnable'
    elif damage == 'old_wire':
        network['wires'] = []
    elif damage == 'old_subdivision':
        network['wires'][0]['end'] = [11,3]
        network['wires'].append(dict(index=1,start=[11,3],end=[18,3]))
    elif damage == 'old_net_connection':
        network['wires'].append(dict(index=1,start=[5,8],end=[5,3]))
    elif damage == 'current_type':
        table,label_row=sources.label('Live_Enable')
        declaration=sources.declarations[table]
        sources=sources.with_declarations({**sources.declarations,
            table:replace(declaration,rows=tuple(replace(row,data_type='INT',type_code=4) if row==label_row else row
                                                for row in declaration.rows))})
    elif damage == 'inactive':
        after['networks'].append(dict(index=1,nodes=[],wires=[],junctions=[]))
        after['active_network']=1
    else:
        after['identity']['process_id'] += 1
    assert verify_active_graph_action(before,after,prepared,sources=sources)['status'] == 'unconfirmed'


@given(x=st.integers(0,70), y=st.integers(10,60), reverse=st.booleans(), mask=st.sampled_from([0,1]))
@settings(max_examples=40,deadline=None)
def test_add_terminal_independent_net_survives_safe_geometry_and_native_reconstruction(x,y,reverse,mask):
    values,sources = live_input_witness()
    before,after = values['before'],values['after']
    action=input_action();action.update(x=x,y=y)
    prepared=prepare_active_graph_action(before,action,sources=sources,device_profile='FX3U')
    added=after['networks'][0]['nodes'][7]
    added['bounds']=[x,y,x+2,y+2]
    added['connections']['ports'][0]['native_type_mask']=mask
    if reverse:
        after['networks'][0]['nodes'].reverse()
        for wire in after['networks'][0]['wires']:
            wire['start'],wire['end']=wire['end'],wire['start']
    for index,node in enumerate(after['networks'][0]['nodes']):
        node.update(index=index,address=0x71000000+index*120)
    assert verify_active_graph_action(before,after,prepared,sources=sources)['status']=='verified'

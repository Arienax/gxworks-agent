"""Offline SFC source transformations against frozen native graph controls."""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
import json
import struct
import zipfile

from hypothesis import given, settings, strategies as st
import pytest

from gxw.models import GXWFormatError
from gxw.sfc_offline import decode_sfc_graph, edit_sfc_source, encode_sfc_symbols, edit_sfc_program, edit_sfc_children
from gxw.sfc_pou import (SfcProjectContext, parse_sfc_pou, read_sfc_project_context,
                         sfc_components, serialize_sfc_components)
from tools.offline_inputs import private_path


@lru_cache(maxsize=3)
def _fixture(name):
    return json.loads(private_path('tests/fixtures/' + name).read_text(encoding='utf-8'))


def _context(raw, cpu):
    return SfcProjectContext(b'', 'Block.Program.pou', ('Block.Program.pou',),
        parse_sfc_pou(raw), {'cpu': cpu, 'codepage': 936, 'text_encoding': 'cp936'}, {}, ())


def _region(raw, part='graph'):
    source = parse_sfc_pou(raw)
    graph = source.layout['graph']
    if part == 'code':
        return raw[graph['code_offset']:graph['code_offset'] + graph['code_size']]
    if part == 'cache':
        return raw[graph['cache_offset']:graph['cache_offset'] + graph['cache_size']]
    return raw[graph['offset']:graph['offset'] + graph['size']]


def _assert_retained(before, after):
    old = parse_sfc_pou(before).layout['graph']
    new = parse_sfc_pou(after).layout['graph']
    assert after[:new['offset'] - 4] == before[:old['offset'] - 4]
    assert after[new['offset'] + new['size']:] == before[old['offset'] + old['size']:]
    assert _region(after, 'cache') == _region(before, 'cache')


@pytest.mark.parametrize('case_index', [0, 1])
def test_nonzero_action_qualifier_matches_original_setter_save_and_cold_read(case_index):
    from gxw.source_header import source_payload_offset
    from integrations.web.schemas import SFCChildEdit
    case = _fixture('gxw_sfc_qualifiers_native.json')['cases'][case_index]
    before, native = (base64.b64decode(case[key]) for key in ('before_source_base64', 'after_source_base64'))
    source = parse_sfc_pou(before)
    action = source.layout['actions'][0]
    registrations = deepcopy(action['registrations'])
    registrations[0]['qualifier'] = 1
    command = dict(kind='action', name=action['name'], registrations=registrations)
    edit = SFCChildEdit.model_validate(command).model_dump(exclude_none=True)
    updated, _ = edit_sfc_children(_context(before, case['cpu']), [edit])
    assert updated[:source_payload_offset(before)] == before[:source_payload_offset(before)]
    assert updated[source_payload_offset(updated):] == native[source_payload_offset(native):]
    observed = [row for row in case['cold'] if row['operation'] == 'Research.Qualifier']
    assert observed[0]['value'] == 1
    assert case['native_check']['compile_status'] == case['native_check']['check_status'] == 'completed_accepted'
    assert parse_sfc_pou(updated).diagnostics == ()


@settings(max_examples=30, deadline=None)
@given(value=st.integers(min_value=0, max_value=0xffffffff))
def test_qualifier_storage_uses_independently_observed_unsigned_word_without_altering_registration(value):
    from gxw.source_header import source_payload_offset
    case = _fixture('gxw_sfc_qualifiers_native.json')['cases'][0]
    before, native = (base64.b64decode(case[key]) for key in ('before_source_base64', 'after_source_base64'))
    # The independent setter/save changes exactly the qualifier's lowest byte
    # outside the common save-status header. It identifies this field's offset.
    changed = [index for index in range(source_payload_offset(before), len(before)) if before[index] != native[index]]
    assert len(before) == len(native) and len(changed) == 1
    offset = changed[0]
    source = parse_sfc_pou(before)
    action = source.layout['actions'][0]
    registrations = deepcopy(action['registrations'])
    registrations[0]['qualifier'] = value
    actual, _ = edit_sfc_children(_context(before, case['cpu']), [
        dict(kind='action', name=action['name'], registrations=registrations)])
    assert actual == before[:offset] + struct.pack('<I', value) + before[offset + 4:]
    assert bool(parse_sfc_pou(actual).diagnostics) is (value not in (0, 1))


def test_internal_child_move_preserves_bodies_action_links_and_other_record_bytes():
    raw = _authoring('fx3u', 'original.gxw')
    context = read_sfc_project_context(raw)
    children, actions = sfc_components(context.source)
    zooms = [row for row in children if row['kind'] == 'zoom']
    assert len(zooms) >= 2
    actual, _ = edit_sfc_children(context, [dict(kind='zoom', name=zooms[1]['name'],
        operation='move_before', target_name=zooms[0]['name'])])
    saved_children, saved_actions = sfc_components(parse_sfc_pou(actual))
    assert [row['name'] for row in saved_children if row['kind'] == 'zoom'][:2] == [zooms[1]['name'], zooms[0]['name']]
    assert {row['name']: row['body'] for row in saved_children} == {row['name']: row['body'] for row in children}
    assert saved_actions == actions
    saved = parse_sfc_pou(actual)
    assert _region(context.source.raw) == _region(actual)
    assert context.source.raw[context.source.layout['tail_offset']:] == actual[saved.layout['tail_offset']:]


@pytest.mark.parametrize('source_bound', [False, True])
def test_fx_sfc_child_keeps_a_missing_timer_preset_as_an_explicit_draft(tmp_path, source_bound):
    from application.sfc import prepare_candidate, read_snapshot
    raw = _authoring('fx3u', 'original.gxw')
    context = read_sfc_project_context(raw)
    child = next(row for row in context.object_model()['children'] if row['kind'] == 'zoom')
    edit = dict(kind='zoom', name=child['name'])
    if source_bound:
        seed = prepare_candidate(tmp_path / 'seed', baseline=raw, child_edits=[dict(kind='zoom',
            name=child['name'], instructions=[dict(op='LD', args=['X0']), dict(op='OUT', args=['Y20'])])])
        raw = (tmp_path / 'seed' / seed['artifacts']['gxw']['path']).read_bytes()
        context = read_sfc_project_context(raw)
        child = next(row for row in context.object_model(token_profile='fx3u')['children']
                     if row['kind'] == 'zoom' and row['name'] == child['name'])
        index, record = next((index, row) for index, row in enumerate(child['records']) if row.get('op') == 'OUT')
        edit['instruction_edits'] = [dict(operation='replace', record_index=index,
            expected={key: record[key] for key in ('op', 'args')}, instructions=[dict(op='OUT', args=['T0'])])]
    else:
        edit['instructions'] = [dict(op='LD', args=['X0']), dict(op='OUT', args=['T0'])]
    with pytest.raises(GXWFormatError):
        prepare_candidate(tmp_path / 'checked', baseline=raw, child_edits=[edit])
    payload = prepare_candidate(tmp_path / 'draft', baseline=raw, child_edits=[edit], save_policy='draft')
    saved = (tmp_path / 'draft' / payload['artifacts']['gxw']['path']).read_bytes()
    current = next(row for row in read_snapshot(saved)['model']['children']
                   if row['name'] == child['name'] and row['kind'] == 'zoom')
    assert any(row.get('op') == 'OUT' and row.get('args') == ['T0'] for row in current['records'])
    checks = payload['metadata']['validation']['source_checks']
    assert any(finding['severity'] == 'error' for check in checks for finding in check.get('findings', []))


@pytest.mark.parametrize('case_index', range(10))
def test_fx_source_edits_match_all_frozen_native_cpu_controls(case_index):
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][case_index]
    before, native = (base64.b64decode(case[key]) for key in ('before_source_base64', 'after_source_base64'))
    context = _context(before, case['cpu'])
    source = context.source
    jump = [row for row in source.graph_records if row['kind'] == 'step-reference'
            and row['raw_hex'].startswith('0562020005') and row['references'] == [0]]
    assert len(jump) == 1
    result, report = edit_sfc_source(context, [
        {'step_reference': 1, 'action_number': 20}, {'step_reference': 2, 'action_number': 10},
        {'kind': 'jump', 'source_offset': jump[0]['offset'],
         'before_step_reference': 0, 'target_step_reference': 1}])
    assert _region(result) == _region(native)
    _assert_retained(before, result)
    assert report['native_calls'] == 0


def test_existing_jump_coordinates_use_cached_current_source_evidence():
    case = next(case for case in _fixture('gxw_sfc_fx_edits.json')['cases'] if case['cpu'] == 'FX3U/FX3UC')
    current = base64.b64decode(case['after_source_base64'])
    expected = base64.b64decode(case['before_source_base64'])
    context = _context(current, case['cpu'])
    result, _ = edit_sfc_source(context, [
        {'step_reference': 1, 'action_number': 10}, {'step_reference': 2, 'action_number': 20},
        {'kind': 'jump', 'x': 0, 'y': 6, 'before_step_reference': 1, 'target_step_reference': 0}],
        graph=case['graph'])
    assert _region(result) == _region(expected)
    _assert_retained(current, result)


def test_jump_offset_and_coordinates_must_agree_when_both_are_supplied():
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    current = base64.b64decode(case['after_source_base64'])
    context = _context(current, case['cpu'])
    other_successor = next(row for row in context.source.graph_records
                           if row['kind'] == 'step-reference' and row['references'] == [1])
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        edit_sfc_source(context, [{'kind': 'jump', 'x': 0, 'y': 6,
            'source_offset': other_successor['offset'], 'before_step_reference': 1,
            'target_step_reference': 0}], graph=case['graph'])


def test_source_offset_jump_cannot_retarget_an_ordinary_forward_edge():
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    context = _context(base64.b64decode(case['before_source_base64']), case['cpu'])
    forward = next(row for row in context.source.graph_records
                   if row['kind'] == 'step-reference' and row['references'] == [1])
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        edit_sfc_source(context, [{'kind':'jump','source_offset':forward['offset'],
            'before_step_reference':1,'target_step_reference':2}])


def test_ql_step_swap_matches_native_rebuild_without_native_geometry():
    case = _fixture('gxw_sfc_ql_rebuild.json')
    before, native = (base64.b64decode(case[key]) for key in ('before_source_base64', 'after_source_base64'))
    result, report = edit_sfc_source(_context(before, case['cpu']), [
        {'kind': 'swap_steps', 'left_step_reference': 1, 'right_step_reference': 2}])
    assert _region(result) == _region(native)
    _assert_retained(before, result)
    assert report['graph']['issues'] == [] and report['native_calls'] == 0


def test_same_geometry_rebuild_uses_source_reference_permutation():
    case = _fixture('gxw_sfc_ql_rebuild.json')
    current = base64.b64decode(case['after_source_base64'])
    expected = base64.b64decode(case['before_source_base64'])
    context = _context(current, case['cpu'])
    graph = {'status': 'source-bound', 'cpu': case['cpu'], 'native_version': case['native_version'],
             'logical_name': context.program_name, 'source_body_hex': _region(current).hex(),
             'after': {'width': 32, 'height': 96, 'cells': deepcopy(case['cells'])}}
    symbols = deepcopy(case['edit']['symbols'])
    for symbol in symbols:
        if symbol['kind'] == 'step' and symbol['number'] in (1, 2):
            symbol['number'] = 3 - symbol['number']
    result, _ = edit_sfc_source(context, [{'kind': 'rebuild', 'symbols': symbols}], graph=graph)
    assert _region(result) == _region(expected)
    _assert_retained(current, result)


@pytest.mark.parametrize('menu', ['L02S_L02S-P', 'Q00U'])
def test_linear_rebuild_matches_independent_native_full_symbol_writer(menu):
    archive = private_path('research/' + 'evidence/gxw-sfc-ql-rebuild-20261014.zip')
    with zipfile.ZipFile(archive) as frozen:
        before = frozen.read('linear-menu-' + menu + '/input.gxw')
        native = frozen.read('linear-menu-' + menu + '/candidate.gxw')
        edits = json.loads(frozen.read('linear-command.json'))
    context = read_sfc_project_context(before)
    expected = read_sfc_project_context(native)
    result, report = edit_sfc_source(context, edits)
    assert _region(result, 'code') == _region(expected.source.raw, 'code')
    _assert_retained(context.source.raw, result)
    assert report['graph']['linear_symbols'] == edits[0]['symbols']


def test_linear_source_projection_uses_actual_reference_order():
    case = _fixture('gxw_sfc_ql_binding.json')
    source = parse_sfc_pou(base64.b64decode(case['source_base64']))
    model = decode_sfc_graph(source, cpu=case['cpu'])
    assert [row['step_reference'] for row in model['steps']] == [0, 7]
    assert [(row['number'], [ref['step_reference'] for ref in row['outgoing']])
            for row in model['transitions']] == [(0, [7]), (1, [999])]
    assert model['position_resolution'] == 'core-linear-source-order'
    assert model['linear_symbols'][-1] == {'kind': 'end', 'x': 0, 'y': 6}


@settings(max_examples=20, deadline=None, derandomize=True)
@given(left=st.integers(min_value=1, max_value=5), delta=st.integers(min_value=1, max_value=4))
def test_ql_permutation_is_invertible_and_preserves_unedited_source(left, delta):
    case = _fixture('gxw_sfc_ql_rebuild.json')
    raw = base64.b64decode(case['before_source_base64'])
    right = (left - 1 + delta) % 5 + 1
    edit = [{'kind': 'swap_steps', 'left_step_reference': left, 'right_step_reference': right}]
    changed, _ = edit_sfc_source(_context(raw, case['cpu']), edit)
    restored, _ = edit_sfc_source(_context(changed, case['cpu']), edit)
    assert restored == raw
    _assert_retained(raw, changed)


@settings(max_examples=16, deadline=None, derandomize=True)
@given(opaque=st.binary(min_size=1, max_size=128))
def test_action_edits_preserve_unknown_cache_bytes(opaque):
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    raw = bytearray(base64.b64decode(case['before_source_base64']))
    start = parse_sfc_pou(raw).layout['graph']['cache_offset'] + 4
    raw[start:start + len(opaque)] = opaque
    before = bytes(raw)
    after, _ = edit_sfc_source(_context(before, case['cpu']), [{'step_reference': 1, 'action_number': 20}])
    _assert_retained(before, after)


@pytest.mark.parametrize('damage', ['missing_graph', 'stale_graph', 'wrong_cell', 'wrong_before', 'unknown_target'])
def test_jump_refuses_unbound_or_inconsistent_coordinates(damage):
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    context = _context(base64.b64decode(case['after_source_base64']), case['cpu'])
    graph = deepcopy(case['graph'])
    edit = {'kind': 'jump', 'x': 0, 'y': 6, 'before_step_reference': 1, 'target_step_reference': 0}
    if damage == 'missing_graph':
        graph = None
    elif damage == 'stale_graph':
        graph['source_body_hex'] = '00'
    elif damage == 'wrong_cell':
        edit['y'] = 3
    elif damage == 'wrong_before':
        edit['before_step_reference'] = 2
    else:
        edit['target_step_reference'] = 300
    with pytest.raises(GXWFormatError):
        edit_sfc_source(context, [edit], graph=graph)


@pytest.mark.parametrize('edit', [
    {'kind': 'swap_steps', 'left_step_reference': 0, 'right_step_reference': 1},
    {'kind': 'swap_steps', 'left_step_reference': 1, 'right_step_reference': 1},
    {'kind': 'swap_steps', 'left_step_reference': True, 'right_step_reference': 2},
    {'step_reference': 1, 'action_number': 999},
])
def test_invalid_or_out_of_scope_edits_return_no_changed_source(edit):
    case = _fixture('gxw_sfc_ql_rebuild.json')
    raw = base64.b64decode(case['before_source_base64'])
    context = _context(raw, case['cpu'])
    with pytest.raises(GXWFormatError):
        edit_sfc_source(context, [edit])
    assert context.source.raw == raw


def test_disconnected_branch_geometry_is_rejected_instead_of_reusing_old_code():
    case = _fixture('gxw_sfc_ql_rebuild.json')
    context = _context(base64.b64decode(case['before_source_base64']), case['cpu'])
    symbols = deepcopy(case['edit']['symbols'])
    symbols[2]['end'] += 1
    with pytest.raises(GXWFormatError, match='unconnected down port'):
        edit_sfc_source(context, [{'kind': 'rebuild', 'symbols': symbols}])


def test_new_ql_jump_matches_independent_original_grid_converter():
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-offline-authoring-20261021.zip')) as frozen:
        raw = frozen.read('ql-jump-v1/original.gxw')
        native = frozen.read('ql-jump-v1/native/sfc-encoded-tokens.bin')
        result = json.loads(frozen.read('ql-jump-v1/result.json'))
    assert result['check']['result'] == 0
    context = read_sfc_project_context(raw)
    symbols = []
    for index in range(2):
        symbols.extend([{'kind': 'initial_step' if not index else 'step', 'x': 0, 'y': index * 3, 'number': index},
                        {'kind': 'transition', 'x': 0, 'y': index * 3 + 1, 'number': index},
                        {'kind': 'line', 'x': 0, 'start': index * 3 + 2, 'end': index * 3 + 2}])
    symbols.append({'kind': 'jump', 'x': 0, 'y': 6, 'number': 0})
    assert encode_sfc_symbols(context.source, symbols, cpu=context.project['cpu']) == native


def _authoring(case, entry):
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-offline-authoring-20261021.zip')) as frozen:
        return frozen.read('authoring-' + case + '-v1/' + entry)


def _component_snapshot(source):
    children, actions = sfc_components(source)
    return {'code': _region(source.raw, 'code'),
        'children': sorted([(r['kind'], r['name'], r['number'], r['body'][20:]) for r in children]),
        'actions': sorted([(r['name'], r['number'], r['registrations']) for r in actions])}


@pytest.mark.parametrize('case', ['fx3u', 'q00j', 'l02'])
def test_graph_add_delete_children_and_cache_invalidation_match_native_saved_controls(case):
    context = read_sfc_project_context(_authoring(case, 'original.gxw'))
    edits = json.loads(_authoring(case, 'edits.json'))
    result, report = edit_sfc_program(context, graph_edits=edits['graph_edits'], child_edits=edits['child_edits'])
    native = read_sfc_project_context(_authoring(case, 'compile-save/candidate.gxw'))
    assert _component_snapshot(parse_sfc_pou(result)) == _component_snapshot(native.source)
    assert _region(result, 'cache') == struct.pack('<I', 4)
    assert report['native_calls'] == 0 and report['cache'] == 'invalidated-to-native-minimal-form'
    graph = report['graph']
    assert [row['step_reference'] for row in graph['steps']] == [0, 80, 1]
    assert [row['number'] for row in graph['transitions']] == [0, 80, 1]
    assert graph['issues'] == [] and graph['edges'][-1]['kind'] == 'jump'
    receipt = json.loads(_authoring(case, 'result.json'))
    assert receipt['compile_save']['compilation']['acceptance'] == 'accepted'
    assert receipt['compile_save']['public_check']['status'] == receipt['cold']['public_check']['status'] == 'passed'
    assert receipt['source_snapshot_exact'] is True


@pytest.mark.parametrize('cpu', ['fx3u', 'q00j'])
def test_native_internal_default_bodies_and_registration_frames_are_independent_controls(cpu):
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-offline-authoring-20261021.zip')) as frozen:
        prefix = 'objects-' + cpu + '-v2/save/'
        zoom = frozen.read(prefix + 'native-default-zoom-80.bin')
        transition = frozen.read(prefix + 'native-default-transition-80.bin')
        context = read_sfc_project_context(frozen.read(prefix + 'candidate.gxw'))
    assert zoom == transition == struct.pack('<IIIII', 20, 1, 12, 0, 0xffffffff)
    children, actions = sfc_components(context.source)
    assert serialize_sfc_components(context.source, children=children, actions=actions) == context.source.raw
    action = next(row for row in actions if row['number'] == 80)
    assert action['registrations'] == [{'qualifier': 0, 'zoom': 'sfc_research_zoom_80'}]


@settings(max_examples=25, deadline=None)
@given(number=st.integers(min_value=256, max_value=32760), role=st.sampled_from(['steps', 'actions']))
def test_joint_renumber_updates_actual_graph_links_and_registration_numbers(number, role):
    context = read_sfc_project_context(_authoring('q00j', 'original.gxw'))
    first = next(row for row in decode_sfc_graph(context.source, cpu=context.project['cpu'])['steps'] if not row['initial'])['step_reference']
    condition = next(row['number'] for row in context.source.layout['children'] if row['kind'] == 'transition')
    before_children, before_actions = sfc_components(context.source)
    result, _ = edit_sfc_program(context, graph_edits=[{'kind': 'renumber',
        role: [{'before': first, 'after': number}], 'transitions': [{'before': condition, 'after': number + 1}]}])
    after = parse_sfc_pou(result)
    children, actions = sfc_components(after)
    assert [row['body'] for row in children] == [row['body'] for row in before_children]
    assert {row['number'] for row in actions} == {number if row['number'] == first else row['number'] for row in before_actions}
    refs = [row for row in after.graph_records if row['kind'] == 'step-action-reference']
    assert number in [row['references'][0] for row in refs]
    assert first not in [row['references'][0] for row in refs]
    assert after.diagnostics == ()


def test_child_renaming_and_source_bound_instruction_edit_keep_links_and_other_bytes():
    context = read_sfc_project_context(_authoring('fx3u', 'original.gxw'))
    child = next(row for row in context.object_model(token_profile='fx3u')['children'] if row['kind'] == 'zoom')
    instruction = next((index, row) for index, row in enumerate(child['records']) if row['kind'] == 'instruction')
    index, row = instruction
    edited, _ = edit_sfc_program(context, child_edits=[{'kind': 'zoom', 'name': child['name'],
        'new_name': '离线动作程序', 'instruction_edits': [{'operation': 'replace', 'record_index': index,
            'expected': {'op': row['op'], 'args': row['args']}, 'instructions': [{'op': row['op'], 'args': ['Y20']}]}]}])
    source = parse_sfc_pou(edited)
    assert source.diagnostics == ()
    new_child = next(row for row in source.layout['children'] if row['name'] == '离线动作程序')
    action = next(row for row in source.layout['actions'] if any(r['zoom'] == '离线动作程序' for r in row['registrations']))
    assert action['number'] == 0 and new_child['token_hex'] != next(r for r in context.source.layout['children'] if r['name'] == child['name'])['token_hex']
    before = {row['name']: context.source.raw[row['payload_offset']:row['tail_offset']] for row in context.source.layout['children']}
    assert all(edited[row['payload_offset']:row['tail_offset']] == before[row['name']] for row in source.layout['children'] if row['name'] != '离线动作程序')


@pytest.mark.parametrize('damage', ['stale_instruction', 'delete_referenced', 'duplicate_number', 'unbound_zoom'])
def test_internal_edit_conflicts_fail_before_mutating_the_source(damage):
    context = read_sfc_project_context(_authoring('q00j', 'original.gxw'))
    before = context.source.raw
    child = next(row for row in context.source.layout['children'] if row['kind'] == 'zoom')
    action = context.source.layout['actions'][0]
    edits = {'stale_instruction': [{'kind': 'zoom', 'name': child['name'], 'instruction_edits': [
                 {'operation': 'replace', 'record_index': 0, 'expected': {'op': 'LD', 'args': ['X777']}, 'instructions': [{'op': 'LD', 'args': ['X0']}]}]}],
             'delete_referenced': [{'kind': 'action', 'name': action['name'], 'operation': 'delete'}],
             'duplicate_number': [{'kind': 'action', 'name': 'newaction', 'operation': 'create', 'number': action['number'], 'registrations': []}],
             'unbound_zoom': [{'kind': 'action', 'name': action['name'], 'registrations': [{'qualifier': 0, 'zoom': 'missing'}]}]}[damage]
    with pytest.raises(GXWFormatError):
        edit_sfc_program(context, child_edits=edits)
    assert context.source.raw == before


def test_internal_graph_and_child_commands_roundtrip_real_http_schemas_and_offline_candidate(tmp_path):
    from application.sfc import prepare_candidate, read_snapshot
    from integrations.web.schemas import SFCPreview
    from integrations.web.program_contracts import SFCModel
    raw = _authoring('fx3u', 'original.gxw')
    edits = json.loads(_authoring('fx3u', 'edits.json'))
    command = SFCPreview.model_validate(dict(project_id='project', version_id='version',
        graph_edits=edits['graph_edits'], child_edits=edits['child_edits'])).model_dump(exclude_none=True)
    assert command['graph_edits'] == edits['graph_edits'] and command['child_edits'] == edits['child_edits']
    payload = prepare_candidate(tmp_path, baseline=raw, graph_edits=command['graph_edits'], child_edits=command['child_edits'])
    saved = (tmp_path / payload['artifacts']['gxw']['path']).read_bytes()
    snapshot = read_snapshot(saved)['model']
    public = SFCModel.model_validate(snapshot).model_dump(mode='json', exclude_none=True)
    assert public['graph']['source_graph']['status'] == 'source-decoded'
    assert [row['step_reference'] for row in public['graph']['source_graph']['steps']] == [0, 80, 1]
    for node in snapshot['graph']['source_graph']['nodes']:
        assert node['binding_status'] in ('source-resolved', 'not-applicable')
        assert all(any(row['name'] == name for row in snapshot['children']) for name in node['child_names'])
    assert public['cpu'] == 'FX3U/FX3UC'
    assert payload['metadata']['validation']['write_backend'] == 'offline'


@pytest.mark.parametrize('unknown_other_table', [False, True])
def test_author_sfc_graph_child_and_local_global_declarations_share_one_transaction(tmp_path, unknown_other_table):
    from application.sfc import prepare_candidate, read_snapshot
    from gxw.declarations import parse_declarations
    from gxw.offline_project import read_offline_project
    from gxw.project_writer import write_project_replacements
    from gxw.source_header import source_payload_offset
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-offline-authoring-20261021.zip')) as frozen:
        raw = frozen.read('author-cmz/original.gxw')
    project = read_offline_project(raw)
    selected = next(name for name in project.streams if 'Block001' in name and name.endswith('.pou'))
    other_table = next(name for name in project.streams if 'Block000' in name and name.endswith('.lh'))
    if unknown_other_table:
        old = project.streams[other_table]
        # An independent table is deliberately outside the declaration parser,
        # retaining a valid common envelope and all original stream identities.
        opaque = old[:source_payload_offset(old)] + b'unknown-declaration-layout'
        raw = write_project_replacements(raw, {other_table: (project.mapping[other_table], old, opaque)}, invalidate=False).data
        project = read_offline_project(raw)
    context = read_sfc_project_context(raw, selected)
    local = next(name for name in context.declarations if name.endswith('.lh'))
    child = next(row for row in context.object_model()['children'] if row['kind'] == 'zoom')
    transition = decode_sfc_graph(context.source, cpu=context.project['cpu'])['transitions'][0]['number']
    patch = {local: {'upserts': [dict(name='SfcOfflineScratch', data_type='INT', comment='联合局部声明变长' * 12)]},
             'Global1.gh': {'upserts': [dict(name='SfcOfflineGlobal', data_type='INT', comment='联合全局声明' * 14)]}}
    payload = prepare_candidate(tmp_path, baseline=raw, program_name=selected,
        graph_edits=[dict(kind='renumber', transitions=[dict(before=transition, after=80)])],
        child_edits=[dict(kind='zoom', name=child['name'], instructions=[dict(op='LD', args=['X0']), dict(op='OUT', args=['Y20'])])],
        declaration_edits=patch)
    saved = (tmp_path / payload['artifacts']['gxw']['path']).read_bytes()
    current = read_offline_project(saved)
    model = read_snapshot(saved, selected)['model']
    assert any(row['number'] == 80 for row in model['graph']['source_graph']['transitions'])
    zoom = next(row for row in model['children'] if row['kind'] == 'zoom' and row['name'] == child['name'])
    assert [(row['op'], row['args']) for row in zoom['records'] if row['kind'] == 'instruction'] == [
        ('LD', ['X0']), ('OUT', ['Y20'])]
    for name in (local, 'Global1.gh'):
        document = parse_declarations(current.streams[name], logical_name=name, source_kind=240 if name == local else None)
        row = next(row for row in document.rows if row.name == patch[name]['upserts'][0]['name'])
        assert row.comment == patch[name]['upserts'][0]['comment']
        assert len(current.streams[name]) > len(project.streams[name])
    assert current.streams[other_table] == project.streams[other_table]
    for name, value in project.streams.items():
        if name in (local, 'Global1.gh', selected):
            continue
        if name.endswith('.pou'):
            offset = source_payload_offset(value)
            assert current.streams[name][:offset - 4] == value[:offset - 4]
            assert current.streams[name][offset:] == value[offset:]
        else:
            assert current.streams[name] == value
    assert current.outer['projectdatalist.xml'] == project.outer['projectdatalist.xml']
    validation = payload['metadata']['validation']
    assert validation['compile_state'] == 'stale'
    assert any(row['source'] == selected and row['language'] == 'sfc' for row in validation['source_checks'])
    if unknown_other_table:
        assert any(row['source'] == other_table and row['status'] == 'unverified' for row in validation['source_checks'])


@pytest.mark.parametrize('damage', ['stale', 'missing_table', 'foreign_source', 'conflict'])
def test_joint_declarations_reject_foreign_sources_tables_and_conflicting_edits(damage):
    from gxw.offline_authoring import prepare_declaration_edits
    from gxw.offline_project import read_offline_project
    raw = _authoring('fx3u', 'original.gxw')
    project = read_offline_project(raw)
    context = read_sfc_project_context(raw)
    table = next(name for name in context.declarations if name.endswith('.lh'))
    patch = {table: {'upserts': [dict(name='OfflineJoint', data_type='INT')]}}
    arguments = {}
    if damage == 'stale':
        arguments['expected_sources'] = {table: project.streams[table] + b'foreign'}
    elif damage == 'missing_table':
        patch = {'missing.Labels.lh': patch[table]}
    elif damage == 'foreign_source':
        arguments['source_replacements'] = {'missing.Program.pou': context.source.raw}
    else:
        patch[table]['upserts'].append(dict(name='OfflineJoint', data_type='WORD'))
    with pytest.raises(GXWFormatError):
        prepare_declaration_edits(raw, patch, **arguments)


def test_new_ql_child_source_labels_require_current_or_joint_declaration_binding(tmp_path):
    from application.sfc import prepare_candidate, read_snapshot
    raw = _authoring('q00j', 'original.gxw')
    context = read_sfc_project_context(raw)
    local = next(name for name in context.declarations if name.endswith('.lh'))
    child = next(row for row in context.object_model()['children'] if row['kind'] == 'zoom')
    edit = [dict(kind='zoom', name=child['name'], instructions=[
        dict(op='LD', args=["'OfflineFlag"]), dict(op='OUT', args=['Y20'])])]
    with pytest.raises(GXWFormatError, match='no current declaration binding'):
        prepare_candidate(tmp_path / 'unbound', baseline=raw, child_edits=edit)
    payload = prepare_candidate(tmp_path / 'bound', baseline=raw, child_edits=edit,
        declaration_edits={local: {'upserts': [dict(name='OfflineFlag', data_type='BOOL')]}})
    saved = (tmp_path / 'bound' / payload['artifacts']['gxw']['path']).read_bytes()
    model = read_snapshot(saved)['model']
    assert any(row['name'] == 'OfflineFlag' for row in model['labels'][local])
    current_child = next(row for row in model['children'] if row['name'] == child['name'] and row['kind'] == 'zoom')
    requirement = next(row['native_requirements'] for row in current_child['records'] if row.get('op') == 'LD')
    assert requirement['requires_label_binding'] and requirement['native_compile'] == 'not_run'
    check = next(row for row in payload['metadata']['validation']['source_checks'] if row['language'] == 'sfc')
    assert check['known_label_references'] == ['OfflineFlag']
    # Reusing the same bound label for a later body-only edit needs no table or
    # ST project context rebuild, and still reports its current source binding.
    payload = prepare_candidate(tmp_path / 'body-only', baseline=saved, child_edits=edit)
    assert payload['metadata']['validation']['source_checks'][0]['known_label_references'] == ['OfflineFlag']


def test_every_native_sfc_menu_source_preserves_unchanged_internal_records():
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-cpu-menus-20261014.zip')) as frozen:
        entries = sorted(name for name in frozen.namelist() if name.startswith('graph-read-') and name.endswith('/input.gxw'))
        # Q12PH also has an explicitly fresh independent-initial-state control.
        assert len(entries) == 58
        cpus = set()
        for entry in entries:
            context = read_sfc_project_context(frozen.read(entry))
            cpus.add(context.project['cpu'])
            source = context.source
            children, actions = sfc_components(source)
            assert serialize_sfc_components(source, children=children, actions=actions) == source.raw, entry
            assert decode_sfc_graph(source, cpu=context.project['cpu'])['issues'] == [], entry
        assert len(cpus) == 57


def test_independent_author_project_keeps_other_sfc_program_and_child_bytes():
    from gxw.offline_project import read_offline_project
    from gxw.project_writer import write_project_replacements
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-offline-authoring-20261021.zip')) as frozen:
        before = frozen.read('author-cmz/original.gxw')
        command = json.loads(frozen.read('author-cmz/edits.json'))
        native = frozen.read('author-cmz/source-save/candidate.gxw')
        receipt = json.loads(frozen.read('author-cmz/result.json'))
    project = read_offline_project(before)
    selected = next(row['source_name'] for row in project.sources() if 'Block001' in row['source_name'])
    context = read_sfc_project_context(before, selected)
    assert len(context.programs) == 2 and len(decode_sfc_graph(context.source, cpu=context.project['cpu'])['steps']) == 7
    result, _ = edit_sfc_program(context, child_edits=command)
    output = write_project_replacements(before, {selected: (project.mapping[selected], context.source.raw, result)}).data
    current = read_offline_project(output)
    assert all(current.streams[name] == raw for name, raw in project.streams.items() if name != selected)
    native_context = read_sfc_project_context(native, next(row['after'] for row in receipt['aliases'] if row['before'] == selected))
    assert _component_snapshot(parse_sfc_pou(result)) == _component_snapshot(native_context.source)
    assert receipt['compile_save']['compilation']['acceptance'] == receipt['cold']['compilation']['acceptance'] == 'accepted'
    assert receipt['all_saved_child_source_exact'] is True
    # The author's first FX block is an actual implicit same-number source,
    # independently of the explicit pair form in its second block.
    first = read_sfc_project_context(before, next(name for name in context.programs if name != selected))
    model = decode_sfc_graph(first.source, cpu=first.project['cpu'])
    assert model['steps'][0]['action_number'] == model['steps'][0]['step_reference'] == 0
    assert model['issues'] == []


def test_explicit_internal_creation_and_joint_renumber_preserve_requested_names_and_bodies():
    context = read_sfc_project_context(_authoring('fx3u', 'original.gxw'))
    edits = json.loads(_authoring('fx3u', 'edits.json'))
    children = [
        {'kind': 'zoom', 'name': 'ChosenZoom', 'operation': 'create', 'instructions': [{'op': 'OUT', 'args': ['Y20']}]},
        {'kind': 'action', 'name': 'ChosenAction', 'operation': 'create', 'number': 80,
         'registrations': [{'qualifier': 0, 'zoom': 'ChosenZoom'}]},
        {'kind': 'transition', 'name': 'ChosenCondition', 'operation': 'create', 'number': 80,
         'instructions': [{'op': 'LD', 'args': ['X0']}]}]
    created, report = edit_sfc_program(context, graph_edits=edits['graph_edits'], child_edits=children)
    source = parse_sfc_pou(created)
    assert source.diagnostics == () and report['native_calls'] == 0
    assert next(row for row in source.layout['actions'] if row['number'] == 80)['name'] == 'ChosenAction'
    current = _context(created, context.project['cpu'])
    renumbered, _ = edit_sfc_program(current, child_edits=[
        {'kind': 'action', 'name': 'ChosenAction', 'operation': 'renumber', 'new_number': 81},
        {'kind': 'transition', 'name': 'ChosenCondition', 'operation': 'renumber', 'new_number': 82}])
    after = parse_sfc_pou(renumbered)
    assert after.diagnostics == ()
    assert next(row for row in after.graph_records if row['kind'] == 'step-action-reference' and row['references'][0] == 80)['references'] == [80, 81]
    assert 82 in [row['references'][0] for row in after.graph_records if row['kind'] == 'transition-reference']
    assert [row['token_hex'] for row in after.layout['children']] == [row['token_hex'] for row in source.layout['children']]


def test_unconverted_graph_keeps_unknown_bytes_during_existing_child_token_edit():
    context = read_sfc_project_context(_authoring('fx3u', 'original.gxw'))
    raw = bytearray(context.source.raw)
    graph = context.source.layout['graph']
    struct.pack_into('<I', raw, graph['offset'] + 4, 1)
    before = _context(bytes(raw), context.project['cpu'])
    child = next(row for row in before.source.layout['children'] if row['kind'] == 'zoom')
    other = [row for row in before.source.layout['children'] if row['kind'] == 'zoom'][1]
    result, report = edit_sfc_program(before, child_edits=[{'kind': 'zoom', 'name': child['name'], 'records_hex': other['token_hex']}])
    assert result[graph['offset']:graph['offset'] + graph['size']] == before.source.raw[graph['offset']:graph['offset'] + graph['size']]
    assert report['graph']['status'] == 'not-established' and report['native_calls'] == 0
    with pytest.raises(GXWFormatError, match='registration changes require a decoded'):
        edit_sfc_program(before, child_edits=[{'kind': 'zoom', 'name': child['name'], 'new_name': 'changed'}])


def test_geometry_encoder_cannot_drop_an_unknown_existing_record():
    case = _fixture('gxw_sfc_ql_rebuild.json')
    raw = bytearray(base64.b64decode(case['before_source_base64']))
    source = parse_sfc_pou(raw)
    reference = next(row for row in source.graph_records if row['kind'] == 'step-reference')
    raw[reference['offset'] + 3] = 0x21
    with pytest.raises(GXWFormatError, match='cannot discard unresolved source'):
        encode_sfc_symbols(parse_sfc_pou(raw), case['edit']['symbols'], cpu=case['cpu'])


def test_repeated_action_record_and_unknown_cpu_are_rejected():
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    raw = base64.b64decode(case['before_source_base64'])
    edit = {'step_reference': 1, 'action_number': 20}
    with pytest.raises(GXWFormatError, match='repeat the same'):
        edit_sfc_source(_context(raw, case['cpu']), [edit, edit])
    with pytest.raises(GXWFormatError, match='observed concrete CPU'):
        edit_sfc_source(_context(raw, 'unknown'), [edit])


def test_noop_action_write_is_byte_exact_and_unknown_records_stay_opaque():
    case = _fixture('gxw_sfc_fx_edits.json')['cases'][0]
    raw = base64.b64decode(case['before_source_base64'])
    noop, report = edit_sfc_source(_context(raw, case['cpu']), [{'step_reference': 1, 'action_number': 10}])
    assert noop == raw and report['changed'] is False
    source = parse_sfc_pou(raw)
    transition = next(row for row in source.graph_records if row['kind'] == 'transition-reference')
    damaged = bytearray(raw)
    damaged[transition['offset'] + 3] = 0x21
    opaque = bytes(damaged)
    changed, report = edit_sfc_source(_context(opaque, case['cpu']), [{'step_reference': 1, 'action_number': 20}])
    assert changed[transition['offset']:transition['offset'] + 5] == opaque[transition['offset']:transition['offset'] + 5]
    assert any(row['reason'] == 'uninterpreted-graph-record' for row in report['graph']['issues'])
    _assert_retained(opaque, changed)


def _writer_symbols(native):
    """Turn independent native cell observations into their writer operands."""
    symbols, connectors, lines = [], {}, {}
    kinds = {0x50:'selective_branch',0x60:'selective_coupling',0x90:'parallel_branch',0xa0:'parallel_coupling'}
    for cell in native['symbols']:
        if cell['result'] == 1:
            kind = 'initial_step' if cell['type'] in (6,10,26) else 'jump' if cell['type'] == 15 else 'end' if cell['type'] == 14 else 'step'
            row = {'kind':kind,'x':cell['x'],'y':cell['y']}
            if kind != 'end':
                row['number'] = cell['number']
            symbols.append(row)
        elif cell['result'] == 2:
            symbols.append({'kind':'transition','x':cell['x'],'y':cell['y'],'number':cell['number']})
        elif cell['branch'] & 0xf0:
            kind = kinds[cell['branch'] & 0xf0]
            connectors.setdefault((kind,cell['y']),[]).append(cell['x'])
        else:
            lines.setdefault(cell['x'],[]).append(cell['y'])
    for (kind,y), values in connectors.items():
        assert sorted(values) == list(range(min(values),max(values)+1))
        symbols.append({'kind':kind,'start':min(values),'end':max(values),'y':y})
    for x, values in lines.items():
        for y in values:
            symbols.append({'kind':'line','x':x,'start':y,'end':y})
    return symbols


def _native_fx_graph_control(name):
    metadata = _fixture('gxw_sfc_native_connections.json')
    case = next(case for case in metadata['cases'] if case['name'] == name)
    archive = private_path('research/experiments/sfc-graph-20260926/public-corpus-discovery/' + metadata['historical_archive'])
    with zipfile.ZipFile(archive) as frozen:
        tokens = frozen.read(case['source_entry'] + 'sfc-encoded-tokens.bin')
    # These controls establish the native graph grammar. Their source programs
    # were not complete project-acceptance controls. Wrap only that independent
    # native code in an existing source frame to test the pure graph encoder.
    base = base64.b64decode(_fixture('gxw_sfc_fx_edits.json')['cases'][0]['before_source_base64'])
    old = parse_sfc_pou(base).layout['graph']
    cache = base[old['cache_offset']:old['cache_offset'] + old['cache_size']]
    size = 16 + len(tokens) + len(cache)
    body = struct.pack('<4I',size,0,0,len(tokens)+4) + tokens + cache
    raw = base[:old['offset']-4] + struct.pack('<I',size) + body + base[old['offset']+old['size']:]
    return parse_sfc_pou(raw), _writer_symbols(case['graph']), tokens


@pytest.mark.parametrize('name', ['linear','parallel','selective','parallel-three-paths','selective-three-paths'])
def test_fx_geometry_encoder_matches_independent_native_branches_and_dependencies(name):
    source, symbols, expected = _native_fx_graph_control(name)
    assert encode_sfc_symbols(source,symbols,cpu='FX3U/FX3UC') == expected
    model = decode_sfc_graph(source,cpu='FX3U/FX3UC')
    assert model['issues'] == []


@pytest.mark.parametrize('name', ['ql_branch','fx_rebuild'])
def test_offline_rebuild_matches_independent_native_save_and_cold_context(name):
    controls = _fixture('gxw_offline_native_controls.json')
    case = controls['sfc'][name]
    with zipfile.ZipFile(private_path(controls['archive'])) as frozen:
        context = read_sfc_project_context(frozen.read(case['before']))
        native = read_sfc_project_context(frozen.read(case['after']))
        edits = json.loads(frozen.read(case['edits']))
        validation = json.loads(frozen.read(case['validation']))
        if 'cold_body' in case:
            assert frozen.read(case['cold_body']) == _region(native.source.raw)
    result, _ = edit_sfc_source(context,edits)
    assert _region(result,'code') == _region(native.source.raw,'code')
    _assert_retained(context.source.raw,result)
    assert validation['compilation']['acceptance'] == 'accepted'
    assert validation['public_check'] == {'status':'passed','completed':True}
    assert validation['current_source_check'] == 'established'


@pytest.mark.parametrize('family', ['q-l','fx'])
def test_combined_parallel_matches_independent_bounded_native_converter(family):
    controls = _fixture('gxw_offline_native_controls.json')
    case = controls['combined_parallel']['cases'][family]
    with zipfile.ZipFile(private_path(controls['archive'])) as frozen:
        frame = read_sfc_project_context(frozen.read(controls['combined_parallel']['base'])).source
        expected = frozen.read(case['tokens'])
        symbols = json.loads(frozen.read(case['symbols']))
        result = json.loads(frozen.read(case['result']))
    # The original source frame supplies existing registrations and children.
    # Only the graph code comes from this independent converter observation;
    # neither expected tokens nor expected dependencies use Core's encoder.
    graph = frame.layout['graph']
    cache = frame.raw[graph['cache_offset']:graph['cache_offset'] + graph['cache_size']]
    size = 16 + len(expected) + len(cache)
    body = struct.pack('<4I',size,0,0,len(expected)+4) + expected + cache
    raw = (frame.raw[:graph['offset']-4] + struct.pack('<I',size) + body
           + frame.raw[graph['offset']+graph['size']:])
    source = parse_sfc_pou(raw)
    assert source.diagnostics == ()
    assert not any(row['kind'] == 'opaque' for row in source.graph_records)
    assert encode_sfc_symbols(source,symbols,cpu=case['cpu']) == expected
    decoded = decode_sfc_graph(source,cpu=case['cpu'])
    assert decoded['issues'] == []
    combined, = [row for row in decoded['transitions'] if row['opcode'] == '056c020805']
    assert combined['number'] == 1
    assert [row['step_reference'] for row in combined['dependencies']] == [1]
    assert [row['step_reference'] for row in combined['outgoing']] == [3,4]
    assert result['native_check'] == {'operation':'SFC.CheckResult','result':0}
    assert len(result['buffer_canaries']) == 9 and all(row['canary_intact'] for row in result['buffer_canaries'])


@settings(max_examples=16,deadline=None,derandomize=True)
@given(dx=st.integers(min_value=0,max_value=3),dy=st.integers(min_value=0,max_value=3))
def test_geometry_translation_preserves_native_reference_code(dx,dy):
    source,symbols,expected = _native_fx_graph_control('selective-three-paths')
    symbols = deepcopy(symbols)
    for row in symbols:
        if 'x' in row:
            row['x'] += dx
        if 'y' in row:
            row['y'] += dy*3
        if row['kind'] == 'line':
            row['start'] += dy*3; row['end'] += dy*3
        elif 'start' in row:
            row['start'] += dx; row['end'] += dx
    assert encode_sfc_symbols(source,symbols,cpu='FX3U/FX3UC') == expected

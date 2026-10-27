"""Exact-menu allocation/storage boundaries; these tests never start an IDE."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from gxw.models import GXWFormatError
from gxw.project_metadata import (
    native_allocation_controls, project_allocation_profile, project_gui_configuration,
    native_allocation_grid_state, native_allocation_observation_state,
    read_native_allocation_grid, read_native_allocation_grids,
    read_gui_system_variables, read_project_system_variables, validate_project_allocation_ranges,
)
from gxw.project_templates import blank_fbd_baseline, blank_fbd_models
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES, native_project_profile


PROGRAM_MENUS = tuple(row.cpu for row in NATIVE_PROJECT_PROFILES if row.program_supported)
PARAMETER_MENUS = tuple(row.cpu for row in NATIVE_PROJECT_PROFILES if not row.program_supported)


def _identity(cpu='FX3U/FX3UC'):
    return dict(process_id=701, window_handle=9001, process_started='123456',
        executable='D:/GXWORKS2/GD2.exe', software_version='1.635.0.1',
        project_path='D:/Lab/live.gxw', cpu_menu=cpu)


def test_allocation_catalogue_keeps_all_native_menu_entries_and_parameter_boundaries():
    assert len(PROGRAM_MENUS) == 57 and set(PROGRAM_MENUS) == set(blank_fbd_models())
    assert len(PARAMETER_MENUS) == 3
    assert project_allocation_profile('L02')['cpu_menu'] == 'L02/L02-P'
    assert project_allocation_profile('FX3GC')['cpu_menu'] == 'FX3G/FX3GC'


@pytest.mark.parametrize('cpu', PROGRAM_MENUS)
def test_exact_menu_profile_reads_its_native_template_without_claiming_live_support(cpu):
    from gxw.fb_authoring import _context
    profile = project_allocation_profile(cpu)
    config, streams, *_ = _context(blank_fbd_baseline(cpu))
    metadata, = [raw for name, raw in streams.items() if name.endswith('.prj')]
    expected = read_project_system_variables(metadata)['ranges']
    assert profile['cpu_menu'] == cpu
    assert profile['stored_cpu'] == config['cpu']
    assert profile['observed_ranges'] == {name: list(pair) for name, pair in expected.items()}
    assert profile['status'] == 'saved_template_observed'
    assert profile['native_controls']['status'] == ('verified' if cpu == 'FX3U/FX3UC' else 'unverified')
    # Returned profile data is not a mutable global support registry.
    profile['observed_ranges']['D'][0] = 42
    assert project_allocation_profile(cpu)['observed_ranges']['D'] == list(expected['D'])


@pytest.mark.parametrize('cpu', PARAMETER_MENUS)
def test_parameter_only_targets_never_inherit_a_program_allocation_profile(cpu):
    profile = project_allocation_profile(cpu)
    assert profile['status'] == 'parameter_only' and not profile['program_supported']
    assert profile['observed_ranges'] == {} and profile['saved_fields'] == []
    with pytest.raises(GXWFormatError, match='parameter-only'):
        validate_project_allocation_ranges({'D': [0, 10]}, cpu=cpu)


@pytest.mark.parametrize('cpu', ('FX5U', 'Q2A', 'Q06H-A', 'L99', '', None))
def test_unknown_or_other_environment_targets_never_fall_back_to_fx3u(cpu):
    assert project_allocation_profile(cpu)['status'] == 'unknown'
    with pytest.raises(GXWFormatError, match='exact native CPU menu'):
        validate_project_allocation_ranges({'D': [0, 10]}, cpu=cpu)


@pytest.mark.parametrize('cpu,device,pair', [
    ('FX0S/FX0', 'D', [11, 31]), ('FX0N', 'M', [300, 400]),
    ('FX1N/FX1NC', 'D', [400, 999]), ('Q13UDV', 'D', [30000, 40000]),
    ('Q03UDV', 'D', [0, 13311]), ('Q00J', 'D', [0, 11135]),
    ('L02', 'D', [6144, 12287]), ('FX3U/FX3UC', 'D', [1000, 7999]),
])
def test_target_ranges_use_observed_or_reviewed_exact_cpu_intervals(cpu, device, pair):
    request = {device: list(pair)}
    assert validate_project_allocation_ranges(request, cpu=cpu) == {device: tuple(pair)}
    assert request == {device: pair}


@pytest.mark.parametrize('cpu,device,pair', [
    ('FX0S/FX0', 'D', [11, 32]), ('FX0N', 'D', [0, 127]),
    ('FX1', 'D', [0, 10]), ('Q13UDV', 'D', [0, 10]),
    ('Q03UDV', 'D', [0, 13312]), ('Q03UDV', 'M', [0, 9216]),
    ('Q00J', 'D', [0, 11136]), ('L02', 'D', [6144, 12288]),
    ('FX3U/FX3UC', 'D', [7999, 8000]), ('FX3U/FX3UC', 'W', [0, 10]),
])
def test_unobserved_ranges_are_rejected_without_inventing_a_cpu_device_maximum(cpu, device, pair):
    with pytest.raises(GXWFormatError, match='outside the observed exact CPU interval'):
        validate_project_allocation_ranges({device: pair}, cpu=cpu)


@pytest.mark.parametrize('edit', [
    {'D': [True, 100]}, {'D': [100, 99]}, {'D': [-1, 10]}, {'D': [0, 2**31]},
    {'D': (512, 600)}, {'D': [1]}, {'ZZ': [1, 2]}, ['D'],
])
def test_target_allocation_syntax_rejects_invalid_values_before_native_planning(edit):
    with pytest.raises(GXWFormatError):
        validate_project_allocation_ranges(edit, cpu='FX3U/FX3UC')


def test_allocation_range_subsets_roundtrip_without_mutating_profile_or_input():
    from hypothesis import given, settings, strategies as st
    rows = [(cpu, device, pair) for cpu in PROGRAM_MENUS
            for device, pair in project_allocation_profile(cpu)['observed_ranges'].items()
            if pair != [0xffffffff, 0xffffffff]]

    @st.composite
    def subset(draw):
        cpu, device, (low, high) = draw(st.sampled_from(rows))
        start = draw(st.integers(low, high))
        end = draw(st.integers(start, high))
        return cpu, device, [start, end]

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(subset())
    def check(example):
        cpu, device, pair = example
        before = project_allocation_profile(cpu)
        request = {device: pair}
        result = validate_project_allocation_ranges(request, cpu=cpu)
        assert list(result[device]) == pair
        assert request == {device: pair} and project_allocation_profile(cpu) == before
    check()


@pytest.mark.parametrize('cpu', PROGRAM_MENUS)
def test_shared_compile_option_decode_is_available_without_promoting_checkbox_evidence(cpu):
    from gxw.live_context import live_compile_plan
    # The independently retained FXU template stores the disable-check tag;
    # preserving its setting is part of the common decode contract.
    assert live_compile_plan(blank_fbd_baseline(cpu), 8) == dict(
        revision=8, public_checks_requested=cpu != 'FXU/FX2C')
    assert project_allocation_profile(cpu)['compile_option_ui'] == ('verified' if cpu == 'FX3U/FX3UC' else 'unverified')


@pytest.mark.parametrize('cpu', PROGRAM_MENUS)
def test_saved_source_readback_keeps_every_exact_menu_and_retained_configuration(cpu):
    from gxw.fb_authoring import _context
    from gxw.live_context import verify_active_saved_context
    from tests.test_gxw_object_model import live_native_context_witness
    raw = blank_fbd_baseline(cpu)
    config = _context(raw)[0]
    identity = _identity(native_project_profile(config['cpu']).cpu)
    # This fixture represents source semantics only. It deliberately carries
    # no native-control verification or per-language live acceptance claim.
    current = live_native_context_witness(raw, identity=identity)
    current['configuration']['coverage'] = 'source_storage_fixture_D_M'
    result = verify_active_saved_context(raw, current, raw)
    assert result['status'] == 'matched' and result['configuration'] == 'matched'
    assert result['compilation'] == 'not_requested' and result['execution'] == 'not_run'


@pytest.mark.parametrize('menu,stored_cpu', [
    ('L02S/L02S-P', 'L02S'), ('L02/L02-P', 'L02'), ('L06/L06-P', 'L06'),
    ('L26/L26-P', 'L26'), ('L26-BT/L26-PBT', 'L26-BT'),
])
def test_frozen_l_gui_host_sentinels_do_not_override_effective_workspace_ranges(menu, stored_cpu):
    from tools.offline_inputs import private_path
    from gxw.fb_authoring import _context
    with ZipFile(private_path('research/' + 'evidence/gxw-fbd-ui-entry-20261013.zip')) as archive:
        raw = archive.read('l-pointer-allocation/' + stored_cpu + '-two-instances.gxw')
    config, streams, *_ = _context(raw)
    original = streams['Gppw2.gpj']
    decoded = read_gui_system_variables(original, cpu=config['cpu'])
    assert decoded['ranges'] == {} and decoded['stored_ranges']['D'] == (0xffffffff, 0xffffffff)
    result = project_gui_configuration(raw)
    assert result['status'] == 'not_verified' and result['cpu_menu'] == menu
    assert result['gui_mirror_status'] == 'inactive_host_values_observed'
    assert result['workspace_ranges']['D'] == [6144, 12287]
    assert result['effective_ranges'] == {} and result['differences'] == {}
    assert streams['Gppw2.gpj'] == original


@pytest.mark.parametrize('cpu', ('Q03UDV', 'L02', 'FX0N'))
def test_missing_unverified_gui_mirror_does_not_claim_agreement_with_workspace(cpu):
    result = project_gui_configuration(blank_fbd_baseline(cpu))
    assert result['status'] == 'not_verified'
    assert result['workspace_ranges'] == project_allocation_profile(cpu)['observed_ranges']
    assert result['effective_ranges'] == {} and result['retained_fields'] == {}


def _observed_configuration():
    cpu = 'FX3U/FX3UC'
    controls = native_allocation_controls('device_range_fx')
    ranges = {'D': [512, 999], 'M': [3840, 7679]}
    return dict(cpu=cpu, status='available', origin='active_native_configuration',
        allocation_profile=project_allocation_profile(cpu)['allocation_profile'],
        control_profile=controls['control_profile'], coverage='observed_D_M_controls', ranges=ranges,
        raw_fields={device: [dict(control_id=control_id, text=str(value), enabled=True)
            for control_id, value in zip((controls['devices'][device]['start_id'], controls['devices'][device]['end_id']), pair)]
            for device, pair in ranges.items()})


def test_observed_allocation_profile_binds_exact_field_ids_and_range_text():
    from gxw.live_context import _allocation_configuration_profile
    observed = _observed_configuration()
    assert _allocation_configuration_profile(observed, observed['cpu']) == {
        key: observed[key] for key in ('allocation_profile', 'control_profile')}
    assert native_allocation_controls('device_range_classic')['status'] == 'static_resource_observed'


@pytest.mark.parametrize('damage', ('cpu_profile', 'control_profile', 'grid_profile', 'missing_profile',
    'coverage', 'field_id', 'numeric_text', 'missing_device', 'enabled_type', 'extra_field'))
def test_allocation_profile_rejects_unbound_or_inconsistent_native_field_observations(damage):
    from gxw.live_context import _allocation_configuration_profile
    observed = _observed_configuration()
    if damage == 'cpu_profile': observed['allocation_profile'] = project_allocation_profile('Q03UDV')['allocation_profile']
    elif damage == 'control_profile': observed['control_profile'] = 'device_range_classic'
    elif damage == 'grid_profile': observed['control_profile'] = 'device_range_grid'
    elif damage == 'missing_profile': observed.pop('control_profile')
    elif damage == 'coverage': observed['coverage'] = 'verified_D_M_controls_only'
    elif damage == 'field_id': observed['raw_fields']['D'][0]['control_id'] = 999
    elif damage == 'numeric_text': observed['raw_fields']['D'][0]['text'] = '1234'
    elif damage == 'missing_device': observed['raw_fields'].pop('M')
    elif damage == 'enabled_type': observed['raw_fields']['D'][0]['enabled'] = 1
    else: observed['raw_fields']['D'].append(deepcopy(observed['raw_fields']['D'][0]))
    with pytest.raises(GXWFormatError):
        _allocation_configuration_profile(observed, observed['cpu'])


@pytest.mark.parametrize('enabled', (True, False))
def test_live_allocation_plan_carries_current_profile_and_rejects_disabled_native_fields(enabled):
    from gxw.fb_authoring import _context
    from gxw.live_context import prepare_live_command, verify_live_change
    from tests.test_gxw_object_model import live_native_context_witness
    raw = blank_fbd_baseline('FX3U/FX3UC')
    current = live_native_context_witness(raw)
    current['configuration'] = {**_observed_configuration(), 'identity': deepcopy(current['identity'])}
    current['configuration']['raw_fields']['D'][0]['enabled'] = enabled
    name, = [name for name in _context(raw)[1] if name.endswith('.prj')]
    command = dict(operation='configure_project', source_name=name,
        options=dict(system_variable_ranges={'D': [600, 800]}))
    if not enabled:
        with pytest.raises(GXWFormatError, match='not enabled'):
            prepare_live_command(raw, current, command, device_profile='FX3U')
        return
    prepared = prepare_live_command(raw, current, command, device_profile='FX3U')
    assert prepared['plan']['control_profile'] == 'device_range_fx'
    assert prepared['plan']['allocation_profile'] == current['configuration']['allocation_profile']
    after = deepcopy(current)
    after['configuration']['ranges']['D'] = [600, 800]
    for row, value in zip(after['configuration']['raw_fields']['D'], (600, 800)):
        row['text'] = str(value)
    result = verify_live_change(current, after, command, prepared)
    assert result['status'] == 'matched' and result['save_performed'] is False


@pytest.mark.parametrize('language', ('zh-CN', 'en', 'ja'))
@pytest.mark.parametrize('codepage', (932, 936, 949, 1252))
def test_live_identity_retains_ui_language_separately_from_project_encoding(language, codepage):
    from gxw.live_context import _identity as read_identity
    value = {**_identity(), 'native_ui_language': language, 'project_codepage': codepage}
    assert read_identity(value) == value
    assert read_identity(_identity()) == _identity()


@pytest.mark.parametrize('extra', [
    {'native_ui_language': 'en'}, {'project_codepage': 1252},
    {'native_ui_language': 'en-US', 'project_codepage': 1252},
    {'native_ui_language': 'ja', 'project_codepage': True},
    {'native_ui_language': 'ja', 'project_codepage': 65001},
    {'native_ui_language': 'zh-CN', 'project_codepage': '936'},
])
def test_live_identity_refuses_partial_or_unrecognized_language_encoding_bindings(extra):
    from gxw.live_context import _identity as read_identity
    with pytest.raises(GXWFormatError, match='language and project-codepage'):
        read_identity({**_identity(), **extra})


@pytest.mark.parametrize('layer', ('table', 'graph', 'configuration'))
@pytest.mark.parametrize('field,value', [('native_ui_language', 'en'), ('project_codepage', 932)])
def test_live_context_rejects_mixed_language_or_encoding_between_its_current_pages(layer, field, value):
    from gxw.live_context import live_context_state
    from tests.test_gxw_object_model import live_native_context_witness
    raw = blank_fbd_baseline('FX3U/FX3UC')
    context = live_native_context_witness(raw)
    identity = {**context['identity'], 'native_ui_language': 'zh-CN', 'project_codepage': 936}
    context['identity'] = identity
    for table in context['tables']:
        table['source_binding']['identity'] = deepcopy(identity)
    for graph in context['graphs']:
        graph['identity'] = deepcopy(identity)
    context['configuration']['identity'] = deepcopy(identity)
    assert live_context_state(context)['identity'] == identity
    target = (context['tables'][0]['source_binding']['identity'] if layer == 'table' else
              context['graphs'][0]['identity'] if layer == 'graph' else context['configuration']['identity'])
    target[field] = value
    with pytest.raises(GXWFormatError):
        live_context_state(context)


@pytest.mark.parametrize('codepage,encoding,label', [
    (1252, 'cp1252', 'Température'), (932, 'cp932', '温度'), (936, 'gb18030', '温度𠀀'),
])
def test_saved_compiler_names_use_project_encoding_and_never_host_encoding(monkeypatch, codepage, encoding, label):
    from gxw import compiler_symbols, compiler_tables, visible_engineering
    assignment = SimpleNamespace(status='assigned', device_family='D', operand='D777',
        iec_address='%MW0.777', constant_value=None, role='word', reserved_count=1)
    symbol = SimpleNamespace(pou_offset=12, component=SimpleNamespace(name_bytes=label.encode(encoding)), assignment=assignment)
    tables = SimpleNamespace(pou_at=lambda _: SimpleNamespace(name_bytes='Owner'.encode(encoding)))
    monkeypatch.setattr(visible_engineering, '_context', lambda *a, **k:
        ({'codepage': codepage, 'text_encoding': encoding}, {'CGTable.dat': b'observed'}, None, None, None))
    monkeypatch.setattr(compiler_tables, 'parse_compiler_tables', lambda _: tables)
    monkeypatch.setattr(compiler_symbols, 'compiler_symbols', lambda _: [symbol])
    assert visible_engineering.saved_assignments(b'project')['rows'][0]['name'] == label


# Allocation-grid contracts use the independently captured Q03UDV Chinese
# IAccessible rows. Translated variants below are controlled resource-aligned
# examples, not English/Japanese native-session acceptance evidence.
@lru_cache(maxsize=1)
def _frozen_q_allocation_grids():
    from tools.offline_inputs import private_path
    root = private_path('research/experiments/native-live-multitarget-20261018/'
                        'q-allocation-native-new/actual-vsflex-msaa')
    if any(not (root / f'grid-{candidate}.json').is_file() for candidate in (14252, 14253)):
        pytest.skip('private Q03UDV native allocation-grid witness unavailable')
    result = {}
    for role, candidate in (('allocation', 14252), ('parameter', 14253)):
        captured = json.loads((root / f'grid-{candidate}.json').read_text(encoding='utf-8-sig'))
        assert captured['control']['cls'] == 'VSFlexGrid8N'
        assert captured['control']['control_id'] != candidate
        result[role] = [dict(native_row=int(row['value']),
            cells=[{key: cell[key] for key in ('name', 'value', 'state')} for cell in row['children']])
            for row in captured['msaa']['tree']['children'] if row['role'] == 28]
    return result


def _q_allocation_grids():
    return deepcopy(_frozen_q_allocation_grids())


def _set_grid_interval(grids, device, pair):
    row = grids['allocation'][{'D': 3, 'M': 10}[device]]
    for column, value in zip((5, 6, 7), (*pair, pair[1] - pair[0] + 1), strict=True):
        row['cells'][column]['value'] = str(value)


def test_allocation_grid_frozen_q_rows_bind_ranges_without_checkbox_or_static_id_inference():
    grids = _q_allocation_grids()
    original = deepcopy(grids)
    observed = read_native_allocation_grids(grids)
    assert observed['ranges'] == {'D': [6656, 13311], 'M': [4608, 9215]}
    assert observed['parameter_ranges'] == {'D': [0, 13311], 'M': [0, 9215]}
    assert observed['coverage'] == 'observed_D_M_grid'
    assert observed['control_profile'] == 'device_range_grid'
    assert observed['selection_status'] == 'not_observed_in_msaa'
    assert observed['raw_fields'] == {
        'D': [dict(grid_role='allocation', native_row=3, native_column=5, text='6656', state=0),
              dict(grid_role='allocation', native_row=3, native_column=6, text='13311', state=0)],
        'M': [dict(grid_role='allocation', native_row=10, native_column=5, text='4608', state=0),
              dict(grid_role='allocation', native_row=10, native_column=6, text='9215', state=0)]}
    assert all(cell['value'] == '' for row in grids['allocation'][2:] for cell in row['cells'][4:5])
    assert grids['allocation'][3]['cells'][4]['state'] == 2  # SELECTED, not CHECKED.
    assert grids == original == observed['grid_rows']
    observed['grid_rows']['allocation'][3]['cells'][4]['value'] = 'changed copy'
    assert grids == original
    schema = native_allocation_controls('device_range_grid')
    assert schema['runtime_control_ids'] == 'dynamic' and 'devices' not in schema
    assert schema['field_columns'] == dict(start=5, end=6, total=7)


_GRID_CONTROLLED_TRANSLATIONS = {
    'en': {
        '软元件': 'Device', '进制': 'Digit', '分配': 'Assign', '分配范围': 'Assignment Range',
        '合计点数': 'Total Points', '选择': 'Selection', '起始': 'Start', '结束': 'End',
        '字软元件': 'Word Device', '位软元件': 'Bit Device', '指针': 'Pointer',
        '定时器': 'Timer', '累积定时器': 'Retentive Timer', '计数器': 'Counter',
        'VAR用': 'VAR Range', 'VAR_RETAIN用': 'VAR_RETAIN Range', '锁存(1)': 'Latch(1)',
        'D锁存': 'D Latch', 'W锁存': 'W Latch', 'ZR锁存': 'ZR Latch', 'L锁存': 'L Latch',
        'B锁存': 'B Latch', 'T锁存': 'T Latch', 'ST锁存': 'ST Latch', 'C锁存': 'C Latch',
        'PLC参数': 'PLC Parameter', '软元件设置范围': 'Device Setting Range', '～': '--',
    },
    'ja': {
        '软元件': 'デバイス', '进制': '進', '分配': '割付', '分配范围': '割付範囲',
        '合计点数': '合計点数', '选择': '選択', '起始': '先頭', '结束': '最終',
        '字软元件': 'ワードデバイス', '位软元件': 'ビットデバイス', '指针': 'ポインタ',
        '定时器': 'タイマ', '累积定时器': '積算タイマ', '计数器': 'カウンタ',
        'VAR用': 'VAR用', 'VAR_RETAIN用': 'VAR_RETAIN用', '锁存(1)': 'ラッチ(1)',
        'D锁存': 'Dラッチ', 'W锁存': 'Wラッチ', 'ZR锁存': 'ZRラッチ', 'L锁存': 'Lラッチ',
        'B锁存': 'Bラッチ', 'T锁存': 'Tラッチ', 'ST锁存': 'STラッチ', 'C锁存': 'Cラッチ',
        'PLC参数': 'PCパラメータ', '软元件设置范围': 'デバイス設定範囲', '～': '～',
    },
}


@pytest.mark.parametrize('language', ('en', 'ja'))
def test_allocation_grid_controlled_resource_aligned_languages_preserve_original_captions(language):
    grids = _q_allocation_grids()
    captions = _GRID_CONTROLLED_TRANSLATIONS[language]
    for rows in grids.values():
        for row in rows:
            for cell in row['cells']:
                for field in ('name', 'value'):
                    cell[field] = captions.get(cell[field], cell[field])
    observed = read_native_allocation_grids(grids, native_ui_language=language)
    assert observed['ranges'] == {'D': [6656, 13311], 'M': [4608, 9215]}
    assert observed['grid_rows'] == grids
    with pytest.raises(GXWFormatError, match='localized headers'):
        read_native_allocation_grids(grids, native_ui_language='zh-CN')


@pytest.mark.parametrize('damage', (
    'missing_row', 'duplicate_row', 'reordered_row', 'bool_row', 'extra_row_field',
    'short_row', 'extra_cell_field', 'missing_cell_field', 'bool_state', 'negative_state',
    'oversized_state', 'nul_text', 'unpaired_surrogate', 'oversized_text', 'nonstring_text',
    'header_order', 'header_value', 'cell_header_language', 'missing_group', 'group_order',
    'group_extra_text', 'unknown_group', 'foreign_device', 'wrong_radix', 'retained_d',
    'duplicate_d', 'missing_m', 'ordinary_latch_owner', 'd_no_explicit_var',
    'total', 'reverse', 'nonnumeric', 'partial_interval', 'overflow',
    'missing_grid_role', 'extra_grid_role', 'right_row_count', 'right_row_number',
    'right_column_width', 'right_caption', 'right_header', 'right_separator',
    'right_reverse', 'outside_parameter_range',
))
def test_allocation_grid_rejects_corrupted_or_foreign_native_rows(damage):
    grids = _q_allocation_grids()
    left, right = grids['allocation'], grids['parameter']
    if damage == 'missing_row': left.pop(5)
    elif damage == 'duplicate_row': left[5]['native_row'] = 4
    elif damage == 'reordered_row': left[3], left[4] = left[4], left[3]
    elif damage == 'bool_row': left[1]['native_row'] = True
    elif damage == 'extra_row_field': left[3]['handle'] = 9001
    elif damage == 'short_row': left[3]['cells'].pop()
    elif damage == 'extra_cell_field': left[3]['cells'][5]['enabled'] = True
    elif damage == 'missing_cell_field': left[3]['cells'][5].pop('state')
    elif damage == 'bool_state': left[3]['cells'][5]['state'] = False
    elif damage == 'negative_state': left[3]['cells'][5]['state'] = -1
    elif damage == 'oversized_state': left[3]['cells'][5]['state'] = 2**32
    elif damage == 'nul_text': left[3]['cells'][5]['value'] = '6656\0'
    elif damage == 'unpaired_surrogate': left[4]['cells'][5]['value'] = '\ud800'
    elif damage == 'oversized_text': left[4]['cells'][5]['value'] = 'a' * 4097
    elif damage == 'nonstring_text': left[3]['cells'][5]['value'] = 6656
    elif damage == 'header_order': left[0]['cells'][2], left[0]['cells'][3] = left[0]['cells'][3], left[0]['cells'][2]
    elif damage == 'header_value': left[1]['cells'][5]['value'] = '结束'
    elif damage == 'cell_header_language': left[17]['cells'][5]['name'] = 'Assignment Range'
    elif damage == 'missing_group': left[22]['cells'][0]['value'] = ''
    elif damage == 'group_order': left[9]['cells'][0]['value'] = '指针'
    elif damage == 'group_extra_text': left[9]['cells'][2]['value'] = 'M'
    elif damage == 'unknown_group': left[9]['cells'][0]['value'] = 'Other Device'
    elif damage == 'foreign_device': left[4]['cells'][2]['value'] = 'ZR'
    elif damage == 'wrong_radix': left[4]['cells'][3]['value'] = '10'
    elif damage == 'retained_d': left[3]['cells'][0]['value'] = 'VAR_RETAIN用'
    elif damage == 'duplicate_d':
        left[4]['cells'][0]['value'], left[4]['cells'][2]['value'], left[4]['cells'][3]['value'] = 'VAR用', 'D', '10'
    elif damage == 'missing_m': left[10]['cells'][2]['value'] = 'B'
    elif damage == 'ordinary_latch_owner': left[3]['cells'][1]['value'] = '锁存(1)'
    elif damage == 'd_no_explicit_var': left[3]['cells'][0]['value'] = ''
    elif damage == 'total': left[3]['cells'][7]['value'] = '6655'
    elif damage == 'reverse': left[3]['cells'][5]['value'] = '13312'
    elif damage == 'nonnumeric': left[3]['cells'][5]['value'] = 'D6656'
    elif damage == 'partial_interval': left[3]['cells'][6]['value'] = ''
    elif damage == 'overflow': left[3]['cells'][6]['value'] = str(2**31)
    elif damage == 'missing_grid_role': grids.pop('parameter')
    elif damage == 'extra_grid_role': grids['other'] = deepcopy(right)
    elif damage == 'right_row_count': right.pop()
    elif damage == 'right_row_number': right[3]['native_row'] = 2
    elif damage == 'right_column_width': right[3]['cells'].pop()
    elif damage == 'right_caption': right[3]['cells'][0]['name'] = 'PLC Parameter'
    elif damage == 'right_header': right[1]['cells'][0]['value'] = 'PLC参数'
    elif damage == 'right_separator': right[3]['cells'][1]['value'] = '-'
    elif damage == 'right_reverse': right[3]['cells'][0]['value'] = '13312'
    else: right[3]['cells'][2]['value'] = '13310'
    with pytest.raises(GXWFormatError):
        read_native_allocation_grids(grids)


def test_allocation_grid_consistent_decimal_intervals_follow_independent_integer_oracle():
    from hypothesis import given, settings, strategies as st

    @st.composite
    def intervals(draw):
        result = {}
        for device, maximum in (('D', 13311), ('M', 9215)):
            first = draw(st.integers(min_value=0, max_value=maximum))
            result[device] = [first, draw(st.integers(min_value=first, max_value=maximum))]
        return result

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(intervals())
    def check(ranges):
        grids = _q_allocation_grids()
        for device, pair in ranges.items():
            _set_grid_interval(grids, device, pair)
        original = deepcopy(grids)
        assert read_native_allocation_grids(grids)['ranges'] == ranges
        assert grids == original
    check()
    grids = _q_allocation_grids()
    _set_grid_interval(grids, 'D', [13311, 13311])
    assert read_native_allocation_grids(grids)['ranges']['D'] == [13311, 13311]


@pytest.mark.parametrize('state', (1, 8, 16, 32, 64, 0x20000000, 0xffffffff))
def test_allocation_grid_state_projection_masks_only_selected_and_focused_bits(state):
    grids = _q_allocation_grids()
    grids['allocation'][3]['cells'][5]['state'] = state
    original = deepcopy(grids)
    projected = native_allocation_grid_state(grids)
    assert projected['allocation'][3]['cells'][5]['state'] == state & ~6
    assert projected['allocation'][3]['cells'][5]['value'] == '6656'
    assert native_allocation_grid_state(projected) == projected
    assert grids == original


def test_allocation_grid_state_and_observation_projection_retain_opaque_values_and_ignore_transient_selection():
    grids = _q_allocation_grids()
    grids['allocation'][4]['cells'][7]['value'] = 'opaque retained text'
    observed = read_native_allocation_grids(grids)
    observed.update(allocation_profile=project_allocation_profile('Q03UDV')['allocation_profile'],
                    window_handle=801, control_handles={'allocation': 9001})
    original = deepcopy(observed)
    repeated = deepcopy(observed)
    repeated['window_handle'], repeated['control_handles'] = 802, {'allocation': 9002}
    for rows in repeated['grid_rows'].values():
        for row in rows:
            for cell in row['cells']:
                cell['state'] ^= 6
    assert native_allocation_observation_state(observed) == native_allocation_observation_state(repeated)
    assert native_allocation_observation_state(observed)['grid_rows']['allocation'][4]['cells'][7]['value'] == 'opaque retained text'
    repeated['grid_rows']['allocation'][4]['cells'][7]['state'] ^= 0x10
    assert native_allocation_observation_state(observed) != native_allocation_observation_state(repeated)
    assert observed == original


@lru_cache(maxsize=1)
def _q_controlled_grid_context():
    # Check optional witness availability before importing the older offline
    # fixture owner, whose module also loads unrelated private evidence.
    grids = _q_allocation_grids()
    from tests.test_gxw_object_model import live_native_context_witness
    # This composes a source-only fixture and a frozen raw-grid witness. It is
    # a Core boundary test and does not prove native editing/save acceptance.
    raw = blank_fbd_baseline('Q03UDV')
    identity = {**_identity('Q03UDV'), 'native_ui_language': 'zh-CN', 'project_codepage': 936}
    current = live_native_context_witness(raw, identity=identity)
    current['configuration'] = dict(status='available', origin='active_native_configuration',
        cpu='Q03UDV', identity=deepcopy(identity),
        allocation_profile=project_allocation_profile('Q03UDV')['allocation_profile'],
        **read_native_allocation_grids(grids))
    return raw, current


def _grid_live_command(raw, edits):
    from gxw.fb_authoring import _context
    name, = [name for name in _context(raw)[1] if name.endswith('.prj')]
    return dict(operation='configure_project', source_name=name, options=dict(system_variable_ranges=edits))


def _reparse_grid_configuration(configuration):
    return {**configuration, **read_native_allocation_grids(configuration['grid_rows'],
        native_ui_language=configuration['identity']['native_ui_language'])}


@pytest.mark.parametrize('edits', ({'D': [6657, 13311]}, {'M': [4609, 9215]},
                                  {'D': [6000, 10000], 'M': [5000, 7000]}))
def test_allocation_grid_core_live_change_allows_only_requested_range_cells_and_total(edits):
    from gxw.live_context import prepare_live_command, verify_live_change
    raw, fixture = _q_controlled_grid_context()
    before = deepcopy(fixture)
    command = _grid_live_command(raw, edits)
    prepared = prepare_live_command(raw, before, command, device_profile='Q03UDV')
    assert prepared['plan']['control_profile'] == 'device_range_grid'
    assert prepared['plan']['allocation_profile'] == before['configuration']['allocation_profile']
    after = deepcopy(before)
    for device, pair in edits.items():
        _set_grid_interval(after['configuration']['grid_rows'], device, pair)
    after['configuration'] = _reparse_grid_configuration(after['configuration'])
    result = verify_live_change(before, after, command, prepared)
    assert result['status'] == 'matched' and result['unrelated_sources'] == 'matched'
    assert result['save_performed'] is False and before == fixture


@pytest.mark.parametrize('damage', (
    'checkbox_text', 'checkbox_checked', 'unavailable_bit', 'p_interval', 't_interval',
    'c_interval', 'other_device_text', 'parameter_interval', 'parameter_state',
    'field_binding', 'field_static_id', 'range_metadata', 'parameter_metadata',
    'total_mismatch', 'status', 'origin', 'cpu', 'coverage', 'allocation_profile', 'control_profile',
))
def test_allocation_grid_core_live_change_rejects_unrequested_raw_or_metadata_changes(damage):
    from gxw.live_context import prepare_live_command, verify_live_change
    raw, fixture = _q_controlled_grid_context()
    before = deepcopy(fixture)
    command = _grid_live_command(raw, {'D': [6657, 13311]})
    prepared = prepare_live_command(raw, before, command, device_profile='Q03UDV')
    after = deepcopy(before)
    configuration = after['configuration']
    _set_grid_interval(configuration['grid_rows'], 'D', [6657, 13311])
    left, right = configuration['grid_rows']['allocation'], configuration['grid_rows']['parameter']
    if damage == 'checkbox_text': left[3]['cells'][4]['value'] = '1'
    elif damage == 'checkbox_checked': left[3]['cells'][4]['state'] |= 0x10
    elif damage == 'unavailable_bit': left[4]['cells'][4]['state'] |= 1
    elif damage == 'p_interval': left[15]['cells'][5]['value'] = '2049'
    elif damage == 't_interval': left[17]['cells'][5]['value'] = '65'
    elif damage == 'c_interval': left[23]['cells'][5]['value'] = '513'
    elif damage == 'other_device_text': left[4]['cells'][7]['value'] = 'retained opaque change'
    elif damage == 'parameter_interval': right[3]['cells'][0]['value'] = '1'
    elif damage == 'parameter_state': right[4]['cells'][0]['state'] = 64
    elif damage == 'total_mismatch': left[3]['cells'][7]['value'] = '6656'
    try:
        after['configuration'] = configuration = _reparse_grid_configuration(configuration)
    except GXWFormatError:
        assert damage == 'total_mismatch'
        with pytest.raises(GXWFormatError):
            verify_live_change(before, after, command, prepared)
        return
    if damage == 'field_binding': configuration['raw_fields']['D'][0]['native_row'] = 4
    elif damage == 'field_static_id': configuration['raw_fields']['D'][0]['control_id'] = 14252
    elif damage == 'range_metadata': configuration['ranges']['D'][0] = 6658
    elif damage == 'parameter_metadata': configuration['parameter_ranges']['D'][0] = 1
    elif damage == 'status': configuration['status'] = 'partial'
    elif damage == 'origin': configuration['origin'] = 'saved_project'
    elif damage == 'cpu': configuration['cpu'] = 'Q04UDV'
    elif damage == 'coverage': configuration['coverage'] = 'observed_D_M_controls'
    elif damage == 'allocation_profile': configuration['allocation_profile'] = project_allocation_profile('L02')['allocation_profile']
    elif damage == 'control_profile': configuration['control_profile'] = 'device_range_fx'
    with pytest.raises(GXWFormatError):
        verify_live_change(before, after, command, prepared)


def test_allocation_grid_core_repeated_observation_ignores_focus_but_preserves_other_raw_state():
    from gxw.live_context import live_context_state, verify_live_observations
    _, fixture = _q_controlled_grid_context()
    before, after = deepcopy(fixture), deepcopy(fixture)
    for rows in after['configuration']['grid_rows'].values():
        for row in rows:
            for cell in row['cells']:
                cell['state'] ^= 6
    after['configuration'] = _reparse_grid_configuration(after['configuration'])
    assert live_context_state(before) == live_context_state(after)
    assert verify_live_observations(before, after)['status'] == 'matched'
    after['configuration']['grid_rows']['parameter'][4]['cells'][0]['state'] ^= 0x10
    after['configuration'] = _reparse_grid_configuration(after['configuration'])
    with pytest.raises(GXWFormatError, match='source changed'):
        verify_live_observations(before, after)


@pytest.mark.parametrize('state', (1, 64, 65))
def test_allocation_grid_core_plan_refuses_unavailable_or_readonly_target_fields(state):
    from gxw.live_context import prepare_live_command
    raw, fixture = _q_controlled_grid_context()
    before = deepcopy(fixture)
    before['configuration']['grid_rows']['allocation'][3]['cells'][5]['state'] = state
    before['configuration'] = _reparse_grid_configuration(before['configuration'])
    with pytest.raises(GXWFormatError, match='not enabled'):
        prepare_live_command(raw, before, _grid_live_command(raw, {'D': [6657, 13311]}), device_profile='Q03UDV')


def test_allocation_grid_core_plan_obeys_current_plc_parameter_interval_without_inventing_capacity():
    from gxw.live_context import prepare_live_command
    raw, fixture = _q_controlled_grid_context()
    before = deepcopy(fixture)
    before['configuration']['grid_rows']['parameter'][3]['cells'][0]['value'] = '6000'
    before['configuration'] = _reparse_grid_configuration(before['configuration'])
    # D5000 is within the independently reviewed Q03UDV capacity, but outside
    # this project's currently observed PLC-parameter setting interval.
    with pytest.raises(GXWFormatError, match='current PLC-parameter grid interval'):
        prepare_live_command(raw, before, _grid_live_command(raw, {'D': [5000, 10000]}), device_profile='Q03UDV')

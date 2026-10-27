"""Native snapshot checks and manual evidence retain their actual provenance."""

from tools.offline_inputs import private_path
import base64
from copy import deepcopy
import json
from functools import lru_cache

import pytest
from hypothesis import given, settings, strategies as st

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from application.fbd import FBDValidationError
from application.native_validation import NativeValidationService
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from gxw.object_model import generate_object_project
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
from plc.target_identity import Q_CPUS, PARAMETER_ONLY
from tests.test_gxw_object_model import two_timers
from tests.test_web_fbd import service, generate, accept
from tests.test_web_api import _app, _login, ORIGIN
from tests.test_gxw_compiler import archived_native_requests, native_request_semantics
from tests.native_fixtures import load_frozen_native_json


def _recorded_sfc_model(value):
    """Fill additive step metadata from the frozen native cells themselves."""
    model = deepcopy(value)
    graph = model.get('graph') or {}
    cells = {row['id']: row for row in graph.get('cells', [])}
    for symbol in (graph.get('topology') or {}).get('symbols', []):
        if symbol['kind'] == 'step' and 'initial' not in symbol:
            # Native initial-step tags are retained in each archived cell.
            symbol['initial'] = cells[symbol['cell']]['type'] in {6, 10, 26}
    return model


def _recorded_sfc_document(value):
    if isinstance(value, list):
        return [_recorded_sfc_document(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: _recorded_sfc_document(item) for key, item in value.items()}
    graph = value.get('graph')
    if isinstance(graph, dict) and 'cells' in graph and 'topology' in graph:
        return _recorded_sfc_model(value)
    return value


def _recorded_complete_ql_sfc_document(value):
    """Add the known references in the complete Q/L numbering witnesses.

    These archives independently enumerate every step/action and transition,
    and their verifiers check that all stored children are referenced. Their
    old projection predates the CPU-aware single-number action binding.
    Rejected and incomplete selections retain their historical limitations.
    """
    value = _recorded_sfc_document(value)
    if (not isinstance(value, dict) or not str(value.get('cpu', '')).startswith(('Q', 'L'))
            or value.get('current_source_check') != 'established'):
        return value
    for source in value.get('source_bodies', {}).get('sources', []):
        children = source.get('sfc_children')
        if not children:
            continue
        scope = children['source_scope']
        if scope['status'] != 'unresolved':
            continue
        assert scope['children'] == []
        assert scope['reason'] in {'graph includes an implicit action reference',
                                   'complete graph references are unavailable'}
        assert children['status'] == 'current' and not children['missing']
        assert all(child['status'] == 'current' and child['role'] == 'unresolved'
                   for child in children['children'])
        for child in children['children']:
            child['role'] = 'referenced'
        children['source_scope'] = dict(status='source-projected', native_check_promoted=False,
            children=[{key: child[key] for key in ('type', 'name', 'role')} for child in children['children']])
    return value


def _sfc_source_model(value):
    """Compare source contents independently of native cache size and grid provenance."""
    def semantic(item):
        if isinstance(item, list):
            return [semantic(row) for row in item]
        if isinstance(item, dict):
            return {key: semantic(row) for key, row in item.items()
                    if key not in {'source_offset', 'record_offset', 'number_offset'}}
        return item
    result = semantic(value)
    # Historical unprofiled projections split an instruction into opaque
    # tokens. Compare their complete stored bytes, then test current decoding
    # through the ordinary source/tool assertions below.
    result.pop('lexical_profile', None)
    for child in result.get('children', []):
        child['records'] = ''.join(row['raw_hex'] for row in child['records'])
    graph = result['graph']
    for key in ('position_resolution', 'width', 'height', 'cells', 'topology', 'native_version',
                'source_graph', 'connections', 'write_symbols', 'edit_profile'):
        graph.pop(key, None)
    return result


def test_native_inspection_queries_bind_current_declarations_before_native_io():
    from gxw.native_diagnostics import native_inspection_queries
    raw = generate_object_project(two_timers()).data
    queries = {'assignments': [{'source': '1', 'name': 'TIMER_A'}], 'locations': [], 'saved_code': True}
    assert native_inspection_queries(raw, queries) == queries
    assert native_inspection_queries(raw, queries) is not queries
    for bad in ({'source': '1', 'name': 'old_instance'}, {'source': 'other', 'name': 'TIMER_A'},
                {'source': '1', 'name': 'TIMER_A', 'slot': 56}):
        with pytest.raises(ValueError):
            native_inspection_queries(raw, {**queries, 'assignments': [bad]})
    for bad in (True, 1.5, -1, 2147483648):
        with pytest.raises(ValueError):
            native_inspection_queries(raw, {**queries, 'locations': [{'resource': 'Sample', 'step': bad}]})
    with pytest.raises(ValueError, match='duplicate'):
        native_inspection_queries(raw, {**queries, 'assignments': queries['assignments'] * 2})


@pytest.mark.parametrize('operation', ['validate', 'create'])
def test_q_remote_io_parameter_project_reads_and_names_the_program_operation_limit(operation):
    from zipfile import ZipFile
    from gxw.offline_project import read_offline_project
    from gxw.diagnostic_sources import native_validation_source_plan
    from gxw.native_write import native_source_creation_plan
    from gxw.models import GXWFormatError
    manifest = json.loads(private_path('research/results/gxworks2-native-cpu-send-20261011.json').read_text('utf-8'))
    case = next(row for row in manifest['cases'] if row['cpu'] in Q_CPUS and not row['program_supported'])
    with ZipFile(private_path(manifest['evidence'])) as archive:
        parent = case['raw'].rsplit('/', 1)[0] + '/'
        name = next(name for name in archive.namelist() if name.startswith(parent) and name.endswith('.gxw'))
        raw = archive.read(name)
    project = read_offline_project(raw)
    # The real project stores a generic label, not the selected menu spelling.
    assert case['cpu'] == 'QJ72LP25/QJ72BR15(RemoteI/O)'
    assert project.context['cpu'] == 'RemoteI/O' and project.sources() == []
    with pytest.raises(GXWFormatError) as error:
        if operation == 'validate':
            native_validation_source_plan(raw)
        else:
            native_source_creation_plan(raw, 'ForbiddenProgram')
    assert project.context['cpu'] in str(error.value)
    assert 'parameter-only' in str(error.value)


def test_native_inspection_local_query_does_not_require_unqueried_global_declarations():
    from gxw.container_writer import replace_project_stream, validate_cfb_streams
    from gxw.native_diagnostics import native_inspection_queries
    from gxw.project_metadata import logical_mapping
    raw = generate_object_project(two_timers()).data
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = replace_project_stream(outer['_hdb'], mapping['Global1.gh'], b'unknown global record')[0]
    raw = replace_project_stream(raw, '_hdb', nested)[0]
    queries = {'assignments': [{'source': '1', 'name': 'TIMER_A'}], 'locations': [], 'saved_code': False}
    assert native_inspection_queries(raw, queries) == queries
    # This only validates the local query; native compilation still decides
    # whether it accepts the project containing the unknown global record.


@settings(max_examples=40, deadline=None, derandomize=True)
@given(st.integers(min_value=0, max_value=2), st.integers(min_value=0, max_value=255), st.booleans())
def test_native_saved_code_observation_uses_independent_bytes_and_never_attests_execution(channel, value, current):
    from gxw.native_diagnostics import project_native_inspection
    raw = generate_object_project(two_timers()).data
    saved = [bytes([17]), bytes([29]), bytes([43])]
    generated = list(saved); generated[channel] = bytes([value])
    events, snapshots = [], {}
    for operation, prefix, values in (('SavedResourceCode', 'saved', saved), ('Resource', 'new', generated)):
        channels = []
        for i, data in enumerate(values):
            filename = f'{prefix}-{i}.bin'; snapshots[filename] = data
            channels.append({'channel': i, 'bytes': 1, 'file': filename})
        events.append({'operation': operation, 'resource' if prefix == 'saved' else 'name': 'Sample', 'channels': channels})
    # A deliberately false collector comparison must never override the saved getter bytes.
    events.append({'operation': 'SavedVersusGeneratedCode', 'resource': 'Sample', 'channel_matches': [True, True, True]})
    validation = {'compilation': {'source_context': {'status': 'current' if current else 'unresolved'}}}
    result = project_native_inspection(raw, {'validation': {'events': events}}, snapshots, validation,
                                      {'assignments': [], 'locations': [], 'saved_code': True})
    assert result['execution_correspondence'] == 'unverified'
    if current:
        assert result['saved_code']['status'] == ('matches' if value == saved[channel][0] else 'differs')
        assert result['saved_code']['resources'][0]['channels'][channel]['matches'] == (value == saved[channel][0])
    else:
        assert result['saved_code']['status'] == 'unresolved'
    snapshots.pop('saved-0.bin')
    missing = project_native_inspection(raw, {'validation': {'events': events}}, snapshots, validation,
                                       {'assignments': [], 'locations': [], 'saved_code': True})
    assert missing['saved_code']['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['none', 'source_context', 'other_query', 'other_owner', 'wrong_count', 'failed_getter', 'boolean_code', 'duplicate'])
def test_native_assignment_projection_keeps_missing_or_unbound_getters_unresolved(damage):
    from gxw.native_diagnostics import project_native_inspection
    raw = generate_object_project(two_timers()).data
    query = {'source': '1', 'name': 'TIMER_A'}
    event = {'operation': 'NativeDeviceAssignment', 'query': query, 'hresult': 0, 'code': 0, 'count': 1,
             'assignments': [{'name': 'TIMER_A.ET', 'source': '1', 'type': 'INT', 'count': 1, 'address': 'D512'}]}
    events = [event]; status = 'current'
    if damage == 'source_context': status = 'unresolved'
    elif damage == 'other_query': event['query'] = {**query, 'name': 'TIMER_B'}
    elif damage == 'other_owner': event['assignments'][0]['source'] = 'other'
    elif damage == 'wrong_count': event['count'] = 0
    elif damage == 'failed_getter': event['code'] = -1
    elif damage == 'boolean_code': event['code'] = False
    elif damage == 'duplicate': events.append(deepcopy(event))
    result = project_native_inspection(raw, {'validation': {'events': events}}, {},
        {'compilation': {'source_context': {'status': status}}}, {'assignments': [query], 'locations': [], 'saved_code': False})
    assignment, = result['assignments']
    assert assignment['status'] == ('resolved' if damage == 'none' else 'unresolved')
    if damage != 'none': assert assignment['assignments'] == []


@lru_cache(maxsize=1)
def _native_assignment_base_source():
    return generate_object_project(two_timers()).data


def _native_assignment_array_source(bounds, scope='local'):
    from gxw.container_writer import replace_project_stream, validate_cfb_streams
    from gxw.declarations import edit_declarations, parse_declarations, serialize_declarations
    from gxw.native_diagnostics import _native_validation_sources
    from gxw.project_metadata import logical_mapping
    raw = _native_assignment_base_source()
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    if scope == 'global':
        nested = validate_cfb_streams(outer['_hdb'])
        document = parse_declarations(nested[mapping['Global1.gh']], logical_name='Global1.gh')
    else:
        _, programs = _native_validation_sources(raw)
        document = programs[0]['declarations']
    array_type = 'ARRAY [' + ','.join(f'{low}..{high}' for low, high in bounds) + '] OF WORD'
    document = edit_declarations(document, upserts=[{'name': 'FirstScanWords', 'data_type': array_type}])
    nested, _ = replace_project_stream(outer['_hdb'], mapping[document.logical_name], serialize_declarations(document))
    return replace_project_stream(raw, '_hdb', nested)[0]


def _array_boundary_cases(bounds):
    """Keep every distinct input/expectation of the original Cartesian checks."""
    from itertools import product
    cases = {}
    for indices in product(*[(low, high) for low, high in bounds]):
        cases[tuple(indices), True] = None
        for dimension, (low, high) in enumerate(bounds):
            for outside in (low - 1, high + 1):
                changed = list(indices)
                changed[dimension] = outside
                cases[tuple(changed), False] = None
    return tuple(cases)


@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.lists(st.tuples(st.integers(-8, 8), st.integers(0, 8)), min_size=1, max_size=3))
def test_array_case_deduplication_preserves_every_original_input_and_expectation(dimensions):
    from itertools import product
    bounds = [(low, low + length) for low, length in dimensions]
    original = []
    # Keep the previous nested enumeration as an independent oracle.
    for point in product(*[(low, high) for low, high in bounds]):
        original.append((tuple(point), True))
        for dimension, (low, high) in enumerate(bounds):
            for outside in (low - 1, high + 1):
                changed = list(point)
                changed[dimension] = outside
                original.append((tuple(changed), False))
    actual = _array_boundary_cases(bounds)
    assert set(actual) == set(original)
    assert len(actual) == len(set(actual))
    for point, inside in actual:
        assert inside == all(low <= value <= high for value, (low, high) in zip(point, bounds))


@pytest.mark.parametrize('bounds,expected', [([(0, 2)] * 3, 32), ([(0, 0)] * 3, 7),
                                           ([(0, 0), (1, 3), (-2, 1)], 20)])
def test_array_case_deduplication_reduces_projection_calls_without_dropping_unique_cases(bounds, expected):
    assert len(_array_boundary_cases(bounds)) == expected


@pytest.mark.parametrize('scope', ['local', 'global'])
@pytest.mark.parametrize('bounds,expected', [([(0, 2)] * 3, 32), ([(0, 0)] * 3, 7),
                                           ([(0, 0), (1, 3), (-2, 1)], 20)])
def test_array_boundary_checks_count_actual_diagnostic_calls(monkeypatch, scope, bounds, expected):
    import gxw.native_diagnostics as diagnostics
    original = diagnostics.project_native_inspection
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(diagnostics, 'project_native_inspection', counted)
    # Execute the actual assertion body with an independent fixed boundary table.
    # The diagnostic still parses the source and verifies every returned result.
    test_native_array_assignments_follow_saved_bounds_and_reject_neighbor_indices.hypothesis.inner_test(
        scope=scope, dimensions=[(low, high - low) for low, high in bounds], current=True)
    assert len(calls) == expected


@pytest.mark.parametrize('scope', ['local', 'global'])
@settings(max_examples=35, deadline=None, derandomize=True)
@given(st.lists(st.tuples(st.integers(-8, 8), st.integers(0, 8)), min_size=1, max_size=3), st.booleans())
def test_native_array_assignments_follow_saved_bounds_and_reject_neighbor_indices(scope, dimensions, current):
    from gxw.native_diagnostics import project_native_inspection
    bounds = [(low, low + length) for low, length in dimensions]
    raw = _native_assignment_array_source(bounds, scope)
    query = {'source': '1', 'name': 'FirstScanWords'} if scope == 'local' else {
        'scope': 'global', 'source': 'Global1.gh', 'name': 'FirstScanWords'}
    requested = {'assignments': [query], 'locations': [], 'saved_code': False}
    validation = {'compilation': {'source_context': {'status': 'current' if current else 'unresolved'}}}
    # Cartesian boundary points come from the declaration, independently of
    # the projection; move one coordinate outside its bounds for each control.
    for indices, inside in _array_boundary_cases(bounds):
        value = {'name': 'FirstScanWords[' + ','.join(map(str, indices)) + ']',
                 'address': 'D13281', 'source': '1' if scope == 'local' else '', 'type': 'WORD', 'count': 1}
        event = {'operation': 'NativeDeviceAssignment', 'query': query, 'hresult': 0, 'code': 0,
                 'count': 1, 'assignments': [value]}
        result = project_native_inspection(raw, {'validation': {'events': [event]}}, {}, validation, requested)
        assert result['assignments'][0]['status'] == ('resolved' if current and inside else 'unresolved')
        if current and inside:
            assert result['assignments'][0]['assignments'] == [value]
        elif not inside:
            assert result['assignments'][0]['assignments'] == []


@pytest.mark.parametrize('name', ['FirstScanWords[0]', 'FirstScanWords[1]', 'FirstScanWords[2]',
    'FirstScanWords[-1]', 'FirstScanWords[0,0]', 'FirstScanWords[0].other', 'FirstScanWords_extra[0]',
    'FirstScanWords[false]', 'FirstScanWords[0]tail', 'TIMER_A[0]',
    pytest.param('FirstScanWords[' + '9' * 5000 + ']', id='oversized_index')])
def test_native_array_assignment_names_retain_exact_root_and_rank(name):
    from gxw.native_diagnostics import project_native_inspection
    raw = _native_assignment_array_source([(0, 1)])
    query = {'source': '1', 'name': 'FirstScanWords' if name.startswith('FirstScanWords') else 'TIMER_A'}
    value = {'name': name, 'address': 'D13281', 'source': '1', 'type': 'WORD', 'count': 1}
    event = {'operation': 'NativeDeviceAssignment', 'query': query, 'hresult': 0, 'code': 0,
             'count': 1, 'assignments': [value]}
    result = project_native_inspection(raw, {'validation': {'events': [event]}}, {},
        {'compilation': {'source_context': {'status': 'current'}}},
        {'assignments': [query], 'locations': [], 'saved_code': False})
    assert result['assignments'][0]['status'] == ('resolved' if name in ('FirstScanWords[0]', 'FirstScanWords[1]') else 'unresolved')


@settings(max_examples=24, deadline=None, derandomize=True)
@given(st.booleans(), st.booleans(), st.integers(min_value=0, max_value=8191), st.booleans())
def test_global_native_assignment_never_uses_same_named_local(current, local_first, address, global_assigned):
    from gxw.native_diagnostics import native_inspection_queries, project_native_inspection
    from tests.test_gxw_object_model import source_binding_shadow_baseline
    raw = source_binding_shadow_baseline()
    queries = [{'source': '1', 'name': 'TIMER_A'},
               {'scope': 'global', 'source': 'Global1.gh', 'name': 'TIMER_A'}]
    requested = {'assignments': queries, 'locations': [], 'saved_code': False}
    before = deepcopy(requested)
    assert native_inspection_queries(raw, requested) == before
    local = {'name': 'TIMER_A.ET', 'source': '1', 'type': 'INT', 'count': 1, 'address': 'D512'}
    global_ = {'name': 'TIMER_A', 'source': '', 'type': 'BOOL', 'count': 1, 'address': f'M{address}'}
    rows = [local, global_] if local_first else [global_, local]
    if not global_assigned:
        rows = [local]
    events = [{'operation': 'NativeDeviceAssignment', 'query': queries[0], 'hresult': 0, 'code': 0,
               'count': 1, 'assignments': [local]},
              {'operation': 'NativeDeviceAssignment', 'query': queries[1], 'hresult': 0, 'code': 0,
               'count': len(rows), 'assignments': rows}]
    result = project_native_inspection(raw, {'validation': {'events': events}}, {},
        {'compilation': {'source_context': {'status': 'current' if current else 'unresolved'}}}, requested)
    assert requested == before and events[1]['assignments'] == rows
    assert result['execution_correspondence'] == 'unverified'
    assert [r['assignments'] for r in result['assignments']] == (
        [[local], [global_] if global_assigned else []] if current else [[], []])
    assert result['assignments'][1]['status'] == (
        ('resolved' if global_assigned else 'not_assigned') if current else 'unresolved')


@pytest.mark.parametrize('query', [
    {'scope': 'global', 'source': '1', 'name': 'FirstScanWords'},
    {'scope': 'global', 'source': 'Global1.gh', 'name': 'missing'},
    {'scope': 'global', 'source': 'missing.gh', 'name': 'FirstScanWords'},
    {'scope': 'global', 'source': 'Global1.gh', 'name': 'TIMER_A'},
    {'scope': 'library', 'source': 'Global1.gh', 'name': 'FirstScanWords'},
    {'scope': False, 'source': 'Global1.gh', 'name': 'FirstScanWords'},
])
def test_native_global_queries_require_the_current_global_declaration(query):
    from gxw.native_diagnostics import native_inspection_queries
    raw = _native_assignment_array_source([(0, 1)], 'global')
    with pytest.raises(ValueError):
        native_inspection_queries(raw, {'assignments': [query], 'locations': [], 'saved_code': False})


@pytest.mark.parametrize('duplicate_kind', ['explicit_local', 'global_other_table'])
def test_native_assignment_rejects_ambiguous_or_duplicate_scope(duplicate_kind, monkeypatch):
    from gxw.native_diagnostics import _native_assignment_sources, native_inspection_queries
    raw = generate_object_project(two_timers()).data
    queries = [{'source': '1', 'name': 'TIMER_A'}, {'scope': 'local', 'source': '1', 'name': 'TIMER_A'}]
    if duplicate_kind == 'global_other_table':
        from dataclasses import replace
        raw = _native_assignment_array_source([(0, 1)], 'global')
        local, global_ = _native_assignment_sources(raw)
        document = global_['Global1.gh']
        # A second global declaration stream with the same symbol is ambiguous
        # even when the request identifies one of the two tables.
        monkeypatch.setattr('gxw.diagnostic_sources._native_assignment_sources',
            lambda _raw, **_options: (local, {**global_, 'Other.gh': replace(document, logical_name='Other.gh')}))
        queries = [{'scope': 'global', 'source': 'Global1.gh', 'name': 'FirstScanWords'}]
    with pytest.raises(ValueError, match='duplicate|ambiguous'):
        native_inspection_queries(raw, {'assignments': queries, 'locations': [], 'saved_code': False})


@settings(max_examples=30, deadline=None, derandomize=True)
@given(st.lists(st.tuples(st.text(alphabet='ABC一二三_', min_size=1, max_size=8),
                          st.integers(min_value=0, max_value=2147483647)), min_size=0, max_size=20, unique=True))
def test_workspace_wire_preserves_bounded_native_location_queries_without_abi_fields(locations):
    from gxworks2.workspace_protocol import workspace_business_request
    raw = generate_object_project(two_timers()).data
    from gxw.native_diagnostics import native_validation_source_plan
    source = native_validation_source_plan(raw)
    source['inspection'] = {'assignments': [], 'locations': [{'resource': r, 'step': s} for r, s in locations], 'saved_code': True}
    before = deepcopy(source)
    wire = workspace_business_request({'operation': 'validate_project', 'protocol_version': 1,
        'installation': 'installation', 'directory': 'scratch', 'owner_token': 'owned', 'source': source})
    assert source == before and wire['change']['inspection'] == source['inspection']
    assert all(key not in json.dumps(wire) for key in ('vtable', 'lexical_reader', 'native_cpu', 'program_kind'))


def test_workspace_compile_save_uses_explicit_business_action_without_editing_source():
    from gxworks2.workspace_protocol import workspace_business_request
    from gxw.native_diagnostics import native_validation_source_plan
    raw=generate_object_project(two_timers()).data
    source=native_validation_source_plan(raw)
    original=deepcopy(source)
    wire=workspace_business_request(dict(operation='compile_save_project',protocol_version=1,
        installation='installation',directory='scratch',owner_token='owned',source=source))
    assert wire['action']=='build_and_save_project' and source==original
    assert all(key not in json.dumps(wire) for key in ('vtable','lexical_reader','native_cpu','program_kind'))


@pytest.mark.parametrize('native_kind,language', [(1, 'ladder'), (193, 'st'), (208, 'fbd'), (240, 'sfc')])
def test_workspace_wire_carries_business_objects_without_vendor_profiles(native_kind, language):
    from gxworks2.workspace_protocol import workspace_business_request
    original = {'operation': 'validate_project', 'protocol_version': 1,
        'installation': 'installation', 'directory': 'scratch', 'owner_token': 'owned',
        'source': {'cpu': 'Q03UDV', 'codepage': 936,
            'programs': [{'name': 'Program', 'program_kind': native_kind, 'body': 'AA=='}],
            'libraries': [], 'lexical_reader': {'module': 'ECCodeGenerator2.dll', 'native_cpu': 209,
                'version': '15.41', 'native_versions': [25]},
            'compilation_method': 'caller-selected', 'graph_profile': {'family': 'caller-selected'}}}
    before = deepcopy(original)
    wire = workspace_business_request(original)
    assert original == before
    assert wire['project'] == {'cpu': 'Q03UDV', 'encoding': 936}
    assert wire['change']['programs'] == [{'name': 'Program', 'language': language, 'body': 'AA=='}]
    assert wire['action'] == 'check_project' and wire['schema_version'] == 3
    encoded = json.dumps(wire)
    for forbidden in ('program_kind', 'pou_type', 'lexical_reader', 'graph_profile',
                      'compilation_method', 'ECCodeGenerator', 'native_cpu', '15.41'):
        assert forbidden not in encoded


@pytest.mark.parametrize('child_type', [True, 66.0, 0, 67])
def test_workspace_business_boundary_rejects_noncanonical_sequence_child(child_type):
    from gxworks2.workspace_protocol import workspace_business_request
    source = {'cpu': 'FX3U/FX3UC', 'codepage': 936, 'program_kind': 240,
              'children': [{'type': child_type, 'name': 'Condition', 'body': 'AA=='}]}
    with pytest.raises(ValueError, match='sequence child'):
        workspace_business_request({'operation': 'save_source', 'protocol_version': 1,
            'installation': 'installation', 'directory': 'scratch', 'owner_token': 'owned', 'source': source})


def test_workspace_business_boundary_keeps_pointer_range_and_child_semantics():
    from gxworks2.workspace_protocol import workspace_business_request
    source = {'cpu': 'L02', 'codepage': 936, 'program_kind': 240,
        'system_variable_initialization': {'offset': 68, 'before': [-1, -1], 'after': [2048, 4095]},
        'children': [{'type': 66, 'name': 'Condition', 'body': 'AA=='},
                     {'type': 68, 'name': 'Action', 'body': 'AQ=='}]}
    wire = workspace_business_request({'operation': 'save_source', 'protocol_version': 1,
        'installation': 'installation', 'directory': 'scratch', 'owner_token': 'owned', 'source': source})
    assert wire['change']['pointer_range'] == {'before': [-1, -1], 'after': [2048, 4095]}
    assert [row['kind'] for row in wire['change']['children']] == ['transition', 'action']
    assert 'offset' not in json.dumps(wire) and 'system_variable_initialization' not in wire['change']


@pytest.mark.parametrize('malformed', ['old_protocol', 'previous_wire_protocol', 'vendor_root', 'vendor_child', 'vendor_library',
                                     'boolean_protocol', 'float_protocol', 'float_encoding', 'unknown_sfc_cpu'])
def test_built_workspace_boundary_rejects_vendor_plans_before_native_io(tmp_path, malformed):
    import os
    import subprocess
    executable = os.environ.get('GX_WORKSPACE_ADAPTER_TEST_EXE')
    if not executable:
        pytest.skip('Set GX_WORKSPACE_ADAPTER_TEST_EXE to the isolated freshly built adapter')
    payload = {'schema_version': 3, 'action': 'read_sequence_chart',
        'workspace': {'installation': str(tmp_path), 'directory': str(tmp_path), 'owner_token': 'protocol-only'},
        'project': {'cpu': 'FX3U/FX3UC', 'encoding': 936},
        'change': {'language': 'sfc', 'programs': [], 'libraries': []}}
    if malformed == 'old_protocol':
        payload = {'operation': 'read_sfc_graph', 'protocol_version': 1, 'installation': str(tmp_path),
                   'directory': str(tmp_path), 'owner_token': 'protocol-only', 'source': {}}
    elif malformed == 'previous_wire_protocol':
        payload['schema_version'] = 2
    elif malformed == 'vendor_root':
        payload['change']['lexical_reader'] = {'module': 'arbitrary.dll', 'native_cpu': 209}
    elif malformed == 'vendor_child':
        payload['change']['children'] = [{'type': 66}]
    elif malformed == 'vendor_library':
        payload['change']['libraries'] = [{'name': 'FB', 'program_kind': 208}]
    elif malformed == 'boolean_protocol':
        payload['schema_version'] = True
    elif malformed == 'float_protocol':
        payload['schema_version'] = 3.0
    elif malformed == 'float_encoding':
        payload['project']['encoding'] = 936.0
    else:
        payload['project']['cpu'] = 'FX5U'
    process = subprocess.run([executable], input=json.dumps(payload), text=True, encoding='utf-8-sig',
        cwd=tmp_path, capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = json.loads(process.stdout)
    assert process.returncode != 0 and result['status'] == 'failed'
    assert result['protocol_version'] == 3 and result['calls'] == []
    assert result['message'] in {'invalid_request_fields', 'vendor_plan_not_accepted',
        'vendor_child_type_not_accepted', 'invalid_native_integer', 'unobserved_sfc_graph_cpu_profile',
        'incompatible_workspace_protocol'}
    assert list(tmp_path.iterdir()) == []


def test_workspace_transport_sends_business_protocol_and_preserves_private_evidence(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    requests = []
    class Process:
        pid = 987654
        returncode = 0
        def communicate(self, message, timeout):
            requests.append(json.loads(message))
            return json.dumps({'protocol_version': 3, 'status': 'observed',
                'calls': [{'operation': 'PrivateNativeRead', 'hresult': 0, 'code': 0}]}), ''
        def poll(self): return 0
    monkeypatch.setattr(adapter.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(adapter, '_clean_native_temp', lambda pid, token: None)
    request = {'operation': 'read_sfc_graph', 'protocol_version': 1, 'installation': 'installed',
        'directory': str(tmp_path), 'owner_token': 'owned',
        'source': {'cpu': 'Q03UDV', 'codepage': 936, 'program_kind': 240, 'graph_profile': {'family': 'q-l'}}}
    code, evidence = adapter._run_native_adapter('isolated-adapter', request, tmp_path, 10, adapter.WorkspaceSaveError)
    assert code == 0 and evidence['protocol_version'] == 1
    assert evidence['calls'][0]['operation'] == 'PrivateNativeRead'
    assert len(requests) == 1 and requests[0]['schema_version'] == 3
    assert requests[0]['action'] == 'read_sequence_chart'
    assert requests[0]['change'] == {'language': 'sfc'}


@pytest.mark.parametrize('version', [None, True, 1, 2, 3.0, 4])
def test_workspace_transport_rejects_previous_and_noncanonical_wire_versions(tmp_path, monkeypatch, version):
    import gxworks2.workspace_adapter as adapter
    class Process:
        pid = 987654
        returncode = 0
        def communicate(self, message, timeout):
            return json.dumps({'protocol_version': version, 'status': 'observed', 'calls': []}), ''
        def poll(self): return 0
    monkeypatch.setattr(adapter.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(adapter, '_clean_native_temp', lambda pid, token: None)
    request = {'operation': 'read_sfc_graph', 'protocol_version': 1, 'installation': 'installed',
        'directory': str(tmp_path), 'owner_token': 'owned',
        'source': {'cpu': 'Q03UDV', 'codepage': 936, 'program_kind': 240}}
    with pytest.raises(adapter.WorkspaceSaveError, match='协议版本不匹配'):
        adapter._run_native_adapter('isolated-adapter', request, tmp_path, 10, adapter.WorkspaceSaveError)


def test_web_and_mcp_public_projections_keep_diagnostics_without_native_implementation():
    from application.projects import public
    from agent_runtime.runtime import _public_value
    private = {'public_check': {'status': 'rejected', 'completed': True},
        'current_source_check': 'established', 'cpu': 'Q03UDV',
        'diagnostics': [{'message': 'C9521', 'program': 'Program', 'network': 2, 'node': 3, 'offset': 7}],
        'native_workspace': {'calls': [{'operation': 'Workspace.SetPOUBodyData'}]},
        'native_code_lexical_reads': {'module': 'ECCodeGenerator2.dll', 'rva': 48879},
        'source_references': {'compiled_interval': [1, 2]},
        'native': {'module_version': '15.41', 'native_cpu': 209, 'vtable': 4}}
    before = deepcopy(private)
    for projection in (public, _public_value):
        result = projection(private)
        assert result['public_check'] == private['public_check']
        assert result['current_source_check'] == 'established' and result['diagnostics'] == private['diagnostics']
        assert result['cpu'] == 'Q03UDV' and result['native'] == {}
        for secret in ('SetPOUBodyData', 'ECCodeGenerator2', '48879', 'compiled_interval', '15.41', 'vtable'):
            assert secret not in json.dumps(result)
    assert private == before


@lru_cache(maxsize=1)
def sfc_cpu_menu_witness():
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_cpu_menus import ARCHIVE
    with ZipFile(ARCHIVE) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@lru_cache(maxsize=1)
def q_native_sfc_programs():
    from zipfile import ZipFile
    with ZipFile(private_path('research/' + 'evidence/gxw-sfc-cpu-menus-20261014.zip')) as archive:
        origins = json.loads(archive.read('menu-cases.json'))['cases']
        result = {row['menu']: archive.read(row['check'] + '/input.gxw') for row in origins
                  if row['menu'] in Q_CPUS and row['menu'] not in PARAMETER_ONLY}
    assert set(result) == set(Q_CPUS) - PARAMETER_ONLY
    return result


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
def test_each_q_sfc_scope_inventory_keeps_its_native_graph_and_declaration_owner(cpu):
    from gxw.native_write import _native_project_source_inventory
    from gxw.sfc_pou import read_sfc_project_context
    raw = q_native_sfc_programs()[cpu]
    context = read_sfc_project_context(raw)
    project, sources = _native_project_source_inventory(raw)
    assert project['cpu'] == cpu and len(sources) == 1
    source, = sources
    graph = context.source.layout['graph']
    document, = [row for row in context.declarations.values() if row.scope == 'local']
    assert source['program_kind'] == 240 and source['name'] == document.owner_name
    assert base64.b64decode(source['body']) == context.source.raw[graph['offset']:graph['offset'] + graph['size']]


@lru_cache(maxsize=1)
def q_mixed_sfc_native_rejection():
    root = private_path('research/experiments/q-gxw-compatibility-20261026')
    recorded = json.loads((root / 'sfc-mixed-first-negative-sfc-evidence.json').read_text(encoding='utf-8'))
    return ((root / 'sfc-mixed-first-negative-sfc.gxw').read_bytes(), recorded['observation'],
            {name: base64.b64decode(data) for name, data in recorded['snapshots'].items()})


@pytest.mark.parametrize('damage', ['none', 'missing-error', 'wrong-count', 'reader-version',
                                   'missing-owned-input', 'wrong-build-method', 'source-snapshot'])
def test_mixed_q_sfc_counts_original_child_errors_and_requires_complete_current_inputs(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observed, snapshots = q_mixed_sfc_native_rejection()
    observed, snapshots = deepcopy(observed), dict(snapshots)
    events = observed['validation']['events']
    batch = next(row for row in events if row.get('operation') == 'ProgramCheckOriginalReports'
                 and any(report.get('kind') == 2 for report in row['reports']))
    target, poll = batch['target'], batch['poll']
    if damage == 'missing-error':
        batch['reports'] = [row for row in batch['reports'] if row.get('kind') != 2]
        for index, report in enumerate(batch['reports']):
            report['report_index'] = index
        for event in events:
            if event.get('operation') == 'ProgramCheckOriginalProgress' and event.get('target') == target and event.get('poll') == poll:
                event['count'] = len(batch['reports'])
    elif damage == 'wrong-count':
        next(row for row in batch['reports'] if row['code'] == 0x23)['arguments'][0]['text'] = '2'
    elif damage == 'reader-version':
        for event in events:
            if event.get('operation') == 'OwnedCheckInput' and event.get('target') == target:
                for row in event['rows']:
                    row['framing']['reader_version'] = '15.51'
    elif damage == 'missing-owned-input':
        events[:] = [row for row in events if row.get('operation') != 'OwnedCheckInput']
    elif damage == 'wrong-build-method':
        next(row for row in events if row.get('operation') == 'Compiler.Build')['operation'] = 'Compiler.ChangeSFCProgram'
    elif damage == 'source-snapshot':
        event = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'after-build')
        before = snapshots[event['file']]
        snapshots[event['file']] = before[:-1] + bytes([before[-1] ^ 1])
    result = project_native_validation(raw, observed, snapshots)
    assert result['compilation']['acceptance'] == 'accepted'
    if damage in {'none', 'wrong-build-method', 'source-snapshot'}:
        assert result['public_check'] == {'status': 'rejected', 'completed': True}
    else:
        assert result['public_check']['completed'] is False
    if damage == 'none':
        assert result['current_source_check'] == 'established'
        checked = next(row for row in result['diagnostic_projection']['target_checks'] if row['target'] == target)
        assert checked['reported_diagnostic_counts'] == checked['observed_diagnostic_counts'] == [1, 0]
        assert checked['diagnostic_count_scope'] == 'selected-resource-report-stream'
        diagnostic, = result['diagnostic_projection']['public_diagnostics']
        assert diagnostic['name']['text'].startswith('Block.') and diagnostic['name']['text'] != checked['completed_resource']
    else:
        assert result['current_source_check'] == 'not_established'


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
def test_each_q_sfc_web_and_mcp_share_two_edits_download_and_reimport(tmp_path, monkeypatch, cpu):
    from agent_runtime.runtime import build_default_tool_runtime
    from shared.model_context import technical_value
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    import gxworks2.workspace_adapter as adapter
    def forbidden(*args, **kwargs):
        pytest.fail('offline source editing started a native operation')
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', forbidden)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', forbidden)
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, 'validate', forbidden)
    workspace = tmp_path / 'workspace'
    raw = q_native_sfc_programs()[cpu]
    app = _app(workspace, tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        imported = client.post('/api/gxw/import', headers=headers, json=dict(
            request_id='q-sfc-import', filename='q-sfc.gxw', data_base64=base64.b64encode(raw).decode()))
        assert imported.status_code == 201, imported.text
        pid, vid = imported.json()['project_id'], imported.json()['version_id']
        for turn in (1, 2):
            source = client.get('/api/sfc/source', params=dict(project_id=pid, version_id=vid))
            assert source.status_code == 200, source.text
            before = source.json()['model']
            assert before['cpu'] == cpu
            child = next(row for row in before['children'] if row['kind'] == 'zoom' and row['records'])
            first = child['records'][0]
            edits = [dict(kind='zoom', name=child['name'], instruction_edits=[dict(
                operation='insert_before' if turn == 1 else 'replace', record_index=0,
                expected=dict(op=first['op'], args=first['args']), instructions=[dict(op='NOP', args=[])] * turn)])]
            command = dict(project_id=pid, version_id=vid, child_edits=edits)
            preview = client.post('/api/sfc/preview', headers=headers, json=command)
            assert preview.status_code == 200, preview.text
            transport = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(workspace, pid, vid))
            candidate = transport.call_tool('create_sfc_candidate', dict(operation='edit', child_edits=edits), 'q-sfc-candidate')
            assert not candidate.is_error, candidate
            assert candidate.structured_content['data']['model'] == technical_value(preview.json()['program'])
            saved = client.post('/api/sfc/proposals', headers=headers,
                json={**command, 'operation': 'edit', 'request_id': f'q-sfc-edit-{turn}'})
            assert saved.status_code == 201, saved.text
            vid = saved.json()['result']['version_id']
            current = client.get('/api/sfc/source', params=dict(project_id=pid, version_id=vid)).json()['model']
            assert current == preview.json()['program']
            read = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(workspace, pid, vid)).call_tool(
                'read_sfc_project', {}, 'q-sfc-read')
            assert not read.is_error and read.structured_content['data']['model'] == technical_value(current)
        downloaded = client.get(f'/api/projects/{pid}/versions/{vid}/artifacts/gxw?download=true')
        assert downloaded.status_code == 200 and downloaded.content != raw
        again = client.post('/api/gxw/import', headers=headers, json=dict(request_id='q-sfc-reimport',
            data_base64=base64.b64encode(downloaded.content).decode(), filename='q-sfc-edited.gxw'))
        assert again.status_code == 201, again.text
        reopened = client.get('/api/sfc/source', params=dict(project_id=again.json()['project_id'],
            version_id=again.json()['version_id']))
        assert reopened.status_code == 200 and reopened.json()['model'] == current


def test_sfc_ql_step_action_binding_replay_keeps_rejected_conversion_distinct(archived_native_requests):
    from research.freeze_gxw_sfc_ql_binding import replay
    result = archived_native_requests(replay, document_projection=_recorded_complete_ql_sfc_document)
    assert result['menu_count'] == 47
    assert all(row['matched']['current_source_check'] == 'established' for row in result['cases'])
    assert all(row['unmatched']['compilation'] == 'rejected' for row in result['cases'])
    assert all(row['unmatched']['public_check'] == {'status': 'not_requested', 'completed': False}
               for row in result['cases'])
    assert len(result['shared_applications']) == 2


def test_sfc_ql_connection_geometry_replays_complete_current_source_checks(archived_native_requests):
    from research.freeze_gxw_sfc_ql_topology import replay
    result = archived_native_requests(replay, document_projection=_recorded_complete_ql_sfc_document)
    assert result['menu_count'] == 47
    assert all(row['current_source_check'] == 'established' for row in result['cases'])
    assert all(row['segments'] == 22 and row['parallel_segments'] == 2 for row in result['cases'])
    assert all(row['stored_children'] == 12 and not row['graph_write_integration'] for row in result['cases'])
    assert len(result['shared_applications']) == 2
    assert all(row['preview_save_cold_mcp_equal'] and row['current_source_check'] == 'established'
               for row in result['shared_applications'])


def test_sfc_instruction_position_menus_replay_complete_rejection_and_current_graph_correspondence(archived_native_requests):
    from research.freeze_gxw_sfc_position_menus import replay
    result = archived_native_requests(replay, document_projection=_recorded_sfc_document)
    assert result['menu_count'] == 57
    assert sum(row['diagnostic_count'] for row in result['cases']) == 325
    assert all(row['public_check'] == {'status': 'rejected', 'completed': True}
               and row['current_source_check'] == 'established' for row in result['cases'])
    assert len(result['shared_applications']) == 2
    assert all(row['instruction_and_cell'] == 'correlated' for row in result['shared_applications'])


def test_sfc_fx_reference_edits_replay_all_menus_and_shared_current_code_checks(archived_native_requests):
    from research.freeze_gxw_sfc_fx_edits import replay
    result = archived_native_requests(replay, document_projection=_recorded_sfc_document)
    assert result['menu_count'] == 10 and len(result['cases']) == 20
    assert all(row['current_source_check'] == 'established' and row['segments'] == 10 for row in result['cases'])
    assert len(result['shared_applications']) == 2
    assert result['mcp_candidate']['requires_confirmation'] and not result['mcp_candidate']['accepted']


@pytest.mark.parametrize('menu', ['FX3G/FX3GC', 'FX0S/FX0'])
def test_sfc_fx_measured_edit_profile_reaches_shared_preview_and_candidate(tmp_path, monkeypatch, menu):
    from zipfile import ZipFile
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save, _native_sfc_graph_output
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import read_sfc_project_context
    from research.freeze_gxw_sfc_fx_edits import ARCHIVE, EDITS
    with ZipFile(ARCHIVE) as archive:
        prefix = 'shared-complete-' + menu.replace('/', '_') + '/'
        files = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist() if name.startswith(prefix)}
    raw = files['input.gxw']; calls = []; save_number = iter((2, 3))
    def read(_self, current, program_name=None, *, timeout=None):
        assert current == raw and timeout is None
        context = read_sfc_project_context(raw)
        prepared = prepare_sfc_project_write(raw, context.program_name, context.source, {})
        body = files['native-01-read_sfc_graph/sfc-graph-after.bin']
        _, graph = _native_sfc_graph_output(prepared, {**native_source_plan(prepared), 'graph_edits': []}, body,
            load_frozen_native_json(files['native-01-read_sfc_graph/observation.json']))
        graph.update(logical_name=context.program_name, source_body_hex=body.hex()); calls.append('read')
        return graph
    def save(_self, prepared, *, timeout=None, graph_edits=None):
        prefix = f'native-{next(save_number):02d}-save_source/'
        assert graph_edits == EDITS and prepared.baseline == raw and timeout is None
        assert native_source_plan(prepared, graph_edits=graph_edits) == native_request_semantics(json.loads(files[prefix + 'request.json'])['source'])
        calls.append('save')
        return verify_native_save(prepared, files[prefix + 'candidate.gxw'], load_frozen_native_json(files[prefix + 'observation.json']),
            graph_edits=graph_edits, graph_body=files[prefix + 'sfc-graph-after.bin'])
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', read)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    # Replay the two independent native saves separately from offline Web edits.
    context = read_sfc_project_context(raw)
    prepared = prepare_sfc_project_write(raw, context.program_name, context.source, {})
    for _ in range(2):
        measured = save(None, prepared, graph_edits=EDITS)
        assert _sfc_source_model(read_sfc_project_context(measured.data).object_model()) == _sfc_source_model(
            _recorded_sfc_model(json.loads(files['current.json'])['model']))
    assert calls == ['save', 'save']
    calls.clear()
    with TestClient(_app(tmp_path / 'workspace', tmp_path / 'state'), base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects', headers=headers, json=dict(name='FX references', plc_model=menu, target_mode='ladder')).json()['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json=dict(operation='import', project_id=pid,
            request_id='fx-import', data_base64=base64.b64encode(raw).decode()))
        assert imported.status_code == 201
        base = imported.json()['result']['version_id']
        before = client.get('/api/sfc/source', headers=headers, params=dict(project_id=pid, version_id=base, native=True))
        assert before.json()['model']['graph']['edit_profile'] == 'fx-references'
        command = dict(project_id=pid, version_id=base, graph_edits=EDITS)
        preview = client.post('/api/sfc/preview', headers=headers, json=command)
        assert preview.status_code == 200, preview.text
        proposal = client.post('/api/sfc/proposals', headers=headers, json={**command, 'operation': 'edit', 'request_id': 'fx-edit'})
        assert proposal.status_code == 201, proposal.text
        current = client.get('/api/sfc/source', headers=headers,
            params=dict(project_id=pid, version_id=proposal.json()['result']['version_id'])).json()['model']
        assert current == preview.json()['program']
        assert _sfc_source_model(current) == _sfc_source_model(
            _recorded_sfc_model(json.loads(files['current.json'])['model']))
        assert current['graph']['position_resolution'] == 'not-established'
        assert [edge['target'] for edge in current['graph']['source_graph']['edges']
                if edge['kind'] == 'jump'] == ['step:1']
    assert calls == ['read']


def test_sfc_ql_original_symbol_rebuild_replays_both_shapes_and_shared_cold_geometry(archived_native_requests):
    from research.freeze_gxw_sfc_ql_rebuild import replay
    result = archived_native_requests(replay, document_projection=_recorded_complete_ql_sfc_document)
    assert result['menu_count'] == 47 and result['graph_cases'] == 94
    assert all(row['current_source_check'] == 'established' and row['stored_children'] == 12 for row in result['cases'])
    assert {row['shape'] for row in result['cases']} == {'combined-swap', 'linear'}
    assert len(result['shared_applications']) == 2
    assert all(row['cold_geometry_published'] and row['preview_save_cold_mcp_equal']
               for row in result['shared_applications'])
    assert len(result['mcp_candidates']) == 2
    assert all(row['requires_confirmation'] and not row['accepted'] for row in result['mcp_candidates'])


def test_sfc_ql_web_and_mcp_swaps_publish_the_saved_cold_grid(tmp_path, monkeypatch):
    from zipfile import ZipFile
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save
    from research.freeze_gxw_sfc_ql_rebuild import ARCHIVE, cold_graph
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    with ZipFile(ARCHIVE) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()
                   if name.startswith(('shared-complete-Q00J/', 'mcp-candidate-Q00J/'))}
    root = 'shared-complete-Q00J'
    raw = entries[root + '/input.gxw']
    reads = iter([root + f'/native-{number:02d}-read_sfc_graph' for number in (1, 3, 5)]
                 + ['mcp-candidate-Q00J/native-02-read_sfc_graph'])
    saves = iter([root + f'/native-{number:02d}-save_source' for number in (2, 4)]
                 + ['mcp-candidate-Q00J/native-01-save_source'])
    calls = []
    def read(_self, current, program_name=None, *, timeout=None):
        prefix = next(reads)
        observed_raw, _context, graph = cold_graph(entries, prefix)
        assert current == observed_raw and timeout is None
        calls.append('cold-read')
        return graph
    def save(_self, prepared, *, timeout=None, graph_edits=None):
        prefix = next(saves)
        assert prepared.baseline == entries[prefix + '/input.gxw'] and timeout is None
        assert native_source_plan(prepared, graph_edits=graph_edits) == native_request_semantics(json.loads(entries[prefix + '/request.json'])['source'])
        calls.append('save')
        return verify_native_save(prepared, entries[prefix + '/candidate.gxw'], load_frozen_native_json(entries[prefix + '/observation.json']),
            graph_edits=graph_edits, graph_body=entries[prefix + '/sfc-graph-after.bin'])
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', read)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    workspace = tmp_path / 'workspace'
    app = _app(workspace, tmp_path / 'state')
    edit = dict(kind='swap_steps', left_step_reference=1, right_step_reference=2)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects', headers=headers,
            json=dict(name='Q/L graph swap', plc_model='Q00J', target_mode='ladder')).json()['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json=dict(operation='import', project_id=pid,
            request_id='import-rebuild', data_base64=base64.b64encode(raw).decode()))
        assert imported.status_code == 201
        base = imported.json()['result']['version_id']
        command = dict(project_id=pid, version_id=base, graph_edits=[edit])
        initial_preview = client.post('/api/sfc/preview', headers=headers, json=command)
        assert initial_preview.status_code == 200, initial_preview.text
        assert initial_preview.json()['program']['graph']['position_resolution'] == 'not-established'
        assert not calls
        assert client.get('/api/sfc/source', headers=headers,
            params=dict(project_id=pid, version_id=base, native=True)).status_code == 200
        invalid = {**command, 'graph_edits': [{**edit, 'left_step_reference': True}]}
        assert client.post('/api/sfc/preview', headers=headers, json=invalid).status_code == 422
        assert calls == ['cold-read']
        preview = client.post('/api/sfc/preview', headers=headers, json=command)
        assert preview.status_code == 200, preview.text
        proposal = client.post('/api/sfc/proposals', headers=headers,
            json={**command, 'operation': 'edit', 'request_id': 'swap-rebuild'})
        assert proposal.status_code == 201, proposal.text
        vid = proposal.json()['result']['version_id']
        current = client.get('/api/sfc/source', headers=headers,
            params=dict(project_id=pid, version_id=vid)).json()['model']
        assert current == preview.json()['program']
        assert _sfc_source_model(current) == _sfc_source_model(
            _recorded_sfc_model(json.loads(entries[root + '/current.json'])['model']))
        assert current['graph']['position_resolution'] == 'not-established'
        assert current['graph']['edit_profile'] == 'q-l-symbols'
        assert [step['step_reference'] for step in current['graph']['source_graph']['steps']] == [0, 2, 1, 3, 4, 5]
    transport = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(workspace, pid, vid))
    candidate = transport.call_tool('create_sfc_candidate', dict(operation='edit', graph_edits=[edit]), 'swap-back')
    assert not candidate.is_error
    assert _sfc_source_model(candidate.structured_content['data']['model']) == _sfc_source_model(
        _recorded_sfc_model(json.loads(entries['mcp-candidate-Q00J/model.json'])))
    assert candidate.structured_content['data']['requires_confirmation']
    assert calls == ['cold-read']


def test_sfc_concrete_cpu_menu_save_and_current_check_replay(archived_native_requests):
    from research.freeze_gxw_sfc_cpu_menus import verify
    result = archived_native_requests(verify, sfc_cpu_menu_witness(), document_projection=_recorded_complete_ql_sfc_document)
    assert result['menu_count'] == 60 and result['programmable_menu_count'] == 57
    assert len(result['cases']) == 57
    assert all(row['current_source_check'] == 'established' for row in result['cases'])
    assert all(row['public_check'] == {'status': 'passed', 'completed': True} for row in result['cases'])
    assert len(result['parameter_only']) == 3
    assert len(result['graph_reads']) == 57
    assert all(row['source_unchanged'] and not row['graph_write'] for row in result['graph_reads'])
    assert len(result['production_graph_reads']) == len(result['shared_applications']) == 3
    assert all(row['preview_save_cold_mcp_equal'] for row in result['shared_applications'])


@pytest.mark.parametrize('menu', [
    profile.cpu for profile in NATIVE_PROJECT_PROFILES
    if profile.program_supported
])
def test_sfc_native_graph_reader_uses_each_measured_cpu_menu_profile(tmp_path, monkeypatch, menu):
    import types
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import read_sfc_project_context, native_sfc_graph_profile
    entries = sfc_cpu_menu_witness()
    row, = [item for item in json.loads(entries['menu-cases.json'])['cases'] if item['menu'] == menu]
    prefix = row['graph_read'] + '/'
    raw = entries[prefix + 'input.gxw']
    context = read_sfc_project_context(raw)
    plan = native_source_plan(prepare_sfc_project_write(raw, context.program_name, context.source, {}))
    plan['graph_profile'] = native_sfc_graph_profile(row['cpu'])
    executable = tmp_path / 'read-replay.exe'
    executable.write_bytes(b'captured native transport; never executed')
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    calls = []
    def capture(exe, request, directory, timeout, error_type):
        assert exe == executable.resolve() and request['operation'] == 'read_sfc_graph'
        assert request['source'] == plan and (directory / 'input.gxw').read_bytes() == raw
        (directory / 'sfc-graph-after.bin').write_bytes(entries[prefix + 'sfc-graph-after.bin'])
        calls.append(request)
        return 0, deepcopy(load_frozen_native_json(entries[prefix + 'observation.json']))
    monkeypatch.setattr(adapter, '_run_native_adapter', capture)
    result = adapter.NativeWorkspaceSourceSave(executable, tmp_path).read_sfc_graph(raw)
    expected = json.loads(entries[prefix + 'graph.json'])
    result.pop('native_workspace')
    assert result == expected and len(calls) == 1
    assert context.native_graph(result) is not None


@pytest.mark.parametrize('cpu', ['FX5U', 'Q2AS', 'FX3UC', 'LJ72GF15-T2', 'QJ72LP25/QJ72BR15(RemoteI/O)'])
def test_sfc_native_graph_reader_does_not_infer_unmeasured_cpu_aliases_or_series(cpu):
    from gxw.sfc_pou import native_sfc_graph_profile
    assert native_sfc_graph_profile(cpu) is None


@settings(max_examples=24, deadline=None, derandomize=True)
@given(menu_index=st.integers(0, 56), byte_index=st.integers(0, 26), mask=st.integers(1, 255))
def test_sfc_menu_current_check_does_not_survive_one_changed_native_child_read(menu_index, byte_index, mask):
    from gxw.native_diagnostics import project_native_validation
    entries = sfc_cpu_menu_witness()
    row = json.loads(entries['menu-cases.json'])['cases'][menu_index]
    prefix = row['check'] + '/'
    observation = deepcopy(load_frozen_native_json(entries[prefix + 'observation.json']))
    snapshots = {name.removeprefix(prefix): raw for name, raw in entries.items()
                 if name.startswith(prefix) and name.endswith('.bin') and '/' not in name.removeprefix(prefix)}
    reads = [event for event in observation['validation']['events']
             if event['operation'] == 'NativeSfcChildRead' and event['stage'] == 'after-check']
    assert reads
    read = reads[0]
    native_body = bytearray(snapshots[read['file']])
    assert len(native_body) == 27
    native_body[byte_index] ^= mask
    snapshots[read['file']] = bytes(native_body)
    result = project_native_validation(entries[prefix + 'input.gxw'], observation, snapshots)
    # The original public report still passed; a changed current source getter
    # prevents that report from establishing a check of these source bytes.
    assert result['public_check'] == {'status': 'passed', 'completed': True}
    assert result['current_source_check'] == 'not_established'
    assert result['source_bodies']['status'] != 'current'


@lru_cache(maxsize=1)
def retained_sfc_diagnostic_witness():
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    prefix = 'retained-diagnostic/native-02-validate_project/'
    with ZipFile(ARCHIVE) as archive:
        return {'raw': archive.read(prefix + 'input.gxw'),
                'observation': load_frozen_native_json(archive.read(prefix + 'observation.json')),
                'snapshots': {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                              if name.startswith(prefix) and name.endswith('.bin')},
                'graph': json.loads(archive.read('retained-diagnostic/native-graph.json'))}


def test_sfc_save_and_check_plans_use_the_paired_block_declaration_layout():
    import struct
    from gxw.declarations import serialize_declarations
    from gxw.lossless import _replace_token_program
    from gxw.native_diagnostics import native_validation_source_plan
    from gxw.native_write import native_source_plan
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import read_sfc_project_context

    raw = retained_sfc_diagnostic_witness()['raw']
    original = read_sfc_project_context(raw)
    labels = original.declarations['Block.Labels.lh']
    data = bytearray(labels.raw)
    # This is a source-layout control, not a native block renumber operation.
    struct.pack_into('<I', data, 90, 1)
    patched = _replace_token_program(raw, labels.logical_name, labels.raw, bytes(data)).data
    context = read_sfc_project_context(patched)
    selected = context.declarations[labels.logical_name]
    assert selected.owner_sfc_block_number == 1 and selected.rows == ()
    assert not context.issues and serialize_declarations(selected) == bytes(data)
    assert native_validation_source_plan(patched)['programs'][0]['name'] == 'Block'
    plan = native_source_plan(prepare_sfc_project_write(patched, context.program_name, context.source, {}))
    assert plan['program_name'] == 'Block' and plan['program_kind'] == 240
    assert plan['before_children'] == plan['after_children']


@pytest.mark.parametrize('stale_graph', [False, True])
def test_sfc_native_name_normalization_keeps_shared_candidate_and_current_graph_binding(tmp_path, monkeypatch, stale_graph):
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.container_writer import replace_project_stream, validate_cfb_streams
    from gxw.project_metadata import current_rows
    from gxw.native_write import verify_native_save, native_source_plan, _native_sfc_graph_output
    from gxw.project_writer import prepare_sfc_project_write, ProjectWriteResult
    from gxw.sfc_pou import read_sfc_project_context
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    import gxworks2.workspace_adapter as adapter

    with ZipFile(ARCHIVE) as archive:
        raw = archive.read('retention-application/input.gxw')
        saved = archive.read('retention-application/candidate.gxw')
        observed = load_frozen_native_json(archive.read('retention-application/native-03-save_source/observation.json'))
        graph_body = archive.read('retention-application/native-01-read_sfc_graph/sfc-graph-after.bin')
        graph_observation = load_frozen_native_json(archive.read('retention-application/native-01-read_sfc_graph/observation.json'))
    context = read_sfc_project_context(raw)
    edits = [{'kind': 'zoom', 'name': '2dcf5e45d1e9449c9afb12a6e21d6bc6', 'records_hex': '032003049d0304'}]
    prepared = prepare_sfc_project_write(raw, context.program_name, context.source,
        {(edits[0]['kind'], edits[0]['name']): bytes.fromhex(edits[0]['records_hex'])})
    frozen = verify_native_save(prepared, saved, observed)
    xml = validate_cfb_streams(raw)['projectdatalist.xml']
    _, encoding = current_rows(xml, 'DSPROJECTDATA', 'D_Projectdata')
    localized = 'Block.程序.pou'
    changed, _ = replace_project_stream(raw, 'projectdatalist.xml',
        xml.decode(encoding).replace(context.program_name, localized).encode(encoding))
    graph_prepared = prepare_sfc_project_write(raw, context.program_name, context.source, {})
    _, graph = _native_sfc_graph_output(graph_prepared,
        {**native_source_plan(graph_prepared), 'graph_edits': []}, graph_body, graph_observation)
    graph.update(logical_name=localized, source_body_hex=graph_body.hex())
    assert read_sfc_project_context(changed).native_graph(graph) is not None
    if stale_graph:
        graph['source_body_hex'] = '00' + graph['source_body_hex'][2:]
    calls = []

    def save(_self, actual, *, timeout=None, graph_edits=None):
        assert actual.baseline == changed and list(actual.programs) == [localized]
        report = deepcopy(frozen.report)
        report['objects'][0]['object'] = localized
        report['objects'][0]['saved_object'] = context.program_name
        calls.append(localized)
        return ProjectWriteResult(saved, report)

    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    localized_context = read_sfc_project_context(changed)
    normalized = save(None, prepare_sfc_project_write(changed, localized, localized_context.source,
        {(edits[0]['kind'], edits[0]['name']): bytes.fromhex(edits[0]['records_hex'])}))
    assert normalized.saved_object_name(localized) == context.program_name
    calls.clear()
    app = _app(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects', headers=headers, json={
            'name': 'SFC normalized source', 'plc_model': 'FX3U', 'target_mode': 'ladder'}).json()['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json={'operation': 'import', 'project_id': pid,
            'request_id': 'localized-import', 'program': localized, 'data_base64': base64.b64encode(changed).decode()})
        assert imported.status_code == 201, imported.text
        base = imported.json()['result']['version_id']
        app.state.service.sfc._graph_reads[(pid, base, localized)] = graph
        command = {'project_id': pid, 'version_id': base, 'program': localized, 'child_edits': edits}
        preview = client.post('/api/sfc/preview', headers=headers, json=command)
        assert preview.status_code == 200, preview.text
        proposal = client.post('/api/sfc/proposals', headers=headers,
            json={**command, 'operation': 'edit', 'request_id': 'normalized-edit'})
        assert proposal.status_code == 201, proposal.text
        vid = proposal.json()['result']['version_id']
        current = client.get('/api/sfc/source', headers=headers, params={'project_id': pid, 'version_id': vid})
        assert current.status_code == 200 and current.json()['model'] == preview.json()['program']
        assert current.json()['model']['program'] == localized
        assert current.json()['model']['graph']['position_resolution'] == ('not-established' if stale_graph else 'native-source-bound')
        assert app.state.service.projects.artifact(pid, base, 'gxw').read_bytes() == changed
        mcp = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(tmp_path / 'workspace', pid, vid))
        read = mcp.call_tool('read_sfc_project', {}, 'normalized-source')
        assert not read.is_error and read.structured_content['data']['model'] == current.json()['model']
    cold = _app(tmp_path / 'workspace', tmp_path / 'cold-state')
    with TestClient(cold, base_url=ORIGIN) as client:
        reread = client.get('/api/sfc/source', headers=_login(client), params={'project_id': pid, 'version_id': vid})
        assert reread.status_code == 200 and reread.json()['model'] == current.json()['model']
    assert not calls


@pytest.mark.parametrize('block,report_block,resolved', [(0, 0, True), (1, 1, True), (0, 1, False), (1, 0, False)])
def test_sfc_instruction_report_binds_to_its_current_block_metadata(block, report_block, resolved):
    import struct
    from gxw.lossless import _replace_token_program
    from gxw.native_diagnostics import (_native_validation_sources, project_native_validation,
                                       project_native_sfc_instruction_position)
    from gxw.sfc_pou import read_sfc_project_context

    witness = retained_sfc_diagnostic_witness()
    result = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    audit = next(row for row in result['diagnostic_projection']['source_audits']
                 if row['original_public_diagnostic']['code'] == 0x050c9319)
    context = read_sfc_project_context(witness['raw'])
    labels = context.declarations['Block.Labels.lh']
    data = bytearray(labels.raw); struct.pack_into('<I', data, 90, block)
    raw = _replace_token_program(witness['raw'], labels.logical_name, labels.raw, bytes(data)).data
    _, sources = _native_validation_sources(raw)
    # Exercise the report-to-source relation after source checking; this
    # synthetic metadata variant is not evidence of another native check.
    report = deepcopy(audit['original_public_diagnostic'])
    report['network'] = report_block
    report['name'] = {'text': report['name']}; report['instance'] = {'text': report['instance']}
    position = project_native_sfc_instruction_position(report, audit['native_source_projection'], sources,
        cpu='FX3U/FX3UC', native_version='1.635.0.1', current_check=True)
    assert (position['status'] == 'source-resolved') == resolved
    if resolved:
        assert position['position']['op'] == 'OUT' and position['position']['args'] == ['X0']


def test_sfc_referenced_error_reaches_shared_web_mcp_instruction_and_graph_projection(tmp_path, monkeypatch, native_replay):
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.sfc_pou import read_sfc_project_context
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.native_write import verify_native_save
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    import gxworks2.workspace_adapter as adapter

    raw, _, calls = native_replay('sfc-retained-error')
    witness = retained_sfc_diagnostic_witness()
    with ZipFile(ARCHIVE) as archive:
        before = archive.read('retained-diagnostic/input.gxw')
        saved_observation = load_frozen_native_json(archive.read('retained-diagnostic/native-01-save_source/observation.json'))
    context = read_sfc_project_context(before)
    edits = [{'kind': 'zoom', 'name': '2dcf5e45d1e9449c9afb12a6e21d6bc6', 'records_hex': '032003049c0004'}]
    prepared = prepare_sfc_project_write(before, context.program_name, context.source,
                                       {('zoom', edits[0]['name']): bytes.fromhex(edits[0]['records_hex'])})
    def save(_self, supplied, *, timeout=None, graph_edits=None):
        assert supplied.baseline == before and supplied.replacements == prepared.replacements
        return verify_native_save(supplied, raw, saved_observation)
    def graph(_self, source, program=None, *, timeout=None):
        assert source in (before, raw)
        return deepcopy(witness['graph'])
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', graph)
    app = _app(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects', headers=headers, json={'name': 'SFC referenced error',
            'plc_model': 'FX3U', 'target_mode': 'ladder'}).json()['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json={'operation': 'import',
            'project_id': pid, 'request_id': 'import-referenced-error', 'data_base64': base64.b64encode(before).decode()})
        assert imported.status_code == 201, imported.text
        base = imported.json()['result']['version_id']
        assert client.get('/api/sfc/source', headers=headers,
            params={'project_id': pid, 'version_id': base, 'native': True}).status_code == 200
        saved = client.post('/api/sfc/proposals', headers=headers, json={'operation': 'edit', 'project_id': pid,
            'version_id': base, 'request_id': 'save-referenced-error', 'child_edits': edits})
        assert saved.status_code == 201, saved.text
        vid = saved.json()['result']['version_id']
        offline_raw = app.state.service.projects.artifact(pid, vid, 'gxw').read_bytes()
        assert _sfc_source_model(read_sfc_project_context(offline_raw).object_model()) == _sfc_source_model(
            read_sfc_project_context(raw).object_model())
        # The collector archive checked the native-saved bytes. Import those
        # exact bytes separately; an offline save cannot inherit that check.
        recorded = client.post('/api/sfc/proposals', headers=headers, json={'operation': 'import',
            'project_id': pid, 'version_id': vid, 'request_id': 'import-recorded-error',
            'data_base64': base64.b64encode(raw).decode()})
        assert recorded.status_code == 201, recorded.text
        vid = recorded.json()['result']['version_id']
        reread = client.get('/api/sfc/source', headers=headers,
            params={'project_id': pid, 'version_id': vid, 'native': True})
        assert reread.status_code == 200, reread.text
        response = client.post(f'/api/projects/{pid}/versions/{vid}/native-validation/check', headers=headers,
                               json={'request_id': 'check-referenced-error'})
        assert response.status_code == 200, response.text
        web = response.json()['data']; result = web['data']
        assert result['referenced_sfc_check']['status'] == 'current'
        assert result['current_source_check'] == 'not_established'
        assert result['public_check'] == {'status': 'rejected', 'completed': True}
        diagnostic, = result['diagnostic_projection']['source_diagnostics']
        assert diagnostic['source_projection']['instruction_position']['position']['args'] == ['X0']
        assert diagnostic['graph_projection']['cell']['id'] == 'cell:0:3'
        mcp = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(tmp_path / 'workspace', pid, vid))
        checked = mcp.call_tool('validate_project', {'native': True}, 'mcp-referenced-error')
        assert not checked.is_error and checked.structured_content == web
        assert len(calls) == 2


@pytest.mark.parametrize('damage', ['before-child', 'after-child', 'checked-child', 'graph', 'selection',
    'phase-order', 'changed-child-id', 'missing-getter', 'imported-source', 'old-check-input', 'unfinished-check'])
def test_sfc_referenced_diagnostic_requires_complete_current_phases_without_promoting_stored_source(damage):
    from gxw.native_diagnostics import project_native_validation, project_native_sfc_graph_diagnostics
    witness = retained_sfc_diagnostic_witness()
    observation, snapshots = deepcopy(witness['observation']), dict(witness['snapshots'])
    events = observation['validation']['events']
    if damage in ('before-child', 'after-child', 'checked-child'):
        stage = {'before-child': 'before-build', 'after-child': 'after-build', 'checked-child': 'after-check'}[damage]
        event = next(row for row in events if row['operation'] == 'NativeSfcChildRead' and row['stage'] == stage)
        snapshots[event['file']] = snapshots[event['file']][:-1] + b'\xff'
    elif damage == 'graph':
        event = next(row for row in events if row['operation'] == 'NativeBodyRead' and row['stage'] == 'before-build')
        snapshots[event['file']] = snapshots[event['file']][:-1] + b'\xff'
    elif damage == 'selection':
        event = next(row for row in events if row['operation'] == 'NativeBodySelection' and row['stage'] == 'before-build')
        events.append(deepcopy(event))
    elif damage == 'phase-order':
        event = next(row for row in events if row['operation'] == 'NativeSfcChildRead' and row['stage'] == 'before-build')
        events.remove(event); events.append(event)
    elif damage == 'changed-child-id':
        event = next(row for row in events if row['operation'] == 'NativeSfcChildRead' and row['stage'] == 'before-build')
        event['identity']['id'][11] ^= 1
    elif damage == 'missing-getter':
        event = next(row for row in events if row['operation'] == 'NativeSfcChildRead' and row['stage'] == 'after-build')
        events.remove(event)
    elif damage == 'imported-source':
        snapshots.pop('imported-hdb.bin')
    elif damage == 'old-check-input':
        event = next(row for row in events if row['operation'] == 'OwnedCheckInput')
        name = event['rows'][0]['file']; snapshots[name] = snapshots[name][:-1] + b'\xff'
    else:
        event = next(row for row in events if row['operation'] == 'ProgramCheckOriginalProgress' and row.get('percent') == 100)
        event['percent'] = 99
        events[:] = [row for row in events if row['operation'] != 'ProgramCheckTargetOutcome']
    result = project_native_validation(witness['raw'], observation, snapshots)
    assert result['current_source_check'] == 'not_established'
    assert result['referenced_sfc_check']['status'] == 'unresolved'
    mapped = project_native_sfc_graph_diagnostics(witness['raw'], result, witness['graph'])
    assert all(row['graph_projection']['status'] != 'uniquely_correlated'
               for row in mapped['diagnostic_projection']['source_diagnostics'])


@settings(max_examples=24, deadline=None, derandomize=True)
@given(st.permutations(range(4)), st.sampled_from(('before-build', 'after-build', 'after-check')))
def test_sfc_referenced_diagnostic_read_order_preserves_instruction_and_cell_without_promoting_whole_source(order, stage):
    from gxw.native_diagnostics import project_native_validation, project_native_sfc_graph_diagnostics
    witness = retained_sfc_diagnostic_witness(); observation = deepcopy(witness['observation'])
    events = observation['validation']['events']
    positions = [i for i, row in enumerate(events) if row['operation'] == 'NativeSfcChildRead' and row['stage'] == stage]
    original = [events[i] for i in positions]
    for position, index in zip(positions, order): events[position] = original[index]
    result = project_native_validation(witness['raw'], observation, witness['snapshots'])
    assert result['referenced_sfc_check']['status'] == 'current'
    assert result['current_source_check'] == 'not_established'
    assert result['public_check'] == {'status': 'rejected', 'completed': True}
    mapped = project_native_sfc_graph_diagnostics(witness['raw'], result, witness['graph'])
    diagnostic, = mapped['diagnostic_projection']['source_diagnostics']
    assert diagnostic['source_projection']['instruction_position']['position']['args'] == ['X0']
    assert diagnostic['graph_projection']['cell']['id'] == 'cell:0:3'
    assert diagnostic['source_projection']['check_scope'] == 'referenced_sfc_source'
    assert not diagnostic['source_projection']['stored_source_check_promoted']
    assert not diagnostic['source_projection']['public_check_promoted']


@pytest.mark.parametrize('case,acceptance,public', [
    ('linear-native-compile', 'rejected', 'not_requested'),
    ('step10-native-compile', 'accepted', 'passed'),
])
def test_sfc_compiler_result_does_not_promote_incomplete_child_source_selection(case, acceptance, public):
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.native_diagnostics import project_native_validation
    from gxw.sfc_pou import read_sfc_project_context

    prefix = 'binding-loader/' + case + '/'
    with ZipFile(ARCHIVE) as archive:
        raw = archive.read(prefix + 'input.gxw')
        observed = load_frozen_native_json(archive.read(prefix + 'observation.json'))
        snapshots = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                     if name.startswith(prefix) and name.endswith('.bin')}
    assert not read_sfc_project_context(raw).object_model()['issues']
    result = project_native_validation(raw, observed, snapshots)
    assert result['compilation']['acceptance'] == acceptance
    assert result['public_check'] == {'status': public, 'completed': public == 'passed'}
    assert result['current_source_check'] == 'not_established'
    assert result['compilation']['source_context']['status'] == 'unresolved'
    assert not result['compilation']['source_context']['public_check_promoted']
    assert result['diagnostic_projection']['source_diagnostics'] == []
    if public == 'passed':
        assert result['published_resources']['correspondence'] == 'current'
        assert result['owned_check_inputs']['correspondence'] == 'current'


@pytest.mark.parametrize('case,acceptance,public,count,missing,stored,scope_count', [
    ('original', 'accepted', 'passed', 5, [], 5, 5),
    ('linear-step1', 'rejected', 'not_requested', 3, ['referenced', 'retained'], 5, 5),
    ('linear-step10', 'accepted', 'passed', 4, ['retained'], 5, 5),
    ('loader-cold-check', 'rejected', 'not_requested', 4, ['unresolved'], 5, 0),
    ('loader-aligned-cold-check', 'accepted', 'passed', 4, [], 4, 4),
    ('loader-restored-cold-check', 'accepted', 'passed', 4, ['retained'], 5, 5),
])
def test_sfc_native_source_scope_reaches_web_and_mcp_without_promoting_missing_children(
        tmp_path, native_replay, case, acceptance, public, count, missing, stored, scope_count):
    pytest.importorskip('mcp')
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter

    raw, _, calls = native_replay('sfc-' + case if case.startswith('loader-') else 'sfc-scope-' + case)
    app = _app(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={
            'name': 'SFC source scope', 'target_mode': 'ladder', 'plc_model': 'FX3U'}).json()
        pid = project['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json={
            'operation': 'import', 'project_id': pid, 'request_id': 'scope-import',
            'data_base64': base64.b64encode(raw).decode()})
        assert imported.status_code == 201, imported.text
        vid = imported.json()['result']['version_id']
        route = f'/api/projects/{pid}/versions/{vid}/native-validation/check'
        command = {'request_id': 'source-scope'}
        checked = client.post(route, headers=headers, json=command)
        assert checked.status_code == 200, checked.text
        web = checked.json()['data']
        result = web['data']
        assert result['cpu'] == 'FX3U/FX3UC'
        assert result['compilation']['acceptance'] == acceptance
        assert result['public_check'] == {'status': public, 'completed': public == 'passed'}
        assert result['current_source_check'] == ('not_established' if missing else 'established')
        child_scope = result['source_bodies']['sources'][0]['sfc_children']
        assert len(child_scope['children']) == count
        assert all(row['status'] == 'current' for row in child_scope['children'])
        assert [row['role'] for row in child_scope['missing']] == missing
        assert len(child_scope['source_scope']['children']) == scope_count
        assert not child_scope['source_scope'].get('native_check_promoted', False)
        assert client.post(route, headers=headers, json=command).json() == checked.json()
        assert len(calls) == 1
        mcp = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(
            tmp_path / 'workspace', pid, vid)).call_tool('validate_project', {'native': True}, 'scope-check')
        assert not mcp.is_error and mcp.structured_content == web
        assert len(calls) == 2
        source = client.get('/api/sfc/source', headers=headers, params={'project_id': pid, 'version_id': vid})
        assert source.status_code == 200 and len(source.json()['model']['children']) == stored
        assert app.state.service.projects.artifact(pid, vid, 'gxw').read_bytes() == raw


def test_sfc_native_source_loader_separates_successful_check_from_unused_source_preservation(archived_native_requests):
    from research.freeze_gxw_sfc_application_graph import replay
    result = archived_native_requests(replay, document_projection=_recorded_sfc_document)['native_source_loader']
    assert result['native_processes'] == 6
    assert [row['current_source_check'] for row in result['checks']] == [
        'not_established', 'established', 'not_established']
    assert [row['stored_children'] for row in result['checks']] == [5, 4, 5]
    assert result['native_unused_source_removal']['removed_children'] == [
        {'kind': 'zoom', 'name': '5d05d0bb1e5c42ca9581959ed41b38e6', 'tokens': '032003049d0204'}]
    assert result['retained_source_restoration'] == {
        'child_record_exact': True, 'action_record_exact': True, 'body_exact': True,
        'stored_child_count': 5, 'issues': []}


def test_sfc_retained_sources_are_editable_in_the_shared_native_application_workflow(archived_native_requests):
    from research.freeze_gxw_sfc_application_graph import replay
    result = archived_native_requests(replay, document_projection=_recorded_sfc_document)['native_retention_application']
    assert result['native_processes'] == 7 and result['preview_save_reopen_mcp_equal']
    assert (result['stored_children'], result['cold_loaded_children']) == (5, 4)
    assert (result['active_output'], result['retained_output']) == ('Y3', 'Y4')
    assert result['public_check'] == 'passed' and result['current_source_check'] == 'not_established'


@settings(max_examples=24, derandomize=True, deadline=None)
@given(st.integers(0, 4), st.integers(1, 4))
def test_sfc_child_edit_sequences_keep_retained_action_identity_bound_to_the_current_source(index, copies):
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.sfc_pou import read_sfc_project_context
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.native_write import native_source_plan

    with ZipFile(ARCHIVE) as archive:
        raw = archive.read('retention-application/input.gxw')
    context = read_sfc_project_context(raw)
    child = context.source.layout['children'][index]
    original = bytes.fromhex(child['token_hex'])
    prepared = prepare_sfc_project_write(raw, context.program_name, context.source,
                                       {(child['kind'], child['name']): original * copies})
    plan = native_source_plan(prepared)
    assert plan['retained_actions'] == [{'number': 20, 'action_name': 'ee392677d964411bbffe7ba92abb2d2f',
                                        'zoom_name': '5d05d0bb1e5c42ca9581959ed41b38e6'}]
    before = {(row['type'], row['name']): base64.b64decode(row['body']) for row in plan['before_children']}
    after = {(row['type'], row['name']): base64.b64decode(row['body']) for row in plan['after_children']}
    assert before.keys() == after.keys() and len(before) == 5
    for key in before:
        if key == (68 if child['kind'] == 'zoom' else 66, child['name']):
            assert after[key][20:] == original * copies
        else:
            assert after[key] == before[key]


@settings(max_examples=24, derandomize=True, deadline=None)
@given(st.permutations(range(4)), st.sets(st.integers(0, 3)),
       st.sampled_from(('before-build', 'after-build', 'after-check')))
def test_sfc_partial_source_witness_order_and_loss_cannot_hide_missing_children(order, removed, stage):
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.native_diagnostics import bind_native_sfc_child_snapshots
    from gxw.sfc_pou import read_sfc_project_context

    prefix = 'source-scope/linear-step10/'
    with ZipFile(ARCHIVE) as archive:
        raw = archive.read(prefix + 'input.gxw')
        observation = load_frozen_native_json(archive.read(prefix + 'observation.json'))
        snapshots = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                     if name.startswith(prefix) and name.endswith('.bin')}
    native = [row for row in observation['validation']['events']
              if row['operation'] == 'NativeSfcChildRead' and row['stage'] == stage]
    assert len(native) == 4
    selected = [native[index] for index in order if index not in removed]
    result = bind_native_sfc_child_snapshots(read_sfc_project_context(raw).source.raw,
        owner_id=native[0]['parent'], stage=stage, reads=selected, snapshots=snapshots)
    assert result['status'] == 'unresolved'
    assert len(result['children']) == 4 - len(removed)
    assert all(row['role'] == 'referenced' and row['status'] == 'current' for row in result['children'])
    # Frozen native reads cover four referenced sources; action 20 is retained only.
    assert sum(row['role'] == 'referenced' for row in result['missing']) == len(removed)
    assert sum(row['role'] == 'retained' for row in result['missing']) == 1
    assert not result['source_scope']['native_check_promoted']


@pytest.mark.parametrize('damage', ['unknown-graph', 'unbound-action', 'unbound-transition'])
def test_sfc_unresolved_reference_inventory_does_not_classify_children_as_unused(damage):
    import struct
    from zipfile import ZipFile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from gxw.native_diagnostics import sfc_child_source_scope
    from gxw.sfc_pou import read_sfc_project_context

    with ZipFile(ARCHIVE) as archive:
        source = read_sfc_project_context(archive.read('source-scope/linear-step10/input.gxw')).source
    raw = bytearray(source.raw)
    if damage == 'unknown-graph':
        raw[source.layout['graph']['code_offset'] + 1] = 0x7f
    elif damage == 'unbound-action':
        struct.pack_into('<I', raw, source.layout['actions'][1]['number_offset'], 11)
    else:
        transition = next(row for row in source.layout['children'] if row['kind'] == 'transition')
        # In this frozen frame, number and storage flag precede the body size.
        number_offset = transition['size_offset'] - 8
        assert struct.unpack_from('<I', raw, number_offset)[0] == transition['number']
        struct.pack_into('<I', raw, number_offset, 11)
    result = sfc_child_source_scope(bytes(raw))
    assert result['status'] == 'unresolved' and result['children'] == []


@pytest.mark.parametrize('case,cpu,count', [
    ('q00j-production-line-77', 'Q00J', 23),
    ('q00-production-line-77', 'Q00', 23),
    ('q00uj-production-prefix-3-isolated', 'Q00UJ', 16),
    ('fx3u-declaration-type-controls-20261015/native-replay/project-line', 'FX3U/FX3UC', 23),
    ('declarations/product-FX3U_FX3UC-line-77', 'FX3U/FX3UC', 5),
    ('declarations/product-FX1N_FX1NC-line-77', 'FX1N/FX1NC', 2),
] + [('control-check-' + cpu.replace('/', '_'), cpu, count) for count, cpus in (
    (23, ('Q01', 'Q02/Q02H', 'Q02PH', 'Q06H', 'Q06PH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH')),
    (16, ('Q00U', 'Q01U', 'Q02U', 'Q03UD', 'Q04UDH', 'Q06UDH', 'Q10UDH', 'Q13UDH', 'Q20UDH', 'Q26UDH', 'Q50UDEH', 'Q100UDEH')),
) for cpu in cpus])
def test_current_declaration_and_library_compile_locations_reach_web_and_standalone_mcp(tmp_path, monkeypatch, case, cpu, count):
    pytest.importorskip('mcp')
    import types
    import gxworks2.workspace_adapter as native
    import application.execution as execution
    from gxw.native_diagnostics import native_validation_source_plan
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    from tests.test_gxw_compiler import compile_location_case

    raw, observation, snapshots = compile_location_case(case)
    plan = native_validation_source_plan(raw)
    program, = [row['logical_name'] for row in native_validation_source_plan(raw, include_logical_names=True)['programs']
                if row['name'] == 'PrgAxis1']
    executable = tmp_path / 'collector-replay.exe'
    executable.write_bytes(b'process transport double; never executed')
    original_init = native.NativeWorkspaceValidation.__init__
    monkeypatch.setattr(native.NativeWorkspaceValidation, '__init__',
        lambda self: original_init(self, executable, tmp_path))
    monkeypatch.setattr(native, 'os', types.SimpleNamespace(name='nt', environ=native.os.environ))
    resource_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: resource_lock(tmp_path / 'native.lock'))
    calls = []

    def collect(exe, request, root, timeout, error_type):
        assert exe == executable.resolve() and timeout is None
        assert request['source'] == plan and (root / 'input.gxw').read_bytes() == raw
        for name, data in snapshots.items():
            (root / name).write_bytes(data)
        calls.append(request['source'])
        return 0, deepcopy(observation)
    monkeypatch.setattr(native, '_run_native_adapter', collect)
    app = _app(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={
            'name': cpu + ' compile locations', 'target_mode': 'fbd', 'plc_model': cpu}).json()
        pid = project['id']
        imported = client.post('/api/fbd/proposals', headers=headers, json={
            'operation': 'import', 'project_id': pid, 'program': program, 'request_id': 'import-locations',
            'data_base64': base64.b64encode(raw).decode()})
        assert imported.status_code == 201, imported.text
        vid = imported.json()['result']['version_id']
        route = f'/api/projects/{pid}/versions/{vid}/native-validation/check'
        command = {'request_id': 'compile-locations', 'program': program}
        response = client.post(route, headers=headers, json=command)
        assert response.status_code == 200, response.text
        web = response.json()['data']
        result = web['data']
        assert web['ok'] and result['cpu'] == cpu
        assert result['snapshot'] == dict(project_id=pid, version_id=vid, requested_program=program, cpu=cpu)
        assert result['compilation']['declaration_context']['status'] == 'current'
        diagnostics = result['compilation']['source_diagnostics']
        resolved = [row['source_projection'] for row in diagnostics if row['source_projection']['status'] == 'source-resolved']
        assert len(resolved) == count and len(diagnostics) == count + (1 if count in (16, 23) else 0)
        assert all(row['instance_resolution'] == 'not_available_in_compile_report' for row in resolved)
        if count == 23:
            position, = [row['source']['declaration'] for row in resolved
                         if row['source']['pou'] == 'PrgAxis1' and row['source']['declaration']['name'] == 'Position']
            assert position['record_id'] == 77 and position['data_type'] == 'LREAL'
        elif count == 16:
            moved = [row['source'] for row in resolved if row['source']['pou'] == 'MC_MoveAbsolute_JEC']
            assert len(moved) == 4
            assert all(row['zero_based_line'] == 107 and 'fbTimer(' in row['text']
                       and row['library'] == 'MotionControl_JEC_V100A' for row in moved)
        else:
            assert result['compilation']['declaration_context']['global_and_structure']['status'] == 'current'
            expected = ('Global1', 'G_stLinkBasic', 'stRemoteRegBasic') if count == 5 else ('AXIS_REF_JEC', 'PosTrqLimit', 'REAL')
            moved = [row['source'] for row in resolved if row['source']['declaration']['record_id'] == 77]
            assert len(moved) == (5 if count == 5 else 1)
            assert all((row['declaration_owner'], row['declaration']['name'], row['declaration']['data_type']) == expected for row in moved)
            assert all('pou' not in row['source'] for row in resolved)
        assert result['compilation']['acceptance'] == 'rejected'
        assert result['public_check']['status'] == 'not_requested'
        assert result['current_source_check'] == result['current_raw_source_check'] == 'not_established'
        assert client.post(route, headers=headers, json=command).json() == response.json() and len(calls) == 1
        provider = SessionToolContextProvider(tmp_path / 'workspace', pid, vid)
        mcp = MCPToolAdapter(build_default_tool_runtime(), provider).call_tool(
            'validate_project', {'native': True, 'program': program}, 'standalone-compile-locations')
        assert not mcp.is_error and mcp.structured_content == web
        assert json.loads(mcp.content[0].text) == web and len(calls) == 2
        assert app.state.service.projects.artifact(pid, vid, 'gxw').read_bytes() == raw


@pytest.mark.parametrize('case', ['baseline', 'jump-closure'])
def test_sfc_graph_web_preview_and_mcp_candidate_use_the_shared_current_source(tmp_path, monkeypatch, case):
    import zipfile
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save, _native_sfc_graph_output
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import read_sfc_project_context
    from research.freeze_gxw_sfc_application_graph import ARCHIVE, EDITS as ACTION_EDITS
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    prefix = 'jump-edits/jump-closure/' if case == 'jump-closure' else 'baseline/'
    EDITS = ([dict(kind='jump', x=0, y=6, before_step_reference=0, target_step_reference=1)] + ACTION_EDITS
             if case == 'jump-closure' else ACTION_EDITS)
    with zipfile.ZipFile(ARCHIVE) as z:
        files = {name.removeprefix(prefix): z.read(name) for name in z.namelist() if name.startswith(prefix)}
    raw = files['input.gxw']; calls = []
    def save(_self, prepared, *, timeout=None, graph_edits=None):
        assert prepared.baseline == raw and graph_edits == EDITS and timeout is None
        prefix = 'save-save_source/'
        assert native_source_plan(prepared, graph_edits=graph_edits) == native_request_semantics(
            json.loads(files[prefix+'request.json'])['source'])
        calls.append('save')
        return verify_native_save(prepared, files[prefix+'candidate.gxw'], load_frozen_native_json(files[prefix+'observation.json']),
            graph_edits=graph_edits, graph_body=files[prefix+'sfc-graph-after.bin'])
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    context = read_sfc_project_context(raw)
    prepared = prepare_sfc_project_write(raw, context.program_name, context.source, {})
    measured = save(None, prepared, graph_edits=EDITS)
    measured_model = read_sfc_project_context(measured.data).object_model()
    calls.clear()
    def read_graph(_self, current, program_name=None, *, timeout=None):
        assert current == raw and timeout is None and case == 'jump-closure'
        prefix = 'read-read_sfc_graph/'
        assert files[prefix + 'input.gxw'] == raw
        body = files[prefix + 'sfc-graph-after.bin']
        _, graph = _native_sfc_graph_output(prepared, {**native_source_plan(prepared), 'graph_edits': []},
            body, load_frozen_native_json(files[prefix + 'observation.json']))
        graph.update(logical_name=context.program_name, source_body_hex=body.hex())
        calls.append('read')
        return graph
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', read_graph)
    app = _app(tmp_path/'sfc-workspace', tmp_path/'sfc-state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects', headers=headers, json={'name':'SFC graph', 'target_mode':'ladder'}).json()['id']
        imported = client.post('/api/sfc/proposals', headers=headers, json=dict(operation='import',project_id=pid,request_id='sfc-import',data_base64=base64.b64encode(raw).decode()))
        assert imported.status_code == 201
        base = imported.json()['result']['version_id']
        command = dict(project_id=pid,version_id=base,graph_edits=EDITS)
        for invalid in [True,1.5,-1,65536]:
            rejected = client.post('/api/sfc/preview', headers=headers, json={**command,'graph_edits':[dict(step_reference=invalid,action_number=10)]})
            assert rejected.status_code == 422
            rejected_jump = client.post('/api/sfc/preview', headers=headers, json={**command, 'graph_edits':[
                dict(kind='jump', x=0, y=6, before_step_reference=0, target_step_reference=invalid)]})
            assert rejected_jump.status_code == 422
        assert not calls
        if case == 'jump-closure':
            # Coordinate edits require the original getter's current grid.
            assert client.post('/api/sfc/preview', headers=headers, json=command).status_code == 400
            graph = client.get('/api/sfc/source', headers=headers,
                params=dict(project_id=pid, version_id=base, native=True))
            assert graph.status_code == 200, graph.text
            from application.projects import ProjectError
            stored = SessionToolContextProvider(tmp_path/'sfc-workspace', pid, base).get_context().sfc_graph
            assert stored is not None
            stale = deepcopy(stored)
            stale['source_body_hex'] = '00' + stale['source_body_hex'][2:]
            with pytest.raises(ProjectError, match='does not match'):
                app.state.service.projects.record_sfc_graph(pid, base, context.program_name, stale)
            assert app.state.service.projects.artifact(pid, base, 'gxw').read_bytes() == raw
        preview = client.post('/api/sfc/preview',headers=headers,json=command)
        assert preview.status_code == 200, preview.text
        saved = client.post('/api/sfc/proposals',headers=headers,json={**command,'operation':'edit','request_id':'sfc-graph'})
        assert saved.status_code == 201, saved.text
        version = saved.json()['result']['version_id']
        current = client.get('/api/sfc/source',params={'project_id':pid,'version_id':version}).json()['model']
        assert current == preview.json()['program']
        assert current['graph']['position_resolution'] == 'not-established'
        assert _sfc_source_model(current) == _sfc_source_model(measured_model)
        assert SessionToolContextProvider(tmp_path/'sfc-workspace', pid, version).get_context().sfc_graph is None
        if case == 'jump-closure':
            assert [edge['target'] for edge in current['graph']['source_graph']['edges']
                    if edge['kind'] == 'jump'] == ['step:1']
        assert calls == (['read'] if case == 'jump-closure' else [])
        transport = MCPToolAdapter(build_default_tool_runtime(),SessionToolContextProvider(tmp_path/'sfc-workspace',pid,version))
        read = transport.call_tool('read_sfc_project',{},'read-current')
        assert not read.is_error and read.structured_content['data']['model'] == current
        proposed = MCPToolAdapter(build_default_tool_runtime(),SessionToolContextProvider(tmp_path/'sfc-workspace',pid,base)).call_tool(
            'create_sfc_candidate',{'operation':'edit','graph_edits':EDITS},'graph-candidate')
        assert not proposed.is_error and proposed.structured_content['data']['model'] == current
        assert calls == (['read'] if case == 'jump-closure' else [])


def test_sfc_checked_graph_cell_is_shared_by_application_and_mcp(tmp_path, monkeypatch):
    import zipfile
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    from application.native_validation import validate_native_snapshot
    from agent_runtime.plc_tools import build_tool_context
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.tool_adapter import MCPToolAdapter
    import gxworks2.workspace_adapter as adapter
    import application.execution as execution
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read('action-error/candidate.gxw')
        check = json.loads(z.read('action-error/check.json'))['data']['data']
        name = next(n for n in z.namelist() if n.startswith('action-error/workspace/') and n.endswith('v0002/write_report.json'))
        geometry = json.loads(z.read(name))['sfc_graph']
    calls = []
    def validate(_self, data, *, timeout=None):
        assert data == raw and timeout is None
        calls.append('check'); return deepcopy(check)
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, 'validate', validate)
    resource_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: resource_lock(tmp_path/'native.lock'))
    web = validate_native_snapshot(raw,project_id='sfc-test',version_id='v0002',program='Block.Program.pou',
                                   target_mode='sfc',expected_cpu='FX3U/FX3UC',sfc_graph=geometry)
    context = build_tool_context({'id':'sfc-test','plc_model':'FX3U/FX3UC'},
        version={'id':'v0002','target_mode':'sfc','program_name':'Block.Program.pou'},native_snapshot=raw,sfc_graph=geometry)
    class Provider:
        def get_context(self): return context
    result = MCPToolAdapter(build_default_tool_runtime(),Provider()).call_tool('validate_project',{'native':True},'sfc-graph-check')
    assert not result.is_error and result.structured_content['data'] == web
    row, = web['diagnostic_projection']['source_diagnostics']
    assert row['graph_projection']['cell']['id'] == 'cell:1:3'
    assert row['source_projection']['native_location_query']['code'] == 0x2d010025
    assert web['public_check'] == {'status':'rejected','completed':True} and calls == ['check','check']


@pytest.mark.parametrize('case,status', [('sfc-grown', 'passed'), ('sfc-zoom-error', 'rejected'),
                                       ('sfc-transition-error', 'rejected'), ('sfc-zoom-wide', 'rejected'),
                                       ('sfc-transition-wide', 'rejected')])
def test_imported_sfc_web_and_mcp_check_the_saved_current_child_source(tmp_path, native_replay, case, status):
    pytest.importorskip('mcp')
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    raw, _, calls = native_replay(case)
    app = _app(tmp_path / 'sfc-workspace', tmp_path / 'sfc-state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={'name': 'SFC current source',
            'plc_model': 'FX3U', 'target_mode': 'ladder'}).json()
        pid = project['id']
        command = {'operation': 'import', 'project_id': pid, 'request_id': 'sfc-import',
                   'data_base64': base64.b64encode(raw).decode()}
        assert client.post('/api/sfc/proposals', json=command).status_code == 403
        response = client.post('/api/sfc/proposals', headers=headers, json=command)
        assert response.status_code == 201, response.text
        proposal = response.json()
        vid = proposal['result']['version_id']
        before = app.state.service.projects.raw_project(pid)
        source = client.get('/api/sfc/source', headers=headers, params={'project_id': pid, 'version_id': vid})
        assert source.status_code == 200, source.text
        model = source.json()['model']
        assert model['program'] == 'Block.Program.pou' and model['cpu'] == 'FX3U/FX3UC'
        assert len(model['children']) == 5 and model['graph']['position_resolution'] == 'not-established'
        assert client.get(f'/api/projects/{pid}/versions/{vid}/program', headers=headers).json() == model
        preview = client.get(f"/api/proposals/{proposal['id']}/preview", headers=headers)
        assert preview.status_code == 200 and preview.json()['program'] == model, preview.text
        route = f'/api/projects/{pid}/versions/{vid}/native-validation/check'
        checked = client.post(route, headers=headers, json={'request_id': 'sfc-check'})
        assert checked.status_code == 200, checked.text
        result = checked.json()['data']['data']
        assert result['public_check']['completed'] and result['public_check']['status'] == status
        assert result['compilation']['acceptance'] == 'accepted' and result['current_source_check'] == 'established'
        if status == 'rejected':
            diagnostics = result['diagnostic_projection']['source_diagnostics']
            diagnostic = diagnostics[-1]
            location = diagnostic['source_projection']
            assert location['source']['body_id'] != location['source']['parent_body_id']
            assert location['position_interpretation'] == 'not-established'
            expected_step = 6 if case.endswith('wide') else 0
            assert location['instruction_position']['status'] == 'source-resolved'
            assert location['instruction_position']['position']['native_step'] == expected_step
            assert location['instruction_position']['position']['op'] == 'OUT'
            if case == 'sfc-transition-wide':
                assert len(diagnostics) == 2
                assert diagnostics[0]['source_projection']['instruction_position']['position']['op'] == 'MOV'
        assert client.post(route, headers=headers, json={'request_id': 'sfc-check'}).json() == checked.json()
        assert len(calls) == 1 and app.state.service.projects.raw_project(pid) == before
        standalone = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(
            tmp_path / 'sfc-workspace', pid, vid)).call_tool('read_sfc_project', {}, 'read-sfc')
        assert not standalone.is_error and standalone.structured_content['data']['model'] == model
        if case.endswith('wide'):
            native_check = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(
                tmp_path / 'sfc-workspace', pid, vid)).call_tool('validate_project', {'native': True}, 'sfc-position-check')
            assert not native_check.is_error and native_check.structured_content == checked.json()['data']
            assert len(calls) == 2
        assert app.state.service.projects.artifact(pid, vid, 'gxw').read_bytes() == raw


def test_sfc_agent_import_uses_the_shared_candidate_transaction(service):
    from tests.test_gxw_lossless import sfc_object_edit_witness
    raw = sfc_object_edit_witness()['shared-save/input.gxw']
    project = service.create_project(name='SFC Agent import', target_mode='ladder')
    command = {'project_id': project['id'], 'name': 'create_sfc_candidate', 'call_id': 'sfc-candidate',
               'arguments': {'operation': 'import', 'data_base64': base64.b64encode(raw).decode()}}
    response = service.agent_call(command)
    assert not response['is_error'], response
    vid = accept(service, service.proposals.get(response['proposal_id']))
    assert service.projects.raw_version(project['id'], vid)['target_mode'] == 'sfc'
    assert service.projects.artifact(project['id'], vid, 'gxw').read_bytes() == raw
    assert service.agent_call(command) == response and len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.mcp_activity(project['id'])['candidate_proposal_id'] == response['proposal_id']


# Original L02 simple/non-label source from the version-bound CSV import.
# Independent GX CSV readback: LD M0; MOV K5 D100; END. Header/trailer kept.
L02_SIMPLE_SOURCE = bytes.fromhex(
    '01000000000001000000000001000a00000000000200000001000000010000000000'
    'ea070a0002000d0006003a003100000000000000012b0000002b00000001000000'
    '0c00000000000000ffffffff03000304900004054c02000504e8050404a86404033403'
    '000000000000000000000000000000000000000000000000')

# Original 1.635.0.1 simple/non-label source bodies after CSV readback.
# Independent listings: FX1S LD M0 / OUT M8028; Q03UDE and L26-BT
# LD M0 / AND status / ANI M1 / OUT M20. Each listing ends with END.
MODEL_DIFFERENCE_SIMPLE_SOURCES = [
    ('FX1S','M8028',
     '01000000000001000000000001000a00000000000200000001000000010000000000'
     'ea070a0002000d00080000002800000000000000012700000027000000010000000c'
     '00000000000000ffffffff030003049000040420020405905c1f05033403000000000000000000000000000000000000000000000000'),
    ('Q03UDE','SD1282.0',
     '01000000000001000000000001000a00000000000200000001000000010000000000'
     'ea070a0002000d00080009000d00000000000000013800000038000000010000000c'
     '00000000000000ffffffff03000304900004030c0304f2000405a9020505030d030490010403200304901404033403000000000000000000000000000000000000000000000000'),
    ('L26-BT','SB0041',
     '01000000000001000000000001000a00000000000200000001000000010000000000'
     'ea070a0002000d0008000b001100000000000000013300000033000000010000000c'
     '00000000000000ffffffff03000304900004030c0304a14104030d030490010403200304901404033403000000000000000000000000000000000000000000000000'),
]


@pytest.mark.parametrize('cpu,device,source_hex',MODEL_DIFFERENCE_SIMPLE_SOURCES)
@pytest.mark.parametrize('damage',[None,'device','cpu'])
def test_native_difference_witness_binds_exact_cpu_and_special_address(monkeypatch,cpu,device,source_hex,damage):
    from agent_runtime.plc_tools import build_tool_context, build_default_tool_registry
    from plc.ir import build_plc_ir
    from tests.test_simulator_verification import _bounded_ladder
    import gxw.native_diagnostics as diagnostics
    import gxw.diagnostic_sources as source_owner
    import gxworks2.workspace_adapter as adapter
    chosen={'M8028':'M8029','SD1282.0':'SD1282.1','SB0041':'SB0042'}[device] if damage=='device' else device
    ladder=(_bounded_ladder((['NO M0'],['COIL '+chosen])) if cpu=='FX1S' else
            _bounded_ladder((['NO M0','NO '+chosen,'NC M1'],['COIL M20'])))
    selected={'FX1S':'FX1N','Q03UDE':'Q03UDV','L26-BT':'L02'}[cpu] if damage=='cpu' else cpu
    program=build_plc_ir(ladder,plc_model=selected)
    source={'name':'MAIN','logical_name':'MAIN.Program.pou','program_kind':1,'raw':bytes.fromhex(source_hex)}
    monkeypatch.setattr(source_owner,'_native_validation_sources',lambda _:({'cpu':cpu,'codepage':936,'text_encoding':'gb18030'},[source]))
    monkeypatch.setattr(source_owner,'_native_validation_library_sources',lambda _:[])
    calls=[]
    def validate(self,raw,**kwargs):
        calls.append(raw)
        return {'compilation':{'completed':True,'acceptance':'accepted'}}
    monkeypatch.setattr(adapter.NativeWorkspaceValidation,'validate',validate)
    context=build_tool_context({'id':'source-witness','plc_model':selected,'target_mode':'ladder'},
        version={'id':'v1','target_mode':'ladder','plc_model':selected,'program_name':'MAIN'},
        program_ir=program,native_snapshot=b'original-source-witness')
    result=build_default_tool_registry().call('validate_project',{'native':True},context)
    assert result['ok'] is (damage is None),result
    assert len(calls)==int(damage is None)
    if damage is None:
        assert result['data']['snapshot']['candidate_binding']['status']=='current'


@settings(max_examples=60, derandomize=True)
@given(m=st.integers(min_value=0, max_value=255), k=st.integers(min_value=0, max_value=255),
       d=st.integers(min_value=0, max_value=255))
def test_l02_lexical_subset_preserves_original_envelope_and_independent_operand_values(m,k,d):
    from gxw.token_listing import decode_token_listing
    raw = bytearray(L02_SIMPLE_SOURCE)
    # Native witness offsets; expected values do not come from a product writer.
    raw[84], raw[93], raw[97] = m,k,d
    listing = decode_token_listing(bytes(raw), profile='l02')
    assert listing.reconstruct() == bytes(raw)
    assert listing.instruction_ir() == [{'op':'LD','args':['M'+str(m)]},
        {'op':'MOV','args':['K'+str(k),'D'+str(d)]}, {'op':'END','args':[]}]


@pytest.mark.parametrize('cpu', ['L02','Q03UDV','FX1S','Q_UNKNOWN','LJ72MS15'])
def test_simple_ladder_native_plan_retains_real_body_and_requires_programmable_identity(monkeypatch,cpu):
    import gxw.native_diagnostics as diagnostics
    import gxw.diagnostic_sources as source_owner
    context = {'cpu':cpu,'codepage':936}
    source = {'name':'MAIN','logical_name':'MAIN.Program.pou','program_kind':1,'raw':L02_SIMPLE_SOURCE}
    monkeypatch.setattr(source_owner,'_native_validation_sources',lambda raw:(context,[source]))
    monkeypatch.setattr(source_owner,'_native_validation_library_sources',lambda raw:[])
    if cpu in {'Q_UNKNOWN','LJ72MS15'}:
        with pytest.raises(ValueError,match='known programmable native CPU identity'):
            diagnostics.native_validation_source_plan(b'snapshot')
    else:
        plan = diagnostics.native_validation_source_plan(b'snapshot')
        assert base64.b64decode(plan['programs'][0]['body']) == L02_SIMPLE_SOURCE[59:102]
        assert plan['programs'][0]['program_kind']==1


@pytest.mark.parametrize('language,library,expected', [(1, '', 'source-resolved'), (193, '', 'unresolved'),
    (True, '', 'unresolved'), (1, 'foreign', 'unresolved')])
def test_compiled_task_root_language_uses_its_actual_local_source(language, library, expected):
    from gxw.diagnostic_declarations import _native_task_source_bindings
    bodies = {'sources': [dict(pou='MAIN', status='current', body_id=[1] * 12)]}
    result = _native_task_source_bindings([dict(resource='MAIN', task='MAIN', programs=['MAIN'])],
        {'MAIN': L02_SIMPLE_SOURCE}, bodies, [], [dict(source='MAIN', instance='MAIN', resource='MAIN',
            task='MAIN', library=library, program_kind=language)], project_id=[2] * 12, native_version='1.635.0.1')
    assert result[0]['status'] == expected


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
def test_q_candidate_binding_keeps_labels_titles_trailing_text_and_one_end(cpu):
    import struct
    from tests.test_gxw_offline_application import q_native_origin_projects
    from gxw.offline_project import read_offline_project, read_ordered_program, edit_ladder_project
    from gxw.project_writer import write_project_replacements
    from gxw.token_encoder import encode_text_record
    from gxw.token_pou import frame_token_pou
    from gxw.token_profiles import source_token_profile
    from gxw.diagnostic_sources import bind_native_ladder_candidate
    raw = q_native_origin_projects()[cpu]
    before = read_offline_project(raw)
    ordered = read_ordered_program(before)
    source_name = ordered['selected']['source_name']
    first = next(row for row in ordered['source_records'] if row.get('op') == 'LD')
    raw = edit_ladder_project(raw, source_name, [dict(operation='insert_before', record_index=first['record_index'],
        expected=dict(op=first['op'], args=first['args']), instructions=[
            dict(kind='statement', text='Circuit title'), dict(op='P10', args=[])])], save_policy='draft').data
    project = read_offline_project(raw)
    source = project.streams[source_name]
    frame = frame_token_pou(source)
    note = encode_text_record('note', 'Trailing text', profile=source_token_profile(cpu),
                              text_encoding=project.context['text_encoding'])
    changed = bytearray(source[:frame.body_end] + note + source[frame.body_end:])
    for offset in (frame.body_offset - 24, frame.body_offset - 20):
        struct.pack_into('<I', changed, offset, struct.unpack_from('<I', source, offset)[0] + len(note))
    saved = write_project_replacements(raw, {source_name: (project.mapping[source_name], source, bytes(changed))}).data
    current = read_ordered_program(read_offline_project(saved), source_name)
    assert current['coverage']['parse_status'] == 'complete'
    assert current['coverage']['unknown_instructions'] == []
    assert current['coverage']['semantic_status'] == 'unverified'
    binding = bind_native_ladder_candidate(saved, current['program_ir'], source_name)
    assert binding['cpu'] == cpu and binding['status'] == 'current'
    assert sum(row.get('op') == 'END' for row in binding['instructions']) == 1
    assert next(row for row in binding['instructions'] if row.get('kind') == 'label')['text'] == 'P10'
    first = next(row for row in current['source_records'] if row.get('op') == 'LD')
    edited = edit_ladder_project(saved, source_name, [dict(operation='replace', record_index=first['record_index'],
        expected=dict(op='LD', args=first['args']), instructions=[dict(op='LD', args=['M2'])])], save_policy='draft').data
    reread = read_ordered_program(read_offline_project(edited), source_name)
    assert reread['source_records'][-1]['text'] == 'Trailing text'
    assert next(row for row in reread['source_records'] if row.get('op') == 'LD')['args'] == ['M2']
    assert bind_native_ladder_candidate(edited, reread['program_ir'], source_name)['status'] == 'current'
    trailing = reread['source_records'][-1]
    with pytest.raises(ValueError, match='terminal END'):
        edit_ladder_project(edited, source_name, [dict(operation='insert_before', record_index=trailing['record_index'],
            expected=dict(kind=trailing['kind'], text=trailing['text']), instructions=[dict(op='NOP', args=[])])],
            save_policy='draft')
    tampered = deepcopy(current['program_ir'])
    label = next(row for network in tampered['networks'] for row in network['ladder']['ordered_instructions']
                 if row.get('kind') == 'label')
    label['text'] = 'P11'
    with pytest.raises(ValueError, match='instruction list differs'):
        bind_native_ladder_candidate(saved, tampered, source_name)


def native_body_envelope_cases():
    return json.loads(private_path('tests/fixtures/gxw_native_body_envelopes.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', native_body_envelope_cases()['cases'], ids=lambda case: case['id'])
def test_ladder_native_body_accepts_independent_source_tails_and_retains_the_self_sized_header(case):
    from zipfile import ZipFile
    from gxw.diagnostic_sources import _native_validation_source_body
    with ZipFile(private_path(native_body_envelope_cases()['archive'])) as archive:
        raw = archive.read(case['source_member'])
        expected = archive.read(case['body_member'])
    actual = _native_validation_source_body(raw)
    assert actual == expected
    assert len(actual) == case['body_bytes'] == int.from_bytes(actual[:4], 'little')
    assert raw[case['body_offset'] + len(actual):] == raw[-case['source_tail_bytes']:]
    assert actual[:20] == expected[:20] and len(actual) > 20


@pytest.mark.parametrize('case', native_body_envelope_cases()['cases'], ids=lambda case: case['id'])
@pytest.mark.parametrize('damage', ['tail', 'size', 'token'])
def test_ladder_native_body_still_rejects_malformed_source_boundaries(case, damage):
    from zipfile import ZipFile
    from gxw.diagnostic_sources import _native_validation_source_body
    from gxw.models import GXWFormatError
    with ZipFile(private_path(native_body_envelope_cases()['archive'])) as archive:
        raw = bytearray(archive.read(case['source_member']))
    if damage == 'tail':
        del raw[-5:]
    elif damage == 'size':
        raw[case['body_offset'] - 4] ^= 1
    else:
        raw[case['body_offset'] + 20] = 0
    with pytest.raises(GXWFormatError):
        _native_validation_source_body(bytes(raw))


@pytest.mark.parametrize('language', ['st', 'fbd'])
def test_ladder_tail_support_does_not_relax_other_native_source_envelopes(language):
    from zipfile import ZipFile
    from gxw.diagnostic_sources import _native_validation_source_body
    from gxw.models import GXWFormatError
    from gxw.source_header import source_payload_offset
    if language == 'fbd':
        from gxw.object_model import read_project, default_baseline
        program, _, _ = read_project(default_baseline())
        raw = program.raw
    else:
        from gxw.offline_project import read_offline_project
        with ZipFile(private_path('research/' + 'evidence/gxw-offline-l-st-20261021.zip')) as archive:
            project = read_offline_project(archive.read('native-baseline.gxw'))
        raw, = [value for name, value in project.streams.items() if name.endswith('.pou')
                and value[source_payload_offset(value)] == 193]
    assert raw[source_payload_offset(raw)] == (193 if language == 'st' else 208)
    assert _native_validation_source_body(raw)
    with pytest.raises(GXWFormatError, match='outside the observed native body envelope'):
        _native_validation_source_body(raw[:-4])


@pytest.mark.parametrize('damage', [None, 'constant', 'condition', 'target', 'cpu', 'missing_ir', 'unknown_opcode'])
def test_simple_ladder_native_candidate_binding_never_launches_for_another_program(monkeypatch, damage):
    from agent_runtime.plc_tools import build_tool_context, build_default_tool_registry
    from plc.ir import build_plc_ir
    from tests.test_simulator_verification import _bounded_ladder
    import gxw.native_diagnostics as diagnostics
    import gxw.diagnostic_sources as source_owner
    import gxworks2.workspace_adapter as adapter
    ladder = _bounded_ladder((['NO M0'], ['MOV K5 D100']))
    if damage == 'constant': ladder['rungs'][0]['branches'][0]['outputs'][0]['operands'][0] = 'K6'
    if damage == 'condition': ladder['rungs'][0]['branches'][0]['inputs'][0]['address'] = 'M1'
    if damage == 'target': ladder['rungs'][0]['branches'][0]['outputs'][0]['operands'][1] = 'D101'
    program = build_plc_ir(ladder, plc_model='Q03UDV' if damage == 'cpu' else 'L02')
    original = L02_SIMPLE_SOURCE
    if damage == 'unknown_opcode':
        original = original[:87] + b'\x4d' + original[88:]
    context_info = {'cpu': 'L02', 'text_encoding': 'gb18030'}
    sources = [{'name': 'MAIN', 'logical_name': 'MAIN.Program.pou', 'program_kind': 1, 'raw': original}]
    monkeypatch.setattr(source_owner, '_native_validation_sources', lambda raw: (context_info, sources))
    monkeypatch.setattr(diagnostics, 'native_validation_source_plan', lambda *a, **k:
                        {**context_info, 'programs': [{k: v for k, v in row.items() if k != 'raw'} for row in sources]})
    calls = []
    def validate(self, raw, **kwargs):
        calls.append(raw)
        return {'compilation': {'completed': True, 'acceptance': 'accepted'}}
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, 'validate', validate)
    context = build_tool_context({'id': 'isolated-ladder', 'plc_model': 'L02', 'target_mode': 'ladder'},
        version={'id': 'v1', 'target_mode': 'ladder', 'plc_model': 'L02', 'program_name': 'MAIN'},
        program_ir=None if damage == 'missing_ir' else program, native_snapshot=b'original-saved-l02')
    result = build_default_tool_registry().call('validate_project', {'native': True}, context)
    assert result['ok'] is (damage is None)
    assert len(calls) == int(damage is None)
    if damage is None:
        binding = result['data']['snapshot']['candidate_binding']
        assert binding['status'] == 'current'
        assert binding['instructions'] == [{'op': 'LD', 'args': ['M0']}, {'op': 'MOV', 'args': ['K5','D100']}, {'op': 'END', 'args': []}]


@pytest.mark.parametrize('cpu,text,allowed', [('L02','IF M0 THEN D100 := 5; END_IF;',True),
    ('Q03UDV','IF M0 THEN D100 := 5; END_IF;',False),('L02','D100 := 0;',False)])
def test_saved_st_native_snapshot_is_cpu_and_source_bound(monkeypatch,cpu,text,allowed):
    from types import SimpleNamespace
    from agent_runtime.plc_tools import build_tool_context, build_default_tool_registry
    import gxw.native_diagnostics as diagnostics
    import gxw.text_pou as text_pou
    import gxworks2.workspace_adapter as adapter
    plan={'cpu':'L02','programs':[{'logical_name':'MAIN.Program.pou','name':'MAIN','program_kind':193}]}
    monkeypatch.setattr(diagnostics,'native_validation_source_plan',lambda *a,**k:deepcopy(plan))
    monkeypatch.setattr(diagnostics,'_native_validation_sources',lambda raw:({},[{'logical_name':'MAIN.Program.pou','raw':b'source'}]))
    monkeypatch.setattr(text_pou,'parse_st_pou',lambda raw:SimpleNamespace(text='IF M0 THEN D100 := 5; END_IF;'))
    calls=[]
    def validate(self,raw,**kwargs):
        calls.append(raw)
        return {'status':'unverified','cpu':'L02','compilation':{'completed':False}}
    monkeypatch.setattr(adapter.NativeWorkspaceValidation,'validate',validate)
    context=build_tool_context({'id':'isolated','plc_model':cpu,'target_mode':'st'},
        version={'id':'v1','target_mode':'st','plc_model':cpu,'program_name':'MAIN'},
        st_text=text,native_snapshot=b'saved-gxw')
    result=build_default_tool_registry().call('validate_project',{'native':True},context)
    assert result['ok'] is allowed
    assert len(calls)==int(allowed)
    if allowed:
        assert result['data']['snapshot']['version_id']=='v1'
        assert result['data']['compilation']['completed'] is False


def test_static_context_preserves_st_source_and_native_snapshot():
    from agent_runtime.plc_tools import build_tool_context
    from integrations.mcp.context_provider import StaticToolContextProvider
    context=build_tool_context({'id':'isolated','plc_model':'L02','target_mode':'st'},
        version={'id':'v1','target_mode':'st'},st_text='D0 := 1;',native_snapshot=b'bound')
    loaded=StaticToolContextProvider(context).get_context()
    assert loaded.st_text==context.st_text and loaded.native_snapshot==b'bound'


def test_engineering_acceptance_reports_conditional_and_all_candidate_denominators():
    from application.engineering_acceptance import acceptance_record, acceptance_summary
    first = acceptance_record(cpu="FX3U", results={"identity_interface": {"status": "passed", "evidence": "source binding"},
        "save_cold_read": {"status": "failed", "reason": "lost port binding"}})
    second = acceptance_record(cpu="FX3U", results={"identity_interface": {"status": "failed", "reason": "wrong source"},
        "save_cold_read": {"status": "not_entered", "reason": "prior layer failed"}})
    summary = acceptance_summary([first, second])
    assert summary["layers"][1]["entering"] == 1
    assert summary["layers"][1]["counts"] == {"failed": 1, "not_entered": 1}
    assert summary["layers"][0]["all_candidate_pass_rate"] == .5
    assert summary["layers"][-1]["counts"] == {"unverified": 2}
    with pytest.raises(ValueError, match="actual evidence"):
        acceptance_record(cpu="FX3U", results={"current_source_compile": {"status": "passed"}})


def command(digest, **updates):
    return {"request_id": "native-result", "source_gxw_sha256": digest, "operator": "工程师甲",
            "tool_version": "GX Works2 1.6xx", "outcome": "passed", "report": "模拟的操作员报告：编译完成，0 错误。",
            "attested": True, **updates}


def test_draft_preview_matches_candidate_without_creating_proposals_or_versions(service):
    from tests.test_web_fbd import fresh_two_timers
    project = service.create_project(name="Preview", target_mode="fbd")
    before = deepcopy(service.projects.raw_project(project["id"]))
    preview = service.fbd.preview(project["id"], fresh_two_timers())
    assert "TIMER_A" in preview["svg"] and preview["gx_compile"] == "not_run"
    assert service.proposals.list(project["id"]) == []
    assert service.projects.raw_project(project["id"]) == before
    proposal = service.fbd.propose({"operation": "generate", "project_id": project["id"],
                                   "request_id": "confirm", "model": fresh_two_timers()})
    assert proposal["status"] == "accepted"
    assert len(service.projects.raw_project(project["id"])["versions"]) == 1
    candidate = service.proposal_preview(proposal["id"])
    assert candidate["svg"] == preview["svg"]
    vid = accept(service, proposal)
    assert service.projects.program(project["id"], vid) == preview["model"]
    draft = service.projects.program(project["id"], vid)
    draft["nodes"][-1]["symbol"] = "Y1"
    changed = service.fbd.preview(project["id"], draft, vid)
    assert "Y1" in changed["svg"] and changed["svg"] != preview["svg"]
    draft["nodes"][0]["x"] = -1
    with pytest.raises(FBDValidationError, match="coordinates"):
        service.fbd.preview(project["id"], draft, vid)


def test_native_report_persists_exact_source_binding_without_upgrading_compilation(service):
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    native = NativeValidationService(service)
    initial = native.list(project["id"], vid)
    assert initial["records"] == [] and initial["automatic_verification"] == "not_run"
    source = service.projects.artifact(project["id"], vid, "gxw").read_bytes()
    upload = {"filename": "saved.gxw", "data_base64": base64.b64encode(source).decode()}
    payload = command(initial["source_gxw_sha256"], native_gxw=upload)
    record = native.record(project["id"], vid, payload)
    assert native.record(project["id"], vid, payload) == record
    assert record["binding_current"] and record["native_gxw"]["matches_source_bytes"]
    assert record["native_gxw"]["selected_program_matches"] and record["native_gxw"]["integrity_verified"]
    assert record["automatic_verification"] == "not_run"
    assert native.artifact(project["id"], vid, record["id"]).read_bytes() == source
    assert service.projects.version(project["id"], vid)["validation"]["gx_compile"] == "not_run"
    assert NativeValidationService(service).list(project["id"], vid)["records"] == [record]
    with pytest.raises(ConflictError, match="request ID"):
        native.record(project["id"], vid, {**payload, "outcome": "failed"})


def test_changed_source_and_native_attachment_never_inherit_reported_pass(service):
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    native = NativeValidationService(service)
    initial = native.list(project["id"], vid)
    source = service.projects.artifact(project["id"], vid, "gxw")
    model = service.projects.program(project["id"], vid)
    model["nodes"][-1]["symbol"] = "Y1"
    changed_native = generate_object_project(model, baseline=source.read_bytes()).data
    payload = command(initial["source_gxw_sha256"], native_gxw={"filename": "changed.gxw", "data_base64": base64.b64encode(changed_native).decode()})
    record = native.record(project["id"], vid, payload)
    assert not record["native_gxw"]["matches_source_bytes"]
    assert not record["native_gxw"]["selected_program_matches"]
    attachment = native.artifact(project["id"], vid, record["id"])
    attachment.write_bytes(b"changed evidence")
    assert native.list(project["id"], vid)["records"][0]["native_gxw"]["integrity_verified"] is False
    with pytest.raises(ConflictError, match="evidence changed"):
        native.artifact(project["id"], vid, record["id"])
    source.write_bytes(source.read_bytes() + b"changed source")
    assert native.list(project["id"], vid)["records"][0]["binding_current"] is False
    with pytest.raises(ConflictError, match="GXW changed"):
        native.record(project["id"], vid, {**payload, "request_id": "retry"})


def test_report_is_scoped_to_version_and_read_only_session_cannot_record(service, tmp_path):
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    native = NativeValidationService(service)
    payload = command(native.list(project["id"], vid)["source_gxw_sha256"])
    native.record(project["id"], vid, payload)
    model = service.projects.program(project["id"], vid)
    model["nodes"][-1]["symbol"] = "Y1"
    newer = accept(service, service.fbd.propose({"operation": "edit", "project_id": project["id"],
        "version_id": vid, "request_id": "new-version", "model": model}))
    assert native.list(project["id"], newer)["records"] == []
    readonly = WorkbenchService(service.store.base_dir, tmp_path / "read-only-state", read_only=True)
    readonly_native = NativeValidationService(readonly)
    assert len(readonly_native.list(project["id"], vid)["records"]) == 1
    with pytest.raises(PermissionError):
        readonly_native.record(project["id"], vid, payload)
    assert not (tmp_path / "read-only-state").exists()


def test_copied_report_cannot_bind_to_another_version_with_identical_gxw(service):
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    native = NativeValidationService(service)
    source = service.projects.artifact(project["id"], vid, "gxw").read_bytes()
    upload = {"filename": "source.gxw", "data_base64": base64.b64encode(source).decode()}
    record = native.record(project["id"], vid, command(native.list(project["id"], vid)["source_gxw_sha256"], native_gxw=upload))
    path = service.store.version_dir(project["id"], vid) / "native-validation" / (record["id"] + ".json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version_id"] = "another-version-with-identical-bytes"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert native.list(project["id"], vid)["records"][0]["binding_current"] is False
    with pytest.raises(ConflictError, match="different project or version"):
        native.artifact(project["id"], vid, record["id"])
    with pytest.raises(ConflictError, match="already bound"):
        native.record(project["id"], vid, command(native.list(project["id"], vid)["source_gxw_sha256"], native_gxw=upload))


def test_http_routes_require_operator_csrf_and_explicit_attestation(tmp_path):
    app = _app(tmp_path / "workspace", tmp_path / "state")
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project, _, proposal = generate(app.state.service)
        vid = accept(app.state.service, proposal)
        route = f'/api/projects/{project["id"]}/versions/{vid}/native-validation'
        current = client.get(route)
        assert current.status_code == 200, current.text
        payload = command(current.json()["source_gxw_sha256"])
        assert client.post(route, json=payload).status_code == 403
        assert client.post(route, json={**payload, "attested": False}, headers=headers).status_code == 422
        response = client.post(route, json=payload, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["automatic_verification"] == "not_run"
        assert "command_hash" not in response.text
        model = app.state.service.projects.program(project["id"], vid)
        response = client.post('/api/fbd/preview', json={"project_id": project["id"], "version_id": vid, "model": model}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["gx_compile"] == "not_run"


@pytest.fixture
def native_replay(tmp_path, monkeypatch):
    """Replay frozen original collector bytes at the adapter process boundary."""
    import types
    import gxworks2.workspace_adapter as adapter
    import application.execution as execution
    from tests.test_gxw_compiler import (native_recheck_validation_case, native_source_capacity_case,
                                        native_compile_position_case, authored_fbd_validation_case, sfc_shared_check_witness,
                                        sfc_instruction_witness)
    executable = tmp_path / 'native-replay.exe'
    executable.write_bytes(b'collector transport double')
    original_init = adapter.NativeWorkspaceValidation.__init__
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, '__init__',
                        lambda self: original_init(self, executable, tmp_path))
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    resource_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: resource_lock(tmp_path / 'replay-native.lock'))
    calls = []

    def install(case):
        if case == 'sfc-retained-error':
            recorded = retained_sfc_diagnostic_witness()
            raw, observation, snapshots = recorded['raw'], recorded['observation'], recorded['snapshots']
            witness = {'cpu': 'FX3U/FX3UC'}
        elif case.startswith(('sfc-scope-', 'sfc-loader-')):
            from zipfile import ZipFile
            from research.freeze_gxw_sfc_application_graph import ARCHIVE
            prefix = ('source-loader/' + case.removeprefix('sfc-loader-') + '/' if case.startswith('sfc-loader-')
                      else 'source-scope/' + case.removeprefix('sfc-scope-') + '/')
            with ZipFile(ARCHIVE) as archive:
                raw = archive.read(prefix + 'input.gxw')
                observation = load_frozen_native_json(archive.read(prefix + 'observation.json'))
                snapshots = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                             if name.startswith(prefix) and name.endswith('.bin')}
                witness = {'cpu': 'FX3U/FX3UC'}
        elif case in ('sfc-zoom-wide', 'sfc-transition-wide'):
            recorded = sfc_instruction_witness('zoom-ldi-mov' if case == 'sfc-zoom-wide' else 'transition-ldi-mov')
            raw, observation, snapshots = recorded['raw'], recorded['observation'], recorded['snapshots']
            witness = {'cpu': recorded['request']['source']['cpu']}
        elif case in ('sfc-grown', 'sfc-zoom-error', 'sfc-transition-error'):
            recorded = (sfc_shared_check_witness() if case == 'sfc-grown' else sfc_shared_check_witness(
                'action-out-x0-count-fixed' if case == 'sfc-zoom-error' else 'transition-out-x0', diagnostic=True))
            raw, observation, snapshots = recorded['raw'], recorded['observation'], recorded['snapshots']
            witness = {'cpu': recorded['request']['source']['cpu']}
        elif case in ('public-conversion-sfc', 'public-conversion-sfc-layout-failure'):
            from tests.test_gxw_compiler import original_sfc_public_validation_case
            raw, observation, snapshots = original_sfc_public_validation_case()
            witness = {'cpu': 'Q06UDV'}
            if case.endswith('layout-failure'):
                report = next(report for event in observation['validation']['events'] if event.get('operation') == 'ProgramCheckOriginalReports'
                              for report in event['reports'] if report['kind'] == 2)
                report['arguments'][1]['text'] += 'changed'
        elif case == 'public-conversion-identity':
            from tests.test_gxw_compiler import public_identity_recheck_witness
            raw, observation, snapshots, _ = public_identity_recheck_witness()
            witness = {'cpu': 'Q06UDV'}
        elif case == 'public-conversion-motion':
            from zipfile import ZipFile
            from pathlib import Path
            prefix = 'production-Q06UDV/'
            with ZipFile(private_path('research/' + 'evidence/gxw-q-public-diagnostic-conversion-20261014.zip')) as archive:
                raw = archive.read(prefix + 'input.gxw')
                observation = load_frozen_native_json(archive.read(prefix + 'observation.json'))
                snapshots = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                             if name.startswith(prefix) and name.removeprefix(prefix).endswith('.bin')
                             and '/' not in name.removeprefix(prefix)}
            witness = {'cpu': 'Q06UDV'}
        elif case.startswith('authored-'):
            raw, observation, snapshots, witness = authored_fbd_validation_case(
                'application-'+case.removeprefix('authored-'), 'candidate')
            witness = {**witness, 'cpu': witness['expected']['cpu']}
        else:
            raw, observation, snapshots, witness = (native_compile_position_case() if case == 'FX3U-compile-position'
                                                  else native_source_capacity_case() if case == 'FX3G-source-capacity'
                                                  else native_recheck_validation_case(case))
        def collect(exe, request, root, timeout, error_type):
            assert exe == executable.resolve()
            assert request['operation'] == 'validate_project' and timeout is None
            assert (root / 'input.gxw').read_bytes() == raw
            assert request['source']['cpu'] == witness['cpu']
            assert all(set(row) == ({'name', 'program_kind', 'body', 'children'} if row['program_kind'] == 240
                                   else {'name', 'program_kind', 'body'}) for row in request['source']['programs'])
            calls.append(request)
            for name, data in snapshots.items():
                (root / name).write_bytes(data)
            return 0, deepcopy(observation)
        monkeypatch.setattr(adapter, '_run_native_adapter', collect)
        return raw, witness, calls
    return install


@pytest.mark.parametrize('case,status,kind', [('sfc-grown', 'passed', None),
                                             ('sfc-zoom-error', 'rejected', 'zoom'),
                                             ('sfc-transition-error', 'rejected', 'transition')])
def test_sfc_native_shared_check_transport_keeps_graph_children_and_owned_input_snapshots(native_replay, case, status, kind):
    from gxworks2.workspace_adapter import NativeWorkspaceValidation
    raw, witness, calls = native_replay(case)
    result = NativeWorkspaceValidation().validate(raw, timeout=None)
    assert len(calls) == 1
    assert 'compilation_method' not in calls[0]['source']
    program, = calls[0]['source']['programs']
    assert program['program_kind'] == 240 and len(program['children']) == 5
    assert result['cpu'] == witness['cpu'] == 'FX3U/FX3UC'
    assert result['source_bodies']['status'] == 'current'
    assert len(result['source_bodies']['sources'][0]['sfc_children']['children']) == 5
    assert result['owned_check_inputs']['correspondence'] == 'current'
    assert result['public_check'] == {'status': status, 'completed': True}
    assert result['current_source_check'] == 'established'
    assert result['current_raw_source_check'] == 'not_established'
    if kind:
        diagnostic, = result['diagnostic_projection']['source_diagnostics']
        assert diagnostic['source_projection']['source']['child']['kind'] == kind
        assert diagnostic['source_projection']['native_location_query']['code'] == 0x2d010025
    else:
        assert not result['diagnostic_projection']['source_diagnostics']


def import_native_case(service, raw):
    project = service.create_project(name='Current native check', target_mode='fbd')
    proposal = service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'native-source', 'program': 'FBD_MATRIX.Program.pou',
        'data_base64': base64.b64encode(raw).decode()})
    return project['id'], accept(service, proposal)


def test_saved_compile_rejection_exposes_current_program_networks_and_nodes(service, native_replay):
    raw, witness, calls = native_replay('FX3U-compile-position')
    project = service.create_project(name='Current Program compile rejection', target_mode='fbd')
    imported = service.fbd.propose({'operation': 'import', 'project_id': project['id'], 'request_id': 'compile-source',
        'program': witness['logical_name'], 'data_base64': base64.b64encode(raw).decode()})
    vid = accept(service, imported)
    before = deepcopy(service.projects.raw_project(project['id']))
    command = {'project_id': project['id'], 'version_id': vid, 'name': 'validate_project',
               'arguments': {'native': True, 'program': witness['logical_name']}, 'call_id': 'compile-position'}
    response = service.agent_call(command)
    assert not response['is_error'], response
    result = response['data']['data']
    assert result['compilation']['acceptance'] == 'rejected'
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['public_check'] == {'status': 'not_requested', 'completed': False}
    assert result['current_source_check'] == result['current_raw_source_check'] == 'not_established'
    for expected in witness['errors']:
        diagnostic, = [row for row in result['compilation']['source_diagnostics'] if row['original'] == expected['report']]
        assert diagnostic['source_projection']['source']['pou'] == witness['pou']
        assert diagnostic['source_projection']['source']['network'] == expected['network']
        assert diagnostic['graph_projection']['object']['symbol'] == expected['symbol']
        assert not diagnostic['source_projection']['public_check_promoted']
    assert service.agent_call(command) == response and len(calls) == 1
    assert service.projects.raw_project(project['id']) == before
    assert service.projects.artifact(project['id'], vid, 'gxw').read_bytes() == raw


def test_saved_native_check_reads_the_complete_65_source_engineering_snapshot(service, native_replay):
    raw, witness, calls = native_replay('FX3G-source-capacity')
    project = service.create_project(name='Complete library context', target_mode='fbd')
    proposal = service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'source-capacity', 'program': 'MAIN.Program.pou',
        'data_base64': base64.b64encode(raw).decode()})
    vid = accept(service, proposal)
    source = service.projects.artifact(project['id'], vid, 'gxw')
    assert source.read_bytes() == raw
    response = service.agent_call({'project_id': project['id'], 'version_id': vid,
        'name': 'validate_project', 'arguments': {'native': True}, 'call_id': 'complete-source-check'})
    assert not response['is_error'], response
    result = response['data']['data']
    assert result['snapshot']['requested_program'] == 'MAIN.Program.pou'
    assert result['snapshot']['cpu'] == witness['cpu'] == 'FX3G'
    assert result['public_check'] == {'status': 'passed', 'completed': True}
    assert result['current_source_check'] == 'established'
    assert len(result['source_bodies']['sources']) == 65
    assert len(calls) == 1
    assert len(calls[0]['source']['programs']) == 2 and len(calls[0]['source']['libraries']) == 63
    assert source.read_bytes() == raw


@pytest.mark.parametrize('name', ['motion', 'mapping'])
def test_saved_authored_fbd_check_preserves_library_nested_instances_and_distinct_results(service, native_replay, name):
    raw, witness, calls = native_replay('authored-'+name)
    project = service.create_project(name='Authored instance closure', plc_model=witness['cpu'], target_mode='fbd')
    proposal = service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='authored-source',
        program=witness['selected_pou'], data_base64=base64.b64encode(raw).decode()))
    vid = accept(service, proposal)
    command = dict(project_id=project['id'], version_id=vid, name='validate_project',
        arguments={'native': True, 'program': witness['selected_pou']}, call_id='authored-current-code')
    response = service.agent_call(command)
    assert not response['is_error'], response
    result = response['data']['data']
    assert result['snapshot']['requested_program'] == witness['selected_pou']
    assert result['snapshot']['cpu'] == witness['cpu']
    assert result['current_raw_source_check'] == 'established' and result['current_source_check'] == 'not_established'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    rows = result['diagnostic_projection']['source_diagnostics']
    assert len(rows) == witness['expected']['diagnostic_count']
    assert all(row['graph_projection']['status'] == 'uniquely_correlated' for row in rows)
    assert [(row['graph_projection']['instance'], row['graph_projection']['source']['zero_based_line'],
             row['graph_projection']['caller']['object_id']) for row in rows] == [
        (row['instance'], row['line'], row['object_id']) for row in witness['expected']['diagnostics']]
    assert len(result['source_bodies']['sources']) == witness['project_sources']+witness['library_sources']
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    assert service.agent_call(command) == response and len(calls) == 1
    assert service.projects.artifact(project['id'], vid, 'gxw').read_bytes() == raw


@pytest.mark.parametrize('case', ['Q04UDV', 'FX3U-FX3UC'])
def test_saved_native_check_keeps_current_input_and_all_distinct_results(service, native_replay, case):
    raw, witness, calls = native_replay(case)
    pid, vid = import_native_case(service, raw)
    before = deepcopy(service.projects.raw_project(pid))
    before_proposals = deepcopy(service.proposals.list(pid))
    command = {'project_id': pid, 'version_id': vid, 'name': 'validate_project',
               'arguments': {'native': True}, 'call_id': 'check-saved-version'}
    response = service.agent_call(command)
    assert not response['is_error'], response
    result = response['data']['data']
    assert result['snapshot'] == {'project_id': pid, 'version_id': vid,
        'requested_program': 'FBD_MATRIX.Program.pou', 'cpu': witness['cpu']}
    assert result['mode'] == 'native' and result['compilation']['acceptance'] == 'accepted'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['diagnostic_projection']['status'] == 'failed'
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['current_source_check'] == 'not_established'
    assert result['current_raw_source_check'] == 'established'
    assert [{'resource': row['original']['name']['text'], 'code': row['original']['code'],
        'step': row['original']['step'], 'arguments': [arg['text'] for arg in row['original']['arguments']]}
        for row in result['raw_diagnostics']] == witness['expected_original_raw_errors']
    assert result['native_source_locations']['status'] == 'completed'
    assert result['native_instance_locations']['status'] == 'completed'
    assert result['reopen']['status'] == 'not_observed' and not result['save']['completed']
    assert service.agent_call(command) == response and len(calls) == 1
    assert service.projects.raw_project(pid) == before
    assert service.projects.artifact(pid, vid, 'gxw').read_bytes() == raw
    assert NativeValidationService(service).list(pid, vid)['automatic_verification'] == 'not_run'
    assert service.proposals.list(pid) == before_proposals


def test_http_and_standalone_mcp_return_same_task_scope_for_a_requested_fb(tmp_path, native_replay):
    pytest.importorskip('mcp')
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    from tests.test_web_api import AGENT
    raw, _, calls = native_replay('FX3U-FX3UC')
    app = _app(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid, vid = import_native_case(app.state.service, raw)
        payload = {'project_id': pid, 'version_id': vid, 'name': 'validate_project',
            'arguments': {'native': True, 'program': 'MODULE_FB.Program.pou'}, 'call_id': 'operator-check'}
        assert client.post('/api/agent/tools/call', json=payload).status_code == 403
        assert client.post('/api/agent/tools/call', headers=headers, json=payload).status_code == 403
        route = f'/api/projects/{pid}/versions/{vid}/native-validation/check'
        command = {'request_id': 'operator-check', 'program': 'MODULE_FB.Program.pou'}
        assert client.post(route, json=command).status_code == 403
        activity = app.state.service.mcp_activity(pid)
        response = client.post(route, headers=headers, json=command)
        assert response.status_code == 200, response.text
        web = response.json()['data']
        assert web['ok'] and web['data']['snapshot']['requested_program'] == 'MODULE_FB.Program.pou'
        assert web['data']['selection']['selected_sources'] == ['FBD_MATRIX']
        assert web['data']['selection']['tasks'][0]['programs'] == ['FBD_MATRIX']
        assert app.state.service.mcp_activity(pid) == activity
        assert client.post(route, headers=headers, json=command).json() == response.json() and len(calls) == 1
        connected = client.post('/api/agent/tools/call', headers={'Authorization': 'Bearer ' + AGENT}, json=payload)
        assert connected.status_code == 200 and connected.json()['data'] == web
        provider = SessionToolContextProvider(tmp_path / 'workspace', pid, vid)
        adapter = MCPToolAdapter(build_default_tool_runtime(), provider)
        before = {str(path.relative_to(tmp_path / 'workspace')): path.read_bytes()
                  for path in (tmp_path / 'workspace').rglob('*') if path.is_file()}
        mcp = adapter.call_tool('validate_project', payload['arguments'], 'standalone-check')
        assert not mcp.is_error and mcp.structured_content == web
        assert json.loads(mcp.content[0].text) == web and len(calls) == 3
        assert before == {str(path.relative_to(tmp_path / 'workspace')): path.read_bytes()
                          for path in (tmp_path / 'workspace').rglob('*') if path.is_file()}


@pytest.mark.parametrize('case,program,count,public_status,identity', [
    ('public-conversion-motion', 'PrgAxis1.Program.pou', 23, 'incomplete', False),
    ('public-conversion-identity', 'FBD_MATRIX.Program.pou', 7, 'rejected', True),
])
def test_complete_public_conversion_reaches_web_and_standalone_mcp_without_promoting_vendor_check(
        tmp_path, native_replay, case, program, count, public_status, identity):
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    raw, witness, calls = native_replay(case)
    app = _app(tmp_path / 'public-conversion-workspace', tmp_path / 'public-conversion-state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={
            'name': 'Public conversion', 'plc_model': witness['cpu'], 'target_mode': 'fbd'}).json()
        imported = client.post('/api/fbd/proposals', headers=headers, json=dict(operation='import',
            project_id=project['id'], request_id='public-conversion-import', program=program,
            data_base64=base64.b64encode(raw).decode())).json()
        version = imported['result']['version_id']
        arguments = {'native': True, 'program': program}
        response = client.post(f"/api/projects/{project['id']}/versions/{version}/native-validation/check", headers=headers,
            json={'request_id': 'public-conversion-check', 'program': program})
        assert response.status_code == 200, response.text
        public = response.json()['data']
        result = public['data']
        conversion = result['public_diagnostic_conversion']
        assert conversion['completed'] and conversion['current_source'] == 'established'
        assert conversion['original_report_count'] == conversion['converted_report_count'] == count
        assert len(conversion['reports']) == count
        assert result['public_check'] == {'status': public_status, 'completed': identity}
        if identity:
            assert result['public_diagnostic_identity']['status'] == 'mismatched'
            assert result['diagnostic_projection']['status'] == 'identity_mismatch'
            assert len(result['diagnostic_projection']['source_diagnostics']) == 2
            assert len(result['diagnostic_projection']['original_public_source_diagnostics']) == 2
        assert result['current_source_check'] == 'not_established'
        mcp = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(
            tmp_path / 'public-conversion-workspace', project['id'], version))
        checked = mcp.call_tool('validate_project', arguments, 'public-conversion-standalone')
        assert not checked.is_error and checked.structured_content == public
        assert len(calls) == 2
        assert app.state.service.projects.artifact(project['id'], version, 'gxw').read_bytes() == raw


@pytest.mark.parametrize('case,complete', [('public-conversion-sfc', True), ('public-conversion-sfc-layout-failure', False)])
def test_original_public_conversion_layout_is_shared_by_web_and_mcp(tmp_path, native_replay, case, complete):
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    raw, witness, calls = native_replay(case)
    workspace = tmp_path / 'original-public-workspace'
    app = _app(workspace, tmp_path / 'original-public-state')
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={
            'name': 'Original public layout', 'plc_model': witness['cpu'], 'target_mode': 'ladder'}).json()
        imported = client.post('/api/sfc/proposals', headers=headers, json=dict(operation='import',
            project_id=project['id'], request_id='original-public-import', data_base64=base64.b64encode(raw).decode()))
        assert imported.status_code == 201, imported.text
        version = imported.json()['result']['version_id']
        response = client.post(f"/api/projects/{project['id']}/versions/{version}/native-validation/check", headers=headers,
            json={'request_id': 'original-public-check', 'program': 'Block.Program.pou'})
        assert response.status_code == 200, response.text
        public = response.json()['data']
        result = public['data']
        assert result['public_check'] == {'status': 'rejected', 'completed': True}
        assert result['public_diagnostic_conversion']['completed'] is complete
        assert result['diagnostic_projection']['status'] == ('completed' if complete else 'conversion_incomplete')
        assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
        mcp = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(workspace, project['id'], version))
        checked = mcp.call_tool('validate_project', {'native': True, 'program': 'Block.Program.pou'}, 'original-public-mcp')
        assert not checked.is_error and checked.structured_content == public and len(calls) == 2


def test_native_check_missing_snapshot_or_occupied_resource_never_starts(service, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from application.execution import DesktopResourceLock, ExecutionUnavailableError
    from agent_runtime.plc_tools import build_tool_context
    from agent_runtime.runtime import build_default_tool_runtime
    from agent_runtime.messages import ToolCall
    def forbidden(*args, **kwargs):
        pytest.fail('Native operation must not start without a snapshot and resource lock')
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, 'validate', forbidden)
    runtime = build_default_tool_runtime()
    result = runtime.invoke(ToolCall('missing', 'validate_project', {'native': True}),
                            build_tool_context({'id': 'empty', 'target_mode': 'fbd'}))
    assert result.is_error and '已保存' in result.data['error']['message']
    project, _, proposal = generate(service)
    vid = accept(service, proposal)
    def occupied(self):
        raise ExecutionUnavailableError('owned by another operation')
    monkeypatch.setattr(DesktopResourceLock, '__enter__', occupied)
    result = service.agent_call({'project_id': project['id'], 'version_id': vid, 'name': 'validate_project',
        'arguments': {'native': True}, 'call_id': 'occupied'})
    assert result['is_error'] and '本次未启动' in result['data']['error']['message']
@pytest.mark.parametrize("change,compile_status,check_status", [
    (None, "passed", "passed"), ("projection_failure", "passed", "unverified"),
    ("cpu", "unverified", "unverified"), ("source", "unverified", "unverified"),
    ("empty_task", "unverified", "unverified"), ("compile_rejected", "failed", "unverified"),
])
def test_layered_acceptance_requires_bound_current_complete_native_observations(change, compile_status, check_status):
    from application.engineering_acceptance import native_acceptance_results
    observed = {"cpu": "FX3U/FX3UC", "native_version": "1.635.0.1",
                "selection": {"status": "verified", "source_bytes_current": True, "configuration_bytes_current": True,
                              "selected_sources": ["1"], "empty_task": False},
                "compilation": {"completed": True, "acceptance": "accepted", "diagnostics_complete": True,
                                "source_context": {"status": "current"}, "diagnostic_conversion": {"status": "completed"}},
                "public_check": {"status": "passed", "completed": True}, "current_source_check": "established",
                "diagnostic_projection": {"status": "completed"}}
    if change == "projection_failure":
        observed["public_check"] = {"status": "failed", "completed": False}
        observed["diagnostic_projection"]["status"] = "failed"
    elif change == "cpu": observed["cpu"] = "Q03UDV"
    elif change == "source": observed["compilation"]["source_context"]["status"] = "stale"
    elif change == "empty_task": observed["selection"]["empty_task"] = True
    elif change == "compile_rejected":
        observed["compilation"]["acceptance"] = "rejected"
        observed["public_check"] = {"status": "not_requested", "completed": False}
        observed["current_source_check"] = "not_established"
    result = native_acceptance_results(observed, cpu="FX3U/FX3UC", software_version="1.635.0.1")
    assert result["current_source_compile"]["status"] == compile_status
    assert result["native_check_complete"]["status"] == check_status
    assert all(r["status"] == "unverified" for r in native_acceptance_results(observed, cpu="FX3U/FX3UC", software_version="1.636.0.1").values())


@pytest.mark.parametrize('case', [
    'sfc-passed', 'sfc-rejected', 'compile-rejected', 'diagnostic-recheck',
    'public-conversion', 'identity-conflict', 'nested-fbd-st', 'sfc-grid',
])
def test_native_http_contract_preserves_real_frozen_public_diagnostics(case):
    from application.projects import public
    from gxw.native_diagnostics import project_native_validation, project_native_sfc_graph_diagnostics
    from integrations.web.native_contracts import NativeCheck, NativeDiagnostic
    from tests.test_gxw_compiler import (
        authored_fbd_validation_case, compile_location_case, native_recheck_validation_case,
        original_sfc_public_validation_case, public_identity_recheck_witness, sfc_shared_check_witness,
    )

    if case.startswith('sfc-') and case != 'sfc-grid':
        witness = sfc_shared_check_witness(
            'current-grown-name-fixed' if case == 'sfc-passed' else 'action-out-x0-count-fixed',
            diagnostic=case == 'sfc-rejected',
        )
    elif case == 'sfc-grid':
        witness = retained_sfc_diagnostic_witness()
    else:
        loaded = {
            'compile-rejected': lambda: compile_location_case('q00j-production-tables'),
            'diagnostic-recheck': lambda: native_recheck_validation_case('FX3U-FX3UC'),
            'public-conversion': original_sfc_public_validation_case,
            'identity-conflict': public_identity_recheck_witness,
            'nested-fbd-st': lambda: authored_fbd_validation_case('application-motion', 'candidate'),
        }[case]()
        witness = dict(zip(('raw', 'observation', 'snapshots'), loaded[:3]))
    observation = witness['observation']
    value = project_native_validation(witness['raw'], observation, witness['snapshots'])
    if case == 'sfc-grid':
        value = project_native_sfc_graph_diagnostics(witness['raw'], value, witness['graph'])
    value = public({**value, 'mode': 'native', 'snapshot': {
        'project_id': 'contract-project', 'version_id': 'contract-version',
        'requested_program': 'Program.Program.pou', 'cpu': observation['cpu'],
    }})
    parsed = NativeCheck.model_validate(value)
    assert parsed.model_dump(mode='json', exclude_unset=True) == value
    for owner in (value['compilation'], value['diagnostic_projection'], value):
        for row in owner.get('source_diagnostics', []) + owner.get('raw_diagnostics', []):
            assert NativeDiagnostic.model_validate(row).model_dump(mode='json', exclude_unset=True) == row


@pytest.mark.parametrize('completed', [None, 'true', 1])
def test_native_http_contract_never_invents_completed_evidence(completed):
    from pydantic import ValidationError
    from integrations.web.native_contracts import NativeCheckState

    value = {'status': 'future-unverified'}
    if completed is not None:
        value['completed'] = completed
    with pytest.raises(ValidationError):
        NativeCheckState.model_validate(value)
    assert NativeCheckState.model_validate({**value, 'completed': False}).model_dump(exclude_unset=True) == {
        'status': 'future-unverified', 'completed': False,
    }


def test_native_compile_save_failure_keeps_bounded_forensics(tmp_path):
    import gxworks2.workspace_adapter as adapter

    (tmp_path/'input.gxw').write_bytes(b'project')
    observation = {'status': 'observed', 'message': '', 'exception_trace': -1, 'validation': {'events': [
        {'operation': 'Progress', 'percent': 100, 'count': 2},
        {'operation': 'CompilePublicReports', 'reports': [
            {'kind': 2, 'code': 0x500c5150, 'arguments': []},
            {'kind': 1, 'code': 25, 'arguments': [{'text': '1'}, {'text': '0'}]}]}]}}
    result = {'compilation': {'acceptance': 'rejected'}, 'public_check': {'status': 'not_requested'}}
    forensics = adapter._native_failure_forensics(tmp_path, observation, result)
    assert forensics['report_sequence'] == ['0x500c5150', '0x00000019']
    assert forensics['reports'][0] == {'kind': 2, 'code': '0x500c5150', 'arguments': []}
    assert forensics['reports'][1]['arguments'] == ['1', '0']
    assert forensics['exception_trace_records'] == -1
    assert forensics['internal_fault'] is True
    assert forensics['save_output_bytes'] is None
    assert [row['name'] for row in forensics['workspace_entries']] == ['input.gxw']
    assert forensics['disk_free_bytes'] > 0
    assert forensics['acceptance'] == 'rejected' and forensics['public_check'] == 'not_requested'


def test_native_failure_forensics_is_bounded_and_tolerates_missing_state(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter

    for index in range(60):
        (tmp_path/('file-%02d.bin' % index)).write_bytes(b'x')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'absent-local'))
    monkeypatch.setenv('TEMP', str(tmp_path/'absent-temporary'))
    forensics = adapter._native_failure_forensics(tmp_path, {}, {})
    assert len(forensics['workspace_entries']) == 40
    assert forensics['vendor_temporary_entries'] is None
    assert forensics['system_temporary_entries'] is None
    assert forensics['report_sequence'] == [] and forensics['reports'] == []
    assert forensics['internal_fault'] is False
    assert forensics['exception_trace_records'] is None and forensics['save_output_bytes'] is None


def test_native_compile_save_rejection_attaches_forensics_and_report_sequence(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    observation = {'protocol_version': 3, 'status': 'observed', 'message': '', 'exception_trace': 18,
                   'cpu': 'Q03UDV', 'validation': {'events': [
                       {'operation': 'CompilePublicReports', 'reports': [
                           {'kind': 2, 'code': 0x500c5150, 'arguments': []},
                           {'kind': 1, 'code': 25, 'arguments': [{'text': '1'}, {'text': '0'}]}]}]},
                   'calls': []}
    result = {'compilation': {'acceptance': 'rejected',
                              'diagnostics': [{'code': 0x500c5150}]},
              'public_check': {'status': 'not_requested'}, 'adapter': {}}

    def run(_executable, request, root, timeout, error_type):
        assert (root/'input.gxw').read_bytes() == before
        return 0, deepcopy(observation)

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: deepcopy(result))
    checker = adapter.NativeWorkspaceValidation(executable, tmp_path)
    with pytest.raises(adapter.WorkspaceSaveError) as raised:
        checker.validate(before, timeout=10, _save_output=True)
    message = str(raised.value)
    assert 'diagnostics=0x500c5150' in message and 'reports=0x500c5150,0x00000019' in message
    forensics = raised.value.observation['forensics']
    assert forensics['report_sequence'] == ['0x500c5150', '0x00000019']
    assert forensics['exception_trace_records'] == 18
    assert forensics['acceptance'] == 'rejected' and forensics['save_output_bytes'] is None
    assert 'input.gxw' in [row['name'] for row in forensics['workspace_entries']]


def test_native_failure_forensics_separates_internal_fault_from_source_diagnostics(tmp_path):
    import gxworks2.workspace_adapter as adapter

    def forensics_for(reports):
        observation = {'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': reports}]}}
        return adapter._native_failure_forensics(tmp_path, observation, {})

    assert forensics_for([{'kind': 2, 'code': 0x500c5150, 'arguments': []}])['internal_fault'] is True
    assert forensics_for([{'kind': 2, 'code': 0x500c8042, 'arguments': []},
                          {'kind': 2, 'code': 0x50010000, 'arguments': []}])['internal_fault'] is False
    assert forensics_for([{'kind': 1, 'code': 0x500c5150, 'arguments': []}])['internal_fault'] is False


def test_native_compile_save_repeats_once_when_only_the_vendor_literal_was_reported(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    roots = []

    def observation(status, reports):
        return {'protocol_version': 3, 'status': status, 'message': '', 'exception_trace': 18, 'cpu': 'Q03UDV',
                'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': reports}]}, 'calls': []}

    projections = [
        {'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                         'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
         'public_check': {'status': 'not_requested'}, 'adapter': {}},
        {'compilation': {'acceptance': 'accepted', 'diagnostics_complete': True, 'diagnostics': []},
         'public_check': {'status': 'not_requested_compile_rejected'}, 'adapter': {}}]

    def run(_executable, request, root, timeout, error_type):
        assert (root/'input.gxw').read_bytes() == before
        roots.append(root)
        if len(roots) == 1:
            return 0, observation('observed', [{'kind': 2, 'code': 0x500c5150, 'arguments': []}])
        (root/'candidate.gxw').write_bytes(b'candidate')
        return 0, observation('observed', [])

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation',
                        lambda raw, observed, snapshots: deepcopy(projections[min(len(roots) - 1, 1)]))
    product, result, observed, _snapshots = adapter.NativeWorkspaceValidation(executable, tmp_path).validate(
        before, timeout=10, _save_output=True)
    assert product == b'candidate' and result['compilation']['acceptance'] == 'accepted'
    assert result['adapter']['retry'] == {'reason': 'vendor_internal_fault', 'attempts': 2}
    assert len(roots) == 2 and roots[0] != roots[1]
    assert observed['retry']['reason'] == 'vendor_internal_fault' and observed['retry']['attempts'] == 2
    assert observed['retry']['first_reports'] == ['0x500c5150']


def test_native_compile_save_does_not_repeat_a_source_rejection(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                   'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                       {'kind': 2, 'code': 0x500c8042, 'arguments': []}]}]}, 'calls': []}

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: {
        'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                        'diagnostics': [{'kind': 2, 'code': 0x500c8042}]},
        'public_check': {'status': 'not_requested'}, 'adapter': {}})
    with pytest.raises(adapter.WorkspaceSaveError) as raised:
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10, _save_output=True)
    assert len(calls) == 1 and 'retry' not in raised.value.observation
    assert 'diagnostics=0x500c8042' in str(raised.value) and 'attempts=2' not in str(raised.value)


def test_native_compile_save_repeat_is_bounded_to_one_attempt(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                   'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                       {'kind': 2, 'code': 0x500c5150, 'arguments': []}]}]}, 'calls': []}

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: {
        'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                        'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
        'public_check': {'status': 'not_requested'}, 'adapter': {}})
    with pytest.raises(adapter.WorkspaceSaveError) as raised:
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10, _save_output=True)
    assert len(calls) == 2
    assert raised.value.observation['retry']['attempts'] == 2
    assert str(raised.value).count('attempts=2') == 1
    assert raised.value.retry == raised.value.observation['retry']
    assert raised.value.retry['first_reports'] == ['0x500c5150']
    assert raised.value.validation['compilation']['acceptance'] == 'rejected'


def test_native_validation_only_repeats_the_vendor_literal_in_the_compile_only_path(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []
    projections = [
        {'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                         'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
         'public_check': {'status': 'not_requested'}, 'adapter': {}},
        {'compilation': {'acceptance': 'accepted', 'diagnostics_complete': True, 'diagnostics': []},
         'public_check': {'status': 'not_requested'}, 'adapter': {}}]

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                   'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                       {'kind': 2, 'code': 0x500c5150, 'arguments': []}]}]}, 'calls': []}

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation',
                        lambda raw, observed, snapshots: deepcopy(projections[min(len(calls) - 1, 1)]))
    result = adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10)
    assert result['compilation']['acceptance'] == 'accepted'
    assert len(calls) == 2 and result['adapter']['retry']['reason'] == 'vendor_internal_fault'
    assert result['adapter']['retry'] == {'reason': 'vendor_internal_fault', 'attempts': 2}


def test_native_compile_only_path_returns_the_rejected_result_when_the_repeat_also_rejects(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                   'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                       {'kind': 2, 'code': 0x500c5150, 'arguments': []}]}]}, 'calls': []}

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: {
        'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                        'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
        'public_check': {'status': 'not_requested'}, 'adapter': {}})
    result = adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10)
    assert len(calls) == 2
    assert result['compilation']['acceptance'] == 'rejected'
    assert result['adapter']['retry']['attempts'] == 2


def test_native_repeat_keeps_the_first_failure_when_the_repeat_breaks(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        if len(calls) == 1:
            return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                       'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                           {'kind': 2, 'code': 0x500c5150, 'arguments': []}]}]}, 'calls': []}
        raise adapter.WorkspaceValidationError('原生命令行适配器在超时内未结束。', {})

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: {
        'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                        'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
        'public_check': {'status': 'not_requested'}, 'adapter': {}})
    with pytest.raises(adapter.WorkspaceValidationError) as raised:
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10, _save_output=True)
    assert len(calls) == 2
    error = raised.value
    assert str(error).endswith('; attempts=2')
    assert error.retry['reason'] == 'vendor_internal_fault' and error.retry['attempts'] == 2
    assert error.retry['first_reports'] == ['0x500c5150']
    assert 'diagnostics=0x500c5150' in error.retry['first_message']
    assert error.observation['retry'] == error.retry


def test_native_compile_only_repeat_also_keeps_the_first_failure(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness

    before = authored_fb_witness()[0]
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls, projections = [], [
        {'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                         'diagnostics': [{'kind': 2, 'code': 0x500c5150}]},
         'public_check': {'status': 'not_requested'}, 'adapter': {}}]

    def run(_executable, request, root, timeout, error_type):
        calls.append(root)
        if len(calls) == 1:
            return 0, {'protocol_version': 3, 'status': 'observed', 'message': '', 'cpu': 'Q03UDV',
                       'validation': {'events': [{'operation': 'CompilePublicReports', 'reports': [
                           {'kind': 2, 'code': 0x500c5150, 'arguments': []}]}]}, 'calls': []}
        raise adapter.WorkspaceValidationError('原生工作区没有返回检查过程记录。')

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    monkeypatch.setattr(adapter, 'project_native_validation', lambda raw, observed, snapshots: deepcopy(projections[0]))
    with pytest.raises(adapter.WorkspaceValidationError) as raised:
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(before, timeout=10)
    assert len(calls) == 2 and str(raised.value).endswith('; attempts=2')
    assert raised.value.retry['first_reports'] == ['0x500c5150']
    assert raised.value.observation is None and raised.value.retry['attempts'] == 2


def test_native_internal_fault_predicate_keeps_other_rejections_out_of_the_repeat_path():
    import gxworks2.workspace_adapter as adapter

    def narrow(result):
        return adapter._vendor_internal_fault_only(result)

    assert narrow({'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                                  'diagnostics': [{'kind': 2, 'code': 0x500c5150}]}}) is True
    assert narrow({'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True,
                                  'diagnostics': [{'kind': 2, 'code': 0x500c5150},
                                                  {'kind': 2, 'code': 0x500c8042}]}}) is False
    assert narrow({'compilation': {'acceptance': 'rejected', 'diagnostics_complete': False,
                                  'diagnostics': [{'kind': 2, 'code': 0x500c5150}]}}) is False
    assert narrow({'compilation': {'acceptance': 'rejected', 'diagnostics_complete': True, 'diagnostics': []}}) is False
    assert narrow({'compilation': {'acceptance': 'accepted', 'diagnostics_complete': True,
                                  'diagnostics': [{'kind': 2, 'code': 0x500c5150}]}}) is False
    assert narrow(None) is False


def test_native_exception_trace_is_opt_in_and_ships_with_the_adapter():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root/'native_adapters/NativeExceptionTrace.cs').read_text(encoding='utf-8')
    assert 'GXW_NATIVE_EXCEPTION_TRACE' in source
    assert 'if (String.IsNullOrEmpty(path)) return;' in source
    assert 'AddVectoredExceptionHandler' in source
    build = (root/'tools/build_workspace_adapter.ps1').read_text(encoding='utf-8')
    assert 'NativeExceptionTrace.cs' in build
    published = json.loads((root/'publication/public-files.json').read_text(encoding='utf-8'))['files']
    assert 'native_adapters/NativeExceptionTrace.cs' in published

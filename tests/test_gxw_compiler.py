"""Stored compiler artifacts checked against archived native observations."""

from tools.offline_inputs import private_path
import base64
from functools import lru_cache
import importlib
import json
from pathlib import Path
import struct
import zipfile

import pytest
from hypothesis import example, given, settings, strategies as st
from tests.fixture_cache import ARCHIVE_CACHE
from tests.native_fixtures import load_frozen_native_json
from gxworks2.workspace_legacy_observations import decode_frozen_workspace_evidence


@pytest.mark.parametrize('events,completed,exported', [
    ([], False, False),
    ([{'operation': 'NativeExport'}], False, True),
    ([{'operation': 'NativeExport', 'hresult': -1, 'code': 7}], False, True),
    ([{'operation': 'ExportOneFileProject', 'hresult': 0, 'code': 0}], False, True),
    ([{'operation': 'ExportOneFileProject', 'hresult': -1, 'code': 0}], False, False),
    ([{'operation': 'ExportOneFileProject', 'hresult': 0, 'code': 7}], False, False),
    ([{'operation': 'ExportOneFileProject', 'hresult': 1, 'code': 0}], False, False),
    ([{'operation': 'ExportOneFileProject'}], False, False),
    ([{'operation': 'ExportOneFileProject', 'hresult': 0}], False, False),
    ([{'operation': 'ExportOneFileProject', 'code': 0}], False, False),
    ([{'operation': 'SaveProject', 'hresult': 0, 'code': 0}], True, False),
    ([{'operation': 'Workspace.UpdatePCodeBeforeProgramCheck', 'hresult': 0, 'code': 0}], False, False),
    ([{'operation': 'SaveProject', 'hresult': -1, 'code': 0}, {'operation': 'NativeExport'}], False, True),
    ([{'operation': 'ExportOneFileProject', 'hresult': -1, 'code': 9},
      {'operation': 'Workspace.UpdatePCodeBeforeProgramCheck', 'hresult': 0, 'code': 0},
      {'operation': 'SaveProject', 'hresult': 0, 'code': 0},
      {'operation': 'ExportOneFileProject', 'hresult': 0, 'code': 0}], True, True),
])
def test_native_export_evidence_is_independent_of_save_and_publication(events, completed, exported):
    from gxw.native_diagnostics import analyze_native_check
    assert analyze_native_check(events, [], {})['save'] == {'completed': completed, 'exported': exported}


@settings(max_examples=100, deadline=None, derandomize=True)
@example([], [], 'before-build', 0)
@given(st.lists(st.fixed_dictionaries({
    'operation': st.sampled_from(['NativeBodyRead', 'NativeDeclarationRead', 'Unrelated', None]),
    'stage': st.sampled_from(['before-build', 'after-build', 'other', None]),
    'library': st.sampled_from(['', 'alpha', 'beta']),
    'name': st.sampled_from(['same', 'other']),
}), max_size=80), st.lists(st.sampled_from(['NativeBodyRead', 'NativeDeclarationRead', 'Missing']), max_size=5),
       st.sampled_from(['before-build', 'after-build', 'Missing']), st.integers(min_value=-1, max_value=100))
def test_native_event_index_preserves_order_positions_duplicates_and_source_names(rows, operations, stage, stop):
    from copy import deepcopy
    from gxw.diagnostic_events import EventIndex
    original = deepcopy(rows)
    events = EventIndex.of(rows)
    assert EventIndex.of(events) is events
    expected = [(i, row) for i, row in enumerate(rows) if row['operation'] in operations]
    assert list(events.numbered(*operations)) == expected
    assert list(events.rows(*operations)) == [row for _, row in expected]
    assert list(events.numbered(*operations, stage=stage)) == [item for item in expected if item[1]['stage'] == stage]
    assert list(events.at_stage(stage)) == [row for row in rows if row['stage'] == stage]
    assert list(events.before('NativeBodyRead', stop)) == [row for i, row in enumerate(rows)
        if i < stop and row['operation'] == 'NativeBodyRead']
    groups = events.grouped(*operations, key=lambda row: (row['library'], row['name']))
    for key in {(row['library'], row['name']) for row in rows}:
        assert groups.get(key, []) == [item for item in expected if (item[1]['library'], item[1]['name']) == key]
    assert all(row is rows[i] for items in groups.values() for i, row in items)
    assert rows == original


class _CountedNativeEvent(dict):
    def __init__(self, values, visits):
        super().__init__(values)
        self.visits = visits

    def get(self, key, default=None):
        self.visits[0] += 1
        return super().get(key, default)


@pytest.mark.parametrize('source_count,noise_count', [(10, 1000), (100, 1000), (100, 10000)])
def test_native_source_binding_event_visits_do_not_multiply_by_source_count(source_count, noise_count):
    from gxw.diagnostic_events import EventIndex
    from gxw.native_diagnostics import (bind_native_compile_declarations, bind_native_compile_declaration_owners,
        bind_native_source_chain)
    visits, noise_visits = [0], [0]
    rows = [_CountedNativeEvent({'operation': 'Unrelated'}, noise_visits) for _ in range(noise_count)]
    sources = [{'name': f'P{i}', 'library': '', 'role': 'global', 'declarations': None,
                'logical_name': f'P{i}.dh', 'declaration_name': f'P{i}.dh'} for i in range(source_count)]
    for source in sources:
        for stage in ('before-build', 'after-build'):
            rows.extend(_CountedNativeEvent(row, visits) for row in (
                {'operation': 'NativeDeclarationRead', 'pou': source['name'], 'stage': stage},
                {'operation': 'NativeDeclarationOwnerRead', 'role': 'global', 'library': '', 'stage': stage,
                 'owner': {'read_name': {'name': source['name']}}},
                {'operation': 'NativeBodySelection', 'name': source['name'], 'stage': stage},
            ))
    events = EventIndex.of(rows)
    visits[0] = noise_visits[0] = 0
    declarations = bind_native_compile_declarations(sources, events=events, source_context={})
    owners = bind_native_compile_declaration_owners(sources, project_id=None, events=events, source_context={})
    chain = bind_native_source_chain(dict.fromkeys(source['name'] for source in sources),
        [source['name'] for source in sources], project_id=None, native_version='1.635.0.1',
        selections=events, reads=events, snapshots={})
    assert len(declarations) == len(owners) == source_count
    assert {row['status'] for row in declarations.values()} == {row['status'] for row in owners.values()} == {'unresolved'}
    assert chain['status'] == 'unresolved' and len(chain['sources']) == source_count
    assert noise_visits[0] == 0
    assert visits[0] < 80 * source_count


@pytest.mark.parametrize('poll_count,noise_count', [(10, 1000), (100, 1000), (100, 10000)])
def test_native_reference_event_visits_do_not_multiply_by_poll_count(poll_count, noise_count):
    from gxw.diagnostic_events import EventIndex
    from gxw.native_diagnostics import bind_native_source_references
    visits, noise_visits = [0], [0]
    rows = [_CountedNativeEvent({'operation': 'Unrelated'}, noise_visits) for _ in range(noise_count)]
    rows += [{'operation': 'OwnedBackendModule', 'name': name, 'version': '1.635.0.1'}
             for name in ('DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll')]
    rows += [{'operation': 'Progress', 'percent': 100},
             {'operation': 'Compiler.CreateProgramAnalysis3', 'hresult': 0, 'code': 0}]
    for poll in range(1, poll_count + 1):
        rows += [_CountedNativeEvent({'operation': 'NativeReferenceProgress', 'poll': poll,
                  'percent': 100 if poll == poll_count else 20, 'hresult': 0, 'code': 0, 'count': 0}, visits),
                 _CountedNativeEvent({'operation': 'NativeReferenceRawReports', 'poll': poll,
                  'percent': 100 if poll == poll_count else 20, 'reports': []}, visits)]
    rows += [{'operation': 'Compiler.GetProgramAnalysis3', 'hresult': 0, 'code': 0, 'count': 0},
             {'operation': 'NativeSourceReferences', 'hresult': 0, 'code': 0, 'query': 'all-references',
              'declared': 0, 'plural': 0, 'symbol': '', 'scope': '', 'rows': [], 'count': 0,
              'evidence': {'schema_version': 1, 'origin': 'source_references'}},
             {'operation': 'Compiler.ProgramCheck'}]
    events = EventIndex.of(rows)
    visits[0] = noise_visits[0] = 0
    result = bind_native_source_references(events, compilation={'acceptance': 'accepted'})
    assert result['status'] == 'current' and result['count'] == 0
    assert noise_visits[0] == 0
    assert visits[0] < 35 * poll_count


@pytest.mark.parametrize('query_count,noise_count', [(10, 1000), (64, 1000), (64, 10000)])
def test_native_inspection_event_visits_do_not_multiply_by_query_or_resource_count(query_count, noise_count):
    from gxw.diagnostic_events import EventIndex
    from gxw.native_diagnostics import project_native_inspection
    visits, noise_visits = [0], [0]
    rows = [_CountedNativeEvent({'operation': 'Unrelated'}, noise_visits) for _ in range(noise_count)]
    snapshots = {'one.bin': b'X'}
    channels = [{'channel': channel, 'file': 'one.bin', 'bytes': 1} for channel in range(3)]
    for i in range(query_count):
        rows += [_CountedNativeEvent(row, visits) for row in (
            {'operation': 'NativeRequestedCodeLocation', 'source_location': {
                'resource': f'R{i}', 'code_step': i, 'hresult': 0, 'code': 0}},
            {'operation': 'SavedResourceCode', 'resource': f'R{i}', 'channels': channels},
            {'operation': 'Resource', 'name': f'R{i}', 'channels': channels},
        )]
    events = EventIndex.of(rows)
    visits[0] = noise_visits[0] = 0
    result = project_native_inspection(b'', {'validation': {'events': events}}, snapshots, {}, {
        'assignments': [], 'locations': [{'resource': f'R{i}', 'step': i} for i in range(query_count)], 'saved_code': True})
    assert len(result['locations']) == len(result['saved_code']['resources']) == query_count
    assert all(row['native_result'] == {'hresult': 0, 'code': 0} for row in result['locations'])
    assert result['saved_code']['status'] == 'unresolved'
    assert noise_visits[0] == 0
    assert visits[0] < 50 * query_count


@pytest.mark.parametrize('kind', ['compile', '65-source', 'recheck', 'sfc-public'])
def test_native_validation_noise_keeps_frozen_results_inputs_and_bounded_event_visits(kind):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    if kind == 'compile':
        raw, observation, snapshots = compile_location_case()
    elif kind == '65-source':
        raw, observation, snapshots, _ = native_source_capacity_case()
    elif kind == 'recheck':
        raw, observation, snapshots, _ = native_recheck_validation_case('Q04UDV')
    else:
        raw, observation, snapshots = original_sfc_public_validation_case()
    original = deepcopy(observation)
    expected = deepcopy(project_native_validation(raw, observation, snapshots))
    assert observation == original
    visits = [0]
    observation['validation']['events'].extend(_CountedNativeEvent(
        {'operation': 'Unrelated', 'check_pass': 'common'}, visits) for _ in range(1000))
    assert project_native_validation(raw, observation, snapshots) == expected
    assert observation['validation']['events'][:-1000] == original['validation']['events']
    assert visits[0] <= 8 * 1000


def test_native_conversion_event_groups_keep_frozen_reports_and_ignore_unrelated_events():
    from gxw.diagnostic_events import EventIndex
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = public_conversion_witness()
    visits = [0]
    rows = witness['observation']['validation']['events'] + [
        _CountedNativeEvent({'operation': 'Unrelated', 'check_pass': 'raw-recheck'}, visits) for _ in range(1000)]
    witness['observation']['validation']['events'] = EventIndex.of(rows)
    visits[0] = 0
    assert _project_public_diagnostic_conversion(witness['result'], witness['observation']) == witness['expected']
    assert visits[0] == 0


def test_native_declaration_groups_ignore_malformed_foreign_source_keys():
    from gxw.native_diagnostics import bind_native_compile_declarations, bind_native_compile_declaration_owners
    sources = [{'name': 'P', 'library': '', 'role': 'global', 'declarations': None,
                'logical_name': 'P.dh', 'declaration_name': 'P.dh'}]
    events = [
        {'operation': 'NativeDeclarationRead', 'library': [], 'pou': 'P', 'stage': 'before-build'},
        {'operation': 'NativeDeclarationOwnerRead', 'role': 'global', 'library': 'foreign', 'owner': None},
    ]
    declarations = bind_native_compile_declarations(sources, events=events, source_context={})
    owners = bind_native_compile_declaration_owners(sources, project_id=None, events=events, source_context={})
    assert declarations['', 'P']['status'] == owners['global', '', 'P']['status'] == 'not_observed'


def test_frozen_fxu_diagnostics_bind_current_sources_without_rebuilding_archived_editor_drafts():
    from research.freeze_gxw_fbd_menu_completion import UI_ARCHIVE
    from gxw.native_diagnostics import project_native_validation
    prefix = 'application-FXU/'
    with zipfile.ZipFile(UI_ARCHIVE) as archive:
        raw = archive.read(prefix + 'candidate.gxw')
        observation = load_frozen_native_json(archive.read(prefix + 'check-fx-grammar-observation.json'))
        manifest = load_frozen_native_json(archive.read(prefix + 'case.json'))
        snapshots = {name: archive.read(prefix + 'check-fx-grammar/' + name) for name in manifest['snapshots']}
        original = load_frozen_native_json(archive.read(prefix + 'fx-grammar-validation.json'))
    result = project_native_validation(raw, observation, snapshots)
    assert result['cpu'] == 'FXU/FX2C'
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['current_raw_source_check'] == 'established'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert [(row['original'], row['report_origin']) for row in diagnostics] == [
        (row['original'], row['report_origin']) for row in original['diagnostic_projection']['source_diagnostics']]
    assert [(row['graph_projection']['instance'], row['original']['step'], row['graph_projection']['caller']['network'])
            for row in diagnostics] == [('POU_01.MODEL_STAGE_BETA', 39, 3), ('POU_01.APP_SOURCE_ALPHA', 43, 1)]


@pytest.fixture(scope='module')
def workspace_memory_harness(tmp_path_factory):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows BSTR allocator and native .NET adapter contract')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    assert compiler.is_file()
    root = tmp_path_factory.mktemp('workspace-memory')
    harness = root / 'MemoryHarness.cs'
    harness.write_text(r'''
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace PlcAi.NativeAdapter {
    internal sealed partial class WorkspaceSourceSave {
        internal static void Regression(string mode) {
            if (mode == "evidence_protocol") {
                var evidence = new List<object>();
                foreach (EvidenceOrigin origin in Enum.GetValues(typeof(EvidenceOrigin)))
                    evidence.Add(WorkspaceEvidence.Describe(origin));
                Console.WriteLine(new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(
                    new { protocol_version = WorkspaceBusinessProtocol.Version, evidence = evidence }));
                try { WorkspaceEvidence.Describe((EvidenceOrigin)int.MaxValue); }
                catch (ArgumentOutOfRangeException) { return; }
                throw new Exception("unknown_evidence_origin_accepted");
            }
            if (mode == "poll_count" || mode == "poll_time") {
                int count = 0;
                try {
                    foreach (int poll in ValidationPolls(mode == "poll_time" ? 0 : 1000, 3, 0)) {
                        count++;
                    }
                    throw new Exception("polling_returned_without_completion");
                } catch (TimeoutException) {
                    if (count != (mode == "poll_time" ? 0 : 3)) throw;
                }
                return;
            }
            var adapter = new WorkspaceSourceSave();
            if (mode == "public_projection_failure") {
                string operation = "Compiler.OriginalPublicCheckProgress";
                int hr = unchecked((int)0x80004005), code = 0x2d010025;
                foreach (string change in new [] {"none", "operation", "hresult", "code"}) {
                    try {
                        adapter.Check(change == "operation" ? "Compiler.BuildProgress" : operation,
                            change == "hresult" ? unchecked((int)0x8007000e) : hr,
                            change == "code" ? code + 1 : code);
                        throw new Exception("native_failure_accepted");
                    } catch (NativeCallException error) {
                        if (IsPublicCheckProjectionFailure(error) != (change == "none"))
                            throw new Exception("wrong_public_projection_failure_policy");
                        if (!error.Message.Contains("HRESULT=0x") || !error.Message.Contains("code=0x"))
                            throw new Exception("native_error_details_lost");
                    }
                }
                if (IsPublicCheckProjectionFailure(new InvalidOperationException(operation + " failed")))
                    throw new Exception("untyped_failure_recovered");
                return;
            }
            if (mode == "events" || mode == "reports" || mode == "report_bytes" || mode == "event_bytes") {
                int completed = 0;
                try {
                    for (int i = 0; i <= MaxValidationEvents; i++) {
                        var row = new Dictionary<string, object> { {"operation", "stuck"}, {"poll", i} };
                        if (mode.EndsWith("bytes")) row["text"] = new String('x', 1024 * 1024);
                        if (mode.StartsWith("report")) adapter.RetainReport(row); else adapter.Event(row);
                        completed++;
                    }
                    throw new Exception("unbounded_validation_records");
                } catch (ValidationLimitException) {
                    int expected = mode == "events" ? MaxValidationEvents : mode == "reports" ? MaxValidationReports : 7;
                    if (completed != expected) throw new Exception("incorrect_validation_budget " + completed);
                }
                return;
            }
            int calls = 0, locationCalls = 0;
            AssignmentReadCall read = delegate(IntPtr self, int keysCount, IntPtr keys, out int count, out IntPtr records, out int code) {
                if (ReadBStr(Marshal.ReadIntPtr(keys, 1)) != "Value") throw new Exception("name_not_alive");
                if (keysCount == 2 && ReadBStr(Marshal.ReadIntPtr(keys, 6)) != "Main") throw new Exception("source_not_alive");
                count = 1; records = Marshal.AllocCoTaskMem(20); code = 0; calls++;
                for (int i = 0; i < 4; i++) Marshal.WriteIntPtr(records, i * 4, Marshal.StringToBSTR("returned"));
                Marshal.WriteInt32(records, 16, 1);
                return mode == "assignment_error" ? unchecked((int)0x80004005) : 0;
            };
            LocationCall locate = delegate(IntPtr self, NativeCodeRange range, IntPtr location, out int code) {
                if (ReadBStr(range.Resource) != "MAIN") throw new Exception("resource_not_alive");
                code = 1; locationCalls++;
                return mode == "assignment_error" ? unchecked((int)0x80004005) : 0;
            };
            IntPtr table = Marshal.AllocCoTaskMem(128), compiler = Marshal.AllocCoTaskMem(4);
            try {
                Marshal.WriteIntPtr(table, 56, Marshal.GetFunctionPointerForDelegate(read));
                Marshal.WriteIntPtr(table, 100, Marshal.GetFunctionPointerForDelegate(locate));
                Marshal.WriteIntPtr(compiler, table);
                var generated = new Dictionary<string, byte[][]> { {"MAIN", new byte[3][]} };
                for (int i = 0; i < 128; i++) {
                    adapter.ReadSelectedAssignments(compiler, new object[] { new Dictionary<string, object> {
                        {"name", "Value"}, {"source", "Main"}, {"scope", i % 2 == 0 ? "local" : "global"} } });
                    adapter.ReadRequestedLocations(compiler, new ObjectId(), new object[] {
                        new Dictionary<string, object> { {"resource", "MAIN"}, {"step", i} } }, generated);
                    adapter.NativeLocation(compiler, new ObjectId(), new Dictionary<string, object> {
                        {"step", i}, {"poll", 1}, {"report_index", i}, {"kind", 2}, {"code", 1},
                        {"name", new Dictionary<string, object> { {"text", "MAIN"} }} });
                    // A released query must not retain a second owner that
                    // will free the same address during adapter shutdown.
                    if (adapter.strings.Count != 0) throw new Exception("freed_query_still_owned_by_adapter");
                }
                adapter.BStr("retained until shutdown");
                adapter.Dispose(); adapter.Dispose();
                if (calls != 128 || locationCalls != 256 || adapter.strings.Count != 0) throw new Exception("invalid_allocator_lifetime");
                GC.KeepAlive(read);
                GC.KeepAlive(locate);
            } finally { Marshal.FreeCoTaskMem(compiler); Marshal.FreeCoTaskMem(table); }
        }
    }
    static class MemoryHarness {
        static int Main(string[] args) {
            try { WorkspaceSourceSave.Regression(args[0]); Console.WriteLine("passed"); return 0; }
            catch (Exception error) { Console.WriteLine(error); return 1; }
        }
    }
}
''', encoding='utf-8')
    executable = root / 'workspace-memory.exe'
    source = Path(__file__).resolve().parents[1] / 'native_adapters'
    names = ['WorkspaceSourceSave.cs', 'WorkspaceValidation.cs', 'WorkspaceTasks.cs',
             'WorkspaceSfcGraph.cs', 'NativeRequest.cs', 'WorkspaceBusinessProtocol.cs']
    result = subprocess.run([str(compiler), '/nologo', '/platform:x86', '/target:exe',
        '/main:PlcAi.NativeAdapter.MemoryHarness', '/reference:System.Web.Extensions.dll',
        '/reference:System.Windows.Forms.dll', '/out:' + str(executable), str(harness),
        *[str(source / name) for name in names]], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


@pytest.mark.parametrize('mode', ['assignments', 'assignment_error', 'poll_count', 'poll_time', 'public_projection_failure',
                                   'events', 'reports', 'report_bytes', 'event_bytes'])
def test_native_workspace_allocator_and_validation_bounds(workspace_memory_harness, mode):
    import subprocess
    result = subprocess.run([str(workspace_memory_harness), mode], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip() == 'passed', result.stdout + result.stderr


def test_compiled_workspace_serializes_the_exact_python_evidence_protocol(workspace_memory_harness):
    import subprocess
    from gxworks2.workspace_observations import EvidenceOrigin, evidence_origin_bound
    from gxworks2.workspace_protocol import WORKSPACE_PROTOCOL
    result = subprocess.run([str(workspace_memory_harness), 'evidence_protocol'],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == 'passed'
    observed = json.loads(lines[0])
    assert observed['protocol_version'] == WORKSPACE_PROTOCOL == 3
    assert len(observed['evidence']) == len(EvidenceOrigin) == 7
    assert {row['origin'] for row in observed['evidence']} == {origin.value for origin in EvidenceOrigin}
    for row in observed['evidence']:
        assert evidence_origin_bound({'evidence': row}, EvidenceOrigin(row['origin']))


@pytest.mark.parametrize('malformed', [None, {}, {'schema_version': True, 'origin': 'source_body'},
    {'schema_version': 1.0, 'origin': 'source_body'}, {'schema_version': 2, 'origin': 'source_body'},
    {'schema_version': 1, 'origin': 'unknown'}, {'schema_version': 1, 'origin': True},
    {'schema_version': 1, 'origin': 'source_body', 'description': 'trusted'}])
def test_evidence_origin_requires_exact_version_type_and_known_shape(malformed):
    from gxworks2.workspace_observations import EvidenceOrigin, evidence_origin_bound
    assert not any(evidence_origin_bound({'evidence': malformed}, origin) for origin in EvidenceOrigin)


def _evidence_binding_case(origin):
    """Use existing independent buffers and context for each protocol consumer."""
    from gxw.native_diagnostics import (bind_native_code_lexical_read, bind_native_source_references,
                                       project_native_validation)
    if origin == 'code_lexical_read':
        events, generated, snapshots = native_lexical_observation()
        return events, lambda rows: bind_native_code_lexical_read(rows, generated, snapshots,
            cpu='FX3U/FX3UC', codepage=936)[0]['status'], 'current'
    if origin == 'source_references':
        _, observation, _ = native_reference_observation()
        events = observation['validation']['events']
        return events, lambda rows: bind_native_source_references(rows,
            compilation={'acceptance': 'accepted'})['status'], 'current'
    if origin == 'sfc_length_prefix':
        witness = sfc_shared_check_witness()
        raw, observation, snapshots = witness['raw'], witness['observation'], witness['snapshots']
    else:
        raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    field = 'current_source_check' if origin == 'sfc_length_prefix' else 'current_raw_source_check'
    return events, lambda rows: project_native_validation(raw,
        {**observation, 'validation': {**observation['validation'], 'events': rows}},
        snapshots)[field], 'established'


def _evidence_documents(value):
    if isinstance(value, dict):
        if 'evidence' in value:
            yield value
        for child in value.values():
            yield from _evidence_documents(child)
    elif isinstance(value, list):
        for child in value:
            yield from _evidence_documents(child)


@pytest.mark.parametrize('origin', ['code_lexical_read', 'owned_check_input', 'source_body',
    'source_references', 'task_selection', 'sfc_length_prefix', 'structured_check_envelope'])
@pytest.mark.parametrize('damage', ['missing', 'unknown', 'wrong_origin', 'schema', 'schema_boolean'])
def test_every_evidence_consumer_rejects_unbound_origin_without_legacy_fallback(origin, damage):
    from copy import deepcopy
    events, bind, expected = _evidence_binding_case(origin)
    assert bind(events) == expected
    changed = deepcopy(events)
    rows = [row for row in _evidence_documents(changed) if row['evidence']['origin'] == origin]
    assert rows, origin
    for row in rows:
        if damage == 'missing':
            del row['evidence']
        elif damage in ('unknown', 'wrong_origin'):
            row['evidence']['origin'] = 'unrecognized' if damage == 'unknown' else (
                'source_body' if origin != 'source_body' else 'owned_check_input')
        else:
            row['evidence']['schema_version'] = True if damage == 'schema_boolean' else 2
    assert bind(changed) != expected


@settings(max_examples=40, deadline=None, derandomize=True)
@given(st.sampled_from(['code_lexical_read', 'owned_check_input', 'source_body',
    'source_references', 'task_selection', 'sfc_length_prefix', 'structured_check_envelope']),
    st.one_of(st.none(), st.text(max_size=300)))
def test_arbitrary_optional_descriptions_cannot_change_evidence_binding(origin, description):
    from copy import deepcopy
    events, bind, expected = _evidence_binding_case(origin)
    changed = deepcopy(events)
    for row in _evidence_documents(changed):
        if description is None:
            row.pop('provenance', None)
        else:
            row['provenance'] = description
    assert bind(events) == bind(changed) == expected


def test_frozen_decoder_is_explicit_nonmutating_and_preserves_existing_invalid_evidence():
    from copy import deepcopy
    origin = {'operation': 'NativeBodyRead', 'provenance':
        'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'}
    legacy = {'protocol_version': 2, 'validation': {'events': [origin]}}
    before = deepcopy(legacy)
    current = decode_frozen_workspace_evidence(legacy)
    assert legacy == before and current['protocol_version'] == 1
    assert current['validation']['events'][0]['evidence'] == {'schema_version': 1, 'origin': 'source_body'}
    assert decode_frozen_workspace_evidence(current) == current
    invalid = {**origin, 'evidence': {'schema_version': 1, 'origin': 'foreign'}}
    assert decode_frozen_workspace_evidence(invalid) == invalid
    assert decode_frozen_workspace_evidence({**origin, 'operation': 'Unknown'}) == {**origin, 'operation': 'Unknown'}
    saved = {'protocol_version': 2, 'validation': None, 'creation': None, 'calls': [],
        'cpu': 'FX3G', 'native_version': '1.635.0.1', 'compile': 'not_requested', 'check': 'not_requested'}
    assert decode_frozen_workspace_evidence(saved)['protocol_version'] == 1
    unrelated = {'protocol_version': 2, 'operation': 'other_protocol'}
    assert decode_frozen_workspace_evidence(unrelated) == unrelated

from gxw.compiler_debug import compiler_st_source_points, parse_compiler_debug
from gxw.compiler_storage import parse_compiler_index, parse_compiler_link_map
from gxw.compiler_tables import parse_compiler_tables
from gxw.container import CompoundFile
from gxw.models import GXWFormatError
from gxw.token_pou import parse_token_fragment, parse_token_region


@pytest.mark.parametrize('language,raw,expected', [
    (language, raw, canonical)
    for language, values in [('zh-CN', ['Error', 'Warning', 'CheckWarning', 'Information']),
                             ('en', ['ERROR', 'WARNING', 'CheckWarning', 'Information']),
                             ('ja', ['Error', 'Warning', 'CheckWarning', 'Information'])]
    for raw, canonical in zip(values, ['Error', 'Warning', 'CheckWarning', 'Information'])
])
def test_native_diagnostic_severity_uses_exact_language_resources(language, raw, expected):
    from gxw.native_locale import native_diagnostic_severity
    assert native_diagnostic_severity(raw, language=language) == expected
    for damaged in ('Unknown', raw+' ', raw.lower()):
        with pytest.raises(GXWFormatError, match='diagnostic severity'):
            native_diagnostic_severity(damaged, language=language)
    if raw in ('ERROR', 'WARNING'):
        with pytest.raises(GXWFormatError, match='diagnostic severity'):
            native_diagnostic_severity(raw.title(), language=language)


@pytest.mark.parametrize('language,summary', [
    ('zh-CN', '2件：软元件/标签"MAIN/Timer.Q"的交叉参照信息'),
    ('en', "2: cross reference information of device/label 'MAIN/Timer.Q'"),
    ('ja', '2件：デバイス/ラベル"MAIN/Timer.Q"のクロスリファレンス情報'),
])
def test_native_cross_reference_count_uses_exact_language_and_qualified_search(language, summary):
    from gxw.native_locale import cross_reference_summary_count
    assert cross_reference_summary_count(summary, 'MAIN/Timer.Q', language=language) == 2
    assert cross_reference_summary_count(summary, 'Other/Timer.Q', language=language) is None
    assert cross_reference_summary_count(summary + ' extra', 'MAIN/Timer.Q', language=language) is None
    assert cross_reference_summary_count(summary, 'Timer.Q', language=language) is None
    for other in {'zh-CN', 'en', 'ja'} - {language}:
        assert cross_reference_summary_count(summary, 'MAIN/Timer.Q', language=other) is None


@pytest.mark.parametrize('language,summary', [
    ('zh-CN', '1件：软元件/标签"Result"的交叉参照信息'),
    ('en', "1: cross reference information of device/label 'Result'"),
    ('ja', '1件：デバイス/ラベル"Result"のクロスリファレンス情報'),
])
@pytest.mark.parametrize('damage', [None, 'count', 'owner', 'query', 'extra_text'])
def test_current_assignment_localized_contract_preserves_count_query_and_source_binding(language, summary, damage):
    """Synthetic localized contract, not native language acceptance evidence."""
    from gxw.live_assignments import verify_live_assignments
    identity = dict(process_id=1, window_handle=2, process_started='3', executable='C:/GX/GD2.exe',
        software_version='1.635.0.1', project_path='C:/Owned/Project.gxw', cpu_menu='Q03UDV',
        native_ui_language=language, project_codepage=936)
    query = dict(scope='global', source='Globals.gh', name='Result')
    assignment = dict(name='Result', source='Globals.gh', scope='global', address='D6656',
        compiled_owner='@GLOBALS', component_path=[], allowed_locations=[dict(resource='MAIN', pou='Program')])
    prepared = dict(kind='current_compile_allocations', revision=4, compile_run_id='current-build',
        identity=identity, queries=[dict(query=query, status='prepared', search_text='Result',
            scope_resolution='unambiguous_source_declaration', assignments=[assignment])])
    record = dict(query=query, search_text='Result', summary=summary,
        visible_completion=dict(completed=True, page='Native cross reference'), rows=[dict(
            label='Result', address='D6656', program='MAIN', pou='Program', location='ST line 1', read_write='R')])
    if damage == 'count': record['summary'] = summary.replace('1', '2', 1)
    elif damage == 'owner': record['rows'][0]['pou'] = 'OtherProgram'
    elif damage == 'query': record['search_text'] = 'OtherProgram/Result'
    elif damage == 'extra_text': record['summary'] += ' additional result'
    observed = dict(revision=4, compile_run_id='current-build', identity=identity, cross_references=[record])
    result = verify_live_assignments(prepared, observed)
    assert result['status'] == ('current' if damage is None else 'unavailable')
    assert len(result['rows']) == (1 if damage is None else 0)


@pytest.mark.parametrize('menu,accepted', [('L02/L02-P', True), ('L02', True), ('L06/L06-P', False)])
def test_current_assignment_binding_uses_saved_l_cpu_menu_equivalence_without_inventing_addresses(monkeypatch, menu, accepted):
    from gxw import live_assignments
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline('L02/L02-P')
    # This case isolates menu identity; the empty template supplies no symbols.
    monkeypatch.setattr(live_assignments, 'native_inspection_queries', lambda *_: None)
    identity = dict(process_id=1, window_handle=2, process_started='3', executable='C:/GX/GD2.exe',
        software_version='1.635.0.1', project_path='C:/Owned/Project.gxw', cpu_menu=menu,
        native_ui_language='zh-CN', project_codepage=936)
    arguments = dict(revision=0, compile_run_id='empty-template-is-not-current-evidence', identity=identity)
    queries = [dict(scope='global', source='Global1.gh', name='Missing')]
    if accepted:
        result = live_assignments.prepare_live_assignments(raw, queries, **arguments)
        assert result['status'] == 'unavailable'
        assert result['queries'][0]['assignments'] == []
        assert result['identity']['cpu_menu'] == menu
    else:
        with pytest.raises(GXWFormatError, match='another native version or CPU'):
            live_assignments.prepare_live_assignments(raw, queries, **arguments)


@lru_cache(maxsize=1)
def live_assignment_native_witness():
    """Original saved source and MSAA rows, not generated by the new reader."""
    from copy import deepcopy
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxworks2-visible-engineering-session-20261017.zip')) as archive:
        raw = archive.read('experiments/application-05/native-run/Acceptance06.gxw')
        receipt = load_frozen_native_json(archive.read('experiments/application-05/native-run/receipt.json'))
    observation = deepcopy(next(step for step in receipt['steps'] if step['operation'] == 'show_assignments')['observation'])
    observation.update(identity=receipt['identity'], compile_run_id='isolated-test-run')
    return raw, receipt['identity'], observation


@lru_cache(maxsize=1)
def qualified_live_assignment_native_witness():
    """Independent native save, accepted compile receipt, and original MSAA rows."""
    from copy import deepcopy
    root = private_path('research/experiments/native-live-session-20261018')
    raw = (root / '054-restore-measured-compile-option/native-restored.gxw').read_bytes()
    compilation = load_frozen_native_json((root / '055-independent-saved-phase-readback/compile-receipt.json').read_text(encoding='utf-8'))
    identity = {key: value for key, value in compilation['identity'].items() if key != 'path_binding'}
    records = [load_frozen_native_json((root / ('057-qualified-native-local-instance-searches/query-%02d.json' % index)).read_text(encoding='utf-8'))
               for index in (3, 4, 5)]
    observation = dict(revision=compilation['revision'], compile_run_id=compilation['compile_run_id'],
        identity=identity, pid=identity['process_id'],
        cross_references=[deepcopy(record['cross_references'][0]) for record in records])
    assert all(record['pid'] == identity['process_id'] and record['revision'] == compilation['revision'] for record in records)
    return raw, compilation, observation


def test_live_qualified_local_and_instance_assignments_match_independent_native_searches():
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    raw, compilation, observation = qualified_live_assignment_native_witness()
    queries = [record['query'] for record in observation['cross_references']]
    prepared = prepare_live_assignments(raw, queries, revision=compilation['revision'],
        compile_run_id=compilation['compile_run_id'], identity=compilation['identity'])
    assert [entry['search_text'] for entry in prepared['queries']] == [
        'Live_ST_Program/ProgramCount', 'POU_01/Live_Inst.Accum', 'POU_01/Live_Inst.NextValue']
    assert {entry['scope_resolution'] for entry in prepared['queries']} == {'owner_qualified_native_search'}
    result = verify_live_assignments(prepared, observation)
    assert result['status'] == 'current'
    assert [(row['source'], row['name'], row['address'], row['type'], row['compiler_type_code'],
             row['declared_count'], row['reserved_count']) for row in result['rows']] == [
        ('Live_ST_Program', 'ProgramCount', 'D895', 'INT', 2, 1, 1),
        ('POU_01', 'Live_Inst.Accum', 'D897', 'INT', 2, 1, 1),
        ('POU_01', 'Live_Inst.NextValue', 'D898', 'INT', 2, 1, None)]
    accum, output = prepared['queries'][1]['assignments'][0], prepared['queries'][2]['assignments'][0]
    assert accum['component_path'] == [588, 524] and output['component_path'] == [588, 456]
    assert accum['compiled_owner'] == output['compiled_owner'] == 'Live_ST_FB'
    assert accum['component_offset'] == accum['component_path'][-1]
    assert output['count_origin'] == 'unavailable'  # Two native uses do not prove a reservation count.


@pytest.mark.parametrize('index,damage', [
    (index, damage) for index in range(3) for damage in
    ('plain_search', 'foreign_owner_search', 'plain_summary', 'foreign_summary', 'extra_quoted_summary',
     'address', 'foreign_resource', 'foreign_pou', 'suffix_label', 'foreign_query')
] + [(1, 'bare_caller_label'), (2, 'bare_caller_label'), (1, 'foreign_instance_label')])
def test_live_qualified_native_search_rejects_unbound_sources_and_member_suffixes(index, damage):
    from copy import deepcopy
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    raw, compilation, original = qualified_live_assignment_native_witness()
    observation = deepcopy(original)
    record = observation['cross_references'][index]
    observation['cross_references'] = [record]
    prepared = prepare_live_assignments(raw, [record['query']], revision=compilation['revision'],
        compile_run_id=compilation['compile_run_id'], identity=compilation['identity'])
    query, row = record['query'], record['rows'][0]
    if damage == 'plain_search': record['search_text'] = query['name']
    elif damage == 'foreign_owner_search': record['search_text'] = 'OtherProgram/' + query['name']
    elif damage == 'plain_summary': record['summary'] = '2件：软元件/标签"' + query['name'] + '"的交叉参照信息'
    elif damage == 'foreign_summary': record['summary'] = '2件：软元件/标签"OtherProgram/' + query['name'] + '"的交叉参照信息'
    elif damage == 'extra_quoted_summary': record['summary'] += ' "' + record['search_text'] + '"'
    elif damage == 'address': row['address'] = 'D898' if index != 2 else 'D897'
    elif damage == 'foreign_resource': row['program'] = 'OtherResource'
    elif damage == 'foreign_pou': row['pou'] = 'OtherFB'
    elif damage == 'suffix_label': row['label'] = 'Unrelated.' + row['label']
    elif damage == 'foreign_query': query['source'] = 'OtherProgram'
    elif damage == 'bare_caller_label': row['pou'] = 'POU_01'
    elif damage == 'foreign_instance_label': row['label'] = 'Other_Inst.Accum'
    result = verify_live_assignments(prepared, observation)
    assert result['status'] == 'unavailable' and result['rows'] == []


@pytest.mark.parametrize('index', [0, 1])
def test_live_owner_qualified_search_resolves_same_named_declarations_in_other_programs(monkeypatch, index):
    from dataclasses import replace
    from gxw import live_assignments
    raw, compilation, original = qualified_live_assignment_native_witness()
    observation = {**original, 'cross_references': [original['cross_references'][index]]}
    query = observation['cross_references'][0]['query']
    local, globals_ = live_assignments._native_assignment_sources(raw)
    document = local[query['source']]
    root_name = query['name'].split('.')[0]
    shadow = next(row for row in document.rows if row.name == root_name)
    other_owner = 'POU_01' if index == 0 else 'Live_ST_Program'
    local[other_owner] = replace(local[other_owner], rows=local[other_owner].rows + (shadow,))
    monkeypatch.setattr(live_assignments, '_native_assignment_sources', lambda *_: (local, globals_))
    prepared = live_assignments.prepare_live_assignments(raw, [query], revision=compilation['revision'],
        compile_run_id=compilation['compile_run_id'], identity=compilation['identity'])
    assert len(prepared['queries'][0]['source_candidates']) == 2
    assert prepared['queries'][0]['scope_resolution'] == 'owner_qualified_native_search'
    assert live_assignments.verify_live_assignments(prepared, observation)['status'] == 'current'


def test_live_allocations_read_native_source_type_and_reserved_units_separately():
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    raw, identity, observation = live_assignment_native_witness()
    query = dict(source='Global1.gh', name='Result', scope='global')
    prepared = prepare_live_assignments(raw, [query], revision=3, compile_run_id='isolated-test-run', identity=identity)
    result = verify_live_assignments(prepared, observation)
    assigned, = result['rows']
    assert result['status'] == 'current' and result['execution'] == 'not_run'
    assert (assigned['address'], assigned['type'], assigned['declared_count'], assigned['reserved_count']) == ('D7999', 'INT', 1, 1)
    assert assigned['compiled_owner'] == '@GLOBALS' and assigned['source'] == 'Global1.gh'
    assert assigned['compiler_type_code'] == 2 and assigned['count_origin'] == 'compiler_reserved_units'
    member = prepare_live_assignments(raw, [dict(source='POU_01', name='ScaleInst.OutValue', scope='instance')],
        revision=3, compile_run_id='isolated-test-run', identity=identity)['queries'][0]['assignments'][0]
    assert member['address'] == 'D7999' and member['type'] == 'INT' and member['declared_count'] == 1
    assert member['reserved_count'] is None and member['count_origin'] == 'unavailable'
    assert len(member['component_path']) == 2


@pytest.mark.parametrize('damage', ['revision', 'run', 'identity', 'address', 'owner', 'resource', 'label',
    'query', 'duplicate_query', 'missing_query', 'summary', 'unconfirmed_page', 'missing_row', 'repeated_row'])
def test_live_allocations_reject_foreign_compile_query_owner_and_native_rows(damage):
    from copy import deepcopy
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    raw, identity, original = live_assignment_native_witness()
    observation = deepcopy(original)
    query = dict(source='Global1.gh', name='Result', scope='global')
    prepared = prepare_live_assignments(raw, [query], revision=3, compile_run_id='isolated-test-run', identity=identity)
    record = observation['cross_references'][0]
    if damage == 'revision': observation['revision'] = 2
    elif damage == 'run': observation['compile_run_id'] = 'old-native-run'
    elif damage == 'identity': observation['identity'] = {**identity, 'project_path': 'C:/other.gxw'}
    elif damage == 'address': record['rows'][0]['address'] = 'D7998'
    elif damage == 'owner': record['rows'][0]['pou'] = 'UnrelatedFB'
    elif damage == 'resource': record['rows'][0]['program'] = 'UnrelatedResource'
    elif damage == 'label': record['rows'][0]['label'] = 'Foreign'
    elif damage == 'query': record['query']['source'] = 'Other.gh'
    elif damage == 'duplicate_query': observation['cross_references'] *= 2
    elif damage == 'missing_query': observation['cross_references'] = []
    elif damage == 'summary': record['summary'] = '2件：软元件/标签"Result"的交叉参照信息'
    elif damage == 'unconfirmed_page': record['visible_completion']['completed'] = False
    elif damage == 'missing_row': record['rows'][0].pop('location')
    elif damage == 'repeated_row':
        record['rows'] *= 2
        record['summary'] = '2件：软元件/标签"Result"的交叉参照信息'
    result = verify_live_assignments(prepared, observation)
    assert result['status'] == 'unavailable' and result['rows'] == []
    assert result['results'][0]['reason']


@settings(max_examples=20, deadline=None, derandomize=True)
@given(st.integers(min_value=1, max_value=8), st.booleans())
def test_live_native_occurrence_count_never_replaces_allocation_reserved_units(occurrences, other_owner):
    from copy import deepcopy
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    raw, identity, original = live_assignment_native_witness()
    observation = deepcopy(original)
    prepared = prepare_live_assignments(raw, [dict(source='Global1.gh', name='Result', scope='global')],
        revision=3, compile_run_id='isolated-test-run', identity=identity)
    record = observation['cross_references'][0]
    first = record['rows'][0]
    record['rows'] = [{**first, 'native_row': index + 2,
                       'pou': 'Unrelated' if other_owner else first['pou']} for index in range(occurrences)]
    record['summary'] = str(occurrences) + '件：软元件/标签"Result"的交叉参照信息'
    result = verify_live_assignments(prepared, observation)
    assert result['status'] == ('unavailable' if other_owner else 'current')
    if not other_owner:
        assert result['rows'][0]['reserved_count'] == result['rows'][0]['declared_count'] == 1
        assert len(result['results'][0]['native_rows']) == occurrences


def test_live_instance_paths_keep_same_named_native_fb_members_distinct_and_reject_stale_types():
    from gxw.live_assignments import prepare_live_assignments, verify_live_assignments
    _, identity, _ = live_assignment_native_witness()
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-20260910.zip')) as archive:
        raw, stale = archive.read('t1.gxw'), archive.read('t2.gxw')
    queries = [dict(source='1', name=name, scope='instance') for name in ('TON_A.ET', 'TON_B.ET')]
    prepared = prepare_live_assignments(raw, queries, revision=1, compile_run_id='two-native-instances', identity=identity)
    first, second = [entry['assignments'][0] for entry in prepared['queries']]
    assert first['compiled_owner'] == second['compiled_owner'] == 'TON'
    assert first['compiled_owner_offset'] != second['compiled_owner_offset']
    assert [(row['address'], row['type'], row['declared_count'], row['reserved_count']) for row in (first, second)] == [
        ('D996', 'TIME', 1, 2), ('D986', 'TIME', 1, 2)]
    records = [dict(query=query, search_text='1/' + query['name'], summary='1件：软元件/标签"1/' + query['name'] + '"的交叉参照信息',
        visible_completion=dict(completed=True, page='交叉参照'), rows=[dict(label=query['name'], address=address,
        program='MAIN', pou='1', location='native instance use', read_write='')])
        for query, address in zip(queries, ('D996', 'D986'))]
    observation = dict(revision=1, compile_run_id='two-native-instances', identity=identity, cross_references=records)
    assert verify_live_assignments(prepared, observation)['status'] == 'current'
    records[0]['rows'][0]['address'], records[1]['rows'][0]['address'] = 'D986', 'D996'
    assert verify_live_assignments(prepared, observation)['status'] == 'unavailable'
    stale_result = prepare_live_assignments(stale, queries, revision=1, compile_run_id='old-cached-types', identity=identity)
    assert stale_result['queries'][0]['status'] == 'unavailable'
    assert 'type differs' in stale_result['queries'][0]['reason']
    assert stale_result['queries'][1]['assignments'][0]['address'] == 'D986'


def test_live_same_named_global_and_local_cannot_be_identified_by_an_identical_address_and_caller(monkeypatch):
    from dataclasses import replace
    from gxw import live_assignments
    raw, identity, observation = live_assignment_native_witness()
    local, globals_ = live_assignments._native_assignment_sources(raw)
    document = local['ScaleValue']
    shadow = replace(next(row for row in document.rows if row.name == 'OutValue'), name='Result')
    local['ScaleValue'] = replace(document, rows=document.rows + (shadow,))
    monkeypatch.setattr(live_assignments, '_native_assignment_sources', lambda *_: (local, globals_))
    prepared = live_assignments.prepare_live_assignments(raw,
        [dict(source='Global1.gh', name='Result', scope='global')], revision=3,
        compile_run_id='isolated-test-run', identity=identity)
    assert prepared['queries'][0]['scope_resolution'] == 'requires_native_source_selection'
    result = live_assignments.verify_live_assignments(prepared, observation)
    assert result['status'] == 'unavailable' and not result['rows']
    assert 'source selection' in result['results'][0]['reason']


@pytest.mark.parametrize('damage', ['missing_cg', 'invalid_owner', 'invalid_member', 'invalid_global', 'wrong_cpu', 'wrong_version'])
def test_live_assignment_preparation_never_invents_missing_sources_or_allocations(damage):
    from gxw.live_assignments import prepare_live_assignments
    from gxw.container_writer import replace_project_stream, validate_cfb_streams
    raw, identity, _ = live_assignment_native_witness()
    identity = dict(identity)
    query = dict(source='POU_01', name='ScaleInst.OutValue', scope='instance')
    if damage == 'missing_cg':
        outer = validate_cfb_streams(raw)
        # Keep the container valid while removing the compiler-table logical source.
        xml = outer['projectdatalist.xml']
        changed = xml
        for encoding in ('utf-8', 'utf-16le'):
            changed = changed.replace('CGTable.dat'.encode(encoding), 'NoTable.dat'.encode(encoding))
        assert changed != xml
        raw = replace_project_stream(raw, 'projectdatalist.xml', changed)[0]
    elif damage == 'invalid_owner': query['source'] = 'UnrelatedProgram'
    elif damage == 'invalid_member': query['name'] = 'ScaleInst.Missing'
    elif damage == 'invalid_global': query = dict(source='POU_01', name='Result', scope='global')
    elif damage == 'wrong_cpu': identity['cpu_menu'] = 'Q03UDV'
    elif damage == 'wrong_version': identity['software_version'] = 'unverified version'
    if damage in ('invalid_owner', 'invalid_global', 'wrong_cpu', 'wrong_version'):
        with pytest.raises(GXWFormatError):
            prepare_live_assignments(raw, [query], revision=3, compile_run_id='isolated-test-run', identity=identity)
    else:
        result = prepare_live_assignments(raw, [query], revision=3, compile_run_id='isolated-test-run', identity=identity)
        assert result['status'] == 'unavailable' and result['queries'][0]['assignments'] == []

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = private_path('research/' + 'evidence/gxw-structured-compiler-20260920.zip')
COMPONENT_ARCHIVE = private_path('research/' + 'evidence/gxw-compiler-components-20260920.zip')
SYMBOL_ARCHIVE = private_path('research/' + 'evidence/gxw-compiler-symbols-20260920.zip')
REPLAY_ARCHIVE = private_path('research/' + 'evidence/gxw-compiler-replay-20260920.zip')
CALLSITE_INPUT_CASES = load_frozen_native_json(
    (private_path('tests/fixtures/gxw_callsite_inputs.json')).read_text(encoding="utf-8")
)["cases"]
GRAPH_INTERFACE_CASES = load_frozen_native_json(
    (private_path('tests/fixtures/gxw_callable_ports.json')).read_text(encoding="utf-8")
)["interfaces"]


@lru_cache(maxsize=5)
def sfc_shared_check_witness(case='current-grown-name-fixed', *, diagnostic=False):
    prefix = ('diagnostic/' if diagnostic else 'shared-check/') + case + '/'
    native = prefix + ('validate_project/' if diagnostic else 'native/')
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-object-edit-20261013.zip')) as archive:
        return {
            'raw': archive.read(prefix + ('candidate.gxw' if diagnostic else 'input.gxw')),
            'request': load_frozen_native_json(archive.read(native + 'request.json')),
            'observation': load_frozen_native_json(archive.read(native + 'observation.json')),
            'snapshots': {name[len(native):]: archive.read(name) for name in archive.namelist()
                          if name.startswith(native) and name.endswith('.bin')},
        }


@lru_cache(maxsize=8)
def sfc_instruction_witness(case='zoom-ldi-mov'):
    from research.freeze_gxw_sfc_child_positions import ARCHIVE, case_data
    with zipfile.ZipFile(ARCHIVE) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name.startswith(case + '/validate_project/')}
    raw, observation, snapshots = case_data(entries, case)
    observation = decode_frozen_workspace_evidence(observation)
    return dict(raw=raw, observation=observation, snapshots=snapshots,
                request=load_frozen_native_json(entries[case + '/validate_project/request.json']))


def test_sfc_instruction_positions_replay_width_moves_and_distinct_output_control(archived_native_requests):
    from research.freeze_gxw_sfc_child_positions import replay
    result = archived_native_requests(replay)
    assert result['native_save_calls'] == result['cold_native_checks'] == len(result['cases']) == 8
    assert (result['diagnostics'], result['instruction_resolved'], result['instruction_unresolved']) == (13, 10, 3)
    assert result['controls']['wide_prefix_steps'] == [0, 6]
    assert result['controls']['duplicate_output_reports_removed'] == 2


def test_sfc_source_graph_replay_matches_current_children_and_preserves_native_edit_rejection(archived_native_requests):
    from research.freeze_gxw_sfc_source_graph import replay
    result = archived_native_requests(replay)
    assert result['native_observations'] == len(result['cases']) == 5
    assert result['read_only_observations'] == 4 and result['rejected_edit_attempts'] == 1
    assert result['native_edit_calls_rejected'] == 2
    assert {(row['child_kind'], row['x'], row['y']) for row in result['diagnostic_locations']} == {
        ('zoom', 0, 3), ('transition', 0, 1)}
    assert all(row['application_projection'] == 'not_integrated' for row in result['diagnostic_locations'])
    assert result['cases'][3]['edit'] == 'native-rejected/no-change'


def test_sfc_graph_diagnostic_correlates_the_current_cell_without_promoting_the_failed_location_query():
    from gxw.native_diagnostics import project_native_sfc_graph_diagnostics
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read('action-error/candidate.gxw')
        original = load_frozen_native_json(z.read('action-error/check.json'))['data']['data']
        name = next(n for n in z.namelist() if n.startswith('action-error/workspace/') and n.endswith('v0002/write_report.json'))
        graph = load_frozen_native_json(z.read(name))['sfc_graph']
    result = project_native_sfc_graph_diagnostics(raw, original, graph)
    row, = result['diagnostic_projection']['source_diagnostics']
    assert row['graph_projection']['status'] == 'uniquely_correlated'
    assert row['graph_projection']['cell']['id'] == 'cell:1:3'
    assert row['graph_projection']['cell']['binding']['action_number'] == 10
    assert result['public_check'] == original['public_check'] == dict(status='rejected', completed=True)
    assert result['current_source_check'] == original['current_source_check'] == 'established'
    assert row['source_projection']['native_location_query']['code'] == 0x2d010025
    assert original['diagnostic_projection']['source_diagnostics'][0]['graph_projection']['status'] == 'unresolved'


def test_sfc_graph_diagnostic_preserves_stale_evidence_and_multiple_occurrences():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_sfc_graph_diagnostics
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read('action-error/candidate.gxw')
        original = load_frozen_native_json(z.read('action-error/check.json'))['data']['data']
        name = next(n for n in z.namelist() if n.startswith('action-error/workspace/') and n.endswith('v0002/write_report.json'))
        geometry = load_frozen_native_json(z.read(name))['sfc_graph']
    @settings(max_examples=32, deadline=None, derandomize=True)
    @given(st.sampled_from(['current', 'old-body', 'wrong-cpu', 'wrong-program', 'unchecked', 'wrong-child', 'old-offset', 'multiple']))
    def property_case(change):
        graph, check = deepcopy(geometry), deepcopy(original)
        source = check['diagnostic_projection']['source_diagnostics'][0]['source_projection']['source']
        if change == 'old-body': graph['source_body_hex'] = graph['source_body_hex'][:-2] + 'ff'
        elif change == 'wrong-cpu': graph['cpu'] = 'FX3G'
        elif change == 'wrong-program': graph['logical_name'] = 'Another.Program.pou'
        elif change == 'unchecked': check['current_source_check'] = 'not_established'
        elif change == 'wrong-child': source['child']['name'] = 'AnotherChild'
        elif change == 'old-offset': source['child']['token_region']['offset'] += 7
        elif change == 'multiple':
            # Synthetic competing getter cells; no native acceptance is claimed.
            cell = deepcopy(next(row for row in graph['after']['cells'] if row['result'] == 1 and row['number'] == 2))
            cell['x'] = 2; graph['after']['cells'].append(cell)
        result = project_native_sfc_graph_diagnostics(raw, check, graph)
        projection = result['diagnostic_projection']['source_diagnostics'][0]['graph_projection']
        assert projection['status'] == ('uniquely_correlated' if change == 'current' else 'unresolved')
        if change == 'multiple':
            assert projection['reason'] == 'multiple_current_native_cells'
            assert {(row['x'],row['y']) for row in projection['candidates']} == {(1,3),(2,3)}
        assert result['public_check'] == original['public_check']
        assert result['diagnostic_projection']['source_diagnostics'][0]['source_projection']['native_location_query']['code'] == 0x2d010025
    property_case()


def test_sfc_instruction_positions_require_current_code_and_preserve_unknown_step_boundaries():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    changes = ['current', 'old-input', 'missing-child', 'wrong-type', 'report-id', 'wrong-registration',
               'step-interior', 'step-outside', 'unsupported-code', 'network', 'record-size']

    @settings(max_examples=48, deadline=None, derandomize=True)
    @example('zoom-ldi-mov', 'step-interior')
    @example('transition-ldi-mov', 'missing-child')
    @example('zoom-extra-circuit', 'current')
    @given(st.sampled_from(['zoom-ldi-mov', 'transition-ldi-mov', 'zoom-extra-circuit']), st.sampled_from(changes))
    def check(case, damage):
        witness = sfc_instruction_witness(case)
        observation, snapshots = deepcopy(witness['observation']), dict(witness['snapshots'])
        events = observation['validation']['events']
        reports = [row for event in events if event.get('operation') == 'ProgramCheckOriginalReports'
                   for row in event['reports'] if row['kind'] == 2]
        target = next(row for row in reports if row['code'] == 0x050c9319 and row['step'] in (6, 3))
        if damage == 'old-input':
            event = next(row for row in events if row.get('operation') == 'OwnedCheckInput')
            name = event['rows'][0]['file']; snapshots[name] = snapshots[name][:-1] + bytes([snapshots[name][-1] ^ 1])
        elif damage in ('missing-child', 'wrong-type'):
            event = next(row for row in events if row.get('operation') == 'NativeSfcChildRead'
                         and row['stage'] == 'after-check' and row['identity']['id'] == target['source_object_id'])
            if damage == 'missing-child': del snapshots[event['file']]
            else: event['identity']['read_type']['data_type'] ^= 1
        elif damage == 'report-id': target['source_object_id'][0] ^= 1
        elif damage == 'wrong-registration': target['bottom' if case.startswith('transition') else 'right'] += 1
        elif damage == 'step-interior': target['step'] = 5 if case != 'zoom-extra-circuit' else 999
        elif damage == 'step-outside': target['step'] = 999
        elif damage == 'unsupported-code': target['code'] = 0x050c9322
        elif damage == 'network': target['network'] = 1
        elif damage == 'record-size': target['record_size'] = 88
        result = project_native_validation(witness['raw'], observation, snapshots)
        rows = result['diagnostic_projection']['source_diagnostics']
        matching, = [row for row in rows if row['original'] == target]
        instruction = matching['source_projection'].get('instruction_position', {})
        assert (instruction.get('status') == 'source-resolved') is (damage == 'current')
        assert result['compilation']['acceptance'] == 'accepted'
        assert result['public_check']['status'] == ('incomplete' if damage == 'record-size' else 'rejected')
        if damage == 'current':
            position = instruction['position']
            assert (position['native_step'], position['op'], position['args']) == (6 if case != 'zoom-extra-circuit' else 3, 'OUT', ['X0'])
        if damage in ('old-input', 'missing-child', 'wrong-type'):
            assert result['current_source_check'] == 'not_established'
            assert not any(row['source_projection'].get('instruction_position', {}).get('status') == 'source-resolved' for row in rows)
    check()


@lru_cache(maxsize=1)
def sfc_position_menu_witnesses():
    import zlib
    data = load_frozen_native_json((private_path('tests/fixtures/gxw_sfc_position_menus.json')).read_bytes())
    return [{**row, 'raw': zlib.decompress(base64.b64decode(row['raw_zlib_base64'])),
             'snapshots': {name: zlib.decompress(base64.b64decode(value))
                           for name, value in row['snapshots_zlib_base64'].items()}}
            for row in data['cases']]


@pytest.mark.parametrize('index', [0, 1])
def test_sfc_position_menu_reports_match_current_child_steps_and_graph_cells(index):
    from gxw.native_diagnostics import project_native_validation, project_native_sfc_graph_diagnostics
    witness = sfc_position_menu_witnesses()[index]
    result = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    result = project_native_sfc_graph_diagnostics(witness['raw'], result, witness['graph'])
    assert result['current_source_check'] == 'established'
    assert result['public_check'] == dict(status='rejected', completed=True)
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == (9 if index == 0 else 5)
    for diagnostic in diagnostics:
        instruction = diagnostic['source_projection']['instruction_position']
        assert instruction['status'] == 'source-resolved' and not instruction['public_check_promoted']
        position = instruction['position']
        assert position['native_step'] == diagnostic['original']['step']
        assert position['child_byte_offset'] == (20 if position['native_step'] == 6 else 7)
        assert position['op'] in ('OUT', 'MOV')
        assert diagnostic['graph_projection']['status'] == 'uniquely_correlated'
        assert diagnostic['source_projection']['native_location_query']['code'] == 0x2d010025


@settings(max_examples=24, deadline=None, derandomize=True)
@given(index=st.integers(min_value=0, max_value=1), interior=st.integers(min_value=2, max_value=5),
       wrong_block=st.integers(min_value=1, max_value=127))
def test_sfc_position_mapping_rejects_instruction_interiors_wrong_blocks_and_unchecked_sources(index, interior, wrong_block):
    from copy import deepcopy
    from gxw.native_diagnostics import (_native_validation_sources, project_native_sfc_instruction_position,
                                       project_native_validation)
    witness = sfc_position_menu_witnesses()[index]
    result = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    diagnostic = next(row for row in result['diagnostic_projection']['source_diagnostics']
                      if row['source_projection']['instruction_position']['position']['op'] == 'MOV')
    _, sources = _native_validation_sources(witness['raw'])
    report = diagnostic['original']; projection = diagnostic['source_projection']
    def position(original, projected, current=True):
        return project_native_sfc_instruction_position(original, projected, sources, cpu=witness['cpu'],
            native_version='1.635.0.1', current_check=current)
    assert position({**report, 'step': interior}, projection)['status'] == 'unresolved'
    assert position({**report, 'network': wrong_block}, projection)['status'] == 'unresolved'
    assert position(report, projection, False)['status'] == 'unresolved'
    damaged = deepcopy(projection); damaged['source']['child']['token_region']['offset'] += 1
    assert position(report, damaged)['status'] == 'unresolved'
    damaged = deepcopy(projection); damaged['current_source_body'] = 'stale'
    assert position(report, damaged)['status'] == 'unresolved'


def test_sfc_instruction_positions_keep_unbound_prefixes_and_unverified_cpu_scope_unresolved():
    from gxw.native_diagnostics import (_native_validation_sources, project_native_sfc_instruction_position,
                                       project_native_validation)
    witness = sfc_instruction_witness('zoom-ldi-and')
    actual = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    assert actual['current_source_check'] == 'established'
    assert all(row['source_projection']['instruction_position']['status'] == 'unresolved'
               for row in actual['diagnostic_projection']['source_diagnostics'])
    witness = sfc_instruction_witness()
    actual = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    row, = actual['diagnostic_projection']['source_diagnostics']; _, sources = _native_validation_sources(witness['raw'])
    for cpu in ('FXFuture', 'QFuture', 'LFuture', 'Q02H'):
        result = project_native_sfc_instruction_position(row['original'], row['source_projection'], sources,
            cpu=cpu, native_version='1.635.0.1', current_check=True)
        assert result['status'] == 'unresolved'
    assert project_native_sfc_instruction_position(row['original'], row['source_projection'], sources,
        cpu='FX3U/FX3UC', native_version='1.634.0.1', current_check=True)['status'] == 'unresolved'


@pytest.mark.parametrize('case,code_size', [('current-baseline', 170), ('current-grown-name-fixed', 177)])
def test_sfc_shared_check_binds_graph_all_children_public_completion_and_current_input(case, code_size):
    from gxw.native_diagnostics import native_validation_source_plan, project_native_validation
    witness = sfc_shared_check_witness(case)
    plan = native_validation_source_plan(witness['raw'])
    adapter_fields = {'compilation_method', 'lexical_reader', 'graph_profile'}
    assert plan == {key: value for key, value in witness['request']['source'].items() if key not in adapter_fields}
    assert not adapter_fields.intersection(plan)
    assert len(plan['programs'][0]['children']) == 5
    result = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    assert result['selection']['status'] == 'verified'
    assert result['selection']['configuration_bytes_current'] is True
    assert result['source_bodies']['status'] == 'current'
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['compilation']['acceptance'] == 'accepted'
    assert len(witness['snapshots']['pcode-0-0.bin']) == code_size
    assert result['owned_check_inputs']['correspondence'] == 'current'
    assert result['published_resources']['correspondence'] == 'current'
    assert result['public_check'] == {'status': 'passed', 'completed': True}
    assert result['current_source_check'] == 'established'
    assert result['current_raw_source_check'] == 'not_established'


@pytest.mark.parametrize('damage', ['before-child', 'after-child', 'last-child', 'duplicate-child', 'foreign-parent',
                                   'wrong-type', 'old-input', 'input-layout', 'input-version', 'empty-task',
                                   'missing-end', 'old-build-method'])
def test_sfc_shared_check_never_promotes_missing_or_stale_source_and_check_evidence(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    witness = sfc_shared_check_witness()
    observation, snapshots = deepcopy(witness['observation']), dict(witness['snapshots'])
    events = observation['validation']['events']
    children = [row for row in events if row.get('operation') == 'NativeSfcChildRead']
    if damage in ('before-child', 'after-child', 'last-child'):
        stage = {'before-child': 'before-build', 'after-child': 'after-build', 'last-child': 'after-check'}[damage]
        row = next(row for row in children if row['stage'] == stage)
        snapshots[row['file']] = snapshots[row['file']][:-1] + bytes([snapshots[row['file']][-1] ^ 1])
    elif damage == 'duplicate-child':
        events.append(deepcopy(children[-1]))
    elif damage == 'foreign-parent':
        children[-1]['parent'] = [1] * 12
    elif damage == 'wrong-type':
        children[-1]['identity']['read_type']['data_type'] = 62
    elif damage in ('old-input', 'input-layout', 'input-version'):
        for row in events:
            if row.get('operation') != 'OwnedCheckInput':
                continue
            if damage == 'old-input':
                name = row['rows'][0]['file']
                snapshots[name] = snapshots[name][:-1] + bytes([snapshots[name][-1] ^ 1])
            elif damage == 'input-layout':
                row['compiler_member_offset'] = 12
            else:
                row['input_backend']['version'] = '1.634.0.1'
    elif damage == 'empty-task':
        for row in events:
            if row.get('operation') == 'NativeTaskSelection':
                for task in row['tasks']:
                    task['programs'] = []
    elif damage == 'missing-end':
        for row in events:
            if row.get('operation') == 'ProgramCheckOriginalReports':
                row['reports'] = [r for r in row['reports'] if not (r['kind'] == 1 and r['code'] == 35)]
    else:
        next(row for row in events if row.get('operation') == 'Compiler.ChangeSFCProgram')['operation'] = 'Compiler.Build'
    result = project_native_validation(witness['raw'], observation, snapshots)
    assert result['current_source_check'] == 'not_established'
    assert result['current_raw_source_check'] == 'not_established'


def test_sfc_child_read_order_is_independent_but_all_current_phase_reads_are_required():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    witness = sfc_shared_check_witness()
    @settings(max_examples=24, deadline=None, derandomize=True)
    @given(st.permutations(range(5)), st.sampled_from(['before-build', 'after-build', 'after-check']),
           st.integers(0, 4))
    def exercise(order, stage, missing):
        observation = deepcopy(witness['observation'])
        events = observation['validation']['events']
        positions = [i for i, row in enumerate(events) if row.get('operation') == 'NativeSfcChildRead' and row['stage'] == stage]
        rows = [events[i] for i in positions]
        for i, index in enumerate(order):
            events[positions[i]] = rows[index]
        current = project_native_validation(witness['raw'], observation, witness['snapshots'])
        assert current['current_source_check'] == 'established'
        snapshots = dict(witness['snapshots'])
        del snapshots[rows[missing]['file']]
        partial = project_native_validation(witness['raw'], observation, snapshots)
        assert partial['current_source_check'] == 'not_established'
        assert partial['compilation']['acceptance'] == 'accepted'
    exercise()


@pytest.mark.parametrize('case,kind,native_type', [('action-out-x0-count-fixed', 'zoom', 68),
                                                 ('transition-out-x0', 'transition', 66)])
def test_sfc_child_named_error_counts_and_source_identity_survive_failed_native_position_query(case, kind, native_type):
    from gxw.native_diagnostics import project_native_validation
    witness = sfc_shared_check_witness(case, diagnostic=True)
    result = project_native_validation(witness['raw'], witness['observation'], witness['snapshots'])
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['public_check'] == {'status': 'rejected', 'completed': True}
    assert result['current_source_check'] == 'established'
    assert result['current_raw_source_check'] == 'not_established'
    assert result['diagnostic_projection']['source_body_binding'] == 'consistent'
    assert result['diagnostic_projection']['empty_public_list_means_no_errors'] is False
    diagnostic, = result['diagnostic_projection']['source_diagnostics']
    assert diagnostic['original']['code'] == 0x050c9319
    source = diagnostic['source_projection']
    assert source['status'] == 'source-resolved' and source['current_source_body'] == 'current'
    assert source['source']['body_id'] == diagnostic['original']['source_object_id']
    child = source['source']['child']
    assert (child['kind'], child['native_type']) == (kind, native_type)
    assert diagnostic['original']['name']['text'] == 'Block.' + child['name']
    if kind == 'zoom':
        assert [registration['action_number'] for registration in child['registrations']] == [10]
    else:
        assert child['number'] == 0 and not child['registrations']
    assert source['position_interpretation'] == source['node_port_resolution'] == 'not-established'
    assert source['native_location_query']['hresult'] == -2147467259
    assert source['native_location_query']['code'] == 0x2d010025
    assert diagnostic['graph_projection']['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['diagnostic-body-id', 'qualified-name', 'instance-name', 'source-shape',
                                   'child-snapshot', 'parent', 'missing-getter'])
def test_sfc_child_source_projection_requires_independent_current_identity_not_just_a_public_name(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    witness = sfc_shared_check_witness('action-out-x0-count-fixed', diagnostic=True)
    observation, snapshots = deepcopy(witness['observation']), dict(witness['snapshots'])
    events = observation['validation']['events']
    report, = [report for event in events if event['operation'] == 'ProgramCheckOriginalReports'
               for report in event['reports'] if report['kind'] == 2]
    child, = [event for event in events if event['operation'] == 'NativeSfcChildRead'
              and event['stage'] == 'after-check' and event['identity']['id'] == report['source_object_id']]
    if damage == 'diagnostic-body-id':
        other = next(event for event in events if event['operation'] == 'NativeSfcChildRead'
                     and event['stage'] == 'after-check' and event['identity']['id'] != report['source_object_id'])
        report['source_object_id'] = other['identity']['id']
    elif damage == 'qualified-name':
        report['name']['text'] = report['instance']['text'] = 'OtherBlock.' + child['identity']['read_name']['name']
    elif damage == 'instance-name':
        report['instance']['text'] = 'MAIN'
    elif damage == 'source-shape':
        report['program_kind'] = 208
    elif damage == 'child-snapshot':
        name = child['file']
        snapshots[name] = snapshots[name][:-1] + bytes([snapshots[name][-1] ^ 1])
    elif damage == 'parent':
        child['parent'] = [1] * 12
    else:
        child['evidence'] = {'schema_version': 1, 'origin': 'cached_body'}
    result = project_native_validation(witness['raw'], observation, snapshots)
    assert result['public_check'] == {'status': 'rejected', 'completed': True}
    diagnostic, = result['diagnostic_projection']['source_diagnostics']
    assert diagnostic['source_projection']['status'] == 'unresolved'
    assert result['diagnostic_projection']['source_body_binding'] == 'partial_or_unresolved'
    if damage in ('child-snapshot', 'parent', 'missing-getter'):
        assert result['current_source_check'] == 'not_established'
    else:
        assert result['current_source_check'] == 'established'


def test_sfc_child_diagnostic_identity_is_independent_of_read_order_and_rejects_a_mutated_public_id():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    @settings(max_examples=24, deadline=None, derandomize=True)
    @given(st.sampled_from(['action-out-x0-count-fixed', 'transition-out-x0']), st.permutations(range(5)),
           st.integers(min_value=0, max_value=11), st.integers(min_value=1, max_value=3))
    def exercise(case, order, word, delta):
        witness = sfc_shared_check_witness(case, diagnostic=True)
        observation = deepcopy(witness['observation'])
        events = observation['validation']['events']
        positions = [index for index, event in enumerate(events) if event['operation'] == 'NativeSfcChildRead'
                     and event['stage'] == 'after-check']
        children = [events[index] for index in positions]
        for index, chosen in zip(positions, order):
            events[index] = children[chosen]
        current = project_native_validation(witness['raw'], observation, witness['snapshots'])
        diagnostic, = current['diagnostic_projection']['source_diagnostics']
        assert diagnostic['source_projection']['status'] == 'source-resolved'
        report, = [report for event in events if event['operation'] == 'ProgramCheckOriginalReports'
                   for report in event['reports'] if report['kind'] == 2]
        report['source_object_id'][word] ^= delta
        changed = project_native_validation(witness['raw'], observation, witness['snapshots'])
        diagnostic, = changed['diagnostic_projection']['source_diagnostics']
        assert diagnostic['source_projection']['status'] == 'unresolved'
        assert changed['public_check'] == {'status': 'rejected', 'completed': True}
        assert changed['current_source_check'] == 'established'
    exercise()
ARRAY_REFERENCE_CASES = load_frozen_native_json(
    (private_path('tests/fixtures/gxw_array_references_native.json')).read_text(encoding="utf-8")
)["cases"]


def _native_reference_graph(case):
    """Restore the fixture's sparse graph, retaining native record identities."""
    from gxw.compiler_tables import CompilerTable, CompilerTableRecord, CompilerTables
    tables = []
    for row in case["tables"]:
        records = tuple(CompilerTableRecord(r["absolute_offset"], r["table_offset"],
                        base64.b64decode(r["raw_base64"])) for r in row["records"])
        # This buffer supplies the original table extent for range checks.
        # Unselected regions are omitted; it is not a reconstructed CGTable.
        raw = bytearray(struct.pack("<I", row["payload_size"]) + bytes(row["payload_size"]))
        for record in records:
            start = 4 + record.table_offset
            raw[start:start + len(record.raw)] = record.raw
        tables.append(CompilerTable(row["index"], row["absolute_offset"], bytes(raw), records))
    return CompilerTables(b"", tuple(tables))


@pytest.mark.parametrize("case", GRAPH_INTERFACE_CASES, ids=lambda row: row["case"] + "/" + row["symbol"])
def test_graph_formals_match_native_descriptor_observations(case, monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    project = importlib.import_module("probe_gxw_library_sources").observed_graph_formals
    from gxw.lossless import sha256

    raw = base64.b64decode(case["declaration_base64"])
    assert sha256(raw) == case["declaration_sha256"]
    actual = project(raw, input_port_count=case["input_count"])
    assert actual["handling"] == "observed-graph-formals" and not actual["gaps"]
    assert [{k: row[k] for k in ("name", "class_code", "type_code")} for row in actual["formals"]] == case["expected"]
    # Matching the native interface does not establish successful compilation,
    # writeback destinations or equivalence of changed port flags.
    assert actual["execution_effects"] == "not inferred"
    assert base64.b64decode(actual["raw_base64"]) == raw


@pytest.mark.parametrize("control", ["truncated", "wrong-input-count", "missing-extensible-count"])
def test_graph_interface_gaps_retain_input_without_guessing(control, monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    project = importlib.import_module("probe_gxw_library_sources").observed_graph_formals
    case = next(c for c in GRAPH_INTERFACE_CASES if c["symbol"] == "ADD_E-3")
    raw = base64.b64decode(case["declaration_base64"])
    count = case["input_count"]
    if control == "truncated":
        raw = raw[:-1]
    elif control == "wrong-input-count":
        count = 0
    else:
        count = None
    result = project(raw, input_port_count=count)
    assert result["handling"] == "opaque-preserved" and result["gaps"]
    assert result["formals"] == []
    assert base64.b64decode(result["raw_base64"]) == raw


@pytest.mark.parametrize("case", CALLSITE_INPUT_CASES, ids=lambda case: case["id"])
def test_repeated_fb_inputs_use_their_own_caller_code(case, monkeypatch):
    """Native FB body cross-checks freeze a destination independent of final CGTable state."""
    monkeypatch.syspath_prepend(str(private_path('research')))
    compare = importlib.import_module("probe_gxw_callsite_inputs").compare_callsite_inputs
    from gxw.lossless import sha256

    fragment = case["input_fragment"]
    assert sha256(base64.b64decode(fragment["raw_base64"])) == fragment["sha256"]
    for group in case["native_groups"]:
        assert sha256(base64.b64decode(group["raw_base64"])) == group["sha256"]
    result = compare(fragment["records"], case["ports"], case["native_caller"], case["final_allocations"])
    assert result["handling"] == "observed-callsite-input-lowering" and not result["gaps"]
    assert {b["port"]: b["operand"] for b in result["bindings"]} == case["expected_bindings"]
    assert {b["port"]: b["mode"] for b in result["bindings"]} == case["expected_modes"]
    assert case["observed_body_groups"]["exact"] == case["observed_body_groups"]["source_groups"] == 163
    # Final allocations are metadata only: later calls may replace their
    # address and representation, including compact allocations versus tag 7.
    without_final = compare(fragment["records"], case["ports"], case["native_caller"])
    assert {b["port"]: b["operand"] for b in without_final["bindings"]} == case["expected_bindings"]
    wrong = compare(fragment["records"], case["ports"], case["wrong_caller"], case["final_allocations"])
    assert wrong["handling"] == "opaque-preserved" and wrong["gaps"]
    assert wrong["source_records"] == fragment["records"]
    assert wrong["native_records"] == case["wrong_caller"]


@pytest.mark.parametrize("change", ["unknown_instruction", "wrong_port_type", "extra_native_group"])
def test_callsite_input_unknown_shapes_remain_opaque(change, monkeypatch):
    from copy import deepcopy
    monkeypatch.syspath_prepend(str(private_path('research')))
    compare = importlib.import_module("probe_gxw_callsite_inputs").compare_callsite_inputs
    case = deepcopy(CALLSITE_INPUT_CASES[0])
    source, ports, native = case["input_fragment"]["records"], case["ports"], case["native_caller"]
    if change == "unknown_instruction":
        source[1]["op"] = "UNOBSERVED"
    elif change == "wrong_port_type":
        next(p for p in ports if p["name"] == "iEn")["type_marker"] = "W"
    else:
        native += [{"kind": "instruction", "op": "LD", "args": ["X0"]},
                   {"kind": "instruction", "op": "OUT", "args": ["M999"]}]
    result = compare(source, ports, native)
    assert result["handling"] == "opaque-preserved" and result["gaps"]
    assert result["source_records"] == source and result["native_records"] == native


def test_native_link_empty_name_placeholder_is_preserved():
    with zipfile.ZipFile(REPLAY_ARCHIVE) as archive:
        raw = archive.read("two-programs-1/1-2-chained/MAIN.map")
    mapping = parse_compiler_link_map(raw)
    assert mapping.reconstruct() == raw
    assert [entry.name_bytes for entry in mapping.entries] == [b"1.1", b"2.2", b"", b"END"]
    placeholder = mapping.entries[2]
    assert len(placeholder.raw) == 44 and placeholder.fields[0] == 0xFFFFFFFD
    assert placeholder.token_length == placeholder.step_count == 0
    assert mapping.opaque_tail
    # Empty link names do not relax the separate Index framing contract.
    with pytest.raises(GXWFormatError):
        parse_compiler_index(struct.pack("<4I", 1, 1, 4, 0))
    malformed = bytearray(raw)
    struct.pack_into("<I", malformed, placeholder.offset + 40, 1)
    with pytest.raises(GXWFormatError):
        parse_compiler_link_map(malformed)


def test_native_source_prediction_matches_real_gxw_patch_compile():
    from gxw.lossless import patch_structured_symbol_equal_size, sha256
    with zipfile.ZipFile(REPLAY_ARCHIVE) as archive:
        prefix = "gui-mutated-compiler-project/"
        before = archive.read(prefix + "baseline-before-patch.gxw")
        expected = archive.read(prefix + "patched-before-native.gxw")
        result = patch_structured_symbol_equal_size(before, expected_sha256=sha256(before),
            logical_name="1.Program.pou", node_offset=219, old_symbol="X1", new_symbol="X3")
        assert result.data == expected
        baseline_code = archive.read("offline-replay-5/pcode-0-1.bin")
        assert baseline_code == archive.read("gui-native-compiler-files/MAIN.qpg")
        predicted = archive.read("source-mutations-1/input-x3/pcode-0-1.bin")
        assert predicted == archive.read(prefix + "native-compiled/MAIN.qpg")
        assert len(predicted) == 420
        assert [i for i, (a, b) in enumerate(zip(baseline_code, predicted)) if a != b] == [5]
        observed = load_frozen_native_json(archive.read(prefix + "native-compiled/plan.json"))
        prediction = load_frozen_native_json(archive.read("source-mutations-1/input-x3/plan.json"))
        def calls(plan):
            return [{k: value for k, value in unit.items() if k != "event_id"} for unit in plan["compile"]]
        assert len(calls(observed)) == 11 and calls(observed) == calls(prediction)
        # The final END compilation must receive the updated allocation table.
        for kind in ("bool", "int", "mixed"):
            chained = archive.read(f"named-variables-1/{kind}-chained/symbols-after-20.bin")
            reset = archive.read(f"named-variables-1/{kind}-reset/symbols-after-20.bin")
            a, b = parse_compiler_tables(chained), parse_compiler_tables(reset)
            assert all(any(a.component_at(r.table_offset).user_info) for r in a.tables[2].records)
            assert all(not any(b.component_at(r.table_offset).user_info) for r in b.tables[2].records)
            assert archive.read(f"named-variables-1/{kind}-chained/pcode-0-1.bin") == archive.read(f"named-variables-1/{kind}-reset/pcode-0-1.bin")


def test_compiler_fragments_need_no_end_but_complete_programs_still_do():
    fragment = bytes.fromhex("056a010705")  # native SRET fragment
    raw = b"prefix" + fragment + b"suffix"
    parsed = parse_token_fragment(raw, 6, len(fragment))
    assert parsed.body == fragment and parsed.reconstruct() == raw
    assert parsed.tokens[0].offset == 6
    with pytest.raises(GXWFormatError):
        parse_token_region(raw, 6, len(fragment))
    assert parse_token_fragment(b"", 0, 0).reconstruct() == b""
    with pytest.raises(GXWFormatError):
        parse_token_fragment(raw, 6, len(fragment) - 1)


def test_compiler_link_membership_keeps_renamed_unlinked_aliases(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    inspect = importlib.import_module("inspect_gxw_compiler").inspect_storage
    with zipfile.ZipFile(ARCHIVE) as z:
        scan = load_frozen_native_json(z.read("artifact-scan/compiler-storage.json"))
        native = load_frozen_native_json(z.read("public-scan/compiler-storage.json"))["fragments"]
        checked = 0
        for storage in scan["storages"]:
            raw = base64.b64decode(storage["raw_base64"])
            actual, _ = inspect(raw)
            assert actual["index_replay"] and actual["maps"] == storage["maps"]
            cfb = CompoundFile(raw)
            index = parse_compiler_index(cfb.read_stream("Index"))
            assert index.reconstruct() == index.raw
            for mapping in actual["maps"]:
                assert mapping["complete_fragment_partition"] and mapping["reconstructed_code_matches"]
                entry = next(e for e in index.entries if e.display_name("cp936") == mapping["name"])
                table = parse_compiler_link_map(cfb.read_stream(entry.stream_name))
                assert table.reconstruct() == table.raw and table.opaque_tail
                for row in mapping["rows"]:
                    if row["token_length"] and not row["unlinked"]:
                        observation = native[row["body_sha256"]]
                        assert observation["native_readings_agree"]
                        assert observation["native_machinecode"]["native_value"] == row["step_count"]
                        checked += 1
        assert checked == 109
        renamed = next(s for s in scan["storages"] if s["storage_sha256"] ==
                       "facbf55e9c60027a55090fdbaf00ce3ac33f1fb559f32ad99110958dac86d751")
        rows = renamed["maps"][0]["rows"]
        old = next(r for r in rows if r.get("name") == "1.1.TON_A")
        new = next(r for r in rows if r.get("name") == "1.1.AUTO_TON_A")
        assert old["unlinked"] and not new["unlinked"]
        assert old["body_sha256"] == new["body_sha256"]
        assert old["token_offset"] == new["token_offset"]


def test_compiler_tables_match_native_names_offsets_instances_and_replay(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    compare = importlib.import_module("native_gxw_compiler").compare_table_dump
    with zipfile.ZipFile(ARCHIVE) as z:
        observations = load_frozen_native_json(z.read("native-table-corpus/report.json"))
        components = instances = 0
        for case in observations:
            prefix = f"native-table-corpus/{case['id']}/"
            raw = z.read(prefix + "CGTable.dat")
            tables = parse_compiler_tables(raw)
            assert len(tables.tables) == 14 and tables.reconstruct() == raw
            assert z.read(prefix + "native-replay.bin") == raw
            actual = compare(raw, z.read(prefix + "native-table-dump.txt"))
            assert actual["pous_equal"] and actual["components_equal"] and actual["instance_refs_equal"]
            components += actual["components"]
            instances += actual["instances"]
        assert len(observations) == 12 and components == 1345 and instances == 38
        tables = parse_compiler_tables(z.read("CGTable.dat"))
        main = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                    if tables.pou_at(r.table_offset).name_bytes == b"1")
        references = {c.name_bytes: c.instance_pou_offset for c in tables.components(main)}
        a, b = (tables.pou_at(references[k]) for k in (b"TON_A", b"TON_B"))
        assert a.name_bytes == b.name_bytes == b"TON"
        assert a.record.table_offset != b.record.table_offset
        assert a.component_start != b.component_start


def test_compiler_table_unknown_records_survive_and_bad_references_fail():
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read("CGTable.dat")
    assert raw[:4] == b"\0" * 4
    opaque = struct.pack("<II", 7, 3) + b"xyz" + raw[4:]
    parsed = parse_compiler_tables(opaque)
    assert parsed.tables[0].records[0].payload == b"xyz"
    assert parsed.reconstruct() == opaque
    with pytest.raises(GXWFormatError, match="record boundary"):
        parsed.pou_at(1)
    for malformed in (raw[:-1], raw + b"tail", struct.pack("<I", len(raw)) + raw[4:]):
        with pytest.raises(GXWFormatError):
            parse_compiler_tables(malformed)
    invalid_range = bytearray(raw)
    # Use the unshifted source table's first POU and its component start field.
    original = parse_compiler_tables(raw).pou_at(0)
    field = original.record.offset + 4 + 1 + len(original.name_bytes) + 4
    struct.pack_into("<I", invalid_range, field, 1)
    bad = parse_compiler_tables(invalid_range)
    with pytest.raises(GXWFormatError, match="record-bounded"):
        bad.components(bad.pou_at(0))


def test_compiler_debug_keeps_offset_tables_unlinked_elements_and_unknown_tail():
    with zipfile.ZipFile(ARCHIVE) as z:
        artifacts = load_frozen_native_json(z.read("artifact-scan/compiler-storage.json"))["compiler_artifacts"]
        cases = [base64.b64decode(a["raw_base64"]) for a in artifacts.values() if a["kind"] == "DebugInformation2.dat"]
        assert len(cases) == 15
        for raw in cases:
            parsed = parse_compiler_debug(raw)
            assert parsed.reconstruct() == raw
            assert all(t.sentinel[0] == -1 for t in parsed.offset_tables)
        raw = z.read("DebugInformation2.dat")
        parsed = parse_compiler_debug(raw)
        assert len(parsed.elements) == len(parsed.offset_tables) == 4
        assert parsed.tail_offset == 746 and len(parsed.opaque_tail) == 407
        counter = next(e for e in parsed.elements if e.names[3] == b"1.counter_a")
        assert counter.linked_step_start == counter.linked_step_end == -1
        assert parsed.offset_tables[counter.offset_table_index].rows
        assert parse_compiler_debug(raw + b"unknown-extension").reconstruct() == raw + b"unknown-extension"
        bad = bytearray(raw)
        struct.pack_into("<I", bad, parsed.elements[0].offset + len(parsed.elements[0].raw) - 4, 99)
        with pytest.raises(GXWFormatError, match="missing offset table"):
            parse_compiler_debug(bad)
        with pytest.raises(GXWFormatError):
            parse_compiler_debug(raw[:parsed.tail_offset - 1])


def _native_creation_debug_pair():
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import logical_mapping

    prefix = 'cases/native-current-created-inout-imported-axis-create-Q06UDV-v1/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-created-inout-20261010.zip')) as archive:
        result = []
        for stage in ('input', 'candidate'):
            outer = validate_cfb_streams(archive.read(prefix + stage + '.gxw'))
            nested = validate_cfb_streams(outer['_hdb'])
            result.append(nested[logical_mapping(outer['projectdatalist.xml'])['DebugInformation2.dat']])
    return tuple(result)


def test_compiler_debug_search_maps_frame_the_actual_native_creation_permutation():
    from gxw.compiler_debug import compiler_debug_search_map_permutation, parse_compiler_debug_search_maps

    before, after = _native_creation_debug_pair()
    assert before != after
    old, new = [parse_compiler_debug_search_maps(parse_compiler_debug(raw)) for raw in (before, after)]
    assert [len(table.entries) for table in old.maps] == [54, 2, 3, 20, 0]
    assert old.reconstruct() == before and new.reconstruct() == after
    assert not old.opaque_tail and not new.opaque_tail
    # These resource fields and element lists come from the independent native
    # file, not a generator or the comparator's expected output.
    resource = next(entry for entry in old.maps[1].entries if entry.key_bytes == b'SECONDARY_SOURCE')
    assert resource.groups == (tuple(range(26)),)
    assert resource.fields == (9496, 9492, 9492, 9493, 9495)
    instance = next(entry for entry in old.maps[2].entries if entry.key_bytes == b'SECONDARY_SOURCE.SECONDARYAXIS2')
    assert instance.groups == (tuple(range(26)),)
    assert instance.fields == (0, 9491) and instance.resource_bytes == b'SECONDARY_SOURCE'
    assert compiler_debug_search_map_permutation(before, after)
    assert parse_compiler_debug(before).opaque_tail == before[old.debug.tail_offset:]


def test_compiler_debug_search_map_permutations_keep_all_other_bytes():
    from hypothesis import given, settings, strategies as st
    from gxw.compiler_debug import compiler_debug_search_map_permutation, parse_compiler_debug_search_maps

    before, _ = _native_creation_debug_pair()
    parsed = parse_compiler_debug_search_maps(parse_compiler_debug(before))
    orders = st.tuples(*(st.permutations(tuple(range(len(table.entries)))) for table in parsed.maps))

    @settings(max_examples=50, deadline=None, derandomize=True)
    @given(orders, st.binary(max_size=32))
    def check(orderings, opaque):
        changed = before[:parsed.debug.tail_offset] + b''.join(
            struct.pack('<I', len(table.entries)) + b''.join(table.entries[index].raw for index in order)
            for table, order in zip(parsed.maps, orderings)) + opaque
        assert compiler_debug_search_map_permutation(before + opaque, changed)
        assert compiler_debug_search_map_permutation(changed, before + opaque)
        reread = parse_compiler_debug_search_maps(parse_compiler_debug(changed))
        assert reread.reconstruct() == changed and reread.opaque_tail == opaque
        assert not compiler_debug_search_map_permutation(before + opaque, changed + b'changed')

    check()


def menu_completion_witness(name):
    import zipfile
    from pathlib import Path
    path = private_path('research/' + 'evidence/gxw-fbd-menu-completion-20261012.zip')
    with zipfile.ZipFile(path) as archive:
        cases = load_frozen_native_json(archive.read('cases.json'))
        case, = [row for row in cases if row['name'] == name]
        entries = {key.removeprefix(case['prefix']): archive.read(key)
                   for key in archive.namelist() if key.startswith(case['prefix'])}
    return case, entries


@pytest.mark.parametrize('name', [
    'FX0S-two-instances', 'FX0S-two-instances-no-string', 'FX0S-explicit-actuals',
    'FX1-two-instances-no-string', 'FX1-explicit-actuals', 'FX1-single-instance',
    'FX1-scalar-control', 'application-FX0S',
])
def test_menu_completion_replays_native_acceptance_and_separate_check_stages(archived_native_requests, name):
    from research.freeze_gxw_fbd_menu_completion import verify_case
    case, entries = menu_completion_witness(name)
    _, result = archived_native_requests(verify_case, entries.__getitem__, case)
    assert result['diagnostic_projection']['empty_public_list_means_no_errors'] is (name == 'FX1-scalar-control')


def test_menu_completion_ui_created_fxu_source_does_not_establish_a_bootstrap_alias(archived_native_evidence):
    from research.freeze_gxw_fbd_menu_completion import replay_ui_entry
    result = archived_native_evidence(replay_ui_entry)
    assert result['ui_fbd_project_saved'] is True
    assert result['saved_project']['cpu'] == result['ui_selection']['cpu'] == 'FXU/FX2C'
    assert result['CPU_alias_equivalence'] == 'not_established'
    assert len(result['native_cold_open_controls']) == 3
    assert all(row['compilation'] == row['check'] == 'not_requested' for row in result['native_cold_open_controls'])
    scope = result['menu_scope']
    assert scope['denominators_interchangeable'] is False
    assert scope['current_native_new_choices'] == 60
    assert len(scope['cases']) == 6
    assert all(row['compiler_support'] == 'not_tested' for row in scope['cases'])
    assert all(row['classification'] == 'compatibility_redirect_no_native_fbd_entry' for row in scope['cases'])
    assert next(row for row in scope['cases'] if row['cpu'] == 'Q3A')['state'] == 'followup-Q3A-label-languages'
    assert result['ui_cold_open']['ui_cold_open'] == 'opened'
    assert result['ui_cold_open']['ui_cpu'] == result['saved_project']['cpu']
    assert result['ui_cold_open']['compilation'] == result['ui_cold_open']['check'] == 'not_requested'
    assert {row['cpu'] for row in result['parameter_inventory']} == {'LJ72GF15-T2', 'LJ72MS15', 'RemoteI/O'}
    assert all(row['native_pou_inventory'] == 'completed-empty' and row['program_check'] == 'not_requested'
               for row in result['parameter_inventory'])
    address, = [row for row in result['original_function_addresses']['entries'] if row['operation'] == 'OpenProjectEX2']
    assert address['module'] == 'DZDataABS_ProjectOperation.dll' and address['function_invoked'] is False
    translation, = [row for row in result['native_cpu_translation']['rows'] if row['input'] == 'FXU/FX2C']
    assert translation['output'] == 'FXU/FX2C' and translation['cpu_value'] == 0
    assert result['group_language']['cpu_getter_before_after']['FXU/FX2C'] == (0, 51)
    assert result['locale_cold_open']['cold_open'] == 'opened'
    assert result['locale_cold_open']['source_body'] == 'current'
    assert {r['cpu'] for r in result['locale_cold_controls']} == {'FX2/FX2C', 'FX3U/FX3UC', 'Q03UDV', 'L02'}
    assert all(r['cold_open'] == 'opened' and r['current_source_reads'] == 2
               and r['compilation'] == r['check'] == 'not_requested' for r in result['locale_cold_controls'])
    assert next(r for r in result['locale_cold_controls'] if r['cpu'] == 'L02')['ui_cpu'] == 'L02/L02-P'
    application = result['fxu_application']
    assert application['native_cpu'] == 516 and application['application_restart'] == 'passed'
    assert application['preview_save_current_ports'] == 'passed'
    assert application['public_check'] == {'status': 'incomplete', 'completed': False}
    assert application['raw_check'] == 'completed-rejected'
    assert application['instance_graphs'] == [
        {'instance': 'POU_01.MODEL_STAGE_BETA', 'step': 39, 'network': 3},
        {'instance': 'POU_01.APP_SOURCE_ALPHA', 'step': 43, 'network': 1}]


def fxu_application_witness():
    """Original UI project and actual application/native output; no execution."""
    entries = ARCHIVE_CACHE.members(private_path('research/' + 'evidence/gxw-fbd-ui-entry-20261013.zip'),
                                    'application-FXU/')
    observation = load_frozen_native_json(entries['check-fx-grammar-observation.json'])
    snapshots = {name: entries['check-fx-grammar/' + name] for name in load_frozen_native_json(entries['case.json'])['snapshots']}
    return entries, observation, snapshots


def merged_menu_application_witness(cpu):
    entries = ARCHIVE_CACHE.members(private_path('research/' + 'evidence/gxw-fbd-ui-entry-20261013.zip'),
                                    'merged-menu/application-' + cpu + '/')
    stage = 'check-fx3g-lexical' if cpu == 'FX3G' else 'check'
    observation = load_frozen_native_json(entries[stage + '-observation.json'])
    snapshots = {name: entries[stage + '/' + name] for name in load_frozen_native_json(entries['case.json'])['snapshots']}
    return entries, observation, snapshots


def test_frozen_archive_cache_reads_only_selected_members_and_keeps_return_values_isolated(tmp_path, monkeypatch):
    from tests.fixture_cache import ArchiveCache
    path = tmp_path / 'frozen.zip'
    def write(value):
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('first/observation.json', json.dumps({'values': [value]}))
            archive.writestr('second/code.bin', b'native bytes')
            archive.writestr('unrelated/large.bin', b'not requested')
    write(1)
    reads = []
    original = zipfile.ZipFile.read
    def read(archive, name, *args, **kwargs):
        reads.append(name)
        return original(archive, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    cache = ArchiveCache()
    first = cache.members(path, 'first/')
    decoded = load_frozen_native_json(first['observation.json'])
    decoded['values'].append(99)
    first.clear()
    assert load_frozen_native_json(cache.members(path, 'first/')['observation.json']) == {'values': [1]}
    assert cache.members(path, 'second/') == {'code.bin': b'native bytes'}
    assert reads == ['first/observation.json', 'second/code.bin']
    write(2)
    assert load_frozen_native_json(cache.members(path, 'first/')['observation.json']) == {'values': [2]}
    assert reads.count('first/observation.json') == 2


@pytest.mark.parametrize('cpu', [None, 'L02'])
def test_application_witness_cache_does_not_share_mutable_evidence(cpu):
    get = fxu_application_witness if cpu is None else lambda: merged_menu_application_witness(cpu)
    entries, observation, snapshots = get()
    expected = json.dumps(observation, sort_keys=True)
    entries.clear()
    observation.clear()
    snapshots.clear()
    later_entries, later_observation, later_snapshots = get()
    assert later_entries and later_snapshots
    assert json.dumps(later_observation, sort_keys=True) == expected


@pytest.mark.parametrize('cpu', ['L02S', 'L02', 'L06', 'L26', 'L26-BT'])
def test_saved_fbd_pointer_initialization_checks_current_configuration_and_keeps_rejection(cpu):
    from gxw.native_diagnostics import project_native_validation
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fbd-ui-entry-20261013.zip')) as archive:
        root = 'l-pointer-allocation/'
        case, = [row for row in load_frozen_native_json(archive.read(root + 'cases.json')) if row['cpu'] == cpu]
        prefix = root + case['application'] + '/'
        raw = archive.read(prefix + 'candidate.gxw')
        observation = load_frozen_native_json(archive.read(prefix + 'check-observation.json'))
        snapshots = {name: archive.read(prefix + 'check/' + name)
                     for name in load_frozen_native_json(archive.read(prefix + 'case.json'))['snapshots']}
    result = project_native_validation(raw, observation, snapshots)
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['selection']['configuration_bytes_current'] is True
    assert result['current_raw_source_check'] == 'established'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['current_source_check'] == 'not_established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    errors = [r['original'] for r in result['diagnostic_projection']['source_diagnostics'] if r['original']['kind'] == 2]
    assert len(errors) == 2 and {r['code'] for r in errors} == {0x050c9300}
    assert result['diagnostic_projection']['empty_public_list_means_no_errors'] is False


@pytest.mark.parametrize('cpu', ['L02S', 'L02', 'L06', 'L26', 'L26-BT', 'FX3G'])
def test_merged_menu_compilation_and_current_checks_keep_independent_outcomes(cpu):
    from gxw.native_diagnostics import project_native_validation
    entries, observation, snapshots = merged_menu_application_witness(cpu)
    result = project_native_validation(entries['candidate.gxw'], observation, snapshots)
    assert result['cpu'] == cpu and result['compilation']['source_context']['status'] == 'current'
    assert result['compilation']['completed'] is True
    assert result['current_source_check'] == 'not_established'
    assert result['diagnostic_projection']['empty_public_list_means_no_errors'] is False
    if cpu == 'FX3G':
        assert result['compilation']['acceptance'] == 'accepted'
        assert result['public_check'] == {'status': 'incomplete', 'completed': False}
        assert result['raw_backend_check']['status'] == 'completed-rejected'
        assert result['current_raw_source_check'] == 'established'
        assert {r['original']['step']: r['graph_projection']['instance']
                for r in result['diagnostic_projection']['source_diagnostics']} == {
                    40: 'POU_01.MODEL_STAGE_BETA', 45: 'POU_01.APP_SOURCE_ALPHA'}
    else:
        assert result['compilation']['acceptance'] == 'rejected'
        assert any(r['kind'] == 2 and r['code'] == 0x050c9004 for r in result['compilation']['diagnostics'])
        assert result['public_check'] == {'status': 'not_requested', 'completed': False}
        assert result['raw_backend_check']['status'] == 'not_observed'
        assert result['current_raw_source_check'] == 'not_established'
        assert not result['diagnostic_projection']['source_diagnostics']
        assert all(r['graph_projection']['status'] != 'uniquely_correlated'
                   for r in result['compilation']['source_diagnostics'])


@pytest.mark.parametrize('cpu,expected_instances', [
    ('FXU/FX2C', {39: 'POU_01.MODEL_STAGE_BETA', 43: 'POU_01.APP_SOURCE_ALPHA'}),
    ('FX3G', {40: 'POU_01.MODEL_STAGE_BETA', 45: 'POU_01.APP_SOURCE_ALPHA'}),
])
def test_fxu_fbd_instance_projection_requires_unambiguous_native_references_in_any_order(cpu, expected_instances):
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    entries, observation, snapshots = (fxu_application_witness() if cpu == 'FXU/FX2C'
                                       else merged_menu_application_witness(cpu))
    references, = [r['rows'] for r in observation['validation']['events'] if r['operation'] == 'NativeSourceReferences']
    baseline = project_native_validation(entries['candidate.gxw'], observation, snapshots)
    originals = [r['original'] for r in baseline['diagnostic_projection']['source_diagnostics']]
    names = ('APP_SOURCE_ALPHA', 'MODEL_STAGE_BETA')

    @settings(max_examples=40, deadline=None, derandomize=True)
    @example(list(range(20)), 0, 'duplicate-input', 1)
    @example(list(range(20)), 0, 'missing-input', 1)
    @example(list(reversed(range(20))), 1, 'network', 1)
    @given(st.permutations(list(range(20))), st.integers(min_value=0, max_value=1),
           st.sampled_from(['current', 'duplicate-input', 'duplicate-output', 'duplicate-call',
                            'missing-input', 'missing-output', 'missing-call', 'network', 'bbox', 'operand']),
           st.integers(min_value=1, max_value=127))
    def check(order, selected, damage, delta):
        assert len(references) == 20
        changed = deepcopy(observation)
        event, = [r for r in changed['validation']['events'] if r['operation'] == 'NativeSourceReferences']
        event['rows'] = [deepcopy(references[i]) for i in order]
        target = names[selected]
        role = damage.split('-', 1)[-1] if '-' in damage else 'call'
        if role == 'input':
            native, = [r for r in event['rows'] if r.get('source') == 'MODULE_FB'
                       and r.get('instance') == 'POU_01.' + target and r.get('name') == 'SIGNAL']
        elif role == 'output':
            native, = [r for r in event['rows'] if r.get('source') == 'MODULE_FB'
                       and r.get('instance') == 'POU_01.' + target and r.get('name') == 'Y1']
        else:
            native, = [r for r in event['rows'] if r.get('source') == 'POU_01'
                       and r.get('name') == target and r.get('class_code') == 1]
        if damage.startswith('duplicate-'):
            event['rows'].append(deepcopy(native))
        elif damage.startswith('missing-'):
            event['rows'].remove(native)
        elif damage == 'network':
            native['network'] = 2  # An empty source network, not the other call.
        elif damage == 'bbox':
            native['left'] += delta
        elif damage == 'operand':
            output, = [r for r in event['rows'] if r.get('source') == 'MODULE_FB'
                       and r.get('instance') == 'POU_01.' + target and r.get('name') == 'Y1']
            output['address'] = 'Y002'
        event['count'] = len(event['rows'])
        query, = [r for r in changed['validation']['events'] if r['operation'] == 'Compiler.GetProgramAnalysis3']
        query['count'] = event['count']  # Keep the envelope valid; vary reference ambiguity alone.
        result = project_native_validation(entries['candidate.gxw'], changed, snapshots)
        diagnostics = result['diagnostic_projection']['source_diagnostics']
        assert [r['original'] for r in diagnostics] == originals
        assert result['public_check'] == {'status': 'incomplete', 'completed': False}
        assert result['current_raw_source_check'] == 'established'
        for row in diagnostics:
            instance = expected_instances[row['original']['step']]
            # SIGNAL is also the retained original anchor of each interval
            # query. Removing it contradicts the complete native range table,
            # so neither instance may inherit that table's unique interval.
            expected = 'unresolved' if (damage == 'missing-input'
                or damage != 'current' and instance == 'POU_01.' + target) else 'uniquely_correlated'
            assert row['graph_projection']['status'] == expected
            if damage == 'missing-input':
                assert row['source_projection']['instance_projection']['reason'] == (
                    'native interval reference differs from current source references')
            assert row['graph_projection']['public_check_promoted'] is False
    check()


@pytest.mark.parametrize('pou', ['MODULE_FB', 'POU_01'])
@pytest.mark.parametrize('cpu', ['FXU/FX2C', 'FX3G'])
def test_fxu_fbd_instance_projection_does_not_inherit_a_stale_callee_or_caller(pou, cpu):
    from gxw.native_diagnostics import project_native_validation
    entries, observation, snapshots = (fxu_application_witness() if cpu == 'FXU/FX2C'
                                       else merged_menu_application_witness(cpu))
    selections = [r for r in observation['validation']['events']
                  if r.get('operation') == 'NativeBodySelection' and r.get('name') == pou]
    identity = selections[-1]['source_object']['body']['id']
    read = next(r for r in reversed(observation['validation']['events'])
                if r.get('operation') == 'NativeBodyRead' and r['body'] == identity)
    changed = dict(snapshots)
    changed[read['file']] = changed[read['file']][:-1] + bytes([changed[read['file']][-1] ^ 1])
    result = project_native_validation(entries['candidate.gxw'], observation, changed)
    assert result['source_bodies']['status'] == 'stale'
    assert result['current_raw_source_check'] == 'not_established'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == 2
    assert all(r['graph_projection']['status'] == 'unresolved' for r in diagnostics)


@pytest.mark.parametrize('name', ['FX0S-explicit-actuals', 'application-FX0S'])
@pytest.mark.parametrize('damage', ['network', 'bbox'])
def test_menu_completion_fx_instances_do_not_follow_conflicting_native_reference(name, damage):
    from gxw.native_diagnostics import project_native_validation
    case, entries = menu_completion_witness(name)
    observation = load_frozen_native_json(entries['check-observation.json'])
    references, = [row['rows'] for row in observation['validation']['events']
                   if row.get('operation') == 'NativeSourceReferences']
    symbol = 'APP_SOURCE_ALPHA' if case['application'] else 'MODEL_STAGE_ALPHA'
    reference, = [r for r in references if r.get('source') == 'POU_01'
                   and r.get('name') == symbol and r.get('program_kind') == 208
                   and r.get('attribute') == 1]
    if damage == 'network':
        reference['network'] = 2  # Empty network, between the two real calls.
    else:
        reference.update(left=100, top=100, right=105, bottom=104)
    result = project_native_validation(entries['candidate.gxw'], observation,
                                      {key: entries[key] for key in case['snapshots']})
    original = load_frozen_native_json(entries['validation.json'])['diagnostic_projection']['source_diagnostics']
    reports = result['diagnostic_projection']['source_diagnostics']
    assert [r['original'] for r in reports] == [r['original'] for r in original]
    assert result['current_raw_source_check'] == 'established'
    assert sum(r['graph_projection']['status'] == 'uniquely_correlated' for r in reports) == 1
    assert next(r['graph_projection']['instance'] for r in reports
                if r['graph_projection']['status'] == 'uniquely_correlated') == 'POU_01.MODEL_STAGE_BETA'


@pytest.mark.parametrize('change', ['prefix', 'key', 'group-order', 'reference', 'field', 'resource'])
def test_compiler_debug_search_map_nonpermutation_changes_are_refused(change):
    from hypothesis import given, settings, strategies as st
    from gxw.compiler_debug import compiler_debug_search_map_permutation, parse_compiler_debug_search_maps

    before, _ = _native_creation_debug_pair()
    parsed = parse_compiler_debug_search_maps(parse_compiler_debug(before))
    entry = parsed.maps[1].entries[0]
    start = entry.offset + 4 + len(entry.key_bytes) + 1
    reference_start = start + 8
    field_start = start + 4 + sum(4 + 4 * len(group) for group in entry.groups)
    resource = parsed.maps[2].entries[0]
    resource_start = resource.offset + len(resource.raw) - len(resource.resource_bytes) - 1

    @settings(max_examples=12, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=255))
    def check(mask):
        changed = bytearray(before)
        if change == 'group-order':
            size = len(entry.groups[0]) * 4
            changed[reference_start:reference_start + size] = struct.pack('<' + 'i' * len(entry.groups[0]), *reversed(entry.groups[0]))
        else:
            offset = {'prefix': parsed.debug.offset_tables[0].offset + 8 + 20,
                      'key': entry.offset + 4, 'reference': reference_start,
                      'field': field_start, 'resource': resource_start}[change]
            changed[offset] ^= mask
        assert bytes(changed) != before
        assert not compiler_debug_search_map_permutation(before, bytes(changed))

    check()


@pytest.mark.parametrize('damage', ['duplicate-key', 'missing-element', 'truncated', 'unsupported-version'])
def test_compiler_debug_search_map_invalid_views_retain_the_original_tail(damage):
    from gxw.compiler_debug import compiler_debug_search_map_permutation, parse_compiler_debug_search_maps

    before, _ = _native_creation_debug_pair()
    parsed = parse_compiler_debug_search_maps(parse_compiler_debug(before))
    changed = bytearray(before)
    if damage == 'duplicate-key':
        table = parsed.maps[0]
        entry = table.entries[0]
        changed[table.offset:table.offset + len(table.raw)] = (
            struct.pack('<I', len(table.entries) + 1) + entry.raw + b''.join(row.raw for row in table.entries))
    elif damage == 'missing-element':
        entry = parsed.maps[1].entries[0]
        reference = entry.offset + 4 + len(entry.key_bytes) + 1 + 8
        struct.pack_into('<i', changed, reference, len(parsed.debug.elements))
    elif damage == 'truncated':
        changed = changed[:-1]
    else:
        struct.pack_into('<i', changed, 0, 4)
    changed = bytes(changed)
    assert not compiler_debug_search_map_permutation(before, changed)
    if damage != 'unsupported-version':
        base = parse_compiler_debug(changed)
        assert base.reconstruct() == changed
        with pytest.raises(GXWFormatError):
            parse_compiler_debug_search_maps(base)
        assert base.opaque_tail == changed[base.tail_offset:]


def _native_st_debug():
    fixture = load_frozen_native_json((private_path('tests/fixtures/gxw_st_source_points_native.json')).read_text(encoding="utf-8"))
    raw = base64.b64decode(fixture["debug_base64"])
    return fixture, raw, parse_compiler_debug(raw)


def test_st_source_points_match_native_reads_prefix_counts_and_saved_contexts():
    fixture, raw, debug = _native_st_debug()
    points = compiler_st_source_points(debug)
    assert len(points) == 45
    actual = [dict(element_index=debug.elements.index(p.element), row_index=p.row_index,
                   source_line=p.source_line, compiled_step_start=p.compiled_step_start,
                   context_names=[name.decode("ascii") for name in p.context_names],
                   resource=p.element.resource_bytes.decode("ascii")) for p in points]
    assert actual == fixture["expected"]
    assert debug.reconstruct() == raw
    assert all(p.row == p.table.rows[p.row_index] for p in points)


def test_st_same_type_instances_and_overlapping_caller_points_remain_separate():
    _, _, debug = _native_st_debug()
    points = compiler_st_source_points(debug)
    first = [p for p in points if p.context_names[4] == b"Keypad_1"]
    second = [p for p in points if p.context_names[4] == b"Keypad_2"]
    assert len(first) == len(second) == 18
    assert {p.definition_name_bytes for p in first + second} == {b"Keypad"}
    assert [p.source_line for p in first] == [p.source_line for p in second]
    assert first[0].compiled_step_start == 31
    assert second[0].compiled_step_start == 116
    # Caller points include an expansion while its own points remain distinct.
    caller = next(p for p in points if not p.context_names[4] and p.source_line == 8)
    assert caller.compiled_step_start == first[0].compiled_step_start
    # A stored endpoint reaches the next expansion; it is not statement ownership.
    assert first[-1].source_line == 31
    assert first[-1].compiled_step_end == second[0].compiled_step_start


@pytest.mark.parametrize("change", ["table-language", "negative-line", "negative-step", "reversed-steps", "network-row", "row-kind"])
def test_unsupported_st_coordinate_views_preserve_the_debug_bytes(change):
    _, raw, debug = _native_st_debug()
    damaged = bytearray(raw)
    table = debug.offset_tables[0]
    row_offset = table.offset + 8
    if change == "table-language":
        offset, value = table.offset, 1
    else:
        field, value = {"negative-line": (0, -2), "negative-step": (1, -1),
                        "reversed-steps": (2, -1), "network-row": (3, 1),
                        "row-kind": (4, 14)}[change]
        offset = row_offset + field * 4
    struct.pack_into("<i", damaged, offset, value)
    reparsed = parse_compiler_debug(bytes(damaged))
    assert reparsed.reconstruct() == bytes(damaged)
    with pytest.raises(GXWFormatError):
        compiler_st_source_points(reparsed)
    assert reparsed.reconstruct() == bytes(damaged)


def test_st_coordinates_keep_uninterpreted_row_fields():
    _, raw, debug = _native_st_debug()
    changed = bytearray(raw)
    struct.pack_into("<i", changed, debug.offset_tables[0].offset + 8 + 20, 12345)
    reparsed = parse_compiler_debug(bytes(changed))
    point = compiler_st_source_points(reparsed)[0]
    assert point.row[5] == 12345
    assert point.source_line == point.compiled_step_start == 0
    assert reparsed.reconstruct() == bytes(changed)


def test_compiler_component_fields_match_native_reads_and_global_indirection(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    compare = importlib.import_module("native_gxw_compiler").compare_component_reads
    with zipfile.ZipFile(COMPONENT_ARCHIVE) as z:
        count = 0
        for i in range(12):
            prefix = f"native-component-corpus/{i}/"
            raw = z.read(prefix + "CGTable.dat")
            rows = [load_frozen_native_json(s) for s in z.read(prefix + "native-components.jsonl").splitlines()]
            result = compare(raw, rows)
            assert result["component_reads_complete"] and not result["component_field_mismatches"]
            assert z.read(prefix + "native-replay.bin") == raw
            count += result["component_count"]
        assert count == 1345
        raw = z.read("external-type-control/CGTable.dat")
        rows = [load_frozen_native_json(s) for s in z.read("external-type-control/native-components.jsonl").splitlines()]
        tables = parse_compiler_tables(raw)
        component = tables.component_at(0)
        assert component.global_offset == 0 and component.type_code is None
        assert tables.component_type(component) == 2
        assert compare(raw, rows)["component_field_mismatches"] == []
        assert not compare(raw, rows[:-1])["component_reads_complete"]
        user = bytearray(base64.b64decode(rows[0]["user_info_base64"]))
        user[0] ^= 1
        rows[0]["user_info_base64"] = base64.b64encode(user).decode()
        assert compare(raw, rows)["component_field_mismatches"] == [{"offset": 0, "field": "user_info"}]


def test_global_fb_instances_match_native_references_and_keep_distinct_paths(monkeypatch):
    from gxw.compiler_symbols import compiler_symbol_paths
    from gxw.lossless import sha256
    monkeypatch.syspath_prepend(str(private_path('research')))
    oracle = importlib.import_module("native_gxw_compiler")
    fixture = load_frozen_native_json((private_path('tests/fixtures/gxw_global_instances.json')).read_text(encoding="utf-8"))
    artifacts = {name: base64.b64decode(value["base64"]) for name, value in fixture["artifacts"].items()}
    for name, raw in artifacts.items():
        assert len(raw) == fixture["artifacts"][name]["bytes"]
        assert sha256(raw) == fixture["artifacts"][name]["sha256"]
    raw = artifacts["CGTable.dat"]
    assert raw == artifacts["native-replay.bin"]
    tables = parse_compiler_tables(raw)
    assert tables.reconstruct() == raw
    rows = [load_frozen_native_json(line) for line in artifacts["native-components.jsonl"].splitlines()]
    assert not oracle.compare_component_reads(raw, rows)["component_field_mismatches"]
    comparison = oracle.compare_table_dump(raw, artifacts["native-table-dump.txt"])
    assert comparison["pous_equal"] and comparison["components_equal"] and comparison["instance_refs_equal"]
    assert (comparison["components"], comparison["instances"]) == (76, 2)
    globals_pou = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                       if tables.pou_at(r.table_offset).name_bytes == b"@GLOBALS")
    references = {c.name_bytes: tables.component_instance_pou_offset(c) for c in tables.components(globals_pou)}
    assert references == {b"GlobalInstance": 0, b"GlobalOther": 65}
    native = {row["requested_offset"]: base64.b64decode(row["record_base64"]) for row in rows}
    for component in tables.components(globals_pou):
        assert component.type_code is None and component.global_offset is not None
        assert tables.component_instance_pou_offset(component) == struct.unpack_from("<I", native[component.record.table_offset], 0x111)[0]
    paths = compiler_symbol_paths(tables, globals_pou.record.table_offset)
    assert len(paths) == 76
    members = {p.names[1]: p for p in paths if p.names[-1] == b"rPrm"}
    assert set(members) == {b"GlobalInstance", b"GlobalOther"}
    assert members[b"GlobalInstance"].symbol.pou_offset == 0
    assert members[b"GlobalOther"].symbol.pou_offset == 65
    assert members[b"GlobalInstance"].symbol.assignment.iec_address != members[b"GlobalOther"].symbol.assignment.iec_address


@pytest.mark.parametrize("reference,error", [(1, "record boundary"), (130, "cycle")])
def test_global_fb_reference_corruption_remains_visible(reference, error):
    from gxw.compiler_symbols import compiler_symbol_paths
    fixture = load_frozen_native_json((private_path('tests/fixtures/gxw_global_instances.json')).read_text(encoding="utf-8"))
    raw = bytearray(base64.b64decode(fixture["artifacts"]["CGTable.dat"]["base64"]))
    tables = parse_compiler_tables(raw)
    global_record = tables.tables[3].records[0]
    name, _ = global_record.named_fields()
    struct.pack_into("<I", raw, global_record.offset + 5 + len(name) + 8, reference)
    changed = parse_compiler_tables(raw)
    assert changed.reconstruct() == raw
    with pytest.raises(GXWFormatError, match=error):
        compiler_symbol_paths(changed, 130)


def test_compiler_assignment_native_text_does_not_turn_zero_into_x0():
    from gxw.compiler_assignment import parse_compiler_assignment
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        rows = [load_frozen_native_json(s) for s in z.read("native-assignment-corpus/outputs.jsonl").splitlines()]
    decoded = unassigned = 0
    for row in rows:
        raw = base64.b64decode(row["input_base64"])
        actual = parse_compiler_assignment(raw)
        assert actual.raw == base64.b64decode(row["after_base64"]) == raw
        native = base64.b64decode(row["output_base64"]).decode("ascii")
        if actual.status == "unassigned":
            assert not any(raw) and native == "%IX0"
            assert actual.fx_operand is None and actual.iec_address is None
            unassigned += 1
        else:
            assert actual.status == "decoded" and actual.iec_address == native
            decoded += 1
    assert (decoded, unassigned) == (1205, 140)
    unknown = bytearray.fromhex("2405e603000002000000") + bytearray(16)
    unknown[-1] = 1
    assert parse_compiler_assignment(unknown).status == "opaque"
    with pytest.raises(GXWFormatError):
        parse_compiler_assignment(bytes(25))


def test_inline_assignments_match_native_operands_without_inventing_allocations():
    from gxw.compiler_assignment import parse_compiler_assignment
    fixture = load_frozen_native_json((private_path('tests/fixtures/gxw_inline_assignments_native.json')).read_text(encoding="utf-8"))
    original_constants = original_devices = 0
    for row in fixture["cases"]:
        raw = base64.b64decode(row["input_base64"])
        actual = parse_compiler_assignment(raw)
        assert actual.raw == raw
        native_operand = base64.b64decode(row["operand_base64"]).decode("ascii")
        native_address = base64.b64decode(row["output_base64"]).decode("ascii")
        after = base64.b64decode(row["after_base64"])
        # Native uppercases its private copy; parsing preserves original bytes.
        assert after[:21] == raw[:21] and after[22:] == raw[22:]
        assert chr(after[21]) == chr(raw[21]).upper()
        if actual.status == "opaque":
            assert row["origin"] == "prospective-number-controls"
            assert actual.operand is None and actual.iec_address is None
            continue
        assert actual.status == "decoded" and actual.operand == native_operand
        assert actual.iec_address == (native_address or None)
        assert actual.reserved_count is None
        if actual.constant_value is not None:
            assert actual.role == "constant"
            assert actual.device_family is None and actual.number is None
            assert actual.fx_operand is None and not native_address
            original_constants += row["origin"] == "original-stored-cache"
        else:
            assert actual.role == "device_reference"
            if actual.device_family == "W":
                assert actual.fx_operand is None
                assert actual.operand == f"W{actual.number:X}"
            else:
                assert actual.fx_operand == native_operand
            original_devices += row["origin"] == "original-stored-cache"
    assert (original_constants, original_devices) == (34, 31)


@pytest.mark.parametrize("offset", [1, 8, 13, 20, 22, 25])
def test_inline_assignment_unknown_modifiers_stay_opaque(offset):
    from gxw.compiler_assignment import parse_compiler_assignment
    raw = bytearray.fromhex("070000000000000000f91c000000000000000000004d00000000")
    raw[offset] = 1
    actual = parse_compiler_assignment(raw)
    assert actual.raw == raw and actual.status == "opaque"
    assert actual.operand is None and actual.iec_address is None
    assert actual.constant_value is None


@pytest.mark.parametrize("case", ARRAY_REFERENCE_CASES, ids=lambda row: row["case"])
def test_array_member_addresses_match_saved_native_compiler_references(case):
    from gxw.compiler_symbols import compiler_array_reference
    tables = _native_reference_graph(case)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    for row in case["references"]:
        actual = compiler_array_reference(tables, row["component_offset"], tuple(row["indices"]),
            member_offset=row["member_offset"], member_indices=tuple(row["member_indices"]))
        assert actual.operand == row["expected_operand"]
        assert actual.iec_address == row["expected_iec_address"]
        assert actual.type_pou_offset == row["expected_type_pou_offset"]
        assert actual.primitive_type == row["expected_primitive_type"]
        assert actual.primitive_width == row["expected_primitive_width"]
        assert actual.family_stride == row["expected_family_stride"]
        assert actual.base_assignment.raw == (actual.member or actual.component).user_info
        assert actual.descriptor.record.raw == tables.tables[13].record_at(actual.descriptor.record.table_offset).raw
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


@pytest.mark.parametrize("invalid", ["below-lower", "above-upper", "wrong-rank", "non-integer", "scalar-member-index"])
def test_array_member_reference_rejects_invalid_coordinates_without_rewriting_cache(invalid):
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/Member-endpoints"))
    tables = _native_reference_graph(case)
    row = case["references"][0]
    descriptor = tables.array_at(tables.component_array_offset(tables.component_at(row["component_offset"])))
    indices = tuple(row["indices"])
    member_indices = ()
    if invalid == "below-lower":
        indices = (descriptor.dimensions[0].lower - 1,)
    elif invalid == "above-upper":
        indices = (descriptor.dimensions[0].upper + 1,)
    elif invalid == "wrong-rank":
        indices += (0,)
    elif invalid == "non-integer":
        indices = (True,)
    else:
        member_indices = (0,)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    with pytest.raises(GXWFormatError):
        compiler_array_reference(tables, row["component_offset"], indices,
                                 member_offset=row["member_offset"], member_indices=member_indices)
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


def test_array_member_reference_preserves_same_named_type_instance_identity():
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/Second-alarm"))
    tables = _native_reference_graph(case)
    first, second = case["references"][:2]
    assert first["expected_type_pou_offset"] != second["expected_type_pou_offset"]
    assert tables.pou_at(first["expected_type_pou_offset"]).name_bytes == tables.pou_at(second["expected_type_pou_offset"]).name_bytes
    with pytest.raises(GXWFormatError, match="does not belong"):
        compiler_array_reference(tables, first["component_offset"], tuple(first["indices"]),
                                 member_offset=second["member_offset"])


@pytest.mark.parametrize("parameter", [14, 32768])
def test_string_array_reference_refuses_inconsistent_or_unmeasured_extent(parameter):
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/outer12-inner5-conditional"))
    case = load_frozen_native_json(json.dumps(case))
    row = next(r for r in case["references"] if r["text"] == "probeArray[0]")
    tables = _native_reference_graph(case)
    offset = tables.component_array_offset(tables.component_at(row["component_offset"]))
    descriptor = next(r for r in case["tables"][13]["records"] if r["table_offset"] == offset)
    raw = bytearray(base64.b64decode(descriptor["raw_base64"]))
    struct.pack_into("<I", raw, 8, parameter)
    descriptor["raw_base64"] = base64.b64encode(raw).decode()
    tables = _native_reference_graph(case)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    with pytest.raises(GXWFormatError):
        compiler_array_reference(tables, row["component_offset"], tuple(row["indices"]))
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


def test_compiler_address_and_array_values_match_native_objects(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    oracle = importlib.import_module("native_gxw_compiler")
    addresses = arrays = references = 0
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        for i in range(12):
            prefix = f"native-array-corpus/{i}/"
            raw = z.read(prefix + "CGTable.dat")
            assert raw == z.read(prefix + "native-replay.bin")
            tables = parse_compiler_tables(raw)
            for kind, compare in (("addresses", oracle.compare_address_reads), ("arrays", oracle.compare_array_reads)):
                rows = [load_frozen_native_json(s) for s in z.read(prefix + f"native-{kind}.jsonl").splitlines()]
                result = compare(raw, rows)
                assert result["address_reads_complete" if kind == "addresses" else "array_reads_complete"]
                assert not result["address_field_mismatches" if kind == "addresses" else "array_field_mismatches"]
                if kind == "addresses":
                    addresses += len(rows)
                else:
                    arrays += len(rows)
            for record in tables.tables[2].records:
                component = tables.component_at(record.table_offset)
                offset = tables.component_array_offset(component)
                if offset is not None:
                    array = tables.array_at(offset)
                    assert array.element_type == 2 and array.total_count == 4
                    assert array.dimensions[0].lower == 0 and array.dimensions[0].upper == 3
                    assert array.count_matches_dimensions
                    references += 1
            for record in tables.tables[4].records:
                address = tables.address_at(record.table_offset)
                if not address.name_bytes:
                    assert address.iec_address_bytes is None
        assert (addresses, arrays, references) == (23, 16, 16)


def test_compiler_array_native_acceptance_preserves_inconsistent_counts(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    compare = importlib.import_module("native_gxw_compiler").compare_array_reads
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        prefix = "native-array-controls/"
        raw = z.read(prefix + "CGTable.dat")
        rows = [load_frozen_native_json(s) for s in z.read(prefix + "native-arrays.jsonl").splitlines()]
        controls = load_frozen_native_json(z.read(prefix + "controls.json"))["controls"]
        assert raw == z.read(prefix + "native-replay.bin")
        assert compare(raw, rows) == {"array_reads_complete": True, "array_count": 15, "array_field_mismatches": []}
    tables = parse_compiler_tables(raw)
    cases = {c["name"]: tables.array_at(c["offset"]) for c in controls}
    assert len(cases) == 13
    assert all(a.type_padding == b"\xde\xad\xbe" for a in cases.values())
    assert [(d.lower, d.upper) for d in cases["mixed-two-dimensions"].dimensions] == [(-5, -2), (10, 12)]
    assert cases["numeric-kind-3"].dimensions[0].lower == -70000
    assert cases["numeric-kind-6"].total_count == 4000000000
    assert cases["inconsistent-total"].total_count == 99
    assert cases["inconsistent-total"].dimensions[0].extent == 4
    assert not cases["inconsistent-total"].count_matches_dimensions
    assert not cases["negative-extent"].count_matches_dimensions
    assert cases["negative-extent"].dimensions[0].upper is None
    assert not cases["no-dimensions"].count_matches_dimensions
    for name in ("element-parameter-15", "element-parameter-52", "element-parameter-53"):
        assert cases[name].element_parameter == 0x12345678
    mutated = bytearray(raw)
    first = tables.tables[13].records[0]
    mutated[first.offset + 4 + 5] = 15
    framed = parse_compiler_tables(mutated)
    assert framed.reconstruct() == mutated
    with pytest.raises(GXWFormatError, match="numeric encoding"):
        framed.array_at(first.table_offset)


def test_compiler_symbol_paths_and_timer_aliases_survive_instruction_links():
    from gxw.compiler_symbols import compiler_symbols, compiler_symbol_paths, compiler_operand_occurrences
    from gxw.token_listing import decode_token_program
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        scan = load_frozen_native_json(z.read("symbol-array-scan/compiler-storage.json"))
    match = next(x for x in scan["symbol_cross_references"] if x["storage_sha256"] ==
                 "73912bb8b92bf6fea01c3ee00d3cde8bac7d436a06384a91cd66d5951b45ce81")
    raw = base64.b64decode(scan["compiler_artifacts"][match["table_sha256"]]["raw_base64"])
    tables = parse_compiler_tables(raw)
    root = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                if tables.pou_at(r.table_offset).name_bytes == b"1")
    paths = compiler_symbol_paths(tables, root.record.table_offset)
    path_names = {p.symbol.component.record.table_offset: b".".join(p.names) for p in paths}
    body = base64.b64decode(scan["fragments"][match["code_sha256"]]["body_base64"])
    listing = decode_token_program(parse_token_fragment(body, 0, len(body)))
    occurrences = compiler_operand_occurrences(compiler_symbols(tables), listing)
    assert len(occurrences) == 43 and sum(bool(x.component_offsets) for x in occurrences) == 28
    coil = next(x for x in occurrences if x.step == 78 and x.text == "T199")
    assert {path_names[offset] for offset in coil.component_offsets} == {
        b"1.TON_A.T_FB.Coil", b"1.TON_A.T_FB.ValueIn", b"1.TON_A.T_FB.ValueOut", b"1.TON_A.T_FB.Status"}
    with pytest.raises(GXWFormatError, match="expansion exceeds limit"):
        compiler_symbol_paths(tables, root.record.table_offset, limit=1)
    # A corrupt self-reference must fail instead of expanding indefinitely.
    child = next(c for c in tables.components(root) if c.instance_pou_offset is not None)
    field = child.record.offset + 4 + 1 + len(child.name_bytes) + 12
    cyclic = bytearray(raw)
    struct.pack_into("<I", cyclic, field, root.record.table_offset)
    with pytest.raises(GXWFormatError, match="cycle"):
        compiler_symbol_paths(parse_compiler_tables(cyclic), root.record.table_offset)


def test_source_declarations_expose_cached_fb_type_until_native_recompile():
    from dataclasses import replace
    from gxw.compiler_symbols import compiler_declaration_bindings
    from gxw.declarations import parse_declarations
    from gxw.lossless import inspect_project, sha256

    def bind(project):
        image = inspect_project(project)
        source = next(s for s in image.streams if s.logical_name == "1.Labels.lh")
        cache = next(s for s in image.streams if s.logical_name == "CGTable.dat")
        doc = parse_declarations(source.raw, logical_name=source.logical_name)
        tables = parse_compiler_tables(cache.raw)
        return tables, doc, compiler_declaration_bindings(tables, doc)

    # Existing native GUI compile evidence: TOF source declaration with the
    # old TON compiler cache; successful native compile produces a TOF cache.
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-20260910.zip')) as z:
        before, after = z.read("t2.gxw"), z.read("t2c.gxw")
    assert sha256(before) == "ff7ed97c1275913beca2764e0429d33cb25013283bf34cd81da34ce9b9cd8e24"
    assert sha256(after) == "2b3c0b6bb2565be28aa2cf1826ff251dc1ed0105d227dc84a5606952b106afc9"
    tables, doc, old = bind(before)
    _, _, new = bind(after)
    a = next(b for b in old if b.name == "TON_A")
    b = next(b for b in new if b.name == "TON_A")
    assert a.declared_type == b.declared_type == "TOF"
    assert a.status == "instance-type-differs" and a.candidates[0].instance_type_name == b"TON"
    assert b.status == "instance-type-agrees" and b.candidates[0].instance_type_name == b"TOF"
    assert next(b for b in new if b.name == "elapsed_a").status == "name-match"
    # Multiple compiled instances of one source FB must remain alternatives.
    ambiguous = replace(doc, owner_name="TON", rows=(replace(doc.rows[0], name="IN", type_reference=""),))
    result = compiler_declaration_bindings(tables, ambiguous)[0]
    assert result.status == "ambiguous-compiled-owner"
    assert len(result.owner_offsets) == len(result.candidates) == 2


def test_source_added_declarations_bind_only_after_archived_native_compile():
    from gxw.compiler_symbols import compiler_declaration_bindings
    from gxw.declarations import parse_declarations
    from gxw.lossless import inspect_project
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-generation-20260910.zip')) as z:
        cases = [z.read(name) for name in ("auto_decl.gxw", "auto_decl_compile.gxw")]
    states = []
    for raw in cases:
        image = inspect_project(raw)
        source = next(s for s in image.streams if s.logical_name == "1.Labels.lh")
        cache = next(s for s in image.streams if s.logical_name == "CGTable.dat")
        rows = compiler_declaration_bindings(parse_compiler_tables(cache.raw),
            parse_declarations(source.raw, logical_name=source.logical_name))
        generated = [b for b in rows if b.name.startswith("generated_")]
        assert len(generated) == 1000
        states.append({b.status for b in generated})
    assert states == [{"no-compiled-component"}, {"name-match"}]


@pytest.mark.parametrize('name', [
    *(cpu+suffix for cpu in ('FX3U-FX3UC','Q03UDV','L02') for suffix in ('-current','-duplicate-output')),
    'Q03UDV-current-compiler-stale-workspace', 'FX3G-duplicate-coil-legacy',
    'FX3G-duplicate-coil-fresh', 'SFC-empty-task-cache',
])
def test_current_code_checks_preserve_stale_reads_incomplete_diagnostics_and_empty_tasks(monkeypatch, name):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import analyze_check
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fbd-application-v2-20261002.zip')) as archive:
        case = next(row for row in load_frozen_native_json(archive.read('check-witnesses.json')) if row['case']==name)
    generated = {tuple(row['id']): tuple(base64.b64decode(b) for b in row['channels_base64'])
                 for row in case['generated']}
    result = analyze_check(case['native_events'],case['observations'],generated,selection=case['selection'])
    assert result['compilation']['completed']
    # This frozen trace predates module-version observations. Its original
    # byte comparisons remain evidence, but its caller RVAs alone cannot
    # establish the version-bound native checker path.
    assert not result['checker_resources']['scope_verified']
    assert result['checker_resources']['correspondence'] == (
        'not_observed' if name == 'SFC-empty-task-cache' else 'not_established')
    assert all(row['origin'] == 'unknown' for row in result['checker_resources']['reads'])
    if name != 'SFC-empty-task-cache':
        assert {row['status'] for row in result['checker_resources']['reads']} == {case['expected']['correspondence']}
    assert result['public_check']['status'] == case['expected']['public_check']
    assert result['diagnostic_projection']['status'] == case['expected']['projection']
    assert result['current_source_check'] == 'not_established'
    if name.endswith('-duplicate-output'):
        assert result['targets']['backend_completed'] == result['targets']['planned']
        assert any(d['kind']==2 for d in result['diagnostic_projection']['backend_diagnostics'])
        assert not result['diagnostic_projection']['public_diagnostics']
        assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
        assert not result['public_check']['completed']
    if 'stale' in name or name.endswith('-legacy'):
        assert result['public_check']['status']=='passed'
        assert any(r['status']=='stale' for r in result['checker_resources']['reads'])
    if name=='SFC-empty-task-cache':
        assert result['compilation']['returned_resources']==1
        assert base64.b64decode(case['selection']['task_base64'])[-4:]==b'\0'*4
        assert case['selection']['empty_task'] and not case['selection']['selected_sources']


def native_resource_read_inputs(primary=b'current code', secondary=b'current secondary code'):
    # Caller/stack sites and resource identities from the Q06UDV 1.635.0.1
    # partial-publication control. Small independent bytes exercise attribution,
    # not the vendor's instruction encoding or native acceptance.
    first = [0, 3959422976, 1, 1] + [0] * 8
    second = [0, 3959422976, 1, 2] + [0] * 8
    events = [{'operation': 'OwnedBackendModule', 'name': module, 'version': '1.635.0.1'} for module in (
        'DZDataABS_Workspace.dll', 'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll')]
    events += [{'operation': 'ProgramCheckTargetOrder', 'targets': [first, second]},
        {'operation': 'ProgramCheckTarget', 'id': first},
        {'operation': 'ProgramCheckTargetSkipped', 'target': second, 'reason': 'published code differs'}]
    generated = {tuple(first): (primary, b'', b''), tuple(second): (secondary, b'', b'')}
    native = {'event': 'workspace-pcode-read', 'phase': 'check', 'serial': 7,
        'check_index': 0, 'check_target': first, 'target': first, 'hresult': 0, 'code': 0,
        'requested_sizes': [len(primary), 0, 0],
        'caller': {'module': 'DZDataABS_Converter.dll', 'rva': '0x82a34'},
        'stack': [{'module': 'DZDataABS_Converter.dll', 'rva': '0x82a34'},
            {'module': 'DZDataABS_Workspace.dll', 'rva': '0x78d08'},
            {'module': 'DZDataABS_CompilerAdapter.dll', 'rva': '0x6c0e'},
            {'module': 'clr.dll', 'rva': '0x12526'}],
        'buffers': [{'size': len(primary), 'raw_hex': primary.hex()},
                    {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}
    observations = [{'event': 'check-start', 'check_index': 0, 'target': first}, native]
    auxiliary = {**native, 'serial': 29, 'target': second,
        'caller': {'address': '0x8b6550f'},
        'stack': [{'address': '0x8b6550f'}, {'module': 'DZDataABS_Workspace.dll', 'rva': '0x78d08'},
                  {'module': 'clr.dll', 'rva': '0x12526'}],
        'requested_sizes': [14, 0, 0],
        'buffers': [{'size': 14, 'raw_hex': b'old guard code'.hex()},
                    {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}
    events.append({'operation': 'PublishedResourceCodeCorrespondence', 'target': second})
    return events, observations, generated, auxiliary


def test_native_checker_reads_keep_late_stale_publication_guard_separate():
    from gxw.native_diagnostics import analyze_native_check
    events, observations, generated, auxiliary = native_resource_read_inputs()
    query = {**auxiliary, 'serial': 28, 'requested_sizes': [0, 0, 0],
        'buffers': [{'size': 14, 'raw_hex': None}, {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}
    result = analyze_native_check(events, observations + [query, auxiliary], generated)
    reads = result['checker_resources']
    assert reads['scope_verified'] and reads['correspondence'] == 'partial_targets'
    assert [(row['origin'], row['native_check_target_bound'], row['status']) for row in reads['reads']] == [
        ('native-checker', True, 'current'), ('auxiliary-publication', False, 'stale')]
    assert reads['size_queries'] == [28]
    assert reads['size_query_reads'][0]['origin'] == 'auxiliary-publication'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['current_source_check'] == 'not_established'


@pytest.mark.parametrize('damage', ['missing_version', 'conflicting_version', 'missing_caller',
    'wrong_converter', 'unknown_rva', 'missing_adapter_frame', 'wrong_adapter_rva',
    'foreign_resource', 'wrong_start', 'missing_start', 'conflicting_start', 'malformed_hex',
    'unattributed_query'])
def test_native_checker_input_requires_version_caller_and_original_target_start(damage):
    from gxw.native_diagnostics import analyze_native_check
    events, observations, generated, _ = native_resource_read_inputs()
    native = observations[-1]
    if damage == 'missing_version':
        events.pop(0)
    elif damage == 'conflicting_version':
        events.append({**events[0], 'version': 'unobserved'})
    elif damage == 'missing_caller':
        del native['caller']
    elif damage == 'wrong_converter':
        native['caller']['module'] = 'UNRELATED.dll'
    elif damage == 'unknown_rva':
        native['caller']['rva'] = '0x82a35'
    elif damage == 'missing_adapter_frame':
        native['stack'] = native['stack'][:2]
    elif damage == 'wrong_adapter_rva':
        native['stack'][2]['rva'] = '0x6c0f'
    elif damage == 'foreign_resource':
        native['target'] = events[3]['targets'][1]
    elif damage == 'wrong_start':
        observations[0]['target'] = events[3]['targets'][1]
    elif damage == 'missing_start':
        observations.pop(0)
    elif damage == 'conflicting_start':
        observations.insert(1, {**observations[0], 'target': events[3]['targets'][1]})
    elif damage == 'malformed_hex':
        native['buffers'][0]['raw_hex'] = 'invalid'
    else:
        observations.append({**native, 'serial': 8, 'caller': {}, 'requested_sizes': [0, 0, 0],
            'buffers': [{'size': 12, 'raw_hex': None}, {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]})
    result = analyze_native_check(events, observations, generated)
    assert result['checker_resources']['correspondence'] == 'not_established'
    assert result['current_source_check'] == 'not_established'


def test_auxiliary_reads_cannot_establish_checker_inputs_and_do_not_hide_native_staleness():
    from gxw.native_diagnostics import analyze_native_check
    events, observations, generated, auxiliary = native_resource_read_inputs()
    assert analyze_native_check(events, [auxiliary], generated)['checker_resources']['correspondence'] == 'not_observed'
    query = {**observations[-1], 'requested_sizes': [0, 0, 0],
        'buffers': [{'size': 12, 'raw_hex': None}, {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}
    assert analyze_native_check(events, [observations[0], query], generated)['checker_resources']['correspondence'] == 'not_established'
    observations[-1]['buffers'][0] = {'size': 3, 'raw_hex': b'old'.hex()}
    result = analyze_native_check(events, observations + [auxiliary], generated)
    assert result['checker_resources']['correspondence'] == 'stale'


def test_native_checker_attribution_is_independent_of_auxiliary_bytes_and_check_order():
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import analyze_native_check

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(st.binary(min_size=1, max_size=64), st.binary(min_size=1, max_size=64),
           st.lists(st.binary(max_size=64), max_size=8), st.booleans(), st.booleans(), st.booleans())
    def check(primary, secondary, guard_bytes, reversed_order, check_secondary, stale_native):
        events, observations, generated, auxiliary = native_resource_read_inputs(primary, secondary)
        first, second = events[3]['targets']
        if reversed_order:
            events[3]['targets'] = [second, first]
        if stale_native:
            data = b'\0' + primary
            observations[-1]['buffers'][0] = {'size': len(data), 'raw_hex': data.hex()}
        guards = [{**auxiliary, 'serial': 30 + index, 'requested_sizes': [len(data), 0, 0],
            'buffers': [{'size': len(data), 'raw_hex': data.hex()},
                        {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}
            for index, data in enumerate(guard_bytes)]
        observations += guards
        if check_secondary:
            events = [row for row in events if row.get('operation') != 'ProgramCheckTargetSkipped']
            events.append({'operation': 'ProgramCheckTarget', 'id': second})
            observations += [{'event': 'check-start', 'check_index': 1, 'target': second},
                {**observations[1], 'serial': 50, 'check_index': 1, 'check_target': second, 'target': second,
                 'requested_sizes': [len(secondary), 0, 0],
                 'buffers': [{'size': len(secondary), 'raw_hex': secondary.hex()},
                             {'size': 0, 'raw_hex': None}, {'size': 0, 'raw_hex': None}]}]
        result = analyze_native_check(events, observations, generated)
        assert result['checker_resources']['correspondence'] == (
            'stale' if stale_native else 'current' if check_secondary else 'partial_targets')
        assert [row['status'] for row in result['checker_resources']['reads']
            if row['origin'] == 'auxiliary-publication'] == [
                'current' if data == secondary else 'stale' for data in guard_bytes]
        assert not result['public_check']['completed'] and result['current_source_check'] == 'not_established'
    check()


@pytest.mark.parametrize('owned,traced,completed,errors,expected', [
    ('current', 'not_observed', True, [], True),
    ('stale', 'current', True, [], False),
    ('unresolved', 'current', True, [], False),
    ('partial_targets', 'not_observed', True, [], False),
    ('current', 'stale', True, [], False),
    ('current', 'not_observed', False, [], False),
    ('current', 'not_observed', True, ['failed independent read'], False),
])
def test_native_check_code_guard_keeps_stale_conflicting_and_incomplete_evidence(
        monkeypatch, owned, traced, completed, errors, expected):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import has_current_native_check_code
    evidence = {'owned_check_inputs': {'correspondence': owned},
        'checker_resources': {'correspondence': traced, 'observation_errors': errors},
        'raw_backend_check': {'completed': completed}}
    assert has_current_native_check_code(evidence) is expected


def native_check_input_snapshots(body, padding=b''):
    from copy import deepcopy
    # Captured ECCompiler 15.50 task header: outer=64, inner=4, mode=1.
    # Synthetic opaque padding varies framing independently of the primary
    # code. These tests concern byte binding, not native code acceptance.
    header = bytes.fromhex('40002201' + '20' * 32 +
        '0402d100030301080403000000000004071000ffffffffffffffffff0400ffff')
    assert len(header) == 68
    first = bytearray(header[:64])
    first.extend(padding)
    first[:2] = len(first).to_bytes(2, 'little')
    header = bytes(first) + header[64:]
    full = header + body
    submitted = {'operation': 'OwnedCheckInput', 'target': [1] + [0] * 11,
        'stage': 'submitted', 'mask': 0x7fffffff, 'manager': 'owned-task',
        'compiler_member_offset': 12, 'manager_records_offset': 32,
        'evidence': {'schema_version': 1, 'origin': 'owned_check_input'},
        'rows': [{'resource': 'MAIN', 'bytes': len(full), 'file': 'submitted.bin', 'tag': -1,
            'framing': {'reader_module': 'ECCompiler_IEC.dll', 'reader_version': '15.50',
                'reader_rva': 0x39f30, 'code': 0, 'mode': 1, 'body_offset': len(header),
                'body_bytes': len(body), 'first_length': len(first), 'second_length': 4,
                'evidence': {'schema_version': 1, 'origin': 'structured_check_envelope'}}}]}
    completed = deepcopy(submitted)
    completed['stage'] = 'completed'
    completed['rows'][0]['file'] = 'completed.bin'
    return submitted, completed, {'generated': {'MAIN': body},
        'snapshots': {'submitted.bin': full, 'completed.bin': full},
        'native_version': '1.635.0.1', 'checker_version': '15.50'}


def test_native_raw_resource_results_keep_skipped_targets_and_completed_rejections_separate():
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import analyze_native_check

    @settings(max_examples=80, deadline=None, derandomize=True)
    @example([('stale', b'first'), ('rejected', b'second')], False)
    @example([('unbound', b'first'), ('empty', b'second')], True)
    @given(st.lists(st.tuples(st.sampled_from(('accepted', 'rejected', 'stale', 'unbound', 'empty')),
                            st.binary(min_size=1, max_size=32)), min_size=1, max_size=5), st.booleans())
    def check(cases, reverse):
        # Independent synthetic code and target outcomes exercise aggregation,
        # retained input correspondence and ordering; they are not PLC programs.
        targets = [[0, 3959422976, 1, i + 1] + [0] * 8 for i in range(len(cases))]
        order = list(reversed(range(len(cases)))) if reverse else list(range(len(cases)))
        events = [{'operation': 'OwnedBackendModule', 'name': name, 'version': version}
                  for name, version in [('DZDataABS_CompilerAdapter.dll', '1.635.0.1'),
                      ('DZDataABS_Compiler_IEC.dll', '1.635.0.1'),
                      ('DZDataABS_SICConverter_IEC.dll', '1.635.0.1'), ('ECCompiler_IEC.dll', '15.50')]]
        events.append({'operation': 'ProgramCheckTargetOrder', 'targets': [targets[i] for i in order]})
        generated, published, snapshots, checked, skipped = {}, {}, {}, [], []
        for i in order:
            state, body = cases[i]
            target, name = targets[i], f'工程資源_{i}'
            if state != 'unbound': generated[tuple(target)] = (body, b'', b'')
            actual = b'' if state == 'empty' else b'\0' + body if state == 'stale' else body
            published[tuple(target)] = (actual, b'', b'')
            facts = {'id': target, 'read_type': {'hresult': 0, 'code': 0, 'data_type': 8},
                     'read_name': {'hresult': 0, 'code': 0, 'name': name}}
            events.extend([
                {'operation': 'NativeObject', 'id': target, 'name': name, 'name_hresult': 0, 'name_code': 0},
                {'operation': 'PublishedResourceCodeRead', 'target': target, 'hresult': 0, 'code': 0},
                {'operation': 'PublishedResourceCodeCorrespondence', 'target': target, 'resource': facts}])
            if state not in ('accepted', 'rejected'):
                skipped.append(target)
                events.append({'operation': 'ProgramCheckTargetSkipped', 'target': target, 'reason': state})
                continue
            checked.append(target)
            submitted, completed, copies = native_check_input_snapshots(body)
            snapshots.update({f'{i}-{key}': value for key, value in copies['snapshots'].items()})
            for copy in (submitted, completed):
                copy['target'] = target
                copy['rows'][0].update(resource=name, file=f'{i}-' + copy['rows'][0]['file'],
                    resource_name={'codepage': 932, 'raw_hex': name.encode('cp932').hex()})
            reports = ([{'kind': 2, 'code': 0x050c9300, 'name': {'text': name}, 'arguments': []}]
                       if state == 'rejected' else [])
            reports.append({'kind': 1, 'code': 0x23, 'name': {'text': name},
                            'arguments': [{'text': '1' if state == 'rejected' else '0'}, {'text': '0'}]})
            events.extend([
                {'operation': 'ProgramCheckTarget', 'id': target},
                {'operation': 'Compiler.ProgramCheck', 'hresult': 0, 'code': 0},
                {'operation': 'ProgramCheckReportContext', 'target': target, 'path': 'owned-native-backend'},
                submitted,
                {'operation': 'ProgramCheckRawProgress', 'poll': 1, 'percent': 100, 'count': len(reports),
                 'hresult': 0, 'code': 0},
                {'operation': 'ProgramCheckRawReports', 'poll': 1, 'percent': 100, 'reports': reports}, completed,
                {'operation': 'ProgramCheckTargetOutcome', 'target': target, 'backend_check': 'completed-' + state}])
        events.append({'operation': 'ProgramCheckPollingFinished', 'targets': len(cases)})
        result = analyze_native_check(events, [], generated, published=published, owned_snapshots=snapshots)
        assert result['targets']['started'] == checked
        assert [row['target'] for row in result['raw_backend_check']['skipped_targets']] == skipped
        assert [row['target'] for row in result['raw_backend_check']['targets']] == checked
        assert [row['status'] for row in result['raw_backend_check']['targets']] == [
            'completed-' + cases[i][0] for i in order if cases[i][0] in ('accepted', 'rejected')]
        assert result['raw_backend_check']['completed'] is (not skipped)
        assert result['raw_backend_check']['status'] == ('incomplete' if skipped else
            'completed-rejected' if any(state == 'rejected' for state, _ in cases) else 'completed-accepted')
        assert result['owned_check_inputs']['correspondence'] == (
            'not_observed' if not checked else 'partial_targets' if skipped else 'current')
        assert all(row['status'] == 'current' for row in result['owned_check_inputs']['targets'])
        assert not result['public_check']['completed']
        assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    check()


def native_validation_observation():
    """Synthetic collector transport over a frozen source, not native acceptance."""
    from copy import deepcopy
    from gxw.object_model import default_baseline
    from gxw.container_writer import validate_cfb_streams
    raw = default_baseline()
    _, _, source = native_source_chain_inputs()
    selected = deepcopy(source['selections'][0])
    selected['stage'] = 'before-build'
    selected['name'] = selected['source_object']['name'] = '1'
    selected['source_object']['owner']['read_name']['name'] = '1'
    body = source['snapshots']['LEAF-body.bin']
    submitted, completed, code = native_check_input_snapshots(b'current compiled code')
    snapshots = {**code['snapshots'], 'imported-hdb.bin': validate_cfb_streams(raw)['_hdb'],
                 'generated.bin': b'current compiled code', 'second.bin': b'', 'third.bin': b'',
                 'published.bin': b'current compiled code'}
    target = submitted['target']
    def facts(identity, kind, name):
        return {'id': identity, 'read_type': {'hresult': 0, 'code': 0, 'data_type': kind},
                'read_name': {'hresult': 0, 'code': 0, 'name': name}}
    resource = facts(target, 8, 'MAIN')
    events = [selected, {'operation': 'NativeTaskSelection', 'resource': resource,
        'tasks': [{'task': facts([30] + [0] * 11, 10, 'TASK'),
                   'programs': [facts([31] + [0] * 11, 12, '1')]}],
        'evidence': {'schema_version': 1, 'origin': 'task_selection'}}]
    for stage in ('before-build', 'after-build', 'after-check'):
        filename = stage + '.bin'; snapshots[filename] = body
        if stage != 'before-build':
            events.append({**deepcopy(selected), 'stage': stage})
        events.append({**source['reads'][0], 'file': filename, 'stage': stage})
    events.extend({'operation': 'OwnedBackendModule', 'name': name, 'version': '1.635.0.1'}
        for name in ('DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll', 'DZDataABS_SICConverter_IEC.dll'))
    diagnostic = {'kind': 2, 'code': 0x050c9300, 'step': 28, 'library': {'text': ''},
                  'name': {'text': 'MAIN'}, 'arguments': [{'text': 'Y0'}]}
    events.extend([
        {'operation': 'OwnedBackendModule', 'name': 'ECCompiler_IEC.dll', 'version': '15.50'},
        {'operation': 'Compiler.Build', 'hresult': 0, 'code': 0},
        {'operation': 'Progress', 'poll': 1, 'hresult': 0, 'code': 0, 'percent': 100, 'count': 0},
        {'operation': 'CompileRawReports', 'poll': 1, 'percent': 100, 'reports': []},
        {'operation': 'Resource', 'name': 'MAIN', 'index': 0, 'channels': [
            {'channel': i, 'bytes': len(snapshots[name]), 'file': name}
            for i, name in enumerate(('generated.bin', 'second.bin', 'third.bin'))]},
        {'operation': 'Workspace.UpdatePCodeBeforeProgramCheck', 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckTargetOrder', 'targets': [target]},
        {'operation': 'NativeObject', 'id': target, 'name': 'MAIN', 'name_hresult': 0, 'name_code': 0},
        {'operation': 'PublishedResourceCodeRead', 'target': target, 'name': 'MAIN',
         'hresult': 0, 'code': 0, 'sizes': [len(snapshots['published.bin']), 0, 0]},
        {'operation': 'PublishedResourceCodeCorrespondence', 'target': target, 'resource': resource, 'status': 'current',
         'channels': [{'channel': i, 'published_size': len(snapshots[name]), 'published_snapshot': name}
                      for i, name in enumerate(('published.bin', 'second.bin', 'third.bin'))]},
        {'operation': 'ProgramCheckTarget', 'id': target},
        {'operation': 'Compiler.ProgramCheck', 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckReportContext', 'target': target, 'path': 'owned-native-backend'}, submitted,
        {'operation': 'ProgramCheckRawProgress', 'poll': 1, 'percent': 100, 'count': 2, 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckRawReports', 'poll': 1, 'percent': 100, 'reports': [diagnostic,
            {'kind': 1, 'code': 0x23, 'name': {'text': 'MAIN'}, 'arguments': [{'text': '1'}, {'text': '0'}]}]},
        completed,
        {'operation': 'NativeDiagnosticLocation', 'target_resource': 'MAIN',
         'original': {'poll': 1, 'report_index': 0, 'kind': 2, 'code': 0x050c9300, 'resource': 'MAIN', 'step': 28},
         'source_location': {'resource': 'MAIN', 'code_step': 28, 'hresult': 0, 'code': 0,
             'location': {'library': '', 'pou': '1', 'program_kind': 208, 'network': 1, 'start_step': 1},
             'source_object': selected['source_object']}},
        {'operation': 'ProgramCheckTargetOutcome', 'target': target, 'backend_check': 'completed-rejected'},
        {'operation': 'ProgramCheckPollingFinished', 'targets': 1}])
    # Retain the collector's actual ordering around compilation and checking.
    for stage, before_operation in [('after-build', 'Resource'), ('after-check', 'ProgramCheckPollingFinished')]:
        read = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == stage)
        selection = next(row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == stage)
        events.remove(read); events.remove(selection)
        index = next(i for i, row in enumerate(events) if row.get('operation') == before_operation)
        events[index:index] = [selection, read]
    return raw, {'protocol_version': 1, 'status': 'observed', 'native_version': '1.635.0.1', 'cpu': 'FX3U/FX3UC',
                 'validation': {'project': source['project_id'], 'events': events}}, snapshots


def native_public_validation_observation():
    """Synthetic public transport over the existing frozen source; no native acceptance."""
    raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    target = next(row['id'] for row in events if row.get('operation') == 'ProgramCheckTarget')
    for event in events:
        operation = event.get('operation')
        if operation == 'ProgramCheckReportContext':
            event['path'] = 'original-public-get-progress'
        elif operation == 'ProgramCheckRawProgress':
            event.update(operation='ProgramCheckOriginalProgress', target=target,
                         report_interface='public-compiler', record_size=100)
        elif operation == 'ProgramCheckRawReports':
            event.update(operation='ProgramCheckOriginalReports', target=target,
                         report_interface='public-compiler', record_size=100)
            for index, report in enumerate(event['reports']):
                report.pop('library', None)
                report.update(instance_kind=6, instance={'text': 'MAIN'},
                    program_kind=1 if report['kind'] in (2, 3) else 0,
                    source_object_id=[999] + [0] * 11, poll=event['poll'], report_index=index,
                    report_interface='public-compiler', record_size=100)
        elif operation == 'ProgramCheckTargetOutcome':
            event['public_check'] = event.pop('backend_check')
    return raw, observation, snapshots


@pytest.mark.parametrize('damage, public_complete, input_status, source_status, body_binding, current', [
    (None, True, 'current', 'completed', 'conflicted', True),
    ('layout', False, 'current', 'partial_or_unresolved', 'partial_or_unresolved', False),
    ('count', False, 'current', 'partial_or_unresolved', 'conflicted', False),
    ('end-count', False, 'current', 'partial_or_unresolved', 'conflicted', False),
    ('missing-reports', False, 'current', 'not_requested', 'not_observed', False),
    ('unknown-context', False, 'not_observed', 'not_requested', 'not_observed', False),
    ('copy-before-terminal', True, 'unresolved', 'completed', 'conflicted', False),
    ('submission-after-poll', True, 'unresolved', 'completed', 'conflicted', False),
    ('stale-input', True, 'stale', 'completed', 'conflicted', False),
    ('query-before-check', True, 'current', 'partial_or_unresolved', 'partial_or_unresolved', True),
    ('wrong-target', False, 'current', 'partial_or_unresolved', 'conflicted', False),
])
def test_native_public_validation_keeps_completion_current_code_and_source_identity_separate(
        damage, public_complete, input_status, source_status, body_binding, current):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_public_validation_observation()
    events = observation['validation']['events']
    event = lambda operation: next(row for row in events if row.get('operation') == operation)
    if damage == 'layout':
        event('ProgramCheckOriginalReports')['reports'][0]['record_size'] = 68
    elif damage == 'count':
        event('ProgramCheckOriginalProgress')['count'] += 1
    elif damage == 'end-count':
        event('ProgramCheckOriginalReports')['reports'][-1]['arguments'][0]['text'] = '2'
    elif damage == 'missing-reports':
        events.remove(event('ProgramCheckOriginalReports'))
    elif damage == 'unknown-context':
        event('ProgramCheckReportContext')['path'] = 'unobserved'
    elif damage == 'copy-before-terminal':
        copy = next(row for row in events if row.get('operation') == 'OwnedCheckInput' and row['stage'] == 'completed')
        events.remove(copy)
        events.insert(events.index(event('ProgramCheckOriginalProgress')), copy)
    elif damage == 'submission-after-poll':
        copy = next(row for row in events if row.get('operation') == 'OwnedCheckInput' and row['stage'] == 'submitted')
        events.remove(copy)
        events.insert(events.index(event('ProgramCheckOriginalProgress')) + 1, copy)
    elif damage == 'stale-input':
        for name in ('submitted.bin', 'completed.bin'):
            original = snapshots[name]
            snapshots[name] = original[:-1] + bytes([original[-1] ^ 1])
    elif damage == 'query-before-check':
        query = event('NativeDiagnosticLocation')
        events.remove(query)
        events.insert(events.index(event('Compiler.ProgramCheck')), query)
    elif damage == 'wrong-target':
        event('ProgramCheckOriginalProgress')['target'] = [998] + [0] * 11
    result = project_native_validation(raw, observation, snapshots)
    assert result['public_check'] == {'status': 'rejected' if public_complete else 'incomplete', 'completed': public_complete}
    assert result['owned_check_inputs']['correspondence'] == input_status
    assert result['current_source_check'] == ('established' if current else 'not_established')
    assert result['current_raw_source_check'] == 'not_established' and result['raw_diagnostics'] == []
    assert result['raw_backend_check']['status'] == 'not_observed'
    assert result['native_source_locations']['status'] == source_status
    projection = result['diagnostic_projection']
    assert projection['source_body_binding'] == body_binding
    assert not projection['empty_public_list_means_no_errors']
    if damage not in ('missing-reports', 'unknown-context'):
        diagnostic = projection['source_diagnostics'][0]
        assert diagnostic['original']['code'] == 0x050c9300
        assert diagnostic['report_origin']['report_interface'] == 'public-compiler'
        assert not diagnostic['source_projection']['public_check_promoted']
        if not current:
            assert diagnostic['graph_projection']['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['missing-task-selection', 'unbound-task-program', 'empty-task',
    'missing-source-phase', 'changed-global-declarations', 'missing-generated-buffer', 'missing-compile-reports',
    'wrong-end-count', 'missing-check-start', 'unstarted-target', 'conversion-failed-cached-output', 'source-read-before-check'])
def test_native_validation_keeps_failed_or_partial_stages_distinct(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['native_source_locations']['status'] == 'completed'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    events = observation['validation']['events']
    event = lambda operation: next(row for row in events if row.get('operation') == operation)
    if damage == 'missing-task-selection':
        events.remove(event('NativeTaskSelection'))
    elif damage == 'unbound-task-program':
        event('NativeTaskSelection')['tasks'][0]['programs'][0]['read_name']['name'] = 'UNSELECTED'
    elif damage == 'empty-task':
        event('NativeTaskSelection')['tasks'][0]['programs'] = []
    elif damage == 'missing-source-phase':
        del snapshots['before-build.bin']
    elif damage == 'changed-global-declarations':
        from gxw.container_writer import replace_project_stream, validate_cfb_streams
        from gxw.project_metadata import logical_mapping
        mapping = logical_mapping(validate_cfb_streams(raw)['projectdatalist.xml'])
        key = mapping['Global1.gh']
        original = validate_cfb_streams(snapshots['imported-hdb.bin'])[key]
        snapshots['imported-hdb.bin'], _ = replace_project_stream(snapshots['imported-hdb.bin'], key, original[:-1] + bytes([original[-1] ^ 1]))
    elif damage == 'missing-generated-buffer':
        del snapshots['generated.bin']
    elif damage == 'missing-compile-reports':
        events.remove(event('CompileRawReports'))
    elif damage == 'wrong-end-count':
        event('ProgramCheckRawReports')['reports'][-1]['arguments'][0]['text'] = '2'
    elif damage == 'missing-check-start':
        events.remove(event('Compiler.ProgramCheck'))
    elif damage == 'unstarted-target':
        event('ProgramCheckTargetOrder')['targets'].append([99] + [0] * 11)
    elif damage == 'source-read-before-check':
        read = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'after-check')
        events.remove(read)
        events.insert(0, read)
    else:
        event('Progress')['count'] = 1
        event('CompileRawReports')['reports'] = [{'kind': 1, 'code': 0x20}]
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'not_established'
    if damage == 'conversion-failed-cached-output':
        assert result['compilation']['acceptance'] == 'rejected'
        assert result['generated_resources']


def test_native_validation_ignores_stored_match_flags_when_original_buffers_change():
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.sampled_from(['before-build.bin', 'after-build.bin', 'after-check.bin',
                           'generated.bin', 'published.bin', 'submitted.bin', 'completed.bin']),
           st.integers(min_value=0, max_value=100000))
    def check(filename, index):
        raw, observation, snapshots = native_validation_observation()
        original = snapshots[filename]
        offset = index % len(original)
        snapshots[filename] = original[:offset] + bytes([original[offset] ^ 1]) + original[offset + 1:]
        result = project_native_validation(raw, observation, snapshots)
        assert result['current_raw_source_check'] == 'not_established'
        assert not result['public_check']['completed']
    check()


def test_native_validation_missing_location_preserves_the_original_error():
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    observation['validation']['events'] = [row for row in observation['validation']['events']
        if row.get('operation') != 'NativeDiagnosticLocation']
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['native_source_locations']['status'] == 'partial_or_unresolved'
    assert result['raw_diagnostics'][0]['original']['code'] == 0x050c9300
    assert result['raw_diagnostics'][0]['source_projection']['status'] == 'unresolved'


def native_recheck_validation_case(case):
    import base64
    import zlib
    witness = next(row for row in native_library_diagnostic_witness()['current_public_raw_recheck']['cases']
                   if row['case'] == case)
    decode = lambda value: zlib.decompress(base64.b64decode(value))
    return (decode(witness['input_gxw_zlib_base64']), witness['observation'],
            {name: decode(value) for name, value in witness['snapshots_zlib_base64'].items()}, witness)


def native_source_capacity_case():
    import zlib

    witness = native_library_diagnostic_witness()['source_capacity_control']
    decode = lambda value: zlib.decompress(base64.b64decode(value))
    return (decode(witness['input_gxw_zlib_base64']), load_frozen_native_json(decode(witness['observation_zlib_base64'])),
            {name: decode(value) for name, value in witness['snapshots_zlib_base64'].items()}, witness)


def test_native_validation_keeps_every_source_in_the_actual_65_source_project():
    from gxw.native_diagnostics import native_validation_source_plan, project_native_validation

    raw, observation, snapshots, witness = native_source_capacity_case()
    plan = native_validation_source_plan(raw)
    assert (plan['cpu'], plan['codepage']) == ('FX3G', 1252)
    assert len(plan['programs']) == witness['local_sources'] == 2
    assert len(plan['libraries']) == witness['library_sources'] == 63
    result = project_native_validation(raw, observation, snapshots)
    assert result['public_check'] == witness['expected']['public_check'] == {'status': 'passed', 'completed': True}
    assert result['current_source_check'] == witness['expected']['current_source_check'] == 'established'
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['source_bodies']['status'] == 'current'
    assert len(result['source_bodies']['sources']) == witness['expected']['source_count'] == 65
    assert [row['name'] for row in result['generated_resources']] == witness['expected']['configured_resources']
    assert result['selection']['configuration_bytes_current'] is True
    for stage, expected in witness['stage_source_counts'].items():
        events = [row for row in observation['validation']['events'] if row.get('stage') == stage]
        assert sum(row['operation'] == 'NativeBodyRead' for row in events) == expected == 65
        assert sum(row['operation'] == 'NativeBodySelection' for row in events) == expected


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_native_validation_cannot_omit_or_use_a_stale_65th_source(damage):
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation

    raw, observation, snapshots, _ = native_source_capacity_case()
    last = {stage: [row for row in observation['validation']['events']
        if row.get('operation') == 'NativeBodyRead' and row.get('stage') == stage][-1]
        for stage in ('before-build', 'after-build', 'after-check')}

    @settings(max_examples=9, deadline=None, derandomize=True)
    @given(st.sampled_from(tuple(last)), st.integers(min_value=1, max_value=255))
    def check(stage, mask):
        changed = dict(snapshots)
        filename = last[stage]['file']
        if damage == 'missing':
            del changed[filename]
        else:
            body = changed[filename]
            changed[filename] = body[:-1] + bytes([body[-1] ^ mask])
        result = project_native_validation(raw, observation, changed)
        # The native task really completed; its result cannot establish source
        # correspondence when any selected source observation is missing/stale.
        assert result['public_check'] == {'status': 'passed', 'completed': True}
        assert result['current_source_check'] == 'not_established'
        assert result['compilation']['source_context']['status'] == 'unresolved'

    check()


def test_native_validation_source_capacity_retains_the_adapter_allocation_bound(monkeypatch):
    import gxw.native_diagnostics as diagnostics

    import gxw.diagnostic_sources as sources
    monkeypatch.setattr(sources, '_native_validation_sources', lambda raw: (
        {'cpu': 'FX3G', 'codepage': 1252}, [{'name': 'MAIN'}]))
    monkeypatch.setattr(sources, '_native_validation_library_sources', lambda raw: (
        [{}] * diagnostics.NATIVE_SOURCE_CAPACITY))
    with pytest.raises(GXWFormatError, match='selection exceeds the bound'):
        diagnostics.native_validation_source_plan(b'')
    assert diagnostics.bind_native_source_chain({}, ['MAIN'] * (diagnostics.NATIVE_SOURCE_CAPACITY + 1),
        project_id=[], native_version='', selections=[], reads=[], snapshots={})['status'] == 'unresolved'


@pytest.mark.parametrize('case', ['Q04UDV', 'FX3U-FX3UC'])
def test_native_diagnostic_recheck_preserves_failed_public_check_and_original_errors(case):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, witness = native_recheck_validation_case(case)
    result = project_native_validation(raw, observation, snapshots)
    assert result['cpu'] == witness['cpu']
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['diagnostic_projection']['status'] == 'failed'
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    assert result['current_source_check'] == 'not_established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['raw_backend_check']['completed'] is True
    assert result['raw_backend_check']['check_pass'] == 'raw-recheck'
    assert result['current_raw_source_check'] == 'established'
    assert result['diagnostic_recheck']['status'] == 'current'
    assert result['diagnostic_recheck']['previous_tasks_terminal']
    assert result['diagnostic_recheck']['same_compilation']
    assert result['diagnostic_recheck']['same_checker_input_bytes']
    actual = [{'resource': row['original']['name']['text'], 'code': row['original']['code'],
               'step': row['original']['step'], 'arguments': [arg['text'] for arg in row['original']['arguments']]}
              for row in result['raw_diagnostics']]
    assert actual == witness['expected_original_raw_errors']
    assert all(row['report_origin']['check_pass'] == 'raw-recheck' for row in result['raw_diagnostics'])
    assert result['native_source_locations']['status'] == result['native_instance_locations']['status'] == 'completed'


@pytest.mark.parametrize('damage', ['prior-input', 'publication-byte', 'recompile-between',
    'public-task-not-terminal', 'missing-raw-marker', 'missing-raw-terminal', 'unknown-pass', 'unknown-trigger'])
def test_native_diagnostic_recheck_requires_original_task_terminal_and_current_code(damage):
    from gxw.models import GXWFormatError
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, _ = native_recheck_validation_case('FX3U-FX3UC')
    events = observation['validation']['events']
    if damage in ('unknown-pass', 'unknown-trigger'):
        row = next(row for row in events if row['check_pass'] == 'public'
            and row['operation'] == 'ProgramCheckOriginalProgress' and row['code'] == 0x2d010025)
        row['check_pass' if damage == 'unknown-pass' else 'code'] = 'unbound' if damage == 'unknown-pass' else 0x2d010001
        with pytest.raises(GXWFormatError): project_native_validation(raw, observation, snapshots)
        return
    if damage == 'prior-input':
        row = next(row for row in events if row['operation'] == 'OwnedCheckInput' and row['check_pass'] == 'public')
        filename = row['rows'][0]['file']
        snapshots[filename] = snapshots[filename][:-1] + bytes([snapshots[filename][-1] ^ 1])
    elif damage == 'publication-byte':
        row = [row for row in events if row['operation'] == 'PublishedResourceCodeCorrespondence'
               and row['check_pass'] == 'raw-recheck'][-1]
        filename = row['channels'][0]['published_snapshot']
        snapshots[filename] = snapshots[filename][:-1] + bytes([snapshots[filename][-1] ^ 1])
    elif damage == 'recompile-between':
        index = next(i for i, row in enumerate(events) if row['check_pass'] == 'raw-recheck'
                     and row['operation'] == 'Compiler.ProgramCheck')
        events.insert(index, {'operation': 'Compiler.Build', 'hresult': 0, 'code': 0, 'check_pass': 'common'})
    elif damage == 'public-task-not-terminal':
        for row in events:
            if row['operation'] == 'ProgramCheckOriginalProgress' and row['code'] == 0x2d010025: row['percent'] = 17
        events[:] = [row for row in events if not (row['check_pass'] == 'public-task-tail'
            and row['operation'] == 'ProgramCheckRawProgress' and row['percent'] == 100)]
    elif damage == 'missing-raw-terminal':
        events[:] = [row for row in events if not (row['check_pass'] == 'raw-recheck'
            and row['operation'] == 'ProgramCheckRawProgress' and row['percent'] == 100)]
    else:
        row = next(row for row in events if row['check_pass'] == 'raw-recheck'
            and row['operation'] == 'ProgramCheckRawReports' and any(report['code'] == 0x23 for report in row['reports']))
        row['reports'] = [report for report in row['reports'] if report['code'] != 0x23]
    result = project_native_validation(raw, observation, snapshots)
    assert not result['public_check']['completed']
    assert result['current_source_check'] == result['current_raw_source_check'] == 'not_established'
    assert result['diagnostic_recheck']['status'] == 'incomplete'
    assert result['diagnostic_projection']['status'] == 'failed'
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']


def test_native_diagnostic_recheck_does_not_trust_claimed_acceptance_or_byte_match_flags():
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    @settings(max_examples=40, deadline=None, derandomize=True, database=None)
    @given(case=st.sampled_from(['Q04UDV', 'FX3U-FX3UC']), changed=st.integers(min_value=0, max_value=100000),
           target=st.sampled_from(['claim', 'input', 'publication']))
    def check(case, changed, target):
        raw, observation, snapshots, witness = native_recheck_validation_case(case)
        events = observation['validation']['events']
        if target == 'claim':
            for row in events:
                if row['operation'] == 'ProgramCheckTargetOutcome' and 'backend_check' in row:
                    row['backend_check'] = 'completed-accepted'
        else:
            if target == 'input':
                row = next(row for row in events if row['operation'] == 'OwnedCheckInput' and row['check_pass'] == 'raw-recheck')
                filename = row['rows'][0]['file']
                offset = row['rows'][0]['framing']['body_offset'] + changed % row['rows'][0]['framing']['body_bytes']
            else:
                row = [row for row in events if row['operation'] == 'PublishedResourceCodeCorrespondence'
                       and row['check_pass'] == 'raw-recheck'][-1]
                filename = row['channels'][0]['published_snapshot']; offset = changed % len(snapshots[filename])
            data = snapshots[filename]
            snapshots[filename] = data[:offset] + bytes([data[offset] ^ 1]) + data[offset + 1:]
        result = project_native_validation(raw, observation, snapshots)
        assert result['raw_backend_check']['status'] == 'completed-rejected'
        assert result['current_raw_source_check'] == ('established' if target == 'claim' else 'not_established')
        assert not result['public_check']['completed']
        assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
        assert len(result['raw_diagnostics']) == len(witness['expected_original_raw_errors'])
    check()


def test_native_validation_signed_all_check_mask_preserves_separate_public_status():
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    for event in observation['validation']['events']:
        if event.get('operation') == 'OwnedCheckInput': event['mask'] = -1
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['owned_check_inputs']['correspondence'] == 'current'
    assert actual['current_raw_source_check'] == 'established'
    assert actual['raw_backend_check']['status'] == 'completed-rejected'
    assert actual['public_check'] == {'status': 'incomplete', 'completed': False}
    assert actual['raw_diagnostics'][0]['original']['code'] == 0x050c9300


def native_reference_observation():
    """Synthetic reference/range transport; acceptance still comes from native runs."""
    raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    reference = {'library': '', 'source': '1', 'program_kind': 208, 'network': 1,
                 'name': 'Y0', 'resource': 'MAIN', 'task': 'TASK', 'instance': '1'}
    records = {'operation': 'NativeSourceReferences', 'hresult': 0, 'code': 0,
        'query': 'all-references', 'declared': 0, 'plural': 0, 'symbol': '', 'scope': '',
        'count': 1, 'rows': [reference],
        'evidence': {'schema_version': 1, 'origin': 'source_references'}}
    # These are ordinary informational analysis messages, not conversion failure.
    reports = [{'kind': 1, 'code': code} for code in (0x19, 0x20)]
    analysis = [
        {'operation': 'Compiler.CreateProgramAnalysis3', 'hresult': 0, 'code': 0},
        {'operation': 'NativeReferenceProgress', 'poll': 1, 'hresult': 0, 'code': 0, 'count': 2, 'percent': 100},
        {'operation': 'NativeReferenceRawReports', 'poll': 1, 'percent': 100, 'reports': reports},
        {'operation': 'Compiler.GetProgramAnalysis3', 'hresult': 0, 'code': 0, 'count': 1}, records]
    index = next(i for i, row in enumerate(events) if row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck')
    events[index:index] = analysis
    query = next(row['source_location'] for row in events if row.get('operation') == 'NativeDiagnosticLocation')
    query['location'].update(step_count=-1, element_id=-1)
    query['instance_ranges'] = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': 28,
        'candidates': [{'original_reference': reference, 'hresult': 0, 'code': 0,
            'query': {'library': '', 'pou': '1', 'program_kind': 208, 'network': 1,
                      'start_step': 1, 'step_count': -1, 'element_id': -1},
            'range': {'resource': 'MAIN', 'start_step': 28, 'step_count': 1, 'timestamp': 0}}]}
    return raw, observation, snapshots


def native_lexical_observation():
    """Synthetic prefix transport over independently recorded FX3U IL text."""
    from gxw.native_diagnostics import native_code_reader_scope
    case = load_frozen_native_json((private_path('tests/fixtures/gxw_fx3g_lexical_native.json')).read_text(encoding='utf8'))['cases'][0]
    body = bytes.fromhex(case['body_hex'])
    output = base64.b64decode(case['native']['520']['output_base64'])
    scope = native_code_reader_scope('FX3U/FX3UC')
    # This frozen MOV has a five-step header followed by K1 and R0 tokens.
    read = {**scope, 'operation': 'NativeCodeLexicalRead', 'resource': 'MAIN',
        'body_bytes': 13, 'provided_bytes': 14, 'consumed_bytes': 13,
        'input_file': 'native-input.bin', 'output_file': 'native-output.bin', 'output_bytes': len(output),
        'calls': {'object_new': True, 'open': 0, 'set_version': None, 'decode': 0, 'close': 0},
        'prefixes': [{'input_bytes': offset, 'return_code': 0, 'steps': 5} for offset in (5, 9, 13)],
        'completed': True,
        'evidence': {'schema_version': 1, 'origin': 'code_lexical_read'}}
    events = [{'operation': 'OwnedBackendModule', 'name': scope['module'], 'version': scope['version']},
              {'operation': 'ProgramCheckRawProgress', 'percent': 100, 'hresult': 0, 'code': 0}, read]
    return events, {'MAIN': (body, b'', b'')}, {'native-input.bin': body+b'\0', 'native-output.bin': output}


@pytest.mark.parametrize('damage', ['missing-prefix', 'wrong-prefix-step', 'duplicate-prefix', 'wrong-native-text',
    'missing-output', 'changed-input', 'partial-decode', 'wrong-version', 'wrong-cpu', 'boolean-code',
    'before-check', 'failed-reader', 'opaque-text', 'multiple-readers', 'incomplete-check', 'failed-check'])
@pytest.mark.parametrize('terminal', ['ProgramCheckRawProgress', 'ProgramCheckOriginalProgress'])
def test_native_lexical_binding_keeps_incomplete_or_conflicting_reads_unresolved(damage, terminal):
    from gxw.native_diagnostics import bind_native_code_lexical_read
    events, generated, snapshots = native_lexical_observation()
    events[1]['operation'] = terminal
    bind = lambda: bind_native_code_lexical_read(events, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)[0]
    actual = bind()
    assert actual['status'] == 'current'
    assert actual['records'] == [{'record_index': 0, 'native_step': 0, 'source_offset': 0,
                                 'source_end': 13, 'op': 'MOV', 'args': ['K1', 'R0']}]
    read = events[-1]
    if damage == 'missing-prefix':
        read['prefixes'].pop()
    elif damage == 'wrong-prefix-step':
        read['prefixes'][-1]['steps'] += 1
    elif damage == 'duplicate-prefix':
        read['prefixes'][-1] = dict(read['prefixes'][0])
    elif damage == 'wrong-native-text':
        snapshots['native-output.bin'] = snapshots['native-output.bin'].replace(b'R0', b'R1')
    elif damage == 'missing-output':
        del snapshots['native-output.bin']
    elif damage == 'changed-input':
        snapshots['native-input.bin'] = b'?' + snapshots['native-input.bin'][1:]
    elif damage == 'partial-decode':
        read['consumed_bytes'] -= 1
    elif damage == 'wrong-version':
        events[0]['version'] = '15.32'
    elif damage == 'wrong-cpu':
        read['native_cpu'] = 521
    elif damage == 'boolean-code':
        read['calls']['decode'] = False
    elif damage == 'before-check':
        events.remove(read); events.insert(0, read)
    elif damage == 'failed-reader':
        read['calls']['close'] = 1
    elif damage == 'opaque-text':
        snapshots['native-output.bin'] = snapshots['native-output.bin'].replace(b'R0', b'\x81\x30')
    elif damage == 'incomplete-check':
        events[1]['percent'] = 99
    elif damage == 'failed-check':
        events[1]['hresult'] = -1
    else:
        events.append(dict(read))
    actual = bind()
    assert actual['status'] == 'unresolved' and not actual['records']
    assert not actual['public_check_promoted']


@pytest.mark.parametrize('cpu,native_cpu', [('Q03UDV', 209), ('FX3U/FX3UC', 520),
                                         ('FXU/FX2C', 516), ('FX3G', 521), ('FX3G/FX3GC', None),
                                         ('Q02', None), ('L02', None), ('FX0N', None)])
def test_native_lexical_reader_scope_is_bound_to_exact_observed_cpu_menu(cpu, native_cpu):
    from gxw.native_diagnostics import native_code_reader_scope
    scope = native_code_reader_scope(cpu)
    assert (scope['native_cpu'] if scope else None) == native_cpu
    if scope:
        assert scope['scope_cpu'] == cpu


def test_native_lexical_binding_never_accepts_changed_input_or_missing_prefixes():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_code_lexical_read

    @settings(max_examples=40, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=13), st.integers(min_value=0, max_value=2))
    def check(position, prefix):
        events, generated, snapshots = native_lexical_observation()
        original = bind_native_code_lexical_read(events, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)
        assert original[0]['status'] == 'current'
        altered = bytearray(snapshots['native-input.bin']); altered[position] ^= 1
        changed = {**snapshots, 'native-input.bin': bytes(altered)}
        assert bind_native_code_lexical_read(events, generated, changed, cpu='FX3U/FX3UC', codepage=936)[0]['status'] == 'unresolved'
        partial = deepcopy(events); del partial[-1]['prefixes'][prefix]
        assert bind_native_code_lexical_read(partial, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)[0]['status'] == 'unresolved'
    check()


def fbd_output_projection_inputs():
    """Two synthetic FBs write the same address through separate current ports."""
    query = {'hresult': 0, 'code': 0, 'resource': 'MAIN', 'code_step': 1,
             'location': {'library': '', 'pou': 'FBD_MAIN', 'program_kind': 208, 'network': 1}}
    diagnostic = {'kind': 2, 'code': 0x050c9300, 'name': {'text': 'MAIN'}, 'step': 1, 'arguments': [{'text': 'Y00'}]}
    records = [{'record_index': i, 'native_step': i, 'source_offset': i*8, 'source_end': (i+1)*8,
                'op': 'LD' if i % 2 == 0 else 'OUT', 'args': [f'M{100+i//2}' if i%2 == 0 else 'Y0']}
               for i in range(4)]
    model = {'cpu': 'Q03UDV', 'schema_version': 2, 'unknown_record_count': 0,
             'program': 'FBD_MAIN.Program.pou', 'nodes': [], 'wires': []}
    references = []
    for i, symbol in enumerate(('FIRST', 'SECOND')):
        y, offset = i*4, 100+i*100
        model['nodes'].extend([
            {'id': f'n{offset}', 'source_offset': offset, 'symbol': symbol, 'template': 'function_block:TEST_FB',
             'x': 0, 'y': y, 'width': 2, 'height': 3,
             'ports': [{'name': 'RESULT', 'formal_name': 'RESULT', 'class_code': 4, 'data_type': 'BOOL',
                        'side': 'out', 'x': 2, 'y': 1, 'negated': False}]},
            {'id': f'n{offset+50}', 'source_offset': offset+50, 'symbol': 'Y0', 'template': 'output',
             'x': 2, 'y': y, 'width': 2, 'height': 2, 'ports': [{'x': 0, 'y': 1, 'negated': False}]}])
        base = {'library': '', 'source': 'FBD_MAIN', 'program_kind': 208, 'network': 1, 'resource': 'MAIN',
                'task': 'TASK', 'instance': 'FBD_MAIN', 'array_data_type': 0, 'left': 0, 'top': y, 'right': 2, 'bottom': y+3}
        references.extend([
            {**base, 'name': symbol+'.RESULT', 'address': f'M{100+i}', 'attribute': 1, 'class_code': 4, 'data_type': 1},
            {**base, 'name': symbol, 'type': 'TEST_FB', 'attribute': 1, 'class_code': 1},
            {**base, 'name': 'Y0', 'address': 'Y00', 'attribute': 2, 'class_code': 0, 'data_type': 0,
             'left': 2, 'right': 4, 'bottom': y+2}])
    return query, diagnostic, records, references, model


def test_fbd_output_projection_keeps_instance_and_port_identity_under_permutations():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import correlate_fbd_bool_output

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=1), st.permutations(range(6)), st.permutations(range(4)),
           st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ_', min_size=1, max_size=24))
    def check(selected, order, node_order, renamed):
        query, diagnostic, records, references, model = fbd_output_projection_inputs()
        step = selected*2+1; query['code_step'] = diagnostic['step'] = step
        source = model['nodes'][selected*2]; source['symbol'] = renamed
        references[selected*3]['name'] = renamed+'.RESULT'; references[selected*3+1]['name'] = renamed
        # A same-address, same-geometry reference in another instance is not this call.
        foreign = {**references[selected*3], 'task': 'OTHER_TASK', 'instance': 'FOREIGN'}
        shuffled = [references[i] for i in order] + [foreign]
        model['nodes'] = [model['nodes'][i] for i in node_order]
        project = lambda rows=shuffled: correlate_fbd_bool_output(query, diagnostic, records, rows, model,
            selected_pou='FBD_MAIN', instance=('TASK', 'FBD_MAIN'))
        actual = project()
        assert actual['status'] == 'uniquely_correlated'
        assert actual['source']['object_id'] == f'n{100+selected*100}'
        assert actual['source']['formal'] == 'RESULT' and actual['source']['side'] == 'out'
        assert actual['source']['instance'] == renamed
        assert actual['output']['object_id'] == f'n{150+selected*100}'
        assert not actual['public_check_promoted']
        # Conflicting current facts cannot be resolved by reference/node order.
        assert project(shuffled+[deepcopy(references[selected*3])])['status'] == 'unresolved'
        source['ports'][0]['class_code'] = 5
        assert project()['status'] == 'unresolved'
    check()


def test_fbd_output_projection_uses_network_membership_with_coincident_nodes_and_empty_blocks():
    from copy import deepcopy
    from dataclasses import replace
    from hypothesis import given, settings, strategies as st
    from gxw.connectivity import ConnectivityGraph, ConnectivityNet, PortRef
    from gxw.models import NodeKind, Point
    from gxw.native_diagnostics import correlate_fbd_bool_output

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=2, max_value=5), st.data())
    def check(count, data):
        selected = data.draw(st.integers(min_value=0, max_value=count-1))
        # The empty block occupies an ordinal but has no code, nodes or nets.
        order = data.draw(st.permutations(range(count+1)))
        query, diagnostic, _, original_refs, original_model = fbd_output_projection_inputs()
        model = {**original_model, 'blocks': [{} for _ in order], 'nodes': [], 'wires': []}
        records, references, nets = [], [], []
        for identity in range(count):
            block = order.index(identity)
            offset = 100+identity*200
            source, output = deepcopy(original_model['nodes'][:2])
            source.update(id=f'n{offset}', source_offset=offset, symbol=f'STAGE_{identity}', y=0, block=block)
            output.update(id=f'n{offset+50}', source_offset=offset+50, x=4, y=0, block=block)
            model['nodes'].extend([source, output])
            model['wires'].append({'source_offset': offset+75, 'block': block, 'start': [2,1], 'end': [4,1]})
            member, call, terminal = deepcopy(original_refs[:3])
            for row in (member, call, terminal):
                row.update(network=block+1, top=0)
            member.update(name=source['symbol']+'.RESULT', address=f'M{100+identity}', bottom=3)
            call.update(name=source['symbol'], bottom=3)
            terminal.update(left=4, right=6, bottom=2)
            references.extend([member, call, terminal])
            records.extend([
                {'record_index': identity*2, 'native_step': identity*2, 'source_offset': identity*16,
                 'source_end': identity*16+8, 'op': 'LD', 'args': [member['address']]},
                {'record_index': identity*2+1, 'native_step': identity*2+1, 'source_offset': identity*16+8,
                 'source_end': identity*16+16, 'op': 'OUT', 'args': ['Y0']}])
            nets.append(ConnectivityNet(identity, (
                PortRef(offset, 0, NodeKind.FUNCTION_BLOCK, source['symbol'], 0, Point(2,1)),
                PortRef(offset+50, 0, NodeKind.OUTPUT, 'Y0', 3, Point(4,1))), (offset+75,), block))
        query['location']['network'] = order.index(selected)+1
        query['code_step'] = diagnostic['step'] = selected*2+1
        model['nodes'] = list(data.draw(st.permutations(model['nodes'])))
        model['wires'] = list(data.draw(st.permutations(model['wires'])))
        references = list(data.draw(st.permutations(references)))
        connectivity = ConnectivityGraph(model['program'], tuple(data.draw(st.permutations(nets))))
        project = lambda graph=connectivity: correlate_fbd_bool_output(query, diagnostic, records, references,
            model, selected_pou='FBD_MAIN', instance=('TASK', 'FBD_MAIN'), connectivity=graph)
        actual = project()
        assert actual['status'] == 'uniquely_correlated'
        assert actual['source']['object_id'] == f'n{100+selected*200}'
        assert actual['output']['object_id'] == f'n{150+selected*200}'
        assert actual['network'] == order.index(selected)+1
        assert actual['connection']['block_index'] == order.index(selected)
        assert not actual['public_check_promoted']
        # Source, native reference and topology membership must agree. A valid
        # membership in another block is still not this diagnostic's source.
        foreign = order.index(count)
        chosen = next(node for node in model['nodes'] if node['id'] == actual['source']['object_id'])
        chosen['block'] = foreign
        assert project()['status'] == 'unresolved'
        chosen['block'] = order.index(selected)
        wire = next(w for w in model['wires'] if w['source_offset'] == 175+selected*200)
        wire['block'] = foreign
        assert project()['status'] == 'unresolved'
        wire['block'] = order.index(selected)
        damaged = replace(connectivity, nets=tuple(replace(net, block_index=foreign) if net.index == selected else net
                                                  for net in connectivity.nets))
        assert project(damaged)['status'] == 'unresolved'
        query['location']['network'] = foreign+1
        assert project()['status'] == 'unresolved'
        query['location']['network'] = order.index(selected)+1
        model['cpu'] = 'FX3U/FX3UC'
        assert project()['status'] == 'unresolved'  # Multi-network FX scope has no native control yet.
    check()


def test_native_compile_positions_keep_in_out_formal_ambiguity_and_current_writeback_direction():
    from dataclasses import replace
    from hypothesis import given, settings, strategies as st
    from gxw.connectivity import ConnectivityGraph, ConnectivityNet, PortRef
    from gxw.models import NodeKind, Point
    from gxw.native_diagnostics import correlate_native_compile_position

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=5), st.integers(min_value=0, max_value=64),
           st.integers(min_value=0, max_value=64), st.data())
    def check(network, x, y, data):
        # The native controls identify a FB by network/bbox plus formal name,
        # or identify its writeback terminal by network/bbox without that name.
        logical_name = 'FBD_MAIN.' + data.draw(st.sampled_from(['Program', '程序'])) + '.pou'
        model = {'cpu': 'Q03UDV', 'schema_version': 2, 'program': logical_name,
                 'unknown_record_count': 0, 'blocks': [{} for _ in range(network+1)], 'nodes': [], 'wires': []}
        for block in range(network+1):
            offset = 100+block*200
            model['nodes'].extend([
                {'id': f'n{offset}', 'source_offset': offset, 'block': block, 'symbol': 'STAGE_'+str(block),
                 'template': 'function_block:TEST_FB', 'x': x, 'y': y, 'width': 2, 'height': 3,
                 'ports': [{'name': side+'.STATE', 'formal_name': 'STATE', 'side': side, 'class_code': 5,
                            'data_type': 'WORD', 'x': px, 'y': 1} for side, px in [('in',0), ('out',2)]]},
                {'id': f'n{offset+50}', 'source_offset': offset+50, 'block': block, 'symbol': 'Y1',
                 'template': 'output', 'x': x+2, 'y': y, 'width': 2, 'height': 2,
                 'ports': [{'name': 'IN', 'x': 0, 'y': 1}]}])
        model['nodes'] = list(data.draw(st.permutations(model['nodes'])))
        report = {'kind': 2, 'code': 0x500c2025, 'instance_kind': 6, 'library': {'text': ''},
            'name': {'text': 'FBD_MAIN'}, 'instance': {'text': 'FBD_MAIN'}, 'program_kind': 208,
            'network': network, 'step': -1, 'left': x, 'top': y, 'right': x+2, 'bottom': y+3,
            'arguments': [{'text': 'STATE'}]}
        project = lambda r=report, graph=None: correlate_native_compile_position(r, cpu='Q03UDV', pou='FBD_MAIN',
            program_kind=208, model=model, connectivity=graph, logical_name=logical_name)
        actual = project(); graph = actual['graph_projection']
        offset = 100+(network-1)*200
        assert actual['status'] == 'source-resolved' and actual['instance_resolution'] == 'not_available_in_compile_report'
        assert graph['object']['object_id'] == f'n{offset}'
        model['program'] = 'OTHER.Program.pou'
        assert project()['status'] == 'unresolved'
        model['program'] = logical_name
        assert graph['port_association']['status'] == 'ambiguous'
        assert {(p['port_name'], p['side']) for p in graph['port_association']['candidates']} == {
            ('in.STATE','in'), ('out.STATE','out')}
        # Original report coordinates select the terminal. The current Core
        # connectivity associates it with out.STATE; no error-code side rule.
        writeback = {**report, 'code': 0x500c2017, 'left': x+2, 'right': x+4, 'bottom': y+2, 'arguments': []}
        net = ConnectivityNet(0, (
            PortRef(offset, 1, NodeKind.FUNCTION_BLOCK, 'STAGE_'+str(network-1), 0, Point(x+2,y+1)),
            PortRef(offset+50, 0, NodeKind.OUTPUT, 'Y1', 3, Point(x+2,y+1))), (), network-1)
        connectivity = ConnectivityGraph(model['program'], (net,))
        resolved = project(writeback, connectivity)
        association = resolved['graph_projection']['port_association']
        assert association['status'] == 'uniquely_correlated'
        assert association['candidates'][0]['port_name'] == 'out.STATE'
        assert not resolved['public_check_promoted']
        # Public reports carry an original native body ID. Names, network
        # and coordinates cannot substitute for that identity.
        body_id = [1, *data.draw(st.lists(st.integers(min_value=0, max_value=0xffffffff), min_size=11, max_size=11))]
        public = {**writeback, 'report_interface': 'public-compiler', 'record_size': 100,
                  'source_object_id': body_id}
        public.pop('library')  # There is no library string in the public record.
        public_project = lambda r=public, identity=body_id: correlate_native_compile_position(r, cpu='Q03UDV',
            pou='FBD_MAIN', program_kind=208, model=model, connectivity=connectivity, source_body_id=identity,
            logical_name=logical_name)
        assert public_project()['graph_projection'] == resolved['graph_projection']
        changed_id = list(body_id); index = data.draw(st.integers(min_value=0, max_value=11))
        changed_id[index] = (changed_id[index]+1) & 0xffffffff
        assert public_project({**public, 'source_object_id': changed_id})['status'] == 'unresolved'
        assert public_project(identity=changed_id)['status'] == 'unresolved'
        assert public_project(identity=None)['status'] == 'unresolved'
        assert public_project({**public, 'record_size': 68})['status'] == 'unresolved'
        damaged = replace(connectivity, nets=(replace(net, block_index=network),))
        assert project(writeback, damaged)['graph_projection']['port_association']['status'] == 'unresolved'
        wrong_endpoint = replace(net.ports[0], port_index=0, point=Point(x,y+1))
        damaged = replace(connectivity, nets=(replace(net, ports=(wrong_endpoint,net.ports[1])),))
        assert project(writeback, damaged)['graph_projection']['port_association']['status'] == 'unresolved'
        assert project({**report, 'instance_kind': 3})['status'] == 'unresolved'
        # Another current object at the same location is genuinely ambiguous.
        chosen = next(n for n in model['nodes'] if n['id'] == f'n{offset}')
        model['nodes'].append({**chosen, 'id': 'duplicate', 'source_offset': 9999})
        assert project()['graph_projection']['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('damage', ['conversion-failure', 'missing-record', 'duplicate-poll',
                                  'mixed-interfaces', 'missing-layout'])
def test_native_validation_public_compile_conversion_is_separate_from_compile_and_check(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    poll = next(row for row in events if row.get('operation') == 'Progress')
    record = next(row for row in events if row.get('operation') == 'CompileRawReports')
    poll['report_interface'] = 'public-compiler'
    record.update(operation='CompilePublicReports', report_interface='public-compiler', record_size=100)
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['acceptance'] == 'accepted'
    assert actual['compilation']['diagnostic_conversion'] == {'status': 'completed', 'failures': []}
    assert actual['compilation']['source_context']['status'] == 'current'
    assert actual['raw_backend_check']['status'] == 'completed-rejected'
    assert not actual['public_check']['completed']
    if damage == 'conversion-failure':
        # A later empty successful poll must not erase an earlier public
        # conversion error or turn it into an ordinary source rejection.
        poll['poll'] = record['poll'] = 2
        events.insert(events.index(poll), {**poll, 'poll': 1, 'hresult': -2147467259,
            'code': 0x2d010025, 'percent': 90, 'count': 1})
    elif damage == 'missing-record':
        events.remove(record)
    elif damage == 'duplicate-poll':
        events.insert(events.index(poll), deepcopy(poll))
    elif damage == 'mixed-interfaces':
        record['operation'] = 'CompileRawReports'
    else:
        del record['record_size']
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['acceptance'] == 'not_established'
    assert actual['compilation']['diagnostics_complete'] is False
    assert actual['compilation']['source_context']['status'] == 'unresolved'
    assert actual['compilation']['diagnostic_conversion']['status'] == (
        'failed' if damage == 'conversion-failure' else 'incomplete')
    assert actual['current_raw_source_check'] == 'not_established'


@pytest.mark.parametrize('damage', ['missing-before', 'missing-after', 'stale-before', 'stale-after',
                                  'read-order', 'configuration', 'partial-reports', 'body-identity'])
def test_native_compile_source_context_requires_both_original_phase_reads_and_complete_reports(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['source_context']['status'] == 'current'
    events = observation['validation']['events']
    before = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'before-build')
    after = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'after-build')
    if damage == 'missing-before':
        events.remove(before)
    elif damage == 'missing-after':
        events.remove(after)
    elif damage.startswith('stale-'):
        row = before if damage == 'stale-before' else after
        snapshots[row['file']] = snapshots[row['file']][:-1] + b'!'
    elif damage == 'read-order':
        events.remove(before); events.append(before)
    elif damage == 'configuration':
        snapshots['imported-hdb.bin'] = b'unrelated configuration'
    elif damage == 'partial-reports':
        events.remove(next(row for row in events if row.get('operation') == 'CompileRawReports'))
    else:
        selection = next(row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == 'after-build')
        identity = deepcopy(selection['source_object']); selection['source_object'] = identity
        changed = list(identity['lookup']['id']); changed[-1] += 1
        identity['lookup']['id'] = identity['body']['id'] = changed
        after['body'] = changed
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['source_context']['status'] == 'unresolved'
    assert not actual['compilation']['source_context']['public_check_promoted']


def test_native_compile_st_position_retains_source_line_without_inventing_expanded_instance():
    from gxw.native_diagnostics import correlate_native_compile_position
    report = {'kind': 2, 'code': 0x500c1200, 'instance_kind': 6, 'library': {'text': ''},
              'name': {'text': 'INNER_FB'}, 'instance': {'text': 'INNER_FB'}, 'program_kind': 193,
              'network': -1, 'step': 1, 'left': -1, 'top': 1, 'right': -1, 'bottom': -1}
    project = lambda row=report, cpu='Q03UDV': correlate_native_compile_position(row, cpu=cpu,
        pou='INNER_FB', program_kind=193, text='\nRESULT :? SIGNAL;')
    actual = project()
    assert actual['status'] == 'source-resolved'
    assert actual['source']['zero_based_line'] == 1 and actual['source']['text'] == 'RESULT :? SIGNAL;'
    assert actual['instance_resolution'] == 'not_available_in_compile_report'
    assert 'instance' not in actual and not actual['public_check_promoted']
    assert project({**report, 'step': 0})['status'] == 'unresolved'
    assert project({**report, 'top': 2, 'step': 2})['status'] == 'unresolved'
    assert project(cpu='FX3U/FX3UC')['status'] == 'unresolved'


@lru_cache(maxsize=1)
def public_compile_position_witness():
    return load_frozen_native_json((private_path('tests/fixtures/gxw_compile_positions_native.json')).read_text(encoding='utf-8'))


def native_compile_position_case():
    import zlib
    from copy import deepcopy
    case = next(row for row in public_compile_position_witness()['cases'] if row['cpu'] == 'FX3U/FX3UC')
    transport = case['transport_witness']
    decode = lambda value: zlib.decompress(base64.b64decode(value))
    return (decode(transport['input_zlib_base64']), load_frozen_native_json(decode(transport['observation_zlib_base64'])),
            {name: decode(value) for name, value in transport['snapshots_zlib_base64'].items()}, deepcopy(case))


def project_public_compile_position(case, report, *, model=None, body_id=None):
    from gxw.native_diagnostics import correlate_native_compile_position
    return correlate_native_compile_position(report, cpu=case['cpu'], pou=case['pou'], program_kind=208,
        model=case['model'] if model is None else model,
        source_body_id=case['body_id'] if body_id is None else body_id, logical_name=case['logical_name'])


@pytest.mark.parametrize('index', range(57))
def test_public_fbd_compile_positions_bind_each_frozen_cpu_and_codepage_source(index):
    import zlib
    from gxw.structured_pou import parse_structured_pou
    from gxw.native_diagnostics import PUBLIC_FBD_COMPILE_POSITION_CPUS
    witness = public_compile_position_witness()
    verified = {row['cpu'] for row in witness['menu_options'] if row['status'] == 'current-source-compile-position-boundary-observed'}
    assert PUBLIC_FBD_COMPILE_POSITION_CPUS == verified
    case = witness['cases'][index]
    source = parse_structured_pou(zlib.decompress(base64.b64decode(case['source_zlib_base64'])),
                                  logical_name=case['logical_name'])
    groups = list(source.block_records())
    assert len(groups) == 3 and not groups[1][1]
    for expected in case['errors']:
        projected = project_public_compile_position(case, expected['report'])
        assert projected['status'] == 'source-resolved'
        assert projected['source']['network'] == expected['network']
        assert projected['instance_resolution'] == 'not_available_in_compile_report'
        graph = projected['graph_projection']
        assert graph['status'] == 'node-correlated'
        assert graph['object']['symbol'] == expected['symbol']
        assert graph['object']['block_index'] == expected['network'] - 1
        assert graph['object']['bbox'] == expected['bounds']
        assert not projected['public_check_promoted'] and not graph['public_check_promoted']


def test_public_fbd_compile_positions_keep_native_identity_and_network_under_permutations():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    cases = public_compile_position_witness()['cases']

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(data=st.data())
    def check(data):
        case = data.draw(st.sampled_from(cases))
        expected = data.draw(st.sampled_from(case['errors']))
        model = deepcopy(case['model'])
        model['nodes'] = list(data.draw(st.permutations(model['nodes'])))
        model['wires'] = list(data.draw(st.permutations(model['wires'])))
        report = deepcopy(expected['report'])
        original = deepcopy(report)
        actual = project_public_compile_position(case, report, model=model)
        assert actual['graph_projection']['object']['symbol'] == expected['symbol']
        assert actual['graph_projection']['object']['block_index'] == expected['network'] - 1
        assert report == original
        identity = list(case['body_id'])
        identity[data.draw(st.integers(min_value=0, max_value=11))] ^= data.draw(st.integers(min_value=1, max_value=0xffffffff))
        assert project_public_compile_position(case, report, model=model, body_id=identity)['status'] == 'unresolved'
        empty = project_public_compile_position(case, {**report, 'network': 2}, model=model)
        assert empty['graph_projection']['status'] == 'unresolved'
        assert 'object' not in empty['graph_projection']
    check()


def test_public_fbd_compile_position_scope_does_not_promote_unverified_cpus_st_or_raw_reports():
    from gxw.native_diagnostics import correlate_native_compile_position
    witness = public_compile_position_witness()
    case = next(row for row in witness['cases'] if row['cpu'] == 'FX3U/FX3UC')
    report = case['errors'][0]['report']
    for menu in witness['menu_options']:
        if menu['status'] == 'unverified-inherited-source-boundary':
            altered = {**case, 'cpu': menu['cpu'], 'model': {**case['model'], 'cpu': menu['cpu']}}
            assert project_public_compile_position(altered, report)['status'] == 'unresolved'
    legacy = {**report, 'library': {'text': ''}}
    legacy.pop('report_interface')
    assert project_public_compile_position(case, legacy)['status'] == 'unresolved'
    st_report = {**report, 'program_kind': 193, 'network': -1, 'step': 0, 'top': 0,
                 'left': -1, 'right': -1, 'bottom': -1}
    assert correlate_native_compile_position(st_report, cpu=case['cpu'], pou=case['pou'], program_kind=193,
        text='BAD;', source_body_id=case['body_id'])['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['two-outputs', 'array-producer', 'unknown-record', 'multiple-blocks',
                                  'wrong-network', 'negated-port', 'broken-code-adjacency',
                                  'missing-producer-name', 'missing-producer-position'])
def test_fbd_output_projection_retains_ambiguity_and_unobserved_boundaries(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import correlate_fbd_bool_output
    query, diagnostic, records, references, model = fbd_output_projection_inputs()
    if damage == 'two-outputs':
        duplicate = {**deepcopy(model['nodes'][1]), 'id': 'n999', 'source_offset': 999}
        model['nodes'].append(duplicate)
    elif damage == 'array-producer':
        references[0]['array_data_type'] = 1
    elif damage == 'unknown-record':
        model['unknown_record_count'] = 1
    elif damage == 'multiple-blocks':
        model['blocks'] = [{}, {}]
    elif damage == 'wrong-network':
        query['location']['network'] = 2
    elif damage == 'negated-port':
        model['nodes'][0]['ports'][0]['negated'] = True
    elif damage == 'broken-code-adjacency':
        records[1]['source_offset'] += 1
    elif damage == 'missing-producer-name':
        del references[0]['name']
    else:
        del references[0]['left']
    actual = correlate_fbd_bool_output(query, diagnostic, records, references, model, selected_pou='FBD_MAIN',
                                       instance=('TASK', 'FBD_MAIN'))
    assert actual['status'] == 'unresolved' and not actual['public_check_promoted']
    if damage == 'two-outputs':
        assert actual['source_status'] == 'uniquely_correlated'
        assert actual['output_status'] == 'ambiguous' and set(actual['output_candidates']) == {'n150', 'n999'}


@pytest.mark.parametrize('damage', ['missing-analysis', 'missing-reports', 'partial-analysis', 'rejected-analysis',
    'truncated-rows', 'query-failure', 'old-analysis-order', 'duplicate-analysis', 'conflicting-version',
    'missing-instance-range', 'foreign-source-range', 'stale-check-code'])
def test_native_validation_instance_locations_require_current_complete_original_analysis(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_reference_observation()
    result = project_native_validation(raw, observation, snapshots)
    assert result['source_references']['status'] == 'current'
    assert result['native_instance_locations']['status'] == 'completed'
    assert result['raw_diagnostics'][0]['source_projection']['instance_projection']['instance'] == '1'
    assert result['raw_diagnostics'][0]['graph_projection']['status'] == 'unresolved'
    events = observation['validation']['events']
    event = lambda operation: next(row for row in events if row.get('operation') == operation)
    if damage == 'missing-analysis':
        events.remove(event('Compiler.CreateProgramAnalysis3'))
    elif damage == 'missing-reports':
        events.remove(event('NativeReferenceRawReports'))
    elif damage == 'partial-analysis':
        event('NativeReferenceProgress')['percent'] = event('NativeReferenceRawReports')['percent'] = 99
    elif damage == 'rejected-analysis':
        event('NativeReferenceRawReports')['reports'][0]['kind'] = 2
    elif damage == 'truncated-rows':
        event('NativeSourceReferences')['rows'] = []
    elif damage == 'query-failure':
        event('Compiler.GetProgramAnalysis3')['hresult'] = -2147467259
    elif damage == 'old-analysis-order':
        row = event('Compiler.CreateProgramAnalysis3'); events.remove(row); events.insert(0, row)
    elif damage == 'duplicate-analysis':
        events.append(dict(event('NativeSourceReferences')))
    elif damage == 'conflicting-version':
        events.append({'operation': 'OwnedBackendModule', 'name': 'DZDataABS_Compiler_IEC.dll', 'version': 'unobserved'})
    elif damage in ('missing-instance-range', 'foreign-source-range'):
        query = event('NativeDiagnosticLocation')['source_location']['instance_ranges']
        if damage == 'missing-instance-range':
            query['candidates'] = []
        else:
            query['candidates'][0]['query']['pou'] = 'FOREIGN'
    else:
        snapshots['submitted.bin'] = snapshots['submitted.bin'][:-1] + b'!'
    result = project_native_validation(raw, observation, snapshots)
    assert result['native_instance_locations']['status'] != 'completed'
    assert result['raw_diagnostics'][0]['source_projection'].get('instance_projection', {}).get('status') != 'instance-resolved'
    assert result['raw_diagnostics'][0]['original']['code'] == 0x050c9300
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert not result['public_check']['completed']


def test_native_instance_ranges_preserve_identity_under_order_and_code_position_changes():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_instance_interval

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(st.sampled_from([193, 208]), st.integers(min_value=0, max_value=100000),
           st.integers(min_value=1, max_value=1024), st.integers(min_value=0, max_value=3),
           st.permutations(range(8)))
    @example(193, 0, 1, 0, list(reversed(range(8))))
    def check(kind, shift, size, selected, order):
        location = {'program_kind': kind, 'network': -1 if kind == 193 else 1,
                    'start_step': 3 if kind == 193 else 1, 'step_count': -1, 'element_id': -1}
        step = shift + selected * size
        query = {'query': {'resource': 'MAIN', 'start_step': step}, 'location': location}
        refs, candidates = [], []
        for index in range(8):
            path = ('CALLER_A' if index < 4 else 'INACTIVE') + '.MODULE_' + str(index) + '.INNER'
            row = {'task': 'TASK', 'instance': path}
            refs.append(row)
            value = {'resource': 'MAIN', 'start_step': shift + index * size, 'step_count': size, 'timestamp': 0}
            if index >= 4:
                value = {'resource': None, 'start_step': -1, 'step_count': -1, 'timestamp': -1}
            candidates.append({'original_reference': row, 'hresult': 0, 'code': 0, 'range': value,
                'query': {'library': '', 'pou': path, **location, 'step_count': 1 if kind == 193 else -1}})
        evidence = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': step,
                    'candidates': [candidates[index] for index in order]}
        result = bind_native_instance_interval(query, list(reversed(refs)), evidence)
        assert result['interval']['name'] == 'TASK.' + refs[selected]['instance']
        assert result['interval']['step_count'] == size
        # Omitting even a nonexecuting declaration makes the selection incomplete.
        for missing in range(8):
            partial = {**evidence, 'candidates': [row for row in evidence['candidates'] if row is not candidates[missing]]}
            assert bind_native_instance_interval(query, refs, partial)['interval'] is None
        ambiguous = deepcopy(evidence)
        other = next(row for row in ambiguous['candidates'] if row['original_reference'] == refs[(selected + 1) % 4])
        other['range'] = dict(candidates[selected]['range'])
        assert bind_native_instance_interval(query, refs, ambiguous)['interval'] is None
    check()


def test_native_st_call_chain_uses_explicit_calls_and_current_caller_offsets():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import correlate_st_diagnostic

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=4), st.integers(min_value=0, max_value=1),
           st.integers(min_value=1, max_value=10000), st.integers(min_value=1, max_value=5), st.data())
    def check(depth, selected, current_offset, block_count, data):
        references, leaf_refs, nodes, paths = [], [], [], []
        texts = {'INNER_FB': '\n\n\nY1 := SIGNAL;'}
        network = data.draw(st.integers(min_value=1, max_value=block_count))
        for caller_index, caller in enumerate(['FBD_LEFT', 'FBD_RIGHT']):
            parent_instance = caller
            task = 'TASK_' + str(caller_index)
            for level in range(depth + 1):
                parent_pou = caller if level == 0 else 'WRAPPER_' + str(level - 1)
                child_pou = 'WRAPPER_' + str(level) if level < depth else 'INNER_FB'
                member = 'SHARED_STAGE' if level == 0 else 'NESTED_' + str(level)
                row = {'library': '', 'resource': 'MAIN', 'task': task, 'instance': parent_instance,
                    'name': member, 'type': child_pou, 'source': parent_pou, 'class_code': 1, 'attribute': 1,
                    'program_kind': 208 if level == 0 else 193, 'network': network if level == 0 else -1,
                    'left': 8, 'top': 9 if level == 0 else 0, 'right': 13, 'bottom': 14}
                references.append(row)
                if level:
                    texts[parent_pou] = member + '();'
                parent_instance += '.' + member
            paths.append(parent_instance)
            leaf = {'library': '', 'resource': 'MAIN', 'task': task, 'instance': parent_instance,
                    'name': 'Y1', 'source': 'INNER_FB', 'attribute': 2, 'program_kind': 193, 'top': 3}
            references.append(leaf); leaf_refs.append(leaf)
            nodes.append({'id': 'current-' + caller, 'source_offset': current_offset + caller_index,
                          'symbol': 'SHARED_STAGE', 'template': 'function_block:WRAPPER_0',
                          'x': 8, 'y': 9, 'width': 5, 'height': 5, 'block': network-1})
        # Both callers deliberately share the same symbol, FB type and geometry.
        # Their tasks, explicit call references and native ranges distinguish them.
        query = {'hresult': 0, 'code': 0, 'query': {'resource': 'MAIN', 'start_step': 20 + selected * 10},
            'location': {'library': '', 'pou': 'INNER_FB', 'program_kind': 193,
                         'network': -1, 'start_step': 3, 'step_count': -1, 'element_id': -1}}
        ranges = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': query['query']['start_step'],
            'candidates': [{'original_reference': row, 'hresult': 0, 'code': 0,
                'query': {'library': '', 'pou': row['instance'], 'program_kind': 193,
                          'network': -1, 'start_step': 3, 'step_count': 1, 'element_id': -1},
                'range': {'resource': 'MAIN', 'start_step': 20 + index * 10, 'step_count': 3, 'timestamp': 0}}
                for index, row in enumerate(leaf_refs)]}
        references = list(data.draw(st.permutations(references)))
        caller = ['FBD_LEFT', 'FBD_RIGHT'][selected]
        caller_nodes = [nodes[selected]] + [{**nodes[selected], 'id': 'foreign-block-'+str(block),
                        'source_offset': current_offset+100+block, 'block': block}
                        for block in range(block_count) if block != network-1]
        caller_nodes = list(data.draw(st.permutations(caller_nodes)))
        actual = correlate_st_diagnostic(query, [], references, texts, caller_nodes,
                                         caller_pou=caller, native_ranges=ranges, caller_block_count=block_count)
        assert actual['status'] == 'uniquely_correlated'
        assert actual['instance'] == paths[selected]
        assert len(actual['call_chain']) == depth + 1
        assert actual['caller']['object_id'] == 'current-' + caller
        assert actual['caller']['source_offset'] == current_offset + selected
        # One missing intermediate call cannot be inferred by splitting the path.
        for call in actual['call_chain']:
            partial = [row for row in references if row is not call['native_reference']]
            assert correlate_st_diagnostic(query, [], partial, texts, caller_nodes,
                caller_pou=caller, native_ranges=ranges, caller_block_count=block_count)['status'] == 'unresolved'
        # A matching cached link interval must not override incomplete original API evidence.
        partial_ranges = deepcopy(ranges); partial_ranges['candidates'].pop()
        assert correlate_st_diagnostic(query, [actual['compiled_interval']], references, texts, caller_nodes,
            caller_pou=caller, native_ranges=partial_ranges, caller_block_count=block_count)['status'] == 'unresolved'
        assert correlate_st_diagnostic(query, [], references, texts, [nodes[1 - selected]],
            caller_pou=['FBD_LEFT', 'FBD_RIGHT'][1 - selected], native_ranges=ranges,
            caller_block_count=block_count)['status'] == 'unresolved'
        if block_count > 1:
            del nodes[selected]['block']
            assert correlate_st_diagnostic(query, [], references, texts, caller_nodes, caller_pou=caller,
                native_ranges=ranges, caller_block_count=block_count)['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('damage', [None, 'protocol', 'version', 'missing-create-call'])
def test_native_source_creation_transport_uses_the_current_project_and_checks_readback(tmp_path, monkeypatch, damage):
    import types
    from copy import deepcopy
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import source_creation_witness
    raw, saved, case = source_creation_witness()
    observation = deepcopy(case['native_creation'])
    observation['protocol_version'] = adapter.WORKSPACE_PROTOCOL
    if damage == 'protocol':
        observation['protocol_version'] = True
    elif damage == 'version':
        observation['native_version'] = '1.636.0.1'
    elif damage == 'missing-create-call':
        observation['calls'] = [row for row in observation['calls'] if row['operation'] != 'Workspace.CreateData']
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    roots, cleaned = [], []
    class Process:
        pid, returncode = 123, 0
        def __init__(self, arguments, **kwargs):
            assert arguments == [str(executable.resolve())]
            roots.append(Path(kwargs['cwd']))
        def communicate(self, payload, timeout):
            assert 0 < timeout <= 300
            request = load_frozen_native_json(payload)
            assert request['action'] == 'add_program_unit' and request['schema_version'] == adapter.WORKSPACE_PROTOCOL
            assert (roots[-1] / 'input.gxw').read_bytes() == raw
            assert (request['change']['program_name'], request['change']['kind'], request['change']['language']) == (
                case['created_name'], 'function_block', 'fbd')
            assert [row['name'] for row in request['change']['programs']] == ['1']
            (roots[-1] / 'candidate.gxw').write_bytes(saved)
            return json.dumps(observation), ''
        def poll(self):
            return self.returncode
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter.subprocess, 'Popen', Process)
    monkeypatch.setattr(adapter, '_clean_native_temp', lambda pid, token: cleaned.append((pid, token)))
    service = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    if damage:
        with pytest.raises(adapter.WorkspaceSaveError):
            service.create(raw, case['created_name'])
    else:
        result = service.create(raw, case['created_name'])
        assert result.data == saved and result.report['created_program'] == case['created_program']
        assert result.report['validation']['gxworks_compile'] == 'not_run'
    assert cleaned and cleaned[0][0] == 123 and len(cleaned[0][1]) == 32
    assert not roots[0].exists()


@pytest.mark.parametrize('control', ['current', 'missing-setter', 'foreign-version', 'old-source', 'no-op'])
def test_native_sfc_save_transport_keeps_current_children_and_separate_compile_status(tmp_path, monkeypatch, control):
    import types
    import gxworks2.workspace_adapter as adapter
    from gxw.lossless import inspect_project
    from gxw.native_write import native_source_plan
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import parse_sfc_pou
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-object-edit-20261013.zip')) as archive:
        raw = archive.read('shared-save/input.gxw')
        output = archive.read('shared-save/candidate.gxw')
        observation = load_frozen_native_json(archive.read('shared-save/observation.json'))
    logical = 'Block.Program.pou'
    source = parse_sfc_pou(next(s.raw for s in inspect_project(raw).streams if s.logical_name == logical))
    child = source.layout['children'][0]
    edits = {} if control == 'no-op' else {(child['kind'], child['name']): bytes.fromhex('030103049c0f04' + child['token_hex'])}
    prepared = prepare_sfc_project_write(raw, logical, source, edits)
    plan = native_source_plan(prepared)
    if control in ('missing-setter', 'no-op'):
        observation['calls'] = [c for c in observation['calls'] if c['operation'] != 'Workspace.SetPOUBodyData']
    if control == 'foreign-version':
        observation['native_version'] = '1.999.0.1'
    if control in ('old-source', 'no-op'):
        output = raw
    executable = tmp_path / 'adapter.exe'
    executable.write_bytes(b'transport double; never executed')
    roots = []

    def collect(executable_arg, request, root, timeout, error_type):
        assert executable_arg == executable.resolve() and timeout == 50
        assert request['operation'] == 'save_source' and request['source'] == plan
        assert (root / 'input.gxw').read_bytes() == raw
        (root / 'candidate.gxw').write_bytes(output)
        roots.append(root)
        return 0, observation

    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter, '_run_native_adapter', collect)
    service = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    if control in ('missing-setter', 'foreign-version', 'old-source'):
        with pytest.raises(adapter.WorkspaceSaveError):
            service.save(prepared, timeout=50)
    else:
        result = service.save(prepared, timeout=50)
        assert result.data == output
        assert result.report['validation']['source_body'] == 'exact'
        assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    assert len(roots) == 1 and not roots[0].exists()


@pytest.mark.parametrize('damage', [None, 'protocol', 'missing-getter-call', 'missing-getter-facts', 'failed-call'])
def test_native_program_task_transport_keeps_current_input_and_requires_configuration(tmp_path, monkeypatch, damage):
    import types
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import task_binding_witness
    raw, saved, program, expected, observation = task_binding_witness()
    # Transport double: source/append/configuration facts are frozen original
    # observations. Required collector call names use the current adapter ABI.
    observation['calls'] = [{'operation': name, 'hresult': 0, 'code': 0} for name in (
        'OpenProjectEX2', 'Workspace.GetTaskEventName', 'Workspace.GetTaskInterval', 'Workspace.GetTaskPriority',
        'Workspace.CreateTaskElement', 'Workspace.SetTaskElementName', 'Workspace.SetTaskElementLine',
        'SaveProject', 'ExportOneFileProject')]
    if damage == 'protocol':
        observation['protocol_version'] = True
    elif damage == 'missing-getter-call':
        observation['calls'].pop(1)
    elif damage == 'missing-getter-facts':
        for phase in ('before_tasks', 'after_tasks'):
            for row in observation['creation'][phase]:
                row.pop('configuration')
    elif damage == 'failed-call':
        observation['calls'][1].update(hresult=-1, code=0x2d010025)
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    roots = []

    def collect(executable_arg, request, root, timeout, error_type):
        assert executable_arg == executable.resolve() and timeout is None
        assert request['operation'] == 'bind_program_task' and request['protocol_version'] == 1
        assert (root / 'input.gxw').read_bytes() == raw and len(request['owner_token']) == 32
        assert {key: value for key, value in request['source'].items() if key != 'tasks'} == expected
        assert request['source']['tasks']
        roots.append(root)
        (root / 'candidate.gxw').write_bytes(saved)
        return 0, observation

    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter, '_run_native_adapter', collect)
    service = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    if damage:
        with pytest.raises(adapter.WorkspaceSaveError):
            service.bind_program(raw, program, resource=expected['resource'], task=expected['task'])
    else:
        result = service.bind_program(raw, program, resource=expected['resource'], task=expected['task'])
        assert result.data == saved and result.report['native_task_configuration'] == 'current'
        assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    assert roots and not roots[0].exists()


@pytest.mark.parametrize('scope_kind', ['resource', 'task'])
@pytest.mark.parametrize('damage', [None, 'protocol', 'version', 'cpu', 'missing-create-call',
    'missing-getter-call', 'missing-getter-facts', 'failed-call'])
def test_native_scope_transport_keeps_current_input_and_requires_original_configuration(tmp_path, monkeypatch, scope_kind, damage):
    import types
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import scope_creation_witness
    raw, saved, expected, observation = scope_creation_witness(2, scope_kind)
    # Original source, constructor and configuration facts are frozen. Only
    # collector operation names use the current transport ABI in this double.
    constructor = 'Workspace.CreateTaskScopeData' if scope_kind == 'task' else 'Workspace.CreateResourceScopeData'
    operations = ['OpenProjectEX2', 'Workspace.GetPOUCollection', 'Workspace.GetResourceCollection',
        constructor, 'Workspace.GetTaskEventName', 'Workspace.GetTaskInterval', 'Workspace.GetTaskPriority',
        'SaveProject', 'ExportOneFileProject']
    if scope_kind == 'task':
        operations.extend(('Workspace.GetTaskCollection', 'Workspace.SetTaskScopeName'))
    observation['calls'] = [{'operation': name, 'hresult': 0, 'code': 0} for name in operations]
    if damage == 'protocol':
        observation['protocol_version'] = True
    elif damage == 'version':
        observation['native_version'] = '1.636.0.1'
    elif damage == 'cpu':
        observation['cpu'] = 'unselected_cpu'
    elif damage in ('missing-create-call', 'missing-getter-call'):
        missing = constructor if damage == 'missing-create-call' else 'Workspace.GetTaskEventName'
        observation['calls'] = [row for row in observation['calls'] if row['operation'] != missing]
    elif damage == 'missing-getter-facts':
        for phase in ('before_tasks', 'after_tasks'):
            for row in observation['creation'][phase]:
                row.pop('configuration')
    elif damage == 'failed-call':
        observation['calls'][0].update(hresult=-1, code=0x2d010025)
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    roots = []

    def collect(executable_arg, request, root, timeout, error_type):
        assert executable_arg == executable.resolve() and timeout is None
        assert request['operation'] == 'create_' + scope_kind + '_scope'
        assert request['protocol_version'] == 1 and len(request['owner_token']) == 32
        assert (root / 'input.gxw').read_bytes() == raw
        source = request['source']
        assert {key: value for key, value in source.items() if key not in ('resources', 'tasks')} == expected
        assert source['resources'] and source['tasks']
        assert any(row['event_name'] == 'I750' for row in source['tasks'])
        assert {'T#100ms', 'T#20ms'} <= {row['interval'] for row in source['tasks']}
        roots.append(root)
        (root / 'candidate.gxw').write_bytes(saved)
        return 0, observation

    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter, '_run_native_adapter', collect)
    service = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    arguments = {'resource': expected['resource']} if scope_kind == 'task' else {}
    create = service.create_task if scope_kind == 'task' else service.create_resource
    if damage:
        with pytest.raises(adapter.WorkspaceSaveError):
            create(raw, expected[scope_kind], **arguments)
    else:
        result = create(raw, expected[scope_kind], **arguments)
        assert result.data == saved and result.report['native_task_configuration'] == 'current'
        assert result.report['validation']['gxworks_compile'] == result.report['validation']['gxworks_check'] == 'not_run'
    assert roots and not roots[0].exists()


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('cleanup_failure', [None, 'child', 'workspace'])
def test_native_validation_transport_reads_copies_before_removing_its_workspace(tmp_path, monkeypatch, partial, cleanup_failure, caplog):
    import types
    import gxworks2.workspace_adapter as adapter
    raw, observation, snapshots = native_validation_observation()
    observation['protocol_version'] = adapter.WORKSPACE_PROTOCOL
    if partial:
        observation['status'] = 'failed'
        observation['message'] = 'native_check_incomplete_timeout'
        observation['validation']['events'] = [row for row in observation['validation']['events']
            if row.get('operation') not in ('ProgramCheckRawReports', 'ProgramCheckPollingFinished')]
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    roots, cleaned = [], []
    class Process:
        pid = 123
        returncode = 1 if partial else 0
        def __init__(self, arguments, **kwargs):
            assert arguments == [str(executable.resolve())]
            roots.append(Path(kwargs['cwd']))
        def communicate(self, payload, timeout):
            request = load_frozen_native_json(payload)
            assert request['action'] == 'check_project' and request['schema_version'] == adapter.WORKSPACE_PROTOCOL
            assert (roots[-1] / 'input.gxw').read_bytes() == raw
            assert request['change']['programs'][0]['name'] == '1'
            for name, data in snapshots.items():
                (roots[-1] / name).write_bytes(data)
            return json.dumps(observation), ''
        def poll(self):
            return self.returncode
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter.subprocess, 'Popen', Process)
    def clean(pid, token):
        cleaned.append((pid, token))
        if cleanup_failure == 'child':
            raise PermissionError('native temporary directory is still in use')
    monkeypatch.setattr(adapter, '_clean_native_temp', clean)
    if cleanup_failure == 'workspace':
        original_directory = adapter.tempfile.TemporaryDirectory
        def directory(**kwargs):
            temporary = original_directory(**kwargs)
            def cleanup():
                temporary.cleanup()
                raise PermissionError('temporary workspace is still in use')
            return types.SimpleNamespace(name=temporary.name, cleanup=cleanup)
        monkeypatch.setattr(adapter.tempfile, 'TemporaryDirectory', directory)
    result = adapter.NativeWorkspaceValidation(executable, tmp_path).validate(raw)
    assert cleaned and cleaned[0][0] == 123 and len(cleaned[0][1]) == 32
    assert not roots[0].exists()
    assert result['adapter']['exit_code'] == (1 if partial else 0)
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['raw_backend_check']['completed'] is not partial
    assert result['current_raw_source_check'] == ('not_established' if partial else 'established')
    if cleanup_failure:
        assert 'cleanup failed' in caplog.text


def test_compile_save_retains_cold_failure_observation_and_original_cause(tmp_path, monkeypatch):
    import gxworks2.workspace_adapter as adapter
    import gxw.native_write as native_write
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    original, cold_observation = {'stage': 'save'}, {'stage': 'cold', 'diagnostics': ['original failure']}
    first, cold = {'phase': 'save'}, {'phase': 'cold', 'public_check': {'status': 'incomplete'}}
    def validate(_self, raw, *, _save_output=False, _evidence=None, **kwargs):
        if _save_output:
            assert raw == b'original'
            return b'saved', first, original, {}
        assert raw == b'saved' and kwargs['inspection']['saved_code']
        _evidence['observation'] = cold_observation
        return cold
    cause = GXWFormatError('native compile save requires current completed compilation and public check: cold_reopen')
    def verify(before, saved, validation, rechecked, observed):
        assert validation is first and rechecked is cold and observed is original
        raise cause
    monkeypatch.setattr(adapter.NativeWorkspaceValidation, 'validate', validate)
    monkeypatch.setattr(native_write, 'verify_native_compilation_save', verify)
    with pytest.raises(adapter.WorkspaceSaveError, match='cold_reopen') as failure:
        adapter.NativeWorkspaceSourceSave(executable, tmp_path).compile_project(b'original')
    assert failure.value.observation is cold_observation and failure.value.validation is cold
    assert failure.value.__cause__ is cause


def test_compile_save_missing_output_keeps_the_native_result_without_dumping_it_in_the_message(tmp_path, monkeypatch):
    import types
    import gxworks2.workspace_adapter as adapter
    raw, observation, snapshots = native_validation_observation()
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter, '_run_native_adapter', lambda *args: (0, observation))
    monkeypatch.setattr(adapter, '_validation_snapshots', lambda *args: snapshots)
    with pytest.raises(adapter.WorkspaceSaveError, match='compilation=accepted') as failure:
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(raw, _save_output=True)
    assert failure.value.observation is observation
    assert failure.value.validation['compilation']['acceptance'] == 'accepted'
    assert len(str(failure.value)) < 400 and '无法绑定' not in str(failure.value)


@pytest.mark.parametrize('timeout', [None, 1])
@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_native_validation_transport_stops_timed_out_child_before_native_cleanup(tmp_path, monkeypatch, timeout, cleanup_failure):
    import types
    import gxworks2.workspace_adapter as adapter
    from gxw.object_model import default_baseline
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    lifecycle = []
    class Process:
        pid = 456
        returncode = None
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, *args, **kwargs):
            if self.returncode is None:
                assert 0 < kwargs['timeout'] <= (timeout or 300)
                raise adapter.subprocess.TimeoutExpired('isolated child', 1)
            lifecycle.append('drained')
            return '', ''
        def kill(self):
            lifecycle.append('stopped')
            self.returncode = 1
        def poll(self):
            return self.returncode
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter.subprocess, 'Popen', Process)
    def clean(pid, token):
        lifecycle.append('owned-cleanup')
        if cleanup_failure:
            raise PermissionError('native temporary directory is still in use')
    monkeypatch.setattr(adapter, '_clean_native_temp', clean)
    with pytest.raises(adapter.WorkspaceValidationError, match='超时'):
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(default_baseline(), timeout=timeout)
    assert lifecycle == ['stopped', 'drained', 'owned-cleanup']


@pytest.mark.parametrize('damage', ['missing-copy', 'different-target', 'different-resource',
                                  'different-framing', 'different-version', 'different-header',
                                  'different-mask', 'unsupported-mask', 'boolean-mask', 'different-contract'])
@pytest.mark.parametrize('contract', ['legacy', 'named'])
def test_native_check_input_binding_refuses_missing_or_unbound_task_copies(damage, contract):
    from gxw.native_diagnostics import bind_native_check_input
    submitted, completed, evidence = native_check_input_snapshots(b'original code')
    if contract == 'named':
        submitted['input_contract'] = completed['input_contract'] = {
            'schema_version': 1, 'kind': 'structured', 'status': 'verified'}
    assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'current'
    if damage == 'missing-copy':
        del evidence['snapshots']['completed.bin']
    elif damage == 'different-target':
        completed['target'][0] = 2
    elif damage == 'different-resource':
        completed['rows'][0]['resource'] = 'OTHER'
    elif damage == 'different-framing':
        completed['rows'][0]['framing']['body_offset'] = 69
    elif damage == 'different-version':
        evidence['checker_version'] = '15.51'
    elif damage == 'different-mask':
        completed['mask'] = -1
    elif damage == 'unsupported-mask':
        submitted['mask'] = completed['mask'] = -2
    elif damage == 'boolean-mask':
        submitted['mask'] = completed['mask'] = True
    elif damage == 'different-contract':
        completed['input_contract'] = {'schema_version': 1, 'kind': 'sfc', 'status': 'verified'}
    else:
        full = bytearray(evidence['snapshots']['completed.bin'])
        full[4] ^= 1
        evidence['snapshots']['completed.bin'] = bytes(full)
    assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'unresolved'


def test_native_check_input_binding_never_promotes_changed_generated_or_task_bytes():
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_check_input

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.binary(min_size=1, max_size=128), st.integers(min_value=0, max_value=127),
           st.binary(max_size=32), st.sampled_from([-1, 0x7fffffff]))
    @example(b'\0', 0, b'', -1)
    def check(body, index, padding, mask):
        submitted, completed, evidence = native_check_input_snapshots(body, padding)
        submitted['mask'] = completed['mask'] = mask
        assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'current'
        position = index % len(body)
        changed = bytearray(body)
        changed[position] ^= 1
        evidence['generated']['MAIN'] = bytes(changed)
        stale = bind_native_check_input(submitted, completed, **evidence)
        assert stale['status'] == 'stale' and stale['task_copy_stable']
        assert not stale['public_check_promoted']
        evidence['generated']['MAIN'] = body
        full = bytearray(evidence['snapshots']['completed.bin'])
        full[completed['rows'][0]['framing']['body_offset'] + position] ^= 1
        evidence['snapshots']['completed.bin'] = bytes(full)
        unstable = bind_native_check_input(submitted, completed, **evidence)
        assert unstable['status'] == 'unresolved' and not unstable['task_copy_stable']

    check()


@pytest.mark.parametrize('damage', [None, 'wrong-codepage', 'missing-codepage', 'wrong-text',
                                  'odd-hex', 'invalid-bytes', 'embedded-nul'])
def test_native_task_name_preserves_the_original_cp932_resource_identity(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import bind_native_check_input
    witness = native_library_diagnostic_witness()['retained_task_context']
    submitted, completed, evidence = native_check_input_snapshots(b'current code')
    name = witness['resource_name']['text']
    evidence['generated'] = {name: evidence['generated']['MAIN']}
    for event in (submitted, completed):
        event['mask'] = witness['mask']
        event['rows'][0].update(resource=name, resource_name={key: value for key, value in witness['resource_name'].items() if key != 'text'})
    original_name = deepcopy(completed['rows'][0]['resource_name'])
    current = completed['rows'][0]['resource_name']
    if damage == 'wrong-codepage': current['codepage'] = 936
    elif damage == 'missing-codepage': del current['codepage']
    elif damage == 'wrong-text': completed['rows'][0]['resource'] = 'UNRELATED'
    elif damage == 'odd-hex': current['raw_hex'] += '0'
    elif damage == 'invalid-bytes': current['raw_hex'] = '81'
    elif damage == 'embedded-nul': current['raw_hex'] += '00'
    actual = bind_native_check_input(submitted, completed, **evidence)
    assert actual['status'] == ('current' if damage is None else 'unresolved')
    assert not actual['public_check_promoted']
    assert submitted['rows'][0]['resource_name'] == original_name
    if damage is None:
        assert actual['resources'] == [{'resource': name, 'body_bytes': 12, 'status': 'current'}]


def test_native_source_snapshot_requires_the_last_original_read_buffer(monkeypatch):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import bind_native_source_snapshot
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    body = workspace_body(source.raw)
    identity = [1] + [0] * 11
    before = {'operation': 'NativeBodyRead', 'body': identity, 'bytes': len(body),
        'file': 'before.bin',
        'evidence': {'schema_version': 1, 'origin': 'source_body'}}
    after = {**before, 'bytes': len(body) + 2, 'file': 'after.bin', 'stored_match': True}
    snapshots = {'before.bin': body, 'after.bin': body + b'\0\0'}
    result = bind_native_source_snapshot(source.raw, body_id=identity,
        reads=[before, after], snapshots=snapshots)
    assert result['status'] == 'stale' and result['native_snapshot'] == 'after.bin'
    assert result['source_body_bytes'] + 2 == result['native_body_bytes']
    assert bind_native_source_snapshot(source.raw, body_id=identity,
        reads=[before, after], snapshots={'before.bin': body})['status'] == 'unresolved'
    assert bind_native_source_snapshot(source.raw, body_id=[0] * 12,
        reads=[before], snapshots=snapshots)['status'] == 'unresolved'


def test_native_source_snapshot_binding_isolated_from_other_body_reads(monkeypatch):
    hypothesis = pytest.importorskip('hypothesis')
    from hypothesis import strategies as st
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import bind_native_source_snapshot
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    body = workspace_body(source.raw)
    identity, other = [1] + [0] * 11, [2] + [0] * 11

    def read(filename, raw, selected):
        return {'operation': 'NativeBodyRead', 'body': selected, 'bytes': len(raw), 'file': filename,
            'evidence': {'schema_version': 1, 'origin': 'source_body'}}

    @hypothesis.settings(max_examples=80, deadline=None, derandomize=True)
    @hypothesis.given(st.lists(st.binary(max_size=64), max_size=16), st.binary(min_size=1, max_size=64))
    @hypothesis.example([], b'\0\0')
    def check(unrelated, extra):
        snapshots = {'current.bin': body}
        reads = [read('current.bin', body, identity)]
        for index, raw in enumerate(unrelated):
            filename = f'other-{index}.bin'
            snapshots[filename] = raw
            reads.append(read(filename, raw, other))
        result = bind_native_source_snapshot(source.raw, body_id=identity, reads=reads, snapshots=snapshots)
        assert result['status'] == 'current' and result['native_snapshot'] == 'current.bin'
        # Later unrelated reads cannot revive the stale selected-body snapshot.
        changed = body + extra
        snapshots['changed.bin'] = changed
        reads.insert(1, read('changed.bin', changed, identity))
        result = bind_native_source_snapshot(source.raw, body_id=identity, reads=reads, snapshots=snapshots)
        assert result['status'] == 'stale' and result['native_snapshot'] == 'changed.bin'
        del snapshots['changed.bin']
        assert bind_native_source_snapshot(source.raw, body_id=identity,
            reads=reads, snapshots=snapshots)['status'] == 'unresolved'

    check()


def native_source_chain_inputs():
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    project = [9] + [0] * 11
    names = ['LEAF', 'PARENT', 'CALLER']
    programs, selections, reads, snapshots = {}, [], [], {}
    body = workspace_body(source.raw)
    for index, name in enumerate(names, 1):
        owner_id, body_id = [1, index] + [0] * 10, [2, index] + [0] * 10
        source_object = {'status': 'verified-source-object', 'parent': project,
            'lookup_type': 32, 'name': name,
            'lookup': {'hresult': 0, 'code': 0, 'id': body_id},
            'owner_lookup': {'lookup_type': 26, 'hresult': 0, 'code': 0, 'id': owner_id},
            'body_parent': {'hresult': 0, 'code': 0, 'id': owner_id},
            'body': {'id': body_id, 'read_type': {'hresult': 0, 'code': 0, 'data_type': 32},
                     'read_name': {'hresult': 0, 'code': 0, 'name': 'Program'}},
            'owner': {'id': owner_id, 'read_type': {'hresult': 0, 'code': 0, 'data_type': 26},
                      'read_name': {'hresult': 0, 'code': 0, 'name': name}},
            'language': {'hresult': 0, 'code': 0, 'object_id': owner_id, 'value': 208, 'expected': 208}}
        selections.append({'operation': 'NativeBodySelection', 'name': name,
                           'program_kind': 208, 'source_object': source_object})
        filename = name + '-body.bin'
        reads.append({'operation': 'NativeBodyRead', 'body': body_id, 'bytes': len(body), 'file': filename,
            'evidence': {'schema_version': 1, 'origin': 'source_body'}})
        programs[name], snapshots[filename] = source.raw, body
    return programs, names, dict(project_id=project, native_version='1.635.0.1',
                                 selections=selections, reads=reads, snapshots=snapshots)


def native_library_diagnostic_witness():
    return load_frozen_native_json((private_path('tests/fixtures/gxw_native_library_diagnostic.json')).read_text(encoding='utf8'))


def test_native_library_st_diagnostics_bind_the_current_typed_fbd_caller():
    import base64
    from copy import deepcopy
    from dataclasses import replace
    from hypothesis import example, given, settings, strategies as st
    from gxw.callable_sources import ProjectCallableSources
    from gxw.declarations import parse_declarations
    from gxw.native_diagnostics import correlate_st_diagnostic, project_native_source_diagnostic
    from gxw.object_model import ProjectSourceContext
    from gxw.structured_pou import parse_structured_pou

    damages = [None, 'no-context', 'no-ranges', 'incomplete-range', 'foreign-library',
               'wrong-interface-owner', 'ambiguous-interface', 'caller-body', 'caller-pou',
               'missing-call', 'duplicate-call', 'nested-st-caller', 'wrong-call-kind',
               'stale-graph-offset', 'duplicate-graph-object', 'missing-library-text', 'foreign-range']

    controls = native_library_diagnostic_witness()['direct_library_fbd_caller']['controls']
    positions = [(i, j) for i, control in enumerate(controls) for j in range(len(control['diagnostics']))]

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(st.sampled_from(positions), st.sampled_from(damages),
           st.integers(min_value=1, max_value=5), st.booleans())
    @example((0, 0), None, 1, False)
    @example((1, 1), None, 2, True)
    @example((1, 0), 'stale-graph-offset', 1, False)
    @example((2, 2), None, 2, True)
    @example((2, 5), None, 1, False)
    def check(position, damage, other_libraries, reverse):
        control_index, diagnostic_index = position
        witness = native_library_diagnostic_witness()['direct_library_fbd_caller']
        control = witness['controls'][control_index]
        diagnostic = control['diagnostics'][diagnostic_index]
        expected = control['expected_node']
        documents = {name: parse_declarations(base64.b64decode(witness[field]), logical_name=name)
                     for name, field in [(witness['local_table'], 'local_declarations_base64'),
                                         (witness['interface_stream'], 'interface_declarations_base64')]}
        program = parse_structured_pou(base64.b64decode(control['caller_program_base64']),
                                      logical_name='MAIN.Program.pou', preserve_unsupported_records=True)
        sources = ProjectCallableSources(witness['scope']['cpu'], program.logical_name, documents, {},
            global_variable_hiding=True,
            user_library_owners={witness['interface_stream']: witness['interface_owner']})
        context = ProjectSourceContext(b'', program, documents, [program.logical_name], sources)
        nodes = context.object_model()['nodes']
        query = diagnostic['original_source_location']
        query['query'] = {'resource': query['resource'], 'start_step': query['code_step'],
                          'original_code': diagnostic['original']['code'], 'original_kind': diagnostic['original']['kind']}
        call = control['original_call_reference']
        line_reference = diagnostic['original_line_reference']
        # Extra same-spelled library references cannot substitute the original
        # scoped line or the project-local call. The native cold controls are
        # independent oracles for the current record offset and physical line.
        references = [call, *diagnostic.get('original_line_references', [line_reference])]
        for index in range(other_libraries):
            library = 'OtherLibrary_' + str(index)
            references.extend([{**call, 'library': library}, {**line_reference, 'library': library}])
        physical_lines = witness['source_text_lines']
        source_text = '\n'.join(physical_lines.get(str(line), '')
                                for line in range(max(map(int, physical_lines))+1))
        texts = {('Modbus', 'MB_PROCESS_50'): source_text,
                 'MB_PROCESS_50': 'Unrelated same-named local ST source'}
        options = {'caller_pou': 'MAIN', 'native_ranges': query['instance_ranges'], 'caller_context': context}
        if damage == 'no-context': options['caller_context'] = None
        elif damage == 'no-ranges': options['native_ranges'] = None
        elif damage == 'incomplete-range': query['instance_ranges']['status'] = 'incomplete'
        elif damage == 'foreign-library': query['location']['library'] = 'OtherLibrary'
        elif damage == 'wrong-interface-owner': sources.user_library_owners[witness['interface_stream']]['library'] = 'OtherLibrary'
        elif damage == 'ambiguous-interface':
            stream = 'OtherLibrary.Labels.lnl'
            documents[stream] = replace(documents[witness['interface_stream']], logical_name=stream)
            owners = {**sources.user_library_owners,
                      stream: {**witness['interface_owner'], 'library': 'OtherLibrary'}}
            ambiguous = ProjectCallableSources(witness['scope']['cpu'], program.logical_name, documents, {},
                global_variable_hiding=True, user_library_owners=owners)
            options['caller_context'] = replace(context, sources=ambiguous)
        elif damage == 'caller-body':
            changed = replace(program, nodes=tuple(
                replace(node, symbol='OtherCaller') if node.offset == expected['source_offset'] else node
                for node in program.nodes))
            options['caller_context'] = replace(context, program=changed)
        elif damage == 'caller-pou': options['caller_pou'] = 'OTHER_PROGRAM'
        elif damage == 'missing-call': references.remove(call)
        elif damage == 'duplicate-call': references.append(deepcopy(call))
        elif damage == 'nested-st-caller': call.update(program_kind=193, network=-1)
        elif damage == 'wrong-call-kind': call['data_type'] = 13
        elif damage == 'stale-graph-offset':
            stale = next(c['expected_node']['source_offset'] for c in witness['controls']
                         if c['expected_node']['source_offset'] != expected['source_offset'])
            next(node for node in nodes if node['id'] == expected['id'])['source_offset'] = stale
        elif damage == 'duplicate-graph-object':
            nodes.append(deepcopy(next(node for node in nodes if node['id'] == expected['id'])))
        elif damage == 'missing-library-text': del texts[('Modbus', 'MB_PROCESS_50')]
        elif damage == 'foreign-range':
            query['instance_ranges']['candidates'][0]['original_reference']['library'] = 'OtherLibrary'
        if reverse:
            references.reverse()
            nodes.reverse()
        result = correlate_st_diagnostic(query, [], references, texts, nodes, **options)
        assert not result['public_check_promoted']
        if damage is not None:
            assert result['status'] == 'unresolved'
        else:
            assert result['status'] == 'uniquely_correlated'
            assert result['source']['library'] == 'Modbus'
            assert result['source']['zero_based_line'] == diagnostic['expected_source_line']
            assert result['source']['text'] == diagnostic['expected_text']
            assert (result['caller']['object_id'], result['caller']['source_offset']) == (
                expected['id'], expected['source_offset'])
            assert result['caller']['interface_source'] == witness['interface_stream']
            source = project_native_source_diagnostic({**query, 'source_object': witness['source_object']},
                diagnostic['original'], project_id=witness['source_object']['parent'],
                native_version=witness['scope']['native_version'], references=references)
            assert source['status'] == 'source-resolved'
            assert source['instance_projection']['status'] == 'instance-resolved'
            assert not source['public_check_promoted']
    check()


def test_library_task_program_binding_requires_an_original_root_namespace():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import _native_task_source_bindings

    damages = [None, 'missing-reference', 'missing-reference-with-local-shadow',
               'foreign-reference', 'ambiguous', 'nested-instance',
               'namespace-object', 'missing-selection', 'version', 'not-program', 'stale-body',
               'duplicate-body', 'malformed-root', 'wrong-language', 'local-name-collision', 'project']

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(st.sampled_from(damages), st.integers(min_value=1, max_value=6), st.booleans())
    @example(None, 1, False)
    @example('local-name-collision', 2, True)
    @example('missing-reference-with-local-shadow', 1, False)
    @example('not-program', 1, False)
    def check(damage, other_libraries, reverse):
        witness = native_library_diagnostic_witness()['library_task_program']
        expected = witness['expected']
        selected, body = witness['selection'], witness['source_body']
        root = witness['root_reference']
        # Same POU spelling in other instances, tasks and resources cannot
        # identify the task's root. The oracle is the original native witness.
        unrelated = [{**root, 'library': 'OtherLibrary_' + str(index), 'instance': expected['pou'] + '.inner'}
                     for index in range(other_libraries)]
        unrelated.extend([{**root, 'task': 'OTHER_TASK'}, {**root, 'resource': 'OTHER_RESOURCE'}])
        references, selections, bodies = [root, *unrelated], [selected], [body]
        local = {}
        options = {'project_id': witness['project_id'], 'native_version': witness['native_version']}
        if damage == 'missing-reference': references = None
        elif damage == 'missing-reference-with-local-shadow':
            references = None
            local[expected['pou']] = b'unrelated local source'
            bodies.append({**body, 'library': ''})
        elif damage == 'foreign-reference': root['library'] = 'OtherLibrary'
        elif damage == 'ambiguous': references.append({**root, 'library': 'OtherLibrary'})
        elif damage == 'nested-instance': root['instance'] += '.inner'
        elif damage == 'namespace-object': selected['source_object']['namespace_object']['read_name']['name'] = 'OtherLibrary'
        elif damage == 'missing-selection': selections = []
        elif damage == 'version': options['native_version'] = 'unobserved'
        elif damage == 'not-program':
            def replace_program_kind(value):
                if isinstance(value, dict):
                    for child in value.values(): replace_program_kind(child)
                elif isinstance(value, list):
                    if len(value) == 12 and value[0] == 3 and value[6] == 0x1000000:
                        value[6] = 0x1000002
                    else:
                        for child in value: replace_program_kind(child)
            replace_program_kind(selected)
            replace_program_kind(body)
        elif damage == 'stale-body': body['status'] = 'stale'
        elif damage == 'duplicate-body': bodies.append(deepcopy(body))
        elif damage == 'malformed-root': references.append({**root, 'library': None})
        elif damage == 'wrong-language': root['program_kind'] = 208
        elif damage == 'local-name-collision':
            local[expected['pou']] = b'unrelated local source'
            bodies.append({**body, 'library': ''})
        elif damage == 'project': options['project_id'] = [123] + [0] * 11
        if reverse:
            if references is not None: references.reverse()
            bodies.reverse()
        result, = _native_task_source_bindings([witness['task']], local, {'sources': bodies},
                                               selections, references, **options)
        assert not result['public_check_promoted']
        if damage not in (None, 'local-name-collision'):
            assert result['status'] == 'unresolved'
        else:
            assert result['status'] == 'source-resolved'
            assert (result['library'], result['pou'], result['body_id'], result['pou_id']) == (
                expected['library'], expected['pou'], expected['body_id'], expected['pou_id'])
    check()


@pytest.mark.parametrize('damage', [None, 'version', 'project', 'lookup-namespace', 'lookup-id',
    'lookup-failure', 'namespace-name', 'owner-name', 'body-type', 'namespace-parent',
    'owner-parent', 'collection-parent', 'body-parent', 'language-owner', 'language', 'missing-namespace'])
def test_native_library_diagnostic_requires_complete_namespace_proof(damage):
    from gxw.native_diagnostics import project_native_source_diagnostic
    witness = native_library_diagnostic_witness()
    query, diagnostic = witness['query'], witness['diagnostic']
    source = query['source_object']
    options = {'project_id': witness['project_id'], 'native_version': witness['native_version']}
    references = [candidate['original_reference'] for candidate in query['instance_ranges']['candidates']]
    if damage == 'version': options['native_version'] = '1.636.0.1'
    elif damage == 'project': options['project_id'] = [123] + [0]*11
    elif damage == 'lookup-namespace': source['lookup']['owner_parent'] = source['owner']['id']
    elif damage == 'lookup-id': source['lookup']['body_id'] = source['owner']['id']
    elif damage == 'lookup-failure': source['lookup']['body_code'] = 0x2d010025
    elif damage == 'namespace-name': source['namespace_object']['read_name']['name'] = 'OtherLibrary'
    elif damage == 'owner-name': source['owner']['read_name']['name'] = 'OtherPOU'
    elif damage == 'body-type': source['body']['read_type']['data_type'] = 32
    elif damage == 'namespace-parent': source['namespace_parent']['id'] = source['owner']['id']
    elif damage == 'owner-parent': source['owner_parent']['id'] = source['namespace_object']['id']
    elif damage == 'collection-parent': source['owner_parent']['collection_parent'] = source['owner']['id']
    elif damage == 'body-parent': source['body_parent']['id'] = source['namespace_object']['id']
    elif damage == 'language-owner': source['language']['object_id'] = source['body']['id']
    elif damage == 'language': source['language']['value'] = 208
    elif damage == 'missing-namespace': del query['location']['library']
    result = project_native_source_diagnostic(query, diagnostic, references=references, **options)
    assert not result['public_check_promoted']
    if damage is not None:
        assert result['status'] == 'unresolved'
    else:
        expected = witness['expected']
        assert result['status'] == 'source-resolved'
        assert result['source']['library'] == expected['library']
        assert result['source']['pou'] == expected['pou']
        assert result['native_position']['start_step'] == expected['source_line']
        assert result['instance_projection']['status'] == 'instance-resolved'
        assert result['instance_projection']['instance'] == expected['instance']
        assert result['original_diagnostic'] == diagnostic


def test_native_library_instance_binding_excludes_other_namespaces():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_source_diagnostic

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=8), st.booleans())
    def check(other_libraries, reverse):
        witness = native_library_diagnostic_witness()
        query = witness['query']
        reference = query['instance_ranges']['candidates'][0]['original_reference']
        unrelated = [{**reference, 'library': 'OtherLibrary_' + str(index)} for index in range(other_libraries)]
        references = [reference, *unrelated]
        if reverse: references.reverse()
        options = {'project_id': witness['project_id'], 'native_version': witness['native_version']}
        result = project_native_source_diagnostic(query, witness['diagnostic'], references=references, **options)
        assert result['instance_projection']['status'] == 'instance-resolved'
        assert result['instance_projection']['instance'] == witness['expected']['instance']
        # Identical names, source positions and intervals in another library
        # cannot supply the response for the selected native reference.
        wrong = deepcopy(query)
        wrong['instance_ranges']['candidates'][0]['original_reference'] = unrelated[0]
        result = project_native_source_diagnostic(wrong, witness['diagnostic'], references=references, **options)
        assert result['status'] == 'source-resolved'
        assert result['instance_projection']['status'] == 'unresolved'
        assert result['original_diagnostic'] == witness['diagnostic']
    check()


def test_same_named_library_bodies_require_their_own_selection_and_last_read():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_source_chain
    from gxw.native_write import workspace_body

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.permutations([0, 1, 2]), st.integers(min_value=0, max_value=2),
           st.binary(min_size=1, max_size=32))
    def check(order, selected_index, extra):
        raw_programs, _, _ = native_source_chain_inputs()
        raw = raw_programs['LEAF']; body = workspace_body(raw)
        witness = native_library_diagnostic_witness()
        programs, selections, reads, snapshots, keys = {}, [], [], {}, []
        for index in range(3):
            library, pou = 'Library_' + str(index), witness['expected']['pou']
            key = (library, pou); keys.append(key)
            source = deepcopy(witness['query']['source_object'])
            # Rename an entire synthetic namespace, retaining the independently
            # recorded native parent/type/language layout.
            source['library'] = source['namespace_object']['read_name']['name'] = library
            source['language'].update(value=208, expected=208)
            for facts in (source['namespace_object'], source['owner'], source['body']): facts['id'][0] += 100 * (index + 1)
            lookup = source['lookup']
            for field in ('owner_parent', 'body_parent', 'namespace_id', 'owner_id', 'body_id'): lookup[field][0] += 100 * (index + 1)
            for row, fields in ((source['namespace_parent'], ('id', 'collection')),
                                (source['owner_parent'], ('id', 'collection', 'collection_parent')),
                                (source['body_parent'], ('id',)), (source['language'], ('object_id',))):
                for field in fields: row[field][0] += 100 * (index + 1)
            selections.append({'operation': 'NativeBodySelection', 'name': pou, 'library': library,
                               'program_kind': 208, 'source_object': source})
            filename = 'library-' + str(index) + '.bin'
            reads.append({'operation': 'NativeBodyRead', 'body': source['body']['id'], 'bytes': len(body), 'file': filename,
                          'evidence': {'schema_version': 1, 'origin': 'source_body'}})
            snapshots[filename] = body; programs[key] = raw
        options = {'project_id': witness['project_id'], 'native_version': witness['native_version'],
                   'selections': [selections[i] for i in order], 'reads': reads, 'snapshots': snapshots}
        selected = keys[selected_index]
        assert bind_native_source_chain(programs, [selected], **options)['status'] == 'current'
        options['selections'] = [row for row in options['selections'] if row['library'] != selected[0]]
        assert bind_native_source_chain(programs, [selected], **options)['status'] == 'unresolved'
        options['selections'] = selections
        selections[selected_index]['program_kind'] = 193
        selections[selected_index]['source_object']['language'].update(value=193, expected=193)
        assert bind_native_source_chain(programs, [selected], **options)['status'] == 'unresolved'
        selections[selected_index]['program_kind'] = 208
        selections[selected_index]['source_object']['language'].update(value=208, expected=208)
        changed = body + extra; snapshots['changed.bin'] = changed
        reads.append({**reads[selected_index], 'file': 'changed.bin', 'bytes': len(changed)})
        assert bind_native_source_chain(programs, [selected], **options)['status'] == 'stale'
        assert all(bind_native_source_chain(programs, [key], **options)['status'] == 'current'
                   for key in keys if key != selected)
    check()


def test_original_public_library_source_query_does_not_follow_the_projected_body_or_coordinates():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import compare_public_source_diagnostic, project_native_source_diagnostic
    witness = native_library_diagnostic_witness()['public_resource_diagnostic']
    expected, query = witness['expected'], witness['query']
    references = [row['original_reference'] for row in query['instance_ranges']['candidates']]
    source_id = query['source_object']['body']['id']
    options = {'project_id': witness['project_id'], 'native_version': witness['native_version']}

    @settings(max_examples=50, deadline=None, derandomize=True)
    @example(identity=[0] * 12, top=0)
    @example(identity=source_id, top=21)
    @given(identity=st.lists(st.integers(min_value=0, max_value=0xffffffff), min_size=12, max_size=12),
           top=st.integers(min_value=-1, max_value=0x7fffffff))
    def check(identity, top):
        # The same-check original source query supplies the oracle. Public
        # body and display coordinates vary independently; no native rerun or
        # PLC behavior is inferred from these generated transport variants.
        report = {**deepcopy(witness['diagnostic']), 'source_object_id': identity, 'top': top}
        native = project_native_source_diagnostic(query, report, references=references, **options)
        assert native['status'] == 'source-resolved'
        assert (native['source']['library'], native['source']['pou'], native['native_position']['start_step']) == (
            expected['library'], expected['pou'], expected['source_line'])
        assert native['source']['body_id'] == source_id and 'library' not in native['original_diagnostic']
        assert (native['instance_projection']['instance'], native['instance_projection']['task']) == (
            expected['instance'], expected['task'])
        public = {**report, 'name': report['name']['text'], 'object_id': identity}
        audit = compare_public_source_diagnostic(public, query, report, **options)
        assert audit['status'] == ('unresolved' if not any(identity) else
            'source-consistent' if identity == source_id else 'source-conflict')
        assert not audit['public_check_promoted']
        if any(identity):
            assert audit['public_position_binding'] == {'status': 'different-representation',
                'public_program_kind': 1, 'source_program_kind': 193, 'direct_source_mapping_allowed': False}
        for key, value in [('record_size', 68), ('report_interface', 'unobserved'),
                           ('instance_kind', 1), ('program_kind', 193), ('instance', {'text': 'another-resource'})]:
            assert project_native_source_diagnostic(query, {**report, key: value}, **options)['status'] == 'unresolved'
    check()


def test_public_source_name_collision_cannot_promote_a_body_or_generated_code_position():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import compare_public_source_diagnostic

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.permutations(['LEAF', 'PARENT', 'CALLER']), st.integers(min_value=0, max_value=2),
           st.sampled_from([193, 208]), st.integers(min_value=0, max_value=4096))
    def check(names, actual_index, language, step):
        _, _, evidence = native_source_chain_inputs()
        sources = {row['name']: deepcopy(row['source_object']) for row in evidence['selections']}
        resource, actual_pou = names[0], names[actual_index]
        source = sources[actual_pou]
        source['language'].update(value=language, expected=language)
        query = {'resource': resource, 'code_step': step, 'hresult': 0, 'code': 0,
            'location': {'library': '', 'pou': actual_pou, 'program_kind': language, 'network': 1,
                         'start_step': 1, 'step_count': -1, 'element_id': 0, 'action_transition_present': False},
            'source_object': source}
        diagnostic = {'kind': 2, 'code': 0x050c9300, 'step': step,
                      'library': {'text': ''}, 'name': {'text': resource}}
        public = {'kind': 2, 'code': diagnostic['code'], 'name': resource, 'instance': resource,
            'object_id': sources[resource]['body']['id'], 'program_kind': 1, 'step': step,
            'network': -1, 'left': -1, 'top': step, 'right': -1, 'bottom': -1}
        compare = lambda p=public, q=query: compare_public_source_diagnostic(p, q, diagnostic,
            project_id=evidence['project_id'], native_version=evidence['native_version'])
        result = compare()
        assert result['status'] == ('source-consistent' if actual_pou == resource else 'source-conflict')
        assert result['native_source_projection']['source']['pou'] == actual_pou
        assert result['public_position_binding'] == {'status': 'different-representation',
            'public_program_kind': 1, 'source_program_kind': language, 'direct_source_mapping_allowed': False}
        assert not result['public_check_promoted'] and result['original_public_diagnostic'] == public
        # Matching both body and language still supplies no independent
        # proof that these public coordinates are current source positions.
        matching = {**public, 'object_id': source['body']['id'], 'program_kind': language}
        assert compare(matching)['public_position_binding']['status'] == 'not_established'
        assert not compare(matching)['public_position_binding']['direct_source_mapping_allowed']
        assert compare({**public, 'step': step+1})['status'] == 'unresolved'
        assert compare(q={**query, 'code': 0x2d010025})['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('damage', [None, 'adapter', 'compiler', 'workspace', 'missing_adapter', 'missing_compiler'])
def test_saved_public_source_audit_keeps_version_binding_with_workspace_reads(tmp_path, monkeypatch, damage):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import analyze_directory
    _, _, evidence = native_source_chain_inputs()
    sources = {row['name']: row['source_object'] for row in evidence['selections']}
    query = {'resource': 'PARENT', 'code_step': 28, 'hresult': 0, 'code': 0,
        'location': {'library': '', 'pou': 'LEAF', 'program_kind': 208, 'network': 1,
                     'start_step': 1, 'step_count': -1, 'element_id': 0, 'action_transition_present': False},
        'source_object': sources['LEAF']}
    public = {'kind': 2, 'code': 0x050c9300, 'name': 'PARENT', 'instance': 'PARENT',
        'object_id': sources['PARENT']['body']['id'], 'program_kind': 1, 'step': 28,
        'network': -1, 'left': -1, 'top': 28, 'right': -1, 'bottom': -1}
    modules = {'adapter': 'DZDataABS_CompilerAdapter.dll', 'compiler': 'DZDataABS_Compiler_IEC.dll',
               'workspace': 'DZDataABS_Workspace.dll'}
    events = [{'operation': 'ProjectID', 'words': evidence['project_id']}]
    events.extend({'operation': 'OwnedBackendModule', 'name': name,
                   'version': 'unobserved' if damage == key else '1.635.0.1'}
                  for key, name in modules.items() if damage != 'missing_' + key)
    (tmp_path/'native-events.jsonl').write_text('\n'.join(json.dumps(row) for row in events), encoding='utf-8')
    (tmp_path/'public-source-diagnostic-audit.json').write_text(json.dumps([{
        'audit': {'original_public_diagnostic': public}, 'source_query': query,
        'raw_observation': {'kind': public['kind'], 'code': public['code'], 'step': 28,
                            'library_hex': '', 'name_hex': b'PARENT'.hex()}}]), encoding='utf-8')
    result = analyze_directory(tmp_path)
    audit = result['diagnostic_projection']['source_audits'][0]
    assert result['diagnostic_projection']['source_body_binding'] == (
        'conflicted' if damage is None else 'partial_or_unresolved')
    assert audit['status'] == ('source-conflict' if damage is None else 'unresolved')
    assert not audit['public_check_promoted'] and not result['public_check']['completed']
    if damage is None:
        assert audit['native_source_projection']['source']['pou'] == 'LEAF'
        assert audit['public_position_binding']['status'] == 'different-representation'


@pytest.mark.parametrize('missing', ['LEAF', 'PARENT', 'CALLER'])
def test_native_source_chain_cannot_replace_a_missing_body_with_identical_other_sources(monkeypatch, missing):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import bind_native_source_chain
    programs, names, evidence = native_source_chain_inputs()
    result = bind_native_source_chain(programs, names, **evidence)
    assert result['status'] == 'current' and len(result['sources']) == 3
    # All bodies contain identical bytes, but each belongs to a different POU.
    del evidence['snapshots'][missing + '-body.bin']
    result = bind_native_source_chain(programs, names, **evidence)
    assert result['status'] == 'unresolved'
    assert next(row for row in result['sources'] if row['pou'] == missing)['status'] == 'unresolved'
    assert bind_native_source_chain(programs, [], **evidence)['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['owner_name', 'parent', 'language', 'latest_selection', 'version'])
def test_native_source_chain_requires_original_identity_for_every_caller(monkeypatch, damage):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import bind_native_source_chain
    programs, names, evidence = native_source_chain_inputs()
    parent = evidence['selections'][1]
    source = parent['source_object']
    if damage == 'owner_name':
        source['owner']['read_name']['name'] = 'UNRELATED'
    elif damage == 'parent':
        source['body_parent']['id'] = evidence['selections'][2]['source_object']['owner_lookup']['id']
    elif damage == 'language':
        source['language']['value'] = 193
    elif damage == 'latest_selection':
        evidence['selections'].append({'operation': 'NativeBodySelection', 'name': 'PARENT'})
    else:
        evidence['native_version'] = 'unobserved'
    assert bind_native_source_chain(programs, names, **evidence)['status'] == 'unresolved'


def test_native_source_chain_status_is_independent_of_required_order_and_unrelated_reads(monkeypatch):
    from hypothesis import example, given, settings, strategies as st
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import bind_native_source_chain

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.permutations(['LEAF', 'PARENT', 'CALLER']), st.sampled_from(['LEAF', 'PARENT', 'CALLER']),
           st.binary(min_size=1, max_size=32), st.lists(st.binary(max_size=32), max_size=8))
    @example(['CALLER', 'PARENT', 'LEAF'], 'PARENT', b'\0', [b''])
    def check(order, changed, extra, unrelated):
        programs, _, evidence = native_source_chain_inputs()
        read = next(row for row in evidence['reads'] if row['file'] == changed + '-body.bin')
        data = evidence['snapshots'][read['file']] + extra
        latest = {**read, 'bytes': len(data), 'file': 'changed.bin'}
        evidence['reads'].append(latest)
        evidence['snapshots']['changed.bin'] = data
        for index, raw in enumerate(unrelated):
            filename = 'unrelated-' + str(index) + '.bin'
            evidence['reads'].append({**read, 'body': [99, index] + [0] * 10,
                                      'file': filename, 'bytes': len(raw)})
            evidence['snapshots'][filename] = raw
        result = bind_native_source_chain(programs, order, **evidence)
        assert result['status'] == 'stale'
        assert next(row for row in result['sources'] if row['pou'] == changed)['native_snapshot'] == 'changed.bin'
        del evidence['snapshots']['changed.bin']
        assert bind_native_source_chain(programs, order, **evidence)['status'] == 'unresolved'

    check()


def diagnostic_source_witnesses():
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-native-diagnostic-sources-20261002.zip')) as archive:
        return load_frozen_native_json(archive.read('cpu-source-witnesses.json'))


@pytest.mark.parametrize('case', diagnostic_source_witnesses(), ids=lambda case: case['cpu'])
def test_native_fb_diagnostics_correlate_only_unique_current_instance_ranges(monkeypatch, case):
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import correlate_st_diagnostic
    result = case['phase_result']
    references = result['source_references']['queries'][0]['rows']
    actual = [correlate_st_diagnostic(query, result['link_witness']['rows'], references,
        case['source_texts'], case['caller_nodes']) for query in result['diagnostic_locations']]
    assert actual == case['projections']
    assert all(row['status'] == case['expected_status'] and not row['public_check_promoted'] for row in actual)
    phases = case['phase_classification']
    assert phases['checker_resources']['correspondence'] == 'current'
    assert phases['public_check']['status'] == 'incomplete'
    assert phases['diagnostic_projection']['status'] == 'failed'
    assert phases['current_source_check'] == 'not_established'
    if case['cpu'] == 'FX0N':
        assert all(row['source_instance_candidates'] == [
            'FBD_MATRIX.APP_MODULE_STAGE_A', 'FBD_MATRIX.APP_MODULE_STAGE_B'] for row in actual)
        assert all(row['compiled_interval_name'] == 'TASK_MATRIX.FBD_MATRIX' for row in actual)
    else:
        assert {row['instance'] for row in actual} == {
            'FBD_MATRIX.APP_MODULE_STAGE_A', 'FBD_MATRIX.APP_MODULE_STAGE_B'}
        assert all(row['source']['text'] == 'Y1 := SIGNAL;' for row in actual)


@pytest.mark.parametrize('damage', ['native_failure', 'empty_location', 'library', 'coarse_fbd',
    'duplicate_interval', 'range_boundary', 'duplicate_reference', 'missing_object'])
def test_diagnostic_source_mapping_keeps_missing_and_ambiguous_evidence_unresolved(monkeypatch, damage):
    from copy import deepcopy
    monkeypatch.syspath_prepend(str(private_path('research')))
    from program_check_evidence import correlate_st_diagnostic
    case = diagnostic_source_witnesses()[0]
    result = case['phase_result']
    query = deepcopy(result['diagnostic_locations'][0])
    links = deepcopy(result['link_witness']['rows'])
    references = deepcopy(result['source_references']['queries'][0]['rows'])
    nodes = deepcopy(case['caller_nodes'])
    if damage == 'native_failure':
        query['hresult'] = -2147467259
    elif damage == 'empty_location':
        query['location'] = None
    elif damage == 'library':
        query['location']['library'] = 'UNRELATED_LIBRARY'
    elif damage == 'coarse_fbd':
        query['location']['program_kind'] = 208
    elif damage in ('duplicate_interval', 'range_boundary'):
        interval = next(row for row in links if row['name'] == 'TASK_MATRIX.FBD_MATRIX.APP_MODULE_STAGE_A')
        if damage == 'duplicate_interval':
            links.append(deepcopy(interval))
        else:
            query['query']['start_step'] = interval['step_start'] + interval['step_count']
            links = [interval]
    elif damage == 'duplicate_reference':
        reference = next(row for row in references if row['name'] == 'Y1' and row['instance'] == 'FBD_MATRIX.APP_MODULE_STAGE_A')
        references.append(deepcopy(reference))
    else:
        nodes = []
    actual = correlate_st_diagnostic(query, links, references, case['source_texts'], nodes)
    assert actual['status'] == 'unresolved' and not actual['public_check_promoted']


def test_native_debug_range_rejection_keeps_original_error_and_does_not_replace_public_check():
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-native-diagnostic-sources-20261002.zip')) as archive:
        cases = load_frozen_native_json(archive.read('native-debug-boundary.json'))
    assert len(cases) == 3
    for case in cases:
        assert case['returncode'] == 0 and case['public_check']
        assert len(case['debug_observations']) == 12
        assert all(row['hresult'] == -2147467259 and row['code'] == 0x50010006
            and row['initialized'] and row['count'] == 0 and not row['ranges_present']
            for row in case['debug_observations'])


def authored_fbd_validation_case(name='motion', stage='original'):
    """Original complete author graphs and actual cold collector snapshots."""
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-instances-20261012.zip')) as archive:
        case = next(row for row in load_frozen_native_json(archive.read('cases.json'))
                    if row['case'] == name and row['stage'] == stage)
        prefix = case['prefix']
        return (archive.read(prefix+'input.gxw'), load_frozen_native_json(archive.read(prefix+'observation.json')),
                {key: archive.read(prefix+key) for key in case['snapshots']}, case)


def network_instance_validation_case(cpu):
    """One actual native save/cold check, using that exact CPU's own project."""
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fbd-network-instances-20261012.zip')) as archive:
        case = next(row for row in load_frozen_native_json(archive.read('cases.json')) if row['cpu'] == cpu)
        prefix = case['prefix']
        return (archive.read(prefix+'candidate.gxw'), load_frozen_native_json(archive.read(prefix+'check-observation.json')),
                {key: archive.read(prefix+key) for key in case['snapshots']}, case)


@pytest.mark.parametrize('cpu', [row['cpu'] for row in load_frozen_native_json(
    (private_path('research/results/gxw-fbd-network-instances-20261012.json')).read_text(encoding='utf-8'))['cases']])
def test_multi_network_fbd_instances_match_original_cpu_specific_references(cpu):
    from gxw.native_diagnostics import project_native_validation
    from gxw.object_model import read_project_context
    raw, observation, snapshots, witness = network_instance_validation_case(cpu)
    result = project_native_validation(raw, observation, snapshots)
    assert result['cpu'] == cpu
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['source_references']['status'] == 'current'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['current_source_check'] == 'not_established'
    assert result['current_raw_source_check'] == 'established'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == 2
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    for diagnostic in diagnostics:
        graph = diagnostic['graph_projection']
        native, = [row for row in witness['native_callers'] if graph['instance'] == 'FBD_MATRIX.' + row['name']]
        assert graph['status'] == 'uniquely_correlated' and graph['source_chain']['status'] == 'current'
        assert graph['caller']['pou'] == native['source']
        assert graph['caller']['network'] == native['network']
        assert graph['caller']['bbox'] == [native[field] for field in ('left', 'top', 'right', 'bottom')]
        node, = [row for row in context.object_model()['nodes'] if row['symbol'] == native['name']]
        assert graph['caller']['object_id'] == node['id']
        assert graph['caller']['source_offset'] == node['source_offset']
        assert not graph['public_check_promoted']
    assert {row['graph_projection']['instance'] for row in diagnostics} == {
        'FBD_MATRIX.MODEL_STAGE_ALPHA', 'FBD_MATRIX.MODEL_STAGE_BETA'}


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'L02', 'Q04UDV'])
@pytest.mark.parametrize('damage', ['network', 'bbox'])
def test_multi_network_native_reference_conflict_keeps_diagnostics_without_selecting_another_instance(cpu, damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, _ = network_instance_validation_case(cpu)
    references, = [row['rows'] for row in observation['validation']['events']
                   if row.get('operation') == 'NativeSourceReferences']
    native, = [row for row in references if row.get('source') == 'FBD_MATRIX'
               and row.get('program_kind') == 208 and row.get('name') == 'MODEL_STAGE_ALPHA']
    native['network' if damage == 'network' else 'left'] += 2 if damage == 'network' else 1
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'established'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == 2
    unique = [row['graph_projection'] for row in diagnostics if row['graph_projection']['status'] == 'uniquely_correlated']
    assert [row['instance'] for row in unique] == ['FBD_MATRIX.MODEL_STAGE_BETA']
    assert sum(row['graph_projection']['status'] == 'unresolved' for row in diagnostics) == 1


@pytest.mark.parametrize('name,stage', [('motion', 'original'), ('motion', 'candidate'),
    ('motion-library', 'candidate'), ('motion-callee', 'candidate'),
    ('motion-declarations-added', 'candidate'), ('mapping', 'original'), ('mapping', 'candidate')])
def test_authored_complete_fbd_graphs_retain_current_instance_diagnostics(name, stage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, witness = authored_fbd_validation_case(name, stage)
    result = project_native_validation(raw, observation, snapshots)
    expected = witness['expected']
    assert result['cpu'] == expected['cpu']
    assert result['compilation']['source_context']['status'] == 'current'
    assert result['current_raw_source_check'] == 'established'
    assert result['source_bodies']['status'] == 'current'
    assert len(result['source_bodies']['sources']) == witness['project_sources'] + witness['library_sources']
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert result['current_source_check'] == 'not_established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == expected['diagnostic_count']
    for actual, frozen in zip(diagnostics, expected['diagnostics']):
        graph = actual['graph_projection']
        assert graph['status'] == 'uniquely_correlated' and graph['source_chain']['status'] == 'current'
        assert (graph['instance'], graph['task']) == (frozen['instance'], frozen['task'])
        assert (graph['source']['pou'], graph['source']['zero_based_line']) == (frozen['callee'], frozen['line'])
        assert graph['source'].get('library', '') == frozen['library']
        assert graph['caller']['pou'] == frozen['caller']
        assert (graph['caller']['network'], graph['caller']['object_id'], graph['caller']['source_offset']) == (
            frozen['network'], frozen['object_id'], frozen['source_offset'])
        assert not graph['public_check_promoted']
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']


@pytest.mark.parametrize('change', ['motion-callee', 'motion-declarations-added'])
@pytest.mark.parametrize('old_source', [False, True])
def test_unchanged_authored_fbd_graph_cannot_reuse_old_callee_or_declarations(change, old_source):
    from gxw.native_diagnostics import project_native_validation
    original, original_observation, original_snapshots, _ = authored_fbd_validation_case()
    changed, changed_observation, changed_snapshots, _ = authored_fbd_validation_case(change, 'candidate')
    from gxw.object_model import read_project_context
    from gxw.native_write import workspace_body
    assert workspace_body(read_project_context(original, 'PrgAxis1.Program.pou').program.raw) == (
        workspace_body(read_project_context(changed, 'PrgAxis1.Program.pou').program.raw))
    raw = original if old_source else changed
    observation = changed_observation if old_source else original_observation
    snapshots = changed_snapshots if old_source else original_snapshots
    result = project_native_validation(raw, observation, snapshots)
    assert result['compilation']['source_context']['status'] == 'unresolved'
    assert result['source_bodies']['status'] == ('stale' if change == 'motion-callee' else 'current')
    assert result['selection']['status'] == 'unresolved'
    assert result['current_raw_source_check'] == result['current_source_check'] == 'not_established'
    diagnostics = result['diagnostic_projection']['source_diagnostics']
    assert len(diagnostics) == 18 and all(row['graph_projection']['status'] == 'unresolved' for row in diagnostics)
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}


def test_authored_project_and_library_source_chains_require_each_final_getter():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import _native_validation_sources, _native_validation_library_sources, bind_native_source_chain
    raw, observation, snapshots, witness = authored_fbd_validation_case()
    _, sources = _native_validation_sources(raw)
    programs = {row['name']: row['raw'] for row in sources}
    programs.update({(row['library'], row['name']): row['raw'] for row in _native_validation_library_sources(raw)})
    keys = list(programs)
    events = observation['validation']['events']
    key_of = lambda row: (row['library'], row['name']) if row.get('library') else row['name']
    selections = [row for row in events if row.get('operation') == 'NativeBodySelection']
    reads = [row for row in events if row.get('operation') == 'NativeBodyRead']
    final = {key_of(row): row for row in selections}
    body_ids = {key: final[key]['source_object']['body']['id'] for key in keys}

    @settings(max_examples=60, deadline=None, derandomize=True)
    @example(list(range(19)), 0, 'late-stale', b'\0')
    @example(list(reversed(range(19))), 18, 'late-missing', b'\0')
    @given(st.permutations(list(range(19))), st.integers(min_value=0, max_value=18),
           st.sampled_from(['current', 'late-stale', 'late-missing', 'missing-selection']),
           st.binary(min_size=1, max_size=8))
    def check(order, selected_index, damage, extra):
        assert len(keys) == witness['project_sources'] + witness['library_sources'] == 19
        selected = keys[selected_index]
        rank = {keys[index]: value for value, index in enumerate(order)}
        identities = {tuple(body_ids[key]): rank[key] for key in keys}
        selected_rows = sorted(deepcopy(selections), key=lambda row: rank[key_of(row)])
        body_reads = sorted(deepcopy(reads), key=lambda row: identities[tuple(row['body'])])
        copies = dict(snapshots)
        latest = next(row for row in reversed(body_reads) if row['body'] == body_ids[selected])
        if damage.startswith('late-'):
            data = copies[latest['file']] + extra
            body_reads.append({**latest, 'file': 'late-source.bin', 'bytes': len(data)})
            if damage == 'late-stale': copies['late-source.bin'] = data
        elif damage == 'missing-selection':
            selected_rows = [row for row in selected_rows if key_of(row) != selected]
        result = bind_native_source_chain(programs, [keys[index] for index in order],
            project_id=observation['validation']['project'], native_version=observation['native_version'],
            selections=selected_rows, reads=body_reads, snapshots=copies)
        expected = {'current': 'current', 'late-stale': 'stale', 'late-missing': 'unresolved',
                    'missing-selection': 'unresolved'}[damage]
        assert result['status'] == expected
        for row in result['sources']:
            key = (row['library'], row['pou']) if row.get('library') else row['pou']
            assert row['status'] == (expected if key == selected else 'current')
    check()


def test_authored_cpu_conversion_retains_native_failure_and_current_instance_checks(archived_native_requests):
    from research.freeze_gxw_authored_fbd_cpu_conversion import replay
    assert archived_native_requests(replay) == 2


@pytest.mark.parametrize('prefix', ['cold-check/', 'edit/check/'])
def test_authored_cpu_conversion_check_cannot_reuse_original_cpu_snapshot(prefix):
    import zipfile
    from gxw.models import GXWFormatError
    from gxw.native_diagnostics import project_native_validation
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-cpu-conversion-20261013.zip')) as archive:
        original = archive.read('conversion/input.gxw')
        observation = load_frozen_native_json(archive.read(prefix+'observation.json'))
        snapshots = {name:archive.read(prefix+name)
                     for name in load_frozen_native_json(archive.read(prefix+'snapshot-names.json'))}
    with pytest.raises(GXWFormatError, match='unbound protocol, version or CPU'):
        project_native_validation(original, observation, snapshots)


def test_authored_cpu_menu_matrix_retains_each_native_outcome_and_current_instance_check(archived_native_requests):
    from research.freeze_gxw_authored_fbd_cpu_matrix import replay
    assert archived_native_requests(replay) == 16


def test_authored_cpu_menu_matrix_cannot_mix_different_actual_cpu_checks():
    from hypothesis import example, given, settings, strategies as st
    from gxw.models import GXWFormatError
    from gxw.native_diagnostics import project_native_validation
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-cpu-matrix-20261013.zip')) as archive:
        cases = [row for row in load_frozen_native_json(archive.read('cases.json')) if row['status'] == 'observed']
        witnesses = []
        for case in cases:
            prefix = case['prefix'] + 'check/'
            names = load_frozen_native_json(archive.read(prefix+'snapshot-names.json'))
            witnesses.append((case['name'], archive.read(prefix+'input.gxw'),
                load_frozen_native_json(archive.read(prefix+'observation.json')), {name:archive.read(prefix+name) for name in names}))
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-authored-fbd-menu-20261013.zip')) as archive:
        for case in load_frozen_native_json(archive.read('summary.json'))['cases']:
            if case['construction'] != 'constructed_and_saved':
                continue
            prefix = case['prefix'] + 'edit/check/'
            names = load_frozen_native_json(archive.read(prefix+'snapshot-names.json'))
            witnesses.append((case['cpu'], archive.read(prefix+'input.gxw'),
                load_frozen_native_json(archive.read(prefix+'observation.json')), {name:archive.read(prefix+name) for name in names}))
    assert len(witnesses) == 23 and len({row[0] for row in witnesses}) == 23

    @settings(max_examples=32, deadline=None, derandomize=True)
    @example(0, 1)
    @example(15, 15)
    @example(16, 1)
    @example(20, 1)
    @given(st.integers(min_value=0, max_value=len(witnesses)-1),
           st.integers(min_value=1, max_value=len(witnesses)-1))
    def check(index, shift):
        cpu, raw, _, _ = witnesses[index]
        foreign_cpu, _, observation, snapshots = witnesses[(index + shift) % len(witnesses)]
        assert cpu != foreign_cpu
        with pytest.raises(GXWFormatError, match='unbound protocol, version or CPU'):
            project_native_validation(raw, observation, snapshots)
    check()


@lru_cache(maxsize=8)
def _recorded_source_save_witness(case):
    if case != 'l02':
        assert case in ('fxu', 'L02S', 'L02', 'L06', 'L26', 'L26-BT', 'FX3G')
        archive = 'gxw-fbd-ui-entry-20261013.zip'
        prefix = 'application-FXU/' if case == 'fxu' else 'merged-menu/application-' + case + '/'
        names = tuple(prefix + name for name in ('fb-source.gxw', 'initial-draft.json',
                      'initial-save-request.json', 'initial.gxw', 'initial-save-observation.json'))
    else:
        assert case == 'l02'
        archive = 'gxw-authored-fbd-native-transfer-20261013.zip'
        names = ('library-import-L02/candidate.gxw', 'library-caller-L02/created-draft.json',
                 'library-caller-L02/create/request.json', 'library-caller-L02/create/candidate.gxw',
                 'library-caller-L02/create/observation.json')
    with zipfile.ZipFile(private_path('research/' + 'evidence/' + archive)) as frozen:
        before, draft, request, saved, observation = [frozen.read(name) for name in names]
    return before, load_frozen_native_json(draft), load_frozen_native_json(request), saved, load_frozen_native_json(observation)


@pytest.mark.parametrize('case', ['fxu', 'l02', 'L02S', 'L02', 'L06', 'L26', 'L26-BT', 'FX3G'])
def test_recorded_source_save_preserves_the_original_request_and_verifies_saved_output(case):
    from copy import deepcopy
    from gxw.native_write import native_source_plan, verify_native_save
    from research.recorded_source_save import prepare_recorded_source_save

    before, draft, request, saved, observation = _recorded_source_save_witness(case)
    originals = deepcopy((draft, request, observation))
    legacy_pointer = case in ('L02S', 'L02', 'L06', 'L26', 'L26-BT')
    prepared = prepare_recorded_source_save(before, draft, request, legacy_l_pointer=legacy_pointer)
    assert prepared.baseline == before
    expected = request['source']
    if legacy_pointer:
        expected = {**expected, 'system_variable_initialization':
                    {'offset': 68, 'before': [-1, -1], 'after': [2048, 4095]}}
    assert native_source_plan(prepared) == expected
    verified = verify_native_save(prepared, saved, observation)
    assert verified.data == saved
    if legacy_pointer:
        assert verified.report['validation']['unedited_configuration'] == 'preserved'
    assert (draft, request, observation) == originals


@pytest.mark.parametrize('case', ['fxu', 'l02', 'L02S', 'L02', 'L06', 'L26', 'L26-BT', 'FX3G'])
def test_recorded_pointer_initialization_difference_requires_the_exact_legacy_control(case):
    from gxw.models import GXWFormatError
    from research.recorded_source_save import prepare_recorded_source_save

    before, draft, request, _, _ = _recorded_source_save_witness(case)
    legacy_pointer = case in ('L02S', 'L02', 'L06', 'L26', 'L26-BT')
    with pytest.raises(GXWFormatError):
        prepare_recorded_source_save(before, draft, request, legacy_l_pointer=not legacy_pointer)


@pytest.mark.parametrize('case', ['fxu', 'l02'])
@pytest.mark.parametrize('damage', [
    'baseline', 'cpu', 'codepage', 'source_name', 'draft_source', 'before_body',
    'invalid_after_body', 'changed_after_body', 'request_declaration', 'draft_declaration',
    'saved_output', 'operation', 'protocol',
])
def test_recorded_source_save_rejects_foreign_or_modified_evidence(case, damage):
    from copy import deepcopy
    from gxw.models import GXWFormatError
    from gxw.native_write import verify_native_save
    from research.recorded_source_save import prepare_recorded_source_save

    before, draft, request, saved, observation = _recorded_source_save_witness(case)
    draft, request = deepcopy((draft, request))
    plan = request['source']
    if damage == 'baseline':
        before = saved
    elif damage == 'cpu':
        plan['cpu'] = 'FX3U/FX3UC'
    elif damage == 'codepage':
        plan['codepage'] += 1
    elif damage == 'source_name':
        plan['program_name'] += '_FOREIGN'
    elif damage == 'draft_source':
        draft['program'] = 'FOREIGN.Program.pou'
    elif damage == 'before_body':
        plan['before_body'] = plan['after_body']
    elif damage == 'invalid_after_body':
        plan['after_body'] = '!not-base64!'
    elif damage == 'changed_after_body':
        body = base64.b64decode(plan['after_body'])
        changed = body.replace('M0'.encode('utf-16le'), 'M9'.encode('utf-16le'))
        assert changed != body
        plan['after_body'] = base64.b64encode(changed).decode('ascii')
    elif damage == 'request_declaration':
        plan['local_after'][0]['data_type'] = 'BOOL'
    elif damage == 'draft_declaration':
        patch, = draft['declaration_edits'].values()
        patch['upserts'][0]['name'] += '_FOREIGN'
    elif damage == 'saved_output':
        saved = before
    elif damage == 'operation':
        request['operation'] = 'create_source'
    else:
        assert damage == 'protocol'
        request['protocol_version'] = True
    with pytest.raises(GXWFormatError):
        prepared = prepare_recorded_source_save(before, draft, request)
        verify_native_save(prepared, saved, observation)


def test_authored_library_transfer_keeps_unused_check_and_instantiated_rejection_separate(archived_native_requests):
    from research.freeze_gxw_authored_fbd_native_transfer import replay

    result = archived_native_requests(replay)
    assert result['library_import']['sources'] == 16
    assert result['check']['caller_nodes'] == 0
    assert result['check']['public_check'] == {'status': 'passed', 'completed': True}
    assert result['constructed_caller']['native_saves'] == 2
    assert result['constructed_caller']['compilation'] == 'completed-rejected'
    assert result['constructed_caller']['public_check'] == {'status': 'not_requested', 'completed': False}
    assert result['constructed_caller']['current_source_check'] == 'not_established'
    assert result['authored_program_transfer']['code'] == '0x2b200005'


def test_authored_reconstruction_preserves_complete_callers_and_current_instance_diagnostics(archived_native_requests):
    from research.freeze_gxw_authored_fbd_reconstruction import replay

    result = archived_native_requests(replay)
    assert result['target_cpu'] == 'L02'
    assert (result['project_sources'], result['library_sources'], result['local_declarations']) == (3, 16, 87)
    assert (result['globals'], result['tasks'], result['declaration_comments']) == (3, 2, 5)
    assert result['edits']['native_saves'] == 2
    assert [(r['before_folder'], r['after_folder']) for r in result['folder_relocations']] == [(2, 1), (3, 2), (4, 3)]
    final = result['checks'][-1]
    assert final['compilation'] == 'accepted'
    assert final['source_getter_counts'] == [19, 19]
    assert final['public_check'] == {'status': 'incomplete', 'completed': False}
    assert final['raw_check'] == 'completed-rejected'
    assert final['diagnostics'] == final['uniquely_correlated'] == 18


def test_authored_menu_reconstruction_keeps_native_rejections_and_encoding_losses_visible(archived_native_requests):
    from research.freeze_gxw_authored_fbd_menu import replay

    result = archived_native_requests(replay)
    assert (result['native_menu_count'], result['programmable_menu_count']) == (60, 57)
    assert result['completed_l_menu_count'] == 5
    assert len(result['cases']) == 8
    saved = {row['cpu']: row for row in result['cases'] if row['construction'] == 'constructed_and_saved'}
    for cpu in ('L02S', 'L06', 'L26', 'L26-BT'):
        row = saved[cpu]
        assert row['compilation'] == 'accepted'
        assert row['source_getter_counts'] == [19, 19]
        assert row['raw_diagnostics'] == row['uniquely_correlated'] == 18
        assert row['public_check'] == {'status': 'incomplete', 'completed': False}
        assert all(item['status'] == 'matched' for item in row['library_text_changes'])
    for cpu, count, codes in (
        ('Q00J', 24, {'0x500c5027': 23, '0x50010000': 1}),
        ('Q00', 24, {'0x500c5027': 23, '0x50010000': 1}),
        ('Q00UJ', 17, {'0x500c8015': 16, '0x50010000': 1}),
    ):
        row = saved[cpu]
        assert row['compilation'] == 'rejected'
        assert row['compile_diagnostics'] == count and row['compile_codes'] == codes
        assert row['compile_source_resolved'] == row['uniquely_correlated'] == 0
        assert row['public_check'] == {'status': 'not_requested', 'completed': False}
        assert row['resource_binding']['target'] == 'MAIN'
        assert row['resource_binding']['authored'] == 'Sample'
        assert sum(item['changed_characters'] for item in row['library_text_changes']) == 2806
        assert all(item['status'] == 'codepage_replacement' for item in row['library_text_changes'])
    failed, = [row for row in result['cases'] if row['construction'] == 'rejected']
    assert failed['failure']['code'] == 0x2d01009c
    assert failed['compilation'] == 'not_requested'


FX3U_DECLARATION_CONTROLS = 'fx3u-declaration-type-controls-20261015/native-replay/'


@lru_cache(maxsize=44)
def compile_location_case(name='q00j-production-tables'):
    from research.freeze_gxw_compile_source_locations import ARCHIVE, case_data
    if name.startswith('control-check-'):
        from research.freeze_gxw_authored_fbd_q_menus import ARCHIVE
    elif name.startswith('declarations/'):
        from research.freeze_gxw_authored_fbd_fx_menus import ARCHIVE
    elif name.startswith(FX3U_DECLARATION_CONTROLS):
        ARCHIVE = private_path('research/' + 'evidence/motion-cross-model-20261014.zip')
    with zipfile.ZipFile(ARCHIVE) as archive:
        prefix = name + '/'
        entries = {item: archive.read(item) for item in archive.namelist() if item.startswith(prefix)}
    raw, observation, snapshots = case_data(entries, name)
    return raw, decode_frozen_workspace_evidence(observation), snapshots


def test_remaining_authored_q_menus_retain_each_save_rejection_and_controlled_source_position(archived_native_requests):
    from gxw.native_diagnostics import PUBLIC_DECLARATION_COMPILE_POSITION_CPUS, PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS
    from research.freeze_gxw_authored_fbd_q_menus import DECLARATION_CPUS, LIBRARY_CPUS, replay
    result = archived_native_requests(replay)
    assert result['new_entries'] == len(result['cases']) == len(result['controls']) == 23
    assert result['complete_q_menu_count'] == 42
    assert set(DECLARATION_CPUS) <= PUBLIC_DECLARATION_COMPILE_POSITION_CPUS
    assert set(LIBRARY_CPUS) <= PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS
    assert len(result['initial_failures']) == 2


def test_current_compile_locations_replay_native_identity_and_independent_position_controls(archived_native_requests):
    from research.freeze_gxw_compile_source_locations import replay
    result = archived_native_requests(replay)
    assert len(result['cases']) == 7 and len(result['failed_attempts']) == 3
    assert [(row['cpu'], row['changed_reports']) for row in result['controls']] == [
        ('Q00J', 1), ('Q00', 1), ('Q00UJ', 4)]
    for row in result['cases']:
        if row['cpu'] in ('Q00J', 'Q00'):
            assert (row['source_resolved'], row['unresolved']) == (23, 1)
        elif row['cpu'] == 'Q00UJ':
            assert (row['source_resolved'], row['unresolved']) == (16, 1)
        else:
            assert row['compilation'] == 'accepted' and row['raw_check'] == 'completed-rejected'


def test_fx3u_declaration_controls_move_only_the_selected_report_or_remove_its_type_rejection(archived_native_requests):
    from research.freeze_motion_cross_model import replay_fx3u_declaration_controls
    with zipfile.ZipFile(private_path('research/' + 'evidence/motion-cross-model-20261014.zip')) as archive:
        result = archived_native_requests(replay_fx3u_declaration_controls, archive.read)
    assert result['cpu'] == 'FX3U/FX3UC'
    assert [(row['case'], row['source_resolved']) for row in result['cases']] == [
        ('baseline', 23), ('library-line', 23), ('project-line', 23), ('library-type', 22)]
    assert [(row['field'], row['before'], row['after']) for row in result['controls']] == [
        ('record_id', 3, 103), ('record_id', 32, 77), ('data_type', 'LREAL', 'REAL')]
    assert result['precision_adaptation'] == 'not_established'
    assert all(row['diagnostic_control_only'] for row in result['controls'])


@pytest.mark.parametrize('damage', [None, 'raw-marker', 'observed-marker', 'wrong-simulator'])
def test_l02_first_scan_observer_replays_native_words_and_rejects_detached_claims(archived_native_requests, damage):
    from research.freeze_motion_cross_model import L02_FIRST_SCAN_FOLDER, replay_l02_first_scan_observer
    prefix = L02_FIRST_SCAN_FOLDER+'/local-command-control/execution/named-first-scan-cases/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/motion-cross-model-20261014.zip')) as archive:
        read = archive.read
        if damage:
            plan = load_frozen_native_json(read(prefix+'plan.json'))
            result = load_frozen_native_json(read(prefix+'result.json'))
            sample = result['cases'][0]['samples'][0]
            if damage == 'raw-marker':
                sample['native'][plan['fields']['1.FirstScanMarker']['address']] ^= 1
            elif damage == 'observed-marker':
                sample['observed']['1.FirstScanMarker'] ^= 1
            else:
                result['connection']['target_simulator'] = 1
            payload = json.dumps(result).encode()
            with pytest.raises(AssertionError):
                archived_native_requests(replay_l02_first_scan_observer, lambda name: payload if name == prefix+'result.json' else read(name))
        else:
            result = archived_native_requests(replay_l02_first_scan_observer, read)
            assert result['cases'] == 3 and result['stable_samples'] == 6
            assert result['raw_word_oracle_checks'] == 320 and result['latch_sentinel'] == 'passed'
            assert result['cleanup'] == 'passed' and result['simulator_D_ended']


@pytest.mark.parametrize('damage', [None, 'dint-words', 'binary64-words', 'fixture-and-observation', 'simulator'])
def test_fx3u_numeric_fbd_module_replays_raw_values_with_an_independent_binary64_oracle(archived_native_requests, damage):
    from research.freeze_motion_cross_model import FX_NUMERIC_FBD_FOLDER, replay_fx3u_numeric_fbd_module
    fixed = FX_NUMERIC_FBD_FOLDER+'/sign-and-carry/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/motion-cross-model-20261014.zip')) as archive:
        read = archive.read
        if damage:
            execution = load_frozen_native_json(read(fixed+'fixed-vector-running.json'))
            replacement = {}
            if damage == 'dint-words':
                sample = execution['cases'][6]['samples'][0]
                sample['values'][execution['output_dint'][0]] ^= 1
                sample['dint_value'] ^= 1
            elif damage == 'binary64-words':
                sample = execution['cases'][35]['samples'][0]
                sample['values']['D30'] ^= 1
                sample['binary64_words'][0] ^= 1
            elif damage == 'fixture-and-observation':
                fixture = load_frozen_native_json(read(fixed+'fixed-vectors.json'))
                fixture['cases'][2]['expected_value'] = 0
                execution['cases'][2]['vector']['expected_value'] = 0
                for sample in execution['cases'][2]['samples']:
                    sample['dint_value'] = 0
                    for address in execution['output_dint']:
                        sample['values'][address] = 0
                replacement[fixed+'fixed-vectors.json'] = json.dumps(fixture).encode()
            else:
                execution['connection']['target_simulator'] = 1
            replacement[fixed+'fixed-vector-running.json'] = json.dumps(execution).encode()
            with pytest.raises(AssertionError):
                archived_native_requests(replay_fx3u_numeric_fbd_module, lambda name: replacement[name] if name in replacement else read(name))
        else:
            result = archived_native_requests(replay_fx3u_numeric_fbd_module, read)
            assert result['fixed_cases'] == 51 and result['stable_samples'] == 102
            assert result['raw_word_oracle_checks'] == 1310
            assert result['negation_counterexample_retained'] and result['source_preserving_negation'] == 'passed'
            assert result['public_check'] == {'status': 'passed', 'completed': True}
            assert result['authored_dual_axis_acceptance_promoted'] is False


@pytest.mark.parametrize('damage', [None, 'cpu', 'public-completion', 'configuration', 'saved-code', 'cold-graph'])
def test_fx3u_word_motion_replays_authored_engineering_without_promoting_execution(archived_native_requests, damage):
    from research.freeze_motion_cross_model import FX_WORD_MOTION_FOLDER, replay_fx3u_word_motion_module
    folder = FX_WORD_MOTION_FOLDER+'/gui-rebase/construct/mcp/FX3U_FX3UC/'
    name = folder+('cold-axis2-response.json' if damage == 'cold-graph' else 'current-native-check-response.json')
    with zipfile.ZipFile(private_path('research/' + 'evidence/motion-cross-model-20261014.zip')) as archive:
        read = archive.read
        if damage:
            payload = load_frozen_native_json(read(name))
            data = payload['structured_content']['data']
            if damage == 'cpu':
                data['cpu'] = data['snapshot']['cpu'] = 'FX3G/FX3GC'
            elif damage == 'public-completion':
                data['public_check']['completed'] = False
            elif damage == 'configuration':
                data['selection']['configuration_bytes_current'] = False
            elif damage == 'saved-code':
                data['inspection']['saved_code']['status'] = 'matches'
            else:
                data['model']['wires'][0]['end'][1] += 1
            changed = json.dumps(payload).encode()
            with pytest.raises(AssertionError):
                archived_native_requests(replay_fx3u_word_motion_module, lambda member: changed if member == name else read(member))
        else:
            result = archived_native_requests(replay_fx3u_word_motion_module, read)
            assert (result['project_sources'], result['library_sources']) == (3, 21)
            assert (result['binary64_declarations'], result['helper_types'], result['helper_instances']) == (23, 5, 11)
            assert result['callers'] == {'schema_version': 2, 'nodes_each': 90, 'wires_each': 41}
            assert result['native_controls'][2]['public_check'] == {'status': 'incomplete', 'completed': False}
            assert result['native_controls'][2]['raw_diagnostics'] == 4
            assert result['mcp_runs'][-1]['public_check'] == {'status': 'passed', 'completed': True}
            assert result['saved_code']['status'] == 'differs'
            assert result['full_dual_axis_execution'] == result['final_gui_compile_and_save'] == 'not_executed'
            assert result['authored_dual_axis_acceptance_promoted'] is False


@pytest.mark.parametrize('damage', [None, 'cpu', 'current-source', 'saved-code', 'rounding-oracle', 'timer', 'retained-counterexample'])
def test_fx3u_dual_axis_execution_replays_current_code_and_independent_raw_expectations(archived_native_requests, damage):
    from research.freeze_motion_cross_model import FX_WORD_MOTION_FOLDER, FX_ROUNDED_EXECUTION_FOLDER, replay_fx3u_dual_axis_execution
    gui = FX_WORD_MOTION_FOLDER+'/capacity/real-rounded-shared/gui-runtime/mcp-cold-binding/'
    execution = FX_ROUNDED_EXECUTION_FOLDER+'/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/motion-cross-model-20261014.zip')) as archive:
        read = archive.read
        replacements = {}

        def change(name, edit):
            payload = load_frozen_native_json(replacements.get(name, read(name)))
            edit(payload)
            replacements[name] = json.dumps(payload).encode()

        if damage == 'cpu':
            change(execution+'numeric-summary.json', lambda payload: payload['connection'].update(actual_cpu_name='FX3U'))
        elif damage == 'current-source':
            change(gui+'native-binding-response.json', lambda payload: payload['structured_content']['data']['selection'].update(configuration_bytes_current=False))
        elif damage == 'saved-code':
            change(gui+'summary.json', lambda payload: payload['saved_code']['resources'][0]['channels'][0].update(matches=False))
            change(gui+'native-binding-response.json', lambda payload: payload['structured_content']['data']['inspection']['saved_code']['resources'][0]['channels'][0].update(matches=False))
        elif damage == 'rounding-oracle':
            folder = execution+'fx3u-real-rounding-fixed-cases/'
            key, index = 'axis1.power.wPosTrqLimit', 3
            plan = load_frozen_native_json(read(folder+'plan.json'))
            address = plan['fields'][key]['address']

            def alter_reference(payload):
                payload['cases'][1]['oracle'][key]['expected'] = 0
                payload['steps'][index]['expect'][key] = 0

            def alter_result(payload):
                payload['steps'][index]['step']['expect'][key] = 0
                for sample in payload['steps'][index]['samples']:
                    sample['native'][address] = sample['observed'][key] = 0

            change('fx3u-real-rounding-fixed-cases/plan.json', alter_reference)
            change(folder+'plan.json', lambda payload: payload['steps'][index]['expect'].update({key: 0}))
            change(folder+'result.json', alter_result)
        elif damage == 'timer':
            plan = load_frozen_native_json(read(execution+'timer-phases/plan.json'))
            key = 'home.preset'
            address = plan['fields'][key]['address']

            def alter_timer(payload):
                sample = payload['timer_pairs'][1]['samples'][-1]
                sample['native'][address] = sample['observed'][key] = 106

            change(execution+'timer-phases/result.json', alter_timer)
        elif damage == 'retained-counterexample':
            folder = 'fx3u-shared-module-execution-20261015/fractional-conversion-cases/'
            plan = load_frozen_native_json(read(folder+'plan.json'))
            key = 'axis1.torque.wTorque'
            address = plan['fields'][key]['address']

            def alter_failure(payload):
                sample = payload['steps'][-1]['samples'][0]
                sample['native'][address] = (-4) & 0xffff
                sample['observed'][key] = -4

            change(folder+'result.json', alter_failure)
        if damage:
            with pytest.raises(AssertionError):
                archived_native_requests(replay_fx3u_dual_axis_execution, lambda name: replacements.get(name, read(name)))
        else:
            result = archived_native_requests(replay_fx3u_dual_axis_execution, read)
            assert result['fixed_steps'] == 137 and result['stable_samples'] == 199
            assert result['raw_word_oracle_checks'] == 1586
            assert result['timer_roles'] == 4 and result['timer_samples'] == 24
            assert result['real_rounding_fixed_pairs'] == 9 and result['REAL_truncation_counterexample_retained']
            assert result['cold_code_binding'] == result['simulator_code_and_parameters'] == 'matches'
            assert result['actual_cpu_name'] == 'FX3UC' and result['target_simulator'] == 0
            assert result['native_processes_started'] == 0 and result['full_model_acceptance'] == 'not_established'


@pytest.mark.parametrize('case', ['q00j-production-tables', FX3U_DECLARATION_CONTROLS + 'baseline'])
def test_current_compile_declaration_binding_requires_ordered_getters_and_current_fields(case):
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import (_native_validation_library_sources, _native_validation_sources,
        bind_native_compile_declarations, project_native_validation)
    raw, observation, snapshots = compile_location_case(case)
    _, projects = _native_validation_sources(raw)
    sources = projects + _native_validation_library_sources(raw)
    context = project_native_validation(raw, observation, snapshots)['compilation']['source_context']
    original = observation['validation']['events']
    key = lambda row: (row.get('library', ''), row.get('pou', row.get('name')))
    identity = [key(row) for row in sources]
    changes = ['current', 'missing-before', 'missing-after', 'duplicate-read', 'late-before',
        'body-parent', 'table-parent', 'variable-parent', 'variable-kind', 'table-identity',
        'body-identity', 'namespace', 'duplicate-variable', 'duplicate-variable-identity',
        'data_type', 'class_code', 'line', 'initial_value', 'comment']

    @settings(max_examples=64, deadline=None, derandomize=True)
    @example(0, 'missing-after', 0, 'x')
    @example(3, 'namespace', 0, 'x')
    @example(2, 'table-parent', 0, 'x')
    @example(1, 'current', 2, 'x')
    @given(st.integers(min_value=0, max_value=18), st.sampled_from(changes),
           st.integers(min_value=0, max_value=80), st.text(min_size=1, max_size=8))
    def check(index, damage, selected_row, extra):
        events = deepcopy(original)
        selected = [row for row in events if row.get('operation') == 'NativeDeclarationRead'
                    and key(row) == identity[index]]
        before, = [row for row in selected if row['stage'] == 'before-build']
        after, = [row for row in selected if row['stage'] == 'after-build']
        variable = after['rows'][selected_row % len(after['rows'])]
        # Native enumeration order must not become a physical row index.
        before['rows'].reverse()
        after['rows'].reverse()
        if damage.startswith('missing-'):
            events.remove(before if damage == 'missing-before' else after)
        elif damage == 'duplicate-read':
            events.append(deepcopy(after))
        elif damage == 'late-before':
            events.remove(before)
            events.append(before)
        elif damage in ('body-parent', 'table-parent'):
            after[damage.replace('-', '_')]['id'][0] ^= 1
        elif damage == 'variable-parent':
            variable['parent']['id'][0] ^= 1
        elif damage == 'variable-kind':
            variable['identity']['read_type']['data_type'] ^= 1
        elif damage == 'table-identity':
            after['table']['id'][0] ^= 1
        elif damage == 'body-identity':
            after['body_id'][0] ^= 1
        elif damage == 'namespace':
            after['library'] += 'missing/' + extra
        elif damage == 'duplicate-variable':
            after['rows'].append(deepcopy(variable))
        elif damage == 'duplicate-variable-identity':
            other = next(row for row in after['rows'] if row['name'] != variable['name'])
            variable['identity']['id'] = other['identity']['id'][:]
        elif damage in ('class_code', 'line'):
            variable[damage] += 100
        elif damage != 'current':
            variable[damage] += '\0' + extra
        result = bind_native_compile_declarations(sources, events=events, source_context=context)
        for position, name in enumerate(identity):
            assert result[name]['status'] == ('unresolved' if position == index and damage != 'current' else 'current')
    check()


@pytest.mark.parametrize('name', ['q00j-production-tables', 'q00-production-tables', 'q00uj-production-baseline',
                                 FX3U_DECLARATION_CONTROLS + 'baseline'])
def test_compile_location_identity_scope_does_not_guess_from_names_or_promote_summary_reports(name):
    from copy import deepcopy
    from gxw.native_diagnostics import (_native_validation_library_sources, _native_validation_sources,
        bind_native_compile_declarations, correlate_native_compile_declaration,
        correlate_native_compile_position, project_native_validation)
    raw, observation, snapshots = compile_location_case(name)
    result = project_native_validation(raw, observation, snapshots)
    reports = result['compilation']['source_diagnostics']
    assert reports[-1]['source_projection']['status'] == 'unresolved'
    diagnostic = next(row for row in reports if row['source_projection']['status'] == 'source-resolved')
    report, source = diagnostic['original'], diagnostic['source_projection']['source']
    if report['instance_kind'] == 5:
        _, projects = _native_validation_sources(raw)
        bindings = bind_native_compile_declarations(projects + _native_validation_library_sources(raw),
            events=observation['validation']['events'], source_context=result['compilation']['source_context'])
        binding = bindings[source['library'], source['pou']]
        project = lambda r=report, cpu=result['cpu'], b=binding: correlate_native_compile_declaration(r, cpu=cpu, binding=b)
        assert project()['status'] == 'source-resolved'
        assert project(cpu='Q03UDV')['status'] == 'unresolved'
        assert project(cpu='FX3G')['status'] == 'unresolved'
        assert project(cpu='FX3S')['status'] == 'unresolved'
        for field, value in [('source_object_id', source['declaration']['variable_id']), ('top', 999),
                             ('left', 0), ('report_interface', 'raw-compiler'), ('record_size', 88)]:
            assert project({**report, field: value})['status'] == 'unresolved'
        ambiguous = deepcopy(binding)
        other = next(row for row in ambiguous['rows'].values() if row['name'] != source['declaration']['name'])
        other['record_id'] = source['declaration']['record_id']
        assert project(b=ambiguous)['status'] == 'unresolved'
    else:
        library, = [row for row in _native_validation_library_sources(raw)
                    if (row['library'], row['name']) == (source['library'], source['pou'])]
        from gxw.text_pou import parse_st_pou
        project = lambda r=report, cpu=result['cpu'], namespace=source['library']: correlate_native_compile_position(
            r, cpu=cpu, pou=source['pou'], library=namespace, program_kind=193,
            text=parse_st_pou(library['raw']).text, source_body_id=source['body_id'])
        assert project()['status'] == 'source-resolved'
        assert project(cpu='Q03UDV')['status'] == 'unresolved'
        assert project(namespace='')['status'] == 'unresolved'
        for field, value in [('source_object_id', source['body_id'][:-1] + [1]), ('top', 99999),
                             ('step', source['zero_based_line']), ('report_interface', 'raw-compiler')]:
            assert project({**report, field: value})['status'] == 'unresolved'


@pytest.mark.parametrize('case', ['q00j-production-tables', FX3U_DECLARATION_CONTROLS + 'baseline'])
def test_compile_declaration_record_reuse_preserves_context_but_keeps_ambiguous_position_unresolved(case):
    from copy import deepcopy
    from dataclasses import replace
    from gxw.native_diagnostics import (_native_validation_sources, bind_native_compile_declarations,
        correlate_native_compile_declaration, project_native_validation)
    raw, observation, snapshots = compile_location_case(case)
    _, sources = _native_validation_sources(raw)
    source, = [row for row in sources if row['name'] == 'PrgAxis1']
    document = source['declarations']
    position, = [row for row in document.rows if row.name == 'Position']
    other, = [row for row in document.rows if row.name == 'Velocity']
    reused = replace(other, record_id=position.record_id)
    source = {**source, 'declarations': replace(document, rows=tuple(reused if row.name == other.name else row for row in document.rows))}
    events = deepcopy(observation['validation']['events'])
    for event in events:
        if event.get('operation') == 'NativeDeclarationRead' and event['pou'] == 'PrgAxis1':
            row, = [row for row in event['rows'] if row['name'] == other.name]
            row['line'] = position.record_id
    result = project_native_validation(raw, observation, snapshots)
    binding = bind_native_compile_declarations([source], events=events,
        source_context=result['compilation']['source_context'])['', 'PrgAxis1']
    assert binding['status'] == 'current'
    report, = [row for row in result['compilation']['diagnostics']
               if row['name']['text'] == 'PrgAxis1' and row['top'] == position.record_id]
    assert correlate_native_compile_declaration(report, cpu=result['cpu'], binding=binding)['status'] == 'unresolved'


def test_authored_fx_menus_replay_each_native_entry_and_declaration_record_control(archived_native_requests):
    from research.freeze_gxw_authored_fbd_fx_menus import replay
    from gxw.native_diagnostics import (PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS,
                                       PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS)
    result = archived_native_requests(replay)
    assert result['complete_fx_menu_count'] == len(result['cases']) == len(result['controls']) == 10
    assert result['global_controls'] == len(PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS) == 4
    assert result['library_structure_controls'] == len(PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS) == 6
    assert result['unmapped_link_control']['compile_diagnostics'] == 24
    assert len(result['initial_insufficient_observations']) == 2


def test_current_compile_declaration_owners_require_current_fields_and_full_parent_chain():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import (_native_validation_declaration_owners,
        bind_native_compile_declaration_owners, project_native_validation)
    cases = [compile_location_case('declarations/product-' + name + '-line-77') for name in ('FX3U_FX3UC', 'FX1N_FX1NC')]
    originals = [(raw, observation['validation']['project'], observation['validation']['events'],
                  project_native_validation(raw, observation, snapshots)['compilation']['source_context'])
                 for raw, observation, snapshots in cases]
    changes = ['current', 'missing-before', 'missing-after', 'duplicate-read', 'late-before', 'project',
        'owner-parent', 'collection-parent', 'owner-kind', 'owner-identity', 'namespace-parent',
        'variable-parent', 'variable-kind', 'duplicate-variable', 'duplicate-variable-identity',
        'data_type', 'line', 'initial_value', 'comment', 'global-storage-or-namespace']

    @settings(max_examples=64, deadline=None, derandomize=True)
    @example(0, 'global-storage-or-namespace', 0, 'x')
    @example(1, 'namespace-parent', 0, 'x')
    @example(0, 'current', 2, 'x')
    @example(1, 'duplicate-variable-identity', 1, 'x')
    @given(st.integers(min_value=0, max_value=1), st.sampled_from(changes),
           st.integers(min_value=0, max_value=4), st.text(min_size=1, max_size=8))
    def check(index, damage, selected_row, extra):
        raw, project, original, context = originals[index]
        sources = _native_validation_declaration_owners(raw)
        role = 'global' if index == 0 else 'library-structure'
        events = deepcopy(original)
        selected = [row for row in events if row.get('operation') == 'NativeDeclarationOwnerRead' and row['role'] == role]
        before, = [row for row in selected if row['stage'] == 'before-build']
        after, = [row for row in selected if row['stage'] == 'after-build']
        variable = after['rows'][selected_row % len(after['rows'])]
        before['rows'].reverse(); after['rows'].reverse()
        if damage.startswith('missing-'):
            events.remove(before if damage == 'missing-before' else after)
        elif damage == 'duplicate-read':
            events.append(deepcopy(after))
        elif damage == 'late-before':
            events.remove(before); events.append(before)
        elif damage == 'project':
            after['project_id'][0] ^= 1
        elif damage in ('owner-parent', 'collection-parent'):
            after[damage.replace('-', '_')]['id'][0] ^= 1
        elif damage == 'owner-kind':
            after['owner']['read_type']['data_type'] ^= 1
        elif damage == 'owner-identity':
            after['owner']['id'][0] ^= 1
        elif damage == 'namespace-parent':
            after['collection_parent' if index == 0 else 'namespace_parent']['id'][0] ^= 1
        elif damage == 'variable-parent':
            variable['parent']['id'][0] ^= 1
        elif damage == 'variable-kind':
            variable['identity']['read_type']['data_type'] ^= 1
        elif damage == 'duplicate-variable':
            after['rows'].append(deepcopy(variable))
        elif damage == 'duplicate-variable-identity':
            other = next(row for row in after['rows'] if row['name'] != variable['name'])
            variable['identity']['id'] = other['identity']['id'][:]
        elif damage == 'line':
            variable['line'] += 100
        elif damage == 'global-storage-or-namespace':
            if index == 0:
                variable['devices'][0][0] += '\0' + extra
            else:
                after['namespace_object']['read_name']['name'] += '\0' + extra
        elif damage != 'current':
            variable[damage] += '\0' + extra
        bindings = bind_native_compile_declaration_owners(sources, project_id=project, events=events, source_context=context)
        assert len(bindings) == 2
        for key, value in bindings.items():
            assert value['status'] == ('unresolved' if key[0] == role and damage != 'current' else 'current')
    check()


@pytest.mark.parametrize('cpu,role', [('FX3U/FX3UC', 'global'), ('FX1N/FX1NC', 'library-structure')])
def test_compile_declaration_owner_scope_and_duplicate_records_keep_uncertain_positions_unresolved(cpu, role):
    from copy import deepcopy
    from dataclasses import replace
    from gxw.native_diagnostics import (_native_validation_declaration_owners, bind_native_compile_declaration_owners,
        correlate_native_compile_declaration_owner, project_native_validation)
    raw, observation, snapshots = compile_location_case('declarations/product-' + cpu.replace('/', '_') + '-line-77')
    actual = project_native_validation(raw, observation, snapshots)
    diagnostic = next(row for row in actual['compilation']['source_diagnostics'] if row['original']['top'] == 77)
    sources = _native_validation_declaration_owners(raw)
    events = deepcopy(observation['validation']['events'])
    project_id = observation['validation']['project']
    binding = next(value for key, value in bind_native_compile_declaration_owners(sources, project_id=project_id,
        events=events, source_context=actual['compilation']['source_context']).items() if key[0] == role)
    project = lambda r=diagnostic['original'], c=cpu, b=binding: correlate_native_compile_declaration_owner(r, cpu=c, binding=b)
    assert project()['status'] == 'source-resolved'
    assert project(c='Q03UDV')['status'] == 'unresolved'
    for field, value in [('source_object_id', diagnostic['source_projection']['source']['declaration']['variable_id']),
                         ('top', 999), ('left', 0), ('report_interface', 'raw-compiler'), ('record_size', 88), ('program_kind', 208)]:
        assert project({**diagnostic['original'], field: value})['status'] == 'unresolved'
    selected, = [row for row in sources if row['role'] == role]
    document = selected['declarations']; field = 'rows' if role == 'global' else 'members'
    original_rows = getattr(document, field)
    other = next(row for row in original_rows if row.record_id != 77)
    selected['declarations'] = replace(document, **{field: tuple(replace(row, record_id=77) if row.name == other.name else row for row in original_rows)})
    for row in events:
        if row.get('operation') == 'NativeDeclarationOwnerRead' and row['role'] == role:
            native, = [item for item in row['rows'] if item['name'] == other.name]
            native['line'] = 77
    ambiguous = next(value for key, value in bind_native_compile_declaration_owners(sources, project_id=project_id,
        events=events, source_context=actual['compilation']['source_context']).items() if key[0] == role)
    assert ambiguous['status'] == 'current'
    assert project(b=ambiguous)['status'] == 'unresolved'


def public_conversion_witness():
    from pathlib import Path
    import json
    return load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['boundary']


def original_sfc_public_witness():
    return load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['original_sfc_public']


def original_sfc_public_validation_case():
    prefix = 'sfc-original-public-Q06UDV/sfc-warning-check-validate_project/'
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-q-public-diagnostic-conversion-20261014.zip')) as archive:
        return (archive.read(prefix + 'input.gxw'), load_frozen_native_json(archive.read(prefix + 'observation.json')),
                {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                 if name.startswith(prefix) and name.endswith('.bin')})


def test_public_conversion_layout_failure_remains_separate_from_completed_vendor_check():
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = original_sfc_public_validation_case()
    report = next(report for event in observation['validation']['events'] if event.get('operation') == 'ProgramCheckOriginalReports'
                  for report in event['reports'] if report['kind'] == 2)
    report['arguments'][1]['text'] += 'changed'
    result = project_native_validation(raw, observation, snapshots)
    assert result['public_check'] == {'status': 'rejected', 'completed': True}
    assert result['current_source_check'] == 'established'
    assert result['diagnostic_projection']['status'] == 'conversion_incomplete'
    assert not result['public_diagnostic_conversion']['completed']
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']


def test_public_conversion_preserves_original_sfc_child_reports_without_iec_adaptation():
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = original_sfc_public_witness()
    result = _project_public_diagnostic_conversion(witness['result'], witness['observation'])
    assert result == witness['expected']
    errors = [row for row in result['reports'] if row['kind'] == 2]
    assert len(errors) == 2 and all(row['code'] == 0x050c9512 for row in errors)
    assert errors[0]['source_object_id'] != errors[1]['source_object_id']
    assert all(row['instance_kind'] == 6 and row['program_kind'] == 1 for row in errors)
    assert witness['result']['public_check'] == {'status': 'rejected', 'completed': True}


@pytest.mark.parametrize('damage', ['name-text', 'instance-bytes', 'argument-text', 'argument-missing-bytes',
                                  'bad-hex', 'null-with-data', 'boolean-code', 'large-step',
                                  'boolean-id', 'large-id', 'too-many-arguments'])
def test_public_conversion_rejects_inconsistent_original_public_layout_without_changing_vendor_state(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = original_sfc_public_witness()
    observation = deepcopy(witness['observation'])
    row = next(report for event in observation['validation']['events'] for report in event['reports'] if report['kind'] == 2)
    if damage == 'name-text': row['name']['text'] += 'changed'
    elif damage == 'instance-bytes': row['instance']['raw_hex_utf16le'] += 'ff'
    elif damage == 'argument-text': row['arguments'][0]['text'] += 'changed'
    elif damage == 'argument-missing-bytes': del row['arguments'][0]['raw_hex_utf16le']
    elif damage == 'bad-hex': row['name']['raw_hex_utf16le'] = 'not hex'
    elif damage == 'null-with-data': row['name']['is_null'] = True
    elif damage == 'boolean-code': row['code'] = True
    elif damage == 'large-step': row['step'] = 1 << 31
    elif damage == 'boolean-id': row['source_object_id'][2] = True
    elif damage == 'large-id': row['source_object_id'][2] = 1 << 32
    elif damage == 'too-many-arguments': row['arguments'] *= 65
    result = _project_public_diagnostic_conversion(witness['result'], observation)
    assert result['status'] == 'incomplete' and not result['completed'] and result['reports'] == []
    assert not result['public_check_promoted']
    assert witness['result']['public_check'] == {'status': 'rejected', 'completed': True}


def test_public_conversion_original_text_matches_frozen_utf16_for_every_report():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = original_sfc_public_witness()
    values = [(i, j, key, None) for i, event in enumerate(witness['observation']['validation']['events'])
              for j, report in enumerate(event['reports']) for key in ('name', 'instance')]
    values += [(i, j, 'arguments', k) for i, event in enumerate(witness['observation']['validation']['events'])
               for j, report in enumerate(event['reports']) for k in range(len(report['arguments']))]

    @settings(max_examples=30, derandomize=True, deadline=None)
    @given(st.sampled_from(values), st.text(alphabet=st.characters(blacklist_categories=('Cs',)), min_size=1, max_size=20))
    def check(position, suffix):
        observation = deepcopy(witness['observation'])
        i, j, key, index = position
        report = observation['validation']['events'][i]['reports'][j]
        text = report[key] if index is None else report[key][index]
        text['text'] += suffix
        assert not _project_public_diagnostic_conversion(witness['result'], observation)['completed']
        text['raw_hex_utf16le'] = text['text'].encode('utf-16le').hex()
        text['is_null'] = False
        result = _project_public_diagnostic_conversion(witness['result'], observation)
        assert result['completed'] and result['reports'][sum(len(event['reports']) for event in observation['validation']['events'][:i]) + j] == report
    check()


def test_complete_public_conversion_retains_every_native_report_and_original_failure():
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = public_conversion_witness()
    result = _project_public_diagnostic_conversion(witness['result'], witness['observation'])
    assert result == witness['expected']
    assert result['original_report_count'] == result['converted_report_count'] == 23
    assert sum(row['original']['kind'] == 2 for row in result['reports']) == 18
    assert all(row['report']['source_object_id'] == row['target'] for row in result['reports'] if row['original']['kind'] == 2)
    assert witness['result']['public_check'] == {'status': 'incomplete', 'completed': False}
    assert witness['result']['current_source_check'] == 'not_established'
    assert not result['public_check_promoted']


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'target', 'scope', 'lookup-type', 'lookup-parent',
    'lookup-id', 'source-id', 'original-name', 'name-bytes', 'arguments', 'code', 'position', 'version',
    'rva', 'failed', 'original-mutated', 'not-current', 'task-incomplete', 'unknown-cpu',
    'unadapted-resource', 'unadapted-zero-id'])
def test_public_conversion_cannot_complete_missing_stale_or_changed_native_reports(damage):
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = public_conversion_witness()
    proof, observation = witness['result'], witness['observation']
    events = observation['validation']['events']
    row = next(e for e in events if e.get('operation') == 'NativePublicDiagnosticConversion' and e['scope_adapted'])
    if damage == 'missing': events.remove(row)
    elif damage == 'duplicate': events.append(row)
    elif damage == 'target': row['target'] = [0] * 12
    elif damage == 'scope': row['report']['instance_kind'] = 6
    elif damage == 'lookup-type': row['resource_lookup']['requested_type'] = 32
    elif damage == 'lookup-parent': row['resource_lookup']['parent'] = [0] * 12
    elif damage == 'lookup-id': row['resource_lookup']['id'] = [0] * 12
    elif damage == 'source-id': row['report']['source_object_id'] = [0] * 12
    elif damage == 'original-name': row['original']['name']['text'] = 'FOREIGN'
    elif damage == 'name-bytes': row['report']['name']['raw_hex_utf16le'] = '4100'
    elif damage == 'arguments': row['report']['arguments'] = [{'text': 'lost', 'is_null': False, 'raw_hex_utf16le': '6c006f0073007400'}]
    elif damage == 'code': row['report']['code'] += 1
    elif damage == 'position': row['report']['step'] += 1
    elif damage == 'version': row['version'] = '1.635.0.2'
    elif damage == 'rva': row['converter_rva'] += 1
    elif damage == 'failed': row['hresult'] = -2147467259
    elif damage == 'original-mutated': row['original_report_unchanged'] = False
    elif damage == 'not-current': proof['current_raw_source_check'] = 'not_established'
    elif damage == 'task-incomplete': proof['raw_backend_check']['completed'] = False
    elif damage == 'unknown-cpu': proof['cpu'] = 'QFuture'
    elif damage in ('unadapted-resource', 'unadapted-zero-id'):
        row['scope_adapted'] = False
        row['report']['instance_kind'] = row['original']['instance_kind']
        if damage == 'unadapted-zero-id': row['report']['source_object_id'] = [0] * 12
    result = _project_public_diagnostic_conversion(proof, observation)
    assert not result['completed'] and result['status'] == 'incomplete'
    assert result['reports'] == [] and not result['public_check_promoted']


def test_public_conversion_order_and_native_field_changes_have_independent_oracles():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = public_conversion_witness()
    reports = [e for e in witness['observation']['validation']['events'] if e.get('operation') == 'NativePublicDiagnosticConversion']
    oracle = {(row['original']['poll'], row['original']['report_index']): row['report'] for row in witness['expected']['reports']}

    @settings(max_examples=30, derandomize=True, deadline=None)
    @given(st.permutations(range(len(reports))), st.integers(min_value=0, max_value=len(reports) - 1),
           st.sampled_from(['code', 'step', 'top', 'bottom', 'program_kind']), st.integers(min_value=1, max_value=0xffff))
    def check(order, selected, field, delta):
        observation = deepcopy(witness['observation'])
        retained = [e for e in observation['validation']['events'] if e.get('operation') != 'NativePublicDiagnosticConversion']
        rows = [deepcopy(reports[index]) for index in order]
        observation['validation']['events'] = retained + rows
        result = _project_public_diagnostic_conversion(witness['result'], observation)
        assert result['completed']
        assert {(row['original']['poll'], row['original']['report_index']): row['report'] for row in result['reports']} == oracle
        rows[selected]['report'][field] += delta
        assert not _project_public_diagnostic_conversion(witness['result'], observation)['completed']
    check()


def test_every_resource_diagnostic_requires_its_frozen_native_resource_identity():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import _project_public_diagnostic_conversion
    witness = public_conversion_witness()
    native_errors = [row for row in witness['expected']['reports'] if row['original']['kind'] == 2]
    identities = {tuple(row['target']) for row in native_errors}
    assert identities == {tuple(native_errors[0]['report']['source_object_id'])}

    @settings(max_examples=30, derandomize=True, deadline=None)
    @given(st.integers(min_value=0, max_value=len(native_errors)-1), st.booleans(),
           st.booleans(), st.integers(min_value=0, max_value=11), st.integers(min_value=1, max_value=0xffff))
    def check(selected, remove_adaptation, erase_identity, word, delta):
        observation = deepcopy(witness['observation'])
        expected = native_errors[selected]
        row, = [e for e in observation['validation']['events']
                if e.get('operation') == 'NativePublicDiagnosticConversion'
                and e['target'] == expected['target'] and e['original'] == expected['original']]
        if remove_adaptation:
            row['scope_adapted'] = False
            row['report']['instance_kind'] = row['original']['instance_kind']
        row['report']['source_object_id'] = [0] * 12 if erase_identity else list(expected['target'])
        if not erase_identity:
            row['report']['source_object_id'][word] ^= delta
        actual = _project_public_diagnostic_conversion(witness['result'], observation)
        assert not actual['completed'] and actual['reports'] == []
        assert witness['result']['public_check'] == {'status': 'incomplete', 'completed': False}
    check()


def test_successful_public_check_does_not_validate_a_same_named_unused_program():
    from pathlib import Path
    import json
    from gxw.native_diagnostics import _public_diagnostic_identity, _project_public_diagnostic_conversion
    witness = load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['public_identity_collision']
    identity = _public_diagnostic_identity(witness['observation'])
    assert identity['status'] == 'mismatched' and identity['checked_report_count'] == 2
    assert [row['original']['step'] for row in identity['mismatches']] == witness['expected']['steps']
    assert all(row['expected_object_id'] == witness['expected']['resource_id']
               and row['reported_object_id'] == witness['expected']['wrong_body_id']
               and row['original']['code'] == witness['expected']['error_code']
               for row in identity['mismatches'])
    conversion = _project_public_diagnostic_conversion(witness['result'], witness['observation'])
    assert not conversion['completed'] and conversion['reports'] == []
    assert witness['result']['public_check'] == {'status': 'rejected', 'completed': True}


def test_public_resource_identity_is_derived_from_native_rows_instead_of_mismatch_flags():
    from copy import deepcopy
    from pathlib import Path
    import json
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import _public_diagnostic_identity
    witness = load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['public_identity_collision']

    @settings(max_examples=30, derandomize=True, deadline=None)
    @given(st.integers(min_value=0, max_value=11), st.integers(min_value=1, max_value=0xffffffff), st.booleans())
    def check(word, delta, claim_valid):
        observation = deepcopy(witness['observation'])
        errors = [r for e in observation['validation']['events'] if e.get('operation') == 'ProgramCheckOriginalReports'
                  for r in e['reports'] if r.get('kind') == 2]
        for row in errors:
            row['instance_kind'] = 3
            row['source_object_id'] = list(witness['expected']['resource_id'])
        observation['validation']['public_diagnostic_identity_valid'] = claim_valid
        assert _public_diagnostic_identity(observation)['status'] == 'verified'
        errors[0]['source_object_id'][word] ^= delta
        actual = _public_diagnostic_identity(observation)
        assert actual['status'] == 'mismatched' and len(actual['mismatches']) == 1
    check()


def test_public_resource_identity_does_not_apply_iec_scope_rules_to_sfc_reports():
    from pathlib import Path
    import json
    from gxw.native_diagnostics import _public_diagnostic_identity
    witness = load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['public_identity_collision']
    for event in witness['observation']['validation']['events']:
        if event.get('operation') == 'OwnedCheckInput':
            for row in event['rows']:
                row['framing']['reader_module'] = 'DZDataABS_SICConverter.dll'
                row['framing']['reader_version'] = '1.635.0.1'
    result = _public_diagnostic_identity(witness['observation'])
    assert result['status'] == 'not_observed' and result['mismatches'] == []


def public_identity_recheck_witness():
    from pathlib import Path
    from zipfile import ZipFile
    import json
    expected = load_frozen_native_json((private_path('tests/fixtures/gxw_public_diagnostic_conversion.json')).read_bytes())['public_identity_collision']['expected']
    prefix = 'identity-recheck-Q06UDV/'
    with ZipFile(private_path('research/' + 'evidence/gxw-q-public-diagnostic-conversion-20261014.zip')) as archive:
        return (archive.read(prefix + 'input.gxw'), load_frozen_native_json(archive.read(prefix + 'observation.json')),
                {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()
                 if name.startswith(prefix) and name.endswith('.bin')}, expected)


def test_public_conversion_recovers_complete_diagnostics_after_successful_wrong_identity():
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, expected = public_identity_recheck_witness()
    result = project_native_validation(raw, observation, snapshots)
    assert result['public_check'] == {'status': 'rejected', 'completed': True}
    assert result['public_diagnostic_identity']['status'] == 'mismatched'
    assert result['diagnostic_projection']['status'] == 'identity_mismatch'
    assert result['current_source_check'] == 'not_established'
    assert result['current_raw_source_check'] == 'established'
    assert result['diagnostic_recheck']['trigger_reasons'] == ['public_resource_identity_mismatch']
    assert 'trigger_code' not in result['diagnostic_recheck']
    conversion = result['public_diagnostic_conversion']
    assert conversion['completed'] and conversion['converted_report_count'] == conversion['original_report_count'] == 7
    errors = [row['report'] for row in conversion['reports'] if row['original']['kind'] == 2]
    assert [row['step'] for row in errors] == expected['steps']
    assert all(row['source_object_id'] == expected['resource_id'] and row['instance_kind'] == 3 for row in errors)
    assert result['selection']['selected_sources'] == [expected['selected_pou']]
    projection = result['diagnostic_projection']
    assert len(projection['source_diagnostics']) == len(errors) == 2
    assert all(row['report_origin']['check_pass'] == 'raw-recheck' for row in projection['source_diagnostics'])
    assert [row['original']['source_object_id'] for row in projection['original_public_source_diagnostics']] == [expected['wrong_body_id']] * 2


@pytest.mark.parametrize('damage', ['original-input', 'task-terminal', 'code-change'])
def test_public_identity_recheck_still_requires_the_same_current_completed_task(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots, _ = public_identity_recheck_witness()
    events = observation['validation']['events']
    if damage == 'original-input':
        owned = next(row for row in events if row.get('check_pass') == 'public'
                     and row.get('operation') == 'OwnedCheckInput' and row.get('stage') == 'submitted')
        snapshots[owned['rows'][0]['file']] += b'changed input'
    elif damage == 'task-terminal':
        for row in events:
            if row.get('check_pass') == 'public' and row.get('operation') == 'ProgramCheckOriginalProgress':
                if row.get('percent') == 100: row['percent'] = 99
    else:
        first = next(index for index, row in enumerate(events) if row.get('check_pass') == 'raw-recheck')
        events.insert(first, {'operation': 'Compiler.Build', 'check_pass': 'raw-recheck', 'hresult': 0, 'code': 0})
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_source_check'] == 'not_established'
    assert result['diagnostic_recheck']['status'] == 'incomplete'
    assert not result['public_diagnostic_conversion']['completed']
@pytest.mark.parametrize('failure', [None, 'source_project_saved', 'import_incomplete'])
def test_library_adapter_uses_isolated_two_stage_protocol_and_rejects_incomplete_results(tmp_path, monkeypatch, failure):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import library_transfer_witness
    raw, donor, saved = library_transfer_witness()
    executable = tmp_path / 'adapter.exe'
    executable.write_bytes(b'isolated-test-placeholder')
    calls = []

    def run(_executable, request, root, timeout, error_type):
        operation = request['operation']
        calls.append(operation)
        assert (root / 'input.gxw').read_bytes() == (donor if operation == 'export_library' else raw)
        wire = adapter.workspace_business_request(request)
        assert wire['action'] == ('export_user_library' if operation == 'export_library' else 'import_user_library')
        native = ['OpenProjectEX2']
        if operation == 'export_library':
            (root / 'library.sul').write_bytes(b'frozen-native-library')
            native += ['Library.SetOwnedLibPath', 'Library.SaveUserLibrary']
            if failure == 'source_project_saved':
                native += ['SaveProject']
        else:
            assert (root / 'library.sul').read_bytes() == b'frozen-native-library'
            (root / 'candidate.gxw').write_bytes(saved)
            native += ['Library.CreateUserLibrary', 'SaveProject', 'ExportOneFileProject']
        observation = {'status': 'exported' if operation == 'export_library' else 'saved',
            'protocol_version': 1, 'native_version': '1.635.0.1', 'cpu': request['source']['cpu'],
            'compile': 'not_requested', 'check': 'not_requested',
            'calls': [dict(operation=name, hresult=0, code=0) for name in native]}
        if failure == 'import_incomplete' and operation == 'import_library':
            observation['status'] = 'failed'
        return 0, observation

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    workspace = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    if failure:
        with pytest.raises(adapter.WorkspaceSaveError):
            workspace.import_library(raw, donor, 'MotionControl_JEC_V100A')
        assert calls == (['export_library'] if failure == 'source_project_saved' else ['export_library', 'import_library'])
    else:
        result = workspace.import_library(raw, donor, 'MotionControl_JEC_V100A')
        assert result.data == saved
        assert len(result.report['native_workspace']['stages']) == 2
        assert result.report['validation']['gx_compile'] == 'not_run'


def test_authored_fb_mcp_retains_current_source_rejections_and_complete_final_check(archived_native_requests):
    from research.freeze_gxw_fb_authoring_mcp import replay
    result = archived_native_requests(replay)
    assert result['imported_user_libraries'] == 0 and result['definitions'] == 3
    assert result['callable_instances'] == 2 and result['retained_negative_checks'] == 2
    assert result['current_source_compile'] == 'accepted'
    assert result['public_check'] == 'completed-passed'
    assert result['compiler_diagnostics'] == {'0x500c2034':2}
    assert result['simulation'] == 'not_run'
    assert result['gui_save']['gui_project_tree'] == 'user_confirmed'
    assert result['gui_save']['gui_fb_ports'] == 'user_confirmed'
    assert result['gui_save']['prior_c2034'] == 2
    assert result['gui_save']['final_compiler_diagnostics'] == 0
    assert result['gui_save']['public_check'] == 'completed-passed'
    assert result['gui_save']['motion_program'] == 'not_reproduced'
    motion = result['parameter_motion']
    assert motion['current_source_compile'] == 'accepted'
    assert motion['public_check'] == 'completed-passed' and motion['saved_code'] == 'cold-matched'
    assert motion['gui_configuration']['status'] == 'missing'
    assert motion['gui_configuration']['missing'] == ['T']


@pytest.mark.parametrize('damage', [None, 'legacy_status', 'legacy_extra', 'current_missing',
                                   'current_source', 'current_difference', 'current_selector', 'other_evidence'])
def test_motion_replay_summary_allows_only_the_recorded_timer_projection_difference(damage):
    from copy import deepcopy
    from gxw.project_metadata import project_gui_configuration
    from research.freeze_gxw_fb_authoring_mcp import ARCHIVE, _verify_recorded_summary

    with zipfile.ZipFile(ARCHIVE) as archive:
        recorded = load_frozen_native_json(archive.read('summary.json'))
        current = deepcopy(recorded)
        gui = project_gui_configuration(archive.read('motion/versions/v0048/program.gxw'))
    current['parameter_motion']['gui_configuration'] = gui
    legacy = recorded['parameter_motion']['gui_configuration']
    if damage == 'legacy_status':
        legacy['status'] = 'not_verified'
    elif damage == 'legacy_extra':
        legacy['missing'] = ['D']
    elif damage == 'current_missing':
        gui['missing'].append('D')
    elif damage == 'current_source':
        gui['source'] = 'foreign.gpj'
    elif damage == 'current_difference':
        gui['differences']['D'] = {'workspace': [2000, 7999], 'gui': [512, 999]}
    elif damage == 'current_selector':
        gui['word_device'] = 1
    elif damage == 'other_evidence':
        current['parameter_motion']['public_check'] = 'not_requested'
    originals = deepcopy((current, recorded))
    if damage is None:
        _verify_recorded_summary(current, recorded)
        _verify_recorded_summary(current, current)
    else:
        with pytest.raises(AssertionError):
            _verify_recorded_summary(current, recorded)
    assert (current, recorded) == originals


@pytest.mark.parametrize('corruption', [None, 'raw_word', 'cleanup'])
def test_parameter_motion_simulator_evidence_rechecks_words_and_restoration(corruption):
    import json
    import zipfile
    from research.freeze_gxw_fb_authoring_mcp import ARCHIVE, verify_motion_execution

    with zipfile.ZipFile(ARCHIVE) as archive:
        def read(name):
            raw = archive.read(name)
            if name == 'motion/runtime/execution/monitor-value-cases/result.json' and corruption == 'raw_word':
                result = load_frozen_native_json(raw)
                plan = load_frozen_native_json(archive.read('motion/runtime/execution/monitor-value-cases/plan.json'))
                device = plan['fields']['axis1.MonTorque']['address']
                result['steps'][0]['samples'][0]['native'][device] ^= 1
                return json.dumps(result).encode()
            if name == 'motion/runtime/execution/fx3u-real-rounding-fixed-cases/result.json' and corruption == 'cleanup':
                result = load_frozen_native_json(raw)
                result['numeric_restore_verified'] = False
                return json.dumps(result).encode()
            return raw

        if corruption:
            with pytest.raises(AssertionError):
                verify_motion_execution(read)
        else:
            result = verify_motion_execution(read)
            assert result['status'] == 'bounded_simulator_passed'
            assert result['fixed_steps'] == 52 and result['raw_word_oracle_checks'] > 0
            assert result['actual_cpu_name'] == 'FX3UC' and result['actual_cpu_type'] == 243
            assert result['simulator_code_and_parameters'] == 'matches'
            assert result['inputs_restored'] and result['simulator_stopped'] and not result['program_changed']


@pytest.mark.parametrize('failed',[False,True])
def test_project_definition_adapter_is_version_bound_and_has_no_compile_side_effect(tmp_path,monkeypatch,failed):
    from copy import deepcopy
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import authored_fb_witness
    before, after, command, report = authored_fb_witness()
    executable = tmp_path/'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []
    def run(_exe,request,root,timeout,error_type):
        assert (root/'input.gxw').read_bytes()==before
        wire=adapter.workspace_business_request(request)
        assert wire['action']=='author_project_definition' and wire['project']['cpu']=='FX3U/FX3UC'
        assert wire['change']['st_text']==command['st_text']
        calls.append(request['operation'])
        (root/'candidate.gxw').write_bytes(after)
        observed=deepcopy(report['native_workspace'])
        if failed: observed['status']='failed'
        return int(failed),observed
    monkeypatch.setattr(adapter,'_run_native_adapter',run)
    workspace=adapter.NativeWorkspaceSourceSave(executable,tmp_path)
    if failed:
        with pytest.raises(adapter.WorkspaceSaveError): workspace.author(before,command)
    else:
        result=workspace.author(before,command)
        assert result.data==after and result.report['validation']['gx_compile']=='not_run'
    assert calls==['author_definition']


@pytest.mark.parametrize('create', [False, True])
@pytest.mark.parametrize('damage', [None, 'native_failed', 'wrong_source', 'missing_getter'])
def test_ordinary_st_program_adapter_preserves_offline_routing_and_verifies_the_result(tmp_path, monkeypatch, create, damage):
    from copy import deepcopy
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_object_model import background_st_program_witness
    before, saved, command, observed = background_st_program_witness(create)
    executable = tmp_path / 'adapter.exe'
    executable.write_bytes(b'not-executed')
    calls = []

    def run(actual_executable, request, root, timeout, error_type):
        assert actual_executable == executable
        assert (root / 'input.gxw').read_bytes() == before
        assert request['operation'] == 'author_definition' and request['source']['operation'] == command['operation']
        assert request['source']['programs'] and request['source']['tasks']
        assert request['source']['st_text'] == command['st_text']
        wire = adapter.workspace_business_request(request)
        assert wire['action'] == 'author_project_definition'
        assert wire['project'] == dict(cpu='FX3U/FX3UC', encoding=936)
        assert all('program_kind' not in row and 'pou_type' not in row for row in wire['change']['programs'])
        assert not {'process_id', 'window_handle', 'project_path'} & wire['change'].keys()
        calls.append(request['operation'])
        (root / 'candidate.gxw').write_bytes(before if damage == 'wrong_source' else saved)
        receipt = deepcopy(observed)
        if damage == 'native_failed': receipt['status'] = 'failed'
        elif damage == 'missing_getter':
            receipt['calls'] = [row for row in receipt['calls'] if row['operation'] != 'Definition.GetProgramLanguage']
        return int(damage == 'native_failed'), receipt

    monkeypatch.setattr(adapter, '_run_native_adapter', run)
    workspace = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    if damage:
        with pytest.raises(adapter.WorkspaceSaveError):
            workspace.author(before, command)
    else:
        result = workspace.author(before, command)
        assert result.data == saved
        assert result.report['definition_after']['kind'] == 'program'
        assert result.report['validation']['gx_compile'] == 'not_run'
    assert calls == ['author_definition']


@pytest.mark.parametrize('patch', [
    {'resource': 'Missing'}, {'task': 'Missing'}, {'scope_mode': 'new_task'},
    {'declarations': [{'name': 'Instance', 'data_type': 'MissingFB', 'kind': 'function_block'}]},
])
def test_ordinary_st_program_adapter_preflight_rejects_before_starting_native_work(tmp_path, monkeypatch, patch):
    import gxworks2.workspace_adapter as adapter
    from gxw.models import GXWFormatError
    from tests.test_gxw_object_model import background_st_program_witness
    before, _, command, _ = background_st_program_witness(True)
    executable = tmp_path / 'adapter.exe'
    executable.write_bytes(b'not-executed')
    monkeypatch.setattr(adapter, '_run_native_adapter', lambda *_: pytest.fail('Preflight must not start a native process'))
    workspace = adapter.NativeWorkspaceSourceSave(executable, tmp_path)
    with pytest.raises(GXWFormatError):
        workspace.author(before, {**command, **patch})


def native_request_semantics(source, *, operation=None):
    """Project old frozen requests onto the current Core input contract."""
    selectors = {'lexical_reader', 'compilation_method'} if 'programs' in source else set()
    if operation != 'read_sfc_graph' and source.get('graph_profile') == {'family': 'q-l'}:
        selectors.add('graph_profile')
    return {key: value for key, value in source.items() if key not in selectors}


def _frozen_native_replays(monkeypatch, *, project_requests):
    """Compare frozen request semantics after the native ABI selectors moved to C#.

    Each replay module explicitly decodes frozen JSON evidence and projects old
    request selectors. Archive bytes, source identities and diagnostic assertions
    remain unchanged.
    """
    import sys
    from types import FunctionType, SimpleNamespace

    def semantic_request(payload, *args, **kwargs):
        value = load_frozen_native_json(payload, *args, **kwargs)
        source = value.get('source') if isinstance(value, dict) else None
        if project_requests and isinstance(source, dict):
            # Legacy transport selectors are no longer Core inputs.
            # Keep every other request field for the replay's exact comparison.
            value['source'] = native_request_semantics(source, operation=value.get('operation'))
        return value

    def replay_with_current_request(replay, *args, document_projection=None, **kwargs):
        def load(payload, *load_args, **load_kwargs):
            value = semantic_request(payload, *load_args, **load_kwargs)
            return document_projection(value) if document_projection else value

        pending = [replay.__module__]
        visited = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            module = sys.modules[name]
            if hasattr(module, 'json'):
                monkeypatch.setattr(module, 'json', SimpleNamespace(
                    loads=load, dumps=json.dumps))
            # A replay may import another frozen verifier as its independent
            # control; give that verifier the same offline evidence and request projection.
            pending.extend(value.__module__ for value in vars(module).values()
                           if isinstance(value, FunctionType)
                           and value.__module__.startswith('research.'))
        return replay(*args, **kwargs)

    return replay_with_current_request


@pytest.fixture
def archived_native_evidence(monkeypatch):
    """Decode frozen evidence without changing historical request comparisons."""
    return _frozen_native_replays(monkeypatch, project_requests=False)


@pytest.fixture
def archived_native_requests(monkeypatch):
    """Decode evidence and compare requests after moving ABI selectors to C#."""
    return _frozen_native_replays(monkeypatch, project_requests=True)

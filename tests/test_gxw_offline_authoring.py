"""Existing ST/label transactions over an independently saved native project."""
from copy import deepcopy
from functools import lru_cache
import zipfile

from hypothesis import given, settings, strategies as st
import pytest

from gxw.container_writer import validate_cfb_streams
from gxw.declarations import parse_declarations, serialize_label
from gxw.fb_authoring import _context, _rows
from gxw.integrity import read_project_hdb
from gxw.models import GXWFormatError
from gxw.offline_authoring import prepare_existing_definition_write, write_existing_definition
from gxw.project_metadata import logical_mapping
from gxw.project_writer import write_prepared_project, write_project_replacements


def test_multi_source_draft_keeps_invalid_body_and_other_edits_then_accepts_a_repair(monkeypatch):
    from gxw.offline_edit import edit_offline_project
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('native launch')))
    raw = native_project()
    before = payloads(raw)
    edits = [dict(source_name='CalcOutput_ST.Program.pou', command=dict(
        st_text='Counter := ;', declarations=[dict(name='Counter', data_type='INT')])),
        dict(source_name='ScaleValue.Program.pou', command=dict(
            st_text='IF Enabled THEN OutValue := InValue * Gain + 29; ELSE OutValue := 0; END_IF;'))]
    result = edit_offline_project(raw, edits)
    saved = payloads(result.data)
    assert parse_st_pou(saved['CalcOutput_ST.Program.pou']).text == 'Counter := ;'
    assert '29' in parse_st_pou(saved['ScaleValue.Program.pou']).text
    assert result.report['artifact_state'] == 'draft' and result.report['save_policy'] == 'draft'
    assert any(finding['severity'] == 'error' for check in result.report['source_checks']
               for finding in check.get('findings', []))
    targets = {'CalcOutput_ST.Program.pou', 'CalcOutput_ST.Labels.lh', 'ScaleValue.Program.pou'}
    assert all(saved[name] == value for name, value in before.items() if name not in targets)
    assert result.report['invalidated_results']['compilation'] == 'stale'
    repaired = edit_offline_project(result.data, [dict(source_name='CalcOutput_ST.Program.pou',
        command=dict(st_text='Counter := 27;'))], save_policy='checked')
    assert parse_st_pou(payloads(repaired.data)['CalcOutput_ST.Program.pou']).text == 'Counter := 27;'
    assert '29' in parse_st_pou(payloads(repaired.data)['ScaleValue.Program.pou']).text
    assert payloads(raw) == before


@pytest.mark.parametrize('problem', ['checked_error', 'duplicate_source', 'late_failure', 'foreign_source'])
def test_multi_source_invalid_transactions_do_not_produce_a_partial_project(problem):
    from gxw.offline_edit import edit_offline_project
    raw = native_project()
    before = payloads(raw)
    edits = [dict(source_name='CalcOutput_ST.Program.pou', command=dict(st_text='Counter := 28;'))]
    kwargs = {}
    if problem == 'checked_error':
        edits[0]['command']['st_text'] = 'Counter := ;'
        kwargs['save_policy'] = 'checked'
    elif problem == 'duplicate_source':
        edits.append(dict(source_name='CalcOutput_ST', command=dict(st_text='Counter := 29;')))
    elif problem == 'late_failure':
        edits.append(dict(source_name='ScaleValue.Program.pou', command=dict(
            st_text='OutValue := 1;\x00')))
    else:
        kwargs['expected_sources'] = {'CalcOutput_ST.Program.pou': b'foreign'}
    with pytest.raises(GXWFormatError):
        edit_offline_project(raw, edits, **kwargs)
    assert payloads(raw) == before


def test_fbd_joint_st_interface_body_and_two_callers_use_the_final_inout_contract():
    """A controlled second FBD body over existing identities; no native claim."""
    from gxw.offline_edit import edit_offline_project
    from gxw.offline_project import read_offline_project
    from gxw.source_header import source_payload_offset
    from gxw.object_model import read_project_context
    raw = native_project()
    project = read_offline_project(raw)
    source = 'CalcOutput_ST.Program.pou'
    old, graph = project.streams[source], project.streams['POU_01.Program.pou']
    converted = old[:source_payload_offset(old)] + graph[source_payload_offset(graph):]
    raw = write_project_replacements(raw, {source:(project.mapping[source], old, converted)}).data
    _, _, _, sources, _ = _context(raw)
    declarations = _rows(sources.declarations['ScaleValue.Labels.lh'], sources)
    for row in declarations:
        if row['name'] == 'InValue':
            row['class_name'] = 'VAR_IN_OUT'
    callers = ['POU_01.Program.pou', source]
    def caller_edits(actuals):
        edits = []
        for name,actual in zip(callers,actuals):
            call = dict(id='scale', type='ScaleValue', instance='ScaleInst',
                        arguments=dict(Enabled='TRUE',InValue=actual,OutValue='D40'))
            model = dict(program=name, networks=[dict(calls=[call])])
            edits.append(dict(source_name=name, command=dict(operation='edit', model=model)))
        return edits
    change = dict(source_name='ScaleValue.Program.pou', command=dict(operation='edit_st_fb',
        st_text='OutValue := InValue;', declarations=declarations))
    first = edit_offline_project(raw, [change, *caller_edits(['K4','K5'])], save_policy='checked')
    for name in callers:
        context = read_project_context(first.data, name)
        call = next(node for node in context.program.nodes if node.kind.value=='function_block')
        assert context.sources.inout_output_usage(call, 'InValue') == (False, 'st')
        assert not any(node.kind.value=='output' and node.symbol in ('K4','K5') for node in context.program.nodes)
    body = dict(source_name='ScaleValue.Program.pou', command=dict(operation='edit_st_fb',
        st_text='InValue := InValue + 1; OutValue := InValue;'))
    with pytest.raises(GXWFormatError) as failure:
        edit_offline_project(first.data, [body], save_policy='checked')
    assert {row['source_name'] for row in failure.value.findings if row['code']=='fbd_inout_constant'} == set(callers)
    final = edit_offline_project(first.data, [body, *caller_edits(['D0','D20'])], save_policy='checked')
    assert 'InValue := InValue + 1' in parse_st_pou(payloads(final.data)['ScaleValue.Program.pou']).text
    assert not any(finding['severity']=='error' for check in final.report['source_checks']
                   for finding in check.get('introduced_errors', []))
from gxw.text_pou import parse_st_pou
from tools.offline_inputs import private_path


@lru_cache(maxsize=1)
def native_project():
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-visible-st-program-creation-20261017.zip')) as source:
        # Native GUI-created Program, native-authored ScaleValue FB, globals,
        # and an existing task. This is not a source-body replay fixture.
        return source.read('015-task-replace/saved.gxw')


def payloads(raw):
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = read_project_hdb(outer).payloads
    return {name: nested[stream] for name, stream in mapping.items() if stream in nested}


def labels(raw, logical):
    return parse_declarations(payloads(raw)[logical], logical_name=logical)


def test_st_program_body_and_new_local_rows_form_one_offline_transaction(monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('offline authoring attempted to launch a native process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    raw = native_project()
    original = payloads(raw)
    command = dict(operation='edit_st_program', source_name='CalcOutput_ST.Program.pou',
        st_text='Counter := 3;\r\nCounter := Counter + 1;\r\n', declarations=[dict(name='Counter', data_type='INT')])
    supplied = deepcopy(command)
    prepared = prepare_existing_definition_write(raw, command,
        expected_sources={name: original[name] for name in ('CalcOutput_ST.Program.pou', 'CalcOutput_ST.Labels.lh')})
    assert command == supplied and prepared.baseline == raw
    assert set(prepared.replacements) == {'CalcOutput_ST.Program.pou', 'CalcOutput_ST.Labels.lh'}
    result = write_prepared_project(prepared)
    saved = payloads(result.data)
    assert parse_st_pou(saved['CalcOutput_ST.Program.pou']).text == command['st_text']
    assert [(row.name, row.data_type) for row in labels(result.data, 'CalcOutput_ST.Labels.lh').rows] == [('Counter', 'INT')]
    assert all(saved[name] == value for name, value in original.items()
               if name not in {'CalcOutput_ST.Program.pou', 'CalcOutput_ST.Labels.lh'})
    before_outer, after_outer = validate_cfb_streams(raw), validate_cfb_streams(result.data)
    assert before_outer['projectdatalist.xml'] == after_outer['projectdatalist.xml']
    assert result.report['validation']['compile_state'] == 'stale'
    checks = prepared.objects[0]['offline_authoring']['source_checks']
    selected = next(row for row in checks if row['source'] == 'CalcOutput_ST.Program.pou')
    assert selected['syntax'] == 'passed' and selected['coverage']['project_context_bound']
    assert selected['native_compile'] == 'unverified' and selected['execution'] == 'not_run'


def test_st_fb_body_edit_preserves_native_formals_labels_and_all_other_source_streams():
    raw = native_project()
    original = payloads(raw)
    text = 'IF Enabled THEN\r\n OutValue := InValue * Gain + 17;\r\nELSE OutValue := 0; END_IF;\r\n'
    result = write_existing_definition(raw, dict(operation='edit_st_fb', source_name='scalevalue', st_text=text))
    saved = payloads(result.data)
    assert parse_st_pou(saved['ScaleValue.Program.pou']).text == text
    assert saved['ScaleValue.Labels.lh'] == original['ScaleValue.Labels.lh']
    assert all(saved[name] == value for name, value in original.items() if name != 'ScaleValue.Program.pou')
    assert result.report['definition_after']['name'] == 'ScaleValue'
    assert result.report['source_checks'][0]['syntax'] == 'passed'


def test_complete_fb_declarations_add_remove_local_rows_without_recreating_formals():
    raw = native_project()
    _, _, _, sources, _ = _context(raw)
    old = sources.declarations['ScaleValue.Labels.lh']
    rows = [row for row in _rows(old, sources) if row['name'] != 'Gain']
    rows.append(dict(name='Scratch', data_type='INT', comment='离线新增的局部变量'))
    prepared = prepare_existing_definition_write(raw, dict(operation='edit_st_fb', source_name='ScaleValue',
        declarations=rows, st_text='Scratch := 2; IF Enabled THEN OutValue := InValue * Scratch; ELSE OutValue := 0; END_IF;'))
    assert prepared.declarations[old.logical_name].header == old.header
    result = write_prepared_project(prepared)
    changed = labels(result.data, old.logical_name)
    assert {row.name for row in changed.rows} == {'Enabled', 'InValue', 'OutValue', 'Scratch'}
    original = {row.name: row for row in old.rows}
    assert all(serialize_label(row) == serialize_label(original[row.name]) for row in changed.rows if row.name != 'Scratch')
    assert changed.header[:50] == old.header[:50] and changed.header[54:] == old.header[54:]
    assert changed.trailer == old.trailer
    assert changed.owner_pou_type == old.owner_pou_type
    assert payloads(result.data)['POU_01.Program.pou'] == payloads(raw)['POU_01.Program.pou']


def test_global_upsert_and_same_table_removal_preserve_addresses_and_unknown_fields():
    raw = native_project()
    before = labels(raw, 'Global1.gh')
    result = write_existing_definition(raw, dict(operation='edit_global_labels', source_name='Global1',
        declarations=[dict(name='ExtraOffline', data_type='INT', device='D100', comment='new')]))
    changed = labels(result.data, 'Global1.gh')
    added = next(row for row in changed.rows if row.name == 'ExtraOffline')
    assert added.device == 'D100' and added.iec_address
    assert all(serialize_label(row) == serialize_label(before.rows[index]) for index, row in enumerate(changed.rows[:-1]))
    removed = write_existing_definition(result.data, dict(operation='edit_global_labels', source_name='Global1.gh',
        declarations=[], remove_declarations=['ExtraOffline']))
    final = labels(removed.data, 'Global1.gh')
    assert [serialize_label(row) for row in final.rows] == [serialize_label(row) for row in before.rows]
    assert payloads(removed.data)['ScaleValue.Program.pou'] == payloads(raw)['ScaleValue.Program.pou']


def test_pou_label_upserts_removals_keep_the_native_st_body_and_source_identity():
    raw = native_project()
    result = write_existing_definition(raw, dict(operation='edit_pou_labels', source_name='CalcOutput_ST.Labels.lh',
        declarations=[dict(name='Unused', data_type='INT')]))
    assert payloads(result.data)['CalcOutput_ST.Program.pou'] == payloads(raw)['CalcOutput_ST.Program.pou']
    removed = write_existing_definition(result.data, dict(operation='edit_pou_labels', source_name='CalcOutput_ST',
        declarations=[], remove_declarations=['unused']))
    assert labels(removed.data, 'CalcOutput_ST.Labels.lh').rows == ()
    assert payloads(removed.data)['CalcOutput_ST.Program.pou'] == payloads(raw)['CalcOutput_ST.Program.pou']


@pytest.mark.parametrize('operation', ['create_st_program', 'create_st_fb', 'create_structure', 'create_global_labels', 'configure_project'])
def test_offline_definition_entry_never_creates_engineering_objects(operation):
    with pytest.raises(GXWFormatError, match='existing'):
        prepare_existing_definition_write(native_project(), dict(operation=operation, source_name='New'))


@pytest.mark.parametrize('text', ['Counter := ;', 'Counter := TRUE;'])
def test_invalid_st_is_rejected_before_preparing_any_project_output(text):
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        prepare_existing_definition_write(native_project(), dict(operation='edit_st_program', source_name='CalcOutput_ST',
            st_text=text, declarations=[dict(name='Counter', data_type='INT')]))


@pytest.mark.parametrize('text', ['Counter := ;', 'Counter := TRUE;'])
def test_definition_draft_saves_invalid_body_with_current_diagnostics(text, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('draft save launched native code'))
    raw = native_project()
    before = payloads(raw)
    command = dict(operation='edit_st_program', source_name='CalcOutput_ST', st_text=text,
        declarations=[dict(name='Counter', data_type='INT')])
    result = write_existing_definition(raw, command, save_policy='draft')
    saved = payloads(result.data)
    assert parse_st_pou(saved['CalcOutput_ST.Program.pou']).text == text
    assert labels(result.data, 'CalcOutput_ST.Labels.lh').rows[0].name == 'Counter'
    assert all(saved[name] == value for name, value in before.items()
               if name not in ('CalcOutput_ST.Program.pou', 'CalcOutput_ST.Labels.lh'))
    check = next(row for row in result.report['source_checks'] if row['source'] == 'CalcOutput_ST.Program.pou')
    assert check['status'] == 'failed' and check['introduced_errors']
    assert result.report['save_policy'] == 'draft'
    assert result.report['validation']['compile_state'] == 'stale'
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        prepare_existing_definition_write(raw, command, save_policy='checked')


def test_definition_draft_keeps_format_source_and_scope_boundaries():
    raw = native_project()
    command = dict(operation='edit_st_program', source_name='CalcOutput_ST', st_text='Counter := ;')
    with pytest.raises(GXWFormatError, match='stale or foreign'):
        prepare_existing_definition_write(raw, command, save_policy='draft',
            expected_sources={'CalcOutput_ST.Program.pou': b'foreign'})
    with pytest.raises(GXWFormatError):
        prepare_existing_definition_write(raw[:-700], command, save_policy='draft')
    with pytest.raises(GXWFormatError, match='FUNCTION'):
        prepare_existing_definition_write(raw, dict(operation='edit_pou_labels', source_name='CalcOutput_ST',
            return_type='BOOL'), save_policy='draft')
    with pytest.raises(GXWFormatError, match='save_policy'):
        prepare_existing_definition_write(raw, command, save_policy='ignore')


@pytest.mark.parametrize('initial', ['TRUE', '[bad'])
def test_definition_initializer_errors_are_draft_diagnostics_and_checked_failures(initial):
    raw = native_project()
    command = dict(operation='edit_global_labels', source_name='Global1',
        declarations=[dict(name='InitScratch', data_type='INT', initial_value=initial)])
    result = write_existing_definition(raw, command, save_policy='draft')
    assert next(row for row in labels(result.data, 'Global1.gh').rows if row.name == 'InitScratch').initial_value == initial
    check = next(row for row in result.report['source_checks'] if row['source'] == 'Global1.gh')
    assert check['status'] == 'failed' and any(row['name'] == 'InitScratch' and row['severity'] == 'error' for row in check['findings'])
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        prepare_existing_definition_write(raw, command, save_policy='checked')


def test_definition_lightweight_context_retains_ownership_without_building_graph_execution(monkeypatch):
    from gxw.st_program import project_context_snapshot
    import gxw.object_model as objects
    monkeypatch.setattr(objects, 'read_project_context', lambda *a, **k: pytest.fail('static context built a graph execution projection'))
    context = project_context_snapshot(native_project(), include_graphs=False)
    st_source = next(row for row in context['pous'] if row['program'] == 'CalcOutput_ST.Program.pou')
    assert st_source['labels'] == 'CalcOutput_ST.Labels.lh' and st_source['language'] == 'st'
    assert all(row['source_status'] == 'interface_only' for row in context['pous'] if row['language'] == 'fbd')
    assert context['tasks'] and context['configuration']['compile_options']


def test_definition_user_library_st_body_and_local_declarations_share_the_offline_transaction(monkeypatch):
    import subprocess
    from gxw.offline_project import read_offline_project
    from gxw.project_metadata import project_stream_mapping
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('library draft attempted native execution'))
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-source-compile-status-20261021.zip')) as archive:
        raw = archive.read('header-pending-q13udv-v2/original.gxw')
    before = read_offline_project(raw)
    logical, table = r'HMI05.Program\HMI.lnb', r'HMI05.Labels\HMI.lnl'
    result = write_existing_definition(raw, dict(operation='edit_st_source', source_name=logical,
        st_text='OfflineDraft := ;'), save_policy='draft')
    from gxw.offline_authoring import prepare_declaration_edits
    prepared, _ = prepare_declaration_edits(result.data, {table: dict(upserts=[dict(
        name='OfflineDraft', data_type='ARRAY[0..2] OF INT', initial_value='[3(7)]')])}, save_policy='draft')
    edited = write_prepared_project(prepared)
    after = read_offline_project(edited.data)
    assert parse_st_pou(after.streams[logical]).text == 'OfflineDraft := ;'
    document = parse_declarations(after.streams[table], logical_name=table)
    assert (document.rows[-1].name, document.rows[-1].data_type) == ('OfflineDraft', 'ARRAY[0..2] OF INT')
    from gxw.project_metadata import existing_source_groups, mark_observed_source_compile_pending
    groups, _ = existing_source_groups(before.outer['projectdatalist.xml'], before.streams)
    for name, value in before.streams.items():
        if name in (logical, table):
            continue
        expected = mark_observed_source_compile_pending(value, logical_name=name) if name in groups else value
        assert after.streams[name] in (value, expected)
    assert after.mapping == before.mapping == project_stream_mapping(before.outer)
    assert any(check['source'] == logical and check['status'] == 'failed' for check in result.report['source_checks'])


def test_stale_or_foreign_current_payload_is_rejected_even_for_a_valid_command():
    with pytest.raises(GXWFormatError, match='stale or foreign'):
        prepare_existing_definition_write(native_project(), dict(operation='edit_st_program', source_name='CalcOutput_ST',
            st_text='Y0 := X0;'), expected_sources={'CalcOutput_ST.Program.pou': b'foreign source'})


def test_unresolved_shared_semantic_coverage_remains_explicit_without_a_native_claim():
    prepared = prepare_existing_definition_write(native_project(), dict(operation='edit_st_program',
        source_name='CalcOutput_ST', st_text='UnboundName := 5;'))
    report = next(row for row in prepared.objects[0]['offline_authoring']['source_checks']
                  if row['source'] == 'CalcOutput_ST.Program.pou')
    assert report['status'] == 'unverified'
    assert any(finding['severity'] == 'unverified' for finding in report['findings'])
    assert report['native_compile'] == 'unverified' and report['execution'] == 'not_run'


def test_complete_interface_removal_cannot_leave_existing_fbd_callers_bound_to_old_ports():
    raw = native_project()
    _, _, _, sources, _ = _context(raw)
    declarations = [row for row in _rows(sources.declarations['ScaleValue.Labels.lh'], sources) if row['name'] != 'Enabled']
    with pytest.raises(GXWFormatError, match='FBD binding|interface'):
        prepare_existing_definition_write(raw, dict(operation='edit_st_fb', source_name='ScaleValue',
            st_text='OutValue := InValue * Gain;', declarations=declarations))


def test_complete_declaration_list_preserves_order_and_reports_affected_callers():
    raw = native_project()
    _, _, _, sources, _ = _context(raw)
    document = sources.declarations['ScaleValue.Labels.lh']
    rows = list(reversed(_rows(document, sources)))
    command = dict(operation='edit_st_fb', source_name='ScaleValue',
        st_text=parse_st_pou(payloads(raw)['ScaleValue.Program.pou']).text, declarations=rows)
    result = write_existing_definition(raw, command, save_policy='draft')
    edited = labels(result.data, document.logical_name)
    assert [row.name for row in sorted(edited.rows, key=lambda row: row.record_id)] == [row['name'] for row in rows]
    assert {row.name: (row.unknown_text, row.comment, row.value_extension) for row in edited.rows} == {
        row.name: (row.unknown_text, row.comment, row.value_extension) for row in document.rows}
    assert any(row['source'] == 'POU_01.Program.pou' and row['status'] == 'failed'
               for row in result.report['source_checks'])
    with pytest.raises(GXWFormatError, match='joint caller graph'):
        prepare_existing_definition_write(raw, command)


def test_deleting_a_used_fb_instance_is_rejected_instead_of_auto_recreating_it():
    with pytest.raises(GXWFormatError, match='graph reference'):
        prepare_existing_definition_write(native_project(), dict(operation='edit_pou_labels', source_name='POU_01',
            declarations=[], remove_declarations=['ScaleInst']))


def test_deleting_a_used_st_local_cannot_be_downgraded_to_an_unverified_free_identifier():
    with pytest.raises(GXWFormatError, match='ST reference'):
        prepare_existing_definition_write(native_project(), dict(operation='edit_pou_labels', source_name='ScaleValue',
            declarations=[], remove_declarations=['Gain']))


@pytest.mark.parametrize('remove', [['Enabled', 'enabled'], ['Enabled']])
def test_conflicting_or_referenced_local_removals_are_rejected(remove):
    command = dict(operation='edit_pou_labels', source_name='ScaleValue', declarations=[], remove_declarations=remove)
    with pytest.raises(GXWFormatError):
        prepare_existing_definition_write(native_project(), command)


def test_noop_st_source_is_a_byte_identical_project_and_never_refreshes_compile_results():
    raw = native_project()
    body = parse_st_pou(payloads(raw)['ScaleValue.Program.pou']).text
    result = write_existing_definition(raw, dict(operation='edit_st_fb', source_name='ScaleValue', st_text=body))
    assert result.data == raw
    assert result.report['validation']['compile_state'] == 'unchanged'
    assert result.report['native_compile'] == 'not_run' and result.report['execution'] == 'not_run'


def test_project_transaction_checks_an_explicit_unchanged_draft_without_rewriting_it():
    from gxw.offline_edit import edit_offline_project
    invalid = edit_offline_project(native_project(), [dict(source_name='CalcOutput_ST.Program.pou',
        command=dict(st_text='Counter := ;', declarations=[dict(name='Counter', data_type='INT')]))])
    result = edit_offline_project(invalid.data, [dict(source_name='CalcOutput_ST.Program.pou',
        command=dict(st_text='Counter := ;'))])
    assert result.data == invalid.data
    assert result.report['validation']['compile_state'] == 'unchanged'
    assert result.report['artifact_state'] == 'draft'
    selected = [check for check in result.report['source_checks']
                if check['source'] == 'CalcOutput_ST.Program.pou']
    assert selected and any(finding['severity'] == 'error'
                            for check in selected for finding in check['findings'])


@settings(max_examples=6, deadline=None, derandomize=True, database=None)
@given(comment=st.text(alphabet='ABC中文\r\n😀', max_size=80))
def test_local_comment_edits_preserve_native_storage_and_revert_exact_logical_bytes(comment):
    raw = native_project()
    old = labels(raw, 'ScaleValue.Labels.lh')
    gain = next(row for row in old.rows if row.name == 'Gain')
    command = dict(operation='edit_pou_labels', source_name='ScaleValue',
        declarations=[dict(name='Gain', data_type='INT', comment=comment)])
    prepared = prepare_existing_definition_write(raw, command)
    document = prepared.declarations[old.logical_name]
    current = next(row for row in document.rows if row.name == 'Gain')
    assert (current.record_id, current.unknown_u32, current.unknown_text, current.value_extension,
            current.type_code, current.type_reference, current.array_marker, current.device, current.iec_address) == (
            gain.record_id, gain.unknown_u32, gain.unknown_text, gain.value_extension,
            gain.type_code, gain.type_reference, gain.array_marker, gain.device, gain.iec_address)
    assert all(serialize_label(row) == serialize_label(previous) for row, previous in zip(document.rows, old.rows)
               if row.name != 'Gain')
    assert current.comment == comment


def test_prepared_transaction_rejects_rebased_source_at_final_writer_boundary():
    raw = native_project()
    prepared = prepare_existing_definition_write(raw, dict(operation='edit_st_program', source_name='CalcOutput_ST',
        st_text='D0 := 1;'))
    name = 'CalcOutput_ST.Program.pou'
    stream, old, new = prepared.replacements[name]
    changed = write_project_replacements(raw, {name: (stream, old, new)}).data
    with pytest.raises(GXWFormatError, match='stale or foreign'):
        write_project_replacements(changed, prepared.replacements)


@pytest.mark.parametrize('case', ['warehouse', 'sfc-object', 'sfc-retention'])
def test_existing_ladder_sfc_local_table_edits_preserve_owner_properties_tasks_and_mark_source_pending(case):
    from gxw.offline_project import read_offline_project
    from gxw.sfc_pou import read_sfc_project_context
    if case == 'warehouse':
        raw = private_path('research/fixtures/gxw-offline/warehouse/warehouse.gxw').read_bytes()
        logical, table, kind = 'MAIN.Program.pou', 'MAIN.Labels.lh', 1
    else:
        archive, member = ({'sfc-object': ('gxw-sfc-object-edit-20261013.zip', 'shared-save/input.gxw'),
            'sfc-retention': ('gxw-sfc-application-graph-20261013.zip', 'retention-application/input.gxw')}[case])
        with zipfile.ZipFile(private_path('research/' + 'evidence/' + archive)) as source:
            raw = source.read(member)
        logical, table, kind = 'Block.Program.pou', 'Block.Labels.lh', 240
    before = read_offline_project(raw)
    original = parse_declarations(before.streams[table], logical_name=table, source_kind=kind)
    prepared = prepare_existing_definition_write(raw, dict(operation='edit_pou_labels', source_name=logical,
        declarations=[dict(name='OfflineLocal', data_type='INT', comment='现有表新增行')]))
    draft = prepared.declarations[table]
    assert draft.owner_name == original.owner_name and draft.owner_pou_type == original.owner_pou_type
    assert draft.owner_sfc_block_number == original.owner_sfc_block_number
    if kind == 240:
        assert draft.count_offset == original.count_offset == 130
        assert draft.header[90:130] == original.header[90:130]
    result = write_prepared_project(prepared)
    after = read_offline_project(result.data)
    saved = parse_declarations(after.streams[table], logical_name=table, source_kind=kind)
    assert [(row.name, row.data_type) for row in saved.rows] == [('OfflineLocal', 'INT')]
    assert saved.owner_name == original.owner_name and saved.owner_sfc_block_number == original.owner_sfc_block_number
    assert saved.trailer == original.trailer
    assert after.streams[logical][:50] == before.streams[logical][:50]
    assert after.streams[logical][54:] == before.streams[logical][54:]
    assert after.streams[logical][50:54] == b'\x01\0\0\0'
    assert all(after.streams[name] == value for name, value in before.streams.items() if name not in {logical, table})
    check = next(row for row in prepared.objects[0]['offline_authoring']['source_checks'] if row['source'] == logical)
    assert check['semantic'] == 'unverified' and check['known_label_references'] == []
    assert check['native_compile'] == check['execution'] == 'not_run'
    removed = write_existing_definition(result.data, dict(operation='edit_pou_labels', source_name=logical,
        declarations=[], remove_declarations=['OfflineLocal']))
    final = read_offline_project(removed.data)
    assert parse_declarations(final.streams[table], logical_name=table, source_kind=kind).rows == ()
    assert final.streams[logical][54:] == before.streams[logical][54:]
    if kind == 240:
        rebound = read_sfc_project_context(removed.data, logical)
        assert rebound.declarations[table].owner_sfc_block_number == original.owner_sfc_block_number


FUNCTION_CASES = [
    ('worldofwheat-fx3g.gxw', 'getCurrentPlayer.Program.pou', 'INT', 'currentPlayer + 10'),
    ('tarman3-fx3u.gxw', 'crc_my.Program.pou', 'WORD', 'Word0'),
    ('oph032-q06udv.gxw', 'LeapYear.Program.pou', 'BOOL', '(i_Year MOD 4) = 0'),
    ('oph032-q06udv.gxw', 'newDATE2SEC.Program.pou', 'DINT', 'DINT#17'),
]


@lru_cache(maxsize=None)
def authored_st_project(filename):
    return private_path('research/fixtures/gxw-offline/st-authored/' + filename).read_bytes()


@pytest.mark.parametrize('filename,logical,return_type,expression', FUNCTION_CASES)
def test_original_author_function_body_and_local_declarations_keep_return_interface_and_other_sources(
        filename, logical, return_type, expression):
    raw = authored_st_project(filename)
    original = payloads(raw)
    _, _, groups, sources, _ = _context(raw, require_fbd=False)
    table = groups[logical]['labels']
    document = sources.declarations[table]
    assert document.owner_pou_type == 0x1000003 and document.owner_return_type == return_type
    old_interface = sources.st_interface(document.owner_name, 'FUNCTION')
    declarations = [*_rows(document, sources), dict(name='OfflineScratch', data_type=return_type)]
    text = f'(* Existing FUNCTION edit. *)\r\nOfflineScratch := {expression};\r\n{document.owner_name} := OfflineScratch;\r\n'
    result = write_existing_definition(raw, dict(operation='edit_st_function', source_name=logical,
        st_text=text, declarations=declarations), expected_sources={logical: original[logical], table: original[table]})
    saved = payloads(result.data)
    current = parse_declarations(saved[table], logical_name=table)
    assert parse_st_pou(saved[logical]).text == text
    assert current.owner_return_type == return_type and current.owner_pou_type == document.owner_pou_type
    assert current.owner_name == document.owner_name and current.owner_pou_status == document.owner_pou_status
    assert current.header[:50] == document.header[:50]
    assert current.header[54:document.count_offset] == document.header[54:document.count_offset]
    assert current.trailer == document.trailer
    assert [serialize_label(row) for row in current.rows[:-1]] == [serialize_label(row) for row in document.rows]
    assert current.rows[-1].name == 'OfflineScratch'
    refreshed = sources.with_declarations({**sources.declarations, table: current})
    assert refreshed.st_interface(document.owner_name, 'FUNCTION') == old_interface
    assert all(saved[name] == value for name, value in original.items() if name not in {logical, table})
    assert result.report['definition_after']['kind'] == 'function'
    assert result.report['definition_after']['return_type'] == return_type
    assert result.report['validation']['compile_state'] == 'stale'
    check = next(row for row in result.report['source_checks'] if row['source'] == logical)
    assert check['syntax'] == 'passed' and check['status'] != 'failed'
    assert check['execution'] == 'not_run'


@pytest.mark.parametrize('filename,logical,return_type,expression', FUNCTION_CASES)
def test_generic_existing_st_function_noop_preserves_whole_original_project(filename, logical, return_type, expression):
    raw = authored_st_project(filename)
    source = parse_st_pou(payloads(raw)[logical])
    result = write_existing_definition(raw, dict(operation='edit_st_source', source_name=logical, st_text=source.text))
    assert result.data == raw
    assert result.report['definition_after']['return_type'] == return_type
    assert result.report['validation']['compile_state'] == 'unchanged'


@pytest.mark.parametrize('operation', ['edit_st_program', 'edit_st_fb'])
def test_precise_st_operation_cannot_change_a_function_owner_kind(operation):
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    with pytest.raises(GXWFormatError, match='POU kind'):
        write_existing_definition(raw, dict(operation=operation, source_name='getCurrentPlayer',
            st_text='getCurrentPlayer := 1;'))


def test_function_return_type_is_bound_to_the_original_header_and_cannot_be_redeclared_in_its_body():
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        write_existing_definition(raw, dict(operation='edit_st_function', source_name='getCurrentPlayer',
            st_text='getCurrentPlayer := TRUE;'))
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        write_existing_definition(raw, dict(operation='edit_st_function', source_name='getCurrentPlayer',
            st_text='getCurrentPlayer := getCurrentPlayer(TRUE);'))
    with pytest.raises(GXWFormatError, match='body, not embedded'):
        write_existing_definition(raw, dict(operation='edit_st_function', source_name='getCurrentPlayer',
            st_text='FUNCTION getCurrentPlayer : BOOL getCurrentPlayer := TRUE; END_FUNCTION'))
    with pytest.raises(GXWFormatError, match='syntax/semantic'):
        write_existing_definition(raw, dict(operation='edit_st_function', source_name='getCurrentPlayer',
            return_type='BOOL', st_text='getCurrentPlayer := 1;'))


def test_definition_function_return_type_changes_only_its_counted_header_and_current_body():
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    logical, table = 'getCurrentPlayer.Program.pou', 'getCurrentPlayer.Labels.lh'
    original = payloads(raw)
    old = labels(raw, table)
    result = write_existing_definition(raw, dict(operation='edit_st_function', source_name=logical,
        return_type='BOOL', st_text='getCurrentPlayer := TRUE;'), save_policy='draft')
    new = labels(result.data, table)
    assert (old.owner_return_type, new.owner_return_type) == ('INT', 'BOOL')
    assert new.owner_name == old.owner_name and new.owner_pou_type == old.owner_pou_type
    assert new.count_offset == old.count_offset + 2
    assert new.trailer == old.trailer
    assert [serialize_label(row) for row in new.rows] == [serialize_label(row) for row in old.rows]
    saved = payloads(result.data)
    assert all(saved[name] == value for name, value in original.items() if name not in (logical, table))
    assert result.report['definition_after']['return_type'] == 'BOOL'
    assert parse_st_pou(saved[logical]).text == 'getCurrentPlayer := TRUE;'
    assert any(row['source'] != logical and row['language'] in ('st', 'fbd')
               for row in result.report['source_checks'])
    restored = write_existing_definition(result.data, dict(operation='edit_st_function', source_name=logical,
        return_type='INT', st_text=parse_st_pou(original[logical]).text), save_policy='draft')
    from gxw.project_metadata import mark_observed_source_compile_pending
    assert payloads(restored.data)[table] == mark_observed_source_compile_pending(original[table], logical_name=table)


def test_existing_function_label_upserts_and_removals_preserve_formals_and_check_current_callers():
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    logical, table = 'getCurrentPlayer.Program.pou', 'getCurrentPlayer.Labels.lh'
    original = payloads(raw)
    result = write_existing_definition(raw, dict(operation='edit_pou_labels', source_name=logical,
        declarations=[dict(name='OfflineUnused', data_type='INT', comment='local row')]))
    changed = labels(result.data, table)
    assert changed.owner_return_type == 'INT' and changed.rows[-1].name == 'OfflineUnused'
    assert payloads(result.data)[logical][54:] == original[logical][54:]
    restored = write_existing_definition(result.data, dict(operation='edit_pou_labels', source_name=logical,
        declarations=[], remove_declarations=['OfflineUnused']))
    assert [serialize_label(row) for row in labels(restored.data, table).rows] == [
        serialize_label(row) for row in labels(raw, table).rows]
    with pytest.raises(GXWFormatError, match='ST reference|syntax/semantic'):
        write_existing_definition(raw, dict(operation='edit_pou_labels', source_name=logical,
            declarations=[], remove_declarations=['currentPlayer']))
    with pytest.raises(GXWFormatError, match='syntax/semantic|binding|interface'):
        write_existing_definition(raw, dict(operation='edit_st_function', source_name=logical,
            st_text='getCurrentPlayer := 1;', declarations=[dict(name='currentPlayer', data_type='BOOL', class_name='VAR_INPUT')]))


@pytest.mark.parametrize('filename,logical,return_type,expression', FUNCTION_CASES)
def test_saved_function_candidate_uses_source_return_type_and_has_no_program_task(tmp_path, monkeypatch,
        filename, logical, return_type, expression):
    import subprocess
    from application.native_st import prepare_candidate, model_project_context
    def forbidden(*args, **kwargs):
        raise AssertionError('offline FUNCTION projection attempted a native operation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    raw = authored_st_project(filename)
    _, _, _, sources, _ = _context(raw, require_fbd=False)
    payload = prepare_candidate(tmp_path, imported=raw, program_name=logical, plc_model=sources.cpu)
    binding = payload['metadata']['validation']['source_binding']
    assert binding['kind'] == 'function' and binding['return_type'] == return_type
    assert binding['task_bindings'] == []
    assert payload['metadata']['validation']['task_membership'] == 'not_applicable'
    assert payload['metadata']['validation']['gx_compile'] == 'not_run'
    from gxw.st_program import project_context_snapshot
    context = model_project_context(project_context_snapshot(raw, logical))
    selected = next(row for row in context['pous'] if row['name'] == binding['owner'])
    assert selected['return_type'] == return_type


def test_native_plan_and_gui_coverage_do_not_block_an_offline_function_projection(tmp_path, monkeypatch):
    import application.native_st as native_st
    def unavailable(*args, **kwargs):
        raise GXWFormatError('independent native acceptance context is not covered')
    monkeypatch.setattr(native_st, 'native_validation_source_plan', unavailable)
    monkeypatch.setattr(native_st, 'project_gui_configuration', unavailable)
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    payload = native_st.prepare_candidate(tmp_path, imported=raw,
        program_name='getCurrentPlayer.Program.pou', plc_model='FX3G')
    import json
    report = json.loads((tmp_path / payload['artifacts']['write_report']['path']).read_bytes())
    assert report['source']['compiler_sources']['status'] == 'unverified'
    assert report['source']['gui_configuration']['status'] == 'not_verified'
    assert payload['metadata']['validation']['gx_compile'] == 'not_run'


def test_generic_st_edit_preserves_original_q13udv_fb_interface_and_instance_rows():
    raw = authored_st_project('oph032-q13udv.gxw')
    logical = 'TrafficLamp.Program.pou'
    original = payloads(raw)
    _, _, groups, sources, _ = _context(raw, require_fbd=False)
    table = groups[logical]['labels']
    document = sources.declarations[table]
    text = 'OfflineScratch := 7;\r\nPhase := OfflineScratch;\r\n'
    result = write_existing_definition(raw, dict(operation='edit_st_source', source_name=logical,
        st_text=text, declarations=[*_rows(document, sources), dict(name='OfflineScratch', data_type='INT')]))
    saved = payloads(result.data)
    assert parse_st_pou(saved[logical]).text == text
    assert [serialize_label(row) for row in labels(result.data, table).rows[:-1]] == [serialize_label(row) for row in document.rows]
    assert all(saved[name] == value for name, value in original.items() if name not in {logical, table})
    assert result.report['definition_after']['kind'] == 'function_block'


@settings(max_examples=4, deadline=None, derandomize=True, database=None)
@given(value=st.integers(min_value=-300, max_value=300), comment=st.text(alphabet='ABC中文😀', max_size=80))
def test_existing_native_function_body_variants_keep_header_return_and_input_records(value, comment):
    raw = authored_st_project('worldofwheat-fx3g.gxw')
    logical, table = 'getCurrentPlayer.Program.pou', 'getCurrentPlayer.Labels.lh'
    original = payloads(raw)
    text = f'(* {comment} *)\r\ngetCurrentPlayer := currentPlayer + ({value});\r\n'
    result = write_existing_definition(raw, dict(operation='edit_st_function', source_name=logical, st_text=text))
    saved = payloads(result.data)
    assert parse_st_pou(saved[logical]).text == text
    assert saved[table] == original[table]
    assert all(saved[name] == data for name, data in original.items() if name != logical)
    assert result.report['definition_after']['return_type'] == 'INT'


def test_native_l02s_st_witness_grows_and_shrinks_existing_body_and_declarations_without_native_runtime(monkeypatch):
    import ctypes
    import json
    import subprocess
    from gxw.fb_authoring import st_program_snapshot
    def forbidden(*args, **kwargs):
        raise AssertionError('offline L ST edit attempted a native operation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(ctypes, 'CDLL', forbidden)
    monkeypatch.setattr(ctypes, 'WinDLL', forbidden)
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-offline-l-st-20261021.zip')) as source:
        raw = source.read('native-baseline.gxw')
        independently_saved = source.read('compile-save/candidate.gxw')
        cold = source.read('cold-check/input.gxw')
        evidence = json.loads(source.read('result.json'))
    assert evidence['cpu'] == 'L02S' and evidence['native_version'] == '1.635.0.1'
    for observation in (evidence['compile_observation'], evidence['cold_observation']):
        assert observation['compile'] == observation['check'] == 'completed_accepted'
    assert evidence['plc_access'] is False and evidence['user_gui_attached'] is False
    original = payloads(raw)
    before = st_program_snapshot(raw, 'OfflineLST')
    selected, = before['programs']
    logical, table = selected['program'], selected['labels']
    assert before['cpu'] == 'L02S' and selected['st_text'] == 'Counter := 1;\r\n'
    grown_text = 'Counter := Counter + 2;\r\nOfflineBuffer[0] := Counter;\r\n'
    grown = write_existing_definition(raw, dict(operation='edit_st_source', source_name='OfflineLST',
        st_text=grown_text, declarations=[dict(name='Counter', data_type='INT'),
            dict(name='OfflineBuffer', data_type='ARRAY[0..63] OF INT', comment='L ST declaration growth ' * 30)]))
    grown_streams = payloads(grown.data)
    assert parse_st_pou(grown_streams[logical]).text == grown_text
    assert len(grown_streams[logical]) > len(original[logical]) and len(grown_streams[table]) > len(original[table])
    saved_entry, = st_program_snapshot(independently_saved, 'OfflineLST')['programs']
    cold_entry, = st_program_snapshot(cold, 'OfflineLST')['programs']
    assert saved_entry['st_text'] == cold_entry['st_text'] == 'Counter := Counter + 3;\r\nScratch := Counter;\r\n'
    final = write_existing_definition(grown.data, dict(operation='edit_st_program', source_name=logical,
        st_text=cold_entry['st_text'], declarations=[dict(name='Counter', data_type='INT'),
            dict(name='Scratch', data_type='INT')]))
    after = payloads(final.data)
    assert len(after[table]) < len(grown_streams[table])
    assert parse_st_pou(after[logical]).text == saved_entry['st_text']
    assert [(row.name, row.data_type) for row in labels(final.data, table).rows] == [('Counter', 'INT'), ('Scratch', 'INT')]
    assert [(row['name'], row['data_type']) for row in cold_entry['declarations']] == [('Counter', 'INT'), ('Scratch', 'INT')]
    final_snapshot = st_program_snapshot(final.data, 'OfflineLST')
    assert final_snapshot['cpu'] == 'L02S' and final_snapshot['tasks'] == before['tasks']
    assert final_snapshot['programs'][0]['task_bindings'] == selected['task_bindings']
    assert all(after[name] == value for name, value in original.items() if name not in {logical, table})
    assert serialize_label(labels(final.data, table).rows[0]) == serialize_label(labels(raw, table).rows[0])
    assert final.report['validation']['compile_state'] == 'stale'
    assert final.report['write_backend'] == 'offline'


def test_original_retain_declaration_edits_remain_available_without_changing_the_fbd_body():
    import json
    from application.gxw_files import read_source
    from gxw.offline_authoring import prepare_declaration_edits
    from gxw.project_writer import write_prepared_project
    fixture = json.loads(private_path('tests/fixtures/gxw_retain_declarations_native.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(private_path(fixture['archive'])) as archive:
        raw = archive.read(fixture['source_member'])
    logical, table = fixture['source_name'], fixture['table']
    original = payloads(raw)
    prepared, checks = prepare_declaration_edits(raw, {table: dict(
        upserts=[dict(name='iCounter', initial_value='17', comment='retained local source edit')])}, save_policy='checked')
    result = write_prepared_project(prepared)
    view = read_source(result.data, logical)
    assert view['language'] == 'fbd'
    table_view = next(row for row in view['declaration_tables'] if row['source_name'] == table)
    retained = next(row for row in table_view['rows'] if row['name'] == 'iCounter')
    assert retained['class_name'] == 'VAR_RETAIN' and retained['initial_value'] == '17'
    from gxw.source_header import source_payload_offset
    after = payloads(result.data)
    assert after[logical][source_payload_offset(after[logical]):] == original[logical][source_payload_offset(original[logical]):]
    assert not any(row.get('language') == 'st' and row.get('source') == logical for row in checks)
    assert result.report['validation']['compile_state'] == 'stale'


def test_existing_structure_input_formal_and_st_body_share_a_source_bound_draft_transaction(monkeypatch):
    from dataclasses import replace
    import ctypes
    import json
    import subprocess
    from gxw.offline_authoring import prepare_declaration_edits
    from gxw.project_writer import write_prepared_project
    from gxw.text_pou import serialize_st_pou
    from gxw.source_header import source_payload_offset
    def forbidden(*args, **kwargs):
        raise AssertionError('existing structure declaration edit called a native component')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(ctypes, 'CDLL', forbidden)
    monkeypatch.setattr(ctypes, 'WinDLL', forbidden)
    fixture = json.loads(private_path('tests/fixtures/gxw_structure_class_edits_native.json').read_text(encoding='utf-8'))
    case = next(value for value in fixture['cases'] if value['id'] == 'project0')
    with zipfile.ZipFile(private_path(fixture['archive'])) as archive:
        raw = archive.read(case['original_member'])
    logical, table = case['source'], case['before_table']
    original = payloads(raw)
    source = parse_st_pou(original[logical])
    text = source.text + '\r\n(* existing structure interface direction draft *)\r\n'
    prepared, checks = prepare_declaration_edits(raw, {table: dict(upserts=[dict(name='Axis', class_name='VAR_INPUT')])},
        source_replacements={logical: serialize_st_pou(source, text)}, save_policy='draft')
    result = write_prepared_project(prepared)
    after = payloads(result.data)
    assert parse_st_pou(after[logical]).text == text
    previous = next(row for row in labels(raw, table).rows if row.name == 'Axis')
    current = next(row for row in labels(result.data, table).rows if row.name == 'Axis')
    assert (previous.class_code, current.class_code, current.type_code) == (5, 3, 0)
    assert current.value_extension == previous.value_extension
    assert serialize_label(current) == serialize_label(replace(previous, class_code=3))
    assert result.report['validation']['compile_state'] == 'stale' and checks
    for name, value in original.items():
        if name in {logical, table}:
            continue
        if name.endswith(('.pou', '.lnb', '.lh', '.lnl', '.gh')):
            assert after[name][source_payload_offset(after[name]):] == value[source_payload_offset(value):]
        else:
            assert after[name] == value

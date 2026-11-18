"""Source-bound offline project transactions on the supplied warehouse sample."""
from copy import deepcopy
import ctypes
from dataclasses import replace
import json
from pathlib import Path
import struct
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

from gxw.integrity import inspect_project_integrity
from gxw.models import GXWFormatError
from gxw.offline_project import (
    edit_ladder_project, inspect_offline_project, read_offline_project, read_ordered_program,
)
from gxw.project_writer import write_project_replacements
from gxw.source_header import source_payload_offset
from gxw.token_encoder import encode_instruction
from gxw.token_pou import frame_token_pou, parse_token_pou
from tools.offline_inputs import private_path
from plc.target_identity import Q_CPUS, PARAMETER_ONLY


@pytest.mark.parametrize('case_id', ['cycle3', 'cycle3_renamed', 'cycle4', 'priority_branch'])
def test_synthetic_control_sources_roundtrip_without_private_projects(case_id):
    from scripts.control_mining_cases import decode_case, ordered_controller, synthetic_cases
    from plc.ir import ir_to_ladder, lower_rung_instructions

    case = next(case for case in synthetic_cases() if case['case_id'] == case_id)
    decoded = decode_case(case)
    ordered = decoded['ordered']
    expected = ordered_controller(case)
    assert [{'op': row['op'], 'args': row['args']}
            for row in ordered['source_records']] == expected
    assert [{'op': row['op'], 'args': row['args']}
            for rung in ir_to_ladder(decoded['program_ir'])['rungs']
            for row in lower_rung_instructions(rung)] == expected
    assert ordered['plc_model'] == 'FX3U/FX3UC'
    assert ordered['coverage']['parse_status'] == 'complete'
    assert ordered['coverage']['semantic_status'] == 'static_only'
    assert ordered['coverage']['runtime_verified'] is False
    assert ordered['selected']['capabilities']['native_compile'] == 'not_run'
    assert edit_ladder_project(decoded['raw'], 'MAIN', []).data == decoded['raw']


def q_source_with_opaque_record(cpu):
    from tests.test_gxw_offline_application import q_native_origin_projects
    raw = q_native_origin_projects()[cpu]
    project = read_offline_project(raw)
    ordered = read_ordered_program(project)
    name = ordered['selected']['source_name']
    source = project.streams[name]
    frame = frame_token_pou(source)
    unknown = bytes.fromhex('03fe03')  # Unassigned, framed Q instruction header.
    changed = bytearray(source[:frame.body_offset] + unknown + source[frame.body_offset:])
    for offset in (frame.body_offset - 24, frame.body_offset - 20):
        struct.pack_into('<I', changed, offset, struct.unpack_from('<I', source, offset)[0] + len(unknown))
    return write_project_replacements(raw, {name: (project.mapping[name], source, bytes(changed))}).data, name


@pytest.mark.parametrize('cpu', ['Q00J', 'Q02/Q02H', 'Q02PH', 'Q00UJ', 'Q03UDV'])
@pytest.mark.parametrize('language', ['ladder', 'sfc'])
def test_q_program_and_sfc_child_report_stack_errors_and_preserve_old_diagnostics(cpu, language):
    from gxw.offline_edit import edit_offline_project
    if language == 'ladder':
        from tests.test_gxw_offline_application import q_native_origin_projects
        raw = q_native_origin_projects()[cpu]
        name = read_ordered_program(read_offline_project(raw))['selected']['source_name']
        def records(data):
            return read_ordered_program(read_offline_project(data), name)['source_records']
        def command(edit):
            return dict(source_name=name, command=dict(instruction_edits=[edit]))
    else:
        import zipfile
        from gxw.sfc_pou import read_sfc_project_context
        from gxw.token_profiles import source_token_profile
        with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-cpu-menus-20261014.zip')) as archive:
            origin = next(row for row in json.loads(archive.read('menu-cases.json'))['cases'] if row['menu'] == cpu)
            raw = archive.read(origin['check'] + '/input.gxw')
        context = read_sfc_project_context(raw)
        name = context.program_name
        child_name = context.source.layout['actions'][0]['registrations'][0]['zoom']
        def records(data):
            model = read_sfc_project_context(data).object_model(token_profile=source_token_profile(cpu, language='sfc'))
            child = next(row for row in model['children'] if row['kind'] == 'zoom' and row['name'] == child_name)
            return [dict(row, record_index=index) for index, row in enumerate(child['records'])]
        def command(edit):
            return dict(source_name=name, command=dict(child_edits=[dict(kind='zoom', name=child_name,
                instruction_edits=[edit])]))
    first = next(row for row in records(raw) if row.get('op') == 'LD') if language == 'ladder' else records(raw)[0]
    invalid = command(edit_for(first, instructions=[dict(op='ORB', args=[])]))
    with pytest.raises(GXWFormatError, match='stack underflow'):
        edit_offline_project(raw, [invalid], save_policy='checked')
    draft = edit_offline_project(raw, [invalid], save_policy='draft')
    assert draft.report['artifact_state'] == 'draft'
    check = next(row for row in draft.report['source_checks'] if row['source'] == name)
    assert check['status'] == 'failed' and check['introduced_errors'][0]['code'] == 'TOKEN_INSTRUCTION'
    assert 'stack underflow' in check['introduced_errors'][0]['message']
    target = next(row for row in records(draft.data) if row.get('op') == 'ORB')
    shifted = edit_offline_project(draft.data, [command(edit_for(target, 'insert_before', [dict(op='NOP', args=[])]))],
                                   save_policy='checked')
    check = next(row for row in shifted.report['source_checks'] if row['source'] == name)
    assert check['status'] == 'failed' and check['introduced_errors'] == []
    assert shifted.report['artifact_state'] == 'draft'
    target = next(row for row in records(shifted.data) if row.get('op') == 'ORB')
    repaired = edit_offline_project(shifted.data, [command(edit_for(target,
        instructions=[dict(op=first['op'], args=first['args'])]))], save_policy='checked')
    assert not any(finding['severity'] == 'error' for check in repaired.report['source_checks']
                   for finding in check.get('findings', []))


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
def test_each_q_preserves_unknown_records_while_editing_known_source(cpu):
    from gxw.diagnostic_sources import bind_native_ladder_candidate
    raw, name = q_source_with_opaque_record(cpu)
    before = read_ordered_program(read_offline_project(raw), name)
    assert before['coverage']['parse_status'] == 'partial'
    first = next(row for row in before['source_records'] if row.get('op') == 'LD')
    result = edit_ladder_project(raw, name, [edit_for(first, instructions=[dict(op='LD', args=['M2'])])],
                                 save_policy='draft')
    after = read_ordered_program(read_offline_project(result.data), name)
    assert after['source_records'][0]['raw_hex'] == before['source_records'][0]['raw_hex'] == '03fe03'
    assert after['coverage']['parse_status'] == 'partial'
    assert [row['raw_hex'] for row in after['source_records'] if row['record_index'] != first['record_index']] == [
        row['raw_hex'] for row in before['source_records'] if row['record_index'] != first['record_index']]
    with pytest.raises(GXWFormatError, match='unsupported source records'):
        bind_native_ladder_candidate(result.data, after['program_ir'], name)


@settings(max_examples=20, deadline=None, derandomize=True)
@given(cpu=st.sampled_from(['Q00J', 'Q02/Q02H', 'Q02PH', 'Q00UJ', 'Q03UDV']),
       edits=st.lists(st.tuples(st.integers(1, 200), st.integers(1, 3)), min_size=1, max_size=4))
def test_q_edit_sequences_keep_original_record_order_and_unknown_bytes(cpu, edits):
    raw, name = q_source_with_opaque_record(cpu)
    for address, count in edits:
        before = read_ordered_program(read_offline_project(raw), name)
        first = next(row for row in before['source_records'] if row.get('op') == 'LD')
        end = next(row for row in before['source_records'] if row.get('op') == 'END')
        changed = edit_ladder_project(raw, name, [
            edit_for(first, instructions=[dict(op='LD', args=[f'M{address}'])]),
            edit_for(end, 'insert_before', [dict(op='NOP', args=[])] * count)], save_policy='draft').data
        after = read_ordered_program(read_offline_project(changed), name)
        assert next(row for row in after['source_records'] if row.get('op') == 'LD')['args'] == [f'M{address}']
        retained = [row['raw_hex'] for row in before['source_records'] if row['record_index'] != first['record_index']]
        inserted = after['source_records'][end['record_index']:end['record_index'] + count]
        assert [(row['op'], row['args']) for row in inserted] == [('NOP', [])] * count
        assert [row['raw_hex'] for index, row in enumerate(after['source_records'])
                if index != first['record_index'] and not end['record_index'] <= index < end['record_index'] + count] == retained
        raw = changed


@pytest.fixture(scope="module", autouse=True)
def no_external_processes():
    def forbidden(*args, **kwargs):
        raise AssertionError("offline project operations must not start external/native processes")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setenv("GXWORKS2_INSTALL_DIR", "Z:/missing-gxworks2-for-offline-tests")
    yield
    monkeypatch.undo()


@pytest.fixture(scope="module")
def warehouse():
    path = Path("D:/factory io/仓库.gxw")
    if not path.is_file():
        pytest.skip("the user-supplied warehouse acceptance GXW is unavailable")
    raw = path.read_bytes()
    project = read_offline_project(raw)
    ordered = read_ordered_program(project)
    assert len(ordered["listing"].instructions) == 155
    assert len(ordered["raw_networks"]) == 9
    assert len(ordered["device_comments"]) == 22
    return path, raw, project, ordered


def edit_for(record, operation="replace", instructions=None):
    return {"operation": operation, "record_index": record["record_index"],
            "expected": {"op": record["op"], "args": list(record["args"])},
            "instructions": list(instructions or [])}


def raw_records(ordered):
    return [bytes.fromhex(record["raw_hex"]) for record in ordered["source_records"]]


def reread(result):
    project = read_offline_project(result.data)
    return project, read_ordered_program(project)


def assert_other_streams_equal(before, after, changed):
    assert before.mapping == after.mapping
    assert before.streams.keys() == after.streams.keys()
    assert {name: data for name, data in before.streams.items() if name not in changed} == {
        name: data for name, data in after.streams.items() if name not in changed}
    assert {name: data for name, data in before.outer.items() if name not in {"_hdb", "history.xml", "Project.gd2"}} == {
        name: data for name, data in after.outer.items() if name not in {"_hdb", "history.xml", "Project.gd2"}}
    assert inspect_project_integrity(after.outer)["status"] in {"absent", "verified"}


def test_inspection_keeps_all_source_instructions_networks_comments_and_order(warehouse):
    _, raw, project, ordered = warehouse
    report = inspect_offline_project(raw)
    assert report["write_backend"] == "offline" and report["native_required"] is False
    assert report["native_compile"] == "not_run" and report["runtime_verified"] is False
    assert report["cpu_menu"] == "FX3U/FX3UC" and report["codepage"] == 936
    assert report["selected_source"]["source_name"] == "MAIN.Program.pou"
    assert report["coverage"]["parse_status"] == "complete"
    native = [(record.mnemonic, list(record.args)) for record in ordered["listing"].instructions]
    networks = [(record["op"], record["args"]) for network in report["networks"]
                for record in network["raw_instructions"]]
    assert networks == native and len(networks) == 155
    assert [op for op, _ in networks if op in {"MPS", "MRD", "MPP"}] == [
        record.mnemonic for record in ordered["listing"].instructions if record.mnemonic in {"MPS", "MRD", "MPP"}]
    assert ordered["program_ir"] is not None
    assert project.raw == raw


def test_noop_instruction_and_empty_transaction_return_original_bytes(warehouse):
    _, raw, _, ordered = warehouse
    first = next(record for record in ordered["source_records"] if record.get("op") == "MOV")
    noop = edit_for(first, instructions=[{"op": first["op"], "args": list(first["args"])}])
    for edits in ([], [noop]):
        saved = edit_ladder_project(raw, "MAIN", edits)
        assert saved.data == raw
        assert saved.report["validation"]["compile_state"] == "unchanged"


def test_move_uses_original_record_coordinates_and_preserves_each_original_byte(warehouse):
    _, raw, original, ordered = warehouse
    records = ordered['source_records']
    first, target = records[1], records[4]
    move = edit_for(first, operation='move_before')
    move['target_record_index'] = target['record_index']
    saved = edit_ladder_project(raw, 'MAIN', [move], save_policy='draft', defer_checks=True)
    project, after = reread(saved)
    expected = raw_records(ordered)
    moved = expected.pop(1)
    expected.insert(3, moved)
    assert raw_records(after) == expected
    assert_other_streams_equal(original, project, {'MAIN.Program.pou'})
    reverse = edit_for(after['source_records'][3], operation='move_before')
    reverse['target_record_index'] = 1
    restored = edit_ladder_project(saved.data, 'MAIN', [reverse], save_policy='draft')
    assert raw_records(reread(restored)[1]) == raw_records(ordered)


def test_inline_statement_draft_can_grow_shrink_and_delete_without_reencoding_instructions(warehouse):
    _, raw, original, ordered = warehouse
    end = ordered['source_records'][-1]
    inserted = edit_ladder_project(raw, 'MAIN', [edit_for(end, operation='insert_before',
        instructions=[{'kind': 'statement', 'text': 'Draft statement'}])], save_policy='draft')
    _, current = reread(inserted)
    row = current['source_records'][-2]
    assert row['kind'] == 'statement' and row['text'] == 'Draft statement'
    for text in ('Longer draft statement with more content', '短'):
        change = {'operation': 'replace', 'record_index': row['record_index'],
                  'expected': {'kind': row['kind'], 'text': row['text'], 'raw_hex': row['raw_hex']},
                  'instructions': [{'kind': 'statement', 'text': text}]}
        inserted = edit_ladder_project(inserted.data, 'MAIN', [change], save_policy='draft')
        _, current = reread(inserted)
        row = current['source_records'][-2]
        assert row['text'] == text
        assert raw_records(current)[:-2] + raw_records(current)[-1:] == raw_records(ordered)
    removed = edit_ladder_project(inserted.data, 'MAIN', [{
        'operation': 'delete', 'record_index': row['record_index'],
        'expected': {'kind': row['kind'], 'text': row['text']}}], save_policy='draft')
    project, after = reread(removed)
    assert raw_records(after) == raw_records(ordered)
    assert_other_streams_equal(original, project, {'MAIN.Program.pou'})


@pytest.mark.parametrize('target,expected_hex', [
    ('T0', '03210304c20004'), ('T10Z0', '03210304f0000404c20a04'),
    ('C0', '03210304c50004'), ('C200', '03210304c5c804'),
])
def test_timer_draft_without_preset_can_be_saved_reopened_and_completed(warehouse, target, expected_hex):
    _, raw, original, ordered = warehouse
    terminal = ordered['source_records'][-1]
    change = edit_for(terminal, operation='insert_before', instructions=[{'op': 'OUT', 'args': [target]}])
    with pytest.raises(GXWFormatError, match='preset'):
        edit_ladder_project(raw, 'MAIN', [change])
    draft = edit_ladder_project(raw, 'MAIN', [change], save_policy='draft')
    _, current = reread(draft)
    inserted = current['source_records'][-2]
    assert inserted['op'] == 'OUT' and inserted['args'] == [target]
    # This exact incomplete source fragment is a separate retained native
    # constructor witness, not an ordinary timer OUT with its operand lost.
    assert inserted['raw_hex'] == expected_hex
    check = next(row for row in draft.report['source_checks'] if row['source'] == 'MAIN.Program.pou')
    assert check['status'] == check['syntax'] == 'failed'
    finding, = [row for row in check['findings'] if row['code'] == 'TOKEN_REQUIRED_PRESET']
    assert finding['severity'] == 'error' and finding['operands'] == [target]
    assert current['program_ir']['analysis']['counts']['error']
    assert any(row['code'] == 'SOURCE_REQUIRED_PRESET' and row['severity'] == 'error'
               for row in current['program_ir']['analysis']['findings'])
    completed = edit_ladder_project(draft.data, 'MAIN', [edit_for(inserted,
        instructions=[{'op': 'OUT', 'args': [target, 'K10']}])], save_policy='draft')
    rebuilt, after = reread(completed)
    assert after['source_records'][-2]['args'] == [target, 'K10']
    assert not any(row['severity'] == 'error' for check in completed.report['source_checks']
                   for row in check['findings'])
    assert not any(row['code'] == 'SOURCE_REQUIRED_PRESET'
                   for row in after['program_ir']['analysis']['findings'])
    assert raw_records(after)[:-2] + raw_records(after)[-1:] == raw_records(ordered)
    assert_other_streams_equal(original, rebuilt, {'MAIN.Program.pou'})


@pytest.mark.parametrize('failure', ['stale_record', 'move_target_deleted', 'move_end'])
def test_move_conflicts_and_stale_record_bytes_are_rejected_before_saving(warehouse, failure):
    _, raw, _, ordered = warehouse
    source, target = ordered['source_records'][1], ordered['source_records'][4]
    move = edit_for(source, operation='move_before')
    move['target_record_index'] = target['record_index']
    changes = [move]
    if failure == 'stale_record':
        move['expected']['raw_hex'] = '00'
    elif failure == 'move_target_deleted':
        changes.append(edit_for(target, operation='delete'))
    else:
        move.update(record_index=ordered['source_records'][-1]['record_index'],
                    expected={'op': 'END', 'args': []})
    with pytest.raises(GXWFormatError):
        edit_ladder_project(raw, 'MAIN', changes, save_policy='draft')


@pytest.mark.parametrize("args,delta", [(["K2", "D0"], 0), (["K32767", "D1000"], 2)])
def test_replace_same_and_variable_length_preserves_all_other_records_and_streams(warehouse, args, delta):
    path, raw, project, ordered = warehouse
    first = next(record for record in ordered["source_records"] if record.get("op") == "MOV")
    saved = edit_ladder_project(raw, "MAIN", [edit_for(first, instructions=[{"op": "MOV", "args": args}])])
    after, reread_program = reread(saved)
    changed = first["record_index"]
    before_records, after_records = raw_records(ordered), raw_records(reread_program)
    assert len(after_records) == len(before_records) == 155
    assert after_records[:changed] == before_records[:changed]
    assert after_records[changed + 1:] == before_records[changed + 1:]
    assert reread_program["source_records"][changed]["args"] == args
    logical = ordered["selected"]["source_name"]
    assert len(after.streams[logical]) == len(project.streams[logical]) + delta
    assert_other_streams_equal(project, after, {logical})
    assert saved.report["validation"]["compile_state"] == "stale"
    assert saved.report["validation"]["gxworks_compile"] == "not_run"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("operation,rows,delta", [
    ("insert_before", [{"op": "MOV", "args": ["K2", "D0"]}], 1),
    ("replace", [{"op": "MOV", "args": ["K2", "D0"]}, {"op": "MOV", "args": ["K3", "D1"]}], 1),
    ("delete", [], -1),
])
def test_insert_delete_and_sequence_replacement_keep_original_coordinate_order(warehouse, operation, rows, delta):
    _, raw, project, ordered = warehouse
    first = next(record for record in ordered["source_records"] if record.get("op") == "MOV")
    saved = edit_ladder_project(raw, "MAIN", [edit_for(first, operation, rows)])
    after, current = reread(saved)
    index = first["record_index"]
    old = raw_records(ordered)
    replacement = [encode_instruction(row["op"], row["args"], profile=ordered["listing"].profile,
                                      text_encoding=project.context["text_encoding"], plc_model=project.context["cpu"]) for row in rows]
    expected = old[:index] + replacement + (old[index:] if operation == "insert_before" else old[index + 1:])
    assert raw_records(current) == expected
    assert len(current["listing"].instructions) == 155 + delta
    assert current["listing"].instructions[-1].mnemonic == "END"
    assert_other_streams_equal(project, after, {ordered["selected"]["source_name"]})


def test_multiple_edits_use_original_record_indices_and_are_order_independent(warehouse):
    _, raw, project, ordered = warehouse
    first, second = [record for record in ordered["source_records"] if record.get("op") == "MOV"][:2]
    plans = [edit_for(first, "insert_before", [{"op": "MOV", "args": ["K22", "D20"]}]),
             edit_for(second, "replace", [{"op": "MOV", "args": ["K33", "D21"]}])]
    a = edit_ladder_project(raw, "MAIN", plans)
    b = edit_ladder_project(raw, "MAIN", list(reversed(plans)))
    assert a.data == b.data
    _, current = reread(a)
    records = current["source_records"]
    assert records[first["record_index"]]["args"] == ["K22", "D20"]
    assert records[second["record_index"] + 1]["args"] == ["K33", "D21"]
    expected = raw_records(ordered)
    expected[second["record_index"]] = encode_instruction("MOV", ["K33", "D21"], profile="fx3u", text_encoding=None, plc_model="FX3U/FX3UC")
    expected.insert(first["record_index"], encode_instruction("MOV", ["K22", "D20"], profile="fx3u", text_encoding=None, plc_model="FX3U/FX3UC"))
    assert raw_records(current) == expected


def test_instruction_and_long_comment_are_one_lossless_transaction(warehouse):
    _, raw, project, ordered = warehouse
    source = next(record for record in ordered["source_records"] if record.get("op") == "MOV")
    address = next(iter(ordered["device_comments"]))
    text = "离线编辑的完整设备注释，保留超出CSV常见长度的文本。" * 12 + " 🚚"
    saved = edit_ladder_project(raw, "MAIN", [edit_for(source, instructions=[{"op": "MOV", "args": ["K2", "D0"]}])],
                                device_comments={address: text}, comment_table="COMMENT.qcd")
    after, current = reread(saved)
    assert current["source_records"][source["record_index"]]["args"] == ["K2", "D0"]
    assert current["device_comments"] == {**ordered["device_comments"], address: text}
    assert len(current["device_comments"]) == 22
    assert_other_streams_equal(project, after, {ordered["selected"]["source_name"], "COMMENT.qcd"})
    assert set(saved.report["modified_objects"]) == {"MAIN.Program.pou", "COMMENT.qcd"}


@pytest.mark.parametrize("damage", ["expected_op", "expected_args", "expected_source", "duplicate", "bool_index",
                                   "bad_comment", "missing_table", "replace_end", "delete_end", "embedded_end"])
def test_conflicts_invalid_comments_and_terminal_end_cannot_publish_partial_source(warehouse, damage):
    path, raw, _, ordered = warehouse
    first = next(record for record in ordered["source_records"] if record.get("op") == "MOV")
    edit = edit_for(first, instructions=[{"op": "MOV", "args": ["K2", "D0"]}])
    edits, kwargs = [edit], {}
    if damage == "expected_op":
        edit["expected"]["op"] = "DMOV"
    elif damage == "expected_args":
        edit["expected"]["args"] = ["K99", "D0"]
    elif damage == "expected_source":
        kwargs["expected_source"] = "00"
    elif damage == "duplicate":
        edits.append(deepcopy(edit))
    elif damage == "bool_index":
        edit["record_index"] = True
    elif damage == "bad_comment":
        kwargs["device_comments"] = {"S0": "bad\0comment"}
    elif damage == "missing_table":
        kwargs.update(device_comments={"S0": "changed"}, comment_table="absent.qcd")
    elif damage in {"replace_end", "delete_end"}:
        edits = [edit_for(ordered["source_records"][-1], "replace" if damage == "replace_end" else "delete",
                          [{"op": "MOV", "args": ["K2", "D0"]}] if damage == "replace_end" else [])]
    else:
        edit["instructions"] = [{"op": "end", "args": []}]
    with pytest.raises(GXWFormatError):
        edit_ladder_project(raw, "MAIN", edits, **kwargs)
    assert path.read_bytes() == raw


def project_with_unknown_record(warehouse):
    _, raw, project, ordered = warehouse
    logical = ordered["selected"]["source_name"]
    source = project.streams[logical]
    frame = frame_token_pou(source)
    # A genuinely unknown header is bounded independently; never use the
    # codec under test to create its own expected bytes.
    first = ordered["source_records"][1]["source_offset"]
    changed = bytearray(source[:first] + bytes.fromhex("037f03") + source[first:])
    for offset in (frame.body_offset - 24, frame.body_offset - 20):
        struct.pack_into("<I", changed, offset, struct.unpack_from("<I", source, offset)[0] + 3)
    return write_project_replacements(raw, {logical: (project.mapping[logical], source, bytes(changed))}).data


def test_unknown_surrounding_record_is_visible_and_preserved_during_safe_edit(warehouse):
    raw = project_with_unknown_record(warehouse)
    project = read_offline_project(raw)
    before = read_ordered_program(project)
    unknown, = [row for row in before["source_records"] if row.get("kind") == "opaque"]
    assert unknown["raw_hex"] == "037f03"
    assert before["coverage"]["parse_status"] == "partial"
    assert before['program_ir']['source']['format'] == 'instruction_sequence_v2'
    rows = [row for network in before['program_ir']['networks'] for row in network['ladder']['ordered_instructions']]
    assert [row['record_index'] for row in rows] == list(range(len(before['source_records'])))
    assert [row['raw_hex'] for row in rows] == [row['raw_hex'] for row in before['source_records']]
    assert any(row['severity'] == 'unverified' for row in before['program_ir']['analysis']['findings'])
    target = next(row for row in before["source_records"] if row.get("op") == "MOV")
    saved = edit_ladder_project(raw, "MAIN", [edit_for(target, instructions=[{"op": "MOV", "args": ["K2", "D0"]}])])
    _, after = reread(saved)
    assert raw_records(after)[:target["record_index"]] == raw_records(before)[:target["record_index"]]
    assert raw_records(after)[target["record_index"] + 1:] == raw_records(before)[target["record_index"] + 1:]
    assert after["coverage"]["parse_status"] == "partial"
    assert after['program_ir']['source']['format'] == 'instruction_sequence_v2'


def test_unknown_target_is_explicitly_rejected(warehouse):
    raw = project_with_unknown_record(warehouse)
    current = read_ordered_program(read_offline_project(raw))
    unknown, = [row for row in current["source_records"] if row.get("kind") == "opaque"]
    edit = {"operation": "replace", "record_index": unknown["record_index"],
            "expected": {"op": "UNOBSERVED", "args": []}, "instructions": [{"op": "NOP", "args": []}]}
    with pytest.raises(GXWFormatError, match="opaque"):
        edit_ladder_project(raw, "MAIN", [edit])


def test_source_labels_insert_replace_delete_keep_unknown_records_and_native_bytes(warehouse):
    seed = project_with_unknown_record(warehouse)
    before_project = read_offline_project(seed)
    before = read_ordered_program(before_project)
    terminal = next(row for row in before['source_records'] if row.get('op') == 'END')
    inserted = edit_ladder_project(seed, 'MAIN', [edit_for(terminal, 'insert_before', [
        {'op': 'P256', 'args': []}, {'op': 'I256', 'args': []}])])
    project, program = reread(inserted)
    labels = [row for row in program['source_records'] if row.get('kind') == 'label']
    # Independent native FX converter controls, not an encoder-generated oracle:
    # P256 occupies two steps; I256 still occupies one.
    assert [(row['text'], row['raw_hex']) for row in labels] == [
        ('P256', '043c020405d0000105'), ('I256', '033c0305d1000105')]
    assert raw_records(program)[:-3] == raw_records(before)[:-1]
    assert raw_records(program)[-1] == raw_records(before)[-1]
    assert len(program['listing'].instructions) == 155 and len(program['raw_networks']) == 9
    assert program['program_ir']['source']['format'] == 'instruction_sequence_v2'
    assert_other_streams_equal(before_project, project, {'MAIN.Program.pou'})
    label = labels[0]
    replacement = {'operation': 'replace', 'record_index': label['record_index'],
        'expected': {'op': 'P256', 'args': []}, 'instructions': [{'op': 'P1', 'args': []}]}
    changed = edit_ladder_project(inserted.data, 'MAIN', [replacement])
    _, changed_program = reread(changed)
    changed_label = next(row for row in changed_program['source_records'] if row.get('text') == 'P1')
    assert changed_label['raw_hex'] == '033c0304d00104'
    end = next(row for row in changed_program['source_records'] if row.get('op') == 'END')
    with pytest.raises(GXWFormatError, match='duplicate source label'):
        edit_ladder_project(changed.data, 'MAIN', [edit_for(end, 'insert_before', [{'op': 'P1', 'args': []}])])
    removals = [{'operation': 'delete', 'record_index': row['record_index'],
                 'expected': {'op': row['text'], 'args': []}} for row in changed_program['source_records']
                if row.get('kind') == 'label']
    restored = edit_ladder_project(changed.data, 'MAIN', removals)
    restored_project, restored_program = reread(restored)
    assert raw_records(restored_program) == raw_records(before)
    assert restored_program['device_comments'] == before['device_comments']
    assert_other_streams_equal(before_project, restored_project, {'MAIN.Program.pou'})
    assert warehouse[0].read_bytes() == warehouse[1]


def test_q_project_allocation_context_controls_inserted_and_replaced_instruction_widths():
    raw = private_path('research/experiments/sfc-graph-20260926/public-corpus-discovery/'
        'noeul-native-batch/case-00/input.gxw').read_bytes()
    logical = '기본동작.Program.pou'
    facts = json.loads(private_path('tests/fixtures/gxw_q_token_native_20261021.json').read_text('utf-8'))
    group = next(row for row in facts['allocation_contexts']
                 if row['group'] == 'q71' and row['context_name'] == 'bank2-zr64')
    project = read_offline_project(raw)
    parameter = project.streams['Param.wpa']
    records, offset = [], 0
    while offset + 2 <= len(parameter):
        length, = struct.unpack_from('<H', parameter, offset)
        if length == 0xffff:
            records.append(parameter[offset:])
            break
        record = parameter[offset:offset + length]
        records.append(bytes.fromhex(group['parameter_hex']) if record[2:4] == b'\x00\x20' else record)
        offset += length
    seed = write_project_replacements(raw, {'Param.wpa': (project.mapping['Param.wpa'], parameter, b''.join(records))},
                                      invalidate=False).data
    project = read_offline_project(seed)
    context = project.token_storage_context()
    assert context.status == 'available' and context.has_extended_ranges is True
    before = read_ordered_program(project, logical)
    terminal = next(row for row in before['source_records'] if row.get('op') == 'END')
    inserted = edit_ladder_project(seed, logical, [edit_for(terminal, 'insert_before', [{'op': 'MOV', 'args': ['D16', 'D2']}])])
    after = read_ordered_program(read_offline_project(inserted.data), logical)
    target = after['source_records'][terminal['record_index']]
    oracle = next(row for row in group['results'] if row['text'] == 'MOV D16 D2')
    assert target['raw_hex'] == bytes.fromhex(oracle['raw'])[:-group['end_bytes']].hex()
    changed = edit_ladder_project(inserted.data, logical, [edit_for(target, instructions=[{'op': 'MOV', 'args': ['D15', 'D2']}])])
    current = read_ordered_program(read_offline_project(changed.data), logical)
    oracle = next(row for row in group['results'] if row['text'] == 'MOV D15 D2')
    assert current['source_records'][terminal['record_index']]['raw_hex'] == bytes.fromhex(oracle['raw'])[:-group['end_bytes']].hex()
    assert_other_streams_equal(project, read_offline_project(changed.data), {logical})
    # Missing and ambiguous parameters are unknown; neither becomes a default
    # no-extension allocation or a whole-project reading failure.
    missing = replace(project, streams={name: value for name, value in project.streams.items() if name != 'Param.wpa'})
    ambiguous = replace(project, streams={**project.streams, 'param.WPA': parameter})
    for unavailable in (missing, ambiguous):
        storage_context = unavailable.token_storage_context()
        assert storage_context.status == 'unavailable' and storage_context.has_extended_ranges is None
        with pytest.raises(GXWFormatError, match='allocation context'):
            encode_instruction('MOV', ['D16', 'D2'], profile='q03udv', plc_model=project.context['cpu'],
                               text_encoding=project.context['text_encoding'], storage_context=storage_context)
        assert encode_instruction('LD', ['M0'], profile='q03udv', plc_model=project.context['cpu'],
            text_encoding=project.context['text_encoding'], storage_context=storage_context).hex() == '03000304900004'


@pytest.mark.parametrize('header_name', ['POU_01.Program.pou', 'POU_01.Labels.lh'])
def test_described_ladder_keeps_native_header_and_original_records_when_growing(warehouse, header_name):
    _, raw, project, ordered = warehouse
    control = read_offline_project(private_path(
        'research/experiments/offline-sfc-20261021/described-source-pending-v2/described-input.gxw').read_bytes())
    header = control.streams[header_name]
    descriptions = header[:source_payload_offset(header) - 42]
    logical = ordered['selected']['source_name']
    original = project.streams[logical]
    # Independent native descriptions move the existing Ladder body; its
    # opcode bytes and the native common tail remain the original witness.
    described = descriptions + original[source_payload_offset(original) - 42:]
    seed = write_project_replacements(raw,
        {logical: (project.mapping[logical], original, described)}, invalidate=False).data
    before = read_ordered_program(read_offline_project(seed))
    assert raw_records(before) == raw_records(ordered)
    target = next(row for row in before['source_records'] if row.get('op') == 'MOV')
    saved = edit_ladder_project(seed, 'MAIN', [edit_for(target,
        instructions=[{'op': 'MOV', 'args': ['K32767', 'D1000']}])])
    rebuilt, after = reread(saved)
    source = rebuilt.streams[logical]
    payload = source_payload_offset(described)
    assert source[:payload - 4] == described[:payload - 4]
    assert struct.unpack_from('<I', source, payload - 4)[0] == 1
    assert len(source) > len(described)
    index = target['record_index']
    assert raw_records(after)[:index] == raw_records(before)[:index]
    assert raw_records(after)[index + 1:] == raw_records(before)[index + 1:]
    assert len(after['listing'].instructions) == 155 and len(after['raw_networks']) == 9
    assert after['device_comments'] == ordered['device_comments']


@settings(max_examples=40, deadline=None, derandomize=True)
@given(cut=st.integers(min_value=0, max_value=1000))
def test_described_source_truncation_has_bounded_format_errors(warehouse, cut):
    from gxw.structured_pou import parse_structured_pou
    control = read_offline_project(private_path(
        'research/experiments/offline-sfc-20261021/described-source-pending-v2/described-input.gxw').read_bytes())
    fbd = control.streams['POU_01.Program.pou']
    original = warehouse[2].streams['MAIN.Program.pou']
    ladder = fbd[:source_payload_offset(fbd) - 42] + original[source_payload_offset(original) - 42:]
    for raw, parser in ((fbd, parse_structured_pou), (ladder, frame_token_pou), (ladder, parse_token_pou)):
        end = cut * len(raw) // 1001
        with pytest.raises(GXWFormatError):
            parser(raw[:end])

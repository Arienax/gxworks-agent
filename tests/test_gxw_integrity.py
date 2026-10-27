"""Existing native save fields around the nested CFB, independent of PLC syntax."""
from __future__ import annotations

from functools import lru_cache

from hypothesis import given, settings, strategies as st
import pytest

from gxw.container_writer import validate_cfb_streams
from gxw.integrity import (GXWIntegrityError, inspect_project_integrity, read_project_hdb,
                           rebuild_project_integrity, replace_hdb_streams)
from gxw.models import GXWFormatError
from gxw.project_metadata import logical_mapping
from tools.offline_inputs import private_path


@lru_cache(maxsize=2)
def _native_outer(saved=False):
    # Independently generated CreateProjectHash controls, retained with the
    # original read-only CryptoAPI trace. Expectations come from native output,
    # never a table constructed by the product implementation under test.
    root = private_path('research/experiments/sfc-graph-20260926/public-corpus-discovery/'
                        'project-hash-domain-trace')
    path = root / ('native-saved.gxw' if saved else 'input.gxw')
    if not path.is_file():
        pytest.skip('private frozen native integrity control is unavailable: ' + str(path))
    return validate_cfb_streams(path.read_bytes())


def _source_stream(outer):
    return next(key for logical, key in logical_mapping(outer['projectdatalist.xml']).items()
                if logical.endswith('.pou'))


@pytest.mark.parametrize('saved,suffix_bytes', [(False, 0), (True, 64)])
def test_native_integrity_controls_and_noop_are_exact(saved, suffix_bytes):
    outer = _native_outer(saved)
    envelope = read_project_hdb(outer)
    assert envelope.active and envelope.status == 'verified'
    assert len(envelope.opaque_suffix) == suffix_bytes
    body, allocations = replace_hdb_streams(envelope, envelope.payloads)
    assert body == envelope.data and allocations == []
    assert rebuild_project_integrity(envelope, outer, body) == outer
    assert inspect_project_integrity(outer) == {
        'status': 'verified', 'supported': True, 'opaque_suffix_bytes': suffix_bytes,
        'size_changes_supported': not suffix_bytes}


@pytest.mark.parametrize('member', ['history.xml', 'Project.gd2', '_hdb'])
def test_corrupt_native_records_are_distinguished_from_unknown_layout(member):
    outer = dict(_native_outer())
    changed = bytearray(outer[member])
    offset = -1 if member != 'history.xml' else 0
    changed[offset] = ord('1') if changed[offset] != ord('1') else ord('2')
    outer[member] = bytes(changed)
    assert inspect_project_integrity(outer)['status'] == 'corrupt'
    with pytest.raises(GXWIntegrityError, match='integrity records differ'):
        read_project_hdb(outer)


@pytest.mark.parametrize('entry', [b'unknown envelope', b'A' * 64])
def test_unknown_nonempty_native_envelope_is_not_discarded(entry):
    outer = dict(_native_outer(), **{'Project.gd2': entry})
    before = dict(outer)
    assert inspect_project_integrity(outer)['status'] == 'unsupported'
    with pytest.raises(GXWIntegrityError, match='unsupported nonempty'):
        read_project_hdb(outer)
    assert outer == before


@pytest.mark.parametrize('empty_entry', [None, b''])
def test_ordinary_projects_never_acquire_a_native_table(empty_entry):
    from tests.test_gxw_project_writer import cfb_fixture
    outer = {'_hdb': cfb_fixture({'1': b'unchanged', '2': b'other'}), 'history.xml': b'history'}
    if empty_entry is not None:
        outer['Project.gd2'] = empty_entry
    envelope = read_project_hdb(outer)
    assert not envelope.active and envelope.status == 'absent'
    body, _ = replace_hdb_streams(envelope, {'1': b'longer source'})
    result = rebuild_project_integrity(envelope, outer, body)
    assert result.get('Project.gd2') == empty_entry
    assert read_project_hdb(result).payloads == {'1': b'longer source', '2': b'other'}
    assert result['history.xml'] == b'history'


@pytest.mark.parametrize('size', [0, 1, 4095, 4096, 9000])
def test_aligned_native_container_supports_growth_shrink_and_unchanged_payloads(size):
    outer = _native_outer()
    envelope = read_project_hdb(outer)
    stream = _source_stream(outer)
    replacement = b'v' * size
    body, allocations = replace_hdb_streams(envelope, {stream: replacement})
    result = rebuild_project_integrity(envelope, outer, body)
    actual = read_project_hdb(result)
    assert actual.payloads == {**envelope.payloads, stream: replacement}
    assert all(result[key] == value for key, value in outer.items()
               if key not in ('_hdb', 'Project.gd2'))
    assert allocations and actual.active


@settings(max_examples=20, deadline=None, derandomize=True)
@given(replacement=st.binary(min_size=0, max_size=6000))
def test_native_transaction_retains_independent_payloads_for_arbitrary_edits(replacement):
    outer = _native_outer()
    envelope = read_project_hdb(outer)
    stream = _source_stream(outer)
    before = dict(outer)
    body, _ = replace_hdb_streams(envelope, {stream: replacement})
    updated = rebuild_project_integrity(envelope, outer, body)
    restored = read_project_hdb(updated)
    assert restored.payloads[stream] == replacement
    assert {k: v for k, v in restored.payloads.items() if k != stream} == {
        k: v for k, v in envelope.payloads.items() if k != stream}
    assert outer == before
    assert rebuild_project_integrity(restored, updated, restored.data) == updated


def test_partial_native_suffix_is_preserved_and_size_changes_refused():
    outer = _native_outer(True)
    envelope = read_project_hdb(outer)
    stream = _source_stream(outer)
    original = envelope.payloads[stream]
    replacement = bytes([original[0] ^ 1]) + original[1:]
    body, _ = replace_hdb_streams(envelope, {stream: replacement})
    result = rebuild_project_integrity(envelope, outer, body)
    actual = read_project_hdb(result)
    assert actual.payloads == {**envelope.payloads, stream: replacement}
    assert actual.opaque_suffix == envelope.opaque_suffix
    with pytest.raises(GXWIntegrityError, match='opaque partial'):
        replace_hdb_streams(envelope, {stream: replacement + b'grow'})
    assert envelope.payloads[stream] == original


def test_failed_multi_stream_transaction_does_not_mutate_the_baseline():
    outer = _native_outer()
    envelope = read_project_hdb(outer)
    stream = _source_stream(outer)
    before = dict(envelope.payloads)
    with pytest.raises(GXWFormatError, match='cannot add a nested stream'):
        replace_hdb_streams(envelope, {stream: b'changed', 'not-registered': b'failure'})
    assert envelope.payloads == before
    assert rebuild_project_integrity(envelope, outer, envelope.data) == outer


def test_missing_integrity_input_is_corrupt_not_a_plain_project():
    outer = dict(_native_outer())
    del outer['securitylevel.xml']
    assert inspect_project_integrity(outer)['status'] == 'corrupt'
    with pytest.raises(GXWIntegrityError, match='inputs are missing'):
        read_project_hdb(outer)


def test_diagnostic_readers_share_native_active_envelope_and_preserve_rejections(monkeypatch):
    """ST readback uses these inventories; the envelope oracle is native saved.

    The independent active-envelope witness contains ordinary Ladder, so this
    checks only the common GXW readers and does not invent native ST evidence.
    """
    import subprocess
    from gxw import integrity
    from gxw.container_writer import replace_project_stream
    from gxw.diagnostic_sources import (_native_validation_sources, _native_validation_library_sources,
                                         _native_assignment_sources)
    from gxw.diagnostic_declarations import _native_validation_declaration_owners
    from gxw.native_write import _native_scope_inventory
    from gxw.project_metadata import _project_allocation_records
    raw = private_path('research/experiments/sfc-graph-20260926/public-corpus-discovery/'
                       'project-hash-domain-trace/native-saved.gxw').read_bytes()
    outer = _native_outer(True)
    envelope = read_project_hdb(outer)
    assert envelope.active and len(envelope.opaque_suffix) == 64
    seen = []
    def read_current(value):
        seen.append(value['Project.gd2'])
        return read_project_hdb(value)
    def forbidden(*args, **kwargs):
        raise AssertionError('a saved-source diagnostic reader attempted native execution')
    monkeypatch.setattr(integrity, 'read_project_hdb', read_current)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    context, programs = _native_validation_sources(raw)
    assert context['cpu'] and len(programs) == 1 and programs[0]['name'] == 'MAIN'
    selected = programs[0]
    mapping = logical_mapping(outer['projectdatalist.xml'])
    assert selected['raw'] == envelope.payloads[mapping[selected['logical_name']]]
    assert _native_validation_library_sources(raw) == []
    local, globals_ = _native_assignment_sources(raw)
    assert local['MAIN'].raw == selected['declarations'].raw
    assert globals_ and all(document.raw == envelope.payloads[mapping[name]] for name, document in globals_.items())
    owners = _native_validation_declaration_owners(raw)
    assert {(row['role'], row['logical_name']): row['declarations'].raw for row in owners} == {
        ('global', name): document.raw for name, document in globals_.items()}
    scope, tasks = _native_scope_inventory(raw)
    assert set(scope) <= mapping.keys() and isinstance(tasks, list)
    assert any(program['name'] == selected['name'] for row in tasks for program in row['programs'])
    metadata_name, metadata, application = _project_allocation_records(raw)
    assert metadata == envelope.payloads[mapping[metadata_name]]
    assert seen and all(value == outer['Project.gd2'] for value in seen)
    readers = (_native_validation_sources, _native_validation_library_sources,
               _native_assignment_sources, _native_validation_declaration_owners,
               _native_scope_inventory, _project_allocation_records)
    corrupt = ('0' if outer['Project.gd2'][:1] != b'0' else '1').encode() + outer['Project.gd2'][1:]
    for value, status in ((b'unknown envelope', 'unsupported'), (corrupt, 'corrupt')):
        changed, _ = replace_project_stream(raw, 'Project.gd2', value)
        for reader in readers:
            with pytest.raises(GXWIntegrityError) as caught:
                reader(changed)
            assert caught.value.status == status


@lru_cache(maxsize=1)
def _native_partial_cases():
    import json
    return json.loads(private_path('tests/fixtures/gxw_native_partial_hdb.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=11)
def _native_partial_outer(case_id):
    from zipfile import ZipFile
    fixture = _native_partial_cases()
    case = next(row for row in fixture['cases'] if row['id'] == case_id)
    with ZipFile(private_path(fixture['archive'])) as archive:
        return (validate_cfb_streams(archive.read(case['project_member'])),
                archive.read(case['cgtable_member']))


@pytest.mark.parametrize('case', _native_partial_cases()['cases'], ids=lambda row: row['id'])
def test_native_saved_partial_final_stream_is_live_data_with_exact_noop(case):
    """Independent Q13UDV/Q06UDV/Q25PH SaveProject and cold-read controls."""
    from gxw.integrity import INTEGRITY_TABLE_BYTES
    outer, expected = _native_partial_outer(case['id'])
    envelope = read_project_hdb(outer)
    assert envelope.active and envelope.status == 'verified'
    assert envelope.opaque_suffix == b''
    assert len(envelope.data) == case['body_bytes']
    assert len(envelope.data) % 512 == case['partial_bytes']
    assert envelope.data == outer['_hdb'][:-INTEGRITY_TABLE_BYTES]
    assert envelope.payloads[case['physical_stream']] == expected
    assert len(expected) == case['stream_bytes']
    assert len(envelope.payloads) == case['payload_count']
    body, allocations = replace_hdb_streams(envelope, envelope.payloads)
    assert body == envelope.data and allocations == []
    assert rebuild_project_integrity(envelope, outer, body) == outer
    assert inspect_project_integrity(outer) == {
        'status': 'verified', 'supported': True, 'opaque_suffix_bytes': 0,
        'size_changes_supported': True}


@pytest.mark.parametrize('change', ['grow', 'shrink_to_mini'])
def test_native_partial_stream_can_be_edited_after_padding_without_losing_other_payloads(change):
    case = _native_partial_cases()['cases'][0]
    outer, original = _native_partial_outer(case['id'])
    envelope = read_project_hdb(outer)
    stream = case['physical_stream']
    replacement = original + b'new table data' * 500 if change == 'grow' else original[:128]
    body, allocations = replace_hdb_streams(envelope, {stream: replacement})
    assert allocations[0]['mode'].startswith('pad_final_sector+')
    updated = rebuild_project_integrity(envelope, outer, body)
    actual = read_project_hdb(updated)
    assert actual.active and actual.opaque_suffix == b''
    assert actual.payloads == {**envelope.payloads, stream: replacement}
    assert all(updated[key] == value for key, value in outer.items()
               if key not in ('_hdb', 'Project.gd2'))
    assert envelope.payloads[stream] == original
    assert rebuild_project_integrity(actual, updated, actual.data) == updated


def _resign_native_body(outer, body):
    # Refresh the pre-existing native format fields only to isolate structural
    # rejection. A valid integrity record must never repair missing payloads.
    from gxw.integrity import INTEGRITY_MEMBERS, _native_integrity_record
    updated = dict(outer)
    table = b''.join(_native_integrity_record(body if name == '_hdb' else updated[name])
                     for name in INTEGRITY_MEMBERS)
    updated['_hdb'] = body + table
    updated['Project.gd2'] = _native_integrity_record(updated['_hdb'])
    return updated


@settings(max_examples=12, deadline=None, derandomize=True)
@given(missing=st.integers(min_value=1, max_value=64), extra=st.binary(min_size=1, max_size=64))
def test_native_partial_stream_never_repairs_missing_data_or_consumes_unexplained_bytes(missing, extra):
    case = _native_partial_cases()['cases'][0]
    outer, _ = _native_partial_outer(case['id'])
    body = read_project_hdb(outer).data
    truncated = _resign_native_body(outer, body[:-missing])
    assert inspect_project_integrity(truncated)['status'] == 'corrupt'
    with pytest.raises(GXWFormatError):
        read_project_hdb(truncated)
    # All real payloads remain readable, but the partial allocated sector now
    # has bytes outside the exact declaration. They cannot become padding.
    extended = body + extra
    assert validate_cfb_streams(extended) == validate_cfb_streams(body)
    unknown = _resign_native_body(outer, extended)
    assert inspect_project_integrity(unknown)['status'] == 'unsupported'
    with pytest.raises(GXWIntegrityError, match='intersects an allocated sector'):
        read_project_hdb(unknown)


def test_native_partial_orphan_allocated_sector_is_still_unsupported():
    import struct
    from gxw.container import CompoundFile, ENDOFCHAIN, FREESECT
    outer = _native_outer(True)
    envelope = read_project_hdb(outer)
    assert envelope.opaque_suffix
    body = bytearray(envelope.data)
    cfb = CompoundFile(body)
    sector = len(body) // cfb.sector_size - 1
    assert cfb._fat[sector] == FREESECT
    per_fat = cfb.sector_size // 4
    fat_sector = cfb._fat_sector_ids[sector // per_fat]
    struct.pack_into('<I', body, (fat_sector + 1) * cfb.sector_size + sector % per_fat * 4, ENDOFCHAIN)
    assert validate_cfb_streams(body) == envelope.payloads
    with pytest.raises(GXWIntegrityError, match='intersects an allocated sector'):
        read_project_hdb(_resign_native_body(outer, bytes(body)))


@lru_cache(maxsize=1)
def _native_fat_boundary_control():
    import json
    from zipfile import ZipFile
    fixture = json.loads(private_path('tests/fixtures/gxw_native_hdb_fat_boundary.json').read_text(encoding='utf-8'))
    with ZipFile(private_path(fixture['archive'])) as archive:
        return (fixture, validate_cfb_streams(archive.read(fixture['native_member'])),
                validate_cfb_streams(archive.read(fixture['input_member'])),
                archive.read(fixture['historical_table_member']))


def test_native_resave_prior_table_crossing_fat_capacity_is_opaque_and_noop_exact():
    from gxw.integrity import INTEGRITY_TABLE_BYTES
    from gxw.project_metadata import project_stream_mapping
    fixture, outer, original, prior_table = _native_fat_boundary_control()
    envelope = read_project_hdb(outer)
    assert envelope.active and envelope.opaque_suffix == prior_table
    assert prior_table == original['_hdb'][-INTEGRITY_TABLE_BYTES:]
    assert len(envelope.opaque_suffix) == fixture['opaque_suffix_bytes'] == 576
    assert len(envelope.data) == fixture['body_bytes']
    assert envelope.data == outer['_hdb'][:-INTEGRITY_TABLE_BYTES]
    assert envelope.payloads == validate_cfb_streams(envelope.data[:-INTEGRITY_TABLE_BYTES])
    assert len(envelope.payloads) == fixture['payload_count']
    before = read_project_hdb(original)
    before_map, after_map = project_stream_mapping(original), project_stream_mapping(outer)
    # Native save/cold-read keeps the final edited source and real QCD bytes.
    for name in before_map:
        if name.endswith(('.pou', '.qcd')):
            assert before.payloads[before_map[name]] == envelope.payloads[after_map[name]]
    body, allocations = replace_hdb_streams(envelope, envelope.payloads)
    assert body == envelope.data and allocations == []
    assert rebuild_project_integrity(envelope, outer, body) == outer
    assert inspect_project_integrity(outer) == {
        'status': 'verified', 'supported': True, 'opaque_suffix_bytes': 576,
        'size_changes_supported': False}
    stream = fixture['source_stream']
    with pytest.raises(GXWIntegrityError, match='opaque partial'):
        replace_hdb_streams(envelope, {stream: envelope.payloads[stream] + b'growth'})


@pytest.mark.parametrize('damage', ['unrecognized_table', 'incomplete_table', 'allocated_history_sector'])
def test_native_resave_fat_boundary_does_not_admit_unknown_or_allocated_tails(damage):
    import struct
    from gxw.container import CompoundFile, ENDOFCHAIN, FREESECT
    _, outer, _, _ = _native_fat_boundary_control()
    body = bytearray(read_project_hdb(outer).data)
    if damage == 'unrecognized_table':
        body[-576] = ord('g')
    elif damage == 'incomplete_table':
        body.pop()
    else:
        cfb = CompoundFile(body)
        sid = len(cfb._fat) - 1
        assert cfb._fat[sid] == FREESECT
        per_fat = cfb.sector_size // 4
        fat_sector = cfb._fat_sector_ids[sid // per_fat]
        struct.pack_into('<I', body, (fat_sector + 1) * cfb.sector_size + sid % per_fat * 4, ENDOFCHAIN)
    assert validate_cfb_streams(body) == read_project_hdb(outer).payloads
    damaged = _resign_native_body(outer, bytes(body))
    assert inspect_project_integrity(damaged)['status'] == 'unsupported'
    with pytest.raises(GXWIntegrityError, match='intersects an allocated sector'):
        read_project_hdb(damaged)

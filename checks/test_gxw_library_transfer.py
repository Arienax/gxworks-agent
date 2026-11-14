"""Offline source-bound library readback, using independent frozen native inputs.

The original Q06UDV -> L02 library transfer contains ST definitions. FBD cases
substitute the already-frozen C/D network bodies into its existing library
owner; they verify offline readback, not a new vendor import/save operation.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from functools import lru_cache
import json
import os
from pathlib import Path
import struct
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
PRIVATE = Path(os.environ.get('GXWORKS_AGENT_PRIVATE_ROOT', ROOT.parent / 'gxworks-agent-private'))
ARCHIVE = PRIVATE / 'research/evidence/gxw-authored-fbd-native-transfer-20261013.zip'
NETWORKS = PRIVATE / 'tests/fixtures/gxw_network_blocks_20260913.json'
LIBRARY = 'MotionControl_JEC_V100A'
DEFINITION = 'MC_Power_JEC'
CALLER = 'POU_01.Program.pou'
pytestmark = pytest.mark.skipif(not ARCHIVE.is_file() or not NETWORKS.is_file(),
                                reason='requires the existing private native library/network inputs')

from gxw.container_writer import replace_project_stream, validate_cfb_streams
from gxw.library_transfer import library_inventory, library_transfer_plan, verify_library_import
from gxw.models import GXWFormatError
from gxw.project_metadata import project_stream_mapping, synchronize_history
from gxw.source_header import source_payload_offset
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import serialize_structured_pou


def _streams(raw):
    outer = validate_cfb_streams(raw)
    payloads = validate_cfb_streams(outer['_hdb'])
    mapping = project_stream_mapping(outer)
    return outer, {name: payloads[key] for name, key in mapping.items() if key in payloads}, mapping


def _replay(raw, logical, payload):
    """Container-only substitution; never represents a native save."""
    outer, streams, mapping = _streams(raw)
    nested, _ = replace_project_stream(outer['_hdb'], mapping[logical], payload)
    result, _ = replace_project_stream(raw, '_hdb', nested)
    history, _, _ = synchronize_history(outer['history.xml'],
        {logical: (mapping[logical], streams[logical], payload)}, current_mapping=mapping)
    return replace_project_stream(result, 'history.xml', history)[0]


@lru_cache(maxsize=1)
def _native_states():
    with zipfile.ZipFile(ARCHIVE) as archive:
        return (archive.read('library-import-L02/input.gxw'), archive.read('author-Q06UDV.gxw'),
                archive.read('library-import-L02/candidate.gxw'))


def _library_body(raw):
    streams = _streams(raw)[1]
    logical, = [name for name in streams if name.startswith(DEFINITION + '.')
                and name.endswith('\\' + LIBRARY + '.lnb')]
    return logical, streams[logical]


def _network_body(case='C'):
    fixture = json.loads(NETWORKS.read_text(encoding='utf-8'))
    return base64.b64decode(fixture[case]['program_pou_base64'])


def _with_fbd(raw, case='C'):
    logical, previous = _library_body(raw)
    captured = _network_body(case)
    return _replay(raw, logical, previous[:source_payload_offset(previous)] +
                   captured[source_payload_offset(captured):])


@lru_cache(maxsize=1)
def _fbd_states():
    before, donor, saved = _native_states()
    return before, _with_fbd(donor), _with_fbd(saved)


def _definition(inventory):
    library, = inventory['libraries']
    return next(row for row in library['definitions'] if row['name'] == DEFINITION)


def test_fbd_library_inventory_uses_the_selected_owner_and_frozen_network_order():
    _, donor, _ = _fbd_states()
    entry = _definition(library_inventory(donor, LIBRARY, include_source=True))
    assert entry['language'] == 'fbd' and 'source_text' not in entry
    graph = entry['source_graph']['model']
    # Independent native C control: two networks, X1 -> Y1 then X2 -> Y2.
    assert [node['symbol'] for node in graph['nodes']] == ['X1', 'Y1', 'X2', 'Y2']
    assert [node['block'] for node in graph['nodes']] == [0, 0, 1, 1]
    assert len(graph['blocks']) == 2 and graph['wires']
    assert graph['unknown_record_count'] == 0
    assert entry['source_graph']['payload_hex'] == _network_body()[source_payload_offset(_network_body()):].hex()
    assert 'source_graph' not in _definition(library_inventory(donor, LIBRARY))
    json.dumps(entry)  # The existing inventory remains JSON transport.


def test_fbd_library_import_matches_its_source_and_ignores_only_project_source_headers():
    before, donor, saved = _fbd_states()
    logical, body = _library_body(donor)
    count = struct.unpack_from('<I', body)[0]
    description = 'Independent donor description\0'.encode('utf-16le')
    donor = _replay(donor, logical, struct.pack('<I', len(description) // 2) +
                    description + body[4 + 2 * count:])
    plan = library_transfer_plan(before, donor, LIBRARY)
    assert plan['source_cpu'] == 'Q06UDV' and plan['cpu'] == 'L02'
    result = verify_library_import(before, donor, LIBRARY, saved, {})
    assert result.data == saved
    assert result.report['validation']['source_graph'] == 'matched'
    assert result.report['validation']['source_text'] == 'matched'  # The other 15 definitions remain ST.
    assert result.report['validation']['gx_compile'] == 'not_run'
    assert len(result.report['library_import']['definitions']) == 16


@pytest.mark.parametrize('change', ['network_order', 'wire', 'opaque_trailer', 'invalid_block_count'])
def test_fbd_library_import_rejects_graph_and_preserved_payload_changes(change):
    before, donor, saved = _fbd_states()
    logical, body = _library_body(saved)
    if change == 'network_order':
        mutant = _with_fbd(saved, 'D')  # Independently captured reversed-network control.
    elif change == 'wire':
        program = parse_structured_pou(body)
        first = program.wires[0]
        axis = 'y' if first.start.x == first.end.x else 'x'
        changed = replace(first, end=replace(first.end, **{axis: getattr(first.end, axis) + 1}))
        mutant = _replay(saved, logical, serialize_structured_pou(
            replace(program, wires=(changed, *program.wires[1:]))))
    else:
        changed = bytearray(body)
        if change == 'opaque_trailer':
            changed[-1] ^= 1  # Same editable graph, changed retained 24-byte suffix.
        else:
            struct.pack_into('<I', changed, source_payload_offset(body) + 13, 0)
        mutant = _replay(saved, logical, bytes(changed))
    with pytest.raises(GXWFormatError, match='definitions differ|structured block count') as failure:
        verify_library_import(before, donor, LIBRARY, mutant, {})
    if change == 'opaque_trailer':
        # A late native suffix change must be visible even when both payloads
        # have identical first 120 hex characters.
        payload_size = len(body) - source_payload_offset(body)
        assert f'byte offset {payload_size - 1} ' in str(failure.value)
        assert "'00' -> '01'" in str(failure.value)


@pytest.mark.parametrize('change, message', [
    ('caller', 'changed target caller'),
    ('declarations', 'changed target caller'),
    ('task', 'changed target task scopes'),
])
def test_fbd_library_import_preserves_target_callers_declarations_and_tasks(change, message):
    from gxw.declarations import edit_declarations, parse_declarations, serialize_declarations
    from gxw.project_metadata import read_task_source

    before, donor, saved = _fbd_states()
    streams = _streams(saved)[1]
    if change == 'caller':
        program = parse_structured_pou(streams[CALLER])
        logical = CALLER
        payload = serialize_structured_pou(replace(program, canvas_height=program.canvas_height + 2))
    elif change == 'declarations':
        logical = 'POU_01.Labels.lh'
        document = parse_declarations(streams[logical], logical_name=logical)
        payload = serialize_declarations(edit_declarations(document,
            upserts=[{'name': 'CallerFlag', 'data_type': 'BOOL'}]))
    else:
        logical = 'Task_01.tsk'
        payload = bytearray(streams[logical])
        payload[read_task_source(payload)['count_offset'] - 8] ^= 1
        payload = bytes(payload)
    with pytest.raises(GXWFormatError, match=message):
        verify_library_import(before, donor, LIBRARY, _replay(saved, logical, payload), {})


def test_existing_st_library_readback_keeps_its_text_and_initial_value_report():
    before, donor, saved = _native_states()
    result = verify_library_import(before, donor, LIBRARY, saved, {})
    entry = next(row for row in result.report['library_import']['definitions'] if row['name'] == DEFINITION)
    assert entry['language'] == 'st' and entry['source_text']
    assert 'source_graph' not in entry and 'source_graph' not in result.report['validation']
    assert result.report['validation']['source_text'] == 'matched'
    assert result.report['native_initial_value_changes'] == []
    assert result.report['validation']['initial_value_semantics'] == 'unverified'

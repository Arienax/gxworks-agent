import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import sys

import pytest

from src.gxw.models import GXWFormatError
from src.gxw.structured_pou import parse_structured_pou
from src.gxw.structured_pou_writer import serialize_structured_pou, replace_node_symbol

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/gxw_network_blocks_20260913.json').read_text(encoding='utf-8'))


def raw(name):
    data = base64.b64decode(FIXTURE[name]['program_pou_base64'])
    assert hashlib.sha256(data).hexdigest() == FIXTURE[name]['program']['sha256']
    return data


def test_research_probe_recognizes_native_controls_and_exact_order_swap():
    sys.path.insert(0, str(Path(__file__).parents[1] / 'research'))
    from probe_block_boundaries import probe
    for name, count in [('A', 1), ('B', 1), ('C', 2), ('D', 2), ('E', 2), ('M1', 2)]:
        result = probe(raw(name))
        assert result['status'] == 'hypothesis_only'  # never a native validation claim
        assert result['candidate_block_count'] == count
    c, d = probe(raw('C')), probe(raw('D'))
    assert [b['sha256'] for b in c['blocks']] == [b['sha256'] for b in reversed(d['blocks'])]
    assert raw('M1') == raw('M1_compile')


@pytest.mark.parametrize('offset,value', [(0x43, 0xffffffff), (71, 0xffffffff), (91, 1000), (95, 400)])
def test_research_probe_rejects_bad_bounds_without_discarding_source(offset, value):
    sys.path.insert(0, str(Path(__file__).parents[1] / 'research'))
    from probe_block_boundaries import probe
    data = bytearray(raw('C'))
    struct.pack_into('<I', data, offset, value)
    result = probe(bytes(data))
    assert result['status'] == 'hypothesis_rejected'
    assert result['raw_hex'] == data.hex()


@pytest.mark.parametrize('name,count', [('A', 1), ('B', 1), ('C', 2), ('D', 2), ('E', 2), ('M1', 2)])
def test_parser_exposes_native_block_membership(name, count):
    p = parse_structured_pou(raw(name))
    assert len(p.blocks) == count
    assert sum(b.record_count for b in p.blocks) == p.record_count
    assert sum(b.byte_length for b in p.blocks) + 71 + 24 == len(p.raw)
    assert [r.offset for _, records in p.block_records() for r in records] == [r.offset for r in p.iter_records()]
    if name == 'C':
        assert [[n.symbol for n in view.nodes] for view in p.block_views()] == [['X1', 'Y1'], ['X2', 'Y2']]


@pytest.mark.parametrize('offset,value', [(0x43, 0), (0x43, 0xffffffff), (71, 23), (71, 0xffffffff), (91, 6), (95, 400)])
def test_parser_rejects_cross_block_or_count_corruption(offset, value):
    data = bytearray(raw('C'))
    struct.pack_into('<I', data, offset, value)
    with pytest.raises(GXWFormatError):
        parse_structured_pou(bytes(data))


@pytest.mark.parametrize('name', list(FIXTURE))
def test_every_native_block_fixture_serializes_byte_exactly(name):
    assert serialize_structured_pou(parse_structured_pou(raw(name))) == raw(name)


def test_variable_length_node_edit_does_not_damage_the_next_block():
    p = parse_structured_pou(raw('C'))
    q = parse_structured_pou(serialize_structured_pou(replace_node_symbol(p, 'X1', 'X100')))
    assert q.blocks[1].offset == p.blocks[1].offset + 4
    assert q.raw[q.blocks[1].offset:-24] == p.raw[p.blocks[1].offset:-24]


def test_identical_coordinates_in_different_blocks_never_connect():
    from src.gxw.connectivity import build_connectivity_graph
    p = parse_structured_pou(raw('C'))
    graph = build_connectivity_graph(p)
    x1, y1, x2, y2 = p.nodes
    assert x1.bbox == x2.bbox
    assert graph.ports_connected(x1.offset, 1, y1.offset, 0)
    assert graph.ports_connected(x2.offset, 1, y2.offset, 0)
    assert not graph.ports_connected(x1.offset, 0, x2.offset, 0)
    assert not graph.ports_connected(y1.offset, 0, y2.offset, 0)


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'foreign'])
def test_multi_block_membership_is_a_complete_partition(damage):
    p = parse_structured_pou(raw('C'))
    offsets = p.blocks[1].record_offsets
    if damage == 'missing': offsets = offsets[:-1]
    if damage == 'duplicate': offsets = (*offsets, p.blocks[0].record_offsets[0])
    if damage == 'foreign': offsets = (*offsets[:-1], 987654)
    p = replace(p, blocks=(p.blocks[0], replace(p.blocks[1], record_offsets=offsets)))
    with pytest.raises(GXWFormatError, match='membership'):
        serialize_structured_pou(p)


def test_opaque_block_header_and_unknown_record_survive_noop_and_other_block_edit():
    data = bytearray(raw('C'))
    data[75:87] = bytes(range(12))
    struct.pack_into('<I', data, 99, 0xface)  # safely bounded, unknown class
    p = parse_structured_pou(bytes(data))
    assert serialize_structured_pou(p) == bytes(data)
    q = parse_structured_pou(serialize_structured_pou(replace_node_symbol(p, 'X2', 'X200')))
    assert q.blocks[0].raw_header == p.blocks[0].raw_header
    assert q.unknown_records[0].raw == p.unknown_records[0].raw


def test_object_model_can_reproduce_native_c_and_swap_order():
    from src.gxw.object_model import export_object_model, build_object_program
    p = parse_structured_pou(raw('C'))
    model = export_object_model(p)
    assert serialize_structured_pou(build_object_program(p, model)) == p.raw
    model['blocks'].reverse()
    for item in (*model['nodes'], *model['wires']):
        item['block'] = 1 - item['block']
    swapped = serialize_structured_pou(build_object_program(p, model))
    assert swapped[71:] == raw('D')[71:]


def test_object_model_cannot_omit_blocks_or_cross_wire_endpoints():
    from src.gxw.object_model import export_object_model, build_object_program
    p = parse_structured_pou(raw('C'))
    model = export_object_model(p)
    del model['blocks']
    with pytest.raises(GXWFormatError, match='explicit'):
        build_object_program(p, model)
    model = export_object_model(p)
    model['wires'].append({'block': 0, 'from': model['nodes'][0]['id'] + '.OUT',
                           'to': model['nodes'][3]['id'] + '.IN'})
    with pytest.raises(GXWFormatError, match='cross block'):
        build_object_program(p, model)


def test_object_model_cannot_silently_remove_unknown_block_contents():
    from src.gxw.object_model import export_object_model, build_object_program
    data = bytearray(raw('C'))
    struct.pack_into('<I', data, 99, 0xface)
    p = parse_structured_pou(bytes(data))
    model = export_object_model(p)
    assert serialize_structured_pou(build_object_program(p, model)) == bytes(data)
    del model['blocks'][0]['source_offset']
    with pytest.raises(GXWFormatError, match='unknown records'):
        build_object_program(p, model)


def test_project_writer_preserves_native_multi_block_noop_and_unrelated_payloads():
    import zipfile
    from src.gxw.object_model import read_project, export_object_model, generate_object_project
    from src.gxw.experiment import compare_projects
    archive = Path(__file__).parents[1] / 'research/evidence/gxw-block-boundary-20260913.zip'
    with zipfile.ZipFile(archive) as z:
        baseline = z.read('samples/C.gxw')
    p, declarations, _ = read_project(baseline)
    model = export_object_model(p, declarations)
    assert generate_object_project(model, baseline=baseline).data == baseline
    model['nodes'][0]['symbol'] = 'X100'
    result = generate_object_project(model, baseline=baseline)
    assert list(compare_projects(baseline, result.data)['logical_stream_changes']) == ['1.Program.pou']


def test_relay_lowering_matches_all_expected_boolean_assignments_and_has_two_blocks():
    from src.gxw.object_model import generate_object_project, read_project
    from src.gxw.ladder_lowering import ladder_to_object_model
    sys.path.insert(0, str(Path(__file__).parents[1] / 'research'))
    from verify_relay_blocks import relay_truth_table
    source = json.loads((Path(__file__).parents[1] / 'research/models/relay-parallel.json').read_text())
    result = generate_object_project(ladder_to_object_model(source))
    p, _, _ = read_project(result.data)
    assert [[n.symbol for n in v.nodes] for v in p.block_views()] == [['X0', 'X1', 'X2', 'Y0', 'Y1'], ['X3', 'Y2']]
    table = relay_truth_table(p)
    assert len(table) == 16
    for row in table:
        x = row['inputs']
        expected = x['X0'] and (x['X1'] or not x['X2'])
        assert row['outputs'] == {'Y0': expected, 'Y1': expected, 'Y2': x['X3']}


def test_preview_places_local_canvases_in_separate_viewports():
    import xml.etree.ElementTree as ET
    from src.gxw.render import render_structured_svg
    root = ET.fromstring(render_structured_svg(parse_structured_pou(raw('C'))))
    blocks = [n for n in root if 'data-block-index' in n.attrib]
    assert len(blocks) == 2
    assert float(blocks[1].attrib['y']) == float(blocks[0].attrib['height'])


@pytest.mark.parametrize('manifest_name', ['evidence-manifest.json', 'generation-evidence-manifest.json'])
def test_archived_native_evidence_hashes_are_complete(manifest_name):
    import zipfile
    root = Path(__file__).parents[1]
    manifest = json.loads((root / 'research/results/block-boundary-20260913' / manifest_name).read_text(encoding='utf-8'))
    assert '\\' not in manifest['archive']
    assert ':' not in manifest['archive'].split('/', 1)[0]
    archive = root / manifest['archive']
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256']
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {f['member'] for f in manifest['files']}
        for entry in manifest['files']:
            data = z.read(entry['member'])
            assert len(data) == entry['size']
            assert hashlib.sha256(data).hexdigest() == entry['sha256']


def test_current_generator_reproduces_natively_validated_relay_bytes():
    import zipfile
    from src.gxw.object_model import generate_object_project, read_project
    from src.gxw.ladder_lowering import ladder_to_object_model
    from src.gxw.experiment import compare_programs
    root = Path(__file__).parents[1]
    model = json.loads((root / 'research/models/relay-parallel.json').read_text())
    with zipfile.ZipFile(root / 'research/evidence/gxw-block-generation-20260913.zip') as z:
        generated, native = z.read('samples/R.gxw'), z.read('samples/R_compile.gxw')
    assert generate_object_project(ladder_to_object_model(model)).data == generated
    p, _, _ = read_project(generated)
    q, _, _ = read_project(native)
    assert p.raw == q.raw
    assert len(q.blocks) == 2
    assert all(compare_programs(p, q)['checks'].values())


def test_single_stream_mutation_is_reproducible_from_frozen_native_controls():
    import zipfile
    sys.path.insert(0, str(Path(__file__).parents[1] / 'research'))
    from mutate_block_boundary import transplant
    with zipfile.ZipFile(Path(__file__).parents[1] / 'research/evidence/gxw-block-boundary-20260913.zip') as z:
        result, report = transplant(z.read('samples/B.gxw'), z.read('samples/C.gxw'))
        assert result == z.read('samples/M1.gxw')
    assert list(report['logical_stream_changes']) == ['1.Program.pou']


def test_object_model_rejects_inconsistent_aggregate_height():
    from src.gxw.object_model import export_object_model, build_object_program
    p = parse_structured_pou(raw('C'))
    model = export_object_model(p)
    model['canvas_height'] += 1
    with pytest.raises(GXWFormatError, match='derived'):
        build_object_program(p, model)

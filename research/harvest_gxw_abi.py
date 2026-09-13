"""Offline, evidence-gated ABI harvesting. Never operates GX Works2 or a PLC.

Native observations are hash-bound attestations, not conclusions from parsing.
This tool emits candidates/reports; it never changes the production registry.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gxw.connectivity import build_connectivity_graph
from gxw.container import CompoundFile
from gxw.declarations import parse_declarations
from gxw.experiment import canonical_program, field_diff
from gxw.models import GXWFormatError
from gxw.project_metadata import logical_mapping
from gxw.project_writer import binary_diff, sha256, write_new_file
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import serialize_structured_pou
from probe_block_boundaries import compare, snapshot


def artifact(ref, root=ROOT):
    """Read immutable loose/ZIP evidence without extracting or trusting paths."""
    path = (root / ref['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('evidence path escapes repository')
    if ref.get('member'):
        with ZipFile(path) as archive:
            raw = archive.read(ref['member'])
    else:
        raw = path.read_bytes()
    if sha256(raw) != ref['sha256']:
        raise ValueError(f"evidence hash mismatch: {ref['path']} {ref.get('member', '')}")
    return raw


def logical_payloads(raw):
    outer = CompoundFile(raw)
    nested = CompoundFile(outer.read_stream('_hdb'))
    mapping = logical_mapping(outer.read_stream('projectdatalist.xml'))
    return {name: nested.read_stream(sid) for name, sid in mapping.items()
            if nested.find_streams(sid)}


def node_layout(node):
    """Offsets are derived from counted strings, never assumed from one length."""
    raw = node.raw
    cursor = 12
    fields = [{'name': 'record_length', 'offset': 0, 'size': 4},
              {'name': 'record_class', 'offset': 4, 'size': 4},
              {'name': 'node_kind', 'offset': 8, 'size': 4}]
    for name in (('instance', 'type') if node.kind_code == 2 else ('symbol',)):
        count = struct.unpack_from('<I', raw, cursor)[0]
        fields.append({'name': name, 'offset': cursor, 'size': 4 + 2 * count,
                       'encoding': 'u32 UTF-16 code-unit count + UTF-16LE including NUL'})
        cursor += 4 + 2 * count
    if node.kind_code != 2:
        fields.extend([{'name': 'object_flag_opaque', 'offset': cursor, 'size': 4},
                       {'name': 'reserved_opaque', 'offset': cursor + 4, 'size': 2}])
        cursor += 6
    fields.extend([{'name': 'bbox', 'offset': cursor, 'size': 16},
                   {'name': 'port_count', 'offset': cursor + 16, 'size': 4},
                   {'name': 'ports', 'offset': cursor + 20, 'size': len(node.ports) * 16}])
    return {'family': 'fb_two_string' if node.kind_code == 2 else 'ordinary_counted_symbol',
            'record_length': len(raw), 'fields': fields, 'raw_hex': raw.hex(), 'sha256': sha256(raw)}


def node_snapshot(node):
    return {'offset': node.offset, 'node_kind': node.kind_code,
            'serialized_symbol': node.symbol, 'serialized_type': node.type_name,
            'serialized_instance': node.instance_name, 'bbox': asdict(node.bbox),
            'object_flag': node.object_flag, 'reserved': node.reserved,
            'layout': node_layout(node), 'port_count': len(node.ports),
            'ports': [{'index': i, 'descriptor_hex': p.raw.hex(),
                       'port_kind_code': p.port_kind_code, 'local_x': p.local_x, 'local_y': p.local_y,
                       'point': asdict(p.absolute_point(node.bbox))} for i, p in enumerate(node.ports)]}


def select(program, selector):
    candidates = program.nodes if 'block' not in selector else list(program.block_views())[selector['block']].nodes
    candidates = [n for n in candidates if n.symbol == selector['symbol']]
    if len(candidates) != 1:
        raise ValueError('target selector must identify exactly one node')
    return candidates[0]


def declarations(payloads):
    result = {}
    for name, raw in payloads.items():
        if not name.endswith(('.Labels.lh', '.gh')):
            continue
        item = {'sha256': sha256(raw), 'size': len(raw), 'raw_hex': raw.hex()}
        try:
            doc = parse_declarations(raw, logical_name=name)
            item.update(status='parsed', scope=doc.scope,
                        rows=[{k: v for k, v in asdict(row).items() if k not in ('raw', 'offset')}
                              for row in doc.rows])
        except ValueError as exc:
            item.update(status='opaque_preserved', error=str(exc))
        result[name] = item
    return result


def collect(raw, selector):
    streams = logical_payloads(raw)
    result = {'inventory': snapshot(raw), 'declarations': declarations(streams)}
    name = selector['program']
    source = streams[name]
    result['program_raw_hex'] = source.hex()
    try:
        program = parse_structured_pou(source, logical_name=name)
        target = select(program, selector)
        graph = build_connectivity_graph(program)
        result.update(status='parsed', target=node_snapshot(target),
                      byte_identical=serialize_structured_pou(program) == source,
                      nodes=[node_snapshot(n) for n in program.nodes],
                      wire_geometry=[{'offset': w.offset, 'start': asdict(w.start), 'end': asdict(w.end),
                                      'raw_hex': w.raw.hex()} for w in program.wires],
                      terminal_nodes=[node_snapshot(n) for n in program.nodes if n.kind_code in (13, 14)],
                      nets=[{'block': net.block_index, 'ports': [asdict(p) for p in net.ports],
                             'wire_offsets': list(net.wire_offsets)} for net in graph.nets],
                      canonical=canonical_program(program),
                      unknown_record_count=len(program.unknown_records))
    except (ValueError, IndexError, KeyError) as exc:
        # No structural recovery is written back. The full source remains evidence.
        result.update(status='opaque_preserved', error=str(exc), byte_identical=False)
    return result


def declaration_signature(sample):
    return {k: v['rows'] if v['status'] == 'parsed' else v['sha256']
            for k, v in sample['declarations'].items()}


def type_control(before, after):
    """Strict first-class control: only target type/kind may change in the POU.

    Different-arity/FB layouts remain candidates for a separately reviewed
    control policy; broadening this policy silently would erase confounders.
    """
    a, b = before['target'], after['target']
    ar, br = bytes.fromhex(a['layout']['raw_hex']), bytes.fromhex(b['layout']['raw_hex'])
    same_other_nodes = [n['layout']['raw_hex'] for n in before['nodes'] if n['offset'] != a['offset']] == [
        n['layout']['raw_hex'] for n in after['nodes'] if n['offset'] != b['offset']]
    checks = {'other_nodes_unchanged': same_other_nodes,
              'wires_unchanged': [w['raw_hex'] for w in before['wire_geometry']] == [w['raw_hex'] for w in after['wire_geometry']],
              'declarations_unchanged': declaration_signature(before) == declaration_signature(after),
              'block_headers_unchanged': [x['header_hex'] for x in next(iter(before['inventory']['programs'].values()))['blocks']] == [x['header_hex'] for x in next(iter(after['inventory']['programs'].values()))['blocks']],
              'only_kind_changed': ar != br and ar[:8] == br[:8] and ar[12:] == br[12:]}
    return {'checks': checks, 'passed': all(checks.values()),
            'target_byte_diff': binary_diff(ar, br), 'target_field_diff': field_diff(a, b)}


def connection(sample, selector, peer_symbol, peer_port, target_port):
    p = parse_structured_pou(bytes.fromhex(sample['program_raw_hex']))
    target = select(p, selector)
    peer = select(p, {**selector, 'symbol': peer_symbol})
    if not 0 <= target_port < len(target.ports) or not 0 <= peer_port < len(peer.ports):
        raise ValueError('port index out of bounds')
    g = build_connectivity_graph(p)
    net = g.net_for_port(target.offset, target_port)
    return {'connected': g.ports_connected(target.offset, target_port, peer.offset, peer_port),
            'coincident': target.ports[target_port].absolute_point(target.bbox) == peer.ports[peer_port].absolute_point(peer.bbox),
            'wire_count': len(net.wire_offsets), 'peer_kind': peer.kind_code}


def geometry_control(direct, spaced, selector, control):
    def signature(sample):
        n = sample['target']
        return [n['node_kind'], n['serialized_symbol'], n['serialized_type'],
                n['object_flag'], n['reserved'], n['bbox']['right'] - n['bbox']['left'],
                n['bbox']['bottom'] - n['bbox']['top'], [p['descriptor_hex'] for p in n['ports']]]
    args = (selector, control['peer_symbol'], control['peer_port'], control['target_port'])
    d, s = connection(direct, *args), connection(spaced, *args)
    checks = {'candidate_layout_stable': signature(direct) == signature(spaced),
              'other_nodes_unchanged': [n['layout']['raw_hex'] for n in direct['nodes'] if n['offset'] != direct['target']['offset']] == [n['layout']['raw_hex'] for n in spaced['nodes'] if n['offset'] != spaced['target']['offset']],
              'declarations_unchanged': declaration_signature(direct) == declaration_signature(spaced),
              'device_identity_unchanged': direct['canonical']['devices'] == spaced['canonical']['devices'],
              'port_topology_unchanged': direct['canonical']['topology'] == spaced['canonical']['topology'],
              'direct_coincident': d['connected'] and d['coincident'] and d['wire_count'] == 0,
              'spaced_explicit': s['connected'] and not s['coincident'] and s['wire_count'] > 0}
    return {'checks': checks, 'passed': all(checks.values()), 'direct': d, 'spaced': s}


STEPS = ['open', 'compile_all', 'save', 'close', 'reopen']


def validate_native(attestation, samples, raws, selector, root):
    before = samples[attestation['sample']]
    after_raw = artifact(attestation['after'], root)
    after = collect(after_raw, selector)
    for key in ('compile', 'closed', 'reopen'):
        artifact(attestation['screenshots'][key], root)
    checks = {'observed_full_sequence': attestation.get('steps') == STEPS and bool(attestation.get('observer')),
              'counts_zero': attestation.get('counts') == {'errors': 0, 'warnings': 0, 'check_warnings': 0},
              'lossless_before_after': before['byte_identical'] and after['byte_identical'],
              'semantic_graph_preserved': before.get('canonical') is not None and before.get('canonical') == after.get('canonical')}
    return {'passed': all(checks.values()), 'checks': checks,
            'basis': 'hash-bound observed GX Works2 UI attestation; screenshots are not OCR-verified',
            'program_byte_identical': before['program_raw_hex'] == after['program_raw_hex'],
            'diff': compare(raws[attestation['sample']], after_raw)}


def harvest(manifest, root=ROOT):
    if manifest.get('schema_version') != 1:
        raise ValueError('unsupported ABI manifest schema')
    selector = manifest['selector']
    raws = {k: artifact(v, root) for k, v in manifest['samples'].items()}
    samples = {k: collect(v, selector) for k, v in raws.items()}
    report = {'schema_version': 1, 'id': manifest['id'], 'samples': samples,
              'manifest_sha256': sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()),
              'controls': {}, 'native_validations': [], 'blocking_reasons': []}
    blockers = report['blocking_reasons']
    for control in manifest.get('controls', []):
        a, b = control['before'], control['after']
        result = {'diff': compare(raws[a], raws[b])}
        try:
            if control['variable'] == 'object_type':
                result.update(type_control(samples[a], samples[b]))
            elif control['variable'] == 'connection_geometry':
                result.update(geometry_control(samples[a], samples[b], selector, control))
            else:
                raise ValueError('control policy requires review: ' + control['variable'])
        except (ValueError, KeyError, IndexError) as exc:
            result.update(passed=False, error=str(exc))
        report['controls'][control['id']] = result
        if not result['passed']:
            blockers.append('uncontrolled_or_unsupported:' + control['id'])
    for validation in manifest.get('native_validations', []):
        item = validate_native(validation, samples, raws, selector, root)
        item['sample'] = validation['sample']
        report['native_validations'].append(item)
        if not item['passed']:
            blockers.append('native_failed:' + validation['sample'])
    roles = manifest.get('port_roles', [])
    target = samples.get('spaced', {}).get('target')
    if not target or [p['index'] for p in roles] != list(range(target['port_count'])):
        blockers.append('port_role_coverage_missing')
    for role in roles:
        if role['role'] == 'unknown' and role.get('formal') is None:
            continue
        proof = role.get('proof', {})
        if proof.get('kind') == 'native_ui':
            artifact(proof['screenshot'], root)
            if not proof.get('observed_label') or not proof.get('observer'):
                blockers.append('unattested_ui_role')
        elif proof.get('kind') == 'controlled_connection':
            ctl = next((c for c in manifest.get('controls', []) if c['id'] == proof.get('control')), None)
            passed = report['controls'].get(proof.get('control'), {}).get('passed')
            # First automatic semantic rule is intentionally narrow: known NO
            # contact right port drives the candidate left execution input.
            geometry = report['controls'].get(proof.get('control'), {})
            if not (passed and ctl and ctl.get('target_port') == role['index'] and
                    ctl.get('peer_port') == 1 and geometry.get('direct', {}).get('peer_kind') == 3 and
                    role['role'] == 'execution_in' and role.get('formal') is None):
                blockers.append('unsupported_connection_role')
        else:
            blockers.append('role_without_ui_or_controlled_connection')
    label = manifest.get('semantic_label_evidence')
    if label:
        artifact(label['screenshot'], root)
        if not label.get('observed_label') or not label.get('observer'):
            blockers.append('missing_semantic_label_attestation')
    else:
        blockers.append('missing_semantic_label_evidence')
    kinds = {c['variable'] for c in manifest.get('controls', []) if report['controls'][c['id']]['passed']}
    if not {'object_type', 'connection_geometry'} <= kinds:
        blockers.append('independent_type_and_geometry_controls_required')
    validated = {v['sample'] for v in report['native_validations'] if v['passed']}
    if not {'direct', 'spaced'} <= validated:
        blockers.append('native_validation_pending')
    if any(not s['byte_identical'] or s.get('unknown_record_count', 1) for s in samples.values()):
        blockers.append('opaque_or_non_lossless_source')
    variants = [samples[k] for k in ('direct', 'spaced') if k in samples and samples[k]['status'] == 'parsed']
    consistent = len(variants) == 2 and variants[0]['target']['node_kind'] == variants[1]['target']['node_kind'] and len({sha256(raws[k]) for k in ('direct', 'spaced')}) == 2
    confidence = 'confirmed' if not blockers else 'strongly_inferred' if consistent else 'unknown'
    report.update(confidence=confidence, registry_eligible=confidence == 'confirmed',
                  native_validation_status='passed' if {'direct', 'spaced'} <= validated else 'native_validation_pending',
                  port_roles=roles, declaration_requirement=manifest.get('declaration_requirement', {'status': 'unknown'}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = harvest(json.loads(args.manifest.read_text(encoding='utf-8')))
    write_new_file(args.output, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    print(json.dumps({k: report[k] for k in ('id', 'confidence', 'registry_eligible', 'blocking_reasons')}))


if __name__ == '__main__':
    main()

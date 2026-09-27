"""Grow observed native FX3U or Q00J SFC seeds using vendor graph tokens.

Clone the existing step-10/action and transition-1 records with fresh names,
step 20, OUT Y2, transition 2 and LD X2. All existing records and graph cache
remain byte-identical. This is a bounded experiment, not a general SFC writer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import uuid
from gxw.lossless import inspect_project
from probe_gxw_sfc_graph import compiled_graph, patch_raw
from probe_gxw_sfc_sources import framing, native_children
from replay_gxw_workspace import run


def grow(raw, tokens, *, add_transition=True, transition_count=1, profile='fx3u'):
    transition_count = transition_count if add_transition else 0
    if transition_count not in (0, 1, 2):
        raise ValueError('this experiment adds at most two transition records')
    layout = framing(raw)
    graph = compiled_graph(raw)
    # Explicit native seed profiles: never infer PLC semantics from an opcode.
    if profile == 'fx3u':
        source_step, source_transition, source_device, new_step = 10, 1, 1, 20
    elif profile == 'q00j':
        source_step, source_transition, source_device, new_step = 0, 0, 0, 1
    else:
        raise ValueError('unobserved native SFC seed profile')
    new_transition, new_device = source_transition + 1, source_device + 1
    if any(a['number'] == new_step for a in layout['actions']):
        raise ValueError('new step already has an action')
    if any(c['kind'] == 'transition' and c['number'] in range(new_transition, new_transition + transition_count)
           for c in layout['children']):
        raise ValueError('new transition already exists')
    action, = [a for a in layout['actions'] if a['number'] == source_step]
    if len(action['registrations']) != 1:
        raise ValueError('expected one native action registration')
    zoom, = [c for c in layout['children'] if c['kind'] == 'zoom' and c['name'] == action['registrations'][0]['zoom']]
    transition, = [c for c in layout['children'] if c['kind'] == 'transition' and c['number'] == source_transition]
    if zoom['token_hex'] != f'032003049d{source_device:02x}04' or transition['token_hex'] != f'030003049c{source_device:02x}04':
        raise ValueError('seed child bodies differ from the inspected native control')
    identities = {c['name']: uuid.uuid4().hex for c in ([action, zoom, transition] if add_transition else [action, zoom])}

    def clone(record):
        start = record['record_offset']
        body = raw[start:start + record['record_size']]
        for before, after in identities.items():
            body = body.replace(before.encode('utf-16le'), after.encode('utf-16le'))
        return bytearray(body)

    z, a, t = clone(zoom), clone(action), clone(transition)
    z[zoom['token_offset'] - zoom['record_offset'] + 5] = new_device
    struct.pack_into('<I', a, action['number_offset'] - action['record_offset'], new_step)
    # Transition number immediately precedes its storage flag and size word.
    struct.pack_into('<I', t, transition['size_offset'] - transition['record_offset'] - 8, new_transition)
    t[transition['token_offset'] - transition['record_offset'] + 5] = new_device
    extra_transitions = []
    transition_bytes = bytes(t) if transition_count else b''
    if transition_count == 2:
        third_name = uuid.uuid4().hex
        extra = bytearray(bytes(t).replace(identities[transition['name']].encode('utf-16le'), third_name.encode('utf-16le')))
        struct.pack_into('<I', extra, transition['size_offset'] - transition['record_offset'] - 8, new_transition + 1)
        extra[transition['token_offset'] - transition['record_offset'] + 5] = new_device + 1
        transition_bytes += extra
        extra_transitions.append(dict(source=transition['name'], name=third_name, number=new_transition + 1))
    gstart = layout['graph']['offset']
    cache = raw[graph['cache_offset']:graph['cache_offset'] + graph['cache_size']]
    new_graph = bytearray(raw[gstart:gstart + 12] + struct.pack('<I', len(tokens) + 4) + tokens + cache)
    struct.pack_into('<I', new_graph, 0, len(new_graph))
    zc, ac, tc = [layout[k + '_count_offset'] for k in ['zoom', 'action', 'transition']]
    u32 = lambda offset, amount=1: struct.pack('<I', struct.unpack_from('<I', raw, offset)[0] + amount)
    new = (raw[:gstart - 4] + struct.pack('<I', len(new_graph)) + new_graph
           + u32(zc) + raw[zc + 4:ac] + z
           + u32(ac) + raw[ac + 4:tc] + a
           + u32(tc, transition_count)
           + raw[tc + 4:layout['tail_offset']] + transition_bytes + raw[layout['tail_offset']:])
    after = framing(new)
    for old_record in layout['children'] + layout['actions']:
        copied, = [r for r in after['children'] + after['actions'] if r['name'] == old_record['name']]
        before_bytes = raw[old_record['record_offset']:old_record['record_offset'] + old_record['record_size']]
        after_bytes = new[copied['record_offset']:copied['record_offset'] + copied['record_size']]
        if before_bytes != after_bytes:
            raise ValueError('existing SFC child record changed')
    if compiled_graph(new)['cache_sha256'] != graph['cache_sha256']:
        raise ValueError('opaque graph cache changed')
    return bytes(new), dict(profile=profile, new_identities=identities, graph_before=graph, graph_after=compiled_graph(new),
                            existing_records='byte-identical', opaque_cache='byte-identical', after=after,
                            extra_transitions=extra_transitions)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('seed', type=Path)
    ap.add_argument('native_tokens', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--keep-transition-count', action='store_true', help='retain the two existing transitions for the parallel graph')
    ap.add_argument('--added-transition-count', type=int, choices=(1, 2), default=1)
    ap.add_argument('--profile', choices=('fx3u', 'q00j'), default='fx3u')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.seed.read_bytes()
    seed, = [s for s in inspect_project(source).streams if s.logical_name and s.logical_name.endswith('.Program.pou')]
    new, evidence = grow(seed.raw, args.native_tokens.read_bytes(), add_transition=not args.keep_transition_count,
                         transition_count=args.added_transition_count, profile=args.profile)
    patched, preservation = patch_raw(source, seed.logical_name, seed.raw, new)
    evidence.update(preservation)
    evidence['native_tokens_sha256'] = hashlib.sha256(args.native_tokens.read_bytes()).hexdigest()
    (args.output / 'mutation.json').write_text(json.dumps(evidence, indent=2) + '\n')
    path = args.output / 'three-steps.gxw'
    path.write_bytes(patched)
    outcome = run(path, args.output / 'native', compile=True, change_sfc=True, program_check=True,
                  snapshot_frontend=True, export_project=True)
    snapshot = args.output / 'native/frontend-snapshot.json'
    if snapshot.exists():
        observed = native_children(json.loads(snapshot.read_text()))
        outcome['native_child_bytes_match'] = all(observed.get((c['kind'], c['name'])) == bytes.fromhex(c['token_hex'])
                                                   for c in evidence['after']['children'])
    (args.output / 'result.json').write_text(json.dumps(outcome, indent=2) + '\n')
    print(json.dumps(outcome, indent=2))


if __name__ == '__main__':
    main()

"""Inspect global bindings and observed user POU dependencies for native transfer.

Only a hash-bound successful native export supplies the donor call-tree cache.
The cache is an independent reference observation, not a complete dependency
oracle. Unresolved selectors remain explicit; no declarations are inferred.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.compiler_call_tree import parse_compiler_call_tree
from gxw.declarations import parse_declarations
from gxw.lossless import inspect_project


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def streams(raw):
    image = inspect_project(raw)
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError('incomplete project inventory')
    result = {}
    for stream in image.streams:
        if stream.logical_name:
            if stream.logical_name in result:
                raise ValueError('ambiguous logical stream')
            result[stream.logical_name] = stream.raw
    return result


def globals_by_name(payloads):
    result = {}
    for name, raw in payloads.items():
        if name.endswith('.gh'):
            for row in parse_declarations(raw, logical_name=name).rows:
                if not row.name.isascii():
                    raise ValueError('native non-ASCII case folding is not implemented')
                result.setdefault(row.name.upper(), []).append((name, row))
    return result


def binding(row):
    # Names/record IDs are independently resolved; offsets and comment text do
    # not define the binding. Retain unknown declaration fields in comparisons.
    values = asdict(row)
    return {key: value for key, value in values.items()
            if key not in ('raw', 'offset', 'record_id', 'name', 'comment')}


def pou_streams(payloads):
    result = {}
    for name in payloads:
        if name.endswith('.Program.pou'):
            owner = name.removesuffix('.Program.pou')
            if not owner.isascii():
                raise ValueError('native non-ASCII POU case folding is not implemented')
            if owner.upper() in result:
                raise ValueError('ambiguous POU name')
            result[owner.upper()] = owner
    return result


def dependency_payloads(source, target, source_name, target_name):
    # No normalization of opaque headers/trailers: native copy changes some
    # dates even when source and declarations match. Report that uncertainty.
    members = []
    for suffix in ('.Program.pou', '.Labels.lh'):
        name = source_name + suffix
        if name not in source:
            raise ValueError('observed user POU is missing a required member')
        other = target.get(target_name + suffix) if target_name else None
        item = dict(stream=name, donor_sha256=digest(source[name]),
                    target_sha256=digest(other) if other is not None else None,
                    status='missing' if other is None else
                           'byte-identical' if source[name] == other else 'differs')
        if suffix == '.Labels.lh' and other is not None:
            left = parse_declarations(source[name], logical_name=name)
            right = parse_declarations(other, logical_name=target_name + suffix)
            item['declarations_equal'] = [dict(name=r.name, **binding(r)) for r in left.rows] == [
                dict(name=r.name, **binding(r)) for r in right.rows]
            item['opaque_header_equal'] = left.header == right.header
            item['opaque_trailer_equal'] = left.trailer == right.trailer
        members.append(item)
    status = 'missing' if any(m['status'] == 'missing' for m in members) else (
        'byte-identical' if all(m['status'] == 'byte-identical' for m in members) else 'differs')
    return dict(name=source_name, status=status, members=members)


def compare(donor_run, target, pou):
    donor_run, target = Path(donor_run).resolve(), Path(target).resolve()
    outcome = json.loads((donor_run / 'outcome.json').read_text())
    if not outcome.get('compile_completed') or outcome.get('compiler_rejected'):
        raise ValueError('donor requires a completed successful native compile')
    exported = outcome.get('native_export', {})
    if exported.get('file') != 'native-saved.gxw':
        raise ValueError('donor native export is unavailable')
    raw = (donor_run / exported['file']).read_bytes()
    if digest(raw) != exported.get('sha256'):
        raise ValueError('donor export no longer matches its native outcome')
    target_raw = target.read_bytes()
    source_streams, target_streams = streams(raw), streams(target_raw)
    source_globals, target_globals = globals_by_name(source_streams), globals_by_name(target_streams)
    source_pous, target_pous = pou_streams(source_streams), pou_streams(target_streams)
    tree = parse_compiler_call_tree(source_streams['CallTree.dat'])
    roots = [node for node in tree.maps[1] if node.kind_code == 23
             and node.names[2].ascii_key == pou.upper()]
    if len(roots) != 1:
        raise ValueError('native POU reference node is not unique')
    targets = {}
    for node in tree.maps[2]:
        targets.setdefault(node.ascii_key, []).append(node)
    references, globals_found, dependencies = [], [], []
    queue, visited = [roots[0]], set()
    while queue:
        current = queue.pop(0)
        owner_key = current.names[2].ascii_key
        if owner_key in visited:
            continue
        if len(visited) >= 256:
            raise ValueError('observed POU closure exceeds probe bound')
        visited.add(owner_key)
        owner = source_pous[owner_key]
        local = parse_declarations(source_streams[owner + '.Labels.lh'], logical_name=owner + '.Labels.lh')
        local_names = {row.name.upper() for row in local.rows if row.name.isascii()}
        for ref in current.references:
            nodes = targets.get(ref.ascii_key, [])
            observation = dict(owner=owner, selector=ref.text(), handling='unresolved-reference')
            if len(nodes) != 1:
                references.append(observation)
                continue
            node = nodes[0]
            name = node.names[2].ascii_key
            observation['kind'] = node.kind_code
            if node.kind_code == 27 and name in source_pous:
                children = [n for n in tree.maps[1] if n.kind_code == 23 and n.names[2].ascii_key == name]
                if len(children) == 1:
                    queue.append(children[0])
                    observation['handling'] = 'user-pou-dependency'
                    if not any(d['name'].upper() == name for d in dependencies):
                        dependencies.append(dependency_payloads(source_streams, target_streams,
                            source_pous[name], target_pous.get(name)))
            elif node.kind_code == 33 and name in source_globals and name not in local_names:
                found, wanted = target_globals.get(name, []), source_globals[name]
                if len(wanted) != 1 or len(found) > 1:
                    status = 'ambiguous'
                elif not found:
                    status = 'missing'
                elif binding(wanted[0][1]) == binding(found[0][1]):
                    status = 'binding-identical'
                else:
                    status = 'binding-differs'
                item = dict(owner=owner, name=name, status=status,
                    donor=[dict(stream=s, declaration=binding(r)) for s, r in wanted],
                    target=[dict(stream=s, declaration=binding(r)) for s, r in found])
                globals_found.append(item)
                observation['handling'] = 'global-binding-compared'
            elif name in local_names:
                observation['handling'] = 'local-or-shadowed-reference'
            references.append(observation)
    return dict(donor_sha256=digest(raw), target_sha256=digest(target_raw), pou=pou,
        reference_evidence='call-tree from hash-bound native compile/export',
        globals=globals_found, references=references, pou_dependencies=dependencies,
        observed_pou_closure=[source_pous[n] for n in sorted(visited)],
        conflicts=sorted({g['name'] for g in globals_found if g['status'] != 'binding-identical'} |
                         {d['name'] for d in dependencies if d['status'] != 'byte-identical'}),
        complete_dependency_proof=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('donor_run', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('pou')
    args = parser.parse_args()
    print(json.dumps(compare(args.donor_run, args.target, args.pou), indent=2))

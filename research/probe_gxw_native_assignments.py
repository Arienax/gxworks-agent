"""Bind source declarations through a fresh isolated native compile/query.

Queries are explicit names and scopes, not wildcard enumeration. An instance
query can expand its arrays and members using native allocation semantics.
Definition-scope FB queries may be ambiguous across instances; retain that
scope and never merge it with instance-qualified results. Empty/error answers
remain gaps. Repeated calls to one instance can expose only the last call's
pin addresses; these results are not a complete call-site connection graph.
No claim of device safety or complete source dependency coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.declarations import parse_declarations
from gxw.lossless import inspect_project, sha256
from replay_gxw_workspace import run


def inspect(source_path: Path, directory: Path) -> dict:
    source_path, directory = source_path.resolve(), directory.resolve()
    source = source_path.read_bytes()
    directory.mkdir(parents=True, exist_ok=False)
    image = inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError('incomplete project inventory')
    result = dict(source_sha256=sha256(source), declarations=[], gaps=[],
        scope='native offline compile/query; definition scope is not a unique FB instance identity; pin addresses may reflect only the last call')
    queries, documents = [], []
    for stream in image.streams:
        name = stream.logical_name or ''
        if not name.endswith(('.Labels.lh', '.gh')):
            continue
        try:
            document = parse_declarations(stream.raw, logical_name=name)
        except ValueError as exc:
            result['gaps'].append(dict(object=name, handling='opaque-preserved', diagnostic=str(exc)))
            continue
        documents.append(document)
    definitions = {row.type_reference.casefold() for document in documents for row in document.rows
                   if row.type_code == 15 and row.type_reference}
    for document in documents:
        name = document.logical_name
        for row in document.rows:
            query_name = 'declaration-' + str(len(queries))
            keys = [dict(category=1, value=row.name)]
            if document.scope == 'local':
                keys.append(dict(category=4, value=document.owner_name))
            queries.append(dict(name=query_name, keys=keys))
            result['declarations'].append(dict(query=query_name, object=name, offset=row.offset,
                row_sha256=sha256(row.raw), name=row.name, source_scope=document.owner_name,
                declared_type=row.data_type, type_code=row.type_code, type_reference=row.type_reference,
                declared_device=row.device, declared_iec_address=row.iec_address,
                resolution_scope=('definition-dependent' if (document.owner_name or '').casefold() in definitions else
                                  'instance' if row.type_code == 15 else document.scope),
                handling='not-queried', assignments=[]))
    (directory / 'query-plan.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    outcome = run(source_path, directory / 'native', compile=True, export_project=True,
                  project_alias='ASSIGNPROBE', assignment_queries=queries or None)
    result['native'] = {k: outcome[k] for k in ['returncode', 'open_succeeded', 'compile_completed',
        'compiler_rejected', 'diagnostics', 'outputs', 'input_copy_unchanged']}
    answers = directory / 'native/native-device-assignments.json'
    if answers.exists():
        rows = json.loads(answers.read_text(encoding='utf-8-sig'))
        if [r['query'] for r in rows] != queries:
            raise ValueError('native query provenance/order differs')
        for declaration, answer in zip(result['declarations'], rows):
            declaration.update(hresult=answer['hresult'], native_code=answer['code'], assignments=answer['assignments'])
            for assignment in declaration['assignments']:
                assignment['handling'] = 'native-address-present' if assignment['address'] else 'native-address-empty'
            declaration['handling'] = ('native-error' if answer['hresult'] < 0 or answer['code'] else
                                       'native-empty' if not answer['count'] else
                                       'partially-resolved' if any(not a['address'] for a in answer['assignments']) else 'native-resolved')
    result['summary'] = dict(Counter(d['handling'] for d in result['declarations']))
    result['expanded_assignment_rows'] = sum(len(d['assignments']) for d in result['declarations'])
    result['address_statuses'] = dict(Counter(a['handling'] for d in result['declarations'] for a in d['assignments']))
    (directory / 'assignments.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = inspect(args.source, args.output)
    print(json.dumps(dict(summary=result['summary'], expanded_assignment_rows=result['expanded_assignment_rows'],
                         gaps=result['gaps'], native=result['native'])))

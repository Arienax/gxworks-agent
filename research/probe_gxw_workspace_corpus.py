"""Run the installed native offline loader/compiler over hash-bound GXW cases."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import zipfile

from replay_gxw_workspace import ROOT, digest, run


def probe(manifest, output, *, skip=()):
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    corpus = json.loads(manifest.read_text(encoding='utf-8'))
    base = (manifest.parent / corpus['base']).resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases, seen = [], {}
    with (output / 'cases.jsonl').open('w', encoding='utf-8') as log:
        for index, case in enumerate(corpus['cases']):
            source = case['source']
            if case['id'] in skip:
                continue
            row = {'id': case['id'], 'source': source, 'expected_native': case.get('expected_native')}
            if source['sha256'] in seen:
                row['duplicate_of'] = seen[source['sha256']]
            else:
                folder = output / f'{index:03d}'
                folder.mkdir()
                archive = base / source['path']
                if 'member' in source:
                    with zipfile.ZipFile(archive) as z:
                        raw = z.read(source['member'])
                else:
                    raw = archive.read_bytes()
                input_copy = folder / 'source.gxw'
                input_copy.write_bytes(raw)
                if digest(input_copy) != source['sha256']:
                    raise ValueError('corpus source hash mismatch: ' + case['id'])
                row['run_directory'] = str(folder / 'run')
                try:
                    row['result'] = run(input_copy, folder / 'run', compile=True)
                except Exception as exc:
                    row['harness_failure'] = type(exc).__name__ + ': ' + str(exc)
                seen[source['sha256']] = case['id']
            log.write(json.dumps(row, ensure_ascii=False) + '\n')
            log.flush()
            cases.append(row)
            result = row.get('result', {})
            print(json.dumps({'id': case['id'], 'opened': result.get('open_succeeded'),
                'completed': result.get('compile_completed'), 'rejected': result.get('compiler_rejected'),
                'returncode': result.get('returncode'), 'duplicate': row.get('duplicate_of'),
                'failure': row.get('harness_failure')}, ensure_ascii=False), flush=True)
    return cases


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'research/corpus/gxw.json')
    parser.add_argument('--skip', action='append', default=[])
    args = parser.parse_args()
    probe(args.manifest, args.output, skip=args.skip)

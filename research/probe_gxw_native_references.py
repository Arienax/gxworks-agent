"""Capture independent native source references from an isolated offline build.

CompilerAdapter CreateProgramAnalysis3/GetProgramAnalysis3 enumerate with an
empty symbol and scope. The older GetProgramAnalysis reads an empty legacy
cache in the current controls. Successful enumeration is not proof of semantic
completeness: repeated FB calls can report the last call's address for earlier
pin references, and native flags/coordinates retain their original values.
Native references can also include an uncalled declared FB instance's body;
enumeration does not establish execution reachability.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.lossless import sha256
from replay_gxw_workspace import run


def inspect(source_path: Path, directory: Path) -> dict:
    source_path, directory = source_path.resolve(), directory.resolve()
    source = source_path.read_bytes()
    directory.mkdir(parents=True, exist_ok=False)
    queries = [dict(name='references', keys=[], symbol='', scope='', declared=0, plural=0),
               dict(name='references-and-declarations', keys=[], symbol='', scope='', declared=1, plural=0)]
    native = run(source_path, directory / 'native', compile=True, export_project=True,
                 project_alias='REFPROBE', analysis_queries=queries, analysis_version=3)
    result = dict(source_sha256=sha256(source), handling='not-queried',
        scope='independent native reference capture; no claim of complete FB call-site address semantics',
        native={k:native[k] for k in ['returncode','timed_out','open_succeeded','compile_completed','compiler_rejected',
            'diagnostics','outputs','project_attributes','project_codepage','input_copy_unchanged','last_event']}, queries=[])
    if native['returncode'] != 0:
        result['handling'] = 'native-error'
    path = directory / 'native/native-program-analysis.json'
    if path.exists():
        answer = json.loads(path.read_text(encoding='utf-8-sig'))
        if [r['query'] for r in answer['queries']] != queries:
            raise ValueError('native reference query provenance/order differs')
        result['handling'] = 'native-query-completed'
        for query in answer['queries']:
            rows = query['rows']
            query['summary'] = dict(rows=len(rows), sources=dict(Counter(r['source'] for r in rows)),
                instances=dict(Counter(r['instance'] for r in rows)),
                division=dict(Counter(str(r['division']) for r in rows)),
                program_kind=dict(Counter(str(r['program_kind']) for r in rows)),
                attributes=dict(Counter(str(r['attribute']) for r in rows)),
                unresolved_addresses=sum(not r['address'] for r in rows))
            query['handling'] = ('native-error' if query['hresult'] < 0 or query['code'] else
                                 'possible-native-limit' if query['count'] >= 80000 else
                                 'native-empty' if not rows else 'native-query-completed')
            # Native constructor 0x7792 initializes a default 80,000-row limit.
            # Retain an explicit gap when reached; do not relabel it complete.
            if query['handling'] in ('native-error','possible-native-limit'):
                result['handling'] = 'partial'
            result['queries'].append(query)
    if source_path.read_bytes() != source:
        raise ValueError('source changed during native reference capture')
    (directory / 'references.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    result = inspect(args.source,args.output)
    print(json.dumps(dict(handling=result['handling'],native=result['native'],
                         summary=[r['summary'] for r in result['queries']])) )

"""Read simple-ladder source through the installed, hash-bound native oracle.

Project CPU/code page come from the observed .prj prefix, independently checked
against native project attributes and the project's code-page getter. This
does not select encoding from filenames, decoded-looking bytes, or the host
ACP. The raw source stays authoritative; successful text conversion is not a
compile result, a complete dependency graph, or a project writer permission.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from native_gxw_tokens import native_batch, native_il_projection, Q_DLL, DEFAULT_DLL
from gxw.lossless import inspect_project, sha256
from gxw.token_pou import frame_token_pou
from gxw.project_metadata import read_project_text_context as project_text_context
from probe_gxw_parameters import device_allocation_record
from probe_gxw_task_graph import inspect_task_graph


def inspect(source: bytes, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    image = inspect_project(source)
    result = dict(source_sha256=sha256(source), programs=[],
                  oracle_scope='native text conversion; no compile/reopen or dependency completeness claim')
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError('incomplete source inventory')
    streams = {s.logical_name: s.raw for s in image.streams if s.logical_name}
    metadata = [(n, raw) for n, raw in streams.items() if n.endswith('.prj')]
    if len(metadata) != 1:
        raise ValueError('expected exactly one project metadata stream')
    context = project_text_context(metadata[0][1])
    result['context'] = dict(context, logical_name=metadata[0][0])
    try:
        device_context = device_allocation_record(streams['Param.wpa'])
        result['device_context'] = dict(offset=device_context.offset, bytes=len(device_context.raw),
                                       sha256=sha256(device_context.raw), handling='raw-preserved')
    except (ValueError, KeyError) as exc:
        result['device_context'] = dict(handling='unsupported', diagnostic=str(exc))
    # FX1S's 0x206 comes from its real offline build's Open calls, not the
    # Navigator CPU enum or an assumed relationship to FX3U's adapter value.
    # Q02/Q02H's adapter CPU 34 is observed in its own offline build; it is
    # not inferred by reusing Q03UDV's code or by inspecting token spellings.
    profile = {'Q03UDV': (Q_DLL, 209), 'Q02/Q02H': (Q_DLL, 34), 'FX3U/FX3UC': (DEFAULT_DLL, 0x208),
               'FX1S': (DEFAULT_DLL, 0x206)}.get(context['cpu'])
    source_graph = inspect_task_graph(source)
    source_names = {p['source_program'] for p in source_graph['programs'].values()}
    result['source_identity_gaps'] = source_graph['gaps']
    requests, selected = [], []
    for name, raw in streams.items():
        if name not in source_names:
            continue
        item = dict(name=name, sha256=sha256(raw), handling='opaque-preserved')
        result['programs'].append(item)
        try:
            program = frame_token_pou(raw)
            if profile is None or context['text_encoding'] is None:
                raise ValueError('CPU/code page is outside the inspected native profile')
            if len(program.body) + 1 > 32768:
                raise ValueError('source exceeds the native oracle input bound')
        except ValueError as exc:
            item['diagnostic'] = str(exc)
            continue
        item.update(body_offset=program.body_offset, body_bytes=len(program.body),
                    body_sha256=sha256(program.body), tokens=len(program.tokens),
                    reconstruction='byte-identical', text_encoding=context['text_encoding'])
        requests.append(dict(mode='decode', program=name, source_sha256=item['sha256'],
            input_base64=base64.b64encode(program.body + b'\0').decode()))
        selected.append(item)
    if requests:
        rows = native_batch(requests, directory / 'native', dll=profile[0], cpu_code=profile[1],
                            native_versions=() if context['cpu'] == 'Q02/Q02H' else None)
        for item, row in zip(selected, rows):
            item['native_return_code'] = row['return_code']
            item['native_consumed_bytes'] = row['consumed_bytes']
            if row['return_code'] != '0x00000000' or row['consumed_bytes'] != item['body_bytes']:
                item['diagnostic'] = 'native decoder rejected or did not consume the full source body'
                continue
            try:
                projection = native_il_projection(base64.b64decode(row['output_base64']),
                                                   encoding=context['text_encoding'])
            except (ValueError, UnicodeError) as exc:
                item['diagnostic'] = str(exc)
                continue
            item.update(projection)
    result['summary'] = dict(Counter(item['handling'] for item in result['programs']))
    (directory / 'listing.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.source.read_bytes(), args.output)
    print(json.dumps(dict(context=result['context'], summary=result['summary']), ensure_ascii=True))

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
import struct

from native_gxw_tokens import native_batch, native_il_projection, Q_DLL, DEFAULT_DLL
from gxw.lossless import inspect_project, sha256
from gxw.token_pou import frame_token_pou
from probe_gxw_parameters import device_allocation_record


def project_text_context(raw: bytes) -> dict:
    """Read a bounded observed prefix; retain remaining metadata as opaque.

    The code page sits 48 bytes after two counted UTF-16 strings. Five public
    native read controls agree (Q/FX, 932/936/949/1252); changing only this field
    from 949 to 932 changes the native getter and breaks the Korean build.
    """
    offset, values = 0, []
    for _ in range(2):
        if offset + 4 > len(raw):
            raise ValueError('truncated project metadata string length')
        count = struct.unpack_from('<I', raw, offset)[0]
        end = offset + 4 + 2 * count
        if not 1 <= count <= 8192 or end > len(raw):
            raise ValueError('unbounded project metadata string')
        text = raw[offset + 4:end].decode('utf-16le')
        if not text.endswith('\0') or '\0' in text[:-1]:
            raise ValueError('unsupported project metadata string terminator')
        values.append(text[:-1])
        offset = end
    if len(raw) < offset + 52 or raw[offset:offset + 14] != bytes.fromhex('0100000000000000000000000000'):
        raise ValueError('unsupported project metadata prefix')
    codepage = struct.unpack_from('<I', raw, offset + 48)[0]
    # Converter 0x2F770 substitutes 54936 (GB18030) for stored code page 936.
    # Other code pages are deliberately not guessed from their numeric value.
    encoding = {932: 'cp932', 936: 'gb18030', 949: 'cp949', 1252: 'cp1252'}.get(codepage)
    return dict(cpu=values[1], codepage=codepage, text_encoding=encoding,
                codepage_offset=offset + 48, metadata_sha256=sha256(raw),
                handling='partially-decoded')


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
    profile = {'Q03UDV': (Q_DLL, 209), 'FX3U/FX3UC': (DEFAULT_DLL, 0x208),
               'FX1S': (DEFAULT_DLL, 0x206)}.get(context['cpu'])
    requests, selected = [], []
    for name, raw in streams.items():
        if not name.endswith('.Program.pou'):
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
        rows = native_batch(requests, directory / 'native', dll=profile[0], cpu_code=profile[1])
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

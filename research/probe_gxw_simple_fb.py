"""Raw-preserved simple-ladder source framing and FB call observations.

The public FX samples retain 20-byte trailers; native save appends four zero
bytes without changing their size fields or token body. Q03UDV source uses the
same bounded envelope, with a different END and FB bodies without END. This
probe deliberately makes no FX opcode/device projection from these tokens.

FB markers are corroborated by native GUI output and the eight-way dispatcher
in GD2DataOprPOUConvert.dll (RVA 0x106E0). Connections remain bounded raw token
fragments: names prefixed with underscore are native block-local placeholders,
not declarations to create globally. This is not a complete dependency oracle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.declarations import parse_declarations
from gxw.lossless import inspect_project, sha256
from gxw.token_pou import frame_token_pou


def frame_source(raw):
    """Recognize only the observed envelope; retain CPU/POU semantics as opaque."""
    result = frame_token_pou(raw)
    if result.reconstruct() != raw:
        raise ValueError('framing changed source bytes')
    return result


def marker(token):
    if len(token.raw) < 4 or token.raw[1:3] != b'\x80\x01':
        return None
    value = token.raw[3:-1]
    if not value.startswith((b';FB', b';INSTANCE_NAME')):
        return None
    # No guessed codepage or name folding: non-ASCII metadata stays opaque.
    try:
        return value.decode('ascii')
    except UnicodeDecodeError:
        return '<opaque-non-ascii-fb-marker>'


def span(raw, start, end):
    return dict(offset=start, length=end-start, sha256=sha256(raw[start:end]),
                handling='opaque-preserved')


def observe_calls(program):
    """Partition native marker scopes; never interpret the surrounding ladder."""
    calls, gaps, current = [], [], None
    ident = r'[A-Za-z_][A-Za-z_0-9]*'
    for token in program.tokens:
        text = marker(token)
        if text is None:
            if current and current['phase'] == 'metadata':
                current['gaps'].append(dict(offset=token.offset, reason='non-marker in FB metadata'))
            continue
        start = re.fullmatch(rf';FB BLK START ({ident})\(({ident})\)', text)
        if start:
            if current:
                gaps.append(dict(offset=current['offset'], reason='unterminated or nested FB scope'))
            current = dict(offset=token.offset, definition=start[1], instance=start[2],
                           phase='before', ports=[], gaps=[],
                           input_start=token.offset+len(token.raw))
            continue
        if current is None:
            gaps.append(dict(offset=token.offset, reason='FB marker outside recognized scope', marker=text))
            continue
        phase = current['phase']
        if text == ';FB START' and phase == 'before':
            current['input_fragment'] = span(program.raw, current.pop('input_start'), token.offset)
            current['phase'] = 'metadata'
        elif text == ';FB_NAME ' + current['definition'] and phase == 'metadata' and not current.get('definition_confirmed'):
            current['definition_confirmed'] = True
        elif text == ';INSTANCE_NAME ' + current['instance'] and phase == 'metadata' and not current.get('instance_confirmed'):
            current['instance_confirmed'] = True
        elif phase == 'metadata' and (pin := re.fullmatch(rf';FB IN_([BWDS]):({ident})', text)):
            current['ports'].append(dict(direction='input', type_marker=pin[1], name=pin[2], offset=token.offset))
        elif phase == 'metadata' and (pin := re.fullmatch(rf';FB OUT_({ident}):([BWDS])', text)):
            current['ports'].append(dict(direction='output', type_marker=pin[2], name=pin[1], offset=token.offset))
        elif text == ';FB END' and phase == 'metadata':
            current['phase'] = 'after'
            current['output_start'] = token.offset+len(token.raw)
        elif text == ';FB BLK END' and phase == 'after':
            current['output_fragment'] = span(program.raw, current.pop('output_start'), token.offset)
            current.update(span(program.raw, current['offset'], token.offset+len(token.raw)))
            current['handling'] = 'partially-decoded'
            current.pop('phase')
            if not current.get('definition_confirmed') or not current.get('instance_confirmed'):
                current['gaps'].append(dict(offset=current['offset'], reason='missing matching definition/instance metadata'))
            calls.append(current)
            current = None
        else:
            current['gaps'].append(dict(offset=token.offset, reason='unobserved marker or marker order', marker=text))
    if current:
        gaps.append(dict(offset=current['offset'], reason='unterminated FB scope'))
    return dict(calls=calls, gaps=gaps, complete_dependency_proof=False)


def inspect(source):
    image = inspect_project(source)
    if image.diagnostics or any(s.error for s in image.streams):
        raise ValueError('incomplete container inventory')
    streams = {s.logical_name: s.raw for s in image.streams if s.logical_name}
    result = dict(source_sha256=sha256(source), programs=[])
    for name, raw in streams.items():
        if not name.endswith('.Program.pou'):
            continue
        item = dict(name=name, sha256=sha256(raw))
        result['programs'].append(item)
        try:
            program = frame_source(raw)
        except ValueError as exc:
            item.update(handling='opaque-preserved', diagnostic=str(exc))
            continue
        item.update(handling='framed', body=span(raw, program.body_offset, program.body_end),
                    token_count=len(program.tokens), trailer_bytes=len(raw)-program.body_end,
                    reconstruction='byte-identical', **observe_calls(program))
        owner = name.removesuffix('.Program.pou')
        label_name = owner + '.Labels.lh'
        try:
            labels = parse_declarations(streams[label_name], logical_name=label_name).rows
        except (KeyError, ValueError) as exc:
            item['declaration_diagnostic'] = str(exc)
            continue
        for call in item['calls']:
            rows = [row for row in labels if row.name == call['instance']]
            call['instance_declaration'] = ('matches' if len(rows) == 1 and rows[0].type_code == 15
                and rows[0].type_reference == call['definition'] else 'unresolved-or-differs')
            call['definition_present'] = call['definition'] + '.Program.pou' in streams
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('-o', '--output', type=Path, required=True)
    args = ap.parse_args()
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(inspect(args.source.read_bytes()), output, ensure_ascii=False, indent=2)
        output.write('\n')

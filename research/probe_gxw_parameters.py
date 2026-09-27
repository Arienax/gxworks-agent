"""Bounded, raw-preserved observations of Param.wpa parameter records.

Thirty-nine public Q/FX projects carry consecutive little-endian uint16
length/tag records and a final FFFF word. Record 2000 is also passed verbatim
to the Q text encoder by the native project compiler. Its payload is a count
and four-byte entries; the entries are retained as codes/values, without
assuming all PLC families share device units or allocation rules.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.lossless import inspect_project, sha256


@dataclass(frozen=True)
class ParameterRecord:
    offset: int
    tag: int
    raw: bytes


def frame_parameters(raw: bytes) -> tuple[ParameterRecord, ...]:
    """Recognize the complete record chain; never search past an invalid span."""
    records, offset = [], 0
    while offset + 2 <= len(raw):
        length = struct.unpack_from('<H', raw, offset)[0]
        if length == 0xffff:
            if offset + 2 != len(raw):
                raise ValueError('bytes after terminal parameter marker')
            if b''.join(record.raw for record in records) + raw[offset:] != raw:
                raise ValueError('parameter reconstruction differs')
            return tuple(records)
        if length < 4 or offset + length > len(raw):
            raise ValueError(f'invalid parameter record length at {offset}')
        tag = struct.unpack_from('<H', raw, offset + 2)[0]
        records.append(ParameterRecord(offset, tag, raw[offset:offset + length]))
        offset += length
    raise ValueError('missing parameter terminal marker')


def device_allocation_record(raw: bytes) -> ParameterRecord:
    """Return exactly one bounded 2000 record; no inferred defaults or repair."""
    matches = [record for record in frame_parameters(raw) if record.tag == 0x2000]
    if len(matches) != 1:
        raise ValueError('expected exactly one device-allocation parameter record')
    record = matches[0]
    if len(record.raw) < 6 or len(record.raw) != 6 + 4 * struct.unpack_from('<H', record.raw, 4)[0]:
        raise ValueError('unsupported device-allocation record payload')
    return record


def timer_parameter_record(raw: bytes) -> dict:
    """Observed 1000 timer pair, with CPU-dependent units left explicit.

    DataManager_IEC LocalParseDataTimerSet (2b4ee) copies the two words;
    GetTimerInfo (2b2aa) returns them. A Q03UDV 1000 -> 150 mutation in
    word 2 reaches that native setter verbatim and changes TON_HIGH lowering.
    This is a bounded source view, not a general parameter validity rule.
    """
    matches=[r for r in frame_parameters(raw) if r.tag==0x1000]
    if len(matches)!=1 or len(matches[0].raw)!=8:
        raise ValueError('outside observed single eight-byte timer parameter record')
    r=matches[0]
    low,high=struct.unpack_from('<HH',r.raw,4)
    return dict(offset=r.offset,raw_hex=r.raw.hex(),low_timer_value=low,high_timer_value=high,
        value_offsets=[r.offset+4,r.offset+6],handling='partially-decoded',
        units='CPU-dependent native conversion; not normalized to a common time unit')


def project_allocation_ranges(raw: bytes) -> dict:
    """Read the observed extended system-variable range record, without edits.

    The pinned Workspace type library names all 28 pairs. Its native getters
    agree byte-for-byte in 37 Q03UDV/FX3U/FX1S public projects. A controlled
    disagreement proves the extended record, not the earlier 88-byte copy,
    supplies the compiler's ranges. The intervening count can be 11 or 12;
    its entries and remaining project bytes are still opaque.
    """
    from probe_gxw_native_listing import project_text_context
    context = project_text_context(raw)
    start = context['codepage_offset'] + 4
    if start + 92 > len(raw):
        raise ValueError('truncated project range prefix')
    count = struct.unpack_from('<I', raw, start + 88)[0]
    if count not in (11, 12):
        raise ValueError('intervening project record count outside observed layouts')
    extended = start + 138 + 4 * count
    if extended + 228 > len(raw) or struct.unpack_from('<I', raw, extended)[0] != 228:
        raise ValueError('missing bounded 228-byte extended allocation record')
    names = ('D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10', 'ZR',
             'latch_D1', 'latch_D2', 'latch_W1', 'latch_W2', 'latch_ZR2', 'latch_L2',
             'latch_B1', 'latch_B2', 'latch_T1', 'latch_T2', 'latch_T10',
             'latch_ST1', 'latch_ST2', 'latch_ST10', 'latch_C1', 'latch_C2')
    pairs = []
    for index, name in enumerate(names):
        offset = extended + 4 + index * 8
        lower, upper = struct.unpack_from('<II', raw, offset)
        pairs.append(dict(name=name, offset=offset, lower=lower, upper=upper,
            handling=('unset' if lower == upper == 0xffffffff else
                      'range' if lower <= upper < 0xffffffff else 'opaque-preserved'),
            raw_hex=raw[offset:offset + 8].hex()))
    return dict(handling='partially-decoded', extended_offset=extended, extended_bytes=228,
        extended_sha256=sha256(raw[extended:extended + 228]), ranges=pairs,
        legacy_offset=start, legacy_raw_hex=raw[start:start + 88].hex(),
        legacy_agrees=raw[start:start + 88] == raw[extended + 4:extended + 92],
        intervening_count=count, opaque_regions=[dict(offset=a, bytes=b-a, sha256=sha256(raw[a:b]))
            for a, b in [(0, start), (start + 88, extended), (extended + 228, len(raw))]],
        scope='stored allocation limits; not proof of capacity consistency or program validity')


def inspect(source: bytes) -> dict:
    project = inspect_project(source)
    if project.diagnostics or any(stream.error for stream in project.streams):
        raise ValueError('incomplete project inventory')
    result = dict(source_sha256=sha256(source), parameters=[])
    for stream in project.streams:
        if (stream.logical_name or '').endswith('.prj'):
            try:
                result['project_allocation'] = dict(name=stream.logical_name, **project_allocation_ranges(stream.raw))
            except ValueError as exc:
                result['project_allocation'] = dict(name=stream.logical_name, handling='opaque-preserved', diagnostic=str(exc))
    for stream in project.streams:
        if not (stream.logical_name or '').endswith('.wpa'):
            continue
        item = dict(name=stream.logical_name, sha256=sha256(stream.raw), bytes=len(stream.raw))
        result['parameters'].append(item)
        try:
            records = frame_parameters(stream.raw)
        except ValueError as exc:
            item.update(handling='opaque-preserved', diagnostic=str(exc))
            continue
        item.update(handling='framed', reconstruction='byte-identical', records=[
            dict(offset=r.offset, length=len(r.raw), tag=f'0x{r.tag:04x}',
                 sha256=sha256(r.raw), handling='opaque-preserved') for r in records])
        try:item['timer_parameters']=timer_parameter_record(stream.raw)
        except ValueError as exc:item['timer_parameter_diagnostic']=str(exc)
        try:
            record = device_allocation_record(stream.raw)
        except ValueError as exc:
            item['device_allocation_diagnostic'] = str(exc)
            continue
        item['device_allocation'] = dict(offset=record.offset, length=len(record.raw),
            sha256=sha256(record.raw), handling='partially-decoded',
            entries=[dict(raw_hex=record.raw[i:i + 4].hex(),
                          code=f'0x{struct.unpack_from("<H", record.raw, i)[0]:04x}',
                          value=struct.unpack_from('<H', record.raw, i + 2)[0])
                     for i in range(6, len(record.raw), 4)])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(inspect(args.source.read_bytes()), output, ensure_ascii=False, indent=2)
        output.write('\n')

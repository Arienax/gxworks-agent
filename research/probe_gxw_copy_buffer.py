"""Bounded, raw-preserved inventory of observed native clipboard archives.

This is an experimental envelope, not a general GXW serializer. Types 14 and
26 are observed global tables and user POUs. The native project-mode/CPU/code
page header and opaque object metadata remain intact. Grafting streams into a
native template is explicitly synthetic until accepted by the native importer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import struct


@dataclass(frozen=True)
class Member:
    header: bytes
    raw: bytes

    @property
    def name(self):
        return self.header[4:604].decode('utf-16le').split('\0', 1)[0]

    def serialize(self):
        return self.header + struct.pack('<I', len(self.raw)) + self.raw


@dataclass(frozen=True)
class CopyObject:
    prefix: bytes
    name: str
    metadata: bytes
    payload: bytes
    members: tuple[Member, ...]

    @property
    def data_type(self):
        return struct.unpack_from('<I', self.prefix)[0]

    def serialize(self):
        name = (self.name + '\0').encode('utf-16le')
        payload = b''.join(m.serialize() for m in self.members) if self.data_type == 26 else self.payload
        return (self.prefix + struct.pack('<I', len(name) // 2) + name + self.metadata
                + struct.pack('<I', len(payload)) + payload)


@dataclass(frozen=True)
class CopyBuffer:
    header: bytes
    objects: tuple[CopyObject, ...]

    def serialize(self):
        body = b''.join(obj.serialize() for obj in self.objects)
        header = bytearray(self.header)
        struct.pack_into('<II', header, 0, len(body) + len(header), len(self.objects))
        return bytes(header) + body


def parse(raw):
    if not 32 <= len(raw) <= 16 * 1024 * 1024:
        raise ValueError('copy archive outside bound')
    total, count = struct.unpack_from('<II', raw)
    if total != len(raw) or not 1 <= count <= 32:
        raise ValueError('copy archive header differs from observed framing')
    pos, objects = 32, []
    for _ in range(count):
        if pos + 13 > len(raw):
            raise ValueError('truncated copy object')
        prefix = raw[pos:pos + 9]
        kind = struct.unpack_from('<I', prefix)[0]
        n = struct.unpack_from('<I', raw, pos + 9)[0]
        end_name = pos + 13 + n * 2
        if kind not in (14, 26) or not 1 < n <= 33 or end_name + 64 > len(raw):
            raise ValueError('unsupported copy object framing')
        name_raw = raw[pos + 13:end_name]
        if name_raw[-2:] != b'\0\0':
            raise ValueError('unterminated copied object name')
        name = name_raw[:-2].decode('utf-16le')
        if '\0' in name:
            raise ValueError('embedded NUL in copied object name')
        metadata = raw[end_name:end_name + 60]
        size = struct.unpack_from('<I', raw, end_name + 60)[0]
        start, end = end_name + 64, end_name + 64 + size
        if end > len(raw):
            raise ValueError('truncated copied object payload')
        payload, members = raw[start:end], []
        if kind == 26:
            at = start
            while at < end:
                if at + 608 > end or len(members) >= 32:
                    raise ValueError('truncated or excessive POU members')
                length = struct.unpack_from('<I', raw, at + 604)[0]
                if at + 608 + length > end:
                    raise ValueError('POU member exceeds containing object')
                member = Member(raw[at:at + 604], raw[at + 608:at + 608 + length])
                if not member.name or '\0' not in member.header[4:604].decode('utf-16le'):
                    raise ValueError('unterminated POU member name')
                members.append(member)
                at += 608 + length
        objects.append(CopyObject(prefix, name, metadata, payload, tuple(members)))
        pos = end
    if pos != len(raw):
        raise ValueError('unconsumed copy archive bytes')
    result = CopyBuffer(raw[:32], tuple(objects))
    if result.serialize() != raw:
        raise ValueError('copy archive was not preserved byte for byte')
    return result


def graft_pou(template, *, name, labels, program):
    """Construct a synthetic archive retaining a native template's envelope.

    Preserve the donor streams exactly. Reject a declaration owner, POU-type,
    or language mismatch rather than silently converting it. Opaque envelope
    metadata stays template-owned and is not claimed to match the source GXW.
    """
    from gxw.declarations import parse_declarations
    if len(template.objects) != 1 or template.objects[0].data_type != 26:
        raise ValueError('graft requires one native user POU template')
    obj = template.objects[0]
    declaration = parse_declarations(labels, logical_name=name + '.Labels.lh')
    if not 1 <= len(name) <= 32 or declaration.owner_name != name:
        raise ValueError('source declaration owner differs from graft name')
    owner_end = 54 + 4 + 2 * (len(name.encode('utf-16le')) // 2 + 1)
    if labels[owner_end:owner_end + 4] != obj.prefix[4:8]:
        raise ValueError('native template POU type differs from donor declaration')
    if len(program) <= 54 or program[54] != obj.prefix[8]:
        raise ValueError('native template language differs from donor program')
    if [(struct.unpack_from('<I', m.header)[0], m.name) for m in obj.members] != [(28, 'Labels'), (32, 'Program')]:
        raise ValueError('native POU member set differs from the inspected template')
    changed = replace(obj, name=name, members=(replace(obj.members[0], raw=labels),
                                               replace(obj.members[1], raw=program)))
    result = replace(template, objects=(changed,)).serialize()
    parsed = parse(result)
    if [m.raw for m in parsed.objects[0].members] != [labels, program]:
        raise ValueError('grafted streams changed')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('buffer', type=Path)
    args = parser.parse_args()
    archive = parse(args.buffer.read_bytes())
    print(json.dumps(dict(header_hex=archive.header.hex(), objects=[
        dict(name=o.name, data_type=o.data_type, metadata_hex=o.metadata.hex(), members=[
            dict(name=m.name, size=len(m.raw), sha256=hashlib.sha256(m.raw).hexdigest())
            for m in o.members]) for o in archive.objects]), indent=2))

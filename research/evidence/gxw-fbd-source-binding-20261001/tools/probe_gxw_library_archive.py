"""Read-only reconstruction of the observed native Packwin library decompressor.

Recovered from DZDataABS_PackwinObject.dll SHA-256
491b881556ec3aab359002a6d8414df32ab25dd7340137449bf07d92adec713d.
Keep the original archive and its metadata: decoded bytes are a separate view,
not evidence that arbitrary replacement archives are accepted by GX Works2.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import re


# Native tables at RVA 0xa420 / 0xa520. The first 8 bits select a prefix;
# its remaining bits complete a 12-bit backward distance (RVA 0x21d3).
_POSITION_LENGTHS = (3,) + (4,) * 3 + (5,) * 8 + (6,) * 12 + (7,) * 24 + (8,) * 16
_POSITION_CODE = tuple(code for code, length in enumerate(_POSITION_LENGTHS) for _ in range(1 << (8 - length)))
_POSITION_BITS = tuple(length for length in _POSITION_LENGTHS for _ in range(1 << (8 - length)))
_SYMBOLS = 0x13a
_TREE = _SYMBOLS * 2 - 1
_ROOT = _TREE - 1


@dataclass(frozen=True)
class DecodedLibrary:
    archive: bytes
    decoded: bytes
    declared_size: int
    consumed_bits: int
    tree_rebuilds: int

    @property
    def trailing_bytes(self):
        return self.archive[4 + (self.consumed_bits + 7) // 8:]


class _Huffman:
    def __init__(self):
        self.frequency = [1] * _SYMBOLS + [0] * (_TREE + 1 - _SYMBOLS)
        self.child = [i + _TREE for i in range(_SYMBOLS)] + [0] * (_TREE - _SYMBOLS)
        self.parent = [0] * (_TREE + _SYMBOLS)
        self.rebuilds = 0
        for i in range(_SYMBOLS):
            self.parent[i + _TREE] = i
        left = 0
        for i in range(_SYMBOLS, _TREE):
            self.frequency[i] = self.frequency[left] + self.frequency[left + 1]
            self.child[i] = left
            self.parent[left] = self.parent[left + 1] = i
            left += 2
        self.frequency[_TREE] = 0xffff

    def rebuild(self):
        # RVA 0x18fc: halve leaf frequencies and reconstruct sorted branches.
        frequency, child, parent = self.frequency, self.child, self.parent
        leaves = [((frequency[i] + 1) >> 1, child[i]) for i in range(_TREE) if child[i] >= _TREE]
        for i, (weight, symbol) in enumerate(leaves):
            frequency[i], child[i] = weight, symbol
        left = 0
        for i in range(_SYMBOLS, _TREE):
            weight = frequency[left] + frequency[left + 1]
            at = i
            while at > 0 and weight < frequency[at - 1]:
                at -= 1
            frequency[at + 1:i + 1] = frequency[at:i]
            child[at + 1:i + 1] = child[at:i]
            frequency[at], child[at] = weight, left
            left += 2
        for i in range(_TREE):
            parent[child[i]] = i
            if child[i] < _TREE:
                parent[child[i] + 1] = i
        self.rebuilds += 1

    def update(self, symbol):
        # RVA 0x1a30: increase weights, preserving native sibling ordering.
        if self.frequency[_ROOT] == 0x8000:
            self.rebuild()
        frequency, child, parent = self.frequency, self.child, self.parent
        node = parent[symbol + _TREE]
        while True:
            frequency[node] += 1
            weight = frequency[node]
            other = node + 1
            if weight > frequency[other]:
                while weight > frequency[other + 1]:
                    other += 1
                frequency[node], frequency[other] = frequency[other], weight
                first, second = child[node], child[other]
                parent[first] = other
                if first < _TREE:
                    parent[first + 1] = other
                parent[second] = node
                if second < _TREE:
                    parent[second + 1] = node
                child[node], child[other] = second, first
                node = other
            node = parent[node]
            if node == 0:
                return


def decode_library_archive(raw: bytes, *, maximum_output=32 * 1024 * 1024) -> DecodedLibrary:
    """Decode the .LIB payload (the observed .lif stream has a 20-byte prefix)."""
    if len(raw) < 4:
        raise ValueError('truncated library size')
    size = struct.unpack_from('<I', raw)[0]
    if size > maximum_output:
        raise ValueError('declared library size exceeds output bound')
    source = bytes((value + 0x10) & 255 for value in raw[4:])
    bit_index = 0

    def bits(count):
        nonlocal bit_index
        if bit_index + count > len(source) * 8:
            raise ValueError('truncated library bitstream')
        result = 0
        for _ in range(count):
            result = (result << 1) | ((source[bit_index >> 3] >> (7 - (bit_index & 7))) & 1)
            bit_index += 1
        return result

    tree = _Huffman()
    window = bytearray(b' ' * (4096 - 60) + b'\0' * 60)
    initialized = bytearray(b'\1' * (4096 - 60) + b'\0' * 60)
    cursor = 4096 - 60
    decoded = bytearray()
    while len(decoded) < size:
        node = tree.child[_ROOT]
        while node < _TREE:
            node = tree.child[node + bits(1)]
        symbol = node - _TREE
        tree.update(symbol)
        if symbol < 256:
            decoded.append(symbol)
            window[cursor] = symbol
            initialized[cursor] = 1
            cursor = (cursor + 1) & 4095
        else:
            first = bits(8)
            count = _POSITION_BITS[first] - 2
            distance = (_POSITION_CODE[first] << 6) | (((first << count) | bits(count)) & 63)
            length = symbol - 253
            if len(decoded) + length > size:
                raise ValueError('library match exceeds declared output size')
            offset = (cursor - distance - 1) & 4095
            for i in range(length):
                read_at = (offset + i) & 4095
                if not initialized[read_at]:
                    raise ValueError('library match reads outside initialized history')
                value = window[read_at]
                decoded.append(value)
                window[cursor] = value
                initialized[cursor] = 1
                cursor = (cursor + 1) & 4095
    return DecodedLibrary(raw, bytes(decoded), size, bit_index, tree.rebuilds)


def encode_library_literals(source: bytes, *, maximum_input=32 * 1024 * 1024) -> bytes:
    """Experimental Huffman-only encoding: valid tokens, no match optimization.

    This deliberately uses literal symbols only, separating native acceptance
    of the archive framing from reproducing the vendor's LZ match choices.
    Caller must retain the original archive; this is not byte-identical packing.
    """
    if len(source) > maximum_input:
        raise ValueError('source exceeds experimental encoder bound')
    output = bytearray(struct.pack('<I', len(source)))
    tree = _Huffman()
    accumulator = 0
    used = 0
    for symbol in source:
        node = tree.parent[symbol + _TREE]
        path = []
        while node != _ROOT:
            path.append(node & 1)
            node = tree.parent[node]
        for bit in reversed(path):
            accumulator = accumulator << 1 | bit
            used += 1
            if used == 8:
                output.append((accumulator - 0x10) & 255)
                accumulator = used = 0
        tree.update(symbol)
    if used:
        output.append(((accumulator << (8 - used)) - 0x10) & 255)
    return bytes(output)


def frame_library_text(raw: bytes) -> dict:
    """Frame observed ASC definitions without rewriting comments or encoding.

    CPU sections and flags retain source provenance. They are evidence for
    native selection, not an implementation of all native library directives.
    """
    masked = bytearray(raw)
    comments = []
    depth = 0
    start = 0
    for token in re.finditer(rb'\(\*|\*\)', raw):
        if token[0] == b'(*':
            if depth == 0:
                start = token.start()
            depth += 1
        else:
            if depth == 0:
                raise ValueError('unmatched library comment terminator')
            depth -= 1
            if depth == 0:
                end = token.end()
                comments.append((start, end))
                masked[start:end] = re.sub(rb'[^\r\n]', b' ', raw[start:end])
    if depth:
        raise ValueError('unterminated library comment')
    directives = []
    for start, end in comments:
        value = raw[start + 2:end - 2]
        if value.startswith(b'$'):
            directive, sep, argument = value[1:].partition(b':')
            directives.append(dict(offset=start, end=end, directive=directive.strip().decode('ascii'),
                argument=argument.strip().decode('ascii') if sep else None, raw=raw[start:end]))
    tokens = list(re.finditer(rb'(?mi)^[ \t]*(FUNCTION_BLOCK|FUNCTION|END_FUNCTION_BLOCK|END_FUNCTION)\b([^\r\n]*)', masked))
    if len(tokens) % 2:
        raise ValueError('unpaired library definition')
    definitions = []
    sections = None
    section_offset = None
    pending = []
    d_index = 0
    previous_end = 0
    for index in range(0, len(tokens), 2):
        opening, closing = tokens[index:index + 2]
        kind = opening[1].upper()
        if kind not in (b'FUNCTION', b'FUNCTION_BLOCK') or closing[1].upper() != b'END_' + kind:
            raise ValueError('nested or mismatched library definition')
        header = opening[2].strip()
        name, sep, return_type = header.partition(b':')
        if not re.fullmatch(rb'[A-Za-z_0-9$+*/<>=.\-]+', name.strip()):
            raise ValueError('outside observed library name syntax')
        while d_index < len(directives) and directives[d_index]['offset'] < opening.start():
            directive = directives[d_index]
            if directive['offset'] < previous_end:
                raise ValueError('library directive inside a definition requires separate handling')
            if directive['directive'] == 'SECTION':
                sections = [s.strip() for s in directive['argument'].split(',')]
                section_offset = directive['offset']
            else:
                pending.append(directive)
            d_index += 1
        end = closing.end()
        while end < len(raw) and raw[end:end + 1] in (b'\r', b'\n'):
            end += 1
        definitions.append(dict(name=name.strip().decode('ascii'), kind=kind.decode('ascii'),
            return_type_raw=return_type.strip().decode('ascii') if sep else None,
            offset=opening.start(), end=end, raw=raw[opening.start():end],
            sections=sections, section_directive_offset=section_offset, preceding_directives=tuple(pending)))
        pending = []
        previous_end = end
    if any(d['offset'] < previous_end for d in directives[d_index:]):
        raise ValueError('unhandled directive inside final definition')
    regions = []
    cursor = 0
    for definition in definitions:
        if cursor < definition['offset']:
            regions.append(dict(offset=cursor, end=definition['offset'], kind='preserved-interstitial', raw=raw[cursor:definition['offset']]))
        regions.append(dict(offset=definition['offset'], end=definition['end'], kind='definition', raw=definition['raw']))
        cursor = definition['end']
    if cursor < len(raw):
        regions.append(dict(offset=cursor, end=len(raw), kind='preserved-interstitial', raw=raw[cursor:]))
    if b''.join(r['raw'] for r in regions) != raw:
        raise ValueError('library source partition is not lossless')
    return dict(definitions=definitions, regions=regions, directives=directives,
        text_encoding='not assumed; byte offsets and non-ASCII comments preserved')


def observed_source_declarations(definition: dict) -> dict:
    """Frame the observed ASCII declaration lines; preserve bracket annotations.

    Block transitions remain explicit, including vendor definitions that omit
    END_VAR between two variable classes. No missing terminator is synthesized.
    """
    raw = definition['raw']
    masked = re.sub(rb'\(\*.*?\*\)', lambda m: re.sub(rb'[^\r\n]', b' ', m[0]), raw, flags=re.S)
    rows, transitions, gaps = [], [], []
    current = None
    cursor = 0
    for line in masked.splitlines(keepends=True):
        content = line.strip()
        header = re.fullmatch(rb'(VAR(?:_INPUT|_OUTPUT|_IN_OUT|_IN_EXT)?)(?:[ \t]+(CONSTANT))?', content)
        if header:
            current = b' '.join(x for x in header.groups() if x).decode('ascii')
            transitions.append(dict(offset=cursor, current=current, raw=raw[cursor:cursor + len(line)]))
        elif content == b'END_VAR':
            current = None
            transitions.append(dict(offset=cursor, current=None, raw=raw[cursor:cursor + len(line)]))
        elif content in (b'BODY_IL', b"'IL'", b'BODY') or content.startswith(b'END_FUNCTION'):
            break
        elif current and content:
            match = re.fullmatch(rb'\s*([A-Za-z_][A-Za-z_0-9]*)\s*:\s*([^;]+);(.*)', line.rstrip(b'\r\n'))
            if match is None:
                gaps.append(dict(offset=cursor, raw=raw[cursor:cursor + len(line)]))
            else:
                data_type, sep, initial = match[2].partition(b':=')
                rows.append(dict(offset=cursor, end=cursor + len(line), name=match[1].decode('ascii'),
                    source_class=current, data_type_raw=data_type.strip().decode('ascii'),
                    initial_raw=initial.strip().decode('ascii') if sep else '',
                    annotation_raw=match[3], raw=raw[cursor:cursor + len(line)]))
        cursor += len(line)
    return dict(rows=rows, transitions=transitions, gaps=gaps)


def observed_source_body(definition: dict) -> bytes | None:
    raw = definition['raw']
    starts = list(re.finditer(rb'(?m)^[ \t]*TABINFO[ \t]+[0-9]+[ \t]*,[ \t]*[0-9]+[ \t]*\r?\n', raw))
    ends = list(re.finditer(rb'(?m)^[ \t]*END_NETWORK_BODY\b', raw))
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0].end() > ends[0].start():
        raise ValueError('outside observed single-network library source body')
    return raw[starts[0].end():ends[0].start()]


def observed_source_template_bindings(definition: dict) -> dict | None:
    from probe_gxw_library_sources import observed_template_text
    body = observed_source_body(definition)
    if body is None or not body.lstrip().startswith(b'#'):
        return None
    declarations = observed_source_declarations(definition)
    if declarations['gaps']:
        raise ValueError('source port declarations contain unframed bytes')
    template = observed_template_text(body.decode('ascii'))
    ports = declarations['rows']
    for instruction in template['instructions']:
        for operand in instruction['operands']:
            ids = [int(m[1]) for m in re.finditer(r'@([0-9]+)', operand['raw'])]
            if any(n < 1 or n > len(ports) for n in ids):
                raise ValueError('source template ordinal exceeds complete port declarations')
            operand['source_port_references'] = [dict(ordinal=n, name=ports[n - 1]['name'],
                source_class=ports[n - 1]['source_class'], data_type=ports[n - 1]['data_type_raw'],
                declaration_offset=definition['offset'] + ports[n - 1]['offset']) for n in ids]
    template['placeholder_binding'] = 'one-based complete source declaration order; EN and ENO included'
    template['operand_transformation'] = 'per-operand observed descriptor transformations; CPU conditions and target validity remain explicit'
    return template


def inspect_library_stream(raw: bytes, *, cpu_section: str | None = None) -> dict:
    """Retain the complete observed LIF envelope while exposing a source view."""
    if len(raw) < 24 or struct.unpack_from('<I', raw)[0] != 20:
        raise ValueError('outside observed 20-byte LIF metadata envelope')
    decoded = decode_library_archive(raw[20:])
    framed = frame_library_text(decoded.decoded)
    eligible = framed['definitions'] if cpu_section is None else [d for d in framed['definitions']
        if d['sections'] is None or cpu_section in d['sections']]
    definitions = []
    for definition in eligible:
        declarations = observed_source_declarations(definition)
        definitions.append(dict(definition, declarations=declarations,
            template=observed_source_template_bindings(definition)))
    return dict(raw=raw, header_raw=raw[:20], header_handling='opaque-preserved',
        archive=decoded, source=framed, definitions=definitions,
        cpu_section=cpu_section, selection='all source variants' if cpu_section is None else 'observed SECTION matching',
        handling='decoded-source-with-preserved-raw', writer_support=False)


def main():
    import argparse
    import base64
    from dataclasses import asdict, is_dataclass
    import hashlib
    import json
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from gxw.lossless import inspect_project

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cpu-section', help='literal ASC SECTION token; omitted retains every CPU variant')
    parser.add_argument('--name', help='optional exact POU name to select after CPU filtering')
    args = parser.parse_args()
    raw = args.input.read_bytes()
    streams = [(s.logical_name, s.raw) for s in inspect_project(raw).streams
        if s.logical_name and s.logical_name.lower().endswith('.lif')]
    results = []
    for name, payload in streams:
        row = dict(logical_name=name, sha256=hashlib.sha256(payload).hexdigest(), raw=payload)
        try:
            view = inspect_library_stream(payload, cpu_section=args.cpu_section)
            if args.name:
                view['definitions'] = [d for d in view['definitions'] if d['name'] == args.name]
            # Original archive plus full reconstructed text preserve provenance;
            # report selected semantic views without duplicating every raw span.
            row.update(handling=view['handling'], metadata_raw=view['header_raw'],
                metadata_handling=view['header_handling'], decoded_raw=view['archive'].decoded,
                consumed_bits=view['archive'].consumed_bits, tree_rebuilds=view['archive'].tree_rebuilds,
                trailing_bytes=view['archive'].trailing_bytes, cpu_section=args.cpu_section,
                total_source_definitions=len(view['source']['definitions']), definitions=view['definitions'])
        except ValueError as exc:
            row.update(handling='opaque-preserved', gap=str(exc))
        results.append(row)

    def convert(value):
        if isinstance(value, bytes):
            return {'base64': base64.b64encode(value).decode('ascii'), 'bytes': len(value),
                'sha256': hashlib.sha256(value).hexdigest()}
        if is_dataclass(value):
            return asdict(value)
        raise TypeError(type(value).__name__)

    output = dict(source_path=str(args.input.resolve()), source_sha256=hashlib.sha256(raw).hexdigest(),
        method='read-only native Packwin reconstruction and raw-preserved ASC framing',
        evidence='native text and frontend cross-checks; experimental literal repacking accepted in isolated native controls', streams=results)
    with args.output.open('x', encoding='utf-8') as file:
        json.dump(output, file, default=convert, ensure_ascii=True, indent=2)
    print(json.dumps(dict(streams=len(results),decoded=sum(r['handling']=='decoded-source-with-preserved-raw' for r in results),
        definitions=sum(len(r.get('definitions', [])) for r in results))))


if __name__ == '__main__':
    main()

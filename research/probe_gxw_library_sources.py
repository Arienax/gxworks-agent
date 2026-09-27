"""Bind native library source buffers to saved compiler-debug IL source points.

The snapshot is a pointer-free copy of the vendor frontend ABI, not a GXW
stream. Keep that provenance explicit. Empty buffers and timer placeholders do
not establish missing implementations. This probe frames physical source lines;
it does not introduce a second IL compiler or infer instruction access roles.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.lossless import sha256


def snapshot_library_pous(snapshot: dict) -> list[dict]:
    children_cache = {}

    def raw(blob):
        return base64.b64decode(blob['raw'], validate=True)

    def child(blob, offset):
        key = id(blob)
        if key not in children_cache:
            children = {r['offset']: r['data'] for r in blob['relocations']}
            if len(children) != len(blob['relocations']):
                raise ValueError('duplicate native ABI relocation')
            children_cache[key] = children
        if offset not in children_cache[key]:
            raise ValueError('missing native ABI relocation')
        return children_cache[key][offset]

    def name(blob):
        value = raw(blob)
        if not value.endswith(b'\0') or b'\0' in value[:-1]:
            raise ValueError('unbounded native library name')
        return value[:-1].decode('ascii')

    libraries = child(snapshot['build'], 8)
    if len(raw(libraries)) % 32:
        raise ValueError('incomplete native library array')
    rows = []
    for offset in range(0, len(raw(libraries)), 32):
        library = name(child(libraries, offset))
        pool = child(libraries, offset + 24)
        data = raw(pool)
        if len(data) % 56 or len(data) // 56 > 8192:
            raise ValueError('invalid native POU array')
        for start in range(0, len(data), 56):
            declaration = raw(child(pool, start + 16))
            program = raw(child(pool, start + 24))
            if struct.unpack_from('<I', data, start + 20)[0] != len(declaration) or struct.unpack_from('<I', data, start + 28)[0] != len(program):
                raise ValueError('native buffer length differs from POU descriptor')
            rows.append(dict(library=library, name=name(child(pool, start + 8)),
                descriptor_raw_base64=base64.b64encode(data[start:start + 56]).decode(),
                declaration_raw_base64=base64.b64encode(declaration).decode(),
                program_raw_base64=base64.b64encode(program).decode(),
                program_sha256=sha256(program), program_bytes=len(program)))
    return rows


def observed_library_text(raw: bytes, *, native_kind: int) -> str:
    # These are native frontend-buffer kinds, not project Program.pou kinds.
    # In particular native kind 240 here contains instruction templates.
    if native_kind not in (192, 240):
        raise ValueError('outside observed native library text kinds')
    prefix = b'@\0"\x01' + b' ' * 32
    if (len(raw) < 111 or raw[:36] != prefix or raw[42] != native_kind
            or struct.unpack_from('<IIII', raw, 64) != (len(raw) - 64, 1, 1, len(raw) - 76)
            or struct.unpack_from('<I', raw, 80)[0] != 1
            or raw[84:98] != bytes.fromhex('0001000000000000000000000000')
            or struct.unpack_from('<III', raw, 98) != (6, 12, len(raw) - 110)
            or raw[-1:] != b'\0' or b'\0' in raw[110:-1]):
        raise ValueError('outside observed native library text envelope')
    return raw[110:-1].decode('ascii')


def observed_library_declarations(raw: bytes) -> dict:
    """Frame the legacy ANSI library ABI; this is not a GXW Labels.lh stream."""
    # Bytes 4..35 may contain a description, including F_INCN/F_SHFT in the
    # FX3G custom-library snapshot. Both header variants remain raw below.
    if (len(raw) < 93 or raw[:4] != b'@\0"\x01'
            or struct.unpack_from('<I', raw, 64)[0] != len(raw) - 64
            or struct.unpack_from('<I', raw, 68)[0] not in (0, 1)):
        raise ValueError('outside observed native declaration envelope')
    header_fields = struct.unpack_from('<IIII', raw, 68)
    cursor = 84

    def integer():
        nonlocal cursor
        if cursor + 4 > len(raw):
            raise ValueError('truncated native declaration integer')
        value = struct.unpack_from('<I', raw, cursor)[0]
        cursor += 4
        return value

    def string():
        nonlocal cursor
        length = integer()
        if length < 1 or length > len(raw) - cursor:
            raise ValueError('unbounded native declaration string')
        value = raw[cursor:cursor + length]
        cursor += length
        if value[-1:] != b'\0' or b'\0' in value[:-1]:
            raise ValueError('invalid native declaration terminator')
        return value[:-1].decode('ascii')

    name = string()
    count = integer()
    if count > 8192 or count > (len(raw) - cursor) // 28:
        raise ValueError('native declaration count exceeds buffer')
    header_end = cursor
    rows = []
    for _ in range(count):
        start = cursor
        row = dict(record_id=integer(), name=string(), data_type=string(),
            class_code=integer(), initial_value=string(), comment=string())
        row.update(offset=start, raw_base64=base64.b64encode(raw[start:cursor]).decode())
        rows.append(row)
    return dict(header_fields=list(header_fields), name=name, rows=rows,
        header_raw_base64=base64.b64encode(raw[:header_end]).decode(),
        header_metadata_handling='opaque-preserved', trailer_raw_base64=base64.b64encode(raw[cursor:]).decode(),
        fully_framed=cursor == len(raw),
        implicit_ports='raw declarations may omit compiler-inserted EN and ENO; record IDs are not template ordinals')


def observed_graph_formals(raw: bytes, *, input_port_count: int | None = None) -> dict:
    """Expand observed library declarations into graph formal parameter order.

    SICConverter_IEC 1.635.0.1 RVA 0x4dbe9 supplies the independent native view.
    Its type table is initialized at 0x4de54..0x4df98. This view describes
    interface binding only: IN_OUT expressions can compile without copying
    the result back to the source device, so it is not an access/effect model.
    ``raw`` is the library frontend ABI, never a project Labels.lh payload.
    """
    result = dict(handling='opaque-preserved', raw_base64=base64.b64encode(raw).decode(),
                  formals=[], gaps=[], execution_effects='not inferred')
    try:
        declaration = observed_library_declarations(raw)
        result['declaration'] = declaration
        header = declaration['header_fields']
        if (not declaration['fully_framed'] or header[0] != 1
                or header[1] not in (1, 2) or header[2] not in (1, 2, 3)
                or header[3] not in (0, 2, 4, 5, 6, 13)):
            raise ValueError('library declaration header outside observed graph interfaces')
        rows = declaration['rows']
        if any(r['class_code'] not in (1, 2, 3, 4, 5, 11, 12) for r in rows):
            raise ValueError('unobserved declaration class')
        # Preserve class 11: the FMOV native control rejects a variable at
        # that input even though the interface binding itself succeeds.
        inputs = [r for r in rows if r['class_code'] in (3, 11, 12)]
        outputs = [r for r in rows if r['class_code'] == 4]
        inouts = [r for r in rows if r['class_code'] == 5]
        enabled = bool(header[3] & 4)
        extensible = [r for r in inputs if r['class_code'] == 12]
        if extensible:
            if len(extensible) != 1 or len(inputs) != 1 or input_port_count is None:
                raise ValueError('extensible input requires an observed graph port count')
            repeat = input_port_count - len(inouts) - int(enabled)
            if not 1 <= repeat <= 256:
                raise ValueError('extensible graph input count outside bounded projection')
            inputs = inputs * repeat
        inputs = inputs + inouts
        outputs = outputs + inouts
        if enabled:
            inputs = [dict(name='EN', data_type='BOOL', class_code=3)] + inputs
            outputs = [dict(name='ENO', data_type='BOOL', class_code=4)] + outputs
        if input_port_count is not None and input_port_count != len(inputs):
            raise ValueError('graph input count differs from formal interface')
        type_codes = {
            'BOOL': 1, 'INT': 4, 'DINT': 8, 'WORD': 0x20000, 'DWORD': 0x40000,
            'REAL': 0x200, 'LREAL': 0x400, 'TIME': 0x800, 'STRING': 0x8000,
            'ANY_INT': 0x1fe, 'ANY_NUM': 0x7fe, 'ANY_BIT': 0xf0001,
            'ANY': 0xffffff, 'ANY_SIMPLE': 0xfffff, 'ANY16': 0x20044,
            'ANY32': 0x40088, 'ANY_REAL': 0x600,
        }
        for row in inputs + outputs:
            code = type_codes.get(row['data_type'])
            # RVA 0x4e3db..0x4e4ec collapses these declarations to coarse
            # ARRAY / STRING masks. Bounds and lengths do not survive in
            # the native 16-byte graph formal descriptor. Retain their
            # original spelling below; this mask is not a buffer extent.
            if code is None:
                array = re.fullmatch(r'ARRAY ?\[([^]]+)\] OF (\w+)', row['data_type'])
                if array and array[2] in type_codes:
                    bounds = [re.fullmatch(r'(-?\d+)\.\.(-?\d+)', part)
                              for part in array[1].split(',')]
                    if bounds and all(b and -2**31 <= int(b[1]) <= int(b[2]) < 2**31
                                      for b in bounds):
                        code = 0x100000
                elif re.fullmatch(r'STRING\([1-9][0-9]*\)', row['data_type']):
                    code = 0x8000
            result['formals'].append(dict(name=row['name'], class_code=row['class_code'],
                                          type_code=code, declared_type=row['data_type']))
            if code is None:
                result['gaps'].append('unmodeled formal type: ' + row['data_type'])
        result['input_count'] = len(inputs)
        result['output_count'] = len(outputs)
        result['handling'] = 'partially-decoded' if result['gaps'] else 'observed-graph-formals'
    except ValueError as exc:
        result['gaps'].append(str(exc))
    return result


def observed_builtin_template(raw: bytes) -> dict:
    text = observed_library_text(raw, native_kind=240)
    return observed_template_text(text)


def observed_template_text(text: str) -> dict:
    """Shared framing for native ABI and independently unpacked source text."""
    from probe_gxw_template_operands import observed_operand_transformation
    instructions = []
    for line in text.splitlines(keepends=True):
        fields = line.strip(' \t\r\n').split('##')
        command, operands = fields[0], fields[1:]
        count = str(len(operands))
        # Use the actual delimited operand count to separate the suffix:
        # DLOG102 means opcode DLOG10 with two operands, not opcode DLOG / 102.
        if (not command.startswith('#') or not command.endswith(count)
                or len(command) <= len(count) + 1 or '#' in command[1:]
                or any(not o.strip() or '#' in o for o in operands)):
            raise ValueError('outside observed instruction-template framing')
        opcode = command[1:-len(count)]
        instructions.append(dict(raw_line=line, opcode=opcode, operand_count=len(operands),
            operands=[dict(raw=o, placeholder_id=int(o.strip()[1:]) if re.fullmatch(r'@[0-9]+', o.strip()) else None,
                handling='bare-placeholder' if re.fullmatch(r'@[0-9]+', o.strip()) else 'preserved-template-operand') for o in operands]))
    if not instructions:
        raise ValueError('empty native instruction template')
    for instruction in instructions:
        for operand in instruction['operands']:
            operand['transformation'] = observed_operand_transformation(operand['raw'])
    return dict(handling='bounded-native-instruction-template', text=text, instructions=instructions,
        placeholder_binding='unresolved; integer suffix alone does not establish a declaration or access role')


def observed_library_il(raw: bytes) -> dict:
    text = observed_library_text(raw, native_kind=192)
    placeholder = text == 'LD\tTRUE (* To get no warning message during check *)\r\n'
    lines = []
    offset = 110
    for index, physical in enumerate(text.splitlines(keepends=True)):
        value = physical.encode('ascii')
        lines.append(dict(line_index=index, offset=offset, text=physical.rstrip('\r\n'),
            raw_base64=base64.b64encode(value).decode()))
        offset += len(value)
    return dict(handling='native-library-placeholder' if placeholder else 'bounded-native-library-IL-lines',
        header_raw_base64=base64.b64encode(raw[:64]).decode(), header_metadata_handling='opaque-preserved',
        lines=lines, text=text, coordinate_meaning='zero-based physical line in native library buffer')


def inspect(native_directory: Path, correlation_path: Path, output: Path) -> dict:
    native_directory = native_directory.resolve()
    outcome = json.loads((native_directory / 'outcome.json').read_text())
    request = json.loads((native_directory / 'request.json').read_text())
    plan = json.loads((native_directory / 'plan.json').read_text())
    correlation_raw = correlation_path.read_bytes()
    correlation = json.loads(correlation_raw)
    export = native_directory / 'native-saved.gxw'
    if (outcome.get('returncode') != 0 or not outcome.get('compile_completed') or outcome.get('compiler_rejected')
            or not plan.get('snapshot_frontend')
            or sha256((native_directory / 'input.gxw').read_bytes()) != request['input_sha256']
            or sha256(export.read_bytes()) != outcome.get('native_export', {}).get('sha256')
            or correlation['export_sha256'] != outcome['native_export']['sha256']):
        raise ValueError('library snapshot and correlation are not bound to one successful native run')
    for resource in correlation['resources']:
        if sha256((native_directory / resource['file']).read_bytes()) != resource['sha256']:
            raise ValueError('compiled resource differs from independent decode input')
    snapshot_raw = (native_directory / 'frontend-snapshot.json').read_bytes()
    pous = snapshot_library_pous(json.loads(snapshot_raw.decode('utf-8-sig')))
    result = dict(input_sha256=request['input_sha256'], export_sha256=correlation['export_sha256'],
        snapshot_sha256=sha256(snapshot_raw), correlation_sha256=sha256(correlation_raw),
        source_origin='native frontend library ABI; not contained in the project GXW',
        inventory=dict(Counter(p['library'] for p in pous)), regions=[], complete_semantic_proof=False)
    for expansion in correlation['expansions']:
        if not expansion['handling'].startswith('compiled-library-region-'):
            continue
        library, _, name, instance, _ = expansion['stored_names']
        matches = [p for p in pous if p['library'] == library and p['name'] == name]
        region = dict(element_index=expansion['element_index'], instance=instance,
            handling='unresolved-native-library-source', occurrences=[])
        result['regions'].append(region)
        if len(matches) != 1:
            continue
        source = matches[0]
        region['native_library_pou'] = source
        try:
            framed = observed_library_il(base64.b64decode(source['program_raw_base64']))
        except ValueError as exc:
            region['diagnostic'] = str(exc)
            continue
        region['framed_source'] = framed
        if framed['handling'] == 'native-library-placeholder':
            region['handling'] = framed['handling']
            continue
        rows = expansion['debug_rows']
        if expansion['fields'][0] < 0 or any(r[0] < 0 or r[0] >= len(framed['lines']) or r[3] != 1 or r[4] != 8 or r[5] != 0 for r in rows):
            region['diagnostic'] = 'outside observed single-network IL debug rows'
            continue
        region['handling'] = 'native-library-IL-source-point-correlation'
        for row in rows:
            start, end = [expansion['linked_interval'][0] + row[i] for i in (1, 2)]
            groups = [g for g in expansion['compiled_records'] if start <= g['stored_step'] <= end]
            region['occurrences'].append(dict(source_line=framed['lines'][row[0]], debug_row=row,
                compiled_records=groups, independently_decoded_records=[
                    correlation['decoded_fragments'][g['decode_request']] for g in groups if 'decode_request' in g]))
    result['summary'] = dict(regions=len(result['regions']), correlated_regions=sum(
        r['handling'] == 'native-library-IL-source-point-correlation' for r in result['regions']),
        source_points=sum(len(r['occurrences']) for r in result['regions']),
        placeholders=sum(r['handling'] == 'native-library-placeholder' for r in result['regions']))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_directory', type=Path)
    parser.add_argument('correlation', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.native_directory, args.correlation, args.output)['summary']))

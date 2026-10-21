"""Ordered native instruction sources in the existing PLC IR.

The instruction sequence is authoritative. A graphical preview may be absent;
outputs do not restart or duplicate the accumulator that feeds later contacts.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping


def is_ordered_ladder(value):
    return isinstance(value, Mapping) and any(
        isinstance(rung, Mapping) and 'ordered_instructions' in rung
        for rung in value.get('rungs', []) or [])


def source_record_kind(row):
    return row.get('kind', 'instruction')


def indexed_literal_register(value):
    """Return the index dependency of one stored integer operand.

    This identifies source syntax only. It does not establish the indexed
    value, CPU admission, or the instruction's execution semantics.
    """
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:K[+-]?[0-9]+|H[0-9A-F]+)([ZV])([0-9]+)', value.strip(), re.I)
    return match[1].upper() + str(int(match[2])) if match else None


def buffer_memory_reference(value):
    """Identify a stored U/G reference and its index dependency, not its device.

    Unit configuration, buffer allocation and runtime access remain unverified.
    Keeping the whole reference avoids confusing a module buffer with PLC G or
    ordinary data memory. Both Q source index positions retain their identity.
    """
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(U[0-9A-F]+)([ZV][0-9]+)?\\(G[0-9A-F]+)([ZV][0-9]+)?',
                         value.strip().upper())
    if match is None or match[2] and match[4]:
        return None
    return {'base': match[1] + '\\' + match[3], 'index': match[2] or match[4]}


def project_ordered_graph(rung):
    """Display the stored accumulator flow without lowering it back to PLC code.

    A frozen result is a drawing junction, not a reread of its contacts. Outputs
    tap that result; later contacts and MPS/MRD/MPP retain their source order.
    This projection never changes the authoritative ordered instructions.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory
    events, stack, saved = [], [], []
    unresolved = False

    def combine(kind, left, right):
        if left['kind'] == kind:
            left['children'].append(right)
            return left
        return {'kind': kind, 'children': [left, right]}

    def current():
        return stack[-1] if stack else {'kind': 'unknown' if unresolved else 'true'}

    def freeze():
        if not stack:
            stack.append(current())
        # Older LD blocks also contain already-read values. Freeze them before
        # an output can change a device that a later ANB/ORB will consume.
        for position, value in enumerate(stack):
            if value['kind'] != 'reference':
                anchor = len(events)
                events.append({'kind': 'expression', 'anchor': anchor, 'expression': value})
                stack[position] = {'kind': 'reference', 'anchor': anchor}
        return stack[-1]['anchor']

    for index, row in enumerate(rung.get('ordered_instructions', [])):
        origin = {'source_index': index, 'record_index': row.get('record_index', index),
                  'op': row.get('op', ''), 'args': list(row.get('args', []))}
        kind, op, args = source_record_kind(row), origin['op'], origin['args']
        if kind != 'instruction':
            if kind in {'statement', 'note'}:
                events.append({**origin, 'kind': 'annotation', 'text': row.get('text') or ''})
            else:
                if stack:
                    freeze()
                events.append({**origin, 'kind': 'barrier', 'text': row.get('text') or row.get('reason') or kind})
                stack.clear()
                saved.clear()
                unresolved = True
            continue
        if op in {'ANB', 'ORB'} and len(stack) >= 2:
            right, left = stack.pop(), stack.pop()
            stack.append({**origin, **combine('and' if op == 'ANB' else 'or', left, right)})
        elif op in {'MPS', 'MRD', 'MPP'}:
            if op == 'MPS':
                anchor = freeze()
                saved.append(anchor)
            elif saved:
                anchor = saved[-1] if op == 'MRD' else saved.pop()
                stack[-1:] = [{'kind': 'reference', 'anchor': anchor}]
            else:
                events.append({**origin, 'kind': 'barrier', 'text': 'Unmatched ' + op})
                stack.clear()
                unresolved = True
                continue
            events.append({**origin, 'kind': 'stack', 'anchor': anchor})
        elif op in {'INV', 'MEP', 'MEF'}:
            stack[-1:] = [{**origin, 'kind': 'invert' if op == 'INV' else 'edge', 'child': current()}]
        elif (DEFAULT_INSTRUCTION_REGISTRY.category_of(op) == InstructionCategory.CONDITION
              and op.startswith(('LD', 'AND', 'OR', 'ANI', 'ORI'))):
            terminal = {'kind': 'contact', **origin}
            if op.startswith('LD'):
                stack.append(terminal)
            elif op.startswith(('AND', 'ANI')):
                stack[-1:] = [combine('and', current(), terminal)]
            else:
                stack[-1:] = [combine('or', current(), terminal)]
        elif op in {'ANB', 'ORB'}:
            events.append({**origin, 'kind': 'barrier', 'text': 'Unmatched ' + op})
            stack.clear()
            unresolved = True
        elif op == 'END':
            events.append({**origin, 'kind': 'end'})
        else:
            events.append({**origin, 'kind': 'output', 'anchor': freeze()})
    if stack and current()['kind'] != 'reference':
        freeze()
    return events


def source_analysis_equivalent(before, after):
    """Accept older source findings that omitted display-only finding fields.

    Finding identities, messages, source coordinates, counts and all other
    analysis remain exact. The saved IR can retain its original version binding.
    """
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return False
    old_rows, new_rows = before.get('findings'), after.get('findings')
    if not isinstance(old_rows, list) or not isinstance(new_rows, list) or len(old_rows) != len(new_rows):
        return False
    expanded = copy.deepcopy(dict(before))
    metadata = {'suggestion', 'addresses', 'network_refs', 'json_paths', 'evidence', 'confidence', 'fixable'}
    codes = {'SOURCE_RECORD_SEMANTICS_UNVERIFIED', 'SOURCE_DEVICE_PROFILE_INCOMPLETE'}
    for index, (old, new) in enumerate(zip(old_rows, new_rows)):
        if isinstance(old, Mapping) and isinstance(new, Mapping) and old.get('code') in codes:
            missing = new.keys() - old.keys()
            if missing <= metadata:
                expanded['findings'][index] = {**{key: new[key] for key in missing}, **old}
    return expanded == after


def ordered_source_format(rungs):
    return ('instruction_sequence_v2' if any('kind' in row for rung in rungs
            for row in rung.get('ordered_instructions', [])) else 'instruction_sequence_v1')


def validate_ordered_rung(rung, *, plc_model, require_catalogued=True, syntax_only=False):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.device_profiles import SOURCE_DEVICE_PROFILES, DEVICE_LIMITS
    from plc.validation import (DEVICE_ADDRESS_RE, INDEXED_DEVICE_ADDRESS_RE,
        _validate_operand_token, _validate_instruction_boundaries,
        _validate_source_operand_types, _validate_app_instruction_write_targets,
        _validate_device_address, _validate_indexed_device_address)
    if (not isinstance(rung, Mapping) or type(rung.get('rung_id')) is not int
            or set(rung) - {'rung_id', 'debug_note', 'ordered_instructions'}):
        raise ValueError('ordered source requires rung_id and ordered_instructions')
    instructions = rung.get('ordered_instructions')
    if not isinstance(instructions, list) or not instructions:
        raise ValueError('ordered instruction sequence must not be empty')
    logic, saved = 0, 0
    for index, instruction in enumerate(instructions):
        path = f'ordered_instructions[{index}]'
        fields = {'op', 'args', 'kind', 'record_index', 'source_offset', 'raw_hex', 'step', 'text', 'reason'}
        if (not isinstance(instruction, Mapping) or set(instruction) - fields
                or not isinstance(instruction.get('op'), str)
                or not isinstance(instruction.get('args'), list)):
            raise ValueError(path + ' requires op and args')
        op, args = instruction['op'], instruction['args']
        kind = source_record_kind(instruction)
        if kind not in {'instruction', 'opaque', 'label', 'statement', 'note'}:
            raise ValueError(path + ' has an unknown source record kind')
        if 'kind' in instruction:
            if (type(instruction.get('record_index')) is not int or instruction['record_index'] < 0
                    or type(instruction.get('source_offset')) is not int or instruction['source_offset'] < 0
                    or not isinstance(instruction.get('raw_hex'), str)
                    or not re.fullmatch(r'(?:[0-9a-f]{2})+', instruction['raw_hex'])):
                raise ValueError(path + ' requires its original source record binding')
        if kind != 'instruction':
            if op or args:
                raise ValueError(path + ': non-instruction source records have no opcode or operands')
            if kind in {'opaque', 'label'}:
                # An unresolved record or control-flow label may change the
                # incoming stacks. Do not diagnose a guessed stack state.
                logic, saved = None, None
            continue
        if not op or op != op.strip().upper() or any(not isinstance(arg, str) for arg in args):
            raise ValueError(path + ' has invalid opcode or operands')
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu=plc_model)
        if spec is None:
            if require_catalogued:
                raise ValueError(path + ': instruction is not catalogued for ' + plc_model + ': ' + op)
        elif not spec.accepts_arity(len(args)):
            raise ValueError(path + ': incorrect operand count for ' + op)
        partial = plc_model in SOURCE_DEVICE_PROFILES
        uncovered = [operand for operand in args if DEVICE_ADDRESS_RE.fullmatch(operand)
                     and DEVICE_ADDRESS_RE.fullmatch(operand).group(1).upper() not in DEVICE_LIMITS.get(plc_model, {})]
        for operand in args:
            if not buffer_memory_reference(operand):
                _validate_operand_token(operand, path)
            if not syntax_only:
                if INDEXED_DEVICE_ADDRESS_RE.fullmatch(operand):
                    _validate_indexed_device_address(operand, path, plc_model)
                elif DEVICE_ADDRESS_RE.fullmatch(operand) and not (partial and operand in uncovered):
                    _validate_device_address(operand, path, plc_model)
        if not syntax_only and spec is not None and not (partial and uncovered):
            _validate_instruction_boundaries(spec, args, path, plc_model, opcode=op)
            _validate_source_operand_types(spec, args, path, plc_model, allow_indexed_literals=True,
                                           allow_buffer_references=True)
            _validate_app_instruction_write_targets(op, args, path, plc_model)
        if logic is None or saved is None:
            continue
        if op.startswith('LD'):
            logic += 1
        elif op in {'ANB', 'ORB'}:
            if logic < 2:
                raise ValueError(path + ': logic stack underflow')
            logic -= 1
        elif op == 'MPS':
            if not logic:
                raise ValueError(path + ': MPS has no condition')
            saved += 1
        elif op in {'MRD', 'MPP'}:
            if not saved:
                raise ValueError(path + ': shared stack underflow')
            if op == 'MPP':
                saved -= 1
    if saved or logic is not None and logic > 1:
        raise ValueError('ordered network has an unbalanced logic or shared stack')
    return rung


def build_instruction_ir(networks, *, plc_model, program_name='MAIN', device_comments=None, revision=1):
    """Reuse the existing semantic/access/timing pipeline without graph lowering."""
    from plc.ir import build_plc_ir
    rungs = []
    typed = any(row.get('kind') in {'opaque', 'label'} for network in networks
                for row in network.get('raw_instructions', network.get('instructions', [])))
    for index, network in enumerate(networks, 1):
        instructions = network.get('raw_instructions', network.get('instructions', []))
        if typed:
            rows = [{**{key: copy.deepcopy(row[key]) for key in (
                        'record_index', 'source_offset', 'raw_hex', 'step', 'text', 'reason') if key in row},
                     'kind': row.get('kind', 'instruction'), 'op': row.get('op', ''),
                     'args': copy.deepcopy(row.get('args', []))} for row in instructions]
        else:
            rows = [{'op': row['op'], 'args': copy.deepcopy(row.get('args', []))}
                    for row in instructions]
        if rows:
            rungs.append({'rung_id': index, 'debug_note': str(network.get('comment') or ''),
                          'ordered_instructions': rows})
    return build_plc_ir({'device_comments': dict(device_comments or {}), 'rungs': rungs},
        plc_model=plc_model, program_name=program_name, revision=revision,
        _canonicalize_devices=False)


def source_coverage_findings(networks, *, plc_model):
    """A partial address profile cannot certify every imported source access."""
    import re
    from plc.device_profiles import SOURCE_DEVICE_PROFILES, DEVICE_LIMITS
    covered = DEVICE_LIMITS.get(plc_model, {})
    result = []
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import fx_out_preset_error
    from plc.static_analysis import _finding
    for network in networks:
        if 'ordered_instructions' not in network.get('ladder', {}):
            continue
        for row in network['ladder']['ordered_instructions']:
            kind = source_record_kind(row)
            error = fx_out_preset_error(row.get('op'), row.get('args', [])) if str(plc_model).upper().startswith('FX') else None
            if kind == 'instruction' and error:
                result.append({**_finding('SOURCE_REQUIRED_PRESET', 'error', error,
                    'Complete the timer/counter preset before checking or compiling this source.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_syntax', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': list(row['args'])})
            if kind in {'opaque', 'label'} or (kind == 'instruction' and
                    DEFAULT_INSTRUCTION_REGISTRY.resolve(row['op'], cpu=plc_model) is None):
                result.append({**_finding('SOURCE_RECORD_SEMANTICS_UNVERIFIED', 'unverified',
                    'Original source record is preserved; execution semantics are unverified: '
                    + (row.get('op') or kind),
                    'Check this source record independently before claiming execution coverage.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified',
                    'record_index': row.get('record_index'), 'kind': kind})
            indexed = [operand for operand in row.get('args', []) if indexed_literal_register(operand)]
            if kind == 'instruction' and indexed:
                result.append({**_finding('SOURCE_INDEXED_LITERAL_SEMANTICS_UNVERIFIED', 'unverified',
                    'Indexed integer source is preserved; its value and execution semantics are unverified: '
                    + ', '.join(indexed),
                    'Verify indexed integer execution for the saved CPU independently.',
                    addresses=sorted({indexed_literal_register(operand) for operand in indexed}),
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': indexed})
            buffers = [operand for operand in row.get('args', []) if buffer_memory_reference(operand)]
            if kind == 'instruction' and buffers:
                result.append({**_finding('SOURCE_BUFFER_MEMORY_SEMANTICS_UNVERIFIED', 'unverified',
                    'Stored module buffer references are preserved; module allocation and execution are unverified: '
                    + ', '.join(buffers),
                    'Bind and verify the saved CPU, module configuration and buffer access independently.',
                    addresses=sorted({value for operand in buffers
                        for value in buffer_memory_reference(operand).values() if value}),
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': buffers})
        if plc_model not in SOURCE_DEVICE_PROFILES:
            continue
        devices = sorted({address for address in [*network.get('reads', []), *network.get('writes', [])]
                          if re.match(r'^[A-Z]+', address).group() not in covered})
        if devices:
            result.append({**_finding('SOURCE_DEVICE_PROFILE_INCOMPLETE', 'unverified',
                'Stored source is preserved; address rules are not established for '
                + plc_model + ': ' + ', '.join(devices),
                'Check address applicability for the stored CPU independently.',
                addresses=devices, rung_ids=[network['rung_id']], networks=[network['id']]),
                'category': 'device_applicability_unverified', 'devices': devices})
    return result

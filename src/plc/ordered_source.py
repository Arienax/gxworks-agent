# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

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


def source_label_reference(value):
    """Q/L stored label spelling; its declaration and type need separate binding."""
    return isinstance(value, str) and bool(re.fullmatch(
        r"'[^\W\d]\w*(?:\[[^\[\]\x00\r\n]+\]|\.[^\W\d]\w*)*", value))


def source_grouped_reference(value):
    """A digit-specified bit region as one word operand, without runtime admission."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'K([1-8])((?:SM|SB|[XYMLBSF])[0-9A-F]+)([ZV][0-9]+)?', value, re.I)
    return {'digits': int(match[1]), 'base': match[2].upper(),
            'index': match[3].upper() if match[3] else None} if match else None


def source_extended_reference(value):
    """Hardware-qualified or indirect source access, never an ordinary PLC device."""
    if not isinstance(value, str):
        return None
    from plc.device_identity import REFERENCE_DEVICE_PATTERN
    text = value.strip().upper()
    indirect = text.startswith('@')
    text = text.removeprefix('@')
    prefix, indexes = '', []
    if '\\' in text:
        if text.count('\\') != 1:
            return None
        qualifier, text = text.split('\\')
        match = re.fullmatch(r'(U[0-9A-F]+|J[0-9]+|BL[0-9]+|\.[0-9A-F]+)((?:ZZ|Z|V)[0-9]+)?', qualifier)
        if not match:
            return None
        prefix = match[1] + '\\'
        if match[2]:
            indexes.append(match[2])
    match = re.fullmatch(r'(K[1-8])?(' + REFERENCE_DEVICE_PATTERN + r'|G[0-9]+)((?:ZZ|Z|V)[0-9]+)?', text)
    if not match or not (indirect or prefix or match[3] and match[3].startswith('ZZ')):
        return None
    if match[3]:
        indexes.append(match[3])
    pointer = prefix + (match[1] or '') + match[2]
    return {'base': ('@' if indirect else '') + pointer, 'indexes': tuple(indexes),
            'indirect': indirect, 'pointer': pointer, 'device': match[2], 'group': match[1]}


def native_real_literal(value):
    """Match Mitsubishi E-mantissa[+/-exponent] syntax, without granting a type.

    The verified operand contract decides whether a real constant is allowed;
    numeric range, native input rounding and storage remain their own checks.
    """
    if not isinstance(value, str):
        return None
    return re.fullmatch(r'E([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([+-][0-9]+)?',
                        value.strip(), re.I)


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
    # Unit and bit selectors are hexadecimal; the native G token is decimal.
    match = re.fullmatch(r'(U[0-9A-F]+)([ZV][0-9]+)?\\(G[0-9]+)([ZV][0-9]+)?(\.[0-9A-F])?',
                         value.strip().upper())
    if match is None or match[2] and match[4]:
        return None
    return {'base': match[1] + '\\' + match[3] + (match[5] or ''), 'index': match[2] or match[4]}


def split_ordered_circuits(records):
    """Group complete circuits while preserving their original statement rows.

    The joined note is for display. Leading statements remain separate so a
    source-bound writer does not duplicate them when re-emitting instructions.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory, condition_connector

    def _is_root_input(op):
        return condition_connector(op) == 'LD'

    def _is_condition(op):
        return (condition_connector(op) or op in {'ANB', 'ORB', 'INV', 'MEP', 'MEF'}
                or DEFAULT_INSTRUCTION_REGISTRY.category_of(op) == InstructionCategory.CONDITION)

    _BRANCH_SYSTEM_OPS = {'MPS', 'MRD', 'MPP'}

    networks = []
    current = []
    pending_statements = []
    pending_records = []
    current_statements = []
    leading_statements = []
    has_action = False
    logic_depth = 0
    mps_depth = 0
    mps_seen = False
    after_mpp = False
    after_mpp_action = False

    def finish() -> None:
        nonlocal current, current_statements, leading_statements, has_action, mps_depth, logic_depth
        nonlocal mps_seen, after_mpp, after_mpp_action
        if current:
            note = "\n".join(item for item in current_statements if item).strip()
            networks.append((note, current, list(leading_statements)))
        current = []
        current_statements = []
        leading_statements = []
        has_action = False
        logic_depth = 0
        mps_depth = 0
        mps_seen = False
        after_mpp = False
        after_mpp_action = False

    for record in records:
        if record.get('kind') in {'opaque', 'label'}:
            current.append(record)
            continue
        if not record.get('op', ''):
            text = record.get('statement') or record.get('text') or ''
            if text:
                (current_statements if current else pending_statements).append(text)
            if record.get('kind') in {'statement', 'note'}:
                (current if current else pending_records).append(record)
            continue

        if record.get('op', '') == "END":
            finish()
            continue

        if _is_root_input(record.get('op', '')) and current and has_action and mps_depth == 0 and logic_depth <= 1:
            if not mps_seen or (after_mpp and after_mpp_action):
                finish()

        if not current:
            current_statements = list(pending_statements)
            pending_statements.clear()
            leading_statements = list(current_statements)
            current.extend(pending_records)
            pending_records.clear()
        if record.get('statement', ''):
            current_statements.append(record.get('statement', ''))
        current.append(record)

        if _is_root_input(record.get('op', '')):
            logic_depth += 1
        elif record.get('op', '') in {'ANB', 'ORB'}:
            logic_depth -= 1

        if record.get('op', '') == "MPS":
            mps_depth += 1
            mps_seen = True
        elif record.get('op', '') == "MPP":
            mps_depth = max(0, mps_depth - 1)
            after_mpp = True
        elif not _is_condition(record.get('op', '')) and record.get('op', '') not in _BRANCH_SYSTEM_OPS:
            has_action = True
            if after_mpp:
                after_mpp_action = True

    finish()
    if pending_records or pending_statements:
        if networks:
            note, rows, leading = networks[-1]
            networks[-1] = ('\n'.join([note, *pending_statements]).strip(), [*rows, *pending_records], leading)
        elif pending_records:
            networks.append(('\n'.join(pending_statements), list(pending_records), list(pending_statements)))
    circuits = []
    def needs_incoming(rows):
        logic, saved = 0, 0
        for row in rows:
            if row.get('kind') in {'opaque', 'label'}:
                return False
            if _is_root_input(row.get('op', '')):
                logic += 1
            elif row.get('op', '') in {'ANB', 'ORB'}:
                if logic < 2:
                    return True
                logic -= 1
            elif row.get('op', '') == 'MPS':
                if not logic:
                    return True
                saved += 1
            elif row.get('op', '') in {'MRD', 'MPP'}:
                if not saved:
                    return True
                if row.get('op', '') == 'MPP':
                    saved -= 1
            elif row.get('op') and not logic and (condition_connector(row['op']) in {'AND', 'OR'} or row['op'] in {'OUT', 'SET', 'RST', 'PLS', 'PLF', 'INV', 'MEP', 'MEF'}):
                return True
        return False
    for note, rows, leading in networks:
        # An output taps the accumulator; it does not discard it. A later
        # LD/ANB/ORB may still consume that earlier block. Merge only when
        # the following display partition actually needs the incoming stack.
        while circuits and needs_incoming(rows):
            previous_note, previous_rows, previous_leading = circuits.pop()
            note = '\n'.join(part for part in (previous_note, note) if part)
            rows = [*previous_rows, *rows]
            leading = previous_leading
        circuits.append((note, rows, leading))
    return circuits


def project_ordered_graph(rung):
    """Display the stored accumulator flow without lowering it back to PLC code.

    A frozen result is a drawing junction, not a reread of its contacts. Outputs
    tap that result; later contacts and MPS/MRD/MPP retain their source order.
    This projection never changes the authoritative ordered instructions.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory, condition_connector
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
        elif condition_connector(op):
            terminal = {'kind': 'contact', **origin}
            if condition_connector(op) == 'LD':
                stack.append(terminal)
            elif condition_connector(op) == 'AND':
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


def same_ordered_source_inputs(before, after):
    """Compare authoritative inputs when restoring an older derived IR view."""
    from plc.ir import IR_SCHEMA_VERSION, ir_to_ladder
    if any(not isinstance(value, Mapping) or value.get('schema_version') != IR_SCHEMA_VERSION
           or (value.get('source') or {}).get('format') not in {
               'instruction_sequence_v1', 'instruction_sequence_v2'} for value in (before, after)):
        return False
    if any(before.get(key) != after.get(key) for key in ('plc', 'program_name', 'revision', 'io_map')):
        return False
    if ((before.get('analysis') or {}).get('config') != (after.get('analysis') or {}).get('config')
            or (before.get('logic') or {}).get('requirements') != (after.get('logic') or {}).get('requirements')):
        return False
    return ir_to_ladder(before) == ir_to_ladder(after)


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


def validate_ordered_rung(rung, *, plc_model, require_catalogued=True, syntax_only=False, check_stack=True):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, source_instruction_arities
    from plc.device_profiles import SOURCE_DEVICE_PROFILES, DEVICE_LIMITS
    from plc.validation import (DEVICE_ADDRESS_RE, INDEXED_DEVICE_ADDRESS_RE,
        _validate_operand_token, _validate_instruction_boundaries,
        _validate_source_operand_types, _validate_app_instruction_write_targets,
        _validate_device_address, _validate_indexed_device_address, _validate_reviewed_program_pointer,
        _validate_program_pointer_number_domain, _validate_reviewed_remote_operand)
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
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu=plc_model, operand_count=len(args))
        if spec is None:
            if require_catalogued:
                raise ValueError(path + ': instruction is not catalogued for ' + plc_model + ': ' + op)
        if not require_catalogued:
            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve_source_form(op, len(args), cpu=plc_model)
        if require_catalogued and spec is not None and not spec.supports_cpu(plc_model):
            raise ValueError(path + ': instruction is not supported by ' + plc_model + ': ' + op)
        if spec is not None and not spec.accepts_arity(len(args)):
            raise ValueError(path + ': incorrect operand count for ' + op)
        source_arities = source_instruction_arities(op, plc_model)
        if source_arities and len(args) not in source_arities:
            raise ValueError(path + ': incorrect stored operand count for ' + op)
        partial = (
            plc_model in SOURCE_DEVICE_PROFILES
            and not SOURCE_DEVICE_PROFILES[plc_model].get('address_families_complete')
        )
        uncovered = set()
        for operand in args:
            indexed_match = INDEXED_DEVICE_ADDRESS_RE.fullmatch(operand)
            base_match = DEVICE_ADDRESS_RE.fullmatch(indexed_match.group('base') if indexed_match else operand)
            if base_match and base_match.group(1).upper() not in DEVICE_LIMITS.get(plc_model, {}):
                uncovered.add(operand)
        for operand_index, operand in enumerate(args):
            from plc.device_identity import INDEXED_BASE_PATTERN, external_operand_reference
            from plc.validation import _fail
            external = (external_operand_reference(operand, plc_model)
                        if not str(plc_model).upper().startswith('FX') else None)
            if not (buffer_memory_reference(operand) or external or source_label_reference(operand) or source_extended_reference(operand)):
                _validate_operand_token(operand, path)
                if re.fullmatch(
                    rf'(?:{INDEXED_BASE_PATTERN}[VZ]\d*\.[0-9A-F]+|(?:SD|SW|ZR|[DWR])[0-9][0-9A-F]*\.[0-9A-F]+[VZ]\d*)',
                    operand.strip(),
                    re.I,
                ):
                    _fail(path, f'bit-of-word operand {operand!r} cannot be combined with index modification')
            if not syntax_only:
                _validate_program_pointer_number_domain(spec, args, operand_index, path, plc_model)
                if _validate_reviewed_program_pointer(spec, args, operand_index, path, plc_model):
                    continue
                if _validate_reviewed_remote_operand(spec, args, operand_index, path, plc_model):
                    continue
                if INDEXED_DEVICE_ADDRESS_RE.fullmatch(operand) and not (partial and operand in uncovered):
                    _validate_indexed_device_address(operand, path, plc_model)
                elif DEVICE_ADDRESS_RE.fullmatch(operand) and not (partial and operand in uncovered):
                    _validate_device_address(operand, path, plc_model)
        if not syntax_only and spec is not None and not (partial and uncovered):
            _validate_instruction_boundaries(spec, args, path, plc_model, opcode=op)
            _validate_source_operand_types(spec, args, path, plc_model, allow_indexed_literals=True,
                                           allow_buffer_references=True)
            _validate_app_instruction_write_targets(op, args, path, plc_model, allow_unverified=True, source_spec=spec, allow_buffer_references=True)
        if not check_stack or logic is None or saved is None:
            continue
        if op in {'CHKST', 'CHK', 'CHKCIR', 'CHKEND'} and _chk_context_rule(op, plc_model) is not None:
            # CHK's preceding contacts describe checks, not an ordinary action
            # gate. Each documented diagnostic boundary ends that syntax group.
            logic = 0
        elif op.startswith('LD'):
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
    if check_stack and (saved or logic is not None and logic > 1):
        raise ValueError('ordered network has an unbalanced logic or shared stack')
    return rung


def ordered_circuit_rows(rungs):
    """Partition source without changing the stored network IDs or record order."""
    rows = [{**row, 'statement': row.get('text', '') if row.get('kind') == 'statement' else '',
             '_record': row, '_rung_index': index, '_instruction_index': position}
            for index, rung in enumerate(rungs) for position, row in enumerate(rung.get('ordered_instructions', []))]
    return split_ordered_circuits(rows)


def ordered_display_rungs(rungs):
    """Join legacy display continuations while retaining their navigation identities."""
    joins = set()
    for _note, rows, _leading in ordered_circuit_rows(rungs):
        owners = sorted({row['_rung_index'] for row in rows})
        if owners:
            joins.update(range(owners[0] + 1, owners[-1] + 1))
    displayed = []
    for index, rung in enumerate(rungs):
        if index in joins and displayed and 'ordered_instructions' in rung:
            current = displayed[-1]
            current['ordered_instructions'].extend(copy.deepcopy(rung['ordered_instructions']))
            current['debug_note'] = '\n'.join(part for part in (current.get('debug_note'), rung.get('debug_note')) if part)
            current['_source_rung_ids'].append(rung.get('rung_id', index + 1))
        else:
            displayed.append({**copy.deepcopy(rung), '_source_rung_ids': [rung.get('rung_id', index + 1)]})
    return displayed


def validate_ordered_circuits(rungs, *, plc_model):
    for index, (_note, rows, _leading) in enumerate(ordered_circuit_rows(rungs)):
        validate_ordered_rung({'rung_id': index, 'ordered_instructions': [row['_record'] for row in rows]},
                              plc_model=plc_model, require_catalogued=False, syntax_only=True)


def build_instruction_ir(networks, *, plc_model, program_name='MAIN', device_comments=None, revision=1):
    """Reuse the existing semantic/access/timing pipeline without graph lowering."""
    from plc.ir import build_plc_ir
    rungs = []
    typed = any(row.get('kind', 'instruction') != 'instruction' or 'raw_hex' in row for network in networks
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
    from plc.instruction_constraints import fx_out_preset_error, check_instruction_constraints
    from plc.static_analysis import _finding
    for network in networks:
        for region in network.get('access_regions', ()):
            if region.get('address_space') != 'direct_access_output':
                continue
            result.append({**_finding('SOURCE_DIRECT_ACCESS_OUTPUT_UNVERIFIED', 'unverified',
                'Direct access output identity is preserved; CPU output range and installed module configuration are unverified.',
                'Bind and verify the CPU output range and target module configuration independently.',
                addresses=[region['head']], rung_ids=[network['rung_id']], networks=[network['id']]),
                'category': 'source_semantics_unverified', 'requires': list(region.get('requires', ()))})
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
            if kind == 'instruction' and row.get('op') == 'RST' and str(plc_model).upper().startswith('Q'):
                resets = [value for value in row.get('args', []) if re.fullmatch(r'X[0-9A-F]+', value, re.I)]
                if resets:
                    result.append({**_finding('SOURCE_INPUT_RESET_SEMANTICS_UNVERIFIED', 'unverified',
                        'Stored input-image reset is preserved; physical input and scan effects are unverified.',
                        'Verify the CPU, input image refresh and reset instruction in the actual project.',
                        addresses=resets, rung_ids=[network['rung_id']], networks=[network['id']]),
                        'category': 'source_semantics_unverified'})
            unresolved = (kind == 'instruction' and
                          DEFAULT_INSTRUCTION_REGISTRY.resolve(row['op'], cpu=plc_model) is None)
            unavailable = []
            if unresolved:
                report = check_instruction_constraints(row['op'], {}, plc_model=plc_model,
                    language='ladder', context={'project_mode': 'native_ladder_instruction'})
                unavailable = [finding for finding in report.get('findings', [])
                    if finding.get('dimension') == 'constraints.cpu_availability'
                    and finding.get('status') == 'failed' and finding.get('severity') == 'error']
            if unavailable:
                result.append({**_finding('SOURCE_INSTRUCTION_UNAVAILABLE', 'error',
                    row['op'] + ' is unavailable for ' + plc_model + ' in its reviewed source.',
                    'Use an instruction supported by the saved CPU.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_applicability', 'opcode': row['op'],
                    'record_index': row.get('record_index'), 'source_offset': row.get('source_offset'),
                    'constraint_findings': unavailable, 'kind': kind})
            elif kind in {'opaque', 'label'} or unresolved:
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
            labels = [operand for operand in row.get('args', []) if source_label_reference(operand)]
            if kind == 'instruction' and labels:
                result.append({**_finding('SOURCE_LABEL_BINDING_UNVERIFIED', 'unverified',
                    'Stored label references require their saved declarations and instance context: ' + ', '.join(labels),
                    'Bind the labels in the complete saved project before validating their types or execution.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': labels})
            grouped = [operand for operand in row.get('args', []) if source_grouped_reference(operand)]
            if kind == 'instruction' and grouped:
                result.append({**_finding('SOURCE_GROUPED_DEVICE_SEMANTICS_UNVERIFIED', 'unverified',
                    'Stored digit-specified device regions are preserved: ' + ', '.join(grouped),
                    'Verify the complete region and its execution for the saved CPU.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': grouped})
            extended = [operand for operand in row.get('args', [])
                        if source_extended_reference(operand) and not buffer_memory_reference(operand)]
            if kind == 'instruction' and extended:
                result.append({**_finding('SOURCE_QUALIFIED_DEVICE_SEMANTICS_UNVERIFIED', 'unverified',
                    'Stored indirect or qualified device references need their saved access context: ' + ', '.join(extended),
                    'Verify pointer, station, module and index configuration for the saved CPU.',
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'source_semantics_unverified', 'record_index': row.get('record_index'),
                    'kind': kind, 'operands': extended})
        if plc_model not in SOURCE_DEVICE_PROFILES:
            continue
        from plc.validation import parse_device_address, _source_access
        for address in network.get('writes', []):
            parsed = parse_device_address(address, plc_model)
            regions = SOURCE_DEVICE_PROFILES[plc_model].get('special_regions', {}).get(parsed[0], []) if parsed else []
            if parsed and any(lo <= parsed[1] <= hi for lo, hi in regions) and not _source_access(address, plc_model):
                result.append({**_finding('SOURCE_DEVICE_ACCESS_UNVERIFIED', 'unverified',
                    'Write applicability for the stored special device is not established: ' + address,
                    'Verify the saved CPU and instruction-specific access conditions.', addresses=[address],
                    rung_ids=[network['rung_id']], networks=[network['id']]),
                    'category': 'device_applicability_unverified'})
        devices = sorted({address for address in [*network.get('reads', []), *network.get('writes', [])]
                          if (match := re.match(r'^[A-Z]+', address)) and match.group() not in covered})
        if devices:
            result.append({**_finding('SOURCE_DEVICE_PROFILE_INCOMPLETE', 'unverified',
                'Stored source is preserved; address rules are not established for '
                + plc_model + ': ' + ', '.join(devices),
                'Check address applicability for the stored CPU independently.',
                addresses=devices, rung_ids=[network['rung_id']], networks=[network['id']]),
                'category': 'device_applicability_unverified', 'devices': devices})
    result.extend(break_context_findings(networks, plc_model=plc_model))
    result.extend(chk_context_findings(networks, plc_model=plc_model))
    result.extend(cj_context_findings(networks, plc_model=plc_model))
    return result


def _saved_ordered_context(networks, rows, plc_model):
    """The existing saved-program record identity, shared by context checks."""
    records = [row for _, row in rows]
    complete = all('ordered_instructions' in net.get('ladder', {}) for net in networks)
    complete = complete and [row.get('record_index') for row in records] == list(range(len(records)))
    binding_reason = 'complete_saved_program_source_required'
    if complete:
        # The text fields are not an independent label definition: bind them
        # to the same original token records already used by the GXW reader.
        try:
            from gxw.token_listing import decode_token_program, TokenInstruction, TokenLabel, TokenText
            from gxw.token_pou import parse_token_fragment
            from gxw.token_profiles import source_token_profile
            chunks = [bytes.fromhex(row['raw_hex']) for row in records]
            offsets = [row['source_offset'] for row in records]
            if (not chunks or any(not chunk for chunk in chunks)
                    or any(type(offset) is not int for offset in offsets)
                    or any(offsets[i + 1] != offsets[i] + len(chunks[i]) for i in range(len(chunks) - 1))):
                raise ValueError('incomplete source record binding')
            raw = b''.join(chunks)
            listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                           profile=source_token_profile(plc_model))
            if listing.gaps or len(listing.records) != len(records):
                raise ValueError('unresolved source records')
            for row, decoded in zip(records, listing.records):
                kind = source_record_kind(row)
                if isinstance(decoded, TokenInstruction):
                    matches = kind == 'instruction' and row.get('op') == decoded.mnemonic and row.get('args') == list(decoded.args)
                elif isinstance(decoded, TokenLabel):
                    matches = kind == 'label' and row.get('text') == decoded.text
                else:
                    matches = isinstance(decoded, TokenText) and kind == decoded.role
                if not matches:
                    raise ValueError('source record identity differs from its binding')
            instructions = [r for r in records if source_record_kind(r) == 'instruction']
            semantic_records = [r for r in records if source_record_kind(r) in {'instruction', 'label'}]
            complete = bool(instructions and semantic_records[-1].get('op') == 'END'
                            and sum(r['op'] == 'END' for r in instructions) == 1)
            if not complete:
                binding_reason = 'complete_program_END_boundary_required'
        except (ValueError, KeyError, TypeError, LookupError):
            complete = False
            binding_reason = 'source_record_binding_unverified'
    return {'rows': rows, 'records': records, 'complete': complete,
            'binding_reason': binding_reason,
            'step_widths': ({i: record.step_width for i, record in enumerate(listing.records)
                             if isinstance(record, TokenInstruction)} if complete else {})}


def break_context_findings(networks, *, plc_model):
    """Bind BREAK to one saved program's labels and lexical loop structure.

    This uses the existing ordered records and source decoder. It does not
    establish dynamic reachability, pointer-number limits or loop execution.
    A graph/fragment cannot establish that an absent label or FOR is absent
    from the complete program, so it stays unverified.
    """
    from plc.ir import lower_rung_instructions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, reviewed_native_fact_groups
    from plc.static_analysis import _finding
    rows = []
    for network in networks:
        for row in lower_rung_instructions(network.get('ladder', {})):
            rows.append((network, row))
    breaks = [(i, net, row) for i, (net, row) in enumerate(rows)
              if source_record_kind(row) == 'instruction' and row.get('op') in {'BREAK', 'BREAKP'}]
    if not breaks:
        return []
    context = _saved_ordered_context(networks, rows, plc_model)
    records, complete = context['records'], context['complete']
    binding_reason = context['binding_reason']
    labels, stack, loops, at_break, lexical_stack, nonstructural = {}, [], {}, {}, {}, False
    break_transfer_unknown = False
    balanced = True
    if complete:
        for i, row in enumerate(records):
            lexical_stack[i] = tuple(stack)
            kind, op = source_record_kind(row), row.get('op')
            if kind == 'label' and re.fullmatch(r'P[0-9]+', row.get('text', '')):
                labels.setdefault('P' + str(int(row['text'][1:])), []).append(i)
            if kind != 'instruction':
                continue
            if op == 'FOR':
                stack.append(i)
            elif op == 'NEXT':
                if stack:
                    loops[stack.pop()] = i
                else:
                    balanced = False
            elif op in {'BREAK', 'BREAKP'}:
                at_break[i] = tuple(stack)
            else:
                spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu=plc_model, operand_count=len(row.get('args', [])))
                if op != 'END' and (spec is None or spec.category.value == 'program_control'):
                    nonstructural = True
        balanced = balanced and not stack
        # BREAK itself transfers control. A nested exit may deliberately jump
        # past an outer NEXT and leave another active loop for a later BREAK.
        # Such a program must not be rejected by a lexical-outside check.
        for i, active in at_break.items():
            args = records[i].get('args', [])
            targets = labels.get(args[1] if len(args) == 2 else '', [])
            if active and len(targets) == 1 and lexical_stack[targets[0]] != active[:-1]:
                break_transfer_unknown = True
    findings = []
    for i, network, row in breaks:
        op, args = row['op'], row.get('args', [])
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op, cpu=plc_model, operand_count=len(args))
        source = [g for g in reviewed_native_fact_groups(spec, op, args, plc_model, dimensions={'effects.operation'})
                  if g.value.get('operation') == 'exit_FOR_NEXT_loop'
                  and g.value.get('required_loop_context') == 'FOR_NEXT'
                  and g.value.get('target_requirement') == 'same_program_file_and_defined'
                  and g.value.get('exits_one_nesting_level_per_execution') is True]
        checks = dict(pointer_defined_in_same_program='unverified', inside_FOR_NEXT='unverified')
        reasons, severity = [], 'unverified'
        loop = None
        if not source:
            reasons.append('own_BREAK_context_source_required')
        elif not complete:
            reasons.append(binding_reason)
        else:
            pointer = args[1] if len(args) == 2 else ''
            positions = labels.get(pointer, [])
            if len(positions) == 1:
                checks['pointer_defined_in_same_program'] = 'passed'
            elif not positions:
                checks['pointer_defined_in_same_program'] = 'failed'
                severity = 'error';reasons.append('pointer_not_defined_in_this_program')
            else:
                reasons.append('ambiguous_pointer_definition')
            active = at_break.get(i, ())
            if balanced and active:
                loop = active[-1]
            if balanced and not nonstructural and not break_transfer_unknown:
                if active:
                    checks['inside_FOR_NEXT'] = 'passed'
                else:
                    checks['inside_FOR_NEXT'] = 'failed'
                    severity = 'error';reasons.append('BREAK_outside_FOR_NEXT')
            else:
                reasons.append('nonstructural_control_transfer' if nonstructural else
                               'BREAK_target_stack_context_unverified' if break_transfer_unknown else 'unpaired_FOR_NEXT')
            reasons.append('dynamic_control_flow_not_established')
        findings.append({**_finding('BREAK_PROGRAM_CONTEXT', severity,
            ('BREAK program context is invalid: ' if severity == 'error' else 'BREAK program context remains partially unverified: ')
            + ', '.join(reasons), 'Bind the complete saved program and verify dynamic control flow independently.',
            rung_ids=[network.get('rung_id')], networks=[network.get('id')],
            paths=[row['path']] if row.get('path') else []),
            'category': 'program_context', 'opcode': op, 'record_index': row.get('record_index'),
            'context_checks': checks, 'complete_saved_program_source': complete,
            'context_check_scope': 'lexical_structure_of_one_saved_program',
            'innermost_FOR_record_index': records[loop].get('record_index') if loop is not None else None,
            'matching_NEXT_record_index': records[loops[loop]].get('record_index') if loop in loops else None,
            'exits_one_nesting_level_per_execution': bool(source),
            'control_flow_context_verified': False, 'core_execution_established': False,
            'requires': reasons})
    return findings


def _chk_context_rule(opcode, plc_model):
    """Own reviewed X diagnostic rules; a prototype alone grants no context."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, reviewed_native_fact_groups
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model, operand_count=0)
    simple = opcode in {'CHKST', 'CHK'}
    for group in reviewed_native_fact_groups(spec, opcode, [], plc_model,
                                             dimensions={'effects.documented_processing'}):
        value = group.value
        pages = {page for source in group.sources
                 if source.get('manual_id') == 'sh080809engx' and source.get('revision') == 'X'
                 for page in source.get('pdf_pages', [])}
        if (not value.get('surrounding_ladder_context_required')
                or value.get('ordinary_contact_execution') is not False
                or value.get('core_execution_established') is not False
                or value.get('maximum_locations_per_program_file') != 1):
            continue
        if simple:
            qualified = (value.get('operation') == 'diagnostic_condition_block'
                and {524, 526, 527} <= pages
                and value.get('allowed_condition_instructions') == ['LD', 'LDI', 'AND', 'ANI']
                and value.get('effective_condition_instructions') == ['LD', 'AND']
                and value.get('ignored_check_condition_instructions') == ['LDI', 'ANI']
                and value.get('ignored_conditions_still_allocate_contact_numbers') is True
                and value.get('condition_device_class') == 'X'
                and type(value.get('maximum_condition_contacts')) is int
                and value['maximum_condition_contacts'] == 150)
        else:
            qualified = (value.get('operation') == 'define_diagnostic_ladder_pattern'
                and {528, 530} <= pages
                and value.get('pattern_contact_device_classes') == ['X', 'Y']
                and value.get('pattern_coil_device_class') == 'F'
                and value.get('pattern_index_register') == 'Z0'
                and value.get('dummy_F_numbers_may_overlap') is True
                and value.get('dummy_F_actual_state_changes') is False
                and type(value.get('maximum_pattern_coils')) is int
                and value['maximum_pattern_coils'] == 9
                and type(value.get('maximum_pattern_steps')) is int
                and value['maximum_pattern_steps'] == 256)
        if qualified:
            return copy.deepcopy(value)
    return None


def chk_context_findings(networks, *, plc_model):
    """Check diagnostic source syntax, never its gate, latch or failure result.

    Full ordered records permit local syntactic checks. APP fragments and opaque
    sources retain unknown context. Lexical pairing is not proof that CHKST was
    dynamically executed or that SM80/SD80 were reset before this scan.
    """
    from plc.ir import lower_rung_instructions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.static_analysis import _finding
    rows = [(network, row) for network in networks
            for row in lower_rung_instructions(network.get('ladder', {}))]
    diagnostic = [(i, net, row) for i, (net, row) in enumerate(rows)
                  if source_record_kind(row) == 'instruction'
                  and row.get('op') in {'CHKST', 'CHK', 'CHKCIR', 'CHKEND'}]
    if not diagnostic:
        return []
    context = _saved_ordered_context(networks, rows, plc_model)
    records, complete = context['records'], context['complete']
    nonstructural = False
    for row in records:
        if source_record_kind(row) != 'instruction' or row.get('op') == 'END':
            continue
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(row['op'], cpu=plc_model,
                                                   operand_count=len(row.get('args', [])))
        if spec is None or spec.category.value == 'program_control':
            nonstructural = True
    pairs, unmatched, active = {}, set(), {}
    for i, _, row in diagnostic:
        op = row['op']
        if op in {'CHKST', 'CHKCIR'}:
            if op in active:
                unmatched.add(active[op])
            active[op] = i
        else:
            begin = 'CHKST' if op == 'CHK' else 'CHKCIR'
            if begin in active:
                first = active.pop(begin);pairs[first] = i;pairs[i] = first
            else:
                unmatched.add(i)
    unmatched.update(active.values())
    counts = {op: sum(row['op'] == op for _, _, row in diagnostic)
              for op in {'CHKST', 'CHK', 'CHKCIR', 'CHKEND'}}

    def contact_class(token):
        if re.fullmatch(r'[KH][+-]?[0-9A-F]+', token, re.I):
            return None
        match = re.fullmatch(r'(?:K[1-8])?([A-Z]+)[0-9A-F]+(?:Z[0-9]+)?', token, re.I)
        return match.group(1).upper() if match else 'unverified'

    findings = []
    for index, network, row in diagnostic:
        op = row['op'];simple = op in {'CHKST', 'CHK'}
        rule = _chk_context_rule(op, plc_model)
        checks = {'local_source_pairing': 'unverified', 'per_program_location_limit': 'unverified',
                  'dynamic_execution_context': 'unverified', 'all_executed_program_files_limit': 'unverified',
                  'current_SM710': 'unverified', 'current_diagnostic_latch_and_user_reset': 'unverified',
                  'current_program_task_type': 'unverified'}
        reasons, errors = [], []
        detail = {}
        mate = pairs.get(index)
        if rule is None:
            reasons.append('own_reviewed_CHK_context_source_required')
        elif not complete:
            reasons.append(context['binding_reason'])
        elif nonstructural:
            reasons.append('diagnostic_control_transfer_context_unverified')
        else:
            location_count = max(counts['CHKST'], counts['CHK']) if simple else max(counts['CHKCIR'], counts['CHKEND'])
            checks['per_program_location_limit'] = 'passed' if location_count <= 1 else 'failed'
            if location_count > 1:
                errors.append('more_than_one_' + ('CHK' if simple else 'pattern') + '_location_in_one_program')
            if index in unmatched or mate is None:
                if nonstructural:
                    reasons.append('local_pairing_with_control_transfer_unverified')
                else:
                    checks['local_source_pairing'] = 'failed';errors.append('unpaired_' + op)
            else:
                checks['local_source_pairing'] = 'passed'
                begin, end = sorted((index, mate))
                body = [(i, records[i]) for i in range(begin + 1, end)
                        if source_record_kind(records[i]) == 'instruction']
                barrier = any(source_record_kind(records[i]) in {'label', 'opaque'}
                              for i in range(begin + 1, end))
                if barrier:
                    reasons.append('diagnostic_block_source_barrier')
                elif simple:
                    contacts, illegal = [], []
                    for position, item in body:
                        command, args = item['op'], item.get('args', [])
                        if command not in rule['allowed_condition_instructions']:
                            illegal.append(position);continue
                        good = len(args) == 1 and re.fullmatch(r'X[0-9A-F]+', args[0], re.I)
                        if not good:
                            illegal.append(position)
                        contacts.append({'record_index': item.get('record_index'),
                            'source_offset': item.get('source_offset'), 'opcode': command,
                            'operand': args[0] if len(args) == 1 else None,
                            'contact_number': len(contacts) + 1,
                            'participates_in_failure_check': command in rule['effective_condition_instructions']})
                    checks['check_condition_instructions_and_X_devices'] = 'failed' if illegal else 'passed'
                    checks['condition_contact_limit'] = ('passed' if len(contacts) <= rule['maximum_condition_contacts'] else 'failed')
                    if illegal:errors.append('invalid_CHK_check_condition_instruction_or_non_X_device')
                    if checks['condition_contact_limit'] == 'failed':errors.append('more_than_150_CHK_condition_contacts')
                    detail.update(condition_contacts=contacts, condition_contact_count=len(contacts),
                        ignored_condition_contact_numbers=[c['contact_number'] for c in contacts
                                                          if not c['participates_in_failure_check']],
                        preceding_CHK_contacts_are_execution_gate=False,
                        CHKST_execution_gate_current_value_verified=False)
                else:
                    # CHKEND's ordinary gate follows the last completed OUT F
                    # pattern network; it is not one of the pattern X/Y contacts.
                    coil_positions = [j for j, (_, item) in enumerate(body) if item['op'] == 'OUT']
                    end_gate = []
                    if coil_positions:
                        tail = coil_positions[-1] + 1
                        suffix = body[tail:]
                        gate_ops = set(rule['pattern_contact_instructions'])
                        if suffix and all(item['op'] in gate_ops for _, item in suffix):
                            body, end_gate = body[:tail], suffix
                    coils, illegal, unknown, index_values = [], [], [], []
                    allowed = set(rule['pattern_contact_instructions'])
                    for position, item in body:
                        command, args = item['op'], item.get('args', [])
                        if command == 'OUT':
                            if len(args) == 1 and re.fullmatch(r'F[0-9A-F]+', args[0], re.I):
                                coils.append({'record_index': item.get('record_index'), 'operand': args[0],
                                              'coil_number': len(coils) + 1})
                            else:illegal.append(position)
                            continue
                        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(command, cpu=plc_model, operand_count=len(args))
                        comparative = bool(spec and spec.category.value == 'condition'
                                           and spec.semantic_kind.value == 'comparison')
                        if command not in allowed and not (comparative and rule['comparative_operation_contacts_allowed']):
                            illegal.append(position);continue
                        for operand in args:
                            device = contact_class(operand)
                            if device == 'unverified':unknown.append(position)
                            elif device is not None and device not in rule['pattern_contact_device_classes']:
                                illegal.append(position)
                            if device in rule['pattern_contact_device_classes']:
                                modifier = re.search(r'Z([0-9]+)$', operand, re.I)
                                index_values.append('Z' + modifier[1] if modifier else None)
                    steps = sum(context['step_widths'].get(position, 0) for position, _ in body)
                    checks['pattern_instructions_and_devices'] = 'failed' if illegal else 'unverified' if unknown else 'passed'
                    checks['pattern_coil_limit'] = 'passed' if len(coils) <= rule['maximum_pattern_coils'] else 'failed'
                    checks['pattern_step_limit'] = 'passed' if steps <= rule['maximum_pattern_steps'] else 'failed'
                    checks['pattern_index_binding'] = ('passed' if index_values and all(v == 'Z0' for v in index_values) else 'unverified')
                    if illegal:errors.append('invalid_CHK_pattern_instruction_or_device')
                    if checks['pattern_coil_limit'] == 'failed':errors.append('more_than_9_CHK_pattern_coils')
                    if checks['pattern_step_limit'] == 'failed':errors.append('more_than_256_CHK_pattern_steps')
                    if unknown:reasons.append('pattern_contact_address_form_unverified')
                    if checks['pattern_index_binding'] != 'passed':reasons.append('pattern_Z0_binding_unverified')
                    # X531's index-modification error sentence conflicts with
                    # X528's Z0 requirement. It cannot establish a blanket ban.
                    reasons.append('source_index_modification_error_boundary_not_established')
                    detail.update(pattern_coils=coils, pattern_coil_count=len(coils), pattern_steps=steps,
                        CHKEND_gate_record_indices=[item.get('record_index') for _, item in end_gate],
                        dummy_F_actual_state_changes=False, dummy_F_numbers_may_overlap=True)
            reasons.extend(['dynamic_CHKST_gate_and_execution_order_not_established',
                            'SM80_SD80_user_reset_and_scan_latch_not_established',
                            'SM710_scan_order_current_value_not_established',
                            'all_executed_program_files_context_required', 'program_task_type_required'])
        reasons = list(dict.fromkeys([*errors, *reasons]))
        severity = 'error' if errors else 'unverified'
        findings.append({**_finding('CHK_PROGRAM_CONTEXT', severity,
            ('CHK diagnostic source context is invalid: ' if errors else 'CHK diagnostic context remains partially unverified: ')
            + ', '.join(reasons), 'Bind complete saved source and verify task, scan/latch and project-wide diagnostic context independently.',
            rung_ids=[network.get('rung_id')], networks=[network.get('id')],
            paths=[row['path']] if row.get('path') else []),
            'category': 'program_context', 'opcode': op, 'record_index': row.get('record_index'),
            'source_offset': row.get('source_offset'), 'context_checks': checks,
            'complete_saved_program_source': complete,
            'context_check_scope': 'source_syntax_of_one_saved_program_not_dynamic_execution',
            'matching_diagnostic_record_index': records[mate].get('record_index') if mate is not None else None,
            'control_flow_context_verified': False, 'core_execution_established': False,
            'current_SM710_verified': False, 'current_diagnostic_device_values_verified': False,
            'all_executed_program_files_context_verified': False,
            'ordinary_contact_execution': False, 'requires': reasons, **detail})
    return findings



def _cj_context_rule(opcode, operands, plc_model):
    """Select this owner's native same-file rule, without granting CPU errors."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, reviewed_native_fact_groups
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model, operand_count=len(operands))
    reviewed = reviewed_native_fact_groups(spec, opcode, operands, plc_model, dimensions={'execution.jump'})
    source = [group for group in reviewed
              if group.value.get('operation') == 'jump_to_pointer'
              and group.value.get('target_scope') == 'same_program_file'
              and group.value.get('core_execution_established') is False
              and group.value.get('dynamic_reachability_verified') is False
              and any(s.get('manual_id') == 'sh080809engx' and s.get('revision') == 'X'
                      and 325 in s.get('pdf_pages', []) for s in group.sources)]
    if not source:
        return None
    # The error-table CPU glyph is still unresolved. Only its explicitly
    # source-checked structural condition is used here, never code 4210's
    # applicability, special-pointer semantics or a numeric pointer domain.
    before_end = any(
        group.dimension == 'effects.documented_processing'
        and group.status == 'candidate_evidence' and group.verification == 'source_checked'
        and group.scope.get('models') == [plc_model]
        and group.scope.get('forms') == [opcode] and group.scope.get('languages') == ['ladder']
        and group.conditions == ({'context': 'project_mode', 'equals': 'native_ladder_instruction'},)
        and group.value.get('operation') == 'documented_jump_target_error'
        and 'target_missing_before_END' in group.value.get('reasons', [])
        and group.value.get('CPU_error_row_applicability_established') is False
        and any(s.get('manual_id') == 'sh080809engx' and s.get('revision') == 'X'
                and 326 in s.get('pdf_pages', []) for s in group.sources)
        for group in spec.definition_facts)
    return {'same_program_file': True, 'definition_before_END_required': before_end}


def cj_context_findings(networks, *, plc_model):
    """Bind CJ to saved labels without inferring execution or another CPU's P."""
    from plc.ir import lower_rung_instructions
    from plc.static_analysis import _finding
    from plc.device_profiles import program_pointer_domain
    rows = [(network, row) for network in networks
            for row in lower_rung_instructions(network.get('ladder', {}))]
    jumps = [(i, network, row) for i, (network, row) in enumerate(rows)
             if source_record_kind(row) == 'instruction' and row.get('op') == 'CJ']
    if not jumps:
        return []
    context = _saved_ordered_context(networks, rows, plc_model)
    records, complete = context['records'], context['complete']
    labels = {}
    if complete:
        for i, row in enumerate(records):
            if source_record_kind(row) == 'label' and re.fullmatch(r'P[0-9]+', row.get('text', '')):
                labels.setdefault('P' + str(int(row['text'][1:])), []).append(i)
    findings = []
    for _, network, row in jumps:
        args = row.get('args', [])
        rule = _cj_context_rule('CJ', args, plc_model)
        checks = dict(pointer_defined_in_same_program='unverified', pointer_definition_before_END='unverified',
                      dynamic_reachability='unverified', pointer_numeric_domain='unverified',
                      special_pointer_values='unverified', FEND_boundary='unverified')
        reasons, errors, positions = [], [], []
        numeric_domain = None
        if rule is not None and len(args) == 1 and re.fullmatch(r'P[0-9]+', args[0]):
            numeric_domain = program_pointer_domain(plc_model)
            if numeric_domain is not None:
                number = int(args[0][1:])
                in_domain = numeric_domain['minimum_number'] <= number <= numeric_domain['maximum_number']
                checks['pointer_numeric_domain'] = 'passed' if in_domain else 'failed'
                if not in_domain:
                    errors.append('CJ_pointer_outside_sourced_CPU_numeric_domain')
        if rule is None:
            reasons.append('own_native_CJ_same_program_source_required')
        elif not complete:
            reasons.append(context['binding_reason'])
        elif len(args) != 1 or not re.fullmatch(r'P[0-9]+', args[0]):
            reasons.append('CJ_pointer_form_unverified')
        else:
            positions = labels.get('P' + str(int(args[0][1:])), [])
            if len(positions) == 1:
                checks['pointer_defined_in_same_program'] = 'passed'
                if rule['definition_before_END_required']:
                    checks['pointer_definition_before_END'] = 'passed'
            elif len(positions) > 1:
                reasons.append('ambiguous_pointer_definition')
            elif rule['definition_before_END_required']:
                checks['pointer_defined_in_same_program'] = 'failed'
                checks['pointer_definition_before_END'] = 'failed'
                errors.append('CJ_target_not_defined_before_END_in_this_program')
            else:
                reasons.append('CJ_target_definition_requirement_unverified')
        if numeric_domain is None:
            reasons.append('pointer_numeric_domain_not_established')
        reasons.extend(['dynamic_control_flow_not_established',
                        'special_pointer_values_not_established', 'FEND_boundary_not_established',
                        'current_scan_completion_not_established'])
        reasons = [*errors, *reasons]
        findings.append({**_finding('CJ_PROGRAM_CONTEXT', 'error' if errors else 'unverified',
            ('CJ saved target context is invalid: ' if errors else 'CJ target context remains partially unverified: ')
            + ', '.join(reasons), 'Bind the complete saved program and verify dynamic jump and scan behavior independently.',
            rung_ids=[network.get('rung_id')], networks=[network.get('id')],
            paths=[row['path']] if row.get('path') else []),
            'category': 'program_context', 'opcode': 'CJ', 'record_index': row.get('record_index'),
            'source_offset': row.get('source_offset'), 'context_checks': checks,
            'complete_saved_program_source': complete,
            'context_check_scope': 'label_definition_in_one_saved_program_not_dynamic_execution',
            'target_label_record_indices': [records[i].get('record_index') for i in positions],
            'control_flow_context_verified': False, 'dynamic_reachability_verified': False,
            'current_scan_completion_verified': False, 'core_execution_established': False,
            'pointer_numeric_domain_verified': numeric_domain is not None,
            'pointer_numeric_domain_source': ({'kind': 'cpu_program_pointer_domain', 'domain': numeric_domain}
                                              if numeric_domain is not None else None),
            'pointer_allocation_verified': False, 'special_pointer_values_verified': False,
            'FEND_boundary_verified': False, 'CPU_error_row_applicability_established': False,
            'requires': reasons})
    return findings

# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""One spelling for Mitsubishi device aliases, without renumbering devices.

X001 and X1 denote the same index; X010 becomes X10, NEVER X8. This
representation-only boundary strips leading zeroes, not a base conversion.
CPU/range/writability validation remains the existing PLC validator's job.
Comments, literals, contact types and control topology are not rewritten.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping

DEVICE_PREFIXES = (
    "ER", "SM", "SD", "TS", "TC", "TN", "CS", "CC", "CN",
    "X", "Y", "M", "S", "T", "C", "D", "R", "V", "Z", "P", "I",
)
_DEVICE_PREFIX_PATTERN = "(?:" + "|".join(DEVICE_PREFIXES) + ")"
DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])" + _DEVICE_PREFIX_PATTERN
    + r"\d+(?:\.\d+)?(?![A-Za-z0-9_])",
    re.I,
)
# Read-only manual references include Q/L hexadecimal I/O/link devices and
# their additional device classes. This lexer grants neither an address range
# nor writability/execution support; the generation validator stays separate.
REFERENCE_DEVICE_PATTERN = r'(?:(?:SB|SW|DX|DY|[XYBW])[0-9A-F]+|(?:ZR|SM|SD|ST|TS|TC|TN|CS|CC|CN|ER|[MLFSTCDRVZPI])\d+)(?:\.[0-9A-F]+)?'
REFERENCE_DEVICE_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_])' + REFERENCE_DEVICE_PATTERN + r'(?![A-Za-z0-9_])', re.I)


def reference_device_tokens(value):
    """Literal source tokens for retrieval, without runtime CPU validation."""
    return tuple(match.group(0).upper() for match in REFERENCE_DEVICE_TOKEN_RE.finditer(str(value or '')))


def canonical_reference_device(value):
    if not isinstance(value, str) or not REFERENCE_DEVICE_TOKEN_RE.fullmatch(value.strip()):
        return value
    match = re.fullmatch(r'(ZR|SM|SD|ST|SB|SW|DX|DY|TS|TC|TN|CS|CC|CN|ER|[A-Z])([0-9A-F]+)(.*)', value.strip().upper())
    return match[1] + (match[2].lstrip('0') or '0') + match[3] if match else value


def reference_device_parts(value):
    if not isinstance(value, str) or not REFERENCE_DEVICE_TOKEN_RE.fullmatch(value.strip()):
        return None
    match = re.fullmatch(r'(ZR|SM|SD|ST|SB|SW|DX|DY|TS|TC|TN|CS|CC|CN|ER|[A-Z])([0-9A-F]+)', value.strip().upper())
    return match.groups() if match else None
INDEXABLE_HEX_PREFIXES = ("SB", "SW", "X", "Y", "B", "W")
INDEXABLE_DEC_PREFIXES = (
    "ZR", "SM", "SD", "ST", "TS", "TC", "TN", "CS", "CC", "CN",
    "M", "D", "T", "C", "S", "L", "F", "R", "V",
)
INDEXED_BASE_PATTERN = (
    r"(?:(?:" + "|".join(INDEXABLE_HEX_PREFIXES) + r")[0-9A-F]+|(?:"
    + "|".join(INDEXABLE_DEC_PREFIXES) + r")\d+)"
)
_SIMPLE = re.compile(
    r"(?:(?P<hex_p>SB|SW|B|W)(?P<hex_n>[0-9][0-9A-F]*)"
    r"|(?P<dec_p>ZR|ER|SM|SD|ST|TS|TC|TN|CS|CC|CN|[XYMTSCDRLFVZPI])(?P<dec_n>\d+))",
    re.I,
)
_INDEXED = re.compile(
    r"(?P<base>(?:(?:SB|SW|B|W)[0-9][0-9A-F]*|"
    r"(?:ZR|SM|SD|ST|TS|TC|TN|CS|CC|CN|[XYMTSCDRLFV])\d+))"
    r"(?P<index>[VZ]\d*)",
    re.I,
)


def device_tokens(value):
    """Return explicit Mitsubishi device tokens in source order."""
    text = str(value or "")
    return tuple(match.group(0).upper() for match in DEVICE_TOKEN_RE.finditer(text))


def canonical_device(value):
    """Normalize a simple device only; leave malformed/non-device values intact."""
    if not isinstance(value, str):
        return value
    match = _SIMPLE.fullmatch(value.strip())
    if match is None:
        return value
    prefix = (match.group("hex_p") or match.group("dec_p")).upper()
    number = (match.group("hex_n") or match.group("dec_n")).upper().lstrip("0") or "0"
    return prefix + number


def canonical_operand(value):
    """Handle explicit simple/indexed operands, never numbers, strings or names."""
    if not isinstance(value, str):
        return value
    match = _INDEXED.fullmatch(value.strip())
    if match:
        base = canonical_device(match.group("base"))
        raw_index = match.group("index").strip().upper()
        index = raw_index if len(raw_index) == 1 else canonical_device(raw_index)
        return base + index
    return canonical_device(value)


def decimal_region_address(base, offset):
    """Resolve explicit decimal memory offsets; X/Y numbering is CPU-owned."""
    if type(offset) is int and offset == 0 and re.fullmatch(r'[XYTCS]\d+', str(base), re.I):
        return canonical_device(base)
    match = re.fullmatch(r'(SM|SD|M|D|R)(\d+)', str(base), re.I)
    if match is None or type(offset) is not int or int(match[2]) + offset < 0:
        raise ValueError('Device offset policy is unavailable')
    return match[1].upper() + str(int(match[2]) + offset)


def word_region_addresses(base, words, plc_model):
    """Adjacent words under the Core CPU profile, never bit or indexed devices.

    The operand width belongs to its instruction/type. Numbering and allocation
    belong to the device profile; absence of either is not permission to guess.
    """
    from plc.validation import parse_device_address, is_bit_device, device_address_radix
    parsed = parse_device_address(base, plc_model)
    if (parsed is None or is_bit_device(base, plc_model)
            or type(words) is not int or words < 1):
        raise ValueError('word_device_region_unverified:' + str(base))
    prefix, start = parsed
    radix = device_address_radix(prefix, plc_model)
    addresses = tuple(prefix + format(start + i, 'X' if radix == 16 else 'o' if radix == 8 else 'd')
                      for i in range(words))
    if any(parse_device_address(address, plc_model) is None for address in addresses):
        raise ValueError('word_device_region_exceeds_allocation:' + str(base))
    return addresses


def digit_specified_devices(value, plc_model="FX3U"):
    """Exact FX3U 16-bit digit specification; X/Y use the CPU's octal radix.

    JY997D16601-R, section 5.4 (PDF p162): Kn names n groups of four consecutive bits.
    Wider, indexed and other CPU forms remain outside this bounded subset.
    """
    match = re.fullmatch(r'K([1-4])([XYMS])(\d+)', str(value).strip(), re.I)
    if match is None:
        return None
    if str(plc_model).upper() != "FX3U":
        raise ValueError('Digit specification runtime is unavailable for this CPU')
    from plc.validation import parse_device_address
    head = parse_device_address(match[2].upper()+match[3], plc_model)
    if head is None:
        raise ValueError('Invalid digit specification head device')
    prefix, start = head
    radix = 'o' if prefix in {'X', 'Y'} else 'd'
    devices = tuple(prefix+format(start+i, radix) for i in range(4*int(match[1])))
    if any(parse_device_address(device, plc_model) is None for device in devices):
        raise ValueError('Digit specification exceeds the CPU device region')
    return devices


def digit_specified_operand_devices(value, plc_model, *, maximum_groups):
    """Expand a reviewed construction operand without extending the executor.

    The operand contract supplies its maximum width. Address classes, radices
    and region bounds still come from the existing CPU device definitions.
    """
    if type(maximum_groups) is not int or not 1 <= maximum_groups <= 8:
        raise ValueError('Digit specification needs a reviewed operand width')
    match = re.fullmatch(r'K([1-8])(.+)', str(value).strip(), re.I)
    if match is None:
        return None
    if int(match[1]) > maximum_groups:
        raise ValueError('Digit-specified operand exceeds the source-verified width')
    from plc.validation import parse_device_address, is_bit_device, device_address_radix
    head = parse_device_address(match[2], plc_model)
    if head is None or '.' in match[2] or not is_bit_device(match[2], plc_model) or head[0] in {'T', 'C', 'ST'}:
        raise ValueError('Digit specification needs a supported bit device head')
    prefix, start = head
    radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(prefix, plc_model)]
    devices = tuple(prefix + format(start + offset, radix) for offset in range(4 * int(match[1])))
    if any(parse_device_address(device, plc_model) is None for device in devices):
        raise ValueError('Digit specification exceeds the CPU device region')
    return devices


def digit_specified_operand_reference(value, plc_model, *, maximum_groups):
    """Describe a reviewed Kn head while retaining its unresolved index."""
    if type(maximum_groups) is not int or not 1 <= maximum_groups <= 8:
        raise ValueError('Digit specification needs a reviewed operand width')
    match = re.fullmatch(r'K([1-8])(.+)', str(value).strip(), re.I)
    if match is None:
        return None
    if int(match[1]) > maximum_groups:
        raise ValueError('Digit-specified operand exceeds the source-verified width')
    from plc.validation import parse_device_address, parse_indexed_device_address, is_bit_device, device_address_radix
    indexed = parse_indexed_device_address(match[2], plc_model)
    head = indexed['base_text'] if indexed else match[2]
    address = parse_device_address(head, plc_model)
    if address is None or '.' in head or not is_bit_device(head, plc_model) or address[0] in {'T', 'C', 'ST'}:
        raise ValueError('Digit specification needs a supported bit device head')
    prefix, first = address
    radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(prefix, plc_model)]
    return {'base': prefix + format(first, radix), 'device_prefix': prefix,
            'address': first, 'digit_groups': int(match[1]), 'indexed': bool(indexed),
            'indexed_device': indexed, 'index_registers': [indexed['index_text']] if indexed else []}


def operand_device_point_count(operand, address, plc_model):
    """Use the existing operand width and the selected device's numeric unit."""
    points = operand.device_point_count
    if operand.data_type in {'bit', 'bool', 'any'} or points <= 1:
        return points
    from plc.device_profiles import native_word_value_bits
    bits = native_word_value_bits(address[0], address[1], plc_model)
    return (points * 16 + bits - 1) // bits if bits else points


def external_operand_reference(value, plc_model):
    """Describe an existing FX/Q storage spelling without granting its use."""
    text = str(value or '').strip().upper()
    if '\\' not in text:
        return None
    from plc.storage.errors import StorageRuleError
    try:
        if str(plc_model).upper().startswith('FX'):
            from plc.storage.operands_fx import buffer_memory_operand
            reference = buffer_memory_operand(text)
            return {'form': 'buffer_memory', 'base': f"U{reference['unit']}\\G{reference['address']}",
                    'prefix': f"U{reference['unit']}", 'device_prefix': 'G', 'address': reference['address'],
                    'radix': 10, 'indexed': bool(reference['index']),
                    'index_registers': [reference['index'] + str(reference['index_number'])] if reference['index'] else []}
        from plc.storage.q_encoder import encode_q_operand, operand, validate_descriptor, _cpu_mode
        from plc.storage.operands_q import encoded_tokens
        from plc.storage.tables import instruction_catalog, storage_profile
        profile = storage_profile(plc_model)
        raw = encode_q_operand(text, op='', index=0, profile=profile, cpu=plc_model)
        tokens = encoded_tokens(raw)
        prefixes = [token for token in tokens[:-1] if token[1] in {0xf8, 0xf9}]
        if len(prefixes) != 1 or any(token[1] in {0xf3, 0xfc, 0xfd, 0xfe, 0xff} for token in tokens[:-1]):
            return None
        mode = _cpu_mode(profile, plc_model)
        descriptor = validate_descriptor(operand(raw, mode=mode)[0], mode=mode)
        groups = [int.from_bytes(token[2:-1], 'little') for token in tokens[:-1] if token[1] == 0xf1]
        if len(groups) > 1:
            return None
        prefix_token, device_token = prefixes[0], tokens[-1]
        device, kind = instruction_catalog('l' if profile == 'l02' else profile)['operands'][f'{device_token[1]:02x}']
        address = int.from_bytes(device_token[2:-1], 'little')
        unit = int.from_bytes(prefix_token[2:-1], 'little')
        prefix = ('U' + format(unit, 'X')) if prefix_token[1] == 0xf8 else ('J' + str(unit))
        form = 'buffer_memory' if prefix_token[1] == 0xf8 and device == 'G' else 'link_direct' if prefix_token[1] == 0xf9 else None
        if form is None:
            return None
        registers = ['Z' + str(int.from_bytes(token[2:-1], 'little')) for token in tokens[:-1]
                     if token[1] in {0xf0, 0xf6}]
        prefix_position = next(index for index, token in enumerate(tokens[:-1]) if token[1] in {0xf8, 0xf9})
        index_components = [{'position': 'prefix' if index < prefix_position else 'device',
                             'kind': 'ZZ' if token[1] == 0xf6 else 'Z',
                             'register': ('ZZ' if token[1] == 0xf6 else 'Z')
                                         + str(int.from_bytes(token[2:-1], 'little'))}
                            for index, token in enumerate(tokens[:-1]) if token[1] in {0xf0, 0xf6}]
        if int.from_bytes(descriptor[7:9], 'little') & 1:
            registers = [item['register'] for item in index_components]
        selectors = [int.from_bytes(token[2:-1], 'little') for token in tokens[:-1] if token[1] == 0xf2]
        if len(selectors) > 1 or selectors and (groups or not 0 <= selectors[0] <= 15):
            return None
        base = prefix + '\\' + device + format(address, 'X' if kind == 4 else 'd')
        if selectors:
            base += '.' + format(selectors[0], 'X')
        return {'form': form, 'base': base,
                **({'bit_selector': selectors[0]} if selectors else {}),
                **({'index_components': index_components} if index_components else {}),
                'prefix': prefix, 'device_prefix': device, 'address': address,
                'radix': 16 if kind == 4 else 10, 'indexed': bool(registers), 'index_registers': registers,
                'digit_groups': groups[0] if groups else 0,
                'bit_device': bool(groups) or bool(int.from_bytes(descriptor[7:9], 'little') & 1)}
    except (StorageRuleError, KeyError, TypeError, ValueError):
        return None


def direct_access_output_reference(value, plc_model):
    """Describe an existing Q/L DY spelling; range and hardware stay unbound."""
    from plc.target_identity import target_family
    if target_family(plc_model) not in {'Q', 'L'} or not isinstance(value, str):
        return None
    match = re.fullmatch(r'DY([0-9A-F]+)(Z[0-9]+)?', value.strip().upper())
    if match is None:
        return None
    from plc.storage.q_encoder import encode_q_operand, operand, validate_descriptor
    from plc.storage.operands_q import encoded_tokens
    from plc.storage.tables import storage_profile, cpu_storage_mode
    from plc.storage.errors import StorageRuleError
    from plc.validation import parse_device_address
    index = ('Z' + str(int(match[2][1:]))) if match[2] else None
    if index and parse_device_address(index, plc_model) is None:
        return None
    try:
        text = value.strip().upper()
        raw = encode_q_operand(text, op='', index=0, profile=storage_profile(plc_model), cpu=plc_model)
        tokens = encoded_tokens(raw)
        if tokens[-1][1] != 0xa3 or any(t[1] != 0xf0 for t in tokens[:-1]):
            return None
        descriptor, _ = operand(raw, mode=cpu_storage_mode(plc_model))
        validate_descriptor(descriptor, mode=cpu_storage_mode(plc_model))
        number = int(match[1], 16)
    except (StorageRuleError, ValueError, OverflowError):
        return None
    base = 'DY' + format(number, 'X')
    return {'kind': 'direct_access_output', 'base': base, 'number': number,
            'text': base + (index or ''), 'index': index}


def instruction_operand_regions(spec, opcode, operands, plc_model, *, parameters=None):
    """Resolve only source-declared storage extents, keeping dynamic ones symbolic.

    A word-count layout describes consecutive storage elements. A bit-count
    layout uses the selected device's bit/word form, independent of the numeric
    width of an instruction parameter. Neither requires an opcode-specific rule.
    """
    from plc.instructions import (reviewed_native_operand_parameters, reviewed_remote_device_identity,
                                  reviewed_direct_access_output_identity, reviewed_module_selector_value)
    from plc.instruction_constraints import literal_operand_values
    from plc.instruction_definition import DefinitionError, Expression, UnknownInstructionSemantics, evaluate_expression
    from plc.validation import parse_device_address, parse_indexed_device_address, is_bit_device, device_address_radix
    from plc.ordered_source import buffer_memory_reference
    if spec is None:
        return []
    if parameters is None:
        parameters = reviewed_native_operand_parameters(spec, opcode, operands, plc_model)
    values = literal_operand_values(spec, operands, plc_model=plc_model, opcode=opcode)
    regions = []
    for index, descriptions in enumerate(parameters):
        if index >= len(spec.operands):
            continue
        definition, head = spec.operands[index], str(operands[index]).strip().upper()
        if (re.fullmatch(r'(?:K[+-]?\d+|H[0-9A-F]+)', head)
                or reviewed_module_selector_value(spec, index, head, plc_model) is not None):
            # A repeated literal supplies values without occupying a device region.
            continue
        external = external_operand_reference(head, plc_model)
        digit = None
        if definition.digit_groups_max and not external:
            try:
                digit = digit_specified_operand_reference(head, plc_model, maximum_groups=definition.digit_groups_max)
            except ValueError:
                pass
        claims, bit_layouts = [], []
        for parameter in descriptions:
            if reviewed_direct_access_output_identity(parameter):
                reference = direct_access_output_reference(head, plc_model)
                requirements = ['CPU_module_output_range_not_established',
                                'installed_output_configuration_not_established']
                projected = dict(operand_index=index, head=head, access='write', unit='bits', count=1,
                                 address_space='direct_access_output', dependencies=[])
                if reference:
                    projected['resolved_count'] = 1
                    if reference['index']:
                        projected['dependencies'] = [reference['index']]
                        requirements.append('indexed_effective_address')
                    else:
                        projected['addresses'] = [reference['base']]
                else:
                    requirements.append('direct_access_output_spelling_not_verified')
                projected['requires'] = requirements
                if projected not in regions:
                    regions.append(projected)
                continue
            if 'region_span' in parameter:
                for projected in _declared_parameter_span_regions(spec, opcode, operands, plc_model, index, parameter):
                    if projected not in regions:
                        regions.append(projected)
                continue
            if reviewed_remote_device_identity(parameter):
                projected = _remote_identity_region(spec, operands, index, parameter)
                if projected not in regions:
                    regions.append(projected)
                continue
            layout = parameter.get('word_device_layout')
            if isinstance(layout, Mapping) and layout.get('head_storage_only') is True:
                for projected in (_transfer_payload_regions(spec, operands, plc_model, index, parameter)
                                  if 'transfer_layout' in parameter else
                                  _conditional_head_payload_regions(spec, opcode, operands, plc_model, index, parameter)):
                    if projected not in regions:
                        regions.append(projected)
                continue
            if 'occupied_bit_count' in parameter:
                claim = ('bits', parameter['occupied_bit_count'])
                sequence = parameter.get('bit_sequence_layout')
                if isinstance(sequence, Mapping) and sequence not in bit_layouts:
                    bit_layouts.append(sequence)
            elif isinstance(layout, Mapping) and 'word_count' in layout:
                if (type(layout['word_count']) is int and layout.get('single_scalar') is not False
                        and layout.get('kind') != 'independent_elements' and layout.get('element_bits') != 8):
                    # Existing fixed scalar contracts already account for a
                    # 32-bit counter occupying one physical counter device.
                    continue
                claim = ('words', layout['word_count'])
            else:
                continue
            if claim not in claims:
                claims.append(claim)
        group_reference = external if external and external.get('digit_groups') else digit
        grouped = bool(group_reference and group_reference.get('digit_groups')
                       and group_reference['digit_groups'] <= definition.digit_groups_max
                       and {'operand_types', 'device_classes'} <= set(spec.verified_fields))
        intrinsic_group = grouped and not claims
        group_sequence_unknown = bool(group_reference and group_reference.get('digit_groups')
                                      and not intrinsic_group)
        if intrinsic_group:
            claims.append(('bits', 4 * group_reference['digit_groups']))
        if not claims:
            continue
        for unit, count in claims:
            requirements, dependencies = [], set()
            resolved = None
            if type(count) is int and 0 <= count <= 65536:
                resolved = count
            elif isinstance(count, Mapping):
                try:
                    expression = Expression.from_mapping(count)
                    environment = dict(values)
                    def bind(node):
                        if node.op == 'parameter':
                            if node.name.upper() in values:
                                environment[node.name] = values[node.name.upper()]
                            else:
                                requirements.append('parameter_value:' + node.name)
                        elif node.op in {'device', 'state'}:
                            if parse_device_address(node.name, plc_model) is not None:
                                dependencies.add(node.name.upper())
                            requirements.append('device_value:' + node.name)
                        for argument in node.args:
                            bind(argument)
                    bind(expression)
                    value = evaluate_expression(expression, environment, state=environment)
                    if type(value) is int and 0 <= value <= 65536:
                        resolved = value
                except (DefinitionError, UnknownInstructionSemantics, TypeError, ValueError, KeyError):
                    pass
            if resolved is None and not requirements:
                requirements.append('storage_count_not_resolved')
            indexed = digit['indexed_device'] if digit else parse_indexed_device_address(head, plc_model)
            buffer = buffer_memory_reference(head)
            address = parse_device_address(indexed['base_text'] if indexed else digit['base'] if digit else head, plc_model)
            addresses = None
            physical = None
            unmatched_word_layout = None
            matching_word_layouts = []
            if address and unit == 'words' and resolved != 0:
                from plc.device_profiles import native_word_value_bits
                for parameter in descriptions:
                    layout = parameter.get('word_device_layout')
                    if (isinstance(layout, Mapping) and layout.get('word_count') == count
                            and 'device_unit_bits' in layout):
                        matching_word_layouts.append(layout)
                for layout in matching_word_layouts:
                    if native_word_value_bits(*address, plc_model) != layout['device_unit_bits']:
                        # A declared 16-bit payload count does not establish
                        # how many native 32-bit counters its head occupies.
                        unmatched_word_layout = layout
                        requirements.append('source_region_device_unit_not_established')
                        resolved = None
                        break
            points = resolved
            sequence = bit_layouts[0] if unit == 'bits' and len(bit_layouts) == 1 else None
            sequence_known = (isinstance(sequence, Mapping)
                and set(sequence) == {'word_bits', 'bit_order', 'cross_word'}
                and type(sequence['word_bits']) is int and sequence['word_bits'] == 16
                and sequence['bit_order'] == 'ascending'
                and sequence['cross_word'] in {'not_stated', 'next_word_bit_zero'})
            plain_word_unknown = bool(bit_layouts and address and '.' not in head
                and not digit and not is_bit_device(head, plc_model))
            if indexed:
                dependencies.add(indexed['index_text'])
                requirements.append('indexed_effective_address')
            if buffer and buffer['index']:
                dependencies.add(buffer['index'])
                requirements.append('indexed_effective_address')
            if external and external['indexed']:
                dependencies.update(external['index_registers'])
                requirements.append('indexed_effective_address')
            if group_sequence_unknown and resolved != 0:
                requirements.append('digit_group_sequence_layout')
            if plain_word_unknown:
                requirements.append('plain_word_bit_count_semantics_not_established')
            if address and '.' in head and resolved != 0:
                if not sequence_known:
                    requirements.append('word_bit_sequence_layout')
            if address and unit == 'bits' and resolved is not None:
                bit_form = (bool(digit) or '.' in head or definition.data_type in {'bit', 'bool'}
                            or is_bit_device(head, plc_model) and (
                                sequence_known or address[0] not in {'T', 'C', 'ST'}))
                if not bit_form:
                    from plc.device_profiles import native_word_value_bits
                    bits = native_word_value_bits(address[0], address[1], plc_model)
                    points = (resolved + bits - 1) // bits if bits else None
            if external and unit == 'bits' and resolved is not None:
                bit_form = definition.data_type in {'bit', 'bool'} or external.get('bit_device', False)
                points = resolved if bit_form else (resolved + 15) // 16
            if resolved == 0 and not plain_word_unknown:
                addresses, points = [], 0
                requirements = []
            elif (resolved is not None and not indexed and not (external and external['indexed'])
                  and not group_sequence_unknown):
                if plain_word_unknown:
                    pass  # No source-established unit for an unselected word head.
                elif address and '.' in head and unit == 'bits' and sequence_known:
                    selected = re.fullmatch(r'(.+)\.([0-9A-F])', head)
                    if selected:
                        bit = int(selected[2], 16)
                        if bit + resolved > 16 and sequence['cross_word'] != 'next_word_bit_zero':
                            requirements.append('word_bit_cross_word_layout_not_established')
                        else:
                            prefix, first = address
                            radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(prefix, plc_model)]
                            candidate = [prefix + format(first + (bit + offset) // 16, radix)
                                         + '.' + format((bit + offset) % 16, 'X')
                                         for offset in range(resolved)]
                            if all(parse_device_address(device, plc_model) is not None for device in candidate):
                                addresses = candidate
                                physical = {'address_space': prefix, 'start_bit': first * 16 + bit,
                                            'bit_count': resolved}
                            else:
                                requirements.append('storage_region_exceeds_device_range')
                    else:
                        requirements.append('word_bit_sequence_layout')
                elif address and '.' not in head and points is not None:
                    prefix, first = address
                    radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(prefix, plc_model)]
                    candidate = [prefix + format(first + offset, radix) for offset in range(points)]
                    if all(parse_device_address(device, plc_model) is not None for device in candidate):
                        for layout in matching_word_layouts:
                            if any(native_word_value_bits(*parse_device_address(device, plc_model), plc_model)
                                   != layout['device_unit_bits'] for device in candidate):
                                unmatched_word_layout = layout
                                requirements.append('source_region_device_unit_not_established')
                                resolved = None
                                break
                        else:
                            addresses = candidate
                    else:
                        requirements.append('storage_region_exceeds_device_range')
                elif external and points is not None:
                    if 'bit_selector' in external:
                        if unit == 'bits' and resolved == 1:
                            addresses = [external['base']]
                        else:
                            requirements.append('word_bit_sequence_layout')
                    else:
                        candidate = [external['prefix'] + '\\' + external['device_prefix']
                                     + format(external['address'] + offset, 'X' if external['radix'] == 16 else 'd')
                                     for offset in range(points)]
                        if all(external_operand_reference(device, plc_model) is not None for device in candidate):
                            addresses = candidate
                        else:
                            requirements.append('storage_region_exceeds_device_range')
                elif re.fullmatch(r'K[1-8][A-Z].*', head):
                    requirements.append('digit_group_sequence_layout')
                elif not re.fullmatch(r'[KH][+-]?[0-9A-F]+', head):
                    requirements.append('storage_head_not_resolved')
                elif points is not None and points > 1:
                    requirements.append('literal_source_extension_not_defined')
                else:
                    addresses = []
            region = {'operand_index': index, 'head': head,
                      'access': 'read' if definition.role.value == 'control' else definition.role.value,
                      'unit': unit, 'count': copy.deepcopy(count),
                      'dependencies': sorted(dependencies)}
            if unmatched_word_layout is not None:
                region['source_layout'] = copy.deepcopy(unmatched_word_layout)
            if resolved is not None and not plain_word_unknown:
                region['resolved_count'] = resolved
            if addresses is not None and len(claims) == 1:
                region['addresses'] = addresses
                # Only resolved, unindexed storage gets a shared physical view.
                # T/ST/C coil and current-value storage are different views;
                # no physical alias mapping between them is established here.
                if not requirements and resolved is not None:
                    if physical is None and address and '.' not in head and address[0] not in {'T', 'ST', 'C'}:
                        prefix, first = address
                        bit_device = is_bit_device(head, plc_model)
                        if unit == 'words' and not bit_device or unit == 'bits' and bit_device:
                            width = 1 if bit_device else 16
                            physical = {'address_space': prefix, 'start_bit': first * width,
                                        'bit_count': resolved * width}
                    if physical is not None:
                        region['physical_bit_region'] = physical
            else:
                region['requires'] = sorted(set(requirements or ['conditional_storage_layout']))
            if region not in regions:
                regions.append(region)
        for parameter in descriptions:
            for conditional in [*_conditional_string_tail_regions(parameter, index, head, plc_model),
                                *_conditional_string_byte_regions(parameter, index, head, plc_model)]:
                if conditional not in regions:
                    regions.append(conditional)
    return regions



def _declared_parameter_span_regions(spec, opcode, operands, plc_model, index, parameter):
    """Bind reviewed word/bit extents to the existing expression/region path.

    These counts describe storage points, independently of the scalar head's
    value width. A missing count cannot turn a head into a complete payload.
    """
    from plc.validation import parse_indexed_device_address
    span = parameter['region_span']
    head = str(operands[index]).strip().upper()
    indexed = parse_indexed_device_address(head, plc_model)
    unresolved = {'operand_index': index, 'head': head,
                  'access': 'write' if parameter.get('direction') == 'output' else 'read',
                  'unit': 'bits' if isinstance(span, Mapping) and span.get('unit') == 'bit' else 'words',
                  'count': None, 'dependencies': [indexed['index_text']] if indexed else [],
                  'requires': ['source_region_span_not_resolved'], 'source_layout': copy.deepcopy(span)}
    integer = {'kind': 'int', 'bits': 32, 'signed': False}
    names = {name.upper(): name for name in spec.native_operand_order}
    def count_parameter(name):
        if isinstance(name, str) and name.upper() in names:
            return {'op': 'parameter', 'type': integer, 'name': names[name.upper()]}
        return None
    count = None
    if isinstance(span, Mapping):
        count = count_parameter(span.get('count_operand'))
        expression = span.get('count_expression')
        if count is None and isinstance(expression, Mapping) and expression.get('operator') == 'multiply':
            parts = expression.get('operands')
            if isinstance(parts, list) and len(parts) == 2:
                args = []
                for part in parts:
                    if type(part) is int and 0 <= part <= 65536:
                        args.append({'op': 'constant', 'type': integer, 'value': part})
                    elif isinstance(part, Mapping) and set(part) == {'parameter'}:
                        args.append(count_parameter(part['parameter']))
                    else:
                        args.append(None)
                if all(arg is not None for arg in args):
                    count = {'op': 'mul', 'type': integer, 'args': args}
    if count is None or span.get('unit') not in {'word', 'bit'}:
        return [unresolved]
    if span['unit'] == 'word' and 'device_unit_bits' in span:
        from plc.validation import parse_device_address
        from plc.device_profiles import native_word_value_bits
        address = parse_device_address(indexed['base_text'] if indexed else head, plc_model)
        if address and native_word_value_bits(*address, plc_model) != span['device_unit_bits']:
            # A count of 16-bit registers does not establish the number of
            # native 32-bit counters occupied by the same instruction.
            return [{**unresolved, 'count': copy.deepcopy(count),
                     'requires': ['source_region_device_unit_not_established']}]
    concrete = copy.deepcopy(parameter)
    concrete.pop('region_span')
    if span['unit'] == 'word' and isinstance(concrete.get('word_device_layout'), Mapping):
        concrete['word_device_layout'] = {**concrete['word_device_layout'],
            'word_count': count, 'head_storage_only': False, 'single_scalar': False}
    elif span['unit'] == 'bit' and 'occupied_bit_count' in concrete:
        concrete['occupied_bit_count'] = count
    else:
        return [unresolved]
    parameters = [[] for _ in operands]
    parameters[index] = [concrete]
    projected = instruction_operand_regions(spec, opcode, operands, plc_model, parameters=parameters)
    return [{**region, 'source_layout': copy.deepcopy(span)} for region in projected] or [unresolved]


def _remote_identity_region(spec, operands, index, parameter):
    """Preserve another CPU's namespace without assigning local storage."""
    target = parameter['selector_CPU_parameter']
    target_index = next((i for i, name in enumerate(spec.native_operand_order)
                         if name.upper() == target.upper()), None)
    token = str(operands[index]).strip()
    requires = ['remote_CPU_context_not_established', 'remote_device_range_not_established']
    if token.startswith(('"', "'")) and parameter.get('quoted_device_grammar_verified') is not True:
        requires.append('quoted_remote_device_grammar_not_established')
    return {'operand_index': index, 'head': token, 'address_space': 'remote_CPU',
            'target_CPU_parameter': target,
            'target_CPU_token': str(operands[target_index]).strip() if target_index is not None else None,
            'access': parameter['remote_access'], 'unit': 'devices', 'count': None,
            'dependencies': [], 'requires': requires}


def _transfer_payload_regions(spec, operands, plc_model, index, parameter):
    """A control-record value names the transfer extent; it is not a literal n."""
    from plc.validation import parse_device_address, device_address_radix
    layout = parameter.get('transfer_layout')
    source = layout.get('word_count_source') if isinstance(layout, Mapping) else None
    head = str(operands[index]).strip().upper()
    result = {'operand_index': index, 'head': head,
              'access': 'write' if parameter.get('direction') == 'output' else 'read',
              'unit': 'words', 'count': None, 'dependencies': [],
              'source_layout': copy.deepcopy(layout), 'requires': ['transfer_word_count_value_not_established']}
    if (not isinstance(source, Mapping) or not isinstance(source.get('source_parameter'), str)
            or type(source.get('offset')) is not int or source['offset'] < 0
            or layout.get('unit') != 'words' or layout.get('word_stride') != 1):
        result['requires'].append('transfer_count_source_not_resolved')
        return [result]
    name, offset = source['source_parameter'], source['offset']
    source_index = next((i for i, role in enumerate(spec.native_operand_order) if role.upper() == name.upper()), None)
    integer = {'kind': 'int', 'bits': 16, 'signed': False}
    result['count'] = {'op': 'read', 'type': integer, 'args': [
        {'op': 'parameter', 'name': name, 'type': {'kind': 'opaque'}},
        {'op': 'constant', 'value': offset, 'type': integer}]}
    address = parse_device_address(str(operands[source_index]).strip().upper(), plc_model) if source_index is not None else None
    if address:
        radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(address[0], plc_model)]
        count_device = address[0] + format(address[1] + offset, radix)
        if parse_device_address(count_device, plc_model) is not None:
            result['dependencies'] = [count_device]
            result['requires'].append('device_value:' + count_device)
        else:
            result['requires'].append('transfer_count_source_exceeds_device_range')
    else:
        result['requires'].append('transfer_count_source_not_resolved')
    return [result]


def _conditional_head_payload_regions(spec, opcode, operands, plc_model, index, parameter):
    """Project reviewed byte-mode payloads without treating their heads as spans."""
    from plc.validation import parse_device_address, parse_indexed_device_address, is_bit_device
    head = str(operands[index]).strip().upper()
    indexed = parse_indexed_device_address(head, plc_model)
    base_dependencies = {indexed['index_text']} if indexed else set()
    access = 'write' if parameter.get('direction') == 'output' else 'read'
    key = 'conditional_write_layout' if access == 'write' else 'conditional_read_layout'
    layout = parameter.get(key)
    unresolved = {'operand_index': index, 'head': head, 'access': access, 'unit': 'words', 'count': None}
    if not isinstance(layout, Mapping) or parameter.get('passing') != 'device_reference':
        return [{**unresolved, 'dependencies': sorted(base_dependencies),
                 'requires': ['head_payload_span_not_established']}]
    selector = layout.get('selector')
    if (not isinstance(selector, str) or parse_device_address(selector, plc_model) is None
            or not is_bit_device(selector, plc_model)):
        return [{**unresolved, 'dependencies': sorted(base_dependencies), 'source_layout': copy.deepcopy(layout),
                 'requires': ['conditional_layout_selector_not_resolved']}]
    results = []
    for state in ('OFF', 'ON'):
        branch = layout.get(state)
        condition = {'context': 'device_state', 'device': selector, 'equals': state == 'ON'}
        requirements = {'device_value:' + selector}
        if indexed:
            requirements.add('indexed_effective_address')
        if (not isinstance(branch, Mapping) or 'word_count' not in branch
                or branch.get('general_exact_span_verified') is False
                or branch.get('word_stride', 1) != 1):
            requirements.add('conditional_source_span_not_established' if access == 'read'
                             else 'conditional_destination_span_not_established')
            projected = [dict(unresolved)]
        else:
            # Reuse the existing expression, device grammar and extent checks.
            # Only the source's mode word_count replaces the head-only layout.
            concrete = copy.deepcopy(parameter)
            concrete.pop('conditional_read_layout', None)
            concrete.pop('conditional_write_layout', None)
            concrete['word_device_layout'] = {**concrete['word_device_layout'],
                'word_count': copy.deepcopy(branch['word_count']), 'head_storage_only': False,
                'single_scalar': False}
            projected_parameters = [[] for _ in operands]
            projected_parameters[index] = [concrete]
            projected = instruction_operand_regions(spec, opcode, operands, plc_model,
                                                    parameters=projected_parameters)
            if not projected:
                requirements.add('conditional_payload_span_not_resolved')
                projected = [dict(unresolved)]
        for region in projected:
            pending = set(region.get('requires', [])) | requirements
            if 'storage_region_exceeds_device_range' in pending:
                pending.remove('storage_region_exceeds_device_range')
                pending.add('conditional_storage_region_exceeds_device_range')
            if isinstance(branch, Mapping) and branch.get('general_destination_upper_byte_policy_verified') is False:
                pending.add('destination_upper_byte_policy_not_established')
            results.append({**region, 'condition': condition, 'source_layout': copy.deepcopy(branch),
                'dependencies': sorted(set(region.get('dependencies', [])) | base_dependencies | {selector}),
                'requires': sorted(pending)})
    return results


def _conditional_string_tail_regions(parameter, index, head, plc_model):
    fixed = _conditional_string_tail_region(parameter, index, head, plc_model)
    if fixed is not None:
        return [fixed]
    tail = parameter.get('conditional_tail_write')
    if not isinstance(tail, Mapping) or tail.get('word_offset_established') is not False:
        return []
    conflict = tail.get('source_conflict')
    candidates = []
    if isinstance(conflict, Mapping):
        for key in ('printed_prose_word_offset', 'own_diagram_word_offset'):
            offset = conflict.get(key)
            if type(offset) is int and offset >= 0 and offset not in candidates:
                candidates.append(offset)
    regions = []
    for offset in candidates:
        candidate = copy.deepcopy(parameter)
        candidate['conditional_tail_write']['word_offset'] = offset
        region = _conditional_string_tail_region(candidate, index, head, plc_model, offset_candidate=True)
        if region is not None:
            region.pop('physical_bit_region', None)
            region.update(candidate=True, source_conflict=copy.deepcopy(conflict))
            region['requires'] = sorted(set(region.get('requires', [])) | {'source_tail_offset_conflict'})
            regions.append(region)
    return regions


def _conditional_string_tail_region(parameter, index, head, plc_model, *, offset_candidate=False):
    """Keep a sourced optional string terminator separate from its data."""
    from plc.validation import parse_device_address, parse_indexed_device_address, device_address_radix, is_bit_device
    layout, tail = parameter.get('word_device_layout'), parameter.get('conditional_tail_write')
    if (parameter.get('type') != 'STRING' or parameter.get('direction') != 'output'
            or parameter.get('passing') != 'device_reference'
            or not isinstance(layout, Mapping) or layout.get('device_unit_bits') != 16
            or layout.get('element_bits') != 8 or type(layout.get('word_count')) is not int
            or not isinstance(tail, Mapping) or type(tail.get('word_offset')) is not int
            or (tail['word_offset'] != layout['word_count'] and not offset_candidate) or tail['word_offset'] < 0
            or tail.get('current_selector_value_verified') is not False):
        return None
    selector = tail.get('selector')
    if not isinstance(selector, str) or parse_device_address(selector, plc_model) is None or not is_bit_device(selector, plc_model):
        return None
    active = [state for state in ('ON', 'OFF') if tail.get(state) == {'word_count': 1, 'value': 0}
              and tail.get('OFF' if state == 'ON' else 'ON') == 'unchanged']
    if len(active) != 1:
        return None
    indexed = parse_indexed_device_address(head, plc_model)
    address = parse_device_address(indexed['base_text'] if indexed else head, plc_model)
    dependencies = {selector}
    requires = {'device_value:' + selector}
    result = {'operand_index': index, 'head': head, 'access': 'write', 'unit': 'words',
              'offset': tail['word_offset'], 'count': 1, 'resolved_count': 1,
              'condition': {'context': 'device_state', 'device': selector, 'equals': active[0] == 'ON'},
              'value_when_condition': 0}
    if indexed:
        dependencies.add(indexed['index_text'])
        requires.add('indexed_effective_address')
    elif (address is not None and '.' not in head
          and (not is_bit_device(head, plc_model) or address[0] in {'T', 'ST', 'C'})):
        radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(address[0], plc_model)]
        candidate = address[0] + format(address[1] + tail['word_offset'], radix)
        if parse_device_address(candidate, plc_model) is not None:
            result['addresses'] = [candidate]
        else:
            requires.add('conditional_storage_region_exceeds_device_range')
    else:
        requires.add('conditional_storage_head_not_resolved')
    result.update(dependencies=sorted(dependencies), requires=sorted(requires))
    return result


def _conditional_string_byte_regions(parameter, index, head, plc_model):
    """Keep source-declared padding bytes inside their occupied word span."""
    from plc.validation import parse_device_address, parse_indexed_device_address, device_address_radix, is_bit_device
    layout, byte = parameter.get('word_device_layout'), parameter.get('conditional_byte_write')
    if (parameter.get('type') != 'STRING' or parameter.get('direction') != 'output'
            or parameter.get('passing') != 'device_reference'
            or not isinstance(layout, Mapping) or layout.get('device_unit_bits') != 16
            or layout.get('element_bits') != 8 or type(layout.get('word_count')) is not int
            or not isinstance(byte, Mapping) or type(byte.get('word_offset')) is not int
            or not 0 <= byte['word_offset'] < layout['word_count']
            or byte.get('bit_offset') not in {0, 8} or byte.get('value_bits') != 8
            or byte.get('current_selector_value_verified') is not False):
        return []
    selector = byte.get('selector')
    if not isinstance(selector, str) or parse_device_address(selector, plc_model) is None or not is_bit_device(selector, plc_model):
        return []
    indexed = parse_indexed_device_address(head, plc_model)
    address = parse_device_address(indexed['base_text'] if indexed else head, plc_model)
    results = []
    for state in ('OFF', 'ON'):
        branch = byte.get(state)
        region = {'operand_index': index, 'head': head, 'access': 'write', 'unit': 'bits',
                  'offset': byte['word_offset'], 'bit_offset': byte['bit_offset'], 'value_bits': 8,
                  'count': 8, 'resolved_count': 8,
                  'condition': {'context': 'device_state', 'device': selector, 'equals': state == 'ON'}}
        dependencies, requires = {selector}, {'device_value:' + selector}
        if isinstance(branch, Mapping) and type(branch.get('value')) is int and 0 <= branch['value'] <= 255:
            region['value_when_condition'] = branch['value']
        else:
            requires.add('conditional_byte_value_not_established')
        if indexed:
            dependencies.add(indexed['index_text'])
            requires.add('indexed_effective_address')
        elif (address is not None and '.' not in head
              and (not is_bit_device(head, plc_model) or address[0] in {'T', 'ST', 'C'})):
            radix = {8: 'o', 10: 'd', 16: 'X'}[device_address_radix(address[0], plc_model)]
            target_number = address[1] + byte['word_offset']
            target = address[0] + format(target_number, radix)
            if parse_device_address(target, plc_model) is not None:
                region['addresses'] = [target]
                if address[0] not in {'T', 'ST', 'C'}:
                    region['physical_bit_region'] = {'address_space': address[0],
                        'start_bit': target_number * 16 + byte['bit_offset'], 'bit_count': 8}
            else:
                requires.add('conditional_storage_region_exceeds_device_range')
        else:
            requires.add('conditional_storage_head_not_resolved')
        region.update(dependencies=sorted(dependencies), requires=sorted(requires))
        results.append(region)
    return results


def canonical_expression(value):
    if not isinstance(value, str):
        return value
    # Only whole whitespace-delimited operands. In particular quoted strings,
    # K/H constants, bit-group syntax and unrelated labels stay untouched.
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\S+',
                  lambda m: m[0] if m[0].startswith(('"', "'")) else canonical_operand(m[0]), value)


def canonical_device_map(values):
    """Coalesce aliases using the exporter's established first-declaration rule.

    An explicitly empty declaration stays empty. Conflicting source comments
    remain in the immutable source/response audit; they cannot create multiple
    physical devices or override the first declaration through another alias.
    """
    if not isinstance(values, Mapping):
        return copy.deepcopy(values)
    result = {}
    for key, value in values.items():
        identity = canonical_device(key)
        if identity not in result:
            result[identity] = copy.deepcopy(value)
    return result


def canonical_io_rows(rows):
    """Merge zero-padding aliases, retaining row metadata and the chosen label.

    Exact duplicate rows still reach the draft validator before canonicalization.
    This adapter does not treat different physical addresses as interchangeable.
    """
    if not isinstance(rows, list):
        return copy.deepcopy(rows)
    groups, order = {}, []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("address"):
            order.append((False, copy.deepcopy(row)))
            continue
        address = canonical_device(row["address"])
        if address not in groups:
            order.append((True, address))
            groups[address] = []
        groups[address].append(row)
    result = []
    for grouped, entry in order:
        if not grouped:
            result.append(entry)
            continue
        candidates = sorted(groups[entry], key=lambda r: (
            str(r.get("source", "")).casefold() not in {"user", "confirmed"},
            str(r["address"]).strip().upper() != entry,
            not bool(r.get("label")), str(r["address"]),
        ))
        merged = copy.deepcopy(dict(candidates[0]))
        for row in candidates[1:]:
            for key, value in row.items():
                merged.setdefault(key, copy.deepcopy(value))
        merged["address"] = entry
        result.append(merged)
    return result


def canonical_ladder_devices(ladder):
    """Return a copy with consistent address/comment spelling, including ORs."""
    result = copy.deepcopy(ladder)
    if not isinstance(result, dict):
        return result
    if "device_comments" in result:
        result["device_comments"] = canonical_device_map(result["device_comments"])

    def element(item):
        if not isinstance(item, dict):
            return
        if "address" in item:
            item["address"] = canonical_operand(item["address"])
        if item.get("type") in {"COMPARE", "BLOCK_INPUT", "BLOCK_OUTPUT"} and "expression" in item:
            item["expression"] = canonical_expression(item["expression"])
        if item.get("type") in {"TIMER", "COUNTER"} and "value" in item:
            item["value"] = canonical_operand(item["value"])
        if item.get("type") == "APP_INSTR" and isinstance(item.get("operands"), list):
            item["operands"] = [canonical_operand(v) for v in item["operands"]]
        if item.get("type") == "parallel_block" and isinstance(item.get("branches"), list):
            for branch in item["branches"]:
                if isinstance(branch, list):
                    for child in branch:
                        element(child)

    rows = result.get("rungs")
    for rung in rows if isinstance(rows, list) else ():
        if not isinstance(rung, dict):
            continue
        element(rung.get("header_element"))
        shared = rung.get("shared_inputs")
        for item in shared if isinstance(shared, list) else ():
            element(item)
        branches = rung.get("branches")
        for branch in branches if isinstance(branches, list) else ():
            if not isinstance(branch, dict):
                continue
            for field in ("inputs", "outputs"):
                items = branch.get(field)
                for item in items if isinstance(items, list) else ():
                    element(item)
    return result


def canonical_requirement_devices(requirements):
    """Normalize typed requirement device lists, not their IDs or prose."""
    result = copy.deepcopy(requirements)
    if isinstance(result, (list, tuple)):
        for requirement in result:
            if isinstance(requirement, dict) and isinstance(requirement.get("devices"), list):
                requirement["devices"] = list(dict.fromkeys(canonical_device(v) for v in requirement["devices"]))
    return result


def canonical_analysis_devices(config):
    """Normalize address-bearing fields of an already-normalized analysis config."""
    result = copy.deepcopy(config)
    if not isinstance(result, dict):
        return result
    for pair in result.get("mutex", []) or []:
        if isinstance(pair, dict) and isinstance(pair.get("devices"), list):
            pair["devices"] = [canonical_device(v) for v in pair["devices"]]
    for expectation in result.get("same_scan_expectations", []) or []:
        if isinstance(expectation, dict) and "device" in expectation:
            expectation["device"] = canonical_device(expectation["device"])
    if isinstance(result.get("terminal_states"), Mapping):
        result["terminal_states"] = canonical_device_map(result["terminal_states"])
    return result

"""Shared, source-scoped constraints for retrieval, static checks and execution.

Only reviewed formal predicates are executable. Missing values, scope context,
dependencies and unparsed qualifications remain explicit unknowns.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping

from plc.instruction_definition import (
    DefinitionError, Expression, InstructionFactGroup, UnknownInstructionSemantics, ValueType,
    evaluate_expression,
)

UNKNOWN = object()


def fx_out_preset_error(opcode, operands):
    """Return the existing FX timer/counter source-arity error, if present.

    OUT's registry shape also covers ordinary coils. The saved T/C source
    form needs its separate preset, even when a draft retains its incomplete
    one-operand record. This rule establishes no CPU instruction admission.
    """
    if (str(opcode).upper() == 'OUT' and len(operands) == 1
            and isinstance(operands[0], str)
            and re.fullmatch(r'[TC][0-9]+(?:[ZV][0-7])?', operands[0], re.I)):
        return 'timer/counter OUT requires its observed preset operand'
    return None


def _stale_report(target):
    return {'status': 'unverified', 'target': target, 'checks': [], 'findings': [
        {'code': 'PLC_FACT_CONTEXT', 'severity': 'unverified',
         'message': 'fact_resource_changed_reload_context'}]}


def _cpu_members(model):
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(model)
    return tuple(profile.cpu.upper().split('/')) if profile and model == profile.cpu.upper() else (model,)


def _combined_reports(reports, *, target, model):
    statuses = {report['status'] for report in reports}
    return {'status': 'failed' if 'failed' in statuses else 'unverified' if 'unverified' in statuses else 'passed',
            'target': target, 'cpu_menu': model,
            'checks': [row for report in reports for row in report['checks']],
            'findings': [row for report in reports for row in report['findings']]}


def _context_value(context, path):
    value = context
    for part in path.split('.'):
        if not isinstance(value, Mapping) or part not in value:
            return UNKNOWN
        value = value[part]
    return value


def _condition(condition, values, context):
    if not isinstance(condition, Mapping):
        raise UnknownInstructionSemantics('condition_not_formalized')
    if set(condition) == {'context', 'equals'}:
        actual = _context_value(context, condition['context'])
        if actual is UNKNOWN or actual is None:
            raise UnknownInstructionSemantics('condition_context_missing:' + condition['context'])
        return type(actual) is type(condition['equals']) and actual == condition['equals']
    expression = condition.get('predicate', condition)
    parsed = Expression.from_mapping(expression)
    if parsed.value_type.kind != 'bool':
        raise DefinitionError('Constraint condition must be Boolean')
    return evaluate_expression(parsed, values, state=context, memory_reader=context.get('memory_reader'))


def _has_nonfinite_parameter(expression, values):
    parsed = Expression.from_mapping(expression)
    def visit(node):
        if node.op == 'parameter' and node.value_type.kind == 'real':
            value = values.get(node.name.upper(), UNKNOWN)
            return type(value) is float and not math.isfinite(value)
        return any(visit(child) for child in node.args)
    return visit(parsed)


def _scope(group, *, target, model, language, version):
    unknown = False
    for key, value in (('models', model), ('forms', target), ('languages', language), ('versions', version)):
        choices = (group.scope or {}).get(key)
        if not choices:
            continue
        if value is None:
            unknown = True
        elif value not in choices:
            return 'not_applicable'
    return 'unverified' if unknown else 'passed'


def _address(token, model):
    from plc.validation import parse_device_address, device_address_radix
    if not isinstance(token, str) or re.search(r'[VZ]\d+$|\[', token, re.I):
        return None
    address = parse_device_address(token, model)
    if address is not None:
        return address
    # An address relation can be checked for a catalogued, unadmitted device.
    # This decomposition grants neither CPU address support nor writability.
    from plc.device_identity import reference_device_parts
    parts = reference_device_parts(token)
    if parts is None:
        return None
    radix = device_address_radix(parts[0], model)
    if radix is None and parts[0] not in {'X', 'Y', 'B', 'W', 'SB', 'SW', 'DX', 'DY'} and parts[1].isdecimal():
        radix = 10  # The shared reference lexer declares these spellings decimal.
    if radix is None:
        return None
    try:
        return parts[0], int(parts[1], radix)
    except ValueError:
        return None


def _additional_constraint(dimension, rule, values, context, model):
    """Evaluate source-bound configuration/memory rules without parsing prose."""
    if dimension == 'constraints.context_domain':
        actual = _context_value(context, rule['path'])
        if actual is UNKNOWN or actual not in rule['allowed']:
            return 'unverified', rule['rule']
        return 'passed', ''
    tokens = values.get('$OPERANDS', ())
    def operand(index):
        return tokens[index] if index < len(tokens) else None
    def number(index):
        value = values.get('$' + str(index), UNKNOWN)
        if type(value) not in {int, float} or not math.isfinite(value):
            raise UnknownInstructionSemantics('constraint_input_unknown')
        return value
    def read(base, offset=0):
        if context.get('consume_step'):
            context['consume_step']()
        reader = context.get('memory_reader')
        if base is None or reader is None:
            raise UnknownInstructionSemantics('memory_parameter_values_required')
        value = reader(base, offset)
        if value is None or value is UNKNOWN:
            raise UnknownInstructionSemantics('memory_parameter_value_unknown:' + str(base))
        return value
    def requirement(base, item):
        value = _context_value(base, item['path'])
        if value is UNKNOWN or value is None:
            raise UnknownInstructionSemantics('context_value_missing:' + item['path'])
        return value == item['equals'] if 'equals' in item else value != item['not_equals']
    def region(token, count, unit='word'):
        address = _address(token, model)
        if address is None or type(count) is not int or count < 0:
            raise UnknownInstructionSemantics('operand_region_binding_required')
        prefix, first = address
        return prefix, first, first + count
    def overlaps(left, right):
        return left[0] == right[0] and left[1] < right[2] and right[1] < left[2]
    try:
        if dimension == 'constraints.cpu_availability':
            state = rule.get('model_states', {}).get(model)
            if state == 'unsupported':
                return 'failed', rule['rule']
            if state == 'supported':
                return 'passed', ''
            if state == 'conditional':
                raise UnknownInstructionSemantics('CPU_firmware_or_header_condition_required')
            raise UnknownInstructionSemantics('CPU_availability_not_in_source_scope')
        if dimension == 'constraints.context':
            base = _context_value(context, rule['context'])
            if not isinstance(base, Mapping):
                raise UnknownInstructionSemantics('context_value_missing:' + rule['context'])
            if not all(requirement(base, item) for item in rule.get('requirements', ())):
                return 'failed', rule['rule']
            for item in rule.get('conditional_requirements', ()):
                if requirement(base, item['when']) and not requirement(base, item):
                    return 'failed', rule['rule']
            return 'passed', ''
        if dimension == 'constraints.device_context':
            mapping = _context_value(context, rule['context'])
            if not isinstance(mapping, Mapping) or operand(rule['operand_index']) is None:
                raise UnknownInstructionSemantics('device_context_required:' + rule['context'])
            from plc.device_identity import canonical_reference_device
            mapping = {canonical_reference_device(k): v for k, v in mapping.items()}
            return ('passed', '') if mapping.get(canonical_reference_device(operand(rule['operand_index']))) else ('failed', rule['rule'])
        if dimension == 'constraints.channel':
            channel = number(rule['operand_index'])
            if channel not in rule.get('model_channels', {}).get(model, rule['allowed']):
                return 'failed', rule['rule']
            conditional = rule.get('conditional_model_channels', {}).get(model)
            if conditional and channel in conditional['excluded_channels']:
                point_count = _context_value(context, conditional['context'])
                if point_count is UNKNOWN:
                    raise UnknownInstructionSemantics('context_value_missing:' + conditional['context'])
                if point_count in conditional['excluded_values']:
                    return 'failed', rule['rule']
            return 'passed', ''
        if dimension == 'constraints.disjoint_regions':
            source = region(operand(rule['source_operand_index']), int(number(rule['source_length_operand_index']))
                            * rule.get('source_length_multiplier', 1))
            destination = region(operand(rule['destination_operand_index']), int(number(rule['destination_length_operand_index']))
                                 * rule.get('destination_length_multiplier', 1))
            return ('failed', rule['rule']) if overlaps(source, destination) else ('passed', '')
        if dimension == 'constraints.string_memory':
            if 'when_operand' in rule and number(rule['when_operand']) == 0:
                return 'not_applicable', ''
            maximum = rule.get('maximum_bytes', 4096)
            if 'type_operand' in rule:
                data_type = read(operand(rule['type_operand']), rule.get('type_offset', 0))
                maximum = rule['type_maximum_bytes'].get(str(data_type))
                if maximum is None:
                    return 'not_applicable', ''
            allowed = rule.get('subsequent_bytes_allowed')
            # One extra byte distinguishes a full-length terminated string
            # from a truncated or over-capacity buffer. Never assume zero fill.
            for index in range(maximum + 1):
                word = read(operand(rule['operand_index']), index // 2)
                if type(word) is not int:
                    raise UnknownInstructionSemantics('string_memory_word_type_unknown')
                byte = (word >> (8 * (index % 2))) & 255
                if byte == 0:
                    return ('failed', rule['rule']) if index == 0 and rule.get('first_byte_allowed') else ('passed', '')
                if index == maximum:
                    return ('failed', rule['rule']) if 'maximum_bytes' in rule or 'type_maximum_bytes' in rule else (
                        'unverified', 'local_constraint_memory_read_limit')
                if index == 0 and 'first_byte_allowed' in rule and byte not in rule['first_byte_allowed']:
                    return 'failed', rule['rule']
                if index > 0 and allowed is not None and byte not in allowed:
                    return 'failed', rule['rule']
                if rule.get('allowed_ASCII') == 'CF_DOS_filename' and not (
                    48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122
                    or chr(byte) in "!#$%&'()+-@^_`~"):
                    return 'failed', rule['rule']
            return 'failed', rule['rule']
        if dimension == 'constraints.motion_interrupt':
            output = _address(operand(rule['output_index']), model)
            if output is None or output[0] != 'Y' or not 0 <= output[1] <= 3:
                raise UnknownInstructionSemantics('pulse_output_binding_required')
            enabled = read(rule['enable_device'])
            if not enabled:
                return 'not_applicable', ''
            digit = (int(read(rule['selection_register'])) & 65535) >> (output[1] * rule['bits_per_output']) & 15
            if digit in rule['forbidden_digits']:
                return 'failed', rule['rule']
            minimum = rule['user_command_minimum_firmware'] if digit == rule['user_command_digit'] else rule['selection_minimum_firmware'].get(model)
            if minimum:
                firmware = _context_value(context, 'hardware.firmware_version')
                if firmware is UNKNOWN or not re.fullmatch(r'\d+(?:\.\d+)*', str(firmware)):
                    raise UnknownInstructionSemantics('CPU_firmware_version_required')
                if tuple(map(int, str(firmware).split('.'))) < tuple(map(int, minimum.split('.'))):
                    return 'failed', rule['rule']
            return 'passed', ''
        if dimension == 'constraints.startup_state':
            ranges = _context_value(context, 'project.retained_device_ranges')
            if ranges is UNKNOWN:
                raise UnknownInstructionSemantics('retained_device_ranges_required')
            own = region(operand(rule['operand_index']), 1)
            retained = any(overlaps(own, region(item['head'], item['count'])) for item in ranges)
            if not retained:
                return 'not_applicable', ''
            if context.get('initial_scan') is False:
                return 'not_applicable', ''
            if context.get('initial_scan') is not True:
                raise UnknownInstructionSemantics('RUN_startup_context_required')
            return ('passed', '') if read(operand(rule['operand_index'])) == rule['initial_value'] else ('failed', rule['rule'])
        if dimension in {'constraints.reserved_regions', 'constraints.instance_regions'}:
            uses = context.get('program_accesses')
            if uses is None or context.get('current_instruction') is None:
                raise UnknownInstructionSemantics('program_resource_context_required')
            for item in rule['regions']:
                count = item.get('count')
                if 'count_by_operand' in item:
                    bound = item['count_by_operand']
                    count = bound['below'] if number(bound['index']) < bound['threshold'] else bound['at_or_above']
                if 'count_from_control' in item:
                    bound = item['count_from_control']
                    control = read(operand(item['operand_index']), bound['offset'])
                    count = (bound['limit_cycle_count'] if control & bound['limit_cycle_mask'] else
                             bound['extended_count'] if control & bound['extended_mask'] else bound['base_count'])
                own = region(operand(item['operand_index']), count, item['unit'])
                for use in uses:
                    if use['id'] == context['current_instruction']:
                        continue
                    if dimension == 'constraints.instance_regions' and use['opcode'] not in rule['family']:
                        continue
                    accesses = use['writes'] if item.get('access', 'write') == 'write' else use.get('configured_uses', [])
                    for token in accesses:
                        if overlaps(own, region(token, 1)):
                            return 'failed', rule['rule']
            if context.get('program_accesses_complete') is False:
                raise UnknownInstructionSemantics('program_resource_context_incomplete')
            return 'passed', ''
        if dimension == 'constraints.input_resources':
            selections = [operand(index) for index in rule.get('operand_indexes', ())]
            if rule.get('source') == 'DVIT_selection':
                output = _address(operand(rule['output_index']), model)
                if output is None or output[0] != 'Y' or not 0 <= output[1] <= 3:
                    raise UnknownInstructionSemantics('pulse_output_binding_required')
                digit = ((int(read(rule['selection_register'])) & 65535) >> (output[1] * 4) & 15
                         if read(rule['enable_device']) else output[1])
                if digit in {8, 15}:
                    return 'not_applicable', ''
                if digit > 7:
                    return 'failed', rule['rule']
                selections = ['X' + format(digit, 'o')]
            if any(token is None for token in selections):
                raise UnknownInstructionSemantics('high_speed_input_binding_required')
            uses = _context_value(context, 'hardware.input_functions')
            if not isinstance(uses, Mapping):
                raise UnknownInstructionSemantics('configured_input_function_context_required')
            for token in selections:
                address = _address(token, model)
                if address is None:
                    raise UnknownInstructionSemantics('high_speed_input_binding_required')
                if rule.get('high_speed_only') and address[1] > (5 if model == 'FX3S' else 7):
                    continue
                from plc.device_identity import canonical_reference_device
                functions = uses.get(canonical_reference_device(token), ())
                if set(functions) & set(rule['conflicting_functions']):
                    return 'failed', rule['rule']
            return 'passed', ''
    except (DefinitionError, ValueError, KeyError, IndexError, TypeError, OverflowError) as error:
        return 'unverified', str(error)
    return None


def check_fact_constraints(groups, values, *, target, plc_model, language=None, version=None,
                           context=None, phase='input'):
    """Evaluate applicable constraint roots with their complete fact closure."""
    target, plc_model = str(target).strip().upper(), str(plc_model).strip().upper()
    language = (str(language).strip().casefold() or None) if language is not None else None
    phase = str(phase).strip().casefold()
    if phase not in {'input', 'result'}:
        raise DefinitionError('Unknown constraint phase')
    values = {str(k).upper(): v for k, v in values.items() if v is not UNKNOWN and v is not None}
    context = {'numeric_engine': 'cpu_manual', **(context or {})}
    by_id = {}
    for raw in groups:
        group = raw if isinstance(raw, InstructionFactGroup) else InstructionFactGroup.from_mapping(raw)
        if group.id in by_id:
            raise DefinitionError('Duplicate constraint fact identity')
        by_id[group.id] = group
    checks, findings, visiting, cached = [], [], set(), {}

    def outcome(group, status, reason=''):
        row = {'fact_id': group.id, 'dimension': group.dimension, 'status': status, 'reason': reason,
               'target': target, 'phase': phase, 'scope': copy.deepcopy(group.scope or {}),
               'sources': copy.deepcopy(list(group.sources))}
        if status in {'failed', 'unverified'}:
            row.update(code=group.value.get('code', 'PLC_CONSTRAINT'),
                       severity='error' if status == 'failed' else 'unverified',
                       message=reason or group.value.get('rule', group.id))
            for key in ('error_code', 'error_domain'):
                if group.value.get(key):
                    row[key] = group.value[key]
            findings.append(row)
        checks.append(row)
        return status

    def visit(identity):
        if identity in cached:
            return cached[identity]
        group = by_id.get(identity)
        if group is None or identity in visiting or len(visiting) >= 128:
            return 'unverified'
        status = _scope(group, target=target, model=plc_model, language=language, version=version)
        if status != 'passed':
            return outcome(group, status, 'scope_context_missing' if status == 'unverified' else '')
        visiting.add(identity)
        try:
            if group.status != 'source_verified' or group.verification != 'source_checked':
                return outcome(group, 'not_applicable' if group.status == 'not_applicable' else 'unverified',
                               'source_conflict' if group.status == 'conflict' else 'constraint_not_source_checked')
            unknown = False
            if any(source.get('conditions') or source.get('applicability') for source in group.sources):
                return outcome(group, 'unverified', 'source_conditions_require_binding')
            for condition in group.conditions:
                try:
                    if not _condition(condition, values, context):
                        return outcome(group, 'not_applicable')
                except (DefinitionError, TypeError, ValueError):
                    unknown = True
            if unknown:
                return outcome(group, 'unverified', 'condition_not_resolved')
            dependencies = [visit(dependency) for dependency in group.depends_on]
            if any(status != 'passed' for status in dependencies):
                return outcome(group, 'unverified', 'constraint_dependency_unresolved')
            value = group.value
            if not group.dimension.startswith('constraints.'):
                return outcome(group, 'passed')
            if 'predicate' in value:
                try:
                    if _has_nonfinite_parameter(value['predicate'], values):
                        return outcome(group, 'failed', 'non_finite_numeric_value')
                    okay = _condition({'predicate': value['predicate']}, values, context)
                except (DefinitionError, TypeError, ValueError, OverflowError):
                    return outcome(group, 'unverified', 'constraint_input_unknown_or_unrepresentable')
                return outcome(group, 'passed' if okay else 'failed', '' if okay else value.get('rule', 'constraint_violated'))
            additional = _additional_constraint(group.dimension, value, values, context, plc_model)
            if additional is not None:
                return outcome(group, *additional)
            if group.dimension == 'constraints.device_models':
                if plc_model in value['allowed_models']:
                    return outcome(group, 'passed')
                tokens = values.get('$OPERANDS')
                if tokens is None:
                    return outcome(group, 'unverified', 'operand_device_binding_required')
                unknown = False
                for token in tokens:
                    if token is None:
                        unknown = True
                        continue
                    text = re.sub(r'\s+', '', str(token)).upper()
                    kind = ('U_G' if re.fullmatch(r'U[0-9A-F]+\\G\d+', text)
                            else 'R' if re.fullmatch(r'R\d+(?:[VZ]\d+)?', text)
                            else 'D_bit' if re.fullmatch(r'D\d+\.[0-9A-F]+(?:[VZ]\d+)?', text) else None)
                    if kind in value['device_kinds']:
                        return outcome(group, 'failed', value['rule'])
                return outcome(group, 'unverified' if unknown else 'not_applicable',
                               'operand_device_binding_required' if unknown else '')
            if group.dimension == 'constraints.operand_devices':
                tokens = values.get('$OPERANDS')
                positions = value['positions']
                if tokens is None or any(i >= len(tokens) or tokens[i] is None for i in positions):
                    return outcome(group, 'unverified', 'operand_device_binding_required')
                selected = [str(tokens[i]).replace(' ', '').upper() for i in positions]
                digits = [re.fullmatch(r'K([1-8])(?:X|Y|M|S|B|SM)\w+', token) for token in selected]
                kind, okay = value['kind'], True
                if kind == 'same_digit_count':
                    numbers = [int(match[1]) for match in digits if match]
                    okay = len(set(numbers)) <= 1
                elif kind == 'digit_count':
                    okay = all(not match or int(match[1]) in value['digits'] for match in digits)
                elif kind == 'digit_width':
                    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
                    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(target, cpu=plc_model)
                    if form is None:
                        return outcome(group, 'unverified', 'instruction_width_required')
                    maximum = 8 if form.double or form.spec.instruction_width == 32 else 4
                    okay = all(not match or int(match[1]) <= maximum for match in digits)
                elif kind == 'forbidden_device':
                    okay = not any(token in value['devices'] for token in selected)
                elif kind in {'device_range', 'model_device_range'}:
                    from plc.validation import parse_device_address
                    devices = [parse_device_address(token, plc_model) for token in selected]
                    if any(device is None for device in devices):
                        return outcome(group, 'unverified', 'operand_device_binding_required')
                    maximum = value.get('model_maximum', {}).get(plc_model, value['maximum'])
                    okay = all(device[0] == value['prefix'] and value['minimum'] <= device[1] <= maximum
                               for device in devices)
                elif kind == 'digit_alignment':
                    from plc.validation import parse_device_address
                    for token, digit in zip(selected, digits):
                        if not digit:
                            continue
                        device = parse_device_address(token[2:], plc_model)
                        if device is None:
                            return outcome(group, 'unverified', 'operand_device_binding_required')
                        okay &= device[1] % value['alignment'] == 0
                elif kind == 'address_alignment':
                    devices = [_address(token, plc_model) for token in selected]
                    if any(device is None for device in devices):
                        return outcome(group, 'unverified', 'operand_device_binding_required')
                    okay = all((device[0] != value['prefix'] and not value.get('required_prefix'))
                               or device[0] == value['prefix'] and device[1] % value['alignment'] == 0 for device in devices)
                    okay &= all(value.get('minimum', 0) <= device[1] <= value.get('maximum', device[1]) for device in devices)
                elif kind == 'ordered_region':
                    from plc.validation import parse_device_address
                    devices = [parse_device_address(token, plc_model) for token in selected]
                    if any(device is None for device in devices):
                        return outcome(group, 'unverified', 'operand_device_binding_required')
                    a, b = devices
                    # A reversed FX region resets only D1; the source explicitly
                    # defines it, so it is not an illegal operand order.
                    okay = a[0] == b[0]
                    if a[0] == b[0] == 'C':
                        okay &= (a[1] < value['counter_width_split']) == (b[1] < value['counter_width_split'])
                else:
                    return outcome(group, 'unverified', 'operand_device_rule_not_defined')
                return outcome(group, 'passed' if okay else 'failed', '' if okay else value['rule'])
            if group.dimension == 'constraints.numeric_multiple':
                number = values.get('$' + str(value['operand_index']), UNKNOWN)
                if type(number) is not int:
                    return outcome(group, 'unverified', 'constraint_input_unknown')
                okay = number % value['divisor'] == 0
                return outcome(group, 'passed' if okay else 'failed', '' if okay else value['rule'])
            if group.dimension == 'constraints.core_contract' and value.get('checker') == 'pulse_hardware':
                if plc_model != 'FX3U':
                    return outcome(group, 'unverified', 'pulse_hardware_model_profile_required')
                profile = context.get('hardware') or context.get('hardware_profile')
                if not profile:
                    return outcome(group, 'unverified', 'pulse_hardware_context_required')
                tokens = values.get('$OPERANDS')
                if tokens is None or any(token is None for token in tokens):
                    return outcome(group, 'unverified', 'operand_device_binding_required')
                from plc.validation import _validate_fx3u_pulse_hardware, PLCJsonValidationError
                from plc.validation import _profile_output_is_transistor, _profile_output_is_relay, _profile_high_speed_output_adapter_count
                if (not _profile_output_is_transistor(profile) and not _profile_output_is_relay(profile)
                        and not _profile_high_speed_output_adapter_count(profile)):
                    return outcome(group, 'unverified', 'pulse_output_type_or_adapter_required')
                from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
                form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(target, cpu=plc_model)
                if form is None or form.spec.contract_coverage().get('hardware_applicability') != 'source_verified':
                    return outcome(group, 'unverified', 'pulse_hardware_contract_not_source_checked')
                actual = [f"K{values['$' + str(i)]}" if type(values.get('$' + str(i))) is int else token
                          for i, token in enumerate(tokens)]
                rungs = [{'branches': [{'outputs': [{'type': 'APP_INSTR', 'opcode': target, 'operands': actual}]}]}]
                try:
                    _validate_fx3u_pulse_hardware(rungs, {'hardware_profile': profile})
                except PLCJsonValidationError as error:
                    return outcome(group, 'failed', str(error))
                return outcome(group, 'passed')
            if group.dimension == 'constraints.shift_lengths':
                length = values.get('$' + str(value['length_position']), UNKNOWN)
                shift = values.get('$' + str(value['shift_position']), UNKNOWN)
                if type(length) is not int or type(shift) is not int:
                    return outcome(group, 'unverified', 'constraint_input_unknown')
                maximum = value['small_maximum'] if plc_model in value['small_models'] else value['maximum']
                okay = 0 <= shift <= length <= maximum
                return outcome(group, 'passed' if okay else 'failed', '' if okay else value['rule'])
            if group.dimension == 'constraints.memory_fields':
                tokens, reader = values.get('$OPERANDS'), context.get('memory_reader')
                if tokens is None or reader is None:
                    return outcome(group, 'unverified', 'memory_parameter_values_required')
                fields = dict(values)
                for field in value['fields']:
                    position = field['operand_index']
                    if position >= len(tokens) or tokens[position] is None:
                        continue
                    try:
                        fields[field['name']] = reader(tokens[position], field.get('offset', 0))
                    except (DefinitionError, ValueError, KeyError, IndexError):
                        pass
                try:
                    okay = _condition({'predicate': value['expression']}, fields, context)
                except (DefinitionError, TypeError, ValueError):
                    return outcome(group, 'unverified', 'memory_parameter_values_required')
                if okay and value.get('requires_context'):
                    state = _context_value(context, value['requires_context'])
                    if fields.get('TYPE') == 8 and state is UNKNOWN:
                        return outcome(group, 'unverified', 'context_value_missing:' + value['requires_context'])
                    if fields.get('TYPE') == 8 and state is not True:
                        okay = False
                return outcome(group, 'passed' if okay else 'failed', '' if okay else value['rule'])
            if group.dimension == 'constraints.numeric_ranges':
                unknown = False
                for boundary in value.get('ranges', []):
                    number = values.get('$' + str(boundary['operand_index']), UNKNOWN)
                    if type(number) is float and not math.isfinite(number):
                        return outcome(group, 'failed', 'non_finite_numeric_value')
                    if number is UNKNOWN or type(number) not in {int, float}:
                        unknown = True
                        continue
                    checked = abs(number) if boundary.get('absolute') else number
                    low, high = boundary.get('minimum'), boundary.get('maximum')
                    if low is not None and checked < low or high is not None and checked > high:
                        return outcome(group, 'failed', 'operand_outside_numeric_range')
                return outcome(group, 'unverified' if unknown else 'passed', 'constraint_input_unknown' if unknown else '')
            return outcome(group, 'unverified', 'constraint_not_formalized')
        finally:
            visiting.remove(identity)

    roots = [g.id for g in by_id.values() if g.dimension.startswith('constraints.')
             and g.value.get('phase', 'input') in {phase, 'both'}]
    for identity in roots:
        cached[identity] = visit(identity)
    statuses = [row['status'] for row in checks]
    return {'status': 'failed' if 'failed' in statuses else 'unverified' if 'unverified' in statuses else 'passed',
            'checks': checks, 'findings': findings, 'phase': phase, 'target': target}


def check_instruction_constraints(opcode, values, *, plc_model, language=None, version=None,
                                  context=None, phase='input', registry=None):
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    opcode, plc_model = str(opcode).strip().upper(), str(plc_model).strip().upper()
    members = _cpu_members(plc_model)
    if len(members) > 1:
        return _combined_reports([check_instruction_constraints(opcode, values, plc_model=model, language=language,
            version=version, context=context, phase=phase, registry=registry) for model in members], target=opcode, model=plc_model)
    if not registry.fact_bundle_current():
        return _stale_report(opcode)
    entry = registry.definition_entry(opcode, cpu=plc_model)
    if entry is None:
        return {'status': 'unverified', 'target': opcode, 'checks': [], 'findings': [
            {'code': 'PLC_RULE_COVERAGE', 'severity': 'unverified', 'message': 'instruction_facts_missing:' + opcode}]}
    return check_fact_constraints(entry.get('facts', []), values, target=opcode, plc_model=plc_model,
                                  language=language, version=version, context=context, phase=phase)


def check_language_constraints(target, values, *, plc_model, version=None, context=None,
                               phase='input', registry=None):
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    target, plc_model = str(target).strip().upper(), str(plc_model).strip().upper()
    members = _cpu_members(plc_model)
    if len(members) > 1:
        return _combined_reports([check_language_constraints(target, values, plc_model=model,
            version=version, context=context, phase=phase, registry=registry) for model in members], target=target, model=plc_model)
    if not registry.fact_bundle_current():
        return _stale_report(target)
    entry = registry.language_feature(target)
    if entry is None:
        return {'status': 'unverified', 'checks': [], 'findings': [{'code': 'ST_RULE_COVERAGE',
                'severity': 'unverified', 'message': 'language_feature_not_defined:' + target}], 'target': target}
    context = {'numeric_engine': 'cpu_manual', **(context or {})}
    values = dict(values)
    if (target == 'ST.OPERATOR.POWER' and phase == 'result'
            and context['numeric_engine'] == 'gx_simulator2_fx'):
        try:
            values['EXP_RESULT'] = evaluate_language_numeric(target, values['LEFT'], values['RIGHT'],
                plc_model=plc_model, precision=context.get('numeric_precision'), version=version,
                registry=registry, stage='exp')
            values['EVALUATED_RESULT'] = evaluate_language_numeric(target, values['LEFT'], values['RIGHT'],
                plc_model=plc_model, precision=context.get('numeric_precision'), version=version, registry=registry)
        except (KeyError, ValueError, TypeError, UnknownInstructionSemantics):
            values['EXP_RESULT'] = UNKNOWN
            values['EVALUATED_RESULT'] = UNKNOWN
    report = check_fact_constraints(entry.get('facts', []), values, target=target, plc_model=plc_model,
                                    language='st', version=version, context=context, phase=phase)
    for reference in entry.get('references', []):
        if plc_model not in reference.get('models', [plc_model]):
            continue
        bound = {name.upper(): values.get(source, UNKNOWN) for name, source in reference['parameters'].items()}
        child = check_instruction_constraints(reference['opcode'], bound, plc_model=plc_model, language='st',
            version=version, context=context, phase=phase, registry=registry)
        report['checks'].extend(child['checks'])
        report['findings'].extend(child['findings'])
        if child['status'] == 'failed' or child['status'] == 'unverified' and report['status'] == 'passed':
            report['status'] = child['status']
    return report


def require_constraints(report):
    """Execution stops on a known violation or a missing required rule."""
    if report['status'] != 'passed':
        error = UnknownInstructionSemantics('constraint_' + report['status'] + ':' + report['target'])
        error.constraint_report = report
        raise error


def evaluate_language_numeric(target, left, right, *, plc_model, precision, version=None, registry=None, stage='stored'):
    """Evaluate a source-defined numeric pipeline with its actual precision.

    Predicates remain the authority for domain/error checks. Returning infinity
    here exposes an overflow to that checker; it is never accepted as a result.
    """
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    if stage not in {'stored', 'exp'}:
        raise DefinitionError('Unknown numeric pipeline stage')
    target, model = str(target).upper(), str(plc_model).upper()
    if not registry.fact_bundle_current():
        raise UnknownInstructionSemantics('fact_resource_changed_reload_context')
    feature = registry.language_feature(target) or {}
    profiles = []
    for member in _cpu_members(model):
        selected = []
        for raw in feature.get('facts', []):
            if raw.get('dimension') != 'parameters.numeric_evaluation':
                continue
            fact = InstructionFactGroup.from_mapping(raw)
            if (_scope(fact, target=target, model=member, language='st', version=version) != 'passed'
                    or fact.status != 'source_verified' or fact.verification != 'source_checked'):
                continue
            if any(source.get('conditions') or source.get('applicability') for source in fact.sources):
                raise UnknownInstructionSemantics('numeric_evaluation_source_condition_unresolved')
            if all(_condition(condition, {}, {'numeric_precision': precision}) for condition in fact.conditions):
                selected.append(fact.value)
        if len(selected) != 1:
            raise UnknownInstructionSemantics('numeric_evaluation_profile_missing_or_ambiguous')
        profiles.extend(selected)
    if not profiles or any(profile != profiles[0] for profile in profiles):
        raise UnknownInstructionSemantics('numeric_evaluation_profiles_conflict')
    rule = profiles[0]
    if (rule.get('pipeline') != ['log', 'multiply', 'exp'] or rule.get('round_intermediates') is not True
            or rule.get('precision') != precision or rule.get('result_underflow') not in {'zero', 'preserve'}
            or version is not None and version != rule.get('compiler_version')):
        raise UnknownInstructionSemantics('numeric_evaluation_rule_not_supported')
    scalar = ValueType.from_mapping({'kind': 'real', 'bits': precision})
    left, right = scalar.decode_bits(left), scalar.decode_bits(right)
    if left <= 0:
        raise DefinitionError('Numeric pipeline requires a positive logarithm input')
    try:
        logarithm = scalar.decode_bits(math.log(left))
        exponent = scalar.decode_bits(logarithm * right)
        result = math.exp(exponent)
    except (OverflowError, DefinitionError):
        return math.inf
    if stage == 'exp':
        return result
    minimum = math.ldexp(1.0, -126 if precision == 32 else -1022)
    if rule['result_underflow'] == 'zero' and result < minimum:
        return 0.0
    try:
        return scalar.decode_bits(result)
    except DefinitionError:
        return math.inf


def numeric_operand_type(spec, index):
    if index < len(spec.native_operand_order):
        name = spec.native_operand_order[index].upper()
        for group in spec.definition_facts:
            if (group.dimension != 'data_types.operands' or group.status != 'source_verified'
                    or group.verification != 'source_checked' or group.conditions
                    or any(s.get('conditions') or s.get('applicability') for s in group.sources)):
                continue
            raw = group.value.get('operands', {})
            if not isinstance(raw, Mapping):
                continue
            types = {str(key).upper(): value for key, value in raw.items()}
            if name in types and isinstance(types[name], Mapping):
                return ValueType.from_mapping(types[name])
    if 'operand_types' in spec.verified_fields and index < len(spec.operands):
        operand = spec.operands[index]
        if operand.data_type in {'word', 'dword'}:
            return ValueType.from_mapping({'kind': 'int', 'bits': 32 if operand.data_type == 'dword' else 16 * operand.word_count, 'signed': True})
    if 'instruction_width' in spec.verified_fields and spec.instruction_width in {16, 32, 64}:
        return ValueType.from_mapping({'kind': 'int', 'bits': spec.instruction_width, 'signed': True})
    return None


def literal_operand_values(spec, operands):
    """Resolve only explicit instruction constants; device contents stay unknown."""
    values = {'$OPERANDS': list(operands)}
    for index, token in enumerate(operands):
        if not isinstance(token, str):
            continue
        text = token.strip().upper()
        if re.fullmatch(r'K[+-]?\d+', text):
            number = int(text[1:])
        elif re.fullmatch(r'H[0-9A-F]+', text):
            number = int(text[1:], 16)
            value_type = numeric_operand_type(spec, index)
            if value_type is not None and value_type.kind == 'int':
                if number >= 2 ** value_type.bits:
                    continue
                number = value_type.decode_bits(number)
            elif number >= 32768:
                continue  # Signed/unsigned interpretation needs the bound type.
        else:
            continue
        values['$' + str(index)] = number
        if index < len(spec.native_operand_order):
            values[spec.native_operand_order[index].upper()] = number
    return values


def instruction_program_accesses(ladder, *, plc_model):
    """Reuse the canonical IR's reads/writes and instruction identities."""
    from plc.ir import lower_rung_instructions, analyze_instruction_access
    rows = []
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    for index, rung in enumerate(ladder.get('rungs', [])):
        for instruction in lower_rung_instructions(rung):
            reads, writes = analyze_instruction_access(instruction['op'], instruction['args'], plc_model=plc_model)
            configured = []
            entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(instruction['op'], cpu=plc_model) or {}
            for fact in entry.get('facts', []):
                if fact['dimension'] == 'constraints.reserved_regions':
                    configured.extend(instruction['args'][r['operand_index']] for r in fact['value']['regions']
                                      if r.get('access') == 'all' and r['operand_index'] < len(instruction['args']))
            rows.append({'id': (index, instruction.get('path')), 'opcode': instruction['op'],
                         'operands': list(instruction['args']), 'reads': reads, 'writes': writes,
                         'configured_uses': configured})
    return rows


def inspect_ladder_constraints(ladder, *, plc_model, context=None):
    from plc.ir import lower_rung_instructions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    reports = []
    context = {**(context or {}), 'program_accesses': instruction_program_accesses(ladder, plc_model=plc_model)}
    for index, rung in enumerate(ladder.get('rungs', [])):
        for instruction in lower_rung_instructions(rung):
            form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(instruction['op'], cpu=plc_model)
            if form is None:
                continue
            report = check_instruction_constraints(form.opcode,
                literal_operand_values(form.spec, instruction['args']), plc_model=plc_model,
                language='ladder', version=context.get('software_version'),
                context={**context, 'current_instruction': (index, instruction.get('path'))})
            if report['checks']:
                reports.append({**report, 'rung_id': rung.get('id'),
                                'path': f'$.rungs[{index}]' + ('.' + instruction['path'] if instruction.get('path') else '')})
    return reports

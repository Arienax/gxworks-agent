# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

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


def documented_version_parts(value):
    """Normalize a documented dotted version without treating it as a number."""
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)*', value):
        raise UnknownInstructionSemantics('documented_version_format_required')
    result = list(map(int, value.split('.')))
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return tuple(result)


def version_condition_bounds(condition, *, model=None):
    """Return canonical bounds for the existing source version condition.

    A non-version condition returns None. A malformed or unresolved version
    condition raises rather than becoming an unconditional requirement. The
    offline compiler uses the same bounds when comparing applicability guards.
    """
    version_keys = {'context', 'minimum', 'minimum_exclusive', 'maximum_exclusive',
                    'minimum_by_model', 'minimum_exclusive_by_model'}
    if (not isinstance(condition, Mapping) or 'context' not in condition
            or not set(condition) <= version_keys or len(condition) <= 1):
        return None
    path = condition['context']
    if not isinstance(path, str) or not path or any(not part for part in path.split('.')):
        raise UnknownInstructionSemantics('documented_version_context_required')
    lower_keys = set(condition).intersection({
        'minimum', 'minimum_exclusive', 'minimum_by_model', 'minimum_exclusive_by_model'})
    if len(lower_keys) > 1:
        raise UnknownInstructionSemantics('CPU_version_binding_required')
    lower_key = next(iter(lower_keys), None)
    minimum = None
    minimum_exclusive = lower_key in {'minimum_exclusive', 'minimum_exclusive_by_model'}
    if lower_key in {'minimum_by_model', 'minimum_exclusive_by_model'}:
        requirements = condition[lower_key]
        if (not isinstance(requirements, Mapping) or not requirements or model not in requirements):
            raise UnknownInstructionSemantics('CPU_version_binding_required')
        # Review the whole declaration, not only a conveniently valid member.
        if any(not isinstance(name, str) or not name for name in requirements):
            raise UnknownInstructionSemantics('CPU_version_binding_required')
        normalized = {name: documented_version_parts(value) for name, value in requirements.items()}
        minimum = normalized[model]
    elif lower_key is not None:
        minimum = documented_version_parts(condition[lower_key])
    maximum = (documented_version_parts(condition['maximum_exclusive'])
               if 'maximum_exclusive' in condition else None)
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise UnknownInstructionSemantics('documented_version_range_required')
    return path, minimum, maximum, minimum_exclusive


def _condition(condition, values, context):
    if not isinstance(condition, Mapping):
        raise UnknownInstructionSemantics('condition_not_formalized')
    if condition.get('context') == 'cpu_version' and context.get('$CHECK_CPU_VERSION') is False:
        return True
    if set(condition) == {'any_of'}:
        alternatives = condition['any_of']
        if not isinstance(alternatives, (list, tuple)) or not alternatives:
            raise DefinitionError('Condition alternatives must be a nonempty array')
        unknown = None
        for alternative in alternatives:
            try:
                if _condition(alternative, values, context):
                    return True
            except UnknownInstructionSemantics as error:
                unknown = error
        if unknown is not None:
            raise unknown
        return False
    if condition.get('kind') == 'operand_form' and set(condition) == {'kind', 'required_operands'}:
        roles = context.get('$OPERAND_ROLES')
        if roles is None:
            raise UnknownInstructionSemantics('operand_form_binding_required')
        return all(str(name).upper() in roles for name in condition['required_operands'])
    if set(condition) == {'native_operand_count'}:
        if context.get('$LANGUAGE') not in {None, 'ladder'}:
            return False
        mode = context.get('project_mode')
        if mode == 'native_ladder':
            mode = 'native_ladder_instruction'
        roles = context.get('$OPERAND_ROLES')
        if mode is None or roles is None:
            raise UnknownInstructionSemantics('native_operand_signature_context_required')
        if mode != 'native_ladder_instruction':
            return False
        count = condition['native_operand_count']
        if type(count) is not int or count < 0:
            raise DefinitionError('Native operand count must be a nonnegative integer')
        return len(roles) == count
    if condition.get('kind') == 'cpu_model' and set(condition) == {'kind', 'models'}:
        model = context.get('$PLC_MODEL')
        if model is None:
            raise UnknownInstructionSemantics('cpu_model_binding_required')
        return model in condition['models']
    if set(condition) == {'context', 'equals'}:
        actual = _context_value(context, condition['context'])
        if actual is UNKNOWN or actual is None:
            raise UnknownInstructionSemantics('condition_context_missing:' + condition['context'])
        expected = condition['equals']
        if condition['context'] == 'project_mode':
            # Core native validation already calls this interface native_ladder;
            # source declarations use native_ladder_instruction to distinguish
            # it from a structured block in the same Ladder language.
            actual = 'native_ladder_instruction' if actual == 'native_ladder' else actual
            expected = 'native_ladder_instruction' if expected == 'native_ladder' else expected
        return type(actual) is type(expected) and actual == expected
    version_bounds = version_condition_bounds(condition, model=context.get('$PLC_MODEL'))
    if version_bounds is not None:
        path, minimum, maximum, minimum_exclusive = version_bounds
        actual = _context_value(context, path)
        if actual is UNKNOWN or actual is None:
            raise UnknownInstructionSemantics('condition_context_missing:' + path)
        actual = documented_version_parts(actual)
        return ((minimum is None or (actual > minimum if minimum_exclusive else actual >= minimum))
                and (maximum is None or actual < maximum))
    expression = condition.get('predicate', condition)
    parsed = Expression.from_mapping(expression)
    if parsed.value_type.kind != 'bool':
        raise DefinitionError('Constraint condition must be Boolean')
    # Public operand keys are canonicalized above; source parameter spelling
    # remains literal in formal expressions. Bind only parameter leaves here.
    bound_values = dict(values)
    pending = [parsed]
    while pending:
        node = pending.pop()
        if node.op == 'parameter' and node.name.upper() in values:
            bound_values[node.name] = values[node.name.upper()]
        pending.extend(node.args)
    return evaluate_expression(parsed, bound_values, state=context, memory_reader=context.get('memory_reader'))


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


def _documented_pulse_output_path(rule, values, context, model):
    """Check documented output paths without certifying wiring or motion state."""
    from plc.validation import (parse_device_address, _profile_output_is_transistor,
                                _profile_output_is_relay, _profile_has_high_speed_output_adapter,
                                _profile_high_speed_output_adapter_count)
    profile = context.get('hardware') or context.get('hardware_profile')
    if not isinstance(profile, Mapping) or not profile:
        return 'unverified', 'pulse_hardware_context_required'
    family = profile.get('plc_family')
    if family and str(family).upper() != model:
        return 'failed', 'pulse_hardware_model_mismatch'
    adapter_present = _profile_has_high_speed_output_adapter(profile)
    adapter = rule.get('high_speed_output_adapter')
    if not isinstance(adapter, Mapping):
        if adapter_present:
            return 'unverified', 'pulse_adapter_source_contract_not_established'
        adapter = {}
    if adapter_present and adapter.get('allowed') is False:
        return 'failed', 'pulse_output_adapter_not_supported_by_model'
    adapter_count = _profile_high_speed_output_adapter_count(profile)
    tokens = values.get('$OPERANDS', ())
    pulse_index, direction_index = rule['pulse_output_operand_index'], rule['direction_output_operand_index']
    if max(pulse_index, direction_index) >= len(tokens):
        return 'unverified', 'pulse_output_operand_binding_required'
    pulse = parse_device_address(tokens[pulse_index], model)
    if pulse is None or pulse[0] != 'Y':
        return 'unverified', 'pulse_output_address_binding_required'
    pulse_name = 'Y' + format(pulse[1], 'o')
    direction = parse_device_address(tokens[direction_index], model)
    allowed_directions = rule.get('software_direction_destinations')
    if direction is not None and allowed_directions and direction[0] not in allowed_directions:
        return 'failed', 'pulse_direction_device_not_source_supported'
    main = rule['main_unit']
    needed = adapter.get('adapter_count_required', {}).get(pulse_name)
    use_adapter = adapter_present and type(needed) is int and adapter_count >= needed
    if use_adapter:
        path = adapter
        expected = adapter.get('direction_output_pairs', {}).get(pulse_name)
        if direction is not None and direction[0] == 'Y' and expected:
            if 'Y' + format(direction[1], 'o') != expected:
                return 'failed', 'pulse_adapter_direction_output_pair_mismatch'
    else:
        # An omitted module list does not establish that no adapter is fitted.
        absence_known = (profile.get('positioning_module_quantity') == 0
                         or 'modules' in profile and profile['modules'] == []
                         or adapter_present)
        relay_or_triac = (_profile_output_is_relay(profile)
                          or 'triac' in str(profile.get('output_type', '')).casefold())
        if relay_or_triac and absence_known:
            return 'failed', 'pulse_transistor_output_or_adapter_required'
        if not _profile_output_is_transistor(profile) or not absence_known:
            return 'unverified', 'pulse_output_type_and_adapter_configuration_required'
        if pulse_name not in main['pulse_devices']:
            return 'failed', ('pulse_output_adapter_count_insufficient' if adapter_present
                              else 'pulse_main_unit_output_not_supported')
        for exception in main.get('device_exceptions', []):
            if exception.get('device') != pulse_name:
                continue
            exclusions = exception.get('unavailable_when', {})
            for field, excluded in exclusions.items():
                if field not in profile:
                    return 'unverified', 'pulse_main_unit_' + field + '_required'
                if profile[field] in excluded:
                    return 'failed', 'pulse_main_unit_output_not_supported'
        path = main
    frequency = values.get('$' + str(rule['frequency_operand_index']), UNKNOWN)
    if type(frequency) not in {int, float} or not math.isfinite(frequency):
        return 'unverified', 'pulse_frequency_value_required'
    if not path['minimum_Hz'] <= frequency <= path['maximum_Hz']:
        return 'failed', 'pulse_frequency_outside_selected_output_path_range'
    # A source-authorized software destination cannot prove the physical
    # direction route, busy axis or interrupt configuration.
    return 'unverified', 'pulse_physical_direction_routing_and_axis_configuration_required'


def _call_parameter_index(binding, values):
    """Bind a source-declared structured parameter without a Native offset."""
    name = binding.get('name') if isinstance(binding, Mapping) else None
    names = values.get('$PARAMETER_NAMES', ())
    if not isinstance(name, str) or not isinstance(names, (list, tuple)):
        raise UnknownInstructionSemantics('structured_parameter_binding_required')
    matches = [index for index, item in enumerate(names)
               if isinstance(item, str) and item.casefold() == name.casefold()]
    if len(matches) != 1:
        raise UnknownInstructionSemantics('structured_parameter_binding_required')
    index = matches[0]
    if 'call_argument_index' in binding and binding['call_argument_index'] != index:
        raise UnknownInstructionSemantics('structured_parameter_index_mismatch')
    return index


def _structured_positioning_path(rule, values, context, model):
    """Adapt the documented structured paths to the shared pulse checker."""
    bindings = rule['bindings']
    adapted = {'frequency_operand_index': _call_parameter_index(bindings['frequency'], values),
               'pulse_output_operand_index': _call_parameter_index(bindings['pulse_output'], values),
               'direction_output_operand_index': _call_parameter_index(bindings['direction_signal'], values),
               'software_direction_destinations': rule['software_direction_device_types']}
    paths = rule['paths']
    mains = [path for path in paths if path.get('path') in {
        'built_in_transistor_output', 'basic_unit_transistor_output'}]
    adapters = [path for path in paths if path.get('path') == 'fx3u_high_speed_output_special_adapter']
    if len(mains) != 1 or len(adapters) > 1 or len(mains) + len(adapters) != len(paths):
        return 'unverified', 'structured_output_paths_not_formalized'
    def convert(path):
        return {**path, 'pulse_devices': path['pulse_output_devices'],
                'minimum_Hz': path['frequency_Hz']['minimum'],
                'maximum_Hz': path['frequency_Hz']['maximum']}
    adapted['main_unit'] = convert(mains[0])
    if 'high_speed_output_adapter' in rule:
        adapted['high_speed_output_adapter'] = dict(rule['high_speed_output_adapter'])
        if adapters:
            adapted['high_speed_output_adapter'].update(convert(adapters[0]))
    elif adapters:
        return 'unverified', 'pulse_adapter_source_contract_not_established'
    return _documented_pulse_output_path(adapted, values, context, model)


def _documented_analog_module_io(rule, values, context, model):
    """Match documented analog-module device bindings to one project interface.

    This verifies only the explicit project configuration and address extent.
    It does not claim that the physical module is installed or operating.
    """
    from plc.validation import device_address_radix, parse_device_address

    if rule.get('representation') != 'st_statement_call':
        return 'unverified', 'analog_module_io_statement_representation_required'
    interface_module = rule.get('interface_module')
    analog_module = rule.get('analog_module')
    io = rule.get('io')
    if (not isinstance(interface_module, str) or not interface_module.strip()
            or not isinstance(analog_module, str) or not analog_module.strip()
            or not isinstance(io, list) or not io):
        return 'unverified', 'analog_module_io_source_contract_incomplete'

    normalized_io = []
    names = set()
    for item in io:
        if not isinstance(item, Mapping):
            return 'unverified', 'analog_module_io_source_contract_incomplete'
        binding = item.get('binding')
        name = binding.get('name') if isinstance(binding, Mapping) else None
        direction = item.get('direction')
        prefix = item.get('device_prefix')
        points = item.get('points')
        if (not isinstance(name, str) or not name.strip() or name.casefold() in names
                or direction not in {'input', 'output'}
                or not isinstance(prefix, str) or not prefix.strip()
                or ('points' in item and (type(points) is not int or points <= 0))):
            return 'unverified', 'analog_module_io_source_contract_incomplete'
        names.add(name.casefold())
        try:
            index = _call_parameter_index(binding, values)
        except UnknownInstructionSemantics as error:
            return 'unverified', str(error)
        token = values.get('$OPERANDS', ())
        if index >= len(token) or token[index] is None:
            return 'unverified', 'analog_module_io_operand_address_required'
        normalized_io.append((direction, prefix.upper(), points, token[index]))

    hardware = _context_value(context, 'hardware')
    if not isinstance(hardware, Mapping) or 'analog_interfaces' not in hardware:
        return 'unverified', 'analog_module_interface_context_required'
    interfaces = hardware.get('analog_interfaces')
    if not isinstance(interfaces, list):
        return 'unverified', 'analog_module_interface_context_required'
    if not interfaces:
        return 'failed', 'analog_module_interface_not_configured'

    def matches_module(row):
        return (isinstance(row, Mapping)
                and isinstance(row.get('interface_module'), str)
                and row['interface_module'].casefold() == interface_module.casefold()
                and isinstance(row.get('analog_module'), str)
                and row['analog_module'].casefold() == analog_module.casefold())

    candidates = [row for row in interfaces if matches_module(row)]
    if not candidates:
        if any(not isinstance(row, Mapping)
               or not isinstance(row.get('interface_module'), str)
               or not isinstance(row.get('analog_module'), str) for row in interfaces):
            return 'unverified', 'analog_module_interface_context_incomplete'
        return 'failed', 'analog_module_configuration_mismatch'

    def parsed_address(value):
        if not isinstance(value, str):
            return None, False
        address = _address(value, model)
        if address is None:
            return None, False
        return address, parse_device_address(value, model) is not None

    def render_index(prefix, index):
        radix = device_address_radix(prefix, model)
        if radix == 8:
            digits = format(index, 'o')
        elif radix == 16:
            digits = format(index, 'X')
        elif radix == 10:
            digits = str(index)
        else:
            return None
        return prefix + digits

    def inspect_row(row):
        unknown = False
        for direction, expected_prefix, source_points, actual_token in normalized_io:
            head_key = 'input_head' if direction == 'input' else 'output_head'
            count_key = 'input_points' if direction == 'input' else 'output_points'
            configured_head = row.get(head_key)
            actual_address, actual_legal = parsed_address(actual_token)
            configured_address, configured_legal = parsed_address(configured_head)
            if actual_address is None or configured_address is None:
                unknown = True
                continue
            if (actual_address != configured_address
                    or actual_address[0] != expected_prefix
                    or configured_address[0] != expected_prefix):
                return 'failed', 'analog_module_io_head_mismatch'
            if not actual_legal or not configured_legal:
                return 'failed', 'analog_module_io_head_outside_model_range'

            configured_points = row.get(count_key)
            if source_points is not None:
                if type(configured_points) is not int or configured_points <= 0:
                    unknown = True
                    span_points = source_points
                elif configured_points != source_points:
                    return 'failed', 'analog_module_io_configured_points_mismatch'
                else:
                    span_points = configured_points
            elif type(configured_points) is int and configured_points > 0:
                # With no source count, validate only the explicitly configured
                # extent; this does not infer a source-required module width.
                span_points = configured_points
            else:
                span_points = None

            if span_points is None:
                continue
            prefix, first = actual_address
            for offset in range(span_points):
                address_text = render_index(prefix, first + offset)
                if address_text is None:
                    unknown = True
                    break
                address = parse_device_address(address_text, model)
                if address is None or address != (prefix, first + offset):
                    return 'failed', 'analog_module_io_device_span_outside_model_range'
        return ('unverified', 'analog_module_io_configuration_incomplete') if unknown else ('passed', '')

    outcomes = [inspect_row(row) for row in candidates]
    if any(status == 'passed' for status, _ in outcomes):
        return 'passed', ''
    if any(status == 'unverified' for status, _ in outcomes):
        return 'unverified', next(reason for status, reason in outcomes if status == 'unverified')
    return 'failed', outcomes[0][1] if outcomes else 'analog_module_configuration_mismatch'


def _numeric_range_check(rule, values):
    ranges = rule.get('ranges')
    if not isinstance(ranges, list) or not ranges:
        return 'unverified', 'numeric_range_operand_binding_missing'
    unknown = False
    for boundary in ranges:
        if not isinstance(boundary, Mapping):
            return 'unverified', 'numeric_range_operand_binding_missing'
        index = boundary.get('operand_index')
        if 'binding' in boundary:
            try:
                index = _call_parameter_index(boundary['binding'], values)
            except UnknownInstructionSemantics as error:
                return 'unverified', str(error)
        if type(index) is not int or index < 0:
            return 'unverified', 'numeric_range_operand_binding_missing'
        alternatives = boundary.get('by_hardware_path', [boundary])
        def has_numeric_bound(item):
            return any(item.get(key) is not None for key in ('minimum', 'maximum'))
        excluded = boundary.get('excluded_values')
        exclusion_only = ('by_hardware_path' not in boundary
            and not has_numeric_bound(boundary)
            and isinstance(excluded, (list, tuple)) and bool(excluded)
            and all(type(value) in {int, float} and math.isfinite(value) for value in excluded))
        if (not isinstance(alternatives, list) or not alternatives or any(
                not isinstance(item, Mapping)
                or not has_numeric_bound(item) and not (item is boundary and exclusion_only)
                or any(item.get(key) is not None and type(item[key]) not in {int, float}
                       for key in ('minimum', 'maximum'))
                or any(key in item and type(item[key]) is not bool
                       for key in ('minimum_inclusive', 'maximum_inclusive')) for item in alternatives)
                or not any(has_numeric_bound(item) for item in alternatives) and not exclusion_only):
            return 'unverified', 'numeric_range_operand_binding_missing'
        number = values.get('$' + str(index), UNKNOWN)
        if type(number) is float and not math.isfinite(number):
            return 'failed', 'non_finite_numeric_value'
        if number is UNKNOWN or type(number) not in {int, float}:
            unknown = True
            continue
        checked = abs(number) if boundary.get('absolute') else number
        def contains(item):
            low, high = item.get('minimum'), item.get('maximum')
            return (not (low is not None and (checked < low or checked == low and not item.get('minimum_inclusive', True))
                         or high is not None and (checked > high or checked == high and not item.get('maximum_inclusive', True)))
                    and checked not in item.get('excluded_values', ()))
        if checked in boundary.get('excluded_values', ()) or not any(contains(item) for item in alternatives):
            return 'failed', 'operand_outside_numeric_range'
        # This union only proves that some documented path can accept it.
        # The output-path constraint checks the actual hardware selection.
        unknown |= 'by_hardware_path' in boundary
    return ('unverified', 'constraint_input_or_path_selection_required') if unknown else ('passed', '')


def _source_cpu_version_boundary(value, model):
    """Read one source-declared CPU version boundary, not a current CPU value."""
    if not isinstance(value, Mapping):
        return None
    models = value.get('models')
    if (not isinstance(models, list) or not models
            or any(not isinstance(item, str) or not item for item in models)
            or len(models) != len(set(models)) or model not in models):
        return None
    present = [key for key in ('minimum_series_version', 'maximum_series_version_exclusive')
               if key in value]
    if len(present) != 1 or set(value) - {'models', 'minimum_series_version',
                                          'maximum_series_version_exclusive', 'basis'}:
        return None
    key = present[0]
    if (key == 'maximum_series_version_exclusive'
            and value.get('basis') != 'own_instruction_retirement_footnote'):
        return None
    raw = value[key]
    if not isinstance(raw, str):
        return None
    rendered = raw[4:] if raw.casefold().startswith('ver.') else raw
    try:
        return key, documented_version_parts(rendered), rendered
    except UnknownInstructionSemantics:
        return None


def _conditional_cpu_availability(group, by_id, model):
    """Accept a conditional CPU row only with its exact, evaluated source guard."""
    value = group.value
    states = value.get('model_states')
    if not isinstance(states, Mapping):
        return 'unverified', 'conditional_CPU_availability_model_state_required'
    if states.get(model) != 'conditional':
        return None
    if 'available' in value:
        return 'unverified', 'conditional_CPU_availability_must_not_be_boolean'

    version_dependencies = [by_id[identity] for identity in group.depends_on
                            if identity in by_id and by_id[identity].dimension == 'constraints.cpu_version']
    if len(version_dependencies) != 1:
        return 'unverified', 'conditional_CPU_version_source_dependency_required'
    version_fact = version_dependencies[0]
    boundary = _source_cpu_version_boundary(version_fact.value, model)
    if boundary is None:
        return 'unverified', 'conditional_CPU_version_source_boundary_required'
    boundary_key, boundary_value, boundary_text = boundary

    guards = []
    try:
        for condition in group.conditions:
            if not isinstance(condition, Mapping):
                return 'unverified', 'conditional_CPU_version_guard_required'
            if condition.get('context') != 'cpu_version':
                continue
            parsed = version_condition_bounds(condition, model=model)
            if parsed is None:
                return 'unverified', 'conditional_CPU_version_guard_required'
            path, minimum, maximum, minimum_exclusive = parsed
            if path != 'cpu_version':
                return 'unverified', 'conditional_CPU_version_guard_mismatch'
            guards.append((minimum, maximum, minimum_exclusive))
    except (DefinitionError, TypeError, ValueError, KeyError, UnknownInstructionSemantics):
        return 'unverified', 'conditional_CPU_version_guard_unresolved'
    if len(guards) != 1:
        return 'unverified', 'conditional_CPU_version_guard_required'

    minimum, maximum, minimum_exclusive = guards[0]
    if boundary_key == 'minimum_series_version':
        if minimum != boundary_value or minimum_exclusive or maximum is not None:
            return 'unverified', 'conditional_CPU_version_guard_mismatch'
    elif boundary_key == 'maximum_series_version_exclusive':
        if minimum is not None or maximum != boundary_value:
            return 'unverified', 'conditional_CPU_version_guard_mismatch'
        header_dependencies = [by_id[identity] for identity in group.depends_on
                               if identity in by_id and by_id[identity].dimension == 'constraints.source_cpu_header']
        if len(header_dependencies) != 1:
            return 'unverified', 'conditional_CPU_source_header_dependency_required'
        header_value = header_dependencies[0].value
        header_maximum = header_value.get('maximum_version_exclusive')
        if not isinstance(header_maximum, str):
            return 'unverified', 'conditional_CPU_source_header_version_mismatch'
        header_text = (header_maximum[4:] if header_maximum.casefold().startswith('ver.')
                       else header_maximum)
        try:
            if documented_version_parts(header_text) != boundary_value:
                return 'unverified', 'conditional_CPU_source_header_version_mismatch'
        except UnknownInstructionSemantics:
            return 'unverified', 'conditional_CPU_source_header_version_mismatch'
    else:
        return 'unverified', 'conditional_CPU_version_source_boundary_required'
    return 'passed', ''


def _documented_operand_relations(rule, values, context, model):
    """Interpret shared, source-checked operand relations, never opcode names.

    Unrecognised clauses stay unknown: recognising one clause does not certify
    the rest of a source packet. Address roles come from the bound signature.
    """
    def numeric(name):
        value = values.get(str(name).upper(), UNKNOWN)
        if type(value) is not int:
            raise UnknownInstructionSemantics('constraint_input_unknown:' + str(name))
        return value

    def token(name):
        roles = context.get('$OPERAND_ROLES')
        operands = values.get('$OPERANDS')
        key = str(name).upper()
        if roles is None or key not in roles or operands is None:
            raise UnknownInstructionSemantics('operand_device_binding_required:' + key)
        index = roles.index(key)
        if index >= len(operands) or not isinstance(operands[index], str):
            raise UnknownInstructionSemantics('operand_device_binding_required:' + key)
        return re.sub(r'\s+', '', operands[index]).upper()

    if set(rule) == {'ordered_bounds'} and isinstance(rule['ordered_bounds'], list) and rule['ordered_bounds']:
        for bound in rule['ordered_bounds']:
            if (not isinstance(bound, Mapping) or 'parameter' not in bound
                    or set(bound) - {'parameter', 'minimum', 'maximum', 'minimum_operand', 'maximum_operand'}
                    or len(bound) < 2):
                raise UnknownInstructionSemantics('ordered_bounds_not_formalized')
            actual = numeric(bound['parameter'])
            for field in ('minimum', 'maximum', 'minimum_operand', 'maximum_operand'):
                if field not in bound:
                    continue
                limit = numeric(bound[field]) if field.endswith('_operand') else bound[field]
                if type(limit) is not int:
                    raise UnknownInstructionSemantics('ordered_bound_not_integer')
                if actual < limit if field.startswith('minimum') else actual > limit:
                    return 'failed', 'operand_bound_violated:' + str(bound['parameter'])
        return 'passed', ''
    if set(rule) != {'rules'} or not isinstance(rule['rules'], list) or not rule['rules']:
        return None
    for clause in rule['rules']:
        if not isinstance(clause, Mapping):
            raise UnknownInstructionSemantics('operand_relation_not_formalized')
        if clause.get('kind') == 'same_head':
            if (set(clause) - {'kind', 'operands', 'models', 'result', 'code', 'operation_error'}
                    or len(clause.get('operands', [])) != 2):
                raise UnknownInstructionSemantics('same_head_rule_not_formalized')
            if 'models' in clause and model not in clause['models']:
                continue
            # A documented no-error case grants no alias execution semantics.
            if clause.get('operation_error') is False:
                continue
            if clause.get('result') != 'operation_error':
                raise UnknownInstructionSemantics('same_head_result_not_formalized')
            from plc.device_identity import canonical_reference_device, REFERENCE_DEVICE_TOKEN_RE
            heads = [token(name) for name in clause['operands']]
            heads = [re.sub(r'^K[1-8](?=[A-Z])', '', head) for head in heads]
            if not all(REFERENCE_DEVICE_TOKEN_RE.fullmatch(head) for head in heads):
                raise UnknownInstructionSemantics('resolved_operand_address_required')
            if canonical_reference_device(heads[0]) == canonical_reference_device(heads[1]):
                return 'failed', 'source_destination_same_head'
        elif clause.get('kind') == 'word_bit_operand':
            if (set(clause) - {'kind', 'operand', 'models', 'index_modification'}
                    or 'operand' not in clause or 'models' not in clause):
                raise UnknownInstructionSemantics('word_bit_rule_not_formalized')
            head = token(clause['operand'])
            # This qualification applies only to word.bit syntax, not M/X bits.
            if '.' not in head:
                continue
            match = re.fullmatch(r'([A-Z]+[0-9A-F]+\.[0-9A-F]+)([VZ]\d+)?', head)
            if match is None:
                raise UnknownInstructionSemantics('word_bit_address_binding_required')
            if model not in clause['models']:
                return 'failed', 'word_bit_operand_model_unsupported'
            if match[2] and clause.get('index_modification') is False:
                return 'failed', 'word_bit_index_modification_forbidden'
        else:
            raise UnknownInstructionSemantics('operand_relation_not_formalized')
    return 'passed', ''


def _additional_constraint(dimension, rule, values, context, model, *, target=None, registry=None):
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
        if dimension in {'constraints.documented_restriction', 'constraints.operand_relation'}:
            return _documented_operand_relations(rule, values, context, model)
        if dimension == 'constraints.operand_alias':
            if (set(rule) != {'operands', 'when_same_head', 'digit_specification_requires_same_digit_count'}
                    or not isinstance(rule['when_same_head'], Mapping)
                    or set(rule['when_same_head']) - {'kind', 'error_code'}
                    or rule['when_same_head'].get('kind') != 'operation_error'):
                raise UnknownInstructionSemantics('operand_alias_rule_not_formalized')
            relation = _documented_operand_relations({'rules': [{'kind': 'same_head',
                'operands': rule['operands'], 'result': 'operation_error'}]}, values, context, model)
            if relation[0] != 'passed':
                return relation
            if rule['digit_specification_requires_same_digit_count'] is not True:
                raise UnknownInstructionSemantics('digit_count_relation_not_formalized')
            roles = context.get('$OPERAND_ROLES', ())
            digits = [re.match(r'^K([1-8])(?=[A-Z])', str(tokens[roles.index(name.upper())]).upper())
                      for name in rule['operands']]
            if len({digit[1] for digit in digits if digit}) > 1:
                return 'failed', 'digit_specification_count_mismatch'
            return 'passed', ''
        if dimension == 'constraints.documented_requirements' and rule.get('kind') == 'pulse_output_path':
            return _documented_pulse_output_path(rule, values, context, model)
        if (dimension == 'constraints.documented_requirements'
                and rule.get('kind') == 'structured_positioning_output_path'):
            return _structured_positioning_path(rule, values, context, model)
        if (dimension == 'constraints.documented_requirements'
                and rule.get('kind') == 'structured_analog_module_io'):
            return _documented_analog_module_io(rule, values, context, model)
        if dimension == 'constraints.source_cpu_header':
            # This reviewed observation is a source premise, not a requirement
            # that the current CPU already meets its documented minimum.  In
            # particular the below-minimum exclusion depends on this premise.
            basis = rule.get('basis')
            allowed = {'basis', 'model', 'state', 'minimum_version', 'maximum_version_exclusive'}
            if basis == 'own_instruction_applicability_table_row':
                allowed |= {'model_group', 'source_mark'}
                if any(not isinstance(rule.get(key), str) or not rule[key].strip()
                       for key in ('model_group', 'source_mark')):
                    raise UnknownInstructionSemantics('reviewed_source_cpu_header_required')
            elif basis != 'reviewed_own_instruction_header_cpu_version_badges':
                raise UnknownInstructionSemantics('reviewed_source_cpu_header_required')
            if (set(rule) - allowed
                    or rule.get('model') != model
                    or rule.get('state') not in {'supported', 'unsupported', 'conditional'}):
                raise UnknownInstructionSemantics('reviewed_source_cpu_header_required')
            minimum = rule.get('minimum_version')
            if minimum is not None:
                documented_version_parts(minimum)
            maximum = rule.get('maximum_version_exclusive')
            if maximum is not None:
                documented_version_parts(maximum)
            if (minimum is not None and maximum is not None
                    and documented_version_parts(minimum) >= documented_version_parts(maximum)):
                raise UnknownInstructionSemantics('source_cpu_header_version_range_required')
            if rule['state'] == 'conditional' and minimum is None and maximum is None:
                raise UnknownInstructionSemantics('source_cpu_header_version_bound_required')
            return 'passed', ''
        if dimension == 'constraints.cpu_version':
            boundary = _source_cpu_version_boundary(rule, model)
            if boundary is None:
                raise UnknownInstructionSemantics('source_cpu_version_boundary_not_formalized')
            return 'passed', ''
        if dimension == 'constraints.cpu_availability':
            state = rule.get('model_states', {}).get(model)
            if state == 'unsupported':
                return 'failed', rule['rule']
            if state == 'supported':
                return 'passed', ''
            if state == 'conditional':
                raise UnknownInstructionSemantics('conditional_CPU_availability_requires_source_guard')
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
            if 'source_unit' in rule or 'destination_unit' in rule:
                # A comparison reads n words and writes n bits. Resolve both
                # through the source-qualified device region engine, including
                # the selected bit; plain device-number arithmetic loses it.
                source_multiplier = rule.get('source_length_multiplier', 1)
                if (rule.get('source_unit') != 'words' or rule.get('destination_unit') != 'bits'
                        or source_multiplier not in (1, 2)
                        or rule.get('destination_length_multiplier', 1) != 1):
                    raise UnknownInstructionSemantics('mixed_region_units_not_established')
                source_index, destination_index = rule['source_operand_index'], rule['destination_operand_index']
                if (type(source_index) is not int or type(destination_index) is not int
                        or not 0 <= source_index < len(tokens) or not 0 <= destination_index < len(tokens)):
                    raise UnknownInstructionSemantics('operand_region_binding_required')
                if registry is None:
                    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
                    registry = DEFAULT_INSTRUCTION_REGISTRY
                spec = registry.resolve(target, cpu=model, operand_count=len(tokens)) if target else None
                if spec is None:
                    raise UnknownInstructionSemantics('source_qualified_region_contract_required')
                source_token = operand(source_index)
                if (isinstance(source_token, str)
                        and re.fullmatch(r'(?:K[+-]?\d+|H[0-9A-F]+)', source_token.strip().upper())
                        and source_token.strip().upper()[0] in dict(spec.operands[source_index].integer_literal_widths)):
                    # A reviewed repeated constant occupies no device region.
                    # Its literal format/range remains a separate validation.
                    return 'not_applicable', ''
                from plc.device_identity import instruction_operand_regions
                resolved_regions = instruction_operand_regions(spec, target, tokens, model)
                selected = []
                for index, unit, count_index, multiplier in (
                        (source_index, 'words', rule['source_length_operand_index'], source_multiplier),
                        (destination_index, 'bits', rule['destination_length_operand_index'], 1)):
                    count = number(count_index) * multiplier
                    candidates = [item for item in resolved_regions
                                  if item['operand_index'] == index and item.get('unit') == unit]
                    if (len(candidates) != 1 or candidates[0].get('resolved_count') != count
                            or candidates[0].get('requires')):
                        raise UnknownInstructionSemantics('source_qualified_region_extent_required')
                    physical = candidates[0].get('physical_bit_region')
                    if (not isinstance(physical, Mapping)
                            or not isinstance(physical.get('address_space'), str) or not physical['address_space']
                            or type(physical.get('start_bit')) is not int or physical['start_bit'] < 0
                            or type(physical.get('bit_count')) is not int or physical['bit_count'] < 0):
                        raise UnknownInstructionSemantics('physical_bit_region_required')
                    selected.append(physical)
                left, right = selected
                overlap = (left['address_space'] == right['address_space']
                           and left['start_bit'] < right['start_bit'] + right['bit_count']
                           and right['start_bit'] < left['start_bit'] + left['bit_count'])
                return ('failed', rule['rule']) if overlap else ('passed', '')
            if rule.get('repeated_constant_has_no_device_region') is True:
                source_index = rule['source_operand_index']
                source_token = operand(source_index)
                if (isinstance(source_token, str)
                        and re.fullmatch(r'(?:K[+-]?\d+|H[0-9A-F]+)', source_token.strip().upper())):
                    if registry is None:
                        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
                        registry = DEFAULT_INSTRUCTION_REGISTRY
                    spec = registry.resolve(target, cpu=model, operand_count=len(tokens)) if target else None
                    if (spec is not None and source_index < len(spec.operands)
                            and source_token.strip().upper()[0]
                            in dict(spec.operands[source_index].integer_literal_widths)):
                        # Literal format and range remain separate checks.
                        return 'not_applicable', ''
            if 'device_unit_bits' in rule:
                from plc.device_profiles import native_word_value_bits
                for index in (rule['source_operand_index'], rule['destination_operand_index']):
                    address = _address(operand(index), model)
                    if address and native_word_value_bits(*address, model) != rule['device_unit_bits']:
                        raise UnknownInstructionSemantics('source_region_device_unit_not_established')
            source = region(operand(rule['source_operand_index']), int(number(rule['source_length_operand_index']))
                            * rule.get('source_length_multiplier', 1))
            destination = region(operand(rule['destination_operand_index']), int(number(rule['destination_length_operand_index']))
                                 * rule.get('destination_length_multiplier', 1))
            if (source == destination and source[1] < source[2]
                    and isinstance(rule.get('identical_region_source_conflict'), Mapping)
                    and rule.get('identical_region_policy_established') is False):
                raise UnknownInstructionSemantics('identical_region_source_conflict')
            if rule.get('allow_identical') is True and source == destination:
                return 'passed', ''
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


def bind_operand_signature(groups, *, target, plc_model, language=None, version=None, context=None,
                           signature_cache=None):
    """Bind names from a source-checked, applicable signature and its closure.

    This is structural binding, not evaluation of operand values or memory.
    An optional replay cache keeps closures for immutable fact groups. Context
    conditions are still evaluated for each binding.
    Retrieval and constraint checking share it; internal caller keys cannot
    manufacture a documented signature.
    """
    from plc.instruction_definition import select_fact_dependencies
    parsed = [g if isinstance(g, InstructionFactGroup) else InstructionFactGroup.from_mapping(g) for g in groups]
    by_id = {group.id: group for group in parsed}
    if len(by_id) != len(parsed):
        return None
    bound_context = dict(context or {})
    bound_context.pop('$OPERAND_ROLES', None)
    bound_context['$PLC_MODEL'] = plc_model
    bound_context['$LANGUAGE'] = language
    bound_context['$CHECK_CPU_VERSION'] = (bool(bound_context.get('check_cpu_version'))
                                          or bound_context.get('cpu_version') is not None)
    def available(group):
        if (_scope(group, target=target, model=plc_model, language=language, version=version) != 'passed'
                or any(source.get('conditions') or source.get('applicability') for source in group.sources)):
            return False
        for condition in group.conditions:
            if not isinstance(condition, Mapping):
                return False
            try:
                if not (set(condition) == {'context', 'equals'}
                        or condition.get('kind') == 'cpu_model' and set(condition) == {'kind', 'models'}
                        or version_condition_bounds(condition, model=plc_model) is not None):
                    return False
                if not _condition(condition, {}, bound_context):
                    return False
            except (DefinitionError, TypeError, ValueError, KeyError):
                return False
        return True
    dependencies = {}
    if signature_cache is not None:
        key = (target, plc_model, language, version)
        cached = signature_cache.get(key)
        if cached is not None and cached[0] is groups:
            dependencies = cached[1]
        else:
            signature_cache[key] = (groups, dependencies)
    signatures = set()
    for group in parsed:
        if group.dimension != 'parameters.typed_operands' or not available(group):
            continue
        if group.id not in dependencies:
            closure = select_fact_dependencies(parsed, [group.id], opcode=target, model=plc_model,
                                               language=language, version=version)
            dependencies[group.id] = (tuple(by_id[fact['id']] for fact in closure['groups'])
                                      if closure['source_verification_complete'] and not closure['gaps'] else ())
        required = dependencies[group.id]
        if not required or not all(available(dependency) for dependency in required):
            continue
        operands = group.value.get('operands')
        if isinstance(operands, list) and (operands or group.value.get('operand_count') == 0) and all(
                isinstance(item, Mapping) and isinstance(item.get('name'), str) and item['name'] for item in operands):
            names = tuple(item['name'].upper() for item in operands)
            if len(names) == len(set(names)) and group.value.get('operand_count', len(names)) == len(names):
                signatures.add(names)
    return next(iter(signatures)) if len(signatures) == 1 else None


def check_fact_constraints(groups, values, *, target, plc_model, language=None, version=None,
                           context=None, phase='input', root_ids=None, registry=None,
                           signature_cache=None):
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
    # Bind only an observed, source-checked signature for this exact scope.
    # Caller-supplied internal keys cannot forge a signature or CPU condition.
    context.pop('$OPERAND_ROLES', None)
    context['$PLC_MODEL'] = plc_model
    context['$LANGUAGE'] = language
    context['$CHECK_CPU_VERSION'] = (bool(context.get('check_cpu_version'))
                                    or context.get('cpu_version') is not None)
    signature = bind_operand_signature(groups if signature_cache is not None else list(by_id.values()),
        target=target, plc_model=plc_model, language=language, version=version, context=context,
        signature_cache=signature_cache)
    if signature is not None:
        context['$OPERAND_ROLES'] = signature
        for index, name in enumerate(signature):
            if name not in values and '$' + str(index) in values:
                values[name] = values['$' + str(index)]
    if target in {'PWM', 'DPWM'} and plc_model in {'FX5S', 'FX5UJ', 'FX5U', 'FX5UC'}:
        from plc.pwm_contract import bind_pwm_values, derive_pwm_formal_facts
        source_bound = any(group.dimension == 'identity.applicability'
            and group.status == 'source_verified' and group.verification == 'source_checked'
            and not group.conditions and group.value.get('model_states', {}).get(plc_model) == 'supported'
            and _scope(group, target=target, model=plc_model, language=language, version=version) == 'passed'
            and any(s.get('manual_number') == 'JY997D55801' and s.get('revision') == 'AB' for s in group.sources)
            for group in by_id.values())
        formal_bound = any(group.id.startswith('pwm-formal:') for group in by_id.values())
        if source_bound or formal_bound:
            values = bind_pwm_values(target, plc_model, values, context)
        if source_bound:
            for raw in derive_pwm_formal_facts(target, plc_model, language, list(by_id.values())):
                group = InstructionFactGroup.from_mapping(raw)
                if group.id in by_id and by_id[group.id] != group:
                    raise DefinitionError('Conflicting derived PWM constraint identity')
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
            # Version qualification is opt-in. Do not turn an old-version
            # exclusion into an unconditional model exclusion when its guard
            # is not being evaluated. Other operand and model rules still run.
            version_guarded = any(c.get('context') == 'cpu_version' for c in group.conditions
                                  if isinstance(c, Mapping))
            version_availability = (group.dimension == 'constraints.cpu_availability'
                and (version_guarded or any(identity in by_id
                     and by_id[identity].dimension == 'constraints.cpu_version'
                     for identity in group.depends_on)))
            if (not context['$CHECK_CPU_VERSION']
                    and (group.dimension == 'constraints.cpu_version' or version_availability)):
                return outcome(group, 'not_checked', 'cpu_version_check_not_requested')
            conditional_cpu = None
            if group.dimension == 'constraints.cpu_availability':
                conditional_cpu = _conditional_cpu_availability(group, by_id, plc_model)
                if conditional_cpu is not None and conditional_cpu[0] != 'passed':
                    return outcome(group, *conditional_cpu)
            unknown = False
            for condition in group.conditions:
                try:
                    if not _condition(condition, values, context):
                        return outcome(group, 'not_applicable')
                except (DefinitionError, TypeError, ValueError):
                    unknown = True
            if unknown:
                return outcome(group, 'unverified', 'condition_not_resolved')
            # Applicability is distinct from the rule's verification. A
            # formally excluded SFC/structured overload does not constrain an
            # ordinary Ladder call; an unresolved condition never excludes it.
            if group.status != 'source_verified' or group.verification != 'source_checked':
                return outcome(group, 'not_applicable' if group.status == 'not_applicable' else 'unverified',
                               'source_conflict' if group.status == 'conflict' else 'constraint_not_source_checked')
            if any(source.get('conditions') or source.get('applicability') for source in group.sources):
                return outcome(group, 'unverified', 'source_conditions_require_binding')
            # A derived PWM predicate proves only its selected relation. Its
            # witness scope/conditions and prerequisite facts still apply; a
            # provenance ID alone must never turn missing evidence into failure.
            if group.id.startswith('pwm-formal:'):
                witness_ids = group.value.get('live_source_witnesses')
                if not isinstance(witness_ids, (list, tuple)) or not witness_ids:
                    return outcome(group, 'unverified', 'source_witness_missing')
                for witness_id in witness_ids:
                    witness = by_id.get(witness_id)
                    if witness is None:
                        return outcome(group, 'unverified', 'source_witness_missing')
                    witness_scope = _scope(witness, target=target, model=plc_model,
                                           language=language, version=version)
                    if witness_scope != 'passed':
                        return outcome(group, witness_scope, 'source_witness_scope')
                    if (witness.status != 'source_verified' or witness.verification != 'source_checked'
                            or any(s.get('conditions') or s.get('applicability') for s in witness.sources)):
                        return outcome(group, 'unverified', 'source_witness_unverified')
                    try:
                        if not all(_condition(c, values, context) for c in witness.conditions):
                            return outcome(group, 'not_applicable', 'source_witness_condition')
                    except (DefinitionError, TypeError, ValueError):
                        return outcome(group, 'unverified', 'source_witness_condition_unresolved')
                    if any(visit(dependency) != 'passed' for dependency in witness.depends_on):
                        return outcome(group, 'unverified', 'source_witness_dependency_unresolved')
            dependencies = [visit(dependency) for dependency in group.depends_on]
            if any(status not in {'passed', 'not_checked'} for status in dependencies):
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
            if (target in {'PWM', 'DPWM'} and group.id.startswith('fx5prog:')
                    and plc_model in {'FX5S', 'FX5UJ', 'FX5U', 'FX5UC'}):
                from plc.pwm_contract import evaluate_pwm_source_constraint
                authored = evaluate_pwm_source_constraint(group, values, context, plc_model)
                if authored is not None:
                    return outcome(group, *authored)
            if (group.dimension == 'constraints.source_cpu_header'
                    and (plc_model not in group.scope.get('models', ())
                         or target not in group.scope.get('forms', ())
                         or language not in group.scope.get('languages', ()))):
                return outcome(group, 'unverified', 'source_cpu_header_scope_required')
            if conditional_cpu is not None:
                return outcome(group, *conditional_cpu)
            additional = _additional_constraint(group.dimension, value, values, context, plc_model,
                                                target=target, registry=registry)
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
                return outcome(group, *_numeric_range_check(value, values))
            return outcome(group, 'unverified', 'constraint_not_formalized')
        finally:
            visiting.remove(identity)

    roots = [g.id for g in by_id.values() if g.dimension.startswith('constraints.')
             and g.value.get('phase', 'input') in {phase, 'both'}]
    if root_ids is not None:
        selected = list(root_ids) if isinstance(root_ids, (list, tuple)) else []
        if (not selected or any(not isinstance(i, str) for i in selected)
                or len(set(selected)) != len(selected)
                or any(identity not in roots for identity in selected)):
            return {'status': 'unverified', 'target': target, 'phase': phase,
                'checks': [], 'findings': [{'code': 'PLC_RULE_COVERAGE', 'severity': 'unverified',
                    'message': 'invalid_or_missing_selected_constraint_root'}],
                'root_selection': selected, 'coverage': 'selected_roots_only'}
        roots = selected
    for identity in roots:
        cached[identity] = visit(identity)
    statuses = [row['status'] for row in checks]
    execution_qualifications = [g.id for g in by_id.values()
        if g.dimension == 'execution.source_qualifications'
        and _scope(g, target=target, model=plc_model, language=language, version=version) != 'not_applicable']
    return {'root_selection': roots, 'coverage': 'all_constraint_roots' if root_ids is None else 'selected_roots_only',
            'execution_coverage': 'unverified' if execution_qualifications else 'not_assessed',
            'execution_qualifications': execution_qualifications, 'status': 'failed' if 'failed' in statuses else 'unverified' if 'unverified' in statuses else 'passed',
            'checks': checks, 'findings': findings, 'phase': phase, 'target': target,
            'cpu_version_check': 'enabled' if context['$CHECK_CPU_VERSION'] else 'not_requested'}


def check_instruction_constraints(opcode, values, *, plc_model, language=None, version=None,
                                  context=None, phase='input', registry=None, root_dimensions=None,
                                  signature_cache=None):
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    opcode, plc_model = str(opcode).strip().upper(), str(plc_model).strip().upper()
    members = _cpu_members(plc_model)
    if len(members) > 1:
        return _combined_reports([check_instruction_constraints(opcode, values, plc_model=model, language=language,
            version=version, context=context, phase=phase, registry=registry,
            root_dimensions=root_dimensions, signature_cache=signature_cache)
            for model in members], target=opcode, model=plc_model)
    if not registry.fact_bundle_current():
        return _stale_report(opcode)
    store = getattr(registry, '_definition_owner_store', None)
    cache = None
    if store is not None:
        # Loaded owners and family tuples are immutable. Check freshness above
        # before reusing facts, including the expensive hydrated owner packet.
        cache = getattr(registry, '_constraint_fact_cache', None)
        if (cache is None or cache[0] is not store
                or cache[1] is not registry._family_knowledge
                or cache[2] is not registry._knowledge_profiles):
            cache = (store, registry._family_knowledge, registry._knowledge_profiles, {})
            registry._constraint_fact_cache = cache
    key = (opcode, plc_model)
    if cache is not None and key in cache[3]:
        facts = cache[3][key]
    else:
        entry = registry.definition_entry(opcode, cpu=plc_model)
        if entry is None:
            return {'status': 'unverified', 'target': opcode, 'checks': [], 'findings': [
                {'code': 'PLC_RULE_COVERAGE', 'severity': 'unverified', 'message': 'instruction_facts_missing:' + opcode}]}
        facts = entry.get('facts', [])
        if getattr(registry, '_family_knowledge', ()):
            from plc.instruction_knowledge import compose_programming_facts
            facts, _ = compose_programming_facts(registry, opcode, cpu=plc_model, existing=facts)
        facts = tuple(fact if isinstance(fact, InstructionFactGroup) else InstructionFactGroup.from_mapping(fact)
                      for fact in facts)
        if cache is not None:
            cache[3][key] = facts
    roots = None
    if root_dimensions is not None:
        roots = [f.id for f in facts if f.dimension in root_dimensions
                 and f.value.get('phase', 'input') in {phase, 'both'}]
        if not roots:
            return {'status': 'passed', 'target': opcode, 'checks': [], 'findings': []}
    return check_fact_constraints(facts, values, target=opcode, plc_model=plc_model,
                                  language=language, version=version, context=context, phase=phase,
                                  registry=registry, root_ids=roots,
                                  signature_cache=signature_cache if cache is not None else None)


def check_language_constraints(target, values, *, plc_model, version=None, context=None,
                               phase='input', registry=None, language='st'):
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    target, plc_model = str(target).strip().upper(), str(plc_model).strip().upper()
    language = str(language).strip().lower()
    members = _cpu_members(plc_model)
    if len(members) > 1:
        return _combined_reports([check_language_constraints(target, values, plc_model=model,
            version=version, context=context, phase=phase, registry=registry, language=language)
            for model in members], target=target, model=plc_model)
    if not registry.fact_bundle_current():
        return _stale_report(target)
    entry = registry.language_feature(target, language=language)
    if entry is None:
        return {'status': 'unverified', 'checks': [], 'findings': [{'code': language.upper() + '_RULE_COVERAGE',
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
                                    language=language, version=version, context=context, phase=phase)
    for reference in entry.get('references', []):
        if plc_model not in reference.get('models', [plc_model]):
            continue
        bound = {name.upper(): values.get(source, UNKNOWN) for name, source in reference['parameters'].items()}
        child = check_instruction_constraints(reference['opcode'], bound, plc_model=plc_model, language=language,
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
    # A reviewed record's storage extent is not one wide scalar value.
    integer_layouts = []
    for group in spec.definition_facts:
        if (group.dimension != 'parameters.typed_operands' or group.status != 'source_verified'
                or group.verification != 'source_checked'):
            continue
        parameters = group.value.get('operands')
        if (not (group.value.get('native_form') or group.value.get('representation') == 'native_ladder_instruction')
                or not isinstance(parameters, list) or len(parameters) != len(spec.native_operand_order)
                or tuple(str(p.get('name', '')).upper() for p in parameters if isinstance(p, Mapping))
                    != tuple(name.upper() for name in spec.native_operand_order)
                or not 0 <= index < len(parameters)):
            continue
        layout = parameters[index].get('word_device_layout')
        if isinstance(layout, Mapping) and (layout.get('single_scalar') is False
                                            or layout.get('kind') == 'independent_elements'):
            return None
        if (isinstance(layout, Mapping) and type(layout.get('signed')) is bool
                and layout.get('value_bits') in {8, 16, 32, 64}):
            integer_layouts.append((layout['value_bits'], layout['signed']))
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
        if operand.data_type in {'real', 'lreal'}:
            return ValueType.from_mapping({'kind': 'real', 'bits': 32 if operand.data_type == 'real' else 64})
        if operand.data_type in {'word', 'dword'}:
            bits = 32 if operand.data_type == 'dword' else 16 * operand.word_count
            if bits not in {8, 16, 32, 64}:
                return None
            signed = next((signed for width, signed in integer_layouts if width == bits), True)
            return ValueType.from_mapping({'kind': 'int', 'bits': bits, 'signed': signed})
    if 'instruction_width' in spec.verified_fields and spec.instruction_width in {16, 32, 64}:
        return ValueType.from_mapping({'kind': 'int', 'bits': spec.instruction_width, 'signed': True})
    return None


def literal_operand_values(spec, operands, *, plc_model=None, opcode=None):
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
        elif text.startswith('E') and plc_model is not None:
            from plc.storage.errors import StorageRuleError
            value_type = numeric_operand_type(spec, index)
            if (value_type is None or value_type.kind != 'real'
                    or 'operand_roles' not in spec.verified_fields or index >= len(spec.operands)
                    or spec.operands[index].role.value not in {'read', 'read_write'}):
                continue
            try:
                from plc.storage.tables import storage_profile
                if storage_profile(plc_model) in {'fx', 'fx1s', 'fx3g', 'fx3u'} and value_type.bits == 32:
                    from plc.storage.operands_fx import real_literal_value as fx_real_literal_value
                    number = fx_real_literal_value(text)
                else:
                    from plc.storage.q_encoder import real_literal_value
                    number = value_type.decode_bits(real_literal_value(
                        text, op=opcode or spec.mnemonic, index=index, cpu=plc_model))
            except (StorageRuleError, DefinitionError):
                continue
        else:
            continue
        values['$' + str(index)] = number
        if index < len(spec.native_operand_order):
            values[spec.native_operand_order[index].upper()] = number
    return values


def instruction_program_accesses(ladder, *, plc_model):
    """Reuse the canonical IR's reads/writes and instruction identities."""
    from plc.ir import lower_rung_instructions, analyze_instruction_access, analyze_instruction_access_regions
    rows = []
    configured_positions = {}
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    for index, rung in enumerate(ladder.get('rungs', [])):
        for instruction in lower_rung_instructions(rung):
            reads, writes = analyze_instruction_access(instruction['op'], instruction['args'], plc_model=plc_model)
            opcode = instruction['op']
            if opcode not in configured_positions:
                entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu=plc_model) or {}
                configured_positions[opcode] = [region['operand_index'] for fact in entry.get('facts', [])
                    if fact['dimension'] == 'constraints.reserved_regions' for region in fact['value']['regions']
                    if region.get('access') == 'all']
            configured = [instruction['args'][pos] for pos in configured_positions[opcode] if pos < len(instruction['args'])]
            row = {'id': (index, instruction.get('path')), 'opcode': instruction['op'],
                   'operands': list(instruction['args']), 'reads': reads, 'writes': writes,
                   'configured_uses': configured}
            regions = analyze_instruction_access_regions(instruction['op'], instruction['args'], plc_model=plc_model)
            if regions:
                row['access_regions'] = regions
            rows.append(row)
    return rows


def inspect_ladder_constraints(ladder, *, plc_model, context=None, root_dimensions=None):
    from plc.ir import lower_rung_instructions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    reports = []
    context = {**(context or {}), 'project_mode': 'native_ladder', 'program_context': 'ladder',
               'program_accesses': instruction_program_accesses(ladder, plc_model=plc_model)}
    if any(region.get('requires') for access in context['program_accesses']
           for region in access.get('access_regions', [])):
        context['program_accesses_complete'] = False
    for index, rung in enumerate(ladder.get('rungs', [])):
        for instruction in lower_rung_instructions(rung):
            form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(instruction['op'], cpu=plc_model)
            if form is None:
                continue
            report = check_instruction_constraints(form.opcode,
                literal_operand_values(form.spec, instruction['args'], plc_model=plc_model, opcode=form.opcode), plc_model=plc_model,
                language='ladder', version=context.get('software_version'),
                context={**context, 'current_instruction': (index, instruction.get('path'))},
                root_dimensions=root_dimensions)
            if report['checks']:
                reports.append({**report, 'rung_id': rung.get('id'),
                                'path': f'$.rungs[{index}]' + ('.' + instruction['path'] if instruction.get('path') else '')})
    return reports

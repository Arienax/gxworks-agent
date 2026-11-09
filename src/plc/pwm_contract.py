"""Source-bounded PWM facts and binding, shared by static and software execution.

This is not PLC admission. The full instruction graph retains unresolved source
qualifications. The optional software contract models only stable, unlimited,
CPU-module output with known configuration; it never authorizes physical I/O.
"""
from collections.abc import Mapping
import re
from plc.instruction_definition import UnknownInstructionSemantics

MODELS = ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')
from plc.pwm_fact_source import (profile, source_binding, rule_supported,
    snapshot_facts, source_identity_matches, load_contract)

def __getattr__(name):
    # Compatibility exports are lazy: unrelated PLC imports need no PWM data.
    if name == 'SOURCE_BINDING':
        return source_binding()
    if name == 'PWM_AB_MINIMUM_TIMES':
        return tuple(profile('PWM', 'FX5U')['AB_timing'])
    raise AttributeError(name)


def pwm_formal_facts(opcode, model):
    """Generated predicates; their live source witnesses are bound by derivation."""
    if opcode not in {'PWM', 'DPWM'} or model not in MODELS:
        return []
    config = profile(opcode, model)
    facts = snapshot_facts(opcode, model)
    return [{'id': 'pwm-formal:' + opcode + ':' + model + ':' + name,
        'dimension': 'constraints.pwm_' + name, 'status': 'source_verified',
        'verification': 'source_checked', 'scope': {'models': [model], 'forms': [opcode],
            'languages': ['ladder', 'st', 'fbd']},
        'sources': [dict(source) for fact in facts for source in fact['sources']],
        'value': {'predicate': predicate, 'binding_contract': source_binding(),
            'coverage': 'bounded_predicate_not_whole_instruction_admission'}}
        for name, predicate in config['predicates'].items()]


def bind_pwm_values(opcode, model, values, context):
    """Bind explicit values only. Unknowns and contradictory aliases stay unknown.

    No memory reads, implicit zero, integer wrapping, or inferred device/channel
    mappings occur here. Destination numeric value and device token are distinct.
    """
    config = profile(opcode, model)
    result = dict(values)
    # Callers cannot inject the conclusions of reviewed predicates.
    for key in list(result):
        if key.startswith('PWM_'):
            del result[key]
    tokens = values.get('$OPERANDS')
    if tokens is not None and (not isinstance(tokens, (list, tuple)) or len(tokens) != 3):
        return {k: v for k, v in result.items() if not k.startswith('PWM_')}
    tokens = tokens if isinstance(tokens, (list, tuple)) else ()
    pwm = context.get('pwm', {})
    hardware = context.get('hardware', {})
    pwm = pwm if isinstance(pwm, Mapping) else {}
    hardware = hardware if isinstance(hardware, Mapping) else {}
    def number(name, index):
        candidates = [values[k] for k in (name, '$' + str(index)) if k in values]
        if not candidates and index < len(tokens) and isinstance(tokens[index], str):
            token = tokens[index].strip().upper()
            if re.fullmatch(r'K[+-]?\d+', token): candidates = [int(token[1:])]
            elif re.fullmatch(r'H[0-9A-F]+', token): candidates = [int(token[1:], 16)]
        if not candidates or any(type(v) is not int for v in candidates): return None
        if len(set(candidates)) != 1: return None
        return candidates[0]
    on, period = number('S1', 0), number('S2', 1)
    for name, index, value in [('S1', 0, on), ('S2', 1, period)]:
        if value is not None:
            result[name] = result['$' + str(index)] = value
    if on is not None and period is not None:
        maximum = profile(opcode, model)['maximum_ON']
        # PWM negative literals need the language's unsigned interpretation.
        if on >= 0 and period >= 0 or opcode == 'DPWM':
            result['PWM_WIDTH_VALID'] = 0 <= on <= maximum and 0 <= period <= config['maximum_period']
        if on >= config['domain_minimum'] and period >= config['domain_minimum']:
            result['PWM_DOMAIN_VALID'] = True
            result['PWM_ON_POSITIVE'], result['PWM_PERIOD_POSITIVE'] = on, period
        elif opcode == 'DPWM' and (on < 0 or period < 0):
            result['PWM_DOMAIN_VALID'] = False
    raw = tokens[2] if len(tokens) > 2 else values.get('D')
    token = raw.strip().upper() if isinstance(raw, str) else None
    if token is not None: result['$PWM_DESTINATION_TOKEN'] = token
    module = hardware.get('module_type')
    if not isinstance(module, str): module = None
    representation = None
    channel = None
    output = None
    if token and re.fullmatch(r'Y[0-7]+', token):
        representation = 'bit_device'
        output = 'Y' + format(int(token[1:], 8), 'o')
        valid = output in config['CPU_outputs']
        if not valid: result['PWM_DESTINATION_VALID'] = False
        elif module == 'CPU_module': result['PWM_DESTINATION_VALID'] = True
        elif module == 'high_speed_pulse_IO_module': result['PWM_DESTINATION_VALID'] = False
        if type(pwm.get('channel')) is int: channel = pwm['channel']
        if valid and 'output_device' in pwm and pwm['output_device'] != output:
            result.pop('PWM_DESTINATION_VALID', None)  # inconsistent mapping is not evidence of an illegal program
        if type(pwm.get('unused_channel_available')) is bool:
            result['PWM_UNUSED_CHANNEL'] = pwm['unused_channel_available']
    elif token and (re.fullmatch(r'[KH][+-]?[0-9A-F]+', token) or re.fullmatch(r'(?:D|W|R|SD|SW|T|ST|C|Z)\d+', token)):
        representation = 'channel'
        channel = number('D', 2)
        if channel is not None and module in {'CPU_module', 'high_speed_pulse_IO_module'}:
            low, high = config['channel_ranges'][module]
            result['PWM_DESTINATION_VALID'] = low <= channel <= high
        result['PWM_UNUSED_CHANNEL'] = True  # Y-only requirement not applicable
        candidate = pwm.get('output_device')
        if isinstance(candidate, str) and candidate in config['CPU_outputs']: output = candidate
        if (result.get('PWM_DESTINATION_VALID') is not False and type(pwm.get('channel')) is int
                and channel is not None and pwm['channel'] != channel):
            result.pop('PWM_DESTINATION_VALID', None)
    elif token and (re.fullmatch(r'(?:X|M|L|SM|F|B|SB|S)\d+', token)
                    or re.fullmatch(r'K[1-8](?:X|Y|M|S|B|SM)\w+', token)):
        result['PWM_DESTINATION_VALID'] = False
    if representation is not None:
        result['$PWM_REPRESENTATION'] = representation
    if channel is not None: result['$PWM_CHANNEL'] = channel
    if output is not None: result['$PWM_OUTPUT'] = output
    if type(pwm.get('configured_for_PWM')) is bool:
        result['PWM_CONFIGURED'] = pwm['configured_for_PWM']
    if 'resource_owner' in pwm:
        if pwm['resource_owner'] is None: result['PWM_RESOURCE_FREE'] = True
        elif pwm['resource_owner'] == 'positioning_instruction': result['PWM_RESOURCE_FREE'] = False
    program = context.get('program_context')
    if not isinstance(program, str): program = None
    if program in {'ladder', 'scan_program', 'st', 'fbd'}: result['PWM_PROGRAM_VALID'] = True
    elif program == 'interrupt_program': result['PWM_PROGRAM_VALID'] = False
    unit = pwm.get('time_unit')
    if not isinstance(unit, str): unit = None
    if unit in {'us', 'ms'}:
        result['PWM_UNIT_KNOWN'] = True
        if on is not None and period is not None:
            scale = 1 if unit == 'us' else 1000
            on_us, period_us = on * scale, period * scale
            result['$PWM_ON_US'], result['$PWM_PERIOD_US'] = on_us, period_us
            # Do not invent a steady-ON/OFF exception to the precaution.
            if 0 < on < period:
                result['PWM_ON_US_PULSING'], result['PWM_PERIOD_US_PULSING'] = on_us, period_us
                if (output is not None and module == 'CPU_module' and model in MODELS
                        and hardware.get('output_type') == 'transistor'
                        and on_us <= max(r['maximum_ON_us'] for r in profile(opcode, model)['AG_timing'])
                        and period_us <= max(r['maximum_period_us'] for r in profile(opcode, model)['AG_timing'])):
                    rows = [r for r in profile(opcode, model)['AB_timing'] if model in r['models']
                            and module == r['hardware'] and output in r['outputs']]
                    if len(rows) == 1:
                        row = rows[0]
                        ab = on_us >= row['minimum_ON_us'] and period_us >= row['minimum_period_us']
                        ag_rows = [r for r in profile(opcode, model)['AG_timing']
                            if model in r['models'] and module == r['hardware'] and output in r['outputs']]
                        if len(ag_rows) == 1:
                            ag_row = ag_rows[0]
                            ag = on_us >= ag_row['minimum_ON_us'] and period_us >= ag_row['minimum_period_us']
                            if ab == ag:
                                result['PWM_TIMING_VALID'] = ab
    return result


def validate_pwm_request(opcode, model, on_time, period, destination, context):
    """Validate the explicit software-model subset through the formal checker.

    UnknownInstructionSemantics means unsupported/unknown, not an illegal PLC
    program. ValueError means a source-backed known constraint violation.
    """
    from plc.instruction_constraints import check_fact_constraints
    opcode, model = str(opcode).upper(), str(model).upper()
    if opcode not in {'PWM', 'DPWM'} or model not in MODELS:
        raise UnknownInstructionSemantics('pwm_model_or_form_not_supported')
    if not isinstance(context, Mapping):
        raise UnknownInstructionSemantics('pwm_context_required')
    pwm, hardware = context.get('pwm', {}), context.get('hardware', {})
    if not isinstance(pwm, Mapping) or not isinstance(hardware, Mapping):
        raise UnknownInstructionSemantics('pwm_context_required')
    # These are coverage boundaries, not claims of source illegality.
    requirements = {'execution_mode': 'core_software_model', 'initially_idle': True,
                    'pulse_count': 0, 'source_binding': source_binding()}
    if any(type(pwm.get(k)) is not type(v) or pwm.get(k) != v for k, v in requirements.items()):
        raise UnknownInstructionSemantics('pwm_bounded_software_contract_required')
    if hardware.get('module_type') != 'CPU_module' or hardware.get('output_type') != 'transistor':
        raise UnknownInstructionSemantics('pwm_CPU_transistor_software_subset_only')
    values = {'S1': on_time, 'S2': period, '$0': on_time, '$1': period,
              '$OPERANDS': [None, None, destination]}
    if type(pwm.get('channel')) is int and isinstance(destination, str) and re.fullmatch(r'(?:D|W|R|SD|SW|T|ST|C|Z)\d+', destination.upper()):
        # The runtime must supply a resolved numeric operand, not merely an
        # unrelated configured channel. Device memory is never guessed here.
        if type(pwm.get('destination_value')) is int: values['$2'] = pwm['destination_value']
    language = context.get('language', 'ladder')
    if not isinstance(language, str) or language not in {'ladder', 'st', 'fbd'}:
        raise UnknownInstructionSemantics('pwm_language_not_supported')
    bound = bind_pwm_values(opcode, model, values, context)
    # A live owner, including an empty/withdrawn owner, always takes priority.
    # The named snapshot is usable only when this owner was never installed.
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    registry = DEFAULT_INSTRUCTION_REGISTRY
    if not registry.fact_bundle_current():
        raise UnknownInstructionSemantics('pwm_live_fact_bundle_stale')
    entry = registry.definition_entry(opcode, cpu=model)
    groups = snapshot_facts(opcode, model) if entry is None else entry.get('facts', [])
    required_ids = {fact['id'] for fact in pwm_formal_facts(opcode, model)}
    report = check_fact_constraints(groups, bound, target=opcode,
        plc_model=model, language=language, version=context.get('software_version'), context=context,
        root_ids=sorted(required_ids))
    report['source_mode'] = 'version_bound_snapshot' if entry is None else 'live_owner'
    report['source_binding'] = source_binding()
    passed_ids = {row['fact_id'] for row in report['checks'] if row['status'] == 'passed'}
    if report['status'] != 'passed' or not required_ids <= passed_ids:
        error = ValueError('pwm_known_constraint_violation') if report['status'] == 'failed' else UnknownInstructionSemantics('pwm_constraint_unverified')
        error.constraint_report = report
        raise error
    channel = bound.get('$PWM_CHANNEL')
    if type(channel) is not int or not 1 <= channel <= 4 or bound.get('$PWM_OUTPUT') is None:
        raise UnknownInstructionSemantics('pwm_resolved_CPU_channel_and_output_required')
    return {'channel': channel, 'output_device': bound['$PWM_OUTPUT'], 'on_time': on_time,
            'period': period, 'on_time_us': bound['$PWM_ON_US'], 'period_us': bound['$PWM_PERIOD_US'],
            'source_binding': source_binding(), 'time_unit': pwm['time_unit'], 'constraint_report': report,
            'coverage': 'bounded_software_model_only', 'whole_graph_admission': 'not_established'}


def _supported_pwm_rule_shape(rule):
    """Only exact reviewed relations, with no ignored condition or extra key."""
    return rule_supported(rule)


def derive_pwm_formal_facts(opcode, model, language, groups):
    """Attach the fixed reviewed contract only where the live graph has witnesses.

    Losing, downgrading, or rescoping a witness invalidates that derived check.
    A fixed software-contract snapshot is separately named and never advertised
    as current registry-wide admission.
    """
    supported = []
    for group in groups:
        scope = group.scope or {}
        if (group.status != 'source_verified' or group.verification != 'source_checked'
                or any(source.get('conditions') or source.get('applicability') for source in group.sources)
                or any(not isinstance(c, Mapping) or c.get('kind') != 'operand_form' or not set(c.get('required_operands', [])) <= {'s1', 's2', 'd'} for c in group.conditions)
                or not source_identity_matches(group, opcode, model)
                or model not in scope.get('models', [model])
                or opcode not in scope.get('forms', [opcode])
                or language not in scope.get('languages', [language])):
            continue
        supported.append(group)
    kinds = {}
    for group in supported:
        for rule in group.value.get('rules', []):
            if _supported_pwm_rule_shape(rule):
                kinds.setdefault(rule.get('kind'), []).append(group)
    witnesses = {
        'operand_width': [g for g in supported if g.dimension == 'parameters.storage_domain'
                          and any(_supported_pwm_rule_shape(r) for r in g.value.get('rules', []))],
        'operand_domain': [g for g in kinds.get('any_value_less_than', [])
                           if any(_supported_pwm_rule_shape(r) and r.get('kind') == 'any_value_less_than' and r.get('value') == 1
                               and r.get('logical_connective') == 'OR' and r.get('subjects') == [{'operand':'s1'}, {'operand':'s2'}] and r.get('error') is True
                               for r in g.value.get('rules', []))],
        'operand_relation': [g for g in kinds.get('operand_relation', [])
                             if any(r == {'kind': 'operand_relation', 'left': {'operand':'s1'}, 'operator': '>',
                                          'right': {'operand':'s2'}, 'error': True} for r in g.value.get('rules', []))],
        'destination': kinds.get('allowed_device_classes', []) + kinds.get('allowed_interval', []),
        'channel_configuration': [g for g in kinds.get('PWM_channel_configuration', []) if any(
            r in [{'kind': 'PWM_channel_configuration', 'channel': {'operand': 'd'},
                   'when': 'not_selected_for_PWM_in_parameters', 'execution': False},
                  {'kind': 'PWM_channel_configuration', 'when': 'channel_not_selected_for_PWM_in_parameters',
                   'operation_error': True}] for r in g.value.get('rules', []))],
        'resource': [g for g in kinds.get('resource_in_use', []) if any(
            _supported_pwm_rule_shape(r) and r.get('kind') == 'resource_in_use' and r.get('resource') == 'pulse_output_destination'
            and r.get('owner') == 'positioning_instruction' and r.get('subject') == {'operand': 'd'}
            for r in g.value.get('rules', []))],
        'unused_channel': [g for g in kinds.get('conditional_result', [])
                           if any(r == {'kind': 'conditional_result',
                               'when': {'all': [{'operand': 'd', 'device_class': 'Y'},
                                                {'parameter_unused_channel_available': False}]},
                               'result': {'operation_error': True, 'output_effect': 'not_established'}}
                                  for r in g.value.get('rules', []))],
        'time_unit': [g for g in supported if g.dimension == 'parameters.control_status_storage'
                      and any(r.get('unit') == 'parameter_selected_us_or_ms' and _supported_pwm_rule_shape(r)
                              and r.get('when') == {'instruction_executed': opcode} for r in g.value.get('rules', []))],
        'hardware_timing': [g for g in kinds.get('PWM_hardware_minimum_times', [])
                            if any(_supported_pwm_rule_shape(r) and r.get('rows') == profile(opcode, model)['AB_timing'] for r in g.value.get('rules', []))],
        'off_time': [g for g in kinds.get('PWM_minimum_OFF_time', [])
                     if any(r == {'kind':'PWM_minimum_OFF_time', 'ON_time':{'operand':'s1'}, 'period':{'operand':'s2'},
                                  'minimum_period_minus_ON_time_us':profile(opcode, model)['minimum_OFF_us']} for r in g.value.get('rules', []))],
        'program_context': [g for g in kinds.get('invalid_program_context', []) if any(
            _supported_pwm_rule_shape(r) and r.get('kind') == 'invalid_program_context' and r.get('context') == 'interrupt_program'
            for r in g.value.get('rules', []))],
    }
    width_operands = {r.get('subject', {}).get('operand')
        for g in witnesses['operand_width'] for r in g.value.get('rules', [])
        if _supported_pwm_rule_shape(r) and r.get('kind') == 'allowed_interval' and r.get('minimum') == 1
        and r.get('maximum') == profile(opcode, model)['maximum_ON']}
    if width_operands != {'s1', 's2'}:
        witnesses['operand_width'] = []
    channel_witnesses = [g for g in kinds.get('allowed_interval', []) if any(
        any(r == {'kind': 'allowed_interval', 'subject': {'operand': 'd'},
                  'when': {'representation_any_of': ['word_device', 'constant'], 'hardware': hw},
                  'quantity': 'channel_number', 'minimum': low, 'maximum': high, 'inclusive': True}
            for hw, low, high in [('CPU_module', 1, 4), ('high_speed_pulse_IO_module', 5, 12)])
        for r in g.value.get('rules', []))]
    device_witnesses = [g for g in kinds.get('allowed_device_classes', []) if any(
        r == {'kind': 'allowed_device_classes', 'subject': {'operand': 'd'},
              'when': {'representation': 'bit_device'}, 'allowed': ['Y'],
              'allowed_addresses': ['Y' + str(n) for n in range(8)], 'hardware': 'CPU_module'}
        for r in g.value.get('rules', []))]
    witnesses['destination'] = channel_witnesses + device_witnesses if channel_witnesses and device_witnesses else []
    result = pwm_formal_facts(opcode, model)
    for fact in result:
        evidence = witnesses.get(fact['id'].rsplit(':', 1)[-1], [])
        fact['value']['live_source_witnesses'] = list(dict.fromkeys(g.id for g in evidence))
        # Preserve dependencies of the reviewed relation, not every unrelated
        # rule in a compound source group. The checker separately validates
        # each witness's effective scope and conditions before using this rule.
        fact['depends_on'] = sorted({dependency for g in evidence for dependency in g.depends_on})
        if evidence:
            fact['sources'] = [dict(source) for g in evidence for source in g.sources]
            if fact['id'].endswith(':hardware_timing'):
                supplemental = load_contract()[0]['supplemental_facts']
                fact['sources'].extend(dict(s) for f in supplemental for s in f['sources'])
                fact['value']['supplemental_source_binding'] = source_binding()
                fact['value']['cross_source_policy'] = 'consensus_without_precedence'
            version_scopes = [set(g.scope['versions']) for g in evidence if g.scope.get('versions')]
            if version_scopes:
                versions = set.intersection(*version_scopes)
                if versions:
                    fact['scope']['versions'] = sorted(versions)
                else:
                    # Empty scope means unrestricted to the generic checker,
                    # so incompatible witnesses must instead stay unverified.
                    fact['status'] = 'declared_unverified'
                    fact['verification'] = 'not_performed'
                    fact['value']['source_witness_scope_conflict'] = True
        if not evidence:
            fact['status'] = 'declared_unverified'
            fact['verification'] = 'not_performed'
            fact['value']['source_witness_missing'] = True
    return result


def evaluate_pwm_source_constraint(group, values, context, model):
    """Compile supported authored rule shapes to the SAME formal predicates.

    Unknown rule shapes are never treated as satisfied. A compound root is
    passed only when every constituent has a supported proof.
    """
    from plc.instruction_definition import evaluate_expression, DefinitionError
    rules = group.value.get('rules', [])
    if not rules:
        return None
    hardware = context.get('hardware', {})
    hardware = hardware if isinstance(hardware, Mapping) else {}
    module = hardware.get('module_type')
    if not isinstance(module, str): module = None
    opcode = next((op for op in (group.scope or {}).get('forms', []) if op in {'PWM', 'DPWM'}), None)
    if opcode is None:
        return 'unverified', 'pwm_source_form_missing'
    predicates = profile(opcode, model)['predicates']
    by_parameter = {'PWM_WIDTH_VALID':'operand_width', 'PWM_DOMAIN_VALID':'operand_domain',
        'PWM_RELATION_VALID':'operand_relation', 'PWM_DESTINATION_VALID':'destination',
        'PWM_CONFIGURED':'channel_configuration', 'PWM_RESOURCE_FREE':'resource',
        'PWM_UNUSED_CHANNEL':'unused_channel', 'PWM_UNIT_KNOWN':'time_unit',
        'PWM_TIMING_VALID':'hardware_timing', 'PWM_OFF_TIME_VALID':'off_time',
        'PWM_PROGRAM_VALID':'program_context'}
    statuses = []
    for rule in rules:
        if not _supported_pwm_rule_shape(rule):
            statuses.append('unverified'); continue
        kind = rule.get('kind')
        name = None
        if kind == 'resource_in_use' and rule.get('owner') == 'positioning_instruction' and rule.get('resource') == 'pulse_output_destination' and rule.get('subject') == {'operand': 'd'}:
            name = 'PWM_RESOURCE_FREE'
        elif kind == 'invalid_program_context' and rule.get('context') == 'interrupt_program':
            name = 'PWM_PROGRAM_VALID'
        elif kind == 'PWM_channel_configuration' and rule in [
                {'kind': 'PWM_channel_configuration', 'channel': {'operand': 'd'},
                 'when': 'not_selected_for_PWM_in_parameters', 'execution': False},
                {'kind': 'PWM_channel_configuration', 'when': 'channel_not_selected_for_PWM_in_parameters', 'operation_error': True}]:
            name = 'PWM_CONFIGURED'
        elif rule == {'kind': 'PWM_minimum_OFF_time', 'ON_time': {'operand': 's1'},
                      'period': {'operand': 's2'}, 'minimum_period_minus_ON_time_us': profile(opcode, model)['minimum_OFF_us']}:
            name = 'PWM_OFF_TIME_VALID'
        elif kind == 'PWM_hardware_minimum_times' and rule.get('rows') == profile(opcode, model)['AB_timing']:
            name = 'PWM_TIMING_VALID'
        elif (kind == 'operand_relation' and rule.get('left') == {'operand':'s1'}
              and rule.get('right') == {'operand':'s2'} and rule.get('operator') == '>' and rule.get('error') is True):
            name = 'PWM_RELATION_VALID'
        elif (kind == 'any_value_less_than' and rule.get('value') == 1 and rule.get('logical_connective') == 'OR'
              and rule.get('subjects') == [{'operand':'s1'}, {'operand':'s2'}] and rule.get('error') is True):
            name = 'PWM_DOMAIN_VALID'
        elif (kind == 'allowed_interval' and any(rule == {
                'kind': 'allowed_interval', 'subject': {'operand': 'd'},
                'when': {'representation_any_of': ['word_device', 'constant'], 'hardware': hw},
                'quantity': 'channel_number', 'minimum': low, 'maximum': high, 'inclusive': True}
                for hw, low, high in [('CPU_module', 1, 4), ('high_speed_pulse_IO_module', 5, 12)])):
            if values.get('$PWM_REPRESENTATION') == 'bit_device':
                statuses.append('passed'); continue
            if rule.get('when', {}).get('hardware') != module:
                if module in {'CPU_module', 'high_speed_pulse_IO_module'}:
                    statuses.append('passed'); continue
            name = 'PWM_DESTINATION_VALID'
        elif rule == {'kind': 'label_type_disallowed', 'label_type': 'digit_specified_bit',
                      'subjects': [{'operand': 'd'}]}:
            token = values.get('$PWM_DESTINATION_TOKEN')
            if token and re.fullmatch(r'K[1-8](?:X|Y|M|S|B|SM)\w+', token): statuses.append('failed')
            elif values.get('$PWM_REPRESENTATION') is not None: statuses.append('passed')
            else: statuses.append('unverified')
            continue
        elif rule == {'kind': 'PWM_module_interrupt_priority', 'module_channel_range': [5, 12],
                      'forbidden_program_interrupt_priority': 1}:
            channel = values.get('$PWM_CHANNEL')
            if type(channel) is int and 1 <= channel <= 4:
                statuses.append('passed'); continue
            statuses.append('unverified'); continue
        elif (kind == 'allowed_device_classes' and rule == {
                'kind':'allowed_device_classes', 'subject':{'operand':'d'}, 'when':{'representation':'bit_device'},
                'allowed':['Y'], 'allowed_addresses':['Y'+str(n) for n in range(8)], 'hardware':'CPU_module'}):
            if values.get('$PWM_REPRESENTATION') == 'channel':
                statuses.append('passed'); continue
            name = 'PWM_DESTINATION_VALID'
        elif rule == {'kind':'disallowed_device_form', 'subject':{'operand':'d'},
                      'forms':['digit_specified_bit','high_speed_pulse_IO_module_bit_device']}:
            name = 'PWM_DESTINATION_VALID'
        elif rule == {'kind':'value_outside_interval', 'subject':{'context':'pwm.pulse_count'},
                      'minimum':0, 'maximum':2147483647, 'inclusive':True, 'error':True}:
            pwm = context.get('pwm', {})
            count = pwm.get('pulse_count') if isinstance(pwm, Mapping) else None
            statuses.append(('passed' if 0 <= count <= 2147483647 else 'failed') if type(count) is int else 'unverified')
            continue
        elif kind == 'conditional_result':
            when = rule.get('when', {})
            if (when == {'operand':'d','device_class':'Y','minimum_address':'Y10','address_notation':'octal','inclusive':True}
                    and rule.get('result') == {'PWM_output': 'stops'}):
                name = 'PWM_DESTINATION_VALID'
            elif (when == {'all':[{'operand':'d','device_class':'Y'},{'parameter_unused_channel_available':False}]}
                  and rule.get('result') == {'operation_error': True, 'output_effect': 'not_established'}):
                name = 'PWM_UNUSED_CHANNEL'
            elif (when == {'all':[{'operand':'d','device_class':'Y'},
                                  {'parameter_unused_channel_available':True}, {'specified_Y_number_in_use':False}]}
                  and rule.get('result') == {'outputs_enabled':True}):
                if values.get('$PWM_REPRESENTATION') == 'channel':
                    statuses.append('passed'); continue
                pwm = context.get('pwm', {})
                used = pwm.get('specified_Y_number_in_use') if isinstance(pwm, Mapping) else None
                if used is False:
                    name = 'PWM_UNUSED_CHANNEL'
                else:
                    statuses.append('unverified'); continue
        if name is None:
            statuses.append('unverified'); continue
        try:
            statuses.append('passed' if evaluate_expression(predicates[by_parameter[name]], values, state=context) else 'failed')
        except (DefinitionError, TypeError, ValueError, OverflowError):
            statuses.append('unverified')
    status = 'failed' if 'failed' in statuses else 'unverified' if 'unverified' in statuses else 'passed'
    return status, '' if status == 'passed' else 'pwm_source_rule_' + status

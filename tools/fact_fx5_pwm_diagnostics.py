"""Source-bound interpretation of the PWM merged error-code table.

Call only after matching the complete reviewed AB error table in
fact_fx5_condition_review._review_pwm_clause. Physical PDF pages 819/826
(printed 817/824) were checked including merged-cell borders. This is
provenance for diagnostics, not authority to synthesize hardware effects.
"""


def pwm_diagnostic_code_relations(form):
    if form not in {'PWM', 'DPWM'}:
        raise ValueError('Only the reviewed PWM / DPWM forms are supported.')
    causes = [
        {'id': 'on_time_exceeds_period',
         'when': {'left': {'operand': 's1'}, 'operator': '>', 'right': {'operand': 's2'}},
         'output_effect': 'stops', 'output_effect_source': 'explicit'},
        {'id': 'invalid_pulse_width_or_period_SD',
         'when': {'runtime_register_fields': ['ON_time', 'period'], 'validity': 'invalid'},
         'output_effect': 'stops', 'output_effect_source': 'explicit'},
        {'id': 'on_time_or_period_less_than_one',
         'when': {'any': [{'operand': 's1', 'operator': '<', 'value': 1},
                          {'operand': 's2', 'operator': '<', 'value': 1}]},
         'zero_example_qualification': 'unresolved',
         'output_effect': 'not_stated'},
        {'id': 'pulse_count_SD_outside_range',
         'when': {'context': 'pwm.pulse_count', 'outside_inclusive_interval': [0, 2147483647]},
         'output_effect': 'not_stated'},
    ]
    unresolved = []
    if form == 'DPWM':
        unresolved.append({
            'id': 'negative_input_clause', 'subjects': [{'operand': 's1'}, {'operand': 's2'}],
            'predicate': 'negative', 'logical_connective': 'not_established',
            'status': 'declared_unverified', 'output_effect': 'stops',
            'output_effect_source': 'explicit',
        })
    return {
        'source': {'manual_number': 'JY997D55801', 'revision': 'AB',
                   'pdf_page': 819 if form == 'PWM' else 826,
                   'review_basis': 'merged_cell_borders_and_complete_cause_rows'},
        'configuration': {
            'code': '3600H', 'when': 'output_destination_not_selected_in_PWM_parameters',
            'subject': {'operand': 'd'}, 'output_effect': 'not_executed',
        },
        'channel_errors': {
            'code_selector': 'selected_PWM_channel',
            'codes_by_channel': {str(ch): f'{0x3610 + ch:04X}H' for ch in range(1, 13)},
            'cause_binding': 'every_listed_cause_applies_to_every_selected_channel',
            'causes': causes, 'unresolved_causes': unresolved,
        },
        'hardware_diagnostic_write_capability': 'not_established',
    }

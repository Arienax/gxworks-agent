"""Offline, source-bound FX5 SFC, positioning and communication relations.

Every successful branch consumes a complete statement, including its source
antecedent. These facts describe the manual and grant no executor capability.
"""
from __future__ import annotations

import re


def _operand(name, offset=0):
    return {'operand': name, **({'offset': offset} if offset else {})}


def _operands(value):
    if isinstance(value, dict):
        if 'operand' in value:
            yield value['operand']
        for child in value.values():
            yield from _operands(child)
    elif isinstance(value, list):
        for child in value:
            yield from _operands(child)


def review_processing_clause(clause, *, section, block, value, declared):
    page = block['page']
    if not 971 <= page <= 1205:
        return None
    source = block.get('text', '')
    page_source = next((text for number, text in section.get('page_layouts', []) if number == page), '')
    declared = {str(name).casefold() for name in declared}

    def full(pattern):
        return re.fullmatch(pattern, clause, re.I)

    def reviewed(rules, *, dimension='effects.documented_processing', languages=None,
                 conditions=None, content_role='manual_behavior', forms=None, **updates):
        subjects = sorted(set(_operands(rules)))
        if any(name.casefold() not in declared for name in subjects):
            return None
        return {'rules': rules, 'dimension': dimension, 'full_clause_review': True,
                'content_role': content_role,
                **({'languages': languages} if languages else {}),
                **({'conditions': conditions} if conditions else {}),
                **({'forms': forms} if forms else {}),
                'value_updates': {'subjects': [_operand(name) for name in subjects],
                                  'operand_binding_required': bool(subjects),
                                  'numeric_values': [], 'ranges': [], 'comparisons': [],
                                  'source_formulas': [], 'unresolved_glyphs': [],
                                  'unparsed_input_qualification': False,
                                  'unbound_condition_markers': [], 'context_binding': 'resolved',
                                  'knowledge_kind': 'manual_behavior', **updates}}

    # SFC commands may be issued from a sequence program or from an SFC action.
    # Their default block is therefore a conditional relation, not always block 0.
    if page <= 992:
        if full(r'These instructions batch-read \(in units of 32-bit binary data\) the status \(active or inactive\) '
                r'of steps in the specified block\.'):
            return reviewed([{'kind': 'SFC_step_status_read', 'packing_width_bits': 32, 'read_state': 'active_or_inactive'}])
        if full(r'These instructions batch-read \(in units of the specified number of words starting from the specified '
                r'step\) the status \(active or inactive\) of steps in the specified block\.'):
            return reviewed([{'kind': 'SFC_step_status_read', 'packing': 'specified_word_count',
                              'start': 'specified_step', 'read_state': 'active_or_inactive'}])
        if full(r'When a block is not specified, the status \(active or inactive\) of the following block is read\. '
                r'- Sequence program: Block 0 - SFC program \(within the action\): Block where the instruction is executed '
                r'\(current block\)'):
            return reviewed([{'kind': 'SFC_default_read_block', 'when_block_unspecified': True,
                              'cases': [{'program_context': 'sequence_program', 'block': 0},
                                        {'program_context': 'SFC_action', 'block': 'current_execution_block'}]}])
        if full(r'The read data are stored in the device specified by \(d\)\.'):
            return reviewed([{'kind': 'SFC_step_status_destination', 'destination': _operand('d')}])
        active = full(r'When the step is (active|inactive), ([01]) is stored\.')
        if active and (active[1].lower() == 'active') == (active[2] == '1'):
            return reviewed([{'kind': 'SFC_step_status_bit', 'step_state': active[1].lower(), 'stored_bit': int(active[2])}])
        if full(r'When there is a missing step No\., 0 is stored in the corresponding bit\.'):
            return reviewed([{'kind': 'SFC_step_status_bit', 'step_state': 'nonexistent', 'stored_bit': 0}])
        ignored = full(r'If the instruction is executed to an? (active|inactive) (block|step), '
                       r'the instruction is ignored and processing will continue\.')
        if ignored:
            return reviewed([{'kind': 'SFC_conditional_no_operation', 'target': ignored[2].lower(),
                              'when_target_state': ignored[1].lower(), 'program_processing_continues': True}])
        if full(r'Note that if the specified step is holding the operating status, the hold status is reset\.'):
            return reviewed([{'kind': 'SFC_step_hold_release', 'when_target_state': 'holding_operating_status',
                              'effect': 'reset_hold_status'}])
        if full(r'The step becomes a normal step, and executes the action and transition\.') and 'hold status is reset' in source:
            return reviewed([{'kind': 'SFC_step_hold_release', 'when_target_state': 'holding_operating_status',
                              'resulting_step_type': 'normal', 'execute': ['action', 'transition']}])
        if full(r'If no block is specified, (?:the following block is targeted depending on the execution program type|'
                r'the following block will be deactivated)\. - Sequence program: Block 0 - SFC program '
                r'\(within the action\): Block where the instruction is executed \(current block\)'):
            return reviewed([{'kind': 'SFC_default_target_block', 'when_block_unspecified': True,
                              'cases': [{'program_context': 'sequence_program', 'block': 0},
                                        {'program_context': 'SFC_action', 'block': 'current_execution_block'}]}])
        if full(r'When (?:the number of active steps in the specified becomes 0, the specified block will be inactive|'
                r'the instruction is executed and the number of activated steps in the corresponding block becomes 0, '
                r'the block will be deactivated)\.'):
            return reviewed([{'kind': 'SFC_block_deactivation', 'event': 'instruction_execution',
                              'when_active_step_count': 0, 'resulting_block_state': 'inactive'}])
        if full(r'This instruction batch-deactivates the activated steps within the range specified by \(d1\) and \(d2\)\.'):
            return reviewed([{'kind': 'SFC_batch_step_deactivation', 'start': _operand('d1'), 'end': _operand('d2'),
                              'target_state': 'active', 'resulting_state': 'inactive'}])
        if full(r'Unify the device specified to \(d1\) or \(d2\) into S\[GLYPH-F06F\] or '
                r'BL\[GLYPH-F06F\]\\S\[GLYPH-F06F\]\.'):
            return reviewed([{'kind': 'SFC_step_address_form_match', 'subjects': [_operand('d1'), _operand('d2')],
                              'allowed_paired_forms': ['S_step', 'BL_block_backslash_S_step'],
                              'mixed_forms_allowed': False}], dimension='constraints.documented_restriction')
        if full(r'When devices are not unified \(Example: \[ZRST S2 BL0\\S5\]\), '
                r'an operation error occurs and the status of the step is not changed\.'):
            return reviewed([{'kind': 'SFC_step_address_form_mismatch', 'subjects': [_operand('d1'), _operand('d2')],
                              'error': 'operation_error', 'step_state': 'retained'}])
        if full(r'If no block is specified, make sure to specify the step Nos\. so as to be \(d1\) < \(d2\)\.'):
            return reviewed([{'kind': 'SFC_batch_step_order', 'when_block_unspecified': True,
                              'recommended_order': {'left': _operand('d1'), 'operator': '<', 'right': _operand('d2')}}])
        if page in {991, 992} and full(r'When \(d1\) \[GLYPH-F0B3\] \(d2\), only one step No\. that is specified '
                                r'to \(d1\) will be deactivated\.'):
            return reviewed([{'kind': 'SFC_batch_reversed_range', 'when_block_unspecified': page == 991,
                              'when': {'left': _operand('d1'), 'operator': '>=', 'right': _operand('d2')},
                              'deactivated_steps': [_operand('d1')]}],
                            notation_review_basis='original_PDF_visual_review_p991')
        absent = full(r'When a (step|block) that does not exist is specified to \(d1\) or \(d2\), '
                      r'the steps that exist in the specified range will be deactivated\.')
        if absent:
            return reviewed([{'kind': 'SFC_batch_nonexistent_boundary', 'start': _operand('d1'), 'end': _operand('d2'),
                              'missing_boundary_kind': absent[1].lower(), 'effect': 'deactivate_existing_steps_in_range'}])
        if full(r'When a block is specified, specification is possible over blocks\.'):
            return reviewed([{'kind': 'SFC_batch_cross_block_range', 'when_block_explicit': True, 'allowed': True}])

    if page in {1004, 1008}:
        if full(r'Specify the number of the function to be started or stopped in \(s1\), the bit of the channel to be started '
                r'in \(s2\), and the bit of the channel to be stopped in \(s3\)\.'):
            return reviewed([{'kind': 'high_speed_IO_control_operands',
                              'function_number': _operand('s1'), 'start_channel_bitmask': _operand('s2'),
                              'stop_channel_bitmask': _operand('s3')}], dimension='parameters.documented_role')
        if full(r'The following table shows the function numbers which can be specified in \(s1\)\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'function_number_table', 'subject': _operand('s1')}],
                            dimension='resources.section_structure', content_role='source_navigation')

    if page == 995:
        if full(r'When the current value of a high-speed counter of the channel specified in \(s2\) becomes the '
                r'comparison value \(s1\) \(for example, when the current value changes from "199" to "200" or from '
                r'"201" to "200" if the comparison value is K200\), the bit device \(d\) is set to ON regardless of the scan time\.'):
            return reviewed([{'kind': 'high_speed_counter_compare_set', 'channel': _operand('s2'),
                              'comparison_value': _operand('s1'), 'destination': _operand('d'),
                              'when': 'counter_becomes_equal', 'value': True, 'scan_independent': True}])
        if full(r'In this instruction, the comparison processing is executed after the count processing in the high-speed counter\.'):
            return reviewed([{'kind': 'high_speed_counter_processing_order', 'sequence': ['count', 'compare']}],
                            dimension='execution.documented_processing')
        if full(r'If using the high-speed comparison match interrupt, the corresponding interrupt programs can be executed '
                r'by setting the interrupt pointers \(I16 to I23\) to \(d\) as shown below\.'):
            return reviewed([{'kind': 'high_speed_counter_compare_interrupt', 'destination': _operand('d'),
                              'destination_pointer_class': 'I', 'pointer_range': {'minimum': 16, 'maximum': 23},
                              'effect': 'execute_corresponding_interrupt_program'}])
    if page == 1001:
        if full(r'Make sure that the comparison value 1 and the comparison value 2 have the following relationship: '
                r'\[Comparison value 1\] \[GLYPH-F0A3\] \[Comparison value 2\]\.'):
            return reviewed([{'kind': 'high_speed_zone_bound_order', 'left': _operand('s1'), 'operator': '<=',
                              'right': _operand('s2')}], dimension='constraints.documented_restriction',
                            notation_review_basis='original_PDF_visual_review_p1001')
        if full(r'When the setting differs from the above, an operation error occurs and the DHSZ instruction will not '
                r'(?:\| 12 )?perform any operation\.') and 'Comparison value 1' in source:
            return reviewed([{'kind': 'high_speed_zone_invalid_bound_order', 'lower': _operand('s1'), 'upper': _operand('s2'),
                              'when_lower_greater_than_upper': True, 'error': 'operation_error', 'operation_performed': False}])
        if full(r'The current value of a high-speed counter specified in \(s3\) is compared with two comparison points '
                r'\(comparison value 1 and comparison value 2\)\.'):
            return reviewed([{'kind': 'high_speed_zone_compare', 'channel': _operand('s3'),
                              'lower': _operand('s1'), 'upper': _operand('s2')}])
        if full(r'Based on the zone comparison result, "smaller than the lower comparison value", "inside the comparison '
                r'zone" or "larger than the upper comparison value", one among \(d\), \(d\)\+1 and \(d\)\+2 is set '
                r'to ON regardless of the scan time\.'):
            return reviewed([{'kind': 'high_speed_zone_compare_outputs', 'channel': _operand('s3'),
                              'lower': _operand('s1'), 'upper': _operand('s2'), 'scan_independent': True,
                              'one_hot_outputs': [{'destination': _operand('d'), 'when': 'below_lower'},
                                                  {'destination': _operand('d', 1), 'when': 'within_inclusive_bounds'},
                                                  {'destination': _operand('d', 2), 'when': 'above_upper'}]}],
                            notation_review_basis='original_PDF_visual_review_p1001')
    if page in {995, 1001}:
        if full(r'(?:Use DHSCS if|It is used when) the output should be given when the counting result becomes equivalent '
                r'to the comparison value regardless of the scan time of the CPU module\.'):
            return reviewed([{'kind': 'high_speed_counter_usage', 'trigger': 'count_result_matches_comparison',
                              'output_time': 'scan_independent'}], dimension='resources.instruction_usage')
        if full(r'When the number of instructions that can be simultaneously used is exceeded, use a general-purpose comparison instruction\.'):
            return reviewed([{'kind': 'high_speed_comparison_capacity_alternative', 'when': 'simultaneous_instruction_limit_exceeded',
                              'alternative': 'general_purpose_comparison_instruction'}], dimension='resources.instruction_usage')

    # CPU I/O positioning parameters are part of each rule, not a generic
    # assumption about all Y outputs or about all pulse axes.
    if page in {1012, 1014}:
        read = full(r'When the (D?HCMOV) instruction is executed, the latest value is acquired such as the current value '
                    r'of a high- speed counter and transferred to \(d\)\.')
        write = full(r'When the (D?HCMOV) instruction is executed, value such as the current value of a high-speed '
                     r'counter is changed\.')
        if read and 'When (s) is a device supporting high-speed transfer' in page_source:
            return reviewed([{'kind': 'high_speed_device_read', 'form': read[1], 'source': _operand('s'),
                              'destination': _operand('d'), 'when_source_supports_high_speed_transfer': True,
                              'read_time': 'instruction_execution', 'value': 'latest_value'}])
        if write and 'When (d) is a device supporting high-speed transfer' in page_source:
            return reviewed([{'kind': 'high_speed_device_write', 'form': write[1], 'destination': _operand('d'),
                              'when_destination_supports_high_speed_transfer': True,
                              'write_time': 'instruction_execution'}])
        heading = full(r'When \(([sd])\) is a device supporting high-speed transfer')
        if heading:
            return reviewed([{'kind': 'source_condition_heading', 'subject': _operand(heading[1]),
                              'condition': 'supports_high_speed_transfer', 'target': 'following_operation_bullet'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        interrupt = full(r'By using both input interrupt and (D?HCMOV) instruction, the current value of a high-speed '
                         r'counter can be received at the rising edge or falling edge of an external input\.')
        if interrupt:
            return reviewed([{'kind': 'interrupt_counter_capture', 'requires': ['input_interrupt', interrupt[1]],
                              'trigger_object': 'external_input', 'supported_edges': ['rising', 'falling'],
                              'captured_value': 'high_speed_counter_current_value'}], dimension='execution.documented_processing')

    if 1018 <= page <= 1062:
        if full(r'(?:FX5S \| FX5UJ \| FX5U \| FX5UC )?This instruction executes interrupt 1-speed constant quantity feed\.'):
            return reviewed([{'kind': 'positioning_operation', 'mode': 'interrupt_one_speed_constant_quantity_feed'}])
        if full(r'For \(n\), specify the table number to be executed according to the output specified in \(d\)\.'):
            return reviewed([{'kind': 'positioning_table_selection', 'table': _operand('n'), 'output': _operand('d')}],
                            dimension='parameters.documented_role')
        if full(r'FX5S \| FX5UJ \| FX5U \| FX5UC This instruction executes one specified table operation from the '
                r'instructions set in the data table using the engineering tool etc\. Only CPU module is supported\.'):
            return reviewed([{'kind': 'positioning_table_execution', 'table_count': 1,
                              'table_configuration': 'engineering_tool', 'target_module': 'CPU_module_only'}])
        if full(r'This instruction executes positioning operation set in multiple data tables with the engineering tool '
                r'in the continuous operation or stepping operation\.'):
            return reviewed([{'kind': 'positioning_multiple_table_execution', 'table_configuration': 'engineering_tool',
                              'modes': ['continuous', 'stepping']}])
        if full(r'To execute such operation, this instruction needs to be executed only once\.') and 'multiple data tables' in source:
            return reviewed([{'kind': 'positioning_multiple_table_execution', 'instruction_executions_per_operation': 1}],
                            dimension='execution.documented_processing')
        input_role = full(r'For \((s[12])\), specify the (near-point dog|zero-phase) signal input device number\.')
        if input_role:
            return reviewed([{'kind': 'positioning_signal_operand', 'subject': _operand(input_role[1]),
                              'signal': input_role[2].lower().replace('-', '_').replace(' ', '_')}],
                            dimension='parameters.documented_role')
        signal_logic = full(r'When an X device is specified, the (near-point dog|zero-phase) signal functions '
                            r'follow the logic set by parameter\.')
        if signal_logic:
            name = 's1' if signal_logic[1].lower() == 'near-point dog' else 's2'
            return reviewed([{'kind': 'positioning_signal_logic', 'subject': _operand(name), 'when_device_class': 'X',
                              'polarity_source': 'positioning_parameters'}])
        if full(r'When other than X device is specified, the device functions follow the positive logic\.'):
            subject = re.search(r'For \((s[12])\), specify the (?:near-point dog|zero-phase) signal', source)
            if subject:
                return reviewed([{'kind': 'positioning_signal_logic', 'subject': _operand(subject[1]),
                                  'when_device_class_not': 'X', 'polarity': 'positive'}])
        output_role = full(r'For \((d[12]?)\), specify the (device from which pulses are output|'
                           r'bit device from which the rotation direction signal is output)\.')
        if output_role:
            return reviewed([{'kind': 'positioning_output_operand', 'subject': _operand(output_role[1]),
                              'signal': 'pulse' if output_role[2].startswith('device') else 'rotation_direction'}],
                            dimension='parameters.documented_role')
        if full(r'Only the (?:output devices \(Y\)|Y devices) having positioning parameters can be specified\.'):
            subject = re.search(r'For \((d[12]?)\), specify', source)
            if subject:
                return reviewed([{'kind': 'positioning_output_eligibility', 'subject': _operand(subject[1]),
                                  'device_class': 'Y', 'requires_positioning_parameters': True}],
                                dimension='constraints.documented_restriction')
        if full(r'Only the device specified with (?:a|the) parameter or general-purpose outputs can be specified\.'):
            subject = re.search(r'For \((d[12]?)\), specify', source)
            if subject:
                return reviewed([{'kind': 'direction_output_eligibility', 'subject': _operand(subject[1]),
                                  'allowed_any': ['parameter_assigned_device', 'general_purpose_output']}],
                                dimension='constraints.documented_restriction')
        if full(r'When the output devices \(Y\) is executed by another function \(PWM, positioning PULSE axis, '
                r'or CW/CCW axis etc\.\), the device does not function and causes an error\.'):
            subject = re.search(r'For \((d[12]?)\), specify', source)
            if subject:
                return reviewed([{'kind': 'positioning_output_conflict', 'subject': _operand(subject[1]),
                                  'when_device_class': 'Y', 'when_in_use_by': ['PWM', 'positioning_PULSE_axis', 'CW_CCW_axis',
                                                                           'other_function'],
                                  'effect': 'no_output_function', 'error': True}])
        speed = full(r'For \((s[12]?)\), specify the (zero return speed|speed|command speed) '
                     r'(?:in (?:the )?user units|to be output)\. \(The speed must be 200 kpps or lower in frequency\.\)')
        if speed:
            return reviewed([{'kind': 'positioning_speed', 'subject': _operand(speed[1]),
                              'role': speed[2].replace(' ', '_'),
                              'unit': 'user_units' if 'user units' in clause else 'command_speed_units',
                              'frequency_maximum': 200, 'frequency_unit': 'kpps'}], dimension='parameters.documented_domain')
        if full(r'For \(s2\), specify the creep speed in the user units\.'):
            return reviewed([{'kind': 'positioning_speed', 'subject': _operand('s2'), 'role': 'creep_speed',
                              'unit': 'user_units'}], dimension='parameters.documented_role')
        if full(r'Set the creep speed equal to or slower than the zero return speed set in \(s1\)\. '
                r'\(The speed must be 200 kpps or lower in frequency\.\)') and 'For (s2), specify the creep speed' in source:
            return reviewed([{'kind': 'positioning_creep_speed_limit', 'subject': _operand('s2'),
                              'upper_bound': _operand('s1'), 'frequency_maximum': 200, 'frequency_unit': 'kpps'}],
                            dimension='parameters.documented_domain')
        flag = full(r'For \(d2\), specify the bit device of the (?:zero return complete|normal complete) '
                    r'flag or abnormal end flag(?: for the ([A-Z]+) instruction)?\.')
        if flag:
            return reviewed([{'kind': 'positioning_completion_operand', 'subject': _operand('d2'),
                              'status_kinds': ['normal_complete', 'abnormal_end'],
                              **({'form': flag[1].upper()} if flag[1] else {})}], dimension='parameters.documented_role',
                            forms=[flag[1].upper()] if flag[1] else None)
        abnormal_flag = full(r'For \(d2\), specify the bit device of the abnormal end flag for the (D?PLSV) instruction\. '
                             r'\(This device does not have the normal complete status, and only has the abnormal end status '
                             r'\(\(d2\)\+1\)\.')
        if abnormal_flag:
            return reviewed([{'kind': 'positioning_completion_operand', 'subject': _operand('d2'),
                              'form': abnormal_flag[1].upper(), 'normal_complete_status': False,
                              'abnormal_end_status': _operand('d2', 1)}], dimension='parameters.documented_role',
                            forms=[abnormal_flag[1].upper()])
        axis_table = full(r'For \((n[2-5])\), specify the table number that is executed with the axis \(n1\)(?:\+([123]))?\.')
        if axis_table and int(axis_table[1][-1]) == int(axis_table[2] or 0) + 2:
            return reviewed([{'kind': 'positioning_axis_table', 'subject': _operand(axis_table[1]),
                              'axis': {'base': _operand('n1'), 'offset': int(axis_table[2] or 0)}}],
                            dimension='parameters.documented_role')
        unused_axis = full(r'When not executing the axis \(n1\)(?:\+([123]))?, specify K0\.')
        if unused_axis:
            offset = int(unused_axis[1] or 0)
            return reviewed([{'kind': 'positioning_unused_axis_table', 'subject': _operand('n' + str(offset + 2)),
                              'when_axis_not_executed': {'base': _operand('n1'), 'offset': offset}, 'value': 0}])
        if full(r'If axis \(n1\) \+ 2 is not executed or the axis number of the high-speed pulse input/output module '
                r'is specified to \(n1\), specify K0\.'):
            return reviewed([{'kind': 'positioning_axis_table_disable', 'subject': _operand('n4'), 'value': 0,
                              'when_any': [{'axis_not_executed': {'base': _operand('n1'), 'offset': 2}},
                                           {'axis_base_module': 'high_speed_pulse_IO', 'subject': _operand('n1')}]}])
        if full(r'If axis \(n1\)\+3 is not executed or the axis number of the FX5S/FX5UJ CPU module or '
                r'high-speed pulse input/output module is specified to \(n1\), specify K0\.'):
            return reviewed([{'kind': 'positioning_axis_table_disable', 'subject': _operand('n5'), 'value': 0,
                              'when_any': [{'axis_not_executed': {'base': _operand('n1'), 'offset': 3}},
                                           {'axis_base_module_in': ['FX5S_CPU', 'FX5UJ_CPU', 'high_speed_pulse_IO'],
                                            'subject': _operand('n1')}]}])
        if full(r'This instruction reads the absolute position \(ABS\) data when a servo amplifier '
                r'\(equipped with the absolute position detection function\) is connected\.'):
            return reviewed([{'kind': 'servo_absolute_position_read', 'requires': 'servo_absolute_position_detection'}])
        if full(r'The data is converted into pulse when being read\.'):
            return reviewed([{'kind': 'servo_absolute_position_read', 'read_value_unit': 'pulse'}])
        if full(r'For \(d2\), specify the device that stores the absolute position \(ABS\) data read from the servo amplifier\.'):
            return reviewed([{'kind': 'servo_absolute_position_destination', 'subject': _operand('d2')}],
                            dimension='parameters.documented_role')

    # The completion pair alone does not specify pulse duration; timing remains
    # the separately reviewed END-processing rule for this instruction family.
    completion = full(r'The completion of the ([A-Z.]+|S\(P\)\.CPRTCL) instruction can be checked using '
                      r'the completion devices \((d[12]?)\)\+0 and \(\2\)\+1\.')
    if completion:
        return reviewed([{'kind': 'completion_status_pair', 'instruction_form': completion[1],
                          'devices': [_operand(completion[2]), _operand(completion[2], 1)]}])
    close = full(r'This instruction closes the connection specified by \(s1\)\. \(Connection disconnection\) '
                 r'The completion of the ([A-Z.]+) instruction can be checked using the completion devices '
                 r'\(d\)\+0 and \(d\)\+1\.')
    if close:
        return reviewed([{'kind': 'socket_connection_close', 'connection': _operand('s1'), 'form': close[1]},
                         {'kind': 'completion_status_pair', 'devices': [_operand('d'), _operand('d', 1)]}])
    if full(r'This instruction opens the connection specified by \(s1\)\.'):
        return reviewed([{'kind': 'socket_connection_open', 'connection': _operand('s1')}])
    if full(r'The setting value used by the open processing is selected by \(s2\)\+0\.'):
        return reviewed([{'kind': 'socket_open_setting_selection', 'selector': _operand('s2')}])
    if full(r'These instructions read the data for the number of words specified by \(n\) from the socket '
            r'communication receive data area of the connection specified by \(s1\) to the devices from '
            r'the device specified by \(d\) onwards\.'):
        return reviewed([{'kind': 'socket_receive_buffer_read', 'connection': _operand('s1'),
                          'length_words': _operand('n'), 'destination': _operand('d')}])
    if full(r'No processing is performed when \(n\) is 0\.'):
        return reviewed([{'kind': 'conditional_no_operation', 'when': {'subject': _operand('n'), 'equals': 0}}])
    if full(r'When \(n\) is 1, the receive data length can be read\.'):
        return reviewed([{'kind': 'socket_receive_length_read', 'when_word_count': {'subject': _operand('n'), 'equals': 1}}])
    if full(r'By doing this, the device for storing the receive data can be changed when the SP\.SOCRCV instruction is executed\.') \
            and 'When (n) is 1, the receive data length can be read.' in source:
        return reviewed([{'kind': 'socket_receive_destination_selection', 'precondition': 'read_receive_length_with_n_equal_1',
                          'subsequent_instruction': 'SP.SOCRCV', 'destination_change_allowed': True}])
    if full(r'This instruction executes the protocol registered using the engineering tool\.'):
        return reviewed([{'kind': 'registered_protocol_execution', 'registration_origin': 'engineering_tool'}])
    protocol = full(r'Using (the connection|CH) specified by \((s1|n1)\), the instruction executes the protocol '
                    r'in accordance with the control data stored in the device specified by \((s3|s)\) and later\.')
    if protocol:
        return reviewed([{'kind': 'registered_protocol_execution',
                          'transport_kind': 'connection' if protocol[1].lower().startswith('the') else 'channel',
                          'transport': _operand(protocol[2]), 'control_data': _operand(protocol[3])}])
    protocol_count = full(r'The instruction continuously executes as many protocols as specified by \((s2|n2)\) '
                          r'\(a maximum of 8 protocols\) at one time\.')
    if protocol_count:
        return reviewed([{'kind': 'continuous_protocol_execution', 'count': _operand(protocol_count[1]), 'maximum': 8}])
    executed_count = full(r'The number of executed protocols is stored in the device specified by \((s3|s)\)\+([01])\.')
    if executed_count:
        return reviewed([{'kind': 'executed_protocol_count', 'destination': _operand(executed_count[1], int(executed_count[2]))}])
    send_frame = full(r'This instruction sends the request (frame|data) in the device specified by \(s2\) and later '
                      r'to the (target|external) device specified by the target device IP address in the control data\.')
    if send_frame:
        return reviewed([{'kind': 'SLMP_request_send', 'source': _operand('s2'),
                          'destination': 'target_IP_address_in_control_data'}])
    if full(r'When a response message is received from the target device, it is stored in the device specified by \(d1\)\.'):
        return reviewed([{'kind': 'SLMP_response_receive', 'event': 'target_response_received', 'destination': _operand('d1')}])
    if full(r'This instruction send the data set by \(s3\) to the target device of the connection specified by \(s1\)\.'):
        return reviewed([{'kind': 'socket_send', 'source': _operand('s3'), 'connection': _operand('s1')}])
    exclusive_channel = full(r'Multiple (link dedicated|SLMPSND) instructions specifying the same channels? cannot be used concurrently\.')
    if exclusive_channel:
        return reviewed([{'kind': 'communication_channel_exclusion',
                          'instruction_family': exclusive_channel[1].replace(' ', '_'),
                          'concurrent_same_channel': False}], dimension='constraints.documented_restriction')
    avoid_overlap = full(r'When executing multiple (link dedicated|SLMPSND) instructions concurrently, be careful '
                         r'not to overlap the channels of the (link dedicated|SLMPSND) instructions\.')
    if avoid_overlap and avoid_overlap[1].casefold() == avoid_overlap[2].casefold():
        return reviewed([{'kind': 'communication_channel_exclusion', 'instruction_family': avoid_overlap[1].replace(' ', '_'),
                          'concurrent_same_channel': False}], dimension='constraints.documented_restriction')
    pulse = full(r'(Read|Write|Send) processing is performed only once on the rising edge when the (read|write|send) '
                 r'command turns on\.(?: \| 16)?')
    if pulse and pulse[1].lower() == pulse[2].lower():
        return reviewed([{'kind': 'communication_trigger', 'operation': pulse[1].lower(), 'trigger_object': 'command',
                          'edge': 'rising', 'executions_per_edge': 1}], dimension='execution.documented_processing')
    if full(r'The communication execution status \(d2\) is output according to the status of the ADPRW instruction '
            r'such as communicating/completed normally/completed with an error\.'):
        return reviewed([{'kind': 'MODBUS_execution_status', 'destination': _operand('d2'),
                          'states': ['communicating', 'normal_completion', 'error_completion']}])
    if full(r'The data received from the sending station is stored in the own station channel specified by the sending station '
            r'and the corresponding bit in the GP \.RECV instruction execution request area corresponding to each channel is set to on\.'):
        return reviewed([{'kind': 'link_receive_buffer_arrival', 'channel_selector': 'sending_station',
                          'storage': 'own_station_selected_channel', 'request_area': 'GP.RECV_execution_request_area',
                          'request_bit': 'selected_channel_bit', 'request_bit_state': True}])
    if full(r'When the corresponding bit in the GP \.RECV instruction execution request area is set to on, '
            r'the received data is read from the receive data storage channel\.'):
        return reviewed([{'kind': 'link_receive_request', 'request_area': 'GP.RECV_execution_request_area',
                          'when_channel_request_bit': True, 'effect': 'read_channel_receive_storage'}])

    if page in {1084, 1176}:
        if full(r'In normal end, 0 is stored\.') and re.search(r'\(d1\)\+1\s*\|?\s*End code', page_source):
            return reviewed([{'kind': 'SLMP_response_end_code', 'destination': _operand('d1', 1),
                              'when': 'normal_completion', 'value': 0}])
        if full(r'In abnormal \[GLYPH-F0C9\] \| System end, an error code set by the target device is stored\.\*1 '
                r'\(d1\)\+2 to Response data \| Execution results for the request data are set\. \| '
                r'\[GLYPH-F0C9\]\*2 \| System \(d1\)\+n \| \(Some commands do not return response data\.\)'):
            return reviewed([{'kind': 'SLMP_response_end_code', 'destination': _operand('d1', 1),
                              'when': 'abnormal_completion', 'value_source': 'target_device_error_code',
                              'error_meaning_dependency': 'target_device_specification'},
                             {'kind': 'SLMP_response_data', 'destination_base': _operand('d1'),
                              'first_offset': 2, 'last_offset': 'response_last_word', 'writer': 'system',
                              'content': 'request_execution_result', 'may_be_absent': True,
                              'absence_condition': 'selected_command_returns_no_response_data',
                              'format_dependency': 'FX5_Communication_manual'}])
        if full(r'Device Item \| Description \| Setting range Set by \(d1\)\+0 \| Response data length '
                r'The data length from the end code to the response data is stored\. \(In units of bytes\) '
                r'\[GLYPH-F0C9\] \| System \(d1\)\+1 \| End code \| The result of command processing is stored\.'):
            return reviewed([{'kind': 'SLMP_response_buffer', 'writer': 'system',
                              'fields': [{'destination': _operand('d1'), 'role': 'response_data_length', 'unit': 'bytes',
                                          'measured_from': 'end_code', 'measured_through': 'response_data'},
                                         {'destination': _operand('d1', 1), 'role': 'command_processing_result'}]}])
    if page in {1085, 1178, 1179}:
        if full(r'The following figures show the request data and the response data in normal/abnormal end\. '
                r'\[GLYPH-F0FA\]Request data \[GLYPH-F0FA\]Response data'):
            return reviewed([{'kind': 'source_navigation', 'target': 'request_and_response_frame_figures'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if page == 1178 and full(r'Specified by \(s1\) \(except for some items\) \| Specified by \(s2\) '
                                r'\[GLYPH-F0FA\] Response message \(in normal end\)'):
            return reviewed([{'kind': 'SLMP_request_field_ownership', 'frame': '3E_or_4E',
                              'control_fields': _operand('s1'), 'control_field_exceptions': 'as_marked_in_figure',
                              'request_payload': _operand('s2')},
                             {'kind': 'source_navigation', 'target': 'normal_response_message_figure'}])
        if page == 1179 and full(r'\(s1\)\+3 to \(s1\)\+5 \| \(s1\)\+6 \| \(s1\)\+7 \| \(s1\)\+8 \| '
                                r'\(s1\)\+9 \| \(s2\)\+0 \| \(s2\)\+1 \(s2\)\+2 to \(s2\)\+n '
                                r'Specified by \(s1\) \(except for some items\) \| Specified by \(s2\) '
                                r'\[GLYPH-F0FA\] Response message \(in normal end\)'):
            return reviewed([{'kind': 'SLMP_request_buffer_layout', 'frame': 'station_number_extension',
                              'control_source': _operand('s1'), 'control_offsets': [3, 4, 5, 6, 7, 8, 9],
                              'control_field_exceptions': 'as_marked_in_figure', 'request_source': _operand('s2'),
                              'request_fields': [{'offset': 0, 'role': 'data_length'}, {'offset': 1, 'role': 'monitoring_timer'},
                                                 {'first_offset': 2, 'last_offset': 'request_last_word', 'role': 'request_data'}]},
                             {'kind': 'source_navigation', 'target': 'normal_response_message_figure'}])
        if page == 1178 and full(r'Response End code Information of \| Command Subcommand Data Footer '
                                r'data length \| error responding station'):
            return reviewed([{'kind': 'SLMP_error_response_field_order', 'frame': '3E_or_4E',
                              'fields': ['response_data_length', 'end_code', 'error_responding_station',
                                         'command', 'subcommand', 'data', 'footer']}])
        if page == 1179 and full(r'Response End code Command Subcommand Fixed \| System area Information of \| Data Footer '
                                r'data length \| value \| error responding 00 \| station'):
            return reviewed([{'kind': 'SLMP_error_response_field_order', 'frame': 'station_number_extension',
                              'fields': ['response_data_length', 'end_code', 'command', 'subcommand',
                                         'fixed_00', 'system_area', 'error_responding_station', 'data', 'footer']}])
        if full(r'\(d1\)\+0 \| \(d1\)\+1 Error information'):
            return reviewed([{'kind': 'SLMP_error_response_buffer', 'length': _operand('d1'), 'end_code': _operand('d1', 1),
                              'following_data_role': 'error_information'}])
        error_info = full(r'(?:\(d1\)\+0 \| \(d1\)\+1 Error information )?Stored in \(d1\) The network number, '
                          r'station number, request destination module I/O number, (and multidrop station number|'
                          r'multidrop station number, 00 \(fixed value\), and extension station number) are stored to the '
                          r'information of the error responding station\.')
        if error_info:
            fields = ['network_number', 'station_number', 'request_destination_module_IO_number', 'multidrop_station_number']
            if page == 1179:
                fields += ['fixed_00', 'extension_station_number']
            return reviewed([{'kind': 'SLMP_error_response_station_information', 'buffer_base': _operand('d1'),
                              'frame': 'station_number_extension' if page == 1179 else '3E_or_4E',
                              'field_order': fields}])
    if page in {1070, 1126, 1133, 1140, 1148, 1156, 1157, 1162, 1189, 1191, 1192}:
        timing_caption = full(r'The following figure shows the execution timing of the (GP\.READ|GP\.WRITE|GP\.SWRITE|SEND) instruction\.')
        if timing_caption:
            return reviewed([{'kind': 'source_navigation', 'target': 'execution_timing_figure', 'form': timing_caption[1]}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'When completed (normally|with an error)'):
            return reviewed([{'kind': 'source_figure_case', 'completion': 'normal' if 'normally' in clause else 'error'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'Target station error'):
            return reviewed([{'kind': 'source_figure_event_label', 'event': 'target_station_error'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'Completion status Error code \(s1\)\+1'):
            return reviewed([{'kind': 'source_figure_signal_label', 'role': 'completion_status', 'device': _operand('s1', 1),
                              'shown_value_kind': 'error_code'}], dimension='resources.section_structure',
                            content_role='source_navigation')
        if full(r'FX5-CCLIEF \| (Parameter setting|Station number setting) error'):
            return reviewed([{'kind': 'source_figure_event_label', 'module': 'FX5-CCLIEF',
                              'event': 'parameter_setting_error' if page == 1189 else 'station_number_setting_error'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'Transmission error \| Resending data \(Number of resends: 2\)'):
            return reviewed([{'kind': 'source_timing_example', 'event': 'transmission_error',
                              'action': 'resend_data', 'example_resend_count': 2}],
                            dimension='resources.worked_example', content_role='worked_example')
        if full(r'Data reading processing Data ACK reception \(Only TCP\)'):
            return reviewed([{'kind': 'source_figure_signal_label', 'signals': ['data_reading_processing', 'data_ACK_reception'],
                              'ACK_applies_to': 'TCP_only'}], dimension='resources.section_structure', content_role='source_navigation')
        if full(r'The following table lists the GP \.RECV instruction execution request areas\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'GP.RECV_execution_request_area_table'}],
                            dimension='resources.section_structure', content_role='source_navigation')

    if 1089 <= page <= 1095:
        if page == 1090 and full(r"Use one-byte '\\' or '/' as a delimiter to specify the boundary of the folder path\.\*1"):
            return reviewed([{'kind': 'FTP_destination_path_delimiter', 'subject': _operand('s3'),
                              'delimiter_codes': [0x5C, 0x2F], 'character_width_bytes': 1,
                              'server_condition': 'backslash_delimiter_may_be_unsupported_by_server'}],
                            dimension='constraints.documented_restriction')
        if page == 1090 and full(r'The maximum number of characters used in the folder path is 255\.'):
            return reviewed([{'kind': 'FTP_destination_folder_length', 'subject': _operand('s3'), 'maximum': 255,
                              'unit': 'characters'}], dimension='constraints.documented_restriction')
        if page == 1093 and full(r"\(1\) \| \(2\) \(1\)Use one-byte '\\' or '/' as a delimiter of the folder path or file\.\*1 "
                                r'\(2\)The specified file name should include an extension\.'):
            return reviewed([{'kind': 'FTP_source_path_components', 'subject': _operand('s2'),
                              'delimiter_character_codes': [0x5C, 0x2F], 'delimiter_width_bytes': 1,
                              'file_extension_required': True,
                              'server_condition': 'backslash_delimiter_may_be_unsupported_by_server'}],
                            dimension='constraints.documented_restriction')
        if page == 1094 and full(r"\(1\) \(2\) \| \(3\) \| \(4\) \(1\)Drive numbers that can be specified are 2\. "
                                r"\(\[GLYPH-F0C0\]MELSEC iQ-F FX5 User's Manual \(Application\)\) "
                                r"\(2\)Use one-byte ':\\' or ':/' as a delimiter of the drive number\. "
                                r"\(3\)Use one-byte '\\' or '/' as a delimiter to specify the boundary of the folder path\. "
                                r'\(4\)The delimiter at the end of string can be omitted\.'):
            return reviewed([{'kind': 'FTP_destination_path_components', 'subject': _operand('s3'), 'drive_number': 2,
                              'drive_delimiters': [[0x3A, 0x5C], [0x3A, 0x2F]], 'folder_delimiters': [0x5C, 0x2F],
                              'delimiter_character_width_bytes': 1, 'trailing_delimiter_optional': True}],
                            dimension='constraints.documented_restriction')
        if full(r'Specify the transfer source drive number \(2\) of the CPU module, the folder path where the files '
                r'are stored, and the file name \(including an extension\) in \(s2\) in Unicode string\.'):
            return reviewed([{'kind': 'FTP_source_path', 'subject': _operand('s2'), 'encoding': 'Unicode', 'drive': 2,
                              'components': ['drive_number', 'folder_path', 'file_name_with_extension']}])
        if full(r'The only files which can be specified as a path are those stored under the LOGGING or DEBUG folder '
                r'in the SD memory card \(Drive 2\)\.'):
            return reviewed([{'kind': 'FTP_source_location', 'subject': _operand('s2'), 'drive': 2,
                              'storage': 'SD_memory_card', 'allowed_parent_folders': ['LOGGING', 'DEBUG']}],
                            dimension='constraints.documented_restriction')
        if full(r'The maximum number of characters used in a file path is 255\.'):
            return reviewed([{'kind': 'FTP_path_length', 'subject': _operand('s2'), 'maximum': 255,
                              'unit': 'characters', 'includes': 'file_name_and_extension'}], dimension='parameters.documented_domain')
        if full(r'The maximum number of characters used in a path, excluding the file name, is 246 \(not including a delimiter\)\.'):
            return reviewed([{'kind': 'FTP_folder_length', 'subject': _operand('s2'), 'maximum': 246,
                              'unit': 'characters', 'excluded': ['file_name', 'delimiter']}], dimension='parameters.documented_domain')
        delimiter = full(r"Use one-byte '\\' or '/' as a delimiter to specify the (?:boundaries between the elements in a file path|"
                         r"boundary of the folder path)\.(?:\*1 The maximum number of characters used in the folder path is 255\.)?")
        if delimiter:
            rules = [{'kind': 'FTP_path_delimiter', 'subject': _operand('s2' if page == 1089 else 's3'),
                      'one_byte_codes': [0x5C, 0x2F]}]
            if 'maximum' in clause:
                rules.append({'kind': 'FTP_folder_length', 'subject': _operand('s3'), 'maximum': 255, 'unit': 'characters'})
            return reviewed(rules, dimension='parameters.documented_domain')
        if full(r'Wild card characters \(\*, \?\) can be used in the file name or the extension specified in \(s2\)\.'):
            return reviewed([{'kind': 'FTP_path_wildcards', 'subject': _operand('s2'), 'character_codes': [0x2A, 0x3F],
                              'allowed_components': ['file_name', 'extension']}])
        if full(r'Wild card characters do not recognize periods\.'):
            return reviewed([{'kind': 'FTP_path_wildcards', 'excluded_character_codes': [0x2E]}])
        if full(r'Using wild card characters in the following ways results in an error\.') and all(
                phrase in page_source for phrase in ["Two or more asterisks '*'", "An asterisk '*' and a question mark '?'"]):
            return reviewed([{'kind': 'FTP_invalid_wildcard_combination', 'scope': 'each_file_name_or_extension_component',
                              'invalid_any': [{'asterisk_count_at_least': 2}, {'asterisk_and_question_mark': True}],
                              'result': 'completion_error'}], dimension='constraints.documented_restriction')
        wildcard_capacity = full(r'(?:When any wild card character is used, the number of files that can be transferred '
                                 r'is determined by the total size of the file names of the specified files|'
                                 r'The specified files can be transferred when the number of these files and the total size '
                                 r'of the file names satisfy the following condition|If a file transfer function instruction '
                                 r'is executed without satisfying the following condition, the instruction completes with an error)\.')
        if wildcard_capacity and re.search(r'\(Fi \+ NM\) \+ 1 < 32768 \[bytes\]', page_source) and 'Fixed value: 6 bytes' in page_source:
            return reviewed([{'kind': 'FTP_wildcard_capacity', 'when_wildcards_used': True,
                              'sum': ['matched_file_names_size_bytes', {'matched_file_count_multiplier': 6}, 1],
                              'operator': '<', 'limit': 32768, 'unit': 'bytes', 'otherwise': 'completion_error'}],
                            dimension='constraints.documented_restriction')
        overflow = full(r'If the number of characters in the file path which includes a file name and an extension exceeds 255, '
                        r'files will not be transferred even though the wild card specification conditions are satisfied\.'
                        r'(?: \(1\)Drive numbers that can be specified are 2\. \(\[GLYPH-F0C0\]MELSEC iQ-F FX5 User\x27s Manual '
                        r'\(Application\)\) \(2\)Use one-byte \x27:\\\x27 or \x27:/\x27 as a delimiter of the drive number\. '
                        r'\(3\)Use one-byte \x27\\\x27 or \x27/\x27 as a delimiter of the folder path and file\. '
                        r'\(4\)The specified file name should include an extension\.)?')
        if overflow:
            rules = [{'kind': 'FTP_path_length_overflow', 'subject': _operand('s2'), 'maximum': 255, 'unit': 'characters',
                      'includes': ['file_name', 'extension'], 'transfer_performed': False,
                      'wildcard_eligibility_does_not_override': True}]
            if '(1)' in clause:
                rules.append({'kind': 'FTP_source_path_syntax', 'drive': 2, 'drive_delimiters': [[0x3A, 0x5C], [0x3A, 0x2F]],
                              'folder_delimiters': [0x5C, 0x2F], 'file_extension_required': True})
            return reviewed(rules, dimension='constraints.documented_restriction')
        if full(r"Symbol Description \* An asterisk '\*' is replaced with any character or string \(including none\) in a file name\. \?"):
            return reviewed([{'kind': 'FTP_wildcard_match', 'character_code': 0x2A, 'matches': 'zero_or_more_characters'}])
        if full(r"A question mark '\?' is replaced with a character \(excluding none\) in a file name\. \('\?' can be used multiple times\.\)"):
            return reviewed([{'kind': 'FTP_wildcard_match', 'character_code': 0x3F, 'matches': 'exactly_one_character',
                              'multiple_occurrences_allowed': True}])
        if full(r"Symbol \| Description \* \| An asterisk '\*' is replaced with any character or string \(including none\) "
                r"in a file name\. \? \| A question mark '\?' is replaced with a character \(excluding none\) in a file name\. "
                r"\('\?' can be used multiple times\.\)"):
            return reviewed([{'kind': 'FTP_wildcard_match', 'character_code': 0x2A, 'matches': 'zero_or_more_characters'},
                             {'kind': 'FTP_wildcard_match', 'character_code': 0x3F, 'matches': 'exactly_one_character',
                              'multiple_occurrences_allowed': True}])
        destination = full(r'Specify the folder path of the transfer destination (FTP server|CPU module) in \(s3\) in Unicode string\.')
        if destination:
            return reviewed([{'kind': 'FTP_destination_path', 'subject': _operand('s3'), 'encoding': 'Unicode',
                              'destination': destination[1].replace(' ', '_')}])
        if full(r'The specified folder path shall be a relative path from home directory of the FTP server\.'):
            return reviewed([{'kind': 'FTP_destination_path', 'subject': _operand('s3'), 'path_kind': 'relative',
                              'relative_to': 'FTP_server_home'}])
        if full(r'Note that the total number of characters in a folder path \(including the delimiter at the end\) and the '
                r'file name specified in \(s2\) must be within the maximum path length supported by the transfer destination FTP server\.'):
            return reviewed([{'kind': 'FTP_server_path_limit', 'components': [_operand('s3'), _operand('s2')],
                              'trailing_folder_delimiter_included': True, 'maximum_source': 'destination_FTP_server'}],
                            dimension='constraints.documented_restriction')
        if full(r'The delimiter at the end of a string can be omitted\.'):
            return reviewed([{'kind': 'FTP_folder_trailing_delimiter', 'subject': _operand('s3'), 'optional': True}])
        if full(r"When omitted, '\\' is assumed to be set at the end\.") and 'delimiter at the end' in source:
            return reviewed([{'kind': 'FTP_folder_trailing_delimiter', 'subject': _operand('s3'),
                              'when_omitted': {'default_character_code': 0x5C}}])
        if full(r'If a nonexistent folder path is specified, a folder is automatically created by the system at execution '
                r'of the instruction, and then the processing is performed\.(?: 247 characters maximum \[ … "2:\\database1\\recipe\\" … \])?'):
            return reviewed([{'kind': 'FTP_destination_folder_creation', 'subject': _operand('s3'),
                              'when_folder_missing': True, 'sequence': ['create_folder', 'perform_transfer']}])
        if full(r'If only a file name is specified by \(s2\), the CPU module directly accesses under the home directory of the FTP server\.'):
            return reviewed([{'kind': 'FTP_source_path', 'subject': _operand('s2'), 'when_file_name_only': True,
                              'parent_directory': 'FTP_server_home'}])
        if full(r'When specifying only a file name, start with a delimiter\.'):
            return reviewed([{'kind': 'FTP_source_path', 'subject': _operand('s2'), 'when_file_name_only': True,
                              'leading_delimiter_required': True}])
        if full(r'For details, follow the FTP server specifications\.'):
            return reviewed([{'kind': 'external_interface_dependency', 'interface': 'FTP_server',
                              'topic': 'remote_path_syntax'}], dimension='resources.external_dependency')
        if full(r'The maximum number of characters in the folder path is 247 \(246 when a delimiter at the end of the string is omitted\)\.'):
            return reviewed([{'kind': 'FTP_folder_length', 'subject': _operand('s3'), 'unit': 'characters',
                              'with_trailing_delimiter': 247, 'without_trailing_delimiter': 246}],
                            dimension='parameters.documented_domain')
        if full(r'Note that the total number of characters in a folder path \(including the delimiter at the end\) and '
                r'the file name specified in \(s2\) must be within the maximum path length \(255 characters\) supported '
                r'by the CPU module\. \(When the delimiter at the end is omitted, it must not exceed 254 characters\.\)'):
            return reviewed([{'kind': 'FTP_destination_path_length', 'components': [_operand('s3'), _operand('s2')],
                              'unit': 'characters', 'with_trailing_delimiter': 255, 'without_trailing_delimiter': 254}],
                            dimension='constraints.documented_restriction')
        if full(r'If a file with the same name exists in the transfer destination, the file will be overwritten\.'):
            return reviewed([{'kind': 'FTP_destination_existing_file', 'when_name_matches': True, 'effect': 'overwrite'}])
        if full(r'In the following cases, the instruction completes with an error: when there is no free space in the '
                r'transfer destination; or when b3 \(Temporary file create setting\) of \(s1\)\+0 is set to 0 \(Create\), '
                r'but there is not enough free space for storing transfer- target files and a temporary file '
                r'\(same size as transfer-target files\) in the transfer destination\.'):
            return reviewed([{'kind': 'FTP_destination_capacity_error', 'error': 'completion_error',
                              'when_any': ['no_free_space', {'temporary_file_create': {'subject': _operand('s1'), 'bit': 3, 'equals': 0},
                                                           'insufficient_space_for': ['target_files', 'same_size_temporary_file']}]}])

    if page in {1195, 1196, 1202}:
        if full(r'The positioning data of the following axis is read from the servo amplifier supporting the absolute position '
                r'and the value with the unit converted is stored in the "Current feed value" and "Machine feed value" '
                r'areas in the positioning module\.'):
            return reviewed([{'kind': 'absolute_position_restore', 'source': 'servo_absolute_position',
                              'conversion': 'configured_positioning_units', 'target_module': 'positioning_module',
                              'destinations': ['current_feed_value', 'machine_feed_value']}])
        if full(r'For the absolute position detection system, restore the absolute position once '
                r'when it is powered on or the CPU module is reset\.'):
            return reviewed([{'kind': 'absolute_position_restore_initialization', 'events': ['power_on', 'CPU_reset'],
                              'executions_per_event': 1}])
        if full(r'Completion of absolute position restoration can be checked with the \(s\)\+4 status\.'):
            return reviewed([{'kind': 'absolute_position_restore_status', 'subject': _operand('s', 4)}])
        if full(r'The data of "\[Md\.20\] Current feed value" of one of the following target axes is set '
                r'in the positioning or arc address\.'):
            return reviewed([{'kind': 'positioning_teach', 'source': 'Md20_current_feed_value',
                              'destination': 'positioning_or_arc_address', 'axis': 'instruction_target_axis'}])
        if full(r'The positioning data other than the positioning or arc address must be set using the engineering tool or program\.'):
            return reviewed([{'kind': 'positioning_teach_scope', 'written_data': 'positioning_or_arc_address',
                              'other_data_origin': ['engineering_tool', 'program']}])
        axes = full(r'Instruction symbol \| Target axis (?P<form>G\.ABRST|GP\.TEACH)1 \| Axis 1 (?P=form)2 \| Axis 2')
        if axes:
            return reviewed([{'kind': 'positioning_target_axis', 'cases': [{'form': axes[1] + '1', 'axis': 1},
                                                                         {'form': axes[1] + '2', 'axis': 2}]}])
        procedure = ('Output data from ((s)+3).' in source and 'Set data in ((s)+2).' in source
                     and 'Check whether the value in ((s)+4) is 0 or not.' in source)
        if procedure:
            if full(r'The G\.ABRST\[GLYPH-F06F\] instruction performs absolute position restoration by following procedure\. 1\.'):
                return reviewed([{'kind': 'absolute_restore_sequence', 'ordered_steps': ['program_write_output_signals',
                                  'program_read_input_signals', 'execute_G_ABRST', 'check_status'],
                                  'repeat_until': {'subject': _operand('s', 4), 'equals': 0}}])
            if full(r'Output data from \(\(s\)\+3\)\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 1, 'action': 'program_write_signals',
                                  'source': _operand('s', 3)}])
            if full(r'Servo on, ABS transfer mode, and ABS request flag are output by using the program\. 2\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 1, 'source': _operand('s', 3),
                                  'program_outputs': ['servo_on', 'ABS_transfer_mode', 'ABS_request']}])
            if full(r'Set data in \(\(s\)\+2\)\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 2, 'action': 'program_read_signals',
                                  'destination': _operand('s', 2)}])
            if full(r'ABS data bit 0/bit 1 and the send data ready flag are set using the program\. 3\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 2, 'destination': _operand('s', 2),
                                  'program_inputs': ['ABS_data_bit_0', 'ABS_data_bit_1', 'send_data_ready']}])
            if full(r'Execute the G\.ABRST\[GLYPH-F06F\] instruction\. 4\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 3, 'action': 'execute_G_ABRST'}])
            if full(r'When the value is other than 0, the processing returns to step 1\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 4,
                                  'when': {'subject': _operand('s', 4), 'operator': '<>', 'value': 0}, 'next_step': 1}])
            if full(r'When the value is 0, the processing ends\.'):
                return reviewed([{'kind': 'absolute_restore_step', 'step': 4,
                                  'when': {'subject': _operand('s', 4), 'operator': '=', 'value': 0}, 'next_step': 'end'}])
    return None

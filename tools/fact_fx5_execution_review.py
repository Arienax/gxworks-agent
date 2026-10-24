"""Authored, source-bound FX5 execution and timing relations.

The author preserves a complete premise and consequence.  Documented examples
remain examples; a reviewed timing relation grants no executor capability.
"""
from __future__ import annotations

import re

from tools.fact_fx5_processing_extended import _chart_tokens_only, _ASCII_words


def _operand(name, offset=0):
    return {'operand': name.casefold(), **({'offset': offset} if offset else {})}


def _operands(value):
    if isinstance(value, dict):
        if 'operand' in value:
            yield value['operand']
        for child in value.values():
            yield from _operands(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _operands(child)


def _page_layout(section, page):
    return next((text for number, text in section.get('page_layouts', ()) if number == page), '')


def _form_name(name):
    return re.sub(r'\s+', '', name).upper().replace('[GLYPH-F06F]', '_axis_suffix')


def _file_wave_rules(form, completion):
    return [{'kind': 'file_instruction_timing_chart', 'form': form,
             'reactivation_event': 'instruction_OFF_then_ON', 'while_command_remains_ON': 'no_repeated_processing',
             'at_reactivation': [{'device': _operand(completion), 'state': False},
                                 {'device': _operand(completion, 1), 'state': False}],
             'completion': {'timing': 'END_in_processing_completion_scan',
                            'device': _operand(completion), 'state': True,
                            'error_indicator': {'device': _operand(completion, 1), 'normal': False, 'error': True},
                            'OFF_event': 'next_END',
                            'exception': 'execution_error_detection_does_not_assert_completion_flags'},
             'access': {'device': 'SM753', 'ON_event': 'instruction_activation',
                        'OFF_event': 'completion_flag_ON', 'span': 'processing_in_progress'}},
            {'kind': 'source_conflict', 'conflict': 'legend_5_one_scan_duration_disagrees_with_access_span_in_diagram',
             'legend_5_duration_scans': 1, 'SM753_span_marked_5': 'activation_to_completion_across_multiple_drawn_scans',
             'body_SM753_meaning': 'instruction_processing_in_progress', 'resolution': 'not_established'}]


def review_execution_clause(clause, *, section, block, value, declared):
    page = block['page']
    source = block.get('text', '')
    title = section.get('section_token', '')
    declared = {str(name).casefold() for name in declared}

    def full(pattern):
        return re.fullmatch(pattern, clause, re.I)

    def reviewed(rules, *, dimension='execution.documented_behavior', languages=None, native=False,
                 conditions=None, forms=None, status='source_verified', content_role='manual_behavior', **updates):
        subjects = sorted(set(_operands(rules)))
        if any(name not in declared for name in subjects):
            return None
        if native:
            languages = ['ladder']
            conditions = [*(conditions or []), {'context': 'project_mode', 'equals': 'native_ladder'}]
        return {'rules': rules, 'dimension': dimension, 'full_clause_review': True,
                'status': status, 'content_role': content_role,
                **({'languages': languages} if languages else {}),
                **({'conditions': conditions} if conditions else {}),
                **({'forms': forms} if forms else {}),
                'value_updates': {'subjects': [_operand(name) for name in subjects],
                                  'operand_binding_required': bool(subjects),
                                  'numeric_values': [], 'ranges': [], 'comparisons': [], 'source_formulas': [],
                                  'unresolved_glyphs': [], 'unbound_condition_markers': [],
                                  'unparsed_input_qualification': False, 'context_binding': 'resolved',
                                  'knowledge_kind': 'manual_example' if content_role == 'worked_example' else
                                                    'source_navigation' if content_role == 'source_navigation' else 'manual_behavior',
                                  **updates}}

    if page == 137 and full(r'When the OUT T instruction is skipped using the CJ instruction, etc\. while the OUT T '
                            r'and OUT ST instructions are ON, these instructions do not update the current value or turn ON/OFF the contacts\.'):
        return reviewed([{'kind': 'skipped_timer_processing', 'when': 'OUT_T_skipped_by_CJ_while_OUT_T_and_OUT_ST_ON',
                          'affected_forms_as_printed': ['OUT_T', 'OUT_ST'], 'current_value': 'not_updated',
                          'contact_status': 'not_changed'}], native=True)
    if page == 142 and full(r'After a count up, the count value and contact status do not change until the RST or ZRST instruction is executed\.'):
        return reviewed([{'kind': 'count_completed_state', 'event': 'count_up_completed',
                          'hold': ['count_value', 'contact_status'], 'hold_until': ['RST_execution', 'ZRST_execution']}], native=True)
    if page in {145, 147}:
        setting = page == 145
        if full(r'The status of the specified device changes as follows when the execution command turns ON\.'):
            return reviewed([{'kind': 'device_status_dispatch', 'subject': _operand('d'),
                              'when_execution_command': True, 'operation': 'SET' if setting else 'RST',
                              'cases': ([{'device_form': 'bit_device', 'coil': True, 'contact': True},
                                         {'device_form': 'word_device_bit', 'bit': 1}] if setting else
                                        [{'device_form': 'bit_device', 'coil': False, 'contact': False},
                                         {'device_form': 'timer_or_counter', 'current_value': 0, 'contact': False},
                                         {'device_form': 'word_device_bit', 'bit': 0},
                                         {'device_form': 'word_or_module_access_or_index_register', 'value': 0}])}], native=True)
        bit = full(r'(?:Device \| Device status )?Bit devices \| Turns coils and contacts (ON|OFF)\.')
        if bit and ((bit[1].upper() == 'ON') == setting):
            return reviewed([{'kind': 'bit_coil_state', 'subject': _operand('d'), 'when_execution_command': True,
                              'when_device_form': 'bit_device', 'coil': setting, 'contact': setting}], native=True)
        word_bit = full(r'Bit specification of word device \| Sets? the specified bit to ([01])\.')
        if word_bit and int(word_bit[1]) == int(setting):
            return reviewed([{'kind': 'word_bit_state', 'subject': _operand('d'), 'when_execution_command': True,
                              'when_device_form': 'word_device_bit', 'bit_value': int(word_bit[1])}], native=True)
        if not setting and full(r'Timers, counters \| Sets the current value to 0, and turns contacts OFF\.'):
            return reviewed([{'kind': 'timer_counter_reset', 'subject': _operand('d'), 'when_execution_command': True,
                              'when_device_form': 'timer_or_counter', 'current_value': 0, 'contact': False}], native=True)
        if not setting and full(r'Word devices, module access device, index registers \| Sets content to 0\.'):
            return reviewed([{'kind': 'word_device_reset', 'subject': _operand('d'), 'when_execution_command': True,
                              'when_device_form': 'word_or_module_access_or_index_register', 'value': 0}], native=True)
        if full(r'When the execution command is OFF, the device status does not change\.'):
            return reviewed([{'kind': 'disabled_device_state', 'subject': _operand('d'),
                              'when_execution_command': False, 'device_status': 'unchanged'}], native=True)
        if setting and full(r'Devices that are turned ON by the SET instruction can be turned OFF by the RST instruction\.'):
            return reviewed([{'kind': 'SET_device_reset', 'subject': _operand('d'), 'when': 'previously_SET_ON',
                              'reset_form': 'RST', 'new_state': False}], native=True)
    if page in {150, 152}:
        annunciator = full(r'This instruction turns (ON|OFF) the annunciator specified by \(d\) when the execution command turns ON\.')
        if annunciator and ((annunciator[1].upper() == 'ON') == (page == 150)):
            return reviewed([{'kind': 'annunciator_state', 'subject': _operand('d'), 'when_execution_command': True,
                              'new_state': page == 150}], native=True)
    if page in {157, 158}:
        pulse = full(r'When there is one (PLS|PLF) instruction programmed for the device specified by \(d\) during a scan, '
                     r'the specified device turns ON for one scan\.')
        if pulse and pulse[1].upper() == ('PLS' if page == 157 else 'PLF'):
            return reviewed([{'kind': 'single_pulse_writer_duration', 'form': pulse[1].upper(), 'subject': _operand('d'),
                              'when_writers_per_scan': 1, 'ON_duration_scans': 1}], native=True)
        restart = full(r'If the RUN/STOP/RESET switch is changed from RUN to STOP after execution of the (PLS|PLF) instruction, '
                       r'the \1 instruction will not be executed even if the switch is set to RUN again\.')
        if restart and restart[1].upper() == ('PLS' if page == 157 else 'PLF'):
            return reviewed([{'kind': 'pulse_restart_behavior', 'form': restart[1].upper(),
                              'sequence': ['pulse_instruction_executed', 'RUN_to_STOP', 'STOP_to_RUN'],
                              'restart_alone_executes_pulse_instruction': False}], native=True)
    if page == 160 and full(r'Device \| Device status Before execution of FF instruction \| After execution of FF instruction '
                            r'Bit devices \| OFF \| ON ON \| OFF Bit specification of word device \| 0 \| 1 1 \| 0'):
        return reviewed([{'kind': 'flip_flop_state_table', 'subject': _operand('d'),
                          'cases': [{'before': False, 'after': True}, {'before': True, 'after': False}],
                          'device_forms': ['bit_device', 'word_device_bit']}], native=True)
    if page == 184:
        if full(r'When the execution command of the MC instruction turns ON at the start of master control, the operation '
                r'result between the MC and MCR instructions is as per the instructions \(according to ladder\)\.'):
            return reviewed([{'kind': 'master_control_enabled_region', 'when_MC_command': True,
                              'region': 'between_matching_MC_and_MCR', 'operation': 'according_to_region_ladder'}], native=True)
        if full(r'When an instruction \(e\.g\. FOR to NEXT instructions etc\.\) not requiring NO contact instruction is '
                r'programmed in a ladder using master control, the CPU module executes that instruction regardless of '
                r'the execution command of this instruction\.'):
            return reviewed([{'kind': 'master_control_unconditional_instruction',
                              'when_instruction_class': 'does_not_require_normally_open_contact',
                              'examples': ['FOR', 'NEXT'], 'MC_command_gates_execution': False}], native=True)
        if full(r'When the execution command of MC instruction turns OFF, the operation result between the MC and '
                r'MCR instructions becomes as follows\.'):
            return reviewed([{'kind': 'master_control_disabled_region', 'when_MC_command': False,
                              'region': 'between_matching_MC_and_MCR', 'cases': [
                                  {'device_class': 'nonretentive_timer', 'current_value': 0, 'coil': False, 'contact': False},
                                  {'device_class': 'counter_or_retentive_timer', 'coil': False,
                                   'current_value': 'held', 'contact': 'held'},
                                  {'instruction_class': 'OUT', 'output': False},
                                  {'instruction_classes': ['SET', 'RST', 'SFT(P)', 'basic_instructions', 'applied_instructions'],
                                   'device_status': 'held'}]}], native=True,
                            source_binding='complete_MC_OFF_device_status_table')
    if page in {180, 182}:
        right = page == 180
        transfer = full(r'After the shift, \(n2\) points from \(s\) are set into \(n2\) points from '
                        r'\(d\)(\+\(n1-n2\))?\.')
        if transfer and bool(transfer[1]) == right:
            return reviewed([{'kind': 'word_shift_inserted_data', 'direction': 'right' if right else 'left',
                              'source': _operand('s'), 'destination': _operand('d'), 'point_count': _operand('n2'),
                              'destination_start_offset': {'subtract': [_operand('n1'), _operand('n2')]} if right else 0}], native=True)
        if full(r'This instruction sets the specified value for \(n2\) points of devices from \(d\) \+ \(n1-n2\) '
                r'after the shift when K is specified for \(s\)\.'):
            rules = [{'kind': 'word_shift_constant_fill', 'when_constant_class': 'K', 'source_value': _operand('s'),
                      'destination': _operand('d'), 'point_count': _operand('n2'),
                      'printed_destination_start_offset': {'subtract': [_operand('n1'), _operand('n2')]}}]
            if not right:
                rules.append({'kind': 'source_conflict', 'conflict': 'WSFL_constant_fill_prose_uses_WSFR_tail_offset',
                              'general_WSFL_sentence_and_diagram_start_offset': 0,
                              'constant_fill_printed_start_offset': {'subtract': [_operand('n1'), _operand('n2')]},
                              'resolution': 'not_established'})
            return reviewed(rules, native=True, status='source_verified' if right else 'conflict')
    if page == 190:
        if full(r'This instruction cannot be programmed midway during the main sequence program\.'):
            return reviewed([{'kind': 'END_placement', 'form': 'END', 'prohibited_location': 'midway_in_main_sequence_program'}],
                            native=True, dimension='constraints.documented_restriction')
        if full(r'When this processing is required midway during the program, use the FEND instruction\.'):
            return reviewed([{'kind': 'intermediate_program_termination_form', 'when_location': 'midway_in_program',
                              'required_form': 'FEND'}], native=True, dimension='resources.instruction_usage')
    if page == 187 and full(r'When this instruction is executed, program execution returns to the program at step 0 after '
                            r'output processing, input processing and refreshing of the watchdog timer\.'):
        return reviewed([{'kind': 'END_scan_boundary', 'ordered_processing': ['output_processing', 'input_processing',
                                                                          'watchdog_timer_refresh', 'return_to_step_0']}], native=True)
    if page == 192:
        if full(r'This instruction resets outputs \(Y\) and stops operation of the CPU module when the execution command '
                r'turns ON\. \(This operation is the same as setting the switch to STOP\.\)'):
            return reviewed([{'kind': 'STOP_execution', 'when_execution_command': True,
                              'outputs_Y': 'reset', 'CPU_operation': 'stopped', 'equivalent_switch_position': 'STOP'}], native=True)
        if full(r'To restart operation of the CPU module after this instruction is executed, return the switch from '
                r'RUN\[GLYPH-F0AE\]STOP and set it to RUN again\.'):
            return reviewed([{'kind': 'STOP_restart', 'after': 'STOP_instruction_execution',
                              'required_switch_sequence': ['RUN_to_STOP', 'STOP_to_RUN']}], native=True)

    if page in {199, 201, 203, 205, 666, 668, 922, 925}:
        hold = full(r'(?:Latched \(d\)\+2 Latched )?\(1\) \(1\): Even if the command input(?: X0)? turns OFF '
                    r'(?:and the (CMP|DCMP|ZCP|DZCP) instruction is not executed, \(d\) to \(d\)\+2 latches the status '
                    r'just before the command input turns from ON to OFF\.|before the (DECMP|DEZCP) instruction is fully '
                    r'executed, \(d\) to \(d\)\+2 hold the status\.)(?: \| 7)?')
        clock_hold = full(r'(?:\(d\), \(d\)\+1, and \(d\)\+2 hold the state before the command contact is turned off '
                          r'even if, the TCMP instruction is not executed by switching on \[GLYPH-F0AE\] off the command contact\.|'
                          r'Even if the command contact turns off from on and the TZCP instruction is not executed, '
                          r'\(d\), \(d\)\+1, and \(d\)\+2 hold the status before the command contact turned off\.)')
        if hold or clock_hold:
            return reviewed([{'kind': 'comparison_result_retention', 'event': 'command_input_ON_to_OFF',
                              'when': 'comparison_instruction_not_executed' if page not in {666, 668} else
                                      'command_OFF_before_instruction_fully_executed',
                              'destinations': [_operand('d', i) for i in range(3)], 'retained_value': 'state_immediately_before_command_OFF'}])
    if page == 409 and full(r'While the command input is OFF, the transfer destination \(d\) does not change\.'):
        return reviewed([{'kind': 'transfer_destination_retention', 'when_command_input': False,
                          'destination': _operand('d'), 'value': 'unchanged'}])

    if page in {331, 333, 335, 337, 340, 342, 343}:
        conversion = full(r'These instructions convert the (single-precision real number|16-bit signed binary data|'
                          r'16-bit unsigned binary data) in the device specified by \(s\) to (16|32)-bit '
                          r'(signed|unsigned) binary data, and store the converted data in the device specified by \(d\)\.')
        if conversion:
            source_type = {'single-precision real number': 'REAL32', '16-bit signed binary data': 'INT16',
                           '16-bit unsigned binary data': 'UINT16'}[conversion[1].lower()]
            return reviewed([{'kind': 'numeric_type_conversion', 'source': _operand('s'), 'destination': _operand('d'),
                              'source_type': source_type, 'destination_width_bits': int(conversion[2]),
                              'destination_signed': conversion[3].lower() == 'signed'}], dimension='effects.documented_conversion')
        if full(r'After conversion, the digits after the decimal point of the single- precision real number, specified with \(s\), are truncated\.'):
            return reviewed([{'kind': 'real_to_integer_fraction', 'source': _operand('s'), 'destination': _operand('d'),
                              'fractional_part': 'discarded', 'rounding_direction': 'towards_zero'}], dimension='effects.documented_conversion')
    if page == 339 and full(r'\(2\) \(1\): The most significant bit of data before conversion is stored\. '
                            r'\(2\): Data before conversion is stored in the lower 16 bits\.'):
        return reviewed([{'kind': 'signed_integer_extension_layout', 'source': _operand('s'), 'destination': _operand('d'),
                          'source_width_bits': 16, 'destination_width_bits': 32, 'low_word': 'source_16_bits',
                          'high_word': 'all_bits_copy_source_sign_bit'}], dimension='memory.numeric_conversion_layout',
                        source_binding='INT2DINT_bit_assignment_diagram_and_two_legends')
    if page in {344, 345, 347, 348} and full(r'\(1\) \(1\): Data before conversion is stored in the lower 16 bits\.'):
        return reviewed([{'kind': 'integer_narrowing_layout', 'source': _operand('s'), 'destination': _operand('d'),
                          'source_width_bits': 32, 'destination_width_bits': 16, 'stored_bits': [0, 15],
                          'source_high_word': 'discarded'}], dimension='memory.numeric_conversion_layout')
    if page in {340, 342, 343} and clause.startswith('(s) | (d) SM402 '):
        vocabulary = set(('a b d f h movp m int udint uint dint int2udint uint2dint uint2udint the value is stored data before conversion in lower bits').split())
        if _chart_tokens_only(clause, vocabulary):
            raw, numeric, form = {340: (0xA000, -24576, 'INT2UDINT'), 342: (0xD000, 53248, 'UINT2DINT'),
                                  343: (0xF000, 61440, 'UINT2UDINT')}[page]
            return reviewed([{'kind': 'documented_zero_extension_example', 'source': _operand('s'), 'destination': _operand('d'),
                              'form': form, 'source_raw_word': raw, 'source_printed_numeric_value': numeric,
                              'destination_low_word': raw, 'destination_high_word': 0, 'destination_numeric_value': raw}],
                            dimension='resources.documented_conversion_example', content_role='worked_example')
    if page in {374, 376}:
        if full(r'They are used when a positive or negative sign is to be inverted\.'):
            return reviewed([{'kind': 'twos_complement_purpose', 'destination': _operand('d'),
                              'width_bits': 16 if page == 374 else 32, 'operation': 'sign_inversion'}],
                            dimension='resources.instruction_usage')
        vocabulary = {'b', 'before', 'after', 'execution'}
        if clause.startswith(('b15 |', 'b31 |')) and _chart_tokens_only(clause, vocabulary):
            wide = page == 376
            return reviewed([{'kind': 'documented_twos_complement_example', 'destination': _operand('d'),
                              'width_bits': 32 if wide else 16, 'before_signed': -218460 if wide else -21846,
                              'after_signed': 218460 if wide else 21846,
                              'before_raw': 0xFFFC_AAA4 if wide else 0xAAAA,
                              'after_raw': 218460 if wide else 21846}],
                            dimension='resources.documented_conversion_example', content_role='worked_example')
    if page in {1259, 1261} and full(r'After conversion, the first digit after the decimal point of the REAL type data value is rounded down\.'):
        return reviewed([{'kind': 'REAL_integer_conversion_rounding', 'source': _operand('s'), 'destination': _operand('d'),
                          'precision': 'integer', 'printed_rounding': 'rounded_down',
                          'negative_tie_rule': 'not_established_by_this_clause'}], dimension='effects.documented_conversion')

    # NULL packing is conditional on character count, not on an arbitrary
    # numerical value held by the source device.
    if page in {599, 620, 623, 624, 627, 629, 650, 660, 662}:
        termination = full(r'When the number of (?:all digits|characters after the concatenation|extracted characters '
                           r'"\(s2\)\+1"|characters after insertion, \(s1\)\+\(d\),|characters after deletion, \(d\),) is '
                           r'(odd|even), (?:a NULL code \(00H\)|"?0 ?0H"?|"?0000H"?) is stored '
                           r'(?:in the (?:upper byte(?: \(8 bits\))? of (?:a |the )?device storing the (?:last|final) character|'
                           r'last device \(upper 8 bits\) of the character string)|'
                           r'in the device(?: \(1 word\))? after the last device storing the character string|'
                           r'in the device after the last character)\.')
        if termination:
            count = ('extracted_character_count' if page == 650 else 'total_display_character_count' if page in {620, 623, 624, 627, 629}
                     else 'post_insertion_character_count' if page == 660 else 'post_deletion_character_count' if page == 662 else
                     'post_concatenation_character_count')
            return reviewed([{'kind': 'character_string_NULL_packing', 'destination': _operand('d'), 'character_count_role': count,
                              'when_count_parity': termination[1].lower(),
                              'zero_storage': 'final_character_word_upper_byte' if termination[1].lower() == 'odd' else 'word_after_final_character'}],
                            dimension='memory.string_termination')
        if page == 599 and full(r'After two character strings are connected, "00H" is automatically added at the end\.'):
            return reviewed([{'kind': 'string_concatenation_terminator', 'destination': _operand('d'), 'terminator_byte': 0}],
                            dimension='memory.string_termination')
        point = full(r'If the number of digits in the decimal part is set to other than 0, "2EH" \(\.\) is automatically '
                     r'stored at the position before t ?he specified number of digits\.')
        no_point = full(r'If the number of digits in the decimal part is 0, "2EH" \(\.\) is not stored\.'
                        r'(?: \(1\): (?:Number of digits of decimal part|Total number of digits) '
                        r'\(2\): (?:Added automatically|Number of digits of decimal part)(?: \(3\): Added automatically)?)?')
        if point or no_point:
            settings = _operand('s1', 1) if page in {620, 623} else _operand('s2', 2)
            return reviewed([{'kind': 'numeric_string_decimal_point', 'fractional_digit_count': settings,
                              'when_fractional_count': 'nonzero' if point else 'zero',
                              'decimal_point_byte': 46, 'stored': bool(point),
                              **({'position': 'immediately_before_fractional_digits'} if point else {})}],
                            dimension='memory.numeric_string_layout')
    if page in {644, 647, 650, 653}:
        string_end = full(r'A character string stored in \((s|s1)\)( or \(d\))? indicates data stored in devices from the '
                          r'specified device until "00H" is first detected in units of (?:1 )?byte\.')
        if string_end:
            return reviewed([{'kind': 'byte_string_extent', 'sources': [_operand(string_end[1])] +
                              ([_operand('d')] if string_end[2] else []), 'end': 'first_zero_byte', 'scan_unit_bits': 8}],
                            dimension='memory.string_termination')

    # File instructions distinguish processing completed with an error from
    # an error detected during execution.  Preserve both source event names;
    # this author does not invent an unprinted lifecycle phase for either.
    file_pages = {488, 493, 520, 542, 551, 561, 570, 578}
    if page in file_pages:
        complete = full(r'The (?:processing completion bit device|completion device specified by) \((d|d2|d3)\) '
                        r'automatically turns on (?:at the execution of the END instruction in the scan in which the completion '
                        r'of processing of the SP\s*\.\s*([A-Z]+) instruction is detected|upon execution of the END instruction '
                        r'following the detection of processing completion of the SP\s*\.\s*([A-Z]+) instruction)'
                        r'(?: and turns off upon execution of the END instruction in the next scan, so it is used as the '
                        r'execution complete flag of the SP\s*\.\s*([A-Z]+) instruction)?\.')
        if complete:
            form = 'SP.' + (complete[2] or complete[3]).upper()
            if complete[4] and complete[4].upper() != form[3:]:
                return None
            return reviewed([{'kind': 'asynchronous_completion_device', 'form': form, 'device': _operand(complete[1]),
                              'ON_event': 'END_in_scan_where_processing_completion_is_detected',
                              **({'OFF_event': 'END_in_next_scan'} if complete[4] else {})}])
        clear = full(r'If the processing completion bit device \((d2|d3)\) is ON, it will automatically turn OFF '
                     r'when the SP\s*\.\s*([A-Z]+) instruction is executed\.')
        if clear:
            return reviewed([{'kind': 'completion_flag_reactivation', 'form': 'SP.' + clear[2].upper(),
                              'device': _operand(clear[1]), 'when_current_state': True,
                              'event': 'instruction_execution', 'new_state': False}])
        error = full(r'If the SP\s*\.\s*([A-Z]+) instruction completes with an error, the error completion device '
                     r'(?:specified by )?\((d|d2|d3)\)\+1 turns on or off (?:in synchronization with|at the same time as '
                     r'the completion device specified by) \(\2\)\.')
        if error:
            return reviewed([{'kind': 'asynchronous_error_completion', 'form': 'SP.' + error[1].upper(),
                              'when_completion': 'error', 'error_device': _operand(error[2], 1),
                              'state_events_synchronized_with': _operand(error[2])}])
        access = full(r'SM753 \(File being accessed\) turns on while the SP\s*\.\s*([A-Z]+) instruction is (?:being )?executed\.')
        if access:
            return reviewed([{'kind': 'file_access_flag', 'form': 'SP.' + access[1].upper(),
                              'device': 'SM753', 'while': 'instruction_processing', 'state': True}])
        busy = full(r'(?:While SM753 is on|If SM753 has already been on), the SP\s*\.\s*([A-Z]+) instruction '
                    r'cannot be executed\. \(If (?:the instruction is )?executed, no processing is performed\.\)')
        if busy:
            return reviewed([{'kind': 'conditional_no_processing', 'form': 'SP.' + busy[1].upper(),
                              'when_device': 'SM753', 'device_state': True, 'effect': 'no_processing'}])
        pre_error = full(r'(?:If|Even if) an error is detected during (?:the )?execution of the (?:SP\.DEVST )?instruction, '
                         r'(?:the completion device \(d\), error completion device \(d\)\+1, and|Processing Complete \(d3\), '
                         r'Error Completion \(d3\)\+1, and|the bit devices \(d2\) and \(d2\)\+1 and) SM753 do not turn on\.')
        if pre_error:
            device = 'd' if page == 488 else 'd3' if page == 493 else 'd2'
            return reviewed([{'kind': 'detected_execution_error_no_completion_flags',
                              'when': 'error_detected_during_instruction_execution',
                              'not_turned_ON': [_operand(device), _operand(device, 1), {'device': 'SM753'}],
                              'different_from': 'asynchronous_processing_completed_with_error'}])

    if page in {585, 588, 591}:
        form = {585: 'ERREAD', 588: 'ERWRITE', 591: 'ERINIT'}[page]
        observe = full(r'The normal or abnormal completion of the (ERREAD|ERWRITE|ERINIT) instruction can be confirmed '
                       r'with the instruction completion device \(d\) specified with the setting data\.')
        if observe and observe[1].upper() == form:
            return reviewed([{'kind': 'extended_file_completion_observation', 'form': form,
                              'normal_completion_device': _operand('d'), 'abnormal_completion_device': _operand('d', 1)}])
        event = full(r'Instruction completion device \(d\)\+([01]): Turns off when the (ERREAD|ERWRITE|ERINIT) instruction '
                     r'is activated, and turns on during the instruction processing for the scan in which the \2 instruction '
                     r'has completed (normally|abnormally)\.')
        if event and event[2].upper() == form and (int(event[1]), event[3].lower()) in {(0, 'normally'), (1, 'abnormally')}:
            return reviewed([{'kind': 'extended_file_completion_device', 'form': form, 'device': _operand('d', int(event[1])),
                              'OFF_event': 'instruction_activation', 'ON_event': 'instruction_processing_in_completion_scan',
                              'completion_outcome': 'normal' if event[1] == '0' else 'abnormal'}])
        if full(r'Also, when the (ERREAD|ERWRITE|ERINIT) instruction has completed normally/abnormally, instruction execution '
                r'complete flag \(SM8029\) and instruction execution abnormal end flag \(SM8329\) turn on only for one '
                r'scan at the same timing with the instruction completion device \(d\)\.'):
            return reviewed([{'kind': 'extended_file_global_completion_flags', 'form': form,
                              'normal': {'device': 'SM8029', 'synchronized_with': _operand('d')},
                              'abnormal': {'device': 'SM8329', 'synchronized_with': _operand('d', 1)}, 'ON_duration_scans': 1}])
        if full(r'After the contact of the (ERREAD|ERWRITE|ERINIT) instruction turns on, the extended file register '
                r'\(ER\) access flag \(SM9366\) turns on during access to the extended file register(?: \(ER\))?\.'):
            return reviewed([{'kind': 'extended_file_access_flag', 'form': form, 'device': 'SM9366',
                              'after_event': 'instruction_contact_ON', 'while': 'accessing_extended_file_register', 'state': True}])
        if full(r'Even if the command input of (ERREAD|ERWRITE|ERINIT) instruction is set to OFF while the extended file '
                r'register is (?:being read|being written|initialized in a batch), \1 instruction is executed until '
                r'(?:reading|writing|initialization) is completed\.'):
            return reviewed([{'kind': 'extended_file_command_OFF', 'form': form, 'when': 'processing_in_progress',
                              'event': 'command_input_OFF', 'processing': 'continues_until_completion'}])

    # The following network instructions use a pulse on every completion, plus
    # a separate outcome indication.  This must not be conflated with ER's two
    # mutually exclusive normal/abnormal completion devices above.
    if 1065 <= page <= 1208:
        instruction = r'((?:(?:SP|S\(P\)|GP|G\(P\)|G)\s*\.\s*[A-Z]+(?:\[GLYPH-F06F\])?|SLMPSND))'
        completion = full(r'(?:Device \|? (?:Operation|Description) )?(?:Completion device \((d|d2)\)(?:\+0)?\s*'
                          r'(?:\||:)?\s*(?:This device |The device )?(?:is turned on by |turns on (?:during |in )?|Turns ON during )'
                          r'(?:the )?END processing (?:for the scan in which|of the scan where) (?:the )?' + instruction +
                          r' instruction (?:is completed|completes|completed)(?:,? and (?:is turned|turns) off (?:during |in )?'
                          r'(?:the )?next END processing)\.)')
        if completion:
            return reviewed([{'kind': 'network_completion_pulse', 'form': _form_name(completion[2]),
                              'device': _operand(completion[1]), 'ON_event': 'END_in_completion_scan',
                              'OFF_event': 'next_END', 'ON_duration_scans': 1}])
        status = full(r'(?:Completion status indication(?: device)?|Completion device)(?: \((d|d2)\)\+1)?(?: \||:)? '
                      r'(?:The device |This device )?(?:is turned|turns) on or off depending on the (?:completion status of|status '
                      r'(?:of )?when) (?:the )?' + instruction + r' instru\s*c\s*ti\s*on(?: is completed)?\.?(?: device \((d|d2)\)\+1 \|)?'
                      r'(?: \| 21 When completed normally: Unchanged from off\.)?')
        if status:
            operand = status[1] or status[3]
            if not operand:
                # The device name is a separate physical row only when the
                # owning table identifies it explicitly on this same page.
                match = re.search(r'Completion status indication device\s*\((d|d2)\)\+1', _page_layout(section, page))
                operand = match[1] if match else None
            if operand:
                return reviewed([{'kind': 'network_completion_outcome_indicator', 'form': _form_name(status[2]),
                                  'device': _operand(operand, 1), 'state_depends_on': 'instruction_completion_outcome'}])
        observation = full(r'(?:The execution(?: status)? and the completion status of|The execution of|'
                           r'The completion status of|Whether(?: the execution of)?) (?:the )?' + instruction +
                           r' instruction(?:s)?(?: and whether it has been completed normally or completed with an error|'
                           r' has been completed normally or with an error)? can be checked with the '
                           r'completion device \((d|d2)\) (?:and the|or) completion status indication device \(\2\)\+1\.')
        if observation:
            return reviewed([{'kind': 'network_completion_observation', 'form': _form_name(observation[1]),
                              'completion_device': _operand(observation[2]), 'completion_status_device': _operand(observation[2], 1)}])
        error_status = full(r'When completed (?:with an error|abnormally): The device (?:turns on (?:during |in )?'
                            r'(?:the )?END processing of the scan where (?:the )?' + instruction + r' instruction completes|'
                            r'is turned on by END processing for the scan in which (?:the )?' + instruction +
                            r' instruction is completed),? and (?:turns off (?:during |in )?(?:the )?next END processing|'
                            r'turned off by next END processing)\.')
        normal_status = full(r'(?:\(d\)\+1 \| )?When completed (?:normally|successfully): '
                             r'(?:The device remains off|Unchanged from off)\.')
        if error_status or normal_status:
            layout = _page_layout(section, page)
            operand_match = re.search(r'Completion status indication(?:\s+device)?\s*\((d|d2)\)\+1', layout)
            if not operand_match:
                operand_match = re.search(r'Completion status indication.+?device\s*\((d|d2)\)\+1', layout, re.S)
            if operand_match:
                return reviewed([{'kind': 'network_completion_outcome_state', 'device': _operand(operand_match[1], 1),
                                  'completion_outcome': 'error' if error_status else 'normal',
                                  'state': bool(error_status),
                                  **({'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END'} if error_status else {})}])
        socket_receive = full(r'In the END processing after the execution of the (SP|GP)\.SOCRCV instruction, the receive '
                              r'data of the connection specified by \(s1\) is read from the socket communication receive data area\.')
        if socket_receive:
            return reviewed([{'kind': 'socket_receive_data_read', 'form': socket_receive[1].upper() + '.SOCRCV',
                              'connection': _operand('s1'), 'source': 'socket_communication_receive_data_area',
                              'timing': 'END_after_instruction_execution'}])

    if page in {897, 898, 899}:
        clock = full(r'(?:Device Name \| Description )?(?:Binary code(?: \(FX3 compatible area\))? )?'
                     r'(SD\d+) \|? ?Binary clock data \((year|month|day|hour|minute|second)\) \| '
                     r'The \2 data in the clock data is stored as a (four-digit binary code|binary code)\.')
        if clock:
            return reviewed([{'kind': 'binary_clock_storage', 'device': clock[1].upper(), 'field': clock[2].lower(),
                              'encoding': 'binary', **({'year_digits': 4} if clock[2].lower() == 'year' else {})}],
                            dimension='memory.clock_layout')
        weekday = full(r'(SD\d+) \|? ?Binary clock data \(day of the week\) The day-of-a-week data in the clock data '
                       r'\(0: Sunday, 1: Monday, \.\.\., 6: Saturday\) is stored as a binary code\.')
        if weekday:
            return reviewed([{'kind': 'binary_clock_storage', 'device': weekday[1].upper(), 'field': 'day_of_week',
                              'encoding': 'binary', 'numbering': {'Sunday': 0, 'Monday': 1, 'Saturday': 6}}],
                            dimension='memory.clock_layout')
        if full(r'The clock data stored in these special registers is updated during the END processing\.'):
            return reviewed([{'kind': 'clock_special_register_update', 'timing': 'END_processing',
                              'register_sets': [['SD210', 'SD216'], ['SD8013', 'SD8019']]}], dimension='memory.clock_update')
    if page in {916, 919}:
        invalid = full(r'If the comparison target data in the device are not recognized as (date|time) data, SM709 turns '
                       r'on after the instruction is executed and the operation result will be non-continuity\.')
        if invalid:
            return reviewed([{'kind': 'clock_comparison_invalid_data', 'data_kind': invalid[1].lower(),
                              'when': 'comparison_target_not_recognized_as_clock_data', 'after_instruction': {'SM709': True},
                              'result': 'non_conductive',
                              **({'flag_exception': 'unrecognized_date_data_within_documented_setting_range'} if page == 916 else {})}])
        if page == 916 and full(r'Even if the data are not recognized as date data, SM709 does not turn on if the data are within the setting range\.'):
            return reviewed([{'kind': 'clock_comparison_invalid_date_exception', 'when': 'unrecognized_date_but_values_within_setting_range',
                              'SM709_turned_ON': False}])
        if full(r'If the device areas specified by \(s1\) to \(s1\)\+2 or \(s2\) to \(s2\)\+2 exceed the corresponding '
                r'device range, SM709 turns on after the instruction is executed and the operation result will be non-continuity as well\.'):
            return reviewed([{'kind': 'clock_comparison_invalid_device_range',
                              'required_ranges': [{'start': _operand(name), 'last_offset': 2} for name in ['s1', 's2']],
                              'when_any_range': 'outside_corresponding_device_range', 'after_instruction': {'SM709': True},
                              'result': 'non_conductive'}])
        if full(r'Once SM709 turns on, the on state is held until the CPU module is powered off or reset\.'):
            return reviewed([{'kind': 'clock_comparison_error_latch', 'device': 'SM709', 'when_state': True,
                              'hold_until': ['CPU_power_OFF', 'CPU_reset']}])
        if full(r'Turn off SM ?709 as needed\.'):
            return reviewed([{'kind': 'clock_comparison_error_reset', 'device': 'SM709', 'reset': 'user_program_as_needed'}],
                            dimension='resources.instruction_usage')
    if page in {930, 932}:
        if full(r'In \(s\)(?:\+1 and \(s\))?, specify the period of time until the device specified by \(d2\) is turned on in units of hour\.'):
            return reviewed([{'kind': 'hour_meter_alarm_setting', 'threshold': _operand('s'),
                              'threshold_width_bits': 16 if page == 930 else 32, 'unit': 'hour', 'alarm_device': _operand('d2')}])
        if full(r'Specify a latched \(battery backed\) type data register as \(d1\) so that the current value data can be '
                r'continuously used even after the power to CPU module turns off\.'):
            return reviewed([{'kind': 'hour_meter_storage_retention_requirement', 'current_value_storage': _operand('d1'),
                              'to_preserve_across': 'CPU_power_OFF', 'required_register_class': 'latched_battery_backed'}],
                            dimension='resources.memory_requirement')
        if full(r'If a general data type register is used, the current value data is cleared when the power to the CPU '
                r'module is turned OFF or when the controller mode switches from STOP to RUN\.'):
            return reviewed([{'kind': 'hour_meter_nonlatched_current_value', 'current_value_storage': _operand('d1'),
                              'when_register_class': 'general_nonlatched', 'clear_events': ['CPU_power_OFF', 'STOP_to_RUN']}])
        if full(r'Even after the alarm output specified by \(d2\) turns ON, the measurement is continued\.'):
            return reviewed([{'kind': 'hour_meter_post_alarm', 'when_alarm': {'device': _operand('d2'), 'state': True},
                              'measurement': 'continues'}])
    if page in {872, 875, 878}:
        if full(r'When the command input turns on, data sorting is started\.'):
            return reviewed([{'kind': 'sorting_start', 'event': 'command_input_ON', 'operation': 'data_sorting_started'}])
        if full(r'Data sorting is completed after \(n1\) scans, and the instruction execution complete flag SM8029 is set to on\.'):
            return reviewed([{'kind': 'sorting_completion', 'duration_scans': _operand('n1'),
                              'at_completion': {'device': 'SM8029', 'state': True}}])

    if page in {365, 366, 616, 617, 850, 892}:
        byte_mode = 8 if page in {366, 617} else 16
        selector = full(r'(?:When using the (16|8)-bit conversion mode, set SM8161 to normally (OFF|ON)|'
                        r'SM8161 must always be (off|on) in the (16|8)-bit (?:conversion )?mode)\.')
        if selector:
            width, state = (int(selector[1]), selector[2].upper()) if selector[1] else (int(selector[4]), selector[3].upper())
            if width == byte_mode and ((width == 8) == (state == 'ON')):
                return reviewed([{'kind': 'byte_conversion_mode', 'selector': 'SM8161', 'required_state': width == 8,
                                  'bits_per_device_used': width}], dimension='constraints.documented_restriction')
        if full(r'SM8161 is cleared when the CPU module mode is changed from RUN to STOP ?\.'):
            return reviewed([{'kind': 'byte_conversion_selector_clear', 'device': 'SM8161', 'event': 'RUN_to_STOP', 'new_state': False}])
        shared = full(r'SM8161 is (?:also used for|shared with) the (ASCI\(P\), CRC\(P\), and CCD\(P\)|'
                      r'ASCI\(P\), HEXA\(P\), and CRC\(P\)|ASCI\(P\), HEXA\(P\), and CCD\(P\)) instructions\.')
        if shared:
            return reviewed([{'kind': 'shared_conversion_selector', 'device': 'SM8161',
                              'other_forms': re.findall(r'[A-Z]+\(P\)', shared[1].upper()), 'configuration_scope': 'shared_device'}],
                            dimension='resources.shared_device_usage')
        if page in {365, 366}:
            convert = full(r'(16|8)-bit conversion mode \(while SM8161 is (OFF|on)\) The ASCII data stored in '
                           r'(high-order 8 bits and low-order 8 bits \(byte\)|low-order 8 bits) of the device specified by '
                           r'\(s\) is converted to hexadecimal code, and transferred to the device specified by \(d\) in units of 4 digits\.')
            if convert and int(convert[1]) == byte_mode:
                return reviewed([{'kind': 'ASCII_hexadecimal_conversion', 'source': _operand('s'), 'destination': _operand('d'),
                                  'when_SM8161': byte_mode == 8, 'source_bytes': 'low_byte_only' if byte_mode == 8 else 'both_bytes',
                                  'output_packing': 'four_hexadecimal_digits_per_word'}], dimension='effects.documented_conversion')
            if full(r'The number of characters to be converted is specified by \(n\)\.'):
                return reviewed([{'kind': 'conversion_character_count', 'count': _operand('n'), 'unit': 'characters'}],
                                dimension='parameters.count_unit')
            if page == 365 and full(r'Moreover, when using the 16-bit conversion mode, the ASCII data must also be '
                                    r'stored in high-order 8 bits of the device specified by \(s\)\.'):
                return reviewed([{'kind': 'ASCII_conversion_source_requirement', 'source': _operand('s'),
                                  'when_SM8161': False, 'required_ASCII_positions': ['low_byte', 'high_byte']}],
                                dimension='constraints.documented_restriction')
        if page in {616, 617}:
            convert = full(r'(16|8)-bit conversion mode \(while SM8161 is (OFF|ON)\) Each digit of hexadecimal data stored '
                           r'in the device specified by \(s\) and later is converted into ASCII code, and transferred to '
                           r'the (upper 8 bits and lower 8 bits|lower 8 bits) of each device specified by \(d\) and later\.')
            if convert and int(convert[1]) == byte_mode:
                return reviewed([{'kind': 'hexadecimal_ASCII_conversion', 'source': _operand('s'), 'destination': _operand('d'),
                                  'when_SM8161': byte_mode == 8, 'destination_bytes': 'low_byte_only' if byte_mode == 8 else 'both_bytes',
                                  'conversion_unit': 'hexadecimal_digit_to_ASCII_character'}], dimension='effects.documented_conversion')

    if page in {369, 372}:
        if full(r'The total number of digits stored in \(d1\) is the total number of characters \(including the sign and '
                r'decimal point\) representing the numeric value\.'):
            return reviewed([{'kind': 'numeric_string_parse_count', 'destination': _operand('d1'), 'unit': 'characters',
                              'includes': ['sign', 'decimal_point', 'digits']}], dimension='parameters.result_encoding')
        if full(r'The number of digits in the decimal part stored in \(d1\)\+1 is the number of characters representing '
                r'the decimal part after 2EH \(\.\)\.'):
            return reviewed([{'kind': 'numeric_string_parse_fraction_count', 'destination': _operand('d1', 1),
                              'counted_part': 'characters_after_decimal_point', 'decimal_point_ASCII': 46}],
                            dimension='parameters.result_encoding')
        parsed = full(r'The (16|32)-bit binary data stored in \(d2\) is binary value converted from a character string '
                      r'with the decimal point ignored\.')
        if parsed and int(parsed[1]) == (16 if page == 369 else 32):
            return reviewed([{'kind': 'numeric_string_parse_value', 'destination': _operand('d2'), 'width_bits': int(parsed[1]),
                              'decimal_point': 'ignored_when_forming_integer_value'}], dimension='parameters.result_encoding')
    if page in {410, 423}:
        if page == 410 and full(r'These instructions invert the bit data in the device specified by \(s\), and transfer '
                                r'the result into the device specified by \(d\)\.'):
            return reviewed([{'kind': 'bit_transfer', 'source': _operand('s'), 'destination': _operand('d'), 'invert': True}],
                            dimension='effects.documented_transfer')
        if page == 423 and full(r'These instructions transfer bit data specified by \(s\) to \(d\)\.'):
            return reviewed([{'kind': 'bit_transfer', 'source': _operand('s'), 'destination': _operand('d'), 'invert': False}],
                            dimension='effects.documented_transfer')
    if page in {480, 482}:
        inserting = page == 480
        if full(r'These instructions insert 16-bit binary data specified by \(s\) to the data table specified by \(d\) '
                r'as the \(n\)th data\.'):
            return reviewed([{'kind': 'data_table_insert', 'value': _operand('s'), 'table': _operand('d'),
                              'position_one_based': _operand('n'), 'element_width_bits': 16,
                              'following_elements': 'moved_one_point_towards_table_end'}], dimension='effects.documented_table_edit')
        if full(r'These instructions remove the \(n\)th data in the data table specified by \(d\) and store the data in '
                r'the device specified by \(s\)\.'):
            return reviewed([{'kind': 'data_table_delete', 'removed_value_destination': _operand('s'), 'table': _operand('d'),
                              'position_one_based': _operand('n'), 'element_width_bits': 16,
                              'following_elements': 'moved_one_point_towards_table_start'}], dimension='effects.documented_table_edit')
        shifted = full(r'After these instructions are executed, the data after the \(n\)(\+1)?th data in the data table '
                       r'is moved (down|up) by one data point\.')
        if shifted and (inserting == (shifted[2].lower() == 'down')):
            return reviewed([{'kind': 'data_table_edit_shift', 'table': _operand('d'), 'edit_position': _operand('n'),
                              'first_affected_position_relative_to_edit': 0 if inserting else 1,
                              'direction': 'towards_table_end' if inserting else 'towards_table_start', 'distance_points': 1}],
                            dimension='effects.documented_table_edit')
    if page in {496, 523, 524}:
        if full(r'If the extension of the target file is omitted, the extension will be "\.BIN"\.'):
            return reviewed([{'kind': 'default_file_extension', 'when': 'target_extension_omitted', 'suffix': 'BIN', 'leading_dot': True}],
                            dimension='parameters.file_path_default')
        if page == 496 and full(r'If the specified file does not exist, an error \(8002H\) occurs\.'):
            return reviewed([{'kind': 'file_not_found', 'when': 'specified_file_absent', 'result': 'error', 'error_code': '8002H'}])
        beyond = full(r'If the (?:position is specified exceeding|specified position exceeds) the existing file size, '
                      r'0 point of data is (read|written) and the processing completes successfully\.')
        if beyond:
            return reviewed([{'kind': 'file_position_after_end', 'when': 'requested_position_greater_than_existing_size',
                              'operation': 'read' if beyond[1].lower() == 'read' else 'write',
                              'transferred_points': 0, 'completion': 'success'}])
        if page == 523:
            if full(r'If a file that does not exist is specified, it will be created and data will be saved to the beginning of the file\.'):
                return reviewed([{'kind': 'file_write_create', 'when': 'specified_file_absent', 'create': True,
                                  'write_position': 'file_beginning'}])
            if full(r'The newly created file has the archive attribute\.'):
                return reviewed([{'kind': 'created_file_attribute', 'when': 'file_created_by_this_instruction', 'attribute': 'archive'}])
            if full(r'When an existing file is specified, data will be saved to the beginning of the file\.'):
                return reviewed([{'kind': 'file_write_existing', 'when': 'specified_file_exists', 'write_position': 'file_beginning'}])
            if full(r'If the size of data exceeds the size of the existing area in the file during writing, the excess data is additionally stored\.'):
                return reviewed([{'kind': 'file_write_extend', 'when': 'written_data_exceeds_existing_file_area', 'excess': 'appended'}])
        if full(r'If the media runs out of free space during additional saving of data, an error occurs\.'):
            return reviewed([{'kind': 'file_append_storage_full', 'when': 'media_free_space_exhausted_during_append', 'completion': 'error'}])
        if full(r'In this case, the successfully (?:written and additionally )?saved data is kept in the written state, '
                r'and the program will terminate abnormally after additionally saving only the part that can be '
                r'additionally saved\. \(8002H is stored in \(d1\)\+1\.\)'):
            if 'If the media runs out of free space during additional saving of data' in source or (
                    'If the media runs out of free space during additional saving of data' in _page_layout(section, page)):
                return reviewed([{'kind': 'partial_file_append_failure', 'when': 'media_free_space_exhausted_during_append',
                                  'retained_data': 'successfully_written_prefix', 'additional_data': 'only_part_fitting_remaining_space',
                                  'completion': 'abnormal', 'status_storage': _operand('d1', 1), 'status_value': 0x8002}])
    if page == 578:
        if full(r'This function acquires the status of the file or folder specified by \(s2\) in the drive specified by '
                r'\(s1\) and stores it in the device \(d2\) and after\.'):
            return reviewed([{'kind': 'file_status_acquisition', 'path': _operand('s2'), 'drive': _operand('s1'),
                              'destination': _operand('d2')}], dimension='effects.documented_file_operation')
        if full(r'When a folder is specified by \(s2\), 0 is stored in \(d2\)\+2 to \(d2\)\+3\.'):
            return reviewed([{'kind': 'folder_status_size_fields', 'when_path_is_folder': _operand('s2'),
                              'destination_low': _operand('d2', 2), 'destination_high': _operand('d2', 3), 'value': 0}],
                            dimension='parameters.file_status_result')
    if page in {585, 588}:
        ER_read = page == 585
        if full(r'The current value of the extended file register \(ER\) stored into the SD memory card is read for '
                r'\(n\) points to the file register \(R\) in the CPU built-in memory specified with \(s\)\.') or full(
                r'The current value of the file register \(R\) in the CPU built-in memory is written \(transferred\) '
                r'for \(n\) points to the extended file register \(ER\) in the SD memory card specified with \(s\)\.'):
            return reviewed([{'kind': 'extended_file_register_transfer', 'source': 'ER_in_SD_card' if ER_read else 'R_in_CPU_memory',
                              'destination': 'R_in_CPU_memory' if ER_read else 'ER_in_SD_card', 'start': _operand('s'), 'point_count': _operand('n')}],
                            dimension='effects.documented_file_operation')
    if page == 591 and full(r'Initializes all the points of the extended file register \(ER\) in the SD memory card in a batch\.'):
        return reviewed([{'kind': 'extended_file_register_initialization', 'storage': 'ER_in_SD_card', 'extent': 'all_ER_points'}],
                        dimension='effects.documented_file_operation')
    if page in {601, 603}:
        overlap = full(r'(?:Even though the device range of the data to be transferred \(s\) to \(s\)\+n and the device '
                       r'range for storing the transferred data \(d\) to \(d\)\+n overlap|Even when the device range '
                       r'\(\(s\) to \(s\)\+n\)\) in which the Unicode character string data to be transferred and the device '
                       r'range \(\(d\) to \(d\)\+n\) for storing the transferred data are overlapping), the processing is performed normally\.')
        if overlap:
            return reviewed([{'kind': 'overlapping_string_transfer', 'source': _operand('s'), 'destination': _operand('d'),
                              'when_ranges': 'overlap', 'processing': 'normal', 'representation': 'byte_string' if page == 601 else 'Unicode_string'}],
                            dimension='effects.documented_transfer')
        if page == 601 and full(r'When "00H" is stored in the lower byte of \(s\)\+n, "00H" is stored to both the upper '
                                r'byte and lower byte of \(d\)\+n\.'):
            return reviewed([{'kind': 'string_transfer_NULL_word', 'when_source_terminal_word_low_byte': 0,
                              'source_start': _operand('s'), 'destination_start': _operand('d'),
                              'terminal_word_offset_symbol': 'n', 'destination_terminal_word': 0,
                              'source_terminal_high_byte': 'not_transferred'}], dimension='memory.string_termination')
    if page in {980, 981, 983, 986, 989}:
        if page == 981 and full(r'All the steps become inactive immediately after execution of the instruction\.'):
            return reviewed([{'kind': 'SFC_block_pause', 'event': 'instruction_execution', 'steps': 'all_inactive_immediately'}])
        if page == 981 and full(r'The coil output status caused by the OUT instruction at the temporary stop depends '
                                r'on the status of SM325 \(output mode at pausing a block\)\.'):
            return reviewed([{'kind': 'SFC_pause_output_mode', 'selector': 'SM325', 'affected_outputs': 'OUT_coils',
                              'cases': [{'SM325': True, 'at_pause': 'hold_output_state_before_pause'},
                                        {'SM325': False, 'at_pause': 'turn_output_OFF'}]}])
        if page == 981 and full(r'(?:Status of SM325 \| Coil output status by using the OUT instruction )?On \| '
                                r'During the stop, the coil output remains the state immediately before stop\.'):
            return reviewed([{'kind': 'SFC_pause_output_state', 'when_SM325': True, 'affected_outputs': 'OUT_coils',
                              'at_pause': 'hold_output_state_before_pause'}])
        if page == 981 and full(r'Off \| The coil output turns off at stop\.'):
            return reviewed([{'kind': 'SFC_pause_output_state', 'when_SM325': False, 'affected_outputs': 'OUT_coils', 'at_pause': False}])
        if page == 983 and full(r'After the step sequence is restarted, the operation of the PLS instruction and the '
                                r'instructions which are executed only on the rising edge depends on the status of SM325 '
                                r'\(output mode at pausing a block\)\.'):
            return reviewed([{'kind': 'SFC_restart_edge_instruction', 'selector': 'SM325', 'event': 'step_sequence_restart',
                              'affected_forms': ['PLS', 'rising_edge_only_instructions'],
                              'cases': [{'SM325': True, 'execution_on_restart': False},
                                        {'SM325': False, 'execution_on_restart': True}]}])
        restart = full(r'(?:Status of SM325 \| Operation of the PLS instruction and the instructions executed on the '
                       r'rising edge )?(On|Off) \(Coil output is (held|off)\.\) \| The instruction is (not executed|executed again)\.')
        if page == 983 and restart and (restart[1].lower(), restart[2].lower(), restart[3].lower()) in {
                ('on', 'held', 'not executed'), ('off', 'off', 'executed again')}:
            return reviewed([{'kind': 'SFC_restart_edge_instruction_case', 'event': 'step_sequence_restart',
                              'when_SM325': restart[1].lower() == 'on', 'execute': restart[1].lower() == 'off',
                              'affected_forms': ['PLS', 'rising_edge_only_instructions']}])
        if page == 986:
            if full(r'This instruction activates the specified step in the specified block\.'):
                return reviewed([{'kind': 'SFC_step_activation', 'target': 'specified_step_in_specified_block', 'new_state': 'active'}])
            if full(r'The operation of the specified block will be as follows depending on the status \(active or inactive\) of the specified block\.'):
                return reviewed([{'kind': 'SFC_step_activation_block_cases', 'cases': [
                    {'prior_block_state': 'inactive', 'effect': 'activate_block_and_start_at_specified_step'},
                    {'prior_block_state': 'active', 'effect': 'activate_specified_step_and_continue_already_active_steps'}]}])
            if full(r'Status of the specified block \| Operation Inactive \| The specified block is activated at execution '
                    r'of the instruction, and starts the processing from the specified step\.'):
                return reviewed([{'kind': 'SFC_inactive_block_step_activation', 'prior_block_state': 'inactive',
                                  'event': 'instruction_execution', 'effect': 'activate_block_and_start_at_specified_step'}])
            if full(r'Active \| The specified step is newly activated while the step which has already been active continues processing\.'):
                return reviewed([{'kind': 'SFC_active_block_step_activation', 'prior_block_state': 'active',
                                  'effect': 'activate_specified_step_and_continue_already_active_steps'}])
        if page == 989:
            contact = full(r'When the drive contact is (on|off), this instruction (activates|deactivates) the specified step in the specified block\.')
            if contact and (contact[1].lower(), contact[2].lower()) in {('on', 'activates'), ('off', 'deactivates')}:
                return reviewed([{'kind': 'SFC_driven_step_state', 'when_drive_contact': contact[1].lower() == 'on',
                                  'target': 'specified_step_in_specified_block', 'new_state': 'active' if contact[1].lower() == 'on' else 'inactive',
                                  **({'inside_action': 'instruction_ignored'} if contact[1].lower() == 'off' else {})}])
    file_charts = {522: ('SP.FWRITE', 'd2'), 542: ('SP.FDELETE', 'd2'), 553: ('SP.FCOPY', 'd2'),
                   563: ('SP.FMOVE', 'd2'), 571: ('SP.FRENAME', 'd2'), 579: ('SP.FSTATUS', 'd3')}
    if page in file_charts:
        vocabulary = set(('no processing during scanning except when the instruction is turned off and on updating '
                          'turning upon normal error completion only scan after execution complete flag file being '
                          'accessed access write delete copy move rename status acquisition sp fwrite fdelete fcopy '
                          'fmove frename fstatus u h below shown timing chart from to of for operation specifications '
                          'function are before overwriting copied end processingscan folder user csv bin melprj sample samplechange sample_change '
                          'application setting area name path source destination in sd memory card a specified even '
                          'it contains data empty current same exists because not moved with folders number').split())
        timing = '(1) No processing during scanning except' in clause or (
            page == 563 and clause.startswith('Below is shown the timing chart'))
        if timing and _chart_tokens_only(clause, vocabulary):
            form, completion = file_charts[page]
            rules = _file_wave_rules(form, completion)
            if page == 553:
                rules += [{'kind': 'documented_copy_overwrite_example', 'setting': {'subject': _operand('d1'), 'value': 7},
                           'diagram_source_path_components': ['folder1', 'user1.csv'], 'destination_folder_components': ['folder2'],
                           'source_file': 'retained', 'destination_file': 'overwritten_with_source_contents',
                           'printed_caption_source_path_components': ['folder2', 'user1.csv'],
                           'printed_caption_overwritten_path_components': ['folder1', 'user1.csv']},
                          {'kind': 'source_conflict', 'conflict': 'overwrite_caption_reverses_the_source_and_destination_shown_in_diagram',
                           'resolution': 'not_established'}]
            if page == 542:
                rules.append({'kind': 'file_delete_mode_table', 'selector': _operand('d1'),
                              'cases': [{'value': 0, 'target': 'file'}, {'value': 1, 'target': 'folder_even_with_contents'},
                                        {'value': 3, 'target': 'folder_only_if_empty'}], 'path_argument': _operand('s2')})
            if page == 579:
                rules.append({'kind': 'documented_file_status_example', 'selector': {'subject': _operand('d1'), 'value': 0},
                              'file_name': 'sample.bin', 'result_device_range': ['D100', 'D109'], 'result_argument': _operand('d2')})
            return reviewed(rules, status='conflict', source_binding='whole_flag_waveforms_numbered_legends_and_own_processing_text')

    if page in {585, 588, 591}:
        vocabulary = set(('on off end processing execute erread erwrite erinit instruction when failed scan file access '
                          'program completion device execution complete flag abnormal extended register er during '
                          'turns an is finished normally abnormally name description').split())
        if _chart_tokens_only(clause, vocabulary) and any(marker in clause for marker in
                ['END processing', 'Completion device', 'Device | Name | Description']):
            form = {585: 'ERREAD', 588: 'ERWRITE', 591: 'ERINIT'}[page]
            return reviewed([{'kind': 'extended_file_timing_chart', 'form': form,
                              'at_activation': {'clear': [_operand('d'), _operand('d', 1)], 'set': 'SM9366'},
                              'processing': {'access_flag': 'SM9366', 'command_OFF_does_not_cancel': True},
                              'at_completion': {'timing': 'instruction_processing_in_completion_scan', 'clear': 'SM9366',
                                                'cases': [{'outcome': 'normal', 'set_device': _operand('d'), 'pulse_flag': 'SM8029'},
                                                          {'outcome': 'abnormal', 'set_device': _operand('d', 1), 'pulse_flag': 'SM8329'}]},
                              'completion_devices_hold_until': 'next_activation', 'global_flag_pulse_duration_scans': 1,
                              'diagram_scan_count': 'illustration_not_fixed_instruction_latency'}],
                            source_binding='full_ER_command_completion_outcome_and_access_waveforms')

    if page == 493 and full(r'When "Odd number of bytes" is specified by \(d1\)\+7, the read data is stored as shown below\. '
                            r'\(In the shaded area shown below, the data before execution of the instruction is kept\.\)'):
        return reviewed([{'kind': 'odd_byte_file_read_layout', 'data_type_selector': {'subject': _operand('d1', 7), 'value': 3},
                          'destination': _operand('d2'), 'offset_zero': 'number_of_data_actually_read', 'data_first_word_offset': 1,
                          'data_byte_order': 'low_byte_first', 'odd_final_word': {'low_byte': 'last_read_byte', 'high_byte': 'preserve_before_execution'}}],
                        dimension='memory.file_read_layout', source_binding='odd_byte_destination_diagram_with_shaded_final_high_byte')
    if page == 488 and full(r'Therefore, the device is used as the error completion flag of the SP.DEVST instruction\.'):
        if 'error completion device specified by (d)+1' in source:
            return reviewed([{'kind': 'error_completion_device_role', 'form': 'SP.DEVST', 'device': _operand('d', 1),
                              'role': 'processing_error_completion'}], dimension='parameters.completion_device_role')
    if page == 524:
        if full(r'When an existing file has been specified and "Add converting the last line feed code of file to a comma" '
                r'has been specified in \(d1\)\+3\.b0, the data will be saved after the last line feed code of the file is converted to a comma\.'):
            return reviewed([{'kind': 'CSV_append_terminal_line_feed', 'when_file': 'existing',
                              'when_setting': {'subject': _operand('d1', 3), 'bit': 0, 'mode': 'convert_terminal_line_feed_to_comma'},
                              'terminal_line_feed': 'converted_to_comma_before_append'}])
        if full(r'However, the last character of the file is not a line feed code, the character will not be converted to '
                r'a comma, and the data will be saved to the end of the file\.') and 'last line feed code' in _page_layout(section, page):
            return reviewed([{'kind': 'CSV_append_without_terminal_line_feed', 'when_setting': 'terminal_line_feed_to_comma_append',
                              'when_last_character': 'not_line_feed', 'last_character': 'preserved', 'write_position': 'file_end'}])
    if page == 570 and full(r'If the file path \+ folder name and the folder path exceed the limit number of characters '
                            r'after renaming, the program is finished abnormally\. \(8006H is stored in \(d1\)\+1\.\)'):
        return reviewed([{'kind': 'renamed_path_length_error', 'when': 'post_rename_paths_exceed_documented_character_limit',
                          'completion': 'abnormal', 'status_storage': _operand('d1', 1), 'status_value': 0x8006}])
    if page in {586, 589, 591} and full(r'Device \| Name \| Description SM8029 \| Instruction execution \| Turns on when '
                                     r'an instruction is finished normally\. complete SM8329 \| Instruction execution '
                                     r'\| Turns on when an instruction is finished abnormally\. abnormal end SM9366 '
                                     r'\| Extended file register \| Turns on during access to extended file register '
                                     r'\(ER\)\. \(ER\) access flag'):
        return reviewed([{'kind': 'extended_file_status_device_table', 'fields': [
            {'device': 'SM8029', 'meaning': 'instruction_normal_completion'},
            {'device': 'SM8329', 'meaning': 'instruction_abnormal_completion'},
            {'device': 'SM9366', 'meaning': 'ER_access_in_progress'}]}], dimension='memory.status_fields')

    if page == 599 and full(r'When the number is even, 0000H is stored in the device after the last character\.'):
        if 'When the number of characters after the concatenation is odd' in source:
            return reviewed([{'kind': 'character_string_NULL_packing', 'destination': _operand('d'),
                              'character_count_role': 'post_concatenation_character_count', 'when_count_parity': 'even',
                              'zero_storage': 'word_after_final_character'}], dimension='memory.string_termination')
    if page == 626 and full(r'The data after conversion varies depending on the display specification stored in \(s2\)\.'):
        return reviewed([{'kind': 'real_string_display_selector', 'selector': _operand('s2'),
                          'cases': [{'value': 0, 'format': 'decimal'}, {'value': 1, 'format': 'exponent'}]}],
                        dimension='parameters.display_format', source_binding='display_specification_table_in_own_page')
    if page == 629:
        if full(r'The exponent part is fixed to 2 digits\.'):
            return reviewed([{'kind': 'exponent_string_digit_count', 'destination': _operand('d'), 'exponent_digits': 2}],
                            dimension='memory.numeric_string_layout')
        if full(r'When the exponent part is 1 digit, "30H \(0\)" is stored after the sign of the exponent part\. '
                r'\(1\): Total number of digits \(2\): Fixed to 2 digit'):
            return reviewed([{'kind': 'exponent_string_zero_padding', 'when_exponent_digits': 1,
                              'padding_byte': 48, 'padding_position': 'after_exponent_sign_before_exponent_digit',
                              'stored_exponent_digits': 2}], dimension='memory.numeric_string_layout')
    if page in {695, 696}:
        if full(r'With regard to character string, six digits excluding the sign, decimal point and exponent part are '
                r'valid, and the 7th and later digits are discarded during conversion\.'):
            return reviewed([{'kind': 'real_string_parse_precision', 'source': _operand('s'), 'significant_mantissa_digits': 6,
                              'excluded_from_digit_count': ['sign', 'decimal_point', 'exponent_part'],
                              'seventh_and_later_mantissa_digits': 'discarded'}], dimension='effects.documented_conversion')
        plus = full(r'(?:When 2BH \(\+\) is specified as the sign in the floating point format or when the sign is '
                    r'omitted, a character string is converted into a positive value\.|String data in the exponent format '
                    r'is handled as positive value during conversion when the sign of the exponent part is set to '
                    r'2BH \(\+\) or when the sign is omitted\.)')
        if plus:
            return reviewed([{'kind': 'real_string_parse_sign', 'source': _operand('s'),
                              'field': 'number_sign' if page == 695 else 'exponent_sign',
                              'when_sign': ['plus_ASCII_2BH', 'omitted'], 'interpretation': 'positive'}],
                            dimension='effects.documented_conversion')
        if full(r'(?:It is handled as negative value during conversion when the sign is set to 2DH \(-\)\.|'
                r'When 2DH \(-\) is specified as the sign, a character string is converted into a negative value\.)'):
            return reviewed([{'kind': 'real_string_parse_sign', 'source': _operand('s'),
                              'field': 'number_sign' if page == 695 else 'exponent_sign', 'when_sign': 'minus_ASCII_2DH',
                              'interpretation': 'negative'}], dimension='effects.documented_conversion')
        if full(r'When 20H \(space\) or 30H \(0\) exists between numbers except the first 0 in a character string specified '
                r'by \(s\), 20H or 30H is ignored during conversion\.'):
            return reviewed([{'kind': 'real_string_parse_ignored_characters', 'source': _operand('s'),
                              'when_position': 'between_numbers_except_first_zero', 'ignored_ASCII': [32, 48],
                              'scope': 'literal_documented_conversion_rule'}], dimension='effects.documented_conversion')
        if page == 696 and full(r'When 30H \(0\) exists between a number and "E" in a character string in the exponent '
                                r'format, 30H is ignored during conversion\.'):
            return reviewed([{'kind': 'exponent_string_parse_trailing_zero', 'source': _operand('s'), 'when_position': 'between_number_and_E',
                              'ignored_ASCII': 48}], dimension='effects.documented_conversion')
    if page == 779:
        if full(r'When the long counter specified by \(d\) is a high-speed counter, the instruction operates to start '
                r'and stop the high-speed counter, and the current counter value is updated by turning ON/OFF an '
                r'external input instead of the drive contact\.'):
            return reviewed([{'kind': 'long_high_speed_counter_drive', 'counter': _operand('d'), 'when_counter_kind': 'high_speed',
                              'instruction_role': 'start_and_stop_counter', 'count_update_trigger': 'external_input_ON_OFF',
                              'drive_contact_is_count_trigger': False}])
        if full(r'Counting up or down can be switched by whether the supported special device \(SM4580 to SM4595\) is on or off\.'):
            return reviewed([{'kind': 'high_speed_counter_direction_selector', 'selector_range': ['SM4580', 'SM4595'],
                              'effect': 'switch_counting_up_or_down', 'ON_direction': 'not_defined_by_this_sentence'}])
        if full(r'During up-counting, the output contact turns ON when the current value changes from a value less '
                r'than the set value to a value not less than the set value\.'):
            return reviewed([{'kind': 'counter_output_threshold_crossing', 'counter': _operand('d'), 'direction': 'up',
                              'prior_relation': '<', 'current_relation': '>=', 'threshold': 'counter_set_value', 'contact_new_state': True}])
        if full(r'During down-counting, the output contact turns OFF when the current value changes from a value not '
                r'less than the set value to a value equivalent to the "set value -1" or less\.'):
            return reviewed([{'kind': 'counter_output_threshold_crossing', 'counter': _operand('d'), 'direction': 'down',
                              'prior_relation': '>=', 'current_relation': '<', 'threshold': 'counter_set_value', 'contact_new_state': False}])
        if full(r'Counting is continued also when the drive contact changes from OFF to ON after the output contact '
                r'changes\. \(Only when specify other than a high-speed counter for \(d\)\)'):
            return reviewed([{'kind': 'normal_counter_restart_continuation', 'counter': _operand('d'),
                              'when_counter_kind': 'not_high_speed', 'event': 'drive_contact_OFF_to_ON_after_output_contact_change',
                              'counting': 'continues'}])
    if page == 784:
        if full(r'After transfer is completed, the instruction execution complete flag SM8029 turns ON, and the '
                r'\(d\) value is returned to the \(s1\) value\.'):
            return reviewed([{'kind': 'ramp_cycle_completion', 'event': 'ramp_transfer_completed', 'set_flag': 'SM8029',
                              'destination': _operand('d'), 'next_start_value': _operand('s1'),
                              'when_mode': 'SM8026_OFF_repeating_mode'}])
        if full(r'When the operation result is acquired at a constant time interval \(constant scan mode\), write a '
                r'prescribed scan time \(which is longer than the actual scan time\) to SD8039 and set SM8039 to ON\.'):
            return reviewed([{'kind': 'ramp_constant_scan_configuration', 'when_required': 'constant_result_time_interval',
                              'scan_time_register': 'SD8039', 'enable_device': 'SM8039', 'enable_state': True,
                              'required_scan_time_relation': 'prescribed_scan_time_greater_than_actual_scan_time'}],
                            dimension='resources.instruction_usage')
        if full(r'The value used in the constant scan mode can be set in the parameter setting of an engineering tool '
                r'\(constant scan execution interval setting of CPU parameter\)\.'):
            return reviewed([{'kind': 'constant_scan_interval_parameter', 'parameter': 'CPU_constant_scan_execution_interval',
                              'effect': 'sets_constant_scan_interval'}], dimension='resources.instruction_usage')
    if page in {815, 816, 823}:
        if page == 815 and full(r'When the specified number of output pulses is equal to or less than the number of '
                                r'pulses which have already been output, pulse output stops after outputting pulses which are being output\.'):
            return reviewed([{'kind': 'PWM_changed_pulse_limit', 'when_requested_pulses': 'less_than_or_equal_to_already_output_count',
                              'stop_timing': 'after_currently_output_pulse_finishes'}])
        if page == 815 and full(r'When the specified number of output pulses is larger than the number of pulses which '
                                r'have already been output, pulse output stops after outputting set number of pulses\.'):
            return reviewed([{'kind': 'PWM_changed_pulse_limit', 'when_requested_pulses': 'greater_than_already_output_count',
                              'stop_timing': 'when_total_output_count_reaches_requested_limit'}])
        if page == 815 and full(r'The ON time and period can be set during the pulse output\.'):
            return reviewed([{'kind': 'PWM_live_timing_settings', 'fields': ['ON_time', 'period'], 'can_update_while': 'pulse_output_in_progress'}])
        if page == 815 and full(r'Setting values are always read and updated\.'):
            if 'The ON time and period can be set during the pulse output' in source:
                return reviewed([{'kind': 'PWM_timing_setting_reflection', 'fields': ['ON_time', 'period'], 'update_policy': 'continuously_read_and_update'}])
        if full(r'The monitor of the current number of output pulses can be changed during the pulse output\.'):
            return reviewed([{'kind': 'PWM_current_count_monitor_write', 'while': 'pulse_output_in_progress', 'write': 'permitted'}])
        if full(r'The maximum value of the monitor of the current number of output pulses is "FFFFFFFFH"\.'):
            return reviewed([{'kind': 'PWM_current_count_monitor_domain', 'maximum': 0xFFFFFFFF}], dimension='parameters.result_domain')
        if full(r'After the current number of output pulses reaches the maximum value, the monitor of the current '
                r'number of output pulses starts to count again from "0"\.'):
            return reviewed([{'kind': 'PWM_current_count_monitor_wrap', 'when_count_reaches': 0xFFFFFFFF, 'next_count': 0}])
    if page == 847:
        if full(r'When the final process specified by \(n\) is finished, the execution complete flag SM8029 turns on '
                r'and remains on for one operation cycle\.'):
            return reviewed([{'kind': 'increment_drum_completion', 'last_process': _operand('n'), 'event': 'last_process_finished',
                              'flag': 'SM8029', 'ON_duration_operation_cycles': 1}])
        if full(r'SM8029 is used for many instructions as the instruction execution complete flag\.'):
            return reviewed([{'kind': 'shared_instruction_completion_flag', 'device': 'SM8029', 'scope': 'multiple_instruction_forms'}],
                            dimension='resources.shared_device')
        if full(r'Use SM8029 as a contact just after a corresponding instruction\.'):
            return reviewed([{'kind': 'completion_flag_contact_placement', 'device': 'SM8029',
                              'required_location': 'immediately_after_corresponding_instruction'}], dimension='resources.instruction_usage')

    if page == 935:
        if full(r'This function refreshes only the corresponding devices in the middle of a scan and receives an '
                r'external input or generates an output\.'):
            return reviewed([{'kind': 'explicit_IO_refresh', 'timing': 'middle_of_scan', 'extent': 'corresponding_devices_only',
                              'operations': ['receive_external_input', 'generate_external_output']}])
        if full(r'Since the input receptions and external outputs are performed at one time only after the END '
                r'instruction is executed in the program, a pulse signal cannot be output externally in the middle of a scan\.'):
            return reviewed([{'kind': 'ordinary_scan_IO_refresh', 'timing': 'after_END', 'middle_of_scan_external_pulse': False,
                              'qualification': 'ordinary_IO_refresh_without_explicit_REF'}])
        if full(r'The execution of the I/O refresh instruction forcibly refreshes the corresponding input \(X\) or '
                r'output \(Y\) in the middle of program execution, and then a pulse signal can be output externally in the middle of a scan\.'):
            return reviewed([{'kind': 'explicit_IO_refresh_external_effect', 'target_classes': ['X', 'Y'],
                              'timing': 'middle_of_program_execution', 'middle_of_scan_external_pulse': True}])
    if page in {995, 998}:
        comparison = full(r'When the current value of a high-speed counter of the channel specified in \(s2\) becomes '
                          r'the comparison value \(s1\) \(for example, when the current value changes from "199" to '
                          r'"200" or from "201" to "200" if the comparison value is K200\), the bit device \(d\) is '
                          r'(set to ON|reset to OFF) regardless of the scan time\.')
        if comparison and (comparison[1].lower() == 'set to on') == (page == 995):
            return reviewed([{'kind': 'hardware_counter_equality_event', 'channel': _operand('s2'), 'comparison_value': _operand('s1'),
                              'when_counter': 'becomes_equal_to_comparison_value', 'trigger_count_directions': ['up', 'down'],
                              'destination': _operand('d'), 'new_state': page == 995, 'timing': 'independent_of_CPU_scan',
                              'documented_example': {'comparison_value': 200, 'transitions': [[199, 200], [201, 200]]}}])
        if page == 995 and full(r'In this instruction, the comparison processing is executed after the count processing in the high-speed counter\.'):
            return reviewed([{'kind': 'high_speed_counter_comparison_order', 'ordered_processing': ['counter_count_update', 'comparison']}])
        if page == 995 and full(r'Use DHSCS if the output should be given when the counting result becomes equivalent '
                                r'to the comparison value regardless of the scan time of the CPU module\.'):
            return reviewed([{'kind': 'hardware_counter_comparison_use', 'recommended_form': 'DHSCS',
                              'when_required': 'output_at_counter_equality_independent_of_CPU_scan'}], dimension='resources.instruction_usage')
        if full(r'When the number of instructions that can be simultaneously used is exceeded, use a general-purpose comparison instruction\.'):
            return reviewed([{'kind': 'hardware_counter_comparison_capacity_fallback', 'when': 'simultaneous_instruction_capacity_exceeded',
                              'use': 'general_purpose_comparison_instruction'}], dimension='resources.instruction_usage')
        if page == 995 and full(r'If using the high-speed comparison match interrupt, the corresponding interrupt '
                                r'programs can be executed by setting the interrupt pointers \(I16 to I23\) to \(d\) as shown below\.'):
            return reviewed([{'kind': 'counter_comparison_interrupt_destination', 'destination': _operand('d'),
                              'when_function': 'high_speed_comparison_match_interrupt', 'pointer_domain': ['I16', 'I23'],
                              'effect': 'execute_corresponding_interrupt_program'}])
    if page in {1012, 1014}:
        if full(r'These instructions transfer the data in the device specified by \(s\) to the device specified by \(d\)\.'):
            return reviewed([{'kind': 'high_speed_current_value_transfer', 'source': _operand('s'), 'destination': _operand('d'),
                              'width_bits': 16 if page == 1012 else 32, 'source_value': 'latest_hardware_counter_value'}],
                            dimension='effects.documented_transfer')
        clear = full(r'(?:At this time, if the value of \(n\) is K0, the value of \(s\) is not cleared\.|'
                     r'If the value of \(n\) is K1, the value of \(s\) is cleared to "0" after the transfer\.)')
        if clear:
            return reviewed([{'kind': 'high_speed_transfer_source_clear_selection', 'selector': _operand('n'), 'source': _operand('s'),
                              'cases': [{'value': 0, 'after_transfer': 'source_not_cleared'},
                                        {'value': 1, 'after_transfer': 'source_cleared_to_zero_if_clearable_source'}]}])
        if page == 1012 and full(r'The value is cleared only for the SD8099 special device supporting high-speed transfer\.'):
            return reviewed([{'kind': 'high_speed_transfer_clearable_sources', 'source': _operand('s'), 'when_selector': {'subject': _operand('n'), 'value': 1},
                              'clearable_devices': ['SD8099']}])
        if page == 1014 and full(r'The value is cleared only for SD devices for the current value of high-speed counters '
                                 r'or LC devices used as a high-speed counter when using the FX3 compatible high-speed counter\.'):
            return reviewed([{'kind': 'high_speed_transfer_clearable_sources', 'source': _operand('s'), 'when_selector': {'subject': _operand('n'), 'value': 1},
                              'cases': [{'device_class': 'SD_high_speed_counter_current_value'},
                                        {'device_class': 'LC_high_speed_counter', 'when': 'FX3_compatible_high_speed_counter_enabled'}]}])
        adjacent = full(r'When (HCMOV|DHCMOV) instruction is used just before a comparison instruction '
                        r'\((CMP, ZCP|DCMP, DZCP) or comparison contact instruction\), the latest value of the high-speed counter is used in comparison\.')
        if adjacent and ((adjacent[1].upper() == 'HCMOV') == (page == 1012)):
            return reviewed([{'kind': 'high_speed_transfer_adjacent_comparison', 'transfer_form': adjacent[1].upper(),
                              'when_order': 'transfer_immediately_before_comparison',
                              'comparison_forms': adjacent[2].upper().split(', ') + ['comparison_contact'],
                              'comparison_input': 'latest_high_speed_counter_value'}])
    if page in {832, 833}:
        vocabulary = set(('the switch functions shown in table below are assigned to each of devices specified for selector '
                          'switches operation mode when is it necessary set as rotary so that they do not turn on at '
                          'same time wire unused but cannot be used any other purpose because occupied by ist instruction '
                          'source device number example function descriptions individual load turned and off an pushbutton '
                          'return zero point pressed machine automatically returns stepping every start button performs '
                          'one process cycle while located automatic stops if stop middle immediately after continuous '
                          'from last position finishes current then starts condition given command input following '
                          'switched controlled status always remains except during in turns transfer disable '
                          'run pulse instantaneously complete completed user program established all output reset '
                          'executed stl monitoring enable either step relay s or monitor relays initial state a selected '
                          'among again can finished no changed ladder').split())
        if page == 832 and full(r'When the stop button is pressed, the machine finishes the current cycle until the zero '
                                r'point, and then stops at the zero point\.'):
            return reviewed([{'kind': 'IST_continuous_mode_stop', 'when_mode': 'continuous', 'event': 'STOP_pressed',
                              'operation': ['finish_current_cycle', 'stop_at_zero_point']}], native=True)
        if page == 833 and full(r'If the devices are switched among individual operation \(X20\), zero return \(X21\) and '
                                r'automatic operation \(X22, X23 and X24\) while the zero return complete device '
                                r'\(SM8043\) is OFF, all outputs are set to OFF\.'):
            return reviewed([{'kind': 'IST_mode_switch_outputs', 'when_SM8043': False,
                              'event': 'switch_between_individual_zero_return_automatic_mode', 'outputs': False}], native=True)
        if page == 833 and full(r'Automatic operation can be started again after zero return is completed\.'):
            return reviewed([{'kind': 'IST_automatic_restart', 'requires': 'zero_return_completed'}], native=True)
        if _chart_tokens_only(clause, vocabulary):
            if page == 832:
                return reviewed([{'kind': 'IST_selector_input_table', 'source': _operand('s'),
                                  'source_example_head': 'X20', 'occupied_input_count': 8,
                                  'unwired_occupied_inputs': 'cannot_be_reused_for_other_purposes',
                                  'mode_selector_offsets': [0, 1, 2, 3, 4], 'mode_selectors': 'mutually_exclusive_rotary_switch_positions',
                                  'inputs': [{'offset': 0, 'role': 'individual_operation', 'operation': 'each_load_by_its_own_pushbutton'},
                                             {'offset': 1, 'role': 'zero_return_mode'},
                                             {'offset': 2, 'role': 'stepping', 'each_START_press': 'one_process'},
                                             {'offset': 3, 'role': 'cycle', 'START_at_zero': 'one_automatic_cycle_then_stop_at_zero',
                                              'STOP_midcycle': 'stop_immediately', 'START_after_midcycle_stop': 'resume_from_last_position_then_stop_at_zero'},
                                             {'offset': 4, 'role': 'continuous', 'START_at_zero': 'start_continuous_cycles',
                                              'STOP': 'finish_current_cycle_then_stop_at_zero'},
                                             {'offset': 5, 'role': 'zero_return_START'}, {'offset': 6, 'role': 'automatic_START'},
                                             {'offset': 7, 'role': 'STOP'}]}], native=True,
                                dimension='parameters.IST_selector_functions', source_binding='all_eight_selector_rows_and_cycle_stop_restart_qualifications')
            return reviewed([{'kind': 'IST_automatic_device_control', 'when_IST_command_input': True,
                              'when_IST_command_input_OFF': 'no_automatic_switching',
                              'devices': [
                                  {'device': 'SM8040', 'role': 'STL_transfer_disable',
                                   'ON_conditions': ['individual_mode', 'stepping_mode_except_START_press',
                                                     'STOP_pressed_in_zero_return_or_cycle_mode'],
                                   'OFF_conditions': ['START_pressed_in_stepping_mode',
                                                      'after_STOP_pressed_in_zero_return_or_cycle_mode'],
                                   'after_STOP_phase_duration': 'not_stated_in_table'},
                                  {'device': 'SM8041', 'role': 'transfer_start',
                                   'ON_conditions': ['START_pressed_in_stepping_or_cycle_mode', 'after_START_pressed_in_cycle_mode'],
                                   'OFF_conditions': ['RUN_to_STOP', 'individual_mode', 'zero_return_mode',
                                                      'after_STOP_pressed_in_continuous_mode']},
                                  {'device': 'SM8042', 'role': 'start_pulse', 'ON_condition': 'START_pressed_instantaneously',
                                   'OFF_condition': 'otherwise', 'exact_pulse_scans': 'not_stated_in_table'},
                                  {'device': 'SM8043', 'role': 'zero_return_complete', 'ON_condition': 'zero_return_completed_in_user_program',
                                   'OFF_conditions': ['RUN_to_STOP', 'zero_return_not_completed']},
                                  {'device': 'SM8044', 'role': 'zero_return_condition', 'ON_condition': 'zero_point_condition_established_in_user_program',
                                   'printed_OFF_conditions': ['RUN_to_STOP', 'zero_return_not_completed']},
                                  {'device': 'SM8045', 'role': 'all_output_reset_disable',
                                   'ON_condition': 'all_output_reset_not_executed_in_user_program',
                                   'OFF_condition': 'all_output_reset_executed_in_user_program'},
                                  {'device': 'SM8046', 'role': 'STL_state_ON',
                                   'ON_condition': {'all': ['SM8047_ON', 'any_step_relay_ON']},
                                   'OFF_condition': {'any': ['SM8047_OFF', {'all': ['SM8047_ON', 'all_step_relays_OFF']}]}},
                                  {'device': 'SM8047', 'role': 'enable_STL_monitoring', 'ON_condition': 'IST_command_given',
                                   'OFF_condition': 'step_ladder_finished_in_user_program'},
                                  {'device': 'S0', 'role': 'individual_operation_initial_state', 'ON_condition': 'individual_mode_selected',
                                   'OFF_condition': 'other_than_individual_mode'},
                                  {'device': 'S1', 'role': 'zero_return_initial_state', 'ON_condition': 'zero_return_mode_selected',
                                   'OFF_condition': 'other_than_zero_return_mode'},
                                  {'device': 'S2', 'role': 'automatic_operation_initial_state', 'ON_condition': 'automatic_mode_selected',
                                   'OFF_condition': 'other_than_automatic_mode'}],
                              'mode_switch_while_zero_return_incomplete': {'when_SM8043': False,
                                                                         'mode_groups': ['individual', 'zero_return', 'automatic'],
                                                                         'effect': 'all_controlled_outputs_OFF'},
                              'automatic_restart_requirement': 'zero_return_completed'}], native=True,
                            dimension='execution.IST_device_state_table', source_binding='whole_SM8040_to_SM8047_S0_to_S2_table_with_command_and_mode_change_premises')
    if page == 440:
        if full(r'These instructions execute the program specified by the pointer number when the execution command is on\.'):
            return reviewed([{'kind': 'conditional_pointer_jump', 'when_execution_command': True,
                              'target': 'specified_pointer_number'}], native=True)
        if full(r'When the execution command is off, the program in the next step is executed\.'):
            return reviewed([{'kind': 'conditional_pointer_jump', 'when_execution_command': False,
                              'target': 'next_step'}], native=True)
    if page == 445:
        if full(r'This instruction disables the execution of the interrupt program until the EI instruction is '
                r'executed, even if the interrupt cause occurs\.'):
            return reviewed([{'kind': 'interrupt_disable', 'form': 'DI', 'priority_argument': 'absent',
                              'interrupt_requests': 'held', 'held_until': 'EI_execution'}])
        if full(r'Even though an interrupt occurs between the DI and EI instructions, the execution of the A \| '
                r'interrupt is held until the processing between the instructions ends\.'):
            return reviewed([{'kind': 'deferred_interrupt_execution', 'when_interrupt_request': 'between_DI_and_EI',
                              'interrupt_execution': 'deferred_until_DI_EI_region_finishes'}])
        if full(r'For the operation of the EI instruction when using the interrupt disable instruction with a '
                r'specified priority or lower \(DI instruction with an argument\), refer to \[GLYPH-F0C1\]Page 445 '
                r'Disabling the interrupt program with specified priority or lower\.'):
            return reviewed([{'kind': 'manual_cross_reference', 'topic': 'EI_after_DI_with_priority_argument',
                              'printed_page': 445, 'physical_PDF_page': 447}],
                            dimension='resources.instruction_usage', content_role='source_navigation')
    if page == 448 and full(r'Interrupts \(requests\) that are generated after the DI instruction are processed after '
                            r'the EI instruction is executed\.'):
        return reviewed([{'kind': 'priority_disabled_interrupt_request', 'request_event': 'after_DI_execution',
                          'when_priority': 'disabled_by_DI', 'execution_event': 'after_corresponding_EI_execution'}])
    if page == 455:
        if full(r'When an interrupt \(input or timer\) is generated while the main program is executing, the program '
                r'execution jumps to an interrupt \(I\) routine\.'):
            return reviewed([{'kind': 'interrupt_program_entry', 'when': 'input_or_timer_interrupt_during_main_program',
                              'target': 'corresponding_interrupt_I_routine'}])
        if full(r'The IRET instruction returns the program execution to the main routine\.'):
            return reviewed([{'kind': 'interrupt_return', 'event': 'IRET_execution', 'target': 'interrupted_main_routine'}])
        if full(r'The table below shows three types of jump to an interrupt routine\.') or full(r'Function \| Interrupt No\.') or (
                clause.startswith('Description Interrupt from inputs') and _chart_tokens_only(clause, set(
                    'description interrupt from inputs including pointer used for the cpu built in functions such as input '
                    'high speed comparison counter match setting internal fixed cycle interrupts of timer module '
                    'with equipped interruption function to'.split()))):
            return reviewed([{'kind': 'interrupt_pointer_function_table', 'ranges': [
                {'first': 'I0', 'last': 'I23', 'role': 'CPU_built_in_input_and_high_speed_comparison_match_interrupt'},
                {'first': 'I28', 'last': 'I31', 'role': 'internal_timer_fixed_cycle_interrupt'},
                {'first': 'I50', 'last': 'I177', 'role': 'module_with_interrupt_function', 'requires': 'module_footnote_1'}]}],
                            dimension='parameters.interrupt_pointer_functions')
    if page == 458:
        if full(r'These instructions are used when the scan time exceeds the value set for the watchdog timer '
                r'depending on the condition\.'):
            return reviewed([{'kind': 'watchdog_reset_usage', 'when': 'conditional_long_scan_exceeds_watchdog_setting',
                              'instruction_forms': ['WDT', 'WDTP']}], dimension='resources.instruction_usage')
        if full(r'If the scan time exceeds the value set for the watchdog timer every scan, change the setting of '
                r'the watchdog timer in the parameter setting of the engineering tool\.'):
            return reviewed([{'kind': 'watchdog_parameter_usage', 'when': 'every_scan_exceeds_watchdog_setting',
                              'required_action': 'change_watchdog_timer_parameter'}], dimension='resources.instruction_usage')
        if full(r'The WDT\(P\) instruction can be used more than once in one scan\.'):
            return reviewed([{'kind': 'watchdog_reset_frequency', 'allowed': 'multiple_executions_per_scan'}])
        if full(r'However, note that turning off the output takes some time if an error occurs\.') and 'WDT' in _page_layout(section, page):
            return reviewed([{'kind': 'watchdog_reset_error_latency', 'when': 'error_occurs_in_program_using_WDT',
                              'output_OFF': 'delayed', 'delay_value': 'not_specified'}])
    if page == 465:
        if full(r'While the command input is ON, the CALL instruction is executed and the program execution jumps '
                r'to a step with a label \(Pn\)\.') or full(r'Then, a subroutine program with the label \(Pn\) is executed\.'):
            return reviewed([{'kind': 'subroutine_call', 'when_command': True, 'form': 'CALL',
                              'target': 'subroutine_label_Pn'}], native=True)
        if full(r'When the RET \(SRET\) instruction is executed, the program execution returns to the step following '
                r'the CALL instruction\.'):
            return reviewed([{'kind': 'subroutine_return', 'event': ['RET', 'SRET'], 'target': 'step_after_CALL'}], native=True)
        if full(r'At the end of the main program, put FEND instruction\.') or full(
                r'Put a label \(Pn\) for the CALL instruction after the FEND instruction\.'):
            return reviewed([{'kind': 'subroutine_program_placement', 'main': {'first': 'step_0', 'last': 'FEND'},
                              'subroutine': {'first': 'label_Pn_after_FEND', 'last': 'RET'}}],
                            native=True, dimension='parameters.program_layout')
        if clause.startswith('A | A: User program Command |') and _chart_tokens_only(clause, set(
                'a user program command main call pn area from the step to fend instruction subroutine label ret'.split())):
            return reviewed([{'kind': 'subroutine_program_regions', 'main': {'first': 'step_0', 'last': 'FEND'},
                              'subroutine': {'first': 'label_Pn', 'last': 'RET'},
                              'call': 'main_to_label_Pn', 'return': 'RET_to_step_following_CALL'}], native=True)
    if page == 470:
        off_result = [
            {'device_class': 'nonretentive_1ms_10ms_100ms_timer', 'current_value': 0},
            {'device_class': 'retentive_1ms_10ms_100ms_timer_or_counter', 'status': 'held'},
            {'instruction_class': 'OUT', 'output': False},
            {'instruction_classes': ['SET', 'RST', 'SFT(P)', 'basic_instructions', 'applied_instructions'], 'status': 'held'},
            {'instruction_classes': ['PLS', 'pulse_instruction_suffix_P'], 'operation': 'condition_contact_OFF_processing'}]
        if (full(r'The following table lists the operation result of each coil instruction after the non-execution processing\.') or
                full(r'Regardless of the status of the condition contact, the following result is applied\.') or
                (clause.startswith(('Device used for operation |', 'Device in the ', 'PLS instruction, pulse instruction ')) and
                 _chart_tokens_only(clause, set('device used for operation result status ms timer retentive counter the current '
                                               'is held in out instruction forcibly turned off set rst or sft p basic applied '
                                               'pls pulse same as when condition contact'.split())))):
            return reviewed([{'kind': 'XCALL_non_execution_processing_table', 'event': 'XCALL_command_falling_edge',
                              'actual_subroutine_condition_contacts': 'ignored_for_this_processing', 'cases': off_result}], native=True)
        if clause.startswith('FEND P1 M0 |') and _chart_tokens_only(clause, set(
                'fend on ret off rising edge of the subroutine program is executed while not included falling '
                'non execution processing p'.split())):
            return reviewed([{'kind': 'XCALL_documented_edge_example', 'command': 'X0', 'subroutine_label': 'P1',
                              'cases': [{'command_event': 'OFF_to_ON', 'processing': 'execute_subroutine'},
                                        {'command_event': 'steady_ON', 'processing': 'execute_subroutine'},
                                        {'command_event': 'ON_to_OFF', 'processing': 'one_non_execution_processing'}]}],
                            native=True, content_role='worked_example')
    if page == 928:
        if full(r'The table below shows the related devices \(special register\)\.'):
            return reviewed([{'kind': 'DUTY_scan_counter_mapping', 'mappings': [
                {'output_aliases': ['SM' + str(420 + i), 'SM' + str(8330 + i)], 'scan_counter': 'SD' + str(8330 + i)}
                for i in range(5)]}], dimension='parameters.DUTY_device_mapping')
        if clause.startswith('(n1): (n1) scans (n2): (n2) scans ') and _chart_tokens_only(clause, set(
                'scans special relay for outputting the timing clock scan counting device status of and on off fixed '
                'to name description output in duty instruction register counted number instructio n'.split())):
            return reviewed([{'kind': 'DUTY_timing_and_device_table', 'destination': _operand('d'),
                              'positive_duration_cycle': {'ON_scans': _operand('n1'), 'OFF_scans': _operand('n2')},
                              'zero_duration_cases': [
                                  {'when': {'all': [{'subject': _operand('n1'), 'operator': '=', 'value': 0},
                                                   {'subject': _operand('n2'), 'operator': '>=', 'value': 0}]}, 'output': False},
                                  {'when': {'all': [{'subject': _operand('n1'), 'operator': '>', 'value': 0},
                                                   {'subject': _operand('n2'), 'operator': '=', 'value': 0}]}, 'output': True}],
                              'mappings': [{'output_aliases': ['SM' + str(420 + i), 'SM' + str(8330 + i)],
                                            'scan_counter': 'SD' + str(8330 + i)} for i in range(5)]}],
                            source_binding='waveform_zero_duration_cases_and_both_complete_device_tables')
    if page in {950, 954}:
        read_BFM = page == 950
        if read_BFM and full(r'This instruction reads \(n1\) points of buffer memory starting from \(s\) inside '
                             r'the intelligent function module number \(U/H\) to \(d\) in the CPU module\.'):
            return reviewed([{'kind': 'divided_buffer_memory_read', 'source': {'module': _operand('U/H'), 'buffer_head': _operand('s')},
                              'destination': _operand('d'), 'point_count': _operand('n1')}], dimension='effects.documented_transfer')
        if read_BFM and full(r'When \(n1\) exceeds 64 points, it divides and reads by several scans\. '
                             r'\(64 points are read in one scan\)'):
            return reviewed([{'kind': 'divided_buffer_memory_transfer', 'when_points': {'subject': _operand('n1'), 'operator': '>', 'value': 64},
                              'transfer': 'read', 'points_per_scan': 64, 'scan_count': 'several_as_printed'}])
        if not read_BFM and full(r'This instruction writes \(n1\) points of buffer memory starting from \(s1\) inside '
                                 r'the intelligent function module number \(U/H\) to \(s2\) in the CPU module\.'):
            return reviewed([{'kind': 'source_conflict', 'conflict': 'WBFM_prose_direction_disagrees_with_write_diagram',
                              'printed_source': {'module': _operand('U/H'), 'buffer_head': _operand('s1')},
                              'printed_destination': _operand('s2'), 'point_count': _operand('n1'),
                              'diagram_source': _operand('s2'),
                              'diagram_destination': {'module': _operand('U/H'), 'buffer_head': _operand('s1')},
                              'resolution': 'not_established'}], status='conflict', dimension='effects.documented_transfer')
        if not read_BFM and clause.startswith('When (n1) exceeds 64 points, it divides and writes by several scans. ') and _chart_tokens_only(
                clause, set('when exceeds points it divides and writes by several scans are read in one scan command '
                            'input cpu module number wbfm u h write bfm instruction execution complete flag non abnormal end m cid'.split())):
            return reviewed([{'kind': 'divided_buffer_memory_write_diagram', 'source': _operand('s2'),
                              'destination': {'module': _operand('U/H'), 'buffer_head': _operand('s1')},
                              'point_count': _operand('n1'), 'split_when_points_exceed': 64,
                              'normal_complete_device': 'SM8029', 'non_execution_device': 'SM8328', 'error_complete_device': 'SM8329'},
                             {'kind': 'source_conflict', 'conflict': 'WBFM_per_scan_sentence_says_read_during_write_operation',
                              'printed_points_per_scan': 64, 'printed_direction': 'read', 'section_operation': 'write',
                              'resolution': 'not_established'}], status='conflict')
        if not read_BFM and full(r'When this instruction or the RBFM instruction is executed in the same scan, '
                                 r'instruction non-execution flag \(SM8328\) is set to on, and execution of such an instruction is paused\.'):
            return reviewed([{'kind': 'divided_buffer_memory_serialization',
                              'when': {'all': ['RBFM_or_WBFM_in_another_step', 'same_module_number', 'same_scan']},
                              'paused_instruction': 'later_instruction', 'non_execution_flag': 'SM8328', 'flag_state': True}],
                            source_binding='same_module_number_qualification_from_related_device_table')
        if not read_BFM and full(r'When execution of the other target instruction is complete, the paused instruction resumes\.'):
            return reviewed([{'kind': 'divided_buffer_memory_resume',
                              'when': 'other_same_module_number_RBFM_or_WBFM_completes', 'paused_instruction': 'resumes'}])
    if page == 956 and full(r'The LOGTRGR instruction turns off the special relay \(logging completion, logging '
                            r'trigger, and after logging trigger\) with the logging setting number in the device specified by \(s\)\.'):
        return reviewed([{'kind': 'logging_trigger_reset', 'logging_setting_number': _operand('s'),
                          'clears': ['logging_completion', 'logging_trigger', 'after_logging_trigger']}])
    if page == 960:
        if full(r'The table below shows operations of internal circuits connected to the state relays, Internal circuit '
                r'diagram operation Execution in the contact When a state relay turns ON, a connected circuit '
                r'\(internal circuit\) is activated with a STL contact\.'):
            return reviewed([{'kind': 'STL_state_activation', 'when_state_relay': True, 'internal_circuit': 'active'}], native=True)
        if full(r'ON status Execution in the contact When a condition \(transfer condition\) provided between state relays '
                r'is satisfied, the next state relay turns ON, and the state relay OFF status \(for one \| which was been '
                r'ON before hand turns OFF \(transfer operation\)\.'):
            return reviewed([{'kind': 'STL_state_transfer', 'event': 'transfer_condition_satisfied',
                              'next_state': 'ON', 'former_state': 'OFF_via_transfer_lifecycle',
                              'coincident_state_bit_cycle': True, 'former_contact_image': 'OFF_immediately'}], native=True)
        if full(r'In the state relay ON status transfer process, both state relays are operation cycle\) \| ON for one operation cycle\.'):
            return reviewed([{'kind': 'STL_state_bit_overlap', 'when': 'ON_state_transfer',
                              'both_state_relay_bits': True, 'duration_operation_cycles': 1}], native=True)
        if full(r'In the next operation cycle after the ON status is transferred to the next state relay, the former '
                r'state relay is reset to OFF\.'):
            return reviewed([{'kind': 'STL_former_state_reset', 'event': 'operation_cycle_after_transfer',
                              'former_state_bit': False}], native=True)
        if full(r'A drive instruction connected to the bus line of the reset state relay is executed in the contact OFF '
                r'status for one operation cycle regardless of the actual contact status before the drive instruction\.'):
            return reviewed([{'kind': 'STL_forced_OFF_processing', 'when': 'former_state_reset_after_transfer',
                              'duration_operation_cycles': 1, 'processing': 'condition_contact_OFF',
                              'actual_condition_contact': 'ignored'}], native=True)
        if full(r'When the transfer state relay is used in a contact instruction, however, the contact image is executed '
                r'in the OFF status immediately after the transfer condition is satisfied\.'):
            return reviewed([{'kind': 'STL_transfer_contact_image', 'event': 'transfer_condition_satisfied',
                              'former_state_contact_image': False, 'timing': 'immediate'}], native=True)
        if full(r'Non-execution \| An instruction is not executed in the contact OFF status after the operation cycle '
                r'where the instruction was executed in the contact OFF status \(jump status\)\.'):
            return reviewed([{'kind': 'STL_inactive_jump', 'event': 'after_one_forced_OFF_processing_cycle',
                              'internal_instructions': 'not_executed', 'state': 'jump_status'}], native=True)
    if page == 961:
        retention = {'kind': 'STL_latched_state_retention', 'when_state_relay_class': 'latched',
                     'power_failure': 'ON_OFF_state_backed_up_by_nonvolatile_memory', 'RUN_to_STOP': 'state_held',
                     'RUN_again': 'resume_state_before_STOP'}
        if (full(r'When the step relay is latched, the ON/OFF status is backed up by nonvolatile memory against power failure\.') or
                full(r'Use this type of state relay if the operation should be restarted from the last point before power failure\.') or
                full(r'These relays hold the operation status also at the time of RUN to STOP\.') or
                full(r'If it RUN\(s\) again, the operation will be resumed from the State before STOP\.')):
            return reviewed([retention], native=True)
    if page == 989:
        if full(r'Same as the operation by the SET instruction \(SET S\[GLYPH-F06F\]/BL\[GLYPH-F06F\]\\S\[GLYPH-F06F\]\)\. '
                r'\(\[GLYPH-F0C1\]Page 983 Activating a step\)'):
            return reviewed([{'kind': 'SFC_coil_active_equivalence', 'when_drive_contact': True,
                              'operation': 'SET_specified_step_in_specified_block', 'manual_reference_page': 983}])
        if full(r'That is the same operation executed by the RST instruction \(RST S\[GLYPH-F06F\]/BL\[GLYPH-F06F\]\\S\[GLYPH-F06F\]\)\. '
                r'\(\[GLYPH-F0C1\]Page 985 Deactivating a step\) \(However, when the instruction is executed within the action, the instruction is ignored\.') or full(
                r'That is the different operation executed by the RST instruction\.\)'):
            return reviewed([{'kind': 'SFC_coil_deactivation', 'when_drive_contact': False,
                              'cases': [{'when_context': 'outside_action', 'operation': 'RST_specified_step_in_specified_block'},
                                        {'when_context': 'within_action', 'operation': 'instruction_ignored'}],
                              'differs_from_RST_in_action': True, 'manual_reference_page': 985}])
    if page in {448, 449} and clause.startswith('A | EI |'):
        vocabulary = set(('a b t scan execution type program interrupt enabled section for all priority disabled '
                          'the or lower higher because interrupts with are already disabling is not changed without '
                          'an argument by executing ei once where di instruction priorities').split())
        if _chart_tokens_only(clause, vocabulary):
            def event(instruction, disabled_from=None, **details):
                return {'instruction': instruction, 'disabled_numeric_priorities_from': disabled_from, **details}
            events = None
            if page == 448 and clause.startswith('A | EI | DI K3 | DI K2 | EI | EI t '):
                events = [event('EI'), event('DI_K3', 3), event('DI_K2', 2), event('EI', 3), event('EI')]
            elif page == 448 and clause.startswith('A | EI | DI K2 | DI K3 | EI | EI t '):
                events = [event('EI'), event('DI_K2', 2), event('DI_K3', 2, disabling_priority_changed=False), event('EI'), event('EI')]
            elif page == 449 and clause.startswith('A | EI | DI K3 | EI t '):
                events = [event('EI', program='scan'), event('DI_K3', 3, program='scan'),
                          event('DI_K2', 2, program='interrupt'), event('EI', 3, program='interrupt'),
                          event('IRET', 3, program='interrupt'), event('EI', program='scan')]
            elif page == 449 and clause.startswith('A | EI | DI | DI | DI | EI t '):
                events = [event('EI'), event('DI', 1), event('DI', 1), event('DI', 1), event('EI')]
            elif page == 449 and clause.startswith('A | EI | DI K2 | DI | DI | EI | EI | EI t '):
                events = [event('EI'), event('DI_K2', 2), event('DI', 1), event('DI', 1),
                          event('EI', 2), event('EI'), event('EI')]
            elif page == 449 and clause.startswith('A | EI | DI | DI | DI K2 | EI | EI t '):
                events = [event('EI'), event('DI', 1), event('DI', 1), event('DI_K2', 1, disabling_priority_changed=False),
                          event('EI'), event('EI')]
            if events:
                return reviewed([{'kind': 'documented_interrupt_priority_trace', 'events': events,
                                  'numeric_priority_1_is_highest': True, 'null_disabled_priority_means': 'all_priorities_enabled',
                                  'scope': 'documented_program_trace'}], content_role='worked_example',
                                source_binding='whole_priority_waveform_including_DI_and_EI_positions')
    if page == 445 and full(r'DI A EI FEND In \| B A: Sequence program B: Interrupt Program'):
        return reviewed([{'kind': 'documented_interrupt_defer_trace', 'scan_program': 'A', 'interrupt_program': 'B',
                          'request_arrives': 'inside_DI_EI_region', 'interrupt_start': 'after_EI',
                          'return': 'resume_interrupted_scan_program', 'while_DI_EI': 'interrupt_request_held'}],
                        content_role='worked_example')
    network_charts = {1065: ('SP.SOCOPEN', 'd', 'connection_open'),
                      1067: ('SP.SOCCLOSE', 'd', 'connection_closed'),
                      1080: ('SP.ECPRTCL', 'd', 'protocols_executed'),
                      1117: ('S(P).CPRTCL', 'd', 'protocols_executed')}
    if page in network_charts and clause.startswith(('ON OFF Completion device', 'OFF Completion device', 'SP.SOCCLOSE instruction', 'SP.ECPRTCL instruction')):
        vocabulary = set(('sp socopen socclose ecprtcl instruction on off completion device when failed scan normally '
                          'completed connection open closed executing a protocols protocol the in which no is set with '
                          'parameter can be opened and used for details refer to melsec iq f fx users user s manual communication').split())
        if _chart_tokens_only(clause, vocabulary):
            form, completion, operation = network_charts[page]
            rules = [{'kind': 'network_operation_completion_chart', 'form': form, 'operation': operation,
                      'completion': {'device': _operand(completion), 'ON_event': 'END_in_completion_scan',
                                     'OFF_event': 'next_END', 'duration_scans': 1},
                      'outcome': {'device': _operand(completion, 1), 'normal': False, 'error': 'same_single_scan_pulse_as_completion'},
                      'latency_scans': 'not_fixed_by_illustrated_chart'}]
            if page == 1065 and 'no protocol' in clause:
                rules.append({'kind': 'socket_open_without_protocol_parameter', 'connection': 'no_protocol_set_in_parameters', 'allowed': True})
            return reviewed(rules)
    if page == 1065 and full(r'In this case, specify 8000H in \(s2\)\+0 and the contents of the open processing in \(s2\)\+2 to \(s2\)\+6\.'):
        return reviewed([{'kind': 'socket_open_without_protocol_parameter', 'when_connection': 'no_protocol_set_in_parameters',
                          'application_setting': {'subject': _operand('s2'), 'value': 0x8000},
                          'open_processing_settings': {'head': _operand('s2', 2), 'last': _operand('s2', 6)}}])
    if page == 1070:
        if full(r'The following figure shows the timing of the receive processing with the SP\.SOCRCV instruction\.') or (
                clause.startswith('Open | Send | Data reading processing data |') and _chart_tokens_only(clause, set(
                    'open send data reading processing on completion signal off end sequence program receive state '
                    'execute sp socrcv instruction scan when failed n device'.split()))):
            return reviewed([{'kind': 'socket_receive_waveform', 'form': 'SP.SOCRCV', 'connection_open_bit': 'SD10680.n',
                              'receive_state_bit': 'SD10682.n', 'events': [
                                  'open_connection', 'receive_data', 'END_sets_receive_state', 'execute_SP_SOCRCV_after_receive_state_ON',
                                  'END_after_instruction_clears_receive_state', 'data_read_processing_completes', 'END_sets_completion'],
                              'completion': {'device': _operand('d2'), 'duration_scans': 1, 'OFF_event': 'next_END'},
                              'error': {'device': _operand('d2', 1), 'duration': 'same_pulse_as_completion_only_if_failed'},
                              'TCP_only': 'receive_ACK', 'processing_latency_scans': 'not_fixed_by_figure'}],
                            source_binding='complete_receive_waveform_and_TCP_ACK_qualification')
    if page == 1073:
        if clause.startswith('Open completion signal ON OFF ') and _chart_tokens_only(clause, set(
                'open completion signal on off end processing sequence program execute sp socsnd instruction scan '
                'when failed data send n device'.split())):
            return reviewed([{'kind': 'socket_send_waveform', 'form': 'SP.SOCSND', 'connection_open_bit': 'SD10680.n',
                              'when': 'connection_open', 'events': ['execute_instruction', 'data_send_processing',
                                                                   'END_in_processing_completion_scan_sets_completion'],
                              'completion': {'device': _operand('d'), 'duration_scans': 1, 'OFF_event': 'next_END'},
                              'error': {'device': _operand('d', 1), 'normal': False, 'error': 'same_completion_pulse'},
                              'wire_transmission_completion': 'not_implied_by_completion_device_ON'}])
        if full(r'Data transmission \(send\)\*1 \| \*1 Even after completion device ACK \| 14 \(Only TCP\) \| '
                r'turns ON, data transmission may continue\.'):
            return reviewed([{'kind': 'socket_send_wire_timing', 'when': 'completion_device_ON',
                              'data_transmission': 'may_continue', 'ACK_protocol': 'TCP_only'}])
    if page == 1085:
        if full(r'The normal or abnormal completion of the SP \.SLMPSND instruction can be confirmed with the '
                r'completion device \(d2\) specified in the setting data and the completion status indication device \(d2\)\+1\.'):
            return reviewed([{'kind': 'network_completion_observation', 'form': 'SP.SLMPSND',
                              'completion_device': _operand('d2'), 'outcome_device': _operand('d2', 1)}])
        if full(r'Device Operation Completion device \(d2\) The device is turned on by END processing for the scan in '
                r'which the SP\.SLMPSND instruction is completed and turned off by next END processing\.'):
            return reviewed([{'kind': 'network_completion_pulse', 'form': 'SP.SLMPSND', 'device': _operand('d2'),
                              'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END', 'ON_duration_scans': 1}])
        vocabulary = set('request destination module i o no header footer subheader footersubheader station network '
                         'multidrop data length monitoring timer specified by except some devices to when completed '
                         'response end code failed responding error information command subcommandend stored in n'.split())
        if _chart_tokens_only(clause, vocabulary) and clause.startswith(('Request ', 'Header ', 'Response ', 'Station ', 'Error information ')):
            return reviewed([{'kind': 'SLMP_frame_device_layout',
                              'request': {'fields': [
                                  {'field': 'header_subheader', 'head': _operand('s1', 3), 'last': _operand('s1', 5)},
                                  {'field': 'network_number', 'device': _operand('s1', 6)},
                                  {'field': 'station_number', 'device': _operand('s1', 7)},
                                  {'field': 'module_IO_number', 'device': _operand('s1', 8)},
                                  {'field': 'multidrop_station_number', 'device': _operand('s1', 9)},
                                  {'field': 'request_data_length', 'device': _operand('s2')},
                                  {'field': 'monitoring_timer', 'device': _operand('s2', 1)},
                                  {'field': 'request_data', 'head': _operand('s2', 2), 'last_offset': 'n'}],
                                          'header_control_data_exception': 'some_header_devices_not_user_specified_as_drawn'},
                              'response': {'destination': _operand('d1'), 'length_offset': 0, 'end_code_offset': 1,
                                           'normal_data_offset': 2, 'normal_last_offset': 'n',
                                           'error_information': ['responding_network_number', 'responding_station_number',
                                                                 'request_destination_module_IO_number', 'request_destination_multidrop_station_number',
                                                                 'command', 'subcommand'],
                                           'header_and_footer': 'not_part_of_stored_d1_span'}}],
                            dimension='memory.SLMP_frame_layout', source_binding='request_normal_response_and_error_response_diagram_device_brackets')
    if page in {1088, 1093}:
        sending = page == 1088
        form = 'SP.FTPPUT' if sending else 'SP.FTPGET'
        if full(r'This instruction sends files in the CPU module, which are specified by \(s2\), to the folder path of '
                r'the FTP server, which is specified by \(s3\)\.') or full(
                r'This instruction retrieves files on the FTP server, which are specified by \(s2\), to the folder path '
                r'of the CPU module, which is specified by \(s3\)\.'):
            return reviewed([{'kind': 'FTP_transfer_direction', 'form': form,
                              'source': {'files': _operand('s2'), 'location': 'CPU_module' if sending else 'FTP_server'},
                              'destination': {'folder_path': _operand('s3'), 'location': 'FTP_server' if sending else 'CPU_module'}}],
                            dimension='effects.documented_file_operation')
        if full(r'The CPU module opens a connection with the FTP server set in the module parameters '
                r'\("?FTP Client Settings"?\) at execution of the instruction, and closes a connection after '
                r'(?:sending|retrieving) files\.'):
            return reviewed([{'kind': 'FTP_connection_lifecycle', 'form': form, 'server': 'module_FTP_Client_Settings',
                              'open_event': 'instruction_execution', 'close_event': 'after_file_transfer'}])
    if page in {1089, 1094}:
        form = 'SP.FTPPUT' if page == 1089 else 'SP.FTPGET'
        if full(r'If an error occurs in any one of the files to be (?:transferred|retrieved) during execution of the '
                r'SP\s*\.(?:FTPPUT|FTPGET) instruction, the transfer processing will be stopped upon detection of the '
                r'error, and the rest of target files will not be transferred\.'):
            return reviewed([{'kind': 'FTP_multi_file_abort', 'form': form, 'when': 'any_target_file_transfer_errors',
                              'event': 'error_detection', 'processing': 'stopped', 'remaining_files': 'not_transferred'}])
        if full(r'Two or more asterisks \'\*\' are used in a file name \(before the period\) or an extension\. '
                r'\(Example: \*abc\*\.(?:txt|csv)\)'):
            return reviewed([{'kind': 'FTP_wildcard_rejection', 'filename': _operand('s2'),
                              'when': 'two_or_more_asterisks_in_same_name_or_extension', 'result': 'error',
                              'printed_example': 'abc_with_asterisk_before_and_after'}], dimension='constraints.file_wildcard')
        if full(r'An asterisk \'\*\' and a question mark \'\?\' are used in a file name \(before the period\) or an '
                r'extension\. \(Example: \*ab\?\. csv\) When any wild card character is used, the number of files '
                r'that can be transferred is determined by the total size of the file names of the specified files\.'):
            return reviewed([{'kind': 'FTP_wildcard_rejection', 'filename': _operand('s2'),
                              'when': 'asterisk_and_question_mark_together_in_same_name_or_extension', 'result': 'error'},
                             {'kind': 'FTP_wildcard_file_count_limit', 'basis': 'aggregate_target_file_name_size'}],
                            dimension='constraints.file_wildcard')
        formula = '(Fi + NM) + 1 < 32768 [bytes]'
        if formula in clause and _chart_tokens_only(clause, set(
                'an asterisk and a question mark are used in file name before the period or extension example ab '
                'txt fi nm bytes n total number of files that match wild card specification size names m specific '
                'information fixed value characters maximum logging log bin st character nd rd th indicates end string '
                'b if transfer function instruction is executed without satisfying following condition completes with error'.split())):
            rules = [{'kind': 'FTP_wildcard_storage_limit', 'when_filename': 'wildcard_used',
                      'formula': {'operator': '<', 'left': {'sum': ['total_target_file_name_bytes',
                                                                    {'multiply': [6, 'target_file_count']}, 1]}, 'right': 32768},
                      'failure_result': 'instruction_completes_with_error', 'overhead_bytes_per_file': 6}]
            if page == 1089:
                rules.extend([{'kind': 'FTP_wildcard_rejection', 'filename': _operand('s2'),
                               'when': 'asterisk_and_question_mark_together_in_same_name_or_extension', 'result': 'error'},
                              {'kind': 'FTP_source_path_layout', 'filename': _operand('s2'),
                               'maximum_characters': 255, 'encoding': 'Unicode', 'end': 'zero_word',
                               'printed_example': 'drive_2_LOGGING_LOG01_00000001_00000001_BIN'}])
            return reviewed(rules, dimension='constraints.file_wildcard')
        if page == 1094 and full(r'When a file with large size is transferred, the transfer time may be longer\. '
                                 r'\(\(Example\) Transfer time of 16-MB file with scan time of 5 ms: Approx\. 450 sec\)'):
            return reviewed([{'kind': 'documented_FTP_transfer_time_example', 'form': form, 'file_size_MB': 16,
                              'scan_time_ms': 5, 'approx_transfer_seconds': 450, 'scope': 'printed_example'}],
                            dimension='resources.documented_transfer_example', content_role='worked_example')
        if page == 1094 and full(r'In addition, an error code is stored in the device specified by \(s1\)\+1\.'):
            return reviewed([{'kind': 'FTP_transfer_error_status', 'form': form, 'when': 'completed_with_error',
                              'error_code_destination': _operand('s1', 1)}])
    if page in {1091, 1095}:
        form = 'SP.FTPPUT' if page == 1091 else 'SP.FTPGET'
        if full(r'SM1392 \(FTP client connection status\) is on while the CPU module is connected to the FTP server, '
                r'and SM1392 turns off when disconnected\.'):
            return reviewed([{'kind': 'FTP_connection_status', 'device': 'SM1392', 'while_connected': True,
                              'when_disconnected': False}])
        if full(r'SM753 \(File access in progress\) turns on while the SP\s*\.(?:FTPPUT|FTPGET) instruction is being executed\.'):
            return reviewed([{'kind': 'FTP_file_access_flag', 'form': form, 'device': 'SM753', 'while': 'instruction_executing', 'state': True}])
        if full(r'While SM753 is on, the SP\s*\.(?:FTPPUT|FTPGET) instruction cannot be executed\.') or full(
                r'If executed, no processing is performed\.'):
            return reviewed([{'kind': 'FTP_busy_rejection', 'form': form, 'when_SM753': True, 'processing': 'none'}])
        if full(r'If the SP\s*\.(?:FTPPUT|FTPGET) instruction is executed while the SP\s*\.(?:FTPPUT|FTPGET)'
                r'(?: or SP\s*\.FTPGET)? instruction is being executed, the instruction is ignored and not executed '
                r'until the currently executing instruction completes\.'):
            return reviewed([{'kind': 'FTP_instruction_busy_rejection', 'form': form,
                              'busy_forms_as_printed': ['SP.FTPPUT'] if page == 1091 else ['SP.FTPPUT', 'SP.FTPGET'],
                              'new_execution': 'ignored_not_queued', 'busy_period_ends': 'currently_executing_instruction_completes'}])
        if full(r'When the instruction is ignored, SM699 \(Dedicated instruction skip flag\) turns on\.'):
            return reviewed([{'kind': 'FTP_skipped_instruction_flag', 'form': form, 'when': 'instruction_ignored_due_to_busy',
                              'device': 'SM699', 'state': True}])
        if page == 1091 and full(r'When the number of files matching the wild card exceeds the upper limit number of '
                                 r'transferable files, the instruction will be completed abnormally\.'):
            return reviewed([{'kind': 'FTP_wildcard_file_limit', 'when': 'matched_file_count_exceeds_transferable_limit', 'completion': 'abnormal'}])
        if page == 1091 and full(r'When the wild card is designated in the file name, the file name in the transfer '
                                 r'destination will be in all capital letters\.'):
            return reviewed([{'kind': 'FTP_wildcard_destination_name', 'when_filename': 'wildcard_used', 'destination_file_name': 'uppercase'}])
    if page == 1090 and clause.startswith('Sequence scan FTP client connection status (SM1392) ') and _chart_tokens_only(
            clause, set('sequence scan ftp client connection status total number of files to be transferred completion '
                        'sp ftpput instruction device indication execution the file transfer completed with an error successfully'.split())):
        return reviewed([{'kind': 'FTP_send_timing_chart', 'form': 'SP.FTPPUT',
                          'connection_flag': {'device': 'SM1392', 'ON_event': 'END_after_server_connected', 'OFF_event': 'all_files_transferred'},
                          'result_update_event': 'instruction_completion',
                          'results': [{'device': _operand('s1', 1), 'role': 'completion_status'},
                                      {'device': _operand('s1', 2), 'role': 'target_file_count'},
                                      {'device': _operand('s1', 3), 'role': 'transferred_file_count'}],
                          'completion': {'device': _operand('d'), 'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END'},
                          'error_indicator': {'device': _operand('d', 1), 'normal': False, 'error': 'single_scan_completion_pulse'},
                          'scan_count': 'variable_not_fixed_by_drawn_spacing'}])
    if page in {1125, 1131, 1139, 1147, 1155, 1162}:
        forms_by_page = {1125: 'GP.READ', 1131: 'GP.SREAD', 1139: 'GP.WRITE',
                         1147: 'GP.SWRITE', 1155: 'GP.SEND', 1162: 'GP.RECV'}
        form = forms_by_page[page]
        read = full(r'The instruction reads data by the specified number of words \(\(s1\)\+9\) from the target station '
                    r'head device \(s2\) into the own station (?:head|word) device \(after \(d1\)\)\.')
        write = full(r'The instruction writes data by the specified number of words \(\(s1\)\+9\) from the own station '
                     r'head device \(s2\) into the target station word devices\.')
        if read or write:
            return reviewed([{'kind': 'link_device_data_transfer', 'form': form, 'word_count': _operand('s1', 9),
                              'source': {'head': _operand('s2'), 'station': 'target' if read else 'own'},
                              'destination': {'head': _operand('d1'), 'station': 'own' if read else 'target'}}],
                            dimension='effects.documented_transfer')
        if full(r'Specify the target stations in control data \(\(s1\)\+4\) and \(\(s1\)\+5\)\.'):
            return reviewed([{'kind': 'link_target_station_fields', 'fields': [_operand('s1', 4), _operand('s1', 5)]}],
                            dimension='parameters.target_station')
        if full(r'Upon completion of reading the device data specified by \(s2\), the completion device specified by \(d2\) turns on\.') or full(
                r'Upon completion of writing device data to target station, the completion device specified by \(d2\) turns on\.'):
            return reviewed([{'kind': 'link_transfer_completion', 'form': form, 'event': 'read_complete' if page in {1125, 1131} else 'write_complete',
                              'station': 'own', 'device': _operand('d2'), 'state': True}])
        if full(r'In target station, upon completion of sending the device data specified by \(s2\), the device '
                r'specified by \(d3\) turns on\.') or full(r'In the other station, upon completion of writing the '
                r'device data specified by \(s2\), the device specified by \(d3\) turns on\.'):
            return reviewed([{'kind': 'link_target_notification', 'form': form, 'station': 'target', 'device': _operand('d3'),
                              'event': 'target_finished_sending_data' if page == 1131 else 'target_finished_writing_data', 'state': True}])
        if page == 1155:
            if full(r'The instruction sends data by the specified number of words \(\(s1\)\+9\) from the own station '
                    r'head device \(s2\) into the specified channel of a target station\.'):
                return reviewed([{'kind': 'link_channel_send', 'source': {'station': 'own', 'head': _operand('s2')},
                                  'word_count': _operand('s1', 9), 'destination': 'specified_target_station_channel'}],
                                dimension='effects.documented_transfer')
            if full(r'The data that has been sent is stored in the channel specified by \(s2\) in the target station\.'):
                return reviewed([{'kind': 'source_conflict', 'conflict': 'SEND_prose_uses_source_data_operand_as_channel_selector',
                                  'printed_target_channel_selector': _operand('s2'), 'setting_data_role_of_s2': 'send_data_head',
                                  'control_table_channel_selector': _operand('s1', 8), 'resolution': 'not_established'}], status='conflict')
            if full(r'To read the sent data in the target station, use the GP\.RECV instruction\.'):
                return reviewed([{'kind': 'link_channel_receive_usage', 'station': 'target', 'stored_data': 'data_sent_by_GP_SEND',
                                  'read_form': 'GP.RECV'}], dimension='resources.instruction_usage')
            if full(r'Upon completion of sending data to the target station number, the completion device specified by \(d\) turns on\.'):
                return reviewed([{'kind': 'link_transfer_completion', 'form': form, 'event': 'data_sent_to_target_station',
                                  'station': 'own', 'device': _operand('d'), 'state': True}])
        if page == 1162:
            if full(r'This instruction reads data, which were sent from another station by the SEND instruction, from '
                    r'the specified channel of the own station module to the own station word device \(after \(d1\)\)\.'):
                return reviewed([{'kind': 'link_channel_receive', 'source': 'own_module_channel_containing_remote_SEND_data',
                                  'destination': {'station': 'own', 'head': _operand('d1')}}], dimension='effects.documented_transfer')
            if full(r'When the data reading from the specified channel of the own station is completed, the completion device \(d2\) turns on\.'):
                return reviewed([{'kind': 'link_transfer_completion', 'form': form, 'event': 'own_module_channel_data_read',
                                  'station': 'own', 'device': _operand('d2'), 'state': True}])
        if clause.startswith(('WRITE (s2) (d1)', 'SWRITE (s2) (d1)', 'FX5-CCLGN-MS or FX5-CCLIEF Chn')) and _chart_tokens_only(
                clause, set('write swrite fx cpu module network own station target fx cclgn ms or cclief chn ch send recv'.split())):
            return reviewed([{'kind': 'link_data_transfer_topology', 'form': form,
                              'network_module_examples': ['FX5-CCLGN-MS', 'FX5-CCLIEF'],
                              'source_station': 'own', 'destination_station': 'target',
                              'source_data': _operand('s2'),
                              **({'destination_data': _operand('d1')} if page in {1139, 1147} else
                                 {'destination_storage': 'target_module_channel_then_GP_RECV'})}],
                            dimension='resources.link_data_topology')
    link_charts = {1126: ('GP.READ', 'd2'), 1132: ('GP.SREAD', 'd2'), 1133: ('GP.SREAD', 'd2'),
                   1140: ('GP.WRITE', 'd2'), 1148: ('GP.SWRITE', 'd2'), 1156: ('GP.SEND', 'd'),
                   1157: ('GP.SEND', 'd'), 1163: ('GP.RECV', 'd2')}
    if page in link_charts:
        form, completion = link_charts[page]
        vocabulary = set('sequence scan end on start contact off cpu module the own target station completion device status '
                         'indication read write send receive data storage error code sread swrite with arrival acknowledgment '
                         'fx cclgn ms or cclief network channel n transmission notification recv execution request area when '
                         'completed normally an following figure shows timing of gp instruction e d'.split())
        if clause.startswith(('Sequence scan |', 'WRITE Sequence scan |', 'SWRITE Sequence scan |', 'END0E N D ',
                              'OFF OFF OFF ON ON OFF ', 'The following figure shows the execution timing', 'When completed normally')) and _chart_tokens_only(clause, vocabulary):
            error = 'Error code' in clause or (page in {1140, 1148, 1157} and clause.count('| ON') > 1) or page == 1163
            rules = [{'kind': 'link_instruction_completion_waveform', 'form': form,
                      'completion_station': 'own', 'completion': {'device': _operand(completion),
                         'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END', 'duration_scans': 1},
                      'outcome': {'device': _operand(completion, 1), 'normal': False, 'error': 'same_single_scan_pulse_as_completion'},
                      'scan_count': 'illustrated_spacing_does_not_fix_latency'}]
            if form in {'GP.READ', 'GP.SREAD', 'GP.WRITE', 'GP.SWRITE'}:
                read = form in {'GP.READ', 'GP.SREAD'}
                rules.append({'kind': 'link_documented_data_value_trace', 'when_completion': 'normal',
                              'value': 100 if read else 3000,
                              'source': {'device': _operand('s2'), 'station': 'target' if read else 'own'},
                              'destination': {'device': _operand('d1'), 'station': 'own' if read else 'target'},
                              'request': 'one_transfer_on_command_rising_edge', 'value_scope': 'documented_example'})
                if form in {'GP.SREAD', 'GP.SWRITE'}:
                    rules.append({'kind': 'link_target_notification_waveform', 'device': _operand('d3'),
                                  'station': 'target', 'event': 'target_transfer_completes',
                                  'duration': 'one_target_station_scan', 'precedes': 'own_station_completion'})
            if form == 'GP.SEND' and page == 1156:
                rules.append({'kind': 'SEND_RECV_arrival_acknowledgment_example', 'mode': 'with_arrival_acknowledgment',
                              'source': {'station': 'own', 'head': _operand('s2'), 'value': 3000},
                              'order': ['SEND', 'target_channel_data_available', 'target_RECV_request_ON',
                                        'storage_notification_to_sender', 'sender_completion', 'target_GP_RECV', 'receiver_completion'],
                              'sender_completion': 'does_not_wait_for_target_GP_RECV_to_finish',
                              'receiver_call_ports': [{'port': 'd1', 'role': 'target_received_data_head'},
                                                      {'port': 'd2', 'role': 'target_completion'},
                                                      {'port': 'd2_plus_1', 'role': 'target_error_pulse'}],
                              'receiver_completion': 'one_target_station_scan', 'receiver_data': 3000})
            if error:
                rules.append({'kind': 'link_error_completion_waveform', 'completion': _operand(completion),
                              'error_pulse': _operand(completion, 1),
                              'error_code_destination': _operand('s', 1) if form == 'GP.RECV' else _operand('s1', 1)})
            return reviewed(rules, content_role='worked_example', source_binding='full_local_remote_normal_error_waveforms')
    if page == 1166:
        if full(r'The connection in which no protocol is set with the parameter can be opened and used\.'):
            return reviewed([{'kind': 'socket_open_without_protocol_parameter', 'form': 'GP.SOCOPEN', 'allowed': True}])
        if full(r'In this case, specify 8000H in \(s2\)\+0 and the contents of the open processing in \(s2\)\+2 to \(s2\)\+6\.'):
            return reviewed([{'kind': 'socket_open_without_protocol_parameter', 'form': 'GP.SOCOPEN',
                              'when_connection': 'no_protocol_set_in_parameters',
                              'application_setting': {'subject': _operand('s2'), 'value': 0x8000},
                              'open_processing_settings': {'head': _operand('s2', 2), 'last': _operand('s2', 6)}}])
    if page == 1176 and full(r'Device Item \| Description \| Setting range Set by \(s2\)\+0 \| Request data length \| '
                             r'Specify the data length from the monitoring timer to the request data\. \(In units of \| '
                             r'1 to 2000 \| User bytes\) \(s2\)\+1 \| Monitoring timer \| Set the waiting time for the '
                             r'external device that received a request message to wait 0 to 65535 \| User for the response '
                             r'after it issued a processing request to the access destination\. \(Unit: Increments of 250 ms\)'):
        return reviewed([{'kind': 'SLMP_request_fields', 'fields': [
            {'device': _operand('s2'), 'role': 'request_bytes_from_monitoring_timer_through_request_data', 'minimum': 1, 'maximum': 2000, 'set_by': 'user'},
            {'device': _operand('s2', 1), 'role': 'external_device_wait_for_access_destination_response', 'unit_ms': 250,
             'minimum': 0, 'maximum': 65535, 'zero_means': 'infinite_wait', 'set_by': 'user'}]}],
                        dimension='parameters.SLMP_request_fields')
    if page == 1177:
        if full(r'When the execution conditions of the SLMPSND instructions in the same channel are satisfied in the '
                r'same sequence scan, only the SLMPSND instruction that has been executed first is enabled and the '
                r'subsequent SLMPSND instructions are not executed\.'):
            return reviewed([{'kind': 'SLMP_channel_arbitration', 'when': 'multiple_same_channel_calls_ready_in_same_scan',
                              'first_executed_call': 'enabled', 'subsequent_calls': 'not_executed'}])
        if full(r'In addition, any subsequent SLMPSND instruction of the same channel setting as the SLMPSND '
                r'instruction being executed is not executed\.'):
            return reviewed([{'kind': 'SLMP_channel_busy', 'when': 'same_channel_instruction_in_progress', 'subsequent_call': 'not_executed'}])
        if full(r'Specify the arrival monitoring time \(\(s1\)\+11\) of the control data and monitoring timer '
                r'\(\(s2\)\+1\) of the request frame so that the arrival monitoring time \[GLYPH-F0B3\] monitoring timer\.'):
            return reviewed([{'kind': 'SLMP_monitoring_time_relation', 'operator': '>=',
                              'left': {'device': _operand('s1', 11), 'role': 'arrival_monitoring_time'},
                              'right': {'device': _operand('s2', 1), 'role': 'external_access_response_monitoring_timer'},
                              'compare': 'actual_durations_after_applying_each_field_unit'}])
        if full(r'Device Operation Completion device \(d2\) This device turns on in the END processing of the scan '
                r'where the SLMPSND instruction completes, and turns off in the next END processing\.'):
            return reviewed([{'kind': 'network_completion_pulse', 'form': 'SLMPSND', 'device': _operand('d2'),
                              'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END', 'ON_duration_scans': 1}])
        if clause.startswith('When completed with an error: The device turns on in the END processing ') and _chart_tokens_only(
                clause, set('when completed with an error the device turns on in end processing of scan where '
                            'slmpsnd instruction completes and off next request message from target to destination '
                            'response source external'.split())):
            return reviewed([{'kind': 'SLMP_error_pulse', 'device': _operand('d2', 1), 'when': 'completed_with_error',
                              'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END'},
                             {'kind': 'SLMP_request_response_timing_diagram',
                              'events': [{'order': 1, 'message': 'request', 'from': 'request_source', 'to': 'external_device'},
                                         {'order': 2, 'message': 'processing_request', 'from': 'external_device', 'to': 'request_destination'},
                                         {'order': 3, 'message': 'processing_response', 'from': 'request_destination', 'to': 'external_device'},
                                         {'order': 4, 'message': 'response', 'from': 'external_device', 'to': 'request_source'}],
                              'arrival_monitor': {'device': _operand('s1', 11), 'from_event': 1, 'to_event': 4},
                              'external_access_monitor': {'device': _operand('s2', 1), 'from_event': 2, 'to_event': 3}}])
    if page == 1186:
        if full(r'Device \| Operation Completion device \(d\) \| The device is turned on by END processing for the '
                r'scan in which the G\(P\)\.UINI instruction is completed and turned off by next END processing\.'):
            return reviewed([{'kind': 'network_completion_pulse', 'form': 'G(P).UINI', 'device': _operand('d'),
                              'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END', 'ON_duration_scans': 1}])
        if full(r'Completion status indication device \| The device is turned on or off depending on the status '
                r'when the G\(P\)\.UINI instruction is completed\.') or full(r'\(d\)\+1 \| When completed normally: The device remains off\.'):
            return reviewed([{'kind': 'network_completion_outcome_state', 'form': 'G(P).UINI', 'device': _operand('d', 1),
                              'normal': False, 'error': 'single_scan_completion_pulse'}])
        if full(r'When completed abnormally: The device is turned on by END processing for the scan in which the '
                r'G\(P\)\.UINIT instruction is completed and turned off by next END processing\.'):
            return reviewed([{'kind': 'source_conflict', 'conflict': 'UINI_error_table_uses_UINIT_form_name',
                              'declared_form': 'G(P).UINI', 'printed_error_completion_form': 'G(P).UINIT',
                              'printed_device': _operand('d', 1), 'printed_ON_event': 'END_in_completion_scan',
                              'printed_OFF_event': 'next_END', 'resolution': 'not_established'}], status='conflict')
    module_charts = {1189: 'G(P).RLPASET', 1191: 'G(P).RLASET', 1192: 'G(P).RLASET',
                     1196: 'G.ABRST_axis_suffix', 1199: 'GP.PSTRT_axis_suffix', 1202: 'GP.TEACH_axis_suffix',
                     1205: 'GP.PFWRT', 1208: 'GP.PINIT'}
    if page in module_charts:
        vocabulary = set('sequence scan end on off cpu module the own station start contact completion device '
                         'status indication error code instruction execution complete g gp abrst pstrt teach pfwrt '
                         'pinit when completed with an normally scan cid'.split())
        if (clause.startswith('Sequence scan |') or clause == '1 scan') and _chart_tokens_only(clause, vocabulary):
            position = page >= 1196
            rules = [{'kind': 'module_instruction_completion_waveform', 'form': module_charts[page],
                      'completion': {'device': _operand('d'), 'ON_event': 'END_in_completion_scan', 'OFF_event': 'next_END', 'duration_scans': 1},
                      'outcome': {'device': _operand('d', 1), 'normal': False, 'error': 'same_single_scan_pulse_as_completion'},
                      'scan_count': 'not_fixed_by_illustrated_spacing'}]
            if position:
                rules.append({'kind': 'source_conflict', 'conflict': 'positioning_waveform_error_device_name_d1_differs_from_d_in_table',
                              'setting_data_and_table_error_device': _operand('d', 1), 'printed_diagram_error_port_name': 'd1_plus_1',
                              'resolution': 'not_established'})
            if 'Error code' in clause:
                rules.append({'kind': 'module_instruction_error_status',
                              'destination': _operand('s1') if page == 1189 else _operand('s', 1), 'when': 'completed_with_error'})
            return reviewed(rules, status='conflict' if position else 'source_verified')
    if page in {1220, 1222, 1234} and full(r'Each of high-order 16 bits becomes "0" after data conversion\.'):
        return reviewed([{'kind': 'standard_numeric_zero_extension', 'source': _operand('s'), 'destination': _operand('d'),
                          'destination_bits_16_to_31': 0}], languages=['st', 'fbd'], dimension='memory.numeric_conversion_layout')
    if page == 1291:
        minimum = full(r'When (-32768|-2147483648) is input while the data type of \(s\) is (INT|DINT), '
                       r'\1 is output from \(d\)\.(?: \(An operation error does not occur\. "ABS_E" outputs '
                       r'"TRUE" from output variable ENO\.\))?')
        if minimum and ((minimum[2] == 'INT') == (minimum[1] == '-32768')):
            return reviewed([{'kind': 'absolute_value_minimum_signed_input', 'source': _operand('s'), 'type': minimum[2],
                              'when_source': int(minimum[1]), 'destination': _operand('d'), 'output': int(minimum[1]),
                              'operation_error': False, 'ABS_E_ENO': True}], languages=['st', 'fbd'],
                            dimension='effects.numeric_boundary')
    if page == 1351 and full(r'These functions concatenate the character string input to the input variables '
                             r'\(s2\) to \(s28\) after the one input to \(s1\) and output the result from \(d\)\.'):
        return reviewed([{'kind': 'variadic_string_concatenation', 'first': _operand('s1'),
                          'subsequent_input_ports': ['s' + str(i) for i in range(2, 29)],
                          'order': 'increasing_input_port_number', 'destination': _operand('d')}], languages=['st', 'fbd'],
                        dimension='effects.string_concatenation')
    if page == 1353 and full(r'After the character string specified by \(s2\) is inserted to the one specified by '
                             r'\(s1\), 00H indicating an end of the character string specified by \(s2\) is ignored\.'):
        return reviewed([{'kind': 'string_insertion_terminator', 'inserted_string': _operand('s2'), 'base_string': _operand('s1'),
                          'inserted_terminator_byte': 0, 'inserted_terminator_in_result': 'ignored'}], languages=['st', 'fbd'],
                        dimension='memory.string_insertion')
    if page == 955 and full(r'Another LOGTRG instruction is executed without executing the LOGTRGR instruction '
                            r'after a LOGTRG instruction was executed once\.'):
        return reviewed([{'kind': 'logging_trigger_rearm', 'setting': _operand('s'),
                          'when': 'LOGTRG_already_executed_without_intervening_LOGTRGR', 'subsequent_LOGTRG_processing': 'none'}])
    if page == 660 and full(r'These instructions insert the character string specified by \(s1\) at the \(s2\)th '
                            r'character from the start of the character string specified by \(d\)\.'):
        return reviewed([{'kind': 'string_insertion', 'inserted_string': _operand('s1'), 'destination': _operand('d'),
                          'one_based_insertion_position': _operand('s2')}], dimension='effects.string_insertion')
    if page == 662:
        if full(r'These instructions delete \(n\) characters starting from the \(s\)th character \(deletion start position\) '
                r'from the start of the character string data specified by \(d\)\.'):
            return reviewed([{'kind': 'string_deletion', 'destination': _operand('d'),
                              'one_based_start_position': _operand('s'), 'character_count': _operand('n')}], dimension='effects.string_deletion')
        if full(r'The character string after the deleted character string is shifted by \(n\) characters, a NULL code '
                r'\(00H\) is stored in vacant devices\.'):
            return reviewed([{'kind': 'string_deletion_compaction', 'destination': _operand('d'),
                              'shift_characters': _operand('n'), 'following_string': 'towards_deleted_position',
                              'vacated_device_bytes': 0}], dimension='memory.string_deletion')
    if page == 829 and full(r'Device \| Name \| Description SM8029 \| Instruction execution \| ON: Turns ON after the '
                            r'matrix in the nth \(last\) column is input\. complete \| OFF: Remains OFF while the matrix '
                            r'in the 1st to nth \(last\) columns is being input\.'):
        return reviewed([{'kind': 'matrix_input_completion', 'device': 'SM8029', 'while': 'matrix_columns_being_input',
                          'state': False, 'ON_event': 'last_nth_column_input_completed'}])
    if page == 845 and full(r'The current value \(s2\) of the counter is compared with the data table with "n" lines '
                            r'starting from \(s1\) \(which occupies "n" lines \[GLYPH-F0B4\] 4 devices\), and consecutive '
                            r'"n" outputs starting from \(d\) are controlled to on or off during one rotation\.'):
        return reviewed([{'kind': 'absolute_drum_table_comparison', 'counter': _operand('s2'),
                          'table_head': _operand('s1'), 'line_count': _operand('n'), 'devices_per_line': 4,
                          'output_head': _operand('d'), 'output_count': _operand('n'), 'comparison_period': 'one_rotation'}])
    if page == 850 and full(r'16-bit conversion mode \(while SM8161 is OFF\) With regard to \(n\) data points '
                            r'starting from \(s\), the addition data and horizontal parity data of high-order 8 bits '
                            r'and low-order 8 bits are stored to \(d\) and \(d\)\+1 respectively\.'):
        return reviewed([{'kind': 'check_code_16_bit_storage', 'when_SM8161': False, 'source': _operand('s'),
                          'byte_count': _operand('n'), 'selected_source_bytes': 'low_then_high_byte_of_each_word',
                          'sum_destination': _operand('d'), 'horizontal_parity_destination': _operand('d', 1)}],
                        dimension='memory.check_code_result')
    if page == 892:
        if full(r'16-bit conversion mode \(while SM8161 is OFF\) In this mode, the operation is executed for '
                r'high-order 8 bits \(1 byte\) and low-order 8 bits \(1 byte\) of a device specified in \(s\)\.'):
            return reviewed([{'kind': 'CRC_source_layout', 'when_SM8161': False, 'source': _operand('s'),
                              'selected_source_bytes': 'low_then_high_byte_of_each_word'}], dimension='memory.CRC_source_layout')
        if full(r'The operation result is stored to one 16-bit device specified in \(d\)\.'):
            return reviewed([{'kind': 'CRC_result_layout', 'when_SM8161': False, 'destination': _operand('d'),
                              'result_width_bits': 16}], dimension='memory.CRC_result_layout')
        if full(r'In the following program, conversion is executed as follows:'):
            return reviewed([{'kind': 'documented_CRC_conversion_examples', 'source': _operand('s'), 'destination': _operand('d'),
                              'byte_count': 6, 'source_bytes': [1, 3, 3, 2, 0, 20], 'result_CRC': 0x41E4,
                              'cases': [{'when_SM8161': False, 'source_words': [0x0301, 0x0203, 0x1400],
                                         'result_words': [{'offset': 0, 'word': 0x41E4}]},
                                        {'when_SM8161': True, 'source_low_bytes': [1, 3, 3, 2, 0, 20],
                                         'result_low_bytes': [{'offset': 0, 'byte': 0xE4}, {'offset': 1, 'byte': 0x41}]}]}],
                            dimension='resources.documented_CRC_examples', content_role='worked_example')
    if page in {1024, 1026, 1028, 1030} and full(r'For \(s1\), specify the transfer distance that is output after an '
                                               r'interrupt, in user units\. \(The distance must be within the range of '
                                               r'-2147483648 to \+2147483647 in the number of pulses\.\)'):
        return reviewed([{'kind': 'interrupt_positioning_distance', 'distance': _operand('s1'), 'setting_unit': 'user_unit',
                          'execution_phase': 'after_interrupt', 'converted_pulse_range': {'minimum': -2147483648, 'maximum': 2147483647}}],
                        dimension='parameters.interrupt_positioning_distance')
    if page == 1034:
        if full(r'For \(n2\), specify the last table\.'):
            return reviewed([{'kind': 'positioning_table_endpoint', 'subject': _operand('n2'), 'role': 'last_table_number'}],
                            dimension='parameters.table_endpoint')
        if full(r'When \(n1\) and \(n2\) are the same, only one table is executed\.'):
            return reviewed([{'kind': 'positioning_table_single_execution', 'when': {'left': _operand('n1'), 'operator': '=', 'right': _operand('n2')},
                              'table_execution_count': 1}])
        if full(r'The table operation keeps executing until the last table or a table that is not set with parameters is executed\.'):
            return reviewed([{'kind': 'positioning_table_sequence_end', 'execute_until': ['last_table', 'table_without_parameter_setting']}])
    if page == 542:
        if full(r'The operation specifications of the file delete function are shown below\. \[In SD memory card\]') or full(
                r'Application setting area \(d1\)\+0 File name/folder name \(s2\) 0H, 1H, 3H '
                r'\[When a file is specified\] sample/sample\.bin \[When a folder is specified\] sample'):
            return reviewed([{'kind': 'file_delete_mode_table', 'selector': _operand('d1'), 'path': _operand('s2'),
                              'cases': [{'value': 0, 'target': 'specified_file'}, {'value': 1, 'target': 'folder_even_with_contents'},
                                        {'value': 3, 'target': 'empty_folder_only'}],
                              'documented_paths': [{'target': 'file', 'path_components': ['sample', 'sample.bin']},
                                                   {'target': 'folder', 'path_components': ['sample']}]}],
                            dimension='parameters.file_delete_mode')
        if full(r'"Delete the folder even when it contains data\."\)'):
            return reviewed([{'kind': 'file_delete_mode', 'selector': {'subject': _operand('d1'), 'value': 1},
                              'folder_contents': 'also_deleted'}])
    if page == 551 and full(r'The operation specifications of the file copy function are shown below\.'):
        return reviewed([{'kind': 'manual_diagram_reference', 'topic': 'file_copy_operation_specification',
                          'physical_PDF_pages': [552, 553]}], dimension='resources.instruction_usage', content_role='source_navigation')
    if page in {562, 563}:
        if page == 562 and clause.startswith('The operation specifications of the file move function are shown below. '):
            vocabulary = set('the operation specifications of file move function are shown below in sd memory card '
                             'application setting area name folder destination path when a is specified user csv'.split())
            if _chart_tokens_only(clause, vocabulary):
                return reviewed([{'kind': 'file_move_mode_table', 'selector': _operand('d1'), 'source_path': _operand('s2'),
                                  'destination_folder': _operand('s4'), 'cases': [
                                      {'value': 0, 'operation': 'move_specified_file'},
                                      {'value': 1, 'operation': 'move_entire_folder_with_structure'},
                                      {'value': 5, 'operation': 'move_folder_children_keep_source_folder_no_overwrite'},
                                      {'value': 7, 'operation': 'move_folder_children_keep_source_folder_overwrite'}]}],
                                dimension='parameters.file_move_modes')
        if page == 562 and clause.startswith('Before execution of instruction After execution of instruction '):
            match = full(r'Before execution of instruction After execution of instruction '
                         r'(The folder1/user1\.csv file is moved into folder2\.|'
                         r'The folder configuration of the source is moved into folder2\.|'
                         r'Only the file in the source folder is moved into folder2\.)')
            if match:
                mode = 0 if 'user1.csv' in match[1] else 1 if 'configuration' in match[1] else 5
                return reviewed([{'kind': 'documented_file_move_example', 'mode': mode,
                                  'source': ['folder1', 'user1.csv'] if mode == 0 else ['folder1'], 'destination_folder': ['folder2'],
                                  'result': 'file_moved_source_file_removed' if mode == 0 else
                                            'whole_folder_moved_to_folder2_folder1' if mode == 1 else 'children_moved_source_folder_kept'}],
                                content_role='worked_example')
        if page == 563:
            if full(r'The SP\.FCOPY instruction is finished normally\.'):
                return reviewed([{'kind': 'source_conflict', 'conflict': 'FMOVE_collision_paragraph_names_FCOPY',
                                  'owning_form': 'SP.FMOVE', 'printed_form': 'SP.FCOPY',
                                  'when': 'mode_5_destination_file_name_collision', 'printed_completion': 'normal',
                                  'resolution': 'not_established'}], status='conflict')
            if full(r'When there is not a file with the same name in the destination, the file is moved\.'):
                return reviewed([{'kind': 'file_move_no_overwrite', 'when_mode': 5, 'when': 'destination_file_name_absent', 'file': 'moved'}])
            if full(r'File move processing \| File access execution'):
                return reviewed([{'kind': 'waveform_signal_labels', 'signal_roles': ['file_move_processing', 'file_access_execution']}],
                                dimension='resources.section_structure', content_role='source_navigation')
            if clause.startswith(('folder1 |', '*4 folder2 |', 'Before execution of instruction |')) and _chart_tokens_only(
                    clause, set('folder user csv the file is not moved because a with same name exists in destination '
                                'before execution of instruction after overwriting'.split())):
                overwrite = 'overwriting' in clause or clause.startswith(('folder1 | user1.csv | folder1', 'Before execution'))
                rules = [{'kind': 'documented_file_move_collision_examples', 'source_folder': 'folder1', 'destination_folder': 'folder2',
                          'file_name': 'user1.csv', 'cases': [
                              {'mode': 5, 'when_destination_file_exists': True, 'source_file': 'retained', 'destination_file': 'unchanged', 'file_moved': False},
                              {'mode': 7, 'when_destination_file_exists': True, 'source_file': 'removed', 'destination_file': 'overwritten_from_source'}]}]
                if overwrite:
                    rules.append({'kind': 'source_conflict', 'conflict': 'FMOVE_mode_7_caption_reverses_diagram_direction',
                                  'diagram_source_folder': 'folder1', 'diagram_destination_folder': 'folder2',
                                  'printed_caption_source_folder': 'folder2', 'printed_caption_destination_folder': 'folder1',
                                  'resolution': 'not_established'})
                return reviewed(rules, status='conflict' if overwrite else 'source_verified', content_role='worked_example')
    if page == 571:
        if full(r'The operation specifications of the file rename function are shown below\. \[In SD memory card\] '
                r'Below is shown the timing chart \(flag updating timing\) from the execution of the SP \.FRENAME '
                r'instruction to the completion\.'):
            return reviewed(_file_wave_rules('SP.FRENAME', 'd2'), status='conflict')
        if full(r'Application setting area \(d1\)\+0 File name to be changed \(s2\) File name after the change '
                r'\(s3\) 0H sample/sample\.bin sample_change\.bin'):
            return reviewed([{'kind': 'documented_file_rename_example', 'application_setting': {'subject': _operand('d1'), 'value': 0},
                              'old_path': {'subject': _operand('s2'), 'components': ['sample', 'sample.bin']},
                              'new_name': {'subject': _operand('s3'), 'value': 'sample_change.bin'}, 'parent_folder': 'unchanged'}],
                            content_role='worked_example')
    if page == 579 and full(r'The operation specifications of the file status acquisition function are shown below\.'):
        return reviewed([{'kind': 'manual_diagram_reference', 'topic': 'file_status_acquisition_operation_specification',
                          'physical_PDF_page': 579}], dimension='resources.instruction_usage', content_role='source_navigation')
    if page in {544, 545, 572, 573, 581}:
        program_vocabulary = set('ladder program st after run on for one scan only start device where the control data '
                                 'is stored completion status creation of file operation instruction trigger drive being '
                                 'accessed execution latched rst set flag complete error sp fdelete frename fstatus '
                                 'u memory card disable request normal open indication acquisition rename delete '
                                 'sample csv bin samplechange sample_change if then h d m sm end checking that '
                                 'off ldp true false holds contact initialize en enable input always execute s dummy '
                                 'fixed specification name to be changed new bit turns upon processing releasing '
                                 'storing not with and mov application area setting up change'.split())
        is_ST = clause.startswith('[ST program]')
        is_LD = clause.startswith(('[Ladder program]', 'Execution of file rename instruction X0 |'))
        if (is_ST or is_LD) and _chart_tokens_only(clause, program_vocabulary):
            form = 'SP.FDELETE' if page in {544, 545} else 'SP.FRENAME' if page in {572, 573} else 'SP.FSTATUS'
            if is_ST:
                required_call = {'SP.FDELETE': 'SP_FDELETE(TRUE, U1, 2, "\\sample\\sample.csv", D0, M100);',
                                 'SP.FRENAME': 'SP_FRENAME(TRUE, U1, 2, "sample.bin", "sample_change.bin", D0, M100);',
                                 'SP.FSTATUS': 'SP_FSTATUS(TRUE, U1, 2, "sample.bin", D0, D100, M100);'}[form]
                if required_call not in clause or not all(code in clause for code in
                        ['IF SM402 THEN D0:=H0;', 'D1:=H0;', 'IF LDP(TRUE,X0) THEN;', 'IF (SM753 <> TRUE) THEN',
                         'SET(TRUE,M0);', 'RST(TRUE,M100);', 'RST(TRUE,M101);', 'IF M0 THEN',
                         'IF (SM606 <> TRUE) THEN;', 'IF M100 THEN', 'SET((M101 <> TRUE), M150);',
                         'SET(M101, M151);', 'RST(TRUE, M0);']):
                    return None
            if page == 572:
                return reviewed([{'kind': 'documented_file_request_latch_example', 'form': form,
                                  'when': {'all': ['X0_rising_edge', 'SM753_OFF']},
                                  'set': ['M0'], 'reset': ['M100', 'M101']}], native=True, content_role='worked_example')
            return reviewed([{'kind': 'documented_file_instruction_program', 'form': form,
                              'startup': {'when': 'SM402_ON', 'assign_zero': ['D0', 'D1']},
                              'request': {'when': {'all': ['X0_rising_edge', 'SM753_OFF']},
                                          'set': ['M0'], 'reset': ['M100', 'M101']},
                              'call': {'when': {'all': ['M0_ON', 'SM606_OFF']}, 'dummy': 'U1', 'drive': 2,
                                       'control_head': 'D0', 'completion': 'M100',
                                       **({'source_path_components': ['sample', 'sample.csv'], 'leading_path_delimiter': True} if form == 'SP.FDELETE' else
                                          {'source_name': 'sample.bin', 'new_name': 'sample_change.bin'} if form == 'SP.FRENAME' else
                                          {'source_name': 'sample.bin', 'status_destination_head': 'D100'})},
                              'complete': {'when': 'M100_ON', 'normal_M101_OFF': 'SET_M150',
                                           'error_M101_ON': 'SET_M151', 'release_request': 'RST_M0'},
                              'M150_M151': 'latched_results_no_implicit_reset_in_this_example'}],
                            languages=['st'] if is_ST else None, native=is_LD, content_role='worked_example',
                            source_binding='complete_source_program_control_flow_guards_arguments_and_result_latches')
    if page == 124 and clause.startswith('ORF instruction LDF instruction X0 M0 X1 ON ') and _chart_tokens_only(
            clause, set('orf instruction ldf on during one operation cycle in the example shown above is only when or turn from to off'.split())):
        return reviewed([{'kind': 'documented_parallel_falling_edges', 'branches': [
            {'input': 'X0', 'instruction': 'LDF'}, {'input': 'X1', 'instruction': 'ORF'}], 'connection': 'parallel',
                          'output': 'M0', 'ON_events': ['X0_ON_to_OFF', 'X1_ON_to_OFF'], 'ON_duration_operation_cycles': 1}],
                        native=True, content_role='worked_example')
    if page == 157 and full(r'X0 M0PLS X0 M0PLS instruction ON during one operation cycle'):
        return reviewed([{'kind': 'documented_rising_edge_output', 'input': 'X0', 'form': 'PLS', 'output': 'M0',
                          'ON_event': 'X0_OFF_to_ON', 'ON_duration_operation_cycles': 1}], native=True, content_role='worked_example')
    if page == 158 and full(r'Sc \| Sc Sc: 1 scan'):
        return reviewed([{'kind': 'documented_falling_edge_output', 'input': 'X5', 'form': 'PLF', 'output': 'M0',
                          'diagram_ON_event': 'X5_ON_to_OFF', 'ON_duration_scans': 1},
                         {'kind': 'source_conflict', 'conflict': 'PLF_processing_sentence_says_rising_edge_but_heading_and_waveform_say_falling',
                          'processing_sentence_edge': 'OFF_to_ON', 'heading_and_diagram_edge': 'ON_to_OFF', 'resolution': 'not_established'}],
                        native=True, content_role='worked_example', status='conflict')
    if page in {410, 423} and full(r'(?:\(d\) \| \(s\) )?(?:Before|After) transfer \| [01] \| ∙∙∙ \| 1'):
        invert = page == 410
        return reviewed([{'kind': 'documented_bit_transfer_example', 'source': _operand('s'), 'destination': _operand('d'),
                          'source_before': True, 'source_after': True, 'destination_before': invert,
                          'destination_after': not invert, 'operation': 'invert_source_bit' if invert else 'copy_source_bit'}],
                        content_role='worked_example')
    if page == 440 and full(r'CJP \(3\) \(1\): Execution command \(2\): Executed in each scan \(3\): One Scan'):
        return reviewed([{'kind': 'conditional_jump_waveform', 'cases': [
            {'form': 'CJ', 'execution': 'each_scan_while_command_ON'},
            {'form': 'CJP', 'execution': 'single_scan_at_command_OFF_to_ON'}]}], native=True)
    if page == 480 and clause.startswith(('(d) | 3 | N |', '(d)+5 |')) and _chart_tokens_only(clause, set(
            'n number of stored data dtr table range when is set in the inserted'.split())):
        return reviewed([{'kind': 'documented_data_table_insertion', 'source': _operand('s'), 'destination': _operand('d'),
                          'one_based_position': {'subject': _operand('n'), 'value': 2}, 'inserted_value': 4444,
                          'before_words': [3, 5432, 1234, -123, 0, 0], 'after_words': [4, 5432, 4444, 1234, -123, 0],
                          'head_word_role': 'stored_data_count'}], content_role='worked_example')
    if page == 523 and clause.startswith('In 16-bit binary data write mode, when the number of request write data ') and _chart_tokens_only(
            clause, set('in bit binary data write mode when the number of request and file position are specified is '
                        'written as follows h k'.split())):
        return reviewed([{'kind': 'documented_binary_file_write_example',
                          'control': {'head': _operand('d1'), 'execution_type_offset_0': 0, 'written_count_offset_2': 3,
                                      'file_word_position_offset_4_5': 1, 'columns_offset_6': 0, 'data_type_offset_7': 0},
                          'source': {'head': _operand('s3'), 'requested_count_offset_0': 3,
                                     'data_words': [{'offset': 1, 'value': 0x3322}, {'offset': 2, 'value': 0x5544}, {'offset': 3, 'value': 0x7766}]},
                          'file_byte_offsets': [{'offset': 2, 'value': 0x22}, {'offset': 3, 'value': 0x33},
                                                {'offset': 4, 'value': 0x44}, {'offset': 5, 'value': 0x55},
                                                {'offset': 6, 'value': 0x66}, {'offset': 7, 'value': 0x77}],
                          'illustrated_untouched_word_positions': [0, 4]}], content_role='worked_example')
    if page in {365, 366}:
        vocabulary = set('in the following program conversion is executed as follows source data bit mode ascii '
                         'hexadecimal code low order high hex a b c hexa remain same'.split())
        if clause.startswith(('In the following program, conversion is executed as follows:', '(s) ASCII data Hexadecimal code ')) and _chart_tokens_only(clause, vocabulary):
            strings = '0ABC12345'
            return reviewed([{'kind': 'documented_ASCII_to_HEX_examples', 'source': _operand('s'), 'destination': _operand('d'),
                              'count': _operand('n'), 'source_characters': strings, 'source_character_ASCII': [ord(c) for c in strings],
                              'source_mode': 'two_characters_per_word_low_byte_first' if page == 365 else 'one_character_per_word_low_byte_high_byte_ignored',
                              'when_SM8161': page == 366,
                              'results': [{'character_count': n, 'written_words': [
                                  {'offset': i, 'value': (int(strings[:n], 16) >> (16 * i)) & 0xFFFF} for i in range((n + 3) // 4)],
                                           'higher_destination_words': 'unchanged'} for n in range(1, 10)],
                              'illustrated_program': {'command': 'X10', 'source_head': 'D200', 'destination_head': 'D100', 'character_count': 4}}],
                            content_role='worked_example', dimension='resources.documented_HEX_conversion')
    if page in {616, 617}:
        vocabulary = set('in the following program conversion is executed as follows devices specified by and later '
                         'bit mode number of digits characters result do not change configuration when asci low order '
                         'bits set to on selected destination c b a lowest highest byte ascii code a'.split())
        if (clause.startswith(('In the following program, conversion is executed as follows:', 'Destination (n) (d) ')) or
                clause == 'The conversion processing is executed as follows.') and _chart_tokens_only(clause, vocabulary):
            source_number = 0x5678_1234_0ABC
            results = []
            for count in range(1, 10):
                characters = f'{source_number & ((1 << (4 * count)) - 1):0{count}X}'
                results.append({'character_count': count, 'output_characters': characters,
                                'output_ASCII': [ord(character) for character in characters],
                                'packing': 'two_characters_per_word_low_byte_first' if page == 616 else 'one_character_per_word_low_byte',
                                'unwritten_destination_bytes': 'unchanged',
                                **({'written_word_high_byte': 0} if page == 617 else {})})
            rules = [{'kind': 'documented_HEX_to_ASCII_examples', 'source': _operand('s'), 'destination': _operand('d'),
                      'count': _operand('n'), 'when_SM8161': page == 617, 'source_words': [0x0ABC, 0x1234, 0x5678], 'results': results}]
            if page == 617:
                rules.append({'kind': 'source_conflict', 'conflict': 'ASCI_C_character_caption_34H_disagrees_with_bits_and_ASCII_table_43H',
                              'printed_D201_caption_ASCII': 0x34, 'diagram_D201_bits': 0x43, 'ASCII_C_table': 0x43, 'resolution': 'not_established'})
            return reviewed(rules, status='conflict' if page == 617 else 'source_verified',
                            content_role='worked_example', dimension='resources.documented_ASCII_conversion')
    if page == 601 and clause.startswith('b15 | b8 b7 | b0 | b15 |') and _chart_tokens_only(clause, set(
            'b a c d e the high order byte is not transferred it same as character string before transfer automatically stored in'.split())):
        return reviewed([{'kind': 'documented_string_move_NULL_example', 'source': _operand('s'), 'destination': _operand('d'),
                          'source_words': [0x4241, 0x4443, 0x4500], 'destination_words': [0x4241, 0x4443, 0],
                          'source_word_2_high_byte': 'not_transferred_after_low_byte_NULL',
                          'destination_final_high_byte': 'automatically_zeroed'}], content_role='worked_example')
    if page == 620 and clause.startswith('(d)+4 (d)+3 (d)+2 (d)+1 (d) ') and _chart_tokens_only(clause, set(
            'ascii code for sign bin in value specifying number of all digits th digit'.split())):
        return reviewed([{'kind': 'binary16_formatted_string_layout', 'value_source': _operand('s2'),
                          'settings': {'total_digits': _operand('s1'), 'fractional_digits': _operand('s1', 1)},
                          'destination': _operand('d'), 'low_byte_offset_0': 'ASCII_sign',
                          'subsequent_bytes': 'ASCII_digits_in_display_order_low_byte_first',
                          'automatic_terminator': 0, 'maximum_illustrated_digits': 8,
                          'illustrated_terminator_word_offset': 4}], dimension='memory.numeric_string_layout')
    if page == 653 and clause.startswith(('41H (A)42H (B)', 'Position from the left end in the character string ')) and _chart_tokens_only(
            clause, set('a b c d e f g h i before execution after position from the left end in character string '
                        'stored and later number of characters to are not abcdefghi abcd'.split())):
        return reviewed([{'kind': 'documented_string_replacement_examples', 'source': _operand('s1'), 'destination': _operand('d'),
                          'settings': {'one_based_start_position': _operand('s2'), 'character_count': _operand('s2', 1)},
                          'source_words': _ASCII_words('012345678'), 'destination_before_words': _ASCII_words('ABCDEFGHI'),
                          'cases': [{'start_position': 3, 'requested_count': 6, 'after_words': _ASCII_words('AB012345I')},
                                    {'start_position': 5, 'requested_count': 8, 'after_words': _ASCII_words('ABCD01234'),
                                     'not_stored_source_ASCII': [0x35, 0x36, 0x37]}]}], content_role='worked_example')
    if page == 660 and clause.startswith(('b15 | b8 b7 | b0 (s1)', '(d)+4 |')) and _chart_tokens_only(clause, set(
            'b a c d e f g h the character string data of rd and later is inserted shifted leftward by characters '
            'insertion start position overwritten for number to be before'.split())):
        return reviewed([{'kind': 'documented_string_insertion_example', 'inserted_source': _operand('s1'), 'destination': _operand('d'),
                          'position': {'subject': _operand('s2'), 'one_based_value': 3},
                          'source_words': _ASCII_words('01234'), 'destination_before_words': _ASCII_words('ABCDEFGH'),
                          'destination_after_words': _ASCII_words('AB01234CDEFGH'),
                          'previous_memory_after_NULL': [{'offset': 5, 'word': 0x6261}, {'offset': 6, 'word': 0x6463}, {'offset': 7, 'word': 0x6665}],
                          'overwritten_previous_unused_offsets': [5, 6], 'unchanged_offset_7': 0x6665}], content_role='worked_example')
    if page == 662 and clause.startswith(('The character string data of the deleted character |', 'b15 | b8 b7 | b0 |')) and _chart_tokens_only(
            clause, set('the character string data of deleted after shifting is stored and later shifted rightward by '
                        'characters in vacant device b a c d e f g h i j k l number to be n deletion start position '
                        's rd does not change'.split())):
        return reviewed([{'kind': 'documented_string_deletion_example', 'destination': _operand('d'),
                          'position': {'subject': _operand('s'), 'one_based_value': 3}, 'count': {'subject': _operand('n'), 'value': 5},
                          'before_words': _ASCII_words('ABCDEFGHIJKL'), 'after_words': _ASCII_words('ABHIJKL'),
                          'vacated_word_offsets_cleared': [4, 5, 6],
                          'unchanged_later_words': [{'offset': 7, 'word': 0x3130}, {'offset': 8, 'word': 0x3332}, {'offset': 9, 'word': 0x3534}]}],
                        content_role='worked_example')
    if page == 781 and full(r'2-phase switch \(d\) X0 A phase M0 \| 8 Up-counting signal during forward rotation '
                            r'\(d\)\+1 X1 M1 \| B phase \(d\)\+2 X2 M2 \| Zero point detection switch'):
        return reviewed([{'kind': 'documented_rotary_input_device_mapping', 'head': _operand('d'), 'inputs': [
            {'offset': 0, 'role': 'A_phase_forward_up_count_signal', 'illustrated_input': 'X0', 'illustrated_internal': 'M0'},
            {'offset': 1, 'role': 'B_phase', 'illustrated_input': 'X1', 'illustrated_internal': 'M1'},
            {'offset': 2, 'role': 'zero_point_detection', 'illustrated_input': 'X2', 'illustrated_internal': 'M2'}]}],
                        content_role='worked_example')
    if page in {798, 802, 806, 810} and clause.startswith('The following tables show the special relays and special registers related ') and _chart_tokens_only(
            clause, set('the following tables show special relays and registers related to plsy dplsy instruction axis '
                        'number name descriptions positioning activation on during off not activated pulse output '
                        'monitor stop error occurrence occurred command is deceleration forward limit reverse'.split())):
        fields = [(5500, 'positioning_instruction_active', 'during_activation', 'not_activated'),
                  (5516, 'pulse_output_monitor', 'during_output', 'stopped'),
                  (5532, 'positioning_error', 'error_occurred', 'no_error'),
                  (5628, 'pulse_output_stop_command', 'stop_command_ON', 'stop_command_OFF'),
                  (5644, 'pulse_deceleration_stop_command', 'deceleration_stop_command_ON', 'deceleration_stop_command_OFF'),
                  (5660, 'forward_limit', 'forward_limit_ON', 'forward_limit_OFF'),
                  (5676, 'reverse_limit', 'reverse_limit_ON', 'reverse_limit_OFF')]
        return reviewed([{'kind': 'positioning_relay_status_table', 'form': 'PLSY' if page in {798, 802} else 'DPLSY',
                          'axes': [{'axis': i + 1, 'fields': [
                              {'device': 'SM' + str(base + i), 'role': role, 'ON': on, 'OFF': off,
                               **({'source_footnote': 1} if base == 5644 else {})} for base, role, on, off in fields]} for i in range(4)]}],
                        dimension='memory.positioning_relay_status')
    if page == 850 and clause.startswith('In the following program, conversion is executed as follows: (s) D100 K100 ') and _chart_tokens_only(
            clause, set('in the following program conversion is executed as follows s k ccd'.split())):
        return reviewed([{'kind': 'documented_check_code_example', 'source': _operand('s'), 'destination': _operand('d'), 'when_SM8161': False,
                          'input_bytes': [100, 111, 100, 98, 123, 66, 100, 95, 210, 88], 'byte_count': 10,
                          'printed_sum_decimal': 1091, 'sum_result_diagram_bits': 0x0443, 'parity_result': 0x85},
                         {'kind': 'source_conflict', 'conflict': 'CCD_sum_caption_says_BCD_but_bits_show_binary_1091',
                          'caption_encoding': 'BCD', 'illustrated_word': 0x0443, 'BCD_1091_word': 0x1091, 'resolution': 'not_established'}],
                        status='conflict', content_role='worked_example')
    return None

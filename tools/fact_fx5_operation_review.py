"""Authored FX5 instruction effects bound to the instruction's own section.

This is build-time manual knowledge, never an executable instruction registry.
Symbols, operands and numeric conditions are retained as technical fields. The
caller supplies source, CPU, revision and availability dependencies.
"""
import re


def section_conditions(section):
    """Keep overload selection visible wherever the owning section is used."""
    title = section['section_token']
    kinds = {
        'out_excluding_the_timer_counter_and_annunciator': ['ordinary_bit_excluding_F_T_ST_C_LC'],
        'timer': ['T', 'ST'], 'counter': ['C'], 'long_counter': ['LC'],
        'annunciator': ['F'], 'setting_annunciator': ['F'], 'resetting_annunciator': ['F'],
        'setting_devices_excluding_annunciator': ['bit_excluding_F'],
        'resetting_devices_excluding_annunciator': ['bit_word_timer_counter_excluding_F'],
    }
    conditions = ([{'operand': 'd', 'device_kind': kinds[title]}] if title in kinds else [])
    if 'SFC' in section['outline']:
        conditions.append({'context': 'program_context', 'equals': 'sfc'})
    if 'STEP LADDER' in section['outline']:
        conditions.append({'context': 'program_context', 'equals': 'step_ladder'})
    return conditions


def variant_operation_specs(section, record):
    """Two- and three-operand declarations carry distinct dataflow."""
    from tools.fact_fx5_ladder_signatures import _printed_forms
    from knowledge.instruction_document import instruction_declaration_names
    title = section['section_token']
    arithmetic = {'adding': 'add', 'subtracting': 'subtract', 'multiplying': 'multiply', 'dividing': 'divide'}
    operation = arithmetic.get(title.split('_', 1)[0])
    logical = re.search(r'performing_an?_(and|or|xor|xnor)_operation', title)
    concat = title == 'concatenating_character_strings'
    if not (operation or logical or concat):
        return []
    width = re.search(r'(16|32|64)_bit', title)
    result = []
    for page, layout in section.get('page_layouts', ()):
        if 'Ladder diagram' not in layout:
            continue
        declared = instruction_declaration_names(layout, section['outline'], 'programming', source_text=section['text'])
        forms = _printed_forms(layout, declared)
        if forms and record['opcode'] not in forms:
            continue
        intro = re.split(r'Ladder diagram|Setting data', layout, maxsplit=1)[0]
        symbols = set(re.findall(r'\((s[12]?|d)\)', intro))
        # WSUM/DWSUM have s,d,n, not the two-operand destination accumulator.
        two = symbols == {'s', 'd'} and not re.search(r'\(n[12]?\)', intro)
        if not two and not {'s1', 's2', 'd'} <= symbols:
            continue
        value = {'knowledge_kind': 'manual_behavior', 'operation': operation or logical[1] if not concat else 'concatenate',
                 'inputs': ['d', 's'] if two else ['s1', 's2'], 'output': 'd',
                 'reads_destination_before_write': two,
                 **({'width_bits': int(width[1])} if width else {})}
        if operation:
            encoding = 'bcd' if '_bcd_' in title else 'ieee754' if '_real_number' in title else 'binary'
            value.update(encoding=encoding, block_operation='_block_' in title)
            if encoding == 'binary':
                value['signed'] = not record['opcode'].endswith('_U')
        elif logical:
            value.update(granularity='bit', block_operation='_block_' in title)
        else:
            value.update(terminator='00H', concatenation_order=value['inputs'])
        count = 2 if two else 4 if '_block_' in title else 3
        result.append({'key': 'variant_operation_p' + str(page), 'dimension': 'effects.dataflow',
                       'value': value, 'page': page,
                       'conditions': [{'native_operand_count': count}],
                       **({'languages': ['ladder']} if two else {})})
    return result


def operation_specs(section, record):
    if not section['outline'].startswith(('PART 3 ', 'PART 4 ')):
        return []
    title = section['section_token']
    raw = '\n'.join(text for _, text in section.get('page_layouts', ()))
    body = re.sub(r'\s+', ' ', raw)
    # Worked examples are not primary definitions of this instruction.
    intro = re.split(r'Ladder diagram|Setting data|\n\s*FBD/LD|Program example', raw)[0]
    intro = re.sub(r'\s+', ' ', intro)
    opcode = record['opcode']
    base = opcode.removesuffix('_U')
    if base.endswith('P') and base[:-1] in record['forms']:
        base = base[:-1]
    width_match = re.search(r'(16|32|64)_bit', title)
    width = {'width_bits': int(width_match[1])} if width_match else {}
    specs = []

    def add(key, value, *, dimension=None, languages=None, conditions=None, status=None):
        specs.append({'key': key, 'dimension': dimension or 'effects.' + key,
                      'value': {'knowledge_kind': 'manual_behavior', **value},
                      **({'languages': languages} if languages else {}),
                      **({'conditions': conditions} if conditions else {}),
                      **({'status': status} if status else {})})

    if title in {'operation_start_series_connection_parallel_connection',
                 'pulse_operation_start_pulse_series_connection_pulse_parallel_connection',
                 'pulse_not_operation_start_pulse_not_series_connection_pulse_not_parallel_connection'}:
        combine = 'replace' if opcode.startswith('LD') else 'and' if opcode.startswith('AND') or opcode == 'ANI' else 'or'
        edge = ('rising' if 'P' in opcode else 'falling' if 'F' in opcode else None)
        if edge and ('rising edge' in body or 'falling edge' in body):
            add('contact', {'operand': 's', 'test': edge + '_edge',
                            'negate_test': opcode.endswith('I'), 'combine': combine,
                            'combines_with': 'preceding_ladder_operation_result'}, languages=['ladder'])
            # The chapter describes transitions, not an implementation of the
            # history cell or its first/skipped execution lifecycle.
            add('edge_history', {'trigger_object': 'operand_s', 'state_owner': 'not_established',
                                 'update_timing': 'not_established',
                                 'comparison_basis': 'not_established'},
                dimension='execution.edge_history', languages=['ladder'], status='unknown')
        elif 'ON/OFF information' in body:
            add('contact', {'operand': 's', 'test': 'off' if opcode in {'LDI', 'ANI', 'ORI'} else 'on',
                            'combine': combine, 'combines_with': 'preceding_ladder_operation_result'},
                languages=['ladder'])
    if title == 'ladder_block_series_parallel_connection' and 'A and B blocks' in body:
        add('logic_blocks', {'operation': 'and' if opcode == 'ANB' else 'or',
                             'operands': ['ladder_block_A', 'ladder_block_B'],
                             'result': 'operation_result'}, languages=['ladder'])
    if title == 'storing_reading_clearing_the_operation_result' and 'stored by the MPS instruction' in body:
        add('logic_stack', {'operation': {'MPS': 'push', 'MRD': 'read_top', 'MPP': 'pop'}[opcode],
                            'data': 'preceding_operation_result', 'maximum_depth': 16}, languages=['ladder'])
    if title == 'inverting_the_operation_result' and 'inverts the operation result' in body:
        add('logic_result', {'operation': 'not', 'operand': 'preceding_operation_result'}, languages=['ladder'])
    if title == 'converting_the_operation_result_into_a_pulse' and 'edge of the operation result' in body:
        add('logic_edge', {'edge': 'rising' if opcode == 'MEP' else 'falling',
                           'trigger_object': 'preceding_operation_result', 'otherwise': False}, languages=['ladder'])
        if 'indexed contact' in body and 'FOR to NEXT' in body:
            add('indexed_contact_caution', {'indexed_contact': True,
                'contexts': ['subroutine', 'for_next'], 'operation': 'may_not_function_properly'},
                dimension='constraints.source_qualifications', languages=['ladder'])
    if title == 'out_excluding_the_timer_counter_and_annunciator' and 'outputs the operation result' in body:
        add('coil', {'operation': 'assign', 'target': 'd', 'value': 'preceding_operation_result'}, languages=['ladder'])
    if title in {'setting_devices_excluding_annunciator', 'resetting_devices_excluding_annunciator'}:
        setting = opcode == 'SET'
        if 'device status does not change' in body:
            add('device_state', {'operation': 'set' if setting else 'reset', 'target': 'd',
                'bit_value': setting, 'when_disabled': 'unchanged',
                **({} if setting else {'word_value': 0, 'timer_counter': {'current_value': 0, 'contact': False}})})
        if 'nearer the END' in body:
            add('same_scan', {'target_class': 'Y', 'writer_instructions': ['SET', 'RST'],
                              'winning_write': 'later_in_program_order'}, languages=['ladder'])
        if 'held in a reset state' in body:
            add('jump', {'target_class': ['timer', 'counter'],
                         'contexts': ['jump', 'subroutine', 'interrupt'], 'reset_state': 'may_remain_held'},
                dimension='execution.jump', languages=['ladder'])
    if title in {'rising_edge_output', 'falling_edge_output'} and 'for one scan' in body:
        edge = 'rising' if opcode == 'PLS' else 'falling'
        add('pulse_output', {'target': 'd', 'edge': edge, 'trigger_object': 'execution_command',
                             'normal_on_duration_scans': 1, 'otherwise': False}, languages=['ladder'])
        if 'one scan or more' in body:
            add('jump', {'contexts': ['CJ_skips_instruction', 'CALL_does_not_call_subroutine'],
                         'target': 'd', 'on_duration': 'can_exceed_one_scan'},
                dimension='execution.jump', languages=['ladder'])
        if 'RUN to STOP' in body and 'RUN again' in body:
            add('mode_change', {'sequence': ['executed', 'RUN_to_STOP', 'STOP_to_RUN'],
                                'restart_alone_retriggers': False},
                dimension='execution.mode_change', languages=['ladder'])
        if 'write during RUN' in body and 'again' in body:
            add('online_change', {'after_write_during_RUN': 'suppress_initial_pulse',
                                  'resume_on': edge + '_edge'}, dimension='execution.online_change', languages=['ladder'])
    if title == 'inverting_the_bit_device_output' and 'reverses the output status' in intro:
        add('toggle', {'target': 'd', 'operation': 'logical_not',
                        'trigger': 'rising_command' if opcode == 'FF' else 'instruction_execution'})
    if title == 'shifting_bit_devices' and 'before the device specified by (d)' in intro:
        add('shift', {'source': 'd_minus_1', 'target': 'd', 'unit': 'bit'})
    shift = re.search(r'shift(?:s)? \(n1\) (bits|words) of data to the (right|left) by \(n2\)', intro)
    if shift:
        add('shift', {'start': 'd', 'region_length': 'n1', 'distance': 'n2',
                       'unit': shift[1].removesuffix('s'), 'direction': shift[2]})
    if title.startswith('comparing_') and 'comparison operation between' in intro:
        relation = re.search(r'(<=|>=|<>|=|<|>)', opcode)
        if relation:
            add('comparison', {'relation': relation[1], 'operands': ['s1', 's2'], **width,
                'encoding': 'string' if 'character_strings' in title else 'real32' if 'real_numbers' in title else 'binary',
                'signed': not opcode.endswith('_U'),
                'combine': 'and' if opcode.startswith('AND') else 'or' if opcode.startswith('OR') else 'replace',
                **({'count': 'n', 'result': 'd'} if 'block_data' in title else {'result': 'contact'})})
    if title.startswith(('comparison_output_', 'single_precision_real_number_comparison')) and 'comparison' in body:
        add('compare_outputs', {'operands': ['s1', 's2'], 'result_start': 'd',
                                'result_bit_count': 3, 'relations': ['greater', 'equal', 'less'], **width})
    if ('data_band' in title or 'data_band_comparison' in title) and 'comparison' in body:
        add('zone_compare', {'lower': 's1', 'upper': 's2', 'value': 's3',
                             'result_start': 'd', 'result_bit_count': 3, **width})
    if title in {'setting_a_bit_in_the_word_device', 'resetting_a_bit_in_the_word_device'}:
        if '(n)th bit' in intro:
            add('bit_write', {'target': 'd', 'index': 'n', 'value': title.startswith('setting')})
    if title.startswith('performing_a_') and 'bit data at position specified by (s2)' in intro:
        add('bit_read', {'source': 's1', 'index': 's2', 'target': 'd', **width})
    if title.startswith(('two_s_complement_', 'inverting_the_sign_')) and 'invert the sign' in intro:
        add('negation', {'source': 'd', 'target': 'd', 'operation': 'negate', **width})
    if title.startswith('exchanging_'):
        if 'exchange' in intro and '(d1) and (d2)' in intro:
            add('exchange', {'operands': ['d1', 'd2'], **width})
        elif 'swap the value of 8 bits' in intro:
            add('byte_swap', {'target': 'd', 'operation': 'swap_bytes_within_each_word', **width})
    if title == 'transferring_1_bit_data' and 'store bit data' in intro:
        add('bit_copy', {'source': 's', 'target': 'd'})
    if title.startswith('rotating_') and 'rotate the' in intro:
        add('rotation', {'target': 'd', 'distance': 'n', **width,
                          'direction': 'right' if '_right' in title else 'left',
                          'includes_carry': bool(re.match(r'D?RC[LR]', opcode))})
    conversion = re.search(r'converting_(.+?)_to_(.+)', title)
    if conversion and 'convert' in intro:
        types = {'16_bit_binary_data': 'binary16', '32_bit_binary_data': 'binary32', 'gray_code': 'gray_code',
            'decimal_ascii': 'decimal_ascii', 'ascii': 'ascii', 'hex': 'hexadecimal', 'hex_code_data': 'hexadecimal',
            'character_string': 'string', 'single_precision_real_number': 'real32',
            'binary_floating_point': 'real32', 'decimal_floating_point': 'decimal_real',
            'unicode_character_string': 'unicode', 'shift_jis_character_string': 'shift_jis',
            'unicode_character_string_without_byte_order_mark': 'unicode_without_bom',
            'unicode_with_byte_order_mark': 'unicode_with_bom'}
        before, after = types.get(conversion[1]), types.get(conversion[2])
        if before and after:
            add('data_conversion', {'from': before, 'to': after,
                'source': 's2' if title.startswith(('converting_16_bit_binary_data_to_character_string',
                                                   'converting_32_bit_binary_data_to_character_string')) else
                          's1' if title.startswith('converting_single_precision_real_number_to_character_string') else 's',
                'target': 'd2' if title.startswith(('converting_character_string_to_16', 'converting_character_string_to_32')) else 'd',
                **({'digit_count_target': 'd1'} if title.startswith(('converting_character_string_to_16', 'converting_character_string_to_32')) else {})})
    math_names = {'sine': 'sin', 'cosine': 'cos', 'tangent': 'tan', 'arc_sine': 'asin',
                  'arc_cosine': 'acos', 'arc_tangent': 'atan', 'square_root': 'sqrt',
                  'exponent': 'exp', 'natural_logarithm': 'ln', 'common_logarithm': 'log10'}
    for phrase, operation in math_names.items():
        if title.startswith('calculating_the_' + phrase + '_of_') and 'calculate' in intro:
            add('math', {'operation': operation, 'source': 's1' if 'specified by (s1)' in intro else 's',
                         'target': 'd', **width,
                         **({'encoding': 'real32'} if 'single_precision' in title else {})})
    if title == 'calculating_the_exponentiation_of_single_precision_real_number' and 'raise float' in intro:
        add('math', {'operation': 'power', 'base': 's1', 'exponent': 's2', 'target': 'd', 'encoding': 'real32'})
    if title in {'converting_single_precision_real_number_angle_to_radian',
                 'converting_single_precision_real_number_radian_to_angle'} and 'convert a unit of angle' in intro:
        to_radian = title.endswith('to_radian')
        add('angle_conversion', {'source': 's', 'target': 'd',
             'input_unit': 'degree' if to_radian else 'radian', 'output_unit': 'radian' if to_radian else 'degree'})
    if title.startswith(('searching_the_maximum_value_', 'searching_the_minimum_value_')) and 'search' in intro:
        add('extremum', {'operation': 'max' if 'maximum' in title else 'min', 'source_start': 's',
                         'count': 'n', 'target': 'd', **width,
                         **({'encoding': 'real32'} if 'single_precision' in title else {})})
    if title.startswith('calculating_the_mean_value_') and 'calculate the mean value' in intro:
        add('aggregate', {'operation': 'mean', 'source_start': 's', 'count': 'n', 'target': 'd', **width})
    if title.startswith('adding_') and 'add the (n) point(s)' in intro:
        add('aggregate', {'operation': 'sum', 'source_start': 's', 'count': 'n', 'target': 'd',
                          'source_width_bits': int(width_match[1]),
                          'result_width_bits': int(width_match[1]) * 2,
                          'signed': not opcode.endswith('_U'),
                          'reads_destination_before_write': False})
    if title.startswith('sorting_') and 'sort data lines' in intro:
        add('table_sort', {'source_start': 's', 'target_start': 'd', 'rows': 'n1', 'columns': 'n2',
                           'key_column': 'n3', 'order': 'selectable' if 'ascending order or descending order' in intro else 'ascending', **width})
    if title == 'pointer_branch' and 'pointer number within the same program file' in intro:
        add('program_flow', {'operation': 'jump', 'target': 'P', 'target_scope': 'current_program_file'}, languages=['ladder'])
    if title == 'jump_to_end' and 'FEND or END' in intro:
        add('program_flow', {'operation': 'jump_to_FEND_or_END', 'target_scope': 'current_program_file'}, languages=['ladder'])
    if title == 'calling_a_subroutine_program' and 'subroutine program specified by (P)' in intro:
        add('program_flow', {'operation': 'call_subroutine', 'target': 'P'}, languages=['ladder'])
    if title == 'returning_from_the_subroutine_program' and 'end of a subroutine program' in intro:
        add('program_flow', {'operation': 'return_from_subroutine'}, languages=['ladder'])
    if title == 'returning_from_the_interrupt_program' and 'end of the processing of an interrupt program' in intro:
        add('program_flow', {'operation': 'return_from_interrupt'}, languages=['ladder'])
    if title == 'forcibly_terminating_the_for_to_next_instruction_loop' and 'forcibly terminates' in intro:
        add('program_flow', {'operation': 'break_for_next', 'target': 'P'}, languages=['ladder', 'fbd'])
    if title == 'resetting_the_watchdog_timer' and 'reset the watchdog timer' in intro:
        add('watchdog', {'operation': 'reset'})
    if title == 'stopping_the_sequence_program' and 'resets outputs (Y)' in intro:
        add('cpu_control', {'operation': 'stop', 'output_Y': False})
    if title == 'ending_the_sequence_program' and 'end of a program' in intro:
        add('program_flow', {'operation': 'end_current_program'}, languages=['ladder'])
    if title == 'disabling_enabling_interrupt_programs' and 'enable interrupts' in intro:
        add('interrupt_control', {'operation': 'enable' if opcode == 'EI' else 'disable'})
    if title == 'disabling_the_interrupt_program_with_specified_priority_or_lower' and 'until the EI instruction' in intro:
        add('interrupt_control', {'operation': 'disable', 'priority_at_or_below': 's', 'restore_instruction': 'EI'})
    if title == 'interrupt_program_mask' and '16-point bit pattern' in intro:
        add('interrupt_control', {'operation': 'mask', 'pattern_start': 's', 'pattern_bits': 16})
    if title == 'disabling_enabling_the_specified_interrupt_pointer' and 'interrupt pointer number' in intro:
        add('interrupt_control', {'operation': 'set_mask', 'pointer': 'I', 'value': 's'})
    if title == 'reading_the_oldest_data_from_the_data_table' and 'first-in first-out' in intro:
        add('queue', {'operation': 'read_oldest', 'order': 'FIFO'})
    if title == 'reading_the_newest_data_from_the_data_table' and 'latest data written' in intro:
        add('queue', {'operation': 'read_newest', 'order': 'LIFO', 'writer': 'SFWR'})
    if title == 'writing_data_to_the_data_table' and 'write data for first-in first-out' in intro:
        add('queue', {'operation': 'append', 'reader_modes': ['FIFO', 'LIFO']})
    if title == 'inserting_data_to_the_data_table' and 'moved down by one data point' in intro:
        add('table_insert', {'value': 's', 'table': 'd', 'position': 'n', 'later_elements': 'shift_down_one'})
    if title == 'deleting_data_from_the_data_table' and 'moved up by one data point' in intro:
        add('table_delete', {'table': 'd', 'position': 'n', 'removed_value_target': 's', 'later_elements': 'shift_up_one'})
    if title == 'timer' and 'timer counts up to the set value' in intro:
        add('timer', {'target': 'd', 'preset': 'set_value', 'done_comparison': 'current_greater_or_equal_preset',
                      'when_disabled': {'T': 'clear_value_and_contact', 'ST': 'hold_value_and_contact'},
                      'retentive_reset_instruction': 'RST'}, languages=['ladder'])
    if title in {'counter', 'long_counter'} and 'increments the current value' in intro:
        add('counter', {'target': 'd', 'preset': 'set_value', 'increment': 1,
                        'count_event': 'execution_command_rising_edge', 'while_command_on': 'no_additional_count',
                        'after_count_up': 'hold_value_and_contact_until_reset', 'preset_zero_effective_value': 1,
                        'device_kind': 'C' if title == 'counter' else 'LC'})
    if title in {'annunciator', 'setting_annunciator', 'resetting_annunciator'}:
        if 'annunciator' in intro:
            add('annunciator', {'target': 'd', 'operation': 'assign_command' if opcode == 'OUT' else
                                'set' if opcode == 'SET' else 'reset',
                                'number_table': 'SD64..SD79', 'count_register': 'SD63',
                                'table_capacity': 16,
                                'remove_table_entry': opcode == 'RST'})
    if title == 'setting_annunciator_with_check_time' and 'remains ON continuously' in body:
        add('annunciator_delay', {'target': 'd', 'timer': 's', 'delay_units': 'n', 'unit_ms': 100,
                                  'trigger': 'command_continuously_on_for_delay', 'when_disabled': 'reset_evaluation_timer'})
    if title == 'resetting_annunciator_smallest_number_reset' and 'lowest number annunciator' in intro:
        add('annunciator_reset', {'selection': 'lowest_number_active_F', 'number_per_execution': 1})
    if title == 'setting_resetting_the_master_control' and 'starts master control' in intro:
        add('master_control', {'operation': 'start' if opcode == 'MC' else 'end', 'nesting': 'N',
                               'nesting_minimum': 0, 'nesting_maximum': 14,
                               'unconditional_instructions': 'still_execute_when_MC_is_off'}, languages=['ladder'])
    if title == 'ending_the_main_routine_program' and 'refreshing of the watchdog timer' in body:
        add('program_flow', {'operation': 'end_main_routine',
                             'then': ['output_processing', 'input_processing', 'watchdog_refresh', 'return_to_step_0']},
            languages=['ladder'])
    if title == 'for_to_next' and 'executed (n) times without any condition' in intro:
        add('loop', {'operation': 'start' if opcode == 'FOR' else 'repeat_or_exit', 'repetitions': 'n',
                      'maximum_nesting': 16, 'non_positive_n_effective_value': 1,
                      'skip_method': 'CJ_jump'}, languages=['ladder', 'fbd'])
    if title == 'decoding_from_8_to_256_bits' and 'lower-order (n) bits' in intro:
        add('decode', {'source': 's', 'source_bit_count': 'n', 'target_start': 'd', 'target_count': '2_power_n'})
    if title == 'encoding_from_256_to_8_bits' and 'encode the 2(n) bits' in intro:
        add('encode', {'source_start': 's', 'source_count': '2_power_n', 'target': 'd'})
    if title == 'seven_segment_decoding' and 'seven-segment display unit (1 digit)' in intro:
        add('display', {'operation': 'seven_segment_decode', 'digits': 1})
    if title == 'seven_segment_with_latch' and '4-digit seven-segment display' in intro:
        add('display', {'operation': 'latched_seven_segment_scan', 'groups': [1, 2], 'digits_per_group': 4})
    if title == '7_6_digital_switch' and 'set value of digital switches' in intro:
        add('digital_switch', {'operation': 'read', 'groups_by_n': {'1': 1, '2': 2}, 'digits_per_group': 4})
    bit_packing = {
        'separating_4_bits_from_16_bit_data': ('nibbles', 'split', 4),
        'connecting_4_bits_to_16_bit_data': ('lower-order 4 bits', 'join', 4),
        'separating_the_specified_number_of_bits': ('bit units specified by (s2)', 'split', 's2'),
        'connecting_the_specified_number_of_bits': ('bit units specified by (s2)', 'join', 's2'),
        'separating_data_in_byte_units': ('byte units', 'split', 8),
        'connecting_data_in_byte_units': ('lower-order 8 bits', 'join', 8),
    }
    if title in bit_packing and bit_packing[title][0] in intro:
        _, operation, bits = bit_packing[title]
        add('bit_packing', {'operation': operation, 'group_bits': bits,
                            'source': 's1' if 'specified_number' in title else 's', 'target': 'd',
                            **({} if 'specified_number' in title else {'count': 'n'})})
    if title == 'digit_move' and 'nibble (4 bits)' in intro:
        add('digit_transfer', {'unit_bits': 4, 'operation': 'distribute_and_compose'})
    file_ops = {'SP.FREAD': ('read', 'reads device data'), 'SP.FWRITE': ('write', 'writes device data'),
                'SP.FDELETE': ('delete', 'deletes the specified file'), 'SP.FCOPY': ('copy', 'copies the specified file'),
                'SP.FMOVE': ('move', 'moves the specified file'), 'SP.FRENAME': ('rename', 'renames the specified file'),
                'SP.FSTATUS': ('status', 'acquires the status')}
    if opcode in file_ops and file_ops[opcode][1] in intro:
        add('file_operation', {'operation': file_ops[opcode][0], 'storage': 'SD_memory_card',
                               'supports_folders': 'folder' in intro})
    if title == 'writing_data_to_the_data_memory' and 'device data storage file in data memory' in intro:
        add('device_memory', {'operation': 'write_device_data_file', 'storage': 'data_memory'})
    if title == 'reading_data_from_the_data_memory' and 'device data storage file' in body:
        add('device_memory', {'operation': 'read_device_data_file', 'storage': 'data_memory'})
    extended = {'reading_extended_file_register': 'read', 'writing_extended_file_register': 'write',
                 'batch_initialization_function_of_extended_file_register': 'initialize'}
    if title in extended and 'extended file register' in body.casefold():
        add('extended_file_register', {'operation': extended[title]})
    if title == 'detecting_a_character_string_length' and 'detect the length' in intro:
        add('string_length', {'source': 's', 'target': 'd', 'terminator': '00H'})
    if title in {'extracting_character_string_data_from_the_right', 'extracting_character_string_data_from_the_left'}:
        if 'extract' in intro:
            add('string_slice', {'source': 's', 'target': 'd', 'count': 'n',
                                 'origin': 'right' if title.endswith('right') else 'left'})
    if title == 'storing_the_specified_number_of_character_strings' and 'extract the number of characters' in intro:
        add('string_slice', {'source': 's1', 'target': 'd', 'position_memory': 's2', 'count_memory': 's2_plus_1'})
    if title == 'replacing_the_specified_number_of_character_strings' and 'store the extracted data' in intro:
        add('string_replace', {'source': 's1', 'target': 'd', 'position_memory': 's2', 'count_memory': 's2_plus_1'})
    if title == 'searching_character_string' and 'search result' in intro:
        add('string_find', {'needle': 's1', 'haystack': 's2', 'start': 's3', 'target': 'd'})
    if title == 'inserting_character_string' and 'insert the character string' in intro:
        add('string_insert', {'source': 's1', 'position': 's2', 'target': 'd'})
    if title == 'deleting_character_string' and 'delete (n) characters' in intro:
        add('string_delete', {'position': 's', 'count': 'n', 'target': 'd'})
    if title == 'generating_random_number' and 'random' in intro:
        add('random_number', {'operation': 'generate', 'target': 'd'})
    if title.startswith(('saving_all_data_of_the_index_register', 'saving_the_selected_data_of_the_index_register')):
        if 'index register' in body:
            add('index_register_stack', {'operation': 'save', 'selection': 'all' if 'all_data' in title else 'specified'})
    if title.startswith(('returning_all_data_of_the_index_register', 'returning_the_selected_data_of_the_index_register')):
        if 'index register' in body:
            add('index_register_stack', {'operation': 'restore', 'selection': 'all' if 'all_data' in title else 'specified'})
    if title.startswith('upper_and_lower_limit_control_') and 'limit' in intro:
        add('limit_control', {'operation': 'clamp', 'lower': 's1', 'upper': 's2', 'input': 's3', 'target': 'd', **width})
    if title.startswith('dead_band_control_') and 'dead band' in intro:
        add('dead_band', {'lower': 's1', 'upper': 's2', 'input': 's3', 'target': 'd', **width})
    if title.startswith('zone_control_') and 'add the bias value' in intro:
        add('zone_control', {'negative_bias': 's1', 'positive_bias': 's2', 'input': 's3', 'target': 'd',
                             'zero_input_output': 0, 'unsigned_ignores_s1': opcode.endswith('_U'), **width})
    if title.startswith('scaling_') and 'scal' in intro:
        add('scaling', {'operation': 'coordinate_conversion',
                        'coordinate_format': 'xy' if 'xy_coordinates' in title else 'points', **width})
    if title == 'teaching_timer' and '1-second units' in body:
        add('teaching_timer', {'target': 'd', 'current_duration': 'd_plus_1', 'base_unit_seconds': 1,
                               'multiplier': '10_power_s', 'on_command_falling':
                               {'d_plus_1': 0, 'd': 'retained'}})
    if title == 'special_function_timer' and 'four devices' in intro:
        add('timer_outputs', {'timer': 's1', 'preset': 's2', 'unit_ms': 100, 'output_start': 'd',
            'outputs': [{'offset': 0, 'operation': 'off_delay'}, {'offset': 1, 'operation': 'falling_one_shot'},
                        {'offset': 2, 'operation': 'rising_one_shot_until_timeout_or_command_off'},
                        {'offset': 3, 'operation': 'on_delay_and_off_delay'}]})
    if title == 'signed_32_bit_bi_directional_counters' and 'external input instead of the drive contact' in body:
        add('bidirectional_counter', {'target': 'd', 'preset': 's', 'width_bits': 32, 'signed': True,
            'ordinary_count_event': 'command_rising_edge', 'high_speed_count_event': 'external_input',
            'ordinary_direction_devices': 'SM8200..SM8234', 'high_speed_direction_devices': 'SM4580..SM4595',
            'ordinary_LC35_and_later': 'count_up_only', 'pulse_io_module_supported': False})
    if title == 'crc_calculation' and 'X16 + X15 + X2 + 1' in body:
        add('checksum', {'operation': 'crc16', 'polynomial_exponents': [16, 15, 2, 0]})
    if title == 'reading_the_indirect_address' and 'store the indirect address' in intro:
        add('address', {'operation': 'address_of', 'source': 's', 'target': 'd', 'target_words': 2})
    if title in {'reading_clock_data', 'writing_clock_data'} and 'clock' in intro:
        add('clock_data', {'operation': 'read' if title.startswith('reading') else 'write', 'clock': 'CPU_rtc'})
    if title.startswith('converting_time_data_from_') and 'convert' in intro:
        to_seconds = 'hour_minute_second_to_seconds' in title
        add('time_conversion', {'source': 's', 'target': 'd', 'input_format': 'h_m_s' if to_seconds else 'seconds',
                                'output_format': 'seconds' if to_seconds else 'h_m_s', **width})
    if title in {'comparing_date_data', 'comparing_time_data'} and 'Set the comparison target by (s3)' in intro:
        relation = re.search(r'(<=|>=|<>|=|<|>)', opcode)
        if relation:
            add('clock_comparison', {'relation': relation[1], 'operands': ['s1', 's2'],
                                     'target_selector': 's3', 'data_kind': 'date' if 'date' in title else 'time'})
    if title == 'comparing_clock_data' and 'time specified by (s1), (s2), and (s3)' in intro:
        add('clock_comparison', {'left_fields': ['s1', 's2', 's3'], 'right_start': 's4', 'result_start': 'd'})
    if title == 'comparing_clock_data_zones' and 'comparison time zone' in intro:
        add('clock_zone', {'lower': 's1', 'upper': 's2', 'input': 's3', 'result_start': 'd'})
    if title == 'generating_timing_pulses' and 'number of scans specified by (n1)' in intro:
        add('clock_output', {'target': 'd', 'on_scans': 'n1', 'off_scans': 'n2'})
    if title == 'hour_meter' and 'units of hour' in intro:
        add('elapsed_time', {'input': 'execution_command', 'condition': 'on', 'unit': 'hour'})
    if title == 'i_o_refresh' and 'refresh the (n) points' in intro:
        add('io_refresh', {'start': 's', 'count': 'n', 'operation': 'receive_inputs_or_output'})
    if title.startswith('reading_1_word_2_word_data_from_another_module') and 'buffer memory' in intro:
        add('buffer_transfer', {'direction': 'module_to_CPU', 'module': 'U/H', 'buffer_start': 's',
                                'device_start': 'd', 'count': 'n', 'words_per_item': 2 if opcode.startswith('D') else 1})
    if title.startswith('writing_1_word_2_word_data_to_another_module') and 'buffer memory address' in intro:
        add('buffer_transfer', {'direction': 'CPU_to_module', 'module': 'U/H', 'buffer_start': 's1',
                                'device_start': 's2', 'count': 'n', 'words_per_item': 2 if opcode.startswith('D') else 1})
    if title in {'divided_bfm_read', 'divided_bfm_write'} and 'FX3 intelligent function module' in intro:
        add('buffer_transfer', {'direction': 'module_to_CPU' if title.endswith('read') else 'CPU_to_module',
                                'module_family': 'FX3_intelligent_function', 'transfer': 'divided'})
    if title in {'setting_trigger_logging', 'resetting_trigger_logging'} and 'trigger condition' in intro:
        add('logging', {'operation': 'set_trigger' if title.startswith('setting') else 'reset_trigger'})
    if title == '9_1_starts_ends_step_ladder' and 'starts step ladder' in intro:
        add('step_ladder', {'operation': 'start' if opcode == 'STL' else 'end',
                           'state_identifier': 'S_device_specified_by_d' if opcode == 'STL' else None,
                           'state_writers': ['SET', 'OUT', 'RST', 'ZRST']})
        prose = re.sub(r'\s+', ' ', section.get('text', ''))
        if 'both state relays are ON for one operation cycle' in prose:
            add('step_ladder_lifecycle', {
                'state_owner': 'S_step_relay', 'active_state': 'execute_connected_circuit',
                'on_transfer': {'source_and_destination_on_cycles': 1,
                                'source_used_as_contact': 'off_immediately_after_transfer_condition'},
                'following_cycle': {'source_state': False,
                                    'source_drive_instructions': 'execute_off_processing_once',
                                    'actual_preceding_contact_does_not_override_off_processing': True},
                'later_inactive_cycles': 'instruction_not_executed_jump_state',
                'lifecycle_scope': 'step_ladder_state_not_general_ST_condition',
            }, dimension='execution.step_ladder_lifecycle')
        if 'status is backed up by nonvolatile memory' in body:
            add('step_ladder_restart', {
                'latched_S': {'power_failure': 'retain', 'RUN_to_STOP': 'retain',
                              'restart': 'resume_previously_active_state'},
                'non_latched_S': {'power_off_or_RUN_to_STOP': 'clear',
                                  'restart': 'cannot_resume_previously_active_state'},
            }, dimension='execution.step_ladder_restart')
        if 'STL transfer disable' in body:
            add('step_ladder_status', {
                'SM8040': {'when_on': 'disable_all_state_transfers'},
                'SM8046': 'any_step_relay_on',
                'SM8047': 'enable_active_step_number_monitoring',
                'SD8040..SD8047': {'contents': 'active_S_numbers', 'order': 'ascending', 'maximum_count': 8},
            })
    if title in {'checking_the_status_of_a_step', 'checking_the_status_of_a_block'} and 'active or inactive' in intro:
        add('sfc_status', {'object': 'step' if title.endswith('step') else 'block',
                           'test': 'inactive' if opcode in {'LDI', 'ANI', 'ORI'} else 'active',
                           'combine': 'replace' if opcode.startswith('LD') else 'and' if opcode in {'AND', 'ANI'} else 'or'})
    if title == 'batch_reading_the_status_of_steps' and 'batch-read' in intro:
        add('sfc_status', {'operation': 'read_step_states', 'unit_bits': 16, 'result': 'specified_device'})
    sfc_operations = {'starting_a_block': ('block', 'activate_from_initial_step'),
        'ending_a_block': ('block', 'deactivate'), 'pausing_a_block': ('block', 'pause'),
        'restarting_a_block': ('block', 'resume_from_stopped_step'), 'activating_a_step': ('step', 'activate'),
        'deactivating_a_step': ('step', 'deactivate'), 'activating_deactivating_a_step': ('step', 'assign_activity'),
        'batch_deactivating_a_step': ('step_region', 'deactivate')}
    if title in sfc_operations and ('step' in body or 'block' in body):
        object_kind, operation = sfc_operations[title]
        add('sfc_operation', {'object': object_kind, 'operation': operation})
    if title == 'creating_a_dummy_transition_condition' and 'dummy output' in intro:
        add('sfc_transition', {'operation': 'satisfy_transition_condition'})
    if title in {'setting_32_bit_data_comparison', 'reset_32_bit_data_comparison'} and 'high-speed counter' in intro:
        add('high_speed_compare', {'comparison': 'equal', 'operation': 'set' if opcode == 'DHSCS' else 'reset',
                                   'output_update': 'immediate', 'pulse_io_module_supported': False})
    if title.startswith('start_stop_of_the_') and 'high-speed I/O function' in intro:
        add('high_speed_io', {'operation': 'start_or_stop', **width})
    if title.startswith('high_speed_current_value_transfer_') and 'special register' in intro:
        add('high_speed_io', {'operation': 'read_or_write_current_value',
                              'functions': ['counter', 'pulse_width_measurement', 'PWM', 'positioning'], **width})
    positioning = {
        'zero_return_opr_with_32_bit_data_dog_search': ('mechanical zero return', 'dog_search_zero_return'),
        '32_bit_data_interrupt_positioning': ('interrupt 1-speed constant quantity feed', 'interrupt_fixed_feed'),
        'positioning_by_one_table_operation': ('one specified table operation', 'single_table'),
        '32_bit_data_abs_current_value_read': ('absolute position (ABS) data', 'read_absolute_position'),
        '32_bit_data_variable_speed_pulse': ('variable speed pulses', 'variable_speed_pulse'),
        '32_bit_data_relative_positioning': ('incremental drive', 'relative_move'),
        '32_bit_data_absolute_positioning': ('absolute drive', 'absolute_move'),
    }
    if title in positioning and positioning[title][0] in intro:
        add('positioning', {'operation': positioning[title][1], 'module_scope': 'CPU_only' if 'Only CPU module' in intro else 'per_header_and_module_conditions',
                            **width})
    normalized_title = re.sub(r'^\d+_\d+_', '', title)
    communication = {
        'opening_a_connection': ('opens a connection', 'open'),
        'closing_a_connection': ('closes a connection', 'close'),
        'reading_receive_data': ('reads the receive data', 'receive'),
        'reading_receive_data_during_the_end_processing': ('reads the receive data', 'receive_during_END'),
        'sending_data': ('sends data', 'send'),
        'reading_connection_information': ('reads the connection information', 'connection_info'),
        'reading_socket_communications_receive_data': ('socket communication receive data area', 'read_receive_area'),
        'executing_the_registered_protocols': ('executes the communication protocol', 'execute_registered_protocol'),
        'predefined_protocol_support_function_instruction': ('executes the communication protocol', 'execute_registered_protocol'),
        'sending_the_slmp_frame': ('SLMP', 'send_SLMP'),
        'sending_an_slmp_frame': ('SLMP', 'send_receive_SLMP'),
        'serial_communication_2': ('non-protocol communication', 'serial_non_protocol'),
        'modbus_communication_instruction': ('MODBUS Master', 'modbus_master_transaction'),
    }
    if normalized_title in communication and communication[normalized_title][0] in intro:
        add('communication', {'operation': communication[normalized_title][1],
                               'target': 'module' if section['outline'].startswith('PART 4 ') else 'CPU_integrated_port'})
    if title in {'sending_ftp_client_files', 'retrieving_ftp_client_files'} and 'FTP server' in intro:
        add('ftp_transfer', {'direction': 'CPU_to_server' if title.startswith('sending') else 'server_to_CPU',
                             'source': 's2', 'target_folder': 's3'})
    inverter = {'IVCK': ('reads the operation status', 'read_status'),
                'IVDR': ('writes a control value', 'write_control'),
                'IVRD': ('reads a parameter', 'read_parameter'),
                'IVWR': ('writes a parameter', 'write_parameter'),
                'IVBWR': ('writes parameters', 'write_parameter_block'),
                'IVMC': ('2 types of settings', 'two_settings_and_two_monitors')}
    if opcode in inverter and inverter[opcode][0] in intro:
        add('inverter', {'operation': inverter[opcode][1]})
    if title.startswith(('reading_data_from_another_station_programmable_controller',
                         'writing_data_to_another_station_programmable_controller')):
        if 'another station programmable controller' in intro:
            add('network_transfer', {'direction': 'remote_to_local' if title.startswith('reading') else 'local_to_remote',
                                     'unit': 'word', 'remote_completion_notification': 'notification' in title})
    if title in {'sending_data_to_another_station_programmable_controller', 'receiving_data_from_another_station_programmable_controller'}:
        if 'another station programmable controller' in intro:
            add('network_message', {'operation': 'send' if title.startswith('sending') else 'receive'})
    if normalized_title in {'own_station_number_ip_address_setting', 'setting_the_station_number_to_own_station'} and 'station number' in intro:
        add('module_configuration', {'operation': 'set_station_number', 'also_sets_IP': 'IP address' in intro,
                                     'precondition': 'not_yet_set'})
    if normalized_title == 'backing_up_module_data_writing_data_to_the_flash_rom' and 'module extension parameter' in intro:
        add('module_configuration', {'operation': 'write_extension_parameters_to_file', 'source': 'module_buffer_memory'})
    if normalized_title == 'initializing_the_module' and 'factory default settings' in intro:
        add('module_configuration', {'operation': 'restore_factory_defaults',
                                     'resources': ['module_parameters', 'extension_parameters', 'parameter_file']})
    if title == 'rotary_table_shortest_direction_control' and 'shortest direction control' in intro.casefold():
        add('rotary_table', {'operation': 'shortest_direction', 'divisions': 'n1', 'low_speed_sections': 'n2',
            'source_fields': {'0': 'current_count', '1': 'station', '2': 'product'},
            'io_start': 'd', 'input_offsets': {'0': 'phase_A', '1': 'phase_B', '2': 'zero'},
            'output_offsets': {'3': 'forward_fast', '4': 'forward_slow', '5': 'stop',
                               '6': 'reverse_slow', '7': 'reverse_fast'}})
    if title == 'ramp_signal' and 'start value (initial value)' in intro:
        add('ramp', {'operation': 'interpolate_start_to_target', 'steps': 'n'})
    if title.startswith('measuring_the_density_') and 'counts the number of times' in intro:
        add('pulse_density', {'input': 's1', 'edge': 'rising', 'period': 's2', 'period_unit_ms': 1,
                              'target': 'd', 'pulse_io_module_supported': False, **width})
    if title == '32_bit_binary_pulse_output' and 'outputs 32-bit pulse trains' in intro:
        add('pulse_output', {'speed': 's', 'pulse_count': 'n', 'output': 'd', 'width_bits': 32,
                             'module_scope': 'CPU_only'})
    if title.endswith('_bit_binary_pulse_width_modulation') and 'period specified by (s2)' in intro:
        add('pwm_output', {'on_time': 's1', 'period': 's2', 'output': 'd', **width})
    if title == 'input_matrix' and 'Reads matrix input as 8-point input' in intro:
        add('input_matrix', {'input_start': 's', 'input_points': 8, 'column_output_start': 'd1',
                             'column_count': 'n', 'output_type': 'transistor', 'result_start': 'd2',
                             'read_order': 'columns_in_turn', 'interrupt_period_ms': [10, 20]})
    if title == 'initial_state' and 'Automatically controls the initial state' in intro:
        add('initial_state', {'program_kind': 'step_ladder', 'mode_switch_start': 's', 'mode_switch_points': 8,
                              'state_first': 'd1', 'state_last': 'd2', 'controls_special_relays': True})
    if title.endswith('_bit_binary_data_absolute_method') and 'current value' in intro:
        add('output_pattern', {'operation': 'counter_value_absolute_pattern', **width})
    if title == 'relative_method' and 'using a pair of counters' in intro:
        add('output_pattern', {'operation': 'paired_counter_relative_pattern', 'counter_count': 2})
    if title == 'check_code' and 'horizontal parity value and sum check value' in intro:
        add('checksum', {'operations': ['horizontal_parity', 'sum_check']})
    if title.startswith('searching_') and 'same data, maximum value and minimum value in a data table' in intro:
        add('table_search', {'operations': ['equal', 'maximum', 'minimum'], **width})
    if title.startswith('bit_check_') and 'store the total bits of 1' in intro:
        add('population_count', {'source': 's', 'target': 'd', **width})
    if title.startswith('bit_judgment_') and 'check whether (n) bit(s)' in intro:
        add('bit_judgment', {'source': 's', 'bit_position': 'n', 'target': 'd', **width})
    if title == '8_29_real_time_monitor_function_instruction' and 'monitors the data when the instruction is executed' in intro:
        add('monitor_collection', {'timing': 'instruction_execution',
                                   'required_collection_setting': 'RTM_Instruction_Specification',
                                   'interrupt_program': 'does_not_collect', 'maximum_calls_per_scan': 1,
                                   'free_buffer_register': 'SD1484', 'free_buffer_unit': 'KiB'})
    if normalized_title == 'setting_parameters' and 'Set the parameters to the FX5-CCLIEF' in intro:
        add('module_configuration', {'operation': 'set_parameters', 'module': 'FX5-CCLIEF',
                                     'control_start': 's1', 'dummy_operands': ['s2', 's3', 's4'],
                                     'completion': 'd', 'error_completion': 'd_plus_1'})
    return specs + variant_operation_specs(section, record)

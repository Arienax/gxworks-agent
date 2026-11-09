"""Authored PART 5/6 behavior of JY997D55801 AB; no Core execution grant.

The shared declaration parser establishes ownership. Values below encode the
complete operation families and the relevant numeric, string, timing and state
boundaries, rather than abbreviating source sentences. The caller binds CPU
badges and source references to the returned payloads.
"""
from __future__ import annotations

import copy
import re

from knowledge.instruction_document import instruction_declaration_names


ELEMENTARY = ['BOOL', 'INT', 'DINT', 'WORD', 'DWORD', 'REAL', 'STRING', 'TIME']
NUMERIC = ['INT', 'DINT', 'REAL']
SPECIAL_REALS = ['negative_zero', 'subnormal', 'nan', 'positive_infinity', 'negative_infinity']
CONVERSIONS = {
    'BOOL': 'WORD DWORD INT DINT TIME STRING',
    'WORD': 'BOOL DWORD INT DINT TIME',
    'DWORD': 'BOOL WORD INT DINT TIME',
    'INT': 'BOOL WORD DWORD DINT BCD REAL TIME STRING',
    'DINT': 'BOOL WORD DWORD INT BCD REAL TIME STRING',
    'BCD': 'INT DINT', 'REAL': 'INT DINT STRING',
    'TIME': 'BOOL WORD DWORD INT DINT STRING',
    'STRING': 'BOOL INT DINT REAL TIME',
}


def _range(parameter, minimum, maximum, **extra):
    return {'parameter': parameter, 'minimum': minimum, 'maximum': maximum, **extra}


def _conflict(identity, *claims):
    return {'conflict': identity, 'source_claims': list(claims),
            'resolution': 'not_established', 'automatic_admission': False}


def _conversion(base):
    source, target = base.split('_TO_')
    if source not in CONVERSIONS or target not in CONVERSIONS[source].split():
        return None
    operation = {'operation': 'type_conversion', 'source_type': source,
                 'target_type': target, 'input': 's', 'output': 'd'}
    domain, conflicts = {}, []
    if source == 'BOOL':
        operation['false_output'] = '0' if target == 'STRING' else 0
        operation['true_output'] = '1' if target == 'STRING' else 1
        if target == 'TIME':
            operation['output_unit'] = 'ms'
    elif target == 'BOOL':
        if source in {'WORD', 'DWORD', 'INT', 'DINT'}:
            operation.update(zero_output=False, nonzero_output=True)
        elif source == 'STRING':
            operation['documented_character_mapping'] = {'0': False, '1': True}
            domain['input_capacity_characters'] = 1
        else:
            operation['documented_zero_output'] = False
            operation['documented_nonzero_example_output'] = True
            domain['general_nonzero_rule'] = 'not_explicitly_stated'
    if (source, target) in {('WORD', 'DWORD'), ('WORD', 'DINT'), ('INT', 'DWORD')}:
        operation.update(bit_action='zero_extend', source_bits=16, target_bits=32)
    if (source, target) in {('DWORD', 'WORD'), ('DWORD', 'INT'), ('DINT', 'WORD'), ('TIME', 'INT')}:
        operation.update(bit_action='keep_low_bits', retained_bits=16, discarded_high_bits=16)
    if (source, target) in {('WORD', 'INT'), ('INT', 'WORD'), ('DWORD', 'DINT'), ('DINT', 'DWORD')}:
        operation['bit_action'] = 'preserve_bit_pattern'
    if (source, target) == ('INT', 'DINT'):
        operation.update(numeric_action='preserve_integer_value', source_bits=16, target_bits=32)
    if (source, target) == ('DINT', 'INT'):
        domain['ranges'] = [_range('s', -32768, 32767)]
        domain['out_of_range'] = {'error_code': '3401H', 'output': 'undefined'}
    if target == 'BCD':
        operation.update(operation='encode_packed_bcd', decimal_digit_bits=4)
        if source == 'INT':
            domain.update(output_types=['WORD'], ranges=[_range('s', 0, 9999)])
        else:
            domain.update(output_types=['WORD', 'DWORD'], rejected_output_types=['BOOL'],
                          conditional_ranges=[
                              {'output_type': 'WORD', **_range('s', 0, 9999)},
                              {'output_type': 'DWORD', **_range('s', 0, 99999999)}])
            conflicts.append(_conflict('dint_to_bcd_word_error_table_range',
                {'location': 'operation_processing', 'input_range': [0, 9999]},
                {'location': 'operation_error_word_row', 'printed_input_ranges': [[-32768, 32767], [0, 9999]]}))
        domain['invalid_input_error_code'] = '3401H'
    if source == 'BCD':
        operation.update(operation='decode_packed_bcd', decimal_digit_bits=4)
        domain.update(digit_range=[0, 9], invalid_digit_error_code='3401H')
        domain['input_types'] = ['WORD'] if target == 'INT' else ['WORD', 'DWORD']
        domain['packed_digit_counts'] = {'WORD': 4, 'DWORD': 8}
        if target == 'DINT':
            domain['rejected_input_types'] = ['BOOL']
    if target == 'REAL' and source in {'INT', 'DINT'}:
        operation['numeric_action'] = 'integer_to_binary32'
        if source == 'DINT':
            domain.update(documented_no_rounding_error_range=[-16777216, 16777215],
                          outside_that_range='rounding_error_possible', significant_decimal_digits_approx=7)
    if source == 'REAL' and target in {'INT', 'DINT'}:
        limits = [-32768, 32767] if target == 'INT' else [-2147483648, 2147483647]
        domain.update(ranges=[_range('s', *limits)], fractional_digits='discarded',
                      negative_fraction_rounding_direction='not_disambiguated_by_source',
                      invalid_real_classes=SPECIAL_REALS, invalid_real_error_code='3402H',
                      outside_effective_range='correct_operation_not_guaranteed')
    if target == 'STRING' and source in {'INT', 'DINT', 'TIME'}:
        width = 6 if source == 'INT' else 11
        operation.update(operation='format_signed_decimal_string', width_characters=width,
                         positive_sign_ascii=0x20, negative_sign_ascii=0x2D,
                         unused_high_digits_ascii=0x20,
                         termination={'when_device': 'SM701', 'equals': False, 'append_ascii': 0})
        if source == 'TIME':
            operation['numeric_unit'] = 'ms'
    if source == 'REAL' and target == 'STRING':
        operation.update(operation='format_exponent_string', width_characters=13,
                         integer_digits=1, fractional_digits=5, exponent_digits=2,
                         unused_fraction_digits_ascii=0x30, unused_exponent_digits_ascii=0x30,
                         positive_integer_sign_ascii=0x20, negative_integer_sign_ascii=0x2D,
                         positive_exponent_sign_ascii=0x2B, negative_exponent_sign_ascii=0x2D,
                         fixed_ascii_positions={'2': 0x20, '4': 0x2E, '10': 0x45},
                         fractional_tail_after_digit_5='rounded', terminator_ascii=0)
        domain.update(invalid_real_classes=SPECIAL_REALS, invalid_real_error_code='3402H',
                      output_storage_shortage_error_code='3406H')
    if source == 'STRING' and target in {'INT', 'DINT'}:
        limits = [-32768, 32767] if target == 'INT' else [-2147483648, 2147483647]
        operation['operation'] = 'parse_signed_decimal_string'
        domain.update(numeric_range=limits, input_capacity_characters=6 if target == 'INT' else 11,
                      accepted_ascii_ranges=[[0x30, 0x39]], accepted_ascii=[0x20, 0x2D, 0],
                      invalid_input_error_code='3401H')
        conflicts.append(_conflict('signed_string_error_row_omits_minus',
            {'location': 'operation_processing', 'accepted_sign_ascii': 0x2D},
            {'location': 'operation_error', 'listed_nondigit_ascii': [0x20, 0]}))
    if source == 'STRING' and target == 'REAL':
        operation.update(operation='parse_decimal_or_exponent_string', significant_digits=6,
                         later_digits='discarded', spaces='ignored',
                         interior_zero_characters='ignored_except_initial_zero_as_documented',
                         zeros_before_exponent_marker='ignored_as_documented',
                         omitted_sign='positive', positive_sign_ascii=0x2B, negative_sign_ascii=0x2D)
        domain.update(input_capacity_characters=24, spaces_and_zeros_count_toward_capacity=True,
                      input_length_range=[1, 24],
                      accepted_ascii_ranges=[[0x30, 0x39]],
                      accepted_ascii=[0x45, 0x65, 0x2B, 0x2D, 0x2E, 0x20, 0],
                      maximum_decimal_points=1, maximum_exponent_markers=1,
                      maximum_exponent_digits=2, maximum_mantissa_signs=1, maximum_exponent_signs=1,
                      missing_terminator_error_code='2820H', malformed_input_error_code='3401H',
                      absolute_output_upper_bound_exclusive=2 ** 128, overflow_error_code='3403H')
    if source == 'STRING' and target == 'TIME':
        operation.update(operation='parse_decimal_time_string', output_unit='ms')
        domain.update(input_capacity_characters=11,
                      input_numeric_range_as_printed=[-2147483648, 4147483647],
                      accepted_ascii_ranges=[[0x30, 0x39]], accepted_ascii=[0x20, 0],
                      invalid_input_error_code='3401H', effective_upper_bound='source_conflict')
        conflicts.append(_conflict('string_to_time_upper_bound',
            {'location': 'conversion_error_table', 'maximum_as_printed': 4147483647},
            {'location': 'time_arithmetic_signed_32_bit_examples', 'maximum': 2147483647}))
    if (source, target) in {('WORD', 'TIME'), ('DWORD', 'TIME'), ('TIME', 'WORD'), ('TIME', 'DWORD')}:
        operation['binary_numeric_equivalence'] = 'not_established_from_figure'
        conflicts.append(_conflict('time_word_hexadecimal_example_interpretation',
            {'location': 'conversion_figure', 'hexadecimal_digit_examples_match_decimal_ms_digits': True},
            {'location': 'type_name', 'bit_string_type': 'WORD' if 'WORD' in {source, target} else 'DWORD'}))
    return operation, domain, conflicts


def _standard_function(base):
    conversion = _conversion(base) if '_TO_' in base else None
    if conversion:
        return conversion
    operation, domain, conflicts = {'operation': ''}, {}, []
    if base in {'BITARR_TO_INT', 'BITARR_TO_DINT', 'INT_TO_BITARR', 'DINT_TO_BITARR', 'CPY_BITARR'}:
        width = 16 if base in {'BITARR_TO_INT', 'INT_TO_BITARR'} else 32
        operation.update(operation='pack_bit_array' if base.startswith('BITARR_') else
                         'copy_bit_array' if base == 'CPY_BITARR' else 'unpack_integer_bits',
                         input='s', bit_count='n', output='d')
        if base.startswith('BITARR_'):
            operation['unspecified_high_output_bits'] = 'zero'
        elif base != 'CPY_BITARR':
            operation['output_bits_beyond_count'] = 'unchanged'
        domain.update(constant_arguments=['n'], allowed_n=list(range(4, width + 1, 4)))
    elif base in {'GET_BIT_OF_INT', 'SET_BIT_OF_INT', 'CPY_BIT_OF_INT'}:
        operation.update(operation={'GET_BIT_OF_INT': 'read_word_bit', 'SET_BIT_OF_INT': 'write_word_bit',
                                    'CPY_BIT_OF_INT': 'copy_word_bit'}[base], input='s', output='d')
        if base == 'CPY_BIT_OF_INT':
            operation.update(source_bit='n1', destination_bit='n2')
            domain.update(constant_arguments=['n1', 'n2'], ranges=[_range('n1', 0, 15), _range('n2', 0, 15)])
        else:
            operation['bit_index'] = 'n'
            domain.update(constant_arguments=['n'], ranges=[_range('n', 0, 15)])
    elif base in {'GET_BOOL_ADDR', 'GET_INT_ADDR', 'GET_WORD_ADDR'}:
        target = base.split('_')[1]
        operation.update(operation='output_input_variable_as_declared_type', input='s', output='d', output_type=target)
        domain['input_types'] = (['BOOL', 'ARRAY_OF_BOOL'] if target == 'BOOL' else
            ['INT', 'DINT', 'WORD', 'REAL', 'TIME', 'STRING', 'ARRAY_OF_INT', 'ARRAY_OF_DINT',
             'ARRAY_OF_WORD', 'ARRAY_OF_DWORD', 'ARRAY_OF_REAL', 'ARRAY_OF_TIME'])
        if target in {'INT', 'WORD'}:
            domain['input_types_basis'] = 'shared_merged_input_type_cell'
    elif base in {'ABS', 'SQRT', 'LN', 'LOG', 'EXP', 'SIN', 'COS', 'TAN', 'ASIN', 'ACOS', 'ATAN'}:
        operation.update(operation={'ABS': 'absolute_value', 'SQRT': 'square_root', 'LN': 'natural_logarithm',
            'LOG': 'base_10_logarithm', 'EXP': 'natural_exponential', 'SIN': 'sine', 'COS': 'cosine',
            'TAN': 'tangent', 'ASIN': 'arcsine', 'ACOS': 'arccosine', 'ATAN': 'arctangent'}[base], input='s', output='d')
        domain['input_types'] = NUMERIC if base == 'ABS' else ['REAL']
        if base == 'ABS':
            domain['integer_minimum_results'] = [
                {'data_type': 'INT', 'input': -32768, 'output': -32768, 'error': False, 'ENO': True},
                {'data_type': 'DINT', 'input': -2147483648, 'output': -2147483648, 'error': False, 'ENO': True}]
        if base in {'SQRT', 'LN', 'LOG'}:
            domain['negative_input_error_code'] = '3405H'
            if base == 'SQRT':
                domain.update(input_range_as_printed='positive', zero_input_result='source_not_stated',
                              gap_kind='source_not_stated')
            elif base == 'LOG':
                domain.update(input_minimum_exclusive=0, zero_input_error_code='3405H')
                operation.update(negative_zero_result=0, underflow_result=0)
            else:
                domain['zero_input_result'] = 'not_explicitly_resolved'
        if base in {'LN', 'EXP'}:
            operation['documented_e_approximation'] = 2.71828
        if base == 'EXP':
            domain.update(documented_output_intervals=[[-3.40282e38, -1.17549e-38],
                                                       [1.17549e-38, 3.40282e38]],
                          outside_output_intervals_error_code='3403H')
        if base in {'SIN', 'COS', 'TAN'}:
            operation['input_angle_unit'] = 'radian'
            domain['negative_input_error_as_printed'] = '3402H'
        if base == 'TAN':
            operation['approximated_pi_over_2_and_3pi_over_2'] = 'source_conflict'
            conflicts.append(_conflict('tangent_special_angles_error_wording',
                {'location': 'operation_processing', 'angles': ['pi_over_2', '3pi_over_2'], 'operation_error': False},
                {'location': 'same_sentence_explanation', 'radian_value_operation_error': True}))
        if base in {'ASIN', 'ACOS', 'ATAN'}:
            operation['output_angle_unit'] = 'radian'
            if base in {'ASIN', 'ACOS'}:
                domain.update(ranges=[_range('s', -1, 1)], out_of_range_error_code='3405H')
                conflicts.append(_conflict('inverse_trigonometric_negative_input',
                    {'location': 'operation_processing', 'input_range': [-1, 1]},
                    {'location': 'operation_error', 'negative_input_error_code': '3402H'}))
            else:
                domain.update(documented_nonzero_absolute_input_range=[1.17549e-38, 3.40282e38],
                              negative_zero_error_code='3402H')
    elif base in {'ADD', 'MUL', 'SUB', 'DIV', 'MOD', 'EXPT'}:
        operation.update(operation={'ADD': 'sum', 'MUL': 'product', 'SUB': 'difference', 'DIV': 'quotient',
                                    'MOD': 'remainder', 'EXPT': 'exponentiation'}[base], output='d')
        operation['inputs'] = ['s1_to_s28'] if base in {'ADD', 'MUL'} else ['s1', 's2']
        domain['input_types'] = ['INT', 'DINT'] if base == 'MOD' else NUMERIC
        domain['input_and_output_same_type'] = base != 'EXPT'
        if base == 'EXPT':
            domain.update(base_types=['REAL'], exponent_types=NUMERIC, output_types=['REAL'])
        if base in {'ADD', 'MUL'}:
            domain['input_count_range'] = [2, 28]
        if base in {'DIV', 'MOD'}:
            domain.update(nonzero_arguments=['s2'], zero_divisor_error_code='3400H')
            operation['other_division_result'] = 'not_returned'
        if base in {'ADD', 'MUL', 'SUB'}:
            operation['integer_overflow'] = {'INT': 'keep_low_16_bits', 'DINT': 'keep_low_32_bits',
                                             'operation_error': False, 'ENO': True}
            operation['real_overflow'] = {'output': 'undefined', 'operation_error': True}
            operation['real_underflow'] = {'output': 'undefined', 'operation_error': True}
        if base in {'ADD', 'SUB'}:
            operation['zero_result_flag'] = 'SM8020'
    elif base == 'MOVE':
        operation.update(operation='assign_same_type_value', input='s', output='d')
        domain.update(input_types=ELEMENTARY + ['STRUCT', 'ARRAY'], input_and_output_same_type=True)
    elif base in {'SHL', 'SHR', 'ROL', 'ROR'}:
        operation.update(operation='logical_shift' if base in {'SHL', 'SHR'} else 'bit_rotation',
                         direction='left' if base in {'SHL', 'ROL'} else 'right', input='s', output='d', bit_count='n',
                         count_mask_bits={'WORD': 4, 'DWORD': 5})
        if base in {'SHL', 'SHR'}:
            operation['vacated_bits'] = 0
        domain.update(input_types=['WORD', 'DWORD'], input_and_output_same_type=True,
                      effective_count_ranges={'WORD': [0, 15], 'DWORD': [0, 31]})
        conflicts.append(_conflict('shift_count_data_type',
            {'location': 'argument_table', 'n_type': 'ANY_BIT'},
            {'location': 'operation_processing', 'n_type': 'INT'}))
    elif base in {'AND', 'OR', 'XOR', 'NOT'}:
        operation.update(operation='bitwise_' + base.lower(), output='d',
                         inputs=['s'] if base == 'NOT' else ['s1_to_s28'])
        domain.update(input_types=['BOOL', 'WORD', 'DWORD'], input_and_output_same_type=True)
        if base != 'NOT':
            domain['input_count_range'] = [2, 28]
        if base == 'XOR':
            operation['multi_input_evaluation_order'] = 'left_fold'
    elif base in {'SEL', 'MAX', 'MIN', 'LIMIT', 'MUX'}:
        operation['operation'] = {'SEL': 'select_by_boolean', 'MAX': 'maximum', 'MIN': 'minimum',
                                  'LIMIT': 'clamp_to_limits', 'MUX': 'select_by_zero_based_index'}[base]
        domain.update(value_types=ELEMENTARY + (['STRUCT', 'ARRAY'] if base in {'SEL', 'MUX'} else []),
                      selected_value_and_output_same_type=True)
        if base == 'SEL':
            operation.update(condition='s1', false_value='s2', true_value='s3', output='d')
        elif base == 'LIMIT':
            operation.update(lower='s1', value='s2', upper='s3', output='d')
            domain['bound_order'] = {'lower': 's1', 'upper': 's3'}
            conflicts.extend([
                _conflict('limit_upper_operand_in_integer_error_rows',
                    {'location': 'argument_table_and_processing', 'upper_operand': 's3'},
                    {'location': 'int_word_dint_dword_time_error_rows', 'upper_operand': 's2'}),
                _conflict('limit_equal_bounds', {'location': 'prose', 'required_relation': 'lower_lt_upper'},
                    {'location': 'error_table', 'error_relation': 'lower_gt_upper'})])
        elif base == 'MUX':
            operation.update(selector='n', inputs='s1_to_s28', output='d', selector_0='s1',
                             invalid_selector={'output': 'undefined', 'ENO': False, 'operation_error': False})
            domain.update(input_count_range=[2, 28], selector_range=[0, 27], selector_less_than_input_count=True)
        else:
            operation.update(inputs='s1_to_s28', output='d')
            domain['input_count_range'] = [2, 28]
    elif base in {'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'}:
        operation.update(operation='compare', relation={'GT': 'gt', 'GE': 'ge', 'EQ': 'eq', 'LE': 'le', 'LT': 'lt', 'NE': 'ne'}[base],
                         combination='binary' if base == 'NE' else 'all_adjacent_pairs', output='d', output_type='BOOL')
        domain.update(input_types=ELEMENTARY, input_count_range=[2, 2] if base == 'NE' else [2, 28])
        if base in {'LE', 'LT'}:
            domain['listed_string_operation_errors_do_not_apply'] = True
    elif base in {'LEN', 'LEFT', 'RIGHT', 'MID', 'CONCAT', 'INSERT', 'DELETE', 'REPLACE', 'FIND'}:
        operation.update(operation={'LEN': 'string_length', 'LEFT': 'left_substring', 'RIGHT': 'right_substring',
            'MID': 'substring', 'CONCAT': 'concatenate_strings', 'INSERT': 'insert_string', 'DELETE': 'delete_substring',
            'REPLACE': 'replace_substring', 'FIND': 'find_first_substring'}[base], output='d')
        domain.update(input_types=['STRING'], input_length_bytes_range=[0, 255], terminator_ascii=0)
        if base in {'LEFT', 'RIGHT'}:
            operation.update(input='s', length='n')
            domain.update(ranges=[_range('n', 0, 255)], n_not_greater_than_input_length=True)
        elif base in {'MID', 'DELETE', 'REPLACE'}:
            operation.update(input='s1' if base == 'REPLACE' else 's', length='n1', start='n2', position_base=1)
            domain.update(ranges=[_range('n1', 0, 255), _range('n2', 1, 255)],
                          start_within_source_length=True)
            if base == 'REPLACE':
                operation.update(replacement='s2', replacement_count='n1')
                domain['replacement_count_not_greater_than_replacement_length'] = True
            else:
                domain['length_not_greater_than_source_length'] = True
            if base == 'MID':
                conflicts.extend([
                    _conflict('mid_zero_length', {'location': 'processing', 'n1_range': [0, 255]},
                              {'location': 'error_table', 'error_when_n1_lte': 0}),
                    _conflict('mid_end_position', {'location': 'processing_example', 'position_base': 1},
                              {'location': 'error_table', 'reject_when_n1_plus_n2_gt_length': True})])
        elif base == 'CONCAT':
            operation.update(inputs='s1_to_s28', intermediate_terminators='omitted', output_limit_bytes=255,
                             overlength_output='prefix_within_255_bytes')
            domain['input_count_range'] = [2, 28]
        elif base == 'INSERT':
            operation.update(source='s1', inserted='s2', start='n', position_base=1,
                             intermediate_terminator='omitted', output_limit_bytes=255,
                             overlength_output='prefix_within_255_bytes')
            domain.update(ranges=[_range('n', 1, 255)], start_within_source_length=True)
        elif base == 'FIND':
            operation.update(haystack='s1', needle='s2', position_base=1, no_match_output=0)
        else:
            operation['input'] = 's'
    elif base in {'ADD_TIME', 'SUB_TIME', 'MUL_TIME', 'DIV_TIME'}:
        operation.update(operation={'ADD_TIME': 'add_time', 'SUB_TIME': 'subtract_time', 'MUL_TIME': 'scale_time',
                                    'DIV_TIME': 'divide_time'}[base], inputs=['s1', 's2'], output='d', output_type='TIME', unit='ms')
        domain.update(s1_types=['TIME'], s2_types=NUMERIC if base in {'MUL_TIME', 'DIV_TIME'} else ['TIME'])
        if base != 'DIV_TIME':
            operation['overflow'] = {'action': 'keep_low_32_bits_signed_time', 'operation_error': False, 'ENO': True}
            if base == 'MUL_TIME':
                domain['fractional_ms_rounding'] = 'not_explicitly_stated'
        else:
            operation['remainder'] = 'discarded'
            domain.update(nonzero_arguments=['s2'], zero_divisor_error_code='3400H')
    if not operation['operation']:
        return None
    return operation, domain, conflicts


def _standard_fb(base):
    operation, domain, conflicts = {}, {}, []
    if base in {'SR', 'RS'}:
        operation.update(operation='bistable_latch', state='d', set_input='s1', reset_input='s2',
                         simultaneous_priority='set' if base == 'SR' else 'reset',
                         neither_input_active='retain_previous_output')
    elif base in {'R_TRIG', 'F_TRIG'}:
        operation.update(operation='edge_to_pulse', edge='rising' if base == 'R_TRIG' else 'falling',
                         input='s', output='d', pulse_scans=1, next_executed_scan_without_edge=False,
                         initialization='not_specified', disabled_edge_history='not_specified')
    elif base in {'CTU', 'CTD', 'CTUD'}:
        operation.update(operation='counter', edge='rising', preset='n',
                         count='d3' if base == 'CTUD' else 'd2')
        domain['ranges'] = [_range('n', 0, 32767)]
        if base == 'CTU':
            operation.update(up_input='s1', reset_input='s2', reset_value=0, reset_done_output=False,
                             up_done='d1', up_done_at='preset', count_up_at_preset='stop')
        elif base == 'CTD':
            operation.update(down_input='s1', load_input='s2', load_value='n', load_done_output=False,
                             down_done='d1', down_done_at=0, count_down_at_zero='source_conflict')
            conflicts.append(_conflict('ctd_count_below_zero',
                {'location': 'operation_processing', 'count_down_at_zero': 'stop'},
                {'location': 'enabled_timing_chart', 'count_sequence_tail': [0, -1, -2, -3]}))
        else:
            operation.update(up_input='s1', down_input='s2', reset_input='s3', load_input='s4',
                             reset_value=0, load_value='n', up_done='d1', down_done='d2',
                             up_done_at='preset', down_done_at=0, count_up_at_preset='stop',
                             count_down_at_zero='stop', simultaneous_count_edges='up_priority',
                             simultaneous_reset_load='reset_priority')
    elif base == 'COUNTER_FB_M':
        operation.update(operation='counter_with_initial_value', edge='rising', input='s1', preset='s2',
                         initial='s3', count='d1', done='d2', done_at='preset',
                         unchanged_true_input='no_increment', negative_initial_value=0,
                         reset_method='RST_to_instance_Coil')
        domain['ranges'] = [_range('s2', 0, 32767), _range('s3', -32768, 32767)]
    elif base in {'TP', 'TP_10', 'TON', 'TON_10', 'TOF', 'TOF_10'}:
        family = base.split('_')[0]
        unit = 10 if base.endswith('_10') else 100
        operation.update(operation={'TP': 'pulse_timer', 'TON': 'on_delay_timer', 'TOF': 'off_delay_timer'}[family],
                         input='s', preset='n', output='d1', elapsed='d2', timing_resource='timer_device',
                         preset_sampling_edge='falling' if family == 'TOF' else 'rising',
                         preset_changed_during_timing='effective_at_next_sampling_edge',
                         disabled_time_measurement='stopped', resolution_ms=unit)
        domain.update(ranges=[_range('n', 0, 32767 * unit, unit='ms')], minimum_setting_unit_ms=unit,
                      outside_range='correct_operation_not_guaranteed')
        if family == 'TP':
            operation.update(start='rising_input_sets_output', elapsed_at_preset='output_false',
                             input_false_during_pulse='pulse_continues',
                             elapsed_reset='input_false_and_output_false')
        elif family == 'TON':
            operation.update(start='input_true_starts_measurement', elapsed_at_preset='output_true',
                             input_false='output_false_and_elapsed_zero')
        else:
            operation.update(input_true='output_true_and_elapsed_zero',
                             start='falling_input_starts_measurement', elapsed_at_preset='output_false')
    elif base in {'TIMER_1_FB_M', 'TIMER_10_FB_M', 'TIMER_100_FB_M', 'TIMER_CONT_FB_M', 'TIMER_CONTHS_FB_M'}:
        retentive = base in {'TIMER_CONT_FB_M', 'TIMER_CONTHS_FB_M'}
        unit = {'TIMER_1_FB_M': 1, 'TIMER_10_FB_M': 10, 'TIMER_100_FB_M': 100,
                'TIMER_CONT_FB_M': 100, 'TIMER_CONTHS_FB_M': 1}[base]
        operation.update(operation='retentive_timer_with_initial_value' if retentive else 'timer_with_initial_value',
                         input='s1', preset='s2', initial='s3', current='d1', done='d2', count_unit_ms=unit,
                         count_while_input=True, negative_initial_value=0, done_at='preset')
        if retentive:
            operation.update(input_false='retain_current_and_done', next_input_true='resume_from_retained_current',
                             reset_method='RST_to_instance_Coil')
        else:
            operation.update(input_false='reset_current_to_initial_and_done_false')
        domain['ranges'] = [_range('s2', 0, 32767), _range('s3', -32768, 32767)]
        if base == 'TIMER_100_FB_M':
            conflicts.append(_conflict('timer_100_example_duration',
                {'location': 'operation_processing', 'unit_ms': 100, 'preset': 10, 'initial': 1},
                {'location': 'timing_chart', 'printed_duration_ms': 90}))
    if not operation:
        return None
    return operation, domain, conflicts


def _declarations(section):
    names = []
    for _, text in section.get('page_texts') or []:
        names.extend(instruction_declaration_names(text, section['outline'], 'programming', source_text=section['text']))
    return set(names)


def canonical_standard_behavior_facts(section, parsed, record):
    """Return source-checked form/language payloads for caller-owned references.

    Structured Ladder blocks retain their project-mode condition. Core admission,
    native acceptance and the executor remain outside the knowledge assertion.
    """
    is_function = section['outline'].startswith('PART 5 STANDARD FUNCTIONS > ')
    is_fb = section['outline'].startswith('PART 6 FUNCTION BLOCKS > ')
    if not (is_function or is_fb):
        return []
    declared = _declarations(section)
    layout = re.sub(r'[\s|]+', ' ', '\n'.join(text for _, text in section.get('page_layouts') or []))
    result = []
    for form in record['forms']:
        if form not in declared:
            continue
        enabled = form.endswith('_E')
        base = form[:-2] if enabled else form
        semantics = _standard_function(base) if is_function else _standard_fb(base)
        if semantics is None:
            raise ValueError('uninterpreted_standard_form:' + form)
        operation, domain, conflicts = semantics
        def contains_conflict(value):
            return (value == 'source_conflict' if isinstance(value, str) else
                    any(contains_conflict(item) for item in value.values()) if isinstance(value, dict) else
                    any(contains_conflict(item) for item in value) if isinstance(value, list) else False)

        specifications = [('operation', 'effects.documented_operation', operation,
                           'conflict' if contains_conflict(operation) else 'source_verified')]
        if domain:
            specifications.append(('domain', 'constraints.documented_domain', domain,
                                   'conflict' if contains_conflict(domain) else
                                   'declared_unverified' if domain.get('gap_kind') == 'source_not_stated' else 'source_verified'))
        execution = {'evaluation': 'documented_function_invocation' if is_function else 'documented_function_block_invocation',
                     'has_EN_ENO': enabled, 'processing_when_enabled': True}
        if not enabled:
            execution['enable_input'] = 'absent'
        if re.search(r'There is no (?:operation )?error\.', layout):
            execution['operation_error_reporting'] = 'none_documented'
        specifications.append(('execution', 'execution.trigger', execution, 'source_verified'))
        if enabled:
            stopped = re.search(r'FALSE \(Stops operation\) FALSE(?:\s*\*\d+)? (Indefinite value|Previous output value)', layout)
            if not stopped:
                raise ValueError('unparsed_enable_false_result:' + form)
            disabled = {'EN': False, 'ENO': False, 'processing': 'stopped',
                        'outputs': 'undefined' if stopped[1] == 'Indefinite value' else 'previous_output_values'}
            if is_function and disabled['outputs'] == 'undefined':
                disabled['use_output_when_ENO_false'] = False
            specifications.append(('disabled', 'effects.disabled_state', disabled, 'source_verified'))
        failed = re.search(r'FALSE \(Operation error occurred\)(?:\s*\*\d+)? (Indefinite value|Previous output value)', layout)
        if enabled and failed:
            specifications.append(('error_output', 'effects.operation_error_state',
                {'EN': True, 'ENO': False, 'outputs': 'undefined' if failed[1] == 'Indefinite value' else 'previous_output_values'},
                'source_verified'))
        elif not enabled and re.search(r'An operation error occurred Indefinite value', layout):
            specifications.append(('error_output', 'effects.operation_error_state',
                {'when': 'operation_error', 'outputs': 'undefined'}, 'source_verified'))
        if 'denormalized number' in layout:
            specifications.append(('real_domain', 'constraints.real_representation',
                {'when_data_type': 'REAL', 'invalid_input_classes': SPECIAL_REALS,
                 'invalid_input_error_code': '3402H', 'normal_nonzero_absolute_lower_bound': 2 ** -126,
                 'absolute_upper_bound_exclusive': 2 ** 128}, 'source_verified'))
        for index, conflict in enumerate(conflicts):
            specifications.append(('source_conflict_' + str(index), 'constraints.source_consistency', conflict, 'declared_unverified'))
        languages = ['st', 'ladder'] if base in {'GET_BIT_OF_INT', 'SET_BIT_OF_INT', 'CPY_BIT_OF_INT'} else ['st', 'fbd', 'ladder']
        for language in languages:
            conditions = []
            if language == 'ladder':
                conditions.append({'context': 'project_mode', 'equals': 'structured_ladder_block'})
            if (language == 'st' and not enabled and base in {'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'}
                    and 'Supported by engineering tool version "1.035M" and later' in layout):
                conditions.append({'engineering_tool_version_at_least': '1.035M'})
            common = {'form': form, 'declaration_kind': 'standard_function' if is_function else 'standard_function_block',
                      'executor_capability': 'not_established', 'admission_effect': 'none'}
            if language == 'ladder':
                common['representation'] = 'structured_ladder_block'
            for key, dimension, value, status in specifications:
                fact = {'key': key, 'dimension': dimension, 'value': {**common, **copy.deepcopy(value)},
                        'form': form, 'language': language, 'status': status,
                        'page': section['first_page'], 'pages': list(section['pages'])}
                if conditions:
                    fact['conditions'] = copy.deepcopy(conditions)
                result.append(fact)
    return result

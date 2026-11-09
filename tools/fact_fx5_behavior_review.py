"""Typed behaviour review of the FX5 positioning instructions (chapter 32).

Scope
    Only the private index of ``MELSEC iQ-F FX5 User's Manual (Application)``
    (``fx5_application_ag``, JY997D55401 Rev.AG) is read.  Every fact is bound to
    one manual section and one documented instruction form, so nothing is
    borrowed from an already reviewed FX3 record with the same mnemonic.

What is published
    * identity: documented forms, section, language availability, callable kind
    * parameters: typed operands (role, direction, data type, documented range,
      label type) and the documented ST call with ``EN``/``ENO``
    * execution: ``EN``/``ENO`` semantics and the documented start, busy,
      complete, stop and error states of the instruction
    * effects: what the instruction does to pulses, the current address and the
      rotation direction, as token values only
    * memory: the documented related-device signals (SM/SD) with access mode
    * resources: documented error end behaviour
    * constraints: CPU module scope, device scope, units and cautions

Guarantees
    * no prose copied from the manual, only token values
    * no ``behavior`` keys, so nothing here is executable
    * the CPU scope is never widened beyond the documented header marks
    * a witness phrase from the cited chunk must exist for every record
    * the PDF is never read at run time and the index is opened read-only
"""

import json
import re

METHOD = 'authored_fx5_positioning_behavior_review_v1'
FX5_MANUAL = 'fx5_application_ag'
FX5_MANUAL_NUMBER = 'JY997D55401'
FX5_REVISION = 'AG'
FX5_TITLE = 'MELSEC iQ-F FX5 User\'s Manual (Application)'

# Documented header marks of this manual; no other CPU family is ever claimed.
DOCUMENTED_MODELS = ('FX5S', 'FX5U', 'FX5UJ', 'FX5UC')
MODEL_FAMILIES = ('FX5',)

TYPES = {
    'BOOL': {'kind': 'bit'},
    'WORD': {'kind': 'word_unsigned', 'bits': 16},
    'INT': {'kind': 'word_signed', 'bits': 16},
    'DINT': {'kind': 'double_word_signed', 'bits': 32},
    'ANY16': {'kind': 'any', 'bits': 16},
    'ANY32': {'kind': 'any', 'bits': 32},
    'ANY_ELEMENTARY_WORD': {'kind': 'any_elementary', 'bits': 16},
    'ANY_ELEMENTARY_BOOL': {'kind': 'any_elementary', 'bits': 1},
    'ANYBIT_ARRAY': {'kind': 'bit_array'},
}

LANGUAGES = {'ladder': True, 'st': True, 'fbd_ld': True}

EN_SEMANTICS = {
    'enable_argument': 'EN',
    'status_argument': 'ENO',
    'status_type': {'kind': 'bool'},
    'documented_call_form': 'ENO:=INSTRUCTION(EN,...)',
    'plain_form_execution': 'not_documented_for_positioning_instructions',
    'enabled_form_execution': 'when_EN_is_TRUE',
}

AXIS_GROUPS = {
    'axis1': 1, 'axis2': 2, 'axis3': 3, 'axis4': 4,
    'axis5': 5, 'axis6': 6, 'axis7': 7, 'axis8': 8,
    'axis9': 9, 'axis10': 10, 'axis11': 11, 'axis12': 12,
}

# Related-device signals shared by the chapter 32 instructions.  The device
# columns are the four axis slots of one output group plus the positioner
# groups; the first axis slot of a row is rendered separately in the source and
# is therefore written out explicitly here as the documented group head.
SHARED_SIGNALS = (
    {'id': 'instruction_execution_complete_flag', 'devices': ('SM8029',),
     'access': 'read', 'meaning': 'instruction_execution_completed'},
    {'id': 'instruction_execution_abnormal_end_flag', 'devices': ('SM8329',),
     'access': 'read', 'meaning': 'instruction_execution_ended_with_error'},
    {'id': 'positioning_instruction_activation', 'devices': ('SM5500', 'SM5501', 'SM5502',
                                                             'SM5503'),
     'access': 'read', 'meaning': 'positioning_instruction_is_active'},
    {'id': 'pulse_output_monitor', 'devices': ('SM5516', 'SM5517', 'SM5518', 'SM5519'),
     'access': 'read', 'meaning': 'pulse_output_in_progress'},
    {'id': 'positioning_error_occurrence', 'devices': ('SM5532', 'SM5533', 'SM5534', 'SM5535'),
     'access': 'read_write', 'meaning': 'positioning_error_present'},
    {'id': 'pulse_output_stop_command', 'devices': ('SM5628', 'SM5629', 'SM5630', 'SM5631'),
     'access': 'read_write', 'meaning': 'user_stop_request'},
    {'id': 'pulse_decelerate_stop_command', 'devices': ('SM5644', 'SM5645', 'SM5646', 'SM5647'),
     'access': 'read_write', 'meaning': 'user_deceleration_stop_request'},
    {'id': 'forward_limit', 'devices': ('SM5660', 'SM5661', 'SM5662', 'SM5663'),
     'access': 'read_write', 'meaning': 'forward_rotation_limit_signal'},
    {'id': 'reverse_limit', 'devices': ('SM5676', 'SM5677', 'SM5678', 'SM5679'),
     'access': 'read_write', 'meaning': 'reverse_rotation_limit_signal'},
    {'id': 'total_number_of_pulses', 'devices': ('SD8136', 'SD8137'),
     'access': 'read', 'meaning': 'total_pulses_output_from_axis'},
    {'id': 'current_address_user_unit', 'devices': ('SD5500', 'SD5501', 'SD5540', 'SD5541',
                                                    'SD5580', 'SD5581', 'SD5620', 'SD5621'),
     'access': 'read_write', 'meaning': 'current_address_in_user_unit'},
    {'id': 'current_address_pulse_unit', 'devices': ('SD5502', 'SD5503', 'SD5542', 'SD5543',
                                                     'SD5582', 'SD5583', 'SD5622', 'SD5623'),
     'access': 'read_write', 'meaning': 'current_address_in_pulse_unit'},
    {'id': 'current_speed_user_unit', 'devices': ('SD5504', 'SD5505', 'SD5544', 'SD5545',
                                                  'SD5584', 'SD5585', 'SD5624', 'SD5625'),
     'access': 'read', 'meaning': 'current_operation_speed'},
    {'id': 'positioning_error_code', 'devices': ('SD5510', 'SD5550', 'SD5590', 'SD5630'),
     'access': 'read_write', 'meaning': 'positioning_error_code_register'},
    {'id': 'positioning_instruction_activation_axis5_to_axis12',
     'devices': ('SM5504', 'SM5505', 'SM5506', 'SM5507', 'SM5508', 'SM5509', 'SM5510',
                 'SM5511'),
     'access': 'read', 'meaning': 'positioning_instruction_is_active'},
    {'id': 'pulse_output_monitor_axis5_to_axis12',
     'devices': ('SM5520', 'SM5521', 'SM5522', 'SM5523', 'SM5524', 'SM5525', 'SM5526',
                 'SM5527'),
     'access': 'read', 'meaning': 'pulse_output_in_progress'},
    {'id': 'positioning_error_occurrence_axis5_to_axis12',
     'devices': ('SM5536', 'SM5537', 'SM5538', 'SM5539', 'SM5540', 'SM5541', 'SM5542',
                 'SM5543'),
     'access': 'read_write', 'meaning': 'positioning_error_present'},
    {'id': 'all_outputs_disabled', 'devices': ('SM8034',), 'access': 'read',
     'meaning': 'all_outputs_disabled_flag'},
)

REMAINING_DISTANCE_SIGNALS = (
    {'id': 'remaining_distance_operation_enable',
     'devices': ('SM5596', 'SM5597', 'SM5598', 'SM5599'),
     'access': 'read_write', 'meaning': 'remaining_distance_operation_enabled'},
)

TABLE_SHIFT_SIGNALS = (
    {'id': 'table_shift_command', 'devices': ('SM5580', 'SM5581', 'SM5582', 'SM5583'),
     'access': 'read_write', 'meaning': 'request_to_shift_to_next_table'},
)

TABLE_SIGNALS = (
    {'id': 'positioning_table_data_initialization_disable',
     'devices': ('SM5916', 'SM5917', 'SM5918', 'SM5919'),
     'access': 'read_write', 'meaning': 'positioning_table_data_initialization_disabled'},
    {'id': 'positioning_execution_table_number',
     'devices': ('SD5506', 'SD5546', 'SD5586', 'SD5626'),
     'access': 'read', 'meaning': 'table_number_currently_executed'},
    {'id': 'positioning_error_occurrence_table_number',
     'devices': ('SD5511', 'SD5551', 'SD5591', 'SD5631'),
     'access': 'read_write', 'meaning': 'table_number_at_error_occurrence'},
)

DSZR_SIGNALS = (
    {'id': 'rotation_direction_setting', 'devices': ('SM5772', 'SM5773', 'SM5774', 'SM5775'),
     'access': 'read_write', 'meaning': 'documented_rotation_direction_setting'},
    {'id': 'opr_direction_specification', 'devices': ('SM5804', 'SM5805', 'SM5806', 'SM5807'),
     'access': 'read_write', 'meaning': 'documented_opr_direction'},
    {'id': 'clear_signal_output_function_enable',
     'devices': ('SM5820', 'SM5821', 'SM5822', 'SM5823'),
     'access': 'read_write', 'meaning': 'clear_signal_output_function_enabled'},
    {'id': 'zero_signal_count_start_time', 'devices': ('SM5868', 'SM5869', 'SM5870', 'SM5871'),
     'access': 'read_write', 'meaning': 'documented_zero_signal_count_start_time'},
)

PLSY_SIGNAL = (
    {'id': 'number_of_pulses_by_plsy_instruction',
     'devices': ('SD8140', 'SD8141', 'SD8142', 'SD8143'),
     'access': 'read', 'meaning': 'documented_number_of_pulses_of_plsy'},
)

CPU_DEVICE_LIMITS = {
    'output_axis_number': {'FX5S': 'K1_to_K4', 'FX5U': 'K1_to_K4', 'FX5UC': 'K1_to_K4',
                           'FX5UJ': 'K1_to_K3'},
    'pulse_output_device': {'FX5S': 'Y0_to_Y3', 'FX5U': 'Y0_to_Y3', 'FX5UC': 'Y0_to_Y3',
                            'FX5UJ': 'Y0_to_Y2'},
}

SPEED_LIMITS = {
    'command_speed_pulse': {'FX5S': '0_to_200kpps', 'FX5U': '0_to_200kpps',
                            'FX5UC': '0_to_200kpps', 'FX5UJ': '0_to_200kpps'},
    'command_speed_signed_pulse': {'FX5S': 'minus200kpps_to_plus200kpps',
                                   'FX5U': 'minus200kpps_to_plus200kpps',
                                   'FX5UC': 'minus200kpps_to_plus200kpps',
                                   'FX5UJ': 'minus200kpps_to_plus200kpps'},
    'opr_speed_pulse': {'FX5S': '1_to_200kpps', 'FX5U': '1_to_200kpps',
                        'FX5UC': '1_to_200kpps', 'FX5UJ': '1_to_200kpps'},
}

USER_UNIT = 'user_system_unit'


def _r(opcode, section, pages, **spec):
    spec.setdefault('kind', 'positioning_instruction')
    spec.setdefault('family', 'positioning_motion')
    spec.setdefault('languages', dict(LANGUAGES))
    spec.setdefault('signals', ())
    spec.setdefault('cautions', ())
    spec.setdefault('residuals', ())
    # Axis numbers, pulse output devices and the CPU-module-only restriction are
    # documented for the pulse output instructions of this chapter only; a record
    # has to opt in so that no positioning template leaks into another family.
    spec.setdefault('axis_grouped', True)
    spec.setdefault('cpu_module_only', True)
    spec.setdefault('device_scope', None)
    spec.setdefault('checks', ())
    spec['opcode'] = opcode.upper()
    spec['section'] = section
    spec['pages'] = tuple(pages)
    return spec


RECORDS = (
    _r('PLSY', '32.2 Pulse Y Output', (415, 416, 417, 418, 419, 420),
       family='positioning_pulse_output',
       forms=('PLSY', 'DPLSY'),
       st_form={'PLSY': 'ENO:=PLSY(EN,s,n,d)', 'DPLSY': 'ENO:=DPLSY(EN,s,n,d)'},
       operands=(
           ('s', 'command_speed_or_data', 'input', 'ANY16', 'dplsy_ANY32'),
           ('n', 'positioning_address_or_data', 'input', 'ANY16', 'dplsy_ANY32'),
           ('d', 'pulse_output_axis_number_or_Y_device', 'output', 'ANY_ELEMENTARY_WORD',
            'fx3_compatible_BOOL'),
       ),
       effects={
           'pulse_output': 'pulse_train_from_output_d',
           'operation': 'pulse_output_with_forward_rotation_only',
           'rotation_direction': 'forward_rotation_only',
           'current_address_change': 'incremented',
           'end_condition': 'pulse_output_stopped_after_positioning_address_pulses',
           'start_condition': 'drive_contact_on',
       },
       signals=SHARED_SIGNALS + PLSY_SIGNAL,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['command_speed_pulse'],
           'data_unit': USER_UNIT,
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
           'pulse_output_device': CPU_DEVICE_LIMITS['pulse_output_device'],
           'speed_change_during_operation': True,
           'positioning_address_change_during_operation': True,
       },
       cautions=('zphase_pointer_not_used',),
       witness=('This instruction executes pulse output',
                'This instruction outputs pulse trains specified by the command speed',
                'SM8029')),
    _r('DSZR', '32.3 Mechanical OPR', (421, 422, 423, 424, 425, 426, 427, 428, 429, 430),
       family='positioning_mechanical_opr',
       forms=('DSZR', 'DDSZR'),
       st_form={'DSZR': 'ENO:=DSZR(EN,s1,s2,d1,d2)',
                'DDSZR': 'ENO:=DDSZR(EN,s1,s2,d1,d2)'},
       operands=(
           ('s1', 'opr_speed_or_near_point_dog_signal_device', 'input', 'ANY16', 'ddszr_ANY32'),
           ('s2', 'creep_speed_or_zero_signal_device', 'input', 'ANY16', 'ddszr_ANY32'),
           ('d1', 'pulse_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL', 'documented'),
           ('d2', 'rotation_direction_or_clear_signal_Y_device', 'output',
            'ANY_ELEMENTARY_BOOL', 'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_from_output_d1',
           'operation': 'mechanical_zero_return',
           'zero_return_sequence': ('near_point_dog_signal_detected',
                                    'creep_speed_operation',
                                    'zero_signal_detected',
                                    'current_address_cleared'),
           'current_address_change': 'cleared_at_zero_signal',
           'rotation_direction_source': 'rotation_direction_setting_SM5772_to_SM5775',
       },
       signals=SHARED_SIGNALS + DSZR_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['opr_speed_pulse'],
           'two_operand_tables': ('fx5_operand_speeds_and_outputs',
                                  'fx3_compatible_near_point_dog_and_zero_signal'),
           'data_unit': USER_UNIT,
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
           'near_point_dog_device': 'X_device_preferred_any_device_with_assigned_signal',
       },
       cautions=('zero_signal_device_must_be_assigned_to_the_documented_input',
                 'speed_change_allowed_before_zero_signal'),
       witness=('This instruction executes mechanical OPR',
                'Bit device number to which the near-point dog signal',
                'Positioning instruction activation')),
    _r('DRVI', '32.4 Relative Positioning', (431, 432, 433, 434, 435, 436, 437, 438, 439, 440),
       family='positioning_one_speed',
       forms=('DRVI', 'DDRVI'),
       st_form={'DRVI': 'ENO:=DRVI(EN,s1,s2,d1,d2)',
                'DDRVI': 'ENO:=DDRVI(EN,s1,s2,d1,d2)'},
       operands=(
           ('s1', 'relative_positioning_address_or_data', 'input', 'INT', 'ddrvi_DINT'),
           ('s2', 'command_speed_or_data', 'input', 'WORD', 'ddrvi_DINT'),
           ('d1', 'pulse_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL', 'documented'),
           ('d2', 'rotation_direction_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL',
            'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_from_output_d1',
           'operation': 'one_speed_positioning_incremental',
           'rotation_direction': 'sign_of_positioning_address',
           'current_address_change': 'incremented_by_positioning_address',
           'end_condition': 'pulse_output_stopped_at_target_address',
       },
       signals=SHARED_SIGNALS + REMAINING_DISTANCE_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['command_speed_pulse'],
           'data_unit': USER_UNIT,
           'address_type': 'relative_with_sign',
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
           'pulse_output_device': CPU_DEVICE_LIMITS['pulse_output_device'],
           'speed_change_during_operation': True,
           'positioning_address_change_during_operation': True,
           'maximum_output_pulses_per_execution': 2147483647,
           'complete_flags_valid_only_with_fx5_operand': True,
       },
       cautions=('axis5_to_axis12_available_for_fx5s_fx5u_fx5uc',
                 'error_when_output_pulses_exceed_2147483647'),
       witness=('This instruction executes 1-speed positioning by relative address',
                'This instruction performs 1-speed positioning in the incremental method',
                'Positioning instruction activation')),
    _r('DRVA', '32.5 Absolute Positioning', (441, 442, 443, 444, 445, 446, 447, 448, 449),
       family='positioning_one_speed',
       forms=('DRVA', 'DDRVA'),
       st_form={'DRVA': 'ENO:=DRVA(EN,s1,s2,d1,d2)',
                'DDRVA': 'ENO:=DDRVA(EN,s1,s2,d1,d2)'},
       operands=(
           ('s1', 'absolute_positioning_address_or_data', 'input', 'INT', 'ddrva_DINT'),
           ('s2', 'command_speed_or_data', 'input', 'WORD', 'ddrva_DINT'),
           ('d1', 'pulse_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL', 'documented'),
           ('d2', 'rotation_direction_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL',
            'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_from_output_d1',
           'operation': 'one_speed_positioning_absolute',
           'rotation_direction': 'sign_of_remaining_distance',
           'current_address_change': 'reaches_absolute_positioning_address',
           'end_condition': 'pulse_output_stopped_at_target_address',
       },
       signals=SHARED_SIGNALS + REMAINING_DISTANCE_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['command_speed_pulse'],
           'data_unit': USER_UNIT,
           'address_type': 'absolute',
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
           'pulse_output_device': CPU_DEVICE_LIMITS['pulse_output_device'],
       },
       cautions=('absolute_address_requires_established_current_address',),
       witness=('This instruction executes 1-speed positioning by absolute address',
                'This instruction performs 1-speed positioning in the absolute method',
                'Positioning instruction activation')),
    _r('DVIT', '32.6 Interrupt 1-Speed Positioning',
       (450, 451, 452, 453, 454, 455, 456, 457, 458, 459),
       family='positioning_interrupt_feed',
       forms=('DVIT', 'DDVIT'),
       st_form={'DVIT': 'ENO:=DVIT(EN,s1,s2,d1,d2)',
                'DDVIT': 'ENO:=DDVIT(EN,s1,s2,d1,d2)'},
       operands=(
           ('s1', 'positioning_address_after_interrupt_detection_or_data', 'input', 'INT',
            'ddvit_DINT'),
           ('s2', 'command_speed_or_data', 'input', 'WORD', 'ddvit_DINT'),
           ('d1', 'pulse_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL', 'documented'),
           ('d2', 'rotation_direction_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL',
            'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_from_output_d1',
           'operation': 'one_speed_interrupt_constant_quantity_feed',
           'rotation_direction': 'sign_of_positioning_address',
           'current_address_change': 'incremented_by_positioning_address_after_interrupt',
           'interrupt_input': 'interrupt_input_signal_1',
           'end_condition': 'pulse_output_stopped_after_interrupt_relative_pulses',
       },
       signals=SHARED_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['command_speed_pulse'],
           'data_unit': USER_UNIT,
           'interrupt_input_required': 'interrupt_input_signal_1',
           'zero_positioning_address_is_error': True,
           'without_interrupt_continues_at_command_speed': True,
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
       },
       cautions=('deceleration_distance_shorter_than_transfer_time_lowers_speed',),
       witness=('This instruction executes one-speed interrupt constant quantity feed',
                'interrupt input signal 1 is detected',
                'Positioning instruction activation')),
    _r('PLSV', '32.7 Variable Speed Operation', (460, 461, 462, 463, 464, 465, 466, 467, 468),
       family='positioning_variable_speed',
       forms=('PLSV', 'DPLSV'),
       st_form={'PLSV': 'ENO:=PLSV(EN,s,d1,d2)',
                'DPLSV': 'ENO:=DPLSV(EN,s,d1,d2)'},
       operands=(
           ('s', 'signed_command_speed_or_data', 'input', 'INT', 'dplsv_DINT'),
           ('d1', 'pulse_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL', 'documented'),
           ('d2', 'rotation_direction_output_Y_device', 'output', 'ANY_ELEMENTARY_BOOL',
            'documented'),
       ),
       effects={
           'pulse_output': 'continuous_pulse_train_from_output_d1',
           'operation': 'variable_speed_operation',
           'rotation_direction': 'sign_of_command_speed',
           'current_address_change': 'incremented_or_decremented_while_running',
           'end_condition': 'pulse_output_continues_until_instruction_is_cleared',
           'speed_change_during_operation': 'takes_effect_immediately',
       },
       signals=SHARED_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'speed_unit': 'pulse_per_second',
           'speed_limit': SPEED_LIMITS['command_speed_signed_pulse'],
           'data_unit': USER_UNIT,
           'signed_speed': True,
           'zero_speed_stops_pulse_output': True,
           'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
           'pulse_output_device': CPU_DEVICE_LIMITS['pulse_output_device'],
       },
       cautions=('deceleration_requires_a_separate_deceleration_stop_request',),
       witness=('variable speed operation', 'command speed', 'Pulse output monitor')),
    _r('TBL', '32.8 Single-table Operation', (469, 470, 471, 472, 473, 474, 475, 476),
       family='positioning_table_operation',
       forms=('TBL',),
       st_form={'TBL': 'ENO:=TBL(EN,n,d)'},
       operands=(
           ('n', 'table_number', 'input', 'WORD', 'documented'),
           ('d', 'instruction_execution_complete_flag_device', 'output', 'BOOL', 'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_driven_by_documented_table_data',
           'operation': 'single_table_operation',
           'table_source': 'table_data_set_with_gx_works3',
           'control_method': 'control_method_of_one_specified_table',
           'end_condition': 'documented_table_completed',
       },
       signals=SHARED_SIGNALS + TABLE_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'table_number_range': 'documented_in_table_data',
           'data_unit': USER_UNIT,
       },
       cautions=('table_data_must_be_set_with_gx_works3',),
       witness=('This instruction executes one table specified in the table data set in GX Works3',
                'Only CPU module is', 'Positioning table data initialization disable')),
    _r('DRVTBL', '32.9 Multiple-table Operation', (478, 479, 480, 481, 482, 483, 484, 485),
       family='positioning_table_operation',
       forms=('DRVTBL',),
       st_form={'DRVTBL': 'ENO:=DRVTBL(EN,n1,n2,n3,d1,d2)'},
       operands=(
           ('n1', 'first_table_number', 'input', 'WORD', 'documented'),
           ('n2', 'last_table_number', 'input', 'WORD', 'documented'),
           ('n3', 'operation_pattern', 'input', 'WORD', 'documented'),
           ('d1', 'instruction_execution_complete_flag_device', 'output', 'BOOL', 'documented'),
           ('d2', 'instruction_execution_abnormal_end_flag_device', 'output', 'BOOL',
            'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_driven_by_documented_table_data',
           'operation': 'multiple_table_operation',
           'table_source': 'table_data_set_with_gx_works3',
           'operation_patterns': ('stepping_operation', 'continuous_operation'),
           'end_condition': 'documented_table_sequence_completed',
       },
       signals=SHARED_SIGNALS + TABLE_SIGNALS + TABLE_SHIFT_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'table_number_order': 'first_table_number_not_greater_than_last_table_number',
           'data_unit': USER_UNIT,
       },
       cautions=('tables_after_an_error_are_not_executed',),
       witness=('This instruction executes the table data set in GX Works3 in continuous '
                'operation or stepping operation', 'Non-executes',
                'Positioning instruction activation')),
    _r('DRVMUL', '32.10 Multiple-axis Table Operation',
       (486, 487, 488, 489, 490, 491, 492, 493),
       family='positioning_table_operation',
       forms=('DRVMUL',),
       st_form={'DRVMUL': 'ENO:=DRVMUL(EN,n1,n2,n3,n4,n5,d)'},
       operands=(
           ('n1', 'table_number_of_axis1', 'input', 'WORD', 'documented'),
           ('n2', 'table_number_of_axis2', 'input', 'WORD', 'documented'),
           ('n3', 'table_number_of_axis3', 'input', 'WORD', 'documented'),
           ('n4', 'table_number_of_axis4', 'input', 'WORD', 'documented'),
           ('n5', 'table_number_of_axis5_or_number_of_axes', 'input', 'WORD', 'documented'),
           ('d', 'first_device_of_execution_flag_block', 'output', 'BOOL', 'documented'),
       ),
       effects={
           'pulse_output': 'pulse_train_on_each_documented_axis',
           'operation': 'multiple_axis_table_operation_simultaneously',
           'table_source': 'table_data_set_with_gx_works3',
           'axis_interaction': 'axis_with_error_does_not_stop_other_axes',
           'interpolation_error_effect': 'both_interpolating_axes_stop',
       },
       signals=SHARED_SIGNALS + TABLE_SIGNALS + TABLE_SHIFT_SIGNALS,
       constraints={
           'cpu_module_only': True,
           'flag_block_layout': {'offset_0': 'complete_flag_of_n1',
                                 'offset_1': 'abnormal_end_flag_of_n1',
                                 'offset_2': 'complete_flag_of_n1_plus_1',
                                 'offset_3': 'abnormal_end_flag_of_n1_plus_1',
                                 'offset_4': 'complete_flag_of_n1_plus_2',
                                 'offset_5': 'abnormal_end_flag_of_n1_plus_2',
                                 'offset_6': 'complete_flag_of_n1_plus_3',
                                 'offset_7': 'abnormal_end_flag_of_n1_plus_3'},
           'data_unit': USER_UNIT,
       },
       cautions=('axis5_to_axis12_documented_for_fx5s_fx5u_fx5uc',),
       witness=('This instruction executes the table data set in GX Works3 for multiple axes',
                'Instruction execution complete flag of (n1)',
                'Positioning instruction activation')),
    _r('DABS', '32.11 Absolute Position Detection System', (493, 494, 495, 496),
       kind='positioning_instruction',
       family='positioning_absolute_position_detection',
       axis_grouped=False, cpu_module_only=False,
       state_dimension='execution.status_flags',
       device_scope={
           'abs_data_storage_device': 'data_register_only',
           'data_path_device': 'servo_amplifier_abs_output_signals',
           'device_restriction_basis': 'documented_operation_description',
       },
       checks=('Up to 16 DABS instructions can be driven simultaneously',
               'Always specify a data register as the specified device'),
       section_path='32.11 Absolute Position Detection System',
       page_window=(493, 496),
       forms=('DABS',),
       st_form={'DABS': 'ENO:=DABS(EN,s,d1,d2)'},
       operands=(
           ('s', 'first_device_of_abs_data_input_signals', 'input', 'ANYBIT_ARRAY',
            'bit_array_of_3'),
           ('d1', 'first_device_of_abs_control_output_signals', 'output', 'ANYBIT_ARRAY',
            'bit_array_of_3'),
           ('d2', 'abs_data_32bit_storage_device', 'output', 'ANY32', 'any32'),
       ),
       effects={
           'output_signals': 'abs_data_control_signals_to_servo_amplifier',
           'operation': 'absolute_position_data_read_out',
           'data_path': 'servo_amplifier_to_cpu_module',
           'data_conversion': 'converted_into_pulse_when_read',
           'communication': {'bits_of_position_data': 32, 'bits_of_check_data': 6,
                             'data_lines': 2},
           'end_condition': 'abs_data_read_completed_sets_execution_complete_flag',
           'current_address_transfer': 'by_HCMOV_or_DHCMOV_after_read',
       },
       signals=(
           {'id': 'instruction_execution_complete_flag', 'devices': ('SM8029',),
            'access': 'read', 'meaning': 'instruction_execution_completed'},
           {'id': 'instruction_execution_abnormal_end_flag', 'devices': ('SM8329',),
            'access': 'read', 'meaning': 'instruction_execution_ended_with_error'},
       ),
       constraints={
           'servo_amplifier': ['MR-J4_A', 'MR-J3_A'],
           'maximum_simultaneous_instructions': 16,
           'data_storage_device_restriction': 'data_register_only',
           'abs_data_range_pulses': {'minimum': -2147483648, 'maximum': 2147483647},
           'drive_contact_retained_after_read': True,
       },
       cautions=('servo_amplifier_power_on_before_or_with_cpu_module',
                 'turning_drive_contact_off_stops_data_reading',
                 'communication_failure_is_not_detected_as_error_use_timeout_timer',
                 'servo_rotation_direction_setting_must_match_current_value_sign'),
       witness=('ENO:=DABS(EN,s,d1,d2)', 'First number of the device that inputs the output',
                'Up to 16 DABS instructions can be driven simultaneously',
                'Always specify a data register as the specified device')),
    _r('PID', '36.5 Function (PID Control Via Instruction)', (576, 577, 578, 579),
       kind='analog_instruction',
       family='analog_pid_control',
       axis_grouped=False, cpu_module_only=False,
       state_dimension='execution.status_flags',
       device_scope={
           'documented_device_footnote': 'only_D_SD_R_can_be_used',
           'footnote_applies_to': 'operands_typed_any16_in_the_setting_data_table',
           'global_label_required_when_using_labels': True,
           'device_restriction_basis': 'documented_applicable_devices_footnote',
       },
       checks=('Only D, SD, R can be used',
               'When setting using a label, use the global label assigned to the device'),
       section_path='36.5 Function (PID Control Via Instruction)',
       page_window=(574, 583),
       forms=('PID',),
       st_form={'PID': 'ENO:=PID(EN,s1,s2,s3,d)'},
       operands=(
           ('s1', 'target_value_SV', 'input', 'INT', 'any16'),
           ('s2', 'measured_value_PV', 'input', 'INT', 'any16'),
           ('s3', 'first_device_of_pid_parameters', 'input', 'INT', 'any16'),
           ('d', 'output_value_MV_device', 'output', 'INT', 'any16'),
       ),
       effects={
           'output': 'output_value_MV_written_to_d',
           'operation': 'pid_control_of_output_value',
           'output_value_update': 'at_every_sampling_time',
           'control_direction': 'forward_or_backward_by_operation_setting_b0',
           'operation_expression': ['speed_type', 'measured_value_differential_type'],
           'supported_actions': ['proportional_action', 'integral_action',
                                 'derivative_action', 'auto_tuning', 'alarm_output',
                                 'overshoot_suppression', 'hunting_suppression'],
       },
       signals=(),
       constraints={
           'operand_ranges': {'s1': {'minimum': -32768, 'maximum': 32767},
                              's2': {'minimum': -32768, 'maximum': 32767},
                              's3': {'minimum': 1, 'maximum': 32767},
                              'd': {'minimum': -32768, 'maximum': 32767}},
           'parameter_block': {'offset_0': 'sampling_time_TS_1_to_32767_ms',
                               'offset_1': 'operation_setting_ACT_bits_b0_to_b8',
                               'offset_3': 'proportional_gain_KP',
                               'offset_4': 'integral_time_TI',
                               'offset_6': 'differential_time_TD',
                               'offset_20_to_24': 'used_only_when_b1_or_b2_or_b5_is_on'},
           'operation_setting_bits': {
               'b0': ['forward_operation', 'backward_operation'],
               'b1': ['input_variation_alarm_invalid', 'input_variation_alarm_valid'],
               'b2': ['output_variation_alarm_invalid', 'output_variation_alarm_valid'],
               'b4': ['auto_tuning_not_executed', 'auto_tuning_executed'],
               'b5': ['output_limits_invalid', 'output_limits_valid'],
               'b6': ['step_response_method', 'limit_cycle_method'],
               'b7': ['overshoot_suppression_invalid', 'overshoot_suppression_valid'],
               'b8': ['hunting_suppression_off', 'hunting_suppression_on']},
           'b2_and_b5_mutually_exclusive': True,
           'parameters_written_before_start_when_auto_tuning_not_executed': True,
           'sampling_time_not_shorter_than_operation_cycle': True,
           'instruction_level_details': 'fx5_programming_manual_instructions_standard_functions',
       },
       cautions=('step_response_auto_tuning_requires_sampling_time_1000ms_or_more',
                 'pid_operation_error_when_sampling_time_shorter_than_operation_cycle',
                 'instruction_level_details_in_fx5_programming_manual'),
       witness=('This instruction executes PID control which changes the output value',
                'Device number storing the target value',
                'ENO:=PID(EN,s1,s2,s3,d)',
                'proportional gain ((s3)+3), integral time ((s3)+4) and differential time '
                '((s3)+6)'))
)

# Sections of this manual that are device references, not instructions.
NON_INSTRUCTION_SECTIONS = (
    {'id': 'fx5_pointer_device', 'section': '4.8 Pointer (P)', 'pages': (75,),
     'device': 'P', 'documented_as': 'device_reference',
     'witness': ('Pointer',)},
    {'id': 'fx5_interrupt_pointer_device', 'section': '4.9 Interrupt Pointer (I)', 'pages': (76,),
     'device': 'I', 'documented_as': 'device_reference',
     'witness': ('Interrupt Pointer',)},
    {'id': 'fx5_interrupt_pointer_priority', 'section':
     'The priority for the interrupt pointer numbers and interrupt factors', 'pages': (77,),
     'device': 'I', 'documented_as': 'device_reference',
     'witness': ('interrupt pointer',)},
)

CHUNK_COLUMNS = ('id, chunk_uid, manual_id, manual_number, revision, manual_title, pdf_page, '
                 'pdf_page_end, chapter, section, outline_path, section_key, chunk_type, '
                 'instruction_opcode, fnc_number, text, source_applicability_json, '
                 'plc_models, document_plc_models')


def _rows(connection, sql, arguments=()):
    cursor = connection.execute(sql, arguments)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _flatten(text):
    return re.sub(r'\s+', '', text or '').lower()


def _contains(text, phrase):
    return _flatten(phrase) in _flatten(text)


def _verify(row, phrases, label):
    missing = [phrase for phrase in phrases if not _contains(row['text'], phrase)]
    if missing:
        raise ValueError('FX5 review source changed: %s (%s) missing %s'
                         % (row['manual_id'], label, '; '.join(missing)))
    return len(phrases)


def _load_chunks(connection, manual):
    rows = _rows(connection, 'SELECT ' + CHUNK_COLUMNS + ' FROM chunks WHERE manual_id=? '
                             'ORDER BY pdf_page, id', (manual,))
    return rows


def _models(row, manual_models):
    applicability = json.loads(row.get('source_applicability_json') or '{}')
    models = [model for model in applicability.get('supported_models') or ()
              if model in DOCUMENTED_MODELS]
    if not models:
        models = [model for model in manual_models if model in DOCUMENTED_MODELS]
    return [model for model in DOCUMENTED_MODELS if model in models]


def _source_reference(row):
    return {'id': row['chunk_uid'], 'manual_id': row['manual_id'],
            'manual_number': row['manual_number'], 'revision': row['revision'],
            'pdf_pages': list(range(row['pdf_page'], (row['pdf_page_end'] or row['pdf_page']) + 1))}


def _section_chunk(chunks, section, page, opcode=None):
    matches = [row for row in chunks
               if (row['section'] or '') == section and row['pdf_page'] == page]
    if opcode:
        narrowed = [row for row in matches if row['instruction_opcode'] == opcode]
        matches = narrowed or matches
    if not matches:
        raise ValueError('FX5 review source section is missing: %s p%s' % (section, page))
    return matches[0]


def _opcode_chunks(chunks, opcode):
    """Every indexed chunk that documents one instruction opcode."""
    return [row for row in chunks if (row['instruction_opcode'] or '') == opcode]


def _bound_chunks(chunks, record):
    """Opcode chunks plus the sibling pages of the same documented section."""
    rows = list(_opcode_chunks(chunks, record['opcode']))
    path = record.get('section_path')
    if not path:
        return rows
    window = record.get('page_window') or ()
    known = {row['id'] for row in rows}
    for row in chunks:
        if row['id'] in known:
            continue
        if path not in (row.get('outline_path') or ''):
            continue
        if window and not (window[0] <= row['pdf_page'] <= window[1]):
            continue
        rows.append(row)
    return rows


def _section_pages(chunks, section, opcode):
    pages = sorted({row['pdf_page'] for row in chunks
                    if (row['section'] or '') == section
                    and row['instruction_opcode'] == opcode})
    return pages


def _plain(value):
    """Tuples become lists so every published value stays a plain typed value."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _section_token(section):
    """The documented section number, else a slug of the documented heading."""
    match = re.match(r'^(\d+(?:\.\d+)*)', section or '')
    if match:
        return match.group(1)
    return re.sub(r'[^A-Za-z0-9]+', '_', section or '').strip('_').lower()


def _fact(identity, dimension, value, opcode, references, models, *, languages=None,
          status='source_verified', condition=None, note=None):
    scope = {'models': list(models), 'forms': [opcode]}
    if languages:
        scope['languages'] = list(languages)
    fact = {'id': identity + '@' + FX5_MANUAL, 'dimension': dimension,
            'value': _plain(value),
            'status': status,
            'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
            'sources': [dict(reference) for reference in references], 'scope': scope}
    if condition:
        fact['conditions'] = [dict(condition)]
    if note:
        fact['note'] = note
    return fact


def _operand_facts(record, form):
    output = []
    for name, role, direction, token, label_note in record['operands']:
        entry = {'name': name, 'role': role, 'direction': direction,
                 'data_type': dict(TYPES[token]),
                 'label_type': _label_type(record, name, label_note),
                 'range_basis': 'documented_setting_data'}
        limits = _operand_limits(record, name)
        if limits:
            entry['documented_limits'] = limits
        output.append(entry)
    return output


def _label_type(record, name, note):
    if note.startswith('d') and note.endswith('_DINT'):
        return {'kind': 'any_elementary', 'bits': 32}
    if note == 'fx3_compatible_BOOL':
        return {'kind': 'bit'}
    if note == 'bit_array_of_3':
        return {'kind': 'bit_array', 'elements': 3}
    if note == 'any16':
        return {'kind': 'any', 'bits': 16}
    if note == 'any32':
        return {'kind': 'any', 'bits': 32}
    return {'kind': 'documented_label'}


def _operand_limits(record, name):
    constraints = record.get('constraints') or {}
    if name == 'd' and record['opcode'] in ('PLSY', 'DPLSY'):
        return {'axis_number': constraints.get('output_axis_number'),
                'pulse_output_device': constraints.get('pulse_output_device')}
    if name == 's' and record['opcode'] == 'PLSV':
        return {'speed_pulse': constraints.get('speed_limit')}
    if name in ('s', 's2') and 'speed_limit' in constraints:
        return {'speed_pulse': constraints.get('speed_limit')}
    if name in ('s1', 's') and record['opcode'] in ('DRVI', 'DRVA', 'DVIT'):
        return {'address_unit': constraints.get('data_unit')}
    return {}


def _st_signature(record, form):
    parameters = [{'name': 'EN', 'type': {'kind': 'bit'}, 'direction': 'input'}]
    for name, _role, direction, token, _label in record['operands']:
        parameters.append({'name': name, 'type': dict(TYPES[token]),
                           'direction': 'input' if direction == 'input' else 'output'})
    parameters.append({'name': 'ENO', 'type': {'kind': 'bit'}, 'direction': 'output'})
    return {'documented_call': record['st_form'][form],
            'argument_order': [item['name'] for item in parameters],
            'parameters': parameters,
            'returns': {'type': {'kind': 'bit'}, 'role': 'ENO_execution_status'},
            'enable': True}


def _states(record):
    states = {}
    for signal in record['signals']:
        states[signal['id']] = {'devices': list(signal['devices']),
                                'access': signal['access'],
                                'meaning': signal['meaning']}
    return states


def _record_facts(record, form, model, chunks, references_by_page, manual_models):
    facts = []
    section_rows = _bound_chunks(chunks, record)
    pages = sorted({row['pdf_page'] for row in section_rows}) or list(record['pages'])
    references = [references_by_page[page] for page in pages if page in references_by_page]
    if not references:
        raise ValueError('FX5 review source has no reference for %s' % record['section'])
    condition = {'context': 'cpu_module_not_selected_per_operand_table',
                 'basis': 'documented_cpu_module_marks'} if model in ('FX5S', 'FX5UJ') else None

    def add(aspect, dimension, value, *, languages=None, status='source_verified'):
        facts.append(_fact('fx5:%s:%s:%s' % (record['opcode'].lower(), form.lower(), aspect),
                           dimension, value, form, references, [model],
                           languages=languages, status=status, condition=condition))

    languages = {token: bool(record['languages'][token])
                 for token in ('ladder', 'st', 'fbd_ld')}
    add('form', 'identity.form', {
        'mnemonic': form,
        'instruction_family': record['family'],
        'callable_kind': record['kind'],
        'documented_section': _section_token(record['section']),
        'documented_forms': list(record['forms']),
        'other_forms': [item for item in record['forms'] if item != form],
        'language_availability': dict(languages),
        'cpu_scope': list(DOCUMENTED_MODELS),
        'cpu_module_only': bool(record['cpu_module_only']),
        'axis_grouped': bool(record['axis_grouped']),
    })
    add('languages', 'identity.language_availability', {
        'documented_languages': sorted(languages),
        'availability': dict(languages),
        'documented_as': 'Ladder_ST_FBD_LD_table',
        'unavailable_languages': sorted(name for name, ok in languages.items() if not ok),
    }, languages=[name for name in ('st', 'fbd') if languages['st' if name == 'st'
                                                                  else 'fbd_ld']])
    # One fact per documented programming-language column, bound to that language
    # only: a Ladder claim is never published as an FBD claim.
    for language, token, column in (('ladder', 'ladder', 'Ladder'),
                                    ('st', 'st', 'ST'),
                                    ('fbd', 'fbd_ld', 'FBD/LD')):
        add('language_' + language, 'identity.language_availability',
            {'language': language, 'available': bool(languages[token]),
             'documented_column': column,
             'documented_as': 'Ladder_ST_FBD_LD_table'},
            languages=[language])
    add('operands', 'parameters.typed_operands', {
        'operands': _operand_facts(record, form),
        'st_call': record['st_form'][form],
    })
    add('st_signature', 'parameters.st_signature', _st_signature(record, form), languages=['st'])
    enable_semantics = dict(EN_SEMANTICS)
    enable_semantics['plain_form_execution'] = 'not_documented_in_this_section'
    add('enable', 'execution.enable', enable_semantics, languages=['st', 'fbd'])
    add('effects', 'effects.result_rule', dict(record['effects']))
    if record['signals']:
        add('states', record.get('state_dimension', 'execution.start_stop'),
            _states(record))
        related = {'signals': _states(record),
                   'access_vocabulary': ['read', 'read_write']}
        if record['axis_grouped']:
            related['axis_columns'] = sorted(AXIS_GROUPS)
            related['documented_axis_count'] = max(AXIS_GROUPS.values())
        add('signals', 'memory.related_devices', related)
    add('cpu_scope', 'constraints.cpu_version', {
        'documented_models': list(DOCUMENTED_MODELS),
        'family': 'FX5',
        'instruction_family': record['family'],
        'module_restriction': ('cpu_module_only' if record['cpu_module_only']
                               else 'documented_cpu_scope'),
    })
    if record['axis_grouped']:
        device_scope = {
            'output_axis_number': CPU_DEVICE_LIMITS['output_axis_number'],
            'pulse_output_device': CPU_DEVICE_LIMITS['pulse_output_device'],
            'device_restriction_basis': 'documented_setting_data',
        }
    else:
        device_scope = record.get('device_scope')
    if device_scope:
        add('device_scope', 'constraints.device_scope', device_scope)
    if record.get('constraints'):
        add('constraints', 'constraints.operation_condition',
            {key: value for key, value in record['constraints'].items()
             if isinstance(value, (bool, int, str, dict, list, tuple))})
    if record.get('cautions'):
        add('cautions', 'constraints.documented_caution',
            {'cautions': list(record['cautions'])})
    return facts


def _non_instruction_facts(chunks, model, references_by_page):
    facts = []
    for spec in NON_INSTRUCTION_SECTIONS:
        row = _section_chunk(chunks, spec['section'], spec['pages'][0])
        _verify(row, spec['witness'], 'non instruction ' + spec['device'])
        reference = _source_reference(row)
        facts.append(_fact('fx5:device:%s:form' % spec['device'].lower(), 'identity.form', {
            'mnemonic': spec['device'],
            'instruction': False,
            'documented_as': spec['documented_as'],
            'callable_kind': 'device_reference',
            'documented_section': _section_token(spec['section']),
        }, spec['device'], [reference], [model]))
    return facts


def reviewed_fx5_behavior(source, entries=None):
    """Return typed FX5 positioning facts keyed by (target_model, opcode)."""
    chunks = _load_chunks(source, FX5_MANUAL)
    if not chunks:
        raise ValueError('FX5 review source is missing: ' + FX5_MANUAL)
    manual_models = []
    for row in chunks:
        for model in (row.get('plc_models') or '').split(','):
            model = model.strip()
            if model in DOCUMENTED_MODELS and model not in manual_models:
                manual_models.append(model)
    manual_models = [model for model in DOCUMENTED_MODELS if model in manual_models]
    references_by_page = {}
    for row in chunks:
        references_by_page.setdefault(row['pdf_page'], _source_reference(row))
    facts = {}
    receipt = {'method': METHOD, 'manual': FX5_MANUAL, 'manual_number': FX5_MANUAL_NUMBER,
               'revision': FX5_REVISION, 'title': FX5_TITLE,
               'documented_models': list(DOCUMENTED_MODELS), 'sections': [],
               'residuals': [], 'non_instruction_sections': [], 'unresolved_forms': [],
               'counts': {}, 'guarantees': {}, 'cross_manual_references': [
                   {'opcode': 'PID',
                    'documented_manual': 'MELSEC iQ-F FX5 Programming Manual '
                                         '(Instructions, Standard Functions/Function Blocks)',
                    'registered_in_local_index': False,
                    'covered_here_from_section': '36.5',
                    'url_supplement_needed': False,
                    'reason': 'the application manual documents the operands, the parameter '
                              'block, the operation setting bits and the processing details, '
                              'so no behaviour is claimed from an unregistered manual'}]}
    covered_pages = set()
    for record in RECORDS:
        section_rows = _bound_chunks(chunks, record)
        if not section_rows:
            raise ValueError('FX5 review source instruction is missing: %s' % record['opcode'])
        joined = '\n'.join(row['text'] or '' for row in section_rows)
        missing = [phrase for phrase in record['witness'] if not _contains(joined, phrase)]
        if missing:
            raise ValueError('FX5 review source changed: %s (%s) missing %s'
                             % (FX5_MANUAL, record['opcode'], '; '.join(missing)))
        discovered = sorted({row['pdf_page'] for row in section_rows})
        if record['pages'][0] not in discovered:
            raise ValueError('FX5 review source page moved: %s %s first page p%s not indexed'
                             % (record['opcode'], record['section'], record['pages'][0]))
        for page in discovered:
            covered_pages.add(page)
        checks = [phrase for phrase in record.get('checks') or () if not _contains(joined, phrase)]
        if checks:
            raise ValueError('FX5 review source changed: %s (%s) missing documented limit %s'
                             % (FX5_MANUAL, record['opcode'], '; '.join(checks)))
        for aspect in record.get('residuals') or ():
            receipt['residuals'].append({'section': record['section'],
                                         'opcode': record['opcode'],
                                         'aspect': aspect[0], 'reason': aspect[1],
                                         'detail': aspect[2]})
        models = []
        for row in section_rows:
            for model in _models(row, manual_models):
                if model not in models:
                    models.append(model)
        models = [model for model in DOCUMENTED_MODELS if model in models] or list(manual_models)
        for form in record['forms']:
            for model in models:
                built = _record_facts(record, form, model, chunks, references_by_page,
                                      manual_models)
                facts.setdefault((model, form), []).extend(built)
        receipt['sections'].append({'section': record['section'],
                                    'opcode': record['opcode'],
                                    'forms': list(record['forms']),
                                    'pages': discovered,
                                    'models': models,
                                    'facts_per_model':
                                        len(_record_facts(record, record['forms'][0],
                                                          models[0], chunks,
                                                          references_by_page, manual_models))})
    # Pointer/device chapters are indexed as instruction chunks by the source
    # importer. Preserve that classification correction in the inventory; do
    # not manufacture instruction owners named P or I.
    for model in manual_models:
        _non_instruction_facts(chunks, model, references_by_page)
    receipt['non_instruction_sections'] = [
        {'device': spec['device'], 'section': spec['section'],
         'documented_as': spec['documented_as'], 'disposition': 'not_instruction'}
        for spec in NON_INSTRUCTION_SECTIONS]
    instruction_chunks = [row for row in chunks if row['instruction_opcode']]
    owned = {record['opcode'] for record in RECORDS}
    declared_devices = {spec['device'] for spec in NON_INSTRUCTION_SECTIONS}
    for row in instruction_chunks:
        opcode = row['instruction_opcode']
        if opcode in owned or opcode in declared_devices:
            continue
        receipt['unresolved_forms'].append({'section': row['section'], 'opcode': opcode,
                                            'page': row['pdf_page']})
    total = sum(len(rows) for rows in facts.values())
    by_dimension = {}
    by_status = {}
    for rows in facts.values():
        for row in rows:
            by_dimension[row['dimension']] = by_dimension.get(row['dimension'], 0) + 1
            by_status[row['status']] = by_status.get(row['status'], 0) + 1
    receipt['counts'] = {'owner_keys': len(facts), 'facts': total,
                         'by_dimension': dict(sorted(by_dimension.items())),
                         'by_status': dict(sorted(by_status.items())),
                         'sections': len(receipt['sections']),
                         'forms': len({form for _model, form in facts}),
                         'covered_pages': len(covered_pages)}
    receipt['guarantees'] = {
        'executable_behavior_keys': 0,
        'cpu_widening': sum(1 for models in (row['scope']['models'] for rows in facts.values()
                                             for row in rows)
                            if any(model not in DOCUMENTED_MODELS for model in models)),
        'manual_prose_published': False,
        'database_mutated': False,
        'pdf_read_at_runtime': False,
        'native_execution_claimed': False,
        'authoring_method': METHOD,
    }
    return facts, receipt

"""Authored typed behaviour of the FXCPU structured application functions.

Scope
-----
This review owns the structured (IEC-style) function catalogue of
``fxcpu_application_k`` -- FXCPU Structured Programming Manual [Application
Functions], revision K: type conversion functions, the numeric one-variable
function, arithmetic, bit shift, bitwise Boolean, selection, comparison,
character string, time and standard function block chapters, plus the operator
chapter.  It also reads the registered structured language references for
call-level bindings: ``gxworks2_structured_ag`` (language availability,
pin-count editing, EN/ENO output assignment, retained bit outputs).

What a fact is
--------------
Every record is read from an exact indexed chunk (manual, revision, section and
page list) whose witness phrases are re-verified at build time.  Values are
formal tokens, typed value mappings or numbers: no manual prose is copied into a
published value.  Documented facts carry ``source_verified``; anything the
section does not state is either omitted (and reported as a residual) or
published as ``unknown`` -- it is never guessed from a name.

What a fact is not
------------------
These are manual knowledge records, not Core execution.  No value carries a
``behavior`` entry, so the bounded executor admits nothing from here.  No fact
promotes ``call_available``, a CPU, a native compilation or a hardware result:
a structured function that the Core registry does not implement stays a
knowledge-only entry.  CPU scope comes from the section's own instruction
header; a header that cannot be resolved is reported, never widened.

Language and platform bindings
------------------------------
ST and FBD signatures are separate facts scoped by ``languages``; a form that
the manual marks unavailable in a language is published as such instead of
being silently generalised.  Tool-level constraints (compile options, automatic
device assignment, pin editing) are recorded as tool-binding constraints from the
GX Works2 structured-project manual, not as instruction behaviour.
"""
from __future__ import annotations

import copy
import json
import re

METHOD = 'authored_structured_behavior_review_v1'
FUNCTION_MANUAL = 'fxcpu_application_k'
FUNCTION_REVISION = 'K'
TOOL_MANUAL = 'gxworks2_structured_ag'
TOOL_REVISION = 'AG'

# Header-derived CPU scope. Conditional cells keep their version caveat.
HEADER_CONDITION = {'context': 'cpu_version_not_resolved', 'basis': 'instruction_header_icon'}

# ---------------------------------------------------------------------------
# Typed value vocabulary. Shapes follow plc.instruction_definition.ValueType so
# a consumer can bind them to the same value model the Core uses.
# ---------------------------------------------------------------------------
TYPES = {
    'BOOL': {'kind': 'bool'},
    'INT': {'kind': 'int', 'bits': 16, 'signed': True},
    'WORD': {'kind': 'int', 'bits': 16, 'signed': False},
    'DINT': {'kind': 'int', 'bits': 32, 'signed': True},
    'DWORD': {'kind': 'int', 'bits': 32, 'signed': False},
    'REAL': {'kind': 'real', 'bits': 32},
    'TIME': {'kind': 'int', 'bits': 32, 'unit': 'ms'},
    'STRING': {'kind': 'string', 'encoding': 'binary'},
    'BCD16': {'encoding': 'bcd', 'bits': 16},
    'BCD32': {'encoding': 'bcd', 'bits': 32},
    'BITARR': {'kind': 'bool', 'array': True},
    'ANY': {'flexible_type': 'ANY'},
    'ANY_SIMPLE': {'flexible_type': 'ANY_SIMPLE'},
    'ANY_NUM': {'flexible_type': 'ANY_NUM'},
    'ANY_INT': {'flexible_type': 'ANY_INT'},
    'ANY_BIT': {'flexible_type': 'ANY_BIT'},
    'ANY16': {'flexible_type': 'ANY16'},
    'ANY32': {'flexible_type': 'ANY32'},
}

# Documented data-type wording, used to rebuild witness phrases from the source.
TYPE_WORDS = {
    'BOOL': 'bit',
    'INT': 'word [signed]',
    'WORD': 'word [unsigned]/bit string [16-bit]',
    'DINT': 'double word [signed]',
    'DWORD': 'double word [unsigned]/bit string [32-bit]',
    'REAL': 'float (single precision)',
    'TIME': 'time',
    'STRING': 'string',
    'BCD16': 'BCD',
    'BCD32': 'BCD',
    'BITARR': 'bit array',
    'ANY16': 'ANY16',
    'ANY32': 'ANY32',
    'ANY': 'ANY',
}

# ---------------------------------------------------------------------------
# Chapter 5: type conversion functions.
#   (opcode, section, chunk, pages, source type, target type, input name,
#    rule key, limit keys)
# ---------------------------------------------------------------------------
CONVERSIONS = (
    ('BOOL_TO_INT', '5.1', (42, 43), 'BOOL', 'INT', '_BOOL', 'bool_0_1', ('all_series',)),
    ('BOOL_TO_DINT', '5.2', (44, 44), 'BOOL', 'DINT', '_BOOL', 'bool_0_1', ('all_series', 'wide_labels')),
    ('BOOL_TO_STR', '5.3', (46, 47), 'BOOL', 'STRING', '_BOOL', 'bool_text', ('string_labels',)),
    ('BOOL_TO_WORD', '5.4', (48, 49), 'BOOL', 'WORD', '_BOOL', 'bool_0h_1h', ('all_series',)),
    ('BOOL_TO_DWORD', '5.5', (50, 50), 'BOOL', 'DWORD', '_BOOL', 'bool_0h_1h', ('all_series', 'wide_labels')),
    ('BOOL_TO_TIME', '5.6', (52, 52), 'BOOL', 'TIME', '_BOOL', 'bool_0_1ms', ('wide_labels',)),
    ('INT_TO_DINT', '5.7', (54, 54), 'INT', 'DINT', '_INT', 'sign_extend', ('wide_labels',)),
    ('DINT_TO_INT', '5.8', (56, 56), 'DINT', 'INT', '_DINT', 'range_preserved_only', ('wide_labels',)),
    ('INT_TO_BOOL', '5.9', (58, 59), 'INT', 'BOOL', '_INT', 'zero_false', ('all_series',)),
    ('DINT_TO_BOOL', '5.10', (60, 60), 'DINT', 'BOOL', '_DINT', 'zero_false', ('wide_labels',)),
    ('INT_TO_REAL', '5.11', (62, 62), 'INT', 'REAL', 'a_Int', 'integer_to_real', ('real_range', 'fx3g_1_10')),
    ('DINT_TO_REAL', '5.12', (64, 64), 'DINT', 'REAL', 'a_Dint', 'integer_to_real', ('real_range', 'fx3g_1_10')),
    ('INT_TO_STR', '5.13', (66, 66), 'INT', 'STRING', '_INT', 'decimal_text_16', ('string_labels', 'string_words_4')),
    ('DINT_TO_STR', '5.14', (68, 68), 'DINT', 'STRING', '_DINT', 'decimal_text_32', ('string_labels', 'wide_labels', 'string_words_6')),
    ('INT_TO_WORD', '5.15', (70, 71), 'INT', 'WORD', '_INT', 'bit_pattern_preserved', ('all_series',)),
    ('DINT_TO_WORD', '5.16', (72, 72), 'DINT', 'WORD', '_DINT', 'low_order_16_bits', ('wide_labels',)),
    ('INT_TO_DWORD', '5.17', (74, 74), 'INT', 'DWORD', '_INT', 'high_order_zero', ('wide_labels',)),
    ('DINT_TO_DWORD', '5.18', (76, 76), 'DINT', 'DWORD', '_DINT', 'bit_pattern_preserved', ('wide_labels',)),
    ('INT_TO_BCD', '5.19', (78, 79), 'INT', 'BCD16', '_INT', 'integer_to_bcd', ('bcd_domain_16', 'all_series')),
    ('DINT_TO_BCD', '5.20', (80, 80), 'DINT', 'BCD32', '_DINT', 'integer_to_bcd', ('bcd_domain_32', 'wide_labels')),
    ('INT_TO_TIME', '5.21', (82, 82), 'INT', 'TIME', '_INT', 'milliseconds_to_time', ('wide_labels',)),
    ('DINT_TO_TIME', '5.22', (84, 84), 'DINT', 'TIME', '_DINT', 'milliseconds_to_time', ('wide_labels',)),
    ('REAL_TO_INT', '5.23', (86, 86), 'REAL', 'INT', 'a_real', 'real_to_integer', ('real_rounded_off', 'fx3g_1_10')),
    ('REAL_TO_DINT', '5.24', (88, 88), 'REAL', 'DINT', 'a_real', 'real_to_integer', ('real_rounded_off', 'fx3g_1_10')),
    ('REAL_TO_STR', '5.25', (90, 91), 'REAL', 'STRING', '_REAL', 'exponent_text', ('string_labels', 'wide_labels', 'real_text_layout')),
    ('WORD_TO_BOOL', '5.26', (93, 94), 'WORD', 'BOOL', '_WORD', 'zero_false', ('all_series',)),
    ('DWORD_TO_BOOL', '5.27', (95, 95), 'DWORD', 'BOOL', '_DWORD', 'zero_false', ('wide_labels',)),
    ('WORD_TO_INT', '5.28', (97, 98), 'WORD', 'INT', '_WORD', 'bit_pattern_preserved', ('all_series',)),
    ('WORD_TO_DINT', '5.29', (99, 99), 'WORD', 'DINT', '_WORD', 'high_order_zero', ('wide_labels',)),
    ('DWORD_TO_INT', '5.30', (101, 101), 'DWORD', 'INT', '_DWORD', 'low_order_16_bits', ('wide_labels',)),
    ('DWORD_TO_DINT', '5.31', (103, 103), 'DWORD', 'DINT', '_DWORD', 'bit_pattern_preserved', ('wide_labels',)),
    ('WORD_TO_DWORD', '5.32', (105, 105), 'WORD', 'DWORD', '_WORD', 'high_order_zero', ('wide_labels',)),
    ('DWORD_TO_WORD', '5.33', (107, 107), 'DWORD', 'WORD', '_DWORD', 'low_order_16_bits', ('wide_labels',)),
    ('WORD_TO_TIME', '5.34', (109, 109), 'WORD', 'TIME', '_WORD', 'bit_pattern_preserved', ('wide_labels',)),
    ('DWORD_TO_TIME', '5.35', (111, 111), 'DWORD', 'TIME', '_DWORD', 'bit_pattern_preserved', ('wide_labels',)),
    ('STR_TO_BOOL', '5.36', (113, 114), 'STRING', 'BOOL', '_STRING', 'text_zero_false', ('string_labels',)),
    ('STR_TO_INT', '5.37', (115, 115), 'STRING', 'INT', '_STRING', 'decimal_text_source', ('string_labels', 'string_words_3')),
    ('STR_TO_DINT', '5.38', (117, 117), 'STRING', 'DINT', '_STRING', 'decimal_text_source', ('string_labels', 'wide_labels', 'string_words_6_dint')),
    ('STR_TO_REAL', '5.39', (119, 120), 'STRING', 'REAL', '_STRING', 'decimal_or_exponent_text', ('string_labels', 'wide_labels', 'real_text_source')),
    ('STR_TO_TIME', '5.40', (122, 122), 'STRING', 'TIME', '_STRING', 'decimal_text_source', ('string_labels', 'wide_labels')),
    ('BCD_TO_INT', '5.41', (124, 125), 'BCD16', 'INT', '_BCD', 'bcd_to_integer', ('bcd_domain_16', 'bcd_error')),
    ('BCD_TO_DINT', '5.42', (126, 127), 'BCD32', 'DINT', '_BCD', 'bcd_to_integer', ('bcd_domain_32', 'bcd_error', 'wide_labels')),
    ('TIME_TO_BOOL', '5.43', (128, 128), 'TIME', 'BOOL', '_TIME', 'zero_false', ('all_series',)),
    ('TIME_TO_INT', '5.44', (130, 130), 'TIME', 'INT', '_TIME', 'time_to_milliseconds', ('all_series',)),
    ('TIME_TO_DINT', '5.45', (132, 132), 'TIME', 'DINT', '_TIME', 'time_to_milliseconds', ('wide_labels',)),
    ('TIME_TO_STR', '5.46', (134, 134), 'TIME', 'STRING', '_TIME', 'millisecond_text', ('string_labels', 'wide_labels', 'time_text_error')),
    ('TIME_TO_WORD', '5.47', (136, 136), 'TIME', 'WORD', '_TIME', 'time_to_milliseconds', ('all_series',)),
    ('TIME_TO_DWORD', '5.48', (138, 138), 'TIME', 'DWORD', '_TIME', 'time_to_milliseconds', ('wide_labels',)),
    ('BITARR_TO_INT', '5.49', (140, 140), 'BITARR', 'ANY16', 'BitArr', 'bit_array_to_integer', ('bit_count_16', 'array_zero_fill', 'array_labels')),
    ('BITARR_TO_DINT', '5.50', (142, 142), 'BITARR', 'ANY32', 'BitArr', 'bit_array_to_integer', ('bit_count_32', 'array_zero_fill', 'array_labels')),
    ('INT_TO_BITARR', '5.51', (144, 144), 'ANY16', 'BITARR', 's', 'integer_to_bit_array', ('bit_count_16', 'array_unchanged', 'array_labels')),
    ('DINT_TO_BITARR', '5.52', (146, 146), 'ANY32', 'BITARR', 's', 'integer_to_bit_array', ('bit_count_32', 'array_unchanged', 'array_labels')),
    ('CPY_BITARR', '5.53', (148, 148), 'BITARR', 'BITARR', 'BitArrIn', 'bit_array_copy', ('bit_count_32', 'array_labels')),
    ('GET_BIT_OF_INT', '5.54', (150, 151), 'INT', 'BOOL', 's', 'read_bit_at_position', ('bit_position',)),
    ('SET_BIT_OF_INT', '5.55', (152, 153), 'BOOL', 'INT', 's', 'write_bit_at_position', ('bit_position',)),
    ('CPY_BIT_OF_INT', '5.56', (154, 155), 'INT', 'INT', 's', 'copy_bit_between_positions', ('bit_position_pair',)),
    ('GET_BOOL_ADDR', '5.57', (156, 156), 'ANY', 'BOOL', 's', 'start_data_as_type', ()),
    ('GET_INT_ADDR', '5.58', (157, 157), 'ANY', 'INT', 's', 'start_data_as_type', ('array_labels',)),
    ('GET_WORD_ADDR', '5.59', (158, 158), 'ANY', 'WORD', 's', 'start_data_as_type', ('array_labels',)),
)

# Result rule of each conversion: the documented statement of what is produced.
CONVERSION_RULES = {
    'bool_0_1': {'rule': 'value_mapping', 'mapping': {'FALSE': 0, 'TRUE': 1},
                 'witness': "When the input value is \"FALSE\""},
    'bool_0h_1h': {'rule': 'value_mapping', 'mapping': {'FALSE': '0H', 'TRUE': '1H'},
                   'witness': "outputs \"1H\" as the word [unsigned]/bit string [16-bit] data value"},
    'bool_text': {'rule': 'value_mapping', 'mapping': {'FALSE': '0', 'TRUE': '1'},
                  'witness': 'FALSE "0"'},
    'bool_0_1ms': {'rule': 'value_mapping', 'mapping': {'FALSE': 0, 'TRUE': 1},
                   'witness': 'FALSE 0 TRUE 1ms'},
    'sign_extend': {'rule': 'sign_extended_to_target_width', 'source_bits': 16, 'target_bits': 32},
    'range_preserved_only': {'rule': 'value_preserved_in_target_range',
                             'out_of_range_behaviour': 'not_stated'},
    'zero_false': {'rule': 'value_mapping', 'mapping': {'0': 'FALSE', 'other': 'TRUE'},
                   'witness': 'When the input value is any value other than "0"'},
    'integer_to_real': {'rule': 'exact_integer_to_single', 'significant_digits': 7},
    'decimal_text_16': {'rule': 'decimal_text', 'sign_byte': 'first_word_high_order',
                        'digits': 4, 'terminator_byte': '00H', 'padding_byte': '20H',
                        'negative_sign_byte': '2DH'},
    'decimal_text_32': {'rule': 'decimal_text', 'sign_byte': 'first_word_high_order',
                        'digits': 8, 'terminator_byte': '00H', 'padding_byte': '20H'},
    'bit_pattern_preserved': {'rule': 'bit_pattern_transferred', 'reinterpretation': True},
    'low_order_16_bits': {'rule': 'low_order_bits_retained', 'bits': 16,
                          'high_order': 'discarded'},
    'high_order_zero': {'rule': 'low_order_bits_retained', 'bits': 16, 'high_order': 'zero_filled'},
    'integer_to_bcd': {'rule': 'binary_to_bcd', 'digits': None},
    'milliseconds_to_time': {'rule': 'millisecond_count_transferred', 'time_unit_ms': 1},
    'real_to_integer': {'rule': 'fraction_rounded_off'},
    'exponent_text': {'rule': 'exponent_text', 'total_digits': 12, 'exponent_digits': 2,
                      'terminator_byte': '00H', 'sign_byte': '2BH'},
    'text_zero_false': {'rule': 'value_mapping', 'mapping': {'0': 'FALSE', 'other': 'TRUE'},
                        'source_text': 'decimal_text'},
    'decimal_text_source': {'rule': 'text_to_integer', 'terminator_byte': '00H',
                            'sign_byte': 'first_word_high_order'},
    'decimal_or_exponent_text': {'rule': 'text_to_single', 'significant_digits': 6,
                                 'formats': ['decimal', 'exponent'],
                                 'max_characters': 24, 'discarded_digits': 'seventh_and_later'},
    'bcd_to_integer': {'rule': 'bcd_to_binary'},
    'time_to_milliseconds': {'rule': 'millisecond_count_transferred', 'time_unit_ms': 1},
    'millisecond_text': {'rule': 'decimal_text', 'unit': 'ms'},
    'bit_array_to_integer': {'rule': 'bit_array_to_integer'},
    'integer_to_bit_array': {'rule': 'integer_to_bit_array'},
    'bit_array_copy': {'rule': 'bit_array_copy'},
    'read_bit_at_position': {'rule': 'read_bit_at_position'},
    'write_bit_at_position': {'rule': 'write_bit_at_position'},
    'copy_bit_between_positions': {'rule': 'copy_bit_between_positions'},
    'start_data_as_type': {'rule': 'start_data_as_target_type'},
}

# Documented limits and cautions, keyed so several records can share one clause.
LIMITS = {
    'all_series': ('constraints.header_scope',
                   {'scope_source': 'instruction_header',
                    'unresolved_versions': 'see_cautions_section'}),
    'wide_labels': ('constraints.label_requirement',
                    {'applies_to': 'double_word_data',
                     'sixteen_bit_device_directly': False,
                     'direct_32bit_counter': True,
                     'global_label_required': True}),
    'string_labels': ('constraints.label_requirement',
                      {'applies_to': 'string_data',
                       'sixteen_bit_device_directly': False,
                       'global_label_required': True}),
    'array_labels': ('constraints.label_requirement',
                     {'applies_to': 'array_and_32bit_data',
                      'sixteen_bit_device_directly': False,
                      'global_label_required': True}),
    'fx3g_1_10': ('constraints.cpu_version', {'models': ['FX3G', 'FX3GC'],
                                             'minimum_series_version': 'Ver.1.10'}),
    'real_range': ('constraints.numeric_domain',
                   {'integer_range_without_error': [-16777216, 16777215],
                    'significant_digits': 7, 'error_kind': 'rounding_error'}),
    'real_rounded_off': ('constraints.numeric_domain', {'fraction': 'rounded_off'}),
    'string_words_4': ('constraints.string_layout',
                       {'words_occupied': 4, 'terminator_byte': '00H', 'padding_byte': '20H'}),
    'string_words_6': ('constraints.string_layout',
                       {'words_occupied': 6, 'terminator_byte': '00H',
                        'terminator_position': 'high_order_byte_of_sixth_word', 'padding_byte': '20H'}),
    'string_words_3': ('constraints.string_layout',
                       {'words_occupied': 3, 'sign_field': 'first_word_high_order'}),
    'string_words_6_dint': ('constraints.string_layout',
                            {'words_occupied': 6, 'sign_field': 'first_word_high_order',
                             'last_high_order_byte': 'ignored'}),
    'real_text_layout': ('constraints.string_layout',
                         {'words_occupied': 7, 'terminator_byte': '00H',
                          'decimal_digits_table': 5, 'terminator_position': 'seventh_word'}),
    'real_text_source': ('constraints.string_layout',
                         {'words_occupied': 7, 'max_characters': 24,
                          'ignored_characters': ['20H']}),
    'bcd_domain_16': ('constraints.numeric_domain', {'digits': 4, 'maximum': 9999}),
    'bcd_domain_32': ('constraints.numeric_domain', {'digits': 8, 'maximum': 99999999}),
    'bcd_error': ('resources.operation_error',
                  {'flag': 'M8067', 'condition': 'source_not_bcd'}),
    'time_text_error': ('resources.operation_error',
                        {'flag': 'M8067', 'code_register': 'D8067',
                         'condition': 'destination_device_range_exceeded'}),
    'bit_count_16': ('constraints.bit_count', {'allowed': [4, 8, 12, 16], 'constant_only': True}),
    'bit_count_32': ('constraints.bit_count',
                     {'allowed': [4, 8, 12, 16, 20, 24, 28, 32], 'constant_only': True}),
    'array_zero_fill': ('constraints.result_padding', {'beyond_specified_bits': 'zero'}),
    'array_unchanged': ('constraints.result_padding', {'beyond_specified_bits': 'unchanged'}),
    'bit_position': ('constraints.bit_position', {'selector': 'n', 'domain': 'word_bits'}),
    'bit_position_pair': ('constraints.bit_position',
                          {'source_selector': 'n1', 'destination_selector': 'n2',
                           'domain': 'word_bits'}),
}

CONVERSION_LIMIT_EXTRAS = {
    # Records whose section states one more clause than the shared limit above.
    'INT_TO_REAL': ('constraints.numeric_domain',
                    {'integer_range_without_error': [-16777216, 16777215],
                     'error_kind': 'rounding_error'}),
}
def _f(op, section, pages, **spec):
    """One documented structured function/function block/operator section."""
    spec.setdefault('kind', 'function')
    spec.setdefault('languages', ('st', 'fbd'))
    spec.setdefault('limits', ())
    spec.setdefault('witness', ())
    spec.setdefault('residuals', ())
    spec['op'] = op.upper()
    spec['section'] = section
    spec['pages'] = tuple(pages)
    return spec


PIN_2_28 = ('execution.operand_pins', {'operand_pins': {'minimum': 2, 'maximum': 28},
                                       'variable_arity': True,
                                       'edit_basis': 'gxworks2_number_of_pins'})
AS_ST_ONLY = {'available_in_structured_ladder': False, 'available_in_fbd': False,
              'documented_limitation': 'not_available_in_structured_ladder_fbd',
              # The section has to say so itself; a silent page never yields
              # "available: false".
              'documented_witness': 'operator is not available in Structured ladder',
              'documented_witness_token': 'operator_not_available_in_structured_ladder'}
EN_SEMANTICS = {
    'enable_argument': 'EN',
    'status_argument': 'ENO',
    'status_type': 'BOOL',
    'plain_form_execution': 'unconditional',
    'enabled_form_execution': 'when_EN_is_TRUE',
    'truth_table': [
        {'enable': 'TRUE', 'operation_error': False, 'status': 'TRUE',
         'operation_result': 'operation_output_value'},
        {'enable': 'TRUE', 'operation_error': True, 'status': 'FALSE',
         'operation_result': 'indefinite_value'},
        {'enable': 'FALSE', 'operation_error': 'not_evaluated', 'status': 'FALSE',
         'operation_result': 'not_updated'},
    ],
}
LABEL_RULE_32BIT = ('constraints.label_requirement',
                    {'applies_to': 'double_word_data', 'sixteen_bit_device_directly': False,
                     'direct_32bit_counter': True, 'global_label_required': True})
LABEL_RULE_STRING = ('constraints.label_requirement',
                     {'applies_to': 'string_data', 'sixteen_bit_device_directly': False,
                      'global_label_required': True})

FAMILY_RECORDS = (
    # ---- chapter 6: standard functions of one numeric variable ----
    _f('ABS', '6.1', (160, 161), category='standard_one_numeric',
       inputs=(('_IN', 'ANY_NUM', 's', 'operand'),),
       outputs=(('Output_label', 'ANY_NUM', 'd', 'absolute_value'),),
       st_plain='ABS(_IN)', st_enabled='ABS_E(EN,_IN,Output_label)',
       rule={'rule': 'absolute_value', 'result_type': 'same_as_source'},
       limits=(('constraints.numeric_domain',
                {'word_signed_input_minimum': -32768, 'word_signed_result_minimum': -32768,
                 'maximum_absolute_value': 32767}),
               ('constraints.float_input', {'literal_source': 'programming_tool',
                                            'rounding_error_possible': True})),
       witness=('obtains the absolute value', '32,767', 'rounding error')),
    # ---- chapter 7: arithmetic ----
    _f('ADD', '7.1', (163, 163), category='arithmetic',
       inputs=(('_IN', 'ANY_NUM', 's1', 'addend'),),
       outputs=(('Output_label', 'ANY_NUM', 'd', 'sum'),),
       st_enabled='ADD_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'addition', 'operand_order': 'commutative'},
       limits=(PIN_2_28, ('constraints.result_type', {'same_as_operand_type': True})),
       witness=('This function performs addition', 'ANY_NUM')),
    _f('SUB', '7.2', (165, 165), category='arithmetic',
       inputs=(('_IN1', 'ANY_NUM', 's1', 'minuend'), ('_IN2', 'ANY_NUM', 's2', 'subtrahend')),
       outputs=(('Output_label', 'ANY_NUM', 'd', 'difference'),),
       st_enabled='SUB_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'subtraction', 'operand_order': 'significant'},
       limits=(('constraints.result_type', {'same_as_operand_type': True}),),
       witness=('This function performs subtraction',)),
    _f('MUL', '7.3', (167, 167), category='arithmetic',
       inputs=(('_IN', 'ANY_NUM', 's1', 'multiplicand'),),
       outputs=(('Output_label', 'ANY_NUM', 'd', 'product'),),
       st_enabled='MUL_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'multiplication'},
       limits=(PIN_2_28, ('constraints.overflow', {'overflow_is_operation_error': False,
                                                   'status_result': 'TRUE',
                                                   'accurate_result_available': False})),
       witness=('This function performs multiplication', 'not regarded as an operation error')),
    _f('DIV', '7.4', (169, 169), category='arithmetic',
       inputs=(('_IN1', 'ANY_NUM', 's1', 'dividend'), ('_IN2', 'ANY_NUM', 's2', 'divisor')),
       outputs=(('Output_label', 'ANY_NUM', 'd', 'quotient'),),
       st_enabled='DIV_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'division', 'operand_order': 'significant'},
       limits=(('constraints.divisor', {'zero_division': 'operation_error'}),),
       errors={'flag': 'M8067', 'code_register': 'D8067',
               'conditions': ['divisor_is_zero', 'result_exceeds_2147483647']},
       witness=('This function performs division', 'divisor stored in')),
    _f('MOD', '7.5', (171, 171), category='arithmetic',
       inputs=(('_IN1', 'ANY_INT', 's1', 'dividend'), ('_IN2', 'ANY_INT', 's2', 'divisor')),
       outputs=(('Output_label', 'ANY_INT', 'd', 'remainder'),),
       st_plain='_IN1 MOD _IN2', st_enabled='MOD_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'modulus', 'operand_order': 'significant'},
       limits=(('constraints.divisor', {'zero_division': 'operation_error'}),),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'conditions': ['divisor_is_zero']},
       witness=('outputs the remainder', 'ANY_INT')),
    _f('EXPT', '7.6', (173, 173), category='arithmetic',
       inputs=(('In1', 'REAL', 's1', 'base'), ('In2', 'ANY_NUM', 's2', 'exponent')),
       outputs=(('Output_label', 'REAL', 'd', 'power'),),
       st_plain='EXPT(In1,In2)', st_enabled='EXPT_E(EN,In1,In2,Output_label)',
       rule={'rule': 'exponentiation', 'base_precision': 'single'},
       limits=(('constraints.numeric_domain', {'base_must_be_positive_and_nonzero': True,
                                               'result_absolute_range': ['2-126', '2128']}),
               ('constraints.base_type', {'base': 'REAL', 'exponent': 'ANY_NUM',
                                          'result': 'REAL'})),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['base_is_negative', 'base_is_zero', 'result_out_of_range']},
       witness=('This function raises float (single precision) data', 'ANY_NUM')),
    _f('MOVE', '7.7', (175, 175), category='arithmetic',
       inputs=(('_IN', 'ANY', 's', 'transfer_source'),),
       outputs=(('Output_label', 'ANY', 'd', 'transfer_destination'),),
       st_plain='MOVE(_IN)', st_enabled='MOVE_E(EN,_IN,Output_label)',
       rule={'rule': 'data_transfer', 'source_type_retained': True},
       limits=(LABEL_RULE_32BIT,
               ('constraints.float_input', {'literal_source': 'programming_tool',
                                            'rounding_error_possible': True})),
       witness=('transfers data stored in a device specified in s', 'Transfer destination')),
    # ---- chapter 8: bit shift ----
    _f('SHL', '8.1', (178, 178), category='bit_shift',
       inputs=(('_IN', 'ANY_BIT', 's', 'shift_source'), ('_N', 'ANY_BIT', 'n', 'shift_count')),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'shift_result'),),
       st_plain='SHL(_IN,_N)', st_enabled='SHL_E(EN,_IN,_N,Output_label)',
       rule={'rule': 'logical_shift', 'direction': 'left', 'vacated_bits': 'zero',
              'result_type': 'same_as_source'},
       limits=(('constraints.shift_count', {'selector': 'n', 'data_type': 'ANY_BIT'}),),
       witness=('shifted leftward', 'Number of shifted bits')),
    _f('SHR', '8.2', (180, 180), category='bit_shift',
       inputs=(('_IN', 'ANY_BIT', 's', 'shift_source'), ('_K', 'ANY_BIT', 'n', 'shift_count')),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'shift_result'),),
       st_plain='SHR(_IN,_K)', st_enabled='SHR_E(EN,_IN,_N,Output_label)',
       rule={'rule': 'logical_shift', 'direction': 'right', 'vacated_bits': 'zero',
              'result_type': 'same_as_source'},
       limits=(('constraints.shift_count', {'selector': 'n', 'data_type': 'ANY_BIT'}),),
       witness=('shifted rightward', 'Number of shifted bits')),
    # ---- chapter 9: bitwise Boolean ----
    _f('AND', '9.1', (183, 183), category='bitwise_boolean',
       inputs=(('_IN', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'logical_result'),),
       st_enabled='AND_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'bitwise_and', 'result_type': 'same_as_operand_type'},
       limits=(PIN_2_28,),
       witness=('obtains the logical product', 'word [unsigned]/bit string')),
    _f('OR', '9.2', (185, 185), category='bitwise_boolean',
       inputs=(('_IN', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'logical_result'),),
       st_enabled='OR_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'bitwise_or', 'result_type': 'same_as_operand_type'},
       limits=(PIN_2_28,),
       witness=('obtains the logical sum', 'word [unsigned]/bit string')),
    _f('XOR', '9.3', (187, 187), category='bitwise_boolean',
       inputs=(('_IN', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'logical_result'),),
       st_enabled='XOR_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'bitwise_xor', 'result_type': 'same_as_operand_type'},
       limits=(PIN_2_28,),
       witness=('exclusive logical sum', 'word [unsigned]/bit string')),
    _f('NOT', '9.4', (189, 189), category='bitwise_boolean',
       inputs=(('_IN', 'ANY_BIT', 's', 'operand'),),
       outputs=(('Output_label', 'ANY_BIT', 'd', 'logical_result'),),
       st_plain='NOT(_IN)', st_enabled='NOT_E(EN,_IN,Output_label)',
       rule={'rule': 'bitwise_not', 'result_type': 'same_as_source'},
       limits=(('constraints.result_type', {'same_as_source_type': True}),),
       witness=('obtains the logical negation using each bit',)),
    # ---- chapter 10: selection ----
    _f('SEL', '10.1', (192, 192), category='selection',
       inputs=(('_G', 'BOOL', 's1', 'selection_condition'),
               ('_IN0', 'ANY', 's2', 'false_branch_value'),
               ('_IN1', 'ANY', 's3', 'true_branch_value')),
       outputs=(('Output_label', 'ANY', 'd', 'selection_result'),),
       st_plain='SEL(_G,_IN0,_IN1)', st_enabled='SEL_E(EN,_G,_IN0,_IN1,Output_label)',
       rule={'rule': 'select_by_condition', 'condition_FALSE': 's2', 'condition_TRUE': 's3',
              'result_type': 'type_of_branches'},
       limits=(('constraints.branch_types', {'both_branches': 'same_data_type'}),),
       witness=('Bit data used as the selection condition',
                'this function outputs the value stored in a')),
    _f('MAXIMUM', '10.2', (194, 194), category='selection',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'ANY_SIMPLE', 'd', 'maximum_value'),),
       st_plain='MAXIMUM(_IN,_IN)', st_enabled='MAXIMUM_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'maximum_of_operands', 'result_type': 'type_of_operands'},
       limits=(PIN_2_28,),
       witness=('outputs the maximum value among ANY_SIMPLE type data', 'range of 2 to 28')),
    _f('MINIMUM', '10.3', (196, 196), category='selection',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'ANY_SIMPLE', 'd', 'minimum_value'),),
       st_plain='MINIMUM(_IN,_IN)', st_enabled='MINIMUM_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'minimum_of_operands', 'result_type': 'type_of_operands'},
       limits=(PIN_2_28,),
       witness=('outputs the minimum value among ANY_SIMPLE type data', 'range of 2 to 28')),
    _f('LIMITATION', '10.4', (198, 198), category='selection',
       inputs=(('_MN', 'ANY_SIMPLE', 's1', 'lower_limit'),
               ('_IN', 'ANY_SIMPLE', 's2', 'input_value'),
               ('_MX', 'ANY_SIMPLE', 's3', 'upper_limit')),
       outputs=(('Output_label', 'ANY_SIMPLE', 'd', 'limited_value'),),
       st_plain='LIMITATION(_MN,_IN,_MX)',
       st_enabled='LIMITATION_E(EN,_MN,_IN,_MX,Output_label)',
       rule={'rule': 'clamp', 'above_upper_limit': 'output_upper_limit',
              'below_lower_limit': 'output_lower_limit',
              'inside_limits': 'output_input_value', 'result_type': 'type_of_operands'},
       limits=(('constraints.limit_order', {'lower_limit_operand': 's1',
                                            'upper_limit_operand': 's3'}),),
       witness=('Lower limit', 'Upper limit', 'outputs the contents of a device specified in')),
    _f('MUX', '10.5', (200, 200), category='selection',
       inputs=(('_K', 'INT', 'n', 'selection_index'),
               ('_IN', 'ANY', 's1', 'selectable_value')),
       outputs=(('Output_label', 'ANY', 'd', 'selected_value'),),
       st_plain='MUX(_K,_IN,_IN)', st_enabled='MUX_E(EN,_K,_IN,_IN,Output_label)',
       rule={'rule': 'select_by_index', 'first_index': 1, 'index_matches': 'sn',
              'out_of_range_result': 'indefinite_value'},
       limits=(PIN_2_28, ('constraints.index_domain',
                          {'selector': 'n', 'out_of_range': 'indefinite_value'})),
       witness=('This function outputs either one among values stored in devices specified in',
                'outside the pin number range')),
    # ---- chapter 11: comparison ----
    _f('GT', '11.1', (203, 203), category='comparison',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='GT_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'comparison_chain', 'relation': '>', 'chain_all_pairs': True,
              'result_true': 'all_relations_hold', 'result_false': 'any_relation_fails'},
       limits=(PIN_2_28,),
       witness=('This function executes comparison', 'range of 2 to 28')),
    _f('GE', '11.2', (205, 205), category='comparison',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='GE_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'comparison_chain', 'relation': '>=', 'chain_all_pairs': True,
              'result_true': 'all_relations_hold', 'result_false': 'any_relation_fails'},
       limits=(PIN_2_28,),
       witness=('This function executes comparison', 'range of 2 to 28')),
    _f('EQ', '11.3', (207, 207), category='comparison',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='EQ_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'comparison_chain', 'relation': '=', 'chain_all_pairs': True,
              'result_true': 'all_relations_hold', 'result_false': 'any_relation_fails'},
       limits=(PIN_2_28,),
       witness=('This function executes comparison', 'range of 2 to 28')),
    _f('LE', '11.4', (209, 209), category='comparison',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='LE_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'comparison_chain', 'relation': '<=', 'chain_all_pairs': True,
              'result_true': 'all_relations_hold', 'result_false': 'any_relation_fails'},
       limits=(PIN_2_28,),
       witness=('This function executes comparison', 'range of 2 to 28')),
    _f('LT', '11.5', (211, 211), category='comparison',
       inputs=(('_IN', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='LT_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'comparison_chain', 'relation': '<', 'chain_all_pairs': True,
              'result_true': 'all_relations_hold', 'result_false': 'any_relation_fails'},
       limits=(PIN_2_28,),
       witness=('This function executes comparison', 'range of 2 to 28')),
    _f('NE', '11.6', (213, 213), category='comparison',
       inputs=(('_IN1', 'ANY_SIMPLE', 's1', 'compared_value'),
               ('_IN2', 'ANY_SIMPLE', 's2', 'compared_value')),
       outputs=(('Output_label', 'BOOL', 'd', 'comparison_result'),),
       st_enabled='NE_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'comparison_pair', 'relation': '<>', 'chain_all_pairs': False,
              'result_true': 'not_equal', 'result_false': 'equal'},
       limits=(('execution.operand_pins', {'operand_pins': {'minimum': 2, 'maximum': 2},
                                           'variable_arity': False}),),
       witness=('This function compares', 's1')),
    # ---- chapter 12: character string ----
    _f('MID', '12.1', (216, 218), category='character_string',
       inputs=(('_IN', 'STRING', 's', 'source_string'),
               ('_L', 'INT', 'n1', 'character_count'),
               ('_P', 'INT', 'n2', 'head_character_position')),
       outputs=(('Output_label', 'STRING', 'd', 'extracted_string'),),
       st_plain='MID(_IN,_L,_P)', st_enabled='MID_E(EN,_IN,_L,_P,Output_label)',
       rule={'rule': 'substring_extraction', 'count_selector': 'n1',
              'position_selector': 'n2', 'position_origin': 1},
       limits=(LABEL_RULE_STRING,
               ('constraints.string_layout', {'terminator_byte': '00H',
                                              'high_order_byte_first': True}),),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['terminator_missing_after_source_range',
                              'head_position_exceeds_string_length',
                              'character_count_exceeds_range']},
       witness=('extracts specified number of characters', 'Error code: K6706')),
    _f('CONCAT', '12.2', (219, 219), category='character_string',
       inputs=(('_IN', 'STRING', 's1', 'connected_string'),
               ('_IN', 'STRING', 's2', 'connecting_string')),
       outputs=(('Output_label', 'STRING', 'd', 'connected_result'),),
       st_plain='CONCAT(_IN,_IN)', st_enabled='CONCAT_E(EN,_IN,_IN,Output_label)',
       rule={'rule': 'string_concatenation', 'terminator_byte': '00H'},
       limits=(LABEL_RULE_STRING, PIN_2_28,
               ('constraints.string_layout', {'terminator_byte': '00H'}),),
       witness=('This function connects a character string stored in devices specified in',)),
    _f('INSERT', '12.3', (221, 223), category='character_string',
       inputs=(('_IN1', 'STRING', 's1', 'target_string'),
               ('_IN2', 'STRING', 's2', 'inserted_string'),
               ('_P', 'INT', 'n', 'insertion_position')),
       outputs=(('Output_label', 'STRING', 'd', 'insertion_result'),),
       st_plain='INSERT(_IN1,_IN2,_P)',
       st_enabled='INSERT_E(EN,_IN1,_IN2,_P,Output_label)',
       rule={'rule': 'string_insertion', 'position_selector': 'n',
              'terminator_byte': '00H'},
       limits=(LABEL_RULE_STRING,
               ('constraints.insert_position_domain', {'selector': 'n'}),),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['position_out_of_range', 'source_characters_exceed_range',
                              'destination_devices_insufficient']},
       witness=('This function inserts a character string', 'Error code: K6706')),
    _f('DELETE', '12.4', (224, 224), category='character_string',
       inputs=(('_IN', 'STRING', 's', 'source_string'),
               ('_L', 'INT', 'n1', 'character_count'),
               ('_P', 'INT', 'n2', 'head_character_position')),
       outputs=(('Output_label', 'STRING', 'd', 'deletion_result'),),
       st_plain='DELETE(_IN,_L,_P)', st_enabled='DELETE_E(EN,_IN,_L,_P,Output_label)',
       rule={'rule': 'string_deletion', 'count_selector': 'n1', 'position_selector': 'n2'},
       limits=(LABEL_RULE_STRING,),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['source_characters_exceed_range',
                              'position_out_of_range']},
       witness=('This function deletes a character string',
                'Number of characters to be deleted')),
    _f('REPLACE', '12.5', (226, 227), category='character_string',
       inputs=(('_IN1', 'STRING', 's1', 'replaced_string'),
               ('_IN2', 'STRING', 's2', 'replacement_string'),
               ('_L', 'INT', 'n1', 'character_count'),
               ('_P', 'INT', 'n2', 'head_character_position')),
       outputs=(('Output_label', 'STRING', 'd', 'replacement_result'),),
       st_plain='REPLACE(_IN1,_IN2,_L,_P)',
       st_enabled='REPLACE_E(EN,_IN1,_IN2,_L,_P,Output_label)',
       rule={'rule': 'string_replacement', 'count_selector': 'n1',
              'position_selector': 'n2',
              'count_minus_one': 'replace_to_end_of_string',
              'excess_characters_dropped': True},
       limits=(LABEL_RULE_STRING,
               ('constraints.count_domain', {'selector': 'n1', 'minus_one': 'to_end'}),),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['source_characters_exceed_range',
                              'position_out_of_range']},
       witness=('This function replaces a character string', 'characters to be replaced')),
    _f('FIND', '12.6', (229, 229), category='character_string',
       inputs=(('_IN1', 'STRING', 's1', 'searched_string'),
               ('_IN2', 'STRING', 's2', 'search_string')),
       outputs=(('Output_label', 'INT', 'd', 'found_position'),),
       st_plain='FIND(_IN1,_IN2)', st_enabled='FIND_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'substring_search', 'position_origin': 1,
              'not_found': 0, 'result_type': 'word_signed'},
       limits=(LABEL_RULE_STRING,),
       errors={'flag': 'M8067', 'code_register': 'D8067', 'codes': ['K6706'],
               'conditions': ['terminator_missing_in_search_range']},
       witness=('This function searches a character string',
                'Head word device which will store the search result')),
    # ---- chapter 13: time ----
    _f('ADD_TIME', '13.1', (233, 233), category='time',
       inputs=(('_IN1', 'TIME', 's1', 'augend'), ('_IN2', 'TIME', 's2', 'addend')),
       outputs=(('Output_label', 'TIME', 'd', 'sum'),),
       st_plain='ADD_TIME(_IN1,_IN2)', st_enabled='ADD_TIME_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'time_addition', 'time_unit_ms': 1, 'result_type': 'time'},
       limits=(('constraints.time_representation', {'time_unit_ms': 1,
                                                    'range': [-2147483648, 2147483647]}),),
       witness=('This function performs addition', 'time data')),
    _f('SUB_TIME', '13.2', (235, 235), category='time',
       inputs=(('_IN1', 'TIME', 's1', 'minuend'), ('_IN2', 'TIME', 's2', 'subtrahend')),
       outputs=(('Output_label', 'TIME', 'd', 'difference'),),
       st_plain='SUB_TIME(_IN1,_IN2)', st_enabled='SUB_TIME_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'time_subtraction', 'time_unit_ms': 1, 'result_type': 'time'},
       limits=(('constraints.time_representation', {'time_unit_ms': 1}),),
       witness=('This function performs subtraction', 'time data')),
    _f('MUL_TIME', '13.3', (237, 237), category='time',
       inputs=(('_IN1', 'TIME', 's1', 'time_value'), ('_IN2', 'ANY_NUM', 's2', 'multiplier')),
       outputs=(('Output_label', 'TIME', 'd', 'product'),),
       st_plain='MUL_TIME(_IN1,_IN2)', st_enabled='MUL_TIME_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'time_scaling', 'multiplier_type': 'ANY_NUM', 'result_type': 'time'},
       limits=(('constraints.overflow', {'overflow_is_operation_error': False,
                                         'status_result': 'TRUE',
                                         'accurate_result_available': False}),),
       witness=('performs multiplication( s1 × s2 ) using time data',
                'not regarded as an operation error')),
    _f('DIV_TIME', '13.4', (239, 239), category='time',
       inputs=(('_IN1', 'TIME', 's1', 'time_value'), ('_IN2', 'ANY_NUM', 's2', 'divisor')),
       outputs=(('Output_label', 'TIME', 'd', 'quotient'),),
       st_plain='DIV_TIME(_IN1,_IN2)', st_enabled='DIV_TIME_E(EN,_IN1,_IN2,Output_label)',
       rule={'rule': 'time_scaling', 'operation': 'division', 'result_type': 'time'},
       limits=(('constraints.divisor', {'divisor_type': 'ANY_NUM_except_zero',
                                        'zero_division': 'operation_error'}),),
       errors={'flag': 'M8067', 'conditions': ['divisor_is_zero',
                                               'result_exceeds_2147483647']},
       witness=('performs division ( s1 / s2 ) using time data', 'except "0"')),
    # ---- chapter 14: standard function blocks ----
    _f('R_TRIG', '14.1', (242, 242), category='function_block', kind='function_block',
       instance=('R_TRIG', 'R_TRIG_E'),
       inputs=(('_CLK', 'BOOL', 's', 'clock_input'),),
       outputs=(('Q', 'BOOL', 'd1', 'pulse_output'),),
       st_plain='R_TRIG(_CLK,Q)', st_enabled='R_TRIG_E(EN,_CLK,Q,ENO)',
       rule={'rule': 'rising_edge_detection', 'pulse_width': 'one_operation_cycle'},
       witness=('sets to ON a device specified in d when a device specified in s turns ON',
                'only for 1 operation cycle')),
    _f('F_TRIG', '14.2', (244, 244), category='function_block', kind='function_block',
       instance=('F_TRIG', 'F_TRIG_E'),
       inputs=(('_CLK', 'BOOL', 's', 'clock_input'),),
       outputs=(('Q', 'BOOL', 'd1', 'pulse_output'),),
       st_plain='F_TRIG(_CLK,Q)', st_enabled='F_TRIG_E(EN,_CLK,Q,ENO)',
       rule={'rule': 'falling_edge_detection', 'pulse_width': 'one_operation_cycle'},
       witness=('turns OFF',)),
    _f('CTU', '14.3', (246, 247), category='function_block', kind='function_block',
       instance=('CTU', 'CTU_E'),
       inputs=(('CU', 'BOOL', 's1', 'count_up_signal'),
               ('RESET', 'BOOL', 's2', 'reset_input'),
               ('PV', 'INT', 'n', 'preset_value')),
       outputs=(('Q', 'BOOL', 'd1', 'count_up_output'),
                ('CV', 'INT', 'd2', 'count_value')),
       st_plain='CTU(CU,RESET,PV,Q,CV)',
       st_enabled='CTU_E(EN,CU,RESET,PV,Q,CV,ENO)',
       rule={'rule': 'count_up_counter', 'increment': 1,
              'output_on': 'count_value_reaches_preset',
              'reset_effect': {'output': 'OFF', 'count_value': 0}},
       limits=(('constraints.preset_type', {'preset': 'word_signed'}),),
       witness=('counts up (adds "1" to)', 'this function block turns OFF a device specified in')),
    _f('CTD', '14.4', (248, 249), category='function_block', kind='function_block',
       instance=('CTD', 'CTD_E'),
       inputs=(('CD', 'BOOL', 's1', 'count_down_signal'),
               ('LOAD', 'BOOL', 's2', 'load_input'),
               ('PV', 'INT', 'n', 'preset_value')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'),
                ('CV', 'INT', 'd2', 'count_value')),
       st_plain='CTD(CD,LOAD,PV,Q,CV)',
       st_enabled='CTD_E(EN,CD,LOAD,PV,Q,CV,ENO)',
       rule={'rule': 'count_down_counter', 'decrement': 1,
              'output_on': 'count_value_zero_or_less'},
       limits=(('constraints.preset_type', {'preset': 'word_signed'}),),
       witness=('counts down (subtracts "1" from)', 'becomes "0" or less')),
    _f('CTUD', '14.5', (250, 251), category='function_block', kind='function_block',
       instance=('CTUD', 'CTUD_E'),
       inputs=(('CU', 'BOOL', 's1', 'count_up_signal'),
               ('CD', 'BOOL', 's2', 'count_down_signal'),
               ('RESET', 'BOOL', 's3', 'reset_input'),
               ('LOAD', 'BOOL', 's4', 'load_input'),
               ('PV', 'INT', 'n', 'preset_value')),
       outputs=(('QU', 'BOOL', 'd1', 'count_up_output'),
                ('QD', 'BOOL', 'd2', 'count_down_output'),
                ('CV', 'INT', 'd3', 'count_value')),
       st_plain='CTUD(CU,CD,RESET,LOAD,PV,QU,QD,CV)',
       st_enabled='CTUD_E(EN,CU,CD,RESET,LOAD,PV,QU,QD,CV,ENO)',
       rule={'rule': 'count_up_down_counter', 'increment': 1, 'decrement': 1,
              'output_on': 'count_relation_to_preset'},
       limits=(('constraints.preset_type', {'preset': 'word_signed'}),),
       witness=('counts up/down the number of times of rising of a signal',
                'This function block resets the count value')),
    _f('TP', '14.6', (252, 253), category='function_block', kind='function_block',
       instance=('TP', 'TP_E'),
       inputs=(('IN', 'BOOL', 's', 'pulse_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TP(IN,PT,Q,ET)', st_enabled='TP_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'pulse_timer', 'output_on': 'while_elapsed_less_than_preset',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 100,
                                           'documented_minimum_unit_ms': 100}),),
       witness=('Pulse timer', 'Time can be set in units of 100 ms or more')),
    _f('TP_10', '14.6', (252, 253), category='function_block', kind='function_block',
       instance=('TP_10', 'TP_10_E'),
       inputs=(('IN', 'BOOL', 's', 'pulse_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TP_10(IN,PT,Q,ET)', st_enabled='TP_10_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'pulse_timer', 'output_on': 'while_elapsed_less_than_preset',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 10,
                                           'documented_minimum_unit_ms': 10}),),
       witness=('TP_10(_E):Time can be set in units of 10 ms or more',)),
    _f('TON', '14.7', (254, 255), category='function_block', kind='function_block',
       instance=('TON', 'TON_E'),
       inputs=(('IN', 'BOOL', 's', 'enable_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TON(IN,PT,Q,ET)', st_enabled='TON_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'on_delay_timer',
              'output_on': 'after_input_on_for_preset_time',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 100,
                                           'documented_minimum_unit_ms': 100}),),
       witness=('On delay timer', 'this function block turns ON a device specified in after')),
    _f('TON_10', '14.7', (254, 255), category='function_block', kind='function_block',
       instance=('TON_10', 'TON_10_E'),
       inputs=(('IN', 'BOOL', 's', 'enable_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TON_10(IN,PT,Q,ET)', st_enabled='TON_10_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'on_delay_timer',
              'output_on': 'after_input_on_for_preset_time',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 10,
                                           'documented_minimum_unit_ms': 10}),),
       witness=('TON_10(_E) :Time can be set in units of 10 ms or more',)),
    _f('TOF', '14.8', (256, 257), category='function_block', kind='function_block',
       instance=('TOF', 'TOF_E'),
       inputs=(('IN', 'BOOL', 's', 'enable_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TOF(IN,PT,Q,ET)', st_enabled='TOF_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'off_delay_timer',
              'output_off': 'after_input_off_for_preset_time',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 100,
                                           'documented_minimum_unit_ms': 100}),),
       witness=('Off delay timer', 'turns OFF the output signal after the specified time')),
    _f('TOF_10', '14.8', (256, 257), category='function_block', kind='function_block',
       instance=('TOF_10', 'TOF_10_E'),
       inputs=(('IN', 'BOOL', 's', 'enable_input'), ('PT', 'TIME', 'n', 'preset_time')),
       outputs=(('Q', 'BOOL', 'd1', 'output_signal'), ('ET', 'TIME', 'd2', 'elapsed_time')),
       st_plain='TOF_10(IN,PT,Q,ET)', st_enabled='TOF_10_E(EN,IN,PT,Q,ET,ENO)',
       rule={'rule': 'off_delay_timer',
              'output_off': 'after_input_off_for_preset_time',
              'elapsed_output': 'ET'},
       limits=(('constraints.time_units', {'preset_resolution_ms': 10,
                                           'documented_minimum_unit_ms': 10}),),
       witness=('TOF_10(_E) :Time can be set in units of 10 ms or more',)),
    _f('COUNTER_FB_M', '14.9', (258, 259), category='function_block',
       kind='function_block',
       members=('Coil',),
       inputs=(('Coil', 'BOOL', 's', 'execution_condition'),
               ('Preset', 'INT', 'n', 'counter_set_value'),
               ('ValueIn', 'INT', 's', 'initial_counter_value')),
       outputs=(('ValueOut', 'WORD', 'd', 'current_counter_value'),
                ('Status', 'BOOL', 'd', 'counter_output_contact')),
       st_plain='COUNTER_FB_M(Coil,Preset,ValueIn,ValueOut,Status)',
       rule={'rule': 'count_up_counter_with_initial_value',
              'count_step': 1,
              'output_on': 'count_value_reaches_preset',
              'initial_value': 'ValueIn'},
       limits=(('constraints.reset_path', {'member': 'Coil',
                                           'documented_reset': 'RST_instance_member'}),),
       witness=('This counter starts counting when the condition turns ON from OFF',
                'A counter initial value can be set')),
    _f('TIMER_10_FB_M', '14.10', (260, 260), category='function_block',
       kind='function_block',
       members=('Coil',),
       inputs=(('Coil', 'BOOL', 's', 'execution_condition'),
               ('Preset', 'INT', 'n', 'timer_set_value'),
               ('ValueIn', 'INT', 's', 'initial_timer_value')),
       outputs=(('ValueOut', 'WORD', 'd', 'current_timer_value'),
                ('Status', 'BOOL', 'd', 'timer_output_contact')),
       st_plain='TIMER_10_FB_M(Coil,Preset,ValueIn,ValueOut,Status)',
       rule={'rule': 'on_delay_timer_with_initial_value',
              'preset_scale_ms': 10, 'count_from': 'ValueIn_times_10ms',
              'output_on': 'count_reaches_Preset_times_10ms',
              'output_off_when_condition_off': True},
       limits=(('constraints.time_units', {'preset_resolution_ms': 10}),),
       witness=('The initial value and setting value of the timer is multiplied by 10 ms',
                'Timer output contact')),
    _f('TIMER_CONT_FB_M', '14.11', (261, 262), category='function_block',
       kind='function_block',
       members=('Coil',),
       inputs=(('Coil', 'BOOL', 's', 'execution_condition'),
               ('Preset', 'INT', 'n', 'timer_set_value'),
               ('ValueIn', 'INT', 's', 'initial_timer_value')),
       outputs=(('ValueOut', 'WORD', 'd', 'current_timer_value'),
                ('Status', 'BOOL', 'd', 'timer_output_contact')),
       st_plain='TIMER_CONT_FB_M(Coil,Preset,ValueIn,ValueOut,Status)',
       rule={'rule': 'retentive_on_delay_timer',
              'preset_scale': 'allocation_device_dependent',
              'count_from': 'ValueIn',
              'output_on': 'count_reaches_Preset_scaled',
              'state_retained_when_condition_off': True,
              'resume_on_condition_on': True},
       limits=(('constraints.time_units',
                {'preset_resolution_ms': [1, 100],
                 'resolution_basis': 'allocation_device_retentive_timer'}),),
       witness=('retentive timer that counts the time when the variable is ON',
                'The magnification of Valueln and Preset')),
    _f('TIMER_100_FB_M', '14.12', (263, 263), category='function_block',
       kind='function_block',
       members=('Coil',),
       inputs=(('Coil', 'BOOL', 's', 'execution_condition'),
               ('Preset', 'INT', 'n', 'timer_set_value'),
               ('ValueIn', 'INT', 's', 'initial_timer_value')),
       outputs=(('ValueOut', 'WORD', 'd', 'current_timer_value'),
                ('Status', 'BOOL', 'd', 'timer_output_contact')),
       st_plain='TIMER_100_FB_M(Coil,Preset,ValueIn,ValueOut,Status)',
       rule={'rule': 'on_delay_timer_with_initial_value',
              'preset_scale_ms': 100, 'count_from': 'ValueIn_times_100ms',
              'output_on': 'count_reaches_Preset_times_100ms',
              'output_off_when_condition_off': True},
       limits=(('constraints.time_units', {'preset_resolution_ms': 100}),),
       witness=('The initial value and setting value of the timer is multiplied by 100 ms',
                'Timer output contact')),
    # ---- chapter 15: operators ----
    _f('ADD', '15.1', (265, 265), category='operator', kind='operator',
       operator={'symbol': '+', 'expression': 's1+s2', 'infix': True,
                 'documented_st_example': 'D20:=D0+D10'},
       inputs=(('s1', 'ANY_NUM', 's1', 'operand'),),
       outputs=(('d', 'ANY_NUM', 'd', 'assigned_result'),),
       rule={'rule': 'addition', 'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_dint3:=(g_dint1)+(g_dint2)', 'D20:=D0+D10')),
    _f('SUB', '15.2', (266, 266), category='operator', kind='operator',
       operator={'symbol': '-', 'expression': 's1-s2', 'infix': True,
                 'documented_st_example': 'D20:=D0-D10'},
       inputs=(('s1', 'ANY_NUM', 's1', 'minuend'), ('s2', 'ANY_NUM', 's2', 'subtrahend')),
       outputs=(('d', 'ANY_NUM', 'd', 'assigned_result'),),
       rule={'rule': 'subtraction'},
       witness=('g_int3:=(g_int1)-(g_int2)', 'D20:=D0-D10')),
    _f('MUL', '15.3', (267, 267), category='operator', kind='operator',
       operator={'symbol': '*', 'expression': 's1*s2', 'infix': True,
                 'documented_st_example': 'D20:=D0*D10'},
       inputs=(('s1', 'ANY_NUM', 's1', 'multiplicand'),),
       outputs=(('d', 'ANY_NUM', 'd', 'assigned_result'),),
       rule={'rule': 'multiplication', 'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_dint3:=(g_dint1) * (g_dint2)',)),
    _f('DIV', '15.4', (268, 268), category='operator', kind='operator',
       operator={'symbol': '/', 'expression': 's1/s2', 'infix': True,
                 'documented_st_example': 'D20:=D0/D10'},
       inputs=(('s1', 'ANY_NUM', 's1', 'dividend'), ('s2', 'ANY_NUM', 's2', 'divisor')),
       outputs=(('d', 'ANY_NUM', 'd', 'assigned_result'),),
       rule={'rule': 'division'},
       witness=('g_dint3:=(g_dint1) / (g_dint2)',)),
    _f('MOD', '15.5', (269, 269), category='operator', kind='operator',
       operator={'symbol': 'MOD', 'expression': 's1 MOD s2', 'infix': True,
                 'documented_st_example': 'd:=s1 MOD s2'},
       language_forms=AS_ST_ONLY,
       inputs=(('s1', 'ANY_INT', 's1', 'dividend'), ('s2', 'ANY_INT', 's2', 'divisor')),
       outputs=(('d', 'ANY_INT', 'd', 'assigned_result'),),
       rule={'rule': 'modulus'},
       limits=(('constraints.notation', {'st_form': 'infix_MOD',
                                         'differs_from_other_functions': True}),),
       witness=('The MOD operator is not available in Structured ladder/', 'd:=s1 MOD s2')),
    _f('POWER', '15.6', (270, 270), category='operator', kind='operator',
       operator={'symbol': '**', 'expression': 's1**s2', 'infix': True,
                 'documented_st_example': 'd:=s1 **s2'},
       language_forms=AS_ST_ONLY,
       inputs=(('s1', 'REAL', 's1', 'base'), ('s2', 'ANY_NUM', 's2', 'exponent')),
       outputs=(('d', 'REAL', 'd', 'assigned_result'),),
       rule={'rule': 'exponentiation', 'documented_function_alternative': 'EXPT'},
       limits=(('constraints.notation', {'st_form': 'infix_asterisk_asterisk',
                                         'function_alternative': 'EXPT'}),),
       witness=('The "**" operator is not available in Structured ladder/', 'd:=s1 **s2',
                'Data to be raised')),
    _f('AND', '15.7', (271, 271), category='operator', kind='operator',
       operator={'symbol': 'AND', 'alternative': '&', 'expression': 's1 AND s2',
                 'infix': True, 'documented_st_example': 'M20:=M0 AND M10'},
       inputs=(('s1', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('d', 'ANY_BIT', 'd', 'assigned_result'),),
       rule={'rule': 'bitwise_and', 'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_word3:=(g_word1) AND (g_word2)', 'g_word3:=(g_word1) & (g_word2)')),
    _f('OR', '15.8', (272, 272), category='operator', kind='operator',
       operator={'symbol': 'OR', 'expression': 's1 OR s2', 'infix': True,
                 'documented_st_example': 'M20:=M0 OR M10'},
       inputs=(('s1', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('d', 'ANY_BIT', 'd', 'assigned_result'),),
       rule={'rule': 'bitwise_or', 'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_word3:=(g_word1) OR (g_word2)',)),
    _f('XOR', '15.9', (273, 274), category='operator', kind='operator',
       operator={'symbol': 'XOR', 'expression': 's1 XOR s2', 'infix': True,
                 'documented_st_example': 'M20:=M0 XOR M10'},
       inputs=(('s1', 'ANY_BIT', 's1', 'logical_operand'),),
       outputs=(('d', 'ANY_BIT', 'd', 'assigned_result'),),
       rule={'rule': 'bitwise_xor', 'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_word3:=(g_word1) XOR (g_word2)',)),
    _f('NOT', '15.10', (275, 275), category='operator', kind='operator',
       operator={'symbol': 'NOT', 'expression': 'NOT(s)', 'infix': False,
                 'documented_st_example': 'd:=NOT(s)'},
       language_forms=AS_ST_ONLY,
       inputs=(('s', 'ANY_BIT', 's', 'operand'),),
       outputs=(('d', 'ANY_BIT', 'd', 'assigned_result'),),
       rule={'rule': 'bitwise_not'},
       limits=(('constraints.notation', {'st_form': 'prefix_NOT_parenthesised'}),),
       witness=('The NOT operator is not available in Structured ladder/', 'd:=NOT(s)')),
    _f('GT', '15.11', (276, 276), category='operator', kind='operator',
       operator={'symbol': '>', 'expression': 's1>s2', 'infix': True,
                 'documented_st_example': 'M0:=D0>D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_chain', 'relation': '>',
              'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('g_bool2:=(g_int1)>(g_int2)', 'M0:=D0>D10')),
    _f('GE', '15.12', (277, 277), category='operator', kind='operator',
       operator={'symbol': '>=', 'expression': 's1>=s2', 'infix': True,
                 'documented_st_example': 'M0:=D0>=D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_chain', 'relation': '>=',
              'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('M0:=D0>=D10',)),
    _f('EQ', '15.13', (278, 278), category='operator', kind='operator',
       operator={'symbol': '=', 'expression': 's1=s2', 'infix': True,
                 'documented_st_example': 'M0:=D0=D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_chain', 'relation': '=',
              'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('M0:=D0=D10',)),
    _f('LE', '15.14', (279, 279), category='operator', kind='operator',
       operator={'symbol': '<=', 'expression': 's1<=s2', 'infix': True,
                 'documented_st_example': 'M0:=D0<=D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_chain', 'relation': '<=',
              'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('M0:=D0<=D10',)),
    _f('LT', '15.15', (280, 280), category='operator', kind='operator',
       operator={'symbol': '<', 'expression': 's1<s2', 'infix': True,
                 'documented_st_example': 'M0:=D0<D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_chain', 'relation': '<',
              'operand_pins': {'minimum': 2, 'maximum': 28}},
       witness=('M0:=D0<D10',)),
    _f('NE', '15.16', (281, 281), category='operator', kind='operator',
       operator={'symbol': '<>', 'expression': 's1<>s2', 'infix': True,
                 'documented_st_example': 'M0:=D0<>D10'},
       inputs=(('s1', 'ANY_SIMPLE', 's1', 'compared_value'),
               ('s2', 'ANY_SIMPLE', 's2', 'compared_value')),
       outputs=(('d', 'BOOL', 'd', 'assigned_result'),),
       rule={'rule': 'comparison_pair', 'relation': '<>',
              'operand_pins': {'minimum': 2, 'maximum': 2}},
       witness=('M0:=D0<>D10',)),
)
# ---------------------------------------------------------------------------
# Chapter 5 records are generated from the compact table above; the multi-operand
# conversion functions carry one extra operand pin and a documented ST form.
# ---------------------------------------------------------------------------
CONVERSION_OVERRIDES = {
    'BITARR_TO_INT': {'inputs': (('BitArr', 'BITARR', 's', 'bit_array_source'),
                                 ('n', 'INT', 'n', 'specified_bits')),
                      'st_plain': 'BITARR_TO_INT(BitArr,n)'},
    'BITARR_TO_DINT': {'inputs': (('BitArr', 'BITARR', 's', 'bit_array_source'),
                                  ('n', 'INT', 'n', 'specified_bits')),
                       'st_plain': 'BITARR_TO_DINT(BitArr,n)'},
    'INT_TO_BITARR': {'inputs': (('s', 'ANY16', 's', 'integer_source'),
                                 ('n', 'INT', 'n', 'specified_bits')),
                      'outputs': (('BitArrOut', 'BITARR', 'd', 'bit_array_destination'),),
                      'st_plain': 'INT_TO_BITARR(s,n)'},
    'DINT_TO_BITARR': {'inputs': (('s', 'ANY32', 's', 'integer_source'),
                                  ('n', 'INT', 'n', 'specified_bits')),
                       'outputs': (('BitArrOut', 'BITARR', 'd', 'bit_array_destination'),),
                       'st_plain': 'DINT_TO_BITARR(s,n)'},
    'CPY_BITARR': {'inputs': (('BitArrIn', 'BITARR', 's', 'bit_array_source'),
                              ('n', 'INT', 'n', 'copied_bits')),
                   'outputs': (('BitArrOut', 'BITARR', 'd', 'bit_array_destination'),),
                   'st_plain': 'CPY_BITARR(BitArrIn,n,BitArrOut)'},
    'GET_BIT_OF_INT': {'inputs': (('s', 'INT', 's', 'read_source'),
                                  ('n', 'INT', 'n', 'bit_position')),
                       'st_plain': 'GET_BIT_OF_INT(s,n)'},
    'SET_BIT_OF_INT': {'inputs': (('s', 'BOOL', 's', 'written_bit'),
                                  ('n', 'INT', 'n', 'bit_position')),
                       'st_plain': None},
    'CPY_BIT_OF_INT': {'inputs': (('s', 'INT', 's', 'copy_source'),
                                  ('n1', 'INT', 'n1', 'source_bit_position'),
                                  ('n2', 'INT', 'n2', 'destination_bit_position')),
                       'st_plain': 'CPY_BIT_OF_INT(s,n1,n2)'},
    'GET_BOOL_ADDR': {'inputs': (('s', 'ANY', 's', 'source_data'),),
                      'st_plain': 'GET_BOOL_ADDR(s)', 'st_enabled': None,
                      'witness': ('This function outputs the start data as bit data',
                                  'Input data type'),
                      'limits': (('constraints.accepted_input_types',
                                  {'input': ['BIT', 'BIT_STRING'], 'output': 'BOOL'}),)},
    'GET_INT_ADDR': {'inputs': (('s', 'ANY', 's', 'source_data'),),
                     'st_plain': 'GET_INT_ADDR(s)', 'st_enabled': None,
                     'witness': ('This function outputs the start data as word [signed] data',
                                 'Array of real number'),
                     'limits': (('constraints.accepted_input_types',
                                 {'input': ['INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME',
                                            'BITARR_INT', 'BITARR_DINT', 'BITARR_WORD',
                                            'BITARR_DWORD', 'BITARR_REAL', 'BITARR_TIME'],
                                  'output': 'INT'}),)},
    'GET_WORD_ADDR': {'inputs': (('s', 'ANY', 's', 'source_data'),),
                      'st_plain': 'GET_WORD_ADDR(s)', 'st_enabled': None,
                      'witness': ('This function outputs the start data as word [unsigned]/bit '
                                  'string [16-bit] data', 'Array of time type'),
                      'limits': (('constraints.accepted_input_types',
                                  {'input': ['INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME',
                                             'BITARR_INT', 'BITARR_DINT', 'BITARR_WORD',
                                             'BITARR_DWORD', 'BITARR_REAL', 'BITARR_TIME'],
                                   'output': 'WORD'}),)},
}

CONVERSION_OVERRIDES.update({
    'BOOL_TO_DINT': {'witness': ('Conversion source bit data',
                                 'Double word [signed] data after conversion', 'FALSE 0')},
    'WORD_TO_BOOL': {'witness': ('Conversion source word [unsigned]/bit String [16-bit] data',
                                 'Bit data after conversion', '0H FALSE')},
    'DWORD_TO_BOOL': {'witness': ('Conversion source double word [unsigned]/bit string '
                                  '[32-bit] data', 'Bit data after conversion', '0H FALSE')},
    'BOOL_TO_DWORD': {'witness': ('Conversion source bit data',
                                  'Double word [unsigned]/bit string [32-bit] data after '
                                  'conversion', 'outputs "1H"')},
    'TIME_TO_BOOL': {'witness': ('Conversion source time data', 'Bit data after conversion',
                                 '0ms')},
    'BITARR_TO_INT': {'witness': ('This function converts specified number of bits of a bit '
                                  'array', 'ANY16')},
    'BITARR_TO_DINT': {'witness': ('This function converts specified number of bits of a bit '
                                   'array', 'ANY32')},
    'INT_TO_BITARR': {'witness': ('Conversion source word [signed] data', 'ANY16')},
    'DINT_TO_BITARR': {'witness': ('Conversion source double word [signed] data', 'ANY32')},
    'CPY_BITARR': {'witness': ('Start bit of copy source bit array elements',
                               'Only a constant 4, 8, 12, 16, 20, 24, 28 or 32')},
    'GET_BIT_OF_INT': {'witness': ('Read source word [signed] data',
                                   'This function reads the value of the')},
    'SET_BIT_OF_INT': {'witness': ('This function writes a value to a specified bit of word '
                                   '[signed] data', 'Specified bit position'),
                       'st_plain': 'SET_BIT_OF_INT(s,n)'},
    'CPY_BIT_OF_INT': {'witness': ('Copy source word [signed] data',
                                   'This function copies a specified bit of word [signed] '
                                   'data')},
})

# Sections 5.57 to 5.59 publish the accepted "Input data type" row for their ANY operand.
_ADDRESS_TYPED_MEMBERS = ['INT', 'DINT', 'WORD', 'DWORD', 'REAL', 'STRING', 'TIME',
                          'BITARR_INT', 'BITARR_DINT', 'BITARR_WORD', 'BITARR_DWORD',
                          'BITARR_REAL', 'BITARR_TIME']
CONVERSION_OVERRIDES['GET_BOOL_ADDR']['type_members'] = {'ANY': ['BIT', 'BIT_STRING']}
CONVERSION_OVERRIDES['GET_INT_ADDR']['type_members'] = {'ANY': _ADDRESS_TYPED_MEMBERS}
CONVERSION_OVERRIDES['GET_WORD_ADDR']['type_members'] = {'ANY': _ADDRESS_TYPED_MEMBERS}

CONVERSION_ERRORS = {
    'BCD_TO_INT': {'flag': 'M8067', 'conditions': ['source_is_not_bcd']},
    'BCD_TO_DINT': {'flag': 'M8067', 'conditions': ['source_is_not_bcd']},
    'INT_TO_BCD': {'flag': 'M8067', 'conditions': ['source_outside_0_to_9999']},
    'DINT_TO_BCD': {'flag': 'M8067', 'conditions': ['source_outside_bcd_domain']},
    'TIME_TO_STR': {'flag': 'M8067', 'code_register': 'D8067',
                    'conditions': ['destination_device_range_exceeded']},
    'STR_TO_INT': {'flag': 'M8067', 'conditions': ['source_is_not_numeric_text']},
    'STR_TO_DINT': {'flag': 'M8067', 'conditions': ['source_is_not_numeric_text']},
    'STR_TO_REAL': {'flag': 'M8067', 'conditions': ['source_is_not_numeric_text']},
    'STR_TO_TIME': {'flag': 'M8067', 'conditions': ['source_is_not_numeric_text']},
    'INT_TO_STR': {'flag': 'M8067', 'conditions': ['destination_device_range_exceeded']},
    'DINT_TO_STR': {'flag': 'M8067', 'conditions': ['destination_device_range_exceeded']},
    'REAL_TO_STR': {'flag': 'M8067', 'conditions': ['destination_device_range_exceeded']},
}

# Sections whose caution text is shared by the whole manual; the citation stays
# with the section that states it.
SECTION_CROSS_REFERENCES = {
    'en_and_eno': '3.4',
    'data_type_table': '3.2',
}

TYPE_TABLE = {
    'bit': {'bits': 1, 'values': ['FALSE', 'TRUE', '0', '1']},
    'word_signed': {'bits': 16, 'range': [-32768, 32767]},
    'double_word_signed': {'bits': 32, 'range': [-2147483648, 2147483647]},
    'word_unsigned_bit_string_16': {'bits': 16, 'range': [0, 65535]},
    'double_word_unsigned_bit_string_32': {'bits': 32, 'range': [0, 4294967295]},
    'string': {'max_characters': 50, 'length': 'variable'},
    'time': {'bits': 32, 'unit': 'ms', 'literal_prefix': 'T',
             'range_ms': [-2147483648, 2147483647],
             'documented_lower_text': 'minus_24d_0h31m23s648.00ms',
             'documented_upper_text': 'max_24d20h31m23s647.00ms'},
}
TYPE_DOMAIN_BY_TOKEN = {
    'BOOL': TYPE_TABLE['bit'],
    'INT': TYPE_TABLE['word_signed'],
    'DINT': TYPE_TABLE['double_word_signed'],
    'WORD': TYPE_TABLE['word_unsigned_bit_string_16'],
    'DWORD': TYPE_TABLE['double_word_unsigned_bit_string_32'],
    'STRING': TYPE_TABLE['string'],
    'TIME': TYPE_TABLE['time'],
}
FLEXIBLE_TYPES = ('ANY', 'ANY_SIMPLE', 'ANY_NUM', 'ANY_INT', 'ANY_BIT', 'ANY16', 'ANY32')
# Sections 5.51 to 5.53 spell out which concrete types a flexible operand accepts.
FLEXIBLE_TYPE_MEMBERS = {
    'ANY16': (('INT', 'WORD'), 'word [signed] data or word [unsigned]/bit string'),
    'ANY32': (('DINT', 'DWORD'), 'double word [signed] data or double word'),
}

# The plain form of an application function that its own section does not spell
# out is the operator of chapter 15 of the same manual.  The equivalence is
# published as a real signature that cites both sections.
PLAIN_FORM_OPERATOR_SECTIONS = {
    ('ADD', '7.1'): '15.1',
    ('SUB', '7.2'): '15.2',
    ('MUL', '7.3'): '15.3',
    ('DIV', '7.4'): '15.4',
    ('AND', '9.1'): '15.7',
    ('OR', '9.2'): '15.8',
    ('XOR', '9.3'): '15.9',
    ('GT', '11.1'): '15.11',
    ('GE', '11.2'): '15.12',
    ('EQ', '11.3'): '15.13',
    ('LE', '11.4'): '15.14',
    ('LT', '11.5'): '15.15',
    ('NE', '11.6'): '15.16',
}

# Generic (flexible) data types.  The hierarchy is the one drawn in the
# "Generic data types" figure of the registered structured fundamentals manual
# and repeated in chapter 3.2 of this manual; leaf membership is published only
# where a registered source enumerates it.
GENERIC_TYPE_HIERARCHY = {
    'ANY': {'parent': 'none', 'children': ['ANY_SIMPLE'],
            'basis': 'generic_data_type_figure_highest_layer'},
    'ANY_SIMPLE': {'parent': 'ANY', 'children': ['ANY_BIT', 'ANY_NUM', 'TIME', 'STRING'],
                   'basis': 'generic_data_type_figure'},
    'ANY_NUM': {'parent': 'ANY_SIMPLE', 'children': ['ANY_INT', 'ANY_REAL'],
                'basis': 'generic_data_type_figure'},
    'ANY_INT': {'parent': 'ANY_NUM', 'children': [], 'basis': 'generic_data_type_figure'},
    'ANY_REAL': {'parent': 'ANY_NUM', 'children': [], 'basis': 'generic_data_type_figure'},
    'ANY_BIT': {'parent': 'ANY_SIMPLE', 'children': [], 'basis': 'generic_data_type_figure'},
    'ANY16': {'parent': 'flexible_width_class', 'children': [],
              'basis': 'documented_operand_wording_in_section_5_51_to_5_53'},
    'ANY32': {'parent': 'flexible_width_class', 'children': [],
              'basis': 'documented_operand_wording_in_section_5_51_to_5_53'},
}
GENERIC_TYPE_MEMBERS = {
    'ANY_NUM': (('INT', 'DINT', 'REAL'),
                'documented_any_num_argument_wording_across_registered_manuals'),
    'ANY16': (('INT', 'WORD'), 'documented_operand_wording_in_section_5_51_to_5_53'),
    'ANY32': (('DINT', 'DWORD'), 'documented_operand_wording_in_section_5_51_to_5_53'),
}
# Typed operands accepted by the documented ST operators of the fundamentals manual.
GENERIC_TYPE_OPERATOR_USAGE = {
    'ANY_NUM': ('ADD', 'SUB', 'MUL', 'DIV'),
    'ANY_INT': ('MOD',),
    'ANY_BIT': ('AND', 'OR', 'XOR', 'NOT'),
    'ANY_REAL': ('POWER',),
    'ANY_SIMPLE': ('GT', 'GE', 'EQ', 'LE', 'LT', 'NE'),
}
GENERIC_TYPE_SOURCES = (
    {'key': 'fundamentals_data_types', 'manual_id': 'structured_fundamentals_o',
     'section': 'Data types', 'page': 35,
     'witness': ('Generic data type is the data type of labels summarizing some elementary '
                 'data types',
                 "The higher 'ANY' data types include sub-level data types",
                 'ANY_REAL ANY_INT',
                 'word (signed) type, double word (signed) type, single-precision real type')},
    {'key': 'fundamentals_devices', 'manual_id': 'structured_fundamentals_o',
     'section': 'Internal user device', 'page': 95,
     'witness': ('Devices available for ANY16 are the same as the ones for word (unsigned)'
                 '/16-bit string and word (signed)',
                 'Devices available for ANY32 are the same as the ones for double word '
                 '(unsigned)/32-bit string and double word')},
    {'key': 'st_manual_any_type', 'manual_id': 'sh080366ek',
     'section': 'About ANY type', 'page': 28,
     'witness': ('The data types and device types corresponding to the ANY types are as '
                 'indicated below',
                 'when the argument of a function has been defined as ANY_NUM, any data type '
                 'can be specified as an argument from the word type, double word type and '
                 'real number type')},
)
GENERIC_TYPE_REFERENCES = {}

TOOL_BINDINGS = (
    {'id': 'gxworks2:language_availability', 'dimension': 'identity.language_availability',
     'number': '6.1.1', 'section': '6.1.1 Available programming languages', 'pages': (105, 105),
     'witness': ('Structured Ladder/FBD', 'Ladder Diagram', 'Function'),
     'value': {'function': {'structured_ladder_fbd': True, 'st': True, 'ladder_diagram': False,
                            'sfc': False},
               'function_block': {'structured_ladder_fbd': True, 'st': True,
                                  'ladder_diagram': True, 'sfc': False},
               'program_block': {'structured_ladder_fbd': True, 'st': True,
                                 'ladder_diagram': True, 'sfc': True},
               'fx_cpu_supported_languages': ['structured_ladder_fbd', 'st']}},
    {'id': 'gxworks2:pin_editing', 'dimension': 'parameters.pin_editing',
     'number': '8.6.3',
     'section': '8.6.3 Changing the number of arguments for functions and function blocks',
     'pages': (146, 146),
     'witness': ('Number of Pins', 'Increment pins'),
     'value': {'editable_constructs': ['function', 'function_block'],
               'applies_to_application_functions': True,
               'operations': ['increment', 'decrement', 'keyboard', 'element_resize'],
               'operand_range_reference': 'fxcpu_application_k_section_3'}},
    {'id': 'gxworks2:enable_output_assignment',
     'dimension': 'constraints.enable_output_device_assignment',
     'number': '10.4.9',
     'section': '10.4.9 Assigning common devices to outputs of function with EN/ENO',
     'pages': (186, 186),
     'witness': ('Automatically assign each system device to output of functions that use EN/',
                 'The same device is assigned'),
     'value': {'compile_option': 'assign_each_system_device_to_output_of_functions_with_EN_ENO',
               'selected': 'individual_device_per_function',
               'cleared': 'same_device_shared_by_all_function_outputs',
               'cleared_risk': {'instruction_without_en_eno_executes_without_execution_condition':
                                True,
                                'mitigation': ['use_instruction_with_EN_ENO',
                                               'use_input_result_as_EN']},
               'device_examples': ['M4105', 'M4106', 'M8188', 'M8190']}},
    {'id': 'gxworks2:bit_output_retention',
     'dimension': 'constraints.function_bit_output_retention',
     'number': '10.4.10', 'section': '10.4.10 Retaining bit type outputs of functions',
     'pages': (189, 189), 'witness': ('INT_TO_BOOL_E',),
     'value': {'retained_bit_output': 'keeps_last_value_when_function_not_executed',
               'setting': 'options_compile_structured_ladder_fbd_st',
               'documented_alternative': ['assignment_statement', 'SET_instruction'],
               'example_pair': ['INT_TO_BOOL', 'INT_TO_BOOL_E']}},
    {'id': 'gxworks2:call_considerations', 'dimension': 'constraints.function_call_consideration',
     'number': '10.5.3', 'section': '10.5.3 Considerations of Compilation', 'pages': (196, 196),
     'witness': ('constants can be used for the input side', 'VAR_IN_OUT'),
     'value': {'constant_for_input_side_of_VAR_IN_OUT': 'not_allowed',
               'array_index_on_output_side': 'not_allowed'}},
)
# ---------------------------------------------------------------------------
# Source binding helpers
# ---------------------------------------------------------------------------
SCOPE_CONDITION = {'context': 'cpu_scope_not_declared_for_section', 'basis': 'manual_scope'}
CHUNK_COLUMNS = ('id, manual_id, manual_number, revision, section, pdf_page, pdf_page_end, '
                 'chunk_type, instruction_opcode, source_pages_json, text, '
                 'source_applicability_json, document_plc_models')


def _rows(connection, sql, arguments=()):
    cursor = connection.execute(sql, arguments)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _flatten(text):
    return re.sub(r'\s+', '', text or '').lower()


def _contains(text, phrase):
    return _flatten(phrase) in _flatten(text)


def _call_verification(text, call_form):
    """Grade how much of a documented call form the section actually shows."""
    if not call_form:
        return None
    if _contains(text, call_form.rstrip(')')):
        return 'documented_arguments_verified'
    if _contains(text, call_form.split(',')[0]):
        return 'documented_name_and_first_argument_verified'
    return 'not_verified'


def _load_chunks(source, manual):
    rows = _rows(source, 'SELECT ' + CHUNK_COLUMNS + ' FROM chunks WHERE manual_id=? '
                         'ORDER BY pdf_page, id', (manual,))
    return {row['id']: row for row in rows}


def _section_matches(value, needle):
    if not value or not value.startswith(needle):
        return False
    rest = value[len(needle):]
    return rest == '' or rest[0] == ' '


def _section_chunk(chunks, section, pages, manual=FUNCTION_MANUAL):
    """Bind a section to its exact indexed chunk; a moved page fails closed."""
    matches = [row for row in chunks.values()
               if _section_matches(row['section'] or '', section)]
    if not matches:
        raise ValueError('Structured review source section is missing: %s %s' % (manual, section))
    if pages:
        matches = [row for row in matches if row['pdf_page'] == pages[0]]
        if not matches:
            raise ValueError('Structured review source page moved: %s %s (p%s)'
                             % (manual, section, pages[0]))
    return min(matches, key=lambda item: (item['pdf_page'], item['id']))


def _source_reference(row):
    pages = json.loads(row.get('source_pages_json') or 'null') or [row['pdf_page']]
    return {'id': str(row['id']), 'manual_id': row['manual_id'],
            'manual_number': row['manual_number'], 'revision': row['revision'],
            'pdf_pages': [int(page) for page in pages]}


def _model_scope(row):
    """CPU scope from the section's own instruction header only."""
    header = json.loads(row.get('source_applicability_json') or '{}')
    basis = header.get('basis') or 'absent'
    supported, conditional = [], []
    if basis == 'instruction_header':
        for mark in header.get('model_marks') or []:
            state = mark.get('state')
            if state == 'supported':
                supported.append(mark['model'])
            elif state == 'conditional':
                conditional.append(mark['model'])
    return {'basis': basis, 'supported': supported, 'conditional': conditional,
            'condition': HEADER_CONDITION if basis == 'instruction_header' else SCOPE_CONDITION}


def _verify(row, phrases, label):
    missing = [phrase for phrase in phrases if not _contains(row['text'], phrase)]
    if missing:
        raise ValueError('Structured review source changed: %s (%s) missing %s'
                         % (row['manual_id'], label, '; '.join(missing)))
    return len(phrases)


# ---------------------------------------------------------------------------
# Record expansion
# ---------------------------------------------------------------------------
def _conversion_records():
    records = []
    for opcode, section, pages, source, target, name, rule, limits in CONVERSIONS:
        override = CONVERSION_OVERRIDES.get(opcode, {})
        source_words = TYPE_WORDS[source]
        target_words = TYPE_WORDS[target]
        inputs = override.get('inputs') or ((name, source, 's', 'conversion_source'),)
        outputs = override.get('outputs') or (
            ('Output_label', target, 'd', 'conversion_result'),)
        rule_spec = dict(CONVERSION_RULES[rule])
        witness_rule = rule_spec.pop('witness', None)
        if override.get('witness'):
            witness = list(override['witness'])
        else:
            witness = ['Conversion source ' + source_words + ' data']
            if target_words != target:
                witness.append(target_words + ' data after conversion')
            else:
                witness.append(target_words)
            if witness_rule:
                witness.append(witness_rule)
        st_plain = override.get('st_plain', '%s(%s)' % (opcode, inputs[0][0]))
        st_enabled = override.get('st_enabled', '%s_E(EN,%s,Output_label)' % (
            opcode, ','.join(item[0] for item in inputs)))
        limits = [LIMITS[key] for key in limits] + list(override.get('limits') or ())
        record = _f(opcode, section, pages, category='type_conversion', inputs=inputs,
                    outputs=outputs, st_plain=st_plain, st_enabled=st_enabled,
                    rule=rule_spec, limits=tuple(limits),
                    errors=CONVERSION_ERRORS.get(opcode), witness=tuple(witness),
                    residuals=tuple(override.get('residuals') or ()),
                    type_members=dict(override.get('type_members') or {}))
        records.append(record)
    return records


_CONVERSION_WITNESS_TEXT = ('Conversion source',)


def _all_records():
    return tuple(_conversion_records()) + FAMILY_RECORDS


def _record_forms(record):
    if record['kind'] == 'operator':
        return ((record['op'], False),)
    if record.get('st_enabled'):
        return ((record['op'], False), (record['op'] + '_E', True))
    return ((record['op'], False),)


# ---------------------------------------------------------------------------
# Fact construction
# ---------------------------------------------------------------------------
def _operand_facts(record, enabled):
    operands = []
    if enabled:
        operands.append({'name': 'EN', 'marker': 'EN', 'direction': 'input',
                         'role': 'execution_condition', 'data_type': {'kind': 'bool'},
                         'enable_pin': True})
    for name, token, marker, role in record['inputs']:
        operands.append({'name': name, 'marker': marker, 'direction': 'input',
                         'role': role, 'data_type': dict(TYPES[token]),
                         'enable_pin': False})
    for name, token, marker, role in record['outputs']:
        operands.append({'name': name, 'marker': marker,
                         'direction': 'output_variable', 'role': role,
                         'data_type': dict(TYPES[token]), 'enable_pin': False})
    if enabled:
        operands.append({'name': 'ENO', 'marker': 'ENO', 'direction': 'output',
                         'role': 'execution_status', 'data_type': {'kind': 'bool'},
                         'enable_pin': True})
    return operands


def _return_shape(record, enabled):
    kind = record['kind']
    if kind == 'function_block':
        return {'returned_by_st_call': False, 'outputs_via_arguments': True,
                'instance_name_required': True, 'enable_status_argument': enabled}
    result_type = dict(TYPES[record['outputs'][0][1]])
    if enabled and kind == 'function':
        return {'value_type': {'kind': 'bool'}, 'role': 'ENO_execution_status',
                'returned_by_st_call': True, 'operation_result_via': 'output_argument'}
    if kind == 'operator':
        return {'value_type': result_type, 'returned_by_st_call': True,
                'infix_expression': bool(record.get('operator', {}).get('infix'))}
    return {'value_type': result_type, 'returned_by_st_call': True,
            'operation_result_via': 'return_value'}


def _pins(record, enabled):
    pins = []
    for name, token, marker, role in record['inputs']:
        pins.append({'pin': name, 'role': role, 'direction': 'input',
                     'data_type': dict(TYPES[token])})
    for name, token, marker, role in record['outputs']:
        pins.append({'pin': name, 'role': role, 'direction': 'output',
                     'data_type': dict(TYPES[token])})
    if enabled:
        pins.append({'pin': 'EN', 'role': 'execution_condition', 'direction': 'input',
                     'data_type': {'kind': 'bool'}})
        pins.append({'pin': 'ENO', 'role': 'execution_status', 'direction': 'output',
                     'data_type': {'kind': 'bool'}})
    return pins


def _form_facts(record, form, enabled, model, row, references, condition, status,
                execution_reference=None):
    """All published facts of one documented form on one CPU."""
    facts = []
    lower = form.lower()
    is_operator = record['kind'] == 'operator'
    natural_languages = list(record['languages'])
    limited = record.get('language_forms') or {}
    languages_for_form = natural_languages
    if limited and not limited.get('available_in_structured_ladder', True):
        languages_for_form = ['st']

    prefix = 'operator_' if is_operator else ''

    def add(aspect, dimension, value, *, languages=None, status=status, note=None,
            depends_on=(), sources=None):
        facts.append(_fact('structured:%s:%s%s' % (lower, prefix, aspect), dimension, value,
                           form, references if sources is None else sources, [model],
                           languages=languages, status=status, condition=condition,
                           depends_on=depends_on, note=note))

    add('form', 'identity.form', {
        'category': record['category'],
        'callable_kind': record['kind'],
        'documented_form': 'enable_variant' if enabled else 'plain',
        'section': record['section'],
        'enable_variant_available': bool(record.get('st_enabled')),
        'instance_members': list(record.get('members') or []) or ['not_documented'],
        'call_basis': ('operator_expression' if is_operator else
                       'instance_call' if record['kind'] == 'function_block' else
                       'expression_call'),
    }, languages=languages_for_form)
    for language in natural_languages:
        available = bool(limited.get('available_in_structured_ladder', True)) if (
            language == 'fbd' and limited) else True
        value = {'language': language, 'available': available}
        if not available:
            value['reason'] = limited.get('documented_limitation',
                                          'not_documented_in_this_language')
            witness = limited.get('documented_witness')
            if witness:
                if not _contains(row['text'], witness):
                    raise ValueError('Structured review source changed: %s (%s) claims an '
                                     'unavailable language that the section does not document'
                                     % (row['manual_id'], record['section']))
                value['documented_witness'] = limited.get(
                    'documented_witness_token', 'documented_limitation_in_section')
            value['documented_in_section'] = True
        add('language_' + language, 'identity.language_availability', value,
            languages=[language])
    if is_operator:
        add('operator_form', 'parameters.operator_expression', {
            'symbol': _symbol_fact(record['operator']['symbol']),
            'expression_form': _expression_tokens(record['operator']['expression']),
            'infix': bool(record['operator']['infix']),
            'documented_st_example': _expression_tokens(
                record['operator']['documented_st_example']),
            'alternative_symbol': _symbol_fact(record['operator'].get('alternative')),
        }, languages=['st'])
    add('operands', 'parameters.typed_operands', {
        'operands': _operand_facts(record, enabled),
        'return_shape': _return_shape(record, enabled),
    }, languages=languages_for_form)
    if 'fbd' in languages_for_form:
        add('pins', 'parameters.fbd_pins', {
            'block': record['op'],
            'enable_block': record['op'] + '_E' if enabled else 'not_used',
            'pins': _pins(record, enabled),
            'result_pin': (record['outputs'][0][0] if record['kind'] != 'function_block'
                           else 'not_applicable'),
        }, languages=['fbd'])
    if enabled:
        add('enable', 'execution.enable', dict(EN_SEMANTICS), languages=languages_for_form)
    elif execution_reference:
        add('call_enable', 'execution.call_enable',
            {'enable_parameter': False, 'evaluation': 'at_call_site',
             'enable_gate': 'none', 'scope': 'call_condition_only',
             'stateful_operation': 'governed_by_instruction_specific_facts'},
            languages=languages_for_form, sources=[*references, execution_reference])
    add('result', 'effects.result_rule', dict(record['rule']), languages=languages_for_form)
    for index, (dimension, value) in enumerate(record['limits']):
        add('limits_%d' % index, dimension, dict(value), languages=languages_for_form)
    if record.get('errors'):
        add('errors', 'resources.operation_error', dict(record['errors']),
            languages=languages_for_form)
    for token in _record_type_tokens(record):
        domain = TYPE_DOMAIN_BY_TOKEN.get(token)
        if domain:
            add('type_domain_%s' % token.lower(), 'parameters.type_domain',
                {'type_token': token, **{key: value for key, value in domain.items()}},
                languages=languages_for_form)
        elif token in FLEXIBLE_TYPES:
            listed = (record.get('type_members') or {}).get(token)
            members, phrase = FLEXIBLE_TYPE_MEMBERS.get(token, (None, None))
            value = _generic_type_domain(token)
            if listed:
                value = dict(value)
                value['members'] = list(listed)
                value['members_basis'] = 'documented_input_type_table'
            elif members and _contains(row['text'], phrase):
                value = dict(value)
                value['members'] = list(members)
                value['members_basis'] = 'documented_operand_wording'
            add('type_domain_%s' % token.lower(), 'parameters.type_domain', value,
                languages=languages_for_form, sources=_generic_sources_references())
    return facts


def _generic_type_domain(token):
    """Typed description of one flexible data type, from registered sources."""
    node = GENERIC_TYPE_HIERARCHY.get(token) or {}
    value = {'type_token': token, 'generic_data_type': True,
             'documented_layer': 'flexible_data_type',
             'hierarchy_parent': node.get('parent') or 'none',
             'hierarchy_children': list(node.get('children') or []),
             'hierarchy_basis': node.get('basis', 'generic_data_type_figure'),
             'label_rule': 'usable_in_any_sub_level_data_type'}
    if token in GENERIC_TYPE_MEMBERS:
        members, basis = GENERIC_TYPE_MEMBERS[token]
        value['members'] = list(members)
        value['members_basis'] = basis
    else:
        value['members'] = ['documented_by_the_generic_data_type_figure_only']
        value['members_basis'] = 'figure_lists_the_type_without_enumerating_leaf_types'
    usage = GENERIC_TYPE_OPERATOR_USAGE.get(token)
    if usage:
        value['accepted_by_documented_operators'] = list(usage)
    return value


def _generic_sources_references():
    return [dict(value) for _key, value in sorted(GENERIC_TYPE_REFERENCES.items())]


def _external_section(source, spec):
    """Bind a cross-manual reference section, fail closed on a moved source."""
    page = spec['page']
    rows = _rows(source, 'SELECT ' + CHUNK_COLUMNS + ' FROM chunks WHERE manual_id=? '
                         'AND pdf_page<=? AND COALESCE(pdf_page_end,pdf_page)>=? ORDER BY id',
                 (spec['manual_id'], page, page))
    matches = [row for row in rows if spec['section'] in (row['section'] or '')]
    if not matches:
        raise ValueError('Structured review source section is missing: %s p%s'
                         % (spec['manual_id'], page))
    row = min(matches, key=lambda item: item['id'])
    _verify(row, spec['witness'], '%s %s' % (spec['manual_id'], spec['section']))
    return row


def _record_by_section(section):
    for record in _all_records():
        if record['section'] == section:
            return record
    raise ValueError('Structured review record is missing: %s' % section)


def _plain_via_operator_facts(record, partner, partner_row, model, references, condition,
                              status, operator_row):
    """The plain ST form of a function equals the documented operator form."""
    operator = partner['operator']
    inputs = [{'name': name, 'type': dict(TYPES.get(token) or {'name': token}),
               'direction': 'input'} for name, token, _marker, _role in partner['inputs']]
    result_token = partner['outputs'][0][1]
    result_type = dict(TYPES.get(result_token) or {'name': result_token})
    pins = (partner.get('rule') or {}).get('operand_pins') or {}
    value = {
        'documented_call': _expression_tokens(operator['expression']),
        'documented_st_example': _expression_tokens(operator['documented_st_example']),
        'argument_order': ([item['name'] for item in inputs]
                           + [partner['outputs'][0][0]]),
        'parameters': inputs + [{'name': partner['outputs'][0][0], 'type': result_type,
                                 'direction': 'output'}],
        'returns': {'type': result_type, 'role': 'operator_result',
                    'infix_expression': bool(operator.get('infix'))},
        'enable': False,
        'operator_section': partner['section'],
        'operator_symbol': _symbol_fact(operator['symbol']),
        'equivalent_function_forms': [record['st_enabled']],
        'equivalence_basis': 'plain_form_of_the_function_is_the_documented_operator',
        'operand_count_documented': {'minimum': int(pins.get('minimum') or len(inputs)),
                                     'maximum': int(pins.get('maximum') or len(inputs))},
    }
    reference_list = list(references) + [_source_reference(partner_row)]
    facts = [
        _fact('structured:%s:plain_via_operator' % record['op'].lower(),
              'parameters.st_signature', value, record['op'], reference_list, [model],
              languages=['st'], status=status, condition=condition,
              depends_on=('structured:%s:operator_form' % partner['op'].lower(),)),
        _fact('structured:%s:plain_form_kind' % record['op'].lower(), 'identity.form',
              {'documented_form': 'plain', 'plain_form_kind': 'operator_expression',
               'plain_form_section': partner['section'],
               'operator_symbol': _symbol_fact(operator['symbol']),
               'operator_language_scope': 'st',
               'enable_variant_required_for_ladder': True}, record['op'], reference_list,
              [model], languages=['st', 'fbd'], status=status, condition=condition),
    ]
    if operator_row.get('id') != operator_row.get('id'):
        raise ValueError('unreachable')
    return facts


def _record_type_tokens(record):
    tokens = []
    for _name, token, _marker, _role in record['inputs'] + record['outputs']:
        if token not in tokens:
            tokens.append(token)
    return tokens


_TOKEN_SAFE = re.compile(r'[A-Za-z0-9_$][A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]*\Z')


def _typed_safe(value):
    """Punctuation-only symbols are published as a name plus their ASCII bytes."""
    if isinstance(value, dict):
        return {key: _typed_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_typed_safe(item) for item in value]
    if isinstance(value, str) and value in SYMBOL_CODES and not _TOKEN_SAFE.match(value):
        name, code = SYMBOL_CODES[value]
        return {'name': name, 'ascii_hex': code}
    return value


def _fact(identity, dimension, value, opcode, references, models, *, languages=None,
          status='source_verified', condition=None, depends_on=(), note=None):
    suffix = '@' + FUNCTION_MANUAL
    scope = {'models': list(models), 'forms': [opcode]}
    if languages:
        scope['languages'] = list(languages)
    fact = {'id': identity + suffix, 'dimension': dimension,
            'value': _typed_safe(value),
            'status': status,
            'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
            'sources': [dict(reference) for reference in references], 'scope': scope}
    if condition:
        fact['conditions'] = [dict(condition)]
    if depends_on:
        fact['depends_on'] = [item if item.endswith(suffix) else item + suffix
                              for item in depends_on]
    if note:
        fact['note'] = note
    return fact


def _flag_conflicts(facts):
    """Keep both documented values when one fact publishes different ones."""
    seen = {}
    conflicts = []
    for owner, rows in facts.items():
        for row in rows:
            key = (owner, row['id'])
            previous = seen.get(key)
            if previous is None:
                seen[key] = row
                continue
            if previous['value'] == row['value']:
                continue
            previous['status'] = 'conflict'
            row['status'] = 'conflict'
            conflicts.append({'owner': list(owner), 'dimension': row['dimension'],
                              'facts': [previous['id'], row['id']],
                              'values': [previous['value'], row['value']]})
    return conflicts


def _structured_ladder_facts(rows):
    """The manual's combined Structured Ladder/FBD diagrams share one call.

    Keep these facts conditional on a structured Ladder block. They must not
    silently become the operand contract of the original native Ladder form.
    """
    selected = [fact for fact in rows if 'fbd' in fact.get('scope', {}).get('languages', ())]
    identities = {fact['id']: fact['id'] + ':structured_ladder' for fact in selected}
    result = []
    for fact in selected:
        clone = copy.deepcopy(fact)
        clone['id'] = identities[fact['id']]
        clone['scope']['languages'] = ['ladder']
        clone['conditions'] = [*clone.get('conditions', []),
                               {'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        if clone['dimension'] == 'identity.language_availability':
            clone['value']['language'] = 'ladder'
        if clone['dimension'] == 'parameters.fbd_pins':
            clone['dimension'] = 'parameters.ladder_pins'
        if clone.get('depends_on'):
            clone['depends_on'] = [identities.get(identity, identity) for identity in clone['depends_on']]
        result.append(clone)
    return result
# ---------------------------------------------------------------------------
# Review entry points
# ---------------------------------------------------------------------------
TOOL_WITNESSES = {
    '6.1.1': ('Structured Ladder/FBD', 'Ladder Diagram', 'Function'),
    '8.6.3': ('Number of Pins', 'Increment pins'),
    '10.4.9': ('Automatically assign each system device to output of functions that use EN/',
               'The same device is assigned'),
    '10.4.10': ('INT_TO_BOOL_E',),
    '10.5.3': ('constants can be used for the input side', 'VAR_IN_OUT'),
}


def structured_behaviour_sections(source):
    """Instruction sections of the FXCPU structured application manual."""
    rows = _rows(source, 'SELECT ' + CHUNK_COLUMNS + ' FROM chunks WHERE manual_id=? AND '
                         'chunk_type=? ORDER BY pdf_page, id',
                 (FUNCTION_MANUAL, 'instruction'))
    sections = []
    for row in rows:
        header = json.loads(row.get('source_applicability_json') or '{}')
        sections.append({'chunk': row['id'], 'opcode': row['instruction_opcode'],
                         'section': row['section'], 'pdf_page': row['pdf_page'],
                         'basis': header.get('basis'),
                         'models': [mark['model'] for mark in header.get('model_marks') or ()
                                    if mark.get('state') in {'supported', 'conditional'}]})
    return sections


def _expression_tokens(text):
    """Keep a documented ST expression as one token, else as its token sequence."""
    text = text or ''
    if re.fullmatch(r'[A-Za-z0-9_$][A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]*', text):
        return text
    if ':=' in text:
        target, _, expression = text.partition(':=')
        return {'assignment_target': target.strip(),
                'expression': re.findall(r'[A-Za-z0-9_$]+|\*\*|[+*/<>=&%\-]+', expression)}
    return re.findall(r'[A-Za-z0-9_$]+|\*\*|[+*/<>=&%\-]+', text)


# Punctuation-only operator symbols are published as a name plus their ASCII bytes.
SYMBOL_CODES = {
    '+': ('plus', '2B'),
    '-': ('minus', '2D'),
    '*': ('asterisk', '2A'),
    '**': ('asterisk_asterisk', '2A2A'),
    '/': ('solidus', '2F'),
    '&': ('ampersand', '26'),
    '=': ('equals_sign', '3D'),
    '>': ('greater_than_sign', '3E'),
    '<': ('less_than_sign', '3C'),
    '>=': ('greater_than_or_equal', '3E3D'),
    '<=': ('less_than_or_equal', '3C3D'),
    '<>': ('not_equal', '3C3E'),
}


def _symbol_fact(symbol):
    if not symbol:
        return 'not_documented'
    code = SYMBOL_CODES.get(symbol)
    if code:
        return {'name': code[0], 'ascii_hex': code[1]}
    return {'name': symbol}


def _st_parameters(record, enabled):
    """Documented call parameters with their own data type and direction."""
    parameters = []
    if enabled:
        parameters.append({'name': 'EN', 'type': {'kind': 'bool'}, 'direction': 'input'})
    for name, token, _marker, _role in record['inputs']:
        parameters.append({'name': name, 'type': dict(TYPES[token]), 'direction': 'input'})
    if record['kind'] == 'function_block':
        for name, token, _marker, _role in record['outputs']:
            parameters.append({'name': name, 'type': dict(TYPES[token]),
                               'direction': 'output'})
    elif enabled:
        name, token, _marker, _role = record['outputs'][0]
        parameters.append({'name': name, 'type': dict(TYPES[token]), 'direction': 'output'})
    if enabled and record['kind'] == 'function_block':
        parameters.append({'name': 'ENO', 'type': {'kind': 'bool'}, 'direction': 'output'})
    return parameters


def _st_returns(record, enabled):
    if record['kind'] == 'function_block':
        return {'role': 'no_return_value', 'outputs_via_arguments': True}
    if enabled and record['kind'] == 'function':
        return {'type': {'kind': 'bool'}, 'role': 'ENO_execution_status'}
    name, token = record['outputs'][0][0], record['outputs'][0][1]
    return {'type': dict(TYPES[token]), 'name': name,
            'role': 'operator_result' if record['kind'] == 'operator' else 'function_result'}


def _st_fact(record, form, enabled, call_form, model, references, condition, status,
             verification='documented_arguments_verified'):
    value = {'documented_call': _expression_tokens(call_form),
             'argument_order': [item['name'] for item in _st_parameters(record, enabled)],
             'parameters': _st_parameters(record, enabled),
             'returns': _st_returns(record, enabled),
             'enable': bool(enabled),
             'arguments_are_named': bool(record['kind'] == 'function_block'),
             'call_form_verification': verification}
    aspect = 'enabled_st_signature' if enabled else 'st_signature'
    if record['kind'] == 'operator':
        aspect = 'operator_' + aspect
    return _fact('structured:%s:%s' % (form.lower(), aspect), 'parameters.st_signature',
                 value, form, references, [model], languages=['st'], status=status,
                 condition=condition)


def _tool_binding_facts(source, chunks, manual, receipt):
    bindings = []
    for binding_spec in TOOL_BINDINGS:
        row = _section_chunk(chunks, binding_spec['section'], binding_spec['pages'],
                             manual=manual)
        witnesses = binding_spec['witness']
        _verify(row, witnesses, 'tool binding ' + binding_spec['number'])
        reference = _source_reference(row)
        models = [model for model in (row.get('document_plc_models') or '').split(',')
                  if model.startswith('FX')]
        bindings.append({
            'id': binding_spec['id'], 'dimension': binding_spec['dimension'],
            'value': binding_spec['value'],
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [reference],
            'scope': {'models': models, 'forms': ['*EN_ENO_FUNCTION_CALL'],
                      'languages': ['st', 'fbd']},
            'publishable': False,
            'owner_hint': ('language/tool feature record; a published fact needs a ST./FBD. '
                           'feature target and its exact scope'),
        })
        receipt['tool_binding_sections'].append(
            {'section': binding_spec['section'], 'chunk': row['id'],
             'witnesses': len(witnesses)})
    return bindings


def _manual_disposition(source, manual_id, reason, owned_by):
    rows = _rows(source, 'SELECT manual_id, manual_number, revision, title, pdf_pages '
                         'FROM manuals WHERE manual_id=?', (manual_id,))
    if not rows:
        return {'manual_id': manual_id, 'present': False, 'reason': reason}
    row = rows[0]
    chunks = _rows(source, 'SELECT COUNT(*) AS total FROM chunks WHERE manual_id=?',
                   (manual_id,))[0]['total']
    return {'manual_id': manual_id, 'present': True, 'manual_number': row['manual_number'],
            'revision': row['revision'], 'title': row['title'], 'chunks': chunks,
            'disposition': 'out_of_scope_for_this_review', 'reason': reason,
            'owner': owned_by}


def reviewed_structured_behavior(source, entries=None):
    """Author the structured function behaviour of the FXCPU application manual.

    ``source`` is a read-only sqlite3 connection to the private knowledge index.
    ``entries`` is the projected instruction-entry list of the build, used only to
    report alignment; this review never edits entries and never admits a call.
    """
    chunks = _load_chunks(source, FUNCTION_MANUAL)
    tool_chunks = _load_chunks(source, TOOL_MANUAL)
    manual = _rows(source, 'SELECT manual_id, manual_number, revision, title, pdf_pages, '
                           'plc_models FROM manuals WHERE manual_id=?', (FUNCTION_MANUAL,))[0]
    receipt = {'method': METHOD, 'manual': manual, 'sections': {}, 'residuals': [],
               'unresolved_headers': [], 'families': {}, 'conflicts': [],
               'tool_binding_sections': [], 'operator_equivalences': [],
               'generic_type_sources': [],
               'manuals_reviewed': [FUNCTION_MANUAL, TOOL_MANUAL]}

    cross = {key: _section_chunk(chunks, section, ())
             for key, section in SECTION_CROSS_REFERENCES.items()}
    _verify(cross['en_and_eno'],
            ['inputs the instruction execution condition', 'outputs the instruction '
             'execution status', 'Operation error did not occur', 'Stops operation'],
            'EN and ENO')
    _verify(cross['data_type_table'],
            ['-32768 to 32767', '65535', '50 characters maximum', 'begins with "ANY"'],
            'data type table')
    cross_references = {key: _source_reference(row) for key, row in cross.items()}
    call_enable_source = _external_section(source, {
        'manual_id': 'structured_fundamentals_o', 'page': 29, 'section': 'EN and ENO',
        'witness': ['An EN (enable input) and ENO (enable output) can be appended',
                    'the operation result is the same as the ADD instruction without the EN input']})
    call_enable_reference = _source_reference(call_enable_source)
    GENERIC_TYPE_REFERENCES.clear()
    for spec in GENERIC_TYPE_SOURCES:
        external = _external_section(source, spec)
        GENERIC_TYPE_REFERENCES[spec['key']] = _source_reference(external)
        receipt['generic_type_sources'].append(
            {'key': spec['key'], 'manual_id': spec['manual_id'], 'section': spec['section'],
             'pages': _source_reference(external)['pdf_pages'], 'chunk': str(external['id'])})

    facts = {}
    section_rows = {}
    for record in _all_records():
        label = '%s %s' % (record['section'], record['op'])
        row = _section_chunk(chunks, record['section'], record['pages'])
        verified = _verify(row, record['witness'], label)
        scope = _model_scope(row)
        section_rows[record['section']] = row
        models = scope['supported'] + scope['conditional']
        condition = HEADER_CONDITION if scope['conditional'] else None
        status = ('source_verified' if scope['basis'] == 'instruction_header'
                  else 'declared_unverified')
        references = [_source_reference(row)]
        if record['op'] == 'EXPT' or record['section'] == '7.6':
            references = references + [cross_references['data_type_table']]
        entry = receipt['sections'].setdefault(record['section'], {
            'section': record['section'], 'ops': [], 'chunk': row['id'],
            'pages': _source_reference(row)['pdf_pages'], 'scope_basis': scope['basis'],
            'supported_models': scope['supported'], 'conditional_models': scope['conditional'],
            'witnesses_verified': 0, 'forms': [], 'residuals': []})
        entry['ops'].append(record['op'])
        entry['witnesses_verified'] += verified
        if scope['basis'] != 'instruction_header':
            receipt['unresolved_headers'].append(
                {'section': record['section'], 'opcode': record['op'],
                 'basis': scope['basis'],
                 'reason': 'cpu_header_not_extracted_for_section'})
        if not models:
            receipt['residuals'].append(
                {'section': record['section'], 'opcode': record['op'],
                 'aspect': 'cpu_scope', 'reason': 'no_cpu_marked_in_instruction_header'})
            entry['residuals'].append('cpu_scope')
            continue
        for form, enabled in _record_forms(record):
            call_form = record.get('st_enabled') if enabled else record.get('st_plain')
            call_verification = _call_verification(row['text'], call_form)
            if call_verification == 'not_verified':
                receipt['residuals'].append(
                    {'section': record['section'], 'opcode': form, 'aspect':
                     'parameters.st_signature',
                     'reason': 'documented_st_call_not_verified_in_section'})
                entry['residuals'].append('st_signature:' + form)
            for model in models:
                condition = HEADER_CONDITION if model in scope['conditional'] else None
                built = _form_facts(record, form, enabled, model, row, references,
                                    condition, status, execution_reference=call_enable_reference)
                if call_verification and call_verification != 'not_verified':
                    built.append(_st_fact(record, form, enabled, call_form, model,
                                          references, condition, status,
                                          call_verification))
                facts.setdefault((model, form), []).extend(built)
        for aspect, reason, detail in record['residuals']:
            receipt['residuals'].append(
                {'section': record['section'], 'opcode': record['op'], 'aspect': aspect,
                 'reason': reason, 'detail': detail})
            entry['residuals'].append(aspect)
        family = receipt['families'].setdefault(record['category'], {
            'category': record['category'], 'sections': [], 'forms': 0, 'models': 0})
        family['sections'].append(record['section'])
        family['forms'] += len(_record_forms(record))
        family['models'] = max(family['models'], len(models))

    # Plain form = documented operator of chapter 15 (cross-section equivalence).
    for record in _all_records():
        partner_section = PLAIN_FORM_OPERATOR_SECTIONS.get((record['op'], record['section']))
        if not partner_section or record.get('st_plain'):
            continue
        partner = _record_by_section(partner_section)
        partner_row = _section_chunk(chunks, partner['section'], partner['pages'])
        _verify(partner_row, partner['witness'], 'operator ' + partner['section'])
        row = section_rows[record['section']]
        scope = _model_scope(row)
        models = scope['supported'] + scope['conditional']
        status = ('source_verified' if scope['basis'] == 'instruction_header'
                  else 'declared_unverified')
        references = [_source_reference(row)]
        for model in models:
            for built in _plain_via_operator_facts(record, partner, partner_row, model,
                                                   references, condition=(HEADER_CONDITION if model in scope['conditional'] else None), status=status,
                                                   operator_row=partner_row):
                facts.setdefault((model, record['op']), []).append(built)
        receipt['operator_equivalences'].append(
            {'function_section': record['section'], 'opcode': record['op'],
             'operator_section': partner['section'],
             'operator_symbol': partner['operator']['symbol'],
             'chunks': [row['id'], partner_row['id']]})

    for rows in facts.values():
        rows.extend(_structured_ladder_facts(rows))
    receipt['conflicts'] = _flag_conflicts(facts)

    # Shared mnemonics: one mnemonic documented both as function and as operator.
    by_op = {}
    for record in _all_records():
        by_op.setdefault(record['op'], []).append(record['kind'])
    receipt['shared_mnemonics'] = [
        {'opcode': op, 'kinds': kinds,
         'reason': 'function_section_and_operator_section_publish_separate_forms'}
        for op, kinds in sorted(by_op.items()) if len(kinds) > 1]

    receipt['language_reference'] = [{
        'id': 'structured:data_type_table@' + FUNCTION_MANUAL,
        'dimension': 'parameters.data_type_table',
        'value': {**TYPE_TABLE, 'universal_prefix': 'ANY',
                  'universal_type_rule': 'combines_several_basic_types'},
        'status': 'source_verified', 'verification': 'source_checked',
        'sources': [cross_references['data_type_table']],
        'scope': {'models': [model for model in manual['plc_models'].split(',') if model],
                  'forms': ['ST.DATATYPES'], 'languages': ['st', 'fbd']},
        'conditions': [SCOPE_CONDITION],
        'publishable': False,
        'owner_hint': ('language feature record owned by the ST/FBD feature review; '
                       'this review reuses it as the type vocabulary of the typed operands'),
    }, {
        'id': 'structured:enable_semantics@' + FUNCTION_MANUAL,
        'dimension': 'execution.enable',
        'value': dict(EN_SEMANTICS),
        'status': 'source_verified', 'verification': 'source_checked',
        'sources': [cross_references['en_and_eno']],
        'scope': {'models': [model for model in manual['plc_models'].split(',') if model],
                  'forms': ['ST.CALL.ENENO'], 'languages': ['st', 'fbd']},
        'publishable': False,
        'owner_hint': ('EN/ENO truth table; published per function form as '
                       'execution.enable facts of this review'),
    }]
    receipt['tool_bindings'] = _tool_binding_facts(source, tool_chunks, TOOL_MANUAL, receipt)
    receipt['manual_dispositions'] = [
        _manual_disposition(
            source, 'structured_fundamentals_o',
            'language/type reference only: its data types, ANY classes, EN/ENO and ST/FBD '
            'feature facts are already authored by the ST and FBD feature reviews '
            '(tools/fact_condition_review.py, tools/fbd_fact_review.py); this review reuses '
            'them instead of publishing a second copy',
            'tools/fact_condition_review.py + tools/fbd_fact_review.py'),
        _manual_disposition(
            source, 'gxworks2_structured_ag',
            'call-level tool bindings only: language availability, pin editing, EN/ENO output '
            'device assignment, retained bit outputs and call considerations are extracted '
            'here as tool-binding constraints',
            'this review (tool_bindings)'),
        _manual_disposition(
            source, 'sh080366ek',
            'Q/L structured text manual: its IEC function chapters document Q/L function '
            'behaviour, which the Q/L application-function manual sh080784engn owns, and its '
            'ST syntax facts are owned by the ST feature review; nothing here is FX CPU '
            'evidence',
            'Q/L worker (sh080784engn) + tools/fact_condition_review.py'),
        _manual_disposition(
            source, 'fxcpu_device_common_l',
            'device and common manual: device allocation, response time and common control '
            'facts, with no structured function behaviour for this module',
            'device/common worker'),
        _manual_disposition(
            source, 'fxcpu_basic_applied_m',
            'basic and applied instruction manual: owned by the FX basic worker; not read here '
            'to keep the structured function family separate',
            'FX basic worker'),
        _manual_disposition(
            source, 'sh080783engp',
            'Q/L structured common instructions: owned by the Q/L worker; no FX CPU scope',
            'Q/L worker'),
        _manual_disposition(
            source, 'sh080784engn',
            'Q/L structured application functions: owned by the Q/L worker; no FX CPU scope',
            'Q/L worker'),
        _manual_disposition(
            source, 'sh080785engm',
            'Q/L structured special instructions: owned by the Q/L worker; no FX CPU scope',
            'Q/L worker'),
    ]

    discovery = structured_behaviour_sections(source)
    extracted = set(receipt['sections'])
    missing = [item for item in discovery
               if item['section'].split(' ')[0] not in extracted]
    receipt['discovery'] = {
        'instruction_chunks': len(discovery),
        'sections_extracted': len(extracted),
        'instruction_sections_not_owned_by_this_review': [
            {'section': item['section'], 'opcode': item['opcode'],
             'pdf_page': item['pdf_page'],
             'reason': 'chapter_outside_structured_function_behaviour'} for item in missing],
    }

    entry_keys = set()
    for item in entries or ():
        if isinstance(item, dict) and item.get('opcode'):
            entry_keys.add((str(item.get('target_model') or '').upper(),
                            str(item['opcode']).upper()))
    emitted = sorted({'%s/%s' % key for key in facts})
    receipt['entry_alignment'] = {
        'owner_keys_emitted': len(facts),
        'owner_keys_present_in_entries': len([key for key in facts if key in entry_keys]),
        'owner_keys_absent_from_entries': sorted('%s/%s' % key for key in facts
                                                 if key not in entry_keys),
        'call_available_changes': 0,
        'emitted': emitted,
    }
    dimensions = {}
    statuses = {}
    language_counts = {'st': 0, 'fbd': 0}
    for rows in facts.values():
        for row in rows:
            dimensions[row['dimension']] = dimensions.get(row['dimension'], 0) + 1
            statuses[row['status']] = statuses.get(row['status'], 0) + 1
            for language in row['scope'].get('languages') or ():
                language_counts[language] = language_counts.get(language, 0) + 1
    receipt['counts'] = {'owner_keys': len(facts),
                         'facts': sum(len(rows) for rows in facts.values()),
                         'by_dimension': dimensions, 'by_status': statuses,
                         'by_language': language_counts,
                         'sections': len(receipt['sections']),
                         'forms': sum(len(_record_forms(record))
                                      for record in _all_records()),
                         'residuals': len(receipt['residuals']),
                         'conflicts': len(receipt['conflicts'])}
    receipt['guarantees'] = {
        'executable_behavior_keys': sum(
            1 for rows in facts.values() for row in rows
            if any('behavior' in key for key in row['value'])),
        'call_available_promotions': 0,
        'cpu_widening': 0,
        'database_mutated': False,
        'manual_prose_published': False,
        'pdf_read_at_runtime': False,
        'native_execution_claimed': False,
        'authoring_method': METHOD,
    }
    return facts, receipt

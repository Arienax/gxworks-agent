"""Complete, source-bound relations from FX5 processing paragraphs.

This is an offline knowledge author, not an instruction executor.  Each branch
consumes an entire clause.  A neighbouring sentence is used only for an explicit
antecedent in the same physical source block; numerical examples do not establish
general behaviour.  Page-specific notation is never a global PDF glyph mapping.
"""
from __future__ import annotations

import re


def _operand(name):
    return {'operand': name}


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
    title = section.get('section_token', '')
    source = block.get('text', '')
    declared = {str(name).casefold() for name in declared}

    def reviewed(rules, *, dimension='effects.documented_processing', languages=None,
                 content_role='manual_behavior', conditions=None, forms=None, **updates):
        subjects = sorted(set(_operands(rules)))
        # An authored predicate still needs the actual declaration.  Never turn
        # a literal (d), for example, into whichever argument happens to exist.
        if any(name.casefold() not in declared for name in subjects):
            return None
        if page < 194 and languages == ['ladder']:
            conditions = list(conditions or []) + [{'context': 'project_mode', 'equals': 'native_ladder'}]
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

    def full(pattern):
        return re.fullmatch(pattern, clause, re.I)

    # These captions point at the actual table/figure; they are not an omitted
    # independent condition.  Its rows must still pass their own extraction.
    if full(r'The (?:table|following table) (?:below )?shows (?:the )?(?:ON/OFF information used by these instructions|'
            r'digit specification for the data in the device specified by \(s\) and \(d\))\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_table'}],
                        dimension='resources.section_structure', content_role='source_navigation')
    if full(r'(?:The following processing is executed when the OUT instruction is executed:|'
            r'Operation is as follows when annunciator \(F\) is turned (?:ON|OFF)(?: by the OUT F instruction)?[.:])'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_processing_items',
                          'event': 'OUT_execution' if 'OUT instruction' in clause else
                                   'annunciator_OFF' if 'OFF' in clause else 'annunciator_ON'}],
                        dimension='resources.section_structure', content_role='source_navigation', languages=['ladder'])

    # Native edge contacts.  No claim about an ST call, a function block instance
    # or the implementation's saved previous value follows from these clauses.
    if page == 122:
        edge = full(r'The (LDP|LDF) instruction is the (rising|falling) edge pulse operation start instruction, '
                    r'and becomes conductive \(ON\) (only )?at the (rising|falling) edge '
                    r'\((OFF to ON|ON to OFF)\) of the specified bit device\.')
        if (edge and edge[2].lower() == edge[4].lower()
                and (edge[1].upper(), edge[2].lower(), edge[5].upper()) in
                {('LDP', 'rising', 'OFF TO ON'), ('LDF', 'falling', 'ON TO OFF')}):
            rising = edge[2].lower() == 'rising'
            return reviewed([{'kind': 'edge_contact_conduction', 'form': edge[1].upper(),
                              'trigger_object': 'specified_bit_device', 'edge': 'rising' if rising else 'falling',
                              'transition': {'from': not rising, 'to': rising}, 'conductive_only_on_edge': True}],
                            languages=['ladder'], forms=[edge[1].upper()])
        word = full(r'When word devices are specified by bits, this instruction becomes conductive(?: \(ON\))? '
                    r'only when the status of the specified bit changes to ([01])\[GLYPH-F0AE\]([01])\.')
        if word and word[1] != word[2]:
            return reviewed([{'kind': 'edge_contact_conduction', 'device_form': 'word_device_bit',
                              'form': 'LDP' if word[1] == '0' else 'LDF',
                              'transition': {'from': bool(int(word[1])), 'to': bool(int(word[2]))},
                              'conductive_only_on_edge': True}], languages=['ladder'],
                            forms=['LDP' if word[1] == '0' else 'LDF'],
                            notation_review_basis='paired_OFF_ON_and_bit_transition_in_same_paragraph')
        if full(r'When only the LDP instruction is programmed, operation is the same as the conversion of the '
                r'instruction under execution to pulse instruction \(\[GLYPH-F06F\]P\)\.'):
            return reviewed([{'kind': 'conditional_ladder_equivalence',
                              'antecedent': {'contact_sequence': ['LDP']},
                              'consequent': {'contact_sequence': ['LD'], 'following_instruction_form': 'pulse_P'},
                              'scope': 'single_LDP_contact'}], languages=['ladder'], forms=['LDP'])

    if page == 126:
        inverted = full(r'The (LDPI|LDFI) instruction is the (rising|falling) edge pulse NOT operation start instruction, '
                        r'and becomes conductive \(ON\) at (OFF, ON and the falling edge \(ON to OFF\)|'
                        r'the rising edge \(OFF to ON\), OFF and ON) of the specified bit device\.')
        if inverted:
            return reviewed([{'kind': 'inverted_edge_contact_conduction', 'form': inverted[1].upper(),
                              'trigger_object': 'specified_bit_device',
                              'nonconductive_only_on': 'rising_edge' if inverted[1].upper() == 'LDPI' else 'falling_edge',
                              'conductive_states': ['steady_OFF', 'steady_ON',
                                                   'falling_edge' if inverted[1].upper() == 'LDPI' else 'rising_edge']}],
                            languages=['ladder'], forms=[inverted[1].upper()])
        if full(r'Device specified by LDPI, LDFI \| LDPI status \| LDFI status Bit device \| Bit specification of word '
                r'device OFF to ON \| 0\[GLYPH-F0AE\]1 \| OFF \| ON OFF \| 0 \| ON \| ON ON \| 1 \| ON \| ON '
                r'ON to OFF \| 1\[GLYPH-F0AE\]0 \| ON \| OFF'):
            return reviewed([{'kind': 'inverted_edge_contact_truth_table',
                              'input_forms': ['bit_device', 'word_device_bit'],
                              'rows': [{'event': 'rising', 'LDPI': False, 'LDFI': True},
                                       {'event': 'steady_OFF', 'LDPI': True, 'LDFI': True},
                                       {'event': 'steady_ON', 'LDPI': True, 'LDFI': True},
                                       {'event': 'falling', 'LDPI': True, 'LDFI': False}]}], forms=['LDPI', 'LDFI'])

    if page in {128, 129}:
        if full(r'This instruction connects the ladder blocks of two contacts or more in parallel\.'):
            return reviewed([{'kind': 'parallel_block_connection', 'form': 'ORB', 'minimum_contacts_per_block': 2}])
        if full(r'For parallel connection of only one contact, use the OR and ORI instructions; '
                r'there is no need to use this instruction\.'):
            return reviewed([{'kind': 'single_contact_parallel_connection', 'forms': ['OR', 'ORI'],
                              'ORB_required': False}])
        if full(r'The number of parallel circuits that can be connected by the ORB instruction is not limited\. '
                r'\(when the recommended program is used\)'):
            return reviewed([{'kind': 'parallel_block_count', 'form': 'ORB', 'upper_limit': None,
                              'when_program_layout': 'manual_recommended'}])
        if full(r'The ORB instruction can be used by one operation\.'):
            return reviewed([{'kind': 'parallel_block_evaluation', 'form': 'ORB', 'group_operation_count': 1}])
        if full(r'However, note that the LD instruction and the LDI instruction can repeatedly be used 8 times or less\. '
                r'\(when the recommended program is not used\)'):
            return reviewed([{'kind': 'consecutive_operation_limit', 'forms': ['LD', 'LDI'], 'maximum': 8,
                              'when_program_layout': 'not_manual_recommended'}],
                            dimension='constraints.documented_restriction')
        if page == 129 and full(r'This instruction can be used up to 16 times consecutively\.'):
            return reviewed([{'kind': 'consecutive_operation_limit', 'form': 'MPS', 'maximum': 16}],
                            dimension='constraints.documented_restriction')
        if full(r'When MPP instruction is used in between, the number of uses of MPS instruction is decremented by 1\.'):
            return reviewed([{'kind': 'operation_stack_depth_change', 'form': 'MPP', 'stack': 'MPS', 'delta': -1}])
    if page == 133 and full(r'Use of this instruction makes conversion to pulse easier when multiple contacts are connected in series\.'):
        return reviewed([{'kind': 'composite_contact_pulse_application', 'contact_connection': 'series'}],
                        dimension='resources.instruction_usage')
    if page == 134 and full(r'This instruction outputs the operation result up to this instruction to the specified device\.'):
        return reviewed([{'kind': 'output_coil_assignment', 'source': 'preceding_operation_result',
                          'destination': 'specified_device'}])
    if page == 134 and full(r'Condition \| Operation result \| Coil/specified bit When bit device is used \| OFF \| '
                            r'OFF ON \| ON When bit of word device is specified \| OFF \| 0 ON \| 1'):
        return reviewed([{'kind': 'output_coil_truth_table',
                          'rows': [{'operation_result': False, 'bit_device': False, 'word_bit': 0},
                                   {'operation_result': True, 'bit_device': True, 'word_bit': 1}]}])
    if page == 142 and full(r'When signed \(-2147483648 to \+ 2147483647\) or high speed counter is assigned '
                            r'to the LC, the UDCNTF instruction is used\.'):
        return reviewed([{'kind': 'long_counter_form_selection', 'device_class': 'LC',
                          'when_any': ['signed_counter', 'high_speed_counter'], 'form': 'UDCNTF',
                          'signed_domain': {'minimum': -2147483648, 'maximum': 2147483647}}],
                        dimension='parameters.instruction_selection')
    if page == 148 and full(r'Function when a word device is specified by the RST instruction is the same as '
                            r'the following circuit\.') and re.search(r'RST \| D50 \| MOV \| K0 \| D50', source):
        return reviewed([{'kind': 'word_device_reset', 'form': 'RST', 'effect': 'write_zero',
                          'equivalent_instruction': {'form': 'MOV', 'source': 'K0', 'destination': 'same_word_device'}}])
    if page in {154, 155}:
        if full(r'Device \| Name \| Description SM8049 \| ON status annunciator smallest number ?\|? '
                r'When SM8049 is turned ON, SM8048 and SD8049 are enabled\. enabled SM8048 \| Annunciator operation \| '
                r'When one of the F devices is operating, SM8048 turns ON\.'):
            return reviewed([{'kind': 'annunciator_monitor_enable', 'enable_device': 'SM8049', 'enable_state': True,
                              'enabled_devices': ['SM8048', 'SD8049']},
                             {'kind': 'annunciator_monitor_state', 'device': 'SM8048',
                              'when': 'any_F_device_ON', 'state': True}])
        if full(r'SD8049 \| ON status annunciator smallest number ?\|? '
                r'The smallest number of the F devices that are operating is stored\.'):
            return reviewed([{'kind': 'annunciator_minimum_number', 'destination': 'SD8049',
                              'selection': 'minimum_ON_F_device_number', 'enabled_by': 'SM8049'}])

    if page == 152:
        if full(r'When the content of SD63 is 16, annunciator numbers are deleted from SD64 to SD79 by the RST instruction\.'):
            return reviewed([{'kind': 'annunciator_registration_removal', 'form': 'RST',
                              'when': {'device': 'SD63', 'equals': 16},
                              'storage': {'first': 'SD64', 'last': 'SD79'}}])
        if full(r'Also, if an annunciator not registered in SD64 to SD79 turns ON, its number is registered\.'):
            return reviewed([{'kind': 'annunciator_registration_replenishment', 'event': 'RST_removal',
                              'eligible': 'unregistered_ON_annunciator',
                              'storage': {'first': 'SD64', 'last': 'SD79'}}])
        if full(r'When there are two or more unregistered numbers, this instruction adds the numbers '
                r'starting from the smallest annunciator number\.'):
            return reviewed([{'kind': 'annunciator_registration_order', 'event': 'RST_replenishment',
                              'eligible': 'unregistered_ON_annunciators', 'order': 'ascending_annunciator_number'}])
        if full(r'SD63 is not decremented by 1 when the numbers not registered in SD64 to SD79 are turned OFF\.'):
            return reviewed([{'kind': 'annunciator_registered_count_retention', 'device': 'SD63',
                              'when': 'unregistered_annunciator_OFF'}])
    if page == 153:
        if full(r'This instruction sets \(d\) when the command input remains ON continuously for the evaluation time '
                r'\[\(n\)\[GLYPH-F0B4\]100 ms, \(s\)\] or more\.'):
            return reviewed([{'kind': 'continuous_input_evaluation', 'command_state': True,
                              'duration': {'count': _operand('n'), 'unit_ms': 100, 'comparison': '>='},
                              'timer': _operand('s'), 'set_destination': _operand('d')}])
        if full(r'This instruction resets the current value of \(s\) evaluation timer and does not set \(d\) when '
                r'the command time is less than the evaluation time \[\(n\)\[GLYPH-F0B4\]100 ms\]\.'):
            return reviewed([{'kind': 'continuous_input_evaluation',
                              'duration': {'count': _operand('n'), 'unit_ms': 100, 'comparison': '<'},
                              'reset_timer': _operand('s'), 'destination_not_set': _operand('d')}])
        if full(r'Also, this instruction resets the evaluation timer when the command input turns OFF\.'):
            return reviewed([{'kind': 'evaluation_timer_reset', 'trigger': 'command_input_falling_edge',
                              'timer': _operand('s')}])
    if page == 155:
        if full(r'Annunciator \(F device\) that is operating \(in ON status\) is reset when the command input turns ON\.'):
            return reviewed([{'kind': 'annunciator_reset', 'trigger': 'command_input_rising_edge',
                              'target': 'ON_annunciator'}])
        if full(r'This instruction resets the annunciator with the smallest number when multiple annunciators are ON\.'):
            return reviewed([{'kind': 'annunciator_reset_selection', 'eligible': 'ON_annunciators',
                              'selection': 'minimum_annunciator_number', 'reset_count': 1}])
        if full(r'If the command input is turned ON again, this instruction resets the annunciator with the '
                r'next smallest number among annunciators \(F devices\) that are operating\.'):
            return reviewed([{'kind': 'annunciator_reset_selection', 'trigger': 'subsequent_command_input_rising_edge',
                              'eligible': 'remaining_ON_annunciators', 'selection': 'minimum_annunciator_number',
                              'reset_count': 1}])

    if page in {184, 185}:
        if full(r'When this instruction is ON, the coil of the device specified by \(d\) turns ON\.'):
            return reviewed([{'kind': 'master_control_coil', 'subject': _operand('d'),
                              'when_instruction_condition': True, 'coil_state': True}])
        if full(r'Also, the coil becomes a double coil when the same device is used by the OUT instruction, for example\.'):
            return reviewed([{'kind': 'duplicate_coil_use', 'subject': _operand('d'), 'other_form': 'OUT'}])
        if full(r'So, do not use the device specified by \(d\) in other instructions\.'):
            return reviewed([{'kind': 'exclusive_coil_destination', 'subject': _operand('d')}],
                            dimension='constraints.documented_restriction')
        if full(r'This instruction indicates the end of the master control range by the master control release instruction\.'):
            return reviewed([{'kind': 'master_control_range_end', 'form': 'MCR'}])
        if full(r'Do not prefix this instruction with NO contact instruction\.'):
            return reviewed([{'kind': 'instruction_prefix_prohibition', 'form': 'MCR', 'prefix': 'NO_contact'}],
                            dimension='constraints.documented_restriction')
        if full(r'Use these \(MC and MCR\) instructions with same nesting number as a pair\.'):
            return reviewed([{'kind': 'master_control_pairing', 'forms': ['MC', 'MCR'], 'nesting_number_must_match': True}],
                            dimension='constraints.documented_restriction')
        if full(r'Master control instructions can be used in a nested fashion\.'):
            return reviewed([{'kind': 'master_control_nesting', 'supported': True}])
        if full(r'Each master control section is distinguished by nesting \(N\)\.'):
            return reviewed([{'kind': 'master_control_section_identity', 'source': 'nesting_identifier_N'}])
        if full(r'Nesting is available within the range N0 to N14\.'):
            return reviewed([{'kind': 'master_control_nesting_domain', 'device_class': 'N', 'minimum': 0, 'maximum': 14}],
                            dimension='parameters.documented_domain')
        if full(r'A nested structure allows you to create a ladder for successively restricting program execution conditions\.'):
            return reviewed([{'kind': 'nested_master_control_enable', 'condition_composition': 'all_enclosing_conditions'}])
        if full(r'Counters, retentive timers Coils turn OFF but the current status of both count values and contacts is maintained\.'):
            return reviewed([{'kind': 'master_control_disabled_state', 'device_classes': ['counter', 'retentive_timer'],
                              'when_disabled': {'coil': False, 'current_value': 'retained', 'contact': 'retained'}}])
        if full(r'Devices in OUT instruction Forcibly turned OFF\.'):
            return reviewed([{'kind': 'master_control_disabled_state', 'forms': ['OUT'],
                              'when_disabled': {'destination': False}}])
        if full(r'Devices in SET and RST instructions Devices in SFT\(P\) instruction Devices in basic instructions '
                r'and applied instructions Current status is maintained\.'):
            return reviewed([{'kind': 'master_control_disabled_state', 'forms': ['SET', 'RST', 'SFT', 'SFTP'],
                              'other_categories': ['basic_instructions', 'applied_instructions'],
                              'when_disabled': {'destination_state': 'retained'}}])
    if page == 187 and full(r'This instruction branches operation of the sequence program by the CJ instruction '
                            r'or dividing the main routine program into subroutine programs and interrupt programs\.'):
        return reviewed([{'kind': 'program_boundary', 'form': 'FEND',
                          'uses': ['CJ_program_branch', 'main_subroutine_interrupt_partition']}])
    if page == 190 and full(r'The following illustrates how the END and FEND instructions are used properly when '
                            r'a program contains a main routine program, subroutine program, and interrupt program\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'END_FEND_program_layout_figure'}],
                        content_role='source_navigation', dimension='resources.section_structure')
    if page == 190:
        if full(r'This instruction indicates the end of all programs including the main routine program, '
                r'subroutine program, and interrupt program\.'):
            return reviewed([{'kind': 'program_end', 'form': 'END', 'program_classes': ['main', 'subroutine', 'interrupt']}])
        if full(r'When this instruction is executed, the CPU module ends execution of the currently executing program\.'):
            return reviewed([{'kind': 'program_end', 'form': 'END', 'event': 'instruction_execution',
                              'target': 'currently_executing_program'}])
        if full(r'When programming is performed using the engineering tool in ladder edit mode, '
                r'the END instruction is automatically input and cannot be edited\.'):
            return reviewed([{'kind': 'editor_managed_instruction', 'form': 'END', 'editor_mode': 'ladder',
                              'automatic_insertion': True, 'user_editable': False}], dimension='resources.editor_behavior')
    if page == 126:
        inverted_word = full(r'When word devices are specified by bits, this instruction becomes conductive(?: \(ON\))? '
                             r'when the status of the specified bit is 0, 1, and when it changes ([01])\[GLYPH-F0AE\]([01])\.')
        if inverted_word and inverted_word[1] != inverted_word[2]:
            return reviewed([{'kind': 'inverted_edge_contact_conduction', 'device_form': 'word_device_bit',
                              'form': 'LDPI' if inverted_word[1] == '1' else 'LDFI',
                              'conductive_states': ['steady_0', 'steady_1'],
                              'conductive_transition': {'from': int(inverted_word[1]), 'to': int(inverted_word[2])}}],
                            languages=['ladder'], forms=['LDPI' if inverted_word[1] == '1' else 'LDFI'])

    if page in {137, 140, 142}:
        counter = full(r'This instruction increments the current value of the (counter|long counter) specified by \(d\) '
                       r'by 1 when the operation result up to (?:the )?OUT instruction changes from OFF to ON, and when '
                       r'the counter reaches the end of its count \(current value \[GLYPH-F0B3\] set value\), '
                       r'NO contact becomes conductive and NC contact becomes non-conductive\.')
        if counter:
            return reviewed([{'kind': 'counter_increment', 'subject': _operand('d'), 'increment': 1,
                              'trigger': 'preceding_operation_result_rising_edge',
                              'contact_comparison': {'left': 'current_value', 'operator': '>=', 'right': 'set_value'},
                              'contacts_when_comparison_true': {'NO': True, 'NC': False}}], languages=['ladder'])
        if full(r'When the set value is 0, the same processing as for set value 1 is performed\.'):
            return reviewed([{'kind': 'effective_counter_set_value', 'specified': 0, 'effective': 1}], languages=['ladder'])
        if full(r'These instructions count up to the set value when the operation result up to the OUT instruction is ON '
                r'and the coil of the timer/retentive timer specified by \(d\) turns ON\.'):
            return reviewed([{'kind': 'timer_counting', 'subject': _operand('d'),
                              'enabled_when': {'preceding_operation_result': True, 'coil': True},
                              'count_limit': 'set_value'}], languages=['ladder'])
        if full(r'When the timer reaches the end of its count \(current value \[GLYPH-F0B3\] set value\), '
                r'NO contact becomes conductive and NC contact becomes non-conductive\.'):
            return reviewed([{'kind': 'timer_contact_state',
                              'condition': {'left': 'current_value', 'operator': '>=', 'right': 'set_value'},
                              'NO': True, 'NC': False}], languages=['ladder'])
        if full(r'Values used for timers can be set in the range 1 to 32767\.'):
            return reviewed([{'kind': 'timer_preset_domain', 'minimum': 1, 'maximum': 32767}],
                            dimension='parameters.documented_domain', languages=['ladder'])
        if full(r'Actual timer constants are as follows since the OUT, OUTH, and OUTHS instructions '
                r'operate as 100 ms, 10 ms, and 1 ms timers, respectively\.'):
            return reviewed([{'kind': 'timer_resolution', 'unit': 'ms',
                              'forms': {'OUT': 100, 'OUTH': 10, 'OUTHS': 1}}], languages=['ladder'])

    # State retention and table capacity are behaviours, not input validation.
    capacity = full(r'When the content of SD63 is 16 \(16 annunciators are already on\), the annunciator number '
                    r'that turns ON is not stored in SD64 to SD79 even if a new annunciator turns ON\.')
    if capacity and page in {143, 150}:
        return reviewed([{'kind': 'annunciator_registration', 'capacity': 16,
                          'count_device': 'SD63', 'number_storage': {'first': 'SD64', 'last': 'SD79'},
                          'when_full': 'new_ON_number_not_stored'}], languages=['ladder'])
    if page == 143 and full(r'The coil turns OFF, but the contents of SD64 to SD79 do not change\.'):
        return reviewed([{'kind': 'annunciator_OUT_OFF', 'coil': False,
                          'retained_devices': {'first': 'SD64', 'last': 'SD79'}}], languages=['ladder'])

    # Count parameters have defined truncation/modulo behaviour; do not falsely
    # reject every value above the recommended count as an invalid operand.
    modulus = full(r'If (?:a value )?(16|32) or (?:larger|bigger|more) is specified(?: for \((n|s2)\))?, '
                   r'(?:bits?|bit\(s\)) are (shifted|rotated) to the (right|left) by the remainder value of '
                   r'\(?(n|s2)\)?\[GLYPH-F0B8\](16|32)\.')
    if (modulus and int(modulus[1]) == int(modulus[6]) and (not modulus[2] or modulus[2] == modulus[5])
            and page in {166, 168, 431, 434, 436, 439}):
        return reviewed([{'kind': 'effective_bit_count', 'subject': _operand(modulus[5].lower()),
                          'when': {'operator': '>=', 'value': int(modulus[1])},
                          'operation': 'modulo', 'divisor': int(modulus[6]),
                          'effect': modulus[3].lower(), 'direction': modulus[4].lower()}],
                        notation_review_basis='original_PDF_division_symbol_and_written_remainder')
    rotation = full(r'If a value (16|32) or bigger is specified, bits are rotated by the remainder value of '
                    r'n\[GLYPH-F0B8\](16|32)\.')
    if rotation and rotation[1] == rotation[2] and 431 <= page <= 439:
        return reviewed([{'kind': 'effective_bit_count', 'subject': _operand('n'),
                          'when': {'operator': '>=', 'value': int(rotation[1])},
                          'operation': 'modulo', 'divisor': int(rotation[2]), 'effect': 'rotate'}])
    if full(r'If \(n\) exceeds 15, the processing will be done based on the lower 4 bits of \(n\)\.'):
        return reviewed([{'kind': 'effective_bit_count', 'subject': _operand('n'),
                          'when': {'operator': '>', 'value': 15}, 'operation': 'low_bits', 'width_bits': 4}])
    recommended = full(r'Specify any value between 0 and (15|31) for \(n\)\.')
    if recommended:
        return reviewed([{'kind': 'recommended_bit_count', 'subject': _operand('n'),
                          'minimum': 0, 'maximum': int(recommended[1])}], dimension='parameters.documented_domain')
    bit_range = full(r'When \(d\) is a bit device, bits?\(?s?\)? are (?:shifted|rotated) to the (right|left) '
                    r'within the device range specified by digit specification\.')
    if bit_range:
        return reviewed([{'kind': 'bit_group_operation', 'subject': _operand('d'),
                          'when_device_form': 'digit_specified_bit', 'direction': bit_range[1].lower(),
                          'width_source': 'specified_digit_count'}])
    if full(r'The number of bits actually to be rotated is the remainder of \(n\)\[GLYPH-F0B8\]'
            r'\(specified number of bits\)\.') and 431 <= page <= 439:
        if re.search(r'When \(d\) is a bit device', source):
            return reviewed([{'kind': 'effective_bit_count', 'subject': _operand('n'),
                              'when_device_form': {'subject': _operand('d'), 'form': 'digit_specified_bit'},
                              'operation': 'modulo', 'divisor': 'digit_specified_bit_width'}])
    shift = full(r'This instruction shifts the (16|32)-bit data in the device specified by \(d\) to the right '
                 r'by \(n\) bit\(s\) from the most significant bit\.')
    if shift:
        return reviewed([{'kind': 'logical_shift', 'destination': _operand('d'), 'count': _operand('n'),
                          'width_bits': int(shift[1]), 'direction': 'right'}])
    shift_left = full(r'These instructions shift the (16|32)-bit data in the device specified by \(d\) to the left '
                      r'by \(n\) bit\(s\) from the least significant bit\.')
    if shift_left:
        return reviewed([{'kind': 'logical_shift', 'destination': _operand('d'), 'count': _operand('n'),
                          'width_bits': int(shift_left[1]), 'direction': 'left'}])
    if full(r'The \(n\) bit\(s\) from the most significant bit is/are filled with 0\(s\)\.'):
        return reviewed([{'kind': 'shift_vacated_bits', 'count': _operand('n'), 'end': 'most_significant', 'fill': 0}])
    if full(r'\(n\) bits from the least significant bit are filled with "0"\.'):
        return reviewed([{'kind': 'shift_vacated_bits', 'count': _operand('n'), 'end': 'least_significant', 'fill': 0}])
    carry_bit = full(r'Device \| Name \| Description SM700 \| Carry \| ON/OFF according to the status '
                     r'\(1/0\) of the \((n-1|d)\)(th bit| bit)\.')
    if carry_bit and page in {166, 169}:
        position = {'operand': 'n', 'addend': -1} if carry_bit[1].lower() == 'n-1' else _operand('d')
        return reviewed([{'kind': 'carry_bit_mapping', 'device': 'SM700', 'source_bit_position': position,
                          'mapping': [{'bit': 1, 'state': True}, {'bit': 0, 'state': False}]}])
    nothing = full(r'When the value specified for \(n1\) or \(n2\) is 0, the processing is not performed\.')
    if nothing:
        return reviewed([{'kind': 'conditional_no_operation', 'when_any_zero': [_operand('n1'), _operand('n2')]}])

    # Result/status tables retain numerical predicates as results, never as
    # input-operand limits (for example adding two valid REAL operands).
    zero = full(r'(?:Device \| Name \| Description )?(SM\d+) \| Zero \| '
                r'When the operation result is 0, the zero flag is turned ON\.(?: \| \d+)?')
    if zero:
        return reviewed([{'kind': 'result_flag', 'device': zero[1].upper(),
                          'condition': {'result': 0}, 'state': True}], phase='result')
    bound = full(r'(?:Device \| Name \| Description )?(SM\d+(?:, SM\d+)?) ?\|? (Carry|Borrow) \| '
                 r'When the operation result (exceeds the upper|is less than the lower) limit of the '
                 r'data setting range, the (?:carry|borrow) flag is turned ON\.')
    if bound:
        return reviewed([{'kind': 'result_flag', 'devices': bound[1].upper().split(', '),
                          'condition': {'operator': '>' if bound[2].lower() == 'carry' else '<',
                                        'right': 'data_type_maximum' if bound[2].lower() == 'carry' else 'data_type_minimum'},
                          'state': True}], phase='result')
    signed = full(r'(?:Device \| Name \| Description )?(SM\d+) \| Carry \| '
                  r'When the operation result of the signed operation exceeds (\d+), the carry flag is turned ON\.')
    if signed:
        return reviewed([{'kind': 'result_flag', 'device': signed[1].upper(),
                          'condition': {'signed_operation': True, 'operator': '>', 'value': int(signed[2])},
                          'state': True}], phase='result')
    real_zero = full(r'(?:Device \| Name \| Description Condition \| Operation )?SM8020 \| Zero \| The operation result is true The zero flag SM8020 turns on\. '
                     r'"0"\. \(The mantissa part is "0"\)\.')
    if real_zero:
        return reviewed([{'kind': 'result_flag', 'device': 'SM8020',
                          'condition': {'result': 0, 'mantissa': 0}, 'state': True}], phase='result')
    real_overflow = full(r'(?:Device \| Name \| Description Condition \| Operation )?(SM700|SM8022) \| Carry \| '
                         r'The absolute value of the \| The value of \(d\) is the maximum value \(2128\) of '
                         r'32-bit real numbers and the carry flag operation result \[GLYPH-F0B3\] 2128 \| '
                         r'(SM700|SM8022) turns on\.')
    if real_overflow and real_overflow[1] == real_overflow[2]:
        return reviewed([{'kind': 'real_result_saturation', 'format': 'binary32',
                          'condition': {'left': 'absolute_operation_result', 'operator': '>=',
                                        'right': {'base': 2, 'exponent': 128}},
                          'destination': _operand('d'), 'destination_value': {'base': 2, 'exponent': 128},
                          'flag': {'device': real_overflow[1].upper(), 'state': True}}], phase='result',
                        notation_review_basis='original_PDF670_real_status_table')
    if full(r'(?:Device \| Name \| Description Condition \| Operation )?SM8021 \| Borrow \| The absolute value of the \| The value of \(d\) is the minimum value '
            r'\(2-126\) of 32-bit real numbers and the borrow operation result < 2-126 \| flag SM8021 turns on\.'):
        return reviewed([{'kind': 'real_result_saturation', 'format': 'binary32',
                          'condition': {'left': 'absolute_operation_result', 'operator': '<',
                                        'right': {'base': 2, 'exponent': -126}},
                          'destination': _operand('d'), 'destination_value': {'base': 2, 'exponent': -126},
                          'flag': {'device': 'SM8021', 'state': True}}], phase='result',
                        notation_review_basis='original_PDF670_real_status_table')
    wrap = full(r'If (D?(?:INC|DEC)\(P\)(?:_U)?) instruction is executed when contents of device specified by \(d\) '
                r'is (-?\d+), (-?\d+) is stored in the device specified by \(d\)\. \(If (signed|unsigned) is specified\)')
    if wrap:
        return reviewed([{'kind': 'integer_boundary_transition', 'form': wrap[1].upper(),
                          'subject': _operand('d'), 'signed': wrap[4].lower() == 'signed',
                          'before': int(wrap[2]), 'after': int(wrap[3])}], phase='result',
                        forms=[wrap[1].upper().replace('(P)', ''), wrap[1].upper().replace('(P)', 'P')])
    bcd = full(r'If the addition result exceeds (9999|99999999), carry is ignored\.')
    if bcd:
        return reviewed([{'kind': 'bcd_result_overflow', 'operation': 'add', 'threshold': int(bcd[1]),
                          'carry_handling': 'discarded'}], phase='result')

    # Bit destinations do not provide the high word/remainder storage of a word
    # destination.  Keep this distinction in the operation, not in CPU support.
    if full(r'If \(d\) is specified by bit device, remainder of division results is not stored\.'):
        return reviewed([{'kind': 'division_result_storage', 'destination': _operand('d'),
                          'when_device_class': 'bit', 'quotient_stored': True, 'remainder_stored': False}])
    low_product = full(r'When \(d\) is a bit device, only the lower (?:8 nibbles \(32 bits\)|32 bits) of the '
                       r'multiplication result are stored(?:,? and the (?:higher 8 nibbles \(32 bits\)|upper 32 bits) '
                       r'cannot be specified)\.')
    if low_product:
        return reviewed([{'kind': 'multiplication_result_storage', 'destination': _operand('d'),
                          'when_device_class': 'bit', 'stored_bits': {'start': 0, 'length': 32},
                          'upper_bits_available': False}])
    if full(r'If relevant bit is "0", device specified by \(d\) is turned OFF, and if it is "1", device is turned ON\.'):
        return reviewed([{'kind': 'bit_test_output', 'destination': _operand('d'),
                          'tested_bit_mapping': [{'bit': 0, 'state': False}, {'bit': 1, 'state': True}]}])
    if full(r'When \(d\) is a bit device, lower-order bit is specified first\.'):
        return reviewed([{'kind': 'bit_group_storage_order', 'subject': _operand('d'), 'first': 'least_significant_bit'}])
    if full(r'The relevant devices of \(n\) point\(s\) of data starting from the device specified by \(d\) '
            r'are turned ON when the comparison conditions are met and turned OFF when the comparison conditions are not met\.'):
        return reviewed([{'kind': 'block_comparison_output', 'destination': _operand('d'), 'length': _operand('n'),
                          'per_point_mapping': {'comparison_true': True, 'comparison_false': False}}])
    if full(r'When the comparison operation result is all ON \(1\) in all \(n\) point\(s\) starting from \(d\), '
            r'SM704 and SM8090 \(block comparison signal\) turns ON\.'):
        return reviewed([{'kind': 'block_comparison_all_flag', 'destination': _operand('d'), 'length': _operand('n'),
                          'when': 'all_output_points_ON', 'devices': ['SM704', 'SM8090'], 'state': True}])
    if full(r'Large and small comparison is executed algebraically\.'):
        return reviewed([{'kind': 'magnitude_comparison', 'ordering': 'algebraic'}])
    zone = full(r'These instructions perform a comparison operation on the (16|32)-bit binary data in the device '
                r'specified by \(s1\) and the (16|32)-bit binary data in the device specified by \(s2\) with the '
                r'(16|32)-bit binary data in the device specified by comparison source \(s3\), and according to '
                r'the comparison result \(below, within zone, above\), \(d\), \(d\) \+ 1, or \(d\) \+ 2 is turned ON\.')
    if zone and zone[1] == zone[2] == zone[3]:
        return reviewed([{'kind': 'zone_comparison', 'width_bits': int(zone[1]), 'lower': _operand('s1'),
                          'upper': _operand('s2'), 'tested_value': _operand('s3'),
                          'output': _operand('d'), 'output_offsets': {'below': 0, 'within': 1, 'above': 2}}])
    if page in {199, 202, 205} and full(r'\(s1\)(?:, \(s2\), and| and) \(s[23]\) are handled as binary values '
                                      r'within the range of above data setting\.'):
        names = re.findall(r'\((s[123])\)', clause)
        return reviewed([{'kind': 'source_data_interpretation', 'subjects': [_operand(name) for name in names],
                          'encoding': 'binary', 'domain_reference': 'own_setting_data_table'}],
                        dimension='parameters.input_representation')
    if full(r'When underflow or overflow occurs in the operation result, the following processing is executed\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_result_boundary_processing',
                          'events': ['underflow', 'overflow']}],
                        content_role='source_navigation', dimension='resources.section_structure')
    if full(r'If an underflow occurs, the result will be as follows\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_underflow_result_figure'}],
                        content_role='source_navigation', dimension='resources.section_structure')
    if full(r'In this case, the carry flag \(SM700, SM8022\) does not turn ON\.') and re.search(
            r'When underflow or overflow occurs in the operation result', source):
        return reviewed([{'kind': 'result_flag_nonactivation', 'devices': ['SM700', 'SM8022'],
                          'events': ['underflow', 'overflow']}], phase='result')
    if full(r'For the division result, 32-bit is used for word device to store the quotient and remainder '
            r'and 16-bit is used for bit device to store quotient only\.'):
        return reviewed([{'kind': 'division_result_storage',
                          'branches': [{'destination_class': 'word', 'total_width_bits': 32,
                                        'stored_values': ['quotient', 'remainder']},
                                       {'destination_class': 'bit', 'total_width_bits': 16,
                                        'stored_values': ['quotient']}]}])
    if full(r'Remainder…… Stored in the upper 16 bits\. \(This data can be stored for word device only\.\)'):
        return reviewed([{'kind': 'division_remainder_storage', 'destination_class': 'word',
                          'bit_offset': 16, 'width_bits': 16}])
    if full(r'When nibble is specified ranging from K1 to K8 for \(d\), the result is obtained only for the '
            r'lower-order 32 bits, and is not obtained for the higher-order 32 bits\.'):
        return reviewed([{'kind': 'multiplication_result_storage', 'destination': _operand('d'),
                          'device_form': 'digit_specified_bit', 'digits': {'minimum': 1, 'maximum': 8},
                          'stored_bits': {'start': 0, 'length': 32}, 'upper_bits_available': False}])
    if full(r'For the division result of word device, 64-bit binary is used to store the quotient and remainder\.'):
        return reviewed([{'kind': 'division_result_storage', 'destination_class': 'word',
                          'total_width_bits': 64, 'stored_values': ['quotient', 'remainder']}])
    if full(r'For bit device, 32-bit binary is used to store quotient only\.'):
        return reviewed([{'kind': 'division_result_storage', 'destination_class': 'bit',
                          'total_width_bits': 32, 'stored_values': ['quotient']}])
    if full(r'If specified devices are OFF, device status remains unchanged\.') and title == 'batch_resetting_bit_devices':
        return reviewed([{'kind': 'batch_reset_already_OFF', 'when': 'specified_device_OFF', 'effect': 'unchanged'}])
    group_reset = full(r'(OFF \(reset\)|K0) is written to the entire range of devices from \(d1\) to \(d2\) '
                       r'all at once if \(d1\) and/or \(d2\) are (bit|word) devices\.')
    if group_reset:
        return reviewed([{'kind': 'range_reset', 'first': _operand('d1'), 'last': _operand('d2'),
                          'device_class': group_reset[2].lower(), 'value': 0, 'batch': True}])
    conversion_domain = full(r'(?:Data specified by \(s\)|The data in the device specified by \(s\)) can be converted '
                             r'if it is (?:within the range from K0 to K(9999|99999999) BCD \(decimal\)|'
                             r'in the range from 0 to (9999|99999999) \(BCD\))\.')
    if conversion_domain:
        return reviewed([{'kind': 'conversion_input_domain', 'subject': _operand('s'), 'encoding': 'BCD',
                          'minimum': 0, 'maximum': int(conversion_domain[1] or conversion_domain[2])}],
                        dimension='parameters.documented_domain')
    position_mod = full(r'If (16|32) or more is specified for \(s2\), the value of the remainder of '
                        r'\(s2\)\[GLYPH-F0B8\](16|32) is the bit position\.')
    if position_mod and position_mod[1] == position_mod[2] and page in {315, 317}:
        return reviewed([{'kind': 'effective_bit_position', 'subject': _operand('s2'),
                          'when': {'operator': '>=', 'value': int(position_mod[1])},
                          'operation': 'modulo', 'divisor': int(position_mod[2])}])
    if full(r'SM8021 \| Borrow \| The input value is ignored when it is less The value of \(d\) becomes 0 and '
            r'the borrow flag \(SM8021\) turns on\. than 1\.') and page in {331, 333}:
        return reviewed([{'kind': 'conversion_small_input', 'when_input_less_than': 1,
                          'destination': _operand('d'), 'destination_value': 0,
                          'flag': {'device': 'SM8021', 'state': True}}])
    if full(r'The data of \(s\)\+3 or later is ignored\.'):
        return reviewed([{'kind': 'ignored_input_storage', 'start': {'operand': 's', 'word_offset': 3}}])
    if full(r'Any data stored in the upper byte of the device specified by \(s\)\+5 is ignored\.'):
        return reviewed([{'kind': 'ignored_input_storage', 'start': {'operand': 's', 'word_offset': 5}, 'part': 'upper_byte'}])
    sign = full(r'As sign data, set 20H(?: \(space\))? (?:when|if) the ASCII data is positive, and set 2DH(?: \(-\))? '
                r'(?:when|if) the data is negative\. \(If a value other than 20H and 2DH is set, the data will be processed '
                r'as positive data\.\)')
    if sign:
        return reviewed([{'kind': 'ASCII_sign_interpretation', 'positive_code': '20H', 'negative_code': '2DH',
                          'other_codes': 'positive'}])
    numeric_ascii = full(r'The ASCII data in the device specified by \(s\) to \(s\)\+(2|5) is within the range from '
                         r'(-?\d+) to \+?(\d+) when signed data is specified, and it is within the range from 0 '
                         r'to (\d+) when unsigned data is specified\.')
    if numeric_ascii:
        return reviewed([{'kind': 'ASCII_numeric_input_domain', 'first': _operand('s'),
                          'last_word_offset': int(numeric_ascii[1]),
                          'branches': [{'signed': True, 'minimum': int(numeric_ascii[2]), 'maximum': int(numeric_ascii[3])},
                                       {'signed': False, 'minimum': 0, 'maximum': int(numeric_ascii[4])}]}],
                        dimension='parameters.documented_domain')
    string_number = full(r'These instructions convert the character string in the device numbers specified by \(s\) '
                         r'and later to (16|32)-bit binary data, and store the number of digits in the device specified '
                         r'by \(d1\) and the binary data in the device specified by \(d2\)\.')
    if string_number:
        return reviewed([{'kind': 'character_string_to_binary', 'source': _operand('s'),
                          'width_bits': int(string_number[1]), 'digit_count_destination': _operand('d1'),
                          'binary_destination': _operand('d2')}])
    if full(r'"0" to "F" \(hexadecimal numbers\) in low-order 4 bits \(1 digit\) of \(s\) are decoded to data '
            r'for the seven-segment display unit, and stored in the low-order 8 bits of \(d\)\.'):
        return reviewed([{'kind': 'seven_segment_decoding', 'source': _operand('s'), 'source_low_bits': 4,
                          'input_minimum': 0, 'input_maximum': 15, 'destination': _operand('d'), 'output_low_bits': 8}])
    if full(r'When converting a character string into binary data, the data from the device number specified by '
            r'\(s\) to a device number storing "00H" is handled as a character string\.'):
        return reviewed([{'kind': 'string_storage_extent', 'start': _operand('s'), 'terminator': '00H'}])
    no_processing = full(r'When \(n\) is 0, (?:no processing is performed, and the contents of (?:the device specified '
                         r'by \(d\)|the \(n\) number of devices starting from the one specified by \(d\)) do not change|'
                         r'the processing is not performed)\.')
    if no_processing:
        return reviewed([{'kind': 'conditional_no_operation', 'when': {'subject': _operand('n'), 'equals': 0},
                          **({'retained_destination': _operand('d')} if '(d)' in clause else {})}])
    if full(r'If two or more bits are 1, the higher bit position is processed\.'):
        return reviewed([{'kind': 'bit_selection', 'when_multiple_set_bits': True, 'selection': 'highest_position'}])
    if full(r'Low-order 8 bits of \(d\) are occupied, and high-order 8 bits do not change\.'):
        return reviewed([{'kind': 'partial_destination_write', 'destination': _operand('d'),
                          'written_bits': {'start': 0, 'length': 8}, 'retained_bits': {'start': 8, 'length': 8}}])
    if full(r'If the device numbers specified by \(s1\), \(s2\), \(d\) are partially overlapping, an operation error occurs\.'):
        return reviewed([{'kind': 'operand_area_overlap_error', 'subjects': [_operand('s1'), _operand('s2'), _operand('d')],
                          'overlap': 'partial', 'error_domain': 'operation_error'}], dimension='constraints.documented_restriction')
    if full(r'The higher-order 8 bits of the data of \(n\) words stored in device numbers starting from the one specified '
            r'by \(s\) are ignored\.'):
        return reviewed([{'kind': 'source_low_byte_only', 'source': _operand('s'), 'length_words': _operand('n'),
                          'ignored_bit_range_per_word': {'start': 8, 'length': 8}}])
    if full(r'If \(n\) is an odd number, 0 is stored in the higher-order 8 bits of the device for storing the data of the '
            r'\(n\)th byte\.'):
        return reviewed([{'kind': 'packed_byte_padding', 'length_bytes': _operand('n'), 'when_length_parity': 'odd',
                          'last_word_upper_byte': 0}])
    if full(r'When the command input turns ON, only the specified digits in the transfer destination \(d\) are changed\.'):
        return reviewed([{'kind': 'partial_digit_write', 'destination': _operand('d'), 'event': 'command_input_ON',
                          'written_part': 'specified_digits', 'retained_part': 'other_digits'}])
    if full(r'When SM8168 is set to ON first and then SMOV instruction is executed, conversion from binary to BCD is not executed\.'):
        return reviewed([{'kind': 'digit_transfer_conversion_selection', 'form': 'SMOV',
                          'when': {'device': 'SM8168', 'state_before_instruction': True}, 'binary_to_BCD': False}])
    if full(r'If (?:the device number range is exceeded|the number of points specified by \(n\) exceeds the device number range), '
            r'data is transferred within the possible range\.'):
        return reviewed([{'kind': 'transfer_extent_clamping', 'when': 'requested_end_outside_device_range',
                          'effective_extent': 'available_device_range'}])
    if full(r'Data can be transferred even when the device range of the transfer-source device and '
            r'transfer-destination device is overlapping\.'):
        return reviewed([{'kind': 'overlapping_transfer', 'supported': True}])
    if full(r'To transfer data to a device having a smaller device number, transfer from \(s\), and to transfer data '
            r'to a device having a larger device number, transfer from \(s\)\+\(n\)-1\.'):
        return reviewed([{'kind': 'overlapping_transfer_order', 'source': _operand('s'), 'length': _operand('n'),
                          'destination_before_source': 'ascending', 'destination_after_source': 'descending'}])
    rotate = full(r'These instructions rotate the (16|32)-bit binary data in the device specified by \(d\) '
                  r'to the (right|left) by \(n\) bit\(s\) \((not including|including) the carry flag\)\.')
    if rotate:
        return reviewed([{'kind': 'rotation', 'destination': _operand('d'), 'count': _operand('n'),
                          'width_bits': int(rotate[1]), 'direction': rotate[2].lower(),
                          'carry_in_rotation': rotate[3].lower() == 'including'}])
    if full(r'The carry flag is on or off depending on the status prior to the execution of the instruction\.'):
        return reviewed([{'kind': 'rotation_carry_source', 'source_state': 'prior_to_instruction_execution',
                          'flag_devices': ['SM700', 'SM8022']}])
    if full(r'When a constant \(K\) is specified as the transfer source \(s\), it is automatically converted into binary\.'):
        return reviewed([{'kind': 'constant_conversion', 'subject': _operand('s'), 'constant_class': 'K',
                          'target_format': 'binary', 'automatic': True}], dimension='parameters.input_representation')
    bit_position = full(r'For \(s2\) specify the bit position \(0 to (15|31)\) of (word|double word) data\.')
    if bit_position:
        return reviewed([{'kind': 'bit_position_domain', 'subject': _operand('s2'),
                          'minimum': 0, 'maximum': int(bit_position[1]),
                          'container_width_bits': 32 if bit_position[2].lower() == 'double word' else 16}],
                        dimension='parameters.documented_domain')
    if full(r'If a value 20H or 00H is set in each place of ASCII code, the value will be processed as 30H\.'):
        return reviewed([{'kind': 'ascii_digit_normalization', 'input_codes': ['20H', '00H'], 'effective_code': '30H',
                          'scope': 'each_numeric_ASCII_position'}])
    if full(r'\[GLYPH-F0C0\] MELSEC iQ-F FX5 User\x27s Manual \(Application\) A CPU module which does not '
            r'support SM705 operates in the same way as SM705 is off even if it is turned on\.'):
        return reviewed([{'kind': 'unsupported_selection_device', 'device': 'SM705',
                          'when': {'device_supported_by_CPU': False}, 'effective_state': False,
                          'written_state_ignored': True}],
                        dimension='execution.documented_processing')
    if full(r'When an input value is set from the engineering tool, a rounding error may (?:be )?occur\.'):
        return reviewed([{'kind': 'tool_input_rounding', 'input_origin': 'engineering_tool', 'rounding_error_possible': True}],
                        dimension='parameters.input_representation')
    real_constant = full(r'When the constant \(K or H\) is specified (?:the device specified by|in) '
                         r'(\(s1\)(?:,? \(s2\))?(?:,? and \(s[23]\))), these instructions convert '
                         r'(?:the binary value|values) into single-precision real number automatically\.')
    if real_constant:
        names = re.findall(r'\((s[123])\)', real_constant[1])
        return reviewed([{'kind': 'constant_conversion', 'subjects': [_operand(name) for name in names],
                          'constant_classes': ['K', 'H'], 'source_format': 'binary', 'target_format': 'binary32',
                          'automatic': True}], dimension='parameters.input_representation')
    if full(r'When the operation result is -0 or underflow occurs, the operation result is regarded as 0\.'):
        return reviewed([{'kind': 'real_result_normalization', 'when_any': ['negative_zero', 'underflow'],
                          'effective_result': 0}], phase='result')
    normal_real = full(r'Values in the devices specified \(stored\) by ((?:\([sd][12]?\)(?:,? and |, )?)+) '
                       r'should be 0 or 2 ?-126\[GLYPH-F0A3\]\s*\|specified value \(stored value\)\|\s*<2128\.')
    if normal_real and page in {670, 673, 731}:
        return reviewed([{'kind': 'real_storage_domain',
                          'subjects': [_operand(name) for name in re.findall(r'\(([sd][12]?)\)', normal_real[1])],
                          'zero_allowed': True, 'absolute_nonzero_minimum': {'base': 2, 'exponent': -126, 'inclusive': True},
                          'absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': False}}],
                        dimension='parameters.documented_domain', notation_review_basis='original_PDF_visual_review')
    conflicting_real = full(r'Values in the devices specified \(stored\) by \(s1\), \(s2\), and \(d\) should be '
                            r'0 or 2-126( ?|<)\|specified value \(stored value\)\|\[GLYPH-F0A3\]2128\.')
    if conflicting_real and page in {672, 676, 682, 684}:
        result = reviewed([{'kind': 'real_storage_domain_source_conflict',
                            'subjects': [_operand(name) for name in ['s1', 's2', 'd']],
                            'source_claims': [
                                {'source_region': 'processing_details', 'zero_allowed': True,
                                 'absolute_nonzero_minimum': {'base': 2, 'exponent': -126,
                                                             'operator': '<' if page == 684 else 'not_printed'},
                                 'absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': True}},
                                {'source_region': 'operation_error_3402H', 'zero_allowed': True,
                                 'absolute_nonzero_minimum': {'base': 2, 'exponent': -126, 'inclusive': True},
                                 'absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': False}}],
                            'resolution': 'not_established'}], dimension='parameters.documented_domain',
                          notation_review_basis='original_PDF_visual_review',
                          gap_kind='source_conflict', source_disposition='source_conflict')
        if result:
            result['status'] = 'conflict'
        return result

    if page in {168, 171}:
        carry = full(r'Device \| Name \| Description SM700 \| Carry \| ON/OFF according to the status \(1/0\) '
                     r'of the \((n-1|d)\)(th)? bit\.')
        if carry and ((page == 168 and carry[1] == 'n-1') or (page == 171 and carry[1] == 'd')):
            result = reviewed([{'kind': 'shift_carry_source_conflict', 'destination': 'SM700',
                                'source_claims': [{'source_region': 'related_devices_table',
                                                   'base': _operand('d'),
                                                   'printed_bit': 'n_minus_one' if page == 168 else 'd'},
                                                  {'source_region': 'processing_figure',
                                                   'source': 'last_bit_shifted_out_of_high_end'}],
                                'mapping': {'0': False, '1': True}, 'resolution': 'not_established'}],
                              gap_kind='source_conflict', source_disposition='source_conflict',
                              notation_review_basis='original_PDF_visual_review')
            if result:
                result['status'] = 'conflict'
            return result
    if page == 184 and full(r'Note, however, that when this instruction is nested at a single location, all master controls '
                            r'can be ended by just one \(N\) number, the smallest number\. \(Refer to Caution\.\) '
                            r'\(1\) Executed only when X0 is on Device Device status Timer The count value becomes 0, '
                            r'and both coils and contacts turn OFF\.'):
        return reviewed([{'kind': 'master_control_nested_release', 'form': 'MCR',
                          'when': 'single_nested_location', 'nesting_number': 'smallest', 'released': 'all_nested_controls'},
                         {'kind': 'master_control_inactive_device', 'when_MC_command': False,
                          'device_class': 'timer', 'count': 0, 'coil': False, 'contact': False},
                         {'kind': 'source_figure_annotation', 'figure_label': 1,
                          'example_execution_condition': {'device': 'X0', 'state': True}}])
    if page == 201 and full(r'\(s1\) and \(s2\) are handled as binary values within the range of above data setting\.'):
        return reviewed([{'kind': 'operand_representation', 'subjects': [_operand('s1'), _operand('s2')],
                          'encoding': 'binary', 'width_bits': 32,
                          'domain_reference': 'selected_form_setting_data'}], dimension='parameters.input_representation')
    if page == 245 and full(r'If the upper 32 bits data of the multiplication operation result are required, temporarily '
                            r'store the result in a word device, and transfer the data stored in word device \(\(d\)\+2\) '
                            r'and \(\(d\)\+3\) to the specified bit devices\.'):
        return reviewed([{'kind': 'multiplication_upper_result_transfer', 'when': 'upper_32_bits_required_in_bit_devices',
                          'sequence': [{'operation': 'store_full_result', 'destination_class': 'word_device'},
                                       {'operation': 'copy_words_to_bit_devices', 'source_base': _operand('d'),
                                        'word_offsets': [2, 3]}]}], dimension='resources.instruction_usage')
    if page == 247 and full(r'Transfer the data to word devices once, then execute the operation\.') \
            and 'When nibble is specified ranging from K1 to K8 for (d)' in source:
        return reviewed([{'kind': 'multiplication_full_result_storage',
                          'when': 'bit_group_destination_cannot_receive_high_32_bits',
                          'instruction': 'use_word_devices_before_operation'}], dimension='resources.instruction_usage')
    if page in {252, 253, 254, 255, 257, 258, 260, 261} and full(
            r'In this case, the carry flag \(SM700\) does not turn ON\. [0-9 ]+ [+-] [0-9 ]+ \| [0-9 ]+'):
        subtract = page in {254, 255, 260, 261}
        digits = 8 if page >= 257 else 4
        return reviewed([{'kind': 'BCD_wrap_flag', 'digits': digits,
                          'when': 'difference_below_zero' if subtract else 'sum_exceeds_digit_capacity',
                          'device': 'SM700', 'set_on_wrap': False},
                         {'kind': 'source_numeric_example', 'purpose': 'illustrates_BCD_modulo_wrap',
                          'modulus': 10 ** digits}], phase='result')
    if page in {279, 281, 282, 283} and full(r'Flags \(zero, carry and borrow\) are not activated at this time\.'):
        return reviewed([{'kind': 'binary_wrap_flag_effect', 'when': 'increment_or_decrement_wraps_at_form_domain_boundary',
                          'flags': ['zero', 'carry', 'borrow'], 'activated_by_wrap': False}], phase='result')
    if page in {323, 325, 327, 329}:
        nibble_table = re.search(r'K1([XY])0 \| 1-digit \| 0 to 9', clause)
        rows = re.findall(r'K([1-8])([XY])0 \| ([1-8])-digit \| (0+) to (9+)', clause)
        if nibble_table and rows and all(int(a) == int(c) == len(d) == len(e) for a, b, c, d, e in rows):
            maximum_digits = 8 if page in {325, 329} else 4
            if [int(row[0]) for row in rows] == list(range(1, maximum_digits + 1)):
                return reviewed([{'kind': 'BCD_digit_group_capacity', 'subject': _operand('d' if page in {323, 325} else 's'),
                                  'device_examples': [f'K{row[0]}{row[1]}0' for row in rows],
                                  'rows': [{'nibbles': int(row[0]), 'BCD_digits': int(row[2]),
                                            'minimum': 0, 'maximum': int(row[4])} for row in rows]}],
                                dimension='parameters.documented_domain')
    if page in {335, 337} and full(r'SM8021 \| Borrow \| The input value is ignored when it is less The value of \(d\) '
                                  r'becomes 0 and the borrow flag \(SM8021\) turns on\. than 1\.'):
        rules = [{'kind': 'REAL_conversion_borrow', 'printed_condition': {'subject': _operand('s'), 'operator': '<', 'value': 1},
                  'output': {'subject': _operand('d'), 'value': 0}, 'device': 'SM8021', 'state': True}]
        if page == 335:
            rules.append({'kind': 'source_conflict', 'table_claim': 'input_less_than_one_is_zeroed',
                          'same_page_example': {'input': -123456.7, 'output': -123456},
                          'resolution': 'not_established'})
        result = reviewed(rules, notation_review_basis='original_PDF_visual_review_p335',
                          **({'gap_kind': 'source_conflict', 'source_disposition': 'source_conflict'} if page == 335 else {}))
        if result and page == 335:
            result['status'] = 'conflict'
        return result
    if page == 356:
        if full(r'The setting method of the decimal ASCII data to be set in \(s\) depends on the status of '
                r'SM705 \(Number of conversion digits selection\)\.'):
            return reviewed([{'kind': 'ASCII_input_format_selection', 'subject': _operand('s'), 'selector': 'SM705',
                              'case_table_reference': 'SM705_setting_method'}], dimension='parameters.input_representation')
        if full(r'Status of SM705\*1 \| Setting method of \(s\) \| Reference OFF \| Set \(s\) with a fixed number '
                r'of digits \(a sign \+ 5 digits in Page 355 Setting method of \(s\) for when SM705 \(Number of '
                r'the numeric part\)\. \| conversion digits selection\) is off ON \| Set \(s\) with a desired number '
                r'of digits \(maximum: a \| Page 356 Setting method of \(s\) for when SM705 \(Number of '
                r'sign \+ 5 digits in the numeric part\)\. \| conversion digits selection\) is on'):
            return reviewed([{'kind': 'ASCII_input_format_selection', 'subject': _operand('s'), 'selector': 'SM705',
                              'cases': [{'selector_state': False, 'digit_count': 'fixed', 'sign_characters': 1, 'numeric_digits': 5},
                                        {'selector_state': True, 'digit_count': 'variable', 'sign_characters': 1,
                                         'maximum_numeric_digits': 5}], 'CPU_support_dependency': 'SM705_footnote_1'}],
                            dimension='parameters.input_representation')
    if page == 388 and full(r'These instructions link the lower-order 4 bits of the 16-bit binary data of the \(n\) '
                            r'number of devices starting from the one specified by \(s\) to the device storing '
                            r'16-bit binary data specified by \(d\)\.'):
        return reviewed([{'kind': 'pack_low_nibbles', 'source_base': _operand('s'), 'source_word_count': _operand('n'),
                          'bits_per_source_word': 4, 'destination': _operand('d'), 'destination_width_bits': 16}])
    if page == 400:
        switch = full(r'When using (one|two) sets? of 4 digits \(n = K([12])\) A 4-digit BCD digital switch connected '
                      r'to \(s\) to \(s\)\+([37]) is read in turn by the strobe signal \(d1\) to \(d1\)\+3'
                      r'(, and stored in binary format to \(d2\))?\.')
        if switch and ((switch[2], switch[3]) in {('1', '3'), ('2', '7')}):
            return reviewed([{'kind': 'digital_switch_input', 'when': {'subject': _operand('n'), 'equals': int(switch[2])},
                              'source': {'base': _operand('s'), 'last_offset': int(switch[3])},
                              'strobe': {'base': _operand('d1'), 'last_offset': 3},
                              'input_encoding': 'BCD', 'read_order': 'successive_digits',
                              **({'output': _operand('d2'), 'output_encoding': 'binary'} if switch[4] else {})}])
        if full(r'\(s\) to \(s3\)\+3 is stored in \(d2\) and \(s\)\+4 to \(s\)\+7 is stored in \(d2\)\+1 as BIN format\.'):
            result = reviewed([{'kind': 'digital_switch_source_endpoint_conflict', 'when': {'subject': _operand('n'), 'equals': 2},
                                'printed_first_group_end': {'undeclared_operand': 's3', 'offset': 3},
                                'declared_input_base': _operand('s'),
                                'first_group_destination': _operand('d2'),
                                'second_group': {'source_base': _operand('s'), 'offsets': [4, 7],
                                                 'destination_base': _operand('d2'), 'destination_offset': 1},
                                'output_encoding': 'binary', 'resolution': 'not_established'}],
                              gap_kind='source_conflict', source_disposition='source_conflict',
                              notation_review_basis='original_PDF_visual_review_p400')
            if result:
                result['status'] = 'conflict'
            return result
    if page == 660 and full(r'If the number of characters exceeding \(d\) by one character is specified in \(s2\), '
                            r'the character string in \(s1\) is added to the end of the character string in \(d\)\.'):
        return reviewed([{'kind': 'STRING_append_position', 'position': _operand('s2'),
                          'when_position_equals': {'add': [{'length_of': _operand('d')}, 1]},
                          'appended_string': _operand('s1'), 'destination': _operand('d')}])
    if page == 668 and full(r'These instructions compare the comparison values \(s1\) and \(s2\) with the comparison '
                            r'source \(s3\) as floating point data, and one of the bits among \(d\), \(d\)\+1, and '
                            r'\(d\)\+2 turns on according to the result \(smaller, within the range or larger\)\.'):
        return reviewed([{'kind': 'REAL_zone_comparison', 'lower_bound': _operand('s1'), 'upper_bound': _operand('s2'),
                          'value': _operand('s3'), 'output_base': _operand('d'), 'output_offsets': [0, 1, 2],
                          'result_categories': ['below', 'within', 'above'], 'true_output_count': 1}])

    if page >= 1212:
        arithmetic_types = {1309: 'ADD', 1310: 'MUL', 1312: 'SUB'}
        if page in arithmetic_types:
            dtype = block.get('source_table_column')
            eno = full(r'Even if underflow or overflow occurs in the operation result, it is not regarded as an '
                       r'operation error\. "(ADD|MUL|SUB)_E" outputs "TRUE" from ENO\.')
            if eno and dtype in {'INT', 'DINT'} and eno[1] == arithmetic_types[page]:
                return reviewed([{'kind': 'integer_arithmetic_overflow', 'data_type': dtype,
                                  'events': ['underflow', 'overflow'], 'operation_error': False,
                                  'enabled_form': eno[1] + '_E', 'ENO': True}], phase='result')
            multiply_overflow = full(r'Even when the operation result exceeds the (INT|DINT) type data range, the '
                                     r'(INT|DINT) type data is output\. \(The operation result is the (DINT type|64-bit data), '
                                     r'however, the output data is the (INT|DINT) type data with high-order (16|32) bits deleted\.\)')
            if page == 1310 and multiply_overflow and dtype == multiply_overflow[1] == multiply_overflow[2] == multiply_overflow[4]:
                width = 16 if dtype == 'INT' else 32
                if int(multiply_overflow[5]) == width and multiply_overflow[3] == ('DINT type' if width == 16 else '64-bit data'):
                    return reviewed([{'kind': 'integer_multiplication_truncation', 'output_type': dtype,
                                      'intermediate_width_bits': 2 * width, 'retained_low_bits': width,
                                      'discarded_high_bits': width}], phase='result')
            widen = full(r'When the operation result exceeds the (INT|DINT) type data, convert an input value into '
                         r'the (DINT|REAL) type data by (INT_TO_DINT|DINT_TO_REAL) then perform the operation\.')
            if widen and dtype == widen[1] and (widen[1], widen[2], widen[3]) in {
                    ('INT', 'DINT', 'INT_TO_DINT'), ('DINT', 'REAL', 'DINT_TO_REAL')}:
                return reviewed([{'kind': 'arithmetic_input_widening', 'source_type': widen[1], 'target_type': widen[2],
                                  'conversion_form': widen[3], 'when': 'result_outside_source_type_range',
                                  'order': ['convert_input', 'execute_operation']}], dimension='resources.instruction_usage')
            overflow = full(r'If an (underflow|underflow and an overflow) occurs? in the operation result, '
                            r'an operation error occurs and an undefined value is output\.')
            if overflow and dtype == 'REAL':
                return reviewed([{'kind': 'REAL_arithmetic_overflow', 'operation': arithmetic_types[page],
                                  'printed_event_connection': overflow[1].replace(' ', '_'),
                                  'operation_error': True, 'output': 'undefined'}], phase='result')
        string_position = full(r'A value input to \((n|n2)\) is the INT type data value and within the range from 1 to 255\. '
                               r'\(However, the value must be within the number of characters of the character string '
                               r'to be input to \((s|s1)\)\.\)')
        if string_position:
            return reviewed([{'kind': 'STRING_position_domain', 'subject': _operand(string_position[1]),
                              'data_type': 'INT', 'minimum': 1, 'maximum': 255,
                              'additional_upper_bound': {'character_length_of': _operand(string_position[2])}}],
                            dimension='parameters.documented_domain')
        if full(r'These functions are expressed as follows when the input value is "A" and the output operation result is "B"\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_formula',
                              'symbols': {'A': 'input_value', 'B': 'output_result'}}],
                            dimension='resources.section_structure', content_role='source_navigation')
        range_caption = full(r'A value input to \(([sn])\)(?: \(Number of shift bits\))? is the (STRING|REAL|INT) type data value '
                             r'and within the following range\.')
        if range_caption:
            return reviewed([{'kind': 'input_data_type', 'subject': _operand(range_caption[1]), 'data_type': range_caption[2]},
                             {'kind': 'source_navigation', 'target': 'following_operand_range'}],
                            dimension='parameters.documented_domain')
        if page == 1258 and full(r'BOOL cannot be specified\.') and 'WORD or DWORD can be specified to (s).' in source:
            return reviewed([{'kind': 'operand_data_type_exclusion', 'subject': _operand('s'), 'data_type': 'BOOL'}],
                            dimension='parameters.documented_domain')
        if page == 1293 and full(r'A value input to \(s\) is the REAL type data value and within the positive value range\.'):
            result = reviewed([{'kind': 'SQRT_documented_domain', 'subject': _operand('s'), 'data_type': 'REAL',
                                'source_claims': [{'source_region': 'processing_details', 'domain': 'positive'},
                                                 {'source_region': 'operation_error_3405H', 'rejected': 'negative'}],
                                'zero_input_disposition': 'not_explicitly_stated'}],
                              dimension='parameters.documented_domain', gap_kind='source_not_stated',
                              source_disposition='source_not_stated')
            if result:
                result['status'] = 'declared_unverified'
            return result
        if page == 1320:
            if full(r'BOOL, INT, DINT, WORD, DWORD, REAL, STRING, TIME, structure, or array type can be specified for \(s\) and \(d\)\.'):
                return reviewed([{'kind': 'operand_data_types', 'subjects': [_operand('s'), _operand('d')],
                                  'data_types': ['BOOL', 'INT', 'DINT', 'WORD', 'DWORD', 'REAL', 'STRING', 'TIME', 'structure', 'array']}],
                                dimension='parameters.documented_domain')
            if full(r'Specify the same data type for \(s\) and \(d\)\.'):
                return reviewed([{'kind': 'equal_operand_types', 'subjects': [_operand('s'), _operand('d')]}],
                                dimension='constraints.documented_restriction')
        if page == 1368 and full(r'The remainder is ignored\.'):
            return reviewed([{'kind': 'TIME_division_remainder', 'disposition': 'discarded', 'output_component': 'quotient'}])
        sign = full(r'In "Sign data(?: \((integer|exponent) part\))?", "?(20H \(space\)|2BH \(\+\))"? is stored '
                    r'when the input value is positive, and "?2DH \(-\)"? is stored when the inpu ?t value is negative\.')
        if sign:
            return reviewed([{'kind': 'STRING_numeric_sign', 'component': sign[1] or 'integer',
                              'positive_code': 0x20 if sign[2].startswith('20') else 0x2B, 'negative_code': 0x2D}])
        if full(r'20H \(space\) is stored in high-order digits when the number of significant figures is small\.'):
            return reviewed([{'kind': 'STRING_numeric_padding', 'location': 'high_order_digits', 'code': 0x20,
                              'when': 'significant_digits_less_than_output_width'}])
        if full(r'"30H \(0\)" is stored in the decimal part when the number of significant figures is small\.'
                r'(?: \(1\): Total number of digits \(13 digits\) \(2\): Number of digits of decimal part \(5\) '
                r'\(3\): "30H \(0\)" is stored\.)?'):
            return reviewed([{'kind': 'STRING_numeric_padding', 'location': 'decimal_part', 'code': 0x30,
                              'when': 'significant_digits_less_than_output_width'}])
        if full(r'"30H \(0\)" is stored in the tens place of the exponent part when the exponent part consists of 1 digit\.'
                r'(?: \(1\): Total number of digits \(13 digits\) \(2\): Number of digits of exponent part \(2\) '
                r'\(3\): "30H \(0\)" is stored\.)?'):
            return reviewed([{'kind': 'STRING_numeric_padding', 'location': 'exponent_tens_place', 'code': 0x30,
                              'when_exponent_digits': 1}])
        if full(r'00H is stored at the end of the character string when SM701 \(output character number selector signal\) is off\.'):
            return reviewed([{'kind': 'STRING_termination', 'code': 0, 'when': {'device': 'SM701', 'state': False}}])
        if full(r'A value input to \(s\) is the DINT type data value\.'):
            return reviewed([{'kind': 'input_data_type', 'subject': _operand('s'), 'data_type': 'DINT'}],
                            dimension='parameters.documented_domain')
        bcd_type_range = full(r'When \(d\) is (WORD|DWORD), the input value is within the range from 0 to (9999|99999999)\.')
        if bcd_type_range and (bcd_type_range[1] == 'WORD') == (bcd_type_range[2] == '9999'):
            return reviewed([{'kind': 'BCD_conversion_input_domain', 'subject': _operand('s'), 'minimum': 0,
                              'maximum': int(bcd_type_range[2]), 'when_output_type': {'subject': _operand('d'),
                                                                                  'data_type': bcd_type_range[1]}}],
                            dimension='parameters.documented_domain')
        bit_types = full(r'WORD or DWORD can be specified to \(([sd])\)\.')
        if bit_types:
            return reviewed([{'kind': 'operand_data_types', 'subject': _operand(bit_types[1]), 'data_types': ['WORD', 'DWORD']}],
                            dimension='parameters.documented_domain')
        if page == 1249 and full(r'BOOL cannot be specified\.') and 'WORD or DWORD can be specified to (d).' in source:
            return reviewed([{'kind': 'operand_data_type_exclusion', 'subject': _operand('d'), 'data_type': 'BOOL'}],
                            dimension='parameters.documented_domain')
        if page == 1293 and full(r'B= A'):
            return reviewed([{'kind': 'mathematical_function', 'function': 'square_root',
                              'input_symbol': 'A', 'output_symbol': 'B'}],
                            notation_review_basis='original_PDF_visual_review_p1293')
        function = full(r'B=(logeA|eA|SIN A|COS A|TAN A)')
        if function:
            return reviewed([{'kind': 'mathematical_function', 'input_symbol': 'A', 'output_symbol': 'B',
                              'function': {'logeA': 'natural_logarithm', 'eA': 'natural_exponential',
                                           'SIN A': 'sine', 'COS A': 'cosine', 'TAN A': 'tangent'}[function[1]]}])
        if page == 1300 and full(r'Even when the input value is \[GLYPH-F070\]/2 radian or \(3/2\) \[GLYPH-F070\] radian, '
                                r'no error occurs because an operation error occurs in a radian value\.'):
            result = reviewed([{'kind': 'tangent_special_angle_source_conflict', 'angles_radian': ['pi_over_2', '3pi_over_2'],
                                'source_claims': [{'operation_error': False}, {'radian_value_operation_error': True}],
                                'resolution': 'not_established'}],
                              gap_kind='source_conflict', source_disposition='source_conflict',
                              notation_review_basis='original_PDF_visual_review_p1300')
            if result:
                result['status'] = 'conflict'
            return result
        if full(r'When the operation result exceeds the data type range, convert the data type of an input value then perform the operation\.'):
            return reviewed([{'kind': 'arithmetic_input_type_selection', 'when_result_outside_type_range': True,
                              'sequence': ['convert_input_data_type', 'perform_operation']}],
                            dimension='resources.instruction_usage')
        if full(r'Even if underflow or overflow occurs in the operation result, it is not regarded as an operation error\. '
                r'"ADD_E" outputs "TRUE" from ENO\.') and page == 1309 and block.get('source_table_column') in {'INT', 'DINT'}:
            return reviewed([{'kind': 'arithmetic_overflow_result', 'data_type': block['source_table_column'],
                              'events': ['underflow', 'overflow'], 'operation_error': False,
                              'enabled_form': 'ADD_E', 'ENO': True}], phase='result')
        if full(r'If an underflow and an overflow occur in the operation result, an operation error occurs and an undefined value is output\.') \
                and page == 1309 and block.get('source_table_column') == 'REAL':
            return reviewed([{'kind': 'arithmetic_overflow_result', 'data_type': 'REAL',
                              'printed_event_connection': 'underflow_and_overflow', 'operation_error': True,
                              'output': 'undefined'}], phase='result')
        if full(r'When character strings are concatenated, 00H indicating an end of the character string specified by \(s1\) '
                r'is ignored and the character string specified by \(s2\) to \(s28\) is concatenated\.'):
            return reviewed([{'kind': 'STRING_concatenation', 'first_input': _operand('s1'),
                              'following_input_family': {'prefix': 's', 'first': 2, 'last': 28,
                                                         'selection': 'actually_declared_input_pins'},
                              'intermediate_terminator_code_ignored': 0}])
        if full(r'A value input to the input variables \(s1\) and \(s2\) to \(s28\) is the STRING type data value '
                r'and within the range from 0 to 255 byte\(s\)\.'):
            return reviewed([{'kind': 'STRING_input_family_domain', 'input_family': {'prefix': 's', 'first': 1, 'last': 28,
                                                                                  'selection': 'actually_declared_input_pins'},
                              'minimum_bytes': 0, 'maximum_bytes': 255}], dimension='parameters.documented_domain')
        if full(r'A value input to \(s1\) and \(s2\) is the STRING type data value and within the range from 0 to 255 byte\(s\)\.'):
            return reviewed([{'kind': 'STRING_input_domain', 'subjects': [_operand('s1'), _operand('s2')],
                              'minimum_bytes': 0, 'maximum_bytes': 255}], dimension='parameters.documented_domain')
        boolean_conversion = full(r'When the input value is "(FALSE|TRUE)", these functions output ([01])H? '
                                  r'as the (WORD|DWORD|INT|DINT|TIME|STRING) type data value\.')
        if boolean_conversion and (boolean_conversion[1].upper() == 'TRUE') == (boolean_conversion[2] == '1'):
            output_type = boolean_conversion[3].upper()
            return reviewed([{'kind': 'Boolean_conversion', 'input': boolean_conversion[1].upper() == 'TRUE',
                              'output_type': output_type,
                              'output': boolean_conversion[2] if output_type == 'STRING' else int(boolean_conversion[2]),
                              **({'unit': 'ms'} if output_type == 'TIME' else {})}])
        to_bool = full(r'When the input value is (0H?|any value other than 0H?), these functions output "(FALSE|TRUE)"\.')
        if to_bool and (to_bool[1].lower().startswith('any')) == (to_bool[2].upper() == 'TRUE'):
            return reviewed([{'kind': 'numeric_to_Boolean', 'input_comparison': '<>' if to_bool[2].upper() == 'TRUE' else '=',
                              'comparison_value': 0, 'output': to_bool[2].upper() == 'TRUE'}])
        discard = full(r'When (DWORD_TO_WORD|DWORD_TO_INT|DINT_TO_WORD)\(_E\) is executed, the information stored '
                       r'in high-order 16 bits of the (DWORD|DINT) type data value input from \(s\) is discarded\.')
        if discard and discard[1].upper().startswith(discard[2].upper() + '_'):
            return reviewed([{'kind': 'conversion_bit_truncation', 'form': discard[1].upper(), 'input': _operand('s'),
                              'source_type': discard[2].upper(), 'retained_low_bits': 16, 'discarded_high_bits': 16}])
        if full(r'When the data is converted to INT, the TIME type data stored in high-order 16 bits \(1 word\) are ignored\.'):
            return reviewed([{'kind': 'conversion_bit_truncation', 'source_type': 'TIME', 'target_type': 'INT',
                              'retained_low_bits': 16, 'discarded_high_bits': 16}])
        if full(r'The converted data includes an error \(rounding error\) if an integer value is outside the range '
                r'of -16777216 to 16777215\.'):
            return reviewed([{'kind': 'integer_to_REAL_precision', 'exact_integer_minimum': -16777216,
                              'exact_integer_maximum': 16777215, 'outside_interval': 'rounding_error'}],
                            dimension='parameters.input_representation')
        bool_select = full(r'When (FALSE\(=0\)|TRUE\(=1\)) is input to \(s1\), these functions output '
                           r'an input value of \((s2|s3)\) from \(d\)\.')
        if bool_select and (bool_select[1].upper().startswith('TRUE')) == (bool_select[2].lower() == 's3'):
            return reviewed([{'kind': 'Boolean_selection', 'selector': _operand('s1'),
                              'selector_value': bool_select[1].upper().startswith('TRUE'),
                              'selected_input': _operand(bool_select[2].lower()), 'output': _operand('d')}])
        if full(r'When 0 is input to \(n\), these functions output a value input to \(s1\) from \(d\)\.'):
            return reviewed([{'kind': 'indexed_selection', 'selector': _operand('n'), 'selector_value': 0,
                              'selected_input': _operand('s1'), 'output': _operand('d')}])
        if full(r'When \(n\)-1 is input to \(n\), these functions output a value input to \(sn\) from \(d\)\.'):
            return reviewed([{'kind': 'indexed_selection', 'selector': _operand('n'),
                              'selector_value': 'input_count_minus_one', 'selected_input': 'last_input_sn',
                              'output': _operand('d')}])
        if full(r'A value input to \(n\) is the INT type data value and within the range from 0 to 27\. '
                r'\(The value must be in the pin number range for \(s\)\.\)'):
            return reviewed([{'kind': 'selector_input_domain', 'subject': _operand('n'), 'data_type': 'INT',
                              'minimum': 0, 'maximum': 27, 'additional_maximum': 'actual_input_count_minus_one'}],
                            dimension='parameters.documented_domain')
        string_clamp = full(r'When the (concatenated|inserted) character string exceeds 255 bytes, '
                            r'these functions output a character string within 255 bytes\.')
        if string_clamp:
            return reviewed([{'kind': 'STRING_result_capacity', 'operation': string_clamp[1].lower(),
                              'when_length_bytes_exceeds': 255, 'output_maximum_bytes': 255}])
        if full(r'If a character string specified by \(s2\) cannot be searched from the one specified by \(s1\), '
                r'these functions output "0"\.'):
            return reviewed([{'kind': 'STRING_search_result', 'haystack': _operand('s1'), 'needle': _operand('s2'),
                              'when_not_found': 0}])
        if full(r'Even if underflow or overflow occurs in the operation result, it is not regarded as an operation error\.'):
            return reviewed([{'kind': 'result_overflow_reporting', 'result_events': ['underflow', 'overflow'],
                              'operation_error': False}], phase='result')
        time_eno = full(r'The data is output from \(d\) as follows: "(ADD_TIME_E|SUB_TIME_E|MUL_TIME_E)" '
                        r'outputs "TRUE" from the output variable ENO\.(?: \(The operation result is the 64-bit data, '
                        r'however, the output data is the time type data with high-order 32 bits deleted\.\))?')
        if time_eno:
            rules = [{'kind': 'TIME_operation_result', 'form': time_eno[1].upper(), 'output': _operand('d'), 'ENO': True}]
            if '64-bit' in clause:
                rules[0].update(intermediate_width_bits=64, output_width_bits=32, discarded_bits='high_32')
            return reviewed(rules, forms=[time_eno[1].upper()])
        if full(r'These functions divide the TIME type data input to \(s1\) and \(s2\) '
                r'\(\(s1\) \[GLYPH-F0B8\] \(s2\)\), and output the operation result from \(d\) as TIME type data\.'):
            return reviewed([{'kind': 'TIME_division', 'dividend': _operand('s1'), 'divisor': _operand('s2'),
                              'output': _operand('d'), 'output_type': 'TIME'}])
        if full(r'When the operation result is 0, the zero flag \(SM8020\) turns on\.'):
            return reviewed([{'kind': 'result_flag', 'device': 'SM8020', 'condition': {'result': 0}, 'state': True}],
                            phase='result')
    from tools.fact_fx5_processing_extended import review_processing_clause as review_extended
    from tools.fact_fx5_processing_industrial import review_processing_clause as review_industrial
    from tools.fact_fx5_processing_figures import review_processing_clause as review_figures
    arguments = dict(section=section, block=block, value=value, declared=declared)
    return review_extended(clause, **arguments) or review_industrial(clause, **arguments) or review_figures(clause, **arguments)

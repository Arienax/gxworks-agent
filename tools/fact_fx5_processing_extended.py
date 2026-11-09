"""Source-bound reviews for the later FX5 application-processing paragraphs.

This offline author consumes complete clauses.  It does not grant an executor
capability, interpret neighbouring figures as general rules, or repair a printed
formula.  Operand references must be present in the owning declaration.
"""
from __future__ import annotations

import re


def _operand(name, offset=0):
    return {'operand': name, **({'offset': offset} if offset else {})}


def _ASCII_words(characters):
    """Encode an authored, printed example, including its illustrated NULL."""
    data = [ord(character) for character in characters] + [0]
    if len(data) % 2:
        data.append(0)
    return [{'offset': i // 2, 'low_byte': data[i], 'high_byte': data[i + 1]}
            for i in range(0, len(data), 2)]


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


def _chart_tokens_only(clause, vocabulary):
    """A chart fragment must contain only its reviewed notation vocabulary.

    This is a guard for the authored, page-specific diagrams below, not a
    general English sentence recognizer.  An appended qualification fails.
    """
    text = re.sub(r'\[GLYPH-[A-F0-9]+\]', ' ', clause)
    text = re.sub(r'\([sdnm][0-9]?\)', ' ', text, flags=re.I)
    text = re.sub(r'\b(?:SD|SM|LZ|CH|DI|EI|IRET|FEND|NEXT|BREAK|FOR|WDT|ZPUSH|X|Y|M|D|C|S|Z|K|I|L)[0-9]+\b', ' ', text)
    text = re.sub(r'\b[0-9A-F]+H\b', ' ', text)
    words = re.findall(r'[A-Za-z]+', text)
    return all(word.casefold() in vocabulary for word in words)


def _registers_printed(layout, registers):
    return all(re.search(r'(?<![A-Za-z0-9])' + re.escape(register) + r'(?![0-9])', layout) for register in registers)


def _pulse_density_status_table(section, page):
    layout = _page_layout(section, page)
    operation = 'SPD' if page == 790 else 'DSPD'
    enable = 'HIOEN' if page == 790 else 'DHIOEN'
    if not all(marker in layout for marker in ['Monitor in operation', 'Measurement unit time', 'R/W', 'Update timing', 'Clear']):
        return None
    channels = []
    for channel in range(1, 9):
        base = 4500 + 30 * (channel - 1)
        entry = {'channel': channel, 'in_operation': 'SM' + str(4500 + channel - 1),
                 'pulse_density': {'low': 'SD' + str(base + 6), 'high': 'SD' + str(base + 7)},
                 'measurement_time': {'low': 'SD' + str(base + 16), 'high': 'SD' + str(base + 17)}}
        names = [entry['in_operation'], *entry['pulse_density'].values(), *entry['measurement_time'].values()]
        if not _registers_printed(layout, names):
            return None
        channels.append(entry)
    return {'kind': 'pulse_density_status_table', 'form': operation, 'channels': channels,
            'fields': [{'field': 'in_operation', 'access': 'read_only',
                        'update_events': [operation + '_execution', enable + '_execution']},
                       {'field': 'pulse_density', 'access': 'read_only', 'update_events': ['measurement_interval_elapsed']},
                       {'field': 'measurement_time', 'access': 'read_write', 'update_events': [operation + '_execution']}],
            'clear_events_for_all_fields': ['power_on', 'cpu_reset']}


def _PWM_status_tables(section, page):
    double = page in {821, 822}
    operation, enable = ('DPWM', 'DHIOEN') if double else ('PWM', 'HIOEN')
    first_page, second_page = (821, 822) if double else (814, 815)
    first, second = _page_layout(section, first_page), _page_layout(section, second_page)
    if not all(marker in first for marker in ['Number of', 'output pulses', 'ON time', 'Timing of reflection']):
        return None
    if not all(marker in second for marker in ['Period', 'Pulse output', 'monitor', 'Power-on', 'Reset']):
        return None
    channels, current_channels = [], []
    for channel in range(1, 13):
        base = 5300 + 16 * (channel - 1)
        fields = {name: {'low': 'SD' + str(base + offset), 'high': 'SD' + str(base + offset + 1)}
                  for name, offset in [('requested_pulses', 0), ('ON_time', 2), ('period', 4)]}
        monitor = 'SM' + str(5300 + channel - 1)
        if not _registers_printed(first, [*fields['requested_pulses'].values(), *fields['ON_time'].values()]):
            return None
        if not _registers_printed(second, [*fields['period'].values(), monitor]):
            return None
        channels.append({'channel': channel, **fields, 'output_monitor': monitor})
        if channel <= 4:
            current = {'channel': channel, 'low': 'SD' + str(base + 6), 'high': 'SD' + str(base + 7)}
            if not _registers_printed(second, [current['low'], current['high']]):
                return None
            current_channels.append(current)
    return {'kind': 'PWM_status_tables', 'form': operation, 'channels': channels,
            'current_pulse_count_channels': current_channels,
            'requested_pulses': {'access': 'read_write', 'initial_value': 0,
                                 'reflection_events': ['DHCMOV_execution', operation + '_execution', 'END_processing'],
                                 'clear_events': ['STOP_or_PAUSE_to_RUN']},
            'ON_time_and_period': {'access': 'read_write', 'printed_initial_value': 0,
                                   'reflection_events': ['DHCMOV_execution', operation + '_execution', 'END_processing'],
                                   'clear_events': ['STOP_or_PAUSE_to_RUN'],
                                   'initial_parameter_load_event': 'STOP_to_RUN',
                                   'instruction_execution_loads': {'ON_time': _operand('s1'), 'period': _operand('s2')},
                                   'unit': 'parameter_selected_us_or_ms'},
            'output_monitor': {'access': 'read_only', 'initial_value': False,
                               'ON_events': [enable + '_execution', operation + '_execution'],
                               'OFF_events': ['power_on', 'cpu_reset', 'RUN_to_STOP_or_PAUSE',
                                              'specified_pulse_count_reached', 'drive_contact_OFF']},
            'current_pulse_count': {'access': 'read_write', 'initial_value': 0,
                                    'reflection_events': ['DHCMOV_execution', *([] if double else ['PWM_execution']), 'END_processing'],
                                    'clear_events': ['power_on', 'cpu_reset', 'STOP_or_PAUSE_to_RUN']},
            'DHCMOV_qualification': {'read': 'latest_value', 'write_to_writable_device': 'immediate'},
            'source_pages': [first_page, second_page]}


def _priority_trace(page, clause):
    vocabulary = set(('a b t sequence program scan execution type interrupt priority setting no cannot can be executed '
                      'because of the for all lower or higher enabled disabled section when multiple di instructions '
                      'are with argument without an and specified execution order is in counterpart which ' 
                      'fend iret ei di k instruction currently than interrupts').split())
    if not _chart_tokens_only(clause, vocabulary):
        return None
    def event(form, disabled_from=None, **extra):
        return {'instruction': form, 'disabled_numeric_priorities_from': disabled_from, **extra}
    if page == 448:
        if clause.startswith('A: Sequence program'):
            events = [event('EI'), event('DI', 3, argument=3), event('DI', 2, argument=2),
                      event('EI', 3, counterpart='DI_K2'), event('EI', counterpart='DI_K3')]
        elif 'higher than the currently disabled priority' in clause:
            events = [event('EI'), event('DI', 3, argument=3), event('DI', 2, argument=2), event('EI', 3), event('EI')]
        elif 'lower than the currently disabled priority' in clause:
            events = [event('EI'), event('DI', 2, argument=2), event('DI', 2, argument=3, disabling_priority_changed=False),
                      event('EI'), event('EI')]
        else:
            return None
    elif page == 449:
        if clause.startswith('When the DI instruction is executed in an interrupt program'):
            events = [event('EI', program='scan'), event('DI', 3, argument=3, program='scan'),
                      event('DI', 2, argument=2, program='interrupt'), event('EI', 3, program='interrupt'),
                      event('IRET', 3, program='interrupt'), event('EI', program='scan')]
        elif clause.startswith('When the DI instruction without an argument is executed'):
            events = [event('EI'), event('DI', 1), event('DI', 1), event('DI', 1), event('EI')]
        elif 'with an argument [GLYPH-F0AE] DI instruction without an argument' in clause:
            events = [event('EI'), event('DI', 2, argument=2), event('DI', 1), event('DI', 1),
                      event('EI', 2), event('EI'), event('EI')]
        elif 'without an argument [GLYPH-F0AE] DI instruction with an argument' in clause:
            events = [event('EI'), event('DI', 1), event('DI', 1), event('DI', 1, argument=2, disabling_priority_changed=False),
                      event('EI'), event('EI')]
        else:
            return None
    else:
        return None
    return {'kind': 'documented_interrupt_priority_trace', 'events': events, 'numeric_priority_1_is_highest': True,
            'null_disabled_priority_means': 'all_priorities_enabled', 'scope': 'documented_program_trace'}


def _HSC_input_assignment(page, clause):
    if not re.match(r'(?:\[FX5U/FX5UC CPU module\] )?General-purpose input assignment', clause):
        return None
    vocabulary = set(('fx u uc s uj cpu module general purpose input assignment of the phase counter switching '
                      's w h up or down a b c d p e pulse preset reset enable start if one to is specified as '
                      'device an error occurs ch direction').split())
    if not _chart_tokens_only(clause, vocabulary):
        return None
    small = page in {788, 794} and not clause.startswith('[FX5U/FX5UC CPU module]')
    software = 'switching S/W' in clause
    AB = '2-phase 2-input' in clause
    if small and not AB:
        return None
    mode = ('two_phase_two_input' if AB else 'one_phase_one_input_software_direction' if software else
            'one_phase_one_input_hardware_direction' if 'switching H/W' in clause else 'one_phase_two_input')
    channels = []
    if small:
        for channel, A, B, preset, enable in [(1, 'X0', 'X1', 'X2', 'X6'), (4, 'X3', 'X4', 'X5', 'X11'),
                                               (6, 'X5', 'X6', 'X7', 'X13'), (7, 'X6', 'X7', 'X10', 'X14')]:
            channels.append({'channel': channel, 'A': A, 'B': B, 'preset': preset, 'enable': enable})
        errors = ['X1', 'X2', 'X4', 'X5', 'X7']
    else:
        inputs = ['X0', 'X2', 'X4', 'X6', 'X10', 'X12', 'X14', 'X16']
        second = ['X1', 'X3', 'X5', 'X7', 'X11', 'X13', 'X15', 'X17']
        auxiliaries = [('X10', 'X11'), ('X12', 'X13'), ('X14', 'X15'), ('X16', 'X17'),
                       ('X12', 'X13'), ('X14', 'X15'), ('X16', 'X17'), (None, None)]
        for index in range(8):
            role = 'A' if AB else 'up_down_pulse' if software else 'pulse' if 'hardware' in mode else 'up_pulse'
            item = {'channel': index + 1, role: 'X' + str(index) if software else inputs[index]}
            if not software:
                item['B' if AB else 'direction' if 'hardware' in mode else 'down_pulse'] = second[index]
            preset, enable = (('X' + str(10 + 2 * (index % 4)), 'X' + str(11 + 2 * (index % 4)))
                              if software else auxiliaries[index])
            item.update(preset=preset, enable=enable)
            channels.append(item)
        errors = ['X' + str(index) for index in range(10, 18)] if software else second
    selected_role = 'A' if AB else 'up_down_pulse' if software else 'pulse' if 'hardware' in mode else 'up_pulse'
    shaded = [item[selected_role] for item in channels]
    rules = [{'kind': 'pulse_density_input_assignment', 'subject': _operand('s1'), 'when_device_form': 'bit',
              'models': ['FX5S', 'FX5UJ'] if small else ['FX5U', 'FX5UC'], 'counter_mode': mode,
              'channels': channels, 'valid_input_devices_from_shading': shaded,
              'listed_error_input_devices': errors, 'unassigned_auxiliary_cell': 'not_assigned'}]
    overlap = sorted(set(shaded).intersection(errors))
    if overlap:
        rules.append({'kind': 'source_conflict', 'conflict': 'shaded_input_is_also_listed_as_error',
                      'devices': overlap, 'resolution': 'not_established'})
    return rules, 'conflict' if overlap else 'source_verified'


def _sorting_chart(page, clause):
    if not clause.startswith('Sorting result when the instructions are executed with (n3)'):
        return None
    vocabulary = set(('sorting result when the instructions are executed with column no number of groups control height '
                      'weight age data line in case ascending descending order on off sm n').split())
    if not _chart_tokens_only(clause, vocabulary):
        return None
    width = 32 if page in {878, 879} else 16
    column_major = page in {872, 873}
    selected_column = 2 if page in {872, 875, 878} else 3
    descending = page in {876, 879}
    input_rows = [[1, 150, 45, 20], [2, 180, 50, 40], [3, 160, 70, 30], [4, 100, 20, 8], [5, 150, 50, 45]]
    order = [4, 1, 5, 3, 2] if selected_column == 2 else [3, 2, 5, 1, 4] if descending else [4, 1, 2, 5, 3]
    words = width // 16
    offsets = [[words * (row + 5 * column if column_major else row * 4 + column) for column in range(4)] for row in range(5)]
    source_offsets = [list(row) for row in offsets]
    output_offsets = [list(row) for row in offsets]
    conflicts = []
    if page == 872:
        output_offsets[1][0], output_offsets[2][0] = 2, 1
        output_offsets[0][2], output_offsets[1][2] = 11, 10
        conflicts.append({'kind': 'source_conflict', 'conflict': 'sorted_table_offset_labels_disagree_with_column_layout',
                          'printed_output_offsets': output_offsets, 'resolution': 'not_established'})
    if page == 878:
        source_offsets[2][1] = 26
        conflicts.append({'kind': 'source_conflict', 'conflict': 'input_table_reuses_height_offsets_for_two_rows',
                          'rows': [3, 4], 'column': 2, 'printed_low_offset': 26, 'printed_high_offset': 27,
                          'resolution': 'not_established'})
    rules = [{'kind': 'documented_sorting_example', 'source': _operand('s'), 'destination': _operand('d'),
              'element_width_bits': width, 'row_count': 5, 'column_count': 4,
              'layout': 'column_major' if column_major else 'row_major',
              'selected_column': selected_column, 'direction': 'descending' if descending else 'ascending',
              'input_rows': input_rows, 'output_rows': [input_rows[index - 1] for index in order],
              'printed_input_low_word_offsets': source_offsets, 'printed_output_low_word_offsets': output_offsets,
              **({'configuration_diagram': {'row_count': 3, 'column_count': 4,
                                            'low_word_offsets': [[words * (row + 3 * column if column_major else row * 4 + column)
                                                                  for column in range(4)] for row in range(3)]}}
                 if page in {872, 875, 878} else {}),
              **({'SM703_state': descending} if not column_major else {}),
              'equal_key_order_guarantee': 'not_established_from_example', 'generalization': 'documented_example_only'}]
    return rules + conflicts, 'conflict' if conflicts else 'source_verified'


def review_processing_clause(clause, *, section, block, value, declared):
    page = block['page']
    if not (445 <= page <= 657 or 735 <= page <= 970):
        return None
    source = block.get('text', '')
    title = section.get('section_token', '')
    declared = {str(name).casefold() for name in declared}

    def full(pattern):
        return re.fullmatch(pattern, clause, re.I)

    def reviewed(rules, *, dimension='effects.documented_processing', languages=None,
                 conditions=None, content_role='manual_behavior', status='source_verified', forms=None, **updates):
        subjects = sorted(set(_operands(rules)))
        if any(name.casefold() not in declared for name in subjects):
            return None
        return {'rules': rules, 'dimension': dimension, 'full_clause_review': True,
                'content_role': content_role, 'status': status,
                **({'languages': languages} if languages else {}),
                **({'conditions': conditions} if conditions else {}),
                **({'forms': forms} if forms else {}),
                'value_updates': {'subjects': [_operand(name) for name in subjects],
                                  'operand_binding_required': bool(subjects),
                                  'numeric_values': [], 'ranges': [], 'comparisons': [],
                                  'source_formulas': [], 'unresolved_glyphs': [],
                                  'unparsed_input_qualification': False,
                                  'unbound_condition_markers': [], 'context_binding': 'resolved',
                                  'knowledge_kind': 'source_navigation' if content_role == 'source_navigation' else 'manual_behavior',
                                  **updates}}

    # Interrupt state and priority.  Enablement is separate from an interrupt
    # request; none of these predicates assert that a program was dispatched.
    if page == 445:
        if full(r'When the power is turned on or the CPU module is reset, the state in which the DI instruction is executed is applied\.'):
            return reviewed([{'kind': 'interrupt_initial_state', 'events': ['power_on', 'cpu_reset'],
                              'state': 'DI_executed_interrupts_disabled'}])
        if full(r'This instruction releases the execution disabled state of interrupt programs when the DI instruction is executed, '
                r'and enables the execution of the interrupt program with the interrupt pointer number enabled by the IMASK instruction\.'):
            return reviewed([{'kind': 'interrupt_enable', 'form': 'EI',
                              'release': 'DI_interrupt_disabled_state',
                              'eligible_interrupts': 'interrupt_pointers_enabled_by_IMASK'}], forms=['EI'])
        if full(r'For the operation of the DI instruction \(DI instruction without an argument\) when using the interrupt disable '
                r'instruction with a specified priority or lower \(DI instruction with an argument\), refer to '
                r'\[GLYPH-F0C1\]Page 445 Disabling the interrupt program with specified priority or lower\.'):
            return reviewed([{'kind': 'source_navigation', 'target_pdf_page': 447,
                              'target_printed_page': 445, 'topic': 'DI_with_and_without_priority_argument'}],
                            dimension='resources.section_structure', content_role='source_navigation')
    if page == 446:
        if full(r'If the master control contains the EI or DI instruction, such an instruction is executed '
                r'regardless of the execution of the MC instruction\.'):
            return reviewed([{'kind': 'master_control_execution_exception', 'forms': ['EI', 'DI'],
                              'MC_execution_gates_instruction': False}], languages=['ladder'],
                            conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}])
        if full(r'An interrupt pointer occupies two steps\. \(In \(1\) below, I10 is the step 50, X2 is the step 52, '
                r'and Y10 is the step 54\.\)'):
            return reviewed([{'kind': 'interrupt_pointer_storage', 'program_steps': 2,
                              'example_positions': [{'symbol': 'I10', 'step': 50}, {'symbol': 'X2', 'step': 52},
                                                    {'symbol': 'Y10', 'step': 54}]}],
                            dimension='resources.interrupt_pointer_layout')
    if page == 448:
        if full(r'By executing the EI instruction, the interrupt with the priority disabled by the counterpart DI instruction is enabled\.'):
            return reviewed([{'kind': 'interrupt_priority_restore', 'form': 'EI',
                              'restored_scope': 'counterpart_DI_priority_disable'}], forms=['EI'])
        if full(r'However, when interrupts are disabled only with the DI instruction without an argument, '
                r'interrupts with all the priorities are enabled by executing the EI instruction once\.'):
            return reviewed([{'kind': 'interrupt_priority_restore', 'form': 'EI',
                              'when': 'only_argumentless_DI_has_disabled_interrupts',
                              'restored_scope': 'all_priorities', 'EI_execution_count': 1}], forms=['EI'])
        priority = full(r'When multiple DI instructions are executed and the argument has a priority (higher|lower) '
                        r'than the currently disabled priority, (interrupts with a priority lower than that of the argument '
                        r'are disabled|the interrupt disabled state is not changed)\.')
        if priority and ((priority[1].lower() == 'higher') == priority[2].lower().startswith('interrupts with')):
            return reviewed([{'kind': 'nested_interrupt_disable', 'form': 'DI',
                              'new_priority_relative_to_current': priority[1].lower(),
                              'effect': 'disable_priorities_lower_than_argument' if priority[1].lower() == 'higher'
                                        else 'retain_current_disabled_state'}], forms=['DI'])
        if full(r'The interrupt priority of the interrupt pointer can be set with parameters\. '
                r'\(\[GLYPH-F0C0\]MELSEC iQ-F FX5 User\'s Manual \(Application\)\)'):
            return reviewed([{'kind': 'configuration_dependency', 'item': 'interrupt_pointer_priority',
                              'configured_by': 'CPU_parameters', 'manual_role': 'FX5_Application'}],
                            dimension='resources.configuration_dependency')
        if full(r'The interrupt-disabled priority can be checked with SD758 \(interrupt-disabling priority setting value\)\.'):
            return reviewed([{'kind': 'status_register', 'device': 'SD758', 'value_role': 'interrupt_disabled_priority'}])
        if full(r'The following shows the interrupt-disabled section when the DI or EI instruction is executed\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_DI_EI_priority_diagrams'}],
                            dimension='resources.section_structure', content_role='source_navigation')
    if page == 451:
        if full(r'This instruction enables or disables the execution of the interrupt program with the specified interrupt '
                r'pointer number according to the 16-point bit pattern starting from the device specified in \(s\)\.'):
            return reviewed([{'kind': 'interrupt_mask', 'source': _operand('s'),
                              'packing': '16_interrupt_enable_bits_per_word', 'one_means': 'enabled', 'zero_means': 'disabled'}])
        enabled = full(r'([01]) \((ON|OFF)\): The execution of interrupt programs is (enabled|disabled)\.')
        if enabled and ((enabled[1], enabled[2].upper(), enabled[3].lower()) in
                        {('1', 'ON', 'enabled'), ('0', 'OFF', 'disabled')}):
            return reviewed([{'kind': 'interrupt_mask_bit', 'source': _operand('s'),
                              'bit_value': int(enabled[1]), 'execution_enabled': enabled[1] == '1'}])
    if page == 452:
        mapping = full(r'The following shows the assignment of the interrupt pointer numbers to each bit\. '
                       r'b15 b14 b13 b12 b11 b10 b9 b8 b7 b6 b5 b4 b3 b2 b1 b0 (?P<rows>.+)')
        if mapping:
            rows = list(re.finditer(r'\(s\)(?:\+(\d+))? (.*?)(?= \(s\)|$)', mapping['rows']))
            table = []
            for index, row in enumerate(rows):
                tokens = row[2].split()
                if int(row[1] or 0) != index or len(tokens) != 16 or any(
                        token != '-' and not re.fullmatch(r'I\d+', token) for token in tokens):
                    return None
                table.append({'source': _operand('s', index),
                              'bits': [{'bit': 15 - i, 'pointer': token} for i, token in enumerate(tokens) if token != '-'],
                              'unassigned_bits': [15 - i for i, token in enumerate(tokens) if token == '-']})
            if len(table) == 12 and ' '.join(row[0] for row in rows) == mapping['rows']:
                return reviewed([{'kind': 'interrupt_mask_layout', 'words': table,
                                  'unassigned_bit_semantics': 'not_stated_in_table'}])
        if full(r'When the power is turned on or the CPU module is reset, execution of the interrupt programs I0 to I177 is enabled\.'):
            return reviewed([{'kind': 'interrupt_mask_initial_state', 'events': ['power_on', 'cpu_reset'],
                              'pointer_numbers': {'first': 0, 'last': 177}, 'enabled': True}])
        if full(r'The IMASK instruction can enable or disable the interrupt pointers I0 to I177 in a batch\.'):
            return reviewed([{'kind': 'interrupt_mask_scope', 'form': 'IMASK',
                              'pointer_numbers': {'first': 0, 'last': 177}, 'operation': 'batch_enable_or_disable'}])
    if page == 453:
        if full(r'This instruction enables or disables the execution of the interrupt program with the interrupt pointer '
                r'number specified by \(I\) according to the data specified by \(s\)\.?'):
            return reviewed([{'kind': 'interrupt_pointer_enable', 'pointer': _operand('I'),
                              'setting': _operand('s'), 'zero_means': 'disabled', 'one_means': 'enabled'}])
        state = full(r'When ([01]) is set in \(s\): The execution of the interrupt program is (enabled|disabled)\.')
        if state and ((state[1] == '1') == (state[2].lower() == 'enabled')):
            return reviewed([{'kind': 'interrupt_pointer_enable', 'pointer': _operand('I'),
                              'setting': {'subject': _operand('s'), 'equals': int(state[1])},
                              'execution_enabled': state[1] == '1'}])
        if full(r'When the power is turned on or the CPU module is reset, execution of the interrupt programs is enabled\.'):
            return reviewed([{'kind': 'interrupt_pointer_initial_state', 'events': ['power_on', 'cpu_reset'],
                              'execution_enabled': True}])
        pointers = full(r'Interrupt program: I0 to I(31|177)')
        if pointers:
            models = ['FX5S'] if pointers[1] == '31' else ['FX5UJ', 'FX5U', 'FX5UC']
            return reviewed([{'kind': 'interrupt_pointer_scope',
                              'CPU_cases': [{'models': models, 'pointer_numbers': {'first': 0, 'last': int(pointers[1])}}]}],
                            dimension='parameters.documented_domain')
    if page == 454 and full(r'The execution-enabled/disabled states of interrupt pointers are stored in the IMASK instruction mask pattern\.'):
        return reviewed([{'kind': 'shared_interrupt_enable_state', 'storage': 'IMASK_mask_pattern',
                          'contents': 'interrupt_pointer_execution_enabled_or_disabled'}])
    if page == 458 and full(r'Design a program so that t1 from the step 0 to the WDT\(P\) instruction and t2 from the '
                             r'WDT\(P\) instruction to the END \(FEND\) instruction do not exceed the setting value of the watchdog timer\.'):
        return reviewed([{'kind': 'watchdog_interval_limit', 'maximum': 'watchdog_timer_setting',
                          'intervals': [{'from': 'step_0', 'to': 'WDT(P)'}, {'from': 'WDT(P)', 'to': 'END_or_FEND'}]}],
                        dimension='constraints.documented_restriction')
    if page == 459:
        if full(r'When the processing between the FOR and NEXT instructions is executed \(n\) times without any condition, '
                r'the processing of the step following the NEXT instruction is executed\.'):
            return reviewed([{'kind': 'counted_loop', 'count': _operand('n'), 'body': 'between_FOR_and_NEXT',
                              'after_count': 'step_following_NEXT', 'body_iterations_have_no_additional_condition': True}])
        if full(r'If any of -32768 to 0 is specified, the processing of \(n\)=1 is applied\.'):
            return reviewed([{'kind': 'effective_loop_count', 'subject': _operand('n'),
                              'when': {'minimum': -32768, 'maximum': 0}, 'effective_value': 1}])
    if page == 462:
        if full(r'This instruction forcibly terminates the FOR to NEXT instruction loop and shifts the program execution '
                r'to the pointer specified by \(P\)\.'):
            return reviewed([{'kind': 'loop_break', 'loop': 'FOR_to_NEXT', 'next_execution': _operand('P')}])
        if full(r'Only the pointer numbers within the same program file can be specified in \(P\)\.'):
            return reviewed([{'kind': 'pointer_scope', 'subject': _operand('P'), 'allowed_scope': 'same_program_file'}],
                            dimension='constraints.documented_restriction')
        if full(r'In \(d\), the number of remaining FOR to NEXT instruction loops at the forced termination is stored\.'):
            return reviewed([{'kind': 'loop_break_result', 'destination': _operand('d'), 'value': 'remaining_FOR_NEXT_iterations'}])
        if full(r'Note that the number includes the loop when the BREAK\(P\) instructions are executed\.') and re.search(
                r'In \(d\), the number of remaining FOR to NEXT', source):
            return reviewed([{'kind': 'loop_break_result', 'destination': _operand('d'),
                              'remaining_iterations_include_current_iteration': True}],
                            antecedent_basis='remaining_iteration_count_in_same_source_block')
        if full(r'The BREAK\(P\) instructions can be used only between the FOR and NEXT instructions\.'):
            return reviewed([{'kind': 'program_placement', 'forms': ['BREAK', 'BREAKP'], 'within': 'FOR_to_NEXT'}],
                            dimension='constraints.documented_restriction')
    if page == 463:
        if full(r'The BREAK\(P\) instructions can be used for only one nesting level\.'):
            return reviewed([{'kind': 'loop_break_scope', 'forms': ['BREAK', 'BREAKP'], 'nesting_levels_per_execution': 1}])
        if full(r'To forcibly terminate multiple nesting levels, execute as many BREAK\(P\) instructions as the number of nesting levels\.'):
            return reviewed([{'kind': 'nested_loop_break', 'break_execution_count': 'number_of_nesting_levels_to_terminate'}])
    if page == 464:
        if full(r'When the CALL\(P\) instructions are executed, the subroutine program specified by the pointer \(P\) is executed\.'):
            return reviewed([{'kind': 'subroutine_call', 'target': _operand('P')}])
        if full(r'The CALL\(P\) instructions can execute a subroutine program specified by a pointer in the same program file '
                r'or by a common pointer\.'):
            return reviewed([{'kind': 'subroutine_pointer_scope', 'subject': _operand('P'),
                              'allowed': ['same_program_file_pointer', 'common_pointer']}])
    if page == 469 and full(r'When the RET instruction is executed, the program execution returns to the step following '
                             r'the CALL\(P\) or XCALL instruction that called the subroutine program\.'):
        return reviewed([{'kind': 'subroutine_return', 'destination': 'step_following_calling_CALL_CALLP_or_XCALL'}])
    if page == 473:
        if full(r'The data of \(s\)\+1 is transferred \(read\) to \(d\)\.'):
            return reviewed([{'kind': 'fifo_read', 'source': _operand('s', 1), 'destination': _operand('d')}])
        if full(r'Use the pulse operation type SFRDP instruction in programming\.\)'):
            return reviewed([{'kind': 'recommended_execution_form', 'form': 'SFRDP', 'reason': 'read_once_per_command'}],
                            dimension='resources.instruction_usage')
        if full(r'Accompanied by this transfer, the contents of the pointer \(s\) decrease, and the data is shifted '
                r'upward by 1 word\. \(When the continuous operation type SFRD instruction is used, the contents are '
                r'stored in turn in each operation cycle\.'):
            return reviewed([{'kind': 'fifo_read_update', 'event': 'oldest_data_transferred',
                              'pointer': _operand('s'), 'pointer_change': 'decreased', 'data_shift_up_words': 1},
                             {'kind': 'fifo_read_repetition', 'form': 'SFRD', 'repeat_interval': 'operation_cycle',
                              'destination': _operand('d')}])

    # SD-card file operations.  Control selectors stay selectors, rather than
    # being promoted to unrestricted host file operations.
    if page in {493, 520}:
        if full(r'This instruction (reads data from|writes the specified number of data to) the specified file\.'):
            return reviewed([{'kind': 'SD_file_transfer', 'operation': 'read' if page == 493 else 'write',
                              'amount': 'control_data_request_count' if page == 520 else 'control_data_read_specification'}])
        if full(r'Set the execution/completion type in the control data to specify the file (read|write)-target format\.'):
            return reviewed([{'kind': 'file_transfer_format_selector', 'operation': 'read' if page == 493 else 'write',
                              'selector': 'control_data_execution_completion_type'}])
        if full(r'The (read target is the data in|write target is) the SD memory card only\.'):
            return reviewed([{'kind': 'file_storage_scope', 'operation': 'read' if page == 493 else 'write',
                              'storage': 'SD_memory_card', 'exclusive': True}])
    if page in {494, 520}:
        fields = full(r'Specify data in (\(d1\)\+2, \(d1\)\+4, \(d1\)\+5, and \(d2\)\+0|'
                      r'\(s3\)\+0, \(d1\)\+4, \(d1\)\+5, and \(d1\)\+2) depending on the combination of '
                      r'\(d1\)\+0 and \(d1\)\+7\.')
        if fields and ((page == 494) == fields[1].startswith('(d1)')):
            return reviewed([{'kind': 'file_transfer_field_dependency',
                              'selectors': [_operand('d1'), _operand('d1', 7)],
                              'dependent_fields': [_operand('d1', 2), _operand('d1', 4), _operand('d1', 5),
                                                   _operand('d2' if page == 494 else 's3')],
                              'input_range_or_output_unit': 'determined_by_selector_combination_table'}],
                            dimension='parameters.documented_relation')
    if page == 521 and full(r'If the specified file exists and the number of request write data is 0, '
                             r'the program will terminate normally with the number of actually written data to 0 point\.?'):
        return reviewed([{'kind': 'file_zero_length_write', 'when': {'file_exists': True, 'request_data_count': 0},
                          'completion': 'normal', 'actually_written_count': 0}])
    if page == 524:
        if full(r'When an existing file is specified, the following occurs\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_existing_file_write_cases',
                              'when': 'specified_file_exists'}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'If the extension is omitted, the extension will be "\.CSV"\.'):
            return reviewed([{'kind': 'default_file_extension', 'when': 'extension_omitted',
                              'extension': {'leading_dot': True, 'suffix': 'CSV'}}])
        if full(r'If a file that does not exist is specified, it will be created and data will be saved to the beginning of the file\.'):
            return reviewed([{'kind': 'file_creation_on_write', 'when': 'file_missing', 'create': True,
                              'write_position': 'beginning_of_file'}])
        if full(r'The newly created file has the archive attribute\.'):
            return reviewed([{'kind': 'created_file_attribute', 'attribute': 'archive'}])
        if full(r'When the number of columns is set to 0, data is read as a single-row data in a CSV format file\.'):
            return reviewed([{'kind': 'CSV_column_layout', 'when': {'column_count': 0}, 'layout': 'single_row',
                              'printed_operation': 'read_in_file_write_section'}],
                            status='conflict',
                            source_wording_qualification='manual_prints_read_in_write_processing')
        if full(r'When "Add converting the last line feed code of file to a comma \(write continuing the last line\)" '
                r'has been specified, the data will be saved continuing the last line of the file\.'):
            return reviewed([{'kind': 'CSV_append_mode', 'setting': 'convert_last_line_feed_to_comma',
                              'write_position': 'continue_last_line'}])
    if page in {551, 561}:
        transfer = full(r'This instruction (copies|moves) the file or folder specified by \(s2\) in the drive specified by '
                        r'\(s1\) to the folder specified by \(s4\) in the drive specified by \(s3\)\.')
        if transfer:
            return reviewed([{'kind': 'file_or_folder_transfer', 'operation': transfer[1].lower(),
                              'source': {'drive': _operand('s1'), 'path': _operand('s2')},
                              'destination': {'drive': _operand('s3'), 'folder': _operand('s4')}}])
        if full(r'If the folder specified by \(s4\) does not exist in the (?:copy )?destination, '
                r'the folder is created automatically\.'):
            return reviewed([{'kind': 'destination_folder_creation', 'folder': _operand('s4'),
                              'when': 'destination_folder_missing', 'automatic': True}])
        if page == 551 and full(r'In this case, the instruction is not completed with an error even if the value of bit 1 '
                                  r'\(overwrite setting\) of \(d1\) is 0\. \(Copying is skipped\.\)') and re.search(
                r'When a folder is specified by \(s2\), a file and subfolder with the same names may exist in the copy destination\.', source):
            return reviewed([{'kind': 'existing_folder_entry_transfer', 'operation': 'copy',
                              'when': {'source_is_folder': _operand('s2'), 'destination_contains_same_name_entry': True,
                                       'overwrite_enabled': {'subject': _operand('d1'), 'bit': 1, 'value': 0}},
                              'effect': 'skip_copy', 'error_completion': False}],
                            antecedent_basis='folder_and_same_name_entries_in_same_source_block')
        if page == 561 and full(r'When a folder has been specified in \(s2\) and a file or a folder having the same name '
                                  r'exists in the destination, the program does not terminate abnormally even if the value '
                                  r'of b1 \(overwrite setting\) of \(d1\) is 0\. \(The movement is skipped\.\)'):
            return reviewed([{'kind': 'existing_folder_entry_transfer', 'operation': 'move',
                              'when': {'source_is_folder': _operand('s2'), 'destination_contains_same_name_entry': True,
                                       'overwrite_enabled': {'subject': _operand('d1'), 'bit': 1, 'value': 0}},
                              'effect': 'skip_move', 'error_completion': False}])
    if page == 570:
        if full(r'This instruction rename the file or folder specified by \(s2\) in the drive specified by \(s1\) '
                r'to the file name or folder name specified by \(s3\)\.'):
            return reviewed([{'kind': 'file_or_folder_rename', 'drive': _operand('s1'),
                              'source_path': _operand('s2'), 'new_name': _operand('s3')}])
        if full(r'If the file name or folder name specified by \(s3\) already exists, the instruction is completed with an error\.'):
            return reviewed([{'kind': 'file_rename_conflict', 'when_existing': _operand('s3'), 'completion': 'error'}])
    if page in {585, 588}:
        read = page == 585
        if full(r'When \(s\)=0 \(R0\), and \(n\)=0 \(the number of (?:read|write) points: 0\) are specified, '
                r'all the points \(32768 points\) of the (?:extended file register \(ER\) are read to the file register \(R\) '
                r'in the CPU built-in memory|file register \(R\) in the CPU built-in memory are written to the extended file '
                r'register \(ER\)) in a batch\.'):
            return reviewed([{'kind': 'extended_register_transfer', 'when': {'start': _operand('s'), 'start_equals': 0,
                                                                           'count': _operand('n'), 'count_equals': 0},
                              'source': 'ER_in_SD_card' if read else 'R_in_CPU_memory',
                              'destination': 'R_in_CPU_memory' if read else 'ER_in_SD_card',
                              'first': 0, 'point_count': 32768}])
        conditional = full(r'The following action takes place when \(s\) = 1 or more \(from R1\) and \(n\) = 0 '
                           r'\(number of (?:read|write) points: 0\) is set\. - FX5S/FX5U/FX5UC CPU module: '
                           r'(?:ER \(s\) to the range of assigned device points are read out to the file register \(R\) '
                           r'in the CPU built-in memory|R \(s\) to the range of assigned device points in the CPU built-in '
                           r'memory are written to the extended file register \(ER\))\. - FX5UJ CPU module: '
                           r'(?:ER \(s\) to ER32767 are read out to the file register \(R\) in the CPU built-in memory|'
                           r'R \(s\) to R32767 in the CPU built-in memory are written to the extended file register \(ER\))\.')
        if conditional:
            return reviewed([{'kind': 'extended_register_transfer', 'when': {'start': _operand('s'), 'start_minimum': 1,
                                                                           'count': _operand('n'), 'count_equals': 0},
                              'source': 'ER_in_SD_card' if read else 'R_in_CPU_memory',
                              'destination': 'R_in_CPU_memory' if read else 'ER_in_SD_card',
                              'first': _operand('s'),
                              'CPU_cases': [{'models': ['FX5S', 'FX5U', 'FX5UC'], 'last': 'last_assigned_device_point'},
                                            {'models': ['FX5UJ'], 'last': 32767}]}])
    if page == 591 and full(r'The initial value of the extended file register \(ER\) when initializing is 65535 \(FFFFH\)\.'):
        return reviewed([{'kind': 'extended_register_initialization', 'target': 'ER', 'initial_value': 65535}])

    # Character operations use the actual terminator and the owning source
    # operands.  A comparison example never defines an ordering predicate.
    if page == 594:
        if full(r'When all the character strings match, the comparison is considered as matched\.'):
            return reviewed([{'kind': 'string_comparison', 'left': _operand('s1'), 'right': _operand('s2'),
                              'when': 'all_characters_equal', 'relation': 'equal'}])
        if full(r'When the character strings are different, the string with a large character code is considered as the large one\.'):
            return reviewed([{'kind': 'string_comparison', 'left': _operand('s1'), 'right': _operand('s2'),
                              'when': 'strings_differ', 'ordering': 'larger_character_code_is_greater'}])
        if full(r'When the character strings are different, the magnitude relation between them is determined based '
                r'on the size of the first dif ferent character code\.'):
            return reviewed([{'kind': 'string_comparison', 'left': _operand('s1'), 'right': _operand('s2'),
                              'ordering': 'first_differing_character_code'}])
    if page == 595 and full(r'If the character string specified by \(s1\) or \(s2\) has more than 16383 characters, '
                             r'the operation result is the non-conductive state\.'):
        return reviewed([{'kind': 'string_comparison_length_limit', 'operands': [_operand('s1'), _operand('s2')],
                          'when_any_character_count_greater_than': 16383, 'result': False}])
    if page in {596, 599}:
        concatenation = full(r'When character strings are concatenated, 00H indicating an end of the character string '
                             r'specified by \((d|s1)\) is ignored and the character string specified by \((s|s2)\) '
                             r'is concatenated to the last character of \((d|s1)\)\.')
        if concatenation and concatenation[1].lower() == concatenation[3].lower():
            return reviewed([{'kind': 'string_concatenation', 'left': _operand(concatenation[1].lower()),
                              'right': _operand(concatenation[2].lower()), 'ignored_left_terminator': 0}])
    if page in {605, 610} and full(r'The following figure shows the operations? when SM705 '
                                    r'\(Number of conversion digits selection\) is off and on\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'SM705_conversion_digit_selection_figure'}],
                        dimension='resources.section_structure', content_role='source_navigation')

    if page in {620, 623, 624, 627, 628, 629}:
        zero_padding = full(r'If the specified number of digits in the decimal part is greater than the number of digits '
                            r'of the 16-bit binary data, 0\(s\) is automatically added and the data is regarded as '
                            r'"0\.\[GLYPH-F06F\]\[GLYPH-F06F\]\[GLYPH-F06F\]\[GLYPH-F06F\]"\. '
                            r'\(1\): Added automatically')
        if page == 620 and zero_padding:
            return reviewed([{'kind': 'numeric_string_fraction_padding', 'source': _operand('s2'),
                              'fractional_digits': _operand('s1', 1), 'destination': _operand('d'),
                              'when': 'fractional_digit_setting_exceeds_binary_value_digit_count',
                              'pad_with': 0, 'integer_part': 0}],
                            notation_review_basis='repeated_placeholder_boxes_are_fractional_digits_not_operators')
        sign = full(r'As sign data(?: of the integral part)?, "20H" \(space\) is stored if the '
                    r'(16-bit binary data|32-bit binary data|single-precision real number) is positive, '
                    r'and "2DH" \(-\) is (?:stored|s tored) if the data is negative\.')
        if sign:
            return reviewed([{'kind': 'numeric_string_sign', 'source': _operand('s2' if page in {620, 623} else 's1'),
                              'destination': _operand('d'), 'positive_ASCII': 0x20, 'negative_ASCII': 0x2D,
                              'zero_sign': 'not_stated_in_clause',
                              'format': 'exponent_mantissa' if page == 629 else 'decimal'}])
        padding = full(r'If the total number of digits excluding the sign and the decimal point is greater than the '
                       r'number of digits of the (16|32)-bit binary data, "20H" \(space\) is stored between the sign and the numeric value\.')
        if padding:
            return reviewed([{'kind': 'numeric_string_padding', 'destination': _operand('d'),
                              'when': 'available_numeric_digits_exceed_binary_value_digits',
                              'position': 'between_sign_and_numeric_value', 'ASCII': 0x20}])
        over_width = full(r'If the number of digits of the (16|32)-bit binary data is greater, an error occurs\.'
                          r'(?: \(1\): "20H \(SP\)" is stored\.)?')
        if over_width and 'If the total number of digits excluding the sign and the decimal point is greater' in source:
            settings = 's1'
            return reviewed([{'kind': 'numeric_string_capacity_error', 'binary_width_bits': int(over_width[1]),
                              'when': 'binary_value_digits_exceed_available_numeric_digits',
                              'total_digit_setting': _operand(settings), 'fractional_digit_setting': _operand(settings, 1),
                              'result': 'error'}], antecedent_basis='compared_string_capacity_in_same_source_block')
        limit = full(r'Note that the number of digits in the decimal part must be '
                     r'(smaller than or equal to|smaller than|equal to or smaller than) the total number of digits minus (3|7)\.')
        if limit:
            settings = 's1' if page == 620 else 's2'
            return reviewed([{'kind': 'numeric_string_digit_constraint', 'fractional_digits': _operand(settings, 1 if page == 620 else 2),
                              'operator': '<' if limit[1].lower() == 'smaller than' else '<=',
                              'right': {'subtract': [_operand(settings, 0 if page == 620 else 1), int(limit[2])]},
                              'format': 'exponent' if page == 628 else 'decimal'}], dimension='constraints.documented_restriction')
        exponent_sign = full(r'For the sign of the exponent part, "2BH \(\+\)" is stored when the exponent is positive, '
                             r'and "2DH \(-\)" is stored when the exponen t is negative\.')
        if page == 629 and exponent_sign:
            return reviewed([{'kind': 'numeric_string_exponent_sign', 'source': _operand('s1'), 'destination': _operand('d'),
                              'positive_ASCII': 0x2B, 'negative_ASCII': 0x2D, 'zero_sign': 'not_stated_in_clause'}])
        minimum = full(r'When the number of decimal part digits is 0, the number of digits that can be specified by '
                       r'\(s2\)\+1 is "the number of digits \(24 at a maximum\) \[GLYPH-F0B3\] (2|6)"\.')
        if minimum and (page, minimum[1]) in {(627, '2'), (628, '6')}:
            return reviewed([{'kind': 'real_string_digit_domain', 'format': 'decimal' if page == 627 else 'exponent',
                              'when': {'subject': _operand('s2', 2), 'equals': 0},
                              'subject': _operand('s2', 1), 'minimum': int(minimum[1]), 'maximum': 24}],
                            dimension='parameters.documented_domain', notation_review_basis='original_PDF627_628_greater_or_equal_symbol')
        if page == 627 and full(r'For other than 0, the number of digits that can be specified by \(s2\)\+1 is '
                                  r'"the number of digits \(24 at a maximum\) \[GLYPH-F0B3\] '
                                  r'\(the number of decimal point digits \+ 3\)"\.') and 'When the number of decimal part digits is 0' in source:
            return reviewed([{'kind': 'real_string_digit_domain', 'format': 'decimal',
                              'when': {'subject': _operand('s2', 2), 'operator': '<>', 'value': 0},
                              'subject': _operand('s2', 1), 'minimum': {'add': [_operand('s2', 2), 3]}, 'maximum': 24}],
                            dimension='parameters.documented_domain', notation_review_basis='original_PDF627_greater_or_equal_symbol')
        real_padding = full(r'When the total number of digits subtracted by the digits for si gn, decimal point, and decimal '
                            r'part is larger than the integer part of the single-precision real number data, "20H \(space\)" '
                            r'is stored between the sign and the integer part\. \(1\): Total number of digits '
                            r'\(2\): Number of digits of decimal part \(3\): "20H \(SP\)" is stored\.')
        if page == 627 and real_padding:
            return reviewed([{'kind': 'real_string_integer_padding', 'source': _operand('s1'), 'destination': _operand('d'),
                              'total_digits': _operand('s2', 1), 'fractional_digits': _operand('s2', 2),
                              'when': 'available_integer_positions_exceed_integer_part_digit_count',
                              'padding_position': 'between_sign_and_integer_part', 'ASCII': 0x20}])
        rounding = full(r'If the decimal part of the single-precision real number data cannot be accommodated in the '
                        r'number of digits of the decimal par t, lower digits of the decimal part are '
                        r'(rounded off|rounded)\. \(1\): Total number of digits \(2\): Number of digits of decimal part '
                        r'\(3\): (These digits are rounded off|These values are rounded down)\.')
        if rounding and page in {627, 629}:
            return reviewed([{'kind': 'real_string_precision_reduction', 'source': _operand('s1'),
                              'destination': _operand('d'), 'fractional_digits': _operand('s2', 2),
                              'when': 'fractional_digits_do_not_fit_requested_count',
                              'format': 'decimal' if page == 627 else 'exponent',
                              'printed_rounding_description': 'rounded_off' if page == 627 else 'rounded_with_rounded_down_caption',
                              'tie_break_rule': 'not_stated_in_clause'}])
    if page == 626 and full(r'When 0 is specified in \(s2\), the decimal point format is applied\. '
                             r'\(1\): 0: Decimal part format, 1: Exponent format \(2\): Total number of digits '
                             r'\(3\): Number of digits of decimal part \(4\): 2 to 24 can be set\. '
                             r'\(1\): Decimal point format \(2\): Total number of digits \(3\): Number of digits of decimal part '
                             r'\(4\): Sign \(5\): Single-precision real number \(6\): "0000H" is automatically stored '
                             r'at the end of the character string\.'):
        return reviewed([{'kind': 'real_string_format', 'selector': _operand('s2'), 'when_equals': 0, 'format': 'decimal'},
                         {'kind': 'real_string_display_specification', 'selector': _operand('s2'),
                          'total_digits': {'subject': _operand('s2', 1), 'minimum': 2, 'maximum': 24},
                          'fractional_digits': _operand('s2', 2), 'source': _operand('s1'),
                          'destination': _operand('d'), 'automatic_terminal_value': 0}])
    if page == 628 and full(r'When 1 is specified in \(s2\), the exponent format is applied\.'):
        return reviewed([{'kind': 'real_string_format', 'selector': _operand('s2'), 'when_equals': 1, 'format': 'exponent'}])
    if page in {650, 653} and full(r'If the number of characters specified by \(s2\)\+1 is 0, no processing is performed\.'):
        return reviewed([{'kind': 'conditional_no_operation', 'when': {'subject': _operand('s2', 1), 'equals': 0}}])
    if page == 654 and full(r'When \(s2\)\+1 \(the number of characters to be extracted\) is "- 1", '
                             r'the entire character string stored in \(s1\) and later is stored to \(d\) and later\.'):
        return reviewed([{'kind': 'whole_string_transfer', 'when': {'subject': _operand('s2', 1), 'equals': -1},
                          'source': _operand('s1'), 'destination': _operand('d')}])
    if page == 657:
        if full(r'If no matched character string data is found, 0 is stored in \(d\)\.'):
            return reviewed([{'kind': 'string_search_result', 'when': 'no_matching_string',
                              'destination': _operand('d'), 'value': 0}])
        if full(r'When the search start position "s3" is a negative number or "0", search processing is not executed\.'):
            return reviewed([{'kind': 'conditional_no_operation', 'operation': 'string_search',
                              'when': {'subject': _operand('s3'), 'operator': '<=', 'value': 0}}])

    if page == 735:
        if full(r'The start of the block data in the device specified by \(s\) is counted as 1st point when the location is counted\.'):
            return reviewed([{'kind': 'block_location_numbering', 'first_device': _operand('s'), 'first_location': 1}])
        if full(r'When \(n\) is 0, the processing is not performed\.'):
            return reviewed([{'kind': 'conditional_no_operation', 'when': {'subject': _operand('n'), 'equals': 0}}])
    if page in {740, 743, 744, 745, 746}:
        saved = full(r'When the contents of (?:the )?index registers(?: and long index registers)? are saved, "1" is added to \(d\)\.')
        if saved:
            return reviewed([{'kind': 'index_save_depth', 'counter': _operand('d'), 'event': 'index_registers_saved', 'delta': 1}])
        if page in {743, 746} and full(r'When the saved contents of the index registers and long index registers are read, "1" is subtracted from \(d\)\.'):
            return reviewed([{'kind': 'index_save_depth', 'counter': _operand('d'), 'event': 'saved_index_registers_restored', 'delta': -1}])
        if page in {744, 745}:
            if full(r'These instructions save the contents of index registers and long index registers within the range specified by '
                    r'\(s\) in the devices specified by \(d\) and later\.'):
                return reviewed([{'kind': 'selected_index_save', 'selector': _operand('s'), 'destination': _operand('d')}])
            if full(r'The type of the index register or long index register saved is stored in the end of the saved data\.'):
                return reviewed([{'kind': 'index_save_format', 'destination': _operand('d'),
                                  'trailer': 'saved_index_register_type'}])
            if full(r'\(s\) value \| Z or LZ to be saved 0 \| Z, LZ \(whole range\) 1 \| Z \(whole range\) 2 \| LZ \(whole range\)'):
                return reviewed([{'kind': 'index_save_selector', 'subject': _operand('s'),
                                  'cases': [{'value': 0, 'register_classes': ['Z', 'LZ']},
                                            {'value': 1, 'register_classes': ['Z']},
                                            {'value': 2, 'register_classes': ['LZ']}], 'extent': 'whole_register_range'}])
            if full(r'The following shows values specified by \(s\) and the index register or long index register to be saved\.'):
                return reviewed([{'kind': 'source_navigation', 'target': 'following_index_save_selector_table'}],
                                dimension='resources.section_structure', content_role='source_navigation')
        if page in {740, 745}:
            cases = [{'models': ['FX5S', 'FX5U', 'FX5UC'], 'extent': 'assigned_Z_and_LZ_points'},
                     {'models': ['FX5UJ'], 'Z_points': 20, 'LZ_points': 2, 'word_count': 24}]
            if full(r'The number of points differs for each CPU module as explained below\. - FX5S/FX5U/FX5UC CPU module: '
                    r'The assigned range of index registers and long index register points is saved\. - FX5UJ CPU module: '
                    r'The contents for 20 points of the index register and 2 points of the long index register are saved\.'):
                return reviewed([{'kind': 'index_save_extent', 'destination': _operand('d'), 'CPU_cases': cases}])
            if full(r'The contents of index registers and long index registers for 24 words are saved\.') and all(
                    marker in source for marker in ['FX5S/FX5U/FX5UC', 'FX5UJ', '20 points', '2 points']):
                return reviewed([{'kind': 'index_save_extent', 'destination': _operand('d'), 'nominal_word_count': 24,
                                  'CPU_cases': cases}], antecedent_basis='explicit_CPU_qualifications_in_same_source_block')
            if full(r'Check the number of index registers and long index registers by SD300 and SD302, and secure the '
                    r'areas for the number of instructions to be used in advance\.'):
                return reviewed([{'kind': 'index_save_storage_requirement',
                                  'register_count_devices': ['SD300', 'SD302'],
                                  'reserve_before_execution': True, 'capacity_scales_with': 'save_nesting_count'}],
                                dimension='resources.memory_requirement')
        if page in {740, 745} and full(r'When a nesting structure is adopted, the areas to be used are added to \(d\) '
                                      r'and later every time (?:the ZPUSH\(P\) instructions are used|the selected data of '
                                      r'index register/long index register save instructions \(ZPUSH\(P\) instructions\) are executed)\.'):
            return reviewed([{'kind': 'nested_index_save_storage', 'destination': _operand('d'),
                              'allocation': 'append_one_save_area_per_nested_ZPUSH_execution'}])
        if page == 746 and full(r'The data specified by \(s\) is regarded as dummy data and ignored\.'):
            return reviewed([{'kind': 'ignored_operand', 'subject': _operand('s'), 'role': 'dummy'}])

    if page in {748, 750}:
        limit = full(r'To control the input value only with the (upper|lower) limit, set the (minimum|maximum) '
                     r'value within the setting range in \((s1|s2)\)\.')
        if limit and (limit[1].lower(), limit[2].lower(), limit[3].lower()) in {
                ('upper', 'minimum', 's1'), ('lower', 'maximum', 's2')}:
            return reviewed([{'kind': 'single_sided_limit_control', 'active_limit': limit[1].lower(),
                              'other_limit': _operand(limit[3].lower()), 'other_limit_value': 'type_' + limit[2].lower()}])
    if page in {752, 754, 759}:
        overflow_formula = full(r'When the output value(?:s)? to be stored in the device(?:s)? specified by \(d\)'
                                r'(?: and \(d\)\+1)? (?:is a|are) (16|32)-bit signed binary value(?:s)? and the '
                                r'operation result exceeds the range of (-?\d+) to (\d+), the output value is calculated as follows\.')
        if overflow_formula and (int(overflow_formula[1]), int(overflow_formula[2]), int(overflow_formula[3])) in {
                (16, -32768, 32767), (32, -2147483648, 2147483647)}:
            return reviewed([{'kind': 'source_navigation', 'target': 'following_signed_output_overflow_formula',
                              'result_condition': {'destination': _operand('d'), 'width_bits': int(overflow_formula[1]),
                                                   'operation_result_outside': {'minimum': int(overflow_formula[2]),
                                                                                'maximum': int(overflow_formula[3])}},
                              'formula_extracted_by_this_caption': False}],
                            dimension='resources.section_structure', content_role='source_navigation',
                            phase='result', input_numeric_gate=False)
    if page in {761, 764, 767, 771}:
        narrow = page in {761, 767}
        if full(r'If the operation result is not an integer, the number in the first decimal place is rounded off\.'):
            return reviewed([{'kind': 'scaling_result_rounding', 'when': 'non_integer_result',
                              'precision': 'integer', 'round_at': 'first_decimal_place',
                              'tie_break_rule': 'not_stated_in_clause'}])
        if full(r'Set \(s1\) within the scaling conversion data range \(device value(?:s)? of \(s2\)(?: and \(s2\)\+1)?\)\.'):
            return reviewed([{'kind': 'scaling_input_domain', 'subject': _operand('s1'),
                              'range_source': _operand('s2'), 'range_source_width_bits': 16 if narrow else 32}],
                            dimension='parameters.documented_domain')
        if full(r'If the same X coordinate is specified by multiple points, '
                                  r'the Y coordinate value of the point whose number is the largest is output\.'):
            return reviewed([{'kind': 'scaling_duplicate_X_selection', 'when': 'multiple_points_share_X',
                              'selected_point': 'largest_point_number', 'output': 'selected_point_Y'}])
        points = full(r'Set the number of coordinate points for the scaling conversion data within the range of 1 to (65535|4294967295)\.')
        if points and (narrow == (points[1] == '65535')):
            return reviewed([{'kind': 'scaling_coordinate_count', 'subject': _operand('s2'),
                              'minimum': 1, 'maximum': int(points[1]), 'width_bits': 16 if narrow else 32}],
                            dimension='parameters.documented_domain')
    if page in {767, 771} and full(r'Setting item \("n" indicates the number of coordinate points specified by \(s2\)\.\)'):
        return reviewed([{'kind': 'source_notation_binding', 'symbol': 'n', 'meaning': 'coordinate_point_count',
                          'source': _operand('s2')}], dimension='parameters.source_notation')
    if page == 779:
        if full(r'This instruction increments the current value of the counter specified by \(d\) by 1 when the operation '
                r'result up to UDCNTF instruction changes from OFF to ON, and when the counter reaches the end of its count, '
                r'NO contact becomes turns ON and NC contact becomes turns OFF\.'):
            return reviewed([{'kind': 'edge_counter', 'counter': _operand('d'),
                              'trigger': 'preceding_operation_result_OFF_to_ON', 'delta': 1,
                              'at_count_completion': {'NO_contact': True, 'NC_contact': False}}],
                            languages=['ladder'], conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}])
        if full(r'When the long counter specified by \(d\) is a normal counter, the counting direction of LC0 to LC34 '
                r'can be switched by turning on or off the corresponding special device \(SM8200 to SM8234\)\.'):
            return reviewed([{'kind': 'counter_direction_selector', 'counter': _operand('d'),
                              'when_counter_kind': 'normal', 'counter_range': ['LC0', 'LC34'],
                              'selector_range': ['SM8200', 'SM8234'], 'mapping': 'corresponding_offset'}])
        if full(r'For LC35 or later, only up counting is supported\.'):
            return reviewed([{'kind': 'counter_direction_limit', 'counter': _operand('d'),
                              'when_counter_number_at_least': 35, 'direction': 'up_only'}])
        if full(r'When unsigned \(0 to 4294967295\) is assigned to the LC, the OUT LC instruction is used\.'):
            return reviewed([{'kind': 'unsigned_long_counter_representation', 'range': {'minimum': 0, 'maximum': 4294967295},
                              'required_instruction': 'OUT_LC'}], dimension='resources.instruction_usage')
    if page == 784:
        if full(r'When the start value \(s1\) and the end value \(s2\) have been specified and the command input is set '
                r'to ON, the value obtained by adding a value divided equally by "n" times to \(s1\) in the next '
                r'operation cycle is stored to \(d\)\.'):
            return reviewed([{'kind': 'ramp_generation', 'when_command_input': True,
                              'start': _operand('s1'), 'end': _operand('s2'), 'division_count': _operand('n'),
                              'destination': _operand('d'), 'update_period': 'operation_cycle',
                              'first_increment_timing': 'next_operation_cycle', 'increment': 'equally_divided_start_to_end_change'}])
        if full(r'By combining this instruction and an analog output, the cushion start/stop command can be output\.'):
            return reviewed([{'kind': 'ramp_application', 'requires': 'analog_output', 'use': 'cushion_start_stop'}],
                            dimension='resources.instruction_usage')
        if full(r'If the command input is set to OFF in the middle of operation, execution is paused\. '
                r'\(The current value stored in \(d\) is held, and the number of scans stored in \(d\)\+1 is cleared\.\) '
                r'When the command input is set to ON again, \(d\) is cleared, and the operation is started from \(s1\)\.'):
            return reviewed([{'kind': 'ramp_command_state', 'when': 'command_input_OFF_during_operation',
                              'execution': 'paused', 'hold': _operand('d'), 'clear': _operand('d', 1)},
                             {'kind': 'ramp_command_state', 'when': 'command_input_ON_again_after_pause',
                              'clear': _operand('d'), 'restart_from': _operand('s1')}])
        if full(r'If the command input is set to OFF in the middle of operation, execution is paused\. '
                r'\(The current value stored in \(d\) is held, and the number of scans stored in \(d\)\+1 is cleared\.\)'):
            return reviewed([{'kind': 'ramp_command_state', 'when': 'command_input_OFF_during_operation',
                              'execution': 'paused', 'hold': _operand('d'), 'clear': _operand('d', 1)}])
        if full(r'When the command input is set to ON again, \(d\) is cleared, and the operation is started from \(s1\)\.') and (
                'If the command input is set to OFF in the middle of operation' in source):
            return reviewed([{'kind': 'ramp_command_state', 'when': 'command_input_ON_again_after_pause',
                              'clear': _operand('d'), 'restart_from': _operand('s1')}],
                            antecedent_basis='paused_ramp_in_same_source_block')

    if page in {787, 793}:
        density = full(r'This instruction counts the number of times the device input specified by \(s1\) turns off '
                       r'\[GLYPH-F0AE\] on only for the time \(in (16|32)-bit data units\) specified by \(s2\) '
                       r'\[GLYPH-F0B4\] 1ms and stores the operation result in the device specified by \(d\)\.')
        if density and ((page == 787) == (density[1] == '16')):
            return reviewed([{'kind': 'pulse_density_measurement', 'input': _operand('s1'), 'edge': 'OFF_to_ON',
                              'duration': {'count': _operand('s2'), 'unit_ms': 1, 'width_bits': int(density[1])},
                              'destination': _operand('d'), 'result': 'number_of_input_edges_in_duration'}],
                            notation_review_basis='written_OFF_ON_transition_and_millisecond_duration_in_same_sentence')
        if full(r'When a word device is specified by \(s1\), this instruction counts the number of pulses '
                                  r'by the high-speed counter setting of the channel number corresponding to each word device\.'):
            return reviewed([{'kind': 'pulse_density_input_mapping', 'subject': _operand('s1'), 'when_device_form': 'word',
                              'source': 'high_speed_counter_channel_corresponding_to_word_device',
                              'configuration': 'corresponding_high_speed_counter_settings'}])
        if full(r'When a bit device is specified by \(s1\), the following input assignment devices '
                                  r'\(shaded area\) are valid\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_shaded_pulse_density_input_assignment_tables',
                              'when_device_form': {'subject': _operand('s1'), 'form': 'bit'},
                              'table_assignments_extracted_by_this_caption': False}],
                            dimension='resources.section_structure', content_role='source_navigation')
        if full(r'When the high-speed counter FX3 compatibility function is enabled, only the 1-phase '
                                  r'1-input pulse input mode \(S/W\) \(general-purpose input assignment of the 1-phase '
                                  r'1-input counter \(switching S/W up or down\)\) is enabled\.'):
            return reviewed([{'kind': 'pulse_density_input_mode', 'when': 'high_speed_counter_FX3_compatibility_enabled',
                              'allowed': 'one_phase_one_input_software_up_down'}], dimension='constraints.documented_restriction')
    if page in {790, 796} and full(r'The table below shows the related device update timing\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_pulse_density_device_update_timing_table'}],
                        dimension='resources.section_structure', content_role='source_navigation')
    if page in {798, 802, 805, 806}:
        speed = full(r'Set the value from 0 to (65535|2147483647) \(in user unit\) to the command speed \(s\), '
                     r'so that the command speed is 200 kpps or less when the command speed is converted to frequency\.')
        if speed:
            return reviewed([{'kind': 'pulse_output_speed_domain', 'subject': _operand('s'),
                              'user_unit_range': {'minimum': 0, 'maximum': int(speed[1])},
                              'converted_frequency_maximum_pps': 200000}], dimension='parameters.documented_domain')
        address = full(r'Set the value from 0 to (65535|2147483647) \(in user unit\) (?:to|in) the positioning address \(n\), '
                       r'so that the positioning address is within the range from 0 to 2147483647 when the positioning '
                       r'address is converted to number of pulses\.')
        if address:
            return reviewed([{'kind': 'pulse_output_position_domain', 'subject': _operand('n'),
                              'user_unit_range': {'minimum': 0, 'maximum': int(address[1])},
                              'converted_pulse_range': {'minimum': 0, 'maximum': 2147483647}}],
                            dimension='parameters.documented_domain')
    if page in {814, 815, 816, 821, 822, 823}:
        if page in {814, 821} and full(r'Time with a unit selected on the parameter setting screen '
                                      r'\( \[GLYPH-F06D\]s or ms\) can be specified by \(s1\) and \(s2\)\.'):
            return reviewed([{'kind': 'PWM_time_unit', 'subjects': [_operand('s1'), _operand('s2')],
                              'unit_source': 'parameter_setting_screen', 'unit_options': ['us', 'ms']}],
                            notation_review_basis='original_PWM_PDF814_821_microsecond_unit')
        if page in {814, 821} and full(r'The pulse output destination channel number selected on the parameter setting '
                                      r'screen can be specified by \(d\)\.'):
            return reviewed([{'kind': 'PWM_output_channel', 'subject': _operand('d'),
                              'channel_definition_source': 'parameter_setting_screen'}])
        if full(r'This instruction stores? the number of pulses, pulse width, and period output from each channel to an SD device\.'):
            return reviewed([{'kind': 'PWM_channel_status', 'storage': 'channel_SD_devices',
                              'fields': ['number_of_pulses', 'pulse_width', 'period']}])
        if full(r'The pulse width and period are stored in the units set by the parameters\.'):
            return reviewed([{'kind': 'PWM_channel_status_units', 'fields': ['pulse_width', 'period'],
                              'units_source': 'CPU_parameters'}])
        if full(r'When 0 is specified in the pulse output, pulses are output without any limitation\.'):
            return reviewed([{'kind': 'PWM_pulse_count', 'when': {'specified_count': 0}, 'output_count': 'unlimited'}])
        if full(r'This instruction stores the number of pulses output from each channel\.'):
            return reviewed([{'kind': 'PWM_channel_status', 'field': 'actual_output_pulse_count', 'scope': 'each_channel'}])
        if full(r'When the number of output pulses is set from the no limitation output setting \(number of output pulses '
                r'is 0\), the number of output pulses is not updated \(because outputting pulses continues or stops '
                r'in the no limitation output\)\.'):
            return reviewed([{'kind': 'PWM_pulse_count_update', 'when': 'changing_count_from_unlimited_zero_setting',
                              'count_update_applied': False, 'output_behavior': 'continues_or_stops_under_unlimited_setting'}])
        maximum = full(r'The maximum number of output pulses which can be output when the (PWM|DPWM) instruction is '
                       r'executed once \(= maximum value which can be set to an SD device\) is "2147483647"\.')
        if maximum:
            return reviewed([{'kind': 'PWM_pulse_count_limit', 'form': maximum[1].upper(), 'maximum': 2147483647,
                              'scope': 'single_instruction_execution_and_SD_count_setting'}],
                            dimension='parameters.documented_domain')
        if full(r'When the number of output pulses is 0 \(no limitation output setting\), the monitor of the current '
                r'number of output pulses is set to 0\.'):
            return reviewed([{'kind': 'PWM_pulse_count_monitor', 'when': {'specified_output_count': 0}, 'monitor_value': 0}])
        if full(r'When the number of output pulses is specified, the output pulses are monitored\.'):
            return reviewed([{'kind': 'PWM_pulse_count_monitor', 'when': 'finite_output_count_specified', 'monitoring': True}])
        if full(r'When the D?PWM output is executed several times, the monitor of the number of output pulses is an integrated value\.'):
            return reviewed([{'kind': 'PWM_pulse_count_monitor', 'multiple_executions': 'accumulated_pulse_count'}])
        if full(r'The monitor of the current number of output pulses is updated when the number of pulses is counted '
                r'at the falling edge of pulses in the positive logic and at the rising edge of pulses in the negative logic\.'):
            return reviewed([{'kind': 'PWM_pulse_count_monitor_edge',
                              'cases': [{'pulse_logic': 'positive', 'counted_edge': 'falling'},
                                        {'pulse_logic': 'negative', 'counted_edge': 'rising'}]}])
        if full(r'When the output always remains ON or OFF, the monitor of the current number of output pulses does not change\.'):
            return reviewed([{'kind': 'PWM_pulse_count_monitor', 'when': 'output_steadily_ON_or_OFF', 'value': 'held'}])
    if page == 828 and full(r'For each output, the I/O processing is executed immediately in turn in interrupt at every 10 ms or 20 ms\.'):
        return reviewed([{'kind': 'matrix_IO_refresh', 'order': 'each_output_in_turn',
                          'execution': 'immediate_in_interrupt', 'interval_ms_options': [10, 20]}])
    if page == 843 and full(r'The following figure shows the output patterns for device points \(n\) starting from \(d\) '
                             r'when the command input is set to on\.'):
        return reviewed([{'kind': 'source_navigation', 'target': 'following_absolute_drum_output_pattern_figure',
                          'output': _operand('d'), 'point_count': _operand('n'), 'when_command_input': True}],
                        dimension='resources.section_structure', content_role='source_navigation')
    if page == 846:
        if full(r'The current value of a counter is compared with the data table having "n" lines starting from \(s1\) '
                r'\(which occupies "n" lines \[GLYPH-F0B4\] 1 device\)\.'):
            return reviewed([{'kind': 'relative_drum_comparison', 'counter': _operand('s2'),
                              'table': _operand('s1'), 'row_count': _operand('n'), 'devices_per_row': 1}],
                            notation_review_basis='table_row_count_and_one_device_per_row')
        if full(r'When the value is equivalent to the table data, the current output is reset, and the next output is controlled\.') and (
                'The current value of a counter is compared' in source):
            return reviewed([{'kind': 'relative_drum_output_advance', 'counter': _operand('s2'), 'table': _operand('s1'),
                              'output_start': _operand('d'), 'when': 'counter_value_equals_current_table_entry',
                              'actions': ['reset_current_output', 'control_next_output']}],
                            antecedent_basis='counter_and_table_comparison_in_same_source_block')
        if full(r'In this way, the ON/OFF status of specified outputs is controlled in turn\.') and (
                'When the value is equivalent to the table data' in source):
            return reviewed([{'kind': 'relative_drum_output_order', 'output_start': _operand('d'),
                              'control': 'sequential_output_ON_OFF'}],
                            antecedent_basis='counter_equality_output_advance_in_same_source_block')

    if page in {852, 854}:
        found = full(r'When the same data (exists|does not exist), five (32-bit binary data )?devices starting from '
                     r'\(d\)(?:\+1 and \(d\))? store the number of same data, first position, last position, '
                     r'maximum value position and minimum value position\.')
        if found:
            return reviewed([{'kind': 'data_search_result_layout', 'when_matching_data': found[1].lower() == 'exists',
                              'destination': _operand('d'), 'element_width_bits': 16 if page == 852 else 32,
                              'ordered_fields': ['matching_count', 'first_match_position', 'last_match_position',
                                                 'maximum_value_position', 'minimum_value_position']}])
        absent = full(r'In this case, however, 0 is stored in three (32-bit )?devices starting from \(d\)'
                      r'(?:\+1 and \(d\))? \(which store the number of same data, first position and last position\)\.')
        if absent and 'When the same data does not exist' in source:
            return reviewed([{'kind': 'data_search_no_match', 'when': 'no_matching_data', 'destination': _operand('d'),
                              'element_width_bits': 16 if page == 852 else 32,
                              'zero_fields': ['matching_count', 'first_match_position', 'last_match_position']}],
                            antecedent_basis='no_matching_data_in_same_source_block')
    if page in {856, 858}:
        zero = full(r'When all binary (16|32)-bit data of the device specified by \(s\) are 0 \(off\), '
                    r'the zero flag \(SM8020\) turns on\.')
        if zero:
            return reviewed([{'kind': 'zero_test_flag', 'subject': _operand('s'), 'width_bits': int(zero[1]),
                              'when_all_bits_zero': True, 'flag': 'SM8020', 'state': True}])
    if page in {859, 861}:
        if full(r'When a constant \(K\) is specified in the device specified by \(s\), it is automatically converted into binary\.'):
            return reviewed([{'kind': 'constant_representation', 'subject': _operand('s'),
                              'when_constant_class': 'K', 'conversion': 'automatic_binary'}])
        bit = full(r'When the result above is (on|off), these instructions turn \(d\) (on|off)\.')
        if bit and bit[1].lower() == bit[2].lower() and re.search(
                r'check whether \(n\) bit\(s\) of binary (?:16|32)-bit data of the device specified by \(s\)', section.get('text', ''), re.I):
            return reviewed([{'kind': 'bit_test_output', 'source': _operand('s'), 'bit_position': _operand('n'),
                              'destination': _operand('d'), 'when_bit_value': bit[1].lower() == 'on',
                              'destination_state': bit[2].lower() == 'on'}],
                            antecedent_basis='explicit_BON_bit_test_in_own_section')
    if page in {884, 886} and full(r'The remainder is ignored\.') and title in {
            'calculating_the_mean_value_of_16_bit_data', 'calculating_the_mean_value_of_32_bit_data'} and re.search(
            r'The sum is obtained as algebraic sum, and divided by \(n\)', section.get('text', '')):
        return reviewed([{'kind': 'integer_mean_remainder', 'source': _operand('s'), 'count': _operand('n'),
                          'destination': _operand('d'), 'remainder': 'discarded'}],
                        antecedent_basis='explicit_algebraic_sum_division_in_own_mean_section')
    if page == 895 and full(r'The nibble of a bit device, and the bit of a word device cannot be specified in \(s\)\.'):
        return reviewed([{'kind': 'disallowed_device_form', 'subject': _operand('s'),
                          'forms': ['bit_device_nibble', 'word_device_bit']}], dimension='constraints.documented_restriction')

    if page == 898:
        if full(r'Executing these instructions immediately changes the real time clock data\.'):
            return reviewed([{'kind': 'clock_write_timing', 'effect': 'RTC_changed_immediately_on_execution'}])
        if full(r'Therefore, transfer the clock data of a few minutes ahead the current time to the clock data area '
                r'\(s\) to \(s\)\+6 in advance\.'):
            return reviewed([{'kind': 'clock_adjustment_preparation', 'data': _operand('s'), 'last_offset': 6,
                              'prepared_time': 'a_few_minutes_ahead_of_current_time'}], dimension='resources.instruction_usage')
        if full(r'Execute the instruction when the actual time matches the clock data time\.'):
            return reviewed([{'kind': 'clock_adjustment_trigger', 'when': 'actual_time_matches_prepared_clock_data_time'}],
                            dimension='resources.instruction_usage')
        if full(r'When using these instructions to set the clock data \(i\.e\., performing time adjustment\), '
                r'control of special relay SM8015 \(clock stop/adjustment\) is not required\.'):
            return reviewed([{'kind': 'clock_write_control', 'SM8015_control_required': False}])
        if full(r'If incorrect values \(i\.e\., values out of range\) are set to the write source area, the clock data will not be updated\.'):
            return reviewed([{'kind': 'clock_write_invalid_data', 'when': 'source_clock_values_out_of_range', 'RTC_update': False}])
        if full(r'In this case, correct the clock data in the write source area and execute the instruction\.') and 'out of range' in source:
            return reviewed([{'kind': 'clock_write_invalid_data_recovery', 'when': 'source_clock_values_out_of_range',
                              'actions': ['correct_source_clock_data', 'execute_instruction']}],
                            dimension='resources.instruction_usage', antecedent_basis='invalid_values_in_same_source_block')
    if page == 899:
        if full(r'Device Name \| Description SM8019 Real time clock error \| This special data register turns on '
                r'when the clock data value in the special register is exceeding the setting range\.'):
            return reviewed([{'kind': 'clock_data_error_flag', 'device': 'SM8019',
                              'when': 'special_register_clock_value_out_of_setting_range', 'state': True}])
        clock_row = full(r'(?:Binary code(?: \(FX3 compatible area\))? )?(SD\d+) Binary clock data '
                         r'\((year|month|day|hour|minute|second)\) \| The (?:year data in the clock data is stored as a '
                         r'four-digit binary code|(?:month|day|hour|minute|second) data in the clock data is stored as a binary code)\.')
        if clock_row:
            expected = {'SD210': 'year', 'SD211': 'month', 'SD212': 'day', 'SD213': 'hour', 'SD214': 'minute',
                        'SD215': 'second', 'SD8013': 'second', 'SD8014': 'minute', 'SD8015': 'hour', 'SD8016': 'day',
                        'SD8017': 'month', 'SD8018': 'year'}
            if expected.get(clock_row[1].upper()) == clock_row[2].lower():
                return reviewed([{'kind': 'clock_register_field', 'device': clock_row[1].upper(), 'field': clock_row[2].lower(),
                                  'encoding': 'binary', **({'year_digits': 4} if clock_row[2].lower() == 'year' else {})}])
        weekday = full(r'(SD216|SD8019) Binary clock data \(day of the week\) The day-of-a-week data in the clock data '
                       r'\(0: Sunday, 1: Monday, \.\.\., 6: Saturday\) is stored as a binary code\.')
        if weekday:
            return reviewed([{'kind': 'clock_register_field', 'device': weekday[1].upper(), 'field': 'day_of_week',
                              'encoding': 'binary', 'numbering': {'Sunday': 0, 'Monday': 1, 'Saturday': 6}}])

    if page in {916, 919}:
        data_kind = 'date' if page == 916 else 'time'
        # The 0003H row is a printed manual conflict, confirmed on the PDF:
        # its field label names two different fields, but both offsets are +2.
        # Preserve that row literally; do not silently substitute +1 for +2.
        table_header = (r'\(s3\) value when comparing two specified ' + data_kind + r' data '
                        r'\(s3\) value when comparing the specified ' + data_kind + r' data with the current ' + data_kind +
                        r' Comparison target Contents of processing ')
        selector_table = full(table_header + r'(?P<rows>.+)')
        if selector_table:
            fields = (['day', 'month', 'year'] if page == 916 else ['second', 'minute', 'hour'])
            labels = ([fields[0].capitalize(), fields[1].capitalize(), 'Month, day', fields[2].capitalize(),
                       'Year, day', 'Year, month', 'Year, month, day'] if page == 916 else
                      ['Second data', 'Minute data', 'Minute and second data', 'Hour data',
                       'Hour and second data', 'Hour and minute data', 'Hour, minute, and second data'])
            bodies = [
                'Only data in the device specified by (s1)+2 is compared.',
                'Only data in the device specified by (s1)+1 is compared.',
                'Data in the device areas specified by (s1)+2 and (s1)+2 are compared.',
                'Only data in the device specified by (s1) is compared.',
                'Data in the device areas specified by (s1) and (s1)+2 are compared.',
                'Data in the device areas specified by (s1) and (s1)+1 are compared.',
                'The entire ' + data_kind + ' data in the device areas specified by (s1), (s1)+1, and (s1)+2 are compared.',
            ]
            expected = ' '.join('000%dH 800%dH %s %s' % (i, i, labels[i - 1], bodies[i - 1]) for i in range(1, 8))
            if selector_table['rows'].casefold() == expected.casefold():
                selected_fields = [[fields[0]], [fields[1]], [fields[1], fields[0]], [fields[2]],
                                   [fields[2], fields[0]], [fields[2], fields[1]], [fields[2], fields[1], fields[0]]]
                printed_offsets = [[2], [1], [2, 2], [0], [0, 2], [0, 1], [0, 1, 2]]
                return reviewed([{'kind': 'clock_comparison_selector_table', 'selector': _operand('s3'),
                                  'left': _operand('s1'), 'data_kind': data_kind,
                                  'rows': [{'specified_data_selector': i, 'current_clock_selector': 0x8000 + i,
                                            'selected_fields': selected_fields[i - 1],
                                            'printed_left_offsets': printed_offsets[i - 1]}
                                           for i in range(1, 8)]},
                                 {'kind': 'source_conflict', 'conflict': 'field_labels_and_duplicated_source_offsets_disagree',
                                  'selector_values': [3, 0x8003], 'selected_fields': selected_fields[2],
                                  'printed_left_offsets': [2, 2], 'resolution': 'not_established'}],
                                dimension='parameters.clock_comparison_selector', status='conflict',
                                source_conflict_review_basis='original_PDF916_919_0003H_rows')
        if full(r'When 0 is set to the 15 bit, the data in the device specified by \(s1\) and the (?:date|time) '
                r'data in the device specified by \(s2\) are compared\.'):
            return reviewed([{'kind': 'clock_comparison_source', 'data_kind': data_kind,
                              'when': {'subject': _operand('s3'), 'bit': 15, 'value': 0},
                              'left': _operand('s1'), 'right': _operand('s2')}])
        if full(r'When 1 is set, the data in the device specified by \(s1\) is compared with the current (?:date|time)\.') and re.search(
                r'When 0 is set to the 15 bit', source):
            return reviewed([{'kind': 'clock_comparison_source', 'data_kind': data_kind,
                              'when': {'subject': _operand('s3'), 'bit': 15, 'value': 1},
                              'left': _operand('s1'), 'right': 'current_' + data_kind}],
                            antecedent_basis='comparison_bit_15_in_same_source_block')
        if full(r'The (?:date|time) data in the device specified by \(s2\) is ignored\.') and re.search(
                r'current (?:date|time)', source):
            return reviewed([{'kind': 'clock_comparison_dummy_input', 'subject': _operand('s2'),
                              'when': {'selector': _operand('s3'), 'bit': 15, 'value': 1}, 'used': False}],
                            antecedent_basis='current_clock_comparison_in_same_source_block')
        if full(r'Comparing (?:the specified date data with the current date|specified time data with current time data) '
                r'These instructions compare the (?:date|time) data in the device specified by \(s1\) with the current '
                r'(?:date|time) data in accordance with the cond ?itions set by \(s3\)\. '
                r'\(Devices are used as a normally open contact\.\)'):
            return reviewed([{'kind': 'clock_current_value_comparison', 'left': _operand('s1'),
                              'right': 'current_' + data_kind, 'comparison_selector': _operand('s3'),
                              'ladder_contact_representation': 'normally_open'}])
    if page == 928:
        if full(r'In SM420 to SM424 \(SM8330 to SM8334\), when one device is turned on, another device is also turned on at the same time\.'):
            return reviewed([{'kind': 'timing_relay_aliases', 'groups': [{'primary': 'SM' + str(420 + i),
                                                                       'FX3_compatible': 'SM' + str(8330 + i)} for i in range(5)],
                              'ON_update': 'corresponding_pair_simultaneous'}])
        if full(r'The counted number of scans stored among SD8330 to SD8334 is reset when the counted value reaches '
                r'"\(n1\)\+\(n2\)" or when the command input \(instruction\) is set to on\.'):
            return reviewed([{'kind': 'timing_clock_scan_count_reset', 'storage': ['SD8330', 'SD8334'],
                              'events': [{'count_reaches': {'add': [_operand('n1'), _operand('n2')]}}, 'command_input_ON']}])
        if full(r'When the command input is set to ON, the operation is started\.'):
            return reviewed([{'kind': 'timing_clock_state', 'event': 'command_input_ON', 'state': 'started'}])
        if full(r'The special relay of the timing clock output destination is set to ON or OFF by the END instruction\.'):
            return reviewed([{'kind': 'timing_clock_output_update', 'timing': 'END_instruction', 'value': 'ON_or_OFF'}])
        if full(r'Even if the command input is set to OFF, the operation is not stopped\.'):
            return reviewed([{'kind': 'timing_clock_state', 'event': 'command_input_OFF', 'state': 'continues'}])
        if full(r'In the STOP mode, the operation is stopped\.'):
            return reviewed([{'kind': 'timing_clock_state', 'when_CPU_mode': 'STOP', 'state': 'stopped'}])
        if full(r'When the power to the CPU module is turned OFF, the operation is stopped\.'):
            return reviewed([{'kind': 'timing_clock_state', 'event': 'CPU_power_OFF', 'state': 'stopped'}])
        if full(r'When \(n1\) and \(n2\) are set to "0", the status is as shown below:'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_zero_scan_count_timing_table',
                              'caption_inputs': [{'subject': _operand('n1'), 'value': 0}, {'subject': _operand('n2'), 'value': 0}],
                              'table_output_state_extracted_by_this_caption': False}],
                            dimension='resources.section_structure', content_role='source_navigation')
    if page in {930, 932}:
        width = 16 if page == 930 else 32
        hour = full(r'This instruction measures the period of time for which the input contact is on in units of hour, '
                    r'and turns on the device specified by \(d2\) when the accumulated ON time exceeds the time '
                    r'\((16|32)-bit binary data\) specified in \(s\)\.')
        if hour and int(hour[1]) == width:
            return reviewed([{'kind': 'hour_meter', 'measured_quantity': 'accumulated_input_contact_ON_time', 'unit': 'hour',
                              'threshold': _operand('s'), 'threshold_width_bits': width,
                              'when_accumulated_time': 'greater_than_threshold', 'set': _operand('d2')}])
        if full(r'\(d2\) is set to on when the current value in \(d1\)(?:\+1 and \(d1\))? exceeds the time specified by \(s\)\.'):
            return reviewed([{'kind': 'hour_meter_output', 'elapsed_time': _operand('d1'), 'elapsed_width_bits': width,
                              'operator': '>', 'threshold': _operand('s'), 'set': _operand('d2')}])
        if full(r'When the current value reaches the maximum value of (16|32)-bit data, the measurement is stopped\.'):
            return reviewed([{'kind': 'hour_meter_saturation', 'when_current_value': 'maximum_' + str(width) + '_bit_value',
                              'measurement': 'stopped', 'overflow_wrap': False}])
        clear = full(r'For continuing the measurement, clear the current value stored in \(d1\) to \(d1\)\+(1|2)\.')
        if clear and int(clear[1]) == (1 if width == 16 else 2):
            return reviewed([{'kind': 'hour_meter_restart', 'action': 'clear_elapsed_time',
                              'storage': _operand('d1'), 'last_offset': int(clear[1])}], dimension='resources.instruction_usage')
    if page in {941, 942, 948}:
        repeated = full(r'When a constant is specified in \(s2\), \(n\)(?: (x|\[GLYPH-F0B4\]) 2)? words of the '
                        r'same data \(the value specified by \(s2\)\) is written starting from the specified buffer memory address\.')
        if repeated:
            return reviewed([{'kind': 'module_buffer_fill', 'when_constant': _operand('s2'), 'data': _operand('s2'),
                              'element_count': _operand('n'), 'words_per_element': 1 if page == 941 else 2,
                              'destination': 'specified_module_buffer_memory_start'}])
    if page in {950, 954}:
        completed = full(r'When this instruction is finished (normally|abnormally), instruction execution '
                         r'(complete|abnormal end) flag \((SM8029|SM8329)\) turns on\.')
        if completed and (completed[1].lower(), completed[2].lower(), completed[3].upper()) in {
                ('normally', 'complete', 'SM8029'), ('abnormally', 'abnormal end', 'SM8329')}:
            return reviewed([{'kind': 'instruction_completion_flag', 'completion': completed[1].lower(),
                              'device': completed[3].upper(), 'state': True}])
        flag = full(r'(?:Device \| Name \| Description )?(SM8029|SM8329) \| Instruction execution (complete|abnormal end) '
                    r'\| Turns ON when an instruction is finished (normally|abnormally)\.')
        if flag and (flag[1].upper(), flag[2].lower(), flag[3].lower()) in {
                ('SM8029', 'complete', 'normally'), ('SM8329', 'abnormal end', 'abnormally')}:
            return reviewed([{'kind': 'instruction_completion_flag', 'completion': flag[3].lower(),
                              'device': flag[1].upper(), 'state': True}])
        if full(r'SM8328 \| Instruction non-execution \| Turns ON when the RBFM instruction or WBFM instruction '
                r'in another step is executed for the same module number\.'):
            return reviewed([{'kind': 'instruction_non_execution_flag', 'device': 'SM8328', 'state': True,
                              'when': 'RBFM_or_WBFM_at_another_step_executes_for_same_module_number'}])
    if page == 951:
        if full(r'The RAM memory is called buffer memory\.'):
            return reviewed([{'kind': 'module_memory_term', 'memory': 'RAM', 'term': 'buffer_memory'}],
                            dimension='resources.module_memory')
        if full(r'Buffer memory number The intelligent function module incorporates a RAM memory\.'):
            return reviewed([{'kind': 'module_memory', 'module_kind': 'intelligent_function_module', 'memory': 'RAM'}],
                            dimension='resources.module_memory')
        if full(r'The module number is assigned as No\.1 \[GLYPH-F0AE\] No\.2 \[GLYPH-F0AE\] No\.3… '
                r'starting from the equipment nearest the CPU module\. \| 8'):
            return reviewed([{'kind': 'module_number_assignment', 'first_number': 1,
                              'order': 'increasing_distance_from_CPU'}], dimension='resources.module_addressing')
        if full(r'Buffer memory numbers range from #0 to #32767 and their contents vary depending on the function '
                r'of the extension equipment\. \(Setting range: K0 to K32767\) For the contents of buffer memory areas, '
                r'refer to manuals of intelligent function modules\.'):
            return reviewed([{'kind': 'module_buffer_address_domain', 'minimum': 0, 'maximum': 32767,
                              'contents_depend_on': 'specific_intelligent_function_module',
                              'content_schema_source': 'specific_module_manual'}], dimension='parameters.documented_domain')
    if page == 955:
        if full(r'The instruction is enabled when "When trigger instruction executed" is selected in the "Trigger condition"\.'):
            return reviewed([{'kind': 'logging_trigger_configuration', 'required_trigger_condition': 'when_trigger_instruction_executed'}])
        if full(r'A logging setting number in which an item other than "When trigger instruction executed" is selected '
                r'in the "Trigger condition" is specified\.'):
            layout = next((text for number, text in section.get('page_layouts', ()) if number == 955), '')
            local_list = re.search(r'Even if the LOGTRG instruction is executed, no processing is performed in the '
                                   r'following cases\.(.+?)Operation error', layout, re.S)
            if re.search(r'Even if the LOGTRG instruction is executed, no processing is performed', source) or (
                    local_list and clause in ' '.join(local_list[1].split())):
                return reviewed([{'kind': 'conditional_no_operation', 'setting_number': _operand('s'),
                                  'when': 'specified_logging_setting_uses_other_trigger_condition'}],
                                antecedent_basis='explicit_LOGTRG_no_processing_list_in_own_source_page')
        if full(r'Even if the LOGTRG instruction is executed, no processing is performed in the following cases\.'):
            return reviewed([{'kind': 'source_navigation', 'target': 'following_LOGTRG_no_processing_cases'}],
                            dimension='resources.section_structure', content_role='source_navigation')
    if page == 957:
        if full(r'Depending on the monitor condition of the real-time monitor function, '
                r'the data collection timing is when the instruction is executed\.'):
            return reviewed([{'kind': 'real_time_monitor_collection', 'timing': 'instruction_execution',
                              'qualification': 'configured_monitor_condition'}])
        if full(r'When the command input turns on as shown below, the data can be monitored at any timing\.'):
            return reviewed([{'kind': 'real_time_monitor_trigger', 'event': 'command_input_ON',
                              'timing': 'program_selected_instruction_execution',
                              'qualification': 'configured_monitor_condition'}])
    if page == 959:
        if full(r'In a step ladder program, a step relay S is regarded as one control process, and a sequence of input '
                r'conditions and output controls are programmed in a state relay\.'):
            return reviewed([{'kind': 'STL_process_model', 'one_step_relay_represents': 'one_control_process',
                              'state_contains': ['input_condition_sequence', 'output_controls']}], languages=['ladder'])
        if full(r'Because the preceding process is stopped when the program execution proceeds to the next process, '
                r'a machine can be controlled using simple sequences for each process\.'):
            return reviewed([{'kind': 'STL_process_transfer', 'event': 'execution_proceeds_to_next_process',
                              'previous_process': 'stopped', 'program_organization': 'one_sequence_per_process'}], languages=['ladder'])
        if full(r'The step relay number specified by STL instruction is assigned to the State\.'):
            return reviewed([{'kind': 'STL_state_identity', 'identity': 'step_relay_number_specified_by_STL'}], languages=['ladder'])
        if full(r'The start and completion of the State use SET instruction, OUT instruction, RST instruction, and ZRST instruction\.'):
            return reviewed([{'kind': 'STL_state_control_forms', 'forms': ['SET', 'OUT', 'RST', 'ZRST'],
                              'operations': ['state_start', 'state_completion']}], languages=['ladder'])
    if page == 961:
        if full(r'Device \| Name \| Description SM8040 \| STL transfer disable \| When SM8040 is set to ON, '
                r'transfer of the ON status is disabled among all state relays\.'):
            return reviewed([{'kind': 'STL_transfer_disable', 'when_device': 'SM8040', 'state': True,
                              'effect': 'disable_ON_status_transfer_among_all_state_relays'}], languages=['ladder'])
        if full(r'SM8046 \| STL state ON \| When the step relay turns on, SM8046 turns on automatically\.'):
            return reviewed([{'kind': 'STL_active_state_flag', 'event': 'step_relay_ON', 'set_device': 'SM8046'}], languages=['ladder'])
        if full(r'SM8047 \| Enable STL monitoring When SM8047 is turned on, it stores in SD8040 to SD8047 sequentially '
                r'from the young number of the step relay which operates in the step relay\.'):
            return reviewed([{'kind': 'STL_monitor_enable', 'when_device': 'SM8047', 'state': True,
                              'storage': ['SD8040', 'SD8047'], 'contents': 'ON_step_relay_numbers',
                              'order': 'ascending_step_relay_number'}], languages=['ladder'])
        if full(r'SD8040 to \| ON step relay numbers The turned-on step relay number is stored in SD8040 to SD8047 '
                r'\(Max\. 8 points\) sequentially from the SD8047 \| smaller number\.'):
            return reviewed([{'kind': 'STL_monitor_layout', 'storage': ['SD8040', 'SD8047'],
                              'maximum_entries': 8, 'contents': 'ON_step_relay_numbers', 'order': 'ascending_step_relay_number'}],
                            languages=['ladder'])
    if page == 962 and full(r'When the target value \(s1\), measured value \(s2\), and parameters \(s3\) to \(s3\)\+6 '
                             r'are set and a program is executed, the operation result \(MV\) is stored to the output value '
                             r'\(d\) at every sampling time \(s3\)\.'):
        return reviewed([{'kind': 'PID_sampling_update', 'target': _operand('s1'), 'measurement': _operand('s2'),
                          'parameter_start': _operand('s3'), 'parameter_last_offset': 6,
                          'sampling_time': _operand('s3'), 'destination': _operand('d'), 'result_role': 'MV'}])
    if page == 966:
        if full(r'If the block No\. is not specified, the target block will be the block 0\.'):
            return reviewed([{'kind': 'SFC_default_block', 'when': 'block_number_omitted', 'block_number': 0}])
        if full(r'If the block No\. or the step No\. specified is out of range, both of the normally open contact '
                r'and normally closed contact turn off\.'):
            return reviewed([{'kind': 'SFC_status_out_of_range', 'when_any_invalid': ['block_number', 'step_number'],
                              'NO_contact': False, 'NC_contact': False}])
    if page == 968 and full(r'If the block No\. specified is out of range, both of the normally open contact '
                             r'and normally closed contact turn off\.'):
        return reviewed([{'kind': 'SFC_status_out_of_range', 'when_invalid': 'block_number',
                          'NO_contact': False, 'NC_contact': False}])
    if page == 970:
        if full(r'When a block is not specified, the status \(active or inactive\) of the following block is read\.'):
            layout = next((text for number, text in section.get('page_layouts', ()) if number == 970), '')
            if re.search(r'Sequence program: Block 0', layout) and re.search(
                    r'SFC program \(within the action\): Block where the instruction is executed \(current block\)', layout):
                return reviewed([{'kind': 'SFC_step_read_default_block', 'when': 'block_argument_omitted',
                                  'cases': [{'program_kind': 'sequence', 'block_number': 0},
                                            {'program_kind': 'SFC_action', 'block': 'current_execution_block'}]}],
                                antecedent_basis='explicit_default_block_list_in_own_page')
        if full(r'The read data are stored in the device specified by \(d\)\.'):
            return reviewed([{'kind': 'SFC_step_status_read', 'destination': _operand('d')}])
        if full(r'When the step is active, 1 is stored\.') and 'The read data are stored in the device specified by (d)' in source:
            return reviewed([{'kind': 'SFC_step_status_encoding', 'destination': _operand('d'),
                              'when_step': 'active', 'bit_value': 1}])
        if full(r'When the step is inactive, 0 is stored\. \[Example: when K4 is specified to the nibble specification\] '
                r'\[Example: when K3 is specified to the nibble specification\]') and (
                'The read data are stored in the device specified by (d)' in source):
            return reviewed([{'kind': 'SFC_step_status_encoding', 'destination': _operand('d'),
                              'when_step': 'inactive', 'bit_value': 0},
                             {'kind': 'source_navigation', 'target': 'following_nibble_read_examples',
                              'example_nibble_counts': [4, 3]}])
        if full(r'When there is a missing step No\., 0 is stored in the corresponding bit\.'):
            return reviewed([{'kind': 'SFC_step_status_encoding', 'destination': _operand('d'),
                              'when_step': 'missing_number', 'corresponding_bit_value': 0}])

    # The following source fragments are cells/labels of complete physical
    # charts.  Their whole chart is reviewed; no isolated "When ..." cell is
    # promoted to an unconditional instruction predicate.
    if page in {788, 789, 790, 794, 795}:
        assignment = _HSC_input_assignment(page, clause)
        if assignment:
            return reviewed(assignment[0], status=assignment[1], dimension='parameters.pulse_density_input_assignment',
                            source_binding='complete_input_role_grid_shading_and_printed_error_list')
    if page in {872, 873, 875, 876, 878, 879}:
        sorting = _sorting_chart(page, clause)
        if sorting:
            return reviewed(sorting[0], status=sorting[1], dimension='resources.documented_sorting_example',
                            content_role='worked_example', knowledge_kind='manual_example',
                            source_binding='complete_input_output_tables_and_sort_direction')
    if page in {448, 449}:
        trace = _priority_trace(page, clause)
        if trace:
            return reviewed([trace], dimension='resources.documented_execution_trace',
                            content_role='worked_example', knowledge_kind='manual_example',
                            languages=['ladder'], conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}],
                            forms=['DI'], source_binding='complete_priority_diagram_and_numbered_legends')
    if page in {790, 796}:
        vocabulary = set(('function r w update updating timing clear monitor in operation when the spd dspd instruction is '
                          'executed power on hioen dhioen reset high speed counter pulse density each time measurement unit '
                          'elapses').split())
        if _chart_tokens_only(clause, vocabulary) and any(word in clause for word in
                ['Function', 'SPD instruction', 'HIOEN instruction', 'Power-on', 'Reset', 'measurement unit']):
            table = _pulse_density_status_table(section, page)
            if table:
                return reviewed([table], dimension='memory.pulse_density_status',
                                source_binding='full_function_row_update_and_clear_columns',
                                source_table_columns=['function', 'access', 'update_timing', 'clear'])
    if page in {814, 815, 821, 822}:
        vocabulary = set(('pulse output destination channel number of pulses r w initial value timing reflection on operation '
                          'clearing to when the dhcmov pwm dpwm instruction is executed end processing stop pause run period '
                          'time current monitoring power reset an sd device updated specified are this off').split())
        if _chart_tokens_only(clause, vocabulary) and any(word in clause for word in
                ['Pulse output', 'instruction', 'END processing', 'RUN[', 'STOP/', 'Reset', 'Power-on']):
            table = _PWM_status_tables(section, page)
            if table:
                return reviewed([table], dimension='memory.PWM_status',
                                source_binding='actual_field_tables_with_merged_event_columns_and_footnotes',
                                source_table_columns=['field', 'channel', 'access', 'initial_value', 'reflection_or_ON', 'clear_or_OFF'])

    if page == 452 and full(r'The following shows the assignment of the interrupt pointer numbers to each bit\.'):
        layout = _page_layout(section, page)
        if all(marker in layout for marker in ['b15', 'I177', '(s)+11', '(s)+2']):
            assigned = set(range(24)) | set(range(28, 32)) | set(range(50, 178))
            return reviewed([{'kind': 'interrupt_pointer_bit_layout', 'source': _operand('s'),
                              'words': [{'offset': offset,
                                         'bits': [{'bit': bit, 'pointer': 'I' + str(offset * 16 + bit)}
                                                  for bit in range(16) if offset * 16 + bit in assigned],
                                         'unassigned_bits': [bit for bit in range(16) if offset * 16 + bit not in assigned]}
                                        for offset in range(12)],
                              'unassigned_cell_meaning': 'no_interrupt_pointer_assigned',
                              'pointer_availability': 'requires_pointer_to_exist_in_target_CPU'}],
                            dimension='parameters.interrupt_pointer_layout', source_binding='complete_12_word_bit_diagram')
    if page == 447:
        vocabulary = set(('this instruction disables the execution of interrupt program pointer number with an priority '
                          'specified by or lower disabled section for enabled higher setting no can cannot be executed '
                          'because fend iret a b sequence i di k').split())
        if _chart_tokens_only(clause, vocabulary) and any(marker in clause for marker in ['This instruction disables', 'I01', 'FEND']):
            return reviewed([{'kind': 'interrupt_disable_priority', 'threshold': _operand('s'),
                              'effect': 'disable_interrupt_programs_at_numeric_priority_greater_than_or_equal_to_threshold',
                              'numeric_priority_1_is_highest': True},
                             {'kind': 'documented_interrupt_priority_example', 'threshold': 3,
                              'interrupts': [{'pointer': 'I01', 'priority': 2, 'eligible': True},
                                             {'pointer': 'I02', 'priority': 3, 'eligible': False}],
                              'program_labels': {'A': 'sequence', 'B': 'interrupt'},
                              'eligibility_does_not_assert_dispatch': True}],
                            source_binding='DI_K3_diagram_and_both_numbered_pointer_legends')
    if page == 453 and full(r'module I0 to I177 \(s\) \| Enabled or disabled state of the specified interrupt pointer '
                            r'0: Disabled \| 16-bit signed binary \| ANY16 number \| 1: Enabled EN \| Execution condition '
                            r'\| \[GLYPH-F0C9\] \| Bit \| BOOL ENO \| Execution result \| \[GLYPH-F0C9\] \| Bit \| BOOL'):
        return reviewed([{'kind': 'individual_interrupt_enable_encoding', 'selector': _operand('s'),
                          'encoding': [{'value': 0, 'meaning': 'disabled'}, {'value': 1, 'meaning': 'enabled'}],
                          'selector_type': '16_bit_signed_binary',
                          'CPU_cases': [{'models': ['FX5S'], 'pointer_domain': ['I0', 'I31']},
                                        {'models': ['FX5UJ', 'FX5U', 'FX5UC'], 'pointer_domain': ['I0', 'I177']}],
                          'execution_condition_type': 'BOOL', 'execution_result_type': 'BOOL'}],
                        dimension='parameters.interrupt_enable_selector')
    if page == 458 and full(r'Step 0 \| END \(FEND\) WDT'):
        return reviewed([{'kind': 'watchdog_reset_program_intervals',
                          'segments': [['step_0', 'WDT_instruction'], ['WDT_instruction', 'END_or_FEND']],
                          'require_each_segment': 'not_exceed_watchdog_timer_setting'}],
                        languages=['ladder'], conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}],
                        source_binding='watchdog_diagram_and_own_page_t1_t2_requirement')
    if page == 462:
        vocabulary = set(('if a pointer in another program is specified an operation error occurs forced termination '
                          'condition when the satisfied instruction not executed execution returns to for as many times '
                          'by break next k p').split())
        if _chart_tokens_only(clause, vocabulary) and (clause.startswith('If a pointer') or clause.startswith('BREAK (d)')):
            return reviewed([{'kind': 'forced_loop_termination', 'destination': _operand('P'),
                              'destination_file': 'same_program_file', 'when_termination_condition': True,
                              'remaining_loop_count': _operand('d'), 'remaining_count_includes_current_iteration': True,
                              'when_condition_false': 'continue_FOR_NEXT_loop_for_FOR_specified_count',
                              'other_program_pointer': 'operation_error'}],
                            source_binding='whole_FOR_BREAK_NEXT_flow_and_remainder_definition')
    if page in {464, 469} and full(r'A \| B \(P\)'):
        rules = [{'kind': 'subroutine_call_return_flow', 'main_program_label': 'A', 'subroutine_label': 'B',
                  'call': 'CALL', 'return': ['RET', 'SRET'], 'return_destination': 'step_after_call'}]
        if page == 464:
            rules[0]['subroutine_start'] = _operand('P')
        else:
            rules[0]['subroutine_start'] = {'source_diagram_symbol': 'P', 'meaning': 'called_subroutine_start_pointer'}
        return reviewed(rules, languages=['ladder'],
                        conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}],
                        source_binding='complete_main_subroutine_call_and_return_arrows')
    if page in {486, 488}:
        read = page == 486
        transfer = (r'These instructions read device data by the number of points specified by \(n\) from the read offset '
                    r'specified by \(s\) in the device data storage file in data memory, and store it in the device specified by \(d\)\.'
                    if read else r'These instructions retrieves the specified number of points of data specified by \(n\) from '
                    r'the device specified by \(s2\) and write it to the write offset in the device specified by \(s1\) '
                    r'in the device data storage file in data memory\.')
        offset = full(r'\((s|s1)\) indicates the offset from the start of the device data storage file and can be specified '
                      r'by word offsets \(incremented by 1 every 16 bits\)\.')
        if full(transfer) or (offset and offset[1].lower() == ('s' if read else 's1')) or (
                clause == 'Data memory' and 'Device data' in _page_layout(section, page)):
            return reviewed([{'kind': 'data_memory_file_transfer', 'direction': 'read' if read else 'write',
                              'device_data': _operand('d' if read else 's2'), 'file_offset': _operand('s' if read else 's1'),
                              'offset_unit_bits': 16, 'point_count': _operand('n'),
                              'file': 'device_data_storage_file_in_data_memory', 'point_unit': 'word'}],
                            source_binding='complete_transfer_sentence_offset_definition_and_direction_arrow')
    if page == 551 and full(r'When a folder is specified by \(s2\), a file and subfolder with the same names may exist in the copy destination\.'):
        return reviewed([{'kind': 'folder_copy_collision_case', 'folder_argument': _operand('s2'),
                          'possible_collision_objects': ['file', 'subfolder'],
                          'collision_location': 'copy_destination',
                          'overwrite_policy': 'governed_by_control_data_bit_1_in_following_cases'}])
    if page == 594 and clause.startswith('Instruction symbol in [GLYPH-F06F] Result '):
        vocabulary = set(('instruction symbol in result conductive non state b abcde abcdf abcd abcde f e c d a '
                          's b fifteen').split())
        if _chart_tokens_only(clause, vocabulary):
            operators = ['$=', '$<>', '$>', '$<=', '$<', '$>=']
            examples = [('ABCDE', 'ABCDE'), ('ABCDF', 'ABCDE'), ('12345', '12435')]
            return reviewed([{'kind': 'documented_string_comparison_examples', 'left': _operand('s1'), 'right': _operand('s2'),
                              'examples': [{'left_value': left, 'right_value': right,
                                            'conductive': dict(zip(operators, [left == right, left != right, left > right,
                                                                             left <= right, left < right, left >= right]))}
                                           for left, right in examples],
                              'storage': 'ASCII_low_byte_first_two_characters_per_word_NULL_terminated'}],
                            dimension='resources.documented_string_examples', content_role='worked_example',
                            knowledge_kind='manual_example', source_binding='all_three_string_and_operator_result_diagrams')
    if page == 737 and full(r'The start of the block data in the device specified by \(s\) is counted as 1st point when the location is counted\.'):
        return reviewed([{'kind': 'block_result_location_origin', 'source': _operand('s'),
                          'first_source_point_number': 1}], dimension='parameters.result_encoding')
    if page == 757 and full(r'When the output value to be stored in the device specified by \(d\) is a 16-bit signed binary '
                            r'value and the operation result exceeds the range of -32768 to 32767, the output value is calculated as follows\.'):
        return reviewed([{'kind': 'documented_zone_overflow_example', 'conditions': [
                            {'subject': _operand('s1'), 'value': -100}, {'subject': _operand('s3'), 'value': -32768}],
                          'mathematical_sum': -32868, 'printed_result_hex': '7F9CH', 'stored_value': 32668,
                          'destination': _operand('d'), 'decimal_addends': [-32768, -100],
                          'printed_hex_expression': '8000H-FF9CH=7F9CH'},
                         {'kind': 'source_conflict', 'conflict': 'printed_hex_operator_disagrees_with_decimal_sum',
                          'printed_hex_operator': '-', 'resolution': 'not_established'}],
                        dimension='resources.documented_overflow_example', content_role='worked_example',
                        knowledge_kind='manual_example', status='conflict',
                        source_binding='complete_signed_overflow_example_below_caption')
    if page in {767, 771} and clause.startswith('Device assignment Number of coordinate points '):
        vocabulary = set(('device assignment number of coordinate points x y point n npoint output value input '
                          'operation range error erroroperation b').split())
        if _chart_tokens_only(clause, vocabulary):
            wide = page == 771
            rules = [{'kind': 'XY_scaling_coordinate_layout', 'base': _operand('s2'), 'coordinate_count_offset': 0,
                      'element_width_bits': 32 if wide else 16, 'one_based_point_number_symbol': 'i',
                      'point_number_domain': '1_through_n',
                      'X_low_word_offset': '2*i' if wide else 'i',
                      'Y_low_word_offset': '2*n+2*i' if wide else 'n+i',
                      'high_word_relative_offset': 1 if wide else None,
                      'layout': 'all_X_coordinates_followed_by_all_Y_coordinates'},
                     {'kind': 'XY_scaling_graph', 'input': _operand('s1'), 'output': _operand('d'),
                      'between_points': 'straight_line_segments', 'valid_input_domain': 'first_X_through_last_X_inclusive',
                      'outside_domain': 'operation_error', 'extrapolation': False,
                      'printed_input_axis_operand': 'd' if wide else 's1'}]
            if wide:
                rules.append({'kind': 'source_conflict', 'conflict': 'diagram_input_axis_names_output_operand',
                              'body_input': 's1', 'printed_diagram_input': 'd', 'resolution': 'not_established'})
            return reviewed(rules, status='conflict' if wide else 'source_verified',
                            dimension='memory.XY_scaling_layout', source_binding='whole_coordinate_assignment_and_piecewise_linear_graph')
    if page == 745:
        vocabulary = set(('the following shows areas of and later used for instructions when to are d number times '
                          'batch storage reserved by system words z lz zpush save whole range st nesting one nd two type').split())
        if _chart_tokens_only(clause, vocabulary) and (clause.startswith('The following shows the areas') or clause.startswith('(d)')):
            frames = [{'save_selector': 0, 'nesting_index': 1, 'word_count': 25,
                       'registers': [{'register': 'Z' + str(i), 'low_word_offset': 3 + i} for i in range(14)] +
                                    [{'register': 'LZ' + str(i), 'low_word_offset': 17 + i * 2,
                                      'high_word_offset': 18 + i * 2} for i in range(5)],
                       'save_type_offset': 27, 'save_type_value': 0},
                      {'save_selector': 2, 'nesting_index': 2, 'word_count': 11,
                       'registers': [{'register': 'LZ' + str(i), 'low_word_offset': 28 + i * 2,
                                      'high_word_offset': 29 + i * 2} for i in range(5)],
                       'save_type_offset': 38, 'save_type_value': 2}]
            return reviewed([{'kind': 'documented_index_save_layout', 'destination': _operand('d'),
                              'register_configuration': {'index': ['Z0', 'Z13'], 'long_index': ['LZ0', 'LZ4']},
                              'nesting_count_offset': 0, 'system_reserved_offsets': [1, 2], 'frames': frames,
                              'subsequent_frames': 'append_after_previous_frame', 'generalization': 'printed_register_configuration_only'}],
                            dimension='memory.documented_index_save_layout', content_role='worked_example', knowledge_kind='manual_example',
                            source_binding='complete_two_nesting_stack_diagram')
    if page in {787, 793}:
        width = 16 if page == 787 else 32
        if full(r'Counting start \(s2\)(?:\+1, \(s2\))? ms \| \(s2\)(?:\+1, \(s2\))? ms') or full(
                r'\(1\) \| \(1\) \(1\): The elapsed time is judged using the 1ms interrupt and the counted result '
                r'is stored in \(d\)(?:\+1 and \(d\))?\.'):
            return reviewed([{'kind': 'pulse_density_measurement_intervals', 'duration': _operand('s2'),
                              'duration_width_bits': width, 'duration_unit_ms': 1,
                              'elapsed_time_clock': 'one_millisecond_interrupt', 'destination': _operand('d'),
                              'result_width_bits': width, 'update_event': 'end_of_each_measurement_interval',
                              'next_interval': 'starts_after_previous_interval'}],
                            source_binding='complete_repeating_counting_and_update_timing_diagram')
    if page == 785 and full(r'When SM8026 is off \| When SM8026 is on'):
        return reviewed([{'kind': 'ramp_mode_timing_diagram', 'selector': 'SM8026', 'destination': _operand('d'),
                          'start': _operand('s1'), 'end': _operand('s2'),
                          'cases': [{'selector_state': False, 'command_ON': 'repeat_start_to_end_ramp',
                                     'after_reaching_end': 'restart_from_start', 'SM8029': 'one_operation_cycle_ON_per_completion'},
                                    {'selector_state': True, 'command_ON': 'ramp_then_hold_end_value',
                                     'after_reaching_end': 'hold_end_while_command_ON', 'SM8029': 'held_ON_until_command_OFF'}],
                          'command_OFF_during_ramp': 'hold_current_destination_and_pause',
                          'next_command_ON': 'restart_from_start'}], source_binding='both_SM8026_timing_plots_and_command_waveforms')
    if page == 781:
        if full(r'The table rotation is controlled by conditions of "n2", \(s\), and \(d\) so that a product can be '
                r'efficiently put on or taken off the rotary table divided into "n1" \(=10\) sections as shown in the figure below\.') or full(
                r'When the following conditions are specified, forward/backward rotation and high-speed/low-speed/stop are output to \(d\)\+3 to \(d\)\+7\.'):
            return reviewed([{'kind': 'rotary_table_control', 'division_count': _operand('n1'),
                              'low_speed_section_count': _operand('n2'), 'calling_registers': _operand('s'),
                              'inputs': [{'role': role, 'device': _operand('d', offset)}
                                         for offset, role in enumerate(['A_phase', 'B_phase', 'zero_point_detection'])],
                              'outputs': [{'role': role, 'device': _operand('d', offset + 3)}
                                          for offset, role in enumerate(['forward_high_speed', 'forward_low_speed', 'stop',
                                                                         'backward_low_speed', 'backward_high_speed'])],
                              'printed_example_divisions': 10, 'example_does_not_limit_division_count': True}],
                            source_binding='rotation_diagram_and_explicit_d0_through_d7_setting_table')
        if full(r'Provide a 2-phase switch \(X0 and X1\) for detecting the rotation direction \(forward or backward\) '
                r'of the table and the switch X2 which turns ON when the product No\. 0 reaches the station No\. 0\.') or full(
                r'X0 to X2 are replaced with internal contacts of \(d\) to \(d\)\+2\.') or full(
                r'Any head device number can be specified by X or \(d\)\.'):
            return reviewed([{'kind': 'documented_rotary_input_example',
                              'connections': [{'physical': 'X0', 'internal': _operand('d'), 'role': 'A_phase'},
                                              {'physical': 'X1', 'internal': _operand('d', 1), 'role': 'B_phase'},
                                              {'physical': 'X2', 'internal': _operand('d', 2), 'role': 'zero_point_detection'}],
                              'zero_detection_event': 'product_0_at_station_0',
                              'input_and_internal_head_addresses': 'arbitrary_valid_heads'}],
                            dimension='resources.documented_rotary_example', content_role='worked_example', knowledge_kind='manual_example')
    if page == 833:
        vocabulary = set(('do not program the following state relays as general device number descriptions on off condition '
                          'to occupied for initial turns when a step relay s is are used individual operation zero return '
                          'and selected automatic shown above no can be arbitrarily point').split())
        if _chart_tokens_only(clause, vocabulary) and any(marker in clause for marker in ['state relays', 'S0', 'S3', 'S10', 'OFF condition']):
            return reviewed([{'kind': 'IST_reserved_state_relays', 'when_IST_command_input': True,
                              'general_state_relay_use': 'prohibited_for_reserved_ranges',
                              'ranges': [{'first': 'S0', 'last': 'S9', 'role': 'initial_state',
                                          'fixed_initial_states': {'S0': 'individual_operation', 'S1': 'zero_return', 'S2': 'automatic_operation'},
                                          'other_initial_state_choices': ['S3', 'S9']},
                                         {'first': 'S10', 'last': 'S19', 'role': 'zero_return_state'}],
                              'ON_condition': 'the_relay_is_selected_for_its_reserved_state_role',
                              'OFF_condition': 'no_relay_is_selected_for_that_role',
                              'does_not_set_all_relays_in_a_range_ON': True,
                              'when_IST_command_input_OFF': 'no_automatic_device_switching'}],
                            dimension='memory.IST_reserved_states', source_binding='whole_reserved_state_table_and_IST_ON_premise')
    if page == 847:
        first = full(r'When the command contact turns on, the output M0 turns on\.')
        step = full(r'When the current value of C0 reaches the comparison value (D300|D301), the output (M0|M1) is reset, '
                    r'"1" is added to the count value of the process counter C1, and the current value of the counter C0 is reset\.')
        if first or (step and (step[1], step[2]) in {('D300', 'M0'), ('D301', 'M1')}):
            return reviewed([{'kind': 'documented_increment_drum_example',
                              'instruction': {'form': 'INCD', 'operands': ['D300', 'C0', 'M0', 'K4']},
                              'comparison_table': [{'device': 'D300', 'value': 20}, {'device': 'D301', 'value': 30},
                                                   {'device': 'D302', 'value': 10}, {'device': 'D303', 'value': 40}],
                              'source_clock': 'one_second_SM412', 'counter': 'C0', 'process_counter': 'C1',
                              'command_ON': {'set': 'M0'},
                              'completion_steps': [{'when_counter_equals': 'D' + str(300 + i), 'reset_output': 'M' + str(i),
                                                    'increment_C1': 1, 'reset_C0': True, 'next_output': 'M' + str((i + 1) % 4)}
                                                   for i in range(4)],
                              'last_step_completion': {'SM8029': 'one_operation_cycle_ON', 'next_step': 'first'}}],
                            dimension='resources.documented_drum_example', content_role='worked_example', knowledge_kind='manual_example',
                            languages=['ladder'], conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}],
                            source_binding='complete_INCD_program_and_four_output_timing_diagram')
    if page in {859, 861} and (clause.startswith('(s)') or clause.startswith('(s)+1, (s)')):
        vocabulary = {'b', 'on', 'off'}
        if _chart_tokens_only(clause, vocabulary):
            return reviewed([{'kind': 'documented_bit_judgment_example', 'source': _operand('s'), 'destination': _operand('d'),
                              'width_bits': 16 if page == 859 else 32,
                              'selected_bits': [{'bit': 5, 'value': False}, {'bit': 8 if page == 859 else 29, 'value': True}],
                              **({'complete_source_bits_15_through_0': [1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1]}
                                 if page == 859 else {'omitted_high_bits_in_ellipsis': 'not_established'})}],
                            dimension='resources.documented_bit_example', content_role='worked_example', knowledge_kind='manual_example')
    if page in {916, 919}:
        kind = 'date' if page == 916 else 'time'
        low = full(r'When 0 is set to the 0 to 2 bits, the (?:date data|time data \(hour, minute, and second\)) are not compared\.')
        all_fields = full(r'When 1 is set, the entire (?:date data \(year, month, and day\)|time data \(hour, minute, and second\)) are compared\.')
        reserved_and_clock = full(r'If a value other than 0 is set, the operation result will be non- continuity\. '
                                  r'\(5\) When 1 is set to the 15 bit, the data in the device specified by \(s1\) is compared '
                                  r'with the current (?:date|time) in accordance with the conditions set in the 0 to 2 bits\.')
        if low or all_fields or reserved_and_clock:
            layout = _page_layout(section, page)
            if all(marker in layout for marker in ['b15', 'b14', 'b3', 'b2', 'b1', 'b0', '0001H', '0002H', '0004H', '0007H']):
                fields = ['day', 'month', 'year'] if page == 916 else ['second', 'minute', 'hour']
                return reviewed([{'kind': 'clock_comparison_bit_layout', 'selector': _operand('s3'), 'data_kind': kind,
                                  'field_enable_bits': [{'bit': i, 'field': field, 'one_means': 'compare_this_field'}
                                                        for i, field in enumerate(fields)],
                                  'all_fields_selected_mask': 7, 'no_field_selected_mask': 0,
                                  'no_field_selected_result': 'non_conductive',
                                  'reserved_bit_range': [3, 14], 'reserved_required_value': 0,
                                  'reserved_nonzero_result': 'non_conductive',
                                  'bit_15_sources': [{'value': 0, 'left': _operand('s1'), 'right': _operand('s2')},
                                                     {'value': 1, 'left': _operand('s1'), 'right': 'current_' + kind,
                                                      'ignored': _operand('s2')}]}],
                                dimension='parameters.clock_comparison_bits',
                                source_binding='full_bit_diagram_numbered_legends_and_0001_0002_0004_0007_rows')
    if page in {941, 942, 947, 948}:
        if full(r'\(s2\) \(s1\) CPU module Buffer memory \(n\) points \(n\) words \(s2\) \(s1\)'):
            return reviewed([{'kind': 'module_buffer_fill_diagram', 'source': _operand('s2'),
                              'module_buffer_start': _operand('s1'), 'count': _operand('n'),
                              'words_per_element': 1, 'direction': 'CPU_to_module_buffer',
                              'constant_source': {'example_value': 5, 'effect': 'repeat_same_word_in_all_n_words'}}],
                            source_binding='word_transfer_and_constant_5_fill_diagrams')
        if full(r'CPU module \| Buffer memory \(s2\) \| \(s1\) 70000 \| 70000'):
            return reviewed([{'kind': 'documented_module_double_word_fill_example', 'source': _operand('s2'),
                              'module_buffer_start': _operand('s1'), 'constant': 70000,
                              'element_count': _operand('n'), 'words_per_element': 2,
                              'direction': 'CPU_to_module_buffer', 'total_words': {'multiply': [_operand('n'), 2]}}],
                            dimension='resources.documented_module_fill_example', content_role='worked_example', knowledge_kind='manual_example')
        if page == 947 and full(r'When a constant is specified in \(s2\), \(n\) words of the same data '
                                r'\(the value specified by \(s2\)\) is written starting from the specified buffer memory address\.'):
            return reviewed([{'kind': 'module_buffer_fill', 'when_constant': _operand('s2'), 'data': _operand('s2'),
                              'element_count': _operand('n'), 'words_per_element': 1,
                              'destination': 'specified_module_buffer_memory_start'}])
    if page == 951 and clause.startswith('Module number of FX3 intelligent function module '):
        vocabulary = set(('module number of fx intelligent function use the to specify for which equipment rbfm wbfm '
                          'instruction is used setting range h k no bus cpu i o conversion a automatically assigned '
                          'each connected').split())
        if _chart_tokens_only(clause, vocabulary):
            return reviewed([{'kind': 'FX3_extension_module_numbering', 'minimum': 1, 'maximum': 16,
                              'hexadecimal_range': ['1H', '10H'], 'assignment': 'automatic_from_nearest_CPU_outwards',
                              'counted_modules': 'FX3_intelligent_function_modules',
                              'uncounted_in_diagram': ['CPU_module', 'I_O_module', 'bus_conversion_module'],
                              'source_diagram_numbered_examples': [1, 2, 3], 'applies_to_forms': ['RBFM', 'WBFM']}],
                            dimension='resources.module_addressing', source_binding='whole_module_number_diagram_and_literal_setting_range')
    if page == 970:
        if full(r'These instructions batch-read \(in units of 16-bit binary data\) the status \(active or inactive\) '
                r'of steps in the specified block\.'):
            return reviewed([{'kind': 'SFC_step_status_batch_read', 'element_width_bits': 16,
                              'contents': 'active_or_inactive_step_status', 'block': 'specified_block'}])
        if full(r'When a block is not specified, the status \(active or inactive\) of the following block is read\. '
                r'- Sequence program: Block 0 - SFC program \(within the action\): Block where the instruction is executed \(current block\)'):
            return reviewed([{'kind': 'SFC_step_read_default_block', 'when': 'block_argument_omitted',
                              'cases': [{'program_kind': 'sequence', 'block_number': 0},
                                        {'program_kind': 'SFC_action', 'block': 'current_execution_block'}]}])
        if full(r'When the step is inactive, 0 is stored\.') and 'The read data are stored in the device specified by (d)' in source:
            return reviewed([{'kind': 'SFC_step_status_encoding', 'destination': _operand('d'),
                              'when_step': 'inactive', 'bit_value': 0}])

    if page in {623, 624, 626}:
        vocabulary = set(('if the specified number of digits in decimal part is greater than bit binary data s '
                          'automatically added and regarded as b when ascii code for sign bin high order low bits '
                          'value specifying all th digit st nd rd sp stored at end character string point format').split())
        if _chart_tokens_only(clause, vocabulary):
            if page == 623 and (clause.startswith('If the specified number of digits') or 'When -654.321 is specified in (s2)' in clause):
                return reviewed([{'kind': 'binary32_decimal_string_layout', 'source': _operand('s2'),
                                  'total_digits': _operand('s1'), 'fractional_digits': _operand('s1', 1),
                                  'destination': _operand('d'), 'first_byte': 'sign', 'byte_order': 'low_byte_first',
                                  'characters_per_word': 2, 'terminator': 0,
                                  'when_fractional_digits_exceed_source_digits': 'insert_zeroes_after_decimal_point_before_source_digits'},
                                 {'kind': 'documented_binary32_string_examples',
                                  'examples': [{'binary_value': -654321, 'printed_caption_decimal_value': -654.321,
                                                'total_digits': 8, 'fractional_digits': 3, 'words': _ASCII_words('-654.321')},
                                               {'binary_value': 12345678, 'total_digits': 10, 'fractional_digits': 3,
                                                'words': _ASCII_words(' 12345.678')},
                                               {'binary_value': 54321, 'total_digits': 13, 'fractional_digits': 10,
                                                'words': _ASCII_words(' 0.0000054321')}]}],
                                dimension='memory.numeric_string_layout', source_binding='all_three_decimal_string_examples_and_byte_assignment_diagram')
            if page == 624 and clause.startswith('(s1) | 13 '):
                return reviewed([{'kind': 'documented_binary32_string_padding_example', 'source': _operand('s2'),
                                  'destination': _operand('d'), 'binary_value': -543210,
                                  'settings': [{'subject': _operand('s1'), 'value': 13},
                                               {'subject': _operand('s1', 1), 'value': 2}],
                                  'padding_location': 'between_sign_and_numeric_characters', 'padding_byte': 32,
                                  'padding_count': 5, 'words': _ASCII_words('-     5432.10')}],
                                dimension='resources.documented_numeric_string_example', content_role='worked_example',
                                knowledge_kind='manual_example')
            if page == 626 and (clause.startswith('(s1)+1 (s1)') or clause.startswith('ASCII code in ') or clause.startswith('(s2)+1 (s2)+2 (s2)')):
                return reviewed([{'kind': 'real_decimal_string_layout', 'source': _operand('s1'),
                                  'format_selector': {'subject': _operand('s2'), 'value': 0},
                                  'total_digits': _operand('s2', 1), 'fractional_digits': _operand('s2', 2),
                                  'destination': _operand('d'), 'characters_per_word': 2,
                                  'byte_order': 'low_byte_first', 'first_byte': 'sign',
                                  'integer_digits': 'follow_sign_in_most_significant_to_least_significant_order',
                                  'decimal_point_byte': 46, 'fractional_digits_location': 'after_decimal_point',
                                  'terminator': 0, 'illustrated_8_character_terminal_word': 0}],
                                dimension='memory.real_string_layout', source_binding='complete_decimal_display_specification_and_byte_assignment_diagram')
    if page == 843 and (clause.startswith('Each rising point/falling point can be changed by overwriting the data in (s1) to (s1)+2(n)-1.') or full(
            r'The current value \(s2\) of the counter is compared with the data table with "n" lines starting from \(s1\) '
            r'\(which occupies "n" lines \[GLYPH-F0B4\] 2 devices\), and consecutive "n" outputs starting from \(d\) '
            r'are controlled to on or off during one rotation\.')):
        vocabulary = set(('each rising point falling can be changed by overwriting the data in to n the current value of '
                          'counter is compared with table lines starting from which occupies devices and consecutive '
                          'outputs are controlled on or off during one rotation').split())
        if _chart_tokens_only(clause, vocabulary):
            return reviewed([{'kind': 'absolute_drum_edge_table', 'source': _operand('s1'), 'destination': _operand('d'),
                              'counter': _operand('s2'), 'output_count': _operand('n'),
                              'table_word_count': {'multiply': [_operand('n'), 2]},
                              'one_based_row': 'i', 'rising_word_offset': '2*(i-1)', 'falling_word_offset': '2*i-1',
                              'output_bit_offset': 'i-1', 'rising_edge_effect': 'output_ON', 'falling_edge_effect': 'output_OFF',
                              'edge_values_change_method': 'overwrite_source_table'},
                             {'kind': 'documented_absolute_drum_example', 'counter': _operand('s2'),
                              'counter_rotation': {'minimum': 0, 'maximum': 360, 'unit': 'degrees', 'degrees_per_pulse': 1},
                              'when_command_input': True,
                              'outputs': [{'bit_offset': 0, 'ON_edge': 40, 'OFF_edge': 140, 'wraps_zero': False},
                                          {'bit_offset': 1, 'ON_edge': 100, 'OFF_edge': 200, 'wraps_zero': False},
                                          {'bit_offset': 2, 'ON_edge': 160, 'OFF_edge': 60, 'wraps_zero': True},
                                          {'bit_offset': 3, 'ON_edge': 240, 'OFF_edge': 280, 'wraps_zero': False}]}],
                            dimension='memory.absolute_drum_edge_layout', source_binding='edge_pair_table_and_all_four_output_waveforms')
    if page == 650 and clause.startswith('When (s2)+1 (the number of characters to be extracted) is "- 1",'):
        vocabulary = set(('when the number of characters to be extracted is entire character string stored in and '
                          'later th position ascii code for b a c d e f g h i j k abcdefghijk efghi').split())
        if _chart_tokens_only(clause, vocabulary):
            return reviewed([{'kind': 'documented_substring_examples', 'source': _operand('s1'),
                              'settings_start': _operand('s2'), 'destination': _operand('d'),
                              'input_words': _ASCII_words('ABCDEFGHIJK'),
                              'examples': [{'start_position_one_based': 5, 'count': 5, 'output_words': _ASCII_words('EFGHI')},
                                           {'start_position_one_based': 5, 'count': -1, 'output_words': _ASCII_words('EFGHIJK')}]},
                             {'kind': 'source_conflict', 'conflict': 'count_minus_one_prose_says_entire_source_but_diagram_starts_at_selected_position',
                              'literal_prose_scope': 'entire_string_starting_at_s1',
                              'diagram_scope': 'suffix_from_selected_character_position', 'resolution': 'not_established'}],
                            dimension='resources.documented_substring_examples', content_role='worked_example',
                            knowledge_kind='manual_example', status='conflict', source_binding='whole_minus_one_prose_and_both_substring_diagrams')
    if page == 653 and clause.startswith('When the number of characters specified by (s2)+1 exceeds the last character '):
        vocabulary = set(('when the number of characters specified by exceeds last character string data is stored '
                          'up to b a c d e f g h i abcdefghi ab i').split())
        if _chart_tokens_only(clause, vocabulary):
            return reviewed([{'kind': 'string_replacement_length_limit', 'destination': _operand('d'),
                              'requested_count': _operand('s2', 1),
                              'when_requested_span_exceeds_original_end': 'stop_at_original_last_character'},
                             {'kind': 'documented_string_replacement_examples', 'source': _operand('s1'),
                              'settings_start': _operand('s2'), 'destination': _operand('d'),
                              'input_source_words': _ASCII_words('012345678'), 'input_destination_words': _ASCII_words('ABCDEFGHI'),
                              'examples': [{'start_position_one_based': 3, 'count': 6, 'output_words': _ASCII_words('AB012345I')},
                                           {'start_position_one_based': 5, 'count': 8, 'output_words': _ASCII_words('ABCD01234'),
                                            'not_written_ASCII_values': [53, 54, 55]}]}],
                            source_binding='length_limit_sentence_and_both_before_after_replacement_diagrams')
    if page == 810:
        speed = full(r'Set the value from 0 to 2147483647 \(in user unit\) to the command speed \(s\), so that the command '
                     r'speed is 200 kpps or less when the command speed is converted to frequency\.')
        position = full(r'Set the value from 0 to 2147483647 \(in user unit\) to the positioning address \(n\), so that the '
                        r'positioning address is within the range from 0 to 2147483647 when the positioning address is converted to number of pulses\.')
        if speed or position:
            return reviewed([{'kind': 'positioning_user_unit_domain', 'subject': _operand('s' if speed else 'n'),
                              'user_unit_minimum': 0, 'user_unit_maximum': 2147483647,
                              'conversion': 'parameter_configured_user_unit_to_frequency' if speed else 'parameter_configured_user_unit_to_pulse_count',
                              'converted_maximum': 200000 if speed else 2147483647,
                              'converted_unit': 'pulses_per_second' if speed else 'pulses',
                              **({} if speed else {'converted_minimum': 0})}], dimension='parameters.positioning_unit_domain')
    if page in {960, 961}:
        vocabulary = set(('the actual step ladder program is as follows this range divided by stl set instruction '
                          'called state process of start moving forward backward sm when turns on and automatically '
                          'reset programmed in remains even if activated').split())
        if _chart_tokens_only(clause, vocabulary) and any(marker in clause for marker in ['The actual step ladder', 'SM402', 'STL S', 'SET S32']):
            if page == 960:
                rule = {'kind': 'documented_STL_program_example', 'startup': {'when_device': 'SM402', 'SET': 'S0'},
                        'states': [{'relay': 'S0', 'transition': {'when_device': 'X0', 'SET': 'S20'}},
                                   {'relay': 'S20', 'output': {'device': 'Y21', 'contact': {'device': 'Y23', 'state': False},
                                                            'role': 'forward'},
                                    'transition': {'when_device': 'X1', 'SET': 'S21'}},
                                   {'relay': 'S21', 'output': {'device': 'Y23', 'contact': {'device': 'Y21', 'state': False},
                                                            'role': 'backward'}}],
                        'state_boundary': 'one_STL_state_relay_through_next_STL_or_RETSTL', 'end': ['RETSTL', 'END']}
            else:
                rule = {'kind': 'documented_STL_transfer_example', 'startup': {'when_device': 'SM402', 'SET': 'S31'},
                        'states': [{'relay': 'S31', 'outputs': [{'drive_contact': 'SM400', 'OUT': 'Y30'},
                                                             {'drive_contact': 'SM400', 'SET': 'Y31'}],
                                    'transition': {'when_device': 'X1', 'SET': 'S32'}},
                                   {'relay': 'S32', 'outputs': [{'drive_contact': 'SM400', 'OUT': 'Y30'},
                                                             {'drive_contact': 'SM400', 'OUT': 'Y32'}],
                                    'transition': {'when_device': 'X2', 'OUT': 'S33'}}],
                        'transition_effect': {'source_state': 'reset', 'destination_state': 'ON', 'SET_Y31': 'remains_ON'},
                        'output_reuse': 'Y30_in_both_states', 'output_drive_contact': 'required_for_each_output_branch'}
            return reviewed([rule], dimension='resources.documented_STL_example', content_role='worked_example', knowledge_kind='manual_example',
                            languages=['ladder'], conditions=[{'context': 'project_mode', 'equals': 'native_ladder'}],
                            source_binding='complete_program_graph_contact_polarities_and_annotations')
    if page == 962 and full(r'For details, refer to the \[GLYPH-F0C0\]MELSEC iQ-F'):
        if "FX5 User's Manual (Application)" in _page_layout(section, page):
            return reviewed([{'kind': 'external_manual_dependency', 'manual_family': 'MELSEC_iQ-F_FX5',
                              'manual_topic': 'Application', 'referenced_topic': 'PID_control_details'}],
                            dimension='resources.manual_dependency', source_binding='reference_completed_from_following_line_in_own_page')
    return None
